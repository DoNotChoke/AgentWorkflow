# WP-B — Workflow IR v1 và Generic Executor (Phase 4)

- **Trạng thái:** đề xuất
- **Ngày lập:** 2026-09-17
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §5.2, §11, §12, §13, §13.1, §14,
  §14.1, §15.1, §17, §18, §20, §25, §28, §39, §40, §41, §47; ADR-0002, ADR-0003,
  ADR-0004, ADR-0007, **ADR-0008**

Kế hoạch cho Phase 4. Đây là tài liệu **kế hoạch**: nó mô tả việc sẽ làm và thứ
tự làm. Những quyết định trong đây mà đắt để đảo ngược sẽ được nâng thành ADR ở
đúng commit đóng băng chúng (xem §7).

---

## 1. Hiện trạng chính xác

### Phase 0 đã đặt sẵn khá nhiều, và chưa ai chạm vào

| Thứ | Ở đâu | Trạng thái |
|---|---|---|
| Schema `workflow`, `execution` | migration `0001` | đã tạo, không một bảng nào |
| `WORKFLOW`, `WORKFLOW_VERSION`, `EXECUTION`, `NODE_EXECUTION`, `APPROVAL` | `core/ids.py` | đã khai, chưa ai gọi |
| `idempotency_key(execution_id, node_id, operation)` | `core/ids.py` | viết theo §15.1, chưa ai gọi |
| 4 task queue | `core/config.py` | có tên, không worker nào poll |
| `temporalio>=1.9`, `jsonschema>=4.23` | `requirements.txt` | đã cài, `jsonschema` chưa được import ở đâu |
| Temporal 1.27.2 + healthcheck | `docker-compose.yml` | chạy được, chỉ phục vụ `worker_heartbeat` |
| `TraceSource.WORKFLOW`, `TraceSubject.workflow_id/workflow_version_id/execution_id` | `trace/domain/entities.py` | đã chờ sẵn |
| `CallerRef.workflow_id/execution_id/node_id` | `integration/domain/invocation.py` | Gateway nhận được, Agent chưa bao giờ điền |
| `PrincipalKind = "workflow"` + `issue_access_token(principal_kind=…)` | `core/context.py`, `core/security.py` | ký được token workflow ngay hôm nay |
| `principal_kind != "user"` ⇒ đòi approval cho tool `DESTRUCTIVE` | `integration/domain/policy.py:164` | luật đã sống, chưa principal nào kích hoạt nó |
| `workflow`, `execution` trong contract 1 và 2 | `pyproject.toml` | ranh giới đã khai trước khi có code |

`start_agent_trace` còn ghi thẳng trong docstring chỗ sẽ thêm:

> Không có biến thể tương tự cho `source=WORKFLOW`: khi Phase 4 cần, thêm
> `start_workflow_trace` riêng theo cùng khuôn, không mở rộng chữ ký này.

### Chưa có

- `entrypoints/worker.py` — 85 dòng, một activity `worker_heartbeat` trả `"ok"`.
- `packages/schemas/workflow/` — trống. README của `packages/schemas` nói rõ IR v1
  chốt ở Phase 4, *"đó là lúc có đủ Execution Trace thật để biết cần biểu diễn
  những gì"*.
- Tám file rỗng ở `domains/workflow/` và `domains/execution/`.

### Một thứ hỏng, và nó chặn ngay bước đầu tiên

`require_user_id` (`core/http_auth.py`) từ chối mọi token không phải người thật:

```python
claims = decode_access_token(settings, extract_bearer_token(authorization))
if claims.principal_kind != "user":
    raise AuthenticationError("Token không hợp lệ.")
```

`/internal/v1/tools/{tool_id}/invoke` đứng sau nó, và `resolve_context` phân giải
tenant bằng `guard.require_workspace_context(user_id=…)` — một lần đọc membership.

**Một Temporal worker chạy workflow không có token người dùng, và một workflow
principal không có hàng membership.** Cả hai vế đều phải có đường đi trước khi
node `tool` đầu tiên gọi được provider. §20 đòi phân biệt user / service account /
Workflow principal / system principal; hiện chỉ có vế thứ nhất.

---

## 2. Ranh giới của WP-B

**Trong phạm vi.** IR v1 (schema + validate), domain `workflow` (definition,
draft, version, publish), domain `execution` (execution record, node execution),
Generic Executor trên Temporal, trigger `manual`, node `tool` / `condition` /
`llm` / `loop`, workflow principal, Execution Trace cho workflow, một endpoint đọc
tiến trình.

**Ngoài phạm vi, và vì sao.**

| Hạng mục | Vì sao không phải bây giờ |
|---|---|
| Workflow Compiler (trace → IR) | Phase 5. WP-B dựng đích đến; compiler là thứ bắn vào đích. |
| Workflow Editor | Phase 6. WP-B chỉ cần một endpoint tạo/publish để test được. |
| Schedule, webhook, API trigger (§17) | Phase 7. `manual` đủ để chứng minh executor chạy. |
| Node `human_approval` | Domain `approval` rỗng, §8 chưa được hiện thực. Node này kéo theo cả một luồng sản phẩm. |
| Node `parallel`, `wait`, `subworkflow` | Xem Q2. |

Node `llm` và `loop` **nằm trong phạm vi** — xem Q4 và Q13; bản kế hoạch đầu tiên
hoãn `llm`, và ADR-0008 cho thấy đó là một sai lầm sẽ chặn cả Phase 5. Node
`transform` thì ngược lại: nó **ra khỏi** phạm vi WP-B, sang
[WP-C](wp-c-transform-sandbox.md).

---

## 3. Mười sáu quyết định

### Q1 — Đọc trace thật trước, chốt IR sau

README của `packages/schemas` đã cảnh báo đúng chỗ: *"Chốt IR quá sớm, trước khi
có dữ liệu thật từ Agent, gần như chắc chắn dẫn tới một contract sai mà lại đã bị
đóng băng bởi quy tắc bất biến."*

ADR-0006 vừa làm cho lời khuyên đó thực hiện được: từ WP-A, mỗi bước trace giữ
nguyên input và output, không chỉ vân tay. Trước đó không có gì để đọc.

**Việc đầu tiên của WP-B không phải viết code.** Chạy sáu kịch bản thật qua Agent,
rồi đọc `GET /v1/traces/{id}` và payload của từng bước:

1. Slack một bước ghi — `post_message` vào một kênh cố định.
2. Slack đọc rồi ghi — `list_channels` → `post_message`, id kênh lấy từ bước trước.
3. Notion đọc sâu — `search` → `get_page` → `get_block_children`, ba tầng phụ thuộc.
4. Bắc cầu hai provider — `get_channel_history` (Slack) → `create_page` (Notion).
5. Một lượt có rẽ nhánh — model quyết định gọi hay không gọi tool tuỳ nội dung.
6. Một lượt hỏng giữa chừng — để thấy trace của đường lỗi trông thế nào.

Câu hỏi cần trả lời từ sáu cái đó, và chỉ khi có câu trả lời mới viết schema:

- Có bao nhiêu phần trăm input của một bước là **hằng số**, bao nhiêu là **output
  của bước trước**? (§10.2 bắt compiler phân biệt được ba thứ; IR phải biểu diễn
  được cả ba.)
- Cạnh dữ liệu giữa hai bước có luôn là *"lấy nguyên một trường"*, hay có chỗ cần
  *"lấy trường `id` của phần tử đầu tiên trong mảng"*? Câu trả lời quyết định độ
  giàu của binding ở Q8.
- **Bao nhiêu bước là ngôn ngữ thuần tuý?** Tức bước mà output của tool được model
  đọc rồi *diễn giải* — tóm tắt, phân loại, viết lại — chứ không có đường ánh xạ
  máy móc nào từ input sang output. ADR-0008 quyết định những bước này ở lại
  workflow dưới dạng node `llm`, nên con số này nói node `llm` quan trọng đến mức
  nào và cần biểu diễn được những gì (prompt, model, schema đầu ra).
- **Câu yêu cầu gốc của người dùng nói được bao nhiêu?** Với mỗi giá trị hằng
  trong trace, đối chiếu xem câu tự nhiên ban đầu có chứa tín hiệu nào để đoán nó
  là hằng số hay biến không. Đây là đầu vào bắt buộc của chặng 3 trong ADR-0008;
  nếu tín hiệu thường xuyên vắng mặt thì tập câu hỏi phải có đường "không đủ dữ
  kiện, hỏi người dùng".

**Sản phẩm của bước này là một file ghi chép**, `docs/plans/wp-b-quan-sat-trace.md`,
kèm số liệu thật. Không có nó thì IR v1 là phỏng đoán.

### Q2 — Node type v1: năm loại, không phải mười

§11.1 liệt kê mười họ node và nói hệ thống *nên* mở rộng được. Mở rộng được không
có nghĩa là làm hết ngay: §11 đòi IR *"suitable for static analysis"*, và mỗi loại
node là một nhánh trong validator, trong executor, trong editor.

v1 có năm:

| Node | Làm gì | Vì sao trong v1 |
|---|---|---|
| `trigger` | Điểm vào, khai `input_schema` của workflow | Mọi workflow phải có đúng một. v1 chỉ nhận `type: "manual"`. |
| `tool` | Gọi một tool qua Integration Gateway | Đây là lý do hệ thống tồn tại. |
| `condition` | Rẽ hai nhánh theo một phép so sánh | Kịch bản 5 ở Q1 sinh ra nó. |
| `llm` | Một lời gọi model có prompt và schema đầu ra cố định | Quyết định 6 của ADR-0008 — xem Q4. |
| `loop` | Chạy một thân gồm nhiều node trên từng phần tử của một mảng | GĐ 0, P3: gặp ngay ở kịch bản đọc thứ ba — xem Q13. |

**`transform` đã bị rút khỏi v1.** Bản đầu của kế hoạch có nó dưới dạng một map
binding, với lý lẽ *"không có nó thì mọi workflow là một chuỗi tool nối cứng"*.
Sáu ví dụ viết tay ở GĐ 2 bác bỏ câu đó: **không ví dụ nào cần tới nó**, vì binding
đã nắn dữ liệu ngay tại chỗ tiêu thụ. Nó quay lại ở
[WP-C](wp-c-transform-sandbox.md) dưới đúng một dạng — code chạy trong sandbox —
và khi đó nó làm được thứ mà map binding không làm được: tính toán.

Hoãn, kèm điều kiện mở khoá:

- `parallel` — khi có một workflow thật mà hai nhánh độc lập và đủ chậm để đáng
  song song. Temporal làm phần khó (`asyncio.gather` trên activity handle); phần
  đắt là validator phải hiểu đồ thị không còn là chuỗi.
- `wait` — rẻ trên Temporal (`workflow.sleep`), nhưng chưa ai cần.
- `human_approval`, `subworkflow` — xem §2.

**IR phải khai `node.type` là một chuỗi mở, không phải enum đóng ở tầng schema.**
Validator mới là chỗ từ chối loại chưa hỗ trợ, với thông báo nói rõ *"IR version
này chưa chạy được node loại X"* (§39, Runtime Compatibility Validation). Khác
biệt này quan trọng ở Phase 6: editor cần đọc được một IR có node nó chưa vẽ được
để hiển thị "không hỗ trợ" thay vì vỡ.

### Q3 — IR là JSON Schema; schema sinh ra Python, kiểu thì viết tay

Ba thứ đọc IR bằng ba cách khác nhau, nên có ba sản phẩm chứ không phải một:

| Ai | Cần gì | Lấy từ đâu |
|---|---|---|
| Validator (runtime) | Bản thân JSON Schema, để `jsonschema` chạy | `domain/generated/ir_v1.py` — sinh từ JSON, commit |
| Executor, service (Python) | Dataclass để đọc code cho ra hồn | **Viết tay** ở `workflow/domain/ir.py` |
| Editor (TypeScript, Phase 6) | Type | `json-schema-to-typescript`, sinh khi tới Phase 6 |

Vế sinh Python đi theo đúng khuôn `packages/schemas/tool/build_catalog.py`, và vì
đúng lý do đã ghi trong docstring của `infrastructure/specs.py`: đường dẫn tới
`packages/` khác độ sâu giữa dev và container, nên không đọc JSON lúc chạy.
`build_ir_schema.py --check` thoát khác 0 khi bản commit lệch khỏi JSON.

Vế dataclass **không** sinh bằng `datamodel-codegen`. Lý do giống hệt
`build_catalog.py`: *"Sinh ra dữ liệu, không phải kiểu."* Kiểu sinh máy cho ra
`Optional[List[Union[…]]]` lồng bốn tầng, và tầng domain là nơi đọc nhiều nhất.

**Validate hai lớp, không một.** `jsonschema` trả lời *"có đúng hình dạng không"*;
`workflow/domain/validation.py` trả lời bốn câu của §39 mà JSON Schema không với
tới: node nào tham chiếu node không tồn tại, đồ thị có chu trình không, binding
trỏ vào trường không có trong `output_schema` của node nguồn không, tool version
trong IR còn trong catalog không.

### Q4 — Node `llm` vào v1, và đường tới Model Gateway phải quyết trong WP-B

**Bản kế hoạch đầu tiên hoãn node này. Đó là sai.**

Lý lẽ khi hoãn là "chưa ai cần". ADR-0008 cho thấy khách hàng đầu tiên của nó
xuất hiện ngay ở Phase 5: khi compiler gặp một bước ngôn ngữ thuần tuý — *"tóm
tắt 50 tin nhắn thành gạch đầu dòng"* — nó chỉ có ba chỗ để đặt kết quả, và hai
chỗ đều hỏng:

| Đặt vào đâu | Kết quả |
|---|---|
| `transform` tất định | Không có quy tắc nào để viết. Phép biến đổi không tồn tại ở dạng máy móc. |
| Hằng số | Đông cứng 12 gạch đầu dòng **của hôm nay** vào workflow. Chạy lần sau ra y hệt lần đầu — một workflow vô dụng. |
| Node `llm` | Đúng. Bước đó vốn dĩ là một lời gọi model, và nó ở lại là một lời gọi model. |

IR v1 thiếu node `llm` nghĩa là compiler không biên dịch được cả một lớp lượt
Agent rất phổ biến — gần như mọi lượt có chữ "tóm tắt", "phân loại", "viết lại".
Đó không phải thiếu một tính năng, đó là thiếu đích đến cho Phase 5.

**Ràng buộc kiến trúc vẫn nguyên và giờ phải giải, không hoãn được.** Model
Gateway nằm ở `domains/agent/infrastructure/model_gateway.py`. Một node `llm`
chạy trong worker cần nó, nghĩa là `domains.execution` import `domains.agent` —
**contract 1 cấm, theo cả hai chiều.**

Ba đường ra:

1. **Worker gọi Agent Runtime qua HTTP.** Nhất quán với Q5 và với ADR-0003: cùng
   một hình dạng "tiến trình này không import tiến trình kia". Cần thêm cho Agent
   Runtime một endpoint "chạy một prompt rời" — hiện nó chỉ có hội thoại. Endpoint
   đó dù sao cũng cần cho chặng 3 của ADR-0008.
2. **Nâng Model Gateway lên `core`.** Nó là adapter chứ không phải luật nghiệp vụ.
   Nhưng nó import `agent.application.ports` (`ModelClient`, `ModelResponse`,
   `StopReason`), nên phải kéo theo hoặc tách đôi — và `core` là nơi mọi domain
   đều thấy, nên mở rộng `core` là quyết định không nên lấy vì tiện.
3. **Chép sang `execution`.** Rẻ nhất lúc viết, đắt nhất về sau: hai bản catalog
   model lệch nhau là chuyện của vài tháng.

**Nghiêng về (1)**, vì nó lặp lại đúng khuôn đã dùng hai lần và đã được contract
canh, và vì endpoint prompt rời có hai khách hàng chứ không phải một. Chốt ở
ADR-0011 tại commit dựng node `llm`.

Phạm vi node `llm` trong v1 giữ hẹp: một prompt có template theo binding của Q8,
một `model` khai tường minh, một `output_schema` để kết quả còn ánh xạ được sang
node sau. **Không** tool calling, **không** nhiều vòng — đó là Agent Runtime, và
trộn hai thứ lại sẽ xoá mất ranh giới của ràng buộc 1 trong §47.

### Q5 — Worker gọi Integration Gateway qua HTTP, và có một contract canh việc đó

Đây là lựa chọn quan trọng nhất của WP-B.

ADR-0002 chốt ranh giới credential nằm ở `entrypoints/wiring.py`. ADR-0003 dựng
`agent_wiring.py` riêng để Agent Runtime không có đường import tới đó, và contract
6 canh bằng máy. Câu cuối của README nói hệ quả: *"từ Phase 3, Agent buộc phải gọi
Gateway qua HTTP thay vì gọi trong tiến trình."*

Worker đứng trước đúng ngã ba ấy, và **chọn cùng một hướng**:

- worker **không** import `entrypoints/wiring.py`, `core/crypto`, hay
  `connection.infrastructure.credential_store`;
- node `tool` gọi `POST /internal/v1/tools/{tool_id}/invoke` của Control Plane;
- thêm **contract 7**, sao chép contract 6 với `source_modules = ["app.entrypoints.worker"]`.

Cái giá phải trả là thật và phải nói ra: mỗi lời gọi tool thành hai chặng mạng
thay vì một, và Control Plane trở thành đường đi của mọi side effect của mọi
workflow. Đổi lại, **đúng một tiến trình trong hệ thống giải mã được credential**,
và điều đó được một câu lệnh kiểm chứng chứ không phải một quy ước.

§14 cho phép cả hai (*"External side effects must occur in Activities or through
Activity-controlled Integration execution"*), nên đây là lựa chọn của dự án, không
phải yêu cầu của spec → **phải có ADR**.

### Q6 — Workflow principal: token riêng, tenant phân giải từ execution record

Hai việc, và cả hai đều nhỏ hơn vẻ ngoài vì `core/security.py` đã ký được token có
`pk`:

**a. Một dependency mới, không sửa `require_user_id`.** `require_principal` trả về
`Principal` thay vì `str`, chấp nhận `user` và `workflow`. `require_user_id` ở
nguyên — mọi endpoint người dùng vẫn từ chối token máy như cũ, và sự thay đổi chỉ
chạm đúng router Integration.

**b. Tenant không đến từ membership.** Với `pk="workflow"`, `subject` là
`execution_id`. Tenant, workspace và principal khởi xướng đọc từ hàng
`execution.executions` — vốn đã ghi đủ theo §13. Ba thứ vẫn không đến từ thân
request, đúng §19.

**TTL là câu hỏi thật.** Access token hiện sống vài phút; một execution có thể chờ
timer hàng giờ. Đề xuất: **token cấp cho từng activity, không cho cả execution** —
executor xin token ngay trước mỗi lời gọi tool, TTL đúng bằng
`tool.timeout.total_seconds` cộng lề. Việc xin token là một activity riêng, nên nó
không rơi vào workflow code (Q9), và một execution ngủ ba ngày không giữ bí mật
nào trong lịch sử Temporal.

**Điều không được làm:** cấp cho workflow principal một token có `pk="user"`. Nó
sẽ lách qua `require_user_id` mà không ai thấy, và xoá luôn luật ở
`policy.py:164` — luật đòi approval khi một tool `DESTRUCTIVE` bị gọi tự động.

### Q7 — Workflow version chỉ bind được connection phạm vi `workspace`

§13 nói execution record mang *"connection bindings"*. Câu hỏi: khi một workflow
chạy theo lịch lúc 3 giờ sáng, nó dùng connection của ai?

ADR-0004 đã trả lời câu tương tự cho Agent, và lập luận chuyển nguyên sang đây:
connection `PRIVATE` là *"tài khoản riêng mà chỉ chính chủ nhìn thấy và dùng
được"*, và `GET /v1/connections` lọc connection riêng của người khác **ngay trong
câu truy vấn**. Một workflow là tài nguyên chung của workspace, chạy cả khi người
tạo đã rời tổ chức.

**Quyết định: validation lúc publish từ chối một workflow version bind
`SharingPolicy.PRIVATE`.** Đây chính là ô *"connection availability"* của §39,
Policy Validation. Thông báo lỗi phải nói được việc cần làm: *"kết nối X là tài
khoản riêng; hãy dùng một kết nối chung của workspace, hoặc nhờ quản trị viên
workspace thiết lập một cái."*

ADR-0004 đặt điểm gán ở Conversation vì Agent là tương tác có người ngồi đó.
Workflow không có người ngồi đó, nên điểm gán là **version** — nó bất biến cùng
version (§2.3), và execution ghi lại cái nó đã dùng.

### Q8 — Binding là dữ liệu, không phải ngôn ngữ

§11.2 nói thẳng: *"The system should avoid arbitrary unrestricted user code as the
default expression mechanism"*, và liệt kê năm tính chất của một hệ biểu thức có
kiểm soát.

Dự án đã có tiền lệ, và nó hoạt động tốt qua hai provider —
`integration/domain/template.py`:

> Template là dữ liệu, không phải ngôn ngữ. Ba luật, hết.

IR v1 đi theo đúng tinh thần đó. Input của một node là một map, mỗi giá trị là
**một** trong năm dạng:

```json
{"const": "C123"}
{"from_input": "channel_id"}
{"from_node": {"node": "n2", "path": ["channels"], "where": {"name": {"const": "test"}}, "pick": ["id"]}}
{"from_item": ["id"]}
{"from_connection": "workspace_name"}
```

**`where` là phát hiện P2 của GĐ 0.** Kịch bản 2 cho thấy cạnh dữ liệu thật trỏ
vào `channels.4.id` — đúng giá trị, sai ý nghĩa; ý định là *"kênh có `name ==
test`"*. `where` chỉ so **bằng**, trên một hoặc nhiều trường, giá trị so là một
binding; trả về phần tử **đầu tiên** khớp, không khớp thì node hỏng với lỗi
`ValidationError` nói rõ trường và giá trị. Chỉ số thô (`["channels", "4", "id"]`)
vẫn hợp lệ nhưng validator gắn cảnh báo, và compiler không bao giờ tự sinh ra nó.

`from_item` chỉ hợp lệ bên trong thân `loop` (Q13).

Không nội suy chuỗi, không số học, không hàm, không điều kiện lồng. `condition`
so sánh đúng hai toán hạng cùng dạng trên với một toán tử trong tập đóng
(`eq`, `ne`, `lt`, `gt`, `contains`, `exists`).

`from_connection` là ô *"connection metadata"* của §11.2 và **chỉ đọc metadata
không bí mật** — tên workspace, id tài khoản. Không bao giờ token: ràng buộc 3 của
§47, và worker không có đường tới đó (Q5).

`path` là một mảng chuỗi chứ không phải `"items.0.id"` — cùng lý do `FieldMapping.path`
của domain integration là tuple: không phải nghĩ về việc thoát dấu chấm.

**Hàm `select_path` sẽ bị chép lần thứ ba.** `projection.py` đã chép nó từ
`connection/domain/providers.py`, với ghi chú: *"contract 1 cấm import qua đó, và
mười dòng này rẻ hơn việc phá một ranh giới kiến trúc."* Lập luận vẫn đúng lần thứ
ba. Nếu tới lần thứ tư, đó là lúc cân nhắc một `core/paths.py` — và cân nhắc đó
cần ADR vì nó mở một cửa `core` mới.

### Q13 — `loop`: tuần tự, một cấp, có trần, khoá idempotency theo lượt lặp

GĐ 0, P3: kịch bản 3 gọi `notion.get_page` cho `results.0` → `results.3`. Trải
phẳng thành bốn node là đúng hôm nay và sai khi trang có sáu con. Đưa vào v1.

Hình dạng:

```json
{ "id": "n3", "type": "loop",
  "over": {"from_node": {"node": "n2", "path": ["results"]}},
  "max_items": 50,
  "body": [
    { "id": "b1", "type": "tool", "tool_id": "notion.get_page", "tool_version": "1.0",
      "input": { "page_id": {"from_item": ["id"]} } }
  ],
  "collect": "b1" }
```

Năm luật, mỗi luật chặn một loại hỏng cụ thể:

1. **Tuần tự.** Không đồng thời trong v1 — đồng thời là việc của `parallel`, và
   trộn hai thứ làm rate limit sáu cấp của Gateway khó đoán. Lượt sau chạy khi
   lượt trước xong.
2. **Một cấp.** Thân `loop` không chứa `loop`. Cùng lập luận với
   `integration/domain/template.py` không lồng `for_each`: sâu hơn thì IR khó đọc
   hơn chính việc nó mô tả.
3. **Trần cứng.** `max_items` bắt buộc, mặc định 50, trần tuyệt đối 200 do
   validator giữ. Mảng dài hơn → node hỏng với lỗi rõ ràng, **không** cắt ngầm.
   Trần còn bảo vệ lịch sử Temporal: mỗi lượt là vài activity, và 200 × vài
   activity vẫn nằm xa giới hạn sự kiện của một workflow.
4. **Ranh giới thân.** Node trong thân đọc được node phía trên `loop` và node
   đứng trước nó trong cùng thân; node ngoài chỉ đọc được đầu ra của `loop`
   (mảng kết quả của node `collect`), không chạm được node trong thân.
5. **Khoá idempotency mang chỉ số lượt.** Đây là luật đắt nhất nếu sai.
   `core/ids.idempotency_key(execution_id, node_id, operation)` với `node_id =
   "b1"` sẽ cho **cùng một khoá ở mọi lượt** — lượt 2 của một `post_message` nhận
   lại kết quả phát lại của lượt 1 thay vì đăng tin, và ADR-0007 báo `REPLAY`
   hoàn toàn đúng luật. `node_id` bên trong thân phải là `"n3[2].b1"`. Định dạng
   đó đóng băng ngay khi hàng đầu tiên vào sổ → **ADR-0012**.

`loop` × `llm` là một cấp số nhân chi phí: 50 lượt × một lời gọi model. Validator
gắn cảnh báo khi thân có node `llm`; ngân sách thật là việc của Phase 7.

### Q14 — Lưu draft dễ tính, publish khắt khe; và bất biến canh bằng trigger

Người đang sửa dở có quyền lưu một IR chưa hợp lệ — nếu không thì editor của
Phase 6 không lưu nổi trạng thái giữa chừng. Nên hai cửa, hai mức:

| | Kiểm gì | Hỏng thì |
|---|---|---|
| `PATCH /draft` | JSON parse được, dưới trần 256 KiB | từ chối |
| | rồi chạy validator đầy đủ | **vẫn lưu**, trả kèm báo cáo lỗi và cảnh báo |
| `POST /publish` | `report.ok` | từ chối |

Một validator, hai mức khắt khe — đúng thứ `ValidationReport` của GĐ 2 được
thiết kế cho, và là lý do nó trả danh sách issue thay vì ném ở lỗi đầu tiên.

**Bất biến canh ở hai tầng.** Ràng buộc 2 của §47 nói version đã publish là bất
biến. Repository không có `update` hay `delete` nào cho version, **và** migration
0013 dựng một trigger Postgres chặn `UPDATE`/`DELETE` trên `workflow_versions`.
Năm dòng SQL biến một quy ước thành một bảo đảm — cùng lập luận với việc
`lint-imports` canh ranh giới credential thay vì một câu trong tài liệu.

**Publish luôn tạo version mới**, không khử trùng lặp. Nhưng lưu `ir_sha256` để
giao diện nói được *"giống hệt v3"*. Khử trùng lặp âm thầm làm người dùng tưởng
publish hỏng.

### Q15 — Đường từ domain `workflow` tới catalog tool và tới connection

Publish cần hai thứ nằm ngoài domain của nó, và cả hai đều vướng contract thật
chứ không vướng hình thức.

**Connection (Q7).** `workflow` không import `connection` được — contract 1. Nên
một port `ConnectionPolicy` ở `workflow/application/ports.py`, hiện thực ở
`entrypoints/wiring.py`, chỗ duy nhất biết cả hai domain. Đúng khuôn ADR-0002 đã
dựng cho credential và ADR-0003 cho trace.

**Catalog tool.** Khó hơn, vì vướng hai chiều:

- `workflow` không import `integration` — contract 1;
- `entrypoints/wiring.py` không import **trực tiếp** `app.domains.*.domain` —
  contract 5, mà `default_registry()` lại nằm ở `integration/domain/catalog.py`.

| Đường | Vì sao không | |
|---|---|---|
| Gọi HTTP `/internal/v1/tools` | Đúng khuôn Agent Runtime, nhưng Control Plane tự gọi chính nó | ✗ |
| Adapter trong `integration/infrastructure` trả thẳng `ToolContract` | Phải import `workflow` → phá contract 1 | ✗ |
| **`integration/infrastructure` trả dict thuần, `wiring` dựng `ToolContract`** | Không import chéo, không tự gọi HTTP | ✓ |

Dict thuần nghe như lùi một bước về kiểu, nhưng nó đúng bản chất: đây là một
ranh giới serialize hoá, chỉ khác là nằm trong bộ nhớ thay vì trên dây.

### Q16 — Bộ thông dịch IR nằm ở `workflow`; `execution` chỉ giữ bản ghi

Bản đầu của §4 đặt `plan.py` ở `execution/domain`. Không làm được: thông dịch IR
cần `WorkflowIR` và các kiểu binding của `workflow/domain/ir.py`, mà contract 1
cấm `execution` import `workflow`.

| Đường | Vì sao | |
|---|---|---|
| `execution` tự parse lại IR bằng kiểu riêng | Chép ~400 dòng parser; hai bản sẽ lệch nhau — đúng loại lỗi mà `RowToolCatalog` vừa xoá ở GĐ 3 | ✗ |
| Đưa IR vào `core` hoặc một shared kernel | Mở một cửa `core` mới chỉ để né một ranh giới; cần ADR | ✗ |
| **Thông dịch ở `workflow`, bản ghi ở `execution`** | Không chép, không mở cửa mới | ✓ |

Nó khớp §18: Execution sở hữu *"execution records, node execution records,
approvals, execution-facing status"* — bản ghi, không phải việc hiểu IR. Và nó
theo đúng khuôn đã có: Integration Gateway nằm ở `integration` và ghi trace qua
port; executor nằm ở `workflow` và ghi trạng thái node qua port.

Hình dạng cụ thể:

- `workflow/domain/plan.py`, `workflow/domain/bindings.py` — thuần, không
  Temporal (contract 3).
- `workflow/infrastructure/temporal/` — `GenericWorkflowExecutor` và activity.
- `workflow/application/ports.py` thêm `ExecutionRecorder`; hiện thực ở
  `entrypoints/worker_wiring.py` bằng `ExecutionService`.
- `execution` giữ entity, máy trạng thái, bảng, service, router, và client
  Temporal. Client khởi động workflow **bằng tên chuỗi**
  (`"GenericWorkflowExecutor"`), nên Control Plane không import `workflow` để
  làm việc đó.
- `execution/application/ports.py` có `WorkflowVersionSource` — đọc version đã
  publish dưới dạng dữ liệu thuần, hiện thực ở `wiring` bằng `WorkflowService`.

**Lỗi dữ liệu lúc chạy là `WorkflowExecutionError`**, không phải
`ValidationError` như Q8 viết: `where` không khớp, đường không tồn tại, `over`
không phải mảng, vượt `max_items`. Đó là lỗi của *lần chạy này*, không phải của
request hay của định nghĩa — và hạng lỗi đó đã có sẵn trong `ErrorCategory`,
không retry.

### Q9 — Mọi I/O nằm trong activity, kể cả việc nạp plan

Workflow code trên Temporal phải tất định: không mạng, không `datetime.now()`,
không `random`, không đọc database. Sai chỗ này thì lỗi chỉ hiện ra khi một
workflow được replay sau khi worker restart — tức là lúc khó debug nhất.

Ba hệ quả cụ thể cho executor:

1. **Nạp workflow version là một activity**, không phải một lời gọi trong thân
   workflow. §13.1 ghi *"loading the immutable Workflow version"* ở dòng đầu danh
   sách trách nhiệm; nó vẫn là activity.
2. **Mọi mốc thời gian đi qua `workflow.now()`**, và mọi khoảng chờ đi qua
   `workflow.sleep()`.
3. **Ghi `node_executions` là activity.** Executor không viết database trực tiếp.

**Trần kích thước plan.** Plan đi vào lịch sử Temporal (giới hạn payload mặc định
2 MiB, cảnh báo từ 256 KiB). IR v1 khai một trần cứng — đề xuất **256 KiB cho một
workflow version** — và validator từ chối lúc publish, không phải lúc chạy. Cùng
tinh thần trần 1 MiB của ADR-0006: chặn ở chỗ người dùng còn sửa được.

### Q10 — Ánh xạ ErrorCategory → hành vi retry của Temporal

`core/errors.py` đã có `RETRYABLE`, và ADR-0007 vừa thêm hai sắc thái mà Temporal
phải tôn trọng, nếu không toàn bộ WP vừa xong thành vô nghĩa:

| Hạng lỗi | Temporal | Vì sao |
|---|---|---|
| Trong `RETRYABLE` trừ `INVOCATION_IN_PROGRESS` | retry theo `RetryPolicy` của activity | Đúng nghĩa tạm thời |
| `INVOCATION_IN_PROGRESS` | retry, backoff tối thiểu = `retry_after_seconds` | Lời gọi khác đang chạy cùng thao tác; đợi rồi gọi y hệt là câu trả lời đúng |
| `OUTCOME_UNKNOWN` | **`non_retryable`** | Sổ đã ghi `unknown`; gọi lại là đúng thứ ADR-0007 sinh ra để chặn |
| Còn lại | `non_retryable` | Lỗi vĩnh viễn |

`ApplicationError` của Temporal mang `type=<ErrorCategory>` để `non_retryable_error_types`
khai được bằng đúng tên hạng lỗi của platform — không dựng bảng thứ hai.

**Khoá idempotency của node dùng `core/ids.idempotency_key(execution_id, node_id,
operation)`**, thứ đã nằm sẵn trong `core/ids.py` từ Phase 0 và chưa ai gọi. Nó ổn
định qua mọi lần Temporal retry activity — đó là điều kiện để sổ idempotency có
nghĩa. Nó **không** chứa số lần thử, và không được chứa.

### Q11 — `node_executions` là trạng thái vận hành; trace là bản ghi nội dung

Hai thứ dễ trở thành bản sao của nhau. Ranh giới:

| | `execution.node_executions` | `trace.trace_steps` |
|---|---|---|
| Trả lời | node đang ở đâu, thử mấy lần, chờ gì | đã gửi gì, nhận về gì |
| Ai đọc | UI tiến trình, executor lúc resume | người debug, compiler Phase 5 |
| Payload | **không** | có, theo ba tầng của ADR-0006 |

**Và trace step của node `tool` là thứ có sẵn, không phải viết mới.** Gateway đã
ghi một `TOOL_CALL` step kèm payload cho mọi lời gọi; nó chỉ chưa bao giờ nhận
được `CallerRef` có `execution_id`/`node_id` vì Agent không điền. Executor điền
vào là xong — cùng con đường mà commit `c61f075` đã dựng cho `parent_step_id`.

Nên phần trace của WP-B nhỏ hơn vẻ ngoài: `start_workflow_trace`, `complete`, và
điền `CallerRef`.

### Q12 — Queue: thêm một cho executor, activity đi theo side effect của tool

Bốn queue trong Settings là **capability queue** cho activity (§14.1). Workflow
task cần queue của riêng nó → thêm `temporal_task_queue_workflow: "workflow-executor"`.

Định tuyến activity:

- node `tool`, `side_effect.mutates == False` → `integration-read`
- node `tool`, `side_effect.mutates == True` → `integration-write`
- node `llm` → `ai-tasks`
- `condition`, ghi `node_executions`, nạp plan → `data-processing`
- (node `transform` của WP-C sẽ vào `data-processing`)

Bốn queue của Phase 0 đến đây dùng hết, không thừa cái nào — đó là một dấu hiệu
tốt rằng danh sách ở §14.1 được viết từ cùng một mô hình thực thi.

Một `Worker` chỉ poll một queue, nên `worker.py` dựng năm `Worker` trong một tiến
trình cho dev, và §43 tách chúng ra khi cần scale riêng. Cấu trúc code phải để việc
tách đó là đổi cấu hình, không phải đổi code.

---

## 4. Kế hoạch code

### Thêm mới

```
packages/schemas/workflow/
  ir.schema.json                     IR v1 — nguồn chân lý
  README.md                          quy tắc version cho IR
  build_ir_schema.py                 → domain/generated/ir_v1.py, có --check

services/api/app/domains/workflow/
  domain/ir.py                       dataclass của IR (viết tay)
  domain/entities.py                 Workflow, WorkflowDraft, WorkflowVersion, trạng thái
  domain/validation.py               bốn nhóm của §39
  domain/generated/ir_v1.py          JSON Schema dạng Python (sinh, commit)
  domain/plan.py                     IR → thứ tự chạy, NodePath, node chạy được tiếp (Q16)
  domain/bindings.py                 binding → giá trị thật; phần tử của loop (Q16)
  application/ports.py               repository + ToolCatalogReader + ConnectionReader
                                     + ExecutionRecorder
  application/service.py             CRUD draft, publish, đọc version
  infrastructure/models.py           workflows, workflow_drafts, workflow_versions
  infrastructure/repositories.py
  infrastructure/http.py             /v1/workflows…
  infrastructure/temporal/
    workflow.py                      GenericWorkflowExecutor — tất định, không I/O
    activities.py                    nạp plan, gọi tool, ghi state, xin token

services/api/app/domains/execution/
  domain/entities.py                 Execution, NodeExecution, hai máy trạng thái §13
  application/ports.py               repository + WorkflowVersionSource + ExecutionLauncher
  application/service.py             tạo execution, ghi node state, đọc tiến trình
  infrastructure/models.py           executions, node_executions
  infrastructure/repositories.py
  infrastructure/temporal/client.py  khởi động workflow bằng tên, từ Control Plane
  infrastructure/http.py             /v1/executions…

services/api/app/entrypoints/
  worker_wiring.py                   nối workflow↔execution↔trace (khuôn ADR-0003)

services/api/alembic/versions/
  0013_workflow_definitions_and_versions.py
  0014_execution_executions_and_node_executions.py
```

### Sửa

| File | Sửa gì |
|---|---|
| `core/http_auth.py` | thêm `require_principal`; `require_user_id` không đổi |
| `core/security.py` | `issue_workflow_token(execution_id, ttl)` |
| `core/config.py` | `temporal_task_queue_workflow` |
| `entrypoints/agent.py` | endpoint chạy một prompt rời — khách hàng của nó là node `llm` (Q4) và chặng 3 của ADR-0008 |
| `integration/infrastructure/http.py` | `resolve_context` nhận workflow principal, tenant từ execution record |
| `entrypoints/worker.py` | năm `Worker`, đăng ký workflow và activity; bỏ `worker_heartbeat` |
| `entrypoints/http.py` | gắn router `workflow` và `execution` |
| `trace/application/service.py` | `start_workflow_trace` |
| `pyproject.toml` | contract 7 — worker không có đường tới credential; dựng cùng commit tạo `worker_wiring.py` (GĐ 4), không đợi GĐ 5 |
| `README.md`, `packages/schemas/README.md` | trạng thái Phase 4 |

---

## 5. Thứ tự commit

GĐ 0 đã xong — [`wp-b-quan-sat-trace.md`](wp-b-quan-sat-trace.md). Phần dưới là
lộ trình sau GĐ 0, đã tính `loop` (Q13), `where` (Q8) và bảy phát hiện P1–P7.

Mỗi commit xanh độc lập — kiểm bằng cách stash phần còn lại rồi chạy full suite.

**GĐ 1 — Dọn trước khi xây** (độc lập với IR)

1. `docs: what six real agent traces say the IR must carry` — GĐ 0.
2. `feat(schemas): Notion search knows the titles of what it finds` — P7. ✅
   Thêm trường tuỳ chọn vào output là **minor bump** theo luật của
   `packages/schemas/tool/README.md`, nên sửa trong `search.v1.json`: tool và
   `output_schema` lên `1.1`. Bản trước của kế hoạch ghi `search.v2.json` — sai,
   `.v2` dành cho thay đổi phá vỡ. Trang lấy tiêu đề ở
   `properties.title.title[*]`, database ở `title[*]`, qua `any_of`.

**GĐ 2 — IR là contract** (không DB, không Temporal)

3. ✅ `feat(schemas): the Workflow IR, version 1` — sáu node, năm dạng binding
   (`where`, `from_item`), `schema_version`, trần 256 KiB, build script `--check`.
4. ✅ `feat(api): the Workflow IR as types the domain can read` — `domain/ir.py`.
   Viết tay IR cho **cả sáu kịch bản của GĐ 0** làm fixture; cái nào viết gượng là
   tín hiệu schema sai.
5. ✅ `feat(api): a workflow is valid before it is published, or it is not published`
   — validator thuần (đồ thị, chu trình, ranh giới thân `loop`, `from_item` chỉ
   trong thân, trần `max_items`, cảnh báo chỉ số thô) + phần cần catalog qua port
   `ToolCatalogReader` + `literals()` trả `(node, path)` kể cả trong thân `loop`.

**Viết tay sáu IR cho thấy gì** (commit 4 được đặt ra đúng để bắt những chỗ này):

- **`loop` cần lọc phần tử** → đã thêm `filter`. Kịch bản 3 lặp trên khối con của
  trang AWS, nhưng chỉ khối `child_page` mới gọi được `get_page`. Không có
  `filter` thì hoặc node hỏng ở phần tử đầu tiên là đoạn văn, hoặc phải có
  `condition` trong thân — thứ Q13 cấm.
- **`where` so bằng không tìm được trang theo tên.** `title` là mảng các đoạn chữ
  (`["Ghi chú ", "AWS"]`), còn input người dùng là một chuỗi. Kịch bản 3 vì vậy
  vẫn dùng `results.0.id` và nhận cảnh báo `chi_so_tho`, dựa vào việc `search`
  đã lọc theo `query`. Chưa sửa: cách đúng có thể là một trường `title_text` đã
  ghép sẵn ở phía tool, không phải thêm phép so vào IR. **Câu hỏi mở cho GĐ 5.**
- **`condition` không đếm được.** Kịch bản 5 hỏi "nhiều hơn 5 tin nhắn không";
  IR v1 không có độ dài mảng. Viết được bằng `exists` trên `messages.5` — đúng
  ngữ nghĩa, nhưng đọc khó hiểu và dính cảnh báo `chi_so_tho` oan. Chưa thêm
  toán tử: một lần gặp chưa đủ lý do mở rộng tập toán tử đóng.
- **Một giới hạn có chủ đích của validator**, ghi lại để khỏi tưởng là sót: node
  đọc một node tổ tiên nằm trên nhánh `condition` không được đi qua vẫn qua
  validate — lỗi đó lộ ra lúc chạy (GĐ 4).
- **Chưa có adapter thật cho `ToolCatalogReader`.** Test dùng adapter tự viết
  quanh registry thật. Adapter của ứng dụng đến ở GĐ 3 cùng với người gọi đầu
  tiên — và phải đặt ở chỗ contract 5 cho phép, vì `entrypoints` không được
  import thẳng `integration.domain`.

**GĐ 3 — Vòng đời workflow** (quyết định ở Q14 và Q15)

6. ✅ `feat(api): workflows, drafts, and versions that never change` — migration
   0013 (ba bảng + trigger bất biến), entity, repository. Test: version không có
   đường sửa nào, số version tăng đơn điệu, tenant khác không đọc được.
7. ✅ `feat(api): the tool catalogue, seen from the workflow side` — adapter dict
   thuần ở `integration/infrastructure`, `ConnectionPolicy` ở `wiring`, và
   `ToolCatalogReader` thật thay cho adapter tự viết trong test GĐ 2.
8. ✅ `feat(api): publishing a workflow freezes it` — service + router: tạo, liệt
   kê, đọc, `PUT /draft`, `POST /publish`, đọc version, lưu trữ. Quyền sửa và
   publish: người tạo hoặc `ws:admin`, cùng luật `ensure_can_manage` của Agent.
   Lưu trữ là soft archive, không xoá.

**Nghiệm thu GĐ 3:** publish được kịch bản 1 và 2 của GĐ 0 (Slack, connection
`workspace`), và publish kịch bản 3 **bị từ chối** vì connection Notion là
`private` — Q7 tự chứng minh bằng dữ liệu thật.

**GĐ 4 — Bộ xương biết đi** (chưa chạm provider; bộ thông dịch theo Q16)

9. ✅ `feat(api): the order a plan runs in` — thuần, không DB, không Temporal.
   - `workflow/domain/plan.py`: `build_plan(ir)` — thứ tự topo, hoà thì theo thứ
     tự khai báo, từ chối IR sai cấu trúc; `NodePath` (`n2`, `n3[2].b1`, lượt đếm
     từ 0 — định dạng này đóng băng ở ADR-0012); `runnable(settled)` — node chạy
     được tiếp khi mọi tiền bối đã xong hoặc bị bỏ qua, dùng cho resume và cho
     nhánh `condition` ở GĐ 6.
   - `workflow/domain/bindings.py`: `lookup` trả giá trị hoặc `Missing` (GĐ 6 cần
     cho `exists`), `resolve` ném `WorkflowExecutionError` nói rõ node, trường,
     đoạn đường hụt; đi được vào mảng bằng chỉ số (`results.0.id`); `where` lấy
     phần tử đầu tiên khớp, `pick` đi tiếp từ đó; `from_connection` đọc metadata
     có trong `Scope` (GĐ 5 mới điền); `loop_items` áp `filter` rồi kiểm
     `max_items` — thừa thì hỏng, không cắt ngầm.
   - Test trên cả sáu example: thứ tự, NodePath của kịch bản 3 qua ba lượt, mỗi
     dạng binding, `where` hụt, đường hụt, vượt `max_items`, `filter`.
   - **Thiết kế cho thân `loop` ngay từ đầu**: làm sau thì viết lại cả `plan.py`
     lẫn bảng `node_executions`.
10. `feat(api): execution records, and the state each node is in`
    - Migration 0014, schema `execution`: `executions` (tenant, workspace,
      workflow, version, trigger, inputs, người khởi xướng, trạng thái, lỗi, mốc
      thời gian; FK `workflow_version_id` → `workflow.workflow_versions.id`
      `ON DELETE RESTRICT` — ràng buộc mà docstring của 0013 đã hẹn) và
      `node_executions` (`UNIQUE(execution_id, node_path)`, trạng thái, số lần
      thử, lỗi; **không payload** — Q11).
    - `execution/domain/entities.py`: tám trạng thái của §13 cho execution;
      `pending`/`running`/`succeeded`/`failed`/`skipped` cho node — có `skipped`
      ngay để GĐ 6 không cần migration. Chuyển trạng thái **đơn điệu**: activity
      retry không kéo `succeeded` về `running`.
    - Service `create_pending`, `mark_running`, `record_node` (upsert), `finish`,
      `get_progress`; repository; test tenant, upsert, FK chặn xoá version.
11. `feat(api): a worker that runs a plan end to end`
    - `workflow/infrastructure/temporal/workflow.py` — `GenericWorkflowExecutor`,
      tuần tự, `loop` từng lượt, giữ output theo node trong bộ nhớ.
    - `activities.py`: `load_plan`, `record_node_state`, `record_execution_state`,
      và chữ ký `run_tool_node` (bản thật ở GĐ 5).
    - `ExecutionRecorder` ở `workflow/application/ports.py`, hiện thực ở
      `entrypoints/worker_wiring.py`; **contract 7** dựng ngay tại đây.
    - `temporal_task_queue_workflow`; `worker.py` dựng `Worker` từ bảng
      queue → (workflow, activity), bỏ `worker_heartbeat`.
    - Test trên Temporal của docker-compose (gated), `run_tool_node` giả: kịch
      bản 1 `succeeded` đủ hàng node; kịch bản 3 ra `moi_trang[0..2].doc_trang`;
      tool giả hỏng không retry được thì node và execution `failed`, node sau
      vẫn `pending`. Test replay trên lịch sử đã ghi, chạy offline.

    Việc rút `transform` khỏi v1 lấy mất node duy nhất tính toán mà không gọi ra
    ngoài, nên bộ xương không còn tự chạy được bằng node thật. Dùng activity giả là
    cách giữ nguyên giá trị của bước này: chứng minh Temporal, plan, trạng thái
    node và replay đều thông, trước khi có credential hay principal.
12. `feat(api): start a workflow by hand, and see where it is`
    - `execution/infrastructure/temporal/client.py` — start bằng tên, `id =
      execution_id` để start hai lần không thành hai lần chạy; start hỏng thì
      execution `failed`.
    - `WorkflowVersionSource` ở `wiring`; `POST /v1/executions` kiểm `inputs`
      theo `input_schema` của trigger (§13.1 *validating runtime inputs*) → 422
      hoặc 202; `GET /v1/executions/{id}`, `GET /v1/executions?workflow_id=`.
    - Trong GĐ 4, node `tool` chạy thật hỏng với thông báo "chưa hỗ trợ" cho tới
      GĐ 5 — chấp nhận có chủ đích.

**Nghiệm thu GĐ 4:** kịch bản 1 và 3 chạy hết trên Temporal với tool giả, replay
xanh, contract 1–8 giữ.

**GĐ 5 — Node `tool`**

13. `feat(api): a workflow principal that cannot pretend to be a person` — ADR-0010.
14. `feat(api): activities that reach the outside world` — ánh xạ lỗi Q10, queue
    Q12, khoá idempotency **có chỉ số lượt** (ADR-0012). Test: `loop` 3 lượt
    `post_message` ra **3** hàng sổ khác nhau, không phải 1 hàng + 2 replay. Test
    tương tác ADR-0007 × Temporal cancel.
15. `feat(api): a workflow that posts to Slack` — chạy lại kịch bản 1 và 2 của GĐ 0
    dưới dạng workflow, kịch bản 2 dùng `where`.

**GĐ 6 — `condition` và `llm`**

16. `feat(api): a plan that can take one branch or the other` — chạy lại kịch bản 5.
17. `feat(api): a prompt the workflow can run on its own` — ADR-0011; chạy lại
    kịch bản 4 với node `llm` thay cho 22 literal do model viết.

**GĐ 7 — Quan sát và đóng sổ**

18. `feat(api): a workflow run leaves an execution trace` — chạy lại kịch bản 3 dưới
    dạng `loop` và đối chiếu trace với trace Agent gốc.
19. `feat(api): watch an execution as it runs` — SSE §25.
20. `docs: ADR-0009 to ADR-0012` + README.

Mỗi GĐ từ 5 trở đi kết thúc bằng việc **chạy lại một kịch bản của GĐ 0 dưới dạng
workflow**. Sáu kịch bản vì vậy vừa là dữ liệu thiết kế IR, vừa là bộ nghiệm thu
của Phase 4.

Phát hiện P1, P5, P6 và việc ép kiểu khi so literal thuộc Phase 5 (compiler),
không có commit trong WP-B — ghi lại ở đây để không rơi mất.

## 6. Rủi ro và câu hỏi mở

- **IR v1 bị chốt trên dữ liệu mỏng.** Q1 giảm rủi ro, không xoá được nó. Sáu
  kịch bản không phải một phân bố. Giảm thiệt hại bằng `schema_version` tường minh
  ngay từ v1 và một đường nâng cấp đã nghĩ trước — không phải bằng cách cố đoán
  đúng ngay lần đầu.
- **Phase 5 là khách hàng thật của IR, mà nó chưa tồn tại.** Rủi ro: IR thiết kế
  cho *thứ chạy được* lại khó cho compiler *sinh ra*. ADR-0008 thu hẹp rủi ro này
  đáng kể — giờ đã biết compiler lắp IR từ một tập lựa chọn hữu hạn, nên biết
  trước IR phải có gì. Cách rẻ để kiểm phần còn lại: ở commit 3, viết tay IR cho
  cả sáu kịch bản của Q1. Cái nào viết tay đã gượng thì compiler sinh ra cũng
  gượng.
- **Địa chỉ hoá literal là ràng buộc mới, chưa được kiểm.** Quyết định 3 của
  ADR-0008 đòi mọi giá trị hằng trong IR chỉ được tới bằng `(node, path)`. Với
  input phẳng thì hiển nhiên; với `notion.create_page.blocks` — một mảng object
  lồng nhau — thì chưa chắc. Kiểm ngay ở commit 3, cùng lúc viết tay sáu IR.
- **Hai chặng mạng cho mỗi lời gọi tool** (Q5). Chưa đo. Với workflow chạy nền thì
  vài chục ms không đáng kể, nhưng con số phải được đo trước khi ai đó dùng nó làm
  lý do phá ranh giới.
- **Temporal replay là một lớp lỗi mới với dự án này.** Một `datetime.now()` lọt
  vào workflow code sẽ chạy đúng cho tới lần worker restart đầu tiên. Cần
  `workflow.Runtime` với sandbox bật, và một test replay dùng lịch sử đã ghi.
- **Bảng `executions` trở thành đường phân giải tenant** (Q6b). Nó vào đường nóng
  của mọi lời gọi tool từ workflow. Cần index trên `(id)` là đủ, nhưng phải đo khi
  có execution thật.
- **`abandon_stale` của ADR-0007 gặp Temporal.** Activity bị Temporal huỷ giữa
  chừng để lại hàng `in_flight` hết lease; lần thử sau của chính activity đó sẽ đọc
  nó thành `unknown` và dừng — đúng theo luật, nhưng với một tool ghi thì nghĩa là
  một node chết vì chính cơ chế retry của Temporal. **Đây là tương tác cần một test
  riêng trước khi tin**, và có thể là tín hiệu đầu tiên cho điều kiện xem xét lại
  của ADR-0007.
- **Output của tool nằm trong lịch sử Temporal.** Kết quả activity là một phần
  lịch sử; một trang Notion lớn nhân 200 lượt có thể chạm trần sự kiện của một
  workflow. Đo ở commit 11. Nếu một kết quả vượt 256 KiB thì activity đẩy nó sang
  payload store (ADR-0006) và chỉ trả tham chiếu — chưa làm cho tới khi số đo nói
  cần.
- **Không có đường xử lý execution kẹt.** Không huỷ, không resume, không retry tay.
  Chấp nhận trong WP-B, nhưng nó là việc phải làm trước khi có người dùng thật.

---

## 7. ADR sẽ phải viết

| # | Về gì | Đóng băng ở commit |
|---|---|---|
| **0008** | **Compiler là hybrid; LLM không bao giờ phát ra IR** | **đã viết, 2026-09-17** |
| 0009 | Worker gọi Gateway qua HTTP, và contract 7 canh đường tới credential (Q5) | 14 |
| 0010 | Workflow principal: token theo activity, tenant từ execution record, và vì sao không bao giờ mượn `pk="user"` (Q6) | 13 |
| 0011 | Node `llm` với tới Model Gateway bằng đường nào (Q4) | 17 |
| 0012 | `loop`: định danh node theo lượt lặp, và khoá idempotency mang chỉ số lượt (Q13) | 14 |
| 0013 | `transform` chạy code trong sandbox, và sandbox không bao giờ chạy trong Control Plane | [WP-C](wp-c-transform-sandbox.md) |

ADR-0008 được viết **trước** WP-B chứ không phải trong nó, vì nó đặt hai ràng buộc
ngược lên IR v1 — node `llm` phải có (quyết định 6), và mọi literal phải địa chỉ
hoá được (quyết định 3). Cả hai đều đắt để thêm sau khi IR v1 đã đóng băng.

Hình dạng IR v1 tự nó **không** cần ADR: nó là một contract có version, sống trong
`packages/schemas/` với quy tắc nâng version riêng, và §41 đã nói cách tiến hoá nó.
Thứ cần ADR là những quyết định không có đường lùi bằng version — hai cái trên.
