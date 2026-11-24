"""alembic 环境：连接串只从环境变量 DATABASE_URL 读取，不写死在配置里。

平台仓库已有自己的 alembic 环境，本文件只是让本单可以独立跑
``alembic upgrade head`` / ``alembic check`` 复现迁移。
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise SystemExit("ERROR: 请先设置环境变量 DATABASE_URL（alembic 不读配置文件里的口令）")

target_metadata = None  # 本单只用 op.execute 跑 SQL，不依赖 ORM metadata


def run_migrations_offline() -> None:
    context.configure(url=DATABASE_URL, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(DATABASE_URL, poolclass=pool.NullPool, future=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
