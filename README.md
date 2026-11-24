# 行业分类初始化数据脚本（S2-TAX-023）

把行业分类标签初稿导入字典中心 `industry_tags` 表：**100 条**（12 门类 / 38 大类 / 50 中类），
层级封顶 3 级，每条都带 GB/T 4754-2017 国标代码映射。脚本以 `(org_id, code)` 为唯一键做
upsert，**可重复执行，不产生重复标签**，执行后打印各层级的标签数量。

---

## 1 交付内容

| 文件 | 说明 |
|---|---|
| `scripts/seed_industry_tags.py` | 初始化脚本（幂等导入 + 校验 + 层级报表） |
| `data/industry_tags.sample.json` | 虚构但结构完整的样例数据，100 条三级标签 |
| `schema/industry_tags.sql` | 建表 DDL（唯一来源，迁移与测试共用） |
| `alembic/versions/20261124_01_industry_tags.py` | 建表迁移，直接读取上面那份 DDL |
| `tests/test_seed_industry_tags.py` | 25 个用例，跑在真实 PostgreSQL 16 上 |
| `evidence/` | 实测输出：环境、测试、三次导入、库内核对、迁移往返、独立建表、JSON 输出 |
| `README.md` | 本文档（运行与测试方法、对接点、回滚） |

## 2 环境

```
Python  3.11+（实测 3.11.18，另在 3.10.12 上也通过）
PostgreSQL 16（实测 16.16）
SQLAlchemy 2.x（实测 2.0.58）+ psycopg 3（实测 3.3.9）
Alembic 1.20.1
```

依赖清单见 `requirements.txt`，已在**全新虚拟环境**中从零安装并跑通全部用例，
证明交付包不依赖评审环境里预装的包。

```bash
pip install -r requirements.txt
```

连接串只从环境变量读取，**不写进代码、不写进 alembic.ini**：

```bash
export DATABASE_URL='postgresql+psycopg://<user>:<pass>@<host>:5432/<db>'
```

## 3 运行

```bash
# 导入（org_id 必填，写入所有行的 org_id）
python scripts/seed_industry_tags.py --org-id 7c9e6679-7425-40de-944b-e07fc1f90ae7

# 只校验、不写库
python scripts/seed_industry_tags.py --org-id <uuid> --dry-run

# 机器可读结果（给流水线用）
python scripts/seed_industry_tags.py --org-id <uuid> --json

# 自定义数据文件 / 批次大小 / 操作人
python scripts/seed_industry_tags.py --org-id <uuid> \
    --file data/industry_tags.sample.json --batch-size 25 --created-by <uuid>
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--org-id` | 必填 | 租户 UUID，写入 `org_id` |
| `--file` | `data/industry_tags.sample.json` | 数据文件，接受 `{"tags": [...]}` 或裸数组 |
| `--database-url` | 环境变量 `DATABASE_URL` | 连接串 |
| `--batch-size` | 50 | 每批提交行数（每批一个事务） |
| `--site-id` / `--created-by` | 空 | 可选，写入同名列 |
| `--ensure-schema` | 关 | 先建表；真实字典中心由迁移建表，不需要 |
| `--dry-run` | 关 | 只校验并打印计划 |
| `--json` | 关 | JSON 输出 |

退出码：`0` 成功 / `1` 运行或数据库错误 / `2` 数据校验失败（**此时一行都不写**）。

### 实际输出（`evidence/run-1.txt`）

```
=== 行业分类标签导入 ===
org_id      : 7c9e6679-7425-40de-944b-e07fc1f90ae7
数据文件    : industry_tags.sample.json
标签总数    : 100（L1=12  L2=38  L3=50）
批次        : 2 批 × 50
写入模式    : 幂等 upsert，唯一键 (org_id, code)
---
本次结果    : 新增 100 / 更新 0 / 跳过(已软删) 0
库内层级分布: L1=12  L2=38  L3=50   合计 100
```

再执行一次（`evidence/run-2.txt`）——**新增 0 / 更新 100**，库内仍是 100 条：

```
本次结果    : 新增 0 / 更新 100 / 跳过(已软删) 0
库内层级分布: L1=12  L2=38  L3=50   合计 100
```

「库内层级分布」这一行是导入完成后**回查数据库**得到的，不是拿文件统计出来的，所以能反映真实入库结果。

## 4 幂等的实现

```sql
INSERT INTO industry_tags (org_id, site_id, code, name, parent_code, level,
                           gb_code, gb_name, sort_order, created_by)
VALUES (...)
ON CONFLICT (org_id, code) DO UPDATE
   SET name = EXCLUDED.name, parent_code = EXCLUDED.parent_code, level = EXCLUDED.level,
       gb_code = EXCLUDED.gb_code, gb_name = EXCLUDED.gb_name,
       sort_order = EXCLUDED.sort_order, updated_at = now()
 WHERE industry_tags.deleted_at IS NULL
RETURNING (xmax = 0) AS inserted
```

四个要点：

1. **冲突目标**是库里的唯一约束 `uq_industry_tags_org_code (org_id, code)`，不是脚本内的判重，
   所以并发或多实例同时跑也不会插重。
2. **写入顺序按 level 升序**（门类 → 大类 → 中类），父级先落库，满足自引用外键；
   外键同时声明 `DEFERRABLE INITIALLY DEFERRED`，同一事务内顺序不敏感。
3. **已软删除的标签不复活**：`DO UPDATE ... WHERE deleted_at IS NULL` 让停用的标签保持停用，
   本次计入「跳过(已软删)」。业务上删掉的分类不该被初始化脚本悄悄带回来。
4. **不重建行**：命中冲突走 UPDATE，`id` 与 `created_at` 保持不变（有测试断言 `id` 稳定），
   下游引用 `id` 的地方不会失效。

## 5 数据校验（写库前全量校验，失败一行不写）

- `code`：门类为 `A`–`T`，其余 2–4 位数字；文件内不得重复。
- `level`：1–3，封顶 3 级（库层面另有 `CHECK (level BETWEEN 1 AND 3)`）。
- `parent_code`：level 1 必须为空；level > 1 必须存在且在本文件内，父级 level 恰为 `level-1`。
- `gb_code`：必填，且形如 `A`–`T`（门类）或 2–4 位数字（大类/中类/小类）。
- `gb_name`：必填。

校验一次性汇总**所有**问题后退出（`exit 2`），而不是只报第一条，便于修数据。

## 6 测试

```bash
export TEST_DATABASE_URL='postgresql+psycopg://<user>:<pass>@<host>:5432/<db>'
python -m pytest tests -v
```

每个用例建一个独立 schema（`t_seed_xxx`），跑完 `DROP SCHEMA ... CASCADE`，互不干扰；
用的是真实 PostgreSQL 16，不是 SQLite、不是 mock。

实测结果（`evidence/pytest-summary.txt` / `evidence/pytest-verbose.txt`，
Python 3.11.18 + PostgreSQL 16.16，全新 venv 安装）：

```
25 passed in 4.25s
```

覆盖范围：

| 关注点 | 用例 |
|---|---|
| 验收 1：重复执行不产生重复标签 | `test_run_twice_no_duplicate_tags`、`test_run_three_times_keeps_row_ids_stable`、`test_first_run_inserts_all` |
| 验收 2：输出各层级数量 | `test_cli_prints_level_distribution_from_db`、`test_cli_second_run_reports_zero_insert`、`test_cli_json_output` |
| 验收 3：每条都有国标映射 | `test_every_tag_has_gb_mapping`、`test_sample_file_has_100_tags_three_levels` |
| 幂等语义边界 | `test_rerun_refreshes_mutated_fields`（手改字段被刷回）、`test_soft_deleted_tag_is_not_resurrected`（软删不复活）、`test_orgs_are_isolated`（多租户隔离） |
| 数据校验 | 6 组参数化非法数据（缺 gb_code、gb_code 格式错、level=4、父级不存在、code 重复、缺 parent_code）→ `exit 2` 且**库里 0 行** |
| 库层约束 | `test_unique_constraint_blocks_duplicate_row`、`test_parent_fk_is_enforced`、`test_level_check_constraint`、`test_deleted_partial_index_exists` |
| 运行模式 | `test_dry_run_writes_nothing`、`test_batch_size_does_not_change_result`（batch=7 → 15 批，结果一致） |

## 7 迁移

```bash
export DATABASE_URL='postgresql+psycopg://...'
alembic upgrade head      # 建表 + 唯一键 + 层级 CHECK + 父子外键 + 部分索引
alembic downgrade base    # 删表（可回滚）
alembic current           # 20261124_01 (head)
```

往返实测见 `evidence/migration.txt`：upgrade → 两张表（`alembic_version`、`industry_tags`）→
downgrade 后 `industry*` 表数为 0 → 再 upgrade 成功。

> 说明：`alembic check`（autogenerate 漂移检查）需要 ORM `MetaData`，本单是原生 SQL 迁移，
> 不提供 metadata，故该子命令不适用；版本一致性用 `alembic current` 校验。

## 8 与平台字典中心的对接点

本单可独立跑通，接入平台时只需两处对齐（其余不用改）：

1. **表**：以 `schema/industry_tags.sql` 为准。若平台已有等价的 `industry_tags` 表，
   请确认 `(org_id, code)` 上存在唯一约束——这是 upsert 的冲突目标，缺了脚本会直接报
   `there is no unique or exclusion constraint matching the ON CONFLICT specification`，
   不会静默插重。
2. **连接**：沿用平台现有的 SQLAlchemy engine / 会话工厂即可，脚本只要求传一个
   `DATABASE_URL` 或复用已有 engine（`seed(engine, org_id, tags)` 是公开函数，
   不需要走命令行）。

未依赖任何其他工单的接口，因此没有 mock 桩；若后续字典中心提供批量导入 API，
本文的 `seed()` 可直接替换为对 API 的调用，输出与统计逻辑不变。

## 9 回滚

- 单次导入回滚（只撤本 org 的这批标签）：

  ```sql
  DELETE FROM industry_tags WHERE org_id = :org_id;                    -- 谨慎：物理删除
  UPDATE industry_tags SET deleted_at = now() WHERE org_id = :org_id;  -- 推荐：软删
  ```

- 结构回滚：`alembic downgrade base`（删表）。
- 建议先在 `--dry-run` 下确认标签数量与层级分布，再正式导入。

## 10 数据说明

样例数据只有行业分类的标准条目（GB/T 4754-2017 公开分类），没有任何真实企业、个人信息，
也不含客户名称；`gb_name` 保留国标原名，`name` 是业务化改写后的标签名，便于后续国标换版时对照。

## 11 交付要求对照

| 要求（任务原文） | 落实 | 证据 |
|---|---|---|
| 初始化脚本导入字典中心 | `scripts/seed_industry_tags.py`，写入 `industry_tags` | `evidence/run-1.txt` |
| 80–120 个标签 | 100 条 | `evidence/db-verify.txt` |
| 层级不超过 3 级 | 12/38/50，`CHECK (level BETWEEN 1 AND 3)` | `evidence/migration.txt`、`test_level_check_constraint` |
| 含国标代码映射 | 每条都有 `gb_code` + `gb_name`；库里 `missing_gb = 0` | `evidence/db-verify.txt`、`test_every_tag_has_gb_mapping` |
| 脚本可重复执行（幂等） | 唯一键 + `ON CONFLICT DO UPDATE`；第 2、3 次均 `新增 0` | `evidence/run-1.txt`、`run-2.txt`、`test_run_twice_no_duplicate_tags` |
| 执行后输出导入数量与层级分布 | 打印「本次结果」+「库内层级分布」（回查库） | `evidence/run-1.txt` |
| 附虚构但结构完整的样例 JSON | `data/industry_tags.sample.json`，100 条全字段 | `test_sample_file_has_100_tags_three_levels` |
| README 含运行与测试方法 | 第 2–6 节 | — |
| 附测试结果 | 25 passed（3.11.18 / PG 16.16） | `evidence/pytest-summary.txt`、`pytest-verbose.txt` |
| 统一技术约定：PG16 / SQLAlchemy 2 / Alembic / Python 3.11+ | 全部满足，全新 venv 复验 | `evidence/env.txt` |
| 业务表字段 id/org_id/site_id/created_at/created_by/updated_at/deleted_at | 表结构逐字段一致 | `evidence/migration.txt` |
| 配置与密钥只从环境变量读取 | `DATABASE_URL`；`alembic.ini` 的 url 留空 | 第 2 节、`alembic/env.py` |
| 不引入向境外发送数据的第三方 SDK | 依赖仅 stdlib + SQLAlchemy + psycopg + alembic + pytest，无任何网络/遥测调用 | `requirements.txt`、全量导入扫描 |
| 依赖其他工单接口时用 mock 并写明对接点 | 本单无跨工单依赖，无 mock；对接点见第 8 节 | 第 8 节 |
| 只用虚构数据、不含客户名称 | 仅行业分类标准条目 | 第 10 节 |
| 交付物：脚本、样例 JSON、执行说明 | 均有 | 第 1 节 |
| 提交仓库/PR 链接 | ✅ 已推送公开仓库，main 分支可匿名克隆：`https://github.com/zhangxu2024123-star/3283-industry-tags-delivery`；PR 说明见 `docs/PR.md` | 仓库 HEAD `f64083b` |

