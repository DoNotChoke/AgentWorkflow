"""Prompt hệ thống của nền tảng: cách làm việc chung của mọi Agent.

Nội dung nằm ở `prompts/*.v<version>.md`, nạp một lần lúc import. Đứng trước prompt
riêng của từng Agent. Đổi chữ nào ở đó là đổi hành vi của mọi Agent cùng lúc, nên
**đổi nội dung là thêm file của version mới và tăng `PLATFORM_PROMPT_VERSION`**, không
sửa file cũ — trace chỉ ghi version, và file cũ là thứ cho biết version đó đã nói gì.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

PLATFORM_PROMPT_VERSION: Final = "1"

_PROMPTS: Final = Path(__file__).resolve().parent / "prompts"


def load_prompt(name: str, version: str = PLATFORM_PROMPT_VERSION) -> str:
    return (_PROMPTS / f"{name}.v{version}.md").read_text(encoding="utf-8").strip()


#: Cách làm việc chung.
PLATFORM_PROMPT: Final = load_prompt("platform")
#: Tiêu đề và lời dẫn đặt trước prompt riêng của Agent.
_OWN_INSTRUCTIONS: Final = load_prompt("own_instructions")


def compose_system_prompt(agent_prompt: str) -> str:
    """Prompt gửi cho model: phần của nền tảng, rồi tới phần riêng của Agent nếu có."""
    own = agent_prompt.strip()
    if not own:
        return PLATFORM_PROMPT
    return f"{PLATFORM_PROMPT}\n\n{_OWN_INSTRUCTIONS}\n\n{own}"
