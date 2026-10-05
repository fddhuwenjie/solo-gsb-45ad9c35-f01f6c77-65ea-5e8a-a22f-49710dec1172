"""SQLite 连接与建表。

原始观测（observation）与复核记录（review）只追加、不覆盖；
更正通过新的观测行 + supersedes_id 指向旧行实现，旧行 active 置 0。
结论（conclusion）按版本保存，失效仅改状态并登记失效原因，历史行不删除。
"""
import os
import sqlite3

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS observation (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT NOT NULL,           -- 服务端 UTC ISO8601
    line_name           TEXT NOT NULL,
    mileage             REAL NOT NULL,           -- 线路里程 km
    joint_code          TEXT NOT NULL,
    batch               TEXT NOT NULL,           -- 巡检批次
    inspector           TEXT NOT NULL,
    load_low            REAL NOT NULL,           -- 负荷区间下限 A
    load_high           REAL NOT NULL,           -- 负荷区间上限 A
    measure_distance    REAL NOT NULL,           -- 测温距离 m
    delta_temp          REAL NOT NULL,           -- 温差读数 K
    calibration_version TEXT NOT NULL,           -- 设备校准版本
    is_correction       INTEGER NOT NULL DEFAULT 0,
    supersedes_id       INTEGER REFERENCES observation(id),
    correction_note     TEXT,
    active              INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_obs_group
    ON observation(line_name, mileage, joint_code, batch);
CREATE INDEX IF NOT EXISTS idx_obs_batch ON observation(batch);

CREATE TABLE IF NOT EXISTS review (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    line_name   TEXT NOT NULL,
    mileage     REAL NOT NULL,
    joint_code  TEXT NOT NULL,
    batch       TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('retest', 'manual')),
    result      REAL,                            -- 复测温差 K（retest）
    opinion     TEXT,                            -- 明确人工复核意见（manual）
    reviewer    TEXT NOT NULL,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_review_group
    ON review(line_name, mileage, joint_code, batch);

CREATE TABLE IF NOT EXISTS conclusion (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    line_name            TEXT NOT NULL,
    mileage              REAL NOT NULL,
    joint_code           TEXT NOT NULL,
    batch                TEXT NOT NULL,
    version              INTEGER NOT NULL,
    created_at           TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'current'
                                       CHECK (status IN ('current','invalidated')),
    superseded_by_id     INTEGER REFERENCES conclusion(id),
    invalidated_reason   TEXT,
    alignment            TEXT NOT NULL,          -- comparable/insufficient/contradictory
    risk                 TEXT NOT NULL,          -- high/medium/low/pending
    reason               TEXT NOT NULL,
    actions_json         TEXT NOT NULL,
    triggers_json        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conc_group
    ON conclusion(line_name, mileage, joint_code, batch, version);
CREATE INDEX IF NOT EXISTS idx_conc_batch ON conclusion(batch, status);

CREATE TABLE IF NOT EXISTS sealed_report (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT NOT NULL,
    batch            TEXT NOT NULL,
    seal_operator    TEXT NOT NULL,
    joint_count      INTEGER NOT NULL,
    high_risk_count  INTEGER NOT NULL,
    snapshot_json    TEXT NOT NULL
);
"""


def get_conn():
    if "db" not in g:
        path = current_app.config["DATABASE"]
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        g.db = conn
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()
