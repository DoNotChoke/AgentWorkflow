"""Use case của Identity: đăng ký, đăng nhập, xoay refresh token, đăng xuất."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.config import Settings
from app.core.errors import AuthenticationError, ValidationError
from app.core.ids import ResourceKind, new_id
from app.core.logging import get_logger
from app.core.security import (
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    issue_access_token,
    password_needs_rehash,
    refresh_token_expires_at,
    verify_password,
    verify_password_of_missing_user,
)
from app.domains.identity.application.ports import (
    EmailAlreadyRegisteredError,
    RefreshTokenRepository,
    TenantProvisioner,
    UserRepository,
)
from app.domains.identity.domain.entities import (
    RefreshToken,
    TokenVerdict,
    User,
    UserStatus,
    classify_refresh_token,
    normalize_display_name,
    normalize_email,
    validate_password_strength,
)

log = get_logger(__name__)

_LOGIN_FAILED = "Email hoặc mật khẩu không đúng."

_REFRESH_FAILED = "Phiên làm việc không còn hiệu lực. Vui lòng đăng nhập lại."


@dataclass(frozen=True, slots=True)
class AuthResult:
    """Kết quả trả cho client sau khi xác thực thành công."""

    user: User
    access_token: str
    access_token_expires_at: datetime
    refresh_token: str
    refresh_token_expires_at: datetime


class IdentityService:
    def __init__(
        self,
        *,
        settings: Settings,
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        tenant_provisioner: TenantProvisioner,
    ) -> None:
        self._settings = settings
        self._users = users
        self._refresh_tokens = refresh_tokens
        self._tenant_provisioner = tenant_provisioner

    # ─── Đăng ký ─────────────────────────────────────────────────────────────

    async def register(self, *, email: str, password: str, display_name: str = "") -> AuthResult:
        normalized = normalize_email(email)
        validate_password_strength(password)

        now = datetime.now(tz=UTC)
        user = User(
            id=new_id(ResourceKind.USER),
            email=normalized,
            password_hash=hash_password(password),
            display_name=normalize_display_name(display_name, fallback_email=normalized),
            status=UserStatus.ACTIVE,
            email_verified_at=None,
            created_at=now,
            updated_at=now,
        )

        try:
            await self._users.add(user)
        except EmailAlreadyRegisteredError as exc:
            raise ValidationError("Email này đã được đăng ký.", cause=exc) from exc

        # Cùng transaction với INSERT user ở trên: nếu cấp tenant hỏng thì user
        # cũng không tồn tại. Một user không thuộc organization nào thì không
        # thao tác được gì (§19), nên nửa vời còn tệ hơn thất bại hẳn.
        await self._tenant_provisioner.provision_for_new_user(
            user_id=user.id,
            display_name=user.display_name,
        )

        log.info("user_registered", user_id=user.id)
        result, _ = await self._issue_session(user, now=now)
        return result

    # ─── Đăng nhập ───────────────────────────────────────────────────────────

    async def authenticate(self, *, email: str, password: str) -> AuthResult:
        normalized = normalize_email(email)
        user = await self._users.get_by_email(normalized)

        if user is None:
            verify_password_of_missing_user()
            raise AuthenticationError(_LOGIN_FAILED)

        if not verify_password(user.password_hash, password):
            raise AuthenticationError(_LOGIN_FAILED)

        if not user.can_authenticate:
            raise AuthenticationError(_LOGIN_FAILED)

        now = datetime.now(tz=UTC)

        if password_needs_rehash(user.password_hash):
            await self._users.update_password_hash(user.id, hash_password(password), at=now)
            log.info("password_rehashed", user_id=user.id)

        log.info("user_logged_in", user_id=user.id)
        result, _ = await self._issue_session(user, now=now)
        return result

    # ─── Xoay refresh token ──────────────────────────────────────────────────

    async def refresh(self, raw_refresh_token: str) -> AuthResult:
        """Đổi refresh token lấy phiên mới, và xoay vòng chính nó."""
        now = datetime.now(tz=UTC)
        presented = await self._refresh_tokens.get_by_hash(hash_refresh_token(raw_refresh_token))
        verdict = classify_refresh_token(presented, now)

        if presented is None:
            raise AuthenticationError(_REFRESH_FAILED)

        if verdict is TokenVerdict.REUSED:
            await self._refresh_tokens.revoke_family(presented.family_id, at=now)
            log.warning(
                "refresh_token_reuse_detected",
                user_id=presented.user_id,
                family_id=presented.family_id,
            )
            raise AuthenticationError(_REFRESH_FAILED)

        if verdict is not TokenVerdict.VALID:
            raise AuthenticationError(_REFRESH_FAILED)

        user = await self._users.get_by_id(presented.user_id)
        if user is None or not user.can_authenticate:
            await self._refresh_tokens.revoke_family(presented.family_id, at=now)
            raise AuthenticationError(_REFRESH_FAILED)

        result, issued = await self._issue_session(
            user,
            now=now,
            family_id=presented.family_id,
            expires_at=presented.expires_at,
        )
        await self._refresh_tokens.mark_rotated(presented.id, rotated_to_id=issued.id, at=now)
        return result

    # ─── Đăng xuất ───────────────────────────────────────────────────────────

    async def logout(self, raw_refresh_token: str) -> None:
        """Thu hồi cả họ token, không chỉ mắt xích đang cầm."""
        token = await self._refresh_tokens.get_by_hash(hash_refresh_token(raw_refresh_token))
        if token is None:
            return
        await self._refresh_tokens.revoke_family(token.family_id, at=datetime.now(tz=UTC))
        log.info("user_logged_out", user_id=token.user_id)

    # ─── Đọc ─────────────────────────────────────────────────────────────────

    async def get_user(self, user_id: str) -> User:
        user = await self._users.get_by_id(user_id)
        if user is None or not user.can_authenticate:
            raise AuthenticationError("Không tìm thấy người dùng của phiên làm việc này.")
        return user

    # ─── Nội bộ ──────────────────────────────────────────────────────────────

    async def _issue_session(
        self,
        user: User,
        *,
        now: datetime,
        family_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> tuple[AuthResult, RefreshToken]:
        """Cấp một cặp access + refresh token."""
        access_token, claims = issue_access_token(self._settings, subject=user.id)

        raw_refresh, token_hash = generate_refresh_token()
        token = RefreshToken(
            id=new_id(ResourceKind.REFRESH_TOKEN),
            user_id=user.id,
            family_id=family_id or new_id(ResourceKind.TOKEN_FAMILY),
            token_hash=token_hash,
            issued_at=now,
            expires_at=expires_at or refresh_token_expires_at(self._settings),
        )
        await self._refresh_tokens.add(token)

        result = AuthResult(
            user=user,
            access_token=access_token,
            access_token_expires_at=claims.expires_at,
            refresh_token=raw_refresh,
            refresh_token_expires_at=token.expires_at,
        )
        return result, token
