"""Khung + câu trả lời → Workflow IR."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.domains.workflow.domain.compiler.answers import (
    TRIGGER_ID,
    Answers,
    SourceAnswer,
    ValueAnswer,
    is_loop_item,
    loop_id,
    subsumed,
)
from app.domains.workflow.domain.compiler.questions import (
    Candidate,
    Choice,
    DataRef,
    LoopQuestion,
    QuestionSet,
    SourceQuestion,
    ToolShapes,
    current_version,
)
from app.domains.workflow.domain.compiler.skeleton import Skeleton, SkeletonStep, StepKind
from app.domains.workflow.domain.ir import Path

#: Trường của node chứa các binding, theo loại bước.
_BUCKET = {StepKind.TOOL: "input", StepKind.TRANSFORM: "args"}


@dataclass(frozen=True, slots=True)
class Dropped:
    step: str
    tool_id: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class Assembled:
    document: dict[str, Any]
    #: `(node, trường)` → câu hỏi đã quyết định nó. Lỗi của validator trên IR truy về
    #: câu trả lời qua bảng này.
    origin: Mapping[tuple[str, Path], str]
    dropped: tuple[Dropped, ...]
    #: Node → câu hỏi quyết định sự có mặt của nó. Chỗ rơi về của lỗi không gắn với một
    #: trường cụ thể.
    owners: Mapping[str, str] = field(default_factory=dict)
    #: Node → các bước của trace mà nó đứng thay. Thân loop đứng thay mọi bước của vòng lặp.
    traced: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: Tên đầu vào → giá trị của lần chạy mẫu.
    samples: Mapping[str, Any] = field(default_factory=dict)
    #: Node llm → thứ nó đã "sinh ra" ở lần chạy mẫu: giá trị của các trường nó nuôi.
    llm_samples: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: Node `transform` mà trace không có, do người trả lời viết (ADR-0016).
    written: frozenset[str] = frozenset()


@dataclass(slots=True)
class _Build:
    skeleton: Skeleton
    questions: QuestionSet
    answers: Answers
    tools: ToolShapes
    llm_model: str
    loop_max_items: int
    origin: dict[tuple[str, Path], str] = field(default_factory=dict)
    #: Tên đầu vào → giá trị của lần chạy mẫu, theo thứ tự gặp.
    inputs: dict[str, Any] = field(default_factory=dict)
    #: Id node llm → schema của từng trường nó phải sinh ra.
    produced: dict[str, dict[str, Any]] = field(default_factory=dict)
    defs: dict[str, dict[str, Any]] = field(default_factory=dict)
    owners: dict[str, str] = field(default_factory=dict)
    traced: dict[str, tuple[str, ...]] = field(default_factory=dict)
    llm_samples: dict[str, dict[str, Any]] = field(default_factory=dict)
    written: set[str] = field(default_factory=set)
    #: Node llm → bước đầu tiên đọc nó.
    first_reader: dict[str, str] = field(default_factory=dict)

    # ─── Địa chỉ ─────────────────────────────────────────────────────────────

    def node_of(self, step: str) -> str:
        return self.answers.node_id(step)

    def output_path(self, step: str, path: Path) -> list[str]:
        """Đường trong output của node. Tool `system.transform` bọc kết quả trong
        `result`; node `transform` thì trả thẳng kết quả."""
        if self.skeleton.step(step).kind is StepKind.TRANSFORM:
            return list(path[1:])
        return list(path)

    def data(self, key: str) -> dict[str, Any]:
        ref = self.questions.ref(key)
        assert isinstance(ref, DataRef)  # tầng kiểm 1 và schema bảo đảm
        return _from_node(self.node_of(ref.step), self.output_path(ref.step, ref.path))

    # ─── Binding ─────────────────────────────────────────────────────────────

    def bindings(self, step: SkeletonStep, node_id: str) -> dict[str, Any]:
        bucket = _BUCKET[step.kind]
        found: dict[str, Any] = {}
        for question in self.questions.of(step.ref):
            if is_loop_item(self.questions, self.answers, question):
                loop = self.questions.loop_of(step.ref)
                assert isinstance(loop, LoopQuestion)
                found[question.field] = {"from_item": list(loop.loop.pick)}
                self.origin[(node_id, (bucket, question.field))] = loop.id
                continue
            answer = self.answers.sources[question.id]
            binding = self.binding(question, answer)
            if binding is not None:
                found[question.field] = binding
                self.origin[(node_id, (bucket, question.field))] = question.id
        return found

    def binding(self, question: SourceQuestion, answer: SourceAnswer) -> dict[str, Any] | None:
        match answer.choice:
            case Choice.CONST:
                return {"const": question.value}
            case Choice.INPUT:
                return self.input(answer.name, question.value)
            case Choice.TOOL_DEFAULT:
                return None
            case Choice.LLM:
                assert answer.node is not None
                return _from_node(answer.node, [question.field])
            case Choice.FROM_STEP:
                assert answer.candidate is not None
                return self.from_step(question.candidates[answer.candidate - 1], answer)

    def from_step(self, candidate: Candidate, answer: SourceAnswer) -> dict[str, Any]:
        node = self.node_of(candidate.step)
        element = candidate.element
        if answer.key is None or element is None:
            return _from_node(node, self.output_path(candidate.step, candidate.path))
        key = next(key for key in element.keys if key.field == answer.key)
        chosen = (
            self.input(answer.key_name, key.value)
            if answer.key_choice is Choice.INPUT
            else {"const": key.value}
        )
        return {
            "from_node": {
                "node": node,
                "path": self.output_path(candidate.step, element.array),
                "where": {key.field: chosen},
                "pick": list(element.pick),
            }
        }

    def value(self, value: ValueAnswer) -> dict[str, Any]:
        if value.kind == "du_lieu":
            assert value.ref is not None
            return self.data(value.ref)
        if value.kind == "workflow_input":
            return self.input(value.name, value.value)
        return {"const": value.value}

    def input(self, name: str | None, sample: Any) -> dict[str, Any]:
        assert name is not None
        self.inputs.setdefault(name, sample)
        return {"from_input": name}

    def produce(self, llm_id: str, step: SkeletonStep, name: str) -> None:
        """Node llm phải sinh ra đúng thứ mà trường tiêu thụ nó đòi: schema của trường
        đó trong tài liệu tool, không phải thứ model tự khai."""
        schema: Mapping[str, Any] = {}
        if step.kind is StepKind.TOOL and step.tool_version is not None:
            version = current_version(self.tools, step.tool_id, step.tool_version)
            tool = self.tools.get((step.tool_id, version), {})
            schema = tool.get("properties", {}).get(name, {})
            self.defs.setdefault(llm_id, {}).update(tool.get("$defs", {}))
        self.produced.setdefault(llm_id, {})[name] = dict(schema)
        # Hai bước cùng đọc một trường của một node thì giá trị mẫu là của bước đầu;
        # bước sau gửi giá trị khác thì tầng 3 sẽ báo không tái tạo được.
        self.llm_samples.setdefault(llm_id, {}).setdefault(name, step.fields[name])

    # ─── Node ────────────────────────────────────────────────────────────────

    def step_node(self, step: SkeletonStep) -> dict[str, Any]:
        node_id = self.node_of(step.ref)
        bound = self.bindings(step, node_id)
        self.owners[node_id] = step.ref
        self.traced[node_id] = (step.ref,)
        if step.kind is StepKind.TRANSFORM:
            return {
                "id": node_id,
                "type": "transform",
                "code": step.code,
                "args": bound,
                "output_schema": _shape(step.output.get("result")),
            }
        return {
            "id": node_id,
            "type": "tool",
            "tool_id": step.tool_id,
            "tool_version": current_version(self.tools, step.tool_id, step.tool_version or ""),
            "input": bound,
        }

    def loop_node(self, question: LoopQuestion, first: SkeletonStep) -> dict[str, Any]:
        loop = question.loop
        node_id = loop_id(question.id, self.answers)
        body = self.step_node(first)
        node: dict[str, Any] = {
            "id": node_id,
            "type": "loop",
            "over": _from_node(
                self.node_of(loop.source), self.output_path(loop.source, loop.array)
            ),
        }
        if loop.filter:
            node["filter"] = {name: {"const": value} for name, value in loop.filter}
        node |= {"max_items": self.loop_max_items, "body": [body], "collect": body["id"]}
        self.origin[(node_id, ("over",))] = question.id
        self.owners[node_id] = question.id
        self.traced[node_id] = self.traced[body["id"]] = loop.steps
        return node

    def plan_llms(self) -> None:
        """Mọi trường mà từng node llm nuôi, tính trước khi phát node nào. Một node có thể
        được nhiều bước đọc; nó đứng trước bước đầu tiên và phải sinh đủ cho mọi bước."""
        for step in self.skeleton.steps:
            if not self.answers.keeps(step.ref) or subsumed(self.questions, self.answers, step.ref):
                continue
            for question in self.questions.of(step.ref):
                answer = self.answers.sources.get(question.id)
                if (
                    answer is None
                    or answer.choice is not Choice.LLM
                    or answer.node is None
                    or is_loop_item(self.questions, self.answers, question)
                ):
                    continue
                self.produce(answer.node, step, question.field)
                self.first_reader.setdefault(answer.node, step.ref)

    def llm_nodes(self, reader: str) -> list[dict[str, Any]]:
        """Node llm mà bước `reader` là người đọc đầu tiên."""
        nodes: list[dict[str, Any]] = []
        for llm in self.answers.llms:
            if self.first_reader.get(llm.id) != reader:
                continue
            fields = self.produced[llm.id]
            schema: dict[str, Any] = {
                "type": "object",
                "additionalProperties": False,
                "required": list(fields),
                "properties": fields,
            }
            if defs := self.defs.get(llm.id):
                schema["$defs"] = defs
            nodes.append(
                {
                    "id": llm.id,
                    "type": "llm",
                    "model": self.llm_model,
                    "instructions": llm.instructions,
                    "input": {name: self.data(key) for name, key in llm.inputs.items()},
                    "output_schema": schema,
                }
            )
            self.origin[(llm.id, ("instructions",))] = f"llm:{llm.id}"
            self.owners[llm.id] = f"llm:{llm.id}"
        return nodes

    def computed(self, after: str) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        for compute in self.answers.computes:
            if compute.after != after:
                continue
            nodes.append(
                {
                    "id": compute.id,
                    "type": "transform",
                    "code": compute.code.strip(),
                    "args": {name: self.value(value) for name, value in compute.args.items()},
                    "output_schema": {"type": compute.result},
                }
            )
            self.owners[compute.id] = f"tinh_toan:{compute.id}"
            self.written.add(compute.id)
        return nodes

    def conditions(self, before: str) -> list[dict[str, Any]]:
        computes = {compute.id for compute in self.answers.computes}
        nodes: list[dict[str, Any]] = []
        for condition in self.answers.conditions:
            if condition.before != before:
                continue
            left = condition.left if condition.left in computes else self.node_of(condition.left)
            node: dict[str, Any] = {
                "id": condition.id,
                "type": "condition",
                "left": _from_node(left, []),
                "op": str(condition.op),
            }
            if condition.right is not None:
                node["right"] = self.value(condition.right)
            self.owners[condition.id] = f"dieu_kien:{condition.id}"
            nodes.append(node)
        return nodes


def assemble(
    skeleton: Skeleton,
    questions: QuestionSet,
    answers: Answers,
    tools: ToolShapes,
    *,
    llm_model: str,
    loop_max_items: int = 50,
) -> Assembled:
    """`answers` phải đã qua `read_answers` không lỗi."""
    build = _Build(skeleton, questions, answers, tools, llm_model, loop_max_items)
    build.plan_llms()
    nodes: list[dict[str, Any]] = []
    dropped: list[Dropped] = []

    for step in skeleton.steps:
        answer = answers.steps[step.ref]
        if not answer.keep:
            dropped.append(Dropped(step.ref, step.tool_id, answer.reason))
            continue
        if subsumed(questions, answers, step.ref):
            continue

        loop = questions.loop_of(step.ref)
        chosen = answers.loops.get(loop.id) if loop is not None else None
        if loop is not None and chosen is not None and chosen.as_loop:
            node = build.loop_node(loop, step)
        else:
            node = build.step_node(step)

        nodes += build.conditions(step.ref)
        nodes += build.llm_nodes(step.ref)
        nodes.append(node)
        nodes += build.computed(step.ref)

    trigger = {
        "id": TRIGGER_ID,
        "type": "trigger",
        "trigger": "manual",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": list(build.inputs),
            "properties": {name: _input_schema(value) for name, value in build.inputs.items()},
        },
    }
    nodes.insert(0, trigger)

    edges: list[dict[str, Any]] = []
    for source, target in zip(nodes, nodes[1:], strict=False):
        edge: dict[str, Any] = {"from": source["id"], "to": target["id"]}
        if source["type"] == "condition":
            edge["branch"] = "true"
        edges.append(edge)

    has_transform = any(
        inner["type"] == "transform" for node in nodes for inner in (node, *node.get("body", ()))
    )
    return Assembled(
        document={
            "schema_version": "2.1" if has_transform else "2.0",
            "nodes": nodes,
            "edges": edges,
        },
        origin=build.origin,
        dropped=tuple(dropped),
        owners=build.owners,
        traced=build.traced,
        samples=build.inputs,
        llm_samples=build.llm_samples,
        written=frozenset(build.written),
    )


def _from_node(node: str, path: list[str]) -> dict[str, Any]:
    return {"from_node": {"node": node, "path": path}}


def _input_schema(sample: Any) -> dict[str, Any]:
    if isinstance(sample, bool):
        return {"type": "boolean"}
    if isinstance(sample, int):
        return {"type": "integer"}
    if isinstance(sample, float):
        return {"type": "number"}
    return {"type": "string", "minLength": 1}


def _shape(value: Any) -> dict[str, Any]:
    """Schema lỏng nhất còn đúng với kết quả đã quan sát: chỉ kiểu ở tầng ngoài cùng."""
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        return {"type": "array"}
    if isinstance(value, Mapping):
        return {"type": "object"}
    return {}
