"""IR → thứ tự chạy, và tên của từng lần chạy một node."""

from __future__ import annotations

import re
from collections.abc import Mapping, Set
from dataclasses import dataclass, field
from typing import Final

from app.core.errors import WorkflowDefinitionError
from app.domains.workflow.domain.ir import (
    BodyNode,
    Edge,
    LoopNode,
    Node,
    WorkflowIR,
)
from app.domains.workflow.domain.validation import ensure_valid, validate_structure

_PATH: Final = re.compile(
    r"^(?:(?P<loop>[a-z][a-z0-9_]*)\[(?P<iteration>\d+)\]\.)?(?P<node>[a-z][a-z0-9_]*)$"
)


@dataclass(frozen=True, slots=True)
class NodePath:
    """Tên của một lần chạy node: `n2` ở tầng ngoài, `n3[2].b1` ở lượt 2 của loop `n3`."""

    node: str
    loop: str | None = None
    iteration: int | None = None

    def __post_init__(self) -> None:
        if (self.loop is None) != (self.iteration is None):
            raise ValueError("loop và iteration phải cùng có hoặc cùng không.")
        if self.iteration is not None and self.iteration < 0:
            raise ValueError("iteration đếm từ 0.")

    def __str__(self) -> str:
        if self.loop is None:
            return self.node
        return f"{self.loop}[{self.iteration}].{self.node}"

    @classmethod
    def parse(cls, text: str) -> NodePath:
        matched = _PATH.match(text)
        if matched is None:
            raise ValueError(f"Không phải NodePath: {text!r}")
        iteration = matched["iteration"]
        return cls(
            node=matched["node"],
            loop=matched["loop"],
            iteration=None if iteration is None else int(iteration),
        )

    @classmethod
    def in_loop(cls, loop: str, iteration: int, node: str) -> NodePath:
        return cls(node=node, loop=loop, iteration=iteration)


@dataclass(frozen=True, slots=True)
class Plan:
    ir: WorkflowIR
    #: Node tầng ngoài theo thứ tự topo; hoà thì theo thứ tự khai báo.
    order: tuple[str, ...]
    predecessors: Mapping[str, frozenset[str]]
    #: Cạnh đi vào từng node, kèm nhánh nếu nguồn là một `condition`.
    incoming: Mapping[str, tuple[Edge, ...]] = field(default_factory=dict)

    def node(self, node_id: str) -> Node:
        for node in self.ir.nodes:
            if node.id == node_id:
                return node
        raise KeyError(node_id)

    def runnable(self, settled: Set[str]) -> tuple[str, ...]:
        """Node chưa xong mà mọi tiền bối đã xong hoặc bị bỏ qua, theo `order`."""
        return tuple(
            node_id
            for node_id in self.order
            if node_id not in settled and self.predecessors[node_id] <= settled
        )

    def is_cut_off(self, node_id: str, taken: Mapping[str, bool], skipped: Set[str]) -> bool:
        """Không đường nào còn dẫn tới node này: mọi cạnh vào nó đều đi ra từ một node bị
        bỏ qua, hoặc từ nhánh mà `condition` nguồn đã không chọn.

        `taken` là kết quả của các `condition` đã chạy. Node không có cạnh vào thì không
        bao giờ bị cắt."""
        edges = self.incoming.get(node_id, ())
        return bool(edges) and all(
            edge.source in skipped
            or (edge.branch is not None and taken.get(edge.source) != (edge.branch == "true"))
            for edge in edges
        )

    def iteration_paths(self, loop_id: str, iteration: int) -> tuple[NodePath, ...]:
        """Tên các node trong thân `loop_id` ở một lượt, theo thứ tự chạy."""
        return tuple(NodePath.in_loop(loop_id, iteration, inner.id) for inner in self.body(loop_id))

    def body(self, loop_id: str) -> tuple[BodyNode, ...]:
        node = self.node(loop_id)
        if not isinstance(node, LoopNode):
            raise WorkflowDefinitionError(f"Node {loop_id} không phải loop.")
        return node.body


def build_plan(ir: WorkflowIR) -> Plan:
    """Ném `WorkflowDefinitionError` nếu IR sai cấu trúc — plan chỉ dựng từ IR chạy được."""
    ensure_valid(validate_structure(ir))

    position = {node.id: index for index, node in enumerate(ir.nodes)}
    predecessors: dict[str, set[str]] = {node.id: set() for node in ir.nodes}
    for edge in ir.edges:
        predecessors[edge.target].add(edge.source)

    order: list[str] = []
    placed: set[str] = set()
    while len(order) < len(ir.nodes):
        ready = [
            node_id
            for node_id in predecessors
            if node_id not in placed and predecessors[node_id] <= placed
        ]
        # validate_structure đã loại chu trình, nên luôn có ít nhất một node sẵn sàng.
        chosen = min(ready, key=position.__getitem__)
        order.append(chosen)
        placed.add(chosen)

    return Plan(
        ir=ir,
        order=tuple(order),
        predecessors={node_id: frozenset(sources) for node_id, sources in predecessors.items()},
        incoming={
            node.id: tuple(edge for edge in ir.edges if edge.target == node.id) for node in ir.nodes
        },
    )
