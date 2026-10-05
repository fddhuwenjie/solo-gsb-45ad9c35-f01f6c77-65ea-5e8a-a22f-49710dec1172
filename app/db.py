import sqlite3
from datetime import datetime, timezone

SCHEMA_PATH = None


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(path):
    """按 schema.sql 建表（幂等），并在空库时播种演示数据。"""
    global SCHEMA_PATH
    import os
    if SCHEMA_PATH is None:
        SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "schema.sql")
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = f.read()
    conn = get_db(path)
    try:
        conn.executescript(schema)
        conn.commit()
        empty = conn.execute("SELECT COUNT(*) c FROM lines").fetchone()["c"] == 0
        if empty:
            from .seed import seed
            seed(conn)
            conn.commit()
    finally:
        conn.close()


def audit(conn, action, entity, entity_id, detail):
    conn.execute(
        "INSERT INTO audit_log(action, entity, entity_id, detail, logged_at) "
        "VALUES (?,?,?,?,?)",
        (action, entity, entity_id, detail, now_iso()),
    )
