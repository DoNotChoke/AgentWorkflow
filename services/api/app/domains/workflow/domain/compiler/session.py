"""Phiên biên dịch: một lần biến một conversation thành workflow, qua hai lần xác nhận.

```
awaiting_intent ⇄ (người dùng nói lại) ──chấp nhận ý đồ──▶ awaiting_confirmation ⇄ (đổi)
       ▲                                                        │
       └──────────────── quay lại ý đồ ─────────────────────────┤
                                                                └──đồng ý──▶ compiled
mọi trạng thái còn mở ──▶ abandoned
```

Chỉ có trạng thái *chờ người dùng*: lúc model đang chạy thì phiên chưa đổi, và ai ghi
trước thì thắng (`revision`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from app.core.errors import AuthorizationError, ValidationError

_SESSION_NOT_FOUND: Final = "Không tìm thấy phiên biên dịch này."


class SessionStatus(StrEnum):
    #: Chờ người dùng chấp nhận ý đồ, hoặc nói lại.
    AWAITING_INTENT = "awaiting_intent"
    #: Chờ người dùng đồng ý với bảng giả định, hoặc đổi.
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPILED = "compiled"
    ABANDONED = "abandoned"


OPEN: Final = frozenset({SessionStatus.AWAITING_INTENT, SessionStatus.AWAITING_CONFIRMATION})


class MessageKind(StrEnum):
    USER = "user"
    #: Một đề xuất ý đồ.
    INTENT = "intent"
    #: Model hỏi lại thay vì đề xuất.
    QUESTION = "question"
    #: Một bảng giả định.
    ASSUMPTIONS = "assumptions"


@dataclass(frozen=True, slots=True)
class SessionMessage:
    kind: MessageKind
    text: str
    at: datetime

    @property
    def role(self) -> str:
        return "user" if self.kind is MessageKind.USER else "assistant"


@dataclass(frozen=True, slots=True)
class CompileSession:
    id: str
    tenant_id: str
    workspace_id: str
    conversation_id: str
    created_by: str
    #: Các lượt là nguyên liệu của phiên. Lượt người dùng chat thêm sau đó không thuộc về nó.
    trace_ids: tuple[str, ...]
    #: Vân tay của nguyên liệu lúc mở phiên.
    source_sha256: str
    status: SessionStatus
    #: Tăng một sau mỗi lần ghi.
    revision: int
    messages: tuple[SessionMessage, ...]
    question_set_version: str
    prompt_version: str
    intent_model: str
    answer_model: str
    created_at: datetime
    updated_at: datetime
    #: Đề xuất ý đồ đang hiện. `None` khi model còn đang hỏi lại.
    intent: Mapping[str, Any] | None = None
    #: Câu trả lời thô đang hiện, kể cả khi còn lỗi.
    answers: Mapping[str, Any] | None = None
    #: Bảng giả định ở dạng dữ liệu.
    proposal: Mapping[str, Any] | None = None
    #: IR đã qua cả ba tầng kiểm. Có nó thì mới đồng ý được.
    ir: Mapping[str, Any] | None = None
    workflow_id: str | None = None

    @property
    def is_open(self) -> bool:
        return self.status in OPEN

    @property
    def is_ready(self) -> bool:
        return self.status is SessionStatus.AWAITING_CONFIRMATION and self.ir is not None


def ensure_owns(session: CompileSession, *, actor_id: str) -> None:
    """Phiên là của người mở nó. Người khác thấy nó như không tồn tại."""
    if session.created_by != actor_id:
        raise AuthorizationError(_SESSION_NOT_FOUND, details={"session_id": session.id})


def _ensure(session: CompileSession, *allowed: SessionStatus) -> None:
    if session.status not in allowed:
        raise ValidationError(
            f"Phiên biên dịch đang ở trạng thái `{session.status}`; không làm được việc này.",
            details={"session_id": session.id, "status": str(session.status)},
        )


def _moved(
    session: CompileSession, at: datetime, *said: SessionMessage, **changes: Any
) -> CompileSession:
    return replace(
        session,
        messages=(*session.messages, *said),
        revision=session.revision + 1,
        updated_at=at,
        **changes,
    )


def intent_proposed(
    session: CompileSession,
    *,
    said: str | None,
    intent: Mapping[str, Any] | None,
    message: str,
    at: datetime,
) -> CompileSession:
    """Model vừa đề xuất một ý đồ, hoặc hỏi lại. `said` là lời người dùng dẫn tới nó."""
    _ensure(session, SessionStatus.AWAITING_INTENT)
    kind = MessageKind.QUESTION if intent is None else MessageKind.INTENT
    spoken = [SessionMessage(MessageKind.USER, said, at)] if said else []
    return _moved(session, at, *spoken, SessionMessage(kind, message, at), intent=intent)


def assumptions_built(
    session: CompileSession,
    *,
    said: str | None,
    answers: Mapping[str, Any],
    proposal: Mapping[str, Any] | None,
    ir: Mapping[str, Any] | None,
    message: str,
    at: datetime,
) -> CompileSession:
    """Một bảng giả định mới: sau khi chấp nhận ý đồ, hoặc sau khi người dùng đòi đổi."""
    _ensure(session, SessionStatus.AWAITING_INTENT, SessionStatus.AWAITING_CONFIRMATION)
    if session.intent is None:
        raise ValidationError("Chưa có ý đồ nào để chấp nhận.", details={"session_id": session.id})
    spoken = [SessionMessage(MessageKind.USER, said, at)] if said else []
    return _moved(
        session,
        at,
        *spoken,
        SessionMessage(MessageKind.ASSUMPTIONS, message, at),
        status=SessionStatus.AWAITING_CONFIRMATION,
        answers=answers,
        proposal=proposal,
        ir=ir,
    )


def intent_reopened(session: CompileSession, *, at: datetime) -> CompileSession:
    """Điều người dùng muốn đổi là chính nhiệm vụ: bảng giả định đang có không còn nghĩa."""
    _ensure(session, SessionStatus.AWAITING_CONFIRMATION)
    return _moved(
        session, at, status=SessionStatus.AWAITING_INTENT, answers=None, proposal=None, ir=None
    )


def confirmed(session: CompileSession, *, workflow_id: str, at: datetime) -> CompileSession:
    _ensure(session, SessionStatus.AWAITING_CONFIRMATION)
    if session.ir is None:
        raise ValidationError(
            "Bảng giả định này chưa dựng được workflow hợp lệ nên chưa đồng ý được.",
            details={"session_id": session.id},
        )
    return _moved(session, at, status=SessionStatus.COMPILED, workflow_id=workflow_id)


def abandoned(session: CompileSession, *, at: datetime) -> CompileSession:
    _ensure(session, *OPEN)
    return _moved(session, at, status=SessionStatus.ABANDONED)
