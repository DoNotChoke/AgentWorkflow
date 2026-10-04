"""Prompt hệ thống của hai lời gọi model trong một phiên biên dịch.

Nội dung nằm ở `prompts/*.v<version>.md`. Đổi nội dung là thêm file của version mới và
tăng `COMPILER_PROMPT_VERSION`, không sửa file cũ: phiên biên dịch chỉ ghi version (§40).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

COMPILER_PROMPT_VERSION: Final = "1"

_PROMPTS: Final = Path(__file__).resolve().parent / "prompts"


def load_prompt(name: str, version: str = COMPILER_PROMPT_VERSION) -> str:
    return (_PROMPTS / f"{name}.v{version}.md").read_text(encoding="utf-8").strip()


#: Hiểu ý đồ từ conversation.
INTENT_PROMPT: Final = load_prompt("intent")
#: Trả lời tập câu hỏi theo ý đồ đã chấp nhận.
ANSWERS_PROMPT: Final = load_prompt("answers")
