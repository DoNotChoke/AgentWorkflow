"""Kiểm tra use case của Identity bằng repository giả trong bộ nhớ.

Không cần PostgreSQL. Repository giả ở đây hiện thực đúng các ``Protocol`` của
tầng application, nên nếu port đổi chữ ký mà quên sửa hiện thực thật, mypy sẽ
bắt được ở cả hai phía.

Test đáng giá nhất là nhóm "phát hiện token bị dùng lại" — đó là hành vi bảo
mật khó kiểm bằng tay nhất và cũng dễ hỏng nhất khi refactor.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.core.config import Settings
from app.core.errors import AuthenticationError, ValidationError
from app.domains.identity.application.ports import EmailAlreadyRegisteredError
from app.domains.identity.application.service import IdentityService
from app.domains.identity.domain.entities import RefreshToken, User, UserStatus

MAT_KHAU = "mat-khau-du-dai-12"


class FakeUserRepository:
    def __init__(self) -> None:
        self.rows: dict[str, User] = {}

    async def get_by_email(self, email: str) -> User | None:
        return next((u for u in self.rows.values() if u.email == email), None)

    async def get_by_id(self, user_id: str) -> User | None:
        return self.rows.get(user_id)

    async def add(self, user: User) -> None:
        if await self.get_by_email(user.email) is not None:
            raise EmailAlreadyRegisteredError(user.email)
        self.rows[user.id] = user

    async def update_password_hash(self, user_id: str, password_hash: str, *, at: datetime) -> None:
        current = self.rows[user_id]
        self.rows[user_id] = User(
            id=current.id,
            email=current.email,
            password_hash=password_hash,
            display_name=current.display_name,
            status=current.status,
            email_verified_at=current.email_verified_at,
            created_at=current.created_at,
            updated_at=at,
        )


class FakeRefreshTokenRepository:
    def __init__(self) -> None:
        self.rows: dict[str, RefreshToken] = {}

    async def add(self, token: RefreshToken) -> None:
        self.rows[token.id] = token

    async def get_by_hash(self, token_hash: str) -> RefreshToken | None:
        return next((t for t in self.rows.values() if t.token_hash == token_hash), None)

    async def mark_rotated(self, token_id: str, *, rotated_to_id: str, at: datetime) -> None:
        current = self.rows[token_id]
        self.rows[token_id] = RefreshToken(
            id=current.id,
            user_id=current.user_id,
            family_id=current.family_id,
            token_hash=current.token_hash,
            issued_at=current.issued_at,
            expires_at=current.expires_at,
            revoked_at=at,
            rotated_to_id=rotated_to_id,
        )

    async def revoke_family(self, family_id: str, *, at: datetime) -> None:
        for key, token in list(self.rows.items()):
            if token.family_id == family_id and token.revoked_at is None:
                self.rows[key] = RefreshToken(
                    id=token.id,
                    user_id=token.user_id,
                    family_id=token.family_id,
                    token_hash=token.token_hash,
                    issued_at=token.issued_at,
                    expires_at=token.expires_at,
                    revoked_at=at,
                    rotated_to_id=token.rotated_to_id,
                )


@pytest.fixture
def users() -> FakeUserRepository:
    return FakeUserRepository()


@pytest.fixture
def tokens() -> FakeRefreshTokenRepository:
    return FakeRefreshTokenRepository()


class FakeTenantProvisioner:
    """Ghi lại lời gọi, không tạo gì thật.

    Test ở đây kiểm domain Identity. Việc cấp tenant có đúng hay không là
    trách nhiệm của `test_tenant_service.py`; ở đây chỉ cần biết Identity có
    gọi nó không, và gọi với dữ liệu nào.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def provision_for_new_user(self, *, user_id: str, display_name: str) -> object:
        self.calls.append((user_id, display_name))
        return None


class FailingTenantProvisioner:
    async def provision_for_new_user(self, *, user_id: str, display_name: str) -> object:
        raise RuntimeError("cap tenant that bai")


@pytest.fixture
def provisioner() -> FakeTenantProvisioner:
    return FakeTenantProvisioner()


@pytest.fixture
def service(
    users: FakeUserRepository,
    tokens: FakeRefreshTokenRepository,
    provisioner: FakeTenantProvisioner,
) -> IdentityService:
    settings = Settings(jwt_secret="khoa-ky-dung-cho-test-du-32-byte-tro-len")
    return IdentityService(
        settings=settings,
        users=users,
        refresh_tokens=tokens,
        tenant_provisioner=provisioner,
    )


# ─── Đăng ký ─────────────────────────────────────────────────────────────────


async def test_dang_ky_tao_user_va_cap_phien_ngay(service: IdentityService) -> None:
    result = await service.register(email="A@Example.com", password=MAT_KHAU)

    assert result.user.email == "a@example.com"
    assert result.user.status is UserStatus.ACTIVE
    assert result.access_token
    assert result.refresh_token


async def test_dang_ky_khong_luu_mat_khau_dang_ro(
    service: IdentityService, users: FakeUserRepository
) -> None:
    await service.register(email="a@example.com", password=MAT_KHAU)
    stored = next(iter(users.rows.values()))

    assert MAT_KHAU not in stored.password_hash
    assert stored.password_hash.startswith("$argon2")


async def test_dang_ky_trung_email_bi_tu_choi(service: IdentityService) -> None:
    await service.register(email="a@example.com", password=MAT_KHAU)

    with pytest.raises(ValidationError):
        await service.register(email="A@EXAMPLE.COM", password=MAT_KHAU)


async def test_dang_ky_voi_mat_khau_yeu_bi_tu_choi(service: IdentityService) -> None:
    with pytest.raises(ValidationError):
        await service.register(email="a@example.com", password="ngan")


async def test_refresh_token_tho_khong_duoc_luu_vao_repo(
    service: IdentityService, tokens: FakeRefreshTokenRepository
) -> None:
    """Database chỉ giữ hash — rò rỉ database không đủ để mạo danh phiên."""
    result = await service.register(email="a@example.com", password=MAT_KHAU)
    stored = next(iter(tokens.rows.values()))

    assert stored.token_hash != result.refresh_token
    assert result.refresh_token not in stored.token_hash


# ─── Đăng nhập ───────────────────────────────────────────────────────────────


async def test_dang_nhap_thanh_cong(service: IdentityService) -> None:
    await service.register(email="a@example.com", password=MAT_KHAU)
    result = await service.authenticate(email="a@example.com", password=MAT_KHAU)

    assert result.user.email == "a@example.com"


async def test_dang_nhap_khong_phan_biet_hoa_thuong_cua_email(service: IdentityService) -> None:
    await service.register(email="a@example.com", password=MAT_KHAU)
    assert await service.authenticate(email="A@Example.COM", password=MAT_KHAU)


async def test_email_khong_ton_tai_va_sai_mat_khau_bao_cung_mot_thong_diep(
    service: IdentityService,
) -> None:
    """Khác thông điệp là form đăng nhập trở thành công cụ dò danh sách user."""
    await service.register(email="a@example.com", password=MAT_KHAU)

    with pytest.raises(AuthenticationError) as khong_ton_tai:
        await service.authenticate(email="khong-co@example.com", password=MAT_KHAU)
    with pytest.raises(AuthenticationError) as sai_mat_khau:
        await service.authenticate(email="a@example.com", password="mat-khau-sai-nhung-du-dai")

    assert khong_ton_tai.value.message == sai_mat_khau.value.message


async def test_user_bi_vo_hieu_hoa_khong_dang_nhap_duoc(
    service: IdentityService, users: FakeUserRepository
) -> None:
    await service.register(email="a@example.com", password=MAT_KHAU)
    current = next(iter(users.rows.values()))
    users.rows[current.id] = User(
        id=current.id,
        email=current.email,
        password_hash=current.password_hash,
        display_name=current.display_name,
        status=UserStatus.DISABLED,
        email_verified_at=None,
        created_at=current.created_at,
        updated_at=current.updated_at,
    )

    with pytest.raises(AuthenticationError):
        await service.authenticate(email="a@example.com", password=MAT_KHAU)


# ─── Xoay refresh token ──────────────────────────────────────────────────────


async def test_refresh_cap_token_moi(service: IdentityService) -> None:
    dau = await service.register(email="a@example.com", password=MAT_KHAU)
    sau = await service.refresh(dau.refresh_token)

    assert sau.refresh_token != dau.refresh_token
    assert sau.user.id == dau.user.id


async def test_token_cu_bi_vo_hieu_ngay_sau_khi_xoay(service: IdentityService) -> None:
    dau = await service.register(email="a@example.com", password=MAT_KHAU)
    await service.refresh(dau.refresh_token)

    with pytest.raises(AuthenticationError):
        await service.refresh(dau.refresh_token)


async def test_xoay_vong_khong_gia_han_them_ho_token(service: IdentityService) -> None:
    """Nếu mỗi lần xoay đều đẩy hạn ra xa thì phiên sống mãi mãi."""
    dau = await service.register(email="a@example.com", password=MAT_KHAU)
    sau = await service.refresh(dau.refresh_token)

    assert sau.refresh_token_expires_at == dau.refresh_token_expires_at


async def test_dung_lai_token_cu_thu_hoi_ca_ho(
    service: IdentityService, tokens: FakeRefreshTokenRepository
) -> None:
    """Hành vi bảo mật trung tâm của M1.

    Kẻ tấn công lấy được token, nạn nhân xoay trước. Khi kẻ tấn công dùng token
    cũ, cả họ bị thu hồi — token *mới* của nạn nhân cũng chết theo, buộc đăng
    nhập lại. Đó là phản ứng đúng: không thể phân biệt ai là ai.
    """
    dau = await service.register(email="a@example.com", password=MAT_KHAU)
    hop_le = await service.refresh(dau.refresh_token)

    with pytest.raises(AuthenticationError):
        await service.refresh(dau.refresh_token)

    assert all(t.revoked_at is not None for t in tokens.rows.values())

    with pytest.raises(AuthenticationError):
        await service.refresh(hop_le.refresh_token)


async def test_hai_lan_dang_nhap_tao_hai_ho_doc_lap(
    service: IdentityService, tokens: FakeRefreshTokenRepository
) -> None:
    """Thu hồi phiên trên máy này không được làm rớt phiên trên máy kia."""
    may_a = await service.register(email="a@example.com", password=MAT_KHAU)
    may_b = await service.authenticate(email="a@example.com", password=MAT_KHAU)

    await service.refresh(may_a.refresh_token)
    with pytest.raises(AuthenticationError):
        await service.refresh(may_a.refresh_token)

    # Họ của máy B không liên quan, vẫn phải dùng được.
    assert await service.refresh(may_b.refresh_token)


async def test_token_bia_dat_bi_tu_choi(service: IdentityService) -> None:
    with pytest.raises(AuthenticationError):
        await service.refresh("token-hoan-toan-bia-dat")


# ─── Đăng xuất ───────────────────────────────────────────────────────────────


async def test_dang_xuat_thu_hoi_ca_ho(service: IdentityService) -> None:
    dau = await service.register(email="a@example.com", password=MAT_KHAU)
    sau = await service.refresh(dau.refresh_token)

    await service.logout(sau.refresh_token)

    with pytest.raises(AuthenticationError):
        await service.refresh(sau.refresh_token)


async def test_dang_xuat_lap_lai_khong_nem_loi(service: IdentityService) -> None:
    """Client phải thoát được kể cả khi token đã hỏng."""
    dau = await service.register(email="a@example.com", password=MAT_KHAU)

    await service.logout(dau.refresh_token)
    await service.logout(dau.refresh_token)
    await service.logout("token-khong-ton-tai")


async def test_dang_xuat_khong_anh_huong_phien_khac(service: IdentityService) -> None:
    may_a = await service.register(email="a@example.com", password=MAT_KHAU)
    may_b = await service.authenticate(email="a@example.com", password=MAT_KHAU)

    await service.logout(may_a.refresh_token)

    assert await service.refresh(may_b.refresh_token)


# ─── Đọc user ────────────────────────────────────────────────────────────────


async def test_lay_duoc_user_cua_phien(service: IdentityService) -> None:
    dau = await service.register(email="a@example.com", password=MAT_KHAU)
    assert (await service.get_user(dau.user.id)).email == "a@example.com"


async def test_user_khong_ton_tai_thi_nem_loi_xac_thuc(service: IdentityService) -> None:
    with pytest.raises(AuthenticationError):
        await service.get_user("usr_khong-co-that")


# ─── Cấp tenant khi đăng ký ──────────────────────────────────────────────────


async def test_dang_ky_thi_cap_tenant(
    service: IdentityService, provisioner: FakeTenantProvisioner
) -> None:
    result = await service.register(email="an@example.com", password=MAT_KHAU, display_name="An")

    assert provisioner.calls == [(result.user.id, "An")]


async def test_cap_tenant_hong_thi_dang_ky_hong_theo(
    users: FakeUserRepository, tokens: FakeRefreshTokenRepository
) -> None:
    """Không được để lại user không thuộc organization nào — §19."""
    settings = Settings(jwt_secret="khoa-ky-dung-cho-test-du-32-byte-tro-len")
    service = IdentityService(
        settings=settings,
        users=users,
        refresh_tokens=tokens,
        tenant_provisioner=FailingTenantProvisioner(),
    )

    with pytest.raises(RuntimeError):
        await service.register(email="an@example.com", password=MAT_KHAU)
