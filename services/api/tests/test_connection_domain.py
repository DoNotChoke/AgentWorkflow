"""Quy tắc nghiệp vụ của Connection, không cần hạ tầng.

Trọng tâm ở đây là những thứ hỏng lặng lẽ: token lọt vào ``repr``, scope tách
sai vì provider dùng dấu phân cách khác, và trạng thái nào thì thao tác nào còn
hợp lệ.
"""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from app.core.errors import (
    ConnectionExpiredError,
    ErrorCategory,
    MissingScopeError,
    ValidationError,
)
from app.domains.connection.domain.entities import (
    REFRESH_SKEW,
    Connection,
    ConnectionStatus,
    OAuthTokens,
    default_display_name,
    ensure_can_disable,
    ensure_can_enable,
    ensure_not_terminal,
    ensure_scopes_requestable,
    ensure_usable,
    format_scopes,
    needs_refresh,
    normalize_display_name,
    parse_scopes,
)
from app.domains.connection.domain.providers import (
    GOOGLE,
    PROVIDERS,
    SLACK,
    build_authorize_url,
    get_provider,
    list_providers,
    new_pkce_pair,
    read_path,
)

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)


def make_connection(
    *,
    status: ConnectionStatus = ConnectionStatus.ACTIVE,
    scopes: frozenset[str] = frozenset({"chat:write"}),
    expires_at: datetime | None = None,
) -> Connection:
    return Connection(
        id="conn_1",
        organization_id="org_1",
        workspace_id="ws_1",
        provider="slack",
        display_name="Slack",
        status=status,
        granted_scopes=scopes,
        connected_by="usr_1",
        created_at=NOW,
        updated_at=NOW,
        expires_at=expires_at,
    )


# ─── Không để credential lọt ra ngoài ────────────────────────────────────────


def test_repr_cua_oauth_tokens_khong_chua_token() -> None:
    """``repr`` của dataclass in mọi trường — trừ khi bị ghi đè.

    Test này canh đúng chỗ đó. Dataclass bị in ra ở rất nhiều nơi ngoài tầm
    kiểm soát: assert của pytest, structlog, traceback của thư viện thứ ba.
    """
    tokens = OAuthTokens(
        access_token="xoxb-bi-mat-that",
        refresh_token="refresh-bi-mat-that",
        scopes=frozenset({"chat:write"}),
    )

    rendered = repr(tokens)

    assert "xoxb-bi-mat-that" not in rendered
    assert "refresh-bi-mat-that" not in rendered
    # Vẫn phải nói được điều hữu ích khi gỡ lỗi.
    assert "has_refresh_token=True" in rendered


def test_repr_cua_pkce_khong_chua_verifier() -> None:
    pair = new_pkce_pair()
    assert pair.verifier not in repr(pair)


def test_connection_khong_co_truong_nao_ten_giong_token() -> None:
    """Chặn từ gốc: entity này đi vào log và (Phase 3) đi vào trace.

    Một trường ``access_token`` lỡ tay thêm vào đây sẽ vô hiệu hoá §2.4 ngay ở
    tầng kiểu dữ liệu, trước khi contract import kịp lên tiếng.
    """
    fields = set(Connection.__dataclass_fields__)
    assert not {f for f in fields if "token" in f or "secret" in f or "credential" in f}


# ─── Scope ───────────────────────────────────────────────────────────────────


def test_parse_scopes_theo_dau_cach() -> None:
    assert parse_scopes("a b  c") == frozenset({"a", "b", "c"})


def test_parse_scopes_theo_dau_phay_cua_slack() -> None:
    """Slack trả scope ngăn bởi dấu phẩy, không phải dấu cách như RFC 6749."""
    assert parse_scopes("chat:write,channels:read", delimiter=",") == frozenset(
        {"chat:write", "channels:read"}
    )


def test_parse_scopes_rong() -> None:
    assert parse_scopes(None) == frozenset()
    assert parse_scopes("") == frozenset()


def test_format_scopes_on_dinh_theo_thu_tu() -> None:
    assert format_scopes({"b", "a"}) == "a b"
    assert format_scopes({"b", "a"}, delimiter=",") == "a,b"


def test_scope_di_qua_luu_tru_roi_ve_van_nguyen_ven() -> None:
    """Ghi bằng dấu cách rồi đọc lại — quy ước nội bộ phải khép kín."""
    original = frozenset({"https://www.googleapis.com/auth/drive.readonly", "openid"})
    assert parse_scopes(format_scopes(original)) == original


def test_ensure_scopes_requestable_tu_choi_tap_rong() -> None:
    with pytest.raises(ValidationError):
        ensure_scopes_requestable([])


def test_ensure_scopes_requestable_theo_charset_rfc_6749() -> None:
    """RFC 6749 §3.3: ASCII in được, trừ dấu cách, nháy kép và chéo ngược."""
    with pytest.raises(ValidationError):
        ensure_scopes_requestable(["chat write"])
    with pytest.raises(ValidationError):
        ensure_scopes_requestable(['chat"write'])
    with pytest.raises(ValidationError):
        ensure_scopes_requestable(["chat\nwrite"])
    with pytest.raises(ValidationError):
        ensure_scopes_requestable(["quyền-có-dấu"])


def test_ky_tu_url_trong_scope_van_hop_le() -> None:
    """``&`` và ``=`` là ký tự scope hợp lệ, không phải mưu đồ chèn tham số.

    Thứ ngăn chúng phá vỡ URL uỷ quyền là ``urlencode``, không phải hàm kiểm
    tra scope — nên kiểm tra không được từ chối chúng.
    """
    assert ensure_scopes_requestable(["a&b=c"]) == frozenset({"a&b=c"})

    url = build_authorize_url(
        SLACK,
        client_id="cid",
        redirect_uri="https://app.example.com/cb",
        scopes=frozenset({"a&redirect_uri=evil"}),
        state="s",
        pkce=None,
    )
    query = parse_qs(urlparse(url).query)
    # Đã bị mã hoá, nên nó là *giá trị* của `scope`, không thành tham số mới.
    assert query["scope"] == ["a&redirect_uri=evil"]
    assert query["redirect_uri"] == ["https://app.example.com/cb"]


def test_ensure_scopes_requestable_tu_choi_qua_nhieu() -> None:
    with pytest.raises(ValidationError):
        ensure_scopes_requestable([f"scope-{i}" for i in range(200)])


# ─── Tên hiển thị ────────────────────────────────────────────────────────────


def test_normalize_display_name_gop_khoang_trang() -> None:
    assert normalize_display_name("  Slack   cua  Acme ", fallback="x") == "Slack cua Acme"


def test_normalize_display_name_rong_thi_dung_fallback() -> None:
    assert normalize_display_name("   ", fallback="Slack") == "Slack"


def test_default_display_name_khong_co_ten_tai_khoan() -> None:
    assert default_display_name(provider_label="Slack", account_label=None) == "Slack"
    assert default_display_name(provider_label="Slack", account_label="Acme") == "Slack — Acme"


# ─── Hạn token ───────────────────────────────────────────────────────────────


def test_khong_het_han_thi_khong_bao_gio_can_lam_moi() -> None:
    """Bot token của Slack không có hạn — hỏi làm mới là câu hỏi vô nghĩa."""
    assert needs_refresh(make_connection(expires_at=None), now=NOW) is False


def test_con_han_dai_thi_chua_can_lam_moi() -> None:
    connection = make_connection(expires_at=NOW + timedelta(hours=1))
    assert needs_refresh(connection, now=NOW) is False


def test_sap_het_han_trong_khoang_dem_thi_da_phai_lam_moi() -> None:
    """Token còn sống nhưng sẽ chết giữa lúc request đang bay."""
    connection = make_connection(expires_at=NOW + REFRESH_SKEW - timedelta(seconds=1))
    assert needs_refresh(connection, now=NOW) is True


def test_da_het_han_thi_can_lam_moi() -> None:
    connection = make_connection(expires_at=NOW - timedelta(seconds=1))
    assert needs_refresh(connection, now=NOW) is True


# ─── Chuyển trạng thái ───────────────────────────────────────────────────────


def test_du_sau_trang_thai_theo_muc_6_4() -> None:
    assert {str(s) for s in ConnectionStatus} == {
        "active",
        "expired",
        "revoked",
        "missing_scope",
        "reconnect_required",
        "disabled",
    }


def test_ensure_usable_cho_qua_khi_active_va_du_quyen() -> None:
    ensure_usable(make_connection(), required_scopes={"chat:write"})


@pytest.mark.parametrize(
    "status",
    [
        ConnectionStatus.EXPIRED,
        ConnectionStatus.REVOKED,
        ConnectionStatus.MISSING_SCOPE,
        ConnectionStatus.RECONNECT_REQUIRED,
        ConnectionStatus.DISABLED,
    ],
)
def test_ensure_usable_chan_moi_trang_thai_khong_active(status: ConnectionStatus) -> None:
    with pytest.raises(ConnectionExpiredError) as exc:
        ensure_usable(make_connection(status=status))
    assert exc.value.details["status"] == str(status)


def test_loi_ket_noi_hong_khong_bao_gio_dang_retry() -> None:
    """Temporal đọc ``retryable`` để quyết định thử lại.

    Hết hạn hay thiếu quyền thì thử lại bao nhiêu lần cũng vô ích — phải có
    người vào bấm kết nối lại. Đánh dấu sai ở đây sẽ khiến Activity quay vòng
    tới lúc cạn lượt retry rồi mới báo hỏng.
    """
    with pytest.raises(ConnectionExpiredError) as expired:
        ensure_usable(make_connection(status=ConnectionStatus.EXPIRED))
    assert expired.value.retryable is False

    with pytest.raises(MissingScopeError) as missing:
        ensure_usable(make_connection(), required_scopes={"files:write"})
    assert missing.value.retryable is False
    assert missing.value.category is ErrorCategory.MISSING_SCOPE


def test_ensure_usable_bao_dung_quyen_con_thieu() -> None:
    with pytest.raises(MissingScopeError) as exc:
        ensure_usable(
            make_connection(scopes=frozenset({"chat:write"})),
            required_scopes={"chat:write", "files:read", "files:write"},
        )
    assert exc.value.details["missing_scopes"] == ["files:read", "files:write"]


def test_da_thu_hoi_la_trang_thai_ket() -> None:
    with pytest.raises(ValidationError):
        ensure_not_terminal(make_connection(status=ConnectionStatus.REVOKED))


def test_khong_tam_dung_hai_lan() -> None:
    with pytest.raises(ValidationError):
        ensure_can_disable(make_connection(status=ConnectionStatus.DISABLED))


def test_chi_bat_lai_duoc_ket_noi_dang_tam_dung() -> None:
    """Hết hạn không phải thứ một nút bấm chữa được — phải uỷ quyền lại."""
    ensure_can_enable(make_connection(status=ConnectionStatus.DISABLED))

    for broken in (ConnectionStatus.EXPIRED, ConnectionStatus.RECONNECT_REQUIRED):
        with pytest.raises(ValidationError):
            ensure_can_enable(make_connection(status=broken))


# ─── Danh mục provider ───────────────────────────────────────────────────────


def test_get_provider_khong_biet_thi_bao_loi_kem_danh_sach() -> None:
    with pytest.raises(ValidationError) as exc:
        get_provider("khong-ton-tai")
    assert "slack" in exc.value.details["supported"]


def test_get_provider_khong_phan_biet_hoa_thuong() -> None:
    assert get_provider("SLACK").id == "slack"


def test_id_cua_descriptor_khop_voi_khoa_trong_danh_muc() -> None:
    """Lệch nhau thì ``get_provider`` trả về descriptor của provider khác."""
    for key, descriptor in PROVIDERS.items():
        assert key == descriptor.id


def test_moi_provider_deu_co_scope_mac_dinh() -> None:
    """Không có scope mặc định thì nút "Connect" không bấm được."""
    for descriptor in list_providers():
        assert descriptor.default_scopes


def test_slack_dung_dau_phay_va_khong_lam_moi_duoc() -> None:
    assert SLACK.scope_delimiter == ","
    assert SLACK.supports_refresh is False
    # Danh tính team nằm sẵn trong phản hồi token, không phải đi hỏi thêm.
    assert SLACK.reads_identity_from_token_response is True


def test_google_xin_refresh_token_tuong_minh() -> None:
    """Thiếu ``access_type=offline`` thì kết nối chết sau đúng một giờ."""
    params = dict(GOOGLE.extra_authorize_params)
    assert params["access_type"] == "offline"
    assert GOOGLE.supports_pkce is True
    assert GOOGLE.reads_identity_from_token_response is False


# ─── PKCE ────────────────────────────────────────────────────────────────────


def test_pkce_challenge_dung_cong_thuc_s256() -> None:
    """RFC 7636 §4.2. Sai chỗ này thì provider từ chối ở bước đổi token."""
    pair = new_pkce_pair()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(pair.verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert pair.challenge == expected
    assert pair.method == "S256"


def test_pkce_verifier_dai_dung_khoang_rfc_cho_phep() -> None:
    verifier = new_pkce_pair().verifier
    assert 43 <= len(verifier) <= 128


def test_moi_lan_sinh_pkce_ra_gia_tri_khac_nhau() -> None:
    assert new_pkce_pair().verifier != new_pkce_pair().verifier


# ─── URL uỷ quyền ────────────────────────────────────────────────────────────


def test_build_authorize_url_du_tham_so_bat_buoc() -> None:
    url = build_authorize_url(
        SLACK,
        client_id="cid",
        redirect_uri="https://app.example.com/v1/connections/oauth/callback",
        scopes=frozenset({"chat:write", "channels:read"}),
        state="state-abc",
        pkce=None,
    )

    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == SLACK.authorize_url
    assert query["client_id"] == ["cid"]
    assert query["response_type"] == ["code"]
    assert query["state"] == ["state-abc"]
    # Đúng dấu phân cách của Slack, không phải dấu cách của RFC.
    assert query["scope"] == ["channels:read,chat:write"]


def test_build_authorize_url_kem_tham_so_rieng_cua_provider() -> None:
    url = build_authorize_url(
        GOOGLE,
        client_id="cid",
        redirect_uri="https://app.example.com/cb",
        scopes=frozenset({"openid"}),
        state="s",
        pkce=new_pkce_pair(),
    )
    query = parse_qs(urlparse(url).query)

    assert query["access_type"] == ["offline"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"]


def test_build_authorize_url_khong_gui_pkce_toi_provider_khong_ho_tro() -> None:
    with pytest.raises(ValidationError):
        build_authorize_url(
            SLACK,
            client_id="cid",
            redirect_uri="https://app.example.com/cb",
            scopes=frozenset({"chat:write"}),
            state="s",
            pkce=new_pkce_pair(),
        )


# ─── Đọc danh tính theo đường dẫn khai báo ───────────────────────────────────


def test_read_path_doc_duoc_gia_tri_long_nhau() -> None:
    assert read_path({"team": {"id": "T123"}}, ("team", "id")) == "T123"


def test_read_path_thieu_duong_dan_thi_tra_none() -> None:
    """Provider đổi hình dạng phản hồi thì kết nối vẫn phải dùng được.

    Danh tính tài khoản chỉ để hiển thị, nên nó không được là lý do làm hỏng cả
    vòng uỷ quyền.
    """
    assert read_path({"team": {}}, ("team", "id")) is None
    assert read_path({}, ("team", "id")) is None
    assert read_path({"team": "khong-phai-object"}, ("team", "id")) is None
    assert read_path({"team": {"id": "T1"}}, ()) is None


def test_read_path_ep_so_ve_chuoi() -> None:
    assert read_path({"sub": 12345}, ("sub",)) == "12345"
