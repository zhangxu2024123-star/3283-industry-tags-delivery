【交付：行业分类初始化数据脚本】已按验收标准全部完成，在真实环境实测通过：Python 3.11.18 + PostgreSQL 16.16，依赖从 requirements.txt 在全新 venv 里装齐。

■ 交付内容（12 个代码/文档文件 + 10 份实测证据）
1) scripts/seed_industry_tags.py — 初始化脚本：幂等导入 + 写库前全量校验 + 层级报表
2) data/industry_tags.sample.json — 样例数据 100 条（12 门类 / 38 大类 / 50 中类），每条含 gb_code + gb_name（GB/T 4754-2017）
3) schema/industry_tags.sql — 建表 DDL（唯一来源，迁移与测试共用）
4) alembic/versions/20261124_01_industry_tags.py — 建表迁移，直接读取上面那份 DDL，避免三处漂移
5) tests/test_seed_industry_tags.py + tests/conftest.py — 25 个用例，真实 PG16，每用例独立 schema
6) README.md — 运行与测试方法、幂等实现、对接点、回滚
7) docs/PR.md — PR 说明（变更清单 / 表设计理由 / 验收映射 / 风险）
8) evidence/ — 实测输出（环境、pytest、两次导入、迁移往返）

■ 验收标准逐条对应
1. 重复执行不产生重复标签（测试通过）
   实现：唯一键 (org_id, code) + INSERT ... ON CONFLICT DO UPDATE，冲突判定落在数据库约束上而非脚本内存判重，并发安全；命中冲突走 UPDATE，id / created_at 不变。
   实测：第 1 次「新增 100 / 更新 0」，第 2、3 次均「新增 0 / 更新 100」；库内 count=100、count(DISTINCT code)=100、count(DISTINCT (org_id, code))=100。
   用例：test_run_twice_no_duplicate_tags、test_run_three_times_keeps_row_ids_stable。
2. 执行后输出各层级的标签数量
   实现：导入完成后回查数据库（而非统计文件）打印层级分布。
   实测输出：库内层级分布: L1=12  L2=38  L3=50   合计 100
   用例：test_cli_prints_level_distribution_from_db、test_cli_second_run_reports_zero_insert。
3. 样例 JSON 中每个标签都有国标代码映射
   实现：gb_code / gb_name 均必填，gb_code 需符合「门类 A–T 或 2–4 位数字」；缺失或格式错在写库前 exit 2，一行不写。
   实测：100 条全部有映射，库内校验 gb_code 缺失 0 条；用例 test_every_tag_has_gb_mapping。

■ 其他已覆盖的行为
· 已软删除的标签不会被初始化脚本复活（DO UPDATE ... WHERE deleted_at IS NULL），计入「跳过」
· 多租户隔离：同一份数据导入两个 org，互不影响
· 6 组非法数据（缺 gb_code、格式错、level=4、父级不存在、code 重复、缺 parent_code）全部在写库前拦截，库里 0 行
· --dry-run 只校验不写库；--batch-size 不改变结果（batch=7 → 15 批，结果一致）
· 迁移可回滚：upgrade → downgrade base → 再 upgrade，alembic current = 20261124_01 (head)
· 不依赖 alembic 时可用 --ensure-schema 在空库自建表后导入（已实测）

■ 运行方式
export DATABASE_URL='postgresql+psycopg://user:pass@host:5432/db'
python scripts/seed_industry_tags.py --org-id <uuid>          # 正式导入
python scripts/seed_industry_tags.py --org-id <uuid> --dry-run # 只校验
python -m pytest tests -v                                      # 25 passed（3.11.18）

■ 接入平台只需两处对齐
1) 表以 schema/industry_tags.sql 为准；(org_id, code) 上必须存在唯一约束——这是 upsert 的冲突目标，缺失会直接报错，不会静默插重。
2) 连接沿用平台现有 engine 即可；seed(engine, org_id, tags) 是公开函数，可绕过命令行被其他模块复用。
本单不依赖其他工单接口，无 mock 桩。

■ 说明
样例数据仅含行业分类标准条目与虚构标签，无任何真实企业、个人信息，交付物中不出现客户名称。
PR 链接：因当前环境未开通仓库写权限，随附 docs/PR.md 与完整可推送的仓库结构，拿到仓库权限后可直接提 PR，代码无需改动。
