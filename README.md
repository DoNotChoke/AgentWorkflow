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

docker compose up -d                   # postgres, redis, minio, temporal, otel, jaeger
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
| MinIO console | http://localhost:9001 (`minio` / `minio123`) |
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

Lộ trình các phase tiếp theo: xem `docs/adr/0001-*.md` và kế hoạch triển khai.

| Phase | Nội dung |
|---|---|
| 0 | Nền móng, hạ tầng, ranh giới ✅ |
| 1 | Identity, Tenant, Connection, OAuth |
| 2 | Integration Plane, connector đầu tiên (Slack) |
| 3 | Agent Runtime, Model Gateway, Execution Trace |
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
