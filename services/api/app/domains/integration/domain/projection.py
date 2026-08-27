"""Chiếu phản hồi của provider sang `output_schema` của tool."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.domains.integration.domain.tools import FieldMapping, ProviderReference


def select_path(payload: object, path: Sequence[str]) -> Any:
    """Lần theo `path` trong JSON của provider. Lạc đường thì trả `None`.

    Lặp lại `providers.read_path` của domain `connection` — contract 1 cấm
    import qua đó, và mười dòng này rẻ hơn việc phá một ranh giới kiến trúc.
    """
    current: Any = payload
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def _project_item(item: object, mapping: Mapping[str, tuple[str, ...]]) -> dict[str, Any]:
    return {
        name: value
        for name, path in mapping.items()
        if (value := select_path(item, path)) is not None
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

        if not isinstance(value, Sequence) or isinstance(value, str | bytes):
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
