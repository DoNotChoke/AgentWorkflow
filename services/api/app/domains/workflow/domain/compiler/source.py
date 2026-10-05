"""Thứ compiler đọc: một conversation, đã là dữ liệu thuần."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceStep:
    """Một lời gọi tool đã thành công, kèm thứ đã gửi và thứ nhận về."""

    #: Địa chỉ trong nguyên liệu: `l2.b1` là bước 1 của lượt 2.
    ref: str
    #: Bước `tool_call` của Agent trong trace — để giao diện mở lại đúng bước.
    step_id: str
    tool_id: str
    input: Mapping[str, Any]
    output: Mapping[str, Any]
    #: `None` với tool chạy ngay trong Agent Runtime: nó không qua catalog.
    tool_version: str | None = None

    @property
    def is_local(self) -> bool:
        return self.tool_version is None


class SkipReason(StrEnum):
    #: Ngõ cụt model đã thoát ra, không phải một bước của quy trình.
    FAILED = "failed"
    #: Cùng tool, cùng input với một bước trước trong lượt: Gateway trả lại kết quả cũ.
    REPLAYED = "replayed"
    #: Payload không đọc lại được — vượt trần, hoặc đã hết hạn lưu.
    PAYLOAD_MISSING = "payload_missing"


@dataclass(frozen=True, slots=True)
class SkippedStep:
    """Một lời gọi tool có trong trace mà compiler không dùng."""

    step_id: str
    tool_id: str
    reason: SkipReason


@dataclass(frozen=True, slots=True)
class Turn:
    """Một lượt chat: một tin nhắn của người dùng và mọi thứ Agent làm sau nó."""

    #: Từ 1.
    index: int
    trace_id: str
    #: `succeeded`, `failed`, `cancelled`.
    status: str
    #: `None` khi payload của lời gọi model đầu tiên không còn.
    request: str | None
    reply: str | None
    steps: tuple[SourceStep, ...] = ()
    skipped: tuple[SkippedStep, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceMaterial:
    conversation_id: str
    turns: tuple[Turn, ...]

    def steps(self) -> Iterator[SourceStep]:
        for turn in self.turns:
            yield from turn.steps


def fingerprint(source: SourceMaterial) -> str:
    """Vân tay của thứ compiler đọc: lời người dùng và từng bước kèm dữ liệu của nó."""
    seen = [
        [
            turn.trace_id,
            turn.request,
            [
                [step.step_id, step.tool_id, step.tool_version, step.input, step.output]
                for step in turn.steps
            ],
        ]
        for turn in source.turns
    ]
    canonical = json.dumps(seen, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
