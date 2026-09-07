# ADR 0004 — Chọn connection theo Conversation, không theo Agent hay theo user

- **Status:** Accepted
- **Date:** 2026-09-07
- **Deciders:** TuanNghia (solo developer)
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §2.4, §6.3; ADR-0002, ADR-0003

## Bối cảnh

`AgentRuntime._execute_tool_call` (`agent/application/service.py`) cần một `connection_id`
cho mỗi lời gọi tool. Từ WP9, `ConnectionResolver` (`agent/application/ports.py`) mới có
chữ ký, chưa có hiện thực thật — ADR-0003 để việc đó lại cho WP10, và chốt sẵn một ràng
buộc: hiện thực đó **không được import `app.domains.connection`**, phải đi qua HTTP tới
Control Plane, đúng hình dạng đã chọn cho việc thực thi tool.

`ConnectionResolverClient` (đã viết ở WP10) gọi `GET /v1/connections` bằng token của người
đang chat, lọc theo `provider` và `status=active`, rồi **tự chọn** — ưu tiên connection dùng
chung workspace trước connection riêng, lấy cái đầu tiên khớp. Khi một provider có từ hai
connection khả dụng trở lên, hệ thống đoán trong im lặng: không có tín hiệu nào cho người
dùng biết Agent vừa hành động thay mặt connection nào.

Bài toán đặt ra rõ hơn khi đối chiếu với hai sự thật khác của hệ thống:

1. **Agent là tài nguyên chung của workspace, không riêng của người tạo.**
   `AgentAdminService.list_agents` không lọc theo `created_by` — mọi thành viên workspace
   thấy và mở `Conversation` mới được với mọi Agent (chỉ sửa/lưu trữ mới cần là người tạo
   hoặc `ws:admin`, xem `ensure_can_manage`). `Conversation` mới là tài nguyên riêng của
   từng người (`Conversation.user_id`).
2. **Connection có ranh giới riêng tư theo người dùng**, và ranh giới đó vốn đã đúng ngay từ
   endpoint liệt kê: `GET /v1/connections` lọc connection riêng của người khác ngay trong
   câu truy vấn (biến mất hoàn toàn, không phải mờ/khoá) — vì nó luôn gọi bằng token của
   chính người đang hành động, không tự ký danh tính khác (§2.4).

Hai sự thật đó cấm thẳng phương án tưởng chừng đơn giản nhất: gán cứng một `connection_id`
vào Agent lúc tạo. Nếu người tạo chọn một connection riêng của họ, mọi người khác dùng
chung Agent đó sẽ gặp một connection mà backend biết chắc là họ không có quyền nhìn thấy.
Điểm gán đúng không phải là Agent.

Nhu cầu sản phẩm còn thêm một chiều nữa: một provider có thể có **từ hai connection khả
dụng trở lên** (hai tài khoản Slack chẳng hạn), và người dùng cần dùng connection khác nhau
cho những việc khác nhau — không phải một lựa chọn cố định vĩnh viễn, nhưng cũng không phải
hỏi lại ở mỗi lần gọi tool trong cùng một mạch việc đang làm.

## Quyết định

**1. Lựa chọn connection gắn với `Conversation`, theo khoá `(conversation_id, provider)`.**
Không gắn với Agent (lý do ở trên), không gắn với `(user, workspace, provider)` — một mặc
định dùng chung cho mọi cuộc hội thoại sẽ không cho phép hai cuộc hội thoại của cùng một
người, cùng một Agent, dùng hai tài khoản Slack khác nhau, đúng cái nhu cầu vừa nêu.

**2. Ba trường hợp khi một tool cần connection cho một provider, trong một conversation:**

- Conversation đã có lựa chọn cho provider đó → dùng lại, không hỏi, không gọi resolve.
- Chưa có, và có đúng một connection khả dụng → tự chọn **và lưu ngay vào conversation**.
  Từ đó về sau, kể cả khi có thêm connection thứ hai xuất hiện, cuộc hội thoại này vẫn dùng
  connection đã chốt — ổn định trong suốt vòng đời của nó, không đổi ngầm.
- Chưa có, và có từ hai connection khả dụng trở lên → **không đoán**. Ném một lỗi mới,
  phân biệt được với "chưa có connection nào" (`ValidationError` hiện tại), mang theo danh
  sách ứng viên để phía gọi biết cần hỏi người dùng gì.

**3. Lỗi "cần chọn" đi qua đúng con đường lỗi đã có, không cần cơ chế mới.**
`_execute_tool_call` đã bọc việc resolve trong `try/except AppError` và tự biến nó thành
một tin nhắn lỗi trong hội thoại (`role=TOOL`, `is_error=True`) — lượt chat không vỡ, model
thấy lỗi và dừng lại. Lỗi "cần chọn connection" chỉ là một loại `AppError` mới đi qua đúng
đường đó. Người dùng chọn ở giao diện riêng (lưu thẳng vào conversation qua một endpoint mới
trên `entrypoints/agent.py`, vì `Conversation` là tài nguyên của domain `agent`), rồi gửi
lại tin nhắn — lượt kế tiếp resolve được ngay vì đã có lựa chọn lưu sẵn.

Quyết định này cố ý **không** dựng cơ chế tạm dừng lượt chat giữa chừng để chờ người dùng
trả lời rồi chạy tiếp. Một lượt thất bại rõ ràng, người dùng sửa xong gửi lại — cùng mục
tiêu sản phẩm, không cần cơ chế pause/resume mới trong `AgentRuntime`.

**4. Bảng lưu `(conversation_id, provider) → connection_id` thuộc domain `agent`, không
thuộc domain `connection`.** `connection_id` ở đây là một chuỗi bất kỳ mà domain `agent`
cầm và chuyển tiếp, giống hệt cách nó đã cầm `tool_id` (`ToolCallRequest.tool_id`) mà không
cần import domain `integration` để hiểu nó. Một Protocol mới trong
`agent/application/ports.py` (đọc/ghi theo khoá `(conversation_id, provider)`), hiện thực
SQL bình thường trong `agent/infrastructure` — không có HTTP nào ở bước này, khác
`ConnectionResolver`. `AgentRuntime` nhận Protocol này như một dependency nữa, cạnh
`connections` hiện có.

**5. Không xác thực lại connection đã lưu còn hợp lệ khi đọc lại.** Nếu nó đã bị thu hồi hay
hết hạn, lời gọi tool thật (Integration Gateway, qua `CredentialResolver`) sẽ tự báo đúng
loại lỗi khi thật sự thực thi (`ConnectionExpiredError`...) — rơi vào đúng con đường
lỗi-thành-tin-nhắn đã có ở mục 3. Thêm một bước kiểm tra trùng lặp ở đây không mua thêm gì.

## Hệ quả

### Tích cực

- Không phá contract import-linter nào của ADR-0002/0003: dữ liệu mới nằm hoàn toàn trong
  domain `agent`, Agent Runtime vẫn không chạm `app.domains.connection`.
- Không cần cơ chế pause/resume mới — tái dùng nguyên vẹn con đường `AppError` → tin nhắn
  lỗi đã có từ WP9.
- Agent dùng chung được nhiều người mà không xung đột: mỗi `Conversation` (riêng của một
  người) tự giữ lựa chọn của chính nó, không đè lên nhau.
- Đúng yêu cầu sản phẩm: hai cuộc hội thoại của cùng một người, cùng một Agent, dùng được
  hai connection khác nhau của cùng một provider.
- Thay "đoán âm thầm khi mơ hồ" (hành vi cũ của `ConnectionResolverClient`) bằng "yêu cầu
  chọn tường minh" — không còn trường hợp Agent hành động thay một connection mà người dùng
  không biết là connection nào.

### Tiêu cực / chi phí phải trả

- Thêm một bảng, một Protocol, một lượt đọc trước resolve cho mỗi lần gọi tool. Đọc thẳng
  theo khoá chính `(conversation_id, provider)` nên chi phí không đáng kể, nhưng là một
  round-trip DB nữa trên đường nóng của mỗi tool call.
- Một cuộc hội thoại đã chốt một connection rồi sau đó connection ấy bị thu hồi sẽ luôn báo
  lỗi ở lần gọi tool kế tiếp cho tới khi người dùng chủ động đổi — không tự rơi về connection
  còn sống như cách "tự động chọn" cũ từng làm. Đây là đánh đổi có chủ đích (không âm thầm
  đổi connection giữa chừng một cuộc hội thoại), nhưng là một chỗ người dùng có thể bất ngờ
  nếu không biết quy tắc.
- Mỗi cuộc hội thoại **mới** với một Agent có tool chạm tới provider đang có từ hai
  connection trở lên sẽ luôn cần một lượt chọn trước khi tool chạy được lần đầu — kể cả khi
  cuộc hội thoại trước đó, cùng Agent, cùng provider, đã từng chọn rồi. Lựa chọn không kế
  thừa giữa các cuộc hội thoại.

### Rủi ro cần theo dõi

- **Lỗi "cần chọn connection" bị người dùng hiểu nhầm là lỗi thường.** Nếu FE hiển thị nó y
  hệt mọi `AppError` khác (chuỗi thông điệp, không có hành động rõ ràng), người dùng sẽ
  không biết phải làm gì tiếp — cần một chỗ hiển thị riêng, dẫn thẳng tới nơi chọn connection,
  không chỉ một dòng lỗi đỏ.
- **Bảng `(conversation_id, provider)` phình không giới hạn** nếu một workspace tạo rất
  nhiều cuộc hội thoại ngắn hạn. Không phải vấn đề ở quy mô hiện tại (mỗi dòng là vài chục
  byte, xoá theo `ON DELETE CASCADE` khi conversation bị xoá — nếu sau này Conversation có
  vòng đời xoá thật), nhưng đáng để ý nếu workspace có hàng chục nghìn cuộc hội thoại mỗi
  tháng.

## Điều kiện xem xét lại

- Sản phẩm cần **đổi connection giữa chừng một cuộc hội thoại đang chạy** (nửa đầu dùng tài
  khoản Slack A, nửa sau đổi sang B, trong cùng một mạch việc). Mô hình khoá
  `(conversation_id, provider)` hiện tại không cho phép việc này — cần thêm khái niệm "đổi
  lựa chọn giữa chừng" hoặc chuyển hẳn sang chọn theo từng lời gọi tool, không phải theo cả
  cuộc hội thoại.
- Đo được rằng tỉ lệ lượt chat đầu tiên của một cuộc hội thoại mới thất bại vì
  "cần chọn connection" đủ cao để thành ma sát rõ rệt. Khi đó cân nhắc thêm một gợi ý (không
  tự áp) — ví dụ "lần trước bạn chọn B" — ngay trong màn hình chọn, mà không phá mô hình
  theo-conversation.
- Agent Runtime dựng cơ chế tạm dừng/chờ người dùng cho một mục đích khác (ví dụ approval
  cho tool nhạy cảm — đã thấy phác hoạ trong sidebar sản phẩm). Khi đó việc "hỏi connection"
  nên hợp nhất vào cùng cơ chế đó thay vì giữ một con đường lỗi riêng như quyết định này.

## Phương án đã cân nhắc và loại bỏ

**Gán cứng `connection_id` vào Agent lúc tạo.** Loại bỏ vì Agent là tài nguyên chung của
workspace trong khi connection có ranh giới riêng tư theo người dùng — hai điều đó xung đột
trực tiếp, xem "Bối cảnh".

**Mặc định theo `(user, workspace, provider)`, đổi được, không gắn với conversation.** Đơn
giản hơn phương án đã chọn (không cần bảng theo conversation, không cần hỏi lại ở mỗi cuộc
hội thoại mới), nhưng loại bỏ vì không cho phép hai cuộc hội thoại của cùng một người dùng
cùng lúc dùng hai connection khác nhau của cùng một provider — đúng nhu cầu sản phẩm đã nêu
mà phương án này không đáp ứng được.

**Mô hình ba lớp: ghi đè theo conversation, chồng lên mặc định theo `(user, workspace,
provider)`, chồng lên tự động.** Cân nhắc làm cầu nối giữa hai phương án trên, cho cả tiện
lợi (mặc định kế thừa) lẫn linh hoạt (ghi đè riêng). Loại bỏ vì thêm hẳn một tầng dữ liệu và
một tầng ưu tiên chỉ để giải quyết một sự bất tiện nhỏ (phải chọn lại ở mỗi cuộc hội thoại
mới) — chưa có bằng chứng bất tiện đó đủ lớn để trả giá phức tạp thêm ngay từ đầu. Xem
"Điều kiện xem xét lại" — đây là phương án quay lại nếu tín hiệu ma sát xuất hiện thật.

**Hỏi thật sự giữa lượt chat (tạm dừng, chờ người dùng trả lời, chạy tiếp).** Loại bỏ vì cần
dựng cơ chế pause/resume hoàn toàn mới trong `AgentRuntime` — không có trong kiến trúc hiện
tại — trong khi con đường lỗi đã có (`AppError` → tin nhắn lỗi) đạt cùng mục tiêu sản phẩm
(lượt hiện tại thất bại rõ ràng, lượt sau chạy được sau khi đã chọn) với chi phí thấp hơn
nhiều.
