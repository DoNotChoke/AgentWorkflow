# ADR 0003 — `entrypoints/agent_wiring.py` tách khỏi `wiring.py`, không chạm domain `connection`

- **Status:** Accepted
- **Date:** 2026-09-04
- **Deciders:** TuanNghia (solo developer)
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §2.4, §9, §26; ADR-0002

## Bối cảnh

Phase 3 (WP9) cần một chỗ nối `AgentRuntime` (domain `agent`) với `TraceService`
(domain `trace`) — hiện thực `ExecutionTraceSink` để mỗi bước của một lượt Agent
(gọi model, gọi tool, trả lời cuối) được ghi vào Execution Trace (§9).

`entrypoints/wiring.py` đã là "chỗ nối domain lại với nhau" cho Control Plane —
nó nối Identity↔Tenant, Tenant↔Connection, Connection↔Integration. Thêm một cặp
nối Agent↔Trace vào đó là lựa chọn tự nhiên nếu chỉ nhìn vào chức năng.

Nhưng `wiring.py` cũng là module duy nhất trong hệ thống dựng được
`CredentialResolver` (ADR-0002). Contract 6 cấm `app.entrypoints.agent` — tiến
trình Agent Runtime — chạm module đó, kể cả gián tiếp:

```toml
[[tool.importlinter.contracts]]
name = "Agent Runtime không có đường nào tới credential"
type = "forbidden"
source_modules = ["app.entrypoints.agent"]
forbidden_modules = [
    "app.core.crypto",
    "app.domains.connection.infrastructure.credential_store",
    "app.domains.connection.application.service",
    "app.entrypoints.wiring",
]
```

Nếu logic nối Agent↔Trace được thêm thẳng vào `wiring.py`, và Agent Runtime cần
gọi tới nó (để lấy `ExecutionTraceSink`), thì `entrypoints/agent.py` phải
`import app.entrypoints.wiring` — vi phạm contract 6 ngay từ dòng import đầu
tiên, bất kể phần logic Agent↔Trace bên trong module đó có sạch tới đâu.

## Quyết định

**1. Một module riêng: `entrypoints/agent_wiring.py`.** Nó là chỗ nối domain
DUY NHẤT mà tiến trình Agent Runtime được phép import. `wiring.py` vẫn là chỗ
nối domain duy nhất mà tiến trình Control Plane được phép import. Hai module
không import lẫn nhau.

**2. `agent_wiring.py` chỉ được phép biết hai domain: `agent` và `trace`.**
Không domain nào trong hai domain đó mang credential. Đây không phải một quy
ước — nó là điều kiện để contract 6 tiếp tục canh đúng: contract 6 liệt kê
`app.entrypoints.wiring` vào danh sách cấm, không liệt kê `app.entrypoints`
nói chung, vì nếu liệt kê cả gói thì `agent_wiring.py` cũng bị cấm tồn tại.
Điều đó có nghĩa **an toàn của module này không đến từ import-linter — nó đến
từ kỷ luật không thêm import.** Không có contract nào tự động chặn
`agent_wiring.py` import `app.domains.connection` ngày mai.

**3. `ConnectionResolver` (chọn `connection_id` cho một tool call, khai ở
`agent/application/ports.py`) không được hiện thực bằng cách import
`app.domains.connection` vào `agent_wiring.py`, kể cả chỉ để đọc metadata**
(tên kết nối, provider — không đụng token). Lý do không phải "trường dữ liệu
này có nhạy cảm hay không" — `Connection` vốn dĩ không mang token (§21, xem
domain `connection`). Lý do là **ranh giới ở đây là domain, không phải
field**: một khi `agent_wiring.py` có sẵn một import tới `connection`, thêm
một dòng import credential cạnh nó sau này chỉ là một thao tác copy-paste,
không còn gì cản. Khi cần chọn `connection_id`, lời giải đúng là một endpoint
HTTP mới trên Control Plane mà `agent/infrastructure/integration_client.py`
(hoặc một client tương tự) gọi tới — cùng hình dạng đã chọn cho việc thực thi
tool ở WP6, không phải một ngoại lệ riêng cho việc chọn kết nối.

**4. Mỗi lần ghi trong `agent_wiring.py` tự mở transaction riêng
(`session_scope()`).** Không nhận `session` của request đang xử lý lượt Agent
làm tham số. Đây là bài học lặp lại từ `IntegrationTraceSink` (`wiring.py`,
WP3): ghi trace trong transaction của request nghĩa là một lượt Agent lỗi giữa
chừng sẽ rollback cuốn theo các bước trace đã ghi trước đó — đúng lúc chúng
cần nhất để biết "vì sao nó hỏng".

## Hệ quả

### Tích cực

- Contract 6 tiếp tục canh đúng: `entrypoints/agent.py` (WP10) import được
  `agent_wiring.py` để lấy `ExecutionTraceSink`, mà không phải nới bất kỳ
  contract nào.
- `TraceService` có thêm ba cửa nguyên thuỷ dùng chung được cho cả Integration
  lẫn Agent: `start_agent_trace`, `record_step`, `complete_by_id` — cùng khuôn
  `record_tool_call`/`append_step_by_id` đã có từ WP3, không phát minh cách
  thứ hai.
- Việc tách file được viết thành test: `lint-imports` được chạy sau khi cố ý
  thêm một import bị cấm (trực tiếp lẫn qua chuỗi ba bước
  `agent → agent_wiring → wiring → credential_store`), xác nhận cả hai bị
  contract 5 và contract 6 chặn, rồi mới hoàn tác — cùng cách ADR-0002 đã kiểm
  chứng chính nó.

### Tiêu cực / chi phí phải trả

- `TraceService` phình thêm ba phương thức "cửa nguyên thuỷ" chỉ để né contract
  5 (entrypoint không import tầng `domain`). Đây là cái giá cố định của việc
  giữ ranh giới module nghiêm — đã trả một lần cho Integration ở WP3, giờ trả
  thêm một lần cho Agent.
- `ConnectionResolver` chưa có hiện thực thật ở cuối WP9. Endpoint HTTP để nó
  gọi tới chưa tồn tại — đó là việc của WP10.

### Rủi ro cần theo dõi

- **Ai đó thêm `app.domains.connection` vào `agent_wiring.py` vì "chỉ đọc
  metadata, không phải credential".** Không có contract nào chặn việc này —
  quyết định 3 ở trên dựa vào kỷ luật đọc code review, không phải CI. Nếu nhu
  cầu đọc metadata connection từ Agent Runtime trở nên thường xuyên, cân nhắc
  thêm một contract `forbidden` mới liệt kê `app.domains.connection` cho
  `app.entrypoints.agent_wiring`, biến kỷ luật thành máy kiểm.
- **`agent_wiring.py` phình ra** khi Phase 4+ cần nối thêm domain khác (ví dụ
  `approval`) vào Agent Runtime. Nếu phình quá mức đọc được, tách thành
  `agent_wiring/` với một module cho mỗi cặp domain — không phải chuyển adapter
  vào trong domain `agent` hay `trace` (phá contract 1).

## Điều kiện xem xét lại

- WP10 cần hiện thực `ConnectionResolver` thật. Nếu lúc đó phát hiện đi qua
  HTTP tốn overhead không chấp nhận được (đo được, không phải suy đoán), quay
  lại xem xét lại quyết định 3 — nhưng phương án thay thế phải là một contract
  mới canh đúng phạm vi được nới, không phải bỏ qua ranh giới.

## Phương án đã cân nhắc và loại bỏ

**Gộp Agent↔Trace vào `wiring.py`.** Loại bỏ vì buộc `entrypoints/agent.py`
phải `import app.entrypoints.wiring`, vi phạm contract 6 ngay từ dòng import
đầu tiên.

**Thêm `app.entrypoints` (cả gói) vào danh sách cấm của contract 6.** Loại bỏ
vì khi đó `agent_wiring.py` cũng không import được `trace`, và Agent Runtime
mất luôn khả năng ghi trace — quá tay so với vấn đề cần giải.

**Cho `agent_wiring.py` import `connection.infrastructure.repositories` (chỉ
metadata, không phải `credential_store`) để hiện thực `ConnectionResolver`
ngay trong WP9.** Loại bỏ vì lý do ở quyết định 3: ranh giới cần giữ là domain,
không phải field, và quyết định "cách chọn connection" (kết nối chung của
workspace? để người dùng tự chọn?) là một quyết định sản phẩm chưa chốt, không
nên khoá cứng vào lúc chỉ đang nối Trace.
