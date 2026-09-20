# WP-C — `transform`: code do Agent viết, chạy trong sandbox

- **Trạng thái:** đề xuất
- **Ngày lập:** 2026-09-18
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §2.4, §11.2, §14, §19, §38, §39, §40,
  §41, §47 (ràng buộc 3, 4); ADR-0002, ADR-0003, ADR-0008;
  [`wp-b-workflow-ir-va-generic-executor.md`](wp-b-workflow-ir-va-generic-executor.md)

Một node duy nhất chạy code, phục vụ cả tính toán cơ bản lẫn nắn dữ liệu giữa các
tool. Agent gọi nó như một tool trong lúc demo; compiler bê nguyên đoạn code đó
vào workflow.

---

## 1. Vì sao

**Động cơ gốc: LLM đếm sai.** Mọi phép đếm, cộng, so sánh, đổi hình dạng mà hiện
phải nhờ model làm đều là việc máy làm đúng hơn, rẻ hơn, và lặp lại được.

GĐ 0 đã đo được cái giá của việc để model làm: kịch bản 4 có **22 trên 26 literal
do model viết ra**, và model *tự ý viết hoa lại* một tin nhắn Slack khi chép sang
Notion. Với việc chép nguyên văn thì đó là hỏng.

**Vì sao là code chứ không phải thêm toán tử vào IR.** Hướng còn lại là thêm dần
`count`, `join`, `format`, `sum`, `filter`… — một danh sách không có điểm dừng,
mỗi lần thêm là sửa schema, validator, executor. Code phủ hết một lần.

**Vì sao code do LLM viết vẫn đáng tin.** ADR-0008 chốt *"LLM không bao giờ phát
ra IR"*. Đoạn code này **không phải IR**: nó không đặt `tool_id`, không dựng cạnh,
không quyết định cấu trúc. Nó là một giá trị **đã chạy thật và đã được quan sát**
trong trace — cùng hạng với `tool_id` và `tool_version`. Compiler chép lại bằng
chứng, không suy đoán.

**§11.2 cho phép, kèm điều kiện.** Nó nói tránh code tuỳ ý làm cơ chế biểu thức
*mặc định*, và đòi năm tính chất: chạy tất định, có giới hạn thực thi, truy cập an
toàn vào state, không chạm file system, không mạng. Năm tính chất đó là đặc tả của
sandbox ở §3.

---

## 2. Sandbox chạy ở đâu — và không chạy ở đâu

**Không bao giờ trong Control Plane.** ADR-0002 chốt Control Plane là tiến trình
duy nhất giải mã được credential provider. Đặt một sandbox chạy code do LLM viết
vào đúng tiến trình đó nghĩa là một lần thoát sandbox thành một lần lộ token của
mọi tenant. Đây là lý do `system.transform` **không** phải là một tool trong
catalog đi qua Integration Gateway, dù đường đó rẻ hơn về mặt code.

Nó chạy ở hai tiến trình vốn đã bị contract cấm với tới credential:

| Tiến trình | Khi nào | Contract canh |
|---|---|---|
| Agent Runtime | Agent gọi `system.transform` trong một lượt chat | 6 |
| Temporal worker | Executor chạy node `transform` | 7 (WP-B, ADR-0009) |

**Cùng một bản sandbox cho cả hai.** Hai bản lệch nhau là kết quả lệch nhau, đúng
thứ compiler hứa không xảy ra. Contract 1 cấm `domains.agent` và
`domains.execution` import nhau, nên nó nằm ở `app/core/sandbox/` — cùng hạng với
`core/crypto`, `core/redis`: hạ tầng xuyên suốt, không thuộc domain nào.

**Contract 8 mới:** `app.entrypoints.http` **không được** import `app.core.sandbox`.
Ranh giới ở §2 chỉ là một câu trong tài liệu cho tới khi `lint-imports` canh nó.

---

## 3. Đặc tả sandbox

Hai lớp, và cần cả hai: lớp thứ nhất chặn thứ code *viết ra được*, lớp thứ hai
chặn thứ code *làm được* khi đã chạy.

### 3.1 Duyệt AST trước khi chạy

Cấm — mỗi dòng là một đường thoát đã biết:

| Cấm | Vì sao |
|---|---|
| `import`, `__import__` | Cửa chính ra khỏi sandbox |
| Mọi tên bắt đầu bằng `_` (kể cả attribute) | `().__class__.__bases__[0].__subclasses__()` là đường thoát kinh điển |
| `eval`, `exec`, `compile`, `open`, `input` | Chạy code ngoài tầm duyệt, hoặc chạm file |
| `getattr`, `setattr`, `delattr`, `vars`, `globals`, `locals`, `dir` | Truy cập thuộc tính động, né được luật gạch dưới |
| `while`, `async`, `yield`, `class`, `global`, `nonlocal` | Vòng không chặn được, hoặc state ngoài hàm |
| `try`/`except` | v1 chưa cần; lỗi nên nổi lên thành lỗi node |

Cho phép: biểu thức, gán, `if`, `for` trên dữ liệu, comprehension, `def` cục bộ,
`lambda`, `return`, và một danh sách đóng builtin: `len` `sum` `min` `max` `sorted`
`reversed` `enumerate` `zip` `range` `abs` `round` `any` `all` `str` `int` `float`
`bool` `list` `dict` `set` `tuple` `isinstance`.

### 3.2 Chạy trong tiến trình con, có trần

Whitelist AST không chặn được `[0] * 10**9` hay một `for` lồng ba tầng trên dữ
liệu lớn. Nên code chạy trong **tiến trình con**, nói chuyện bằng JSON qua
stdin/stdout:

| Trần | Giá trị đề xuất | Ghi chú |
|---|---|---|
| Thời gian | 2 giây | Giết tiến trình khi quá |
| Bộ nhớ | 256 MiB | `resource.setrlimit` — **chỉ có trên Unix** |
| Kích thước output | 1 MiB | Cùng trần với payload lớn của ADR-0006 |
| Độ dài code | 4 KiB | Xem rủi ro R4 |

**Khác biệt giữa dev và production, phải nói rõ:** `resource` không có trên
Windows. Máy dev chạy Windows nên sẽ **không có trần bộ nhớ**, chỉ có trần thời
gian. Production chạy trong container Linux nên có đủ. Sandbox phải khai ra được
nó đang chạy ở chế độ nào, và test phải chạy được ở cả hai — không được âm thầm bỏ
qua một tầng bảo vệ.

### 3.3 Hợp đồng của một đoạn code

```python
# vào:  args  — dict, đã kiểm JSON-serializable
# ra:   return một giá trị JSON-serializable
return [{"type": "bulleted_list_item", "text": m["text"]} for m in args["messages"]]
```

Trong IR thì đoạn code ấy nằm trong một chuỗi JSON nên phải viết bằng nháy đơn —
xem §4.2.

Bốn luật, và luật 2 là luật cứu ADR-0008:

1. **Hàm thuần.** Vào từ `args`, ra bằng `return`. Không đồng hồ, không random,
   không mạng, không file — §11.2.
2. **Không viết cứng giá trị nghiệp vụ trong code.** Ngưỡng `5`, id kênh
   `C0BTG…` phải đi vào qua `args`. Nằm trong chuỗi code thì chặng 3 của compiler
   không hỏi về nó được, và QĐ 3 của ADR-0008 (mọi literal địa chỉ hoá được) vỡ.
   Validator cảnh báo khi thấy hằng chuỗi dài hoặc hằng số lạ trong code.
3. **Khai `output_schema`.** Runtime kiểm kết quả thật trước khi cho chảy tiếp.
4. **Trần độ dài.**

---

## 4. Ba mặt của cùng một thứ

### 4.1 Tool `system.transform` phía Agent

**Không phải tool trong catalog** (§2). Nó là tool dựng sẵn của Agent Runtime:
Agent Runtime trộn nó vào danh mục tool gửi cho model, tự chạy bằng sandbox, tự
ghi bước trace.

Một điểm phải làm đúng, từ phát hiện P1 của GĐ 0: bước `tool_call` mà
`AgentRuntime` ghi hiện **không mang payload** — payload do Gateway ghi ở bước con.
Tool này không đi qua Gateway, nên Agent Runtime phải tự ghi payload (`code` +
`args` vào, kết quả ra). Không có nó thì compiler nhìn vào trace chỉ thấy một bước
rỗng, và cả work package này vô nghĩa.

`side_effect: read`, không cần khoá idempotency — nó không chạm ra ngoài.

### 4.2 Node `transform` trong IR

```json
{ "id": "dung_blocks", "type": "transform",
  "code": "return [{'type': 'bulleted_list_item', 'text': m['text']} for m in args['messages']]",
  "args": { "messages": { "from_node": { "node": "doc", "path": ["messages"] } } },
  "output_schema": { "type": "array", "items": { "type": "object" } } }
```

`args` là binding thường, nên toàn bộ máy binding của WP-B (`from_node`, `where`,
`pick`, `from_item`) dùng lại nguyên vẹn.

**Thêm node type là thay đổi cộng thêm** → IR lên `1.1`, không phải `2.0`.

### 4.3 Hệ quả: `loop` nhẹ hẳn đi

Code xử lý được cả mảng một lần, nên lặp chỉ để đổi hình dạng không cần `loop`
nữa. `loop` chỉ còn cần khi **mỗi vòng phải gọi tool** — như kịch bản 3 gọi
`get_page` cho từng trang con. Code không gọi tool được (không có mạng), nên ranh
giới rõ: *lặp để gọi ra ngoài* dùng `loop`, *lặp để đổi hình dạng* dùng `transform`.

---

## 5. Mất kiểm tĩnh, bù bằng gì

Validator không nhìn được vào trong code, nên không biết `m['text']` có thật
không. Bù bằng ba thứ, theo thứ tự mạnh dần:

1. **Lúc publish**: code parse được, qua whitelist, `args` khớp binding,
   `output_schema` là JSON Schema hợp lệ.
2. **Lúc chạy**: kết quả thật được kiểm với `output_schema` trước khi chảy tiếp.
3. **Lúc publish, mạnh nhất**: trace đã lưu **input và output thật** của đúng lần
   chạy sinh ra đoạn code (ADR-0006). Chạy lại code với chính input đó, đối chiếu
   với output đã biết. Nó không chứng minh code đúng với mọi dữ liệu, nhưng chứng
   minh code đúng với dữ liệu đã từng chạy — mạnh hơn mọi phân tích tĩnh trên một
   chuỗi code.

Điểm 3 là thứ chỉ hệ này có, vì ADR-0006 đã quyết định giữ payload thay vì vân tay.

---

## 6. Thứ tự commit

**P0 — dọn IR v1** (làm ngay, trong GĐ 2, chưa commit gì nên không mất gì)

0. ✅ Bỏ `transform` dạng binding khỏi IR v1: schema, `ir.py`, hai chỗ trong
   validator, test. v1 còn **năm** node và tất cả đều chạy được thật. Kịch bản 5
   tạm giữ mẹo `exists` trên `messages.5` cho tới commit 3.

**Nhóm A — sandbox và Agent** (độc lập với workflow; chạy song song được với GĐ 3–4 của WP-B)

1. ✅ `feat(api): a sandbox that runs small pure functions` — `core/sandbox/`:
   duyệt AST + chạy tiến trình con có trần. Chưa ai gọi, xanh độc lập.
   Test: chương trình hợp lệ chạy đúng; **mỗi** mục cấm ở §3.1 có một test đỏ
   riêng; vòng lặp vô tận bị giết đúng hạn; output quá trần bị chặn; kết quả
   không JSON-serializable bị từ chối. Thêm contract 8 vào `pyproject.toml`.
2. ✅ `feat(api): the agent can compute instead of guessing` — tool dựng sẵn
   `system.transform` trong Agent Runtime, kèm bước trace **có payload** (§4.1).
   Sau commit này, chạy lại sáu kịch bản GĐ 0 sẽ thấy Agent dùng code thay vì
   tự nhẩm — và đó là dữ liệu đầu vào cho Phase 5.

**Nhóm B — workflow** (sau GĐ 5 của WP-B, khi node `tool` đã chạy thật)

3. `feat(schemas): transform, the only node that runs code` — IR lên `1.1`:
   node `transform`, luật validate (code qua whitelist, `args` là binding,
   `output_schema` hợp lệ, trần độ dài, cảnh báo hằng viết cứng). Viết lại kb5
   bằng `transform` + `condition`, thêm kb7: chép nguyên tin nhắn Slack sang
   Notion không dùng `llm`.
4. `feat(api): the executor runs a transform node` — activity gọi sandbox, queue
   `data-processing`, ghi `node_executions` và bước trace.
5. `feat(api): publishing a transform checks it against the run that created it`
   — kiểm điểm 3 của §5: cần một port đọc trace từ domain `workflow`, vì
   contract 1 cấm import thẳng.
6. `docs: ADR-0013` + README, và cập nhật §40 về version của sandbox.

---

## 6b. Ghi chép khi hiện thực commit 1

**Bố cục.** `guard.py` duyệt AST và **chỉ dùng thư viện chuẩn**; `_child.py` là
chương trình chạy trong tiến trình con, nạp `guard` bằng đường dẫn file chứ không
qua package. Nhờ vậy trình thông dịch con không có `app` trong `sys.path` — nó
không import được gì của ứng dụng kể cả khi thoát được sandbox. `__init__.py` giữ
API (`run`, `Limits`, `SandboxError`, `memory_cap_supported`).

Tiến trình con chạy bằng `python -I` (isolated): bỏ qua biến môi trường và
site-packages của người dùng.

**Hai lỗi encoding, và cái thứ hai nguy hiểm hơn nhiều.** `-I` bỏ qua
`PYTHONIOENCODING`, nên tiến trình con dùng cp1252 mặc định của Windows:

- **stdout**: mọi thông báo lỗi có dấu tiếng Việt làm tiến trình con chết trước
  khi kịp trả lời, nên *mọi* nguyên nhân — `import os`, `eval`, kết quả quá lớn —
  đều hiện ra thành cùng một câu "dừng giữa chừng". Ồn ào, dễ thấy.
- **stdin**: code và dữ liệu tiếng Việt bị giải mã sai **trước khi chạy**. Không
  lỗi nào, chỉ là kết quả sai. `f"Có {n} tin nhắn"` trả về `Có 2 tin nhắn`.
  Test bắt được vì có chuỗi tiếng Việt trong dữ liệu vào; nếu test chỉ dùng ASCII
  thì lỗi này sống sót tới production.

Cả ba luồng của tiến trình con vì vậy đều `reconfigure(encoding="utf-8")`. Đây là
lần thứ ba repo gặp cái bẫy này, sau `build_catalog.py` và `lint-imports`.

**Trần bộ nhớ trên Windows**: xác nhận không có. `memory_cap_supported()` trả
`False`, `Result.memory_capped` mang theo sự thật đó về từng lần chạy, và test
ngốn bộ nhớ được `skipif` chứ không bị xoá.

**Hai lớp đều cần, đã chứng minh bằng test**: `while True` bị guard chặn lúc
duyệt, còn `for i in range(10**12)` cú pháp hợp lệ nên chỉ trần thời gian chặn
được.

### Ghi chép khi hiện thực commit 2

**Hình dạng: một lớp bọc, không phải một nhánh `if` trong runtime.** `TransformTool`
hiện thực cả `ToolCatalog` lẫn `ToolExecutor` và bọc quanh `IntegrationClient`:
thêm một tool vào danh mục gửi cho model, giữ lại đúng lời gọi tool đó để tự chạy,
uỷ quyền mọi thứ còn lại xuống. `AgentRuntime` không biết tool nào là tool dựng
sẵn.

**Một khái niệm mới trong port, không phải hai.** `ModelToolSpec.local` nói tool
chạy ngay trong tiến trình Agent Runtime. Từ đó suy ra cả hai khác biệt, nên
runtime chỉ phải hỏi một câu:

- **không phân giải connection** — `provider` suy từ tiền tố `system.` sẽ không
  bao giờ có kết nối nào, và hỏi "dùng connection nào" cho một phép tính là vô
  nghĩa. `ToolExecutor.invoke` vì vậy nhận `connection_id: str | None`.
- **tự ghi payload vào trace** — không có bước Gateway lồng bên dưới để mang nó
  (GĐ 0, P1). Cả đường thành công lẫn đường hỏng đều ghi `input_payload`, vì đoạn
  code vừa chạy hỏng là thứ đáng giữ nhất.

**Whitelist không có ngoại lệ.** Agent phải bật `system.transform` trong
`enabled_tool_ids` như mọi tool khác; `agent.allows_tool` chạy nguyên si.

**Sandbox chặn event loop.** `run()` mở tiến trình con rồi chờ, nên adapter đẩy nó
qua `asyncio.to_thread` — cùng lý do `ObjectPayloadStore` làm thế với boto3.

**Kết quả bọc dưới một khoá.** `ToolInvocationResult.output` là một `Mapping`, mà
code trả được mảng hay số. Trả `{"result": …}` thay vì nới contract của cả
`ToolExecutor` cho một tool.

**Lỗi sandbox nổi lên thành kết quả tool báo lỗi**, không làm vỡ lượt — model đọc
được thông báo và tự sửa code. `SandboxError` là `AppError` nên đường này có sẵn.

### Chạy thật, 2026-09-20

Agent `agt_a39667e4…` (`claude-sonnet-5`, bật `slack.get_channel_history` +
`system.transform`), kênh Slack thật, chỉ đọc — không tạo side effect nào.

**Đếm.** *"Đếm chính xác kênh C0BTGNJ63TK có bao nhiêu tin nhắn, mỗi người bao
nhiêu"*. Model gọi `get_channel_history` rồi `system.transform`. Kết quả
`{total: 52, by_user: {…25, 14, 13}}` — **khớp từng con số** với dữ liệu thật.
Con số không còn do model nhẩm.

**Chép nguyên văn.** Đúng ca đã hỏng ở GĐ 0 (P4). Model tự viết:

```python
return [{"type": "bulleted_list_item", "text": m["text"]} for m in args["messages"]]
```

Cả 8 chuỗi **khớp từng byte** với bản gốc Slack, kể cả `[WP-B] kịch bản 2` với
chữ *k* thường — đúng chỗ mà ở GĐ 0 model đã âm thầm viết hoa thành `Kịch bản 2`
khi chép sang Notion. Đây là bằng chứng trực tiếp cho §1.

**Vòng tự sửa có thật.** Lần chạy đầu model viết `from collections import
Counter`; guard chặn `ImportFrom`, lỗi nổi lên thành kết quả tool, model viết lại
bằng `dict` thường và chạy đúng. Không vỡ lượt, không cần người can thiệp.

**Nhưng nó tốn một vòng gọi model** — và mô tả tool *đã* ghi "không import". Lời
dặn trong prompt không đủ; thứ thực sự chặn là sandbox. Sửa mô tả để **liệt kê
sẵn danh sách builtin** lấy thẳng từ `SAFE_BUILTINS`: lần chạy sau không còn lần
hỏng nào, trace rút từ 9 bước xuống 7. Kèm một test canh mô tả không trôi khỏi
whitelist thật — một danh sách sai ở đây dạy model sai theo cách khó thấy hơn.

**P1 đã được sửa, quan sát được trong trace thật**: bước `system.transform` là
bước gốc, không có bước Gateway bên dưới, và tự mang cả `input` lẫn `output`
payload — cả ở đường hỏng.

---

## 7. Rủi ro

- **R1 — thoát sandbox.** Hậu quả đã bị §2 giới hạn: hai tiến trình không có
  đường tới credential. Nhưng vẫn là dữ liệu của tenant khác trong cùng tiến
  trình. Giảm thiểu: tiến trình con riêng cho mỗi lần chạy, hai lớp phòng thủ,
  và một bộ test tấn công riêng — không phải test "code chạy đúng", mà test
  "từng đường thoát đã biết đều bị chặn".
- **R2 — Windows không có trần bộ nhớ.** Máy dev thiếu một tầng bảo vệ mà
  production có. Nguy hiểm ở chỗ nó trông y hệt như đang được bảo vệ. Sandbox
  phải khai chế độ, và test phải đọc được chế độ đó.
- **R3 — version sandbox thành mặt tương thích.** Workflow đã publish là bất
  biến và có thể chạy nhiều tháng sau (§41). Nâng cấp sandbox mà đổi ngữ nghĩa
  thì workflow cũ đổi hành vi âm thầm. §40 phải kể thêm "Sandbox version".
- **R4 — `transform` thành cái sọt.** Đây là rủi ro tôi lo nhất, và nó không
  phải bảo mật. Nếu code là cửa thoát vạn năng thì model sẽ nhét ngày càng nhiều
  logic vào đó, workflow thành "vài lời gọi tool cộng một đống code", và §39 —
  phân tích tĩnh, editor, kiểm toán — mất chỗ bám. Ba hàng rào: trần 4 KiB,
  code không gọi được tool hay I/O (nên nó chỉ là dữ liệu vào ra), và một cảnh
  báo khi code dài bất thường.
- **R5 — hai bản sandbox lệch nhau.** Đã chặn bằng cách để nó ở `core` và dùng
  chung. Cần một test chạy cùng một đoạn code qua cả hai đường gọi và so kết quả.
- **R6 — chi phí tiến trình con.** Mỗi lần chạy tốn vài chục ms khởi động. Không
  đáng kể với workflow chạy nền; đáng đo nếu một `loop` 50 vòng gọi `transform`
  mỗi vòng. Chưa tối ưu; nếu cần thì dùng pool tiến trình.

---

## 8. ADR sẽ viết

**ADR-0013 — `transform` chạy code trong sandbox, và sandbox không bao giờ chạy
trong Control Plane.** Bốn thứ đắt để đảo ngược:

1. Code do LLM viết là một giá trị được quan sát, không phải IR — vì sao điều đó
   không mâu thuẫn với ADR-0008.
2. Sandbox không chạy trong tiến trình giữ credential, và contract 8 canh việc đó.
3. Hợp đồng của một đoạn code: hàm thuần, không hằng nghiệp vụ viết cứng, khai
   `output_schema` — luật 2 là điều kiện để compiler còn hỏi được về literal.
4. Kiểm lúc publish bằng chính input/output đã ghi trong trace.
