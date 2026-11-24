#!/usr/bin/env python3
"""行业分类字典初始化脚本（幂等）。

把一个行业分类标签初稿（80–120 条、层级 ≤ 3、每条都带国标代码映射）导入字典中心的
``industry_tags`` 表。以 ``(org_id, code)`` 为唯一键做 upsert，所以：

* 第一次执行：全部新增；
* 再执行一次：不新增任何行，只刷新可变字段（name / gb_code / gb_name / parent_code /
  level / sort_order / updated_at），标签总数不变；
* 已软删除（deleted_at 非空）的标签不会被复活，计入「跳过」；
* 执行结束后从库里回查各层级的标签数量并打印。

用法::

    export DATABASE_URL='postgresql+psycopg://user:pass@host:5432/db'
    python scripts/seed_industry_tags.py --org-id 3f2a...-uuid
    python scripts/seed_industry_tags.py --org-id 3f2a...-uuid --dry-run
    python scripts/seed_industry_tags.py --org-id 3f2a...-uuid --json

退出码：0 成功；1 运行/数据库错误；2 数据校验失败（此时一行都不会写入）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import sqlalchemy as sa

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILE = REPO_ROOT / "data" / "industry_tags.sample.json"
SCHEMA_SQL = REPO_ROOT / "schema" / "industry_tags.sql"

TABLE = "industry_tags"
MAX_LEVEL = 3
MIN_TAGS, MAX_TAGS = 80, 120

# 标签编码：门类为单个大写字母（A–T），大类/中类/小类为 2–4 位数字
CODE_RE = re.compile(r"^(?:[A-T]|\d{2}|\d{3}|\d{4})$")
# 国标代码：门类是单个字母 A–T（GB/T 4754-2017 共 20 个门类），大类/中类/小类是 2–4 位数字；
# 兼容「字母+数字」的写法（如 A01）
GB_CODE_RE = re.compile(r"^(?:[A-T]|[A-T]?\d{2,4})$")

UPSERT_SQL = sa.text(
    """
INSERT INTO industry_tags
    (org_id, site_id, code, name, parent_code, level, gb_code, gb_name, sort_order, created_by)
VALUES
    (:org_id, :site_id, :code, :name, :parent_code, :level, :gb_code, :gb_name, :sort_order, :created_by)
ON CONFLICT (org_id, code) DO UPDATE
   SET name        = EXCLUDED.name,
       parent_code = EXCLUDED.parent_code,
       level       = EXCLUDED.level,
       gb_code     = EXCLUDED.gb_code,
       gb_name     = EXCLUDED.gb_name,
       sort_order  = EXCLUDED.sort_order,
       updated_at  = now()
 WHERE industry_tags.deleted_at IS NULL
RETURNING (xmax = 0) AS inserted
"""
)

LEVEL_COUNTS_SQL = sa.text(
    """
SELECT level, count(*) AS n
  FROM industry_tags
 WHERE org_id = :org_id
   AND deleted_at IS NULL
 GROUP BY level
 ORDER BY level
"""
)


class SeedError(RuntimeError):
    """数据校验失败：一条都不写，直接退出。"""


@dataclass(frozen=True)
class Tag:
    code: str
    name: str
    parent_code: str | None
    level: int
    gb_code: str
    gb_name: str
    sort_order: int


# ---------------------------------------------------------------------------
# 读取与校验
# ---------------------------------------------------------------------------
def load_payload(path: Path) -> Any:
    if not path.is_file():
        raise SeedError(f"数据文件不存在：{path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SeedError(f"数据文件不是合法 JSON：{path} ({exc})") from exc


def parse_tags(payload: Any) -> list[Tag]:
    """接受 ``{"tags": [...]}`` 或裸数组两种形态。"""
    items = payload.get("tags") if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not items:
        raise SeedError("数据文件里没有 tags 数组（或为空）")

    tags: list[Tag] = []
    for idx, raw in enumerate(items):
        if not isinstance(raw, dict):
            raise SeedError(f"第 {idx + 1} 条不是对象：{raw!r}")
        try:
            tag = Tag(
                code=str(raw["code"]).strip(),
                name=str(raw["name"]).strip(),
                parent_code=(str(raw["parent_code"]).strip() if raw.get("parent_code") else None),
                level=int(raw["level"]),
                gb_code=str(raw["gb_code"]).strip(),
                gb_name=str(raw["gb_name"]).strip(),
                sort_order=int(raw.get("sort_order", 0)),
            )
        except KeyError as exc:
            raise SeedError(f"第 {idx + 1} 条缺少必填字段 {exc.args[0]}：{raw!r}") from exc
        except (TypeError, ValueError) as exc:
            raise SeedError(f"第 {idx + 1} 条字段类型不对：{raw!r} ({exc})") from exc
        tags.append(tag)
    return tags


def validate(tags: Sequence[Tag]) -> list[str]:
    """全量校验；有问题抛 SeedError（汇总所有问题，不是只报第一条）。"""
    problems: list[str] = []
    by_code: dict[str, Tag] = {}

    for tag in tags:
        where = f"[{tag.code or '?'}]"
        if not CODE_RE.match(tag.code):
            problems.append(f"{where} code 不合规（应为 A–T 或 2–4 位数字）")
        if tag.code in by_code:
            problems.append(f"{where} code 在文件内重复")
        else:
            by_code[tag.code] = tag
        if not tag.name:
            problems.append(f"{where} name 为空")
        if not (1 <= tag.level <= MAX_LEVEL):
            problems.append(f"{where} level={tag.level} 超出 1–{MAX_LEVEL}")
        if not tag.gb_code:
            problems.append(f"{where} 缺少 gb_code（每个标签都必须有国标代码映射）")
        elif not GB_CODE_RE.match(tag.gb_code):
            problems.append(f"{where} gb_code={tag.gb_code!r} 不符合国标代码格式")
        if not tag.gb_name:
            problems.append(f"{where} gb_name 为空")

    for tag in tags:
        if tag.level == 1:
            if tag.parent_code:
                problems.append(f"[{tag.code}] 顶级标签不应有 parent_code（{tag.parent_code}）")
            continue
        if not tag.parent_code:
            problems.append(f"[{tag.code}] level={tag.level} 但缺少 parent_code")
            continue
        parent = by_code.get(tag.parent_code)
        if parent is None:
            problems.append(f"[{tag.code}] 父级 {tag.parent_code} 不在本文件中")
        elif parent.level != tag.level - 1:
            problems.append(
                f"[{tag.code}] 父级 {tag.parent_code} 的 level={parent.level}，应为 {tag.level - 1}"
            )

    if problems:
        raise SeedError("数据校验未通过：\n  - " + "\n  - ".join(problems))
    return list(by_code)


def level_distribution(tags: Iterable[Tag]) -> dict[int, int]:
    dist: dict[int, int] = {}
    for tag in tags:
        dist[tag.level] = dist.get(tag.level, 0) + 1
    return dict(sorted(dist.items()))


# ---------------------------------------------------------------------------
# 数据库
# ---------------------------------------------------------------------------
def make_engine(database_url: str) -> sa.Engine:
    return sa.create_engine(database_url, pool_pre_ping=True, future=True)


def ensure_schema(engine: sa.Engine) -> None:
    """建表（幂等）。真实字典中心里表由 alembic 迁移建好，这里只用于独立演示/测试。"""
    ddl = SCHEMA_SQL.read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.exec_driver_sql(ddl)


def seed(
    engine: sa.Engine,
    org_id: uuid.UUID,
    tags: Sequence[Tag],
    *,
    batch_size: int = 50,
    created_by: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
) -> dict[str, int]:
    """按 (org_id, code) 幂等写入；父级先于子级（level 升序），每批一个事务。"""
    if batch_size < 1:
        raise SeedError("--batch-size 必须 ≥ 1")

    ordered = sorted(tags, key=lambda t: (t.level, t.sort_order, t.code))
    rows = [
        {
            "org_id": org_id,
            "site_id": site_id,
            "code": t.code,
            "name": t.name,
            "parent_code": t.parent_code,
            "level": t.level,
            "gb_code": t.gb_code,
            "gb_name": t.gb_name,
            "sort_order": t.sort_order,
            "created_by": created_by,
        }
        for t in ordered
    ]

    stats = {"inserted": 0, "updated": 0, "skipped_soft_deleted": 0, "batches": 0, "rows": len(rows)}
    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        with engine.begin() as conn:
            for row in chunk:
                result = conn.execute(UPSERT_SQL, row).first()
                if result is None:
                    # DO UPDATE 被 deleted_at IS NULL 挡掉：该标签已软删除，保持停用
                    stats["skipped_soft_deleted"] += 1
                elif result[0]:
                    stats["inserted"] += 1
                else:
                    stats["updated"] += 1
        stats["batches"] += 1
    return stats


def level_counts(engine: sa.Engine, org_id: uuid.UUID) -> dict[int, int]:
    with engine.connect() as conn:
        rows = conn.execute(LEVEL_COUNTS_SQL, {"org_id": org_id}).all()
    return {int(level): int(n) for level, n in rows}


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="导入行业分类标签字典（幂等，可重复执行）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python scripts/seed_industry_tags.py --org-id 3f2a.... --dry-run\n"
            "  python scripts/seed_industry_tags.py --org-id 3f2a.... --batch-size 25\n"
        ),
    )
    parser.add_argument("--org-id", required=True, help="租户/组织 UUID（必填，写入 org_id）")
    parser.add_argument("--file", default=str(DEFAULT_FILE), help=f"数据文件，默认 {DEFAULT_FILE.name}")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"),
                        help="数据库连接串，默认取环境变量 DATABASE_URL（不落盘、不写死）")
    parser.add_argument("--batch-size", type=int, default=50, help="每批提交行数，默认 50")
    parser.add_argument("--site-id", default=None, help="可选，写入 site_id")
    parser.add_argument("--created-by", default=None, help="可选，写入 created_by（操作者 UUID）")
    parser.add_argument("--ensure-schema", action="store_true",
                        help="先执行 schema/industry_tags.sql 建表（幂等；真实环境由迁移负责，不需要）")
    parser.add_argument("--dry-run", action="store_true", help="只校验并打印计划，不写任何数据")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果（便于流水线消费）")
    return parser.parse_args(argv)


def _uuid_or_fail(value: str, flag: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise SeedError(f"{flag} 不是合法 UUID：{value!r}") from exc


def _fmt_dist(dist: dict[int, int]) -> str:
    return "  ".join(f"L{level}={n}" for level, n in dist.items()) or "（空）"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        org_id = _uuid_or_fail(args.org_id, "--org-id")
        site_id = _uuid_or_fail(args.site_id, "--site-id") if args.site_id else None
        created_by = _uuid_or_fail(args.created_by, "--created-by") if args.created_by else None

        path = Path(args.file).expanduser().resolve()
        tags = parse_tags(load_payload(path))
        validate(tags)
        dist = level_distribution(tags)
        warnings: list[str] = []
        if not (MIN_TAGS <= len(tags) <= MAX_TAGS):
            warnings.append(f"标签数量 {len(tags)} 不在约定区间 {MIN_TAGS}–{MAX_TAGS}，已放行")
    except SeedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    summary: dict[str, Any] = {
        "org_id": str(org_id),
        "file": str(path),
        "tags_total": len(tags),
        "file_distribution": {f"L{k}": v for k, v in dist.items()},
        "dry_run": bool(args.dry_run),
        "batch_size": args.batch_size,
        "warnings": warnings,
    }

    if args.dry_run:
        if not args.json:
            print("=== 行业分类标签导入（DRY RUN，不会写库） ===")
            print(f"org_id      : {org_id}")
            print(f"数据文件    : {path.name}")
            print(f"标签总数    : {len(tags)}（{_fmt_dist(dist)}）")
            print(f"批次        : {-(-len(tags) // max(args.batch_size, 1))} 批 × {args.batch_size}")
            for w in warnings:
                print(f"WARN        : {w}")
            print("---")
            print("校验通过：code/level/父级/gb_code 均合规，未写入任何行。")
        else:
            print(json.dumps(summary, ensure_ascii=False, indent=1))
        return 0

    if not args.database_url:
        print("ERROR: 缺少数据库连接（--database-url 或环境变量 DATABASE_URL）", file=sys.stderr)
        return 1

    engine = make_engine(args.database_url)
    try:
        if args.ensure_schema:
            ensure_schema(engine)
        stats = seed(
            engine, org_id, tags,
            batch_size=args.batch_size, created_by=created_by, site_id=site_id,
        )
        db_dist = level_counts(engine, org_id)
    except SeedError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except sa.exc.SQLAlchemyError as exc:
        print(f"ERROR: 数据库操作失败：{exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    summary.update(
        {
            "inserted": stats["inserted"],
            "updated": stats["updated"],
            "skipped_soft_deleted": stats["skipped_soft_deleted"],
            "batches": stats["batches"],
            "db_distribution": {f"L{k}": v for k, v in db_dist.items()},
            "db_total": sum(db_dist.values()),
        }
    )

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    else:
        print("=== 行业分类标签导入 ===")
        print(f"org_id      : {org_id}")
        print(f"数据文件    : {path.name}")
        print(f"标签总数    : {len(tags)}（{_fmt_dist(dist)}）")
        print(f"批次        : {stats['batches']} 批 × {args.batch_size}")
        print("写入模式    : 幂等 upsert，唯一键 (org_id, code)")
        for w in warnings:
            print(f"WARN        : {w}")
        print("---")
        print(f"本次结果    : 新增 {stats['inserted']} / 更新 {stats['updated']} / "
              f"跳过(已软删) {stats['skipped_soft_deleted']}")
        print(f"库内层级分布: {_fmt_dist(db_dist)}   合计 {sum(db_dist.values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
