# PR：字典中心 · 行业分类初始化数据脚本（S2-TAX-023）

## 这个 PR 做了什么

新增 `industry_tags` 表与初始化脚本，把行业分类标签初稿（100 条、3 级、带国标代码映射）
导入字典中心。脚本幂等，重复执行不产生重复标签，执行后打印各层级数量。

## 变更清单

```
+ schema/industry_tags.sql                                 建表 DDL（唯一来源）
+ alembic/versions/20261124_01_industry_tags.py            建表迁移（读取上面那份 DDL）
+ scripts/seed_industry_tags.py                            初始化脚本（幂等 upsert + 校验 + 报表）
+ data/industry_tags.sample.json                           样例数据 100 条（12/38/50）
+ tests/test_seed_industry_tags.py                         25 个用例，真实 PG16
+ tests/conftest.py                                        每用例独立 schema
+ README.md                                                运行/测试/对接/回滚
```

## 表设计要点

| 项 | 取值 | 理由 |
|---|---|---|
| 主键 | `id uuid`，`gen_random_uuid()` | 与平台其他业务表一致 |
| 唯一键 | `(org_id, code)` | 幂等 upsert 的冲突目标，也是多租户隔离边界 |
| 层级 | `level smallint CHECK (1..3)` | 层级封顶 3 级 |
| 国标映射 | `gb_code varchar(8) NOT NULL` + `gb_name` | 每个标签必须有映射；`gb_name` 保留原名便于国标换版 |
| 父子 | `FK (org_id, parent_code) → (org_id, code)`，`DEFERRABLE INITIALLY DEFERRED` | 同事务内写入顺序不敏感 |
| 软删除 | `deleted_at timestamptz` | 已停用标签不被初始化脚本复活 |
| 列表索引 | `(org_id, level, sort_order) WHERE deleted_at IS NULL` | 列表页只扫未删除行 |

## 幂等实现

`INSERT ... ON CONFLICT (org_id, code) DO UPDATE ... WHERE deleted_at IS NULL RETURNING (xmax = 0)`：

- 冲突落在数据库唯一约束上，不靠脚本内存判重 → 并发安全；
- 命中冲突走 UPDATE，`id` / `created_at` 不变 → 下游引用不失效；
- 软删除行被 `WHERE` 挡掉 → 保持停用，计入「跳过」；
- 按 level 升序分批（默认 50/批，每批一个事务）→ 父级先落库。

## 验证结果

环境：Python 3.11.18（全新 venv 从 requirements.txt 安装）/ PostgreSQL 16.16 / psycopg 3.3.9 / SQLAlchemy 2.0.58 / Alembic 1.20.1

**测试**：`25 passed in 4.25s`（真实 PG，每用例独立 schema）。见 `evidence/pytest-summary.txt`。

**两次执行**：

```
run 1: 本次结果 新增 100 / 更新 0   / 跳过 0    库内 L1=12 L2=38 L3=50 合计 100
run 2: 本次结果 新增 0   / 更新 100 / 跳过 0    库内 L1=12 L2=38 L3=50 合计 100
```

**库内实测**（`evidence/db-verify.txt`）`SELECT level, count(*) FROM industry_tags GROUP BY level` → 12 / 38 / 50，
`count(*) = 100`、`count(DISTINCT code) = 100`、`count(DISTINCT (org_id, code)) = 100`，`gb_code` 缺失 0 条。
第三次执行仍是 `新增 0 / 更新 100`。

**迁移往返**：upgrade → 表就绪；downgrade → `industry*` 表数 0；再 upgrade 成功；
`alembic current` = `20261124_01 (head)`。另验证了不依赖 alembic 的 `--ensure-schema` 独立建表路径
（全新空库，见 `evidence/ensure-schema.txt`）。

## 验收标准逐条对应

| 验收标准 | 对应实现 / 证据 |
|---|---|
| 1. 重复执行脚本不产生重复标签（测试通过） | 唯一键 + `ON CONFLICT DO UPDATE`；`test_run_twice_no_duplicate_tags`（第二次 inserted=0、总数 100、distinct code 100）、`test_run_three_times_keeps_row_ids_stable` |
| 2. 执行后输出各层级的标签数量 | 导入后回查库，打印 `库内层级分布: L1=12 L2=38 L3=50 合计 100`；`test_cli_prints_level_distribution_from_db` |
| 3. 样例 JSON 中每个标签都有国标代码映射 | 100 条全部含 `gb_code` + `gb_name`；缺失或格式错在写库前 `exit 2`；`test_every_tag_has_gb_mapping` |

## 风险与注意

- **接入前确认** `(org_id, code)` 唯一约束已存在，否则 upsert 会报
  `no unique or exclusion constraint matching the ON CONFLICT specification`（不会静默插重）。
- 样例数据 100 条在约定区间 80–120 内；若二次裁剪到区间外，脚本只告警不阻断。
- 本单不依赖其他工单接口，无 mock 桩；`seed(engine, org_id, tags)` 可直接被其他模块复用。
- 回滚方式见 README 第 9 节（推荐软删，不建议整表 DELETE）。
