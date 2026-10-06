# ADR 0011 — Worker gọi model trực tiếp cho node `llm`

- **Status:** Accepted
- **Date:** 2026-10-05
- **Deciders:** TuanNghia
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §2.4, §14, §47; ADR-0002, ADR-0003,
  ADR-0015, ADR-0016; Q4 của `docs/plans/wp-b-workflow-ir-va-generic-executor.md`

## Bối cảnh

Workflow Compiler lắp ra node `llm`: một bước của workflow mà nội dung do model soạn từ
dữ liệu của lần chạy (tóm tắt, viết lại). Trong kb8 nó nằm trong thân một vòng lặp, nên
chạy một lần cho mỗi phần tử.

Executor chạy trong Temporal worker. Model Gateway thì thuộc domain `agent`, và cho tới
giờ chỉ tiến trình Agent Runtime nạp nó cùng API key của provider model.

Kế hoạch WP-B (Q4) nghiêng về việc worker gọi Agent Runtime qua HTTP, cho giống cách nó
sẽ gọi Integration Gateway. Lý do lúc đó là giữ hình dạng *"tiến trình này không import
tiến trình kia"*.

## Quyết định

**Worker nạp Model Gateway và gọi provider model trực tiếp.**

- Domain `workflow` khai cổng `NodeModel`. `entrypoints/worker_wiring.py` hiện thực nó
  bằng `ModelClient` của domain `agent` — cùng cách `compiler_wiring.py` nối compiler với
  Model Gateway. Domain `workflow` không import domain `agent`.
- Tiến trình worker giữ API key của provider model. Nó vẫn **không** có đường nào tới
  credential của connection (Slack, Notion…): contract 7 giữ nguyên.
- Node `llm` chạy trong activity `run_llm_node`, trên queue `ai-tasks`.
- Phạm vi của node giữ hẹp như WP-B đã định: một `instructions`, dữ liệu vào là JSON, một
  `output_schema`. Không tool nào ngoài tool nộp kết quả, không hội thoại nhiều vòng.
- Model nộp kết quả qua một tool mà `input_schema` là `output_schema` của node. Kết quả
  sai kiểu thì hỏi lại model ngay trong activity, tối đa `workflow_llm_max_attempts` lần
  tính cả lần đầu (mặc định 3). Lỗi tạm thời của provider thì để Temporal thử lại.
- Token đã dùng được ghi vào bản ghi của node (`node_executions.usage`).
- Prompt hệ thống của node là file có version (`llm_node.v1.md`), có test vân tay.

## Hệ quả

### Tích cực

- Một chặng mạng thay vì hai cho mỗi lời gọi model, và Agent Runtime không trở thành điểm
  hỏng chung của mọi workflow đang chạy.
- Thử lại, timeout và heartbeat của lời gọi model nằm trong tay Temporal.
- Không phải thêm cho Agent Runtime một endpoint *"chạy một prompt rời"* cùng cơ chế xác
  thực giữa hai tiến trình.

### Tiêu cực / chi phí phải trả

- API key của provider model nằm ở hai tiến trình thay vì một. Lộ worker là lộ key đó.
- Cấu hình model (key, catalog) phải có ở cả hai nơi triển khai.
- Hình dạng *"worker gọi ra ngoài qua HTTP"* của Q5 không còn đúng cho mọi thứ: tool đi
  qua Control Plane, model thì không.

### Rủi ro cần theo dõi

- Chi phí: node `llm` trong vòng lặp nhân với số phần tử, rồi nhân với số lần hỏi lại.
  Hiện mới có trần số phần tử (`max_items`) và trần số lần hỏi lại; chưa có ngân sách theo
  workflow hay theo tenant.
- Dữ liệu của lần chạy đi thẳng tới provider model. Prompt bảo model coi nội dung đó là
  dữ liệu chứ không phải chỉ dẫn, nhưng đó là lời dặn, không phải hàng rào.

## Điều kiện xem xét lại

- Có yêu cầu key model chỉ được nằm ở một tiến trình (kiểm toán, khách hàng doanh nghiệp).
- Rate limit của provider phải được điều phối chung giữa chat và workflow: khi đó một
  tiến trình giữ quota hợp lý hơn hai.
- Node `llm` cần gọi tool hoặc nhiều vòng: đó là việc của Agent Runtime, và lúc đó nên
  gọi sang nó thay vì nới node.

## Phương án đã cân nhắc và loại bỏ

1. **Worker gọi Agent Runtime qua HTTP** (hướng Q4 từng nghiêng về). Giữ key ở một nơi,
   đổi lại thêm một chặng mạng, một endpoint mới và một lớp xác thực giữa hai tiến trình
   — cho một lời gọi vốn không cần gì của Agent ngoài chính Model Gateway.
2. **Đưa Model Gateway lên `core`.** Nó kéo theo các kiểu của `agent.application.ports`,
   và mở rộng `core` vì tiện là thứ kế hoạch WP-B đã cảnh báo.
3. **Chép Model Gateway sang domain `workflow`.** Hai bản catalog model lệch nhau là
   chuyện của vài tháng.
