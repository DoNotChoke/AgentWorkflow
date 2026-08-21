# packages/schemas — contract dùng chung

Thư mục này là **nguồn chân lý duy nhất** cho các contract đi xuyên nhiều tầng
của hệ thống. Contract được viết bằng JSON Schema, rồi sinh ra type cho Python
và TypeScript — không viết tay hai lần ở hai nơi.

§40 liệt kê những thứ bắt buộc phải có version. Ba nhóm quan trọng nhất nằm ở
đây:

```
schemas/
├── workflow/     # Workflow IR (§11) — contract quan trọng nhất của hệ thống
├── tool/         # Định nghĩa Tool và Connector (§6.1, §6.2)
└── event/        # Domain event (§33)
```

## Vì sao JSON Schema chứ không phải Pydantic hay Zod

Workflow IR được đọc và ghi bởi bốn thành phần khác nhau:

- Workflow Compiler (Python) **sinh** ra nó;
- Validator (Python) **kiểm tra** nó;
- Generic Workflow Executor (Python, trên Temporal) **thực thi** nó;
- Workflow Editor (TypeScript, React Flow) **hiển thị và sửa** nó.

Nếu định nghĩa nằm trong code Python, phía TypeScript sẽ phải chép lại bằng
tay, và hai bản sẽ lệch nhau — thường là lệch âm thầm, phát hiện lúc runtime.
JSON Schema đứng ngoài cả hai ngôn ngữ nên không bên nào là bản sao của bên
kia.

## Quy tắc đánh version

Mỗi schema mang một trường `schema_version` tường minh.

§41 là ràng buộc chi phối: một Workflow đã publish có thể chạy nhiều tháng sau
khi được tạo. Vì vậy:

- thay đổi tương thích ngược (thêm trường tuỳ chọn) → tăng minor;
- thay đổi phá vỡ tương thích → tăng major, và bản major cũ **phải** tiếp tục
  diễn giải được trong suốt thời hạn tương thích đã công bố;
- không bao giờ được âm thầm đổi ngữ nghĩa của một version đã tồn tại (§47).

## Sinh code

```bash
# Python  (Phase 0 chưa cần — thêm khi IR v1 ổn định ở Phase 4)
datamodel-codegen --input schemas/workflow --output ../../services/api/app/generated

# TypeScript
npx json-schema-to-typescript -i schemas/workflow -o ../../apps/web/src/generated
```

Code sinh ra **phải** được commit, để build không phụ thuộc vào việc chạy đúng
thứ tự các bước sinh code.

## Trạng thái hiện tại

Trống. IR v1 được chốt ở **Phase 4** — đó là lúc có đủ Execution Trace thật để
biết cần biểu diễn những gì. Chốt IR quá sớm, trước khi có dữ liệu thật từ
Agent, gần như chắc chắn dẫn tới một contract sai mà lại đã bị đóng băng bởi
quy tắc bất biến ở trên.
