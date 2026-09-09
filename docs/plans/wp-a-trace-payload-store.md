# WP-A — Payload store cho Execution Trace

- **Trạng thái:** đang thực hiện (commit 1/6)
- **Ngày lập:** 2026-09-09
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §4.7, §9, §9.1, §10.1, §10.2, §18, §19;
  ADR-0002, ADR-0003

Kế hoạch cho hạng mục chặn Phase 5. Đây là tài liệu **kế hoạch**, không phải ADR:
nó mô tả việc sẽ làm và thứ tự làm. Những quyết định trong đây mà đắt để đảo
ngược sẽ được nâng thành ADR-0006 ở commit cuối (xem §5).

---

## 1. Vấn đề

### Hiện trạng chính xác

Khi Integration Gateway chạy xong một tool (`gateway.py`, `_finish`), nó dựng
`ToolInvocationRecord` với:

```python
input_ref=PayloadRef.of(command.input),     # sha256 + byte_size
output_ref=PayloadRef.of(output),           # sha256 + byte_size
```

`PayloadRef.of()` băm payload rồi **vứt payload đi**. Bản ghi đi tiếp qua
`ToolInvocationSink` → `IntegrationTraceSink` (`entrypoints/wiring.py`) →
`TraceService.record_tool_call(input_sha256=..., input_byte_size=...)` → hai cột
`trace_steps.input_ref_sha256` và `input_ref_byte_size`.

Kết quả: với mỗi lời gọi tool, hệ thống biết *"đã có một input dài 412 byte, băm
ra `abc123…`"*, và **không nơi nào còn giữ 412 byte đó**.

Với bước `model_call` thì còn ít hơn — không có `PayloadRef` nào cả, chỉ
`attributes` gồm model, `stop_reason`, số token vào/ra.

`PayloadRef` đúng như docstring của nó tự nhận: *"Vân tay của một payload lớn —
không phải payload."* Vân tay chỉ có nghĩa khi có thứ để đối chiếu. Hiện không
có.

### Ba thứ đang hỏng vì thiếu nó

**Phase 5 không khởi động được.** §10.2 bắt compiler phân biệt trong một trace
đâu là *hằng số*, đâu là *workflow input*, đâu là *output của node trước*. Cả ba
phán đoán đều đọc **giá trị**: `channel: "C123"` xuất hiện ở hai bước là dấu
hiệu của một hằng số dùng lại; `page_id` của bước 2 bằng `id` trong output bước
1 là một cạnh dữ liệu giữa hai node (§11.2). Với sha256, phép so sánh cuối cùng
còn làm được (hai payload y hệt có cùng băm), còn *"trường nào bên trong khớp
trường nào"* thì không.

**Không debug được một lượt Agent hỏng.** Đúng lúc cần biết "model gửi gì cho
tool, tool trả về gì" thì chỉ có một chuỗi hex.

**§9.1 chưa được thoả.** Nó liệt kê *"input payload or reference"* và *"output
payload or reference"*, rồi chốt: *"Large inputs and outputs should be stored in
object storage with references persisted in PostgreSQL."* Hiện chỉ có nửa sau
của cả hai vế.

### Hạ tầng đã sẵn

`docker-compose.yml` có MinIO, dịch vụ `minio-init` đã tạo sẵn bucket
`aw-traces`, và `Settings.s3_bucket_traces` tồn tại từ Phase 0. **Chưa một dòng
code nào chạm tới chúng** — `grep` toàn repo chỉ ra `core/config.py`.

---

## 2. PayloadStore là gì

Một cửa hai hàm — cất một payload JSON, đọc lại nó theo vân tay — để cặp
`(PostgreSQL, object storage)` chia nhau đúng việc mà §4.7 phân công:
*"Transactional databases should store references and metadata rather than very
large payloads."*

```
trace_steps (PostgreSQL)          object storage (MinIO/S3)
├─ input_ref_sha256   ─────────▶  {tenant}/payloads/ab/abc123….json
├─ input_ref_byte_size
├─ input_location                 ← payload thật nằm đây
└─ input_inline (JSONB)           ← hoặc ở đây, nếu đủ nhỏ
```

---

## 3. Sáu quyết định

### Q1 — Domain `trace` sở hữu payload store, không phải `integration`

§18 xếp *"tool invocation trace metadata"* vào domain Trace. Contract 1 cấm
`integration` import `trace`, nhưng **không cần phá**: payload đi theo đúng con
đường bản ghi đang đi — qua Protocol `ToolInvocationSink`, sang
`entrypoints/wiring.py`, xuống `TraceService`. Chỉ là thêm hai trường vào một
dataclass vốn đã đi con đường đó.

Hệ quả: `integration` không biết object storage tồn tại, đúng như nó không biết
PostgreSQL tồn tại.

### Q2 — Khoá content-addressed, lấy chính `sha256` đã có

```
{tenant_id}/payloads/{sha256[:2]}/{sha256}.json
```

Ba cái lợi, không cái nào phải trả giá:

- **Không cần cột mới để lưu khoá** — `input_ref_sha256` đã là con trỏ.
- **Ghi trùng là no-op** — một replay idempotency ghi lại cùng payload thì đè
  lên chính nó.
- **Khử trùng lặp tự nhiên** — một `list_channels` gọi 40 lần trong một
  workspace là 1 object.

Tiền tố tenant khiến hai tenant có payload giống hệt vẫn thành hai object. Đó là
**cố ý**: §19 bắt tenant boundary có mặt ở *object storage pathing*, và dùng
chung một object giữa hai tenant là chính thứ nó cấm.

Cần **một** cột mới cho mỗi phía (`input_location` / `output_location`, nhận
`absent | inline | object`) để phân biệt "payload đọc được" với "chỉ còn vân
tay". Hàng ghi trước migration và payload vượt trần đều là `absent`.

### Q3 — Ngưỡng inline: nhỏ nằm trong Postgres, lớn mới ra object storage

Đề xuất **16 KiB**. Dưới ngưỡng vào cột JSONB ngay trong `trace_steps`; trên
ngưỡng mới đẩy S3.

Lý do không đẩy hết ra S3 cho gọn: một trace của lượt Agent có khoảng mười bước,
mỗi bước hai payload — màn hình xem trace và compiler đều phải bắn hai mươi GET
cho những object phần lớn dưới 1 KiB. Và §4.7 chỉ đòi *"very large payloads"* ra
ngoài, không đòi tất cả.

Cái giá: hai đường đọc thay vì một. Nó gói gọn trong một hàm của `PayloadStore`,
không rò ra ngoài.

### Q4 — Trần cứng 1 MiB

Trên trần thì **không lưu**, chỉ giữ vân tay và `location=absent`. Ghi 50 MB một
lần vì một provider trả về cả database là cách nhanh nhất để biến bucket trace
thành hoá đơn.

**Phát hiện kèm, ngoài phạm vi WP-A:** `HttpConnector` hiện **không giới hạn
kích thước phản hồi của provider** — không đọc `content-length`, không cắt body.
Một phản hồi khổng lồ đã đi thẳng vào RAM và vào output của tool từ Phase 2, độc
lập với payload store. Cần xử lý riêng.

### Q5 — Đọc qua API, không qua presigned URL; mã hoá ở tầng bucket

Payload chứa dữ liệu người dùng (nội dung trang Notion, tin nhắn Slack).
Presigned URL đưa quyền đọc ra ngoài vòng kiểm tenant, trong khi §19 bắt
tenant-aware authorization có mặt ở *"trace access"* — nên đường đọc phải qua
endpoint đã có `TenantGuard`.

Mã hoá at-rest là **SSE của bucket**, không phải envelope crypto của ứng dụng.
Đây không phải lựa chọn thẩm mỹ: contract 4 cấm `app.domains.trace` import
`app.core.crypto`. Mã hoá payload ở tầng ứng dụng đòi phá contract bảo mật
nghiêm nhất của repo — cái giá sai cho thứ mà cấu hình bucket giải quyết được.

### Q6 — Ghi payload hỏng thì lời gọi tool vẫn thành công

Cùng triết lý `append_step_by_id` đã chọn: *ghi trace là quan sát*. S3 timeout
không được biến một tin nhắn Slack **đã gửi** thành một lời gọi tool báo lỗi.
Log lại và hạ về `location=absent`.

---

## 4. Kế hoạch code

### Thêm mới

| File | Nội dung |
|---|---|
| `app/core/objectstore.py` | Client S3 dùng chung, `init_object_store()` / `close_object_store()` theo đúng khuôn `core/db.py` và `core/redis.py`. `boto3` đã có trong `requirements.txt` nhưng **là client đồng bộ** — bọc mỗi lời gọi trong `asyncio.to_thread` để không chặn event loop. Không thêm `aioboto3` trừ khi đo được là cần. |
| `trace/infrastructure/payload_store.py` | Hiện thực `PayloadStore` trên `core.objectstore`. Serialize **đúng cách `PayloadRef.of` đang serialize** (`sort_keys=True, ensure_ascii=False, separators=(",", ":")`) — lệch một chút thì byte ghi ra không băm lại thành vân tay đã lưu. |
| `alembic/versions/0011_trace_step_payloads.py` | Bốn cột trên `trace_steps`: `input_inline` / `output_inline` (JSONB, nullable), `input_location` / `output_location` (String(8), mặc định `absent`). |
| `tests/test_core_objectstore.py` | Store thật với MinIO, dùng `infra_unavailable()` như `test_agent_repositories.py`. |
| `tests/test_trace_payload_store.py` | Vòng đời cất–đọc, ngưỡng inline, trần cứng. |
| fixture `InMemoryPayloadStore` | Để mọi test khác không cần MinIO. |

### Sửa

**Domain `trace`**

- `domain/entities.py` — `TraceStep` mang payload đọc được. Đề xuất một
  dataclass `StoredPayload(ref, location, inline)` thay vì nhét ba trường rời ×
  hai phía. Tầng domain, nên không biết S3 là gì.
- `application/ports.py` — Protocol `PayloadStore`: `put(tenant_id, payload)` và
  `get(tenant_id, ref)`.
- `application/service.py` — `TraceService.__init__` nhận thêm
  `payloads: PayloadStore`. `record_tool_call` nhận **payload thật** thay cho
  bốn tham số `input_sha256` / `input_byte_size` / `output_sha256` /
  `output_byte_size`, và tự tính vân tay — hiện Integration băm còn Trace lưu,
  hai bên có thể lệch cách serialize mà không ai phát hiện. Thêm `get_payload()`
  cho đường đọc.
- `infrastructure/models.py`, `repositories.py` — bốn cột mới, map hai chiều.
- `infrastructure/http.py` — `GET /v1/traces/{trace_id}/steps/{step_id}/payload/{side}`.
  Object đã hết hạn phải phân biệt được với "step không tồn tại", để UI nói
  "payload đã hết hạn lưu trữ" thay vì "không tìm thấy".

**Domain `integration`**

- `domain/invocation.py` — `ToolInvocationRecord` mang thêm `input` và `output`.
  Giữ nguyên `input_ref` / `output_ref` vì `LoggingToolInvocationSink` vẫn log
  vân tay khi không có trace nào đang mở.
- `application/gateway.py` — `_finish` truyền thêm payload. Một dòng mỗi bên.

**Entrypoints**

- `wiring.py` — `IntegrationTraceSink.record` chuyển payload xuống; dựng
  `PayloadStore` cho `TraceService`.
- `http.py`, `agent.py` — `init_object_store()` / `close_object_store()` trong
  `lifespan`, cạnh `init_engine` và `init_redis`.
- `core/config.py` + `.env.example` — `trace_payload_inline_max_bytes` (16384),
  `trace_payload_max_bytes` (1048576).

**Test phải sửa vì đổi chữ ký**: `test_trace_service.py`, `test_trace_api.py`,
`test_integration_gateway.py`, `test_integration_api.py`.

`docker-compose.yml` **không cần sửa** — `minio-init` đã tạo `aw-traces` sẵn.

### A2 — payload cho `model_call` (tách riêng, nên làm)

`AgentRuntime._complete` hiện chỉ ghi model, `stop_reason` và số token. Thứ
§10.1 cần để "loại bỏ model chatter" là cái **thật sự gửi đi**: system prompt,
danh sách tool được mời, và phản hồi thô — không cái nào nằm trong bảng
`messages` của domain `agent`.

Chạm ba file: `agent/application/ports.py` (`ExecutionTraceSink.append_step`
nhận payload), `agent/application/service.py`, `entrypoints/agent_wiring.py`.
Tách khỏi A1 vì A1 đủ đứng một mình, còn cái này nên chốt sau khi nhìn dữ liệu
thật.

---

## 5. Thứ tự commit

1. `feat(api): an S3 object store for the core` — `core/objectstore.py` + test.
   Chưa ai dùng, xanh độc lập.
2. `feat(api): trace steps keep their payloads, not just fingerprints` — domain
   + port + store + migration + repository + service + fake. Lõi của WP-A.
3. `feat(api): tool payloads travel to the trace` — integration + wiring. Từ đây
   payload thật bắt đầu được ghi.
4. `feat(api): read a trace step's payload` — endpoint đọc.
5. (A2) `feat(api): model calls keep what was sent and what came back`.
6. `docs: ADR-0006` + cập nhật README.

Mỗi commit phải xanh độc lập — kiểm bằng cách stash phần còn lại rồi chạy full
suite, đúng cách đã làm với commit Notion.

**ADR-0006 là bắt buộc**, không phải vì lệch spec (§9.1 *đòi* làm việc này) mà
vì tiêu chí thứ hai của `docs/adr/README.md`: **đắt để đảo ngược**. Ba thứ trong
Q2, Q3 và Q4 đóng băng ngay khi dòng payload đầu tiên được ghi — khoá
content-addressed, ngưỡng inline, trần cứng. Đổi ý sau đó là migrate dữ liệu,
không phải sửa code.

---

## 6. Rủi ro và câu hỏi mở

- **`boto3` là client đồng bộ.** Mọi lời gọi phải qua `asyncio.to_thread`, nếu
  không thì mỗi lần ghi payload chặn event loop của cả tiến trình. Client cấp
  thấp của boto3 là thread-safe nên dùng chung một instance được.
- **Vân tay phải khớp byte đã ghi.** `PayloadRef.of` của domain `trace` và của
  domain `integration` là hai bản sao (contract 1 cấm chia sẻ). Nếu hai bên
  serialize khác nhau thì băm ra hai giá trị khác nhau cho cùng một payload.
  Lời giải: Trace tự tính vân tay từ payload nó nhận được, không nhận băm từ
  ngoài.
- **Ngưỡng 16 KiB và trần 1 MiB là đề xuất, chưa đo.** Dữ liệu để đo đã có sẵn:
  cột `input_ref_byte_size` / `output_ref_byte_size` trong `trace_steps` của
  những lượt Agent đã chạy. Nên đọc phân bố thật trước khi chốt ADR-0006.
- **Retention chưa có.** Bucket sẽ lớn dần vô hạn. Lifecycle rule thuộc hạ tầng
  (Phase 7), nhưng đường đọc phải chịu được object đã biến mất **ngay từ commit
  4** — không phải thêm sau.
