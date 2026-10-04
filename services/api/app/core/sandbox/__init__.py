"""Chạy một đoạn code nhỏ, thuần, do LLM viết — §11.2.

Hai lớp phòng thủ, cần cả hai: `core/codeguard.py` duyệt AST trước khi chạy, và mỗi lần
chạy nằm trong một **tiến trình con** có trần thời gian, bộ nhớ và kích thước kết
quả. Lớp một chặn thứ viết ra được, lớp hai chặn thứ làm được.

Module này **không bao giờ được nạp trong Control Plane** — đó là tiến trình duy
nhất giải mã được credential provider (ADR-0002), và một lần thoát sandbox ở đó là
một lần lộ token của mọi tenant. Contract 8 trong `pyproject.toml` canh điều đó.

Mọi lỗi đều là `SandboxError` với category `VALIDATION`: hỏng ở đây là tính chất
tất định của code cộng dữ liệu, không phải sự cố thoáng qua, nên **không đáng thử
lại** — kể cả quá hạn.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from app.core.errors import ErrorCategory, ValidationError
from app.core.logging import get_logger

log = get_logger(__name__)

_CHILD: Final = Path(__file__).resolve().parent / "_child.py"


class SandboxError(ValidationError):
    """Code không chạy được, hoặc chạy ra thứ không dùng được."""

    category = ErrorCategory.VALIDATION


@dataclass(frozen=True, slots=True)
class Limits:
    """Trần của một lần chạy. Xem §3.2 của kế hoạch WP-C."""

    timeout_seconds: float = 2.0
    memory_bytes: int = 256 * 1024 * 1024
    output_bytes: int = 1024 * 1024
    code_bytes: int = 4096


DEFAULT_LIMITS: Final = Limits()


@dataclass(frozen=True, slots=True)
class Result:
    value: Any
    #: `False` trên hệ không có `resource` (Windows) — trần bộ nhớ không được đặt.
    memory_capped: bool


def run(code: str, args: dict[str, Any], limits: Limits = DEFAULT_LIMITS) -> Result:
    """Chạy `code` với `args`, trả giá trị nó `return`.

    `code` là thân của một hàm nhận đúng một tham số tên `args`.
    """
    encoded_code = code.encode("utf-8")
    if len(encoded_code) > limits.code_bytes:
        raise SandboxError(
            f"Code dài {len(encoded_code)} byte, trần là {limits.code_bytes} byte.",
            details={"byte_size": len(encoded_code), "max_bytes": limits.code_bytes},
        )

    request = json.dumps(
        {
            "code": code,
            "args": args,
            "memory_bytes": limits.memory_bytes,
            "output_bytes": limits.output_bytes,
        },
        ensure_ascii=False,
        allow_nan=False,
    )

    try:
        completed = subprocess.run(  # noqa: S603 — đường dẫn cố định, không có shell
            [sys.executable, "-I", str(_CHILD)],
            input=request,
            capture_output=True,
            text=True,
            encoding="utf-8",
            # Traceback của tiến trình con có thể không phải utf-8 hợp lệ trên
            # Windows; đọc hỏng stderr không được làm hỏng cả lời gọi.
            errors="replace",
            timeout=limits.timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SandboxError(
            f"Code chạy quá {limits.timeout_seconds} giây và đã bị dừng.",
            details={"timeout_seconds": limits.timeout_seconds},
        ) from exc

    return _read(completed)


def memory_cap_supported() -> bool:
    """Hệ này có đặt được trần bộ nhớ không. `False` trên Windows.

    Khai ra thay vì im lặng, vì một tầng bảo vệ vắng mặt trông y hệt một tầng bảo
    vệ đang hoạt động.
    """
    return sys.platform != "win32"


def _read(completed: subprocess.CompletedProcess[str]) -> Result:
    if not completed.stdout:
        # Tiến trình con chết trước khi kịp trả lời — thường là bị hệ điều hành
        # giết vì chạm trần bộ nhớ.
        log.warning("sandbox_child_silent", returncode=completed.returncode)
        raise SandboxError(
            "Code dừng giữa chừng mà không trả về gì. Nhiều khả năng nó dùng quá nhiều bộ nhớ.",
            details={"returncode": completed.returncode},
        )

    payload = json.loads(completed.stdout)
    if payload["ok"]:
        return Result(value=payload["value"], memory_capped=payload["memory_capped"])

    raise SandboxError(payload["reason"], details={"kind": payload["kind"]})
