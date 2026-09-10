# ADR 0006 — Payload của Execution Trace: khoá theo nội dung, hai tầng lưu, trần cứng

- **Status:** Accepted
- **Date:** 2026-09-10
- **Deciders:** TuanNghia (solo developer)
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §2.4, §4.7, §9.1, §10.1, §10.2, §19, §33;
  ADR-0002; [`docs/plans/wp-a-trace-payload-store.md`](../plans/wp-a-trace-payload-store.md)

## Bối cảnh

Execution Trace ghi lại mọi bước của một lượt Agent, nhưng với nội dung của từng
bước nó chỉ giữ **vân tay**: `sha256` và `byte_size`. `PayloadRef.of()` băm
payload rồi vứt nó đi. Bước `model_call` còn không có cả vân tay — chỉ tên model,
`stop_reason` và số token.

Ba thứ hỏng vì vậy:

- **Phase 5 không có dữ liệu.** §10.2 bắt compiler phân biệt hằng số, workflow
  input và output của node trước. Cả ba phán đoán đều đọc **giá trị**; vân tay
  chỉ trả lời được "hai payload có y hệt nhau không".
- **Lượt hỏng không để lại gì.** `admin.record_turn` chỉ chạy khi lượt thành
  công, nên một lượt vỡ giữa chừng không có tin nhắn nào trong bảng `messages`.
  Đúng lúc cần biết "đã gửi gì cho model" thì không nơi nào còn giữ nó.
- **§9.1 chưa được thoả**: *"Large inputs and outputs should be stored in object
  storage with references persisted in PostgreSQL."*

MinIO và bucket `aw-traces` có sẵn từ Phase 0 nhưng chưa dòng code nào dùng.

Kế hoạch triển khai và lý lẽ chi tiết nằm ở `docs/plans/`. ADR này chỉ giữ những
quyết định **đắt để đảo ngược** — những thứ đóng băng ngay khi payload đầu tiên
được ghi.

## Quyết định

**1. Domain `trace` sở hữu payload.** Integration không biết object storage tồn
tại. Payload đi theo đúng đường bản ghi đang đi — `ToolInvocationSink` →
`entrypoints/wiring.py` → `TraceService` — nhưng **cạnh** `ToolInvocationRecord`,
dưới dạng hai tham số `input_payload`/`output_payload`, không phải trường của bản
ghi. Bản ghi là thứ có thể bị log nguyên cả object, nên nó chỉ được mang vân tay
(§33, `test_trace_chi_mang_van_tay_khong_mang_payload`).

**2. Khoá đánh theo nội dung, dưới tiền tố tenant.**

```
aw-traces / {tenant_id}/payloads/{sha256[:2]}/{sha256}.json
```

Byte ghi ra là đúng byte đã băm — cùng một hàm `canonical_bytes`. Ghi lại cùng
payload là đè lên chính nó, nên replay idempotency không nhân đôi lưu trữ. Hai
tenant có payload y hệt vẫn là hai object: khử trùng lặp xuyên tenant là thứ §19
cấm.

**3. Ba vị trí, và cột `location` là thứ quyết định đọc ở đâu.**

| `location` | Khi nào | Nội dung ở đâu |
|---|---|---|
| `inline` | ≤ 16 KiB | Cột JSONB `input_inline`/`output_inline` của `trace.trace_steps` |
| `object` | 16 KiB – 1 MiB | MinIO, theo khoá ở quyết định 2 |
| `absent` | > 1 MiB, ghi hỏng, hoặc hàng ghi trước migration 0011 | Không đâu cả — chỉ còn vân tay |

**Hai con số 16 KiB và 1 MiB được chốt khi chưa đo** — chưa có đủ lượt gọi tool
thật để đọc phân bố kích thước. Sai ở đây không đắt: nó đổi tỉ lệ payload nằm
trong hay ngoài Postgres, không đổi lược đồ dữ liệu.

**4. Ghi payload hỏng không làm hỏng thao tác được quan sát.** Object storage lỗi,
hay tiến trình quên `init_object_store()`, đều hạ về `absent` và ghi log. Thứ tự
ghi là object trước, hàng DB sau: ngược lại thì một lần S3 hỏng để lại một hàng
trỏ vào hư không, vĩnh viễn.

**5. Đọc qua API, trả ba trạng thái.** `GET /v1/traces/{id}/steps/{step_id}/payload/{side}`
đi qua cùng `TenantGuard` như hai route trace còn lại — không presigned URL.
Trả về `available`, `not_stored` (chưa từng cất) hoặc `gone` (đã cất rồi mất),
vì "đã hết hạn lưu trữ" và "không tìm thấy" là hai câu khác nhau. Mã hoá at-rest
là SSE của bucket: contract 4 cấm `app.domains.trace` import `app.core.crypto`.

**6. Bước `model_call` lưu nguyên prompt ở mỗi vòng.**

- `input_payload` = `{model, system, tools, turns}`; `output_payload` =
  `{text, stop_reason, tool_calls, usage, model}`. Nhánh lỗi lưu `input_payload`.
- **Không ghi đè, không xoá vòng trước.** Mỗi vòng là một sự kiện riêng; lượt vỡ
  ở vòng N cần các vòng trước còn nguyên.
- **`tools` chỉ lưu `name` + `version`.** Đo trên catalog hiện có: 12 tool là
  10.735 byte, lặp y hệt ở mọi vòng. Bản đầy đủ nằm trong
  `packages/schemas/tool/<provider>/tools/<tên>.v<N>.json`, đã commit và có
  version — lưu lại nó là trả tiền cho thứ đã nằm trong git.
- **`max_tokens` không được ghi.** Runtime không truyền nó; giá trị dùng là mặc
  định của `ModelClient`, nằm ngoài tầm nhìn của tầng Runtime.

## Hệ quả

### Tích cực

- Phase 5 có giá trị thật để tổng quát hoá, và một lượt hỏng để lại đủ dữ liệu
  để hiểu vì sao nó hỏng.
- Cây trace vẫn dựng bằng một query: nó chỉ mang vị trí và kích thước của
  payload; nội dung chỉ đi qua endpoint riêng khi người dùng mở một bước.
- Một lượt chat bình thường (đo trên ví dụ thật: ~3 KB cho 6 payload) nằm gọn
  trong Postgres, không chạm object storage.
- Vân tay đã có sẵn trở thành con trỏ — không thêm cột nào để giữ khoá.

### Tiêu cực / chi phí phải trả

- Hai đường đọc thay vì một, gói trong `ObjectPayloadStore.get`.
- **Hai chỗ lặp được chấp nhận, đã đo:**
  - Vòng sau chứa trọn vòng trước. LLM API là stateless, nên đây là sự thật đã
    xảy ra trên dây; số vòng bị chặn bởi `max_tool_calls_per_turn` (mặc định 8).
  - Output của tool lưu hai lần: dạng object ở bước `tool_call`, dạng chuỗi JSON
    trong `tool_results` của prompt vòng sau. Hai dạng khác nhau nên vân tay
    không khử được. Với một trang Notion 40 KB, một lượt hai vòng tốn ~82 KB.
- `tools` rút gọn chỉ đúng khi version đã phát hành của một tool **không bao giờ
  bị sửa tại chỗ**. Quy tắc đó đã có ở `packages/schemas/README.md`; ADR này biến
  nó từ quy ước thành điều kiện đúng đắn của dữ liệu trace.

### Rủi ro cần theo dõi

- **Ai đó sửa thẳng một file `.v1.json`** thay vì tạo `.v2.json`. Trace cũ sẽ trỏ
  vào một định nghĩa đã đổi, và không gì báo lỗi.
- **Token lọt vào prompt.** Trước đây vi phạm §2.4 chỉ đi qua RAM; giờ prompt nằm
  lại trên đĩa. `test_prompt_luu_xuong_khong_mang_token_cua_nguoi_dung` chạy một
  lượt với `authorization` đặc biệt rồi soi mọi payload đã ghi.
- **Bucket không có lifecycle rule** nên lớn dần vô hạn. Đường đọc đã chịu được
  object biến mất (`gone`), nhưng quy tắc dọn thì chưa có — việc của Phase 7.
- **`HttpConnector` không giới hạn kích thước phản hồi của provider.** Trần 1 MiB
  bảo vệ bucket, không bảo vệ RAM. Ngoài phạm vi ADR này, cần xử lý riêng.

## Điều kiện xem xét lại

- Sau vài trăm lượt Agent thật, phân bố `input_ref_byte_size` /
  `output_ref_byte_size` cho thấy ngưỡng 16 KiB đẩy phần lớn payload ra MinIO,
  hoặc gần như không bao giờ. Khi đó chỉnh con số — không đổi lược đồ.
- Dung lượng bucket cho thấy tool output lặp chiếm phần lớn. Khi đó thay
  `tool_results.content` trong prompt bằng con trỏ tới payload của bước
  `tool_call` — đổi nội dung ghi, không đổi lược đồ.
- Hội thoại dài làm prompt thường xuyên vượt 1 MiB, tức rơi về `absent` đúng lúc
  cần. Khi đó chuyển sang lưu phần mới thêm của mỗi vòng (xem phương án dưới).
- Có yêu cầu mã hoá payload theo từng tenant ở tầng ứng dụng. Việc đó đụng
  contract 4 và cần ADR riêng.

## Phương án đã cân nhắc và loại bỏ

**Đẩy mọi payload ra object storage.** Một đường đọc, gọn hơn — nhưng mỗi lần xem
một trace mười bước là hai mươi GET cho những object phần lớn dưới 1 KiB, và
§4.7 chỉ đòi payload *lớn* ra ngoài.

**Để mọi payload trong Postgres.** Trái §4.7, và một phản hồi vài MB sẽ đi vào mọi
query đọc bước trace.

**Payload là trường của `ToolInvocationRecord`.** Hiện thực đầu tiên đi theo hướng
này và làm đỏ một test canh §33. Sửa thiết kế, không sửa test.

**Presigned URL cho đường đọc.** Đưa quyền đọc dữ liệu người dùng ra ngoài vòng
kiểm tenant.

**Ghi đè, hoặc xoá vòng trước khi đã có vòng sau.** Lượt vỡ trước khi vòng sau
ghi xong thì mất cả hai; và `absent` sẽ mang thêm nghĩa thứ hai.

**Mỗi vòng chỉ lưu phần mới thêm.** Rẻ nhất khi ghi, nhưng "payload của một bước"
không còn tự trả lời được "đã gửi gì": người đọc phải gọi N lần rồi ghép. Giữ làm
phương án lùi — phép ghép chính xác được, vì `turn_messages` chỉ được nối thêm
(`test_vong_sau_chua_tron_vong_truoc` canh điều đó).

**Lưu nguyên menu tool ở mỗi vòng.** 10,7 KB lặp y hệt ở mọi vòng, cho một thứ
đã nằm trong git.
