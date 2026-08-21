# Architecture Decision Record

§49 của thiết kế hệ thống yêu cầu: *"document any architecture deviation as an
explicit architecture decision"*. Thư mục này là nơi ghi lại những quyết định
đó.

Một ADR được viết khi quyết định thoả **một** trong các điều kiện:

- lệch khỏi `AgentWorkflow_SYSTEM_DESIGN.md`;
- đắt để đảo ngược về sau;
- người đọc code sáu tháng sau sẽ không đoán ra lý do nếu chỉ nhìn code.

Những thay đổi thông thường không cần ADR. Việc lạm dụng ADR làm cho các quyết
định thật sự quan trọng bị chìm trong tiếng ồn.

| # | Quyết định | Trạng thái |
|---|---|---|
| [0001](0001-single-python-runtime-thay-cho-go-control-plane.md) | Dùng một runtime Python duy nhất thay cho Go Control Plane ở giai đoạn đầu | Accepted |

Mẫu: [`0000-template.md`](0000-template.md).

## Quy ước

- Đánh số tuần tự, không tái sử dụng số.
- ADR đã Accepted thì **không sửa nội dung**. Muốn đổi quyết định thì viết ADR
  mới và đánh dấu ADR cũ là `Superseded by ADR-XXXX`. Cùng tinh thần với quy
  tắc bất biến của Workflow version (§2.3): lịch sử quyết định phải đọc lại
  được đúng như lúc nó được đưa ra.
- Mỗi ADR phải có mục **Điều kiện xem xét lại** với tín hiệu cụ thể, đo được.
  Một quyết định không kèm điều kiện đảo ngược sẽ trở thành giáo điều.
