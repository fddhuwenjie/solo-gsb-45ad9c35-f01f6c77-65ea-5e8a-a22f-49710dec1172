from flask import Blueprint, current_app, jsonify, render_template, request

from .db import get_db
from . import services

bp = Blueprint("main", __name__)


def _db():
    return get_db(current_app.config["DB_PATH"])


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


@bp.errorhandler(ApiError)
def _api_error(e):
    return jsonify({"error": e.message}), e.status


@bp.errorhandler(ValueError)
def _value_error(e):
    return jsonify({"error": str(e)}), 400


@bp.route("/")
def index():
    return render_template("index.html")


# ---------- 基础数据 ----------

@bp.route("/api/meta")
def meta():
    conn = _db()
    lines = [dict(x) for x in conn.execute("SELECT * FROM lines ORDER BY name").fetchall()]
    batches = [dict(x) for x in conn.execute(
        "SELECT * FROM batches ORDER BY inspected_at DESC").fetchall()]
    joints = [dict(x) for x in conn.execute(
        "SELECT * FROM joints ORDER BY line_id, mileage").fetchall()]
    return jsonify({"lines": lines, "batches": batches, "joints": joints})


# ---------- 原始记录（只追加） ----------

@bp.route("/api/observations", methods=["POST"])
def create_observation():
    payload = request.get_json(force=True, silent=True) or {}
    conn = _db()
    try:
        result = services.add_observation(conn, payload)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return jsonify(result), 201


@bp.route("/api/reviews", methods=["POST"])
def create_review():
    payload = request.get_json(force=True, silent=True) or {}
    conn = _db()
    try:
        result = services.add_review(conn, payload)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return jsonify(result), 201


# ---------- 批次视图 / 刷新 ----------

@bp.route("/api/batches/<int:batch_id>")
def batch_view(batch_id):
    conn = _db()
    try:
        return jsonify(services.batch_overview(conn, batch_id))
    finally:
        conn.close()


@bp.route("/api/batches/<int:batch_id>/recompute", methods=["POST"])
def batch_recompute(batch_id):
    conn = _db()
    try:
        ids = services.recompute_batch(conn, batch_id)
        conn.commit()
        return jsonify({"recomputed": ids})
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------- 封存 ----------

@bp.route("/api/batches/<int:batch_id>/seal", methods=["POST"])
def seal(batch_id):
    conn = _db()
    try:
        result = services.seal_report(conn, batch_id)
        conn.commit()
        return jsonify(result), 201
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@bp.route("/api/reports")
def reports():
    conn = _db()
    try:
        return jsonify(services.list_reports(conn))
    finally:
        conn.close()


@bp.route("/api/reports/<int:report_id>")
def report_detail(report_id):
    conn = _db()
    try:
        return jsonify(services.get_report(conn, report_id))
    finally:
        conn.close()


# ---------- 证据链 ----------

@bp.route("/api/lines/<int:line_id>/evidence")
def line_evidence(line_id):
    conn = _db()
    try:
        return jsonify(services.line_evidence(conn, line_id))
    finally:
        conn.close()


@bp.route("/api/joints/<int:joint_id>/batches/<int:batch_id>/evidence")
def joint_evidence(joint_id, batch_id):
    conn = _db()
    try:
        return jsonify(services.joint_batch_evidence(conn, joint_id, batch_id))
    finally:
        conn.close()
