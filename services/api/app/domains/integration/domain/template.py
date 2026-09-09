"""Dựng thân request của provider từ input của người gọi.

Mặt gương của `projection.py`. Bên kia hẹp lại thứ provider trả về; bên này
dựng lên thứ provider đòi.

Cần nó vì hình dạng mà provider đòi và hình dạng mà một LLM viết được là hai
thứ khác nhau. Tạo một trang Notion đòi::

    {"parent": {"page_id": "…"},
     "properties": {"title": {"title": [{"text": {"content": "…"}}]}}}

Không có tầng này thì `input_schema` phải khai đúng cái cây đó, và tool trở
thành thứ mà LLM gõ sai nhiều hơn gõ đúng — trong khi chỗ duy nhất cần biết
hình dạng ấy là tài liệu connector.

Template là dữ liệu, không phải ngôn ngữ. Ba luật, hết:

* chuỗi đúng bằng ``"{{ten}}"`` được thay bằng giá trị của ``ten``;
* ``{"for_each": "ten", "template": …}`` lặp template trên từng phần tử của
  mảng ``ten``, với ``{{item}}`` là phần tử hiện tại;
* mọi thứ còn lại là hằng.

Không nội suy giữa chuỗi, không điều kiện, không lồng ``for_each`` trong
``for_each`` — cùng lập luận với giới hạn một cấp của ``output_mapping.items``:
sâu hơn thì tài liệu khó đọc hơn chính đoạn code nó thay thế.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Final, TypeGuard

#: Tên phần tử hiện tại bên trong một `for_each`.
ITEM = "item"

FOR_EACH = "for_each"
TEMPLATE = "template"

#: Chỉ khớp khi placeholder chiếm trọn chuỗi. Nội suy giữa chuỗi bị bỏ có chủ
#: đích: nó kéo theo câu hỏi thoát ký tự thế nào, và chưa provider nào cần.
#:
#: Tên có thể là một đường: ``{{item.type}}`` đọc trường ``type`` của phần tử
#: đang lặp.
_PLACEHOLDER: Final = re.compile(r"^\{\{([a-z0-9_]+(?:\.[a-z0-9_]+)*)\}\}$")


class _Missing:
    """Không có giá trị — khác với `None`, thứ một số provider hiểu là "xoá"."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<missing>"


MISSING: Final = _Missing()


def placeholders(template: object) -> frozenset[str]:
    """Mọi tên **gốc** mà `template` đọc tới, trừ `item` của chính nó.

    Gốc, vì `{{item.type}}` đọc một trường của phần tử đang lặp chứ không đọc
    một tham số riêng — thứ cần đối chiếu với `input_schema` là `item` (và
    `item` thì bị trừ ra vì chính `for_each` sinh ra nó).

    Test đọc tài liệu dùng hàm này để đối chiếu hai chiều với `input_schema` —
    một tên không có trong schema là một trường không bao giờ được điền, và một
    trường không có trong template là một thứ người gọi gửi đi đâu mất.
    """
    if isinstance(template, str):
        found = _PLACEHOLDER.match(template)
        return _root(found.group(1)) if found else frozenset()

    if isinstance(template, Mapping):
        if FOR_EACH in template:
            inner = placeholders(template.get(TEMPLATE))
            return frozenset({str(template[FOR_EACH])}) | inner
        # Khoá cũng đọc được placeholder, nên phải quét cả hai phía.
        from_keys = (_root(m.group(1)) for key in template if (m := _PLACEHOLDER.match(key)))
        from_values = (placeholders(value) for value in template.values())
        return frozenset().union(*from_keys, *from_values)

    if _is_array(template):
        return frozenset().union(*(placeholders(value) for value in template))

    return frozenset()


def key_placeholders(template: object) -> frozenset[str]:
    """Tên xuất hiện ở **vị trí khoá**, giữ nguyên cả đường.

    Khoá động là thứ duy nhất trong template quyết định *hình dạng* của thân
    request chứ không chỉ nội dung, nên test tài liệu soi riêng chúng.
    """
    if isinstance(template, Mapping):
        if FOR_EACH in template:
            return key_placeholders(template.get(TEMPLATE))
        found = {m.group(1) for key in template if (m := _PLACEHOLDER.match(key))}
        return frozenset(found).union(*(key_placeholders(v) for v in template.values()))

    if _is_array(template):
        return frozenset().union(*(key_placeholders(value) for value in template))

    return frozenset()


def render_body(template: Mapping[str, Any], values: Mapping[str, Any]) -> dict[str, Any]:
    """Thân request cho một lời gọi, dựng từ template của tài liệu tool."""
    rendered = _render(template, values)
    return rendered if isinstance(rendered, dict) else {}


# ─────────────────────────────────────────────────────────────────────────────


def _is_array(value: object) -> TypeGuard[Sequence[Any]]:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes)


def _root(name: str) -> frozenset[str]:
    """Tên gốc của một đường, hoặc rỗng nếu đó là `item` của vòng lặp."""
    root = name.split(".", 1)[0]
    return frozenset() if root == ITEM else frozenset({root})


def _lookup(name: str, values: Mapping[str, Any]) -> Any:
    """Giá trị của `ten` hoặc `ten.truong`, hoặc `MISSING` nếu lạc đường.

    Phân biệt "không có khoá" với "khoá mang `None`": cái sau là một giá trị
    người gọi cấp và phải đi tiếp.
    """
    current: Any = values
    for part in name.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return MISSING
        current = current[part]
    return current


def _render(node: object, values: Mapping[str, Any]) -> Any:
    if isinstance(node, str):
        found = _PLACEHOLDER.match(node)
        return _lookup(found.group(1), values) if found else node

    if isinstance(node, Mapping):
        if FOR_EACH in node:
            return _render_each(node, values)
        return _render_object(node, values)

    if _is_array(node):
        items = [_render(value, values) for value in node]
        return [value for value in items if value is not MISSING]

    return node


def _render_object(node: Mapping[str, Any], values: Mapping[str, Any]) -> dict[str, Any]:
    """Một object, với khoá hằng điền trước và khoá động điền sau.

    Khoá nào không có giá trị thì biến mất khỏi thân request, chứ không thành
    `null`: trường tuỳ chọn mà gửi `null` là nói với provider "đặt nó rỗng",
    không phải "tôi không nhắc tới nó".

    Khoá động (`{"{{item.type}}": …}`) tồn tại vì vài provider đặt tên khoá
    theo chính dữ liệu — một khối Notion để nội dung dưới khoá trùng với
    `type` của nó. Nó là tra cứu, không phải điều kiện: một template phủ mọi
    loại khối thay vì một nhánh cho mỗi loại.
    """
    built: dict[str, Any] = {}
    dynamic: list[tuple[str, Any]] = []

    for key, value in node.items():
        rendered = _render(value, values)
        if rendered is MISSING:
            continue
        found = _PLACEHOLDER.match(key)
        if found is None:
            built[key] = rendered
        else:
            dynamic.append((found.group(1), rendered))

    for name, rendered in dynamic:
        resolved = _lookup(name, values)
        # Hai hàng rào, và cả hai đứng độc lập với tầng validate phía trên.
        # Khoá phải là chuỗi không rỗng; và nó **không ghi đè** khoá đã có —
        # nếu không, một `type` mang giá trị `"object"` sẽ thay được hằng
        # `"object": "block"` mà tài liệu đã khai.
        if isinstance(resolved, str) and resolved and resolved not in built:
            built[resolved] = rendered

    return built


def _render_each(node: Mapping[str, Any], values: Mapping[str, Any]) -> Any:
    source = values.get(str(node[FOR_EACH]), MISSING)
    if source is MISSING or not _is_array(source):
        return MISSING

    template = node[TEMPLATE]
    built = [_render(template, {**values, ITEM: element}) for element in source]
    return [value for value in built if value is not MISSING]
