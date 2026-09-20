"""Workflow IR v1 dưới dạng kiểu mà domain đọc được."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Literal

import jsonschema

from app.core.errors import WorkflowDefinitionError
from app.domains.workflow.domain.generated.ir_v1 import IR_SCHEMA

SUPPORTED_MAJOR: Final = 1

#: Trần của một tài liệu IR, đo trên JSON chuẩn hoá. Plan đi vào lịch sử Temporal
MAX_DOCUMENT_BYTES: Final = 256 * 1024

#: Số lỗi hình dạng tối đa trả về trong một lần — đủ để sửa, không đủ để ngập.
_MAX_SHAPE_ERRORS: Final = 20

_VALIDATOR = jsonschema.Draft202012Validator(IR_SCHEMA)

Path = tuple[str, ...]
Branch = Literal["true", "false"]


# ─── Binding ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Const:
    value: Any


@dataclass(frozen=True, slots=True)
class FromInput:
    name: str


@dataclass(frozen=True, slots=True)
class FromItem:
    path: Path


@dataclass(frozen=True, slots=True)
class FromConnection:
    provider: str
    field: str


ScalarBinding = Const | FromInput | FromItem


@dataclass(frozen=True, slots=True)
class FromNode:
    node: str
    path: Path
    #: Có thì `path` trỏ vào một mảng và giá trị là phần tử đầu tiên khớp.
    where: Mapping[str, ScalarBinding] | None = None
    pick: Path | None = None


Binding = Const | FromInput | FromNode | FromItem | FromConnection


# ─── Node ────────────────────────────────────────────────────────────────────


class ConditionOp(StrEnum):
    EQ = "eq"
    NE = "ne"
    LT = "lt"
    GT = "gt"
    CONTAINS = "contains"
    EXISTS = "exists"


@dataclass(frozen=True, slots=True)
class TriggerNode:
    id: str
    trigger: str
    input_schema: Mapping[str, Any]
    label: str | None = None


@dataclass(frozen=True, slots=True)
class ToolNode:
    id: str
    tool_id: str
    tool_version: str
    input: Mapping[str, Binding]
    label: str | None = None


@dataclass(frozen=True, slots=True)
class ConditionNode:
    id: str
    left: Binding
    op: ConditionOp
    right: Binding | None = None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class LlmNode:
    id: str
    model: str
    instructions: str
    input: Mapping[str, Binding]
    output_schema: Mapping[str, Any]
    label: str | None = None


BodyNode = ToolNode | LlmNode


@dataclass(frozen=True, slots=True)
class LoopNode:
    id: str
    over: Binding
    max_items: int
    body: tuple[BodyNode, ...]
    collect: str
    filter: Mapping[str, ScalarBinding] | None = None
    label: str | None = None


Node = TriggerNode | ToolNode | ConditionNode | LlmNode | LoopNode


@dataclass(frozen=True, slots=True)
class Edge:
    source: str
    target: str
    branch: Branch | None = None


@dataclass(frozen=True, slots=True)
class WorkflowIR:
    schema_version: str
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    connections: Mapping[str, str] = field(default_factory=dict)

    def walk(self) -> Iterator[tuple[LoopNode | None, Node]]:
        """Mọi node, kể cả trong thân loop, kèm loop bao nó."""
        for node in self.nodes:
            yield None, node
            if isinstance(node, LoopNode):
                for inner in node.body:
                    yield node, inner


# ─── Parse ───────────────────────────────────────────────────────────────────

_KNOWN_TOP: Final = frozenset({"trigger", "tool", "condition", "llm", "loop"})
_KNOWN_BODY: Final = frozenset({"tool", "llm"})


def parse(document: Mapping[str, Any]) -> WorkflowIR:
    """Tài liệu JSON → `WorkflowIR`. Ném `WorkflowDefinitionError` nếu không chạy được."""
    size = len(json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if size > MAX_DOCUMENT_BYTES:
        raise WorkflowDefinitionError(
            f"Workflow quá lớn: {size} byte, trần là {MAX_DOCUMENT_BYTES} byte.",
            details={"byte_size": size, "max_bytes": MAX_DOCUMENT_BYTES},
        )

    _ensure_supported_version(document.get("schema_version"))
    _ensure_shape(document)
    _ensure_known_node_types(document)

    return WorkflowIR(
        schema_version=document["schema_version"],
        nodes=tuple(_node(raw) for raw in document["nodes"]),
        edges=tuple(
            Edge(source=raw["from"], target=raw["to"], branch=raw.get("branch"))
            for raw in document["edges"]
        ),
        connections=dict(document.get("connections", {})),
    )


def _ensure_supported_version(raw: object) -> None:
    """Chặn trước jsonschema: `pattern` sẽ chỉ nói "không khớp ^1\\.", không nói
    vì sao (§39, Runtime Compatibility Validation)."""
    if not isinstance(raw, str):
        return
    major, _, _ = raw.partition(".")
    if major.isdigit() and int(major) != SUPPORTED_MAJOR:
        raise WorkflowDefinitionError(
            f"IR version {raw} chưa chạy được; runtime này hiểu major {SUPPORTED_MAJOR}.",
            details={"schema_version": raw, "supported_major": SUPPORTED_MAJOR},
        )


def _ensure_shape(document: Mapping[str, Any]) -> None:
    errors = sorted(_VALIDATOR.iter_errors(document), key=lambda e: list(e.absolute_path))
    if not errors:
        return
    shown = [
        {"path": _pointer(error.absolute_path), "message": error.message}
        for error in errors[:_MAX_SHAPE_ERRORS]
    ]
    raise WorkflowDefinitionError(
        f"Workflow sai hình dạng ở {len(errors)} chỗ.",
        details={"errors": shown},
    )


def _ensure_known_node_types(document: Mapping[str, Any]) -> None:
    """Schema để node lạ đi qua (Q2); runtime thì không chạy được nó."""
    for index, raw in enumerate(document["nodes"]):
        _reject_unknown(raw, _KNOWN_TOP, f"/nodes/{index}")
        if raw["type"] == "loop":
            for inner_index, inner in enumerate(raw["body"]):
                _reject_unknown(inner, _KNOWN_BODY, f"/nodes/{index}/body/{inner_index}")


def _reject_unknown(raw: Mapping[str, Any], known: frozenset[str], where: str) -> None:
    if raw["type"] not in known:
        raise WorkflowDefinitionError(
            f"IR version này chưa chạy được node loại {raw['type']!r} (node {raw['id']!r}).",
            details={"path": where, "node": raw["id"], "type": raw["type"]},
        )


def _pointer(path: Any) -> str:
    return "".join(f"/{segment}" for segment in path)


def _node(raw: Mapping[str, Any]) -> Node:
    kind = raw["type"]
    label = raw.get("label")
    if kind == "trigger":
        return TriggerNode(
            id=raw["id"], trigger=raw["trigger"], input_schema=raw["input_schema"], label=label
        )
    if kind == "condition":
        right = raw.get("right")
        return ConditionNode(
            id=raw["id"],
            left=_binding(raw["left"]),
            op=ConditionOp(raw["op"]),
            right=None if right is None else _binding(right),
            label=label,
        )
    if kind == "loop":
        return LoopNode(
            id=raw["id"],
            over=_binding(raw["over"]),
            max_items=raw["max_items"],
            body=tuple(_body_node(inner) for inner in raw["body"]),
            collect=raw["collect"],
            filter=_scalar_map(raw.get("filter")),
            label=label,
        )
    return _body_node(raw)


def _body_node(raw: Mapping[str, Any]) -> BodyNode:
    kind = raw["type"]
    label = raw.get("label")
    if kind == "tool":
        return ToolNode(
            id=raw["id"],
            tool_id=raw["tool_id"],
            tool_version=raw["tool_version"],
            input=_binding_map(raw["input"]),
            label=label,
        )
    return LlmNode(
        id=raw["id"],
        model=raw["model"],
        instructions=raw["instructions"],
        input=_binding_map(raw["input"]),
        output_schema=raw["output_schema"],
        label=label,
    )


def _binding_map(raw: Mapping[str, Any]) -> dict[str, Binding]:
    return {key: _binding(value) for key, value in raw.items()}


def _scalar_map(raw: Mapping[str, Any] | None) -> dict[str, ScalarBinding] | None:
    if raw is None:
        return None
    return {key: _scalar(value) for key, value in raw.items()}


def _scalar(raw: Mapping[str, Any]) -> ScalarBinding:
    binding = _binding(raw)
    assert isinstance(binding, Const | FromInput | FromItem)  # bảo đảm bởi schema
    return binding


def _binding(raw: Mapping[str, Any]) -> Binding:
    if "const" in raw:
        return Const(raw["const"])
    if "from_input" in raw:
        return FromInput(raw["from_input"])
    if "from_item" in raw:
        return FromItem(tuple(raw["from_item"]))
    if "from_connection" in raw:
        source = raw["from_connection"]
        return FromConnection(provider=source["provider"], field=source["field"])
    source = raw["from_node"]
    pick = source.get("pick")
    return FromNode(
        node=source["node"],
        path=tuple(source["path"]),
        where=_scalar_map(source.get("where")),
        pick=None if pick is None else tuple(pick),
    )


# ─── Dựng lại ────────────────────────────────────────────────────────────────


def to_document(ir: WorkflowIR) -> dict[str, Any]:
    """`WorkflowIR` → tài liệu JSON. `parse(to_document(ir)) == ir`."""
    document: dict[str, Any] = {
        "schema_version": ir.schema_version,
        "nodes": [_node_document(node) for node in ir.nodes],
        "edges": [_edge_document(edge) for edge in ir.edges],
    }
    if ir.connections:
        document["connections"] = dict(ir.connections)
    return document


def _edge_document(edge: Edge) -> dict[str, Any]:
    raw: dict[str, Any] = {"from": edge.source, "to": edge.target}
    if edge.branch is not None:
        raw["branch"] = edge.branch
    return raw


def _node_document(node: Node) -> dict[str, Any]:
    raw: dict[str, Any] = {"id": node.id}
    match node:
        case TriggerNode():
            raw |= {"type": "trigger", "trigger": node.trigger, "input_schema": node.input_schema}
        case ToolNode():
            raw |= {
                "type": "tool",
                "tool_id": node.tool_id,
                "tool_version": node.tool_version,
                "input": _binding_map_document(node.input),
            }
        case ConditionNode():
            raw |= {"type": "condition", "left": _binding_document(node.left), "op": str(node.op)}
            if node.right is not None:
                raw["right"] = _binding_document(node.right)
        case LlmNode():
            raw |= {
                "type": "llm",
                "model": node.model,
                "instructions": node.instructions,
                "input": _binding_map_document(node.input),
                "output_schema": node.output_schema,
            }
        case LoopNode():
            raw |= {
                "type": "loop",
                "over": _binding_document(node.over),
                "max_items": node.max_items,
                "body": [_node_document(inner) for inner in node.body],
                "collect": node.collect,
            }
            if node.filter is not None:
                raw["filter"] = _binding_map_document(node.filter)
    if node.label is not None:
        raw["label"] = node.label
    return raw


def _binding_map_document(bindings: Mapping[str, Binding]) -> dict[str, Any]:
    return {key: _binding_document(value) for key, value in bindings.items()}


def _binding_document(binding: Binding) -> dict[str, Any]:
    match binding:
        case Const():
            return {"const": binding.value}
        case FromInput():
            return {"from_input": binding.name}
        case FromItem():
            return {"from_item": list(binding.path)}
        case FromConnection():
            return {"from_connection": {"provider": binding.provider, "field": binding.field}}
        case FromNode():
            source: dict[str, Any] = {"node": binding.node, "path": list(binding.path)}
            if binding.where is not None:
                source["where"] = _binding_map_document(binding.where)
            if binding.pick is not None:
                source["pick"] = list(binding.pick)
            return {"from_node": source}
