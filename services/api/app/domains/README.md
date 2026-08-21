# Domains — bounded context

Mỗi thư mục ở đây là một **bounded context** theo §18 và §30 của
`AgentWorkflow_SYSTEM_DESIGN.md`. Cấu trúc này là thứ giữ cho ADR-0001 (gộp
Control Plane vào Python) không biến thành một monolith rối.

## Cấu trúc mỗi domain

```
<domain>/
├── domain/           # Thực thể, value object, quy tắc nghiệp vụ. Thuần Python.
├── application/      # Use case, orchestration, khai báo interface (Protocol).
└── infrastructure/   # Repository, adapter provider, HTTP router, Temporal activity.
```

Hướng phụ thuộc, không bao giờ ngược lại:

```
infrastructure  →  application  →  domain
```

## Bốn quy tắc bắt buộc

Cả bốn đều được `lint-imports` kiểm tra trong CI. Chúng không phải khuyến nghị.

**1. Không import chéo domain.**
`workflow` không được `from app.domains.connection.infrastructure import ...`.
Cần dữ liệu của domain khác thì khai báo một `Protocol` ở `application/` của
domain mình, rồi cho tầng ngoài inject hiện thực vào.

**2. Tầng `domain` không biết gì về hạ tầng.**
Không import SQLAlchemy, Redis, Temporal, FastAPI, httpx, boto3, hay SDK của
provider. Nếu một quy tắc nghiệp vụ cần chạm database thì quy tắc đó thuộc về
`application/`, không thuộc `domain/`.

**3. Mỗi domain sở hữu một PostgreSQL schema riêng.**
Model khai báo `__table_args__ = {"schema": "<domain>"}`. Không JOIN chéo
schema. Không ghi vào bảng của domain khác. Đây là điều kiện để tách service
sau này mà không phải viết lại tầng dữ liệu.

**4. Credential chỉ tồn tại bên trong Integration Plane.**
`app.core.crypto` và credential store chỉ được import bởi
`connection.infrastructure` và Integration Gateway. Agent Runtime chỉ nhận
`connection_id`.

Quy tắc 4 là quy tắc quan trọng nhất. Xem ADR-0001 mục "Rủi ro cần theo dõi" —
khi Agent và Connection nằm chung process, ranh giới module là thứ **duy nhất**
còn lại bảo vệ §2.4.

## Danh sách domain và phase tương ứng

| Domain | Sở hữu | Phase |
|---|---|---|
| `identity` | user, xác thực, session | 1 |
| `tenant` | organization, workspace, membership, role | 1 |
| `connection` | connector installation, OAuth state, credential mã hoá, scope | 1 |
| `integration` | tool registry, policy engine, integration gateway | 2 |
| `agent` | agent, conversation, message, cấu hình agent | 3 |
| `trace` | execution trace, trace step, tool invocation metadata | 3 |
| `workflow` | workflow definition, draft, version, trạng thái validate | 4–6 |
| `execution` | execution record, node execution, trạng thái hiển thị | 4 |
| `approval` | yêu cầu và quyết định phê duyệt của con người | 4 |
| `usage` | usage ledger, hạn mức | 7 |
| `billing` | billable event, plan limit, tham chiếu nhà cung cấp thanh toán | 7 |
| `audit` | bản ghi audit append-only | 7 |
| `notification` | điều phối thông báo | 7 |

Thư mục được tạo sẵn đầy đủ từ Phase 0 dù phần lớn còn rỗng — để ranh giới
được `import-linter` enforce ngay từ dòng code đầu tiên, thay vì được thêm vào
sau khi vi phạm đã kịp tích tụ.
