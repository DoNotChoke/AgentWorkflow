"""Sinh catalog Python từ các tài liệu JSON trong thư mục này.

    python packages/schemas/tool/build_catalog.py            # ghi ra file
    python packages/schemas/tool/build_catalog.py --check    # chỉ báo lệch, không ghi

JSON là nguồn chân lý; Python là hạ lưu và **phải được commit** — đúng quy tắc
trong `packages/schemas/README.md`. Sinh ra *dữ liệu*, không phải *kiểu*: kiểu
đã có sẵn ở `domains/integration/domain/`, còn thứ không sinh được bằng tay là
bốn tài liệu tool dưới dạng Python.

Lý do phải sinh thay vì đọc JSON lúc chạy nằm ở docstring của
`app/domains/integration/infrastructure/specs.py`: đường dẫn tới `packages/`
khác độ sâu giữa dev và container.

Script này chạy tay. `--check` thoát khác 0 khi bản đã commit lệch khỏi JSON,
để cắm được vào pre-commit hook hoặc CI.
"""

from __future__ import annotations

import argparse
import dataclasses
import re
import subprocess
import sys
from collections.abc import Mapping
from enum import Enum
from pathlib import Path

# Console Windows mặc định cp1252, không mã hoá nổi tiếng Việt. Không có dòng
# này thì script chạy xong việc rồi chết ở câu `print` cuối cùng — thất bại
# trông y hệt thành công bị hỏng.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
API_ROOT = REPO_ROOT / "services" / "api"
TARGET = API_ROOT / "app" / "domains" / "integration" / "domain" / "generated" / "catalog_v1.py"

# Script nằm ngoài package `app`, nên phải tự chỉ đường tới nó. Đây là chỗ DUY
# NHẤT được phép suy ra đường dẫn từ vị trí file — và nó an toàn vì script chỉ
# chạy trong repo đã checkout, không bao giờ chạy trong container.
sys.path.insert(0, str(API_ROOT))

from app.domains.integration.infrastructure.specs import load_documents  # noqa: E402

DOCSTRING = '''"""SINH TỰ ĐỘNG — đừng sửa file này bằng tay.

Nguồn: `packages/schemas/tool/`
Sinh lại:

    python packages/schemas/tool/build_catalog.py

Sửa tay ở đây sẽ bị lần sinh sau ghi đè, và test đối chiếu sẽ đỏ trước đó.
"""

from __future__ import annotations
'''

#: Mọi ký hiệu mà catalog *có thể* cần, theo module. Import nào không xuất hiện
#: trong phần thân sinh ra sẽ bị bỏ — emitter lược mọi trường đang mang giá trị
#: mặc định, nên tập ký hiệu thực dùng phụ thuộc vào nội dung tài liệu JSON.
#: Emit thừa một import là `ruff check` đỏ ngay sau mỗi lần sinh.
IMPORTABLE: dict[str, tuple[str, ...]] = {
    "app.core.errors": ("ErrorCategory",),
    "app.domains.integration.domain.connectors": (
        "AuthSpec",
        "CircuitBreakerSpec",
        "ConnectorSpec",
        "EventCapabilities",
        "ResponseEnvelope",
        "WebhookCapabilities",
    ),
    "app.domains.integration.domain.tools": (
        "ApprovalPolicy",
        "FieldMapping",
        "IdempotencyPolicy",
        "IdempotencyStrategy",
        "PayloadSchema",
        "ProviderReference",
        "ProviderRequest",
        "RateLimitProfile",
        "RateLimitRule",
        "RetryPolicy",
        "SideEffect",
        "TimeoutPolicy",
        "ToolSpec",
    ),
}


def imports_for(body: str) -> str:
    """Khối import chỉ gồm những ký hiệu thực sự xuất hiện trong `body`."""
    lines: list[str] = []
    for module, names in IMPORTABLE.items():
        used = [name for name in names if re.search(rf"\b{name}\b", body)]
        if not used:
            continue
        if len(used) == 1:
            lines.append(f"from {module} import {used[0]}")
        else:
            joined = "".join(f"    {name},\n" for name in used)
            lines.append(f"from {module} import (\n{joined})")
    return "\n".join(lines)


def emit(value: object, indent: int = 0) -> str:
    """Dựng biểu thức Python dựng lại `value`.

    Xử lý dataclass, mapping, tuple/list, frozenset, Enum và nguyên thuỷ — đủ
    cho cây value object của catalog. Bỏ qua trường nào đang mang đúng giá trị
    mặc định của nó, để file sinh ra đọc được thay vì là một bức tường.
    """
    pad = "    " * indent
    inner = "    " * (indent + 1)

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        parts: list[str] = []
        for field in dataclasses.fields(value):
            current = getattr(value, field.name)
            if _is_default(field, current):
                continue
            parts.append(f"{inner}{field.name}={emit(current, indent + 1)},")
        if not parts:
            return f"{type(value).__name__}()"
        body = "\n".join(parts)
        return f"{type(value).__name__}(\n{body}\n{pad})"

    if isinstance(value, Enum):
        return f"{type(value).__name__}.{value.name}"

    if isinstance(value, Mapping):
        if not value:
            return "{}"
        items = "\n".join(
            f"{inner}{key!r}: {emit(item, indent + 1)}," for key, item in value.items()
        )
        return f"{{\n{items}\n{pad}}}"

    if isinstance(value, frozenset):
        if not value:
            return "frozenset()"
        # Sắp xếp để lần sinh nào cũng ra cùng một file — một frozenset không
        # có thứ tự, và một diff nhiễu vì thứ tự băm là một diff không đọc được.
        items = ", ".join(repr(item) for item in sorted(value))
        return f"frozenset({{{items}}})"

    if isinstance(value, tuple):
        if not value:
            return "()"
        items = "\n".join(f"{inner}{emit(item, indent + 1)}," for item in value)
        return f"(\n{items}\n{pad})"

    if isinstance(value, list):
        if not value:
            return "[]"
        items = "\n".join(f"{inner}{emit(item, indent + 1)}," for item in value)
        return f"[\n{items}\n{pad}]"

    return repr(value)


def _is_default(field: dataclasses.Field[object], current: object) -> bool:
    if field.default is not dataclasses.MISSING:
        return bool(current == field.default)
    if field.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
        return bool(current == field.default_factory())  # type: ignore[misc]
    return False


def render(root: Path) -> str:
    connectors, tools = load_documents(root)

    connectors = sorted(connectors, key=lambda item: item.connector_id)
    tools = sorted(tools, key=lambda item: (item.tool_id, item.version))

    body = "\n".join(
        (
            "CONNECTORS: tuple[ConnectorSpec, ...] = " + emit(tuple(connectors)),
            "",
            "TOOLS: tuple[ToolSpec, ...] = " + emit(tuple(tools)),
            "",
        )
    )
    return f"{DOCSTRING}\n{imports_for(body)}\n\n\n{body}"


def format_with_ruff(path: Path) -> None:
    """Sắp import rồi canh lề file vừa sinh, bằng chính `ruff` của repo.

    Hai bước, cùng một lý do: emitter dựng đúng cấu trúc nhưng không biết quy
    ước trình bày. Để `ruff` quyết định — đoán thay nó thì mỗi lần sinh xong
    `ruff check` hoặc `ruff format --check` trong CI lại đỏ, và bản sinh ra
    không bao giờ ở trạng thái commit được.

    `--select I` chạy trước vì sắp lại import làm đổi số dòng; format sau chốt
    phần trình bày.
    """
    ruff = Path(sys.executable).parent / "ruff"
    executable = str(ruff) if ruff.exists() or ruff.with_suffix(".exe").exists() else "ruff"

    for argv in (["check", "--select", "I", "--fix", "--quiet"], ["format"]):
        result = subprocess.run(  # noqa: S603
            [executable, *argv, str(path)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        if result.returncode != 0:
            print(f"ruff {argv[0]} thất bại:\n{result.stderr or result.stdout}", file=sys.stderr)
            raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Chỉ báo bản đã commit có lệch khỏi JSON không; không ghi gì.",
    )
    args = parser.parse_args()

    generated = render(HERE)

    if args.check:
        if not TARGET.exists():
            print(f"Chưa có {TARGET.relative_to(REPO_ROOT)}. Chạy lại không kèm --check.")
            return 1
        # So sau khi format, vì bản đã commit là bản đã qua ruff.
        scratch = TARGET.with_suffix(".check.py")
        scratch.write_text(generated, encoding="utf-8")
        try:
            format_with_ruff(scratch)
            expected = scratch.read_text(encoding="utf-8")
        finally:
            scratch.unlink(missing_ok=True)

        if expected != TARGET.read_text(encoding="utf-8"):
            print(
                f"{TARGET.relative_to(REPO_ROOT)} đã lệch khỏi packages/schemas/tool/.\n"
                "Chạy: python packages/schemas/tool/build_catalog.py"
            )
            return 1
        print("Catalog khớp với JSON.")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(generated, encoding="utf-8")
    format_with_ruff(TARGET)
    print(f"Đã sinh {TARGET.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
