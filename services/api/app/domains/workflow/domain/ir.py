"""Workflow IR v1 dưới dạng kiểu mà domain đọc được."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Literal

import jsonschema

from app.core.errors import WorkflowDefinitionError
from app.domains.workflow.domain.generated.ir_v1 import IR_SCHEMA as IR_V1_SCHEMA
from app.domains.workflow.domain.generated.ir_v2 import IR_SCHEMA as IR_V2_SCHEMA

CURRENT_MAJOR: Final = 2
#: Major cũ vẫn phải diễn giải được: version đã publish là bất biến.
SUPPORTED_MAJORS: Final = frozenset({1, 2})

#: Trần của một tài liệu IR, đo trên JSON chuẩn hoá. Plan đi vào lịch sử Temporal
MAX_DOCUMENT_BYTES: Final = 256 * 1024

#: Số lỗi hình dạng tối đa trả về trong một lần — đủ để sửa, không đủ để ngập.
_MAX_SHAPE_ERRORS: Final = 20

_VALIDATORS: Final = {
    1: jsonschema.Draft202012Validator(IR_V1_SCHEMA),
    2: jsonschema.Draft202012Validator(IR_V2_SCHEMA),
}

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
    """Metadata của connection mà node tool `node` dùng ở lần chạy này."""

    node: str
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


@dataclass(frozen=True, slots=True)
class TransformNode:
    """Chạy `code` trong sandbox. Output của node là giá trị `code` trả về."""

    id: str
    #: Thân của một hàm nhận đúng một tham số `args`.
    code: str
    args: Mapping[str, Binding]
    output_schema: Mapping[str, Any]
    label: str | None = None


BodyNode = ToolNode | LlmNode | TransformNode


@dataclass(frozen=True, slots=True)
class LoopNode:
    id: str
    over: Binding
    max_items: int
    body: tuple[BodyNode, ...]
    collect: str
    filter: Mapping[str, ScalarBinding] | None = None
    label: str | None = None


Node = TriggerNode | ToolNode | ConditionNode | LlmNode | LoopNode | TransformNode


@dataclass(frozen=True, slots=True)
class Edge:
    source: str
    target: str
    branch: Branch | None = None


@dataclass(frozen=True, slots=True)
class WorkflowIR:
    """Không mang connection: lần chạy chọn connection cho từng node tool."""

    schema_version: str
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]

    def walk(self) -> Iterator[tuple[LoopNode | None, Node]]:
        """Mọi node, kể cả trong thân loop, kèm loop bao nó."""
        for node in self.nodes:
            yield None, node
            if isinstance(node, LoopNode):
                for inner in node.body:
                    yield node, inner


# ─── Parse ───────────────────────────────────────────────────────────────────

_KNOWN_TOP: Final = frozenset({"trigger", "tool", "condition", "llm", "loop", "transform"})
_KNOWN_BODY: Final = frozenset({"tool", "llm", "transform"})
#: Loại node thêm vào sau `major.0`, kèm minor đầu tiên có nó.
_SINCE_MINOR: Final = {"transform": 1}


def parse(document: Mapping[str, Any]) -> WorkflowIR:
    """Tài liệu JSON → `WorkflowIR`. Ném `WorkflowDefinitionError` nếu không chạy được."""
    size = len(json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if size > MAX_DOCUMENT_BYTES:
        raise WorkflowDefinitionError(
            f"Workflow quá lớn: {size} byte, trần là {MAX_DOCUMENT_BYTES} byte.",
            details={"byte_size": size, "max_bytes": MAX_DOCUMENT_BYTES},
        )

    major = _major(document.get("schema_version"))
    if major == 1:
        _ensure_shape(document, 1)
        document = upgrade_v1(document)
    _ensure_shape(document, CURRENT_MAJOR)
    _ensure_known_node_types(document)
    _ensure_declared_minor(document)

    return WorkflowIR(
        schema_version=document["schema_version"],
        nodes=tuple(_node(raw) for raw in document["nodes"]),
        edges=tuple(
            Edge(source=raw["from"], target=raw["to"], branch=raw.get("branch"))
            for raw in document["edges"]
        ),
    )


def upgrade_v1(document: Mapping[str, Any]) -> dict[str, Any]:
    """IR v1 → v2: bỏ `connections`; `from_connection` theo provider thành theo node.

    Node được chọn là node tool duy nhất của provider đó, nhận ra qua tiền tố
    `tool_id` — đúng với mọi tool trong catalog. Không có hoặc có nhiều hơn một
    thì không diễn giải được một cách chắc chắn.
    """
    upgraded = copy.deepcopy(dict(document))
    upgraded.pop("connections", None)
    upgraded["schema_version"] = f"{CURRENT_MAJOR}.0"

    owners: dict[str, list[str]] = {}
    for node in upgraded["nodes"]:
        for inner in (node, *node.get("body", ())):
            if inner.get("type") == "tool":
                provider = inner["tool_id"].partition(".")[0]
                owners.setdefault(provider, []).append(inner["id"])

    def rewrite(value: Any) -> Any:
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if not isinstance(value, dict) or "const" in value:
            return value
        source = value.get("from_connection")
        if isinstance(source, dict) and "provider" in source:
            candidates = owners.get(source["provider"], [])
            if len(candidates) != 1:
                raise WorkflowDefinitionError(
                    f"from_connection của provider {source['provider']!r} không nâng lên IR "
                    f"v{CURRENT_MAJOR} được: có {len(candidates)} node tool dùng provider đó.",
                    details={"provider": source["provider"], "nodes": candidates},
                )
            return {"from_connection": {"node": candidates[0], "field": source["field"]}}
        return {key: rewrite(item) for key, item in value.items()}

    upgraded["nodes"] = rewrite(upgraded["nodes"])
    return upgraded


def _major(raw: object) -> int:
    """Chặn trước jsonschema: `pattern` sẽ chỉ nói "không khớp", không nói vì sao."""
    if not isinstance(raw, str):
        return CURRENT_MAJOR
    major, _, _ = raw.partition(".")
    if not major.isdigit():
        return CURRENT_MAJOR
    if int(major) not in SUPPORTED_MAJORS:
        raise WorkflowDefinitionError(
            f"IR version {raw} chưa chạy được; runtime này hiểu major "
            f"{', '.join(str(m) for m in sorted(SUPPORTED_MAJORS))}.",
            details={"schema_version": raw, "supported_majors": sorted(SUPPORTED_MAJORS)},
        )
    return int(major)


def _ensure_shape(document: Mapping[str, Any], major: int) -> None:
    errors = sorted(_VALIDATORS[major].iter_errors(document), key=lambda e: list(e.absolute_path))
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


def _ensure_declared_minor(document: Mapping[str, Any]) -> None:
    """Tài liệu khai `2.0` thì không được chứa thứ chỉ có từ `2.1`: một trình đọc cũ tin
    vào con số đó để biết nó hiểu được tài liệu hay không (§41)."""
    minor = int(document["schema_version"].partition(".")[2])
    for raw in document["nodes"]:
        for inner in (raw, *raw.get("body", ())):
            since = _SINCE_MINOR.get(inner["type"], 0)
            if since > minor:
                raise WorkflowDefinitionError(
                    f"Node loại {inner['type']!r} (node {inner['id']!r}) có từ IR "
                    f"{CURRENT_MAJOR}.{since}, mà tài liệu khai {document['schema_version']}.",
                    details={
                        "node": inner["id"],
                        "type": inner["type"],
                        "schema_version": document["schema_version"],
                        "since": f"{CURRENT_MAJOR}.{since}",
                    },
                )


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
    if kind == "transform":
        return TransformNode(
            id=raw["id"],
            code=raw["code"],
            args=_binding_map(raw["args"]),
            output_schema=raw["output_schema"],
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
        return FromConnection(node=source["node"], field=source["field"])
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
        case TransformNode():
            raw |= {
                "type": "transform",
                "code": node.code,
                "args": _binding_map_document(node.args),
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
            return {"from_connection": {"node": binding.node, "field": binding.field}}
        case FromNode():
            source: dict[str, Any] = {"node": binding.node, "path": list(binding.path)}
            if binding.where is not None:
                source["where"] = _binding_map_document(binding.where)
            if binding.pick is not None:
                source["pick"] = list(binding.pick)
            return {"from_node": source}
