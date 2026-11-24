"""行业分类字典初始化脚本的测试（真实 PostgreSQL 16，非 mock）。

覆盖三条验收标准：
  1. 重复执行脚本不产生重复标签（test_run_twice_no_duplicate_tags 等）
  2. 执行后输出各层级的标签数量（test_cli_prints_level_distribution_from_db）
  3. 样例 JSON 中每个标签都有国标代码映射（test_every_tag_has_gb_mapping）

另覆盖：数据校验失败不写库、dry-run 不写库、软删除不复活、多租户隔离、
唯一键与父子外键在库层面生效。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from scripts import seed_industry_tags as seedmod

ORG = uuid.UUID("11111111-2222-3333-4444-555555555555")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def load_tags(path: Path | None = None):
    return seedmod.parse_tags(seedmod.load_payload(path or seedmod.DEFAULT_FILE))


def count_rows(engine: sa.Engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(sa.text("SELECT count(*) FROM industry_tags")).scalar_one())


def write_variant(tmp_path: Path, mutate) -> Path:
    """基于样例数据造一个变体文件（mutate 接收 tags 列表并就地改）。"""
    payload = json.loads(seedmod.DEFAULT_FILE.read_text(encoding="utf-8"))
    mutate(payload["tags"])
    out = tmp_path / "variant.json"
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# 验收 3：每个标签都有国标代码映射
# ---------------------------------------------------------------------------
def test_sample_file_has_100_tags_three_levels():
    tags = load_tags()
    dist = seedmod.level_distribution(tags)
    assert len(tags) == 100
    assert dist == {1: 12, 2: 38, 3: 50}


def test_every_tag_has_gb_mapping():
    """验收标准 3：每个标签都带 gb_code + gb_name，且格式合规。"""
    for tag in load_tags():
        assert tag.gb_code, f"{tag.code} 缺 gb_code"
        assert seedmod.GB_CODE_RE.match(tag.gb_code), f"{tag.code} gb_code={tag.gb_code!r} 不合规"
        assert tag.gb_name, f"{tag.code} 缺 gb_name"


def test_parent_child_levels_are_consistent():
    tags = load_tags()
    by_code = {t.code: t for t in tags}
    for tag in tags:
        if tag.level == 1:
            assert tag.parent_code is None
        else:
            parent = by_code[tag.parent_code]
            assert parent.level == tag.level - 1, f"{tag.code} 的父级层级不对"


# ---------------------------------------------------------------------------
# 验收 1：重复执行不产生重复标签
# ---------------------------------------------------------------------------
def test_first_run_inserts_all(table, org_id):
    stats = seedmod.seed(table, org_id, load_tags())
    assert stats["inserted"] == 100
    assert stats["updated"] == 0
    assert stats["skipped_soft_deleted"] == 0
    assert count_rows(table) == 100


def test_run_twice_no_duplicate_tags(table, org_id):
    """验收标准 1：连跑两次，总数恒为 100，且第二次一行都不新增。"""
    tags = load_tags()

    first = seedmod.seed(table, org_id, tags)
    second = seedmod.seed(table, org_id, tags)

    assert first["inserted"] == 100
    assert second["inserted"] == 0, "第二次执行不应新增任何标签"
    assert second["updated"] == 100
    assert count_rows(table) == 100

    with table.connect() as conn:
        distinct = conn.execute(sa.text("SELECT count(DISTINCT code) FROM industry_tags")).scalar_one()
    assert distinct == 100


def test_run_three_times_keeps_row_ids_stable(table, org_id):
    tags = load_tags()
    seedmod.seed(table, org_id, tags)
    with table.connect() as conn:
        before = dict(conn.execute(sa.text("SELECT code, id FROM industry_tags")).all())

    seedmod.seed(table, org_id, tags)

    with table.connect() as conn:
        after = dict(conn.execute(sa.text("SELECT code, id FROM industry_tags")).all())
    assert before == after, "幂等执行不应重建行（id 必须稳定）"


def test_rerun_refreshes_mutated_fields(table, org_id):
    tags = load_tags()
    seedmod.seed(table, org_id, tags)

    with table.begin() as conn:
        conn.execute(
            sa.text("UPDATE industry_tags SET name = :n, gb_code = '0000' WHERE code = '651'"),
            {"n": "被手改过"},
        )

    seedmod.seed(table, org_id, tags)

    with table.connect() as conn:
        row = conn.execute(
            sa.text("SELECT name, gb_code FROM industry_tags WHERE code = '651'")
        ).one()
    assert row.name == "软件开发"
    assert row.gb_code == "651"
    assert count_rows(table) == 100


def test_soft_deleted_tag_is_not_resurrected(table, org_id):
    tags = load_tags()
    seedmod.seed(table, org_id, tags)

    with table.begin() as conn:
        conn.execute(sa.text("UPDATE industry_tags SET deleted_at = now() WHERE code = '882'"))

    stats = seedmod.seed(table, org_id, tags)
    assert stats["skipped_soft_deleted"] == 1
    assert stats["inserted"] == 0

    with table.connect() as conn:
        deleted_at = conn.execute(
            sa.text("SELECT deleted_at FROM industry_tags WHERE code = '882'")
        ).scalar_one()
    assert deleted_at is not None, "已软删除的标签不应被初始化脚本复活"
    assert seedmod.level_counts(table, org_id) == {1: 12, 2: 38, 3: 49}


def test_orgs_are_isolated(table, org_id, other_org_id):
    tags = load_tags()
    seedmod.seed(table, org_id, tags)
    seedmod.seed(table, other_org_id, tags)

    assert count_rows(table) == 200
    assert seedmod.level_counts(table, org_id) == {1: 12, 2: 38, 3: 50}
    assert seedmod.level_counts(table, other_org_id) == {1: 12, 2: 38, 3: 50}


# ---------------------------------------------------------------------------
# 验收 2：执行后输出各层级数量
# ---------------------------------------------------------------------------
def test_cli_prints_level_distribution_from_db(table, db_url, org_id, capsys):
    rc = seedmod.main(["--org-id", str(org_id), "--database-url", db_url])
    out = capsys.readouterr().out

    assert rc == 0
    assert "新增 100" in out
    assert "库内层级分布" in out
    assert "L1=12" in out and "L2=38" in out and "L3=50" in out
    assert "合计 100" in out


def test_cli_second_run_reports_zero_insert(table, db_url, org_id, capsys):
    seedmod.main(["--org-id", str(org_id), "--database-url", db_url])
    capsys.readouterr()
    seedmod.main(["--org-id", str(org_id), "--database-url", db_url])
    out = capsys.readouterr().out

    assert "新增 0" in out
    assert "更新 100" in out
    assert "合计 100" in out


def test_cli_json_output(table, db_url, org_id, capsys):
    rc = seedmod.main(["--org-id", str(org_id), "--database-url", db_url, "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["tags_total"] == 100
    assert payload["inserted"] == 100
    assert payload["db_distribution"] == {"L1": 12, "L2": 38, "L3": 50}
    assert payload["db_total"] == 100


# ---------------------------------------------------------------------------
# dry-run 与批次
# ---------------------------------------------------------------------------
def test_dry_run_writes_nothing(table, db_url, org_id, capsys):
    rc = seedmod.main(["--org-id", str(org_id), "--database-url", db_url, "--dry-run"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "DRY RUN" in out
    assert count_rows(table) == 0


def test_batch_size_does_not_change_result(table, org_id):
    tags = load_tags()
    seedmod.seed(table, org_id, tags, batch_size=7)
    stats = seedmod.seed(table, org_id, tags, batch_size=7)

    assert stats["batches"] == 15  # ceil(100 / 7)
    assert stats["inserted"] == 0
    assert count_rows(table) == 100


# ---------------------------------------------------------------------------
# 校验失败：一行都不写
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "mutate, keyword",
    [
        (lambda tags: tags[0].pop("gb_code"), "gb_code"),
        (lambda tags: tags[0].__setitem__("gb_code", "12A4"), "不符合国标代码格式"),
        (lambda tags: tags[50].__setitem__("level", 4), "level=4"),
        (lambda tags: tags[-1].__setitem__("parent_code", "999"), "父级 999 不在本文件中"),
        (lambda tags: tags[60].__setitem__("code", "011"), "code 在文件内重复"),
        (lambda tags: tags[20].__setitem__("parent_code", None), "缺少 parent_code"),
    ],
)
def test_invalid_data_is_rejected_before_writing(table, org_id, tmp_path, mutate, keyword):
    path = write_variant(tmp_path, mutate)

    with pytest.raises(seedmod.SeedError) as exc:
        seedmod.validate(seedmod.parse_tags(seedmod.load_payload(path)))
    assert keyword in str(exc.value)

    rc = seedmod.main(
        ["--org-id", str(org_id), "--file", str(path), "--database-url", "sqlite://"]
    )
    assert rc == 2
    assert count_rows(table) == 0


def test_cli_error_message_goes_to_stderr(tmp_path, capsys):
    path = write_variant(tmp_path, lambda tags: tags[0].pop("gb_code"))
    rc = seedmod.main(["--org-id", str(ORG), "--file", str(path), "--database-url", "sqlite://"])
    captured = capsys.readouterr()

    assert rc == 2
    assert "ERROR" in captured.err
    assert "gb_code" in captured.err


# ---------------------------------------------------------------------------
# 库层面的约束：唯一键 + 父子外键 + 层级上限
# ---------------------------------------------------------------------------
def test_unique_constraint_blocks_duplicate_row(table, org_id):
    seedmod.seed(table, org_id, load_tags())
    with pytest.raises(sa.exc.IntegrityError):
        with table.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO industry_tags (org_id, code, name, level, gb_code, gb_name)"
                    " VALUES (:org, '651', '重复插入', 3, '651', '软件开发')"
                ),
                {"org": org_id},
            )


def test_parent_fk_is_enforced(table, org_id):
    with pytest.raises(sa.exc.IntegrityError):
        with table.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO industry_tags (org_id, code, name, parent_code, level, gb_code, gb_name)"
                    " VALUES (:org, '999', '孤儿标签', '888', 3, '999', '孤儿标签')"
                ),
                {"org": org_id},
            )


def test_level_check_constraint(table, org_id):
    with pytest.raises(sa.exc.IntegrityError):
        with table.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO industry_tags (org_id, code, name, level, gb_code, gb_name)"
                    " VALUES (:org, '900', '四级标签', 4, '900', '四级标签')"
                ),
                {"org": org_id},
            )


def test_deleted_partial_index_exists(table):
    with table.connect() as conn:
        idx = conn.execute(
            sa.text("SELECT indexname FROM pg_indexes WHERE tablename = 'industry_tags'")
        ).scalars().all()
    assert "ix_industry_tags_org_level_sort" in idx
