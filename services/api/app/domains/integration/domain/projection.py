"""Chiếu phản hồi của provider sang `output_schema` của tool."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.domains.integration.domain.tools import (
    WILDCARD,
    FieldMapping,
    ItemSelector,
    ProviderReference,
)


def _is_array(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, str | bytes)


def select_path(payload: object, path: Sequence[str]) -> Any:
    """Lần theo `path` trong JSON của provider. Lạc đường thì trả `None`.

    Gặp `WILDCARD` thì rẽ nhánh: phần đuôi được chiếu lên từng phần tử của
    mảng và kết quả là một danh sách, đã bỏ những phần tử lạc đường. Danh sách
    rỗng vẫn là một kết quả — nó nói "mảng có, giá trị không" — khác với `None`
    nghĩa là "đường này không tồn tại".

    Lặp lại `providers.read_path` của domain `connection` — contract 1 cấm
    import qua đó, và mười dòng này rẻ hơn việc phá một ranh giới kiến trúc.
    """
    current: Any = payload
    for position, key in enumerate(path):
        if key == WILDCARD:
            if not _is_array(current):
                return None
            rest = path[position + 1 :]
            picked = (select_path(item, rest) for item in current)
            return [value for value in picked if value is not None]
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def select(payload: object, selector: ItemSelector) -> Any:
    """Giá trị của đường đầu tiên đi tới nơi."""
    for path in selector.paths:
        value = select_path(payload, path)
        if value is not None:
            return value
    return None


def _project_item(item: object, mapping: Mapping[str, ItemSelector]) -> dict[str, Any]:
    return {
        name: value
        for name, selector in mapping.items()
        if (value := select(item, selector)) is not None
    }


def project(payload: object, mapping: Mapping[str, FieldMapping]) -> dict[str, Any]:
    """Dựng đầu ra của tool từ phản hồi thô."""
    result: dict[str, Any] = {}

    for name, field in mapping.items():
        value = select_path(payload, field.path)
        if value is None:
            continue

        if field.items is None:
            result[name] = value
            continue

        if not _is_array(value):
            # Tài liệu khai đây là mảng mà provider trả về thứ khác. Bỏ qua
            # thay vì đoán — bước validate output sẽ nói ra chuyện này.
            continue

        result[name] = [_project_item(item, field.items) for item in value]

    return result


def build_reference(payload: object, reference: ProviderReference | None) -> str | None:
    """Ghép định danh mà provider cấp cho đối tượng vừa tạo."""
    if reference is None:
        return None

    parts = [select_path(payload, path) for path in reference.paths]
    if any(part is None for part in parts):
        return None
    return reference.separator.join(str(part) for part in parts)
