"""Luật của §39 mà chỉ cần nhìn chính tài liệu IR.

Luật cần catalog tool — input khớp schema của tool, đường `from_node` có trong
output — nằm ở `workflow/application/validation.py`.

Trả về báo cáo chứ không ném ở lỗi đầu tiên: editor cần tô mọi chỗ sai một lần,
và compiler (ADR-0008) cần biết draft nó sinh ra hỏng ở đâu.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

import jsonschema

from app.core import codeguard
from app.core.errors import WorkflowDefinitionError
from app.domains.workflow.domain.ir import (
    Binding,
    ConditionNode,
    Const,
    FromConnection,
    FromInput,
    FromItem,
    FromNode,
    LlmNode,
    LoopNode,
    Node,
    Path,
    ToolNode,
    TransformNode,
    TriggerNode,
    WorkflowIR,
)

#: Cùng trần với `Limits.code_bytes` của sandbox: code dài hơn thì lúc chạy cũng bị từ chối.
MAX_CODE_BYTES: Final = 4096
#: Chuỗi dài hơn thế này trong code nhiều khả năng là nội dung nghiệp vụ, không phải tên trường.
_LONG_STRING: Final = 32
#: Số mà code nào cũng dùng. Số khác — một ngưỡng, một giới hạn — nên đi qua `args`.
_PLAIN_NUMBERS: Final = frozenset({0, 1})


class Severity(StrEnum):
    ERROR = "error"
    #: Chạy được, nhưng gần như chắc là không đúng ý.
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    message: str
    severity: Severity = Severity.ERROR
    node: str | None = None
    field: Path = ()


@dataclass(frozen=True, slots=True)
class ValidationReport:
    issues: tuple[Issue, ...] = ()

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Issue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is Severity.WARNING)

    @property
    def ok(self) -> bool:
        return not self.errors

    def merged(self, other: ValidationReport) -> ValidationReport:
        return ValidationReport(self.issues + other.issues)


def ensure_valid(report: ValidationReport) -> None:
    """Ném nếu còn lỗi. Cảnh báo không chặn."""
    if report.ok:
        return
    raise WorkflowDefinitionError(
        f"Workflow chưa hợp lệ: {len(report.errors)} lỗi.",
        details={
            "issues": [
                {"code": i.code, "message": i.message, "node": i.node, "field": list(i.field)}
                for i in report.errors
            ]
        },
    )


# ─── Literal (ADR-0008, quyết định 3) ────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LiteralSite:
    """Một giá trị hằng, và đường duy nhất tới nó.

    `node` là đường node: `("dang",)` ở tầng ngoài, `("moi_trang", "doc_trang")`
    trong thân loop. `field` là đường trong node: `("input", "channel")`,
    `("filter", "type")`, `("input", "channel", "where", "name")`.
    """

    node: Path
    field: Path
    value: Any


def literals(ir: WorkflowIR) -> tuple[LiteralSite, ...]:
    """Mọi `const` trong tài liệu — thứ chặng 3 của compiler hỏi về."""
    found: list[LiteralSite] = []
    for loop, node in ir.walk():
        address = (node.id,) if loop is None else (loop.id, node.id)
        for field, binding in bindings_of(node):
            found.extend(
                LiteralSite(node=address, field=field + inner, value=value)
                for inner, value in _consts(binding)
            )
    return tuple(found)


def _consts(binding: Binding) -> Iterator[tuple[Path, Any]]:
    if isinstance(binding, Const):
        yield (), binding.value
    elif isinstance(binding, FromNode) and binding.where is not None:
        for key, value in binding.where.items():
            if isinstance(value, Const):
                yield ("where", key), value.value


def bindings_of(node: Node) -> Iterator[tuple[Path, Binding]]:
    """Mọi binding của riêng node này — không đi vào thân loop."""
    match node:
        case ToolNode() | LlmNode():
            for key, binding in node.input.items():
                yield ("input", key), binding
        case ConditionNode():
            yield ("left",), node.left
            if node.right is not None:
                yield ("right",), node.right
        case TransformNode():
            for key, binding in node.args.items():
                yield ("args", key), binding
        case LoopNode():
            yield ("over",), node.over
            for key, binding in (node.filter or {}).items():
                yield ("filter", key), binding
        case TriggerNode():
            return


# ─── Cấu trúc ────────────────────────────────────────────────────────────────


def validate_structure(ir: WorkflowIR) -> ValidationReport:
    issues: list[Issue] = []
    issues += _ids(ir)
    issues += _trigger(ir)
    graph_issues, acyclic = _graph(ir)
    issues += graph_issues
    issues += _loops(ir)
    issues += _schemas(ir)
    issues += _code(ir)
    if acyclic:
        issues += _bindings(ir)
    return ValidationReport(tuple(issues))


def _ids(ir: WorkflowIR) -> Iterator[Issue]:
    seen: set[str] = set()
    for _, node in ir.walk():
        if node.id in seen:
            yield Issue("id_trung", f"Id {node.id!r} dùng cho hơn một node.", node=node.id)
        seen.add(node.id)


def _trigger(ir: WorkflowIR) -> Iterator[Issue]:
    triggers = [node for node in ir.nodes if isinstance(node, TriggerNode)]
    if len(triggers) != 1:
        yield Issue(
            "trigger_so_luong",
            f"Workflow phải có đúng một trigger, đang có {len(triggers)}.",
        )


def _graph(ir: WorkflowIR) -> tuple[list[Issue], bool]:
    """Cạnh, chu trình, node mồ côi. Trả kèm cờ "không chu trình" — các luật
    binding dựa vào quan hệ tổ tiên, và quan hệ đó vô nghĩa trên đồ thị vòng."""
    issues: list[Issue] = []
    top = {node.id: node for node in ir.nodes}
    seen_edges: set[tuple[str, str, str | None]] = set()

    for edge in ir.edges:
        key = (edge.source, edge.target, edge.branch)
        if key in seen_edges:
            issues.append(Issue("canh_trung", f"Cạnh {edge.source} → {edge.target} khai hai lần."))
        seen_edges.add(key)

        missing = [end for end in (edge.source, edge.target) if end not in top]
        if missing:
            issues.append(
                Issue(
                    "canh_node_khong_ton_tai",
                    f"Cạnh {edge.source} → {edge.target} trỏ vào node không có ở tầng ngoài: "
                    f"{', '.join(missing)}.",
                )
            )
            continue
        if edge.source == edge.target:
            issues.append(Issue("canh_tu_than", f"Node {edge.source} có cạnh về chính nó."))
        if isinstance(top[edge.target], TriggerNode):
            issues.append(
                Issue("canh_vao_trigger", "Trigger là điểm vào; không cạnh nào đi tới nó.")
            )

        from_condition = isinstance(top[edge.source], ConditionNode)
        if from_condition and edge.branch is None:
            issues.append(
                Issue(
                    "nhanh_thieu",
                    f"Cạnh ra từ condition {edge.source} phải nói nó là nhánh true hay false.",
                    node=edge.source,
                )
            )
        if not from_condition and edge.branch is not None:
            issues.append(
                Issue(
                    "nhanh_thua",
                    f"Chỉ cạnh ra từ condition mới có nhánh; {edge.source} không phải condition.",
                    node=edge.source,
                )
            )

    successors = _successors(ir, top)
    for node in ir.nodes:
        if isinstance(node, ConditionNode) and not successors[node.id]:
            issues.append(
                Issue(
                    "condition_khong_loi_ra",
                    f"Condition {node.id} không dẫn tới đâu cả.",
                    node=node.id,
                )
            )

    acyclic = _find_cycle(successors) is None
    if not acyclic:
        issues.append(
            Issue("chu_trinh", "Đồ thị có chu trình; workflow v1 không có vòng lặp qua cạnh.")
        )

    roots = [node.id for node in ir.nodes if isinstance(node, TriggerNode)]
    if roots:
        reachable = _reach(roots, successors)
        for node in ir.nodes:
            if node.id not in reachable:
                issues.append(
                    Issue(
                        "node_mo_coi",
                        f"Node {node.id} không đi tới được từ trigger nên sẽ không bao giờ chạy.",
                        node=node.id,
                    )
                )
    return issues, acyclic


def _successors(ir: WorkflowIR, top: Mapping[str, Node]) -> dict[str, set[str]]:
    successors: dict[str, set[str]] = {node_id: set() for node_id in top}
    for edge in ir.edges:
        if edge.source in top and edge.target in top:
            successors[edge.source].add(edge.target)
    return successors


def _reach(starts: Iterable[str], successors: Mapping[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(starts)
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(successors.get(current, ()))
    return seen


def _find_cycle(successors: Mapping[str, set[str]]) -> str | None:
    white, grey, black = 0, 1, 2
    colour = dict.fromkeys(successors, white)

    def visit(start: str) -> str | None:
        stack: list[tuple[str, Iterator[str]]] = [(start, iter(sorted(successors[start])))]
        colour[start] = grey
        while stack:
            current, children = stack[-1]
            child = next(children, None)
            if child is None:
                colour[current] = black
                stack.pop()
            elif colour[child] == grey:
                return child
            elif colour[child] == white:
                colour[child] = grey
                stack.append((child, iter(sorted(successors[child]))))
        return None

    for node_id in successors:
        if colour[node_id] == white and (found := visit(node_id)) is not None:
            return found
    return None


def _loops(ir: WorkflowIR) -> Iterator[Issue]:
    for node in ir.nodes:
        if not isinstance(node, LoopNode):
            continue
        if node.collect not in {inner.id for inner in node.body}:
            yield Issue(
                "collect_khong_trong_than",
                f"Loop {node.id} gom kết quả từ {node.collect!r}, nhưng node đó không nằm "
                "trong thân.",
                node=node.id,
                field=("collect",),
            )
        for inner in node.body:
            if isinstance(inner, LlmNode):
                yield Issue(
                    "llm_trong_loop",
                    f"Node llm {inner.id} nằm trong loop {node.id}: tối đa {node.max_items} "
                    "lời gọi model cho một lần chạy.",
                    severity=Severity.WARNING,
                    node=inner.id,
                )


def _schemas(ir: WorkflowIR) -> Iterator[Issue]:
    for _, node in ir.walk():
        if isinstance(node, TriggerNode):
            yield from _check_schema(node.id, ("input_schema",), node.input_schema)
        elif isinstance(node, LlmNode | TransformNode):
            yield from _check_schema(node.id, ("output_schema",), node.output_schema)


def _check_schema(node_id: str, field: Path, schema: Mapping[str, Any]) -> Iterator[Issue]:
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        yield Issue(
            "json_schema_sai",
            f"{'.'.join(field)} của {node_id} không phải JSON Schema hợp lệ: {exc.message}",
            node=node_id,
            field=field,
        )


# ─── Code ────────────────────────────────────────────────────────────────────


def _code(ir: WorkflowIR) -> Iterator[Issue]:
    """Chỉ đọc code, không chạy nó: sandbox không bao giờ được nạp ở chỗ publish."""
    for _, node in ir.walk():
        if not isinstance(node, TransformNode):
            continue
        size = len(node.code.encode("utf-8"))
        if size > MAX_CODE_BYTES:
            yield Issue(
                "code_qua_dai",
                f"Code của {node.id} dài {size} byte, trần là {MAX_CODE_BYTES} byte.",
                node=node.id,
                field=("code",),
            )
            continue
        try:
            tree = codeguard.parse(node.code)
        except codeguard.UnsafeCodeError as exc:
            # Dòng 1 là dòng `def` bọc ngoài.
            line = "" if exc.line is None else f" (dòng {max(exc.line - 1, 1)})"
            yield Issue(
                "code_khong_an_toan",
                f"Code của {node.id} không chạy được trong sandbox{line}: {exc.reason}",
                node=node.id,
                field=("code",),
            )
            continue
        hard_coded = _hard_coded(tree)
        if hard_coded:
            yield Issue(
                "hang_viet_cung",
                f"Code của {node.id} viết cứng {', '.join(hard_coded)}. Giá trị nghiệp vụ nên "
                "đi vào qua `args`, để đổi được mà không sửa code.",
                severity=Severity.WARNING,
                node=node.id,
                field=("code",),
            )


def _hard_coded(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        value = node.value
        if isinstance(value, str) and len(value) > _LONG_STRING:
            found.append(repr(value[:_LONG_STRING] + "…"))
        elif (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and value not in _PLAIN_NUMBERS
        ):
            found.append(repr(value))
    return found


# ─── Binding ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Scope:
    """Những node mà một binding ở vị trí này được phép đọc."""

    readable: frozenset[str]
    #: Loop mà binding này nằm trong thân, nếu có.
    loop: str | None
    #: Trong `filter` của loop, `from_item` không có nghĩa — phần tử chưa được chọn.
    item_allowed: bool


def _bindings(ir: WorkflowIR) -> Iterator[Issue]:
    top = {node.id: node for node in ir.nodes}
    every = {node.id: node for _, node in ir.walk()}
    ancestors = _ancestors(ir, top)
    inputs = _declared_inputs(ir)
    owner = {inner.id: loop.id for loop, inner in ir.walk() if loop is not None}

    for node in ir.nodes:
        above = ancestors.get(node.id, frozenset())
        scope = _Scope(readable=above, loop=None, item_allowed=False)
        if isinstance(node, LoopNode):
            yield from _check(node.id, ("over",), node.over, scope, every, owner, inputs)
            for key, scalar in (node.filter or {}).items():
                yield from _check(node.id, ("filter", key), scalar, scope, every, owner, inputs)
            earlier: set[str] = set()
            for inner in node.body:
                inner_scope = _Scope(
                    readable=above | frozenset(earlier), loop=node.id, item_allowed=True
                )
                for field, binding in bindings_of(inner):
                    yield from _check(inner.id, field, binding, inner_scope, every, owner, inputs)
                earlier.add(inner.id)
        else:
            for field, binding in bindings_of(node):
                yield from _check(node.id, field, binding, scope, every, owner, inputs)


def _check(
    node_id: str,
    field: Path,
    binding: Binding,
    scope: _Scope,
    every: Mapping[str, Node],
    owner: Mapping[str, str],
    inputs: frozenset[str] | None,
) -> Iterator[Issue]:
    if isinstance(binding, FromItem) and not scope.item_allowed:
        where = "trong filter của loop" if field[:1] == ("filter",) else "ngoài thân loop"
        yield Issue(
            "from_item_sai_cho",
            f"from_item chỉ dùng được trong thân loop; {node_id}.{'.'.join(field)} nằm {where}.",
            node=node_id,
            field=field,
        )
    elif isinstance(binding, FromInput) and inputs is not None and binding.name not in inputs:
        yield Issue(
            "from_input_khong_khai",
            f"{node_id}.{'.'.join(field)} đọc input {binding.name!r} mà trigger không khai.",
            node=node_id,
            field=field,
        )
    elif isinstance(binding, FromNode):
        yield from _check_from_node(node_id, field, binding, scope, every, owner, inputs)
    elif isinstance(binding, FromConnection) and not isinstance(every.get(binding.node), ToolNode):
        yield Issue(
            "from_connection_khong_phai_tool",
            f"{node_id}.{'.'.join(field)} đọc connection của {binding.node!r}, "
            "nhưng chỉ node tool mới có connection.",
            node=node_id,
            field=field,
        )


def _check_from_node(
    node_id: str,
    field: Path,
    binding: FromNode,
    scope: _Scope,
    every: Mapping[str, Node],
    owner: Mapping[str, str],
    inputs: frozenset[str] | None,
) -> Iterator[Issue]:
    location = f"{node_id}.{'.'.join(field)}"
    source = every.get(binding.node)
    if source is None:
        yield Issue(
            "binding_node_khong_ton_tai",
            f"{location} đọc node {binding.node!r} không tồn tại.",
            node=node_id,
            field=field,
        )
        return
    if isinstance(source, TriggerNode):
        yield Issue(
            "binding_doc_trigger",
            f"{location} đọc trigger bằng from_node; dùng from_input.",
            node=node_id,
            field=field,
        )
        return
    if isinstance(source, ConditionNode):
        yield Issue(
            "binding_doc_condition",
            f"{location} đọc condition {binding.node!r}, mà condition không có output.",
            node=node_id,
            field=field,
        )
        return
    source_loop = owner.get(binding.node)
    if source_loop is not None and source_loop != scope.loop:
        yield Issue(
            "binding_doc_than_loop",
            f"{location} đọc {binding.node!r} trong thân loop {source_loop}; ngoài thân đó chỉ "
            f"đọc được output của chính {source_loop}.",
            node=node_id,
            field=field,
        )
    elif binding.node not in scope.readable:
        yield Issue(
            "binding_node_khong_o_phia_tren",
            f"{location} đọc {binding.node!r}, nhưng node đó không chắc đã chạy trước {node_id}.",
            node=node_id,
            field=field,
        )

    raw_indexes = [s for s in (*binding.path, *(binding.pick or ())) if s.isdigit()]
    if raw_indexes:
        yield Issue(
            "chi_so_tho",
            f"{location} chọn phần tử theo vị trí ({', '.join(raw_indexes)}). Thứ tự provider trả "
            "về có thể đổi; cân nhắc `where`.",
            severity=Severity.WARNING,
            node=node_id,
            field=field,
        )

    for key, value in (binding.where or {}).items():
        yield from _check(node_id, (*field, "where", key), value, scope, every, owner, inputs)


def _ancestors(ir: WorkflowIR, top: Mapping[str, Node]) -> dict[str, frozenset[str]]:
    predecessors: dict[str, set[str]] = {node_id: set() for node_id in top}
    for edge in ir.edges:
        if edge.source in top and edge.target in top:
            predecessors[edge.target].add(edge.source)
    return {node_id: frozenset(_reach(predecessors[node_id], predecessors)) for node_id in top}


def _declared_inputs(ir: WorkflowIR) -> frozenset[str] | None:
    """Tên input trigger khai. `None` khi không biết — không có trigger, hoặc
    `input_schema` không liệt kê `properties` — để không báo lỗi oan."""
    for node in ir.nodes:
        if isinstance(node, TriggerNode):
            properties = node.input_schema.get("properties")
            if isinstance(properties, Mapping):
                return frozenset(properties)
    return None
