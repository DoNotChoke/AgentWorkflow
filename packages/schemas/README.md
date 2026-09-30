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

Mỗi thư mục có script sinh riêng, chạy tay, có `--check` để CI bắt lệch:

```bash
python packages/schemas/tool/build_catalog.py        # tool/ → catalog Python
python packages/schemas/workflow/build_ir_schema.py  # workflow/ → schema IR dạng Python
```

Cả hai sinh **dữ liệu**, không sinh kiểu — kiểu viết tay trong domain, lý do ở
`tool/README.md`. TypeScript cho editor sinh ở Phase 6.

Code sinh ra **phải** được commit, để build không phụ thuộc vào việc chạy đúng
thứ tự các bước sinh code.

## Trạng thái hiện tại

`tool/` — **xong ở Phase 2.** Định nghĩa Tool (§6.2) và Connector (§6.1), cùng
connector Slack đầu tiên với bốn tool. Xem [`tool/README.md`](tool/README.md).

`event/` — trống, Phase 7 cùng với §33.

`workflow/` — **IR v2, Phase 4** (v1 vẫn đọc được; v2 bỏ connection khỏi IR, xem
ADR-0014). Năm node (`trigger`, `tool`, `condition`,
`llm`, `loop`), năm dạng binding, và sáu ví dụ viết tay từ trace thật của GĐ 0. Xem [`workflow/README.md`](workflow/README.md).

IR v1 được chốt **sau** khi đọc trace thật, đúng như cảnh báo ở đây trước đó:
chốt quá sớm là chốt một contract sai mà đã bị quy tắc bất biến đóng băng. Hai node (`llm`, `loop`) và hai dạng binding (`where`,
`from_item`) có mặt chỉ vì trace thật đòi chúng; `transform` thì ngược lại — sáu
ví dụ cho thấy không cái nào cần tới nó, nên nó rời v1 và quay lại ở WP-C dưới
dạng code chạy trong sandbox.
