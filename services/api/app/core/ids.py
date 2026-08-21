"""Sinh định danh có tiền tố theo loại tài nguyên."""

from __future__ import annotations

import uuid
from enum import StrEnum


class ResourceKind(StrEnum):
    ORGANIZATION = "org"
    WORKSPACE = "ws"
    USER = "usr"
    MEMBERSHIP = "mem"
    REFRESH_TOKEN = "rft"  # noqa: S105 — tiền tố id, không phải bí mật
    #: Họ token — nối mọi mắt xích sinh ra từ một lần đăng nhập, để thu hồi
    #: được cả chuỗi khi phát hiện token bị dùng lại.
    TOKEN_FAMILY = "rfa"  # noqa: S105 — tiền tố id, không phải bí mật
    CONNECTION = "conn"
    CONNECTOR = "cnr"
    TOOL_INVOCATION = "ti"
    AGENT = "agt"
    CONVERSATION = "cnv"
    MESSAGE = "msg"
    EXECUTION_TRACE = "trc"
    TRACE_STEP = "stp"
    WORKFLOW = "wf"
    WORKFLOW_VERSION = "wfv"
    WORKFLOW_SCHEDULE = "sch"
    EXECUTION = "exe"
    NODE_EXECUTION = "nex"
    APPROVAL = "apr"
    AUDIT_EVENT = "aud"
    USAGE_RECORD = "usg"
    EVENT = "evt"
    IDEMPOTENCY = "idm"


def new_id(kind: ResourceKind) -> str:
    return f"{kind}_{uuid.uuid4().hex}"


def kind_of(identifier: str) -> ResourceKind | None:
    prefix, _, _ = identifier.partition("_")
    try:
        return ResourceKind(prefix)
    except ValueError:
        return None


def is_kind(identifier: str, kind: ResourceKind) -> bool:
    return identifier.startswith(f"{kind}_")


def idempotency_key(*, execution_id: str, node_id: str, operation: str) -> str:
    """Sinh khoá idempotency từ ngữ nghĩa thực thi ổn định"""
    return f"{ResourceKind.IDEMPOTENCY}_{execution_id}:{node_id}:{operation}"
