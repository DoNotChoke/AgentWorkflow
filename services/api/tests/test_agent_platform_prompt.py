"""Prompt hệ thống của nền tảng, và cách nó ghép với prompt riêng của Agent."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from app.domains.agent.application.tool_naming import to_model_name
from app.domains.agent.domain import platform_prompt
from app.domains.agent.domain.entities import BUILT_IN_TOOL_IDS
from app.domains.agent.domain.platform_prompt import (
    PLATFORM_PROMPT,
    PLATFORM_PROMPT_VERSION,
    compose_system_prompt,
    load_prompt,
)

FILES = ("platform", "own_instructions")

#: Vân tay của nội dung ở từng version đã phát hành: mọi file của version đó, nối lại.
_RELEASED = {
    "1": "013c7f5fe3f18369d25db03ad8b54d5bf9917ea8e5285766dff90e0290fc79f9",
}


def _fingerprint(version: str) -> str:
    joined = "\n\n".join(load_prompt(name, version) for name in FILES)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("version", sorted(_RELEASED))
def test_noi_dung_cua_mot_version_da_phat_hanh_khong_doi(version: str) -> None:
    """Trace chỉ ghi version. Sửa file của một version đã phát hành thì hai lượt mang
    cùng một số đã chạy với hai prompt khác nhau, và không gì ghi lại điều đó (§40).

    Đỏ ở đây nghĩa là: chép file sang `*.v<mới>.md`, sửa ở bản mới, tăng
    `PLATFORM_PROMPT_VERSION`, rồi thêm vân tay mới vào `_RELEASED`."""
    assert _fingerprint(version) == _RELEASED[version]


def test_version_dang_dung_la_mot_version_da_ghi_van_tay() -> None:
    assert PLATFORM_PROMPT_VERSION in _RELEASED


def test_file_prompt_khong_phu_thuoc_kieu_xuong_dong(tmp_path: Path, monkeypatch: Any) -> None:
    """Git trên Windows có thể đổi `\\n` thành `\\r\\n` lúc checkout. Prompt gửi cho model
    phải giống nhau trên mọi máy."""
    (tmp_path / "platform.v9.md").write_bytes("# Tiêu đề\r\n\r\nNội dung.\r\n".encode())
    monkeypatch.setattr(platform_prompt, "_PROMPTS", tmp_path)

    assert load_prompt("platform", "9") == "# Tiêu đề\n\nNội dung."


def test_moi_tool_dung_san_deu_duoc_day_cach_dung() -> None:
    """Thêm một tool hệ thống mà quên dạy Agent dùng nó thì nó chỉ là một cái tên."""
    for tool_id in BUILT_IN_TOOL_IDS:
        assert f"`{to_model_name(tool_id)}`" in PLATFORM_PROMPT, tool_id


def test_agent_khong_co_prompt_rieng_chi_nhan_phan_cua_nen_tang() -> None:
    assert compose_system_prompt("") == PLATFORM_PROMPT
    assert compose_system_prompt("  \n ") == PLATFORM_PROMPT


def test_prompt_rieng_dung_sau_va_co_tieu_de_rieng() -> None:
    composed = compose_system_prompt("Bạn là trợ lý của phòng kế toán.")

    assert composed.startswith(PLATFORM_PROMPT)
    rieng = composed[len(PLATFORM_PROMPT) :]
    assert "# Vai trò và chỉ dẫn riêng của bạn" in rieng
    assert rieng.rstrip().endswith("Bạn là trợ lý của phòng kế toán.")
