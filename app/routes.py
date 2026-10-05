"""HTTP API。

原始记录一律通过 POST 追加，没有 PUT/DELETE；
更正/补录只是带 supersedes_id 的新观测行。
"""
import json

from flask import Blueprint, abort, jsonify, request

from . import analysis
from .db import get_conn

bp = Blueprint("api", __name__, url_prefix="/api")


def _json():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400, description="请求体必须为 JSON 对象")
    return data


def _require(data, field, numeric=False, nonempty=True):
    if field not in data or data[field] in (None, ""):
        abort(400, description=f"缺少必填字段：{field}")
    val = data[field]
    if numeric:
        try:
            val = float(val)
        except (TypeError, ValueError):
            abort(400, description=f"字段 {field} 必须为数字")
    elif nonempty and (not isinstance(val, str) or not val.strip()):
        abort(400, description=f"字段 {field} 不能为空")
    return val


def _err(exc):
    return jsonify({"error": str(exc)}), 400


# --------------------------------------------------------------------------
# 基础字典
# --------------------------------------------------------------------------

@bp.get("/lines")
def get_lines():
    conn = get_conn()
    rows = conn.execute(
        """SELECT DISTINCT line_name FROM observation
           UNION SELECT DISTINCT line_name FROM review
           ORDER BY line_name"""
    ).fetchall()
    return jsonify({"lines": [r["line_name"] for r in rows]})


@bp.get("/batches")
def get_batches():
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT batch FROM observation ORDER BY batch"
    ).fetchall()
    return jsonify({"batches": [r["batch"] for r in rows]})


@bp.get("/joints")
def get_joints():
    conn = get_conn()
    batch = request.args.get("batch")
    line_name = request.args.get("line_name")
    joints = analysis.list_joints(
        conn, batch=batch, line_name=line_name
    )
    return jsonify({"joints": joints})


# --------------------------------------------------------------------------
# 原始记录：只追加
# --------------------------------------------------------------------------

@bp.post("/observations")
def post_observation():
    data = _json()
    payload = {
        "line_name": _require(data, "line_name"),
        "mileage": _require(data, "mileage", numeric=True),
        "joint_code": _require(data, "joint_code"),
        "batch": _require(data, "batch"),
        "inspector": _require(data, "inspector"),
        "load_low": _require(data, "load_low", numeric=True),
        "load_high": _require(data, "load_high", numeric=True),
        "measure_distance": _require(data, "measure_distance", numeric=True),
        "delta_temp": _require(data, "delta_temp", numeric=True),
        "calibration_version": _require(data, "calibration_version"),
    }
    if payload["load_low"] > payload["load_high"]:
        abort(400, description="负荷区间下限不能大于上限")
    if payload["measure_distance"] < 0:
        abort(400, description="测温距离不能为负")

    supersedes_id = data.get("supersedes_id")
    if supersedes_id not in (None, ""):
        try:
            payload["supersedes_id"] = int(supersedes_id)
        except (TypeError, ValueError):
            abort(400, description="supersedes_id 必须为观测编号")
        note = (data.get("correction_note") or "").strip()
        if not note:
            abort(400, description="更正或补录必须填写 correction_note 原因说明")
        payload["correction_note"] = note

    conn = get_conn()
    try:
        obs_id, conc_id, result, created_at = analysis.insert_observation(
            conn, payload
        )
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        return _err(exc)
    return jsonify({
        "observation_id": obs_id,
        "conclusion_id": conc_id,
        "created_at": created_at,
        "evaluation": result,
    }), 201


@bp.get("/observations")
def get_observations():
    conn = get_conn()
    q = request.args
    obs = analysis.all_observations(
        conn,
        line_name=q.get("line_name"),
        mileage=float(q["mileage"]) if q.get("mileage") else None,
        joint_code=q.get("joint_code"),
        batch=q.get("batch"),
    )
    return jsonify({"observations": obs})


@bp.post("/reviews")
def post_review():
    data = _json()
    kind = _require(data, "kind")
    if kind not in ("retest", "manual"):
        abort(400, description="kind 只能是 retest 或 manual")
    payload = {
        "line_name": _require(data, "line_name"),
        "mileage": _require(data, "mileage", numeric=True),
        "joint_code": _require(data, "joint_code"),
        "batch": _require(data, "batch"),
        "kind": kind,
        "reviewer": _require(data, "reviewer"),
        "note": data.get("note"),
    }
    if kind == "retest":
        payload["result"] = _require(data, "result", numeric=True)
        payload["opinion"] = None
    else:
        opinion = (data.get("opinion") or "").strip()
        if not opinion:
            abort(400, description="人工复核必须填写明确意见 opinion")
        payload["opinion"] = opinion
        payload["result"] = None

    conn = get_conn()
    try:
        rev_id, conc_id, result, created_at = analysis.insert_review(
            conn, payload
        )
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        return _err(exc)
    return jsonify({
        "review_id": rev_id,
        "conclusion_id": conc_id,
        "created_at": created_at,
        "evaluation": result,
    }), 201


@bp.get("/reviews")
def get_reviews():
    conn = get_conn()
    q = request.args
    if q.get("line_name") and q.get("mileage") and q.get("joint_code") and q.get("batch"):
        rows = analysis.reviews_for(
            conn, q["line_name"], float(q["mileage"]),
            q["joint_code"], q["batch"]
        )
    else:
        sql = "SELECT * FROM review WHERE 1=1"
        args = []
        if q.get("line_name"):
            sql += " AND line_name=?"
            args.append(q["line_name"])
        if q.get("batch"):
            sql += " AND batch=?"
            args.append(q["batch"])
        sql += " ORDER BY id"
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    return jsonify({"reviews": rows})


# --------------------------------------------------------------------------
# 复核台：接头分组、证据链、结论版本
# --------------------------------------------------------------------------

@bp.get("/board")
def get_board():
    conn = get_conn()
    q = request.args
    batch = q.get("batch")
    line_name = q.get("line_name")
    joints = analysis.list_joints(conn, batch=batch, line_name=line_name)

    items = []
    for j in joints:
        obs = analysis.active_observations(
            conn, j["line_name"], j["mileage"], j["joint_code"], j["batch"]
        )
        reviews = analysis.reviews_for(
            conn, j["line_name"], j["mileage"], j["joint_code"], j["batch"]
        )
        conc = analysis.current_conclusion(
            conn, j["line_name"], j["mileage"], j["joint_code"], j["batch"]
        )
        live = analysis.evaluate(obs, reviews)
        items.append({
            **j,
            "key": analysis.joint_key(
                j["line_name"], j["mileage"], j["joint_code"]),
            "conclusion": _conclusion_public(conc) if conc else None,
            "alignment": live["alignment"],
            "risk": live["risk"],
            "reason": live["reason"],
            "actions": live["actions"],
            "covered": live["current_retest"] or live["current_manual"],
            "current_retest": live["current_retest"],
            "current_manual": live["current_manual"],
            "observation_count": len(obs),
            "review_count": len(reviews),
        })

    order = {"high": 0, "pending": 1, "medium": 2, "low": 3}
    items.sort(key=lambda x: (order.get(x["risk"], 9),
                              x["line_name"], x["mileage"], x["joint_code"]))
    summary = {
        "total": len(items),
        "high": sum(1 for i in items if i["risk"] == "high"),
        "medium": sum(1 for i in items if i["risk"] == "medium"),
        "low": sum(1 for i in items if i["risk"] == "low"),
        "pending": sum(1 for i in items if i["risk"] == "pending"),
        "high_uncovered": sum(
            1 for i in items if i["risk"] == "high" and not i["covered"]),
    }
    return jsonify({"items": items, "summary": summary})


def _conclusion_public(conc):
    return {
        "id": conc["id"],
        "version": conc["version"],
        "created_at": conc["created_at"],
        "status": conc["status"],
        "superseded_by_id": conc["superseded_by_id"],
        "invalidated_reason": conc["invalidated_reason"],
        "alignment": conc["alignment"],
        "risk": conc["risk"],
        "reason": conc["reason"],
        "actions": json.loads(conc["actions_json"]),
        "triggers": json.loads(conc["triggers_json"]),
    }


@bp.get("/evidence")
def get_evidence():
    """按线路查看证据链与历史结论失效原因。"""
    conn = get_conn()
    q = request.args
    line_name = q.get("line_name")
    if not line_name:
        abort(400, description="必须指定 line_name")
    batch = q.get("batch")
    mileage = float(q["mileage"]) if q.get("mileage") else None
    joint_code = q.get("joint_code")

    observations = analysis.all_observations(
        conn, line_name=line_name, mileage=mileage,
        joint_code=joint_code, batch=batch,
    )
    groups = {}
    for o in observations:
        k = analysis.joint_key(o["line_name"], o["mileage"], o["joint_code"])
        g = groups.setdefault(k, {
            "line_name": o["line_name"],
            "mileage": o["mileage"],
            "joint_code": o["joint_code"],
            "observations": [],
            "reviews": [],
            "versions": [],
        })
        g["observations"].append(o)

    reviews = [dict(r) for r in conn.execute(
        "SELECT * FROM review WHERE line_name=? ORDER BY id",
        (line_name,),
    ).fetchall()]
    for r in reviews:
        if batch and r["batch"] != batch:
            continue
        if mileage is not None and float(r["mileage"]) != mileage:
            continue
        if joint_code and r["joint_code"] != joint_code:
            continue
        k = analysis.joint_key(r["line_name"], r["mileage"], r["joint_code"])
        if k in groups:
            groups[k]["reviews"].append(r)

    for k, g in groups.items():
        rows = conn.execute(
            """SELECT * FROM conclusion WHERE line_name=?
               AND mileage=? AND joint_code=?
               ORDER BY batch, version""",
            (g["line_name"], g["mileage"], g["joint_code"]),
        ).fetchall()
        vers = [{"batch": row["batch"], **_conclusion_public(dict(row))}
                for row in rows]
        if batch:
            vers = [v for v in vers if v["batch"] == batch]
        g["versions"] = vers

    return jsonify({"line_name": line_name,
                    "groups": list(groups.values())})


# --------------------------------------------------------------------------
# 封存报告
# --------------------------------------------------------------------------

@bp.get("/reports/preview")
def report_preview():
    conn = get_conn()
    batch = request.args.get("batch")
    if not batch:
        abort(400, description="必须指定 batch")
    snap = analysis.batch_snapshot(conn, batch)
    can_seal = not snap["high_risk_uncovered"] and snap["joint_count"] > 0
    return jsonify({"snapshot": snap, "can_seal": can_seal})


@bp.post("/reports")
def create_report():
    data = _json()
    batch = _require(data, "batch")
    operator = _require(data, "seal_operator")
    conn = get_conn()
    try:
        report_id, snap = analysis.seal_report(conn, batch, operator)
        conn.commit()
    except ValueError as exc:
        conn.rollback()
        return jsonify({"error": str(exc),
                        "blockers": analysis.batch_snapshot(
                            conn, batch)["high_risk_uncovered"]}), 422
    return jsonify({"report_id": report_id, "snapshot": snap}), 201


@bp.get("/reports")
def list_reports():
    conn = get_conn()
    batch = request.args.get("batch")
    sql = """SELECT id, created_at, batch, seal_operator, joint_count,
                    high_risk_count
             FROM sealed_report"""
    args = []
    if batch:
        sql += " WHERE batch=?"
        args.append(batch)
    sql += " ORDER BY id DESC"
    rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    return jsonify({"reports": rows})


@bp.get("/reports/<int:report_id>")
def get_report(report_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM sealed_report WHERE id=?", (report_id,)
    ).fetchone()
    if row is None:
        abort(404, description="封存报告不存在")
    report = dict(row)
    snapshot = json.loads(report.pop("snapshot_json"))
    report["snapshot"] = snapshot
    report["drift"] = analysis.snapshot_drift(conn, snapshot)
    return jsonify(report)


# --------------------------------------------------------------------------
# 演示数据（可重复调用，已有任何观测则跳过）
# --------------------------------------------------------------------------

@bp.post("/admin/seed")
def seed_demo():
    from .seed import run_seed
    conn = get_conn()
    existed = conn.execute(
        "SELECT COUNT(*) AS c FROM observation"
    ).fetchone()["c"]
    if existed:
        return jsonify({"skipped": True,
                        "message": "已存在观测数据，演示数据未写入"})
    created = run_seed(conn)
    conn.commit()
    return jsonify({"ok": True, **created}), 201
