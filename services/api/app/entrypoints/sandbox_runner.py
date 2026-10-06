"""`TransformRunner` của domain `workflow`, bằng sandbox.

Chỉ được nạp ở tiến trình được phép chạy code do người dùng viết: Agent Runtime và worker.
Control Plane không import module này (contract 8).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.sandbox import DEFAULT_LIMITS, Limits, run


class SandboxRunner:
    def __init__(self, limits: Limits = DEFAULT_LIMITS) -> None:
        self._limits = limits

    def run(self, code: str, args: Mapping[str, Any]) -> Any:
        return run(code, dict(args), self._limits).value
