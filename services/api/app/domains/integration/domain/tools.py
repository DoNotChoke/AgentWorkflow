"""Tool"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import quote

from app.core.errors import ValidationError

RequestBody = Literal["query", "form", "json"]

HttpMethod = Literal["GET", "POST", "PATCH"]

#: Chỗ điền trong `ProviderRequest.path`.
_PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}")


def path_placeholders(path: str) -> tuple[str, ...]:
    """Tên các chỗ điền trong một `path`, theo thứ tự xuất hiện."""
    return tuple(_PLACEHOLDER.findall(path))


def fill_path(path: str, values: Mapping[str, str]) -> str:
    """Thay từng chỗ điền bằng giá trị đã percent-encode."""
    return _PLACEHOLDER.sub(lambda m: quote(values[m.group(1)], safe=""), path)


class SideEffect(StrEnum):
    """Phân loại tác động ra bên ngoài"""

    NONE = "none"
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"

    @property
    def mutates(self) -> bool:
        return self in {SideEffect.WRITE, SideEffect.DESTRUCTIVE}


class IdempotencyStrategy(StrEnum):
    NONE = "none"
    PROVIDER_TOKEN = "provider_token"  # noqa: S105 — tên một chiến lược, không phải token
    DEDUP_RECORD = "dedup_record"
    EXTERNAL_LOOKUP = "external_lookup"
    DETERMINISTIC_REFERENCE = "deterministic_reference"


RATE_LIMIT_LEVELS: tuple[str, ...] = (
    "provider",
    "installation",
    "tenant",
    "connection",
    "user",
    "tool",
)


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    limit: int
    window_seconds: int


@dataclass(frozen=True, slots=True)
class RateLimitProfile:
    """Ngưỡng theo từng cấp. `None` ở một cấp nghĩa là cấp đó không được đếm."""

    provider: RateLimitRule | None = None
    installation: RateLimitRule | None = None
    tenant: RateLimitRule | None = None
    connection: RateLimitRule | None = None
    user: RateLimitRule | None = None
    tool: RateLimitRule | None = None

    def merged_over(self, base: RateLimitProfile) -> RateLimitProfile:
        """Gộp profile này lên trên `base`, từng cấp một."""
        return RateLimitProfile(
            provider=self.provider or base.provider,
            installation=self.installation or base.installation,
            tenant=self.tenant or base.tenant,
            connection=self.connection or base.connection,
            user=self.user or base.user,
            tool=self.tool or base.tool,
        )

    def rules(self) -> tuple[tuple[str, RateLimitRule], ...]:
        """Các cấp có ngưỡng, theo thứ tự rộng → hẹp."""
        found = ((level, getattr(self, level)) for level in RATE_LIMIT_LEVELS)
        return tuple((level, rule) for level, rule in found if rule is not None)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 30.0
    multiplier: float = 2.0

    def backoff_for(self, attempt: int) -> float:
        """Thời gian chờ trước lần thử thứ `attempt`"""
        if attempt <= 1:
            return 0.0
        raw = self.initial_backoff_seconds * (self.multiplier ** (attempt - 2))
        return min(raw, self.max_backoff_seconds)


@dataclass(frozen=True, slots=True)
class TimeoutPolicy:
    attempt_seconds: float = 15.0
    total_seconds: float = 60.0


@dataclass(frozen=True, slots=True)
class IdempotencyPolicy:
    strategy: IdempotencyStrategy = IdempotencyStrategy.NONE
    required: bool = False
    ttl_seconds: int = 7 * 24 * 3600

    @property
    def active(self) -> bool:
        return self.strategy is not IdempotencyStrategy.NONE


class ApprovalScope(StrEnum):
    """Ai được ký duyệt"""

    REQUESTER = "requester"
    CONNECTION_OWNER = "connection_owner"
    WORKSPACE_ADMIN = "workspace_admin"
    ORG_OWNER = "org_owner"

    @property
    def strictness(self) -> int:
        """Thứ hạng để so hai phạm vi, càng cao càng chặt."""
        return _SCOPE_STRICTNESS[self]


#: Thứ hạng chặt chẽ của từng phạm vi duyệt. `REQUESTER` lỏng nhất — người vừa
#: yêu cầu tự ký; `ORG_OWNER` chặt nhất.
_SCOPE_STRICTNESS: dict[ApprovalScope, int] = {
    ApprovalScope.REQUESTER: 0,
    ApprovalScope.CONNECTION_OWNER: 1,
    ApprovalScope.WORKSPACE_ADMIN: 2,
    ApprovalScope.ORG_OWNER: 3,
}


@dataclass(frozen=True, slots=True)
class ApprovalPolicy:
    required: bool = False
    scope: ApprovalScope = ApprovalScope.REQUESTER


@dataclass(frozen=True, slots=True)
class PayloadSchema:
    """JSON Schema kèm version riêng"""

    version: str
    json_schema: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """Hình dạng lời gọi HTTP."""

    method: HttpMethod
    path: str
    body: RequestBody

    #: Tên các placeholder `{...}` trong `path`, theo thứ tự xuất hiện.
    path_params: tuple[str, ...] = ()


#: Segment nhận mọi phần tử của một mảng và chiếu tiếp phần đuôi lên từng cái.
#: Kết quả là một danh sách. Cần nó vì giá trị dùng được thường nằm *trong* một
#: mảng chứ không phải chính mảng đó: một đoạn văn Notion là
#: `rich_text[].plain_text`, và lấy nguyên `rich_text` là trả về cả khối
#: annotation, màu, href — thứ không ai đọc.
WILDCARD = "*"


@dataclass(frozen=True, slots=True)
class ItemSelector:
    """Chỗ lấy một trường của phần tử. Đường đầu tiên tới nơi sẽ thắng."""

    paths: tuple[tuple[str, ...], ...]
    #: Có thì một mảng chuỗi được ghép lại thành một chuỗi bằng dấu nối này.
    join: str | None = None


@dataclass(frozen=True, slots=True)
class FieldMapping:
    """Một trường đầu ra, và chỗ lấy nó trong phản hồi của provider."""

    path: tuple[str, ...]
    items: Mapping[str, ItemSelector] | None = None


@dataclass(frozen=True, slots=True)
class ProviderReference:
    """Cách dựng định danh mà provider cấp cho đối tượng vừa tạo."""

    paths: tuple[tuple[str, ...], ...]
    separator: str = ":"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Một hành động mà Connector phơi ra."""

    tool_id: str
    provider: str
    version: str
    description: str
    input_schema: PayloadSchema
    output_schema: PayloadSchema
    required_scopes: frozenset[str]
    side_effect: SideEffect
    retry: RetryPolicy
    timeout: TimeoutPolicy
    idempotency: IdempotencyPolicy
    approval: ApprovalPolicy
    request: ProviderRequest
    output_mapping: Mapping[str, FieldMapping]

    #: Template dựng thân request từ input, hoặc `None` để gửi thẳng input đi.
    #:
    #: `None` đủ cho provider nhận thân phẳng. Provider đòi cây lồng nhau thì
    #: khai template ở đây, để `input_schema` giữ được hình dạng mà một LLM
    #: viết đúng được — xem `domain/template.py`.
    input_mapping: Mapping[str, Any] | None = None
    rate_limits: RateLimitProfile = field(default_factory=RateLimitProfile)
    permissions: frozenset[str] = frozenset()
    provider_reference: ProviderReference | None = None
    enabled: bool = True

    @property
    def major(self) -> str:
        """Major của version — khoá tương thích mà registry dùng (§41)."""
        return self.version.split(".", 1)[0]

    @property
    def qualified(self) -> str:
        return f"{self.tool_id}@{self.version}"

    def missing_permissions(self, roles: Iterable[str]) -> frozenset[str]:
        """Vai trò còn thiếu để gọi được tool này.

        Rỗng nghĩa là đủ quyền. Trả về tập còn thiếu thay vì một `bool` để
        thông điệp lỗi nói được *thiếu cái gì* — cùng cách mà
        `Connection.missing_scopes` làm.
        """
        return self.permissions - frozenset(roles)


def parse_version(raw: str) -> tuple[int, int]:
    """Tách `"1.2"` thành `(1, 2)` để so sánh theo số, không theo chuỗi."""
    parts = raw.split(".")
    if len(parts) != 2:
        raise ValidationError(f"Version tool phải có dạng 'major.minor', nhận được {raw!r}.")
    try:
        major, minor = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValidationError(
            f"Version tool phải là hai số nguyên ngăn bởi dấu chấm, nhận được {raw!r}."
        ) from exc
    if major < 0 or minor < 0:
        raise ValidationError(f"Version tool không được âm, nhận được {raw!r}.")
    return major, minor
