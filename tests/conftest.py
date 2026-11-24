"""pytest 夹具：每个用例一个独立 schema，跑在真实 PostgreSQL 16 上。

连接串取环境变量 TEST_DATABASE_URL，默认指向本机 lab 实例：
    postgresql+psycopg://ops:ops@127.0.0.1:5432/opsdb
每个用例建一个 t_seed_xxx schema，结束 DROP SCHEMA ... CASCADE，互不干扰。
"""

from __future__ import annotations

import os
import pathlib
import sys
import uuid

import pytest
import sqlalchemy as sa

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://ops:ops@127.0.0.1:5432/opsdb"
)


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture()
def schema() -> str:
    name = "t_seed_" + uuid.uuid4().hex[:10]
    admin = sa.create_engine(BASE_URL, isolation_level="AUTOCOMMIT", future=True)
    with admin.connect() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{name}"')
    try:
        yield name
    finally:
        with admin.connect() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{name}" CASCADE')
        admin.dispose()


@pytest.fixture()
def engine(schema: str) -> sa.Engine:
    """连到本用例的 schema（search_path 前置），与生产代码用的连接串同构。"""
    eng = sa.create_engine(
        BASE_URL,
        connect_args={"options": f"-csearch_path={schema},public"},
        future=True,
    )
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def db_url(schema: str) -> str:
    """给命令行入口用的连接串（走 URL query 传 search_path）。"""
    return f"{BASE_URL}?options=-csearch_path%3D{schema}%2Cpublic"


@pytest.fixture()
def table(engine: sa.Engine) -> sa.Engine:
    """建好 industry_tags 表，返回引擎。"""
    from scripts.seed_industry_tags import ensure_schema

    ensure_schema(engine)
    return engine


@pytest.fixture()
def org_id() -> uuid.UUID:
    return uuid.UUID("11111111-2222-3333-4444-555555555555")


@pytest.fixture()
def other_org_id() -> uuid.UUID:
    return uuid.UUID("99999999-8888-7777-6666-555555555555")
