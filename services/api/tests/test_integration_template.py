"""Template dựng thân request — `domain/template.py`.

Mặt gương của `projection.py`, và cùng một loại rủi ro: nó chạy trên dữ liệu
người gọi gửi lên, nên mọi ca lệch phải có câu trả lời xác định thay vì một
`KeyError` giữa đường.
"""

from __future__ import annotations

from app.domains.integration.domain.template import (
    key_placeholders,
    placeholders,
    render_body,
)

DOAN = {
    "object": "block",
    "type": "paragraph",
    "paragraph": {"rich_text": [{"text": {"content": "{{item}}"}}]},
}


def test_thay_gia_tri_theo_dung_kieu_cua_no() -> None:
    """Placeholder mang cả giá trị, không phải chuỗi hoá nó: một số vẫn là số."""
    rendered = render_body(
        {"page": "{{name}}", "size": "{{count}}", "on": "{{flag}}"},
        {"name": "Ghi chú", "count": 7, "flag": True},
    )

    assert rendered == {"page": "Ghi chú", "size": 7, "on": True}


def test_chuoi_khong_phai_placeholder_la_hang() -> None:
    """Chỉ khớp khi placeholder chiếm trọn chuỗi. Nội suy giữa chuỗi bị bỏ có
    chủ đích, nên một chuỗi mang dấu ngoặc kép phải đi qua nguyên vẹn."""
    rendered = render_body(
        {"a": "page_id", "b": "xin chào {{name}}", "c": "{{Name}}", "d": "{{ name }}"},
        {"name": "An"},
    )

    assert rendered == {
        "a": "page_id",
        "b": "xin chào {{name}}",
        "c": "{{Name}}",
        "d": "{{ name }}",
    }


def test_khoa_khong_co_gia_tri_bien_mat_khoi_than() -> None:
    rendered = render_body({"title": "{{title}}", "icon": "{{icon}}"}, {"title": "T"})

    assert rendered == {"title": "T"}


def test_khoa_mang_gia_tri_none_van_o_lai() -> None:
    """`None` là một giá trị người gọi cấp, không phải sự vắng mặt — và với
    một số provider nó có nghĩa "xoá trường này"."""
    rendered = render_body({"icon": "{{icon}}"}, {"icon": None})

    assert rendered == {"icon": None}


def test_object_rong_sau_khi_bo_khoa_van_o_lai() -> None:
    """Vắng khoá con không kéo theo vắng khoá cha: provider phân biệt được
    "không gửi parent" với "gửi parent rỗng", và đoán hộ là sai."""
    rendered = render_body({"parent": {"page_id": "{{missing}}"}}, {})

    assert rendered == {"parent": {}}


def test_for_each_lap_template_tren_tung_phan_tu() -> None:
    rendered = render_body(
        {"children": {"for_each": "doan", "template": DOAN}}, {"doan": ["a", "b"]}
    )

    assert rendered["children"] == [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"text": {"content": "a"}}]},
        },
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"text": {"content": "b"}}]},
        },
    ]


def test_for_each_tren_truong_vang_mat_thi_khoa_bien_mat() -> None:
    """Trường tuỳ chọn kiểu mảng phải hành xử như mọi trường tuỳ chọn khác —
    `"children": []` là một câu khác hẳn với việc không nhắc tới `children`."""
    rendered = render_body({"children": {"for_each": "doan", "template": DOAN}}, {})

    assert rendered == {}


def test_for_each_tren_thu_khong_phai_mang_thi_khoa_bien_mat() -> None:
    """Gateway đã validate input theo `input_schema` trước khi tới đây, nên ca
    này là tài liệu tool khai sai. Bỏ khoá vẫn đúng hơn là ném `TypeError`
    giữa lúc dựng request."""
    rendered = render_body({"children": {"for_each": "doan", "template": DOAN}}, {"doan": "a"})

    assert rendered == {}


def test_for_each_tren_mang_rong_cho_ra_mang_rong() -> None:
    """Khác ca vắng mặt: người gọi có nhắc tới trường, và nói nó rỗng."""
    rendered = render_body({"children": {"for_each": "doan", "template": DOAN}}, {"doan": []})

    assert rendered == {"children": []}


def test_item_ngoai_vong_lap_khong_co_gia_tri() -> None:
    """`item` chỉ tồn tại bên trong `for_each`. Ngoài đó nó là một tên như mọi
    tên khác, và không tên nào được tự có giá trị."""
    rendered = render_body({"a": "{{item}}"}, {})

    assert rendered == {}


# ─── Đường dẫn trong placeholder, và khoá động ───────────────────────────────

KHOI = {
    "object": "block",
    "type": "{{item.type}}",
    "{{item.type}}": {
        "rich_text": [{"text": {"content": "{{item.text}}"}}],
        "checked": "{{item.checked}}",
    },
}


def test_placeholder_doc_duoc_mot_truong_cua_phan_tu() -> None:
    rendered = render_body({"a": "{{ten.ho}}"}, {"ten": {"ho": "Nguyễn"}})

    assert rendered == {"a": "Nguyễn"}


def test_duong_lac_thi_khoa_bien_mat_nhu_moi_gia_tri_vang_mat() -> None:
    rendered = render_body(
        {"a": "{{ten.ho}}", "b": "{{ten.dem}}", "c": "{{khong_co.gi}}"},
        {"ten": {"ho": "Nguyễn"}},
    )

    assert rendered == {"a": "Nguyễn"}


def test_khoa_dong_phu_moi_bien_the_bang_mot_template() -> None:
    """Lý do khoá động tồn tại: provider đặt tên khoá theo chính dữ liệu.

    Nếu thiếu nó thì mỗi loại khối cần một nhánh trong tài liệu — và một nhánh
    theo dữ liệu là bước đầu tiên biến template thành ngôn ngữ.
    """
    rendered = render_body(
        {"children": {"for_each": "blocks", "template": KHOI}},
        {
            "blocks": [
                {"type": "bulleted_list_item", "text": "Gạch đầu dòng"},
                {"type": "to_do", "text": "Việc", "checked": True},
            ]
        },
    )

    assert rendered["children"] == [
        {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"text": {"content": "Gạch đầu dòng"}}]},
        },
        {
            "object": "block",
            "type": "to_do",
            "to_do": {"rich_text": [{"text": {"content": "Việc"}}], "checked": True},
        },
    ]


def test_khoa_dong_khong_ghi_de_khoa_hang() -> None:
    """Hàng rào thứ nhất, và nó đứng độc lập với tầng validate phía trên.

    Thiếu nó thì một `type` mang giá trị `"object"` thay được hằng
    `"object": "block"` mà tài liệu đã khai — người gọi đổi được hình dạng
    thân request, không chỉ nội dung.
    """
    rendered = render_body(
        {"khoi": {"object": "block", "{{item.type}}": "bi-chen"}}, {"item": {"type": "object"}}
    )

    assert rendered == {"khoi": {"object": "block"}}


def test_khoa_dong_khong_phai_chuoi_thi_bi_bo() -> None:
    """Hàng rào thứ hai. Một khoá JSON phải là chuỗi; số hay `None` ở vị trí
    khoá là tài liệu tool sai, và bỏ vẫn đúng hơn là dựng ra thân request lạ."""
    for xau in (123, None, "", ["a"]):
        rendered = render_body({"khoi": {"{{item.type}}": "x"}}, {"item": {"type": xau}})
        assert rendered == {"khoi": {}}, xau


def test_key_placeholders_chi_liet_ke_ten_o_vi_tri_khoa() -> None:
    """`item.text` nằm ở vị trí giá trị nên không có mặt — nó không đổi được
    hình dạng thân request, chỉ đổi nội dung."""
    template = {"children": {"for_each": "blocks", "template": KHOI}}

    assert key_placeholders(template) == frozenset({"item.type"})


def test_placeholders_tra_ve_ten_goc_chu_khong_tra_ve_ca_duong() -> None:
    """Thứ cần đối chiếu với `input_schema` là tham số, và `item.type` không
    phải một tham số — nó là một trường của phần tử do `for_each` sinh ra."""
    template = {"children": {"for_each": "blocks", "template": KHOI}}

    assert placeholders(template) == frozenset({"blocks"})


def test_placeholders_liet_ke_moi_ten_ma_template_doc_toi() -> None:
    """Hàm mà test tài liệu dùng để đối chiếu hai chiều với `input_schema`.
    `item` không nằm trong đó — nó do chính `for_each` sinh ra."""
    template = {
        "parent": {"page_id": "{{parent_page_id}}"},
        "properties": {"title": {"title": [{"text": {"content": "{{title}}"}}]}},
        "children": {"for_each": "paragraphs", "template": DOAN},
    }

    assert placeholders(template) == frozenset({"parent_page_id", "title", "paragraphs"})
