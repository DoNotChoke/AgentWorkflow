"""Duyệt AST của một đoạn code: chặn thứ code *viết ra được*.

Chỉ đọc, không chạy — nên Control Plane dùng được nó lúc publish, khác với
`core/sandbox` là thứ chạy code và bị contract 8 cấm ở đó.

**Chỉ dùng thư viện chuẩn.** Tiến trình con của sandbox nạp file này theo đường dẫn,
không qua package `app`.
"""

from __future__ import annotations

import ast
from typing import Final

#: Tên hàm bọc quanh code, để `return` là hợp lệ.
ENTRY: Final = "entrypoint"

#: Builtin mà code được gọi. Danh sách đóng — không có `open`, `eval`, `getattr`.
SAFE_BUILTINS: Final[frozenset[str]] = frozenset(
    {
        "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float",
        "int", "len", "list", "map", "max", "min", "range", "reversed", "round", "set",
        "sorted", "str", "sum", "tuple", "zip", "isinstance",
    }
)  # fmt: skip

#: Tên node AST bị cấm, kèm lý do đưa vào thông báo lỗi.
FORBIDDEN_NODES: Final[dict[str, str]] = {
    "Import": "import là cửa chính ra khỏi sandbox",
    "ImportFrom": "import là cửa chính ra khỏi sandbox",
    "While": "vòng while không chặn được bằng dữ liệu; dùng for trên một mảng",
    "ClassDef": "không cần định nghĩa lớp cho một phép biến đổi",
    "Global": "code không có state ngoài hàm",
    "Nonlocal": "code không có state ngoài hàm",
    "Try": "lỗi phải nổi lên thành lỗi của node, không bị nuốt",
    "Raise": "lỗi phải nổi lên thành lỗi của node, không bị nuốt",
    "With": "không có tài nguyên nào để mở",
    "Delete": "không cần xoá tên",
    "Assert": "dùng if và return thay vì assert",
    "AsyncFunctionDef": "code chạy đồng bộ",
    "Await": "code chạy đồng bộ",
    "AsyncFor": "code chạy đồng bộ",
    "AsyncWith": "code chạy đồng bộ",
    "Yield": "code trả về một giá trị bằng return",
    "YieldFrom": "code trả về một giá trị bằng return",
    "Match": "chưa cần, và nó mở thêm một mặt cú pháp phải soi",
}

#: Tên bị cấm dù cú pháp hợp lệ — hai cái đầu là đường thoát kinh điển.
FORBIDDEN_NAMES: Final[frozenset[str]] = frozenset(
    {"eval", "exec", "compile", "open", "input", "__import__", "getattr", "setattr",
     "delattr", "globals", "locals", "vars", "dir", "type", "super", "object", "breakpoint",
     "memoryview", "help", "exit", "quit"}
)  # fmt: skip


class UnsafeCodeError(Exception):
    """Code không qua được vòng duyệt. Mang theo dòng để người sửa biết chỗ."""

    def __init__(self, reason: str, line: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.line = line


def parse(code: str) -> ast.Module:
    """Bọc `code` thành thân của một hàm nhận `args`, rồi duyệt. Ném `UnsafeCodeError`."""
    indented = "\n".join(f"    {line}" for line in code.splitlines()) or "    pass"
    source = f"def {ENTRY}(args):\n{indented}\n"
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise UnsafeCodeError(f"Code không parse được: {exc.msg}.", exc.lineno) from exc
    check(tree)
    return tree


def check(tree: ast.AST) -> None:
    """Duyệt toàn bộ cây. Ném `UnsafeCodeError` ở vi phạm đầu tiên."""
    for node in ast.walk(tree):
        kind = type(node).__name__
        if (reason := FORBIDDEN_NODES.get(kind)) is not None:
            line = getattr(node, "lineno", None)
            raise UnsafeCodeError(f"{kind} không được phép: {reason}.", line)

        if isinstance(node, ast.Attribute):
            _check_name(node.attr, node.lineno, thing="thuộc tính")
        elif isinstance(node, ast.Name):
            _check_name(node.id, node.lineno, thing="tên")
        elif isinstance(node, ast.FunctionDef | ast.Lambda):
            _check_arguments(node)
        elif isinstance(node, ast.keyword) and node.arg is not None:
            _check_name(node.arg, thing="tham số")


def _check_name(name: str, line: int | None = None, *, thing: str) -> None:
    if name.startswith("_"):
        # `().__class__.__bases__[0].__subclasses__()` — đường thoát kinh điển,
        # và mọi biến thể của nó đều phải đi qua một tên gạch dưới.
        raise UnsafeCodeError(f"{thing} bắt đầu bằng gạch dưới không được phép: {name!r}.", line)
    if name in FORBIDDEN_NAMES:
        raise UnsafeCodeError(f"{thing} {name!r} không được phép.", line)


def _check_arguments(node: ast.FunctionDef | ast.Lambda) -> None:
    arguments = node.args
    every = [
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
        *([arguments.vararg] if arguments.vararg else []),
        *([arguments.kwarg] if arguments.kwarg else []),
    ]
    for argument in every:
        _check_name(argument.arg, argument.lineno, thing="tham số")
