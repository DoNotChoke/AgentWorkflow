"""Chặng 1: trace thô → `SourceMaterial`."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from typing import Any

from app.domains.workflow.domain.compiler.source import (
    SkippedStep,
    SkipReason,
    SourceMaterial,
    SourceStep,
    Turn,
)

_AVAILABLE = "available"
#: Lượt chưa kết thì chưa phải nguyên liệu: bước của nó còn đang được ghi.
_RUNNING = "running"


def read_source(
    document: Mapping[str, Any], *, only: Collection[str] | None = None
) -> SourceMaterial:
    """`document` là thứ `CompileSourceReader` nạp: tin nhắn có chữ của conversation,
    và mỗi trace kèm các bước, mỗi bước kèm hai phía payload và trạng thái đọc được.

    `only` là các trace một phiên đã ghim: lượt khác bị bỏ, cùng tin nhắn của nó."""
    traces = sorted(document["traces"], key=_started)
    # Lượt đang chạy vẫn chặn cửa sổ của lượt trước nó: tin nhắn của nó không
    # thuộc về lượt trước.
    ends = [_started(trace) for trace in traces[1:]] + [None]

    turns: list[Turn] = []
    for trace, end in zip(traces, ends, strict=True):
        if trace["status"] == _RUNNING or (only is not None and trace["id"] not in only):
            continue
        said = [
            message
            for message in document["messages"]
            if _started(trace) <= _at(message["created_at"])
            and (end is None or _at(message["created_at"]) < end)
        ]
        turns.append(_turn(len(turns) + 1, trace, said))

    return SourceMaterial(conversation_id=document["conversation_id"], turns=tuple(turns))


def _at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


def _started(record: Mapping[str, Any]) -> datetime:
    return _at(record["started_at"])


def _turn(index: int, trace: Mapping[str, Any], said: Sequence[Mapping[str, Any]]) -> Turn:
    recorded = sorted(trace["steps"], key=_started)
    children: dict[str, list[Mapping[str, Any]]] = {}
    for step in recorded:
        if step["parent_step_id"] is not None:
            children.setdefault(step["parent_step_id"], []).append(step)

    steps: list[SourceStep] = []
    skipped: list[SkippedStep] = []
    seen: set[tuple[str, str]] = set()

    for call in recorded:
        if call["kind"] != "tool_call" or call["parent_step_id"] is not None:
            continue
        tool_id = call["attributes"]["tool_id"]

        if call["status"] == "failed":
            skipped.append(SkippedStep(call["id"], tool_id, SkipReason.FAILED))
            continue

        # Thứ tự nằm ở bước của Agent, giá trị nằm ở bước Gateway ghi dưới nó.
        # Tool chạy tại chỗ không qua Gateway: giá trị nằm ngay ở bước của Agent.
        carrier = _carrier(children.get(call["id"], ())) or call
        sent, received = _payload(carrier["input"]), _payload(carrier["output"])
        if sent is None or received is None:
            skipped.append(SkippedStep(call["id"], tool_id, SkipReason.PAYLOAD_MISSING))
            continue

        key = (tool_id, _canonical(sent))
        if call["status"] == "replayed" and key in seen:
            skipped.append(SkippedStep(call["id"], tool_id, SkipReason.REPLAYED))
            continue
        seen.add(key)

        steps.append(
            SourceStep(
                ref=f"l{index}.b{len(steps) + 1}",
                step_id=call["id"],
                tool_id=tool_id,
                tool_version=carrier["attributes"].get("tool_version"),
                input=sent,
                output=received,
            )
        )

    model_calls = [step for step in recorded if step["kind"] == "model_call"]
    return Turn(
        index=index,
        trace_id=trace["id"],
        status=trace["status"],
        request=_said(said, "user", last=False) or _request(model_calls),
        reply=_said(said, "assistant", last=True) or _reply(model_calls),
        steps=tuple(steps),
        skipped=tuple(skipped),
    )


def _carrier(children: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Bước Gateway mang kết quả: lần thử cuối cùng không hỏng."""
    done = [step for step in children if step["kind"] == "tool_call" and step["status"] != "failed"]
    return done[-1] if done else None


def _payload(side: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if side["state"] != _AVAILABLE:
        return None
    return side["payload"]  # type: ignore[no-any-return]


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _said(messages: Sequence[Mapping[str, Any]], role: str, *, last: bool) -> str | None:
    texts = [message["content"] for message in messages if message["role"] == role]
    if not texts:
        return None
    return texts[-1] if last else texts[0]  # type: ignore[no-any-return]


def _request(model_calls: Sequence[Mapping[str, Any]]) -> str | None:
    """Lượt vỡ giữa chừng không để lại tin nhắn nào: câu người dùng gõ chỉ còn trong
    lời gọi model đầu tiên, là lượt `user` có chữ cuối cùng."""
    if not model_calls:
        return None
    sent = _payload(model_calls[0]["input"])
    if sent is None:
        return None
    for turn in reversed(sent["turns"]):
        if turn["role"] == "user" and turn["text"]:
            return turn["text"]  # type: ignore[no-any-return]
    return None


def _reply(model_calls: Sequence[Mapping[str, Any]]) -> str | None:
    """Lời gọi model cuối cùng, nếu nó không kết bằng một lời gọi tool."""
    if not model_calls:
        return None
    received = _payload(model_calls[-1]["output"])
    if received is None or received["tool_calls"]:
        return None
    return received["text"] or None
