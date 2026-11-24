-- ============================================================================
-- 字典中心 · 行业分类标签表
-- 单一 DDL 来源：alembic/versions/20261124_01_industry_tags.py 直接读取本文件。
-- 表约定与平台其他业务表一致：id(UUID) / org_id / site_id / created_at /
-- created_by / updated_at / deleted_at（软删除）。金额不涉及本表。
-- ============================================================================

CREATE TABLE IF NOT EXISTS industry_tags (
    id          uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      uuid         NOT NULL,
    site_id     uuid,
    code        varchar(32)  NOT NULL,
    name        varchar(128) NOT NULL,
    parent_code varchar(32),
    level       smallint     NOT NULL,
    gb_code     varchar(8)   NOT NULL,
    gb_name     varchar(128) NOT NULL,
    sort_order  integer      NOT NULL DEFAULT 0,
    created_at  timestamptz  NOT NULL DEFAULT now(),
    created_by  uuid,
    updated_at  timestamptz  NOT NULL DEFAULT now(),
    deleted_at  timestamptz,

    -- 幂等 upsert 的冲突目标：同一个 org 下 code 唯一
    CONSTRAINT uq_industry_tags_org_code UNIQUE (org_id, code),
    -- 层级封顶 3 级
    CONSTRAINT ck_industry_tags_level CHECK (level BETWEEN 1 AND 3),
    -- 父级必须存在；延迟校验，允许同一事务内先读后写的顺序差异
    CONSTRAINT fk_industry_tags_parent FOREIGN KEY (org_id, parent_code)
        REFERENCES industry_tags (org_id, code)
        DEFERRABLE INITIALLY DEFERRED
);

COMMENT ON TABLE  industry_tags IS '行业分类标签字典（层级 ≤ 3，含 GB/T 4754-2017 国标代码映射）';
COMMENT ON COLUMN industry_tags.code       IS '标签业务编码；(org_id, code) 唯一，也是幂等 upsert 的冲突目标';
COMMENT ON COLUMN industry_tags.parent_code IS '父级 code，顶级为 NULL';
COMMENT ON COLUMN industry_tags.level      IS '层级 1=门类 2=大类 3=中类/小类，上限 3';
COMMENT ON COLUMN industry_tags.gb_code    IS 'GB/T 4754-2017 国标代码：门类 A–T、大类 2 位、中类 3 位、小类 4 位';
COMMENT ON COLUMN industry_tags.gb_name    IS '国标原名，与业务化改写后的 name 并存，便于国标换版';
COMMENT ON COLUMN industry_tags.deleted_at IS '软删除标记；非空即已停用，重新导入不会复活该标签';

-- 列表页按 org + 层级 + 排序取数，只扫未删除行
CREATE INDEX IF NOT EXISTS ix_industry_tags_org_level_sort
    ON industry_tags (org_id, level, sort_order)
    WHERE deleted_at IS NULL;
