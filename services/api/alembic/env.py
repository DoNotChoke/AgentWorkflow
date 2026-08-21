"""Cấu hình môi trường migration của Alembic (async)."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from alembic import context
from app.core.config import get_settings
from app.core.db import DOMAIN_SCHEMAS, Base

# Import để model của từng domain được đăng ký vào ``Base.metadata`` trước khi
# Alembic so sánh. Thiếu một dòng ở đây thì `alembic revision --autogenerate`
# sẽ sinh ra migration xoá đúng bảng của domain bị bỏ sót.
from app.domains.identity.infrastructure import models as identity_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# URL luôn lấy từ biến môi trường, không lấy từ alembic.ini.
config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def include_object(
    _object: object,
    name: str | None,
    type_: str,
    _reflected: bool,
    _compare_to: object,
) -> bool:
    """Chỉ quản lý các schema thuộc về ứng dụng.

    Temporal dùng chung cụm PostgreSQL ở môi trường dev. Nếu không lọc, Alembic
    sẽ nhìn thấy bảng của Temporal và sinh migration xoá chúng.
    """
    if type_ == "table":
        schema = getattr(_object, "schema", None)
        return schema in DOMAIN_SCHEMAS
    return name is not None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_object=include_object,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
