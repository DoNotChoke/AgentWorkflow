# ADR 0010 — Một lần chạy workflow có danh tính riêng, không mượn danh một người

- **Status:** Accepted
- **Date:** 2026-10-06
- **Deciders:** TuanNghia
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §2.4, §13, §19; ADR-0002, ADR-0007,
  ADR-0014; Q5 và Q6 của `docs/plans/wp-b-workflow-ir-va-generic-executor.md`

## Bối cảnh

Node `tool` của một workflow chạy trong Temporal worker. Worker không được có đường nào
tới credential của connection (contract 7), nên nó gọi Integration Gateway của Control
Plane qua HTTP — cùng cửa mà Agent Runtime dùng.

Agent gọi cửa đó bằng access token của người đang chat. Workflow thì không có ai ngồi đó:
nó có thể chạy theo lịch lúc ba giờ sáng, hoặc ngủ ba ngày giữa hai node. Gateway vẫn
phải trả lời được ba câu mà §19 cấm lấy từ thân request: tenant nào, thay cho ai, bằng
connection nào.

## Quyết định

**1. Token của lần chạy là một loại token riêng, ký bằng khoá riêng.**
`issue_run_token` ký bằng `AW_WORKFLOW_TOKEN_SECRET`, loại `typ = "run"`, `pk =
"workflow"`, `sub` là `execution_id`. Worker chỉ giữ khoá này. `decode_access_token`
không nhận nó, và `decode_run_token` không nhận access token: mỗi khoá ký được đúng một
loại. Worker bị chiếm không ký được token mang danh một người.

**2. Token cấp cho từng lời gọi tool.** Sống `workflow_token_ttl_seconds` (mặc định ba
phút). Nó được ký ngay trong activity, nên không nằm trong lịch sử Temporal, và một lần
chạy ngủ dài không giữ bí mật nào.

**3. Gateway đọc mọi thứ từ bản ghi lần chạy.** Với token của lần chạy, tenant, workspace
và người khởi chạy lấy từ hàng `execution.executions` mà `sub` trỏ tới. Header chọn tenant
bị bỏ qua. Lần chạy không ở trạng thái `running` thì token vô dụng, kể cả khi chưa hết
hạn.

**4. Quyền tính theo người khởi chạy, và họ phải còn ở đó.** Gateway phân giải membership
của `initiated_by` như với một người dùng thật. Người đó rời workspace thì lời gọi tool
kế tiếp bị từ chối. Connection riêng chỉ dùng được khi nó là của chính người khởi chạy
(ADR-0014, quyết định 5).

**5. Worker không chọn connection.** Thân request chỉ nói *node nào* đang chạy. Gateway
tra connection trong `connection_bindings` của lần chạy theo node đó; `connection_id` do
worker gửi lên bị bỏ qua. Node không có lựa chọn nào thì bị từ chối.

**6. `principal_kind` là `workflow` suốt đường đi.** Policy vì thế vẫn đòi phê duyệt khi
một tool `DESTRUCTIVE` bị gọi tự động — luật đã có từ Phase 2, giờ mới có principal kích
hoạt nó.

**7. Endpoint của người dùng không đổi.** Chúng giữ `require_user_id` và từ chối token
của lần chạy. Chỉ router Integration dùng `require_principal`.

## Hệ quả

### Tích cực

- Vẫn đúng một tiến trình giải mã được credential.
- Worker không cầm thứ gì cho phép nó hành động ngoài những lần chạy đang `running`, và
  trong một lần chạy thì chỉ với những connection người chạy đã chọn.
- Không có token dài hạn nào để lộ.

### Tiêu cực / chi phí phải trả

- Mỗi lời gọi tool là hai chặng mạng và ba lần đọc database ở Gateway (lần chạy,
  membership, connection).
- Thêm một bí mật phải cấu hình ở hai tiến trình và phải khác `AW_JWT_SECRET`.
  `assert_production_safe` chặn trường hợp trùng.
- Người khởi chạy rời tổ chức thì các lần chạy đang dở của họ hỏng giữa chừng.

### Rủi ro cần theo dõi

- Ai có khoá ký của worker thì gọi được tool thay cho **mọi** lần chạy đang `running`,
  trong giới hạn connection của từng lần chạy.
- Lịch chạy (Phase 7) chưa có: khi đó `initiated_by` là ai phải được quyết lại.

## Điều kiện xem xét lại

- Có lịch chạy hoặc trigger không do người khởi động.
- Cần thu hồi quyền của một lần chạy cụ thể mà không dừng nó.
- Số lần đọc database cho mỗi lời gọi tool thành nút thắt.

## Phương án đã cân nhắc và loại bỏ

1. **Cấp cho lần chạy một access token của người khởi chạy.** Nó lách qua
   `require_user_id` mà không ai thấy, xoá luôn luật đòi phê duyệt, và sống lâu hơn mọi
   phiên đăng nhập.
2. **Worker dùng chung `AW_JWT_SECRET`.** Bớt một biến cấu hình; đổi lại worker ký được
   token của bất kỳ ai.
3. **Tin `connection_id` worker gửi, chỉ kiểm quyền.** Worker khi đó dùng được mọi
   connection dùng chung của workspace, không chỉ những cái người chạy đã chọn.
4. **Worker gọi provider trực tiếp.** Trái contract 7: thêm một tiến trình cầm credential.
