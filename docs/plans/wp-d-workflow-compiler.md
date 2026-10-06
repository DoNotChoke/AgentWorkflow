# WP-D — Workflow Compiler: từ hội thoại và trace tới một draft (Phase 5)

- **Trạng thái:** đề xuất
- **Ngày lập:** 2026-10-01
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §2.4, §5.2, §9, §10, §27, §39, §40, §46;
  ADR-0003, ADR-0006, **ADR-0008**, ADR-0014, **ADR-0015**;
  [`wp-b-quan-sat-trace.md`](wp-b-quan-sat-trace.md) (P1–P7),
  [`wp-b-workflow-ir-va-generic-executor.md`](wp-b-workflow-ir-va-generic-executor.md)

Kế hoạch cho Phase 5. ADR-0008 và ADR-0015 đã chốt *cái gì*; tài liệu này chốt *ở
đâu* và *theo thứ tự nào*.

---

## 1. Hiện trạng

### Đã có, và compiler dùng lại nguyên

| Thứ | Ở đâu | Compiler dùng làm gì |
|---|---|---|
| Trace giữ input/output từng bước | `trace` — `TraceService.get`, `get_payload`, `list_for_conversation` | Nguyên liệu |
| Tin nhắn của conversation | `agent` — `MessageRepository.list_for_conversation` | Ngữ cảnh ý đồ |
| `tool_version`, `connection_id` của từng lời gọi | attribute của bước `tool_call` do Gateway ghi (`entrypoints/wiring.py`) | Dựng node `tool` |
| `validate(ir, catalog)` trả báo cáo, không ném | `workflow/application/validation.py` | Tầng kiểm 2 |
| `literals(ir)` — mọi `const` kèm địa chỉ `(node, field)` | `workflow/domain/validation.py` | Ánh xạ lỗi ↔ câu hỏi |
| `resolve_inputs`, `Scope`, `loop_items` | `workflow/domain/bindings.py` | Tầng kiểm 3 |
| `ModelClient.complete(…, tools=…)` | `agent/application/ports.py` | LLM trả lời bằng tool có schema |
| `WorkflowService.create`, `save_draft` | `workflow/application/service.py` | Lưu kết quả |
| Sáu IR viết tay từ sáu trace thật | `packages/schemas/workflow/examples/kb1…kb6` | Đáp án của compiler |
| SSE + khung chat | `entrypoints/agent.py`, `apps/web/src/components/agent-chat.tsx` | Phiên biên dịch |

### Chưa có

- Không có module compiler nào; chỉ có vài docstring nhắc tới nó.
- Sáu trace của GĐ 0 **không nằm trong repo** — chúng ở dev DB. `tests/fixtures/` chỉ có
  một history của Temporal.
- `WorkflowDraft` và `WorkflowVersion` không có chỗ cho ý đồ.
- Node `transform` chưa vào IR (WP-C mới xong sandbox và tool của Agent).

### Phụ thuộc vào Phase 4

| Việc của WP-D | Cần GĐ nào của WP-B |
|---|---|
| Dựng khung, câu hỏi, bộ lắp, ba tầng kiểm | không — thuần, chạy được hôm nay |
| Chạy thật một workflow đã biên dịch có node `tool` | GĐ 5 |
| Chạy thật workflow có `llm`, `condition` | GĐ 6 |

GĐ 1–5 của kế hoạch này làm song song với Phase 4 được. Nghiệm thu cuối (GĐ 9) thì
không.

---

## 2. Ranh giới của WP-D

**Trong phạm vi.** Đọc conversation + trace, dựng khung, tập câu hỏi v1, Agent biên
dịch (đề xuất, sửa theo lời người dùng, tự sửa theo lỗi), ba tầng kiểm, phiên biên
dịch, lưu draft kèm ý đồ, màn hình biên dịch trong web.

**Ngoài phạm vi.**

| Hạng mục | Vì sao |
|---|---|
| Sửa một workflow đã có bằng lời | Việc kế tiếp. WP-D để lại đúng hai thứ nó cần: ý đồ lưu kèm workflow, và tập lựa chọn đóng. |
| Workflow Editor | Phase 6. WP-D chỉ có một trang xem draft. |
| Node `parallel`, `wait`, `human_approval` | Chưa có trong IR. |
| ~~Bước `transform` trong trace~~ | Node đã vào IR 2.1 (2026-10-01, commit 3 của WP-C) — **giờ nằm trong phạm vi**, xem mục *Sau khi `transform` vào IR* ở §5. |
| Biên dịch lại hàng loạt trace cũ | Cần prompt có version trước — WP-D đặt version, chưa dùng nó để chạy lại. |

---

## 3. Mười hai quyết định

### Q1 — Compiler nằm ở domain `workflow`, chạy trong tiến trình Agent Runtime

ADR-0008 để ngỏ tiến trình. Ba thứ compiler cần — model, tin nhắn, trace — đều đã có
mặt ở Agent Runtime (`:8001`); Control Plane không có model nào.

| Đường | | |
|---|---|---|
| Control Plane, gọi Agent Runtime qua HTTP cho từng lời gọi model | Thêm một hop cho một hội thoại nhiều lượt, và phụ thuộc endpoint *prompt rời* của GĐ 6 | ✗ |
| Tiến trình compiler riêng | Thêm một thứ phải deploy cho một đường code | ✗ |
| **Agent Runtime, qua một module wiring riêng** | §5.2 xếp compiler vào AI Plane; SSE và model đã ở đó | ✓ |

Code chia theo đúng khuôn hexagonal đang có:

- `workflow/domain/compiler/` — thuần, không I/O (contract 3).
- `workflow/application/compile_service.py` — phiên biên dịch, qua port.
- `entrypoints/compiler_wiring.py` — chỗ duy nhất biết `workflow`, `agent`, `trace` và
  catalog tool. Không import `entrypoints/wiring.py` (contract 6).

Agent Runtime lần đầu **ghi** vào schema `workflow`. Nó ghi draft, không bao giờ
publish — và một test giữ điều đó.

### Q2 — Nguyên liệu đi vào compiler như dữ liệu thuần

Contract 1 cấm `workflow` import `agent` và `trace`. Nên một port
`CompileSourceReader` nạp tin nhắn và trace thành dữ liệu thuần, và `read_source` dựng
`SourceMaterial` — các lượt, mỗi lượt gồm tin nhắn người dùng, trả lời cuối, và các
bước tool đã chuẩn hoá. Port hiện thực ở `compiler_wiring`.

Ba luật đọc:

- thứ tự lấy ở bước `tool_call` của Agent, giá trị lấy ở bước con do Gateway ghi (P1);
- bước `failed` bị loại ngay ở đây, trước khi bất kỳ thứ gì nhìn thấy nó (P6);
- **tin nhắn đọc từ `agent.messages`, không từ payload của lời gọi model.** Payload
  trên 16 KB nằm ở object storage và mất được: conversation ba lượt trong dev DB đã
  mất gần hết payload `model_call` sau lần đổi MinIO sang RustFS. Payload chỉ là
  đường lùi cho lượt vỡ giữa chừng, khi không tin nhắn nào được lưu.

Payload ở trạng thái `ABSENT` (vượt trần 1 MB, chỉ còn vân tay) thì bước đó không
biên dịch được — nói rõ, không dựng node rỗng.

### Q3 — Sáu trace thật thành fixture, và thêm hai kịch bản

Compiler thuần thì test bằng dữ liệu. Xuất sáu trace của GĐ 0 kèm tin nhắn thành
`tests/fixtures/sources/kb1…kb6.json`, đã thay id thật.

ADR-0015 thêm hai tình huống mà sáu kịch bản cũ không có:

7. **Có bước thừa** — tìm nhầm trang rồi tìm lại, và đăng một tin thử trước tin thật.
8. **Trải ba lượt** — đã có sẵn trong dev DB, không phải chạy mới: lượt 1 đọc Notion
   rồi vướng chọn connection, lượt 2 chỉ là câu chọn connection mà Agent làm nốt việc
   của lượt 1, lượt 3 là yêu cầu mới dùng `message_ts` của lượt 2. 27 lời gọi tool.

Kịch bản 7 chạy thật qua Agent như GĐ 0. IR đáp án của cả hai viết tay vào
`packages/schemas/workflow/examples/`.

### Q4 — Bộ dò nguồn gốc là tất định, và nó chỉ đưa ra ứng viên

Với mỗi literal trong input của một bước, `provenance.py` tìm:

| Tìm thấy | Thành ứng viên |
|---|---|
| Trong tin nhắn người dùng, **có ép kiểu** (bài học `limit: 10` ở GĐ 0) | `workflow_input`, `const` |
| Y hệt trong output của một bước trước | `from_node` với `path` |
| Trong một phần tử mảng của output trước (P2) | `from_node` với `where` — khoá `where` là trường anh em có giá trị khớp tin nhắn người dùng |
| N lời gọi cùng tool, input lấy từ phần tử 0…N-1 của cùng một mảng (P3) | `loop` |
| Một nhánh input mà phần lớn literal không có nguồn (P4) | node `llm` đứng trước |
| Không đâu cả (P5) | `const`, `mac_dinh_cua_tool` |

Nó không quyết. Nó liệt kê, và tầng 1 dùng đúng danh sách đó để kiểm câu trả lời.

### Q5 — Tập câu hỏi v1: sáu phần của một câu trả lời

*Sửa lại sau khi hiện thực (2026-10-01). Bản đầu có bảy loại; hai loại bỏ đi, một loại
thêm vào.*

| Phần | Hỏi về | Chọn trong |
|---|---|---|
| `buoc` | từng bước | `giu` · `bo`, kèm id node và lý do bỏ |
| `gia_tri` | từng **trường input ở tầng trên cùng** của một bước | `const` · `workflow_input` · `from_step#k` (theo chỉ số, hoặc theo một khoá) · `mac_dinh_cua_tool` · `llm` |
| `vong_lap` | từng cụm lời gọi lặp | `loop` · `trai_phang` |
| `llm` | danh sách 0…n | `instructions` tự do; `dau_vao` chọn trong output đã liệt kê |
| `tinh_toan` | danh sách 0…n — node `transform` trace không có (ADR-0016) | `code` tự do; `args` là dữ liệu đã liệt kê, hằng, hoặc đầu vào; kiểu kết quả chọn trong sáu kiểu |
| `dieu_kien` | danh sách 0…n | bước đứng sau; vế trái là một `tinh_toan` hoặc một bước `transform`; `op` của `ConditionOp`; vế phải là hằng hoặc đầu vào |

Kèm `nhiem_vu` và `ten` (tự do, không ảnh hưởng thực thi).

Ba chỗ khác bản đầu, và vì sao:

- **Không có loại `luot`.** Lượt nào được dùng suy ra từ bước nào được giữ. Hỏi riêng là
  hỏi một điều hai lần, và hai câu trả lời có thể cãi nhau.
- **`ngon_ngu` gộp vào `gia_tri`.** Giá trị không nguồn có thêm lựa chọn `llm`.
- **Đơn vị hỏi là trường ở tầng trên cùng, không phải từng literal.** Binding của IR
  chỉ tới đó: `input` là `Mapping[str, Binding]`, không lồng. 22 literal của `blocks` ở
  kb4 vì vậy là **một** câu hỏi. Một mảng có nguồn ở bước trước — kể cả khi model đã
  cắt bớt trường lúc chép (kb9) — cũng là một câu hỏi, với ứng viên là cả mảng.

`output_schema` của node `llm` **không** do người trả lời viết: nó là schema của đúng
trường mà node đó nuôi, cắt từ `input_schema` của tool tiêu thụ, kèm `$defs`.

**Các câu trả lời không độc lập** — bài học của kb7. Chọn `from_step` vào một bước là
giữ bước đó. Tầng kiểm 1 canh những ràng buộc kiểu này; mỗi lỗi mang id câu hỏi.

**Version của tool đi theo catalog.** Trace kb3 và kb8 ghi `notion.search` 1.0; catalog
giờ chỉ có 1.1. Bộ lắp lấy minor mới nhất của cùng major — minor chỉ thêm, không phá.
Khác major thì giữ nguyên bản đã ghi, để validator báo đúng là không còn tool đó.

Tập câu hỏi mang `QUESTION_SET_VERSION`. Nó sống trong Python, không trong
`packages/schemas/`: đây là contract giữa code và model trong cùng một tiến trình,
không đi qua dây.

### Q6 — LLM trả lời bằng một tool có schema dựng riêng cho từng phiên

Mỗi phiên dựng một JSON Schema từ chính tập câu hỏi của nó: id câu hỏi là `enum`,
lựa chọn của từng câu là `enum`. Model nhận nó như `input_schema` của tool
`tra_loi`. Câu trả lời ngoài tập bị chặn ở tầng schema trước khi tới tầng 1 — tầng 1
vẫn chạy, vì không provider nào bảo đảm tuyệt đối.

Model dùng cho compiler là một setting (`compiler_model`), mặc định là model của Agent
nguồn. Đi qua `ModelClient` như mọi lời gọi khác.

### Q7 — LLM giả định; code chỉ trình bày giả định đó ra màn hình

**Mọi giả định là của LLM**: nhiệm vụ là gì, giá trị nào cố định, bước nào bỏ, vì
sao. Nó nộp chúng dưới dạng có cấu trúc — các lựa chọn của Q5, kèm câu `nhiem_vu`,
`instructions`, và một dòng lý do cho mỗi lựa chọn.

Code không giả định gì. Nó chỉ **xếp** các lựa chọn đó thành tin nhắn: *"kênh: cố
định — vì bạn nói 'kênh #support' · khoảng thời gian: đầu vào · bỏ 2 bước"*. Lời lẽ
giải thích là của LLM; khung trình bày là của code.

Lý do không để LLM tự viết cả đoạn văn rồi nộp riêng các lựa chọn: hai thứ đó có thể
lệch nhau — văn nói *"kênh cố định"* mà lựa chọn ghi `workflow_input`. Dựng tin nhắn
từ chính các lựa chọn thì thứ người dùng đọc luôn là thứ bộ lắp nhận (quyết định 6
của ADR-0015).

Tin nhắn gồm: nhiệm vụ; đầu vào; giá trị cố định; lượt được dùng; **bước bị bỏ kèm lý
do**; `instructions` nguyên văn của từng node `llm`; phần chưa biên dịch được. Lựa
chọn mặc định hiển nhiên (literal khớp nguyên văn output trước) gộp thành một dòng
đếm, mở ra xem được.

### Q8 — Ba tầng kiểm chạy **trước** khi người dùng thấy đề xuất

Kiểm sau khi người dùng đồng ý thì vòng sửa của LLM chạy *sau* xác nhận — và mọi lần
sửa đều là đổi một giả định đã xác nhận.

Nên thứ tự hiện thực là:

```
đề xuất → lắp thử trong bộ nhớ → 3 tầng kiểm ─┬─ lỗi → LLM đổi lựa chọn (có trần) ─┐
              ▲                               │                                    │
              └───────────────────────────────┼────────────────────────────────────┘
                                              └─ sạch → hiện cho người dùng
người dùng sửa bằng lời → LLM đổi lựa chọn → quay lại đầu
người dùng đồng ý → lắp lại (tất định, cùng kết quả) → lưu draft
```

Người dùng chỉ thấy đề xuất đã qua kiểm. Sau khi họ đồng ý không còn lời gọi model
nào. *"Không draft nào được dựng trước khi xác nhận"* vẫn đúng: IR lắp thử không được
lưu.

ADR-0015 đã sửa theo đúng thứ tự này (quyết định 12–14).

Hết trần mà còn lỗi: hiện đề xuất kèm lỗi còn lại, bằng lời. Trần mặc định 3 vòng.

### Q9 — Lỗi của cả ba tầng mang id câu hỏi

`Issue` hiện có `(code, node, field)`. Bộ lắp giữ một bảng `(node, field) → id câu
hỏi` lúc dựng IR; `compile_report` gắn id đó vào từng lỗi. LLM nhận *"L3: nguồn
`from_node#2` không có trường `id`"*, không nhận một đường trong IR nó chưa từng thấy.

Lỗi không truy được về câu hỏi nào là lỗi của bộ lắp — bug của code, không trả cho
LLM, ghi log và hỏng phiên.

### Q10 — Tầng 3 dùng lại `resolve_inputs`, không viết bộ thông dịch thứ hai

`replay.py` dựng `Scope` từ dữ liệu trace: `inputs` là giá trị gốc của các literal đã
thành `workflow_input`; `outputs` là output đã ghi; output của node `llm` là chính
nhánh literal mà nó thay. Rồi gọi `resolve_inputs` và so với input trace đã gửi.

Không so: trường `mac_dinh_cua_tool`; bước bị bỏ; trường người dùng đã chủ động đổi
(đánh dấu ở lựa chọn). Lệch là `Issue` mức lỗi.

### Q11 — Phiên biên dịch là một bảng, và có luồng chat riêng

`workflow.compile_sessions`: nguồn (conversation, các trace, băm của khung), đề xuất
hiện tại (JSON), trạng thái, các tin nhắn của phiên, `workflow_id` kết quả, và ba
version — tập câu hỏi, prompt, model (§40).

```
proposing → awaiting_user ⇄ revising → confirmed → compiled
     └──────────────┴───────────┴──→ failed | abandoned
```

Giao diện là một **cửa sổ chat bật lên riêng**, không phải luồng chat đang có. Người
dùng nói chuyện với một Agent khác — Agent biên dịch — có prompt hệ thống riêng và
một nhiệm vụ: hiểu ngữ cảnh để dựng workflow. Nó không có tool nào của Slack hay
Notion; tool duy nhất của nó là `tra_loi`.

Tin nhắn của phiên **không** ghi vào conversation nguồn: conversation là nguyên liệu,
ghi thêm vào nó là sửa nguyên liệu giữa lúc đang đọc.

Đồng ý là một nút bấm, gọi một endpoint riêng. Chat chỉ để đổi. Sau nút đó không còn
lời gọi model nào.

### Q12 — Ý đồ nằm ở draft và ở version

Thêm cột `intent` cho `workflow_drafts` và `workflow_versions`; publish chép từ draft
sang. Nó mô tả đúng IR đó, nên nó đi cùng IR đó — đặt ở `workflows` thì v1 và v5 dùng
chung một câu dù đã khác việc. Executor không đọc cột này.

Version thêm `compiled_from` (id phiên) để truy được một workflow sinh từ đâu.

---

## 4. Kế hoạch code

### Thêm mới

```
services/api/app/domains/workflow/
  domain/compiler/
    source.py        SourceMaterial, Turn, SourceStep — dữ liệu thuần
    normalize.py     chặng 1: bỏ nhiễu, giữ thứ ảnh hưởng hành vi
    skeleton.py      chặng 2: khung từ nhiều lượt
    provenance.py    Q4: ứng viên nguồn gốc
    questions.py     Q5: tập câu hỏi v1, schema trả lời theo phiên (Q6)
    answers.py       câu trả lời + tầng kiểm 1
    assemble.py      khung + câu trả lời → IR, kèm bảng truy ngược (Q9)
    replay.py        tầng kiểm 3
    report.py        báo cáo chung của ba tầng
    proposal.py      đề xuất → tin nhắn giả định (Q7)
    session.py       entity và máy trạng thái của phiên
  application/
    compile_service.py   điều phối phiên; vòng tự sửa
    ports.py             + CompileAdvisor, CompileSourceReader, CompileSessionRepository
  infrastructure/
    compile_models.py, compile_repositories.py
    prompts/compiler_v1.md

services/api/app/entrypoints/
  compiler_wiring.py     nối workflow ↔ agent ↔ trace ↔ catalog; ModelClient → CompileAdvisor
  compiler.py            router, mount vào app của Agent Runtime

services/api/alembic/versions/   compile_sessions; intent + compiled_from
services/api/tests/fixtures/sources/kb1…kb8.json
apps/web/src/components/compile-dialog.tsx, assumption-card.tsx
apps/web/src/lib/compile.ts
apps/web/src/app/(app)/workflows/[id]/page.tsx   xem draft, chỉ đọc
```

### Sửa

| File | Sửa gì |
|---|---|
| `core/ids.py` | `ResourceKind.COMPILE_SESSION` |
| `core/config.py` | `compiler_model`, trần vòng sửa, trần số bước và cỡ prompt |
| `workflow/domain/entities.py`, `infrastructure/models.py`, `repositories.py` | `intent`, `compiled_from` |
| `workflow/application/service.py` | `publish` chép `intent`; `save_draft` nhận `intent` |
| `entrypoints/agent.py` | mount router của compiler |
| `pyproject.toml` | contract 9: tiến trình Agent Runtime không gọi `publish` |
| `apps/web/src/components/agent-chat.tsx` | nút *Chuyển thành Workflow* |

---

## 5. Thứ tự commit

Mỗi commit xanh độc lập. GĐ 1–4 không gọi model, không DB.

**GĐ 0 — Giấy tờ**

1. `docs: ADR-0015, the compiler asks what the workflow is for` — kèm sửa bảng chặng
   theo Q8, và kế hoạch này.

**GĐ 1 — Nguyên liệu**

2. ✅ `test: six real conversations the compiler must understand` — fixture kb1–kb6 và
   kb8, cùng `_export.py` đã xuất chúng.
3. ✅ `feat(api): what the compiler reads, as plain data` — `source.py`, `normalize.py`.
   Test: P1 (đọc đúng tầng), P6 (bỏ `failed`), payload mất, lời gọi lặp, nhiều lượt.
4. ✅ `test: a conversation with wasted steps` — kb7 chạy thật ngày 2026-10-01, bốn
   lượt: liệt kê kênh, đăng thử `ping`, tìm nhầm trang EC2, rồi việc thật (tìm trang
   AWS, đăng link vào kênh test). IR đáp án của kb7 và kb8 viết ở GĐ 3, khi bộ lắp cho
   biết đáp án phải có hình dạng nào.

**Hai điều kb7 cho thấy, phải giải ở GĐ 2–3:**

- *Lượt "thừa" có thể là nguồn dữ liệu.* Lượt 4 không gọi `list_channels` lại; id kênh
  nó dùng đến từ lượt 1. Bỏ lượt 1 thì `channel` chỉ còn là hằng số. Câu hỏi `luot` và
  `giu_bo` vì vậy không độc lập với câu hỏi `nguon`: chọn `from_node` vào một bước là
  giữ bước đó.
- *`where` cần so được với mảng.* `notion.search` 1.1 trả `title` là mảng các đoạn
  (`["AWS"]`), còn `where` của IR chỉ so bằng với một giá trị vô hướng. Chọn trang theo
  tên vì vậy chưa biểu diễn được bằng `where` — hoặc tool trả thêm một `title` đã ghép,
  hoặc bước đó thành node `llm`. Quyết ở GĐ 2.

**GĐ 2 — Khung**

5. ✅ `feat(api): a skeleton from every turn of a conversation` — `skeleton.py`.
6. ✅ `feat(api): where each value in a trace came from` — `provenance.py`. Số literal
   từ câu gốc / từ bước trước / không nguồn của kb1–kb6 khớp bảng tổng của GĐ 0, và
   `limit: 10` của kb4 giờ khớp câu gốc nhờ ép kiểu.

**Bốn điều dữ liệu thật buộc bộ dò phải biết** (không có trong Q4 lúc lập kế hoạch):

- *Output chép lại input không phải là nguồn.* `post_message` trả `channel_id`; không
  loại nó thì mọi tin nhắn sau đều "lấy id kênh từ tin nhắn trước", và nguồn thật —
  `list_channels` — bị đẩy ra khỏi danh sách ứng viên. Giá trị output trùng một giá
  trị input của chính bước đó thì không vào chỉ mục.
- *UUID viết hai kiểu là một giá trị.* Ở kb8 model bỏ gạch nối của id trang trước khi
  gọi tool kế tiếp. Ứng viên như vậy mang `exact=False`; **tầng kiểm 3 (GĐ 4) phải
  chấp nhận lệch đúng kiểu đó**, không báo lỗi.
- *Khoá `where` chỉ hợp lệ khi nó chọn đúng phần tử đầu tiên khớp* — đúng ngữ nghĩa
  `where` của executor. Hai kênh cùng tên thì không có khoá, chỉ còn chỉ số.
- *Chuỗi ngắn hơn ba ký tự và giá trị không phải chuỗi không dò với output.* Trùng do
  tình cờ (`false`, `1`) nhiều hơn do phụ thuộc.

**Hai thứ bộ dò không thấy, để lại cho chặng LLM hoặc nói thẳng là chưa làm được:**

- *Chọn trang Notion theo tên.* `title` là mảng, nên không có khoá `where`; ứng viên
  chỉ là `results.0.id`. Giữ đúng quyết định đã ghi ở WP-B (cảnh báo `chi_so_tho`, chờ
  một `title_text` ghép sẵn ở phía tool) — không thêm phép so vào IR.
- *Vòng lặp ghép đôi.* Lượt 3 của kb8: sáu tin trả lời, mỗi tin có `thread_ts` lấy từ
  một bước khác nhau của lượt 2 và `text` do model viết. Không phải vòng lặp trên một
  mảng, nên `loops` không báo. kb8 nhiều khả năng kết thúc ở *"biên dịch được lượt 1,
  phần còn lại chưa tổng quát hoá được"* — và đó là câu trả lời đúng cho IR v2.
  (Sai: IR biểu diễn được, bộ dò mới là thứ không thấy. Xem *Vòng lặp nhiều làn* ở GĐ 5.)

**Sau khi `transform` vào IR** (2026-10-01, làm trước GĐ 3 theo yêu cầu)

Node `transform` có trong IR 2.1; kb5 mẫu viết lại thành `lich_su → dem (transform) →
du_nhieu (condition gt 5) → dang`. Ba fixture thật mới: `kb5b-co-transform-khong-dung`,
`kb9-transform-doi-hinh-dang`, `kb10-transform-dem`. Chúng đổi ba thứ trong kế hoạch:

- *Khung phải nhận bước `system.transform`.* Hiện `build_skeleton` xếp nó vào
  `unsupported`. GĐ 3 đưa nó thành một loại bước: `code` bê nguyên, `args` là chỗ có
  literal.
- *Bộ dò phải nhận ra phép chiếu.* Model chép dữ liệu vào `args` bằng tay và chỉ giữ
  trường nó cần (kb9: mỗi tin nhắn còn mỗi `text`). So nguyên cây con sẽ không khớp;
  phải so từng lá rồi gom về tiền tố chung — `args.messages.N.text` ← `messages.N.text`
  với mọi N nghĩa là `args.messages` ← `messages`. Tầng kiểm 3 chạy lại code trên
  output đầy đủ và so kết quả, vì Agent biên dịch chạy ở Agent Runtime, nơi sandbox
  được phép nạp.
- *Điều kiện không đến từ trace, kể cả khi Agent có `transform`.* Hai lần chạy lại
  kb5, model không đếm bằng code — nó xin `limit: 6` và coi *nhận đủ 6* là *nhiều hơn
  5*. Vậy với `dieu_kien` chỉ còn hai đường, và phải chọn trước commit 9:

  | Đường | Được | Mất |
  |---|---|---|
  | LLM biên dịch **viết** một node `transform` (`return len(args['messages'])`) rồi `condition gt` | Ngưỡng thành hằng hoặc đầu vào; đọc được | Code không còn là *"giá trị đã chạy thật và đã được quan sát"* (WP-C §1) — cần ADR, và code phải hiện nguyên văn cho người dùng như `instructions` |
  | `exists` trên `messages.N`, đúng mẹo model đã dùng | Không ai viết code mới | Ngưỡng đóng cứng trong đường; `limit` phải bằng N+1 và hai số đó phải đi cùng nhau |

  Đường một kiểm được bằng dữ liệu: chạy code trên output đã ghi, điều kiện phải ra
  đúng nhánh mà trace đã đi.

**GĐ 3 — Câu hỏi và bộ lắp** ✅ (2026-10-01)

7. ✅ `feat(api): the questions a compiler may ask` — `questions.py` (tập câu hỏi và
   JSON Schema của câu trả lời, dựng riêng cho từng phiên), `answers.py` (đọc câu trả
   lời, tầng kiểm 1). Khung nhận bước `system.transform`; bộ dò nhận ra cả một mảng
   lấy từ bước trước, kể cả khi đã bị cắt bớt trường.
8. ✅ `feat(api): an IR assembled from answers` — `assemble.py`, kèm bảng truy ngược
   `(node, trường) → câu hỏi`.
9. ✅ `feat(api): a branch the trace only took one side of` — `tinh_toan` và
   `dieu_kien` theo ADR-0016; đáp án là kb5b.

**Nghiệm thu GĐ 3.** Câu trả lời viết tay ở `tests/fixtures/compiled/*.answers.json`,
IR đáp án ở `*.ir.json` cạnh đó. Chín conversation thật lắp ra đúng đáp án, và cả chín
qua `validate` với catalog thật — cảnh báo duy nhất là `chi_so_tho`.

| Kịch bản | IR lắp ra |
|---|---|
| kb1 | `dang` |
| kb2 | `kenh → dang` — **trùng khớp hoàn toàn với ví dụ viết tay ở GĐ 0** |
| kb3 | `tim → con → moi_trang[doc_trang]`; bước `get_page` thừa bị bỏ |
| kb4 | `doc → tom_tat (llm) → tao` |
| kb5b | `lich_su → dem (transform) → du_nhieu (condition) → dang`; ngưỡng là đầu vào |
| kb6 | không có gì để biên dịch |
| kb7 | `kenh → tim → dang`; tin `ping` và lần tìm nhầm bị bỏ |
| kb8 | `tim → khoi → moi_khoi[doc_khoi]`; 19 bước của lượt 2 và 3 bị bỏ kèm lý do |
| kb9, kb10 | `doc → doi/dem (transform)`; code của Agent bê nguyên |

Đáp án là file mới, không phải các ví dụ trong `packages/schemas/workflow/examples/`:
ví dụ viết từ ý định, không từ trace. Ví dụ kb3 có `filter: child_page` mà trace không
cho căn cứ (cả bốn phần tử đều được dùng); ví dụ kb6 có một node mà bước duy nhất của
trace đã hỏng.

**Để lại cho GĐ 4**, vì cần chạy code hoặc cần đối chiếu với trace:

- chạy `code` của `tinh_toan` trong sandbox trên output đã ghi, và kiểm điều kiện ra
  đúng nhánh trace đã đi (ADR-0016, quyết định 3). Sandbox không nạp được từ domain
  `workflow` — Control Plane import domain này, và contract 8 cấm nó nạp sandbox — nên
  việc chạy đi vào qua một port;
- `hang_viet_cung` thành lỗi với code do LLM viết (quyết định 4);
- gắn id câu hỏi vào lỗi của `validate` qua bảng truy ngược đã có.

**GĐ 4 — Ba tầng kiểm** ✅ (2026-10-02)

10. ✅ `feat(api): a compiled plan must reproduce the run it came from` —
    `workflow/domain/conditions.py` (tính một node `condition`; executor của WP-B GĐ 6
    sẽ dùng lại) và `compiler/replay.py`. Đi qua từng node với output đã ghi trong
    trace thay cho mọi lời gọi ra ngoài:

    | Node | Kiểm |
    |---|---|
    | `tool` | input tính từ binding so với input trace đã gửi |
    | `loop` | số vòng bằng số lời gọi trong trace; input từng vòng so với từng lời gọi |
    | `transform` của Agent | chạy lại code trên dữ liệu đầy đủ, kết quả phải bằng kết quả đã ghi |
    | `transform` do người trả lời viết | code chạy được, kết quả khớp kiểu đã khai (ADR-0016) |
    | `condition` | phải ra `true`: các bước sau nó đã chạy trong trace |

    So *"cùng dữ liệu"* chứ không so từng byte: bản trace đã gửi được phép là bản model
    chép tay và cắt bớt trường, hoặc một UUID viết kiểu khác — cùng hàm `projects` mà
    bộ dò dùng. Một node không cho ra output thì dừng luôn: phần sau chỉ còn lỗi ăn theo.
11. ✅ `feat(api): every error names the question that caused it` — `compiler/report.py`
    và `application/compile_check.py`. `check_answers()` là cửa duy nhất GĐ 5 gọi: câu
    trả lời thô vào, IR đã kiểm hoặc danh sách lỗi theo câu hỏi ra. Tầng sau chỉ chạy
    khi tầng trước sạch.

    Lỗi của IR truy về câu hỏi qua `origin` — từ trường cụ thể nhất lùi dần — rồi rơi về
    `owners` của node. Không truy được là lỗi của bộ lắp (`is_internal`), không trả cho
    người trả lời. `hang_viet_cung` thành lỗi với code do người trả lời viết.

**Nghiệm thu GĐ 4.** Chín đáp án đi qua cả ba tầng, cảnh báo duy nhất là `chi_so_tho`.
Mỗi kiểu hỏng có một test chỉ đúng câu hỏi: điều kiện ngược chiều, code đếm sai, code
vỡ lúc chạy, kết quả sai kiểu, ngưỡng viết cứng, binding trỏ sai phần tử, vòng lặp
chạy nhiều vòng hơn trace, code Agent cho kết quả khác trên dữ liệu đầy đủ. Sandbox
trong test là sandbox thật.

**Sandbox đi vào qua tham số.** `replay()` nhận một hàm chạy code; port
`TransformRunner` nằm ở `workflow/application/ports.py`. Domain `workflow` không nạp
`core.sandbox`: Control Plane import domain này, và contract 8 cấm nó. Hiện thực thật
của port đến ở GĐ 5, trong `entrypoints/compiler_wiring.py`.

**Thứ tầng 3 không thấy**, để người dùng bắt ở chặng xác nhận:

- lựa chọn `const` luôn tái tạo đúng, kể cả khi lẽ ra phải là đầu vào;
- nhánh chưa đi của một điều kiện;
- `instructions` của node `llm` — output của nó được thay bằng giá trị trong trace;
- bước bị bỏ mà không bước nào khác phụ thuộc vào.

**GĐ 5 — Hai lời gọi model** ✅ (2026-10-02)

Luồng đổi so với lúc lập kế hoạch: **hai model, hai lần xác nhận** (ADR-0015, quyết
định 1–3 và 13–14, đã sửa). Q6–Q8 ở trên viết cho một model; chỗ nào lệch thì ADR đúng.

| | Model hiểu ý đồ | Model trả lời câu hỏi |
|---|---|---|
| Đọc | lời người dùng, lời Agent, tên tool + thành công/hỏng | ý đồ đã chấp nhận, tập câu hỏi, dữ liệu trace đã cắt ngắn |
| Không đọc | tham số và output của tool | — |
| Nộp | `de_xuat_y_do`: nhiệm vụ, thay đổi, giữ nguyên, điều kiện, không thuộc nhiệm vụ | `tra_loi`, theo `answer_schema` của phiên |
| Hội thoại | có: người dùng sửa bằng lời | không: lỗi của ba tầng kiểm quay lại, tối đa `compiler_max_rounds` |

12. ✅ `feat(api): the assumptions, written from the choices` — `compiler/proposal.py`.
    Bảng giả định dựng từ câu trả lời; `code` và `instructions` hiện nguyên văn; tham số
    để mặc định của tool được nêu kèm giá trị trace đã gửi.
13. ✅ `feat(api): a model that answers, and nothing else` — `compiler/intent.py`,
    `digest.py`, `briefing.py`, hai prompt ở `compiler/prompts/*.v1.md`; port
    `IntentAdvisor`, `AnswerAdvisor`; `entrypoints/compiler_wiring.py` hiện thực cả hai
    bằng `ModelClient`, và `TransformRunner` bằng sandbox thật.
14. ✅ `feat(api): errors go back to the model until the proposal is clean` —
    `application/compile_flow.py`: `propose_intent()` và `build()`.

Câu trả lời của model thứ hai không còn `nhiem_vu` — ý đồ đã được chấp nhận trước đó —
và mỗi lựa chọn có thêm `ly_do` để hiện cho người dùng.

Cấu hình: `compiler_intent_model`, `compiler_answer_model` (mặc định cùng là
`gpt-6.1-sol`), `compiler_max_rounds`, `compiler_max_briefing_chars`.

**Chạy thật, 2026-10-02.** `.env` chưa có `AW_OPENAI_API_KEY`, nên `gpt-6.1-sol` chưa
gọi được; ba lượt chạy dưới đây dùng `claude-sonnet-5` qua cùng `ModelClient`. Lượt ba
dừng giữa chừng vì tài khoản Anthropic hết số dư.

| Kịch bản | Ý đồ | Câu trả lời | IR |
|---|---|---|---|
| kb1 | đúng | 1 vòng | qua ba tầng; cùng hình dạng với đáp án |
| kb2 | đúng | 3 vòng (sai hình dạng `nguon_khoa`) | qua ba tầng; cùng hình dạng |
| kb3 | đúng | 1 vòng | qua ba tầng; giữ thêm bước `get_page` mà đáp án bỏ |
| kb4 | đúng | 1 vòng | qua ba tầng; cùng hình dạng, có node `llm` |
| kb5b | đúng, nhận ra điều kiện | 3 vòng (code `NameError` hai lần) | qua ba tầng; `transform` + `condition` |
| kb6 | model hỏi lại: *không có việc nào hoàn thành* | — | — |
| kb7 | đúng, nêu `ping` và lần tìm nhầm là không thuộc nhiệm vụ | 1 vòng | qua ba tầng; cùng hình dạng |
| kb8 | đúng | câu trả lời bị cắt ở trần token | chưa có |
| kb9, kb10 | — (hết số dư) | | kb10 đã qua ở lượt một |

Năm điều lượt chạy thật buộc phải sửa, tất cả đã vào code:

- *Đề xuất ý đồ sai hình dạng.* Model nộp `thay_doi` là một chuỗi, hoặc chèn cả
  `<![CDATA[…]]>`. `propose_intent` giờ trả lỗi lại cho model như với câu trả lời.
- *Trường thừa là chỗ điền nhầm.* Model điền `node: "l1.b1"` cho một lựa chọn
  `from_step`. Schema của từng câu hỏi giờ chỉ có trường mà lựa chọn của chính câu đó
  dùng tới.
- *`nguon_khoa` bị thêm trường.* Thêm mô tả nói rõ nó chỉ có `chon` và `ten`.
- *Code đọc thẳng biến `messages`.* Prompt giờ nói `args` là biến duy nhất, kèm ví dụ.
- *Câu trả lời bị cắt.* kb8 có 27 bước; câu trả lời vượt trần token, model nộp một JSON
  cụt, mỗi vòng hỏi lại ra đúng thứ đó. Advisor giờ dừng ngay với lỗi nói rõ lý do.

**Vòng tự sửa có tác dụng thật.** Ở kb5b model viết `return len(messages)`; tầng 3 chạy
code, báo `NameError`, và tới vòng ba model sửa đúng. Không tầng nào trước đó thấy lỗi
này.

**Chạy lại với `gpt-6.1-sol`, 2026-10-02** — sau khi có `AW_OPENAI_API_KEY`, với prompt
ý đồ đã viết lại để tìm *việc gần nhất* trong conversation.

| Kịch bản | Ý đồ | Câu trả lời | IR |
|---|---|---|---|
| kb1, kb2, kb4, kb5b, kb7, kb9, kb10 | đúng | **1 vòng** | qua ba tầng; cùng các loại node với đáp án viết tay |
| kb3 | đúng | 1 vòng | qua ba tầng; giữ thêm bước *mở trang* mà đáp án bỏ — người dùng đã nói *"mở trang đó ra xem"* |
| kb6 | model hỏi lại | — | — |
| kb8 | đúng | 2 vòng | qua ba tầng, **trải phẳng**: 27 bước, 12 node `llm`, 13 cảnh báo `chi_so_tho` |

27 lời gọi model, khoảng 190 nghìn token vào và 28 nghìn token ra cho cả mười kịch bản
(kb8 chạy ba lần).

Ba thứ lượt chạy này buộc phải sửa:

- *`gpt-6.1-sol` không nhận function tool qua Chat Completions* khi đang suy luận.
  `OpenAIModelClient` có thêm đường `/v1/responses` cho các model trong
  `RESPONSES_API_MODELS`; model khác đi đường cũ, không đổi.
- *Bộ lắp phát một node `llm` nhiều lần.* Ở kb8 model cho sáu bước cùng đọc một node;
  bộ lắp phát nó sáu lần — id trùng, đồ thị có chu trình, và lỗi không truy được về câu
  hỏi nào nên vòng tự sửa dừng. Giờ node được phát một lần, trước bước đầu tiên đọc nó;
  hai bước gửi hai nội dung khác nhau thì tầng 3 báo `khong_tai_tao_duoc` ở đúng câu hỏi.
- *Trần mười node `llm` quá thấp* cho một conversation trải phẳng. Nâng lên hai mươi
  (về lại mười khi có vòng lặp nhiều làn, xem dưới).

**kb8 biên dịch được, nhưng không tổng quát.** IR lắp ra đúng cho một trang có đúng sáu
câu hỏi: mọi binding trỏ theo vị trí. Trang có bảy câu thì câu thứ bảy bị bỏ sót, lặng
lẽ. Mười ba cảnh báo `chi_so_tho` là dấu hiệu duy nhất; bảng giả định phải làm cho
người dùng thấy điều đó, không để nó chìm trong danh sách.

**Vòng lặp nhiều làn, 2026-10-04** — để kb8 thành một vòng lặp thay vì 27 bước trải phẳng.

IR vẫn như cũ: thân `loop` vốn đã nhận nhiều node. Thứ thiếu là bộ dò không thấy rằng
sáu tin đăng, sáu lần đọc đáp án và sáu tin trả lời đều *thuộc về* sáu khối của vòng lặp
ở lượt 1.

- *Làn.* Một nhóm bước liền nhau gọi cùng tool, đứng sau một vòng lặp và có đúng số bước
  bằng số vòng. Bước thứ k của làn ứng với vòng thứ k. `LoopCandidate.lanes`; vòng lặp
  gốc là làn 0.
- *Hai mức bằng chứng.* `du_lieu`: một trường của bước thứ k lấy từ cùng một chỗ trong
  dữ liệu của vòng thứ k — phần tử đang lặp, hoặc output của một làn đứng trước.
  `vi_tri`: chỉ có số bước khớp, còn thứ thay đổi là nội dung model tự soạn. Làn theo vị
  trí được đưa ra như một đề nghị; người trả lời quyết theo ý đồ, và tài liệu nói rõ là
  không có dữ liệu nào nối nó với từng phần tử.
- *Khoá suy ra.* Khối đáp án của kb8 đứng ở vị trí 5, 7 hay 6 tuỳ câu. Bộ dò tìm một
  trường mà ở **mọi** vòng, phần tử được chọn là phần tử đầu tiên mang giá trị đó
  (`type` = `toggle`), và binding thành `where`. Không tìm được thì làn không có mối nối
  đó. Bảng giả định ghi rõ đây là điều suy ra từ lần chạy mẫu.
- *Câu trả lời.* `vong_lap.V1.lan` là danh sách số làn được gộp. Chỉ bước đầu của mỗi
  làn có câu hỏi phải trả lời; trường đã có mối nối thì không hỏi. Node `llm` đọc được
  `V1:phan_tu` và output của làn đứng trước trong cùng vòng.
- *Tầng 1* chặn: làn thiếu bước, làn lấy dữ liệu từ một làn không được gộp, node `llm`
  ngoài vòng lặp đọc phần tử đang lặp hay một bước trong thân.
- *Bộ lắp.* Thân vòng lặp là bước đầu của từng làn, theo thứ tự; node `llm` đứng ngay
  trước bước đọc nó, trong thân. Node `loop` bị đẩy xuống sau node ngoài cuối cùng mà
  thân nó đọc (kb8: `kenh` chạy ở lượt 2, sau làn gốc).
- *Tầng 3* chạy từng vòng với output riêng của các node trong thân: node của làn j ở
  vòng k được so với bước thứ k của làn j, và đọc được đúng output của vòng k.

**Thứ tự chạy đổi.** Lần chạy mẫu đăng sáu câu hỏi rồi mới trả lời sáu đáp án; workflow
đăng và trả lời từng câu một. Bảng giả định nói điều đó ở dòng đầu của vòng lặp, và
prompt bảo model đừng gộp khi ý đồ đòi thứ tự kia.

kb8 với đáp án viết tay: 4 node ngoài, một `loop` sáu node, 2 node `llm` (trước là 12),
1 cảnh báo `chi_so_tho` (trước là 13) — còn lại là chọn trang Notion theo vị trí. Trần
node `llm` về lại mười.

Chạy thật với `gpt-6.1-sol` cùng ngày: **1 vòng**, qua ba tầng, cùng hình dạng với đáp án
viết tay — model tự chọn gộp cả ba làn và khai đúng hai node `llm`, cho chúng đọc cả
`V1:phan_tu` lẫn output của làn đứng trước. 2 lời gọi model, 20,5 nghìn token vào và
1,5 nghìn token ra (lần trải phẳng: 2 vòng, câu trả lời từng vượt trần token).

**Bước so le, 2026-10-04.** Agent làm từng phần tử một (`đọc 1, đăng 1, trả lời 1, đọc 2,
…`) thì không hai bước cùng tool nào đứng liền nhau, và cả vòng lặp gốc lẫn các làn đều
không được nhận ra.

- Bộ dò cắt các bước thành *khối*. Khối một nhóm là dãy liền nhau như cũ. Khối nhiều
  nhóm là một chu kỳ lặp lại ít nhất hai lần — cùng tool, cùng tên trường ở từng vị trí
  — và mỗi vị trí trong chu kỳ là một nhóm. Chu kỳ chỉ thắng khi phủ nhiều bước hơn dãy
  liền nhau.
- Nhóm đầu tiên của chu kỳ lặp trên một mảng thành vòng lặp gốc; các nhóm còn lại qua
  đúng phép kiểm làn cũ. Điều kiện *"làn đứng sau vòng lặp"* thành *"ở mỗi vòng, bước
  của làn chạy sau bước của vòng lặp"*.
- Một vòng gọi cùng một tool hai lần với cùng tên trường thì chu kỳ ngắn nhất là sai
  (`đọc, đăng` thay vì `đọc, đăng, đọc, đăng`). Khi không nhóm nào lặp trên mảng, bộ dò
  thử chu kỳ dài gấp đôi, gấp ba.
- Mọi thứ sau bộ dò không đổi: kb8 xếp lại so le lắp ra **đúng IR của kb8**. Bảng giả
  định chỉ báo thứ tự chạy đổi khi nó đổi thật.

Fixture so le dựng từ kb8 bằng cách xếp lại các bước; chưa có conversation thật nào mà
Agent tự làm so le.

Chạy thật bản so le với `gpt-6.1-sol`: **1 vòng**, qua ba tầng, cùng hình dạng với đáp
án của kb8 — một `loop` bốn bước, hai node `llm`, một cảnh báo `chi_so_tho`. 2 lời gọi
model, 20,3 nghìn token vào và 1,6 nghìn token ra.

**Chưa làm trong đợt này:**

- chu kỳ phải đều. Một phần tử thiếu một bước (câu hỏi không có đáp án) hay nhóm đứng
  *trước* vòng lặp gốc trong chu kỳ thì phần đó vẫn trải phẳng;
- dãy liền nhau gọi cùng tool hai lần mỗi vòng mà không có gì chen giữa (`đọc câu hỏi 1,
  đọc đáp án 1, đọc câu hỏi 2, …`);
- `tinh_toan` và `dieu_kien` chưa đặt được vào thân vòng lặp;
- vòng lặp lồng nhau;
- executor (Phase 4) chưa chạy node `llm` trong thân `loop`.

**Còn mở sau GĐ 5:**

- prompt chưa có vân tay canh version như prompt nền tảng của Agent — thêm khi nội dung
  được duyệt;
- chưa có conversation nào trong fixture chứa hai việc không liên quan, là trường hợp
  prompt ý đồ mới được viết lại để xử lý;
- bảng giả định chưa gộp nhiều cảnh báo cùng loại thành một dòng dễ thấy.

**GĐ 6 — Phiên**

15. `feat(api): a workflow remembers what it is for` — migration `intent`,
    `compiled_from`; `publish` chép.
16. `feat(api): a compile session, from proposal to draft` — entity, migration,
    repository, `compile_service`. Test máy trạng thái; test đồng ý xong không còn lời
    gọi model nào; test không ghi vào conversation nguồn.

**GĐ 6 đã làm, 2026-10-04.** Khác Q11 ở ba chỗ, đều do ADR-0015 có hai lần xác nhận:

- *Trạng thái.* Chỉ lưu trạng thái **chờ người dùng**: `awaiting_intent` ⇄ nói lại,
  `awaiting_confirmation` ⇄ đổi, `compiled`, `abandoned`. Không có `proposing` /
  `revising`: lúc model đang chạy thì phiên chưa đổi. Không có `failed`: bảng giả định còn
  lỗi vẫn là một bảng người dùng đọc được và đòi đổi được; chỉ là chưa đồng ý được.
- *Hai lần ghi cùng lúc.* Cột `revision` là khoá lạc quan — một lời gọi model mất hàng
  chục giây, không giữ transaction mở chừng đó để khoá hàng. Ai ghi trước thì thắng.
- *Người dùng nói lại.* Ở bước ý đồ, lời đó tới model hiểu ý đồ cùng cả đoạn chat của
  phiên. Ở bước giả định, nó tới model trả lời cùng câu trả lời đang hiện, và ý đồ đã
  chấp nhận không đổi; muốn đổi nhiệm vụ thì `reopen_intent`, bảng giả định bị bỏ.

Ba quyết định của người dùng: lời ở bước giả định đi tới model trả lời; phiên **ghim**
các trace lúc mở (`read_source(only=…)`), lượt chat thêm sau đó không vào nguyên liệu, và
vân tay nguyên liệu đổi thì phải mở phiên mới; mỗi người **một** phiên đang mở trên một
conversation — service cho phiên cũ nhường chỗ, index duy nhất một phần giữ điều đó ở
database.

`confirm` không gọi model: ghi phiên trước (lần bấm thứ hai dừng ở đó), rồi tạo workflow
và draft mang IR đã kiểm, `intent` và `compiled_from`. `publish` chép hai cột đó sang
version; sửa draft bằng tay không làm mất chúng.

`TraceCompileSourceReader` ở `entrypoints/compiler_wiring.py` đọc conversation, tin nhắn
và trace thật; script xuất fixture giờ dùng chính nó. Chưa có endpoint nào gọi
`CompileService`: việc mở transaction quanh từng thao tác là của GĐ 7.

**GĐ 7 — HTTP**

17. `feat(api): compile a conversation over SSE` — bốn endpoint trên `:8001`:

    | | |
    |---|---|
    | `POST /v1/conversations/{id}/compile-sessions` | mở phiên, stream đề xuất đầu |
    | `POST /v1/compile-sessions/{id}/messages` | người dùng đổi bằng lời, stream đề xuất mới |
    | `POST /v1/compile-sessions/{id}/confirm` | đồng ý → draft; trả `workflow_id` |
    | `GET /v1/compile-sessions/{id}` | đọc lại phiên |

    Chỉ chủ conversation mở được phiên. Rate limit theo người dùng. Contract 9.

**GĐ 7 đã làm, 2026-10-05.** Bảy endpoint thay vì bốn, vì có hai lần xác nhận:

| | | |
|---|---|---|
| `POST /v1/conversations/{id}/compile-sessions` | SSE | mở phiên, đề xuất ý đồ |
| `POST /v1/compile-sessions/{id}/messages` | SSE | người dùng nói lại |
| `POST /v1/compile-sessions/{id}/accept-intent` | SSE | chấp nhận ý đồ, dựng bảng giả định |
| `POST /v1/compile-sessions/{id}/reopen-intent` | JSON | quay lại bước ý đồ |
| `POST /v1/compile-sessions/{id}/confirm` | JSON | đồng ý → draft |
| `POST /v1/compile-sessions/{id}/abandon` | JSON | bỏ phiên |
| `GET /v1/compile-sessions/{id}` | JSON | đọc lại phiên |

- *SSE* có ba loại sự kiện: `status` (một dòng tiến độ), `session` (phiên sau thao tác),
  `error`. Model Gateway chưa stream từng chữ, và ở đây cũng không có gì đáng stream: model
  nộp JSON qua một tool, và người dùng chỉ thấy nó sau khi code đã kiểm. Lỗi giữa chừng thì
  phiên không đổi, vì thao tác chỉ ghi sau khi model trả lời xong.
- *Transaction.* Ba thao tác có gọi model dùng repository mở transaction riêng cho từng
  lần ghi. Bốn thao tác còn lại chạy trong transaction của request; với `confirm`, phiên,
  workflow và draft cùng thành hoặc cùng huỷ.
- *Hạn mức.* 10 thao tác có gọi model mỗi người trong 5 phút, đếm chung ba endpoint SSE
  (`compiler_rate_limit`, `compiler_rate_window_seconds`). Thao tác không gọi model không
  tính.
- *Contract 9.* `app.entrypoints.agent` không nạp `WorkflowService`, phần Temporal của
  domain workflow, hay domain `execution`. Compiler ghi draft qua cổng hẹp `DraftWriter`.
  Có thêm một test nạp thật module để canh cùng điều đó.
- Node `llm` của workflow lắp ra dùng `compiler_llm_node_model` (mặc định `gpt-6.1-sol`).

Chạy thật qua HTTP với `gpt-6.1-sol` trên một conversation có sẵn trong dev DB (4 lượt,
5 lời gọi tool — đúng conversation đã thành fixture kb7): mở phiên → chấp nhận ý đồ → đồng
ý, mỗi bước một lời gọi model, không vòng sửa nào, ra một draft bốn node mang ý đồ và id
phiên.

**GĐ 8 đã làm, 2026-10-05** — trừ màn Run, để sau.

- *Nút* **Turn into Workflow** trên đầu màn chat, hiện khi conversation đã có lời gọi tool
  và không lượt nào đang chạy.
- *Cửa sổ biên dịch* (`compile-dialog.tsx`, hook `lib/compile.ts`): ba bước Intent →
  Assumptions → Draft. Ý đồ và bảng giả định **đang hiện** là thẻ dựng từ dữ liệu có cấu
  trúc; các bản cũ trong đoạn chat là markdown thu gọn. Dòng tiến độ là sự kiện `status`
  của backend, nguyên văn.
- *Mở lại thì tiếp tục*: `GET /v1/conversations/{id}/compile-sessions/current` trả phiên
  đang mở của người gọi, hoặc `null`. Cửa sổ hỏi nó trước khi mở phiên mới.
- *Hai trang* `/workflows` (danh sách) và `/workflows/[id]` (draft, chỉ đọc): ý đồ, các
  bước theo thứ tự chạy, kết quả kiểm, và các bước đã bỏ đọc từ phiên đã sinh ra draft.
  Chưa có nút Publish hay Run.
- Phiên trả thêm `ir` (để vẽ các bước) và mỗi giả định có `by_position`.
- Chữ giao diện tiếng Anh như phần còn lại của web; ý đồ, lý do và văn bản model viết giữ
  ngôn ngữ của conversation.

Khác bản thiết kế trên canvas ở một chỗ: mối nối giữa các làn của vòng lặp (khoá suy ra,
`thread_ts` của cùng vòng) hiện trong mục *Loops and conditions* bằng chính câu backend
viết, chưa thành từng dòng có nhãn `INFERRED` trong mục giá trị — bảng giả định chưa có
dữ liệu có cấu trúc cho phần đó.

Kiểm: `tsc`, eslint và `next build` sạch. Luồng request của giao diện đã đi thật qua proxy
của Next với `gpt-6.1-sol` (mở → `current` → chấp nhận ý đồ → đồng ý → danh sách → draft →
đọc lại phiên). **Chưa ai nhìn giao diện trên trình duyệt**: web chưa có bộ test, và lần
này không nối được trình duyệt.

**Chọn trang Notion theo tên, 2026-10-05.** `notion.search` lên 1.2 và trả thêm
`title_text`: các đoạn của `title` ghép thành một chuỗi (selector có thêm `join` trong tài
liệu tool). Bộ dò vì vậy thấy `title_text` = tên người dùng đã nói như một khoá `where`, và
binding thành *"phần tử của `results` có `title_text` = đầu vào"* thay vì `results.0.id`.
Không thêm phép so nào vào IR, đúng hướng WP-B đã ghi.

Chỉ có tác dụng với conversation chạy **sau** thay đổi này: trace cũ không có `title_text`,
nên biên dịch lại chúng vẫn ra chọn theo vị trí. Khớp là khớp nguyên văn: người dùng nói
"VPC" mà trang tên "VPC notes" thì không có khoá, và lại về vị trí kèm cảnh báo.

**GĐ 8 — Web**

18. `feat(web): turn a conversation into a workflow` — nút, cửa sổ chat bật lên, thẻ giả định, nút
    đồng ý.
19. `feat(web): read a draft` — trang xem draft, chỉ đọc, hiện cả bước đã bỏ.

**GĐ 9 — Nghiệm thu**

20. Chạy tám kịch bản với model thật, từ nút bấm tới draft. Publish và chạy kb1, kb2
    trên Temporal (cần GĐ 5 của WP-B). Ghi lại: số vòng tự sửa, số lượt người dùng
    phải sửa, số lựa chọn bị đổi — đúng những số mà *Điều kiện xem xét lại* của hai ADR
    hỏi tới.
21. `docs:` README, và ADR nếu Q1 hoặc Q8 đổi so với lúc lập kế hoạch.

---

## 6. Rủi ro và câu hỏi mở

- **Trace quá lớn cho một prompt.** Output của tool tới 1 MB. Model không cần giá trị,
  nó cần hình dạng và vài mẫu: gửi input nguyên, output cắt theo trần byte kèm đường
  dẫn. Đặt trần số bước cho một phiên; vượt thì từ chối bằng lời. Trần cụ thể đo ở GĐ 9.
- **`dieu_kien` là chỗ tập câu hỏi dễ thành ngôn ngữ nhất** — đúng tín hiệu cảnh báo
  của ADR-0008. Nếu kb5 đòi nhiều hơn một phép so đơn, dừng và viết ADR, không nới.
- **Bộ dò `where` đoán sai khoá.** Phần tử có hai trường cùng khớp tin nhắn người dùng
  thì có hai ứng viên; cả hai đi vào câu hỏi.
- **Fixture là trace của một model, một ngày.** Model khác gọi tool theo thứ tự khác.
  GĐ 9 chạy lại bằng ít nhất hai model trong catalog.
- **Agent Runtime ghi vào schema `workflow`.** Hai tiến trình ghi một schema; draft là
  hàng duy nhất chúng cùng chạm. `save_draft` hiện ghi đè không kiểm phiên bản — chấp
  nhận ở WP-D vì compiler luôn tạo workflow mới, không ghi đè draft đang có.
- **Số migration.** GĐ 5–7 của WP-B có thể lấy số trước; đánh số lúc viết, không lúc
  lập kế hoạch.
- **Người dùng gõ *"ok"* vào ô chat.** Đồng ý chỉ qua nút bấm, nên Agent trả lời *"bấm
  Đồng ý để tạo workflow"* và không đổi gì trong đề xuất.
- **Câu hỏi mở — bước `transform`.** Chờ WP-C đưa node vào IR. Khi có, đoạn code Agent
  đã chạy được bê nguyên sang, không qua LLM.

---

## 7. ADR

| # | Về gì | Khi nào |
|---|---|---|
| 0008 | Compiler hybrid, LLM không phát ra IR | đã viết |
| 0015 | Xác nhận ý đồ; ý đồ quyết định bước nào ở lại; ba tầng kiểm | đã viết, commit ở GĐ 0 |
| mới | Compiler chạy trong tiến trình Agent Runtime và ghi được draft (Q1) | commit 13, nếu sau GĐ 5 vẫn giữ Q1 |

Tập câu hỏi v1 không cần ADR: nó có version và đổi được bằng version.
