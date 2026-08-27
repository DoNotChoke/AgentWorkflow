"""SINH TỰ ĐỘNG — đừng sửa file này bằng tay.

Nguồn: `packages/schemas/tool/`
Sinh lại:

    python packages/schemas/tool/build_catalog.py

Sửa tay ở đây sẽ bị lần sinh sau ghi đè, và test đối chiếu sẽ đỏ trước đó.
"""

from __future__ import annotations

from app.core.errors import ErrorCategory
from app.domains.integration.domain.connectors import (
    AuthSpec,
    ConnectorSpec,
    EventCapabilities,
    WebhookCapabilities,
)
from app.domains.integration.domain.tools import (
    ApprovalPolicy,
    FieldMapping,
    IdempotencyPolicy,
    IdempotencyStrategy,
    PayloadSchema,
    ProviderReference,
    ProviderRequest,
    RateLimitProfile,
    RateLimitRule,
    RetryPolicy,
    SideEffect,
    TimeoutPolicy,
    ToolSpec,
)

CONNECTORS: tuple[ConnectorSpec, ...] = (
    ConnectorSpec(
        connector_id="slack",
        version="1.0",
        label="Slack",
        base_url="https://slack.com/api",
        auth=AuthSpec(
            kind="oauth2",
            header="authorization",
            prefix="Bearer ",
        ),
        tool_ids=(
            "slack.list_channels",
            "slack.post_message",
            "slack.list_users",
            "slack.get_channel_history",
        ),
        error_mapping={
            "ratelimited": ErrorCategory.RATE_LIMITED,
            "rate_limited": ErrorCategory.RATE_LIMITED,
            "internal_error": ErrorCategory.PROVIDER_UNAVAILABLE,
            "service_unavailable": ErrorCategory.PROVIDER_UNAVAILABLE,
            "fatal_error": ErrorCategory.PROVIDER_UNAVAILABLE,
            "request_timeout": ErrorCategory.PROVIDER_UNAVAILABLE,
            "invalid_auth": ErrorCategory.CONNECTION_EXPIRED,
            "not_authed": ErrorCategory.CONNECTION_EXPIRED,
            "account_inactive": ErrorCategory.CONNECTION_EXPIRED,
            "token_revoked": ErrorCategory.CONNECTION_EXPIRED,
            "token_expired": ErrorCategory.CONNECTION_EXPIRED,
            "missing_scope": ErrorCategory.MISSING_SCOPE,
            "not_allowed_token_type": ErrorCategory.MISSING_SCOPE,
            "restricted_action": ErrorCategory.AUTHORIZATION,
            "ekm_access_denied": ErrorCategory.AUTHORIZATION,
            "channel_not_found": ErrorCategory.VALIDATION,
            "user_not_found": ErrorCategory.VALIDATION,
            "invalid_arguments": ErrorCategory.VALIDATION,
            "invalid_cursor": ErrorCategory.VALIDATION,
            "invalid_limit": ErrorCategory.VALIDATION,
            "msg_too_long": ErrorCategory.VALIDATION,
            "no_text": ErrorCategory.VALIDATION,
            "not_in_channel": ErrorCategory.VALIDATION,
            "is_archived": ErrorCategory.VALIDATION,
        },
        webhooks=WebhookCapabilities(
            supported=True,
            signature_header="x-slack-signature",
            timestamp_header="x-slack-request-timestamp",
        ),
        events=EventCapabilities(
            supported=True,
            event_types=(
                "message",
                "channel_created",
                "member_joined_channel",
            ),
        ),
        default_rate_limits=RateLimitProfile(
            provider=RateLimitRule(
                limit=600,
                window_seconds=60,
            ),
            tenant=RateLimitRule(
                limit=120,
                window_seconds=60,
            ),
            connection=RateLimitRule(
                limit=60,
                window_seconds=60,
            ),
            user=RateLimitRule(
                limit=60,
                window_seconds=60,
            ),
        ),
        notes='Slack trả HTTP 200 kèm {"ok": false} cho mọi thất bại, kể cả rate limit — nên `ok` phải được kiểm trước status code. Bot token không hết hạn, vì vậy token_revoked là tín hiệu duy nhất khi app bị gỡ.',
    ),
)

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        tool_id="slack.get_channel_history",
        provider="slack",
        version="1.0",
        description="Đọc các tin nhắn gần nhất của một kênh Slack, mới nhất trước. Nhận id kênh chứ không nhận tên. Dùng khi cần tóm tắt một cuộc trao đổi hoặc tìm lại điều đã được nói trong kênh.",
        input_schema=PayloadSchema(
            version="1.0",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "channel",
                ],
                "properties": {
                    "channel": {
                        "type": "string",
                        "pattern": "^[CGD][A-Z0-9]{2,}$",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 50,
                    },
                    "cursor": {
                        "type": "string",
                        "maxLength": 512,
                    },
                    "oldest": {
                        "type": "string",
                        "description": "Chỉ lấy tin nhắn sau dấu thời gian này.",
                        "pattern": "^[0-9]+(\\.[0-9]+)?$",
                    },
                    "latest": {
                        "type": "string",
                        "description": "Chỉ lấy tin nhắn trước dấu thời gian này.",
                        "pattern": "^[0-9]+(\\.[0-9]+)?$",
                    },
                },
            },
        ),
        output_schema=PayloadSchema(
            version="1.0",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "messages",
                ],
                "properties": {
                    "messages": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "ts": {
                                    "type": "string",
                                },
                                "user": {
                                    "type": "string",
                                },
                                "text": {
                                    "type": "string",
                                },
                                "type": {
                                    "type": "string",
                                },
                                "thread_ts": {
                                    "type": "string",
                                },
                            },
                        },
                    },
                    "has_more": {
                        "type": [
                            "boolean",
                            "null",
                        ],
                    },
                    "next_cursor": {
                        "type": [
                            "string",
                            "null",
                        ],
                    },
                },
            },
        ),
        required_scopes=frozenset({"channels:history"}),
        side_effect=SideEffect.READ,
        retry=RetryPolicy(
            max_backoff_seconds=20.0,
        ),
        timeout=TimeoutPolicy(
            attempt_seconds=20.0,
            total_seconds=90.0,
        ),
        idempotency=IdempotencyPolicy(),
        approval=ApprovalPolicy(),
        request=ProviderRequest(
            method="GET",
            path="/conversations.history",
            body="query",
        ),
        output_mapping={
            "messages": FieldMapping(
                path=("messages",),
                items={
                    "ts": ("ts",),
                    "user": ("user",),
                    "text": ("text",),
                    "type": ("type",),
                    "thread_ts": ("thread_ts",),
                },
            ),
            "has_more": FieldMapping(
                path=("has_more",),
            ),
            "next_cursor": FieldMapping(
                path=(
                    "response_metadata",
                    "next_cursor",
                ),
            ),
        },
        rate_limits=RateLimitProfile(
            tool=RateLimitRule(
                limit=50,
                window_seconds=60,
            ),
        ),
    ),
    ToolSpec(
        tool_id="slack.list_channels",
        provider="slack",
        version="1.0",
        description="Liệt kê các kênh trong workspace Slack. Trả về id, tên và trạng thái riêng tư của từng kênh, kèm con trỏ để lấy trang tiếp theo. Dùng khi cần tìm id của một kênh từ tên của nó trước khi gửi tin nhắn hay đọc lịch sử.",
        input_schema=PayloadSchema(
            version="1.0",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 100,
                    },
                    "cursor": {
                        "type": "string",
                        "maxLength": 512,
                    },
                    "exclude_archived": {
                        "type": "boolean",
                        "default": True,
                    },
                    "types": {
                        "type": "string",
                        "description": "Danh sách ngăn bởi dấu phẩy: public_channel, private_channel, mpim, im.",
                        "pattern": "^[a-z_]+(,[a-z_]+)*$",
                        "default": "public_channel",
                    },
                },
            },
        ),
        output_schema=PayloadSchema(
            version="1.0",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "channels",
                ],
                "properties": {
                    "channels": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "id": {
                                    "type": "string",
                                },
                                "name": {
                                    "type": "string",
                                },
                                "is_private": {
                                    "type": "boolean",
                                },
                                "is_archived": {
                                    "type": "boolean",
                                },
                                "num_members": {
                                    "type": "integer",
                                },
                            },
                        },
                    },
                    "next_cursor": {
                        "type": [
                            "string",
                            "null",
                        ],
                    },
                },
            },
        ),
        required_scopes=frozenset({"channels:read"}),
        side_effect=SideEffect.READ,
        retry=RetryPolicy(
            max_backoff_seconds=20.0,
        ),
        timeout=TimeoutPolicy(),
        idempotency=IdempotencyPolicy(),
        approval=ApprovalPolicy(),
        request=ProviderRequest(
            method="GET",
            path="/conversations.list",
            body="query",
        ),
        output_mapping={
            "channels": FieldMapping(
                path=("channels",),
                items={
                    "id": ("id",),
                    "name": ("name",),
                    "is_private": ("is_private",),
                    "is_archived": ("is_archived",),
                    "num_members": ("num_members",),
                },
            ),
            "next_cursor": FieldMapping(
                path=(
                    "response_metadata",
                    "next_cursor",
                ),
            ),
        },
        rate_limits=RateLimitProfile(
            tool=RateLimitRule(
                limit=20,
                window_seconds=60,
            ),
        ),
    ),
    ToolSpec(
        tool_id="slack.list_users",
        provider="slack",
        version="1.0",
        description="Liệt kê thành viên của workspace Slack. Trả về id, tên đăng nhập và tên hiển thị, kèm cờ cho biết đó là bot hay tài khoản đã bị xoá. Dùng khi cần đổi tên một người thành id để nhắc tới họ trong tin nhắn.",
        input_schema=PayloadSchema(
            version="1.0",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 200,
                        "default": 100,
                    },
                    "cursor": {
                        "type": "string",
                        "maxLength": 512,
                    },
                },
            },
        ),
        output_schema=PayloadSchema(
            version="1.0",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "members",
                ],
                "properties": {
                    "members": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "id": {
                                    "type": "string",
                                },
                                "name": {
                                    "type": "string",
                                },
                                "real_name": {
                                    "type": "string",
                                },
                                "is_bot": {
                                    "type": "boolean",
                                },
                                "deleted": {
                                    "type": "boolean",
                                },
                            },
                        },
                    },
                    "next_cursor": {
                        "type": [
                            "string",
                            "null",
                        ],
                    },
                },
            },
        ),
        required_scopes=frozenset({"users:read"}),
        side_effect=SideEffect.READ,
        retry=RetryPolicy(
            max_backoff_seconds=20.0,
        ),
        timeout=TimeoutPolicy(),
        idempotency=IdempotencyPolicy(),
        approval=ApprovalPolicy(),
        request=ProviderRequest(
            method="GET",
            path="/users.list",
            body="query",
        ),
        output_mapping={
            "members": FieldMapping(
                path=("members",),
                items={
                    "id": ("id",),
                    "name": ("name",),
                    "real_name": (
                        "profile",
                        "real_name",
                    ),
                    "is_bot": ("is_bot",),
                    "deleted": ("deleted",),
                },
            ),
            "next_cursor": FieldMapping(
                path=(
                    "response_metadata",
                    "next_cursor",
                ),
            ),
        },
        rate_limits=RateLimitProfile(
            tool=RateLimitRule(
                limit=20,
                window_seconds=60,
            ),
        ),
    ),
    ToolSpec(
        tool_id="slack.post_message",
        provider="slack",
        version="1.0",
        description="Gửi một tin nhắn văn bản vào một kênh Slack. Nhận id kênh (ví dụ C0123ABCD) chứ không nhận tên kênh — dùng slack.list_channels để đổi tên thành id trước. Trả về dấu thời gian của tin nhắn, thứ dùng để trả lời trong luồng hoặc để xoá sau này.",
        input_schema=PayloadSchema(
            version="1.0",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "channel",
                    "text",
                ],
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "Id kênh, không phải tên kênh.",
                        "pattern": "^[CGD][A-Z0-9]{2,}$",
                    },
                    "text": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 4000,
                    },
                    "thread_ts": {
                        "type": "string",
                        "description": "Trả lời trong luồng của tin nhắn có dấu thời gian này.",
                        "pattern": "^[0-9]+\\.[0-9]+$",
                    },
                },
            },
        ),
        output_schema=PayloadSchema(
            version="1.0",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "channel_id",
                    "message_ts",
                ],
                "properties": {
                    "channel_id": {
                        "type": "string",
                    },
                    "message_ts": {
                        "type": "string",
                    },
                },
            },
        ),
        required_scopes=frozenset({"chat:write"}),
        side_effect=SideEffect.WRITE,
        retry=RetryPolicy(
            initial_backoff_seconds=1.0,
        ),
        timeout=TimeoutPolicy(),
        idempotency=IdempotencyPolicy(
            strategy=IdempotencyStrategy.DEDUP_RECORD,
            required=True,
        ),
        approval=ApprovalPolicy(),
        request=ProviderRequest(
            method="POST",
            path="/chat.postMessage",
            body="json",
        ),
        output_mapping={
            "channel_id": FieldMapping(
                path=("channel",),
            ),
            "message_ts": FieldMapping(
                path=("ts",),
            ),
        },
        rate_limits=RateLimitProfile(
            tool=RateLimitRule(
                limit=60,
                window_seconds=60,
            ),
        ),
        provider_reference=ProviderReference(
            paths=(
                ("channel",),
                ("ts",),
            ),
        ),
    ),
)
