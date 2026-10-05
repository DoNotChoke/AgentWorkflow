"""Điều phối một phiên biên dịch: mở, nói lại, chấp nhận ý đồ, đồng ý, bỏ."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final, Literal

from app.core.errors import AuthorizationError, ValidationError
from app.core.ids import ResourceKind, new_id
from app.core.logging import get_logger
from app.domains.workflow.application.compile_check import Checked, check_answers
from app.domains.workflow.application.compile_flow import Built, build, propose_intent
from app.domains.workflow.application.ports import (
    AnswerAdvisor,
    CompileSessionRepository,
    CompileSourceReader,
    DialogueTurn,
    DraftWriter,
    IntentAdvisor,
    Progress,
    ToolCatalogReader,
    TransformRunner,
)
from app.domains.workflow.domain.compiler.intent import read_intent, to_document
from app.domains.workflow.domain.compiler.normalize import read_source
from app.domains.workflow.domain.compiler.prompts import COMPILER_PROMPT_VERSION
from app.domains.workflow.domain.compiler.proposal import proposal_document, render_proposal
from app.domains.workflow.domain.compiler.provenance import find_sources
from app.domains.workflow.domain.compiler.questions import (
    QUESTION_SET_VERSION,
    QuestionSet,
    ToolShapes,
    build_questions,
)
from app.domains.workflow.domain.compiler.session import (
    CompileSession,
    MessageKind,
    SessionStatus,
    abandoned,
    assumptions_built,
    confirmed,
    ensure_owns,
    intent_proposed,
    intent_reopened,
)
from app.domains.workflow.domain.compiler.skeleton import Skeleton, build_skeleton
from app.domains.workflow.domain.compiler.source import SourceMaterial, fingerprint
from app.domains.workflow.domain.entities import Workflow, WorkflowDraft

log = get_logger(__name__)

_CONVERSATION_NOT_FOUND: Final = "Không tìm thấy cuộc hội thoại này."
_SESSION_NOT_FOUND: Final = "Không tìm thấy phiên biên dịch này."
MAX_MESSAGE_CHARS: Final = 4000
_MAX_NAME_CHARS: Final = 200


class CompileService:
    def __init__(
        self,
        *,
        sessions: CompileSessionRepository,
        sources: CompileSourceReader,
        workflows: DraftWriter,
        intent_advisor: IntentAdvisor,
        answer_advisor: AnswerAdvisor,
        tools: ToolShapes,
        catalog: ToolCatalogReader,
        runner: TransformRunner,
        intent_model: str,
        answer_model: str,
        llm_node_model: str,
        max_rounds: int = 3,
        max_briefing_chars: int = 60_000,
    ) -> None:
        self._sessions = sessions
        self._sources = sources
        self._workflows = workflows
        self._intent_advisor = intent_advisor
        self._answer_advisor = answer_advisor
        self._tools = tools
        self._catalog = catalog
        self._runner = runner
        self._intent_model = intent_model
        self._answer_model = answer_model
        self._llm_node_model = llm_node_model
        self._max_rounds = max_rounds
        self._max_briefing_chars = max_briefing_chars

    # ─── Đọc ─────────────────────────────────────────────────────────────────

    async def get(self, *, tenant_id: str, session_id: str, actor_id: str) -> CompileSession:
        session = await self._sessions.get(tenant_id=tenant_id, session_id=session_id)
        if session is None:
            raise AuthorizationError(_SESSION_NOT_FOUND, details={"session_id": session_id})
        ensure_owns(session, actor_id=actor_id)
        return session

    async def current(
        self, *, tenant_id: str, conversation_id: str, actor_id: str
    ) -> CompileSession | None:
        """Phiên người này đang mở trên conversation đó, để giao diện tiếp tục thay vì mở
        phiên mới."""
        found = await self._sessions.list_open(
            tenant_id=tenant_id, conversation_id=conversation_id, created_by=actor_id
        )
        return max(found, key=lambda session: session.updated_at, default=None)

    # ─── Mở ──────────────────────────────────────────────────────────────────

    async def open(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        conversation_id: str,
        actor_id: str,
        progress: Progress | None = None,
    ) -> CompileSession:
        await _tell(progress, "Đang đọc cuộc hội thoại.")
        material = await self._material(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            actor_id=actor_id,
            trace_ids=None,
        )
        if not any(material.steps()):
            raise ValidationError(
                "Cuộc hội thoại này chưa có lời gọi tool nào thành công để dựng workflow.",
                details={"conversation_id": conversation_id},
            )

        await _tell(progress, "Đang tìm hiểu ý đồ.")
        outcome = await propose_intent(
            material, [], self._intent_advisor, max_rounds=self._max_rounds
        )
        at = datetime.now(UTC)
        # Mỗi người một phiên đang mở trên một conversation: phiên cũ nhường chỗ.
        for stale in await self._sessions.list_open(
            tenant_id=tenant_id, conversation_id=conversation_id, created_by=actor_id
        ):
            await self._sessions.save(abandoned(stale, at=at), expected_revision=stale.revision)

        blank = CompileSession(
            id=new_id(ResourceKind.COMPILE_SESSION),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            created_by=actor_id,
            trace_ids=tuple(turn.trace_id for turn in material.turns),
            source_sha256=fingerprint(material),
            status=SessionStatus.AWAITING_INTENT,
            revision=0,
            messages=(),
            question_set_version=QUESTION_SET_VERSION,
            prompt_version=COMPILER_PROMPT_VERSION,
            intent_model=self._intent_model,
            answer_model=self._answer_model,
            created_at=at,
            updated_at=at,
        )
        session = intent_proposed(
            blank,
            said=None,
            intent=None if outcome.intent is None else to_document(outcome.intent),
            message=outcome.message,
            at=at,
        )
        await self._sessions.add(session)
        log.info("compile_session_opened", session_id=session.id, conversation_id=conversation_id)
        return session

    # ─── Nói lại ─────────────────────────────────────────────────────────────

    async def say(
        self,
        *,
        tenant_id: str,
        session_id: str,
        actor_id: str,
        text: str,
        progress: Progress | None = None,
    ) -> CompileSession:
        """Ở bước ý đồ, lời này tới model hiểu ý đồ. Ở bước giả định, nó tới model trả lời
        cùng câu trả lời đang hiện; ý đồ đã chấp nhận thì không đổi."""
        text = text.strip()
        if not text or len(text) > MAX_MESSAGE_CHARS:
            raise ValidationError(
                f"Tin nhắn phải có nội dung và không dài quá {MAX_MESSAGE_CHARS} ký tự."
            )
        session = await self.get(tenant_id=tenant_id, session_id=session_id, actor_id=actor_id)
        material = await self._pinned(session)

        if session.status is SessionStatus.AWAITING_INTENT:
            await _tell(progress, "Đang xem lại ý đồ.")
            dialogue = [*_dialogue(session), DialogueTurn("user", text)]
            outcome = await propose_intent(
                material, dialogue, self._intent_advisor, max_rounds=self._max_rounds
            )
            moved = intent_proposed(
                session,
                said=text,
                intent=None if outcome.intent is None else to_document(outcome.intent),
                message=outcome.message,
                at=datetime.now(UTC),
            )
        else:
            built = await self._build(session, material, comment=text, progress=progress)
            moved = _built(session, built, said=text)

        await self._sessions.save(moved, expected_revision=session.revision)
        return moved

    # ─── Hai lần xác nhận ────────────────────────────────────────────────────

    async def accept_intent(
        self, *, tenant_id: str, session_id: str, actor_id: str, progress: Progress | None = None
    ) -> CompileSession:
        session = await self.get(tenant_id=tenant_id, session_id=session_id, actor_id=actor_id)
        if session.status is not SessionStatus.AWAITING_INTENT or session.intent is None:
            raise ValidationError(
                "Chưa có ý đồ nào đang chờ chấp nhận.", details={"session_id": session_id}
            )
        built = await self._build(
            session, await self._pinned(session), comment=None, progress=progress
        )
        moved = _built(session, built, said=None)
        await self._sessions.save(moved, expected_revision=session.revision)
        return moved

    async def reopen_intent(
        self, *, tenant_id: str, session_id: str, actor_id: str
    ) -> CompileSession:
        session = await self.get(tenant_id=tenant_id, session_id=session_id, actor_id=actor_id)
        moved = intent_reopened(session, at=datetime.now(UTC))
        await self._sessions.save(moved, expected_revision=session.revision)
        return moved

    async def confirm(self, *, tenant_id: str, session_id: str, actor_id: str) -> CompileSession:
        """Không lời gọi model nào: IR người dùng vừa đọc là IR được lưu."""
        session = await self.get(tenant_id=tenant_id, session_id=session_id, actor_id=actor_id)
        at = datetime.now(UTC)
        workflow_id = new_id(ResourceKind.WORKFLOW)
        moved = confirmed(session, workflow_id=workflow_id, at=at)
        assert session.ir is not None and session.answers is not None
        # Ghi phiên trước: hai lần bấm cùng lúc thì lần sau dừng ở đây, chưa tạo gì.
        await self._sessions.save(moved, expected_revision=session.revision)

        await self._workflows.add(
            Workflow(
                id=workflow_id,
                tenant_id=tenant_id,
                workspace_id=session.workspace_id,
                name=str(session.answers.get("ten") or "Workflow mới")[:_MAX_NAME_CHARS],
                created_by=actor_id,
                created_at=at,
                updated_at=at,
            )
        )
        await self._workflows.save_draft(
            WorkflowDraft(
                workflow_id=workflow_id,
                tenant_id=tenant_id,
                ir=session.ir,
                updated_at=at,
                updated_by=actor_id,
                intent=session.intent,
                compiled_from=session.id,
            )
        )
        log.info("compile_session_compiled", session_id=session.id, workflow_id=workflow_id)
        return moved

    async def abandon(self, *, tenant_id: str, session_id: str, actor_id: str) -> CompileSession:
        session = await self.get(tenant_id=tenant_id, session_id=session_id, actor_id=actor_id)
        moved = abandoned(session, at=datetime.now(UTC))
        await self._sessions.save(moved, expected_revision=session.revision)
        return moved

    # ─── Nguyên liệu ─────────────────────────────────────────────────────────

    async def _material(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        conversation_id: str,
        actor_id: str,
        trace_ids: tuple[str, ...] | None,
    ) -> SourceMaterial:
        source = await self._sources.read(tenant_id=tenant_id, conversation_id=conversation_id)
        # Chỉ chủ conversation biên dịch được nó; người khác thấy như không tồn tại.
        if source is None or source.user_id != actor_id or source.workspace_id != workspace_id:
            raise AuthorizationError(
                _CONVERSATION_NOT_FOUND, details={"conversation_id": conversation_id}
            )
        return read_source(source.document, only=trace_ids)

    async def _pinned(self, session: CompileSession) -> SourceMaterial:
        material = await self._material(
            tenant_id=session.tenant_id,
            workspace_id=session.workspace_id,
            conversation_id=session.conversation_id,
            actor_id=session.created_by,
            trace_ids=session.trace_ids,
        )
        if fingerprint(material) != session.source_sha256:
            # Payload hết hạn, hoặc một lượt đã bị xoá: ý đồ đã đề xuất không còn nói
            # về đúng thứ sẽ được đọc.
            raise ValidationError(
                "Dữ liệu của cuộc hội thoại đã đổi từ lúc mở phiên. Mở một phiên mới.",
                details={"session_id": session.id},
            )
        return material

    async def _build(
        self,
        session: CompileSession,
        material: SourceMaterial,
        *,
        comment: str | None,
        progress: Progress | None,
    ) -> Built:
        assert session.intent is not None
        skeleton = build_skeleton(material)
        questions = build_questions(skeleton, find_sources(skeleton), self._tools)
        return await build(
            skeleton,
            questions,
            read_intent(session.intent),
            self._answer_advisor,
            lambda raw: self._check(skeleton, questions, raw),
            max_rounds=self._max_rounds,
            max_briefing_chars=self._max_briefing_chars,
            comment=comment,
            previous=session.answers if comment else None,
            progress=progress,
        )

    def _check(self, skeleton: Skeleton, questions: QuestionSet, raw: Mapping[str, Any]) -> Checked:
        return check_answers(
            skeleton,
            questions,
            raw,
            tools=self._tools,
            catalog=self._catalog,
            runner=self._runner,
            llm_model=self._llm_node_model,
        )


async def _tell(progress: Progress | None, text: str) -> None:
    if progress is not None:
        await progress(text)


def _dialogue(session: CompileSession) -> list[DialogueTurn]:
    """Phần chat mà model hiểu ý đồ được đọc lại. Bảng giả định không thuộc về nó."""
    turns: list[DialogueTurn] = []
    for message in session.messages:
        if message.kind is MessageKind.ASSUMPTIONS:
            continue
        role: Literal["user", "assistant"] = (
            "user" if message.kind is MessageKind.USER else "assistant"
        )
        if turns and turns[-1].role == role:
            turns[-1] = DialogueTurn(role, f"{turns[-1].text}\n\n{message.text}")
        else:
            turns.append(DialogueTurn(role, message.text))
    return turns


def _built(session: CompileSession, built: Built, *, said: str | None) -> CompileSession:
    report = built.checked.report
    if built.proposal is None:
        message = "Chưa dựng được bảng giả định:\n" + "\n".join(
            f"- {issue.message}" for issue in report.errors
        )
    else:
        message = render_proposal(built.proposal)
    assembled = built.checked.assembled
    return assumptions_built(
        session,
        said=said,
        answers=built.raw,
        proposal=None if built.proposal is None else proposal_document(built.proposal),
        ir=assembled.document if report.ok and assembled is not None else None,
        message=message,
        at=datetime.now(UTC),
    )
