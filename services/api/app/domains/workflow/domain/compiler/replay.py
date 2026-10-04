"""Tầng kiểm 3: IR vừa lắp có tái tạo được lần chạy mẫu không.

Đi qua từng node với output đã ghi trong trace thay cho mọi lời gọi ra ngoài. Không
tool nào được gọi. Thứ duy nhất chạy thật là code của node `transform`, qua `run` do
bên gọi đưa vào — tầng này không nạp sandbox.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from typing import Any, Final

import jsonschema

from app.core.errors import AppError
from app.domains.workflow.domain.bindings import Missing, Scope, lookup, loop_items
from app.domains.workflow.domain.compiler.assemble import Assembled
from app.domains.workflow.domain.compiler.provenance import projects
from app.domains.workflow.domain.compiler.skeleton import Skeleton, SkeletonStep
from app.domains.workflow.domain.conditions import holds
from app.domains.workflow.domain.ir import (
    Binding,
    ConditionNode,
    FromInput,
    LlmNode,
    LoopNode,
    ToolNode,
    TransformNode,
    TriggerNode,
    parse,
)
from app.domains.workflow.domain.validation import Issue

#: Chạy `code` với `args` trong sandbox, trả giá trị nó `return`. Ném `AppError` khi
#: code không chạy được.
RunCode = Callable[[str, Mapping[str, Any]], Any]

_SHOWN: Final = 120


def replay(skeleton: Skeleton, assembled: Assembled, run: RunCode) -> tuple[Issue, ...]:
    """`assembled.document` phải đã qua validator không lỗi."""
    return tuple(_Replay(skeleton, assembled, run).issues())


class _Replay:
    def __init__(self, skeleton: Skeleton, assembled: Assembled, run: RunCode) -> None:
        self._skeleton = skeleton
        self._assembled = assembled
        self._run = run
        self._outputs: dict[str, Any] = {}
        #: Một node không cho ra output thì mọi thứ sau nó chỉ còn lỗi ăn theo.
        self._halted = False

    def issues(self) -> Iterator[Issue]:
        for node in parse(self._assembled.document).nodes:
            if self._halted:
                return
            scope = self._scope()
            match node:
                case TriggerNode():
                    continue
                case ToolNode():
                    step = self._step(node.id)
                    yield from self._same_input(node.id, "input", node.input, step, scope)
                    self._outputs[node.id] = step.output
                case TransformNode() if node.id in self._assembled.written:
                    yield from self._written(node, scope)
                case TransformNode():
                    yield from self._observed(node, self._step(node.id), scope)
                case LlmNode():
                    yield from self._resolvable(node.id, "input", node.input, scope)
                    self._outputs[node.id] = self._assembled.llm_samples.get(node.id, {})
                case ConditionNode():
                    yield from self._branch(node, scope)
                case LoopNode():
                    yield from self._loop(node, scope)

    def _scope(self) -> Scope:
        return Scope(inputs=self._assembled.samples, outputs=self._outputs)

    def _step(self, node_id: str, position: int = 0) -> SkeletonStep:
        return self._skeleton.step(self._assembled.traced[node_id][position])

    # ─── Input của một node so với thứ trace đã gửi ──────────────────────────

    def _resolve(
        self, node_id: str, bucket: str, bindings: Mapping[str, Binding], scope: Scope
    ) -> tuple[dict[str, Any], list[Issue]]:
        resolved: dict[str, Any] = {}
        issues: list[Issue] = []
        for name, binding in bindings.items():
            if isinstance(binding, FromInput) and binding.name not in scope.inputs:
                continue
            value = lookup(binding, scope)
            if isinstance(value, Missing):
                issues.append(
                    Issue(
                        "khong_tinh_duoc",
                        f"{node_id}.{bucket}.{name}: trên dữ liệu của lần chạy mẫu, {value.reason}",
                        node=node_id,
                        field=(bucket, name),
                    )
                )
            else:
                resolved[name] = value
        return resolved, issues

    def _resolvable(
        self, node_id: str, bucket: str, bindings: Mapping[str, Binding], scope: Scope
    ) -> Iterator[Issue]:
        yield from self._resolve(node_id, bucket, bindings, scope)[1]

    def _same_input(
        self,
        node_id: str,
        bucket: str,
        bindings: Mapping[str, Binding],
        step: SkeletonStep,
        scope: Scope,
    ) -> Iterator[Issue]:
        resolved, issues = self._resolve(node_id, bucket, bindings, scope)
        yield from issues
        for name, value in resolved.items():
            sent = step.fields.get(name)
            # Thứ trace đã gửi có thể là bản model chép tay và cắt bớt trường, hoặc một
            # UUID viết kiểu khác: cùng dữ liệu, không cùng từng byte.
            if not projects(sent, value):
                yield Issue(
                    "khong_tai_tao_duoc",
                    f"{node_id}.{bucket}.{name} tính ra {_show(value)}, còn bước {step.ref} của "
                    f"lần chạy mẫu đã gửi {_show(sent)}.",
                    node=node_id,
                    field=(bucket, name),
                )

    # ─── Transform ───────────────────────────────────────────────────────────

    def _observed(self, node: TransformNode, step: SkeletonStep, scope: Scope) -> Iterator[Issue]:
        """Code Agent đã chạy. Model chép dữ liệu vào `args` bằng tay; binding thì lấy cả
        mảng. Code phải cho cùng kết quả trên dữ liệu đầy đủ đó."""
        recorded = step.output.get("result")
        self._outputs[node.id] = recorded

        mismatched = list(self._same_input(node.id, "args", node.args, step, scope))
        yield from mismatched
        if mismatched:
            return
        resolved, _ = self._resolve(node.id, "args", node.args, scope)
        try:
            result = self._run(node.code, resolved)
        except AppError as exc:
            yield Issue(
                "code_khong_chay_duoc",
                f"Code của {node.id} không chạy được trên dữ liệu của lần chạy mẫu: {exc.message}",
                node=node.id,
                field=("code",),
            )
            return
        if result != recorded:
            yield Issue(
                "ket_qua_khac_lan_chay_mau",
                f"Code của {node.id} cho {_show(result)} trên dữ liệu đầy đủ, còn lúc Agent chạy "
                f"nó cho {_show(recorded)}. Dữ liệu đưa vào `args` không phải thứ Agent đã đưa.",
                node=node.id,
                field=("code",),
            )

    def _written(self, node: TransformNode, scope: Scope) -> Iterator[Issue]:
        """Code trace không có, do người trả lời viết (ADR-0016, quyết định 3)."""
        resolved, issues = self._resolve(node.id, "args", node.args, scope)
        yield from issues
        if issues:
            self._halted = True
            return
        try:
            result = self._run(node.code, resolved)
        except AppError as exc:
            self._halted = True
            yield Issue(
                "code_khong_chay_duoc",
                f"Code của {node.id} không chạy được trên dữ liệu của lần chạy mẫu: {exc.message}",
                node=node.id,
                field=("code",),
            )
            return
        self._outputs[node.id] = result
        error = jsonschema.exceptions.best_match(
            jsonschema.Draft202012Validator(node.output_schema).iter_errors(result)
        )
        if error is not None:
            yield Issue(
                "ket_qua_sai_kieu",
                f"Code của {node.id} trả về {_show(result)}, không khớp kiểu kết quả đã khai: "
                f"{error.message}",
                node=node.id,
                field=("output_schema",),
            )

    # ─── Điều kiện ───────────────────────────────────────────────────────────

    def _branch(self, node: ConditionNode, scope: Scope) -> Iterator[Issue]:
        """Bộ lắp nối mọi bước sau điều kiện vào nhánh `true`, và các bước đó đã chạy
        trong trace. Vậy trên dữ liệu của lần chạy mẫu, điều kiện phải đúng."""
        try:
            taken = holds(node, scope)
        except AppError as exc:
            yield Issue(
                "dieu_kien_khong_tinh_duoc",
                f"Trên dữ liệu của lần chạy mẫu, {exc.message}",
                node=node.id,
            )
            return
        if not taken:
            yield Issue(
                "dieu_kien_sai_nhanh",
                f"Điều kiện {node.id} ra sai trên dữ liệu của lần chạy mẫu, trong khi các bước "
                "đứng sau nó đã thật sự chạy.",
                node=node.id,
            )

    # ─── Vòng lặp ────────────────────────────────────────────────────────────

    def _loop(self, node: LoopNode, scope: Scope) -> Iterator[Issue]:
        members = self._assembled.traced[node.id]
        self._outputs[node.id] = [self._skeleton.step(ref).output for ref in members]
        try:
            items = loop_items(node, scope)
        except AppError as exc:
            yield Issue(
                "khong_tinh_duoc",
                f"Trên dữ liệu của lần chạy mẫu, {exc.message}",
                node=node.id,
                field=("over",),
            )
            return
        if len(items) != len(members):
            yield Issue(
                "vong_lap_lech_so_phan_tu",
                f"Vòng lặp {node.id} sẽ chạy {len(items)} lần trên dữ liệu của lần chạy mẫu, còn "
                f"trace chỉ có {len(members)} lời gọi ({', '.join(members)}). Không có điều kiện "
                "lọc nào giải thích được phần chênh.",
                node=node.id,
                field=("over",),
            )
            return
        (body,) = node.body
        bucket, bindings = (
            ("args", body.args)
            if isinstance(body, TransformNode)
            else (
                "input",
                body.input,
            )
        )
        for item, ref in zip(items, members, strict=True):
            inner = scope.with_item(item, self._outputs)
            yield from self._same_input(body.id, bucket, bindings, self._skeleton.step(ref), inner)


def _show(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= _SHOWN else f"{text[:_SHOWN]}…"
