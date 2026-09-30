# AgentWorkflow

Nền tảng automation multi-tenant. Người dùng làm mẫu một tác vụ bằng ngôn ngữ
tự nhiên với AI Agent, hệ thống ghi lại toàn bộ quá trình thực thi, rồi biên
dịch nó thành một Workflow tái sử dụng được, chạy bền vững.

```
Yêu cầu bằng ngôn ngữ tự nhiên
        ↓
Agent thực thi                    ← xác suất, LLM điều khiển
        ↓
Execution Trace có cấu trúc
        ↓
Chuẩn hoá → Tổng quát hoá → Biên dịch
        ↓
Workflow IR tái sử dụng được
        ↓
Workflow version bất biến
        ↓
Thực thi bền vững trên Temporal   ← quyết định, retry được, quan sát được
```

Thiết kế đầy đủ: [`AgentWorkflow_SYSTEM_DESIGN.md`](AgentWorkflow_SYSTEM_DESIGN.md).
Quyết định kiến trúc: [`docs/adr/`](docs/adr/).

---

## Yêu cầu

- Python 3.13
- Docker Desktop
- Node.js 20+ (từ Phase 1, cho frontend)

## Khởi động

```bash
cp .env.example .env

python -m venv .venv
.venv\Scripts\activate                 # Windows
pip install -r requirements.txt -r requirements-dev.txt

docker compose up -d                   # postgres, redis, s3, temporal, otel, jaeger
alembic upgrade head                   # tạo schema cho từng domain
```

Chạy ứng dụng từ venv để có hot reload:

```bash
uvicorn app.entrypoints.http:app  --reload --app-dir services/api               # :8000
uvicorn app.entrypoints.agent:app --reload --app-dir services/api --port 8001  # :8001

# Temporal worker — cần PYTHONPATH vì chạy bằng -m
$env:PYTHONPATH = "services/api"; python -m app.entrypoints.worker   # PowerShell
PYTHONPATH=services/api python -m app.entrypoints.worker             # bash
```

Hoặc chạy tất cả trong container:

```bash
docker compose --profile app up -d --build
```

Kiểm tra:

```bash
curl http://localhost:8000/healthz
curl http://localhost:8000/readyz
```

## Bảng cổng dịch vụ

| Dịch vụ | Địa chỉ |
|---|---|
| Control Plane API | http://localhost:8000 (docs tại `/docs`) |
| Agent Runtime | http://localhost:8001 |
| Temporal UI | http://localhost:8233 |
| Jaeger UI | http://localhost:16686 |
| S3 console (RustFS) | http://localhost:9001/rustfs/console/ (`minio` / `minio123`) |
| PostgreSQL | `localhost:5432` (`agentworkflow` / `agentworkflow`) |
| Redis | `localhost:6379` |

## Lệnh phát triển

```bash
pytest                     # test
ruff check . --fix         # lint
ruff format .              # format
mypy services/api          # type check

# kiểm tra ranh giới kiến trúc — cần PYTHONPATH để tìm được package `app`
$env:PYTHONPATH = "services/api"; lint-imports    # PowerShell
PYTHONPATH=services/api lint-imports              # bash
```

### `lint-imports` là lệnh không được bỏ qua

ADR-0001 gộp Control Plane và AI Plane vào cùng một process. Việc đó xoá bỏ
ranh giới mạng vốn ngăn Agent Runtime chạm tới credential của provider.

Thứ thay thế nó là các contract import trong `pyproject.toml`. Nếu chúng không
được chạy trong CI, §2.4 — *credential không bao giờ đến tay Agent hay LLM* —
mất đi cơ chế bảo vệ duy nhất còn lại.

Coi một lần `lint-imports` fail như một lần test bảo mật fail, không phải như
một cảnh báo về phong cách code.

## Cấu trúc repo

```
AgentWorkflow/
├── apps/web/                  Next.js (Phase 1)
├── services/api/              Backend Python
│   ├── alembic/               Migration database
│   ├── app/
│   │   ├── core/              Config, tenant context, error model, DB, crypto, OTel
│   │   ├── domains/           13 bounded context, mỗi cái 3 tầng hexagonal
│   │   └── entrypoints/       http · agent · worker (3 tiến trình, chung codebase)
│   └── tests/
├── packages/schemas/          JSON Schema: Workflow IR, Tool, Event
├── infra/                     Dockerfile, cấu hình otel, temporal
└── docs/adr/                  Architecture Decision Record
```

## Trạng thái

**Phase 0 — hoàn tất.** Hạ tầng, ranh giới kiến trúc, và các mối quan tâm xuyên
suốt (tenant context, error model, logging, tracing, crypto).

**Phase 1 — hoàn tất.** Identity (đăng ký, đăng nhập, refresh token xoay theo
họ), Tenant (organization, workspace, hai tầng membership và RBAC), và
Connection (vòng OAuth 2.0 có PKCE, credential mã hoá envelope, tự làm mới
token, thu hồi). Giao diện web phủ hết cả ba: đăng nhập, onboarding,
workspace, thành viên, kết nối, và chính sách kết nối của organization.

Kết nối có hai trục quyền. **Phạm vi** của từng kết nối: `workspace` là tài
khoản chung do quản trị viên workspace thiết lập, `private` là tài khoản riêng
mà chỉ chính chủ nhìn thấy và dùng được. **Chính sách** của organization quyết
định trục trên nhận được giá trị nào: `open` cho phép thành viên tự thêm tài
khoản riêng, `managed` chỉ cho dùng tài khoản chung. Mặc định là `open` cho org
và `private` cho từng kết nối — hẹp ở chỗ quan trọng, rộng ở chỗ vô hại.

**Phase 2 — hoàn tất.** Integration Plane. Tool và Connector được định nghĩa
bằng JSON Schema trong `packages/schemas/tool/` rồi sinh thành catalog Python
đã commit. Integration Gateway chạy đủ mười bốn trách nhiệm của §6.3: phân giải
kết nối, giải mã credential, kiểm scope, chính sách, idempotency, rate limit sáu
cấp, circuit breaker, retry, chuẩn hoá lỗi, phát trace và usage. Hai connector:
Slack với bốn tool, Notion với tám — đọc và ghi.

Không có module nào mang tên Slack hay Notion. Mọi khác biệt của provider — địa
chỉ gốc, cách gắn token, header bắt buộc, bảng ánh xạ lỗi, quirk trả HTTP 200
kèm `{"ok": false}` của Slack và `{"object": "error"}` của Notion — đều là dữ
liệu trong tài liệu connector. §6 cấm những nhánh điều kiện theo provider, và
một test đọc AST của driver giữ điều đó.

Provider thứ hai không hoàn toàn là "thêm một thư mục JSON": hình dạng tài liệu
trước đó vừa khít với đúng một provider, nên Notion đòi mở rộng nó — header cố
định, cách provider báo lỗi trong thân, hai trường phía OAuth, chỗ điền trong
đường dẫn, hai cách chiếu output mà một đường tĩnh không tới được, và một
template dựng thân request. Cái
*không* phải thêm mới là điều đáng kể: không một nhánh `if` nào trong driver, và
Agent Runtime không đổi một dòng.

Chỗ điền trong `request.path` là ngoại lệ có kiểm soát đầu tiên của §38 và có
ADR riêng: [ADR-0005](docs/adr/0005-tham-so-duong-dan-trong-tai-lieu-tool.md).
Ranh giới SSRF được phát biểu lại thành một câu kiểm chứng được — người gọi đổi
được nội dung của đúng một segment, không đổi được cấu trúc — thay vì "người gọi
không chạm vào URL".

Notion cũng là provider đầu tiên **không dùng scope**: quyền của nó là danh
sách trang người dùng chọn chia sẻ ở màn hình chấp thuận, nằm bên phía Notion.
`ProviderDescriptor.uses_scopes` khai điều đó, và URL uỷ quyền bỏ hẳn tham số
`scope` thay vì gửi một giá trị rỗng.

Ranh giới credential được đính chính ở [ADR-0002](docs/adr/0002-ranh-gioi-credential-nam-o-wiring.md):
nó nằm ở `entrypoints/wiring.py`, không phải ở Integration Plane như ADR-0001
mô tả. Contract 6 cấm Agent Runtime với tới đó, kể cả gián tiếp — nên từ Phase 3,
Agent **buộc** phải gọi Gateway qua HTTP thay vì gọi trong tiến trình.

**Phase 3 — hoàn tất.** Agent Runtime, Model Gateway và Execution Trace. Một lượt
chat là một vòng lặp: hỏi model, gọi tool, hỏi lại model, cho tới khi model thôi
gọi tool. Mỗi vòng để lại một bước trong trace, và bước của Gateway nằm lồng dưới
đúng lời gọi tool đã sinh ra nó. Giao diện web có thư viện Agent và màn hình chat
chạy trên SSE.

Model Gateway đứng trước hai provider — Anthropic và OpenAI — sau một danh mục
`model → provider`. Agent khai tên model; không chỗ nào trong vòng lặp biết SDK
nào sẽ được gọi.

Connection được chọn theo **Conversation**, không theo Agent và không theo người
dùng: [ADR-0004](docs/adr/0004-chon-connection-theo-conversation.md). Agent là tài
nguyên chung của workspace, còn connection riêng thì chỉ chính chủ nhìn thấy —
gán cứng connection vào Agent sẽ để người khác thừa kế một thứ backend biết chắc
họ không có quyền dùng.

**Execution Trace giữ nội dung, không chỉ vân tay.** Mỗi bước — một lần gọi
tool, một lần hỏi model — lưu cái đã gửi đi và cái nhận về. Payload nhỏ nằm
ngay trong `trace.trace_steps`, payload lớn ra object storage (S3) theo khoá là chính mã
băm của nó, payload quá lớn chỉ còn vân tay. Lượt chat vỡ giữa chừng không để
lại tin nhắn nào trong `messages` — trace là nơi duy nhất còn giữ nó. Quyết
định ở [ADR-0006](docs/adr/0006-payload-cua-execution-trace.md), kế hoạch ở
[`docs/plans/`](docs/plans/wp-a-trace-payload-store.md).

**Tool ghi không bao giờ tự chạy lại khi kết quả không rõ.** Sổ idempotency có
thêm trạng thái `unknown` — *có thể đã có tác động* — tách hẳn khỏi `failed` —
*chắc chắn không có*. Mỗi lời gọi giữ một lease, và mọi lần chốt sổ phải mang
đúng token đã giành được. Hết lease mà hàng vẫn `in_flight` nghĩa là người giữ đã
chết giữa chừng: tool đọc tiếp quản và chạy lại, tool ghi thì chuyển hàng sang
`unknown` rồi bảo người gọi đi kiểm tra kết quả, chứ không đăng tin nhắn lần thứ
hai. Quyết định ở [ADR-0007](docs/adr/0007-so-idempotency-co-lease-va-trang-thai-khong-ro.md).

Lộ trình các phase tiếp theo: xem `docs/adr/0001-*.md` và kế hoạch triển khai.

| Phase | Nội dung |
|---|---|
| 0 | Nền móng, hạ tầng, ranh giới ✅ |
| 1 | Identity, Tenant, Connection, OAuth ✅ |
| 2 | Integration Plane, connector đầu tiên (Slack) ✅ |
| 3 | Agent Runtime, Model Gateway, Execution Trace ✅ |
| 4 | Workflow IR, Generic Executor trên Temporal |
| 5 | Workflow Compiler (trace → IR) |
| 6 | Workflow Editor, lifecycle draft/publish |
| 7 | Schedule, webhook, billing, audit, hardening |

## Những ràng buộc không được vi phạm

Trích §47. Vi phạm bất kỳ điều nào phải được ghi lại thành một ADR tường minh,
không được âm thầm thay đổi.

1. Agent Runtime và Workflow Runtime tách rời.
2. Workflow version đã publish là bất biến; execution tham chiếu đúng một version.
3. Credential provider không bao giờ vào prompt LLM.
4. Mọi lời gọi provider đi qua Integration Plane.
5. Side effect ra bên ngoài phải có chiến lược idempotency.
6. PostgreSQL là nguồn chân lý; Redis không phải database của workflow.
7. Multi-tenancy được enforce ở mọi tầng.
8. Phân quyền được enforce ở server.
