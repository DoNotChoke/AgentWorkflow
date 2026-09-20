"""Sandbox chạy code do LLM viết.

Phần lớn file này không phải test "code chạy đúng" mà là **test tấn công**: mỗi
đường thoát đã biết một dòng. Một sandbox hỏng vẫn chạy đúng mọi ví dụ hợp lệ, nên
tập test hợp lệ không nói được gì về việc nó có an toàn hay không.
"""

from __future__ import annotations

import pytest

from app.core.sandbox import Limits, SandboxError, memory_cap_supported, run

FAST = Limits(timeout_seconds=5.0)


# ─── Chạy được ───────────────────────────────────────────────────────────────


def test_dem_phan_tu() -> None:
    """Động cơ của cả WP-C: LLM đếm sai, code đếm đúng."""
    assert run("return len(args['messages'])", {"messages": [1, 2, 3]}, FAST).value == 3


def test_nan_hinh_dang_giua_hai_tool() -> None:
    """Slack trả `{ts, user, text}`; Notion đòi `{type, text}`."""
    messages = [{"ts": "1", "user": "u1", "text": "một"}, {"ts": "2", "user": "u2", "text": "hai"}]
    result = run(
        "return [{'type': 'bulleted_list_item', 'text': m['text']} for m in args['messages']]",
        {"messages": messages},
        FAST,
    )
    assert result.value == [
        {"type": "bulleted_list_item", "text": "một"},
        {"type": "bulleted_list_item", "text": "hai"},
    ]


def test_f_string_dung_duoc() -> None:
    """Ghép chuỗi là lý do không cần một node `merge` riêng."""
    result = run("return f\"Có {len(args['m'])} tin nhắn\"", {"m": [1, 2]}, FAST)
    assert result.value == "Có 2 tin nhắn"


def test_ham_cuc_bo_va_sap_xep() -> None:
    code = """
def khoa(m):
    return m['ts']
return [m['text'] for m in sorted(args['m'], key=khoa)]
"""
    assert run(code, {"m": [{"ts": "2", "text": "b"}, {"ts": "1", "text": "a"}]}, FAST).value == [
        "a",
        "b",
    ]


def test_moi_lan_chay_la_mot_the_gioi_rieng() -> None:
    """Không có state rò từ lần chạy trước sang lần sau."""
    run("x = 1\nreturn x", {}, FAST)
    with pytest.raises(SandboxError, match="NameError"):
        run("return x", {}, FAST)


def test_ket_qua_bao_dung_trang_thai_tran_bo_nho() -> None:
    """Một tầng bảo vệ vắng mặt phải nhìn thấy được, không được im lặng."""
    assert run("return 1", {}, FAST).memory_capped is memory_cap_supported()


# ─── Không chạy được: từng đường thoát ───────────────────────────────────────


@pytest.mark.parametrize(
    ("code", "duong_thoat"),
    [
        ("import os\nreturn 1", "import thẳng"),
        ("from os import getcwd\nreturn 1", "import from"),
        ("return __import__('os').getcwd()", "__import__"),
        ("return ().__class__.__bases__[0].__subclasses__()", "leo cây lớp"),
        ("return args.__class__", "dunder trên dữ liệu"),
        ("return eval('1+1')", "eval"),
        ("return exec('x=1')", "exec"),
        ("return compile('1', '<s>', 'eval')", "compile"),
        ("return open('/etc/passwd').read()", "đọc file"),
        ("return getattr(args, 'keys')()", "getattr động"),
        ("return globals()", "globals"),
        ("return locals()", "locals"),
        ("return vars(args)", "vars"),
        ("return dir(args)", "dir"),
        ("return type(args)", "type"),
        ("while True:\n    pass", "vòng while"),
        ("class A:\n    pass\nreturn 1", "định nghĩa lớp"),
        ("global x\nreturn 1", "global"),
        ("try:\n    return 1\nexcept Exception:\n    return 2", "nuốt lỗi"),
        ("raise Exception('x')", "tự ném lỗi"),
        ("with open('x') as f:\n    return 1", "with"),
        ("yield 1", "generator"),
        ("_x = 1\nreturn _x", "tên gạch dưới"),
        ("def _f():\n    return 1\nreturn _f()", "hàm gạch dưới"),
        ("return [x for x in ().__doc__]", "dunder trong comprehension"),
    ],
)
def test_duong_thoat_bi_chan(code: str, duong_thoat: str) -> None:
    with pytest.raises(SandboxError):
        run(code, {"messages": []}, FAST)


def test_ten_ngoai_danh_sach_builtin_khong_phan_giai_duoc() -> None:
    """Guard không biết `os` là gì; thứ chặn nó là `__builtins__` bị thu hẹp."""
    with pytest.raises(SandboxError, match="NameError"):
        run("return os.getcwd()", {}, FAST)


def test_cu_phap_sai_bao_la_cu_phap_sai() -> None:
    with pytest.raises(SandboxError, match="parse"):
        run("return (", {}, FAST)


# ─── Trần ────────────────────────────────────────────────────────────────────


def test_chay_qua_han_thi_bi_dung() -> None:
    """Guard cấm `while`, nhưng `for` trên một range khổng lồ thì hợp lệ — đây là
    lý do phải có lớp phòng thủ thứ hai."""
    with pytest.raises(SandboxError, match="quá"):
        run("for i in range(10**12):\n    pass\nreturn 1", {}, Limits(timeout_seconds=1.0))


def test_ket_qua_qua_lon_bi_tu_choi() -> None:
    with pytest.raises(SandboxError, match="trần kích thước"):
        run("return 'x' * 5000", {}, Limits(timeout_seconds=5.0, output_bytes=1000))


def test_code_qua_dai_bi_tu_choi_truoc_khi_chay() -> None:
    with pytest.raises(SandboxError, match="trần"):
        run("return 1" + " " * 5000, {}, Limits(code_bytes=100))


def test_ket_qua_phai_la_json() -> None:
    with pytest.raises(SandboxError, match="JSON"):
        run("return set([1, 2])", {}, FAST)


def test_ket_qua_khong_duoc_mang_nan() -> None:
    """`NaN` không phải JSON hợp lệ; để lọt thì nó vỡ ở tầng khác, xa chỗ sinh ra."""
    with pytest.raises(SandboxError, match="JSON"):
        run("return float('nan')", {}, FAST)


def test_loi_cua_code_noi_len_thanh_loi_cua_node() -> None:
    with pytest.raises(SandboxError, match="ZeroDivisionError"):
        run("return 1 / 0", {}, FAST)


@pytest.mark.skipif(not memory_cap_supported(), reason="Windows không có resource.setrlimit")
def test_ngon_bo_nho_bi_chan() -> None:
    with pytest.raises(SandboxError):
        run("return [0] * (10 ** 10)", {}, Limits(timeout_seconds=10.0))
