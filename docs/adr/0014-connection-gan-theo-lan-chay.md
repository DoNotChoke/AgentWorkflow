# ADR 0014 — Workflow không lưu connection; mỗi lần chạy chọn connection cho từng node

- **Status:** Accepted
- **Date:** 2026-09-30
- **Deciders:** TuanNghia (solo developer)
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §2.3, §2.4, §11.2, §13, §39, §40, §41;
  ADR-0004; `docs/plans/wp-b-workflow-ir-va-generic-executor.md` Q7

## Bối cảnh

Q7 của kế hoạch WP-B chốt: *"workflow version chỉ bind được connection phạm vi
`workspace`"*. Connection nằm trong IR v1 (`connections: {provider: connection_id}`),
version bất biến, và validator lúc publish từ chối connection `private` cũng như
provider chưa được bind.

Mô hình đó gắn **tài khoản** vào **định nghĩa** của workflow, và hệ quả là:

1. **Tài khoản bản chất là cá nhân không dùng được.** Gmail, Google Drive của một
   người không có "bản dùng chung của workspace".
2. **Một workflow không tái dùng được giữa nhiều người.** Muốn mỗi người chạy bằng
   tài khoản của mình thì phải nhân bản workflow.
3. **Một provider chỉ có một tài khoản trong cả workflow.** Không viết được "đọc
   Notion của phòng A, ghi sang Notion của phòng B".
4. **Workflow sinh từ trace mang theo tài khoản của người làm mẫu** (Phase 5).
5. **§13 đã đặt binding ở chỗ khác.** Execution record *"references … connection
   bindings"* — thuộc tính của lần chạy, không phải của version.

## Quyết định

**1. Workflow không lưu connection.** IR v2 bỏ trường `connections`. Version chỉ nói
node nào gọi tool nào; tool nói provider nào.

**2. Mỗi lần chạy chọn connection cho từng node `tool`.** Hai node cùng provider
dùng được hai connection khác nhau. Node trong thân `loop` dùng một connection cho
mọi lượt.

| Nguồn lựa chọn | Hình dạng |
|---|---|
| Lần chạy tay | `connections: {node_id: connection_id}` và `connections_by_provider: {provider: connection_id}` — theo node thắng theo provider |
| Lịch chạy (Phase 7) | cùng hình dạng, chốt lúc tạo lịch |
| Execution record | map node → connection đã dùng, bất biến |

`connections_by_provider` chỉ là viết tắt để không phải lặp một lựa chọn cho mọi
node của cùng provider — vẫn là chọn tường minh ở lần chạy, không phải mặc định lưu
ở đâu cả.

**3. Không đoán.** Node nào vẫn trống thì `ConnectionChoiceRequiredError` (409), kèm
danh sách `{node, provider}` — cùng tinh thần và cùng hạng lỗi với ADR-0004.

**4. Connection riêng chỉ chính chủ dùng được.** Người chạy chọn được connection dùng
chung của workspace, hoặc connection riêng của **chính mình**. Connection riêng của
người khác nhận cùng thông báo với một id không có thật — không lộ rằng nó tồn tại,
như `GET /v1/connections` đã làm.

**5. Kiểm quyền ở hai chốt.** Control Plane kiểm lúc tạo execution. Integration
Gateway kiểm lại lúc gọi tool (GĐ 5, cùng workflow principal): connection riêng chỉ
dùng được khi chủ của nó là `initiated_by` của execution.

**6. `from_connection` trỏ theo node.** `{"from_connection": {"node": "tim", "field":
…}}` — metadata của connection mà node `tim` dùng ở lần chạy này. Trỏ theo provider
mơ hồ ngay khi hai node cùng provider dùng hai connection.

**7. IR v1 vẫn diễn giải được (§41).** `parse` nhận cả major 1 và 2; tài liệu v1 được
nâng lên v2 trước khi dùng: bỏ `connections`, và `from_connection` theo provider trỏ
vào node tool duy nhất của provider đó (nhận ra qua tiền tố `tool_id`). Không có hoặc
có nhiều hơn một node như vậy thì không diễn giải được một cách chắc chắn — `parse`
báo lỗi, không đoán.

**8. Mọi thành viên workspace chạy được workflow.** Chạy không phải sửa; quyền dùng
connection đã do mục 4 giữ.

## Hệ quả

### Tích cực

- Workflow dùng được tài khoản cá nhân, phục vụ được nhiều người, và dùng được nhiều
  tài khoản của cùng một provider trong một lần chạy.
- Version không bao giờ chứa tài khoản của ai: lập luận của ADR-0004 còn nguyên cho
  thứ được chia sẻ, và compiler ở Phase 5 không đóng tài khoản của người làm mẫu vào
  workflow.
- Publish đơn giản hơn: không còn luật connection ở cửa publish.
- Khớp §13: binding là thuộc tính của execution.

### Tiêu cực / chi phí phải trả

- **Mỗi lần chạy tay phải chọn connection.** Giao diện chạy phải đề xuất sẵn (ví dụ
  lấy lại lựa chọn của lần chạy trước) để việc này là một cú bấm, không phải một thủ
  tục.
- IR lên major 2; parser phải giữ đường nâng v1 và schema v1 mãi mãi.
- `executions` thêm cột `connection_bindings`; `ExecutorInput` mang binding theo.
- Hai chốt kiểm quyền thay vì một.

### Rủi ro cần theo dõi

- **Lịch chạy bám vào connection riêng của người đã rời tổ chức hoặc đã thu hồi
  connection.** Lần chạy hỏng với lỗi connection rõ ràng. Phase 7 phải tạm dừng các
  lịch đó khi xoá thành viên, không để chúng hỏng mỗi đêm.
- **Hiểu nhầm "người chạy" với "người tạo".** Lần chạy do A bấm dùng tài khoản A
  chọn, kể cả khi workflow do B tạo. Giao diện chạy phải hiện rõ node nào hành động
  bằng tài khoản nào.
- **Đường nâng v1 dựa vào tiền tố `tool_id` trùng provider.** Đúng với mọi tool hiện
  có, nhưng catalog không bắt buộc điều đó. Một tool vi phạm sẽ làm `from_connection`
  của v1 không nâng được — lỗi rõ ràng, không phải hành vi sai.

## Điều kiện xem xét lại

- Đo được rằng phần lớn lần chạy tay chỉ lặp lại lựa chọn của lần trước. Khi đó cân
  nhắc nhớ lựa chọn gần nhất theo `(người, workflow)` làm **gợi ý** trên giao diện —
  không tự áp, và không lưu vào version.
- Cần một lần chạy hành động bằng **tài khoản riêng của người khác** có sự đồng ý của
  họ. Mô hình "chỉ chính chủ" không cho phép — cần một khái niệm uỷ quyền tường minh,
  không phải nới mục 4.

## Phương án đã cân nhắc và loại bỏ

- **Giữ Q7 nguyên vẹn.** Loại vì năm hệ quả ở phần Bối cảnh.
- **Version giữ mặc định dùng chung cho từng provider, lần chạy ghi đè.** Bản đầu của
  chính ADR này. Loại: vẫn gắn tài khoản vào định nghĩa, và một provider chỉ có một
  mặc định nên không phục vụ được nhiều tài khoản cùng provider.
- **Cho version chứa connection riêng.** Loại: mọi người chạy version đó hành động
  bằng tài khoản của người publish — đúng lỗi ADR-0004 chặn.
- **Giữ `connections` trong schema v1, chỉ để publish từ chối nó.** Rẻ hơn, nhưng để
  lại một trường mà định dạng không còn nghĩa; chọn bỏ hẳn và lên major 2.
- **Tự đoán khi một node chưa có lựa chọn.** Loại, cùng lý do ADR-0004 loại nó.
