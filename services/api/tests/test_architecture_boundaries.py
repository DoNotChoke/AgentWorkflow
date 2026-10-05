"""Ranh giới kiến trúc, kiểm bằng cách chạy thật thay vì đọc import.

`lint-imports` đọc cây import tĩnh và là hàng rào chính (xem ADR-0001, ADR-0002).
Test ở đây kiểm cùng một bất biến nhưng bằng đường khác: nạp entrypoint trong một
tiến trình sạch rồi nhìn `sys.modules`.

Hai cách bắt hai loại sai khác nhau. `lint-imports` bắt được cả những import chỉ
xuất hiện dưới `TYPE_CHECKING` hay trong nhánh chết; nó không bắt được một
`importlib.import_module("app.core.crypto")` dựng chuỗi lúc chạy. Test này thì
ngược lại. §2.4 đủ quan trọng để đáng có cả hai.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

#: Mọi thứ đủ để dựng được một token đã giải mã. Trùng với `forbidden_modules`
#: của contract 6 trong `pyproject.toml` — giữ hai bên đồng bộ bằng tay.
CREDENTIAL_MODULES = (
    "app.core.crypto",
    "app.domains.connection.infrastructure.credential_store",
    "app.domains.connection.application.service",
    "app.entrypoints.wiring",
)

_PROBE = textwrap.dedent(
    """
    import importlib, json, sys
    importlib.import_module({entrypoint!r})
    print(json.dumps(sorted(m for m in sys.modules if m.startswith("app."))))
    """
)


def _modules_after_importing(entrypoint: str) -> frozenset[str]:
    """Nạp `entrypoint` trong tiến trình con, trả về các module `app.*` đã vào."""
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(sys.path[:1] + ["services/api"]),
        "AW_ENVIRONMENT": "local",
        "AW_DEBUG": "false",
        "AW_OTEL_ENABLED": "false",
        "AW_LOG_JSON": "false",
        "AW_LOG_LEVEL": "WARNING",
    }
    finished = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _PROBE.format(entrypoint=entrypoint)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    if finished.returncode != 0:
        pytest.fail(f"Không nạp được {entrypoint}:\n{finished.stderr}")

    import json

    loaded: list[str] = json.loads(finished.stdout.strip().splitlines()[-1])
    return frozenset(loaded)


def test_agent_runtime_khong_keo_theo_module_credential_nao() -> None:
    """§2.4 — Agent Runtime chạy chung tiến trình với LLM, nên nó không được có
    sẵn trong bộ nhớ bất cứ thứ gì dựng ra được token.

    Đây là bất biến mà contract 6 canh ở mức import tĩnh. Nếu test này đỏ thì
    thứ vừa bị phá không phải phong cách code — mà là ranh giới bảo mật duy nhất
    còn lại sau khi ADR-0001 xoá ranh giới mạng.
    """
    loaded = _modules_after_importing("app.entrypoints.agent")

    leaked = loaded & frozenset(CREDENTIAL_MODULES)
    assert leaked == frozenset(), (
        f"Agent Runtime đã nạp {sorted(leaked)}. Xem ADR-0002 và contract 6 trong "
        "pyproject.toml — Agent phải gọi Integration Gateway qua HTTP, không gọi "
        "trong tiến trình."
    )


def test_control_plane_van_dung_duoc_duong_credential() -> None:
    """Mặt còn lại của cùng một bất biến.

    Nếu không có test này thì cách dễ nhất để làm test trên xanh là gỡ luôn
    đường credential ra khỏi hệ thống — cả hai cùng xanh, và không ai gọi được
    provider nữa. Control Plane **phải** nạp `app.core.crypto`, vì nó là tiến
    trình chịu trách nhiệm giải mã.
    """
    loaded = _modules_after_importing("app.entrypoints.http")

    assert "app.core.crypto" in loaded
    assert "app.domains.connection.infrastructure.credential_store" in loaded


def test_agent_runtime_khong_nap_duong_publish_hay_chay_workflow() -> None:
    """Compiler chạy cạnh LLM và chỉ được để lại một draft (ADR-0015, contract 9)."""
    loaded = _modules_after_importing("app.entrypoints.agent")

    assert "app.domains.workflow.application.compile_service" in loaded
    assert "app.domains.workflow.application.service" not in loaded
    forbidden = ("app.domains.workflow.infrastructure.temporal", "app.domains.execution")
    assert [name for name in loaded if name.startswith(forbidden)] == []
