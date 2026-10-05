"""Xuất một conversation thật từ dev DB thành fixture cho compiler.

    PYTHONPATH=services/api python services/api/tests/fixtures/sources/_export.py kb1 cnv_…

Hình dạng ghi ra là hình dạng `read_source` nhận: tin nhắn có chữ của người dùng và
của Agent, rồi trace và bước ở dạng dữ liệu thuần, payload đã nạp. Id nội bộ và id
của provider được thay bằng id giả, nhất quán trong một file — hai chỗ cùng một id
thật vẫn cùng một id giả.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import dispose_engine, init_engine, session_scope
from app.core.objectstore import close_object_store, init_object_store
from app.entrypoints.compiler_wiring import TraceCompileSourceReader

HERE = Path(__file__).parent

_INTERNAL = re.compile(r"\b(usr|org|ws|conn|agt|cnv|trc|stp|msg|ti)_[0-9a-f]{32}\b")
_UUID = re.compile(
    r"\b[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}\b", re.IGNORECASE
)
_SLACK = re.compile(r"\b[CUTWDB]0[A-Z0-9]{8,10}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")


class _Aliases:
    def __init__(self) -> None:
        self._seen: dict[str, str] = {}

    def scrub(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._text(value)
        if isinstance(value, list):
            return [self.scrub(item) for item in value]
        if isinstance(value, dict):
            # Khoá cũng mang id thật: `{"by_user": {"U0…": 14}}`.
            return {self._text(key): self.scrub(item) for key, item in value.items()}
        return value

    def _text(self, raw: str) -> str:
        raw = _INTERNAL.sub(lambda m: self._alias(m.group(0), f"{m.group(1)}_", 32), raw)
        raw = _UUID.sub(self._uuid, raw)
        raw = _SLACK.sub(lambda m: self._alias(m.group(0), m.group(0)[0] + "0VIDU", 5), raw)
        return _EMAIL.sub(lambda m: self._alias(m.group(0), "nguoi", 2) + "@vidu.test", raw)

    def _uuid(self, match: re.Match[str]) -> str:
        real = match.group(0)
        fake = self._alias(real.replace("-", "").lower(), "", 32)
        if "-" not in real:
            return fake
        return f"{fake[:8]}-{fake[8:12]}-{fake[12:16]}-{fake[16:20]}-{fake[20:]}"

    def _alias(self, real: str, prefix: str, width: int) -> str:
        if real not in self._seen:
            self._seen[real] = f"{prefix}{len(self._seen) + 1:0{width}d}"
        return self._seen[real]


async def _export(conversation_id: str) -> dict[str, Any]:
    settings = get_settings()
    init_engine(settings)
    init_object_store(settings)
    try:
        return await _read(conversation_id)
    finally:
        await close_object_store()
        await dispose_engine()


async def _read(conversation_id: str) -> dict[str, Any]:
    async with session_scope() as session:
        tenant_id = (
            await session.execute(
                text(
                    "select tenant_id from trace.execution_traces "
                    "where conversation_id = :c limit 1"
                ),
                {"c": conversation_id},
            )
        ).scalar_one_or_none()
    if tenant_id is None:
        raise SystemExit(f"Không có trace nào của {conversation_id}.")
    source = await TraceCompileSourceReader().read(
        tenant_id=tenant_id, conversation_id=conversation_id
    )
    if source is None:
        raise SystemExit(f"Không có conversation {conversation_id}.")
    return dict(source.document)


def main() -> None:
    name, conversation_id = sys.argv[1], sys.argv[2]
    document = _Aliases().scrub(asyncio.run(_export(conversation_id)))
    target = HERE / f"{name}.json"
    target.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    steps = sum(len(trace["steps"]) for trace in document["traces"])
    sys.stdout.write(f"{target.name}: {len(document['traces'])} lượt, {steps} bước\n")


if __name__ == "__main__":
    main()
