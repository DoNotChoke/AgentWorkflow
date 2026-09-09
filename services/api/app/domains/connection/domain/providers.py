"""Danh mục provider và hình dạng của giao thức OAuth 2.0."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlencode

from app.core.errors import ValidationError


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    """Mọi thứ cần biết để chạy trọn một vòng OAuth với một provider."""

    id: str
    label: str

    authorize_url: str
    token_url: str
    revoke_url: str | None = None

    default_scopes: tuple[str, ...] = ()

    #: RFC 6749 quy định dấu cách. Slack thì dùng dấu phẩy.
    scope_delimiter: str = " "

    #: Provider có dùng scope OAuth không.
    #:
    #: ``False`` nghĩa là quyền được cấp bằng cơ chế khác hẳn: Notion cấp quyền
    #: theo từng trang người dùng chia sẻ với integration, và endpoint uỷ quyền
    #: của họ không nhận tham số ``scope`` nào cả. Khai ``True`` cho một provider
    #: như thế thì mình gửi sang một tham số vô nghĩa rồi lưu lại một tập
    #: ``granted_scopes`` không phản ánh điều gì có thật.
    uses_scopes: bool = True

    #: PKCE (RFC 7636). Bật khi provider hỗ trợ
    supports_pkce: bool = False

    #: Provider có phát refresh token không.
    supports_refresh: bool = True

    #: Tham số riêng phải kèm vào URL uỷ quyền.
    extra_authorize_params: tuple[tuple[str, str], ...] = ()

    #: Danh tính client đi ở đâu khi gọi endpoint token và endpoint thu hồi.
    #: RFC 6749 §2.3.1 cho phép cả hai chỗ và nói header Basic là chỗ được ưu
    #: tiên; Slack và Google nhận cả hai, Notion **chỉ** nhận Basic.
    client_auth: Literal["body", "basic"] = "body"

    #: Định dạng thân của hai request ấy.
    request_format: Literal["form", "json"] = "form"

    #: Nơi tra danh tính tài khoản sau khi đổi được token. Bỏ trống nghĩa là
    #: danh tính đã nằm sẵn trong phản hồi token — Slack trả kèm khối ``team``.
    identity_endpoint: str | None = None

    #: Đường dẫn tới danh tính trong JSON, tính từ gốc của phản hồi được chọn.
    account_id_path: tuple[str, ...] = ()
    account_label_path: tuple[str, ...] = ()

    #: Ghi chú hiển thị cho người dùng ở màn hình kết nối.
    notes: str = field(default="")

    @property
    def reads_identity_from_token_response(self) -> bool:
        return self.identity_endpoint is None


# ─────────────────────────────────────────────────────────────────────────────
# Danh mục
# ─────────────────────────────────────────────────────────────────────────────

SLACK = ProviderDescriptor(
    id="slack",
    label="Slack",
    authorize_url="https://slack.com/oauth/v2/authorize",
    token_url="https://slack.com/api/oauth.v2.access",  # noqa: S106 — URL, không phải token
    revoke_url="https://slack.com/api/auth.revoke",
    default_scopes=("channels:history", "channels:read", "chat:write", "users:read"),
    scope_delimiter=",",
    supports_pkce=False,
    supports_refresh=False,
    account_id_path=("team", "id"),
    account_label_path=("team", "name"),
    notes="Bot token không hết hạn. Gỡ kết nối phía Slack tại Settings → Manage apps.",
)

GOOGLE = ProviderDescriptor(
    id="google",
    label="Google",
    authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",  # noqa: S106 — URL, không phải token
    revoke_url="https://oauth2.googleapis.com/revoke",
    default_scopes=(
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/userinfo.email",
        "openid",
    ),
    supports_pkce=True,
    supports_refresh=True,
    extra_authorize_params=(("access_type", "offline"), ("prompt", "consent")),
    identity_endpoint="https://www.googleapis.com/oauth2/v3/userinfo",
    account_id_path=("sub",),
    account_label_path=("email",),
)

NOTION = ProviderDescriptor(
    id="notion",
    label="Notion",
    authorize_url="https://api.notion.com/v1/oauth/authorize",
    token_url="https://api.notion.com/v1/oauth/token",  # noqa: S106 — URL, không phải token
    revoke_url="https://api.notion.com/v1/oauth/revoke",
    uses_scopes=False,
    supports_pkce=False,
    supports_refresh=False,
    # ``owner=user`` là bắt buộc: nó nói với Notion rằng đây là một integration
    # công khai xin quyền của một người dùng, chứ không phải integration nội bộ.
    extra_authorize_params=(("owner", "user"),),
    client_auth="basic",
    request_format="json",
    # Notion trả kèm khối workspace ngay trong phản hồi token, nên không cần
    # thêm một lượt gọi tra danh tính.
    account_id_path=("workspace_id",),
    account_label_path=("workspace_name",),
    notes=(
        "Notion không dùng scope. Quyền được cấp theo từng trang bạn chọn chia "
        "sẻ ở màn hình chấp thuận — muốn thêm trang thì kết nối lại. Token "
        "không hết hạn."
    ),
)

#: Danh mục provider được hỗ trợ. Thêm provider = thêm một dòng ở đây.
PROVIDERS: dict[str, ProviderDescriptor] = {p.id: p for p in (SLACK, GOOGLE, NOTION)}


def get_provider(provider_id: str) -> ProviderDescriptor:
    descriptor = PROVIDERS.get(provider_id.strip().lower())
    if descriptor is None:
        raise ValidationError(
            f"Provider {provider_id!r} không được hỗ trợ.",
            details={"supported": sorted(PROVIDERS)},
        )
    return descriptor


def list_providers() -> list[ProviderDescriptor]:
    return sorted(PROVIDERS.values(), key=lambda p: p.label)


# ─────────────────────────────────────────────────────────────────────────────
# PKCE — RFC 7636
# ─────────────────────────────────────────────────────────────────────────────

#: RFC 7636 §4.1 cho phép 43–128 ký tự. 64 byte ngẫu nhiên qua base64url ra 86
#: ký tự, nằm gọn trong khoảng đó.
_VERIFIER_BYTES = 64


@dataclass(frozen=True, slots=True)
class PkcePair:
    verifier: str
    challenge: str
    method: str = "S256"

    def __repr__(self) -> str:
        # `verifier` là bí mật một lần: lộ nó ra thì PKCE mất tác dụng.
        return f"PkcePair(method={self.method!r})"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def new_pkce_pair() -> PkcePair:
    verifier = _b64url(secrets.token_bytes(_VERIFIER_BYTES))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return PkcePair(verifier=verifier, challenge=challenge)


# ─────────────────────────────────────────────────────────────────────────────
# URL uỷ quyền
# ─────────────────────────────────────────────────────────────────────────────


def build_authorize_url(
    descriptor: ProviderDescriptor,
    *,
    client_id: str,
    redirect_uri: str,
    scopes: frozenset[str],
    state: str,
    pkce: PkcePair | None,
) -> str:
    """Dựng URL để trình duyệt người dùng đi tới trang chấp thuận của provider."""
    if pkce is not None and not descriptor.supports_pkce:
        raise ValidationError(f"{descriptor.label} không hỗ trợ PKCE.")

    if scopes and not descriptor.uses_scopes:
        raise ValidationError(f"{descriptor.label} không dùng scope OAuth.")

    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }
    # Provider không dùng scope thì **không** gắn tham số rỗng: một `scope=`
    # trống là thứ provider có quyền từ chối, và nó nói sai về điều đang xảy ra.
    if descriptor.uses_scopes:
        params["scope"] = descriptor.scope_delimiter.join(sorted(scopes))
    params.update(dict(descriptor.extra_authorize_params))

    if pkce is not None:
        params["code_challenge"] = pkce.challenge
        params["code_challenge_method"] = pkce.method

    return f"{descriptor.authorize_url}?{urlencode(params)}"


def read_path(payload: object, path: tuple[str, ...]) -> str | None:
    """Đọc một giá trị chuỗi theo đường dẫn khai báo trong descriptor.

    Trả ``None`` khi đường dẫn không tồn tại thay vì ném lỗi: danh tính tài
    khoản là dữ liệu để hiển thị cho đẹp, không phải điều kiện để kết nối chạy
    được. Provider đổi hình dạng phản hồi thì kết nối vẫn phải dùng được.
    """
    if not path:
        return None

    current: object = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)

    if current is None:
        return None
    return str(current) if isinstance(current, str | int) else None
