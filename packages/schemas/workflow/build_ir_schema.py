"""Sinh module Python mang JSON Schema của Workflow IR, mỗi major một module.

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
GENERATED = REPO_ROOT / "services" / "api" / "app" / "domains" / "workflow" / "domain" / "generated"
#: Major cũ vẫn phải diễn giải được (§41), nên mọi major đều được sinh.
MAJORS = (1, 2)

HEADER = '''"""SINH TỰ ĐỘNG — đừng sửa file này bằng tay.

Nguồn: `packages/schemas/workflow/{source}`
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


def render(source: Path) -> str:
    document = json.loads(source.read_text(encoding="utf-8"))
    header = HEADER.replace("{source}", source.name)
    return f"{header}IR_SCHEMA: Final[dict[str, Any]] = {emit(document)}\n"


def format_with_ruff(path: Path) -> None:
    ruff = Path(sys.executable).parent / "ruff"
    executable = str(ruff) if ruff.exists() or ruff.with_suffix(".exe").exists() else "ruff"
    result = subprocess.run(  # noqa: S603
        [executable, "format", str(path)], capture_output=True, text=True, cwd=REPO_ROOT
    )
    if result.returncode != 0:
        print(f"ruff format thất bại:\n{result.stderr or result.stdout}", file=sys.stderr)
        raise SystemExit(1)


def check(source: Path, target: Path, generated: str) -> int:
    if not target.exists():
        print(f"Chưa có {target.relative_to(REPO_ROOT)}. Chạy lại không kèm --check.")
        return 1
    scratch = target.with_suffix(".check.py")
    scratch.write_text(generated, encoding="utf-8")
    try:
        format_with_ruff(scratch)
        expected = scratch.read_text(encoding="utf-8")
    finally:
        scratch.unlink(missing_ok=True)
    if expected != target.read_text(encoding="utf-8"):
        print(
            f"{target.relative_to(REPO_ROOT)} đã lệch khỏi {source.name}.\n"
            "Chạy: python packages/schemas/workflow/build_ir_schema.py"
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Chỉ báo lệch; không ghi gì.")
    args = parser.parse_args()

    status = 0
    for major in MAJORS:
        source = HERE / f"ir.v{major}.schema.json"
        target = GENERATED / f"ir_v{major}.py"
        generated = render(source)
        if args.check:
            status |= check(source, target, generated)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        (target.parent / "__init__.py").touch()
        target.write_text(generated, encoding="utf-8")
        format_with_ruff(target)
        print(f"Đã sinh {target.relative_to(REPO_ROOT)}")

    if args.check and status == 0:
        print("IR schema khớp với JSON.")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
