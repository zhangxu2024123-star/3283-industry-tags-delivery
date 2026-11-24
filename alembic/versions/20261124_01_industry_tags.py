"""创建字典中心 · 行业分类标签表

DDL 单一来源：schema/industry_tags.sql（迁移直接读取该文件，避免迁移/文档/测试三处漂移）

Revision: 20261124_01
Create Date: 2026-11-24 15:10:00
"""

from __future__ import annotations

import pathlib

from alembic import op

revision = "20261124_01"
down_revision = None
branch_labels = None
depends_on = None

_SQL = pathlib.Path(__file__).resolve().parents[2] / "schema" / "industry_tags.sql"


def upgrade() -> None:
    op.execute(_SQL.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_industry_tags_org_level_sort")
    op.execute("DROP TABLE IF EXISTS industry_tags")
