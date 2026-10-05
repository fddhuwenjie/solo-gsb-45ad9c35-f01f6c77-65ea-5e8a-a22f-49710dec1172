import json

from .db import now_iso, audit
from .evaluation import evaluate


# ---------- 读取辅助 ----------

def _active_observations(conn, joint_id, batch_id):
    return conn.execute(
        "SELECT * FROM observations WHERE joint_id=? AND batch_id=? AND superseded=0 "
        "ORDER BY recorded_at, id",
        (joint_id, batch_id),
    ).fetchall()


def _all_observations(conn, joint_id, batch_id):
    return conn.execute(
        "SELECT o.*, p.recorded_at AS corrected_at, p.inspector AS corrected_by "
        "FROM observations o LEFT JOIN observations p ON o.corrects_id = p.id "
        "WHERE o.joint_id=? AND o.batch_id=? ORDER BY o.recorded_at, o.id",
        (joint_id, batch_id),
    ).fetchall()


def _reviews(conn, joint_id, batch_id):
    return conn.execute(
        "SELECT * FROM reviews WHERE joint_id=? AND batch_id=? ORDER BY recorded_at, id",
        (joint_id, batch_id),
    ).fetchall()


def _current_conclusion(conn, joint_id, batch_id):
    return conn.execute(
        "SELECT * FROM conclusions WHERE joint_id=? AND batch_id=? AND invalidated=0 "
        "ORDER BY id DESC LIMIT 1",
        (joint_id, batch_id),
    ).fetchone()


def _joints_touched(conn, batch_id):
    rows = conn.execute(
        "SELECT DISTINCT joint_id FROM observations WHERE batch_id=? "
        "UNION SELECT DISTINCT joint_id FROM reviews WHERE batch_id=?",
        (batch_id, batch_id),
    ).fetchall()
    return [r["joint_id"] for r in rows]


def _gate_for(joint_label, concl, active_obs, reviews):
    """高风险接头封存门槛：必须有当前复测或明确人工复核意见。"""
    if concl is None or concl["risk_level"] != "high":
        return None
    has_retest = any(o["is_retest"] for o in active_obs)
    if has_retest or len(reviews) > 0:
        return None
    return (
        f"高风险接头 {joint_label} 缺少当前复测读数与明确人工复核意见，"
        "封存前必须补齐其中至少一项"
    )


# ---------- 结论重算（只令当前结论失效，再追加新版本） ----------

def recompute_joint(conn, joint_id, batch_id, invalidated_reason=None):
    prev = _current_conclusion(conn, joint_id, batch_id)
    if prev is not None:
        conn.execute(
            "UPDATE conclusions SET invalidated=1, invalidated_reason=? WHERE id=?",
            (invalidated_reason or "被后续证据取代", prev["id"]),
        )

    active = _active_observations(conn, joint_id, batch_id)
    reviews = _reviews(conn, joint_id, batch_id)
    result = evaluate(active, reviews)
    cur = conn.execute(
        "INSERT INTO conclusions(joint_id, batch_id, state, risk_level, confidence, "
        "max_temp_diff_k, spread_k, reason, dispositions, supersedes_id, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            joint_id, batch_id, result["state"], result["risk_level"], result["confidence"],
            result["max_temp_diff_k"], result["spread_k"], result["reason"],
            result["dispositions_json"], prev["id"] if prev else None, now_iso(),
        ),
    )
    return cur.lastrowid


def recompute_batch(conn, batch_id):
    ids = []
    for joint_id in _joints_touched(conn, batch_id):
        ids.append(recompute_joint(conn, joint_id, batch_id, "批次刷新：原当前结论失效并归档"))
    return ids


# ---------- 原始记录写入（只追加，不覆盖） ----------

def add_observation(conn, data):
    required = ["line_id", "mileage", "joint_no", "batch_id", "load_range",
                "measure_distance_m", "temp_diff_k", "calib_version", "inspector"]
    for f in required:
        if data.get(f) in (None, ""):
            raise ValueError(f"缺少必填字段：{f}")
    try:
        dist = float(data["measure_distance_m"])
        diff = float(data["temp_diff_k"])
    except (TypeError, ValueError):
        raise ValueError("测温距离与温差读数必须是数字")
    if dist < 0:
        raise ValueError("测温距离不能为负")

    line = conn.execute("SELECT id FROM lines WHERE id=?", (data["line_id"],)).fetchone()
    if line is None:
        raise ValueError("线路不存在")
    batch = conn.execute("SELECT id FROM batches WHERE id=?", (data["batch_id"],)).fetchone()
    if batch is None:
        raise ValueError("巡检批次不存在")

    # 接头不存在则登记（按线路+里程+编号）
    conn.execute(
        "INSERT OR IGNORE INTO joints(line_id, mileage, joint_no, created_at) VALUES (?,?,?,?)",
        (data["line_id"], data["mileage"].strip(), data["joint_no"].strip(), now_iso()),
    )
    joint = conn.execute(
        "SELECT id FROM joints WHERE line_id=? AND mileage=? AND joint_no=?",
        (data["line_id"], data["mileage"].strip(), data["joint_no"].strip()),
    ).fetchone()
    joint_id = joint["id"]

    corrects_id = data.get("corrects_id")
    is_correction = corrects_id not in (None, "", 0)
    if is_correction:
        corrects_id = int(corrects_id)
        target = conn.execute(
            "SELECT * FROM observations WHERE id=? AND joint_id=? AND batch_id=?",
            (corrects_id, joint_id, data["batch_id"]),
        ).fetchone()
        if target is None:
            raise ValueError("被更正记录不存在，或不属于同一接头同一批次")
        if target["superseded"]:
            raise ValueError(f"观测#{corrects_id} 已被更正，不能再次作为更正对象")

    recorded_at = data.get("recorded_at") or now_iso()
    cur = conn.execute(
        "INSERT INTO observations(joint_id, batch_id, load_range, measure_distance_m, "
        "temp_diff_k, calib_version, inspector, note, is_retest, corrects_id, recorded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            joint_id, data["batch_id"], data["load_range"].strip(), dist, diff,
            data["calib_version"].strip(), data["inspector"].strip(),
            (data.get("note") or "").strip(),
            1 if data.get("is_retest") else 0,
            corrects_id if is_correction else None, recorded_at,
        ),
    )
    new_id = cur.lastrowid

    if is_correction:
        # 只置位标记，原记录内容保持不变
        conn.execute("UPDATE observations SET superseded=1 WHERE id=?", (corrects_id,))
        reason = f"更正录入：观测#{new_id} 更正了观测#{corrects_id}（录入人 {data['inspector'].strip()}），原结论失效"
        audit(conn, "OBSERVATION_CORRECT", "observation", new_id,
              f"corrects #{corrects_id}, joint {joint_id}, batch {data['batch_id']}")
    elif _current_conclusion(conn, joint_id, data["batch_id"]) is not None:
        reason = f"补录录入：在已有结论上新增观测#{new_id}（录入人 {data['inspector'].strip()}），原结论失效"
        audit(conn, "OBSERVATION_ADD", "observation", new_id,
              f"supplement, joint {joint_id}, batch {data['batch_id']}")
    else:
        reason = None
        audit(conn, "OBSERVATION_RECORD", "observation", new_id,
              f"joint {joint_id}, batch {data['batch_id']}")

    new_conclusion_id = recompute_joint(conn, joint_id, data["batch_id"], reason)
    return {"observation_id": new_id, "joint_id": joint_id,
            "conclusion_id": new_conclusion_id, "invalidated_previous": reason is not None}


def add_review(conn, data):
    for f in ("line_id", "mileage", "joint_no", "batch_id", "reviewer", "opinion"):
        if data.get(f) in (None, ""):
            raise ValueError(f"缺少必填字段：{f}")
    joint = conn.execute(
        "SELECT j.id FROM joints j WHERE j.line_id=? AND j.mileage=? AND j.joint_no=?",
        (data["line_id"], data["mileage"].strip(), data["joint_no"].strip()),
    ).fetchone()
    if joint is None:
        raise ValueError("该线路/里程/接头尚未登记，无法挂接复核意见")
    if not conn.execute("SELECT id FROM batches WHERE id=?", (data["batch_id"],)).fetchone():
        raise ValueError("巡检批次不存在")
    joint_id = joint["id"]

    recorded_at = data.get("recorded_at") or now_iso()
    cur = conn.execute(
        "INSERT INTO reviews(joint_id, batch_id, reviewer, opinion, review_type, recorded_at) "
        "VALUES (?,?,?,?,?,?)",
        (joint_id, data["batch_id"], data["reviewer"].strip(),
         data["opinion"].strip(), "人工复核", recorded_at),
    )
    review_id = cur.lastrowid
    reason = (f"新增人工复核意见（{data['reviewer'].strip()}）：{data['opinion'].strip()}，"
              "原结论失效并据复核意见重算")
    audit(conn, "REVIEW_ADD", "review", review_id,
          f"joint {joint_id}, batch {data['batch_id']}")
    new_conclusion_id = recompute_joint(conn, joint_id, data["batch_id"], reason)
    return {"review_id": review_id, "conclusion_id": new_conclusion_id}


# ---------- 批次总览 ----------

def batch_overview(conn, batch_id):
    batch = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
    if batch is None:
        raise ValueError("批次不存在")
    rows = conn.execute(
        "SELECT j.id joint_id, j.mileage, j.joint_no, l.id line_id, l.name line_name, "
        "(SELECT COUNT(*) FROM observations o WHERE o.joint_id=j.id AND o.batch_id=? AND o.superseded=0) active_count, "
        "(SELECT COUNT(*) FROM observations o WHERE o.joint_id=j.id AND o.batch_id=?) total_count, "
        "(SELECT COUNT(*) FROM reviews r WHERE r.joint_id=j.id AND r.batch_id=?) review_count "
        "FROM joints j JOIN lines l ON l.id=j.line_id "
        "WHERE j.id IN (SELECT DISTINCT joint_id FROM observations WHERE batch_id=? "
        "UNION SELECT DISTINCT joint_id FROM reviews WHERE batch_id=?) "
        "ORDER BY l.name, j.mileage",
        (batch_id, batch_id, batch_id, batch_id, batch_id),
    ).fetchall()

    joints, blockers = [], []
    for r in rows:
        concl = _current_conclusion(conn, r["joint_id"], batch_id)
        active = _active_observations(conn, r["joint_id"], batch_id)
        reviews = _reviews(conn, r["joint_id"], batch_id)
        label = f"{r['line_name']} {r['mileage']} {r['joint_no']}"
        blocker = _gate_for(label, concl, active, reviews)
        if blocker:
            blockers.append(blocker)
        d = dict(r)
        d["conclusion"] = dict(concl) if concl else None
        if d["conclusion"]:
            d["conclusion"]["dispositions"] = json.loads(concl["dispositions"])
        d["seal_blocker"] = blocker
        d["has_retest"] = any(o["is_retest"] for o in active)
        joints.append(d)

    sealed = conn.execute(
        "SELECT id, version, sealed_at FROM reports WHERE batch_id=? ORDER BY version DESC",
        (batch_id,),
    ).fetchall()
    return {
        "batch": dict(batch),
        "joints": joints,
        "sealable": len(blockers) == 0 and len(joints) > 0,
        "blockers": blockers,
        "sealed_reports": [dict(x) for x in sealed],
    }


# ---------- 封存报告（固定批次快照 + 门槛校验） ----------

def build_snapshot(conn, batch_id):
    overview = batch_overview(conn, batch_id)
    joints_out = []
    for j in overview["joints"]:
        obs = _all_observations(conn, j["joint_id"], batch_id)
        reviews = _reviews(conn, j["joint_id"], batch_id)
        history = conn.execute(
            "SELECT id, state, risk_level, invalidated_reason, created_at, supersedes_id "
            "FROM conclusions WHERE joint_id=? AND batch_id=? ORDER BY id",
            (j["joint_id"], batch_id),
        ).fetchall()
        joints_out.append({
            "joint_id": j["joint_id"],
            "line_id": j["line_id"], "line_name": j["line_name"],
            "mileage": j["mileage"], "joint_no": j["joint_no"],
            "current_conclusion": j["conclusion"],
            "observations": [dict(o) for o in obs],
            "reviews": [dict(rv) for rv in reviews],
            "conclusion_history": [dict(h) for h in history],
            "seal_blocker": j["seal_blocker"],
        })
    return {
        "generated_at": now_iso(),
        "batch": overview["batch"],
        "thresholds": {
            "high_temp_diff_k": 8.0, "medium_temp_diff_k": 4.0,
            "contradiction_spread_k": 3.0, "distance_tolerance_m": 1.0,
        },
        "joints": joints_out,
        "gate": {"sealable": overview["sealable"], "blockers": overview["blockers"]},
    }


def seal_report(conn, batch_id):
    if not conn.execute("SELECT id FROM batches WHERE id=?", (batch_id,)).fetchone():
        raise ValueError("批次不存在")
    snapshot = build_snapshot(conn, batch_id)
    if not snapshot["gate"]["sealable"]:
        raise ValueError("封存被拒绝：" + "；".join(snapshot["gate"]["blockers"]) or "批次尚无任何接头记录")

    last = conn.execute(
        "SELECT MAX(version) m FROM reports WHERE batch_id=?", (batch_id,)
    ).fetchone()["m"]
    version = (last or 0) + 1
    cur = conn.execute(
        "INSERT INTO reports(batch_id, version, snapshot, sealed_at) VALUES (?,?,?,?)",
        (batch_id, version, json.dumps(snapshot, ensure_ascii=False), now_iso()),
    )
    audit(conn, "REPORT_SEAL", "report", cur.lastrowid,
          f"batch {batch_id} version {version} sealed")
    return {"report_id": cur.lastrowid, "version": version}


def list_reports(conn):
    rows = conn.execute(
        "SELECT r.id, r.batch_id, r.version, r.sealed_at, b.name batch_name, b.inspected_at "
        "FROM reports r JOIN batches b ON b.id=r.batch_id ORDER BY r.id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_report(conn, report_id):
    row = conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
    if row is None:
        raise ValueError("报告不存在")
    out = dict(row)
    out["snapshot"] = json.loads(row["snapshot"])
    return out


# ---------- 线路证据链 ----------

def line_evidence(conn, line_id):
    line = conn.execute("SELECT * FROM lines WHERE id=?", (line_id,)).fetchone()
    if line is None:
        raise ValueError("线路不存在")
    joints = conn.execute(
        "SELECT * FROM joints WHERE line_id=? ORDER BY mileage", (line_id,)
    ).fetchall()
    out_joints = []
    for j in joints:
        batches = conn.execute(
            "SELECT DISTINCT b.id, b.name, b.inspected_at FROM batches b "
            "WHERE b.id IN (SELECT batch_id FROM observations WHERE joint_id=? "
            "UNION SELECT batch_id FROM reviews WHERE joint_id=?) ORDER BY b.inspected_at",
            (j["id"], j["id"]),
        ).fetchall()
        batches_out = []
        for b in batches:
            cur = _current_conclusion(conn, j["id"], b["id"])
            history = conn.execute(
                "SELECT id, state, risk_level, invalidated, invalidated_reason, created_at "
                "FROM conclusions WHERE joint_id=? AND batch_id=? ORDER BY id",
                (j["id"], b["id"]),
            ).fetchall()
            batches_out.append({
                "batch": dict(b),
                "current_conclusion": dict(cur) if cur else None,
                "history": [dict(h) for h in history],
            })
        if batches_out:
            out_joints.append({"joint": dict(j), "batches": batches_out})
    return {"line": dict(line), "joints": out_joints}


def joint_batch_evidence(conn, joint_id, batch_id):
    """单个接头单批次完整证据（原始记录/更正链/复核/结论历史）。"""
    j = conn.execute(
        "SELECT j.*, l.name line_name, l.id line_id FROM joints j JOIN lines l ON l.id=j.line_id "
        "WHERE j.id=?", (joint_id,),
    ).fetchone()
    if j is None:
        raise ValueError("接头不存在")
    b = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
    if b is None:
        raise ValueError("批次不存在")
    obs = _all_observations(conn, joint_id, batch_id)
    reviews = _reviews(conn, joint_id, batch_id)
    history = conn.execute(
        "SELECT * FROM conclusions WHERE joint_id=? AND batch_id=? ORDER BY id",
        (joint_id, batch_id),
    ).fetchall()
    history_out = []
    for h in history:
        d = dict(h)
        d["dispositions"] = json.loads(h["dispositions"])
        history_out.append(d)
    return {
        "joint": dict(j), "batch": dict(b),
        "observations": [dict(o) for o in obs],
        "reviews": [dict(r) for r in reviews],
        "conclusions": history_out,
    }
