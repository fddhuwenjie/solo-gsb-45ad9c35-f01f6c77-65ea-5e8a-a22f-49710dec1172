"""同一接头 + 同一批次的观测对齐、证据分级与风险结论生成。

分级规则（可通过本模块常量调参）：
- 测温距离差值 > DISTANCE_TOL_M          → 观测条件不一致
- 校准版本不同                            → 观测条件不一致
- 负荷区间无交集                          → 观测条件不一致
- 其余 ≥2 条读数且温差极差 > CONTRADICTION_SPREAD_K → 相互矛盾
- 单条有效读数                            → 证据不足

风险：
- high:    相互矛盾，或最大温差 >= HIGH_TEMP_K（证据不足时降级为 medium）
- medium:  最大温差 >= MED_TEMP_K，或证据不足且偏高
- low:     其余
"""
import json

HIGH_TEMP_K = 8.0
MED_TEMP_K = 4.0
CONTRADICTION_SPREAD_K = 3.0
DISTANCE_TOL_M = 1.0


def parse_load(text):
    """'300-380A' / '300~380' / '300' -> (300.0, 380.0)；无法解析返回 None。"""
    s = (text or "").strip().lower().replace("a", "").replace("～", "~").replace("—", "-")
    for sep in ("~", "-", "至"):
        if sep in s:
            a, b = s.split(sep, 1)
            try:
                lo, hi = float(a.strip()), float(b.strip())
                return (lo, hi) if lo <= hi else (hi, lo)
            except ValueError:
                return None
    try:
        v = float(s)
        return (v, v)
    except ValueError:
        return None


def load_overlap(a, b):
    pa, pb = parse_load(a), parse_load(b)
    if pa is None or pb is None:
        return None  # 无法判定
    return max(pa[0], pb[0]) <= min(pa[1], pb[1])


def _pairwise_issues(obs):
    """返回不一致原因列表（逐条说明哪两条观测、因何不可比）。"""
    issues = []
    for i in range(len(obs)):
        for j in range(i + 1, len(obs)):
            a, b = obs[i], obs[j]
            if abs(a["measure_distance_m"] - b["measure_distance_m"]) > DISTANCE_TOL_M:
                issues.append(
                    f"观测#{a['id']}测温距离{a['measure_distance_m']}m 与 "
                    f"观测#{b['id']}{b['measure_distance_m']}m 相差超过{DISTANCE_TOL_M:g}m"
                )
            if a["calib_version"] != b["calib_version"]:
                issues.append(
                    f"观测#{a['id']}校准版本{a['calib_version']} 与 "
                    f"观测#{b['id']}{b['calib_version']} 不一致"
                )
            ov = load_overlap(a["load_range"], b["load_range"])
            if ov is False:
                issues.append(
                    f"观测#{a['id']}负荷区间{a['load_range']} 与 "
                    f"观测#{b['id']}{b['load_range']} 无交集"
                )
            elif ov is None and a["load_range"] != b["load_range"]:
                issues.append(
                    f"观测#{a['id']}负荷区间“{a['load_range']}” 与 "
                    f"观测#{b['id']}“{b['load_range']}” 无法对齐"
                )
    return issues


def _reading_desc(o):
    tag = "复测" if o["is_retest"] else "初测"
    return (
        f"观测#{o['id']}（{tag}）温差{o['temp_diff_k']}K，负荷{o['load_range']}，"
        f"测温距离{o['measure_distance_m']}m，校准版本{o['calib_version']}"
    )


def _build_dispositions(state, risk, max_diff, has_retest, has_manual, spread):
    d = []
    if state == "contradictory":
        d += [
            {"code": "CHECK_EQUIP", "action": "核对测温设备校准状态，统一校准版本后在相同负荷区间、相同测温距离下重新复测"},
            {"code": "RETEST", "action": "安排第二次复测，至少取得两条同条件可比较读数"},
            {"code": "MANUAL_REVIEW", "action": "人工现场复核接头并出具明确意见"},
        ]
    if risk == "high":
        d.append({"code": "STANDBY", "action": "立即纳入重点盯守，缩短测温周期，必要时转移/限制负荷"})
        if not has_retest and state != "contradictory":
            d.append({"code": "RETEST", "action": "48小时内完成当前复测，复测须在同负荷区间与同测温距离下进行"})
        if not has_manual:
            d.append({"code": "MANUAL_REVIEW", "action": "人工复核并签署明确意见（高风险接头封存前必须具备当前复测或明确复核意见）"})
    elif risk == "medium":
        d.append({"code": "RETEST", "action": "一周内复测确认温升趋势"})
        if not has_manual:
            d.append({"code": "MANUAL_REVIEW", "action": "安排人工复核确认"})
    else:
        d.append({"code": "ROUTINE", "action": "纳入下一批次常规巡检"})

    if has_retest and state != "contradictory":
        d.append({"code": "TREND", "action": "将复测读数与初测并列纳入温升趋势跟踪"})
    if has_manual:
        d.append({"code": "CLOSEOUT", "action": "按人工复核意见执行并归档闭环"})

    # 去重（同 code 合并为一条）
    seen, out = set(), []
    for item in d:
        if item["code"] not in seen:
            seen.add(item["code"])
            out.append(item)
    return out


def evaluate(active_obs, reviews):
    """对同一接头同一批次的当前有效观测 + 复核记录生成结论 dict（不含 joint/batch id）。"""
    temps = [o["temp_diff_k"] for o in active_obs]
    max_diff = max(temps)
    spread = max(temps) - min(temps)
    has_retest = any(o["is_retest"] for o in active_obs)
    has_manual = len(reviews) > 0

    if len(active_obs) <= 1:
        state = "insufficient"
        only_id = f"（{_reading_desc(active_obs[0])}）" if active_obs else "（当前无有效读数）"
        reason = "证据不足：同一接头、同一批次下仅有一条有效读数" + only_id + "，缺少可对齐的同条件观测。"
    else:
        issues = _pairwise_issues(active_obs)
        if issues:
            state = "insufficient"
            reason = "证据不足：读数无法对齐——" + "；".join(issues) + "。"
        elif spread > CONTRADICTION_SPREAD_K:
            state = "contradictory"
            ids = "、".join(f"#{o['id']}" for o in active_obs)
            reason = (
                f"相互矛盾：观测{ids}的测温距离、负荷区间与校准版本均可对齐，"
                f"但温差极差达{spread:.1f}K（>{CONTRADICTION_SPREAD_K:g}K），"
                f"读数{min(temps):g}K～{max(temps):g}K，不能相互印证。"
            )
        else:
            state = "comparable"
            ids = "、".join(f"#{o['id']}" for o in active_obs)
            reason = (
                f"可比较：观测{ids}测温距离、负荷区间、校准版本一致或相容，"
                f"温差极差{spread:.1f}K（≤{CONTRADICTION_SPREAD_K:g}K），读数相互印证，"
                f"最大温差{max_diff:g}K。"
            )

    if state == "contradictory":
        risk, confidence = "high", "high"
    elif state == "insufficient":
        risk = "high" if max_diff >= HIGH_TEMP_K and len(active_obs) > 0 else (
            "medium" if max_diff >= MED_TEMP_K and len(active_obs) > 0 else "low")
        confidence = "low"
    else:
        if max_diff >= HIGH_TEMP_K:
            risk, confidence = "high", "high"
        elif max_diff >= MED_TEMP_K:
            risk, confidence = "medium", "medium"
        else:
            risk, confidence = "low", "high"

    dispositions = _build_dispositions(state, risk, max_diff, has_retest, has_manual, spread)
    reason += f" 综合判定风险等级：{ {'high':'高','medium':'中','low':'低'}[risk] }。"
    if has_retest:
        reason += f" 已有复测读数（{sum(o['is_retest'] for o in active_obs)}条）。"
    if has_manual:
        reason += " 已有人工复核意见。"

    return {
        "state": state,
        "risk_level": risk,
        "confidence": confidence,
        "max_temp_diff_k": max_diff if active_obs else None,
        "spread_k": round(spread, 2),
        "reason": reason,
        "dispositions_json": json.dumps(dispositions, ensure_ascii=False),
    }
