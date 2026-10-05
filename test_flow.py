"""端到端流程验证（临时库，跑完即删）。"""
import os
import tempfile

os.environ["PYTHONPATH"] = "/workspace/.home/.local/lib/python3.11/site-packages"

from app import create_app

tmpdir = tempfile.mkdtemp()
db_path = os.path.join(tmpdir, "test.db")
os.environ["CABLE_DB"] = db_path
app = create_app()
client = app.test_client()

failures = []


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), name, detail)
    if not cond:
        failures.append(name)


# 1. 空状态
r = client.get("/api/board")
check("empty board", r.get_json()["summary"]["total"] == 0)

# 2. 两条可比较、低风险观测
base = dict(line_name="L1", mileage=1.25, joint_code="J1", batch="B1",
            load_low=300, load_high=360, measure_distance=0.5,
            calibration_version="CAL-1")
r = client.post("/api/observations", json={**base, "inspector": "甲", "delta_temp": 2.0})
d = r.get_json()
cid1 = d["conclusion_id"]
check("first obs -> pending(insufficient)",
      r.status_code == 201 and d["evaluation"]["alignment"] == "insufficient"
      and d["evaluation"]["risk"] == "pending")

r = client.post("/api/observations", json={**base, "inspector": "乙", "delta_temp": 2.5,
                                           "measure_distance": 0.6})
d = r.get_json()
cid2 = d["conclusion_id"]
check("second obs -> comparable/low, new version",
      d["evaluation"]["alignment"] == "comparable" and d["evaluation"]["risk"] == "low"
      and cid2 != cid1)

# 3. 历史结论已失效，带原因
r = client.get("/api/evidence?line_name=L1")
g = r.get_json()["groups"][0]
check("evidence versions count=2", len(g["versions"]) == 2,
      f"got {len(g['versions'])}")
v1 = g["versions"][0]
check("v1 invalidated with reason",
      v1["status"] == "invalidated" and "追加观测" in (v1["invalidated_reason"] or ""),
      v1.get("invalidated_reason"))

# 4. 中风险
client.post("/api/observations", json={**base, "joint_code": "J2", "inspector": "甲", "delta_temp": 6.2})
r = client.post("/api/observations", json={**base, "joint_code": "J2", "inspector": "乙", "delta_temp": 6.5})
check("medium", r.get_json()["evaluation"]["risk"] == "medium")

# 5. 高风险、未覆盖 -> 封存阻断
client.post("/api/observations", json={**base, "joint_code": "J3", "inspector": "甲", "delta_temp": 11.0})
client.post("/api/observations", json={**base, "joint_code": "J3", "inspector": "乙", "delta_temp": 11.5})
r = client.get("/api/reports/preview?batch=B1")
prev = r.get_json()
check("preview can_seal False", prev["can_seal"] is False)
check("blocker contains J3",
      any("J3" in x for x in prev["snapshot"]["high_risk_uncovered"]),
      str(prev["snapshot"]["high_risk_uncovered"]))

r = client.post("/api/reports", json={"batch": "B1", "seal_operator": "主管"})
check("seal rejected 422", r.status_code == 422 and "J3" in str(r.get_json()["blockers"]))

# 6. 人工复核（明确意见）-> 可封存
r = client.post("/api/reviews", json={
    "line_name": "L1", "mileage": 1.25, "joint_code": "J3", "batch": "B1",
    "kind": "manual", "reviewer": "主管", "opinion": "确认缺陷，停电更换",
})
check("manual review -> covered, new conclusion",
      r.get_json()["evaluation"]["current_manual"] is True
      and any(a["resolved"] for a in r.get_json()["evaluation"]["actions"]))

# 7. 中风险 J2 不阻断（规则只管高风险）
r = client.get("/api/reports/preview?batch=B1")
check("preview can_seal True", r.get_json()["can_seal"])

r = client.post("/api/reports", json={"batch": "B1", "seal_operator": "主管"})
seal = r.get_json()
rid = seal["report_id"]
check("sealed", r.status_code == 201 and rid)

# 8. 封存后更正高风险接头观测 -> 快照不变 + 漂移提示
obs_list = client.get("/api/observations?line_name=L1&mileage=1.25&joint_code=J3&batch=B1").get_json()["observations"]
target = [o for o in obs_list if o["active"]][0]["id"]
r = client.post("/api/observations", json={
    **base, "joint_code": "J3", "inspector": "甲", "delta_temp": 11.2,
    "supersedes_id": target, "correction_note": "读数笔误更正",
})
check("correction accepted, old kept", r.status_code == 201)

r = client.get(f"/api/reports/{rid}")
rep = r.get_json()
snap_item = [i for i in rep["snapshot"]["items"] if i["joint_code"] == "J3"][0]
sealed_conc_id = snap_item["conclusion_id"]
check("snapshot frozen (still has reason text)", bool(snap_item["reason"]))
check("drift detects J3",
      any(d["joint_code"] == "J3" and d["current_conclusion_id"] != sealed_conc_id
          for d in rep["drift"]),
      str([(d["joint_code"], d["sealed_conclusion_id"], d["current_conclusion_id"])
           for d in rep["drift"]]))

# 旧观测仍在且不可覆盖
all_obs = client.get("/api/observations?line_name=L1&mileage=1.25&joint_code=J3&batch=B1").get_json()["observations"]
check("old obs retained inactive",
      any(o["id"] == target and o["active"] == 0 for o in all_obs)
      and any(o["active"] == 1 and o["is_correction"] == 1 for o in all_obs))

# 不能重复更正同一行
r = client.post("/api/observations", json={
    **base, "joint_code": "J3", "inspector": "甲", "delta_temp": 11.2,
    "supersedes_id": target, "correction_note": "再改一次",
})
check("double correction rejected", r.status_code == 400)

# 更正必须填原因
r = client.post("/api/observations", json={
    **base, "joint_code": "J3", "inspector": "甲", "delta_temp": 11.2,
    "supersedes_id": [o["id"] for o in all_obs if o["active"]][0],
})
check("correction without note rejected", r.status_code == 400)

# 不能用其他接头的编号发起更正（取 J3 当前生效行）
active_j3 = [o["id"] for o in all_obs if o["active"]][0]
r = client.post("/api/observations", json={
    **base, "joint_code": "J2", "inspector": "甲", "delta_temp": 6.4,
    "supersedes_id": active_j3, "correction_note": "串号更正",
})
check("cross-joint correction rejected", r.status_code == 400)

# 9. 矛盾：同条件极差 > 3K
client.post("/api/observations", json={**base, "joint_code": "J4", "inspector": "甲", "delta_temp": 3.0})
r = client.post("/api/observations", json={**base, "joint_code": "J4", "inspector": "乙", "delta_temp": 8.0})
ev = r.get_json()["evaluation"]
check("contradictory -> high",
      ev["alignment"] == "contradictory" and ev["risk"] == "high")

# 10. 证据不足：距离/负荷/校准不一致
client.post("/api/observations", json={**base, "joint_code": "J5", "inspector": "甲", "delta_temp": 5.0})
r = client.post("/api/observations", json={
    **base, "joint_code": "J5", "inspector": "乙", "delta_temp": 5.2,
    "measure_distance": 1.0, "load_low": 200, "load_high": 250,
    "calibration_version": "CAL-0",
})
ev = r.get_json()["evaluation"]
check("misaligned -> insufficient/pending",
      ev["alignment"] == "insufficient" and ev["risk"] == "pending")

# 11. 过期复测（早于最新观测）不算当前
import time
from app import analysis
with app.app_context():
    from app.db import get_conn
    conn = get_conn()
    # J4 最新观测 id 时间晚，直接插一条更早的 review 再重算
    from datetime import datetime, timezone, timedelta
    old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="microseconds")
    conn.execute(
        """INSERT INTO review (created_at,line_name,mileage,joint_code,batch,kind,result,opinion,reviewer,note)
           VALUES (?,?,1.25,'J4','B1','retest',4.0,NULL,'复核','过期复测')""",
        (old, "L1"))
    analysis.recompute_group(conn, "L1", 1.25, "J4", "B1",
                             invalidate_reason="追加过期复测")
    conn.commit()
r = client.get("/api/board?batch=B1")
j4 = [i for i in r.get_json()["items"] if i["joint_code"] == "J4"][0]
check("stale retest not covered", j4["covered"] is False and j4["risk"] == "high")

# 12. 补录当前复测后 B1 仍可再次封存（新报告）
#     注意：第 8 步更正了 J3，其更正观测晚于原人工复核 -> 需重新给出当前意见
r = client.post("/api/reviews", json={
    "line_name": "L1", "mileage": 1.25, "joint_code": "J3", "batch": "B1",
    "kind": "manual", "reviewer": "主管", "opinion": "更正后数据仍确认缺陷，维持更换处置",
})
check("corrected J3 needs fresh opinion",
      r.get_json()["evaluation"]["current_manual"] is True)
r = client.post("/api/reviews", json={
    "line_name": "L1", "mileage": 1.25, "joint_code": "J4", "batch": "B1",
    "kind": "retest", "reviewer": "复核", "result": 7.5,
})
check("current retest covered", r.get_json()["evaluation"]["current_retest"] is True)
r = client.post("/api/reports", json={"batch": "B1", "seal_operator": "主管"})
check("reseal after follow-up", r.status_code == 201 and r.get_json()["report_id"] != rid)

# 13. 其他批次不受影响
client.post("/api/observations", json={**base, "batch": "B2", "joint_code": "J9",
                                       "inspector": "甲", "delta_temp": 2.0})
r = client.get("/api/evidence?line_name=L1&batch=B1")
b2leak = any(v["batch"] == "B2" for g in r.get_json()["groups"] for v in g["versions"])
check("batch filter on evidence", not b2leak)

# 14. 校验错误
r = client.post("/api/observations", json={**base, "joint_code": "JX",
                                           "load_low": 400, "load_high": 300,
                                           "inspector": "甲", "delta_temp": 1})
check("load range validation", r.status_code == 400)
r = client.post("/api/reviews", json={
    "line_name": "L1", "mileage": 1.25, "joint_code": "J1", "batch": "B1",
    "kind": "manual", "reviewer": "r",
})
check("manual opinion required", r.status_code == 400)
r = client.post("/api/reviews", json={
    "line_name": "L1", "mileage": 99, "joint_code": "NOPE", "batch": "B1",
    "kind": "retest", "reviewer": "r", "result": 1,
})
check("review without obs rejected", r.status_code == 400)

print()
if failures:
    print(f"{len(failures)} FAILURES:", failures)
    raise SystemExit(1)
print("ALL TESTS PASSED")
