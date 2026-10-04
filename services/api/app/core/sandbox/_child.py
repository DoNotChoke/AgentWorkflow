"""Tiến trình con chạy một đoạn code. Chỉ thư viện chuẩn, không import `app`.

Nhận một JSON trên stdin, trả một JSON trên stdout. Chạy bằng đường dẫn file chứ
không bằng `-m`, để chính trình thông dịch này cũng không có package `app` trong
`sys.path`.

Không bao giờ gọi trực tiếp; `runner.py` là người gọi.
"""

from __future__ import annotations

import builtins
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

# Console Windows mặc định cp1252, không mã hoá nổi tiếng Việt — và `-I` bỏ qua
# PYTHONIOENCODING, nên phải đặt ở đây. Thiếu `stdout` thì mọi thông báo lỗi có
# dấu làm tiến trình con chết trước khi kịp trả lời; thiếu `stdin` thì code và dữ
# liệu tiếng Việt đã méo trước khi chạy, và kết quả sai mà không có lỗi nào.
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def _load_guard() -> Any:
    """Nạp đúng một file theo đường dẫn. Không thêm gì vào `sys.path`: thư mục chứa nó
    là `app/core`, và code trong sandbox không được thấy thứ gì ở đó."""
    spec = importlib.util.spec_from_file_location(
        "guard", Path(__file__).resolve().parents[1] / "codeguard.py"
    )
    assert spec is not None and spec.loader is not None  # noqa: S101
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


def limit_memory(limit_bytes: int) -> bool:
    """Đặt trần bộ nhớ. `False` khi hệ điều hành không có `resource` (Windows).

    Nạp bằng `__import__` và gán kiểu `Any` có chủ đích: `resource` chỉ tồn tại
    trên Unix, nên mọi cách viết khác đều đỏ mypy ở đúng một trong hai nền tảng —
    `attr-defined` trên Windows, `unused-ignore` trên Linux.
    """
    try:
        resource: Any = __import__("resource")
    except ImportError:
        return False
    resource.setrlimit(resource.RLIMIT_AS, (limit_bytes, limit_bytes))
    return True


def build(code: str) -> Any:
    """Bọc code thành một hàm, duyệt AST, rồi biên dịch."""
    tree = guard.parse(code)

    scope: dict[str, Any] = {
        "__builtins__": {name: getattr(builtins, name) for name in guard.SAFE_BUILTINS}
    }
    exec(compile(tree, "<transform>", "exec"), scope)  # noqa: S102 — đã duyệt ở trên
    return scope[guard.ENTRY]


def main() -> int:
    request = json.loads(sys.stdin.read())
    capped = limit_memory(request["memory_bytes"])

    try:
        result = build(request["code"])(request["args"])
    except guard.UnsafeCodeError as exc:
        return _reply({"ok": False, "kind": "unsafe", "reason": exc.reason, "line": exc.line})
    except MemoryError:
        return _reply({"ok": False, "kind": "memory", "reason": "Code dùng quá nhiều bộ nhớ."})
    except BaseException as exc:  # noqa: BLE001 — mọi lỗi của code đều là dữ liệu trả về
        reason = f"{type(exc).__name__}: {exc}"
        return _reply({"ok": False, "kind": "runtime", "reason": reason})

    try:
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        return _reply({"ok": False, "kind": "result", "reason": f"Kết quả không phải JSON: {exc}"})

    if len(encoded.encode("utf-8")) > request["output_bytes"]:
        return _reply({"ok": False, "kind": "output", "reason": "Kết quả vượt trần kích thước."})

    return _reply({"ok": True, "value": result, "memory_capped": capped})


def _reply(payload: dict[str, Any]) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
