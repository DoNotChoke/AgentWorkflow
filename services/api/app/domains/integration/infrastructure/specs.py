"""Đọc tài liệu JSON của `packages/schemas/tool/` thành value object.

Module này **không chạy lúc ứng dụng khởi động**. Nó chỉ được gọi bởi hai chỗ:
`packages/schemas/tool/build_catalog.py` lúc sinh code, và test đối chiếu bản
sinh ra với JSON gốc. Lúc chạy thật, catalog đã là Python trong
`domain/generated/catalog_v1.py`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import jsonschema

from app.core.errors import ErrorCategory, ValidationError
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
    ApprovalScope,
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
    parse_version,
    path_placeholders,
)

TOOL_META_SCHEMA = "tool.schema.json"
CONNECTOR_META_SCHEMA = "connector.schema.json"

#: Version của định dạng tài liệu mà parser này hiểu.
SUPPORTED_DOCUMENT_MAJOR = 1


def _read_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValidationError(f"Không đọc được {path}: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path} không phải JSON hợp lệ: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValidationError(f"{path} phải là một object JSON.")
    return cast(dict[str, Any], parsed)


def _validate(document: Mapping[str, Any], meta_schema: Mapping[str, Any], where: str) -> None:
    try:
        jsonschema.validate(instance=document, schema=meta_schema)
    except jsonschema.ValidationError as exc:
        location = "/".join(str(part) for part in exc.absolute_path) or "<gốc>"
        raise ValidationError(f"{where} sai schema tại {location}: {exc.message}") from exc


def _check_document_version(document: Mapping[str, Any], where: str) -> None:
    major, _ = parse_version(str(document["schema_version"]))
    if major != SUPPORTED_DOCUMENT_MAJOR:
        raise ValidationError(
            f"{where} dùng định dạng tài liệu major {major}, parser này chỉ đọc được "
            f"major {SUPPORTED_DOCUMENT_MAJOR}."
        )


# ─── Mảnh dùng chung ─────────────────────────────────────────────────────────


def _rate_limit_profile(raw: Mapping[str, Any] | None) -> RateLimitProfile:
    if not raw:
        return RateLimitProfile()
    levels = {
        level: RateLimitRule(limit=int(rule["limit"]), window_seconds=int(rule["window_seconds"]))
        for level, rule in raw.items()
    }
    return RateLimitProfile(**levels)


def _path(raw: Sequence[Any]) -> tuple[str, ...]:
    return tuple(str(part) for part in raw)


def _selector(raw: Sequence[Any] | Mapping[str, Any]) -> ItemSelector:
    """Một đường hay một danh sách đường — chuẩn hoá về cùng một hình.

    Meta-schema phân biệt hai dạng bằng kiểu của giá trị: mảng chuỗi là một
    đường, object có `any_of` là nhiều đường.
    """
    if isinstance(raw, Mapping):
        return ItemSelector(
            paths=tuple(_path(path) for path in raw["any_of"]), join=raw.get("join")
        )
    return ItemSelector(paths=(_path(raw),))


# ─── Tool ────────────────────────────────────────────────────────────────────


def parse_tool(document: Mapping[str, Any], *, where: str = "<tool>") -> ToolSpec:
    """Dịch một tài liệu tool đã validate thành `ToolSpec`."""
    _check_document_version(document, where)

    mapping = {
        name: FieldMapping(
            path=_path(entry["path"]),
            items=(
                {field: _selector(raw) for field, raw in entry["items"].items()}
                if "items" in entry
                else None
            ),
        )
        for name, entry in document["output_mapping"].items()
    }

    reference = document.get("provider_reference")
    retry = document["retry"]
    idempotency = document["idempotency"]

    return ToolSpec(
        tool_id=str(document["tool_id"]),
        provider=str(document["provider"]),
        version=str(document["version"]),
        description=str(document["description"]),
        input_schema=PayloadSchema(
            version=str(document["input_schema"]["version"]),
            json_schema=document["input_schema"]["json_schema"],
        ),
        output_schema=PayloadSchema(
            version=str(document["output_schema"]["version"]),
            json_schema=document["output_schema"]["json_schema"],
        ),
        required_scopes=frozenset(str(scope) for scope in document["required_scopes"]),
        side_effect=SideEffect(document["side_effect"]),
        retry=RetryPolicy(
            max_attempts=int(retry["max_attempts"]),
            initial_backoff_seconds=float(retry.get("initial_backoff_seconds", 0.5)),
            max_backoff_seconds=float(retry.get("max_backoff_seconds", 30.0)),
            multiplier=float(retry.get("multiplier", 2.0)),
        ),
        timeout=TimeoutPolicy(
            attempt_seconds=float(document["timeout"]["attempt_seconds"]),
            total_seconds=float(document["timeout"]["total_seconds"]),
        ),
        idempotency=IdempotencyPolicy(
            strategy=IdempotencyStrategy(idempotency["strategy"]),
            required=bool(idempotency["required"]),
            ttl_seconds=int(idempotency.get("ttl_seconds", 7 * 24 * 3600)),
        ),
        approval=ApprovalPolicy(
            required=bool(document["approval"]["required"]),
            scope=ApprovalScope(document["approval"].get("scope", ApprovalScope.REQUESTER)),
        ),
        request=ProviderRequest(
            method=document["request"]["method"],
            path=str(document["request"]["path"]),
            body=document["request"]["body"],
            path_params=path_placeholders(str(document["request"]["path"])),
        ),
        output_mapping=mapping,
        input_mapping=document.get("input_mapping"),
        rate_limits=_rate_limit_profile(document.get("rate_limits")),
        permissions=frozenset(str(role) for role in document.get("permissions", ())),
        provider_reference=(
            ProviderReference(
                paths=tuple(_path(path) for path in reference["paths"]),
                separator=str(reference.get("separator", ":")),
            )
            if reference
            else None
        ),
        enabled=bool(document.get("enabled", True)),
    )


# ─── Connector ───────────────────────────────────────────────────────────────


def parse_connector(document: Mapping[str, Any], *, where: str = "<connector>") -> ConnectorSpec:
    """Dịch một tài liệu connector đã validate thành `ConnectorSpec`."""
    _check_document_version(document, where)

    breaker = document.get("circuit_breaker")
    envelope = document.get("response_envelope")
    webhooks = document["webhooks"]
    events = document["events"]

    return ConnectorSpec(
        connector_id=str(document["connector_id"]),
        version=str(document["version"]),
        label=str(document["label"]),
        base_url=str(document["base_url"]),
        auth=AuthSpec(
            kind=document["auth"]["kind"],
            header=str(document["auth"]["header"]),
            prefix=str(document["auth"]["prefix"]),
        ),
        tool_ids=tuple(str(tool_id) for tool_id in document["tools"]),
        error_mapping={
            str(code): ErrorCategory(category)
            for code, category in document["error_mapping"].items()
        },
        static_headers={
            str(name).lower(): str(value)
            for name, value in document.get("static_headers", {}).items()
        },
        response_envelope=(
            ResponseEnvelope(
                success_field=str(envelope["success_field"]),
                error_field=str(envelope["error_field"]),
                detail_field=envelope.get("detail_field"),
                failure_value=envelope.get("failure_value", False),
            )
            if envelope
            else None
        ),
        webhooks=WebhookCapabilities(
            supported=bool(webhooks["supported"]),
            signature_header=webhooks.get("signature_header"),
            timestamp_header=webhooks.get("timestamp_header"),
        ),
        events=EventCapabilities(
            supported=bool(events["supported"]),
            event_types=tuple(str(name) for name in events.get("event_types", ())),
        ),
        default_rate_limits=_rate_limit_profile(document.get("default_rate_limits")),
        circuit_breaker=(
            CircuitBreakerSpec(
                failure_threshold=int(breaker["failure_threshold"]),
                global_failure_threshold=int(
                    breaker.get("global_failure_threshold", int(breaker["failure_threshold"]) * 4)
                ),
                window_seconds=int(breaker["window_seconds"]),
                open_seconds=int(breaker["open_seconds"]),
                probe_timeout_seconds=int(breaker.get("probe_timeout_seconds", 120)),
            )
            if breaker
            else CircuitBreakerSpec()
        ),
        notes=str(document.get("notes", "")),
    )


# ─── Nạp cả thư mục ──────────────────────────────────────────────────────────


def load_documents(root: Path) -> tuple[list[ConnectorSpec], list[ToolSpec]]:
    """Đọc và validate mọi tài liệu dưới `root`.

    `root` trỏ tới `packages/schemas/tool/`. Bên gọi truyền vào — xem docstring
    đầu module.
    """
    tool_meta = _read_json(root / TOOL_META_SCHEMA)
    connector_meta = _read_json(root / CONNECTOR_META_SCHEMA)

    connectors: list[ConnectorSpec] = []
    tools: list[ToolSpec] = []

    for connector_path in sorted(root.glob("*/connector.json")):
        document = _read_json(connector_path)
        _validate(document, connector_meta, str(connector_path))
        connectors.append(parse_connector(document, where=str(connector_path)))

        for tool_path in sorted(connector_path.parent.glob("tools/*.json")):
            tool_document = _read_json(tool_path)
            _validate(tool_document, tool_meta, str(tool_path))
            tools.append(parse_tool(tool_document, where=str(tool_path)))

    if not connectors:
        raise ValidationError(f"Không tìm thấy connector nào dưới {root}.")

    return connectors, tools
