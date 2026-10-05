"""演示数据：一条线路“北线 220kV”、一个批次 2026-09-A，覆盖全部判定分支。

- J-01 可比较/低风险
- J-02 可比较/中风险
- J-03 可比较/高风险且无当前复核 -> 封存阻断
- J-04 高风险 + 当前复测 -> 已覆盖
- J-05 高风险 + 明确人工复核意见 -> 已覆盖
- J-06 相互矛盾（极差 > 3 K）且复核过期 -> 封存阻断
- J-07 证据不足：两条观测距离/负荷不一致，无法对齐
- J-08 证据不足：仅一条观测，随后被更正补录（演示失效链）
"""
from datetime import datetime, timedelta, timezone

from . import analysis

BATCH = "2026-09-A"
LINE = "北线 220kV"
BASE = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)


def t(offset_seconds):
    return (BASE + timedelta(seconds=offset_seconds)).isoformat(timespec="microseconds")


def run_seed(conn):
    # (joint, mileage, [(inspector, dist, delta, load_lo, load_hi, calib), ...])
    observations = [
        ("J-01", 1.250, [
            ("张工", 0.50, 2.1, 300, 360, "CAL-2026Q2"),
            ("李工", 0.55, 2.6, 300, 360, "CAL-2026Q2"),
        ]),
        ("J-02", 2.480, [
            ("张工", 0.48, 7.2, 320, 380, "CAL-2026Q2"),
            ("李工", 0.52, 7.8, 320, 380, "CAL-2026Q2"),
        ]),
        ("J-03", 3.710, [
            ("张工", 0.50, 11.5, 300, 360, "CAL-2026Q2"),
            ("李工", 0.54, 12.1, 300, 360, "CAL-2026Q2"),
        ]),
        ("J-04", 4.960, [
            ("张工", 0.50, 10.8, 300, 360, "CAL-2026Q2"),
            ("李工", 0.52, 11.3, 300, 360, "CAL-2026Q2"),
        ]),
        ("J-05", 6.220, [
            ("张工", 0.47, 13.0, 340, 400, "CAL-2026Q2"),
            ("李工", 0.50, 13.6, 340, 400, "CAL-2026Q2"),
        ]),
        ("J-06", 7.850, [
            ("张工", 0.50, 4.0, 300, 360, "CAL-2026Q2"),
            ("李工", 0.53, 8.9, 300, 360, "CAL-2026Q2"),
        ]),
        ("J-07", 9.130, [
            ("张工", 0.50, 5.1, 300, 360, "CAL-2026Q2"),
            ("李工", 1.10, 5.4, 240, 290, "CAL-2026Q1"),
        ]),
        ("J-08", 10.470, [
            ("张工", 0.50, 3.2, 300, 360, "CAL-2026Q2"),
        ]),
    ]

    clock = 0
    obs_count = 0
    for joint, mileage, readings in observations:
        for inspector, dist, delta, lo, hi, calib in readings:
            clock += 10
            analysis.insert_observation(conn, {
                "line_name": LINE,
                "mileage": mileage,
                "joint_code": joint,
                "batch": BATCH,
                "inspector": inspector,
                "load_low": lo,
                "load_high": hi,
                "measure_distance": dist,
                "delta_temp": delta,
                "calibration_version": calib,
            }, created_at=t(clock))
            obs_count += 1

    # J-04：高风险，当前复测（时间晚于全部观测）
    clock += 60
    conn.execute(
        """INSERT INTO review
           (created_at, line_name, mileage, joint_code, batch, kind,
            result, opinion, reviewer, note)
           VALUES (?,?,?,?,?,'retest',11.0,NULL,?,?)""",
        (t(clock), LINE, 4.960, "J-04", BATCH, "王复核",
         "现场复测温差 11.0 K，确认高风险，已纳入跟踪"),
    )
    # J-05：高风险，明确人工复核意见
    clock += 10
    conn.execute(
        """INSERT INTO review
           (created_at, line_name, mileage, joint_code, batch, kind,
            result, opinion, reviewer, note)
           VALUES (?,?,?,?,?,'manual',NULL,?,?,?)""",
        (t(clock), LINE, 6.220, "J-05", BATCH,
         "经现场检查确认接头绝缘受潮，48 小时内停电更换处理",
         "赵主管", "人工复核：缺陷属实"),
    )
    # J-06：复核记录发生在最新观测之前 -> 过期，不满足“当前复测”
    stale_ts = t(15)
    conn.execute(
        """INSERT INTO review
           (created_at, line_name, mileage, joint_code, batch, kind,
            result, opinion, reviewer, note)
           VALUES (?,?,?,?,?,'retest',5.0,NULL,?,?)""",
        (stale_ts, LINE, 7.850, "J-06", BATCH, "王复核",
         "该复测早于后续矛盾观测，已过期"),
    )

    # 重算受复核影响的三个接头（追加 review 走 insert_review 以驱动版本失效；
    # 这里为固定演示时间戳，直接写库后统一重算）
    for mileage, code in ((4.960, "J-04"), (6.220, "J-05"), (7.850, "J-06")):
        analysis.recompute_group(
            conn, LINE, mileage, code, BATCH,
            invalidate_reason="追加复核记录，处置闭环状态变化",
        )

    # J-08：对唯一观测做更正补录（距离笔误 0.50 -> 0.52），并补一条观测
    clock += 60
    first_j8 = conn.execute(
        """SELECT id FROM observation
           WHERE line_name=? AND mileage=10.470 AND joint_code='J-08' AND batch=?
           ORDER BY id LIMIT 1""",
        (LINE, BATCH),
    ).fetchone()["id"]
    analysis.insert_observation(conn, {
        "line_name": LINE,
        "mileage": 10.470,
        "joint_code": "J-08",
        "batch": BATCH,
        "inspector": "张工",
        "load_low": 300,
        "load_high": 360,
        "measure_distance": 0.52,
        "delta_temp": 3.2,
        "calibration_version": "CAL-2026Q2",
        "supersedes_id": first_j8,
        "correction_note": "测温距离录入笔误，由 0.50 m 更正为 0.52 m",
    }, created_at=t(clock))
    clock += 10
    analysis.insert_observation(conn, {
        "line_name": LINE,
        "mileage": 10.470,
        "joint_code": "J-08",
        "batch": BATCH,
        "inspector": "李工",
        "load_low": 300,
        "load_high": 360,
        "measure_distance": 0.54,
        "delta_temp": 3.5,
        "calibration_version": "CAL-2026Q2",
    }, created_at=t(clock))

    review_count = conn.execute(
        "SELECT COUNT(*) AS c FROM review"
    ).fetchone()["c"]
    conclusion_count = conn.execute(
        "SELECT COUNT(*) AS c FROM conclusion"
    ).fetchone()["c"]
    return {"observations": obs_count + 2,
            "reviews": review_count,
            "conclusion_versions": conclusion_count,
            "batch": BATCH, "line": LINE}
