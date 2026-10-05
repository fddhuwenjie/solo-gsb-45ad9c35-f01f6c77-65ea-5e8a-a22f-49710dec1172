/* 电缆隧道接头巡检复核台 —— 原生 JavaScript，无任何依赖 */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const RISK_TEXT = { high: "高风险", medium: "中风险", low: "低风险", pending: "待判定（证据不足）" };
const ALIGN_TEXT = {
  comparable: "可比较",
  insufficient: "证据不足",
  contradictory: "相互矛盾",
};

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtKm(m) {
  return "K" + Number(m).toFixed(3);
}

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { /* 空响应 */ }
  if (!res.ok) {
    const msg = (data && (data.error || data.description)) || `请求失败（${res.status}）`;
    const err = new Error(msg);
    err.payload = data;
    err.status = res.status;
    throw err;
  }
  return data;
}

let toastTimer = null;
function toast(msg, isError = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast" + (isError ? " error" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
}

// --------------------------------------------------------------------------
// Tab 切换
// --------------------------------------------------------------------------

$$(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    const name = btn.dataset.tab;
    $$(".panel").forEach((p) =>
      p.classList.toggle("active", p.id === `tab-${name}`));
    if (name === "board") loadBoard();
    if (name === "evidence") loadEvidenceMeta();
    if (name === "reports") { loadBatches($("#rp-batch")); loadReports(); }
  });
});

// --------------------------------------------------------------------------
// 录入：观测
// --------------------------------------------------------------------------

const formObs = $("#form-obs");
const segEntry = $$("#tab-entry .seg")[0];

segEntry.addEventListener("click", (e) => {
  const btn = e.target.closest(".seg-btn");
  if (!btn) return;
  $$("#tab-entry .seg")[0].querySelectorAll(".seg-btn")
    .forEach((b) => b.classList.toggle("active", b === btn));
  const form = btn.dataset.form;
  formObs.hidden = form !== "obs";
  $("#form-review").hidden = form !== "review";
});

$("#correction-cancel").addEventListener("click", () => setCorrectionMode(null));

function setCorrectionMode(obsId) {
  $("#obs-supersedes").value = obsId || "";
  const banner = $("#correction-banner");
  const noteWrap = $("#correction-note-wrap");
  if (obsId) {
    banner.hidden = false;
    noteWrap.hidden = false;
    $("#correction-target").textContent = "#" + obsId;
  } else {
    banner.hidden = true;
    noteWrap.hidden = true;
    formObs.querySelector('[name="correction_note"]').value = "";
  }
}

window.startCorrection = function (obsId) {
  setCorrectionMode(obsId);
  document.querySelector('.tab[data-tab="entry"]').click();
  $("#form-review").hidden = true;
  formObs.hidden = false;
  $$("#tab-entry .seg")[0].querySelectorAll(".seg-btn")
    .forEach((b) => b.classList.toggle("active", b.dataset.form === "obs"));
  window.scrollTo({ top: 0, behavior: "smooth" });
};

formObs.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(formObs);
  const body = {
    line_name: fd.get("line_name").trim(),
    mileage: fd.get("mileage"),
    joint_code: fd.get("joint_code").trim(),
    batch: fd.get("batch").trim(),
    inspector: fd.get("inspector").trim(),
    load_low: fd.get("load_low"),
    load_high: fd.get("load_high"),
    measure_distance: fd.get("measure_distance"),
    delta_temp: fd.get("delta_temp"),
    calibration_version: fd.get("calibration_version").trim(),
  };
  if (fd.get("supersedes_id")) {
    body.supersedes_id = Number(fd.get("supersedes_id"));
    body.correction_note = fd.get("correction_note").trim();
  }
  const box = $("#entry-result");
  try {
    const data = await api("POST", "/api/observations", body);
    box.hidden = false;
    box.className = "card result-card success";
    box.innerHTML = renderEvalResult(data, "观测");
    formObs.reset();
    setCorrectionMode(null);
    toast("观测已追加，原始记录未改动，受影响接头结论已生成新版本");
  } catch (err) {
    box.hidden = false;
    box.className = "card result-card error";
    box.textContent = "提交失败：" + err.message;
    toast(err.message, true);
  }
});

function renderEvalResult(data, label) {
  const ev = data.evaluation;
  const id = data.observation_id ? `观测编号 #${data.observation_id}` : `复核记录 #${data.review_id}`;
  if (!ev) return `<b>${esc(label)}已保存</b>（${id}），尚未形成结论。`;
  return `<b>${esc(label)}已保存</b>（${id}），结论版本 #${esc(data.conclusion_id)}：
    <div style="margin-top:8px">
      <span class="badge ${esc(ev.risk)}">${esc(RISK_TEXT[ev.risk])}</span>
      <span class="alignment-tag ${esc(ev.alignment)}">${esc(ALIGN_TEXT[ev.alignment])}</span>
    </div>
    <div class="reason">${esc(ev.reason)}</div>
    <ul class="actions-list">${ev.actions.map((a) =>
      `<li>${a.resolved ? '<span class="tick">✔ 已闭环</span>' : '<span class="cross">▢ 待处理</span>'}${esc(a.text)}</li>`).join("")}
    </ul>`;
}

// --------------------------------------------------------------------------
// 录入：复测 / 人工复核
// --------------------------------------------------------------------------

const formReview = $("#form-review");
const reviewSeg = $$("#form-review .seg-btn");
reviewSeg.forEach((btn) => {
  btn.addEventListener("click", () => {
    reviewSeg.forEach((b) => b.classList.toggle("active", b === btn));
    const kind = btn.dataset.kind;
    formReview.querySelector('[name="kind"]').value = kind;
    $("#wrap-result").hidden = kind !== "retest";
    $("#wrap-opinion").hidden = kind !== "manual";
  });
});

formReview.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(formReview);
  const kind = fd.get("kind");
  const body = {
    line_name: fd.get("line_name").trim(),
    mileage: fd.get("mileage"),
    joint_code: fd.get("joint_code").trim(),
    batch: fd.get("batch").trim(),
    kind,
    reviewer: fd.get("reviewer").trim(),
    note: fd.get("note") || null,
  };
  if (kind === "retest") body.result = fd.get("result");
  else body.opinion = fd.get("opinion").trim();

  const box = $("#entry-result");
  try {
    const data = await api("POST", "/api/reviews", body);
    box.hidden = false;
    box.className = "card result-card success";
    box.innerHTML = renderEvalResult(data, "复核记录");
    formReview.reset();
    reviewSeg[0].click();
    toast("复核记录已追加，该接头当前结论已失效并重算");
  } catch (err) {
    box.hidden = false;
    box.className = "card result-card error";
    box.textContent = "提交失败：" + err.message;
    toast(err.message, true);
  }
});

$("#btn-seed").addEventListener("click", async () => {
  try {
    const data = await api("POST", "/api/admin/seed");
    if (data.skipped) {
      toast("已存在观测数据，演示数据未写入");
    } else {
      toast(`演示数据已载入：批次 ${data.batch}，结论版本 ${data.conclusion_versions} 份`);
      fillEntryFromSeed(data.line, data.batch);
    }
  } catch (err) {
    toast(err.message, true);
  }
});

function fillEntryFromSeed(line, batch) {
  formObs.querySelector('[name="line_name"]').value = line;
  formObs.querySelector('[name="batch"]').value = batch;
}

// --------------------------------------------------------------------------
// 复核台
// --------------------------------------------------------------------------

async function loadBatches(select) {
  const data = await api("GET", "/api/batches");
  const cur = select.value;
  select.innerHTML = '<option value="">' +
    (select.id === "rp-batch" ? "请选择批次" : "全部批次") + "</option>" +
    data.batches.map((b) => `<option ${b === cur ? "selected" : ""}>${esc(b)}</option>`).join("");
}

async function loadLines(select, allLabel = "全部线路") {
  const data = await api("GET", "/api/lines");
  const cur = select.value;
  select.innerHTML = `<option value="">${esc(allLabel)}</option>` +
    data.lines.map((l) => `<option ${l === cur ? "selected" : ""}>${esc(l)}</option>`).join("");
}

$("#board-refresh").addEventListener("click", loadBoard);
$("#board-batch").addEventListener("change", loadBoard);
$("#board-line").addEventListener("change", loadBoard);

async function loadBoard() {
  await Promise.all([
    loadBatches($("#board-batch")),
    loadLines($("#board-line")),
  ]);
  const params = new URLSearchParams();
  if ($("#board-batch").value) params.set("batch", $("#board-batch").value);
  if ($("#board-line").value) params.set("line_name", $("#board-line").value);
  const data = await api("GET", "/api/board?" + params.toString());
  renderSummary(data.summary);
  $("#board-list").innerHTML = data.items.map(renderJointCard).join("") ||
    '<div class="card">暂无接头观测数据。可先在「录入」页载入演示数据。</div>';
}

function renderSummary(s) {
  $("#board-summary").innerHTML = `
    <div class="stat"><div class="n">${s.total}</div><div class="l">接头总数</div></div>
    <div class="stat"><div class="n" style="color:var(--high)">${s.high}</div><div class="l">高风险</div></div>
    <div class="stat"><div class="n" style="color:var(--medium)">${s.medium}</div><div class="l">中风险</div></div>
    <div class="stat"><div class="n" style="color:var(--low)">${s.low}</div><div class="l">低风险</div></div>
    <div class="stat"><div class="n" style="color:var(--pending)">${s.pending}</div><div class="l">待判定</div></div>
    <div class="stat"><div class="n" style="color:${s.high_uncovered ? "var(--high)" : "var(--low)"}">${s.high_uncovered}</div><div class="l">高风险未闭环</div></div>`;
}

function renderJointCard(it) {
  const conc = it.conclusion;
  const coverBadge = it.risk === "high"
    ? (it.covered
      ? '<span class="badge ok">已有当前复测/明确意见</span>'
      : '<span class="badge blocked">缺少当前复测或明确意见</span>')
    : "";
  return `<div class="card joint-card risk-${esc(it.risk)}">
    <div class="joint-head">
      <span class="name">${esc(it.line_name)} ${fmtKm(it.mileage)} ${esc(it.joint_code)}</span>
      <span class="meta">批次 ${esc(it.batch)}</span>
      <span class="badge ${esc(it.risk)}">${esc(RISK_TEXT[it.risk])}</span>
      <span class="alignment-tag ${esc(it.alignment)}">${esc(ALIGN_TEXT[it.alignment])}</span>
      ${coverBadge}
      <span class="spacer"></span>
      <span class="meta">观测 ${it.observation_count} 条 · 复核 ${it.review_count} 条 ·
        当前结论 v${conc ? conc.version : "-"} #${conc ? conc.id : "-"}</span>
    </div>
    <div class="reason">${esc(it.reason)}</div>
    <ul class="actions-list">
      ${it.actions.map((a) => `<li>
        ${a.resolved ? '<span class="tick">✔ 已闭环</span>' : '<span class="cross">▢ 待处理</span>'}
        <span>[${esc(a.code)}] ${esc(a.text)}</span></li>`).join("")}
    </ul>
  </div>`;
}

// --------------------------------------------------------------------------
// 证据链（按线路）
// --------------------------------------------------------------------------

$("#ev-refresh").addEventListener("click", loadEvidence);
$("#ev-line").addEventListener("change", loadEvidence);
$("#ev-batch").addEventListener("change", loadEvidence);

async function loadEvidenceMeta() {
  await Promise.all([
    loadLines($("#ev-line"), "请选择线路"),
    loadBatches($("#ev-batch")),
  ]);
  if ($("#ev-line").value) loadEvidence();
}

async function loadEvidence() {
  const line = $("#ev-line").value;
  const box = $("#evidence-list");
  if (!line) {
    box.innerHTML = '<div class="card">请选择要查看证据链的线路。</div>';
    return;
  }
  const params = new URLSearchParams({ line_name: line });
  if ($("#ev-batch").value) params.set("batch", $("#ev-batch").value);
  const data = await api("GET", "/api/evidence?" + params.toString());
  if (!data.groups.length) {
    box.innerHTML = '<div class="card">该线路下暂无记录。</div>';
    return;
  }
  box.innerHTML = data.groups.map(renderEvidenceGroup).join("");
}

function renderEvidenceGroup(g) {
  const obsRows = g.observations.map((o) => {
    const sup = o.is_correction
      ? `<span class="badge gray">取代 #${o.supersedes_id}</span>`
      : "";
    return `<tr class="${o.active ? "" : "superseded"}">
      <td>#${o.id}${o.active ? "" : "（已被取代）"}</td>
      <td>${esc(o.batch)}</td>
      <td>${fmtTime(o.created_at)}</td>
      <td>${esc(o.inspector)}</td>
      <td>${o.load_low}–${o.load_high}</td>
      <td>${o.measure_distance}</td>
      <td><b>${o.delta_temp}</b></td>
      <td>${esc(o.calibration_version)}</td>
      <td>${sup}${o.correction_note ? esc(o.correction_note) : ""}</td>
      <td>${o.active ? `<button class="link" onclick="startCorrection(${o.id})">更正/补录</button>` : ""}</td>
    </tr>`;
  }).join("");

  const revRows = g.reviews.map((r) => `
    <tr>
      <td>#${r.id}</td>
      <td>${esc(r.batch)}</td>
      <td>${fmtTime(r.created_at)}</td>
      <td>${r.kind === "retest" ? "现场复测" : "人工复核"}</td>
      <td>${esc(r.reviewer)}</td>
      <td>${r.result != null ? r.result + " K" : esc(r.opinion || "")}</td>
      <td>${esc(r.note || "")}</td>
    </tr>`).join("") || '<tr><td colspan="7">无复核记录</td></tr>';

  const versions = g.versions.map((v) => `
    <div class="version-row ${v.status}">
      <div class="vhead">
        <b>v${v.version}</b>
        <span class="meta">#${v.id}</span>
        <span class="meta">批次 ${esc(v.batch)}</span>
        <span class="badge ${v.risk}">${esc(RISK_TEXT[v.risk])}</span>
        <span class="alignment-tag ${v.alignment}">${esc(ALIGN_TEXT[v.alignment])}</span>
        ${v.status === "current"
          ? '<span class="badge ok">当前</span>'
          : '<span class="badge gray">已失效</span>'}
        <span class="spacer"></span>
        <span class="meta">${fmtTime(v.created_at)}</span>
      </div>
      <div class="reason">${esc(v.reason)}</div>
      ${v.status === "invalidated"
        ? `<div class="invalid-reason">失效原因：${esc(v.invalidated_reason || "—")}
             ${v.superseded_by_id ? `（由结论 #${v.superseded_by_id} 取代）` : ""}</div>`
        : ""}
    </div>`).join("");

  return `<div class="card">
    <div class="joint-head">
      <span class="name">${esc(g.line_name)} ${fmtKm(g.mileage)} ${esc(g.joint_code)}</span>
      <span class="meta">原始观测 ${g.observations.length} 条（含被取代记录，仅追加不可覆盖）</span>
    </div>
    <details open class="evidence">
      <summary>原始观测与更正链（${g.observations.length}）</summary>
      <table>
        <thead><tr>
          <th>编号</th><th>批次</th><th>时间</th><th>巡检员</th>
          <th>负荷(A)</th><th>距离(m)</th><th>温差(K)</th><th>校准版本</th>
          <th>更正说明</th><th>操作</th>
        </tr></thead>
        <tbody>${obsRows}</tbody>
      </table>
    </details>
    <details class="evidence">
      <summary>复测 / 人工复核（${g.reviews.length}）</summary>
      <table>
        <thead><tr>
          <th>编号</th><th>批次</th><th>时间</th><th>类型</th><th>复核人</th>
          <th>结果/意见</th><th>备注</th>
        </tr></thead>
        <tbody>${revRows}</tbody>
      </table>
    </details>
    <details class="evidence" open>
      <summary>结论版本与失效原因（${g.versions.length}）</summary>
      ${versions || '<p class="hint">暂无结论版本。</p>'}
    </details>
  </div>`;
}

// --------------------------------------------------------------------------
// 封存报告
// --------------------------------------------------------------------------

$("#rp-preview").addEventListener("click", previewReport);
$("#rp-seal").addEventListener("click", sealReport);
$("#rp-list").addEventListener("click", loadReports);

async function previewReport() {
  const batch = $("#rp-batch").value;
  const box = $("#rp-preview-box");
  if (!batch) { toast("请先选择批次", true); return; }
  try {
    const data = await api("GET", `/api/reports/preview?batch=${encodeURIComponent(batch)}`);
    box.hidden = false;
    box.innerHTML = renderSnapshot(data.snapshot, data.can_seal);
  } catch (err) {
    toast(err.message, true);
  }
}

async function sealReport() {
  const batch = $("#rp-batch").value;
  const operator = $("#rp-operator").value.trim();
  if (!batch) { toast("请先选择批次", true); return; }
  if (!operator) { toast("请填写封存人", true); return; }
  try {
    const data = await api("POST", "/api/reports", {
      batch, seal_operator: operator,
    });
    toast(`报告 #${data.report_id} 已封存，快照已固定`);
    $("#rp-preview-box").hidden = true;
    loadReports();
  } catch (err) {
    const blockers = err.payload && err.payload.blockers;
    const box = $("#rp-preview-box");
    box.hidden = false;
    box.innerHTML = `<div class="reason" style="color:var(--high);font-weight:600">
        封存被拒绝：${esc(err.message)}</div>
      ${blockers && blockers.length
        ? `<ul class="blocker-list">${blockers.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>
           <p class="hint">请补录当前复测或登记明确人工复核意见后，再对该批次预检封存。</p>`
        : ""}`;
    toast("封存被拒绝：高风险接头未全部覆盖", true);
  }
}

function renderSnapshot(snap, canSeal) {
  const rows = snap.items.map((it) => `
    <tr>
      <td>${esc(it.line_name)} ${fmtKm(it.mileage)} ${esc(it.joint_code)}</td>
      <td><span class="badge ${esc(it.risk)}">${esc(RISK_TEXT[it.risk])}</span></td>
      <td><span class="alignment-tag ${esc(it.alignment)}">${esc(ALIGN_TEXT[it.alignment])}</span></td>
      <td>${it.observation_ids.length}</td>
      <td>${it.review_ids.length}</td>
      <td>${it.covered
        ? '<span class="badge ok">已覆盖</span>'
        : (it.risk === "high"
          ? '<span class="badge blocked">未覆盖（阻断）</span>'
          : '<span class="badge gray">—</span>')}</td>
    </tr>`).join("");
  return `
    <div class="joint-head">
      <b>批次 ${esc(snap.batch)} 封存预检</b>
      <span class="meta">快照生成于 ${fmtTime(snap.generated_at)}</span>
      <span class="spacer"></span>
      ${canSeal
        ? '<span class="badge ok">满足封存条件</span>'
        : '<span class="badge blocked">不得封存</span>'}
    </div>
    <div class="meta" style="margin:6px 0">接头 ${snap.joint_count} 个 · 高风险 ${snap.high_risk_count} 个 ·
      未覆盖 ${snap.high_risk_uncovered.length} 个</div>
    ${snap.high_risk_uncovered.length
      ? `<ul class="blocker-list">
           ${snap.high_risk_uncovered.map((x) => `<li>${esc(x)}</li>`).join("")}
         </ul>
         <p class="hint">封存规则：每个高风险接头都必须有<b>当前复测</b>或<b>明确人工复核意见</b>，
           否则整份报告不得封存。</p>`
      : ""}
    <table>
      <thead><tr><th>接头</th><th>风险</th><th>对齐</th><th>观测数</th><th>复核数</th><th>覆盖状态</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

async function loadReports() {
  await loadBatches($("#rp-batch"));
  const params = new URLSearchParams();
  if ($("#rp-batch").value) params.set("batch", $("#rp-batch").value);
  const data = await api("GET", "/api/reports?" + params.toString());
  const box = $("#rp-list-box");
  if (!data.reports.length) {
    box.innerHTML = '<div class="card">尚无封存报告。</div>';
    return;
  }
  box.innerHTML = data.reports.map((r) => `
    <div class="card report-item" onclick="showReport(${r.id})">
      <div class="joint-head">
        <b>封存报告 #${r.id}</b>
        <span class="meta">批次 ${esc(r.batch)}</span>
        <span class="sealed-flag">🔒 已固定快照</span>
        <span class="spacer"></span>
        <span class="meta">${fmtTime(r.created_at)} · 封存人 ${esc(r.seal_operator)} ·
          接头 ${r.joint_count} · 高风险 ${r.high_risk_count}</span>
      </div>
    </div>`).join("");
}

window.showReport = async function (id) {
  const data = await api("GET", `/api/reports/${id}`);
  const box = $("#rp-detail");
  box.hidden = false;
  const snap = data.snapshot;
  const rows = snap.items.map((it) => `
    <tr>
      <td>${esc(it.line_name)} ${fmtKm(it.mileage)} ${esc(it.joint_code)}</td>
      <td><span class="badge ${esc(it.risk)}">${esc(RISK_TEXT[it.risk])}</span></td>
      <td>v${it.conclusion_version} / #${it.conclusion_id}</td>
      <td>${esc(it.reason)}</td>
      <td>${it.covered ? '<span class="badge ok">已覆盖</span>' : '<span class="badge gray">—</span>'}</td>
    </tr>`).join("");
  const drift = data.drift || [];
  box.innerHTML = `
    <div class="joint-head">
      <b>封存报告 #${data.id}（不可变快照）</b>
      <span class="meta">批次 ${esc(data.batch)}</span>
      <span class="spacer"></span>
      <span class="meta">封存时间 ${fmtTime(data.created_at)} · ${esc(data.seal_operator)}</span>
    </div>
    <p class="hint">快照固定于 ${fmtTime(snap.generated_at)}，包含 ${snap.joint_count} 个接头、
      ${snap.high_risk_count} 个高风险接头。封存后即使原始库有更正/补录，本报告内容不变。</p>
    ${drift.length
      ? `<div class="drift-note">封存后以下接头证据发生变化（不影响本快照，仅提示）：
          ${drift.map((d) => `${esc(d.line_name)} ${fmtKm(d.mileage)} ${esc(d.joint_code)}`).join("；")}</div>`
      : '<div class="drift-note" style="color:var(--low)">封存后各接头证据无变化。</div>'}
    <table>
      <thead><tr><th>接头</th><th>风险</th><th>快照结论</th><th>原因（快照固定）</th><th>覆盖</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  box.scrollIntoView({ behavior: "smooth" });
};

// 启动
loadBoard();
