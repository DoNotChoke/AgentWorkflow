"""Câu trả lời cho một tập câu hỏi, và tầng kiểm 1: câu trả lời có dùng được không."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

import jsonschema

from app.domains.workflow.domain.compiler.provenance import LaneLink
from app.domains.workflow.domain.compiler.questions import (
    INPUT_NAME,
    Choice,
    LoopQuestion,
    QuestionSet,
    SourceQuestion,
    answer_schema,
)
from app.domains.workflow.domain.compiler.skeleton import Skeleton, StepKind
from app.domains.workflow.domain.ir import ConditionOp

#: Id của trigger trong mọi workflow biên dịch ra.
TRIGGER_ID: Final = "t"
_MAX_SHAPE_ERRORS: Final = 20
_INPUT_NAME: Final = re.compile(INPUT_NAME)


@dataclass(frozen=True, slots=True)
class AnswerIssue:
    #: Id câu hỏi (`N3`, `V1`), địa chỉ bước (`l2.b1`), hoặc `llm:<id>`,
    #: `tinh_toan:<id>`, `dieu_kien:<id>`. Rỗng khi lỗi thuộc về cả câu trả lời.
    question: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class StepAnswer:
    keep: bool
    id: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SourceAnswer:
    choice: Choice
    #: Tên đầu vào, khi chọn `workflow_input`.
    name: str | None = None
    #: Số thứ tự ứng viên, từ 1, khi chọn `from_step`.
    candidate: int | None = None
    #: Chọn phần tử theo trường này thay vì theo chỉ số.
    key: str | None = None
    key_choice: Choice | None = None
    key_name: str | None = None
    #: Id node `llm`, khi chọn `llm`.
    node: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class LoopAnswer:
    as_loop: bool
    id: str | None = None
    reason: str | None = None
    #: Những làn khác được gộp vào thân vòng lặp, theo số thứ tự từ 1.
    lanes: tuple[int, ...] = ()
    #: Tên những trường lọc phần tử, chọn trong số bộ dò đề nghị.
    filters: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LlmAnswer:
    id: str
    instructions: str
    #: Tên input của node → khoá của một `DataRef`.
    inputs: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ValueAnswer:
    """Một giá trị do người trả lời đưa vào: dữ liệu của bước trước, hằng, hay đầu vào."""

    kind: str
    ref: str | None = None
    name: str | None = None
    value: Any = None


@dataclass(frozen=True, slots=True)
class ComputeAnswer:
    """Một node `transform` mà trace không có."""

    id: str
    after: str
    code: str
    result: str
    args: Mapping[str, ValueAnswer]


@dataclass(frozen=True, slots=True)
class ConditionAnswer:
    id: str
    #: Bước chỉ chạy khi điều kiện đúng. Mọi bước sau nó cũng nằm trong nhánh đó.
    before: str
    #: Id của một `ComputeAnswer`, hoặc địa chỉ một bước `transform` trong khung.
    left: str
    op: ConditionOp
    right: ValueAnswer | None = None


@dataclass(frozen=True, slots=True)
class Answers:
    name: str
    steps: Mapping[str, StepAnswer]
    sources: Mapping[str, SourceAnswer]
    loops: Mapping[str, LoopAnswer] = field(default_factory=dict)
    llms: tuple[LlmAnswer, ...] = ()
    computes: tuple[ComputeAnswer, ...] = ()
    conditions: tuple[ConditionAnswer, ...] = ()

    def keeps(self, step: str) -> bool:
        answer = self.steps.get(step)
        return answer is not None and answer.keep

    def node_id(self, step: str) -> str:
        answer = self.steps.get(step)
        return (answer.id if answer else None) or step.replace(".", "_")


# ─── Đọc ─────────────────────────────────────────────────────────────────────


def read_answers(
    questions: QuestionSet, skeleton: Skeleton, raw: Mapping[str, Any]
) -> tuple[Answers | None, tuple[AnswerIssue, ...]]:
    """Câu trả lời thô → `Answers`, kèm mọi lỗi tìm thấy. Sai hình dạng thì không có
    `Answers`: phần còn lại không đáng kiểm trên một thứ chưa đọc được."""
    validator = jsonschema.Draft202012Validator(answer_schema(questions))
    shape = sorted(validator.iter_errors(raw), key=lambda error: list(error.absolute_path))
    if shape:
        return None, tuple(
            AnswerIssue(
                question=_owner(error.absolute_path),
                code="sai_hinh_dang",
                message=f"{'/'.join(str(part) for part in error.absolute_path)}: {error.message}",
            )
            for error in shape[:_MAX_SHAPE_ERRORS]
        )

    answers = _parse(raw)
    return answers, tuple(_check(questions, skeleton, answers))


def _owner(path: Any) -> str:
    """Câu hỏi mà một lỗi hình dạng thuộc về: `gia_tri/N3/chon` → `N3`."""
    parts = [str(part) for part in path]
    if len(parts) >= 2 and parts[0] in {"buoc", "gia_tri", "vong_lap"}:
        return parts[1]
    return ""


def _parse(raw: Mapping[str, Any]) -> Answers:
    return Answers(
        name=raw["ten"],
        steps={
            ref: StepAnswer(keep=item["chon"] == "giu", id=item.get("id"), reason=item.get("ly_do"))
            for ref, item in raw["buoc"].items()
        },
        sources={qid: _source(item) for qid, item in raw["gia_tri"].items()},
        loops={
            qid: LoopAnswer(
                as_loop=item["chon"] == "loop",
                id=item.get("id"),
                reason=item.get("ly_do"),
                lanes=tuple(sorted(item.get("lan", ()))),
                filters=tuple(item.get("loc", ())),
            )
            for qid, item in raw.get("vong_lap", {}).items()
        },
        llms=tuple(
            LlmAnswer(id=item["id"], instructions=item["instructions"], inputs=item["dau_vao"])
            for item in raw.get("llm", ())
        ),
        computes=tuple(
            ComputeAnswer(
                id=item["id"],
                after=item["sau_buoc"],
                code=item["code"],
                result=item["ket_qua"],
                args={name: _value(value) for name, value in item["args"].items()},
            )
            for item in raw.get("tinh_toan", ())
        ),
        conditions=tuple(
            ConditionAnswer(
                id=item["id"],
                before=item["truoc_buoc"],
                left=item["trai"],
                op=ConditionOp(item["op"]),
                right=_value(item["phai"]) if "phai" in item else None,
            )
            for item in raw.get("dieu_kien", ())
        ),
    )


def _source(item: Mapping[str, Any]) -> SourceAnswer:
    key_source = item.get("nguon_khoa") or {}
    return SourceAnswer(
        choice=Choice(item["chon"]),
        name=item.get("ten"),
        candidate=item.get("ung_vien"),
        key=item.get("khoa"),
        key_choice=Choice(key_source["chon"]) if key_source else None,
        key_name=key_source.get("ten"),
        node=item.get("node"),
        reason=item.get("ly_do"),
    )


def _value(item: Mapping[str, Any]) -> ValueAnswer:
    return ValueAnswer(
        kind=item["chon"], ref=item.get("nguon"), name=item.get("ten"), value=item.get("gia_tri")
    )


# ─── Kiểm ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Membership:
    """Chỗ của một bước trong thân một vòng lặp đã chọn."""

    question: LoopQuestion
    #: 0 là làn gốc.
    lane: int
    #: Vòng thứ mấy, từ 0. Bước của vòng 0 đứng thay cả làn trong thân vòng lặp.
    position: int


def included_lanes(question: LoopQuestion, answers: Answers) -> tuple[int, ...]:
    """Các làn nằm trong thân vòng lặp, theo thứ tự của lần chạy mẫu. Rỗng khi không gộp."""
    answer = answers.loops.get(question.id)
    if answer is None or not answer.as_loop:
        return ()
    return (0, *answer.lanes)


def loop_filter(question: LoopQuestion, answers: Answers) -> tuple[tuple[str, Any], ...]:
    """Bộ lọc của vòng lặp: phần lần chạy mẫu buộc phải có, và phần người trả lời chọn thêm."""
    answer = answers.loops.get(question.id)
    chosen = answer.filters if answer is not None else ()
    return (
        *question.loop.filter,
        *((name, value) for name, value in question.loop.suggested if name in chosen),
    )


def body_order(questions: QuestionSet, question: LoopQuestion, answers: Answers) -> tuple[int, ...]:
    """Các làn của thân vòng lặp theo thứ tự chạy.

    Một làn đứng sau mọi làn nó lấy dữ liệu. Trong phạm vi đó, làn chỉ đọc đứng trước làn
    ghi ra ngoài: thứ gì hỏng được thì hỏng trước khi có gì được gửi đi. Còn lại giữ thứ tự
    của lần chạy mẫu."""
    lanes = included_lanes(question, answers)
    needs = {lane: _lane_needs(questions, question, answers, lane) & set(lanes) for lane in lanes}
    writes = {lane: question.loop.lane_steps(lane)[0] in questions.writes for lane in lanes}

    placed: list[int] = []
    while len(placed) < len(lanes):
        ready = [lane for lane in lanes if lane not in placed and needs[lane] <= set(placed)]
        if not ready:
            # Phụ thuộc vòng: không xảy ra với nguồn lấy từ bước chạy trước, nhưng một
            # thứ tự sai vẫn hơn một vòng lặp vô hạn. Tầng 3 sẽ báo.
            ready = [lane for lane in lanes if lane not in placed]
        placed.append(next((lane for lane in ready if not writes[lane]), ready[0]))
    return tuple(placed)


def _lane_needs(
    questions: QuestionSet, question: LoopQuestion, answers: Answers, lane: int
) -> set[int]:
    """Những làn khác của cùng vòng lặp mà bước đầu của `lane` đọc."""
    loop = question.loop
    first = loop.lane_steps(lane)[0]

    def lane_holding(step: str) -> int | None:
        found = questions.lane_of(step)
        return found[1] if found is not None and found[0].id == question.id else None

    needed: set[int | None] = set()
    if lane > 0:
        needed |= {link.lane for link in loop.lanes[lane - 1].links}
    for source in questions.of(first):
        answer = answers.sources.get(source.id)
        if answer is None:
            continue
        if answer.choice is Choice.FROM_STEP and answer.candidate is not None:
            needed.add(lane_holding(source.candidates[answer.candidate - 1].step))
        elif answer.choice is Choice.LLM:
            node = next((llm for llm in answers.llms if llm.id == answer.node), None)
            for key in node.inputs.values() if node is not None else ():
                ref = questions.ref(key)
                if ref is not None:
                    needed.add(lane_holding(ref.step))
    return {found for found in needed if found is not None and found != lane}


def membership(questions: QuestionSet, answers: Answers, step: str) -> Membership | None:
    found = questions.lane_of(step)
    if found is None:
        return None
    question, lane = found
    if lane not in included_lanes(question, answers):
        return None
    return Membership(question, lane, question.loop.lane_steps(lane).index(step))


def subsumed(questions: QuestionSet, answers: Answers, step: str) -> bool:
    """Bước thuộc vòng thứ hai trở đi của một vòng lặp đã chọn: bước của vòng đầu nói thay nó."""
    member = membership(questions, answers, step)
    return member is not None and member.position > 0


def lane_link(
    questions: QuestionSet, answers: Answers, question: SourceQuestion
) -> LaneLink | None:
    """Trường này lấy giá trị từ dữ liệu của chính vòng đang chạy: không cần hỏi."""
    member = membership(questions, answers, question.step)
    if member is None:
        return None
    loop = member.question.loop
    if member.lane == 0:
        if question.path != loop.field:
            return None
        return LaneLink(field=question.field, lane=None, path=loop.pick)
    return next(
        (link for link in loop.lanes[member.lane - 1].links if link.field == question.field),
        None,
    )


def is_loop_item(questions: QuestionSet, answers: Answers, question: SourceQuestion) -> bool:
    return lane_link(questions, answers, question) is not None


def _check(questions: QuestionSet, skeleton: Skeleton, answers: Answers) -> Iterator[AnswerIssue]:
    order = {step.ref: index for index, step in enumerate(skeleton.steps)}
    kept = [step for step in skeleton.steps if answers.keeps(step.ref)]
    if not kept:
        yield AnswerIssue(
            "", "khong_co_gi_de_bien_dich", "Không bước nào được giữ, nên không có workflow."
        )
        return

    yield from _check_loops(questions, answers)
    inputs: dict[str, Any] = {}

    for step in kept:
        if subsumed(questions, answers, step.ref):
            continue
        for question in questions.of(step.ref):
            if is_loop_item(questions, answers, question):
                continue
            answer = answers.sources.get(question.id)
            if answer is None:
                yield AnswerIssue(
                    question.id,
                    "thieu_cau_tra_loi",
                    f"Bước {step.ref} được giữ mà {'.'.join(question.path)} chưa có câu trả lời.",
                )
                continue
            yield from _check_source(questions, answers, order, question, answer, inputs)

    yield from _check_llms(questions, answers)
    yield from _check_computes(questions, skeleton, answers, order, inputs)
    yield from _check_ids(skeleton, questions, answers)


def _check_loops(questions: QuestionSet, answers: Answers) -> Iterator[AnswerIssue]:
    for question in questions.loops:
        answer = answers.loops.get(question.id)
        states = {answers.keeps(step) for step in question.loop.steps}
        if answer is None:
            if True in states:
                yield AnswerIssue(
                    question.id,
                    "thieu_cau_tra_loi",
                    f"Các bước {', '.join(question.loop.steps)} lặp trên một mảng; "
                    "chưa chọn gộp thành vòng lặp hay để riêng.",
                )
            continue
        if not answer.as_loop:
            continue
        if states != {True}:
            yield AnswerIssue(
                question.id,
                "vong_lap_thieu_buoc",
                "Gộp thành vòng lặp thì mọi bước của nó phải được giữ.",
            )
        elif not answers.keeps(question.loop.source):
            yield AnswerIssue(
                question.id,
                "nguon_la_buoc_da_bo",
                f"Vòng lặp lấy mảng từ {question.loop.source}, mà bước đó đã bị bỏ.",
            )
        for number in answer.lanes:
            lane = question.loop.lanes[number - 1]
            if not all(answers.keeps(step) for step in lane.steps):
                yield AnswerIssue(
                    question.id,
                    "vong_lap_thieu_buoc",
                    f"Làn {number} nằm trong vòng lặp thì mọi bước của nó phải được giữ.",
                )
            for link in lane.links:
                if link.lane and link.lane not in answer.lanes:
                    yield AnswerIssue(
                        question.id,
                        "lan_thieu_nguon",
                        f"Làn {number} lấy `{link.field}` từ làn {link.lane} trong cùng vòng, "
                        f"nên làn {link.lane} cũng phải nằm trong vòng lặp.",
                    )


def _check_source(
    questions: QuestionSet,
    answers: Answers,
    order: Mapping[str, int],
    question: SourceQuestion,
    answer: SourceAnswer,
    inputs: dict[str, Any],
) -> Iterator[AnswerIssue]:
    # Lựa chọn ngoài tập và câu hỏi không tồn tại đã bị hình dạng chặn: mỗi câu hỏi có
    # `enum` riêng trong `answer_schema`.
    if answer.choice is Choice.INPUT:
        yield from _check_input(question.id, answer.name, question.value, inputs)

    elif answer.choice is Choice.LLM:
        node = next((llm for llm in answers.llms if llm.id == answer.node), None)
        if node is None:
            yield AnswerIssue(
                question.id,
                "llm_khong_ton_tai",
                f"{question.id} chọn node llm {answer.node!r}, mà câu trả lời không khai node đó.",
            )
        else:
            for name, key in node.inputs.items():
                ref = questions.ref(key)
                if ref is None:
                    continue
                if ref.step not in order:
                    # Phần tử đang lặp: chỉ có nghĩa với node llm nằm trong đúng vòng lặp đó.
                    member = membership(questions, answers, question.step)
                    if member is None or member.question.id != ref.step:
                        yield AnswerIssue(
                            f"llm:{node.id}",
                            "phan_tu_ngoai_vong_lap",
                            f"Input {name!r} đọc phần tử đang lặp của {ref.step}, mà chỗ dùng "
                            "node llm không nằm trong vòng lặp đó.",
                        )
                elif order[ref.step] >= order[question.step]:
                    yield AnswerIssue(
                        f"llm:{node.id}",
                        "doc_buoc_chua_chay",
                        f"Input {name!r} đọc {ref.step}, mà bước đó chạy sau chỗ dùng node llm.",
                    )
                elif _reads_into_loop(questions, answers, reader=question.step, source=ref.step):
                    yield AnswerIssue(
                        f"llm:{node.id}",
                        "nguon_trong_vong_lap",
                        f"Input {name!r} đọc {ref.step}, bước nằm trong một vòng lặp mà chỗ "
                        "dùng node llm thì không.",
                    )

    elif answer.choice is Choice.FROM_STEP:
        if answer.candidate is None:
            yield AnswerIssue(
                question.id, "thieu_ung_vien", "Chọn from_step thì phải nêu ứng viên."
            )
            return
        candidate = question.candidates[answer.candidate - 1]
        if not answers.keeps(candidate.step):
            # Chọn lấy từ một bước là giữ bước đó: hai câu trả lời này không độc lập.
            yield AnswerIssue(
                question.id,
                "nguon_la_buoc_da_bo",
                f"{question.id} lấy giá trị từ {candidate.step}, mà bước đó đã bị bỏ.",
            )
        if _reads_into_loop(questions, answers, reader=question.step, source=candidate.step):
            yield AnswerIssue(
                question.id,
                "nguon_trong_vong_lap",
                f"{candidate.step} nằm trong một vòng lặp; bên ngoài chỉ đọc được kết quả của "
                "cả vòng lặp.",
            )
        if answer.key is None:
            return
        keys = {key.field: key for key in (candidate.element.keys if candidate.element else ())}
        if answer.key not in keys:
            yield AnswerIssue(
                question.id,
                "khoa_khong_dung_duoc",
                f"Ứng viên {answer.candidate} không chọn được phần tử theo {answer.key!r}.",
            )
        elif answer.key_choice is None:
            yield AnswerIssue(
                question.id, "thieu_nguon_khoa", f"Chưa nói {answer.key!r} là hằng hay đầu vào."
            )
        elif answer.key_choice is Choice.INPUT:
            yield from _check_input(question.id, answer.key_name, keys[answer.key].value, inputs)


def _reads_into_loop(questions: QuestionSet, answers: Answers, *, reader: str, source: str) -> bool:
    """`source` nằm trong thân một vòng lặp đã chọn mà `reader` không đọc được nó: `reader`
    ở ngoài vòng lặp, hoặc ở trong nhưng `source` thuộc một vòng khác hay một làn chạy sau."""
    inside = membership(questions, answers, source)
    if inside is None:
        return False
    here = membership(questions, answers, reader)
    if here is None or here.question.id != inside.question.id:
        return True
    lanes = body_order(questions, here.question, answers)
    return inside.position != here.position or lanes.index(inside.lane) >= lanes.index(here.lane)


def _check_input(
    owner: str, name: str | None, value: Any, inputs: dict[str, Any]
) -> Iterator[AnswerIssue]:
    if name is None or not _INPUT_NAME.match(name):
        yield AnswerIssue(owner, "ten_dau_vao_sai", f"Tên đầu vào {name!r} không hợp lệ.")
    elif name in inputs and inputs[name] != value:
        yield AnswerIssue(
            owner,
            "dau_vao_trung_ten",
            f"Đầu vào {name!r} đã đứng cho giá trị {inputs[name]!r}; ở đây giá trị là {value!r}.",
        )
    else:
        inputs[name] = value


def _check_llms(questions: QuestionSet, answers: Answers) -> Iterator[AnswerIssue]:
    used = {answer.node for answer in answers.sources.values() if answer.choice is Choice.LLM}
    for node in answers.llms:
        owner = f"llm:{node.id}"
        if node.id not in used:
            yield AnswerIssue(owner, "llm_khong_ai_dung", f"Không trường nào lấy từ {node.id}.")
        for name, key in node.inputs.items():
            ref = questions.ref(key)
            if ref is None or questions.loop(ref.step) is not None:
                continue
            if not answers.keeps(ref.step):
                yield AnswerIssue(
                    owner,
                    "nguon_la_buoc_da_bo",
                    f"Input {name!r} đọc {ref.step}, mà bước đó đã bị bỏ.",
                )


def _check_computes(
    questions: QuestionSet,
    skeleton: Skeleton,
    answers: Answers,
    order: Mapping[str, int],
    inputs: dict[str, Any],
) -> Iterator[AnswerIssue]:
    computes = {compute.id: compute for compute in answers.computes}
    transforms = {step.ref for step in skeleton.steps if step.kind is StepKind.TRANSFORM}

    for compute in answers.computes:
        owner = f"tinh_toan:{compute.id}"
        if not answers.keeps(compute.after):
            yield AnswerIssue(
                owner, "nguon_la_buoc_da_bo", f"{compute.id} đứng sau {compute.after}, bước đã bỏ."
            )
        for name, value in compute.args.items():
            yield from _check_value(owner, name, value, questions, answers, inputs)
            ref = questions.ref(value.ref) if value.ref else None
            if ref is not None and ref.step not in order:
                yield AnswerIssue(
                    owner,
                    "phan_tu_ngoai_vong_lap",
                    f"Tham số {name!r} đọc phần tử đang lặp; phép tính đứng ngoài vòng lặp.",
                )
            elif ref is not None and order[ref.step] > order[compute.after]:
                yield AnswerIssue(
                    owner,
                    "doc_buoc_chua_chay",
                    f"Tham số {name!r} đọc {ref.step}, bước chạy sau {compute.after}.",
                )

    for condition in answers.conditions:
        owner = f"dieu_kien:{condition.id}"
        if not answers.keeps(condition.before):
            yield AnswerIssue(
                owner,
                "nguon_la_buoc_da_bo",
                f"Điều kiện đứng trước {condition.before}, bước đã bỏ.",
            )
        left = computes.get(condition.left)
        if left is not None:
            if order[left.after] >= order[condition.before]:
                yield AnswerIssue(
                    owner,
                    "doc_buoc_chua_chay",
                    f"Vế trái {left.id} chạy sau {left.after}, không trước {condition.before}.",
                )
        elif condition.left in transforms:
            if not answers.keeps(condition.left):
                yield AnswerIssue(
                    owner, "nguon_la_buoc_da_bo", f"Vế trái là {condition.left}, bước đã bỏ."
                )
            elif order[condition.left] >= order[condition.before]:
                yield AnswerIssue(
                    owner,
                    "doc_buoc_chua_chay",
                    f"Vế trái {condition.left} không chạy trước {condition.before}.",
                )
        else:
            yield AnswerIssue(
                owner,
                "ve_trai_khong_ton_tai",
                f"Vế trái {condition.left!r} không phải phép tính đã khai hay bước transform.",
            )
        if condition.op is ConditionOp.EXISTS:
            if condition.right is not None:
                yield AnswerIssue(owner, "ve_phai_thua", "`exists` không có vế phải.")
        elif condition.right is None:
            yield AnswerIssue(owner, "thieu_ve_phai", f"`{condition.op}` cần một vế phải.")
        elif condition.right.kind == "du_lieu":
            yield AnswerIssue(owner, "ve_phai_sai", "Vế phải là hằng hoặc đầu vào.")
        else:
            yield from _check_value(owner, "phai", condition.right, questions, answers, inputs)


def _check_value(
    owner: str,
    name: str,
    value: ValueAnswer,
    questions: QuestionSet,
    answers: Answers,
    inputs: dict[str, Any],
) -> Iterator[AnswerIssue]:
    if value.kind == "workflow_input":
        yield from _check_input(owner, value.name, value.value, inputs)
    elif value.kind == "du_lieu":
        ref = questions.ref(value.ref or "")
        if ref is not None and questions.loop(ref.step) is None and not answers.keeps(ref.step):
            yield AnswerIssue(
                owner, "nguon_la_buoc_da_bo", f"Tham số {name!r} đọc {ref.step}, bước đã bỏ."
            )


def _check_ids(
    skeleton: Skeleton, questions: QuestionSet, answers: Answers
) -> Iterator[AnswerIssue]:
    seen: dict[str, str] = {TRIGGER_ID: "trigger"}

    def claim(node_id: str, owner: str) -> Iterator[AnswerIssue]:
        if node_id in seen:
            yield AnswerIssue(
                owner, "id_trung", f"Id {node_id!r} đã được dùng cho {seen[node_id]}."
            )
        else:
            seen[node_id] = owner

    for step in skeleton.steps:
        if answers.keeps(step.ref) and not subsumed(questions, answers, step.ref):
            yield from claim(answers.node_id(step.ref), step.ref)
    for question in questions.loops:
        answer = answers.loops.get(question.id)
        if answer is not None and answer.as_loop and answers.keeps(question.loop.steps[0]):
            yield from claim(loop_id(question.id, answers), question.id)
    for llm in answers.llms:
        yield from claim(llm.id, f"llm:{llm.id}")
    for compute in answers.computes:
        yield from claim(compute.id, f"tinh_toan:{compute.id}")
    for condition in answers.conditions:
        yield from claim(condition.id, f"dieu_kien:{condition.id}")


def loop_id(question_id: str, answers: Answers) -> str:
    answer = answers.loops.get(question_id)
    return (answer.id if answer else None) or f"lap_{question_id.lower()}"
