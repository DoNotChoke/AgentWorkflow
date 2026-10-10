# ADR 0009 — Worker gọi Integration Gateway qua HTTP

- **Status:** Accepted
- **Date:** 2026-10-10
- **Deciders:** TuanNghia
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §2.4, §6.3, §14, §47; ADR-0001,
  ADR-0002, ADR-0003, ADR-0007, ADR-0010, ADR-0012; Q5 và Q12 của
  `docs/plans/wp-b-workflow-ir-va-generic-executor.md`

Quyết định này được làm từ commit `4893cfd` (contract 7) và `6ecfe40` (node `tool` gọi
provider thật). ADR viết sau, khi nó đã chạy đủ lâu để biết cái giá.

## Bối cảnh

Executor chạy trong Temporal worker, và node `tool` của một workflow phải gọi Slack hay
Notion bằng credential của người dùng.

ADR-0002 chốt ranh giới credential nằm ở `entrypoints/wiring.py`: đó là module duy nhất
dựng được thứ giải mã token. ADR-0003 cho Agent Runtime một module wiring riêng để nó
không có đường import nào tới đó, và contract 6 canh điều ấy bằng máy. Từ Phase 3, Agent
gọi Gateway qua HTTP.

Worker đứng trước đúng ngã ba đó. §14 cho phép cả hai hướng — *"External side effects must
occur in Activities or through Activity-controlled Integration execution"* — nên đây là
lựa chọn của dự án, không phải yêu cầu của bản thiết kế.

## Quyết định

**Worker không giải mã credential. Node `tool` gọi `POST /internal/v1/tools/{tool_id}/invoke`
của Control Plane.**

- `entrypoints/worker.py` nối domain qua `entrypoints/worker_wiring.py`, không qua
  `entrypoints/wiring.py`.
- **Contract 7** cấm `app.entrypoints.worker` nạp `core.crypto`, credential store,
  `ConnectionService` và `entrypoints.wiring`, kể cả gián tiếp. Một lần `lint-imports` hỏng
  ở contract này là một lần test bảo mật hỏng.
- Domain `workflow` khai cổng `ToolInvoker`; `GatewayToolInvoker` hiện thực nó bằng HTTP.
  Client HTTP và phần đọc lỗi dùng chung với Agent Runtime, ở `core/control_plane.py`.
- Worker không chọn connection và không khai tenant. Nó nói node nào đang chạy; Gateway
  đọc phần còn lại từ bản ghi lần chạy (ADR-0010, ADR-0014).
- Mọi trách nhiệm của §6.3 — policy, idempotency, rate limit, circuit breaker, retry,
  chuẩn hoá lỗi, trace — vì vậy chạy **một lần, ở một nơi**, cho cả Agent lẫn workflow.
- Activity chờ lâu hơn thời hạn của tool trong catalog một khoảng lề, và lời gọi HTTP
  cũng vậy: Gateway phải kịp trả lời, bằng kết quả hoặc bằng lỗi của chính nó, trước khi
  Temporal bỏ lần thử và gọi lại.
- Lỗi của Gateway đi về như `AppError` cùng hạng, rồi thành lỗi Temporal mang đúng tên
  hạng đó. `retry_after_seconds` thành `next_retry_delay`; `OutcomeUnknown` không bao giờ
  được thử lại (ADR-0007, ADR-0012).

## Hệ quả

### Tích cực

- Đúng một tiến trình trong hệ thống giải mã được credential, và điều đó được một câu
  lệnh kiểm chứng chứ không phải một quy ước.
- Worker chạy code do model viết (node `transform`, ADR-0013) và giữ API key của provider
  model (ADR-0011). Cả hai đều chấp nhận được vì worker không có token nào của connection.
- Một node `tool` của workflow và một lời gọi tool của Agent để lại cùng một loại bước
  trace, qua cùng một sổ idempotency.

### Tiêu cực / chi phí phải trả

- Mỗi lời gọi tool là hai chặng mạng thay vì một.
- Control Plane là đường đi của mọi side effect của mọi workflow: nó chết thì workflow
  đứng, kể cả khi provider vẫn sống.
- Có hai tầng thử lại chồng lên nhau — Gateway tự thử lại trong thời hạn của tool, rồi
  Temporal thử lại activity. Chúng phải được khai cho khớp, và khoảng lề ở trên là chỗ
  khớp đó.

### Rủi ro cần theo dõi

- **Chặng HTTP đứt khi Gateway còn đang gọi provider.** Lần thử lại của Temporal gặp một
  hàng `in_flight` trong sổ: còn lease thì được bảo chờ rồi nhận lại kết quả của lời gọi
  trước; hết lease thì nhận `OutcomeUnknown` và node dừng. Cả hai đường có test trên
  Temporal. Đường thứ hai nghĩa là một tool ghi có thể làm hỏng node vì Control Plane chết
  giữa chừng — đúng theo ADR-0007, và là cái giá của việc không đăng hai lần.
- **Độ trễ của chặng thứ hai chưa được đo.** Với workflow chạy nền thì vài chục mili giây
  không đáng kể, nhưng con số phải có trước khi ai đó dùng nó làm lý do phá ranh giới.
- **Hạn mức của endpoint nội bộ tính theo người khởi chạy.** Một vòng lặp dài của một
  người có thể chạm trần gọi tool của chính họ.

## Điều kiện xem xét lại

- Độ trễ đo được của chặng worker → Control Plane vượt 20% thời gian trung vị của một lời
  gọi tool.
- Control Plane thành nút cổ chai đo được của workflow: hàng đợi `integration-*` dài ra
  trong khi provider không chậm.
- Integration Gateway được tách thành một dịch vụ riêng (§32). Khi đó worker gọi dịch vụ
  ấy, và ADR này được thay bằng một ADR nói ranh giới credential đã dời đi đâu.

## Phương án đã cân nhắc và loại bỏ

1. **Worker gọi Gateway trong tiến trình.** Nhanh hơn một chặng mạng, đổi lại worker phải
   nạp `wiring` và giải mã được token — đúng tiến trình sẽ chạy code do model viết. Hai
   tiến trình giữ credential thì ranh giới của ADR-0002 không còn là một câu kiểm chứng
   được.
2. **Worker gọi thẳng provider bằng token Control Plane cấp cho từng lời gọi.** Token rời
   khỏi tiến trình giữ nó và nằm trong bộ nhớ của worker, và mười bốn trách nhiệm của
   §6.3 phải có bản thứ hai ở worker.
3. **Một dịch vụ Integration riêng ngay từ đầu.** Thêm một thứ phải triển khai cho một
   nhóm chưa có tải nào cần nó; ADR-0001 đã chọn tách logic trước, tách vật lý sau.
