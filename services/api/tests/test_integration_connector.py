"""Driver HTTP chung, chạy trên transport giả của httpx.

Không gói tin nào rời khỏi tiến trình. Cùng hình với
`test_connection_oauth_client.py` — vá thẳng `httpx.AsyncClient`, không thêm
`respx`.

Bốn tool Slack thật được dùng làm ca kiểm: chúng là bằng chứng rằng driver này
chạy được một provider có thật mà không cần một dòng code nào riêng cho nó.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from typing import Any

import httpx
import pytest

from app.core.errors import (
    AppError,
    AuthorizationError,
    ConnectionExpiredError,
    Delivery,
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
NOTION = REGISTRY.get_connector("notion")


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


# ─── Yêu cầu đã tới provider chưa ────────────────────────────────────────────


def raising(box: dict[str, Any], error: Exception) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise error

    box["handler"] = handler


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectTimeout("qua han"),
        httpx.PoolTimeout("het connection"),
        httpx.ConnectError("tu choi"),
    ],
)
async def test_loi_truoc_khi_ket_noi_la_chac_chan_chua_gui(
    transport: dict[str, Any], error: Exception
) -> None:
    raising(transport, error)

    with pytest.raises((PlatformTimeoutError, ProviderUnavailableError)) as caught:
        await call("slack.post_message", transport, {"channel": "C123", "text": "hi"})

    assert caught.value.delivery is Delivery.NOT_SENT


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ReadTimeout("cho phan hoi qua lau"), PlatformTimeoutError),
        (httpx.WriteTimeout("gui cham"), PlatformTimeoutError),
        (httpx.RemoteProtocolError("server ngat giua chung"), ProviderUnavailableError),
        (httpx.ReadError("mat ket noi khi doc"), ProviderUnavailableError),
    ],
)
async def test_loi_sau_khi_da_gui_la_khong_ro(
    transport: dict[str, Any], error: Exception, expected: type[AppError]
) -> None:
    """Slack có thể đã đăng tin trước khi phản hồi bị lỡ."""
    raising(transport, error)

    with pytest.raises(expected) as caught:
        await call("slack.post_message", transport, {"channel": "C123", "text": "hi"})

    assert caught.value.delivery is Delivery.UNKNOWN


async def test_5xx_la_khong_ro(transport: dict[str, Any]) -> None:
    replying(transport, status=502, json={})

    with pytest.raises(ProviderUnavailableError) as caught:
        await call("slack.post_message", transport, {"channel": "C123", "text": "hi"})

    assert caught.value.delivery is Delivery.UNKNOWN


async def test_429_la_chua_gui(transport: dict[str, Any]) -> None:
    replying(transport, status=429, json={})

    with pytest.raises(RateLimitedError) as caught:
        await call("slack.post_message", transport, {"channel": "C123", "text": "hi"})

    assert caught.value.delivery is Delivery.NOT_SENT


async def test_ok_false_ratelimited_la_chua_gui(transport: dict[str, Any]) -> None:
    replying(transport, json={"ok": False, "error": "ratelimited"})

    with pytest.raises(RateLimitedError) as caught:
        await call("slack.post_message", transport, {"channel": "C123", "text": "hi"})

    assert caught.value.delivery is Delivery.NOT_SENT


@pytest.mark.parametrize("code", ["internal_error", "fatal_error", "request_timeout"])
async def test_ok_false_loi_tam_thoi_cua_provider_la_khong_ro(
    transport: dict[str, Any], code: str
) -> None:
    replying(transport, json={"ok": False, "error": code})

    with pytest.raises(ProviderUnavailableError) as caught:
        await call("slack.post_message", transport, {"channel": "C123", "text": "hi"})

    assert caught.value.delivery is Delivery.UNKNOWN


async def test_200_khong_phai_json_la_khong_ro(transport: dict[str, Any]) -> None:
    transport["handler"] = lambda _r: httpx.Response(200, text="<html>ok</html>")

    with pytest.raises(ProviderUnavailableError) as caught:
        await call("slack.post_message", transport, {"channel": "C123", "text": "hi"})

    assert caught.value.delivery is Delivery.UNKNOWN


async def test_loi_vinh_vien_khong_mang_delivery(transport: dict[str, Any]) -> None:
    replying(transport, json={"ok": False, "error": "channel_not_found"})

    with pytest.raises(ValidationError) as caught:
        await call("slack.post_message", transport, {"channel": "C123", "text": "hi"})

    assert caught.value.delivery is None


# ─── Token không rò ra ───────────────────────────────────────────────────────


async def test_token_khong_xuat_hien_trong_thong_diep_loi(transport: dict[str, Any]) -> None:
    """§2.4 — một chuỗi lỗi đi thẳng vào log và có thể lên tới người dùng."""
    replying(transport, json={"ok": False, "error": "invalid_auth"})

    with pytest.raises(ConnectionExpiredError) as caught:
        await call("slack.list_channels", transport)

    rendered = f"{caught.value} {caught.value.to_payload()}"
    assert FakeCredential.access_token not in rendered


# ─── Khác biệt của provider, khai bằng tài liệu ──────────────────────────────


async def call_notion(
    box: dict[str, Any], tool_id: str, payload: dict[str, Any] | None = None
) -> Any:
    return await HttpConnector().call(
        tool=REGISTRY.get_tool(tool_id),
        connector=NOTION,
        credential=FakeCredential(),
        payload=payload or {},
    )


async def test_header_co_dinh_cua_connector_di_kem_moi_loi_goi(
    transport: dict[str, Any],
) -> None:
    """Notion trả 400 `missing_version` cho request thiếu `Notion-Version`.

    Không có chỗ khai header cố định thì lựa chọn còn lại là một nhánh
    `if provider == ...` trong driver — thứ §6 cấm.
    """
    replying(transport, json={"object": "list", "results": []})

    await call_notion(transport, "notion.search")

    request = transport["seen"][0]
    assert request.headers["notion-version"] == "2022-06-28"
    assert request.headers["authorization"] == "Bearer xoxb-bi-mat"


async def test_header_co_dinh_khong_cuop_duoc_cho_cua_credential(
    transport: dict[str, Any],
) -> None:
    """Tài liệu connector là dữ liệu, và dữ liệu không được thay token.

    Đây là lý do driver gắn header auth SAU `static_headers` chứ không trộn hai
    dict theo thứ tự ngược lại.
    """
    replying(transport, json={"object": "list", "results": []})
    doctored = replace(NOTION, static_headers={"authorization": "Bearer token-ke-tan-cong"})

    await HttpConnector().call(
        tool=REGISTRY.get_tool("notion.search"),
        connector=doctored,
        credential=FakeCredential(),
        payload={},
    )

    assert transport["seen"][0].headers["authorization"] == "Bearer xoxb-bi-mat"


async def test_envelope_doc_duoc_provider_bao_loi_bang_object_thay_vi_co_boolean(
    transport: dict[str, Any],
) -> None:
    """Notion không có `ok: false`; nó đổi hẳn `object` thành `"error"`.

    Đọc được mã trong thân mới phân biệt được `object_not_found` với
    `restricted_resource` — hai thứ cùng ra HTTP 404 nhưng một cái bảo agent
    thử id khác, cái kia bảo người dùng đi chia sẻ trang.
    """
    replying(
        transport,
        404,
        json={"object": "error", "status": 404, "code": "object_not_found", "message": "..."},
    )

    with pytest.raises(ValidationError) as caught:
        await call_notion(transport, "notion.search", {"query": "ghi chú"})

    assert caught.value.details["provider_code"] == "object_not_found"


async def test_envelope_khong_doc_nham_phan_hoi_thanh_cong_thanh_loi(
    transport: dict[str, Any],
) -> None:
    """`object` của một phản hồi tốt là `"list"` hay `"page"` — chỉ `"error"`
    mới là thất bại. So sánh với `false` như envelope của Slack thì mọi phản
    hồi Notion đều lọt qua, kể cả những cái hỏng."""
    replying(
        transport,
        json={"object": "list", "results": [{"id": "p1", "object": "page"}], "has_more": False},
    )

    result = await call_notion(transport, "notion.search")

    assert result.output["results"] == [{"id": "p1", "object": "page"}]


async def test_tim_kiem_tra_ve_tieu_de_cua_ca_trang_lan_database(
    transport: dict[str, Any],
) -> None:
    """Thiếu tiêu đề thì "tìm trang tên AWS" không làm được bằng một lời gọi —
    Agent phải đoán từ đuôi `url`. Trang để tiêu đề dưới `properties.title`,
    database để ngay ở gốc."""
    replying(
        transport,
        json={
            "object": "list",
            "results": [
                {
                    "id": "p1",
                    "object": "page",
                    "properties": {
                        "title": {"title": [{"plain_text": "Ghi chú "}, {"plain_text": "AWS"}]}
                    },
                },
                {"id": "d1", "object": "database", "title": [{"plain_text": "Việc cần làm"}]},
                {"id": "r1", "object": "page", "properties": {"Tên": {"title": []}}},
            ],
            "has_more": False,
        },
    )

    result = await call_notion(transport, "notion.search")

    assert result.output["results"] == [
        {"id": "p1", "object": "page", "title": ["Ghi chú ", "AWS"]},
        {"id": "d1", "object": "database", "title": ["Việc cần làm"]},
        {"id": "r1", "object": "page"},
    ]


# ─── Tham số đường dẫn (ADR-0005) ────────────────────────────────────────────

TRANG = "1f0a2b3c4d5e6f708192a3b4c5d6e7f8"


async def test_tham_so_di_vao_duong_dan_va_khong_di_them_lan_nua(
    transport: dict[str, Any],
) -> None:
    """Đã điền vào URL thì không được điền tiếp vào query — Notion coi một
    trường lạ trên query string là lỗi và từ chối cả lời gọi."""
    replying(transport, json={"object": "page", "id": TRANG})

    await call_notion(transport, "notion.get_page", {"page_id": TRANG})

    url = transport["seen"][0].url
    assert str(url) == f"https://api.notion.com/v1/pages/{TRANG}"
    assert not url.params


async def test_tham_so_duong_dan_bi_loai_khoi_than_request(transport: dict[str, Any]) -> None:
    replying(transport, json={"object": "list", "results": []})

    await call_notion(transport, "notion.query_database", {"database_id": TRANG, "page_size": 10})

    request = transport["seen"][0]
    assert str(request.url) == f"https://api.notion.com/v1/databases/{TRANG}/query"
    assert json.loads(request.content) == {"page_size": 10}


async def test_gia_tri_thu_dich_khong_thoat_duoc_khoi_mot_segment(
    transport: dict[str, Any],
) -> None:
    """ADR-0005 lớp một — ranh giới SSRF thật, và nó nằm ở đây.

    Driver nằm *dưới* tầng validate của Gateway, nên nó phải tự đứng vững:
    `pattern` trong `input_schema` là ràng buộc thiết kế, không phải hàng rào.
    Đổi `quote(..., safe="")` thành `safe="/"` là test này đỏ.
    """
    replying(transport, json={"object": "page", "id": "x"})

    await call_notion(transport, "notion.get_page", {"page_id": "../../oauth/token"})

    url = str(transport["seen"][0].url)
    assert url.startswith("https://api.notion.com/v1/pages/")
    assert "/oauth/token" not in url
    assert "%2F" in url


async def test_thieu_tham_so_duong_dan_thi_hong_o_tang_minh(transport: dict[str, Any]) -> None:
    """Không có nó thì `fill_path` ném `KeyError` — một lỗi 500 không nói được
    thiếu cái gì, cho một tài liệu tool khai sai."""
    replying(transport, json={})

    with pytest.raises(ValidationError) as caught:
        await call_notion(transport, "notion.get_page", {})

    assert caught.value.details["field"] == "page_id"


# ─── Chiếu output: qua mảng, và qua union có discriminator ───────────────────


async def test_wildcard_gom_gia_tri_nam_trong_mang(transport: dict[str, Any]) -> None:
    """Tiêu đề Notion bị cắt thành nhiều đoạn theo định dạng chữ. Lấy đoạn đầu
    là mất phần còn lại; lấy nguyên mảng là trả về cả khối annotation."""
    replying(
        transport,
        json={
            "object": "page",
            "id": TRANG,
            "properties": {
                "title": {
                    "title": [
                        {"plain_text": "Ghi chú ", "annotations": {"bold": False}},
                        {"plain_text": "tuần này", "annotations": {"bold": True}},
                    ]
                }
            },
        },
    )

    result = await call_notion(transport, "notion.get_page", {"page_id": TRANG})

    assert result.output["title"] == ["Ghi chú ", "tuần này"]


async def test_any_of_doc_duoc_union_co_discriminator(transport: dict[str, Any]) -> None:
    """Notion đặt chữ của khối dưới khoá trùng với `type` của chính khối đó,
    nên không đường tĩnh nào đúng cho mọi khối. Khối không mang chữ thì trường
    `text` vắng mặt, chứ không phải rỗng — `additionalProperties: false` của
    output_schema sẽ bắt được nếu chiếu ra thứ không khai báo."""
    replying(
        transport,
        json={
            "object": "list",
            "results": [
                {
                    "id": "b1",
                    "type": "paragraph",
                    "has_children": False,
                    "paragraph": {"rich_text": [{"plain_text": "Đoạn văn."}]},
                },
                {
                    "id": "b2",
                    "type": "heading_2",
                    "has_children": False,
                    "heading_2": {"rich_text": [{"plain_text": "Tiêu đề phụ"}]},
                },
                {"id": "b3", "type": "divider", "has_children": False, "divider": {}},
            ],
            "has_more": False,
        },
    )

    result = await call_notion(transport, "notion.get_block_children", {"block_id": TRANG})

    assert result.output["results"] == [
        {"id": "b1", "type": "paragraph", "has_children": False, "text": ["Đoạn văn."]},
        {"id": "b2", "type": "heading_2", "has_children": False, "text": ["Tiêu đề phụ"]},
        {"id": "b3", "type": "divider", "has_children": False},
    ]


# ─── Dựng thân request từ template ───────────────────────────────────────────


async def test_template_dung_cay_ma_provider_doi_tu_input_phang(
    transport: dict[str, Any],
) -> None:
    """Đây là toàn bộ lý do `input_mapping` tồn tại.

    Input mà LLM viết được là ba trường phẳng. Thứ Notion đòi là bốn tầng lồng
    nhau. Không có tầng dịch thì `input_schema` phải mang đúng cái cây kia, và
    tool trở thành thứ gõ sai nhiều hơn gõ đúng.
    """
    replying(transport, json={"object": "page", "id": "p1", "url": "https://notion.so/p1"})

    await call_notion(
        transport,
        "notion.create_page",
        {
            "parent_page_id": TRANG,
            "title": "Ghi chú tuần",
            "blocks": [{"type": "paragraph", "text": "Dòng một."}],
        },
    )

    assert json.loads(transport["seen"][0].content) == {
        "parent": {"type": "page_id", "page_id": TRANG},
        "properties": {"title": {"title": [{"text": {"content": "Ghi chú tuần"}}]}},
        "children": [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"text": {"content": "Dòng một."}}]},
            }
        ],
    }


async def test_khoa_dong_phu_moi_loai_khoi_bang_mot_template(
    transport: dict[str, Any],
) -> None:
    """Notion để nội dung khối dưới khoá **trùng với `type` của chính khối đó**.

    Không có khoá động thì mỗi loại khối cần một nhánh, và một nhánh theo dữ
    liệu trong tài liệu là bước đầu tiên biến template thành ngôn ngữ. Đây là
    tra cứu: một template, mọi loại khối.

    `checked` chỉ có nghĩa với `to_do`; nó vắng mặt ở những khối khác nhờ đúng
    luật "khoá không có giá trị thì biến mất".
    """
    replying(transport, json={"object": "list", "results": []})

    await call_notion(
        transport,
        "notion.append_blocks",
        {
            "block_id": TRANG,
            "blocks": [
                {"type": "heading_2", "text": "Việc tuần này"},
                {"type": "bulleted_list_item", "text": "Gạch đầu dòng"},
                {"type": "numbered_list_item", "text": "Đánh số"},
                {"type": "toggle", "text": "Gập được"},
                {"type": "to_do", "text": "Chưa xong", "checked": False},
            ],
        },
    )

    assert json.loads(transport["seen"][0].content)["children"] == [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"text": {"content": "Việc tuần này"}}]},
        },
        {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"text": {"content": "Gạch đầu dòng"}}]},
        },
        {
            "object": "block",
            "type": "numbered_list_item",
            "numbered_list_item": {"rich_text": [{"text": {"content": "Đánh số"}}]},
        },
        {
            "object": "block",
            "type": "toggle",
            "toggle": {"rich_text": [{"text": {"content": "Gập được"}}]},
        },
        {
            "object": "block",
            "type": "to_do",
            "to_do": {"rich_text": [{"text": {"content": "Chưa xong"}}], "checked": False},
        },
    ]


async def test_truong_tuy_chon_vang_mat_khoi_than_thay_vi_thanh_null(
    transport: dict[str, Any],
) -> None:
    """`null` nói với provider "đặt nó rỗng"; vắng mặt nói "tôi không nhắc tới
    nó". Với một tool tạo trang thì hai câu đó khác nhau."""
    replying(transport, json={"object": "page", "id": "p1"})

    await call_notion(
        transport, "notion.create_page", {"parent_page_id": TRANG, "title": "Chỉ tiêu đề"}
    )

    assert "children" not in json.loads(transport["seen"][0].content)


async def test_method_cua_tai_lieu_duoc_ton_trong(transport: dict[str, Any]) -> None:
    """Notion dùng PATCH để viết thêm vào cuối một khối. Gửi POST tới đó là 405."""
    replying(transport, json={"object": "list", "results": [{"id": "b1"}, {"id": "b2"}]})

    result = await call_notion(
        transport,
        "notion.append_blocks",
        {"block_id": TRANG, "blocks": [{"type": "paragraph", "text": "a"}]},
    )

    request = transport["seen"][0]
    assert request.method == "PATCH"
    assert str(request.url) == f"https://api.notion.com/v1/blocks/{TRANG}/children"
    # `*` ở đầu một `path` thường: gom id của từng khối vừa tạo.
    assert result.output["block_ids"] == ["b1", "b2"]


async def test_tham_so_duong_dan_khong_lot_vao_than_da_dung_template(
    transport: dict[str, Any],
) -> None:
    """`block_id` đã nằm trong URL. Nó cũng không có chỗ nào trong template —
    lọt vào thân thì Notion từ chối vì một khoá lạ."""
    replying(transport, json={"object": "list", "results": []})

    await call_notion(
        transport,
        "notion.append_blocks",
        {"block_id": TRANG, "blocks": [{"type": "paragraph", "text": "a"}]},
    )

    assert set(json.loads(transport["seen"][0].content)) == {"children"}


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
