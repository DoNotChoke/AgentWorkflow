"""Sinh module Python mang JSON Schema của Workflow IR.

    python packages/schemas/workflow/build_ir_schema.py            # ghi ra file
    python packages/schemas/workflow/build_ir_schema.py --check    # chỉ báo lệch, không ghi

JSON là nguồn chân lý; Python là hạ lưu và **phải được commit**. Sinh ra đúng
cái schema dưới dạng dữ liệu Python, không sinh kiểu — kiểu viết tay ở
`app/domains/workflow/domain/ir.py`, cùng lý do `tool/build_catalog.py` đã ghi.

Không đọc JSON lúc chạy vì đường dẫn tới `packages/` khác độ sâu giữa dev và
container (xem `packages/schemas/tool/README.md`).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
SOURCE = HERE / "ir.schema.json"
TARGET = (
    REPO_ROOT
    / "services"
    / "api"
    / "app"
    / "domains"
    / "workflow"
    / "domain"
    / "generated"
    / "ir_v1.py"
)

HEADER = '''"""SINH TỰ ĐỘNG — đừng sửa file này bằng tay.

Nguồn: `packages/schemas/workflow/ir.schema.json`
Sinh lại:

    python packages/schemas/workflow/build_ir_schema.py

Sửa tay ở đây sẽ bị lần sinh sau ghi đè, và test đối chiếu sẽ đỏ trước đó.
"""

from __future__ import annotations

from typing import Any, Final

'''


def emit(value: object, indent: int = 0) -> str:
    """Biểu thức Python dựng lại một giá trị JSON."""
    pad = "    " * indent
    inner = "    " * (indent + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = "\n".join(
            f"{inner}{key!r}: {emit(item, indent + 1)}," for key, item in value.items()
        )
        return f"{{\n{items}\n{pad}}}"
    if isinstance(value, list):
        if not value:
            return "[]"
        items = "\n".join(f"{inner}{emit(item, indent + 1)}," for item in value)
        return f"[\n{items}\n{pad}]"
    return repr(value)


def render() -> str:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    return f"{HEADER}IR_SCHEMA: Final[dict[str, Any]] = {emit(document)}\n"


def format_with_ruff(path: Path) -> None:
    ruff = Path(sys.executable).parent / "ruff"
    executable = str(ruff) if ruff.exists() or ruff.with_suffix(".exe").exists() else "ruff"
    result = subprocess.run(  # noqa: S603
        [executable, "format", str(path)], capture_output=True, text=True, cwd=REPO_ROOT
    )
    if result.returncode != 0:
        print(f"ruff format thất bại:\n{result.stderr or result.stdout}", file=sys.stderr)
        raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Chỉ báo lệch; không ghi gì.")
    args = parser.parse_args()

    generated = render()

    if args.check:
        if not TARGET.exists():
            print(f"Chưa có {TARGET.relative_to(REPO_ROOT)}. Chạy lại không kèm --check.")
            return 1
        scratch = TARGET.with_suffix(".check.py")
        scratch.write_text(generated, encoding="utf-8")
        try:
            format_with_ruff(scratch)
            expected = scratch.read_text(encoding="utf-8")
        finally:
            scratch.unlink(missing_ok=True)
        if expected != TARGET.read_text(encoding="utf-8"):
            print(
                f"{TARGET.relative_to(REPO_ROOT)} đã lệch khỏi ir.schema.json.\n"
                "Chạy: python packages/schemas/workflow/build_ir_schema.py"
            )
            return 1
        print("IR schema khớp với JSON.")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    (TARGET.parent / "__init__.py").touch()
    TARGET.write_text(generated, encoding="utf-8")
    format_with_ruff(TARGET)
    print(f"Đã sinh {TARGET.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
