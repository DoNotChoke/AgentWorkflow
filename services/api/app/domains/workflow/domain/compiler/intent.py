"""Ý đồ: workflow này để làm gì. Thứ người dùng chấp nhận trước khi trace được đọc."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

import jsonschema

from app.core.errors import ValidationError


def _lines(about: str) -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": 12,
        "items": {"type": "string", "minLength": 1, "maxLength": 300},
        "description": f"{about} Một mảng các câu ngắn, mỗi phần tử một ý; mảng rỗng nếu "
        "không có gì.",
    }


#: Hình dạng model nộp một đề xuất ý đồ. Toàn văn bản tự do: nó để người đọc, và để
#: model thứ hai lấy làm căn cứ — không gì ở đây đi vào IR.
INTENT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["nhiem_vu", "thay_doi", "co_dinh", "dieu_kien", "khong_thuoc_nhiem_vu"],
    "properties": {
        "nhiem_vu": {
            "type": "string",
            "minLength": 1,
            "maxLength": 500,
            "description": "Workflow làm gì mỗi lần chạy, một hai câu.",
        },
        "thay_doi": _lines("Thứ khác đi giữa các lần chạy."),
        "co_dinh": _lines("Thứ giữ nguyên ở mọi lần chạy."),
        "dieu_kien": _lines("Khi nào thì làm, khi nào thì không."),
        "khong_thuoc_nhiem_vu": _lines(
            "Việc Agent đã làm trong hội thoại mà workflow không cần làm lại."
        ),
    },
}


@dataclass(frozen=True, slots=True)
class Intent:
    task: str
    varies: tuple[str, ...] = ()
    fixed: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()


def read_intent(raw: Mapping[str, Any]) -> Intent:
    error = jsonschema.exceptions.best_match(
        jsonschema.Draft202012Validator(INTENT_SCHEMA).iter_errors(raw)
    )
    if error is not None:
        where = "/".join(str(part) for part in error.absolute_path) or "(gốc)"
        raise ValidationError(f"Đề xuất ý đồ sai hình dạng ở {where}: {error.message}")
    return Intent(
        task=raw["nhiem_vu"].strip(),
        varies=tuple(raw["thay_doi"]),
        fixed=tuple(raw["co_dinh"]),
        conditions=tuple(raw["dieu_kien"]),
        excluded=tuple(raw["khong_thuoc_nhiem_vu"]),
    )


def to_document(intent: Intent) -> dict[str, Any]:
    return {
        "nhiem_vu": intent.task,
        "thay_doi": list(intent.varies),
        "co_dinh": list(intent.fixed),
        "dieu_kien": list(intent.conditions),
        "khong_thuoc_nhiem_vu": list(intent.excluded),
    }


def render_intent(intent: Intent) -> str:
    """Ý đồ dưới dạng văn bản: thứ người dùng đọc, và thứ model thứ hai nhận."""
    parts = [f"**Nhiệm vụ.** {intent.task}"]
    parts += _section("Thay đổi mỗi lần chạy", intent.varies)
    parts += _section("Giữ nguyên", intent.fixed)
    parts += _section("Điều kiện", intent.conditions)
    parts += _section("Không thuộc nhiệm vụ", intent.excluded)
    return "\n\n".join(parts)


def _section(title: str, lines: Sequence[str]) -> list[str]:
    if not lines:
        return []
    return [f"**{title}**\n" + "\n".join(f"- {line}" for line in lines)]
