"""Node `llm`: hỏi model, kiểm kết quả, hỏi lại có hạn. Không hạ tầng."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from app.core.errors import ProviderUnavailableError, WorkflowExecutionError
from app.domains.workflow.application.llm_node import run_llm_node
from app.domains.workflow.application.ports import ModelAnswer, Rejected
from app.domains.workflow.domain.ir import parse
from app.domains.workflow.domain.llm_node import (
    LLM_NODE_PROMPT_VERSION,
    RESULT_TOOL,
    load_prompt,
    system_prompt,
)
from app.domains.workflow.domain.plan import build_plan

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text"],
    "properties": {"text": {"type": "string", "minLength": 1}},
}
COMPILED = Path(__file__).parent / "fixtures" / "compiled"


class Scripted:
    def __init__(self, *answers: ModelAnswer | Exception) -> None:
        self._answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    async def answer(
        self,
        *,
        model: str,
        system: str,
        data: str,
        output_schema: Mapping[str, Any],
        rejected: Sequence[Rejected],
    ) -> ModelAnswer:
        self.calls.append({"system": system, "data": data, "rejected": list(rejected)})
        found = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        if isinstance(found, Exception):
            raise found
        return found


def _ok(text: str = "Xin chào") -> ModelAnswer:
    return ModelAnswer(output={"text": text}, input_tokens=50, output_tokens=10)


async def _run(models: Scripted, **options: Any) -> Any:
    return await run_llm_node(
        models,
        model="gpt-6.1-sol",
        instructions="Viết một lời chào.",
        data={"ten": "An", "so": 3},
        output_schema=SCHEMA,
        **options,
    )


async def test_ket_qua_dung_kieu_thi_mot_lan_hoi_la_xong() -> None:
    models = Scripted(_ok())

    outcome = await _run(models)

    assert (outcome.output, outcome.attempts) == ({"text": "Xin chào"}, 1)
    assert (outcome.input_tokens, outcome.output_tokens) == (50, 10)
    assert json.loads(models.calls[0]["data"]) == {"ten": "An", "so": 3}
    assert models.calls[0]["rejected"] == []


async def test_sai_kieu_thi_ly_do_quay_lai_model() -> None:
    models = Scripted(ModelAnswer(output={"text": ""}, input_tokens=50, output_tokens=5), _ok())

    outcome = await _run(models)

    assert (outcome.attempts, outcome.input_tokens, outcome.output_tokens) == (2, 100, 15)
    (rejected,) = models.calls[1]["rejected"]
    assert rejected.output == {"text": ""}
    assert rejected.reason.startswith("Kết quả sai kiểu ở text:")


async def test_khong_goi_tool_ket_qua_cung_la_mot_lan_bi_tu_choi() -> None:
    models = Scripted(ModelAnswer(output=None, text="Xin chào An!"), _ok())

    outcome = await _run(models)

    assert outcome.attempts == 2
    assert RESULT_TOOL in models.calls[1]["rejected"][0].reason


async def test_het_so_lan_thi_hong_va_noi_ly_do_cuoi() -> None:
    models = Scripted(ModelAnswer(output={"khac": 1}))

    with pytest.raises(WorkflowExecutionError, match="sau 2 lần") as caught:
        await _run(models, max_attempts=2)

    assert len(models.calls) == 2
    assert not caught.value.retryable


async def test_cau_tra_loi_bi_cat_thi_khong_hoi_lai() -> None:
    models = Scripted(ModelAnswer(output=None, truncated=True))

    with pytest.raises(WorkflowExecutionError, match="bị cắt ở trần token"):
        await _run(models)

    assert len(models.calls) == 1


async def test_loi_cua_provider_di_thang_ra_ngoai_de_temporal_thu_lai() -> None:
    models = Scripted(ProviderUnavailableError("quá tải"))

    with pytest.raises(ProviderUnavailableError) as caught:
        await _run(models)

    assert caught.value.retryable


# ─── Prompt ──────────────────────────────────────────────────────────────────

_RELEASED = {"1": "acdb17ff61d324589f9bf7555e1d7542f44249720768f9473762f591b566369f"}


@pytest.mark.parametrize("version", sorted(_RELEASED))
def test_noi_dung_cua_mot_version_da_phat_hanh_khong_doi(version: str) -> None:
    """Prompt này chạy trong mọi workflow đã publish. Đỏ ở đây nghĩa là: chép file sang
    `llm_node.v<mới>.md`, sửa ở bản mới, tăng `LLM_NODE_PROMPT_VERSION`, thêm vân tay."""
    digest = hashlib.sha256(load_prompt(version).encode("utf-8")).hexdigest()
    assert digest == _RELEASED[version]


def test_version_dang_dung_la_mot_version_da_ghi_van_tay() -> None:
    assert LLM_NODE_PROMPT_VERSION in _RELEASED


def test_instructions_co_dau_ngoac_nhon_van_vao_prompt_nguyen_ven() -> None:
    prompt = system_prompt('Trả về {"text": "..."} và gọi {tool} thật.')

    assert 'Trả về {"text": "..."}' in prompt
    assert f"`{RESULT_TOOL}`" in prompt
    assert "{instructions}" not in prompt


# ─── Mọi workflow compiler lắp ra đều dựng được plan ─────────────────────────


@pytest.mark.parametrize("path", sorted(COMPILED.glob("*.ir.json")), ids=lambda p: p.stem)
def test_ir_cua_compiler_dung_duoc_plan_de_chay(path: Path) -> None:
    ir = parse(json.loads(path.read_text(encoding="utf-8")))

    plan = build_plan(ir)

    assert plan.order[0] == "t"
    assert set(plan.order) == {node.id for node in ir.nodes}
