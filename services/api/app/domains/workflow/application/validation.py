"""Luật của §39 cần nhìn catalog tool: input khớp schema của tool, đường
`from_node` có trong output của node nguồn.

Luật chỉ cần chính tài liệu nằm ở `workflow/domain/validation.py`; `validate`
chạy cả hai.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import jsonschema

from app.domains.workflow.application.ports import ToolCatalogReader, ToolContract
from app.domains.workflow.domain.ir import (
    Binding,
    Const,
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
from app.domains.workflow.domain.schema_paths import (
    Located,
    is_array,
    items_of,
    locate,
    locate_from,
)
from app.domains.workflow.domain.validation import (
    Issue,
    ValidationReport,
    bindings_of,
    validate_structure,
)


def validate(ir: WorkflowIR, catalog: ToolCatalogReader) -> ValidationReport:
    structure = validate_structure(ir)
    return structure.merged(ValidationReport(tuple(_CatalogCheck(ir, catalog).run())))


class _CatalogCheck:
    def __init__(self, ir: WorkflowIR, catalog: ToolCatalogReader) -> None:
        self._ir = ir
        self._catalog = catalog
        self._nodes = {node.id: node for _, node in ir.walk()}
        self._contracts: dict[str, ToolContract] = {}
        self._trigger = next((n for n in ir.nodes if isinstance(n, TriggerNode)), None)

    def run(self) -> Iterator[Issue]:
        yield from self._tools()
        for loop, node in self._ir.walk():
            item = self._item_schema(loop) if loop is not None else None
            if isinstance(node, LoopNode):
                yield from self._loop(node)
            for field, binding in bindings_of(node):
                yield from self._binding(node.id, field, binding, item)

    # ─── Tool ────────────────────────────────────────────────────────────────

    def _tools(self) -> Iterator[Issue]:
        for _, node in self._ir.walk():
            if not isinstance(node, ToolNode):
                continue
            contract = self._catalog.find(node.tool_id, node.tool_version)
            if contract is None:
                available = self._catalog.versions(node.tool_id)
                hint = f" Bản đang có: {', '.join(available)}." if available else ""
                yield Issue(
                    "tool_khong_ton_tai",
                    f"Không có tool {node.tool_id} bản {node.tool_version}.{hint}",
                    node=node.id,
                    field=("tool_version",) if available else ("tool_id",),
                )
                continue
            self._contracts[node.id] = contract
            if not contract.enabled:
                yield Issue("tool_da_tat", f"Tool {node.tool_id} đang bị tắt.", node=node.id)
            if contract.needs_approval:
                yield Issue(
                    "tool_can_phe_duyet",
                    f"{node.tool_id} cần người phê duyệt mỗi lần chạy, và workflow v1 chưa có node "
                    "phê duyệt nên nó sẽ luôn bị chặn.",
                    node=node.id,
                )
            yield from self._input(node, contract)

    def _input(self, node: ToolNode, contract: ToolContract) -> Iterator[Issue]:
        schema = contract.input_schema
        properties = schema.get("properties", {})
        for name in schema.get("required", ()):
            if name not in node.input:
                yield Issue(
                    "input_thieu",
                    f"{node.id} thiếu input bắt buộc {name!r} của {node.tool_id}.",
                    node=node.id,
                    field=("input", name),
                )
        if schema.get("additionalProperties") is False:
            for name in node.input:
                if name not in properties:
                    yield Issue(
                        "input_truong_la",
                        f"{node.tool_id} không có input {name!r}.",
                        node=node.id,
                        field=("input", name),
                    )

        # Kiểm hằng số trên cả schema chứ không trên từng property: property có
        # thể `$ref` vào `$defs` của gốc, và tách nó ra thì tham chiếu gãy.
        consts = {
            name: binding.value
            for name, binding in node.input.items()
            if isinstance(binding, Const) and name in properties
        }
        for error in jsonschema.Draft202012Validator(schema).iter_errors(consts):
            if not error.absolute_path:
                continue  # `required` ở gốc — đã báo ở trên, với binding không phải hằng
            name = str(error.absolute_path[0])
            yield Issue(
                "input_hang_sai",
                f"{node.id}.input.{name}: {error.message}",
                node=node.id,
                field=("input", *(str(s) for s in error.absolute_path)),
            )

    # ─── Đường dữ liệu ───────────────────────────────────────────────────────

    def _binding(
        self, node_id: str, field: Path, binding: Binding, item: Located | None
    ) -> Iterator[Issue]:
        location = f"{node_id}.{'.'.join(field)}"
        if isinstance(binding, FromItem) and item is not None and item.schema is not None:
            yield from self._missing(
                location,
                node_id,
                field,
                "phần tử đang lặp",
                binding.path,
                locate_from(item.root, item.schema, binding.path),
            )
        elif isinstance(binding, FromNode):
            yield from self._from_node(node_id, field, binding, item)

    def _from_node(
        self, node_id: str, field: Path, binding: FromNode, item: Located | None
    ) -> Iterator[Issue]:
        location = f"{node_id}.{'.'.join(field)}"
        root = self._output_schema(binding.node)
        if root is None:
            return
        at = locate(root, binding.path)
        yield from self._missing(
            location, node_id, field, f"output của {binding.node}", binding.path, at
        )
        if binding.where is None or at.schema is None:
            return
        if not is_array(at.schema):
            yield Issue(
                "where_khong_tren_mang",
                f"{location} dùng `where` trên {'.'.join(binding.path)}, mà đó không phải mảng.",
                node=node_id,
                field=field,
            )
            return
        element = items_of(at.root, at.schema)
        if element is None:
            return
        for key, value in binding.where.items():
            yield from self._missing(
                location,
                node_id,
                (*field, "where", key),
                f"phần tử của {binding.node}.{'.'.join(binding.path)}",
                (key,),
                locate_from(at.root, element, (key,)),
            )
            yield from self._binding(node_id, (*field, "where", key), value, item)
        if binding.pick is not None:
            yield from self._missing(
                location,
                node_id,
                (*field, "pick"),
                f"phần tử của {binding.node}.{'.'.join(binding.path)}",
                binding.pick,
                locate_from(at.root, element, binding.pick),
            )

    def _loop(self, node: LoopNode) -> Iterator[Issue]:
        over = self._schema_of(node.over)
        if over is None or over.schema is None:
            return
        if not is_array(over.schema):
            yield Issue(
                "loop_over_khong_phai_mang",
                f"Loop {node.id} lặp trên một giá trị không phải mảng.",
                node=node.id,
                field=("over",),
            )
            return
        element = items_of(over.root, over.schema)
        if element is None:
            return
        for key in node.filter or {}:
            yield from self._missing(
                f"{node.id}.filter",
                node.id,
                ("filter", key),
                "phần tử đang lặp",
                (key,),
                locate_from(over.root, element, (key,)),
            )

    def _missing(
        self, location: str, node_id: str, field: Path, where: str, path: Path, at: Located
    ) -> Iterator[Issue]:
        if at.missing_at is None:
            return
        yield Issue(
            "duong_khong_ton_tai",
            f"{location}: {where} không có {'.'.join(path[: at.missing_at + 1])}.",
            node=node_id,
            field=field,
        )

    # ─── Schema của output ───────────────────────────────────────────────────

    def _output_schema(self, node_id: str) -> Mapping[str, Any] | None:
        node = self._nodes.get(node_id)
        return None if node is None else self._output_of(node)

    def _output_of(self, node: Node) -> Mapping[str, Any] | None:
        match node:
            case ToolNode():
                contract = self._contracts.get(node.id)
                return None if contract is None else contract.output_schema
            case LlmNode() | TransformNode():
                return node.output_schema
            case LoopNode():
                collected = self._output_schema(node.collect)
                if collected is None:
                    return {"type": "array"}
                return {
                    "type": "array",
                    "items": {k: v for k, v in collected.items() if k != "$defs"},
                    "$defs": collected.get("$defs", {}),
                }
            case _:
                return None

    def _schema_of(self, binding: Binding) -> Located | None:
        if isinstance(binding, FromNode) and binding.where is None:
            root = self._output_schema(binding.node)
            return None if root is None else locate(root, binding.path)
        if isinstance(binding, FromInput) and self._trigger is not None:
            return locate(self._trigger.input_schema, (binding.name,))
        return None

    def _item_schema(self, loop: LoopNode) -> Located | None:
        over = self._schema_of(loop.over)
        if over is None or over.schema is None or not is_array(over.schema):
            return None
        element = items_of(over.root, over.schema)
        return None if element is None else Located(element, over.root)
