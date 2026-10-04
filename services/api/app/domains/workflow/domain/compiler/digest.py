"""Bản tóm tắt conversation cho model hiểu ý đồ.

Chỉ có lời người dùng, lời Agent, và tên các tool đã gọi. Không tham số, không output:
ý đồ nằm trong lời nói, còn payload ở bước này chỉ là chi phí và nhiễu
"""

from __future__ import annotations

from typing import Final

from app.domains.workflow.domain.compiler.source import SkipReason, SourceMaterial, Turn

#: Câu trả lời của Agent có thể chép lại cả một bảng kết quả; phần đầu là đủ để hiểu.
MAX_REPLY_CHARS: Final = 600

_SKIPPED: Final = {
    SkipReason.FAILED: "hỏng",
    SkipReason.REPLAYED: "gọi lặp, bỏ qua",
    SkipReason.PAYLOAD_MISSING: "thành công, nhưng không còn đọc lại được dữ liệu",
}


def digest(source: SourceMaterial) -> str:
    return "\n\n".join(_turn(turn) for turn in source.turns)


def _turn(turn: Turn) -> str:
    lines = [f"## Lượt {turn.index}", f"Người dùng: {turn.request or '(không đọc lại được)'}"]
    calls = [f"- {step.tool_id} — thành công" for step in turn.steps]
    calls += [f"- {step.tool_id} — {_SKIPPED[step.reason]}" for step in turn.skipped]
    if calls:
        lines += ["Tool Agent đã gọi:", *calls]
    else:
        lines.append("Agent không gọi tool nào.")
    if turn.reply:
        lines.append(f"Agent trả lời: {_cut(turn.reply)}")
    if turn.status != "succeeded":
        lines.append(f"(Lượt này kết thúc ở trạng thái {turn.status}.)")
    return "\n".join(lines)


def _cut(text: str) -> str:
    text = text.strip()
    return text if len(text) <= MAX_REPLY_CHARS else f"{text[:MAX_REPLY_CHARS]}…"
