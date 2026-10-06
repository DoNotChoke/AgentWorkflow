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
    CircuitBreakerSpec,
    ConnectorSpec,
    EventCapabilities,
    ResponseEnvelope,
    WebhookCapabilities,
)
from app.domains.integration.domain.tools import (
    ApprovalPolicy,
    FieldMapping,
    IdempotencyPolicy,
    IdempotencyStrategy,
    ItemSelector,
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
        connector_id="notion",
        version="1.2",
        label="Notion",
        base_url="https://api.notion.com/v1",
        auth=AuthSpec(
            kind="oauth2",
            header="authorization",
            prefix="Bearer ",
        ),
        tool_ids=(
            "notion.search",
            "notion.get_page",
            "notion.get_block_children",
            "notion.query_database",
            "notion.list_users",
            "notion.create_page",
            "notion.append_blocks",
            "notion.create_comment",
        ),
        error_mapping={
            "rate_limited": ErrorCategory.RATE_LIMITED,
            "internal_server_error": ErrorCategory.PROVIDER_UNAVAILABLE,
            "service_unavailable": ErrorCategory.PROVIDER_UNAVAILABLE,
            "bad_gateway": ErrorCategory.PROVIDER_UNAVAILABLE,
            "database_connection_unavailable": ErrorCategory.PROVIDER_UNAVAILABLE,
            "conflict_error": ErrorCategory.PROVIDER_UNAVAILABLE,
            "gateway_timeout": ErrorCategory.TIMEOUT,
            "unauthorized": ErrorCategory.CONNECTION_EXPIRED,
            "invalid_grant": ErrorCategory.CONNECTION_EXPIRED,
            "restricted_resource": ErrorCategory.AUTHORIZATION,
            "insufficient_permissions": ErrorCategory.AUTHORIZATION,
            "object_not_found": ErrorCategory.VALIDATION,
            "validation_error": ErrorCategory.VALIDATION,
            "invalid_json": ErrorCategory.VALIDATION,
            "invalid_request": ErrorCategory.VALIDATION,
            "invalid_request_url": ErrorCategory.VALIDATION,
            "missing_version": ErrorCategory.VALIDATION,
        },
        static_headers={
            "notion-version": "2022-06-28",
        },
        response_envelope=ResponseEnvelope(
            success_field="object",
            error_field="code",
            detail_field="message",
            failure_value="error",
        ),
        default_rate_limits=RateLimitProfile(
            provider=RateLimitRule(
                limit=180,
                window_seconds=60,
            ),
            tenant=RateLimitRule(
                limit=90,
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
        circuit_breaker=CircuitBreakerSpec(
            global_failure_threshold=25,
        ),
        notes="Integration chỉ thấy những trang người dùng đã chia sẻ với nó, nên object_not_found thường nghĩa là 'chưa được chia sẻ' chứ không phải 'không tồn tại'. Notion không dùng scope: quyền nằm ở danh sách trang được chia sẻ, chọn lúc chấp thuận.",
    ),
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
        response_envelope=ResponseEnvelope(
            success_field="ok",
            error_field="error",
        ),
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
        circuit_breaker=CircuitBreakerSpec(
            global_failure_threshold=25,
        ),
        notes='Slack trả HTTP 200 kèm {"ok": false} cho mọi thất bại, kể cả rate limit — nên `ok` phải được kiểm trước status code. Bot token không hết hạn, vì vậy token_revoked là tín hiệu duy nhất khi app bị gỡ.',
    ),
)

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        tool_id="notion.append_blocks",
        provider="notion",
        version="2.0",
        description="Viết thêm nội dung vào cuối một trang hoặc một khối Notion. Id của trang cũng chính là id của khối gốc, nên truyền thẳng page_id vào đây. Mỗi phần tử của blocks là một khối, chọn kiểu bằng type: đoạn văn, tiêu đề, gạch đầu dòng, danh sách đánh số, việc cần làm, toggle, trích dẫn hay callout. Chỉ thêm vào cuối, không sửa và không xoá nội dung đang có.",
        input_schema=PayloadSchema(
            version="2.0",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "block_id",
                    "blocks",
                ],
                "properties": {
                    "block_id": {
                        "type": "string",
                        "description": "Id khối hoặc id trang, 32 ký tự hex có hoặc không có dấu gạch nối.",
                        "pattern": "^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$",
                    },
                    "blocks": {
                        "type": "array",
                        "description": "Các khối cần thêm, theo đúng thứ tự.",
                        "minItems": 1,
                        "maxItems": 100,
                        "items": {
                            "$ref": "#/$defs/block",
                        },
                    },
                },
                "$defs": {
                    "block": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "type",
                            "text",
                        ],
                        "properties": {
                            "type": {
                                "description": "Kiểu khối. bulleted_list_item là gạch đầu dòng, numbered_list_item là danh sách đánh số, to_do là ô tích, toggle là khối gập được.",
                                "enum": [
                                    "paragraph",
                                    "heading_1",
                                    "heading_2",
                                    "heading_3",
                                    "bulleted_list_item",
                                    "numbered_list_item",
                                    "to_do",
                                    "toggle",
                                    "quote",
                                    "callout",
                                ],
                            },
                            "text": {
                                "type": "string",
                                "description": "Nội dung khối. Xuống dòng trong cùng một khối thì dùng \n.",
                                "minLength": 1,
                                "maxLength": 2000,
                            },
                            "checked": {
                                "type": "boolean",
                                "description": "Chỉ có ý nghĩa với to_do. Bỏ trống thì ô chưa tích.",
                            },
                        },
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
                    "block_ids",
                ],
                "properties": {
                    "block_ids": {
                        "type": "array",
                        "description": "Id của từng khối vừa được thêm, theo đúng thứ tự đã gửi.",
                        "items": {
                            "type": "string",
                        },
                    },
                },
            },
        ),
        required_scopes=frozenset(),
        side_effect=SideEffect.WRITE,
        retry=RetryPolicy(
            initial_backoff_seconds=1.0,
        ),
        timeout=TimeoutPolicy(
            attempt_seconds=20.0,
        ),
        idempotency=IdempotencyPolicy(
            strategy=IdempotencyStrategy.DEDUP_RECORD,
            required=True,
        ),
        approval=ApprovalPolicy(),
        request=ProviderRequest(
            method="PATCH",
            path="/blocks/{block_id}/children",
            body="json",
            path_params=("block_id",),
        ),
        output_mapping={
            "block_ids": FieldMapping(
                path=(
                    "results",
                    "*",
                    "id",
                ),
            ),
        },
        input_mapping={
            "children": {
                "for_each": "blocks",
                "template": {
                    "object": "block",
                    "type": "{{item.type}}",
                    "{{item.type}}": {
                        "rich_text": [
                            {
                                "text": {
                                    "content": "{{item.text}}",
                                },
                            },
                        ],
                        "checked": "{{item.checked}}",
                    },
                },
            },
        },
        rate_limits=RateLimitProfile(
            tool=RateLimitRule(
                limit=30,
                window_seconds=60,
            ),
        ),
    ),
    ToolSpec(
        tool_id="notion.create_comment",
        provider="notion",
        version="1.0",
        description="Thêm một bình luận vào một trang Notion. Bình luận hiện ở đầu trang chứ không gắn vào một đoạn cụ thể. Dùng khi cần ghi chú mà không muốn sửa nội dung trang.",
        input_schema=PayloadSchema(
            version="1.0",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "page_id",
                    "text",
                ],
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "Id trang, 32 ký tự hex có hoặc không có dấu gạch nối.",
                        "pattern": "^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$",
                    },
                    "text": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 2000,
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
                    "id",
                ],
                "properties": {
                    "id": {
                        "type": "string",
                    },
                    "discussion_id": {
                        "type": "string",
                    },
                },
            },
        ),
        required_scopes=frozenset(),
        side_effect=SideEffect.WRITE,
        retry=RetryPolicy(
            initial_backoff_seconds=1.0,
        ),
        timeout=TimeoutPolicy(
            attempt_seconds=20.0,
        ),
        idempotency=IdempotencyPolicy(
            strategy=IdempotencyStrategy.DEDUP_RECORD,
            required=True,
        ),
        approval=ApprovalPolicy(),
        request=ProviderRequest(
            method="POST",
            path="/comments",
            body="json",
        ),
        output_mapping={
            "id": FieldMapping(
                path=("id",),
            ),
            "discussion_id": FieldMapping(
                path=("discussion_id",),
            ),
        },
        input_mapping={
            "parent": {
                "type": "page_id",
                "page_id": "{{page_id}}",
            },
            "rich_text": [
                {
                    "text": {
                        "content": "{{text}}",
                    },
                },
            ],
        },
        rate_limits=RateLimitProfile(
            tool=RateLimitRule(
                limit=30,
                window_seconds=60,
            ),
        ),
        provider_reference=ProviderReference(
            paths=(("id",),),
        ),
    ),
    ToolSpec(
        tool_id="notion.create_page",
        provider="notion",
        version="2.0",
        description="Tạo một trang mới bên trong một trang Notion có sẵn. Cần id của trang cha — lấy từ notion.search — và trang cha phải nằm trong số những trang người dùng đã chia sẻ với integration. Truyền blocks để viết luôn nội dung; mỗi phần tử là một khối, chọn kiểu bằng type. Không tạo được trang trong database bằng tool này.",
        input_schema=PayloadSchema(
            version="2.0",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "parent_page_id",
                    "title",
                ],
                "properties": {
                    "parent_page_id": {
                        "type": "string",
                        "description": "Id trang cha, 32 ký tự hex có hoặc không có dấu gạch nối.",
                        "pattern": "^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$",
                    },
                    "title": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 2000,
                    },
                    "blocks": {
                        "type": "array",
                        "description": "Nội dung trang, theo đúng thứ tự. Bỏ trống thì tạo trang rỗng.",
                        "maxItems": 100,
                        "items": {
                            "$ref": "#/$defs/block",
                        },
                    },
                },
                "$defs": {
                    "block": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "type",
                            "text",
                        ],
                        "properties": {
                            "type": {
                                "description": "Kiểu khối. bulleted_list_item là gạch đầu dòng, numbered_list_item là danh sách đánh số, to_do là ô tích, toggle là khối gập được.",
                                "enum": [
                                    "paragraph",
                                    "heading_1",
                                    "heading_2",
                                    "heading_3",
                                    "bulleted_list_item",
                                    "numbered_list_item",
                                    "to_do",
                                    "toggle",
                                    "quote",
                                    "callout",
                                ],
                            },
                            "text": {
                                "type": "string",
                                "description": "Nội dung khối. Xuống dòng trong cùng một khối thì dùng \n.",
                                "minLength": 1,
                                "maxLength": 2000,
                            },
                            "checked": {
                                "type": "boolean",
                                "description": "Chỉ có ý nghĩa với to_do. Bỏ trống thì ô chưa tích.",
                            },
                        },
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
                    "id",
                ],
                "properties": {
                    "id": {
                        "type": "string",
                    },
                    "url": {
                        "type": "string",
                    },
                },
            },
        ),
        required_scopes=frozenset(),
        side_effect=SideEffect.WRITE,
        retry=RetryPolicy(
            initial_backoff_seconds=1.0,
        ),
        timeout=TimeoutPolicy(
            attempt_seconds=20.0,
        ),
        idempotency=IdempotencyPolicy(
            strategy=IdempotencyStrategy.DEDUP_RECORD,
            required=True,
        ),
        approval=ApprovalPolicy(),
        request=ProviderRequest(
            method="POST",
            path="/pages",
            body="json",
        ),
        output_mapping={
            "id": FieldMapping(
                path=("id",),
            ),
            "url": FieldMapping(
                path=("url",),
            ),
        },
        input_mapping={
            "parent": {
                "type": "page_id",
                "page_id": "{{parent_page_id}}",
            },
            "properties": {
                "title": {
                    "title": [
                        {
                            "text": {
                                "content": "{{title}}",
                            },
                        },
                    ],
                },
            },
            "children": {
                "for_each": "blocks",
                "template": {
                    "object": "block",
                    "type": "{{item.type}}",
                    "{{item.type}}": {
                        "rich_text": [
                            {
                                "text": {
                                    "content": "{{item.text}}",
                                },
                            },
                        ],
                        "checked": "{{item.checked}}",
                    },
                },
            },
        },
        rate_limits=RateLimitProfile(
            tool=RateLimitRule(
                limit=30,
                window_seconds=60,
            ),
        ),
        provider_reference=ProviderReference(
            paths=(("id",),),
        ),
    ),
    ToolSpec(
        tool_id="notion.get_block_children",
        provider="notion",
        version="1.0",
        description="Đọc nội dung bên trong một trang hoặc một khối Notion. Id của trang cũng chính là id của khối gốc, nên truyền thẳng page_id vào đây. Trả về từng khối theo thứ tự, kèm phần chữ của nó. Khối nào có has_children là true thì nội dung con phải gọi thêm một lần nữa với id của chính khối đó.",
        input_schema=PayloadSchema(
            version="1.0",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "block_id",
                ],
                "properties": {
                    "block_id": {
                        "type": "string",
                        "description": "Id khối hoặc id trang, 32 ký tự hex có hoặc không có dấu gạch nối.",
                        "pattern": "^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$",
                    },
                    "page_size": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 50,
                    },
                    "start_cursor": {
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
                    "results",
                ],
                "properties": {
                    "results": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "id": {
                                    "type": "string",
                                },
                                "type": {
                                    "type": "string",
                                },
                                "text": {
                                    "type": "array",
                                    "description": "Các đoạn chữ của khối, ghép lại theo thứ tự thì ra nội dung khối. Vắng mặt với khối không mang chữ, ví dụ divider hay image.",
                                    "items": {
                                        "type": "string",
                                    },
                                },
                                "has_children": {
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
                    "has_more": {
                        "type": "boolean",
                    },
                },
            },
        ),
        required_scopes=frozenset(),
        side_effect=SideEffect.READ,
        retry=RetryPolicy(
            max_backoff_seconds=20.0,
        ),
        timeout=TimeoutPolicy(),
        idempotency=IdempotencyPolicy(),
        approval=ApprovalPolicy(),
        request=ProviderRequest(
            method="GET",
            path="/blocks/{block_id}/children",
            body="query",
            path_params=("block_id",),
        ),
        output_mapping={
            "results": FieldMapping(
                path=("results",),
                items={
                    "id": ItemSelector(
                        paths=(("id",),),
                    ),
                    "type": ItemSelector(
                        paths=(("type",),),
                    ),
                    "has_children": ItemSelector(
                        paths=(("has_children",),),
                    ),
                    "text": ItemSelector(
                        paths=(
                            (
                                "paragraph",
                                "rich_text",
                                "*",
                                "plain_text",
                            ),
                            (
                                "heading_1",
                                "rich_text",
                                "*",
                                "plain_text",
                            ),
                            (
                                "heading_2",
                                "rich_text",
                                "*",
                                "plain_text",
                            ),
                            (
                                "heading_3",
                                "rich_text",
                                "*",
                                "plain_text",
                            ),
                            (
                                "bulleted_list_item",
                                "rich_text",
                                "*",
                                "plain_text",
                            ),
                            (
                                "numbered_list_item",
                                "rich_text",
                                "*",
                                "plain_text",
                            ),
                            (
                                "to_do",
                                "rich_text",
                                "*",
                                "plain_text",
                            ),
                            (
                                "toggle",
                                "rich_text",
                                "*",
                                "plain_text",
                            ),
                            (
                                "quote",
                                "rich_text",
                                "*",
                                "plain_text",
                            ),
                            (
                                "callout",
                                "rich_text",
                                "*",
                                "plain_text",
                            ),
                            (
                                "code",
                                "rich_text",
                                "*",
                                "plain_text",
                            ),
                        ),
                    ),
                },
            ),
            "next_cursor": FieldMapping(
                path=("next_cursor",),
            ),
            "has_more": FieldMapping(
                path=("has_more",),
            ),
        },
        rate_limits=RateLimitProfile(
            tool=RateLimitRule(
                limit=60,
                window_seconds=60,
            ),
        ),
    ),
    ToolSpec(
        tool_id="notion.get_page",
        provider="notion",
        version="1.0",
        description="Đọc thông tin một trang Notion theo id: tiêu đề, đường dẫn, thời điểm tạo và sửa lần cuối. Không trả về nội dung trong trang — dùng notion.get_block_children với cùng id để đọc phần thân. Lấy id từ notion.search.",
        input_schema=PayloadSchema(
            version="1.0",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "page_id",
                ],
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "Id trang, 32 ký tự hex có hoặc không có dấu gạch nối.",
                        "pattern": "^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$",
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
                    "id",
                ],
                "properties": {
                    "id": {
                        "type": "string",
                    },
                    "url": {
                        "type": "string",
                    },
                    "title": {
                        "type": "array",
                        "description": "Các đoạn của tiêu đề, ghép lại theo thứ tự thì ra tiêu đề đầy đủ. Vắng mặt với dòng của một database: ở đó tiêu đề nằm dưới tên cột do người dùng đặt chứ không dưới khoá `title`. Phần đuôi của `url` vẫn mang tiêu đề.",
                        "items": {
                            "type": "string",
                        },
                    },
                    "created_time": {
                        "type": "string",
                    },
                    "last_edited_time": {
                        "type": "string",
                    },
                    "archived": {
                        "type": "boolean",
                    },
                    "parent_type": {
                        "type": "string",
                    },
                },
            },
        ),
        required_scopes=frozenset(),
        side_effect=SideEffect.READ,
        retry=RetryPolicy(
            max_backoff_seconds=20.0,
        ),
        timeout=TimeoutPolicy(),
        idempotency=IdempotencyPolicy(),
        approval=ApprovalPolicy(),
        request=ProviderRequest(
            method="GET",
            path="/pages/{page_id}",
            body="query",
            path_params=("page_id",),
        ),
        output_mapping={
            "id": FieldMapping(
                path=("id",),
            ),
            "url": FieldMapping(
                path=("url",),
            ),
            "title": FieldMapping(
                path=(
                    "properties",
                    "title",
                    "title",
                    "*",
                    "plain_text",
                ),
            ),
            "created_time": FieldMapping(
                path=("created_time",),
            ),
            "last_edited_time": FieldMapping(
                path=("last_edited_time",),
            ),
            "archived": FieldMapping(
                path=("archived",),
            ),
            "parent_type": FieldMapping(
                path=(
                    "parent",
                    "type",
                ),
            ),
        },
        rate_limits=RateLimitProfile(
            tool=RateLimitRule(
                limit=60,
                window_seconds=60,
            ),
        ),
    ),
    ToolSpec(
        tool_id="notion.list_users",
        provider="notion",
        version="1.0",
        description="Liệt kê thành viên của workspace Notion. Trả về id, tên hiển thị và loại tài khoản (người hay bot). Dùng khi cần đổi tên một người thành id để gán họ vào một thuộc tính người dùng.",
        input_schema=PayloadSchema(
            version="1.0",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "page_size": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 50,
                    },
                    "start_cursor": {
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
                    "results",
                ],
                "properties": {
                    "results": {
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
                                "type": {
                                    "type": "string",
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
                    "has_more": {
                        "type": "boolean",
                    },
                },
            },
        ),
        required_scopes=frozenset(),
        side_effect=SideEffect.READ,
        retry=RetryPolicy(
            max_backoff_seconds=20.0,
        ),
        timeout=TimeoutPolicy(),
        idempotency=IdempotencyPolicy(),
        approval=ApprovalPolicy(),
        request=ProviderRequest(
            method="GET",
            path="/users",
            body="query",
        ),
        output_mapping={
            "results": FieldMapping(
                path=("results",),
                items={
                    "id": ItemSelector(
                        paths=(("id",),),
                    ),
                    "name": ItemSelector(
                        paths=(("name",),),
                    ),
                    "type": ItemSelector(
                        paths=(("type",),),
                    ),
                },
            ),
            "next_cursor": FieldMapping(
                path=("next_cursor",),
            ),
            "has_more": FieldMapping(
                path=("has_more",),
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
        tool_id="notion.query_database",
        provider="notion",
        version="1.0",
        description="Liệt kê các dòng của một database Notion. Trả về id và đường dẫn của từng dòng theo thứ tự mặc định của database; đọc nội dung một dòng bằng notion.get_page với id đó. Lấy id database từ notion.search.",
        input_schema=PayloadSchema(
            version="1.0",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "database_id",
                ],
                "properties": {
                    "database_id": {
                        "type": "string",
                        "description": "Id database, 32 ký tự hex có hoặc không có dấu gạch nối.",
                        "pattern": "^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$",
                    },
                    "page_size": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 50,
                    },
                    "start_cursor": {
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
                    "results",
                ],
                "properties": {
                    "results": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "id": {
                                    "type": "string",
                                },
                                "url": {
                                    "type": "string",
                                },
                                "created_time": {
                                    "type": "string",
                                },
                                "last_edited_time": {
                                    "type": "string",
                                },
                                "archived": {
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
                    "has_more": {
                        "type": "boolean",
                    },
                },
            },
        ),
        required_scopes=frozenset(),
        side_effect=SideEffect.READ,
        retry=RetryPolicy(
            max_backoff_seconds=20.0,
        ),
        timeout=TimeoutPolicy(),
        idempotency=IdempotencyPolicy(),
        approval=ApprovalPolicy(),
        request=ProviderRequest(
            method="POST",
            path="/databases/{database_id}/query",
            body="json",
            path_params=("database_id",),
        ),
        output_mapping={
            "results": FieldMapping(
                path=("results",),
                items={
                    "id": ItemSelector(
                        paths=(("id",),),
                    ),
                    "url": ItemSelector(
                        paths=(("url",),),
                    ),
                    "created_time": ItemSelector(
                        paths=(("created_time",),),
                    ),
                    "last_edited_time": ItemSelector(
                        paths=(("last_edited_time",),),
                    ),
                    "archived": ItemSelector(
                        paths=(("archived",),),
                    ),
                },
            ),
            "next_cursor": FieldMapping(
                path=("next_cursor",),
            ),
            "has_more": FieldMapping(
                path=("has_more",),
            ),
        },
        rate_limits=RateLimitProfile(
            tool=RateLimitRule(
                limit=30,
                window_seconds=60,
            ),
        ),
    ),
    ToolSpec(
        tool_id="notion.search",
        provider="notion",
        version="1.2",
        description="Tìm trang và database trong Notion theo tiêu đề. Chỉ thấy những thứ người dùng đã chia sẻ với integration này — kết quả rỗng thường nghĩa là chưa được chia sẻ, không phải không tồn tại. Bỏ trống `query` để liệt kê mọi thứ đang thấy được. Trả về id, tiêu đề và đường dẫn của từng mục. Việc tìm là khớp gần đúng và có thể ra nhiều mục: đối chiếu `title_text` với tên người dùng nói trước khi dùng `id`.",
        input_schema=PayloadSchema(
            version="1.0",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Khớp theo tiêu đề. Bỏ trống thì trả về mọi mục thấy được.",
                        "maxLength": 500,
                    },
                    "page_size": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 50,
                    },
                    "start_cursor": {
                        "type": "string",
                        "maxLength": 512,
                    },
                },
            },
        ),
        output_schema=PayloadSchema(
            version="1.2",
            json_schema={
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "results",
                ],
                "properties": {
                    "results": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "id": {
                                    "type": "string",
                                },
                                "object": {
                                    "type": "string",
                                },
                                "url": {
                                    "type": "string",
                                },
                                "title": {
                                    "type": "array",
                                    "description": "Các đoạn của tiêu đề, ghép lại theo thứ tự thì ra tiêu đề đầy đủ. Vắng mặt với dòng của một database: ở đó tiêu đề nằm dưới tên cột do người dùng đặt.",
                                    "items": {
                                        "type": "string",
                                    },
                                },
                                "title_text": {
                                    "type": "string",
                                    "description": "Tiêu đề đầy đủ, là các đoạn của `title` ghép lại. Vắng mặt khi mục không có tiêu đề.",
                                },
                                "created_time": {
                                    "type": "string",
                                },
                                "last_edited_time": {
                                    "type": "string",
                                },
                                "archived": {
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
                    "has_more": {
                        "type": "boolean",
                    },
                },
            },
        ),
        required_scopes=frozenset(),
        side_effect=SideEffect.READ,
        retry=RetryPolicy(
            max_backoff_seconds=20.0,
        ),
        timeout=TimeoutPolicy(),
        idempotency=IdempotencyPolicy(),
        approval=ApprovalPolicy(),
        request=ProviderRequest(
            method="POST",
            path="/search",
            body="json",
        ),
        output_mapping={
            "results": FieldMapping(
                path=("results",),
                items={
                    "id": ItemSelector(
                        paths=(("id",),),
                    ),
                    "object": ItemSelector(
                        paths=(("object",),),
                    ),
                    "url": ItemSelector(
                        paths=(("url",),),
                    ),
                    "title": ItemSelector(
                        paths=(
                            (
                                "properties",
                                "title",
                                "title",
                                "*",
                                "plain_text",
                            ),
                            (
                                "title",
                                "*",
                                "plain_text",
                            ),
                        ),
                    ),
                    "title_text": ItemSelector(
                        paths=(
                            (
                                "properties",
                                "title",
                                "title",
                                "*",
                                "plain_text",
                            ),
                            (
                                "title",
                                "*",
                                "plain_text",
                            ),
                        ),
                        join="",
                    ),
                    "created_time": ItemSelector(
                        paths=(("created_time",),),
                    ),
                    "last_edited_time": ItemSelector(
                        paths=(("last_edited_time",),),
                    ),
                    "archived": ItemSelector(
                        paths=(("archived",),),
                    ),
                },
            ),
            "next_cursor": FieldMapping(
                path=("next_cursor",),
            ),
            "has_more": FieldMapping(
                path=("has_more",),
            ),
        },
        rate_limits=RateLimitProfile(
            tool=RateLimitRule(
                limit=30,
                window_seconds=60,
            ),
        ),
    ),
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
                    "ts": ItemSelector(
                        paths=(("ts",),),
                    ),
                    "user": ItemSelector(
                        paths=(("user",),),
                    ),
                    "text": ItemSelector(
                        paths=(("text",),),
                    ),
                    "type": ItemSelector(
                        paths=(("type",),),
                    ),
                    "thread_ts": ItemSelector(
                        paths=(("thread_ts",),),
                    ),
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
                    "id": ItemSelector(
                        paths=(("id",),),
                    ),
                    "name": ItemSelector(
                        paths=(("name",),),
                    ),
                    "is_private": ItemSelector(
                        paths=(("is_private",),),
                    ),
                    "is_archived": ItemSelector(
                        paths=(("is_archived",),),
                    ),
                    "num_members": ItemSelector(
                        paths=(("num_members",),),
                    ),
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
                    "id": ItemSelector(
                        paths=(("id",),),
                    ),
                    "name": ItemSelector(
                        paths=(("name",),),
                    ),
                    "real_name": ItemSelector(
                        paths=(
                            (
                                "profile",
                                "real_name",
                            ),
                        ),
                    ),
                    "is_bot": ItemSelector(
                        paths=(("is_bot",),),
                    ),
                    "deleted": ItemSelector(
                        paths=(("deleted",),),
                    ),
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
