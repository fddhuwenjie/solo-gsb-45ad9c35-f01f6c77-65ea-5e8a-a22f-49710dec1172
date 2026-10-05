"""对齐分析、风险结论、版本管理、封存快照。

可比较（comparable）：两条及以上有效观测，测温距离差 <= DISTANCE_TOL，
                      且负荷区间一致、校准版本一致。
证据不足（insufficient）：有效观测仅一条；或观测之间因条件不一致无法对齐。
相互矛盾（contradictory）：可比较的读数之间极差 > SPREAD_TOL。
"""
import json
from datetime import datetime, timezone

from .db import get_conn

# ---- 判定阈值（也在 README 中说明）-----------------------------------------
DISTANCE_TOL = 0.2   # 测温距离差 m，不超过该值才认为测点可对齐
SPREAD_TOL = 3.0     # 可比较读数的温差极差 K，超过即相互矛盾
HIGH_TEMP = 10.0     # 单组最大温差 >= 该值 -> 高风险
MEDIUM_TEMP = 6.0    # 单组最大温差 >= 该值 -> 中风险


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def joint_key(line_name, mileage, joint_code):
    return f"{line_name}|{float(mileage):.3f}|{joint_code}"


# --------------------------------------------------------------------------
# 查询
# --------------------------------------------------------------------------

def active_observations(conn, line_name, mileage, joint_code, batch):
    """当前生效的观测：沿 supersedes 链取未被取代的行（active=1）。"""
    rows = conn.execute(
        """SELECT * FROM observation
           WHERE line_name=? AND mileage=? AND joint_code=? AND batch=? AND active=1
           ORDER BY id""",
        (line_name, mileage, joint_code, batch),
    ).fetchall()
    return [dict(r) for r in rows]


def all_observations(conn, line_name=None, mileage=None, joint_code=None,
                     batch=None):
    sql = "SELECT * FROM observation WHERE 1=1"
    args = []
    for col, val in (("line_name", line_name), ("mileage", mileage),
                     ("joint_code", joint_code), ("batch", batch)):
        if val is not None:
            sql += f" AND {col}=?"
            args.append(val)
    sql += " ORDER BY id"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def reviews_for(conn, line_name, mileage, joint_code, batch):
    rows = conn.execute(
        """SELECT * FROM review
           WHERE line_name=? AND mileage=? AND joint_code=? AND batch=?
           ORDER BY id""",
        (line_name, mileage, joint_code, batch),
    ).fetchall()
    return [dict(r) for r in rows]


def list_joints(conn, batch=None, line_name=None):
    """全部接头-批次分组（以当前生效观测为准）。"""
    sql = """SELECT DISTINCT line_name, mileage, joint_code, batch
             FROM observation WHERE active=1"""
    args = []
    if batch is not None:
        sql += " AND batch=?"
        args.append(batch)
    if line_name is not None:
        sql += " AND line_name=?"
        args.append(line_name)
    sql += " ORDER BY line_name, mileage, joint_code, batch"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def current_conclusion(conn, line_name, mileage, joint_code, batch):
    row = conn.execute(
        """SELECT * FROM conclusion
           WHERE line_name=? AND mileage=? AND joint_code=? AND batch=?
             AND status='current'
           ORDER BY version DESC LIMIT 1""",
        (line_name, mileage, joint_code, batch),
    ).fetchone()
    return dict(row) if row else None


def conclusion_versions(conn, line_name, mileage, joint_code, batch):
    rows = conn.execute(
        """SELECT * FROM conclusion
           WHERE line_name=? AND mileage=? AND joint_code=? AND batch=?
           ORDER BY version""",
        (line_name, mileage, joint_code, batch),
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# 对齐与风险
# --------------------------------------------------------------------------

def _compatible(a, b):
    """两条观测是否具备可比性的测量条件。"""
    return (
        abs(float(a["measure_distance"]) - float(b["measure_distance"]))
        <= DISTANCE_TOL + 1e-9
        and float(a["load_low"]) == float(b["load_low"])
        and float(a["load_high"]) == float(b["load_high"])
        and a["calibration_version"] == b["calibration_version"]
    )


def _cluster(observations):
    """并查集把测量条件一致的观测归到同一对齐组。"""
    parent = list(range(len(observations)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for i in range(len(observations)):
        for j in range(i + 1, len(observations)):
            if _compatible(observations[i], observations[j]):
                union(i, j)

    groups = {}
    for i in range(len(observations)):
        groups.setdefault(find(i), []).append(observations[i])
    return list(groups.values())


def evaluate(observations, reviews):
    """纯函数：依据有效观测 + 复核记录，返回结论草案（不写库）。

    返回 dict: alignment / risk / reason / actions / max_obs_id /
               newest_obs_ts / current_retest / current_manual /
               triggers
    """
    reviews = reviews or []
    if not observations:
        return None

    groups = _cluster(observations)
    newest_obs = max(observations, key=lambda o: o["id"])
    newest_ts = newest_obs["created_at"]
    max_obs_id = newest_obs["id"]

    # 复核记录是否“当前”：时间不早于最新观测，即针对最新证据做出。
    def is_current(rev):
        return rev["created_at"] >= newest_ts

    cur_retests = [r for r in reviews
                   if r["kind"] == "retest" and is_current(r)]
    cur_manuals = [r for r in reviews
                   if r["kind"] == "manual" and is_current(r)]

    actions = []
    triggers = [f"latest_obs_id={max_obs_id}"]
    for r in reviews:
        triggers.append(f"review_id={r['id']}@{'current' if is_current(r) else 'stale'}")

    if len(observations) == 1:
        o = observations[0]
        alignment = "insufficient"
        reason = (
            f"仅 1 条有效观测（{o['inspector']} 录入，"
            f"温差 {o['delta_temp']} K，校准版本 {o['calibration_version']}），"
            f"缺少同接头同批次的第二次观测，无法交叉核对，证据不足。"
        )
        risk = "pending"
        actions = [
            {"code": "SUPPLEMENT", "text": "按相同负荷区间、测温距离和校准版本补测，形成至少两条可对齐读数", "resolved": False},
        ]
        return _pack(alignment, risk, reason, actions, max_obs_id, newest_ts,
                     cur_retests, cur_manuals, triggers)

    if len(groups) > 1:
        alignment = "insufficient"
        detail = "；".join(
            f"组{g_i + 1}：" + "、".join(
                f"{o['inspector']} {o['delta_temp']}K@距离{o['measure_distance']}m"
                f"/负荷{o['load_low']}-{o['load_high']}A/校准{o['calibration_version']}"
                for o in g
            )
            for g_i, g in enumerate(sorted(groups, key=lambda g: g[0]["id"]))
        )
        reason = (
            f"{len(observations)} 条观测因测温距离差>{DISTANCE_TOL} m、负荷区间"
            "或校准版本不一致而无法对齐，不具备可比基础，证据不足。" + detail
        )
        risk = "pending"
        actions = [
            {"code": "REALIGN", "text": "统一测温距离（差值≤0.2 m）、负荷区间与校准版本后重新观测", "resolved": False},
            {"code": "VERIFY_EQUIPMENT", "text": "核对测温设备校准状态，必要时统一到同一校准版本", "resolved": False},
        ]
        return _pack(alignment, risk, reason, actions, max_obs_id, newest_ts,
                     cur_retests, cur_manuals, triggers)

    readings = [float(o["delta_temp"]) for o in observations]
    spread = max(readings) - min(readings)
    max_temp = max(readings)
    cond = (
        f"{len(observations)} 条观测测量条件一致"
        f"（距离≤{DISTANCE_TOL} m、负荷 {observations[0]['load_low']}-"
        f"{observations[0]['load_high']} A、校准版本 "
        f"{observations[0]['calibration_version']}）"
    )

    if spread > SPREAD_TOL:
        alignment = "contradictory"
        reason = (
            f"{cond}，但温差读数极差 {spread:.1f} K 超过 {SPREAD_TOL:.1f} K"
            f"（{min(readings)}～{max(readings)} K），读数相互矛盾，"
            "无法确认真实温度状态。"
        )
        risk = "high"
        actions =[
            {"code": "RETEST", "text": "立即安排现场复测，确认接头真实温差",
             "resolved": bool(cur_retests)},
            {"code": "MANUAL_REVIEW", "text": "由复核人给出明确人工复核意见（确认缺陷/排除隐患）",
             "resolved": bool(cur_manuals)},
            {"code": "CHECK_EQUIPMENT", "text": "排查测温设备与测点选取问题，保留原始读数",
             "resolved": False},
        ]
        return _pack(alignment, risk, reason, actions, max_obs_id, newest_ts,
                     cur_retests, cur_manuals, triggers)

    alignment = "comparable"
    if max_temp >= HIGH_TEMP:
        risk = "high"
        reason = (
            f"{cond}，读数可比较，极差 {spread:.1f} K；"
            f"最大温差 {max_temp:.1f} K ≥ {HIGH_TEMP:.1f} K，判定高风险。"
        )
        actions = [
            {"code": "RETEST", "text": "立即安排现场复测并留存复测温差",
             "resolved": bool(cur_retests)},
            {"code": "MANUAL_REVIEW", "text": "复核人给出明确处置意见后方可闭环",
             "resolved": bool(cur_manuals)},
            {"code": "MONITOR_LOAD", "text": "加密负荷高峰时段测温，必要时限负荷",
             "resolved": False},
        ]
    elif max_temp >= MEDIUM_TEMP:
        risk = "medium"
        reason = (
            f"{cond}，读数可比较，极差 {spread:.1f} K；"
            f"最大温差 {max_temp:.1f} K 处于 {MEDIUM_TEMP:.1f}～{HIGH_TEMP:.1f} K，"
            "判定中风险，需跟踪观测。"
        )
        actions = [
            {"code": "RETEST", "text": "安排计划内复测，跟踪温差变化趋势",
             "resolved": bool(cur_retests)},
            {"code": "SCHEDULE_MONITOR", "text": "纳入下一巡检批次重点观测",
             "resolved": False},
        ]
    else:
        risk = "low"
        reason = (
            f"{cond}，读数可比较，极差 {spread:.1f} K，"
            f"最大温差 {max_temp:.1f} K < {MEDIUM_TEMP:.1f} K，判定低风险。"
        )
        actions = [
            {"code": "ROUTINE", "text": "按常规周期巡检", "resolved": False},
        ]
    return _pack(alignment, risk, reason, actions, max_obs_id, newest_ts,
                 cur_retests, cur_manuals, triggers)


def _pack(alignment, risk, reason, actions, max_obs_id, newest_ts,
          cur_retests, cur_manuals, triggers):
    return {
        "alignment": alignment,
        "risk": risk,
        "reason": reason,
        "actions": actions,
        "max_obs_id": max_obs_id,
        "newest_obs_ts": newest_ts,
        "current_retest": bool(cur_retests),
        "current_manual": bool(cur_manuals),
        "triggers": triggers,
    }


# --------------------------------------------------------------------------
# 写入：只追加 + 受影响接头结论失效并重新计算
# --------------------------------------------------------------------------

def _invalidate_current(conn, line_name, mileage, joint_code, batch, reason):
    old = current_conclusion(conn, line_name, mileage, joint_code, batch)
    if old is None:
        return None
    conn.execute(
        "UPDATE conclusion SET status='invalidated', invalidated_reason=? WHERE id=?",
        (reason, old["id"]),
    )
    return old


def _save_new_version(conn, line_name, mileage, joint_code, batch, result,
                      invalidated_id, created_at):
    next_version = conn.execute(
        "SELECT COALESCE(MAX(version),0)+1 AS v FROM conclusion "
        "WHERE line_name=? AND mileage=? AND joint_code=? AND batch=?",
        (line_name, mileage, joint_code, batch),
    ).fetchone()["v"]
    cur = conn.execute(
        """INSERT INTO conclusion
           (line_name, mileage, joint_code, batch, version, created_at,
            status, superseded_by_id, invalidated_reason, alignment, risk,
            reason, actions_json, triggers_json)
           VALUES (?,?,?,?,?,?,'current',NULL,NULL,?,?,?,?,?)""",
        (line_name, mileage, joint_code, batch, next_version,
         created_at, result["alignment"], result["risk"], result["reason"],
         json.dumps(result["actions"], ensure_ascii=False),
         json.dumps(result["triggers"], ensure_ascii=False)),
    )
    new_id = cur.lastrowid
    if invalidated_id is not None:
        conn.execute(
            "UPDATE conclusion SET superseded_by_id=? WHERE id=?",
            (new_id, invalidated_id),
        )
    return new_id


def recompute_group(conn, line_name, mileage, joint_code, batch,
                    invalidate_reason=None):
    """让指定接头-批次的当前结论失效并生成新版本，返回 (new_id, eval_result)。"""
    obs = active_observations(conn, line_name, mileage, joint_code, batch)
    reviews = reviews_for(conn, line_name, mileage, joint_code, batch)
    result = evaluate(obs, reviews)
    created_at = now_iso()
    invalidated_id = None
    if invalidate_reason is not None:
        old = _invalidate_current(conn, line_name, mileage, joint_code, batch,
                                  invalidate_reason)
        invalidated_id = old["id"] if old else None
    else:
        old = current_conclusion(conn, line_name, mileage, joint_code, batch)
        if old is not None:
            invalidated_id = old["id"]
            conn.execute(
                """UPDATE conclusion SET status='invalidated',
                   invalidated_reason='系统重算：证据集合变化' WHERE id=?""",
                (old["id"],),
            )

    new_id = None
    if result is not None:
        new_id = _save_new_version(conn, line_name, mileage, joint_code,
                                   batch, result, invalidated_id, created_at)
    return new_id, result


def insert_observation(conn, data, created_at=None):
    """追加一条观测；更正时旧行置 active=0。只影响该接头-批次的结论。"""
    created_at = created_at or now_iso()
    supersedes_id = data.get("supersedes_id")
    correction_note = data.get("correction_note")
    is_correction = 1 if supersedes_id else 0

    if supersedes_id is not None:
        old = conn.execute(
            "SELECT * FROM observation WHERE id=?", (supersedes_id,)
        ).fetchone()
        if old is None:
            raise ValueError("被更正的原始观测不存在")
        if not old["active"]:
            raise ValueError("该观测已被更正，不能重复更正；请更正其最新生效版本")
        target = (data["line_name"], float(data["mileage"]),
                  data["joint_code"], data["batch"])
        if (old["line_name"], old["mileage"], old["joint_code"],
                old["batch"]) != target:
            raise ValueError(
                "更正记录必须与原记录属于同一线路、里程、接头与批次")
        conn.execute(
            "UPDATE observation SET active=0 WHERE id=?", (supersedes_id,)
        )

    cur = conn.execute(
        """INSERT INTO observation
           (created_at, line_name, mileage, joint_code, batch, inspector,
            load_low, load_high, measure_distance, delta_temp,
            calibration_version, is_correction, supersedes_id,
            correction_note, active)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
        (created_at, data["line_name"], float(data["mileage"]),
         data["joint_code"], data["batch"], data["inspector"],
         float(data["load_low"]), float(data["load_high"]),
         float(data["measure_distance"]), float(data["delta_temp"]),
         data["calibration_version"], is_correction, supersedes_id,
         correction_note),
    )
    obs_id = cur.lastrowid

    reason = None
    if is_correction:
        reason = (
            f"观测 #{obs_id} 更正/补录并取代 #{supersedes_id}"
            + (f"：{correction_note}" if correction_note else "")
        )
    else:
        reason = f"追加观测 #{obs_id}，证据集合变化"
    new_id, result = recompute_group(
        conn, data["line_name"], float(data["mileage"]),
        data["joint_code"], data["batch"], invalidate_reason=reason,
    )
    return obs_id, new_id, result, created_at


def insert_review(conn, data, created_at=None):
    """追加复测或人工复核记录；使该接头-批次当前结论失效并重算。"""
    created_at = created_at or now_iso()
    obs = active_observations(conn, data["line_name"],
                              float(data["mileage"]), data["joint_code"],
                              data["batch"])
    if not obs:
        raise ValueError("该接头-批次尚无有效观测，不能登记复核记录")

    cur = conn.execute(
        """INSERT INTO review
           (created_at, line_name, mileage, joint_code, batch, kind,
            result, opinion, reviewer, note)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (created_at, data["line_name"], float(data["mileage"]),
         data["joint_code"], data["batch"], data["kind"],
         data.get("result"), data.get("opinion"), data["reviewer"],
         data.get("note")),
    )
    rev_id = cur.lastrowid
    kind_text = "复测记录" if data["kind"] == "retest" else "人工复核意见"
    reason = f"追加{kind_text} #{rev_id}（{data['reviewer']}），处置闭环状态变化"
    new_id, result = recompute_group(
        conn, data["line_name"], float(data["mileage"]),
        data["joint_code"], data["batch"], invalidate_reason=reason,
    )
    return rev_id, new_id, result, created_at


# --------------------------------------------------------------------------
# 批次快照与封存
# --------------------------------------------------------------------------

def batch_snapshot(conn, batch):
    joints = list_joints(conn, batch=batch)
    items = []
    high_uncovered = []
    for j in joints:
        conc = current_conclusion(conn, j["line_name"], j["mileage"],
                                  j["joint_code"], batch)
        obs = active_observations(conn, j["line_name"], j["mileage"],
                                  j["joint_code"], batch)
        reviews = reviews_for(conn, j["line_name"], j["mileage"],
                              j["joint_code"], batch)
        live = evaluate(obs, reviews)
        item = {
            "line_name": j["line_name"],
            "mileage": j["mileage"],
            "joint_code": j["joint_code"],
            "conclusion_id": conc["id"] if conc else None,
            "conclusion_version": conc["version"] if conc else None,
            "alignment": conc["alignment"] if conc else live["alignment"],
            "risk": conc["risk"] if conc else live["risk"],
            "reason": conc["reason"] if conc else live["reason"],
            "actions": json.loads(conc["actions_json"]) if conc else live["actions"],
            "observation_ids": [o["id"] for o in obs],
            "review_ids": [r["id"] for r in reviews],
            "covered": live["current_retest"] or live["current_manual"],
        }
        items.append(item)
        if item["risk"] == "high" and not item["covered"]:
            high_uncovered.append(
                f"{j['line_name']} K{j['mileage']:.3f} {j['joint_code']}"
            )
    return {
        "batch": batch,
        "generated_at": now_iso(),
        "joint_count": len(items),
        "high_risk_count": sum(1 for i in items if i["risk"] == "high"),
        "high_risk_uncovered": high_uncovered,
        "items": items,
    }


def seal_report(conn, batch, operator):
    snap = batch_snapshot(conn, batch)
    if not snap["items"]:
        raise ValueError("批次中没有任何接头观测，无法封存")
    if snap["high_risk_uncovered"]:
        raise ValueError("存在高风险接头缺少当前复测或明确复核意见，整份报告不得封存")
    cur = conn.execute(
        """INSERT INTO sealed_report
           (created_at, batch, seal_operator, joint_count,
            high_risk_count, snapshot_json)
           VALUES (?,?,?,?,?,?)""",
        (now_iso(), batch, operator, snap["joint_count"],
         snap["high_risk_count"],
         json.dumps(snap, ensure_ascii=False)),
    )
    return cur.lastrowid, snap


def snapshot_drift(conn, snapshot):
    """把封存快照与当前证据对比，给出封存后发生变化的接头。"""
    drifted = []
    for item in snapshot["items"]:
        conc = current_conclusion(conn, item["line_name"], item["mileage"],
                                  item["joint_code"], snapshot["batch"])
        current_obs = active_observations(conn, item["line_name"],
                                          item["mileage"], item["joint_code"],
                                          snapshot["batch"])
        if (conc is None
                or conc["id"] != item["conclusion_id"]
                or [o["id"] for o in current_obs] != item["observation_ids"]):
            drifted.append({
                "line_name": item["line_name"],
                "mileage": item["mileage"],
                "joint_code": item["joint_code"],
                "sealed_conclusion_id": item["conclusion_id"],
                "current_conclusion_id": conc["id"] if conc else None,
            })
    return drifted
