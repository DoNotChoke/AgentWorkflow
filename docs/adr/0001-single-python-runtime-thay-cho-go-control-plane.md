# ADR 0001 — Dùng một runtime Python duy nhất thay cho Go Control Plane ở giai đoạn đầu

- **Status:** Accepted
- **Date:** 2026-08-18
- **Deciders:** TuanNghia (solo developer)
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §4.2, §4.3, §4.4, §32, §45, §49

## Bối cảnh

Spec kiến trúc quy định Control Plane viết bằng **Go** (§4.2) và AI Plane viết bằng
**Python** (§4.3), giao tiếp nội bộ qua gRPC/Protobuf. Đây là lựa chọn hợp lý cho một
team nhiều người với ranh giới sở hữu rõ ràng.

Dự án hiện được phát triển bởi **một người duy nhất**, chưa có người dùng thật, chưa có
ràng buộc throughput nào được đo đạc.

Với cấu hình Go + Python + TypeScript, các hạng mục sau phải được hiện thực hoá **hai
hoặc ba lần**, mỗi lần bằng một ngôn ngữ khác nhau:

- codegen từ JSON Schema (`workflow-schema`, `tool-schema`, `event-schema`);
- middleware xác thực và lan truyền tenant context;
- error model chuẩn hoá (§37);
- khởi tạo và instrument OpenTelemetry;
- data access layer trên cùng một cụm PostgreSQL;
- contract Protobuf giữa Control Plane và Agent Runtime, phải regenerate hai phía mỗi
  lần đổi field;
- toolchain, CI pipeline, image build, và vá bảo mật dependency.

Chi phí này là chi phí cố định phải trả trước, trong khi lợi ích của Go — throughput
tầng API, static typing, single binary — chỉ hiện thực hoá khi có tải thật.

Đồng thời, ba ràng buộc sau là cố định:

1. AI Plane **buộc** phải là Python (LLM SDK, agent tooling).
2. Temporal có Python SDK chính thức, đủ năng lực chạy Generic Workflow Executor (§13.1).
3. Frontend buộc phải là TypeScript (§4.1).

Do đó Python là ngôn ngữ duy nhất phủ được đồng thời Control Plane, AI Plane và Workflow
Worker. Chọn Python cho cả ba làm tổng số ngôn ngữ của hệ thống giảm từ ba xuống hai.

Spec §32 đã dự liệu tình huống này: *"maintain strong logical separation but avoid
premature microservice fragmentation"*, và §45 xếp việc phân rã microservice quy mô lớn
vào nhóm "optional initially".

## Quyết định

Giai đoạn đầu, Control Plane được hiện thực hoá bằng **Python (FastAPI + SQLAlchemy +
Alembic)**, nằm cùng repository và cùng codebase với Agent Runtime và Temporal Worker,
theo mô hình **modular monolith**.

Go và gRPC nội bộ được hoãn lại, không bị loại bỏ.

Ranh giới **logic** của spec được giữ nguyên toàn bộ. Chỉ ranh giới **process và ngôn
ngữ** bị hoãn.

Bốn quy tắc sau là bắt buộc và được kiểm tra tự động trong CI:

1. **Không import chéo domain.** Một domain không được import trực tiếp module của
   domain khác. Giao tiếp liên domain đi qua interface (Protocol) khai báo ở tầng
   `application`. Enforce bằng `import-linter` (`pyproject.toml`, mục
   `[tool.importlinter]`).

2. **Mỗi domain sở hữu một PostgreSQL schema riêng** (§18). Không JOIN chéo domain,
   không ghi vào bảng thuộc domain khác. Đây là điều kiện tiên quyết để tách service về
   sau; nếu vi phạm, việc tách sẽ trở thành viết lại.

3. **Tầng `domain` không phụ thuộc hạ tầng** (§30). Không import PostgreSQL, Redis,
   Temporal, Kafka, HTTP framework, provider SDK, hay cloud SDK. Enforce bằng
   import-linter.

4. **Entrypoint được tách sẵn từ ngày đầu.** Cùng một codebase nhưng deploy thành các
   process độc lập (`entrypoints/http.py`, `entrypoints/agent.py`,
   `entrypoints/worker.py`), cho phép scale độc lập theo §43 ngay lập tức.

## Hệ quả

### Tích cực

- Một toolchain, một CI pipeline, một bộ dependency.
- Schema contract chỉ cần codegen cho Python và TypeScript.
- Không có chi phí bảo trì Protobuf nội bộ ở giai đoạn contract còn biến động mạnh.
- Stack trace liền mạch xuyên Control Plane → Agent Runtime, giảm mạnh thời gian debug.
- Vẫn scale độc lập được theo workload class nhờ tách entrypoint.

### Tiêu cực / chi phí phải trả

- Throughput tầng API thấp hơn Go. Chấp nhận được ở giai đoạn tiền-PMF, và có thể đo
  đạc trước khi trở thành vấn đề.
- Python cần kỷ luật type checking chủ động (mypy strict) để bù cho việc thiếu static
  typing của Go.
- Việc tách sang Go sau này sẽ tốn công port logic, dù ranh giới module làm cho chi phí
  đó có giới hạn và ước lượng được.

### Rủi ro cần theo dõi

**Rủi ro nghiêm trọng nhất của quyết định này là §2.4** — nguyên tắc credential không
bao giờ chạm tới Agent Runtime hay LLM.

Khi Agent Runtime và Connection domain nằm cùng một process, không còn ranh giới mạng
nào ngăn một câu `import` lấy được credential. Ranh giới này phải được thay thế bằng
ranh giới module, và phải được kiểm tra tự động:

- Integration Gateway là **cửa duy nhất** chạm tới credential đã giải mã.
- Agent Runtime chỉ nhận `connection_id`, không bao giờ nhận token.
- Một contract import-linter (`forbidden`) cấm mọi module ngoài
  `domains.integration.infrastructure` import credential store hoặc module crypto.

Nếu kỷ luật này bị phá vỡ, hậu quả không chỉ là khó tách service về sau, mà là một lỗ
hổng bảo mật thực sự.

## Điều kiện xem xét lại

Quyết định này phải được xem xét lại khi xuất hiện **một** trong các tín hiệu sau:

- p99 latency tầng API vượt ngưỡng SLO vì lý do CPU-bound của chính tầng API (không phải
  do downstream), và đã hết dư địa tối ưu ở tầng ứng dụng;
- có người thứ hai tham gia phát triển toàn thời gian, làm cho ranh giới sở hữu độc lập
  trở nên có giá trị thực;
- một domain phát sinh nhu cầu cách ly bảo mật ở mức process hoặc mạng (§32);
- chi phí hạ tầng của tầng API trở thành khoản chi đáng kể trong tổng chi phí vận hành.

Khi tách, thứ tự ưu tiên đề xuất: Control Plane API tách trước (thuần CRUD, ít phụ thuộc
thư viện Python đặc thù), AI Plane giữ nguyên Python vĩnh viễn.

## Phương án đã cân nhắc và loại bỏ

**Giữ nguyên Go + Python theo đúng spec.** Loại bỏ vì chi phí trùng lặp cố định không
tương xứng với lợi ích chưa hiện thực hoá, trong bối cảnh một người phát triển.

**Toàn bộ TypeScript (Node cho cả Control Plane lẫn AI Plane).** Loại bỏ vì hệ sinh thái
AI/LLM tooling của Python vượt trội, và §4.3 chỉ định Python cho AI Plane — đây là ràng
buộc mạnh hơn sự tiện lợi của việc dùng chung ngôn ngữ với frontend.

**Gộp thành monolith không có ranh giới module.** Loại bỏ vì vi phạm §18, §30, §32 và
làm cho việc tách service về sau tương đương viết lại. Ranh giới logic là thứ không được
phép hoãn.

## Ghi chú về những thứ **không** được hoãn cùng

Việc hoãn Go không kéo theo việc hoãn các hạng mục sau. Cả ba đều có chi phí retrofit
rất cao và phải có mặt từ Phase 0:

- **Temporal** là workflow runtime (§47.2);
- **Tenant context** lan truyền xuyên mọi request, event, log, cache key, storage path
  (§19);
- **Tính bất biến của workflow version** sau khi publish (§47.3, §47.4).
