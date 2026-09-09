"""Client OAuth: chuẩn hoá lỗi provider, và không để token rò ra ngoài.

Chạy trên transport giả của httpx — không có gói tin nào rời khỏi tiến trình.

Hai thứ được canh ở đây, cả hai đều hỏng lặng lẽ nếu sai:

- **Phân loại lỗi.** Temporal đọc ``retryable`` để quyết định thử lại. Đánh dấu
  một credential đã chết là "thử lại được" sẽ khiến Activity quay vòng tới lúc
  cạn lượt; đánh dấu một lần nghẽn mạng là "vĩnh viễn" sẽ bắt người dùng kết
  nối lại một kết nối chẳng có gì sai.
- **Rò rỉ.** Thân phản hồi của endpoint token *chính là* nơi chứa token.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from app.core.errors import (
    PlatformTimeoutError,
    ProviderPermanentError,
    ProviderUnavailableError,
    RateLimitedError,
)
from app.domains.connection.application.ports import ClientCredentials
from app.domains.connection.domain.entities import OAuthTokens
from app.domains.connection.infrastructure.oauth_client import HttpxOAuthClient

CREDENTIALS = {
    "slack": ClientCredentials(client_id="slack-cid", client_secret="slack-secret"),
    "google": ClientCredentials(client_id="google-cid", client_secret="google-secret"),
    "notion": ClientCredentials(client_id="notion-cid", client_secret="notion-secret"),
}


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Thay ``httpx.AsyncClient`` bằng một bản chạy trên transport giả."""
    holder: dict[str, Any] = {"handler": None, "requests": []}

    def handler(request: httpx.Request) -> httpx.Response:
        holder["requests"].append(request)
        assert holder["handler"] is not None
        response: httpx.Response = holder["handler"](request)
        return response

    real_client = httpx.AsyncClient

    def fake_client(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("timeout", None)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    # Vá thẳng `httpx.AsyncClient`. Đây là thay đổi toàn cục trong lúc test
    # chạy — `oauth_client` tra thuộc tính này lúc gọi chứ không giữ tham chiếu
    # riêng, nên không có cách nào hẹp hơn. `monkeypatch` hoàn nguyên sau mỗi
    # test, và bộ test này chạy tuần tự nên không có ai đọc phải bản đã vá.
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    return holder


def respond(status: int = 200, json_body: dict[str, Any] | None = None) -> Any:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=json_body or {})

    return handler


def client() -> HttpxOAuthClient:
    return HttpxOAuthClient(CREDENTIALS)


# ─── Đường thành công ────────────────────────────────────────────────────────


async def test_doi_code_lay_token_va_doc_han(transport: Any) -> None:
    transport["handler"] = respond(
        200,
        {
            "access_token": "xoxb-1",
            "refresh_token": "rt-1",
            "expires_in": 3600,
            "scope": "chat:write,channels:read",
            "token_type": "Bearer",
        },
    )

    tokens, body = await client().exchange_code(
        provider="slack", code="c", redirect_uri="https://a/cb", code_verifier=None
    )

    assert tokens.access_token == "xoxb-1"
    assert tokens.refresh_token == "rt-1"
    assert tokens.expires_at is not None
    # Dấu phẩy của Slack, không phải dấu cách của RFC.
    assert tokens.scopes == frozenset({"chat:write", "channels:read"})
    assert "access_token" not in body


async def test_than_phan_hoi_tra_len_tren_da_bo_moi_bi_mat(transport: Any) -> None:
    """Kể cả bí mật lồng trong object con — Slack đặt một token thứ hai ở
    ``authed_user.access_token``."""
    transport["handler"] = respond(
        200,
        {
            "access_token": "xoxb-bi-mat",
            "refresh_token": "rt-bi-mat",
            "id_token": "jwt-bi-mat",
            "team": {"id": "T1", "name": "Acme"},
            "authed_user": {"id": "U1", "access_token": "xoxp-bi-mat"},
        },
    )

    _, body = await client().exchange_code(
        provider="slack", code="c", redirect_uri="https://a/cb", code_verifier=None
    )

    rendered = repr(body)
    for secret in ("xoxb-bi-mat", "rt-bi-mat", "jwt-bi-mat", "xoxp-bi-mat"):
        assert secret not in rendered
    # Phần còn dùng được thì vẫn phải còn.
    assert body["team"] == {"id": "T1", "name": "Acme"}
    assert body["authed_user"]["id"] == "U1"


async def test_danh_tinh_client_di_dung_cho_provider_doc_no(transport: Any) -> None:
    """RFC 6749 §2.3.1 cho phép hai chỗ; provider thì mỗi bên chọn một.

    Slack đọc ``client_id``/``client_secret`` trong thân form. Notion **chỉ**
    đọc header Basic và trả 400 cho thân form — không tách được hai thứ này ra
    thành dữ liệu của descriptor thì Notion không bao giờ đổi được token, và
    thất bại đó chỉ hiện ra sau khi người dùng đã bấm đồng ý.
    """
    transport["handler"] = respond(200, {"access_token": "at"})

    await client().exchange_code(
        provider="slack", code="c", redirect_uri="https://a/cb", code_verifier=None
    )
    slack = transport["requests"][-1]
    assert slack.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert b"client_secret=slack-secret" in slack.content
    assert "authorization" not in slack.headers

    await client().exchange_code(
        provider="notion", code="c", redirect_uri="https://a/cb", code_verifier=None
    )
    notion = transport["requests"][-1]
    assert notion.headers["content-type"].startswith("application/json")
    expected = base64.b64encode(b"notion-cid:notion-secret").decode()
    assert notion.headers["authorization"] == f"Basic {expected}"
    # Bí mật đi trong header thì không được đồng thời nằm trong thân.
    assert b"notion-secret" not in notion.content
    assert json.loads(notion.content)["grant_type"] == "authorization_code"


async def test_thu_hoi_theo_notion_dung_basic_thay_vi_bearer(transport: Any) -> None:
    """Danh tính client thắng Bearer khi provider khai ``token_auth="basic"``.

    Lời gọi thu hồi của Slack tự xác thực bằng chính token bị thu hồi, nên
    Bearer là mặc định đúng ở đó. Notion xác thực bằng client — gửi Bearer sang
    là bị từ chối, và `revoke` nuốt mọi lỗi nên nó sẽ hỏng hoàn toàn im lặng.
    """
    transport["handler"] = respond(200, {})

    await client().revoke(provider="notion", tokens=OAuthTokens(access_token="secret-token"))

    request = transport["requests"][-1]
    expected = base64.b64encode(b"notion-cid:notion-secret").decode()
    assert request.headers["authorization"] == f"Basic {expected}"
    assert json.loads(request.content) == {"token": "secret-token"}


async def test_pkce_verifier_duoc_gui_kem_khi_co(transport: Any) -> None:
    transport["handler"] = respond(200, {"access_token": "at"})

    await client().exchange_code(
        provider="google", code="c", redirect_uri="https://a/cb", code_verifier="verifier-1"
    )

    sent = transport["requests"][0].content.decode()
    assert "code_verifier=verifier-1" in sent


async def test_khong_gui_code_verifier_khi_khong_dung_pkce(transport: Any) -> None:
    transport["handler"] = respond(200, {"access_token": "at"})

    await client().exchange_code(
        provider="slack", code="c", redirect_uri="https://a/cb", code_verifier=None
    )

    assert "code_verifier" not in transport["requests"][0].content.decode()


async def test_gui_toi_dung_endpoint_cua_provider(transport: Any) -> None:
    """URL tới từ danh mục tĩnh, không bao giờ từ dữ liệu người dùng gửi lên."""
    transport["handler"] = respond(200, {"access_token": "at"})

    await client().exchange_code(
        provider="slack", code="c", redirect_uri="https://a/cb", code_verifier=None
    )

    assert str(transport["requests"][0].url) == "https://slack.com/api/oauth.v2.access"


# ─── Phân loại lỗi ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "code",
    ["invalid_grant", "invalid_client", "invalid_scope", "unauthorized_client"],
)
async def test_loi_oauth_vinh_vien_khong_duoc_danh_dau_retry(transport: Any, code: str) -> None:
    transport["handler"] = respond(400, {"error": code})

    with pytest.raises(ProviderPermanentError) as exc:
        await client().exchange_code(
            provider="google", code="c", redirect_uri="https://a/cb", code_verifier=None
        )

    assert exc.value.retryable is False
    assert exc.value.details["provider_error"] == code


async def test_slack_tra_200_kem_ok_false_van_bi_bat(transport: Any) -> None:
    """Slack không dùng status code — status 200 ở đây không nói lên điều gì."""
    transport["handler"] = respond(200, {"ok": False, "error": "invalid_code"})

    with pytest.raises(ProviderPermanentError) as exc:
        await client().exchange_code(
            provider="slack", code="c", redirect_uri="https://a/cb", code_verifier=None
        )
    assert exc.value.details["provider_error"] == "invalid_code"


async def test_slack_nghen_thi_la_loi_tam_thoi_chu_khong_phai_credential_hong(
    transport: Any,
) -> None:
    """Không tách ra thì một lần Slack quá tải sẽ bị đọc thành credential hỏng
    vĩnh viễn, và kết nối bị đánh dấu cần uỷ quyền lại dù chẳng có gì hỏng."""
    transport["handler"] = respond(200, {"ok": False, "error": "ratelimited"})

    with pytest.raises(ProviderUnavailableError) as exc:
        await client().exchange_code(
            provider="slack", code="c", redirect_uri="https://a/cb", code_verifier=None
        )
    assert exc.value.retryable is True


async def test_http_429_thanh_rate_limited_kem_retry_after(transport: Any) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={}, headers={"retry-after": "42"})

    transport["handler"] = handler

    with pytest.raises(RateLimitedError) as exc:
        await client().exchange_code(
            provider="google", code="c", redirect_uri="https://a/cb", code_verifier=None
        )

    assert exc.value.retryable is True
    assert exc.value.retry_after_seconds == 42


async def test_retry_after_dang_http_date_thi_bo_qua_chu_khong_no(transport: Any) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={}, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}
        )

    transport["handler"] = handler

    with pytest.raises(RateLimitedError) as exc:
        await client().exchange_code(
            provider="google", code="c", redirect_uri="https://a/cb", code_verifier=None
        )
    assert exc.value.retry_after_seconds is None


async def test_provider_hong_5xx_la_loi_thu_lai_duoc(transport: Any) -> None:
    transport["handler"] = respond(503, {})

    with pytest.raises(ProviderUnavailableError) as exc:
        await client().exchange_code(
            provider="google", code="c", redirect_uri="https://a/cb", code_verifier=None
        )
    assert exc.value.retryable is True


async def test_timeout_thanh_loi_timeout_cua_nen_tang(transport: Any) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("qua han")

    transport["handler"] = handler

    with pytest.raises(PlatformTimeoutError) as exc:
        await client().exchange_code(
            provider="google", code="c", redirect_uri="https://a/cb", code_verifier=None
        )
    assert exc.value.retryable is True


async def test_khong_ket_noi_duoc_la_provider_unavailable(transport: Any) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("khong noi duoc")

    transport["handler"] = handler

    with pytest.raises(ProviderUnavailableError):
        await client().exchange_code(
            provider="google", code="c", redirect_uri="https://a/cb", code_verifier=None
        )


async def test_phan_hoi_khong_phai_json_thi_bao_loi_ro_rang(transport: Any) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>")

    transport["handler"] = handler

    with pytest.raises(ProviderUnavailableError):
        await client().exchange_code(
            provider="google", code="c", redirect_uri="https://a/cb", code_verifier=None
        )


async def test_thieu_access_token_la_loi_vinh_vien(transport: Any) -> None:
    transport["handler"] = respond(200, {"token_type": "Bearer"})

    with pytest.raises(ProviderPermanentError):
        await client().exchange_code(
            provider="google", code="c", redirect_uri="https://a/cb", code_verifier=None
        )


async def test_loi_khong_bao_gio_dinh_kem_than_phan_hoi(transport: Any) -> None:
    """Thân phản hồi của endpoint token chính là nơi chứa token."""
    transport["handler"] = respond(
        400, {"error": "invalid_grant", "access_token": "xoxb-lo-ra", "hint": "abc"}
    )

    with pytest.raises(ProviderPermanentError) as exc:
        await client().exchange_code(
            provider="slack", code="c", redirect_uri="https://a/cb", code_verifier=None
        )

    rendered = f"{exc.value} {exc.value.details} {exc.value.to_payload()}"
    assert "xoxb-lo-ra" not in rendered


async def test_provider_chua_cau_hinh_thi_khong_goi_mang(transport: Any) -> None:
    transport["handler"] = respond(200, {"access_token": "at"})

    with pytest.raises(ProviderPermanentError):
        await HttpxOAuthClient({}).exchange_code(
            provider="slack", code="c", redirect_uri="https://a/cb", code_verifier=None
        )

    assert transport["requests"] == []


# ─── Làm mới ─────────────────────────────────────────────────────────────────


async def test_lam_moi_gui_dung_grant_type(transport: Any) -> None:
    transport["handler"] = respond(200, {"access_token": "at-moi", "expires_in": 3600})

    tokens = await client().refresh(provider="google", refresh_token="rt-1")

    sent = transport["requests"][0].content.decode()
    assert "grant_type=refresh_token" in sent
    assert "refresh_token=rt-1" in sent
    assert tokens.access_token == "at-moi"


# ─── Danh tính tài khoản ─────────────────────────────────────────────────────


async def test_tra_danh_tinh_tai_khoan_theo_duong_dan_khai_bao(transport: Any) -> None:
    transport["handler"] = respond(200, {"sub": "123", "email": "an@example.com"})

    account = await client().fetch_account(provider="google", access_token="at")

    assert account.id == "123"
    assert account.label == "an@example.com"
    assert transport["requests"][0].headers["authorization"] == "Bearer at"


async def test_provider_khong_khai_endpoint_thi_khong_goi_mang(transport: Any) -> None:
    """Slack đã có danh tính team ngay trong phản hồi token."""
    transport["handler"] = respond(200, {})

    account = await client().fetch_account(provider="slack", access_token="at")

    assert account.id is None
    assert transport["requests"] == []


async def test_tra_danh_tinh_that_bai_khong_lam_hong_ca_vong_uy_quyen(
    transport: Any,
) -> None:
    """Danh tính chỉ để hiển thị — kết nối vẫn phải dùng được khi thiếu nó."""
    transport["handler"] = respond(403, {"error": "insufficient_scope"})

    account = await client().fetch_account(provider="google", access_token="at")

    assert account.id is None
    assert account.label is None


# ─── Thu hồi ─────────────────────────────────────────────────────────────────


async def test_thu_hoi_nuot_loi_cua_provider(transport: Any) -> None:
    """Credential bị xoá khỏi database ngay sau lời gọi này.

    Để một provider đang chết chặn được bước đó thì kết quả là mình vẫn giữ
    token mà người dùng thì tưởng đã gỡ — tệ hơn hẳn việc token còn sống thêm
    ở phía provider.
    """
    transport["handler"] = respond(500, {})

    await client().revoke(provider="slack", tokens=OAuthTokens(access_token="at"))


async def test_thu_hoi_khi_mat_mang_cung_khong_no(transport: Any) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("mat mang")

    transport["handler"] = handler

    await client().revoke(provider="google", tokens=OAuthTokens(access_token="at"))
