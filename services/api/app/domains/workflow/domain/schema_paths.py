"""Đi một đường binding bên trong JSON Schema, để biết trước lúc chạy nó có tới nơi không.

Ba kết quả, và phân biệt chúng là toàn bộ lý do module này tồn tại:

- **tới nơi** — có schema của giá trị ở cuối đường;
- **không tồn tại** — schema nói chắc chắn đường đó không có, kèm vị trí segment
  hỏng. Chỉ trả khi schema *đóng* (`additionalProperties: false`, hoặc đi tiếp
  vào một giá trị nguyên thuỷ);
- **không biết** — schema không đủ chặt để kết luận. Validator im lặng thay vì
  báo lỗi oan: một cảnh báo sai dạy người dùng bỏ qua cảnh báo.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_SCALARS = frozenset({"string", "integer", "number", "boolean", "null"})


@dataclass(frozen=True, slots=True)
class Located:
    """Kết quả đi đường. `schema is None` và `missing_at is None` là "không biết"."""

    schema: Mapping[str, Any] | None
    #: Schema gốc để tra `$ref` trong `schema`.
    root: Mapping[str, Any]
    missing_at: int | None = None

    @property
    def missing(self) -> bool:
        return self.missing_at is not None


def locate(root: Mapping[str, Any], path: Sequence[str]) -> Located:
    return locate_from(root, root, path)


def locate_from(root: Mapping[str, Any], schema: Mapping[str, Any], path: Sequence[str]) -> Located:
    current: Mapping[str, Any] | None = _deref(root, schema)
    for index, segment in enumerate(path):
        if current is None:
            return Located(None, root)
        types = types_of(current)
        properties = current.get("properties")

        if segment.isdigit() and is_array(current):
            current = items_of(root, current)
            continue
        if isinstance(properties, Mapping) and segment in properties:
            current = _deref(root, properties[segment])
            continue
        if "object" in types or isinstance(properties, Mapping):
            if current.get("additionalProperties") is False:
                return Located(None, root, missing_at=index)
            return Located(None, root)
        if is_array(current) or (types and types <= _SCALARS):
            return Located(None, root, missing_at=index)
        return Located(None, root)
    return Located(current, root)


def types_of(schema: Mapping[str, Any]) -> frozenset[str]:
    raw = schema.get("type")
    if isinstance(raw, str):
        return frozenset({raw})
    if isinstance(raw, list):
        return frozenset(item for item in raw if isinstance(item, str))
    return frozenset()


def is_array(schema: Mapping[str, Any]) -> bool:
    return "array" in types_of(schema) or "items" in schema


def items_of(root: Mapping[str, Any], schema: Mapping[str, Any]) -> Mapping[str, Any] | None:
    items = schema.get("items")
    return _deref(root, items) if isinstance(items, Mapping) else None


def _deref(root: Mapping[str, Any], schema: object) -> Mapping[str, Any] | None:
    """Chỉ hiểu `$ref` cục bộ dạng `#/$defs/<tên>` — dạng duy nhất catalog tool dùng."""
    if not isinstance(schema, Mapping):
        return None
    ref = schema.get("$ref")
    if not isinstance(ref, str):
        return schema
    prefix = "#/$defs/"
    definitions = root.get("$defs")
    if ref.startswith(prefix) and isinstance(definitions, Mapping):
        target = definitions.get(ref.removeprefix(prefix))
        return target if isinstance(target, Mapping) else None
    return None
