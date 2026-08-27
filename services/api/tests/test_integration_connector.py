"""Driver HTTP chung, chạy trên transport giả của httpx.

Không gói tin nào rời khỏi tiến trình. Cùng hình với
`test_connection_oauth_client.py` — vá thẳng `httpx.AsyncClient`, không thêm
`respx`.

Bốn tool Slack thật được dùng làm ca kiểm: chúng là bằng chứng rằng driver này
chạy được một provider có thật mà không cần một dòng code nào riêng cho nó.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import pytest

from app.core.errors import (
    AuthorizationError,
    ConnectionExpiredError,
    MissingScopeError,
    PlatformTimeoutError,
    ProviderPermanentError,
    ProviderUnavailableError,
    RateLimitedError,
    ValidationError,
)
from app.domains.integration.domain.catalog import default_registry
from app.domains.integration.infrastructure.connectors import HttpConnector

REGISTRY = default_registry()
SLACK = REGISTRY.get_connector("slack")


class FakeCredential:
    """Đủ hình để thoả `ProviderCredential`, không hơn."""

    connection_id = "conn_1"
    provider = "slack"
    access_token = "xoxb-bi-mat"  # noqa: S105 — token giả, chỉ tồn tại trong test
    expires_at: datetime | None = None


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Vá `httpx.AsyncClient`; trả về hộp để mỗi test đặt handler và đọc lại request."""
    box: dict[str, Any] = {"handler": None, "seen": []}

    def dispatch(request: httpx.Request) -> httpx.Response:
        box["seen"].append(request)
        return box["handler"](request)  # type: ignore[no-any-return]

    real = httpx.AsyncClient

    def fake(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return real(transport=httpx.MockTransport(dispatch), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake)
    return box


def replying(box: dict[str, Any], status: int = 200, **kwargs: Any) -> None:
    box["handler"] = lambda _request: httpx.Response(status, **kwargs)


async def call(tool_id: str, box: dict[str, Any], payload: dict[str, Any] | None = None) -> Any:
    return await HttpConnector().call(
        tool=REGISTRY.get_tool(tool_id),
        connector=SLACK,
        credential=FakeCredential(),
        payload=payload or {},
    )


# ─── Dựng request ────────────────────────────────────────────────────────────


async def test_token_di_vao_dung_header_ma_connector_khai(transport: dict[str, Any]) -> None:
    replying(transport, json={"ok": True, "channels": []})

    await call("slack.list_channels", transport)

    request = transport["seen"][0]
    assert request.headers["authorization"] == "Bearer xoxb-bi-mat"


async def test_dia_chi_den_tu_tai_lieu_khong_tu_input(transport: dict[str, Any]) -> None:
    """§38 — không tham số nào của người gọi lọt được vào URL.

    Người gọi có gửi kèm thứ trông như đường dẫn thì nó vẫn chỉ là tham số,
    không đổi được đích đến.
    """
    replying(transport, json={"ok": True, "channels": []})

    await call(
        "slack.list_channels",
        transport,
        {"limit": 5, "cursor": "https://ke-tan-cong.example/thu-thap"},
    )

    request = transport["seen"][0]
    assert str(request.url).startswith("https://slack.com/api/conversations.list")


async def test_tool_get_dong_goi_input_vao_query(transport: dict[str, Any]) -> None:
    replying(transport, json={"ok": True, "channels": []})

    await call("slack.list_channels", transport, {"limit": 5})

    assert transport["seen"][0].url.params["limit"] == "5"
    assert transport["seen"][0].method == "GET"


async def test_tool_post_dong_goi_input_vao_json(transport: dict[str, Any]) -> None:
    replying(transport, json={"ok": True, "channel": "C1", "ts": "1.2"})

    await call("slack.post_message", transport, {"channel": "C1", "text": "xin chào"})

    request = transport["seen"][0]
    assert request.method == "POST"
    assert b'"xin ch\\u00e0o"' in request.content or "xin chào".encode() in request.content


# ─── Chiếu output ────────────────────────────────────────────────────────────


async def test_chi_truong_duoc_khai_moi_di_ra_ngoai(transport: dict[str, Any]) -> None:
    """§33 — dữ liệu nhạy cảm phải được tối thiểu hoá.

    `users.list` của Slack trả về email, số điện thoại, múi giờ. Không trường
    nào trong số đó có tên trong `output_mapping`, nên chúng không đi ra.
    """
    replying(
        transport,
        json={
            "ok": True,
            "members": [
                {
                    "id": "U1",
                    "name": "an",
                    "is_bot": False,
                    "deleted": False,
                    "profile": {"real_name": "An Hoàng", "email": "an@acme.test", "phone": "0900"},
                    "tz": "Asia/Ho_Chi_Minh",
                }
            ],
        },
    )

    result = await call("slack.list_users", transport)

    member = result.output["members"][0]
    assert member == {
        "id": "U1",
        "name": "an",
        "real_name": "An Hoàng",
        "is_bot": False,
        "deleted": False,
    }
    assert "email" not in member
    assert "tz" not in member


async def test_chieu_duoc_duong_dan_long_nhau(transport: dict[str, Any]) -> None:
    replying(
        transport,
        json={"ok": True, "channels": [], "response_metadata": {"next_cursor": "trang-2"}},
    )

    result = await call("slack.list_channels", transport)

    assert result.output["next_cursor"] == "trang-2"


async def test_truong_vang_mat_bi_bo_han_khong_thanh_null(transport: dict[str, Any]) -> None:
    """Đặt `None` thì một giá trị rỗng trôi xuống hạ nguồn mà không ai biết.

    Bỏ hẳn thì bước validate output nói ra điều đó với thông điệp đúng.
    """
    replying(transport, json={"ok": True, "channels": []})

    result = await call("slack.list_channels", transport)

    assert "next_cursor" not in result.output


async def test_dung_tham_chieu_provider_tu_nhieu_manh(transport: dict[str, Any]) -> None:
    replying(transport, json={"ok": True, "channel": "C123", "ts": "1712345678.000100"})

    result = await call("slack.post_message", transport, {"channel": "C123", "text": "hi"})

    assert result.provider_reference == "C123:1712345678.000100"
    assert result.output == {"channel_id": "C123", "message_ts": "1712345678.000100"}


async def test_thieu_mot_manh_thi_khong_dung_tham_chieu_do_dang(
    transport: dict[str, Any],
) -> None:
    """Tham chiếu ghép dở còn tệ hơn không có — sổ idempotency sẽ cất lại nó."""
    replying(transport, json={"ok": True, "channel": "C123"})

    result = await call("slack.post_message", transport, {"channel": "C123", "text": "hi"})

    assert result.provider_reference is None


# ─── Quirk `ok: false` của Slack ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("ratelimited", RateLimitedError),
        ("service_unavailable", ProviderUnavailableError),
        ("internal_error", ProviderUnavailableError),
        ("token_revoked", ConnectionExpiredError),
        ("invalid_auth", ConnectionExpiredError),
        ("account_inactive", ConnectionExpiredError),
        ("missing_scope", MissingScopeError),
        ("restricted_action", AuthorizationError),
        ("channel_not_found", ValidationError),
        ("msg_too_long", ValidationError),
        ("not_in_channel", ValidationError),
    ],
)
async def test_moi_ma_loi_duoc_chuan_hoa_theo_bang_trong_manifest(
    transport: dict[str, Any], code: str, expected: type[Exception]
) -> None:
    """HTTP 200 kèm `ok: false` — thân phản hồi phải được đọc trước status code."""
    replying(transport, status=200, json={"ok": False, "error": code})

    with pytest.raises(expected):
        await call("slack.list_channels", transport)


async def test_ma_la_roi_ve_loi_vinh_vien(transport: dict[str, Any]) -> None:
    """Đoán nhầm 'vĩnh viễn' tốn một thao tác người dùng; đoán nhầm 'tạm thời'
    đốt quota trong vòng retry không bao giờ thành công."""
    replying(transport, json={"ok": False, "error": "mot_ma_slack_chua_tung_co"})

    with pytest.raises(ProviderPermanentError):
        await call("slack.list_channels", transport)


async def test_ok_vang_mat_khong_bi_coi_la_that_bai(transport: dict[str, Any]) -> None:
    """Chỉ `ok is False` mới là thất bại; vắng mặt thì không kết luận gì."""
    replying(transport, json={"channels": []})

    result = await call("slack.list_channels", transport)

    assert result.output["channels"] == []


# ─── Lỗi ở tầng HTTP ─────────────────────────────────────────────────────────


async def test_429_mang_theo_retry_after_cua_provider(transport: dict[str, Any]) -> None:
    replying(transport, status=429, json={}, headers={"retry-after": "42"})

    with pytest.raises(RateLimitedError) as caught:
        await call("slack.list_channels", transport)

    assert caught.value.retry_after_seconds == 42


async def test_ok_false_ratelimited_cung_mang_retry_after(transport: dict[str, Any]) -> None:
    """Slack gửi 429 thật, nhưng cũng có lúc gói lỗi vào thân — cả hai đường
    đều phải giữ lại con số của provider."""
    replying(
        transport,
        status=200,
        json={"ok": False, "error": "ratelimited"},
        headers={"retry-after": "7"},
    )

    with pytest.raises(RateLimitedError) as caught:
        await call("slack.list_channels", transport)

    assert caught.value.retry_after_seconds == 7


async def test_5xx_la_provider_khong_san_sang(transport: dict[str, Any]) -> None:
    replying(transport, status=503, json={})

    with pytest.raises(ProviderUnavailableError) as caught:
        await call("slack.list_channels", transport)

    assert caught.value.retryable


async def test_retry_after_dang_ngay_thang_thi_bo_qua(transport: dict[str, Any]) -> None:
    """`Retry-After` cho phép cả HTTP-date. Không parse được thì đừng làm hỏng
    lời gọi — backoff tính toán của tool vẫn còn đó."""
    replying(
        transport, status=429, json={}, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}
    )

    with pytest.raises(RateLimitedError) as caught:
        await call("slack.list_channels", transport)

    assert caught.value.retry_after_seconds is None


async def test_phan_hoi_khong_phai_json(transport: dict[str, Any]) -> None:
    transport["handler"] = lambda _r: httpx.Response(200, text="<html>502 Bad Gateway</html>")

    with pytest.raises(ProviderUnavailableError):
        await call("slack.list_channels", transport)


async def test_json_khong_phai_object(transport: dict[str, Any]) -> None:
    replying(transport, json=["mot", "hai"])

    with pytest.raises(ProviderUnavailableError):
        await call("slack.list_channels", transport)


async def test_qua_han_thanh_timeout_cua_nen_tang(transport: dict[str, Any]) -> None:
    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("qua han")

    transport["handler"] = timeout

    with pytest.raises(PlatformTimeoutError) as caught:
        await call("slack.list_channels", transport)

    assert caught.value.retryable


async def test_khong_noi_duoc_mang_thanh_provider_khong_san_sang(
    transport: dict[str, Any],
) -> None:
    def refused(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("khong noi duoc")

    transport["handler"] = refused

    with pytest.raises(ProviderUnavailableError):
        await call("slack.list_channels", transport)


# ─── Token không rò ra ───────────────────────────────────────────────────────


async def test_token_khong_xuat_hien_trong_thong_diep_loi(transport: dict[str, Any]) -> None:
    """§2.4 — một chuỗi lỗi đi thẳng vào log và có thể lên tới người dùng."""
    replying(transport, json={"ok": False, "error": "invalid_auth"})

    with pytest.raises(ConnectionExpiredError) as caught:
        await call("slack.list_channels", transport)

    rendered = f"{caught.value} {caught.value.to_payload()}"
    assert FakeCredential.access_token not in rendered


# ─── §6: không nhánh nào theo provider ───────────────────────────────────────


# ─── §6: không nhánh nào theo provider ───────────────────────────────────────


def test_driver_khong_co_mot_nhanh_nao_theo_provider() -> None:
    """§6 — Integration Plane không được là những nhánh điều kiện theo provider.

    Kiểm bằng cách đọc chính mã nguồn, vì không test hành vi nào bắt được
    khoảnh khắc ai đó thêm ``if provider == "slack"`` để xử lý nhanh một ca lẻ:
    hành vi vẫn đúng, chỉ kiến trúc là hỏng.

    Đọc bằng AST chứ không quét chuỗi. Docstring **được** nhắc tên provider —
    đó là văn xuôi giải thích, không phải nhánh. Thứ bị cấm là tên provider
    xuất hiện trong *mã*: một hằng chuỗi để so sánh, một tên biến, một thuộc
    tính.
    """
    import ast
    import inspect

    from app.domains.integration.infrastructure import connectors

    tree = ast.parse(inspect.getsource(connectors))

    docstrings = {
        doc
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        if (doc := ast.get_docstring(node, clean=False)) is not None
    }

    in_code: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in docstrings:
                in_code.append(node.value)
        elif isinstance(node, ast.Name):
            in_code.append(node.id)
        elif isinstance(node, ast.Attribute):
            in_code.append(node.attr)

    haystack = " ".join(in_code).lower()
    for provider in ("slack", "notion", "google", "github", "gmail"):
        assert provider not in haystack, (
            f"Mã của driver chung nhắc tới {provider!r}. Khác biệt của provider "
            "phải nằm trong tài liệu connector, không trong code (§6)."
        )
