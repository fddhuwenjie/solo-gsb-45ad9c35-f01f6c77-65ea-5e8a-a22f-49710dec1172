-- 电缆隧道接头巡检复核台 · 数据库结构
-- 原始观测 / 复核 / 结论 / 报告均为只追加（append-only），不做物理覆盖。

PRAGMA foreign_keys = ON;

-- 线路
CREATE TABLE IF NOT EXISTS lines (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL
);

-- 接头（从属于线路，里程定位）
CREATE TABLE IF NOT EXISTS joints (
    id          INTEGER PRIMARY KEY,
    line_id     INTEGER NOT NULL REFERENCES lines(id),
    mileage     TEXT NOT NULL,          -- 线路里程，如 K12+340
    joint_no    TEXT NOT NULL,          -- 接头编号
    created_at  TEXT NOT NULL,
    UNIQUE(line_id, mileage, joint_no)
);

-- 巡检批次
CREATE TABLE IF NOT EXISTS batches (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,   -- 批次，如 2026-09 隧道一轮
    inspected_at TEXT NOT NULL,         -- 巡检日期
    created_at  TEXT NOT NULL
);

-- 观测原始记录（只追加）
-- 更正：写入 corrects_id 指向被更正记录，被更正记录 superseded=1 但永不删除/改写
-- 补录：corrects_id 为空、batch_id 指向已有结论批次的新记录
CREATE TABLE IF NOT EXISTS observations (
    id                INTEGER PRIMARY KEY,
    joint_id          INTEGER NOT NULL REFERENCES joints(id),
    batch_id          INTEGER NOT NULL REFERENCES batches(id),
    load_range        TEXT NOT NULL,    -- 负荷区间，如 300-380A
    measure_distance_m REAL NOT NULL,   -- 测温距离（米）
    temp_diff_k       REAL NOT NULL,    -- 温差读数（K）
    calib_version     TEXT NOT NULL,    -- 设备校准版本
    inspector         TEXT NOT NULL,
    note              TEXT DEFAULT '',
    is_retest         INTEGER NOT NULL DEFAULT 0,   -- 是否复测读数
    corrects_id       INTEGER REFERENCES observations(id),
    superseded        INTEGER NOT NULL DEFAULT 0,   -- 是否已被更正（仅置位，不改内容）
    recorded_at       TEXT NOT NULL
);

-- 人工复核意见（只追加）
CREATE TABLE IF NOT EXISTS reviews (
    id                INTEGER PRIMARY KEY,
    joint_id          INTEGER NOT NULL REFERENCES joints(id),
    batch_id          INTEGER NOT NULL REFERENCES batches(id),
    reviewer          TEXT NOT NULL,
    opinion           TEXT NOT NULL,    -- 明确复核意见
    review_type       TEXT NOT NULL,    -- 人工复核
    recorded_at       TEXT NOT NULL
);

-- 风险结论（只追加；更正/补录/新增复核只令当前结论 invalidated=1，再追加新结论）
CREATE TABLE IF NOT EXISTS conclusions (
    id                INTEGER PRIMARY KEY,
    joint_id          INTEGER NOT NULL,
    batch_id          INTEGER NOT NULL,
    state             TEXT NOT NULL,    -- comparable / insufficient / contradictory
    risk_level        TEXT NOT NULL,    -- high / medium / low
    confidence        TEXT NOT NULL,    -- high / medium / low
    max_temp_diff_k   REAL,
    spread_k          REAL,
    reason            TEXT NOT NULL,
    dispositions      TEXT NOT NULL,    -- JSON 处置清单
    supersedes_id     INTEGER REFERENCES conclusions(id),
    invalidated       INTEGER NOT NULL DEFAULT 0,
    invalidated_reason TEXT DEFAULT '',
    created_at        TEXT NOT NULL
);

-- 封存报告（同一批次每次封存追加新版本；快照 JSON 固定生成时刻的全部证据）
CREATE TABLE IF NOT EXISTS reports (
    id                INTEGER PRIMARY KEY,
    batch_id          INTEGER NOT NULL,
    version           INTEGER NOT NULL,
    snapshot          TEXT NOT NULL,    -- JSON 批次快照
    sealed_at         TEXT NOT NULL,
    UNIQUE(batch_id, version)
);

-- 审计日志
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY,
    action      TEXT NOT NULL,
    entity      TEXT NOT NULL,
    entity_id   INTEGER,
    detail      TEXT NOT NULL,
    logged_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_obs_jb   ON observations(joint_id, batch_id);
CREATE INDEX IF NOT EXISTS idx_rev_jb   ON reviews(joint_id, batch_id);
CREATE INDEX IF NOT EXISTS idx_conc_jb  ON conclusions(joint_id, batch_id, invalidated);
