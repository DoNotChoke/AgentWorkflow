"""Prompt hệ thống của node `llm`.

Nội dung nằm ở `prompts/llm_node.v<version>.md`. Đổi nội dung là thêm file của version mới
và tăng `LLM_NODE_PROMPT_VERSION`, không sửa file cũ: nó đổi hành vi của mọi workflow đã
publish cùng lúc.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

LLM_NODE_PROMPT_VERSION: Final = "1"
#: Tên tool model gọi để nộp kết quả của node.
RESULT_TOOL: Final = "nop_ket_qua"

_PROMPTS: Final = Path(__file__).resolve().parent / "prompts"


def load_prompt(version: str = LLM_NODE_PROMPT_VERSION) -> str:
    return (_PROMPTS / f"llm_node.v{version}.md").read_text(encoding="utf-8").strip()


_TEMPLATE: Final = load_prompt()


def system_prompt(instructions: str) -> str:
    # `replace`, không `format`: `instructions` do model biên dịch viết, có thể chứa dấu ngoặc nhọn.
    return _TEMPLATE.replace("{tool}", RESULT_TOOL).replace("{instructions}", instructions.strip())
