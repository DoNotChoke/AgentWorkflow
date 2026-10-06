"""Prompt của compiler: nội dung của một version đã phát hành không đổi."""

from __future__ import annotations

import hashlib

import pytest

from app.domains.workflow.domain.compiler.prompts import COMPILER_PROMPT_VERSION, load_prompt

FILES = ("intent", "answers")

#: Vân tay của nội dung ở từng version đã phát hành: mọi file của version đó, nối lại.
_RELEASED = {
    "1": "588e564a42870e1be190d011382cd5f3ab815fb321d0c19f671ba09cad514909",
    "2": "6fa7993a97aa6dce82b585cb89cafe3182a971e9f2186c0c3e8f295ec6953434",
}


def _fingerprint(version: str) -> str:
    joined = "\n\n".join(load_prompt(name, version) for name in FILES)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("version", sorted(_RELEASED))
def test_noi_dung_cua_mot_version_da_phat_hanh_khong_doi(version: str) -> None:
    """Phiên biên dịch chỉ ghi version của prompt (§40).

    Đỏ ở đây nghĩa là: chép file sang `*.v<mới>.md`, sửa ở bản mới, tăng
    `COMPILER_PROMPT_VERSION`, rồi thêm vân tay mới vào `_RELEASED`."""
    assert _fingerprint(version) == _RELEASED[version]


def test_version_dang_dung_la_mot_version_da_ghi_van_tay() -> None:
    assert COMPILER_PROMPT_VERSION in _RELEASED
