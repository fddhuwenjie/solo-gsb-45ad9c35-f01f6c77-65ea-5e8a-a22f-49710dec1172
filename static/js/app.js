/* 电缆隧道接头巡检复核台 · 原生 JS（无框架、无构建） */
"use strict";

const app = document.getElementById("app");
const toastEl = document.getElementById("toast");
const RISK_TEXT = { high: "高风险", medium: "中风险", low: "低风险" };
const STATE_TEXT = {
  comparable: "可比较",
  insufficient: "证据不足",
  contradictory: "相互矛盾",
};

/* ---------------- 工具 ---------------- */

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toast(msg, ok = true) {
  toastEl.textContent = msg;
  toastEl.className = "toast " + (ok ? "ok" : "err");
  setTimeout(() => toastEl.classList.add("hidden"), 3500);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `请求失败 (${res.status})`);
  return data;
}

function fmtTime(s) {
  return s ? s.replace("T", " ").replace("Z", " UTC") : "—";
}

function setActiveNav() {
  const key = location.hash.split("/")[1] || "entry";
  document.querySelectorAll("nav a").forEach((a) => {
    a.classList.toggle("active", a.getAttribute("href") === `#/${key}`);
  });
}

/* ---------------- 录入 / 更正 / 人工复核 ---------------- */

async function renderEntry() {
  const meta = await api("/api/meta");
  window.__meta = meta;

  app.innerHTML = `
    <div class="grid-2">
      <section class="card">
        <h2>温度观测录入</h2>
        <p class="hint">原始记录只追加、不可覆盖。选择“更正已有读数”时，原记录保留并标记失效，再写入新读数；对已有结论补录会使该接头当前结论失效。</p>
        <form id="obs-form" class="form">
          ${formLineBatch(meta)}
          <label>测温时负荷区间 <input name="load_range" placeholder="如 300-380A" required></label>
          <div class="row">
            <label>测温距离 (m) <input name="measure_distance_m" type="number" step="0.1" min="0" required></label>
            <label>温差读数 (K) <input name="temp_diff_k" type="number" step="0.1" required></label>
          </div>
          <div class="row">
            <label>设备校准版本 <input name="calib_version" placeholder="如 CAL-2026.09" required></label>
            <label>巡检员 <input name="inspector" required></label>
          </div>
          <label class="check"><input type="checkbox" name="is_retest"> 本次为复测读数</label>
          <label>备注 <input name="note" placeholder="可注明误录更正原因等"></label>
          <div class="correct-box">
            <label class="check"><input type="checkbox" id="correct-toggle"> 更正该接头同批次已有读数</label>
            <select name="corrects_id" id="correct-select" disabled></select>
          </div>
          <button class="btn primary" type="submit">提交观测（自动重算结论）</button>
        </form>
      </section>

      <section class="card">
        <h2>人工复核意见</h2>
        <p class="hint">明确复核意见会作为该接头本批次的证据，并使当前结论失效、重新生成。</p>
        <form id="review-form" class="form">
          ${formLineBatch(meta, "rev")}
          <label>复核人 <input name="reviewer" required></label>
          <label>明确复核意见 <textarea name="opinion" rows="4" placeholder="现场判定、处置要求、是否认可读数等" required></textarea></label>
          <button class="btn primary" type="submit">提交复核意见</button>
        </form>
        <div class="legend">
          <b>判定阈值</b>
          <ul>
            <li>高风险：温差 ≥ 8K，或读数相互矛盾</li>
            <li>中风险：温差 ≥ 4K</li>
            <li>矛盾：同条件读数温差极差 &gt; 3K</li>
            <li>测温距离差 &gt; 1m / 校准版本不同 / 负荷区间无交集 → 证据不足</li>
          </ul>
        </div>
      </section>
    </div>`;

  bindJointPicker(meta, "obs");
  bindJointPicker(meta, "rev");

  document.getElementById("obs-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const body = formLineBatchValues(f, "obs");
    Object.assign(body, {
      load_range: f.get("load_range"),
      measure_distance_m: f.get("measure_distance_m"),
      temp_diff_k: f.get("temp_diff_k"),
      calib_version: f.get("calib_version"),
      inspector: f.get("inspector"),
      note: f.get("note"),
      is_retest: f.get("is_retest") ? 1 : 0,
    });
    if (document.getElementById("correct-toggle").checked) {
      body.corrects_id = f.get("corrects_id");
    }
    try {
      const r = await api("/api/observations", { method: "POST", body: JSON.stringify(body) });
      toast(r.invalidated_previous
        ? `已保存观测#${r.observation_id}，原结论已失效，新结论 #${r.conclusion_id} 已生成`
        : `已保存观测#${r.observation_id}，结论 #${r.conclusion_id} 已生成`);
      e.target.reset();
      document.getElementById("correct-select").innerHTML = "";
      document.getElementById("correct-select").disabled = true;
    } catch (err) { toast(err.message, false); }
  });

  document.getElementById("review-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const body = formLineBatchValues(f, "rev");
    body.reviewer = f.get("reviewer");
    body.opinion = f.get("opinion");
    try {
      const r = await api("/api/reviews", { method: "POST", body: JSON.stringify(body) });
      toast(`复核意见#${r.review_id} 已保存，新结论 #${r.conclusion_id} 已生成`);
      e.target.reset();
    } catch (err) { toast(err.message, false); }
  });
}

function formLineBatch(meta, prefix = "obs") {
  return `
    <label>线路
      <select name="line_id" data-p="${prefix}-line" required>
        <option value="">请选择线路</option>
        ${meta.lines.map((l) => `<option value="${l.id}">${esc(l.name)}</option>`).join("")}
      </select>
    </label>
    <label>接头（里程 + 编号；新接头可直接录入）
      <select name="joint_pick" data-p="${prefix}-pick">
        <option value="">— 手动输入新接头 —</option>
        ${meta.joints.map((j) => {
          const line = meta.lines.find((l) => l.id === j.line_id);
          return `<option value="${j.id}" data-line="${j.line_id}" data-mileage="${esc(j.mileage)}" data-no="${esc(j.joint_no)}">${esc(line.name)} · ${esc(j.mileage)} · ${esc(j.joint_no)}</option>`;
        }).join("")}
      </select>
    </label>
    <div class="row">
      <label>线路里程 <input name="mileage" data-p="${prefix}-mileage" placeholder="K12+340" required></label>
      <label>接头编号 <input name="joint_no" data-p="${prefix}-no" placeholder="J-07" required></label>
    </div>
    <label>巡检批次
      <select name="batch_id" required>
        <option value="">请选择批次</option>
        ${meta.batches.map((b) => `<option value="${b.id}">${esc(b.name)}（${esc(b.inspected_at)}）</option>`).join("")}
      </select>
    </label>`;
}

function bindJointPicker(meta, prefix) {
  const lineSel = document.querySelector(`[data-p="${prefix}-line"]`);
  const pick = document.querySelector(`[data-p="${prefix}-pick"]`);
  const mileage = document.querySelector(`[data-p="${prefix}-mileage"]`);
  const no = document.querySelector(`[data-p="${prefix}-no"]`);

  lineSel.addEventListener("change", () => {
    pick.value = "";
    [...pick.options].forEach((o) => {
      if (!o.value) return;
      o.hidden = lineSel.value && o.dataset.line !== lineSel.value;
    });
  });
  pick.addEventListener("change", () => {
    const o = pick.selectedOptions[0];
    if (o && o.value) {
      mileage.value = o.dataset.mileage;
      no.value = o.dataset.no;
      if (prefix === "obs") loadCorrectTargets();
    }
  });
  document.querySelector('[name="batch_id"]', lineSel.closest("form")).addEventListener("change", () => {
    if (prefix === "obs") loadCorrectTargets();
  });
}

async function loadCorrectTargets() {
  const f = document.getElementById("obs-form");
  const toggle = document.getElementById("correct-toggle");
  const sel = document.getElementById("correct-select");
  const fd = new FormData(f);
  const lineId = fd.get("line_id");
  const batchId = fd.get("batch_id");
  const mileageV = fd.get("mileage");
  const noV = fd.get("joint_no");
  sel.innerHTML = "";
  if (!(lineId && batchId && mileageV && noV)) { toggle.disabled = true; return; }
  toggle.disabled = false;
  const joint = (window.__meta.joints || []).find(
    (j) => j.line_id === Number(lineId) && j.mileage === mileageV && j.joint_no === noV);
  if (!joint) { toggle.disabled = true; return; }
  try {
    const ev = await api(`/api/joints/${joint.id}/batches/${batchId}/evidence`);
    const targets = ev.observations.filter((o) => !o.superseded);
    if (!targets.length) { toggle.disabled = true; return; }
    sel.innerHTML = targets.map((o) =>
      `<option value="${o.id}">#${o.id} ${o.is_retest ? "复测" : "初测"} ${o.temp_diff_k}K / ${esc(o.load_range)} / ${esc(o.calib_version)}</option>`).join("");
    sel.disabled = !toggle.checked;
  } catch { toggle.disabled = true; }
}

function formLineBatchValues(fd, prefix) {
  return {
    line_id: Number(fd.get("line_id")),
    mileage: fd.get("mileage"),
    joint_no: fd.get("joint_no"),
    batch_id: Number(fd.get("batch_id")),
  };
}

document.addEventListener("change", (e) => {
  if (e.target.id === "correct-toggle") {
    document.getElementById("correct-select").disabled = !e.target.checked;
    if (e.target.checked) loadCorrectTargets();
  }
});

/* ---------------- 批次复核 ---------------- */

async function renderBatch(batchId) {
  const meta = await api("/api/meta");
  app.innerHTML = `
    <section class="card">
      <div class="head-row">
        <h2>批次复核</h2>
        <div class="head-actions">
          <select id="batch-select" class="inline-select">
            ${meta.batches.map((b) => `<option value="${b.id}">${esc(b.name)}</option>`).join("")}
          </select>
          <button class="btn" id="recompute-btn">重算本批全部结论</button>
          <button class="btn warn" id="seal-btn">封存批次报告</button>
        </div>
      </div>
      <div id="seal-gate"></div>
      <div id="joint-table"></div>
    </section>`;

  const sel = document.getElementById("batch-select");
  if (batchId) sel.value = batchId;
  sel.addEventListener("change", () => { location.hash = `#/batch/${sel.value}`; });
  document.getElementById("recompute-btn").addEventListener("click", async () => {
    try {
      const r = await api(`/api/batches/${sel.value}/recompute`, { method: "POST" });
      toast(`已重算 ${r.recomputed.length} 个接头的结论`);
      loadBatchOverview(sel.value);
    } catch (err) { toast(err.message, false); }
  });
  document.getElementById("seal-btn").addEventListener("click", async () => {
    try {
      const r = await api(`/api/batches/${sel.value}/seal`, { method: "POST" });
      toast(`报告 v${r.version} 已封存（快照固定，#${r.report_id}）`);
      loadBatchOverview(sel.value);
    } catch (err) { toast(err.message, false); }
  });

  loadBatchOverview(sel.value);
}

async function loadBatchOverview(batchId) {
  const ov = await api(`/api/batches/${batchId}`);
  const gate = document.getElementById("seal-gate");
  if (ov.blockers.length) {
    gate.className = "gate blocked";
    gate.innerHTML = "<b>不可封存：</b><ul>" +
      ov.blockers.map((b) => `<li>${esc(b)}</li>`).join("") + "</ul>";
  } else {
    gate.className = "gate ok";
    gate.innerHTML = "<b>封存门槛已满足：</b>全部高风险接头均具备当前复测读数或明确人工复核意见。";
  }
  if (ov.sealed_reports.length) {
    gate.innerHTML += `<div class="sealed-tags">历史封存：${ov.sealed_reports.map((r) =>
      `<a href="#/reports/${r.id}">v${r.version} · ${fmtTime(r.sealed_at)}</a>`).join(" ")}</div>`;
  }

  const counts = ov.joints.reduce((m, j) => {
    const k = j.conclusion ? j.conclusion.risk_level : "none";
    m[k] = (m[k] || 0) + 1;
    return m;
  }, {});

  document.getElementById("joint-table").innerHTML = `
    <div class="stat-row">
      <span class="stat">接头 ${ov.joints.length}</span>
      <span class="stat risk-high">高风险 ${counts.high || 0}</span>
      <span class="stat risk-medium">中风险 ${counts.medium || 0}</span>
      <span class="stat risk-low">低风险 ${counts.low || 0}</span>
    </div>
    <table class="table">
      <thead><tr>
        <th>线路</th><th>里程</th><th>接头</th><th>证据状态</th><th>风险</th>
        <th>最大温差</th><th>复测/复核</th><th>结论原因与处置</th><th></th>
      </tr></thead>
      <tbody>${ov.joints.map((j) => {
        const c = j.conclusion;
        return `<tr>
          <td>${esc(j.line_name)}</td>
          <td>${esc(j.mileage)}</td>
          <td>${esc(j.joint_no)}</td>
          <td>${c ? `<span class="badge state-${c.state}">${STATE_TEXT[c.state]}</span>` : "—"}
              <div class="muted">有效读数 ${j.active_count} / 原始 ${j.total_count} 条</div></td>
          <td>${c ? `<span class="badge risk-${c.risk_level}">${RISK_TEXT[c.risk_level]}</span>` : "—"}</td>
          <td>${c && c.max_temp_diff_k != null ? c.max_temp_diff_k + " K" : "—"}</td>
          <td>${j.has_retest ? "✅ 复测 " : ""}${j.review_count ? "✅ 人工复核" : (!j.has_retest ? '<span class="muted">无</span>' : "")}</td>
          <td class="reason-cell">${c ? `<details><summary>${esc(c.reason.slice(0, 60))}…</summary>
              <p class="reason-full">${esc(c.reason)}</p>
              <ol class="disp">${c.dispositions.map((d) => `<li><code>${d.code}</code> ${esc(d.action)}</li>`).join("")}</ol>
            </details>` : "—"}
            ${j.seal_blocker ? `<div class="block-tag">⛔ ${esc(j.seal_blocker)}</div>` : ""}</td>
          <td><button class="btn small" data-evidence="${j.joint_id}">证据链</button></td>
        </tr>`;
      }).join("")}</tbody>
    </table>`;

  document.querySelectorAll("[data-evidence]").forEach((btn) =>
    btn.addEventListener("click", () => openEvidence(btn.dataset.evidence, batchId)));
}

/* ---------------- 证据弹窗（原始记录 / 更正链 / 复核 / 结论历史） ---------------- */

async function openEvidence(jointId, batchId) {
  const ev = await api(`/api/joints/${jointId}/batches/${batchId}/evidence`);
  const modal = document.getElementById("modal-root");
  modal.innerHTML = `
    <div class="modal-mask">
      <div class="modal wide">
        <div class="modal-head">
          <h3>${esc(ev.joint.line_name)} · ${esc(ev.joint.mileage)} · ${esc(ev.joint.joint_no)}
              <span class="muted">${esc(ev.batch.name)}</span></h3>
          <button class="btn small" id="modal-close">关闭</button>
        </div>
        <div class="modal-body">
          <h4>原始观测（只追加，被更正记录保留不覆盖）</h4>
          <table class="table small-text">
            <thead><tr><th>#</th><th>时间</th><th>类型</th><th>负荷</th><th>距离</th><th>温差</th><th>校准版本</th><th>巡检员</th><th>状态/备注</th></tr></thead>
            <tbody>${ev.observations.map((o) => `
              <tr class="${o.superseded ? "row-dead" : ""}">
                <td>${o.id}</td><td>${fmtTime(o.recorded_at)}</td>
                <td>${o.is_retest ? "复测" : "初测"}</td>
                <td>${esc(o.load_range)}</td><td>${o.measure_distance_m}m</td>
                <td><b>${o.temp_diff_k}K</b></td><td>${esc(o.calib_version)}</td>
                <td>${esc(o.inspector)}</td>
                <td>${o.superseded
                    ? `<span class="badge dead">已被观测#${ev.observations.find((x) => x.corrects_id === o.id)?.id || ""}更正</span>`
                    : `<span class="badge alive">当前有效</span>`}
                    ${o.corrects_id ? `<div class="muted">更正自观测#${o.corrects_id}：${esc(o.note)}</div>` : ""}
                    ${!o.corrects_id && o.note ? `<div class="muted">${esc(o.note)}</div>` : ""}
                </td>
              </tr>`).join("")}</tbody>
          </table>

          <h4>人工复核记录</h4>
          ${ev.reviews.length ? `<table class="table small-text">
            <thead><tr><th>#</th><th>时间</th><th>复核人</th><th>意见</th></tr></thead>
            <tbody>${ev.reviews.map((r) => `<tr>
              <td>${r.id}</td><td>${fmtTime(r.recorded_at)}</td><td>${esc(r.reviewer)}</td>
              <td>${esc(r.opinion)}</td></tr>`).join("")}</tbody></table>`
            : `<p class="muted">无人工复核记录。</p>`}

          <h4>结论版本（更正/补录只令当前结论失效，历史可追溯）</h4>
          <ol class="history">
            ${ev.conclusions.map((c) => `
              <li class="${c.invalidated ? "dead" : "alive"}">
                <div><b>结论 #${c.id}</b>
                  <span class="badge state-${c.state}">${STATE_TEXT[c.state]}</span>
                  <span class="badge risk-${c.risk_level}">${RISK_TEXT[c.risk_level]}</span>
                  ${c.invalidated ? '<span class="badge dead">已失效</span>' : '<span class="badge alive">当前</span>'}
                  <span class="muted">${fmtTime(c.created_at)}</span></div>
                ${c.invalidated ? `<div class="invalidate-reason">失效原因：${esc(c.invalidated_reason)}</div>` : ""}
                <div>${esc(c.reason)}</div>
                <details><summary>处置清单（${c.dispositions.length} 项）</summary>
                  <ol class="disp">${c.dispositions.map((d) => `<li><code>${d.code}</code> ${esc(d.action)}</li>`).join("")}</ol>
                </details>
              </li>`).join("")}
          </ol>
        </div>
      </div>
    </div>`;
  document.getElementById("modal-close").onclick = () => (modal.innerHTML = "");
  modal.querySelector(".modal-mask").addEventListener("click", (e) => {
    if (e.target.classList.contains("modal-mask")) modal.innerHTML = "";
  });
}

/* ---------------- 线路证据链 ---------------- */

async function renderLine(lineId) {
  const meta = await api("/api/meta");
  app.innerHTML = `
    <section class="card">
      <div class="head-row">
        <h2>线路证据链与失效追溯</h2>
        <select id="line-select" class="inline-select">
          ${meta.lines.map((l) => `<option value="${l.id}">${esc(l.name)}</option>`).join("")}
        </select>
      </div>
      <div id="line-body"></div>
    </section>`;
  const sel = document.getElementById("line-select");
  if (lineId) sel.value = lineId;
  sel.addEventListener("change", () => (location.hash = `#/line/${sel.value}`));
  loadLine(sel.value);
}

async function loadLine(lineId) {
  const data = await api(`/api/lines/${lineId}/evidence`);
  const body = document.getElementById("line-body");
  if (!data.joints.length) { body.innerHTML = '<p class="muted">该线路暂无巡检记录。</p>'; return; }
  body.innerHTML = data.joints.map((jg) => `
    <div class="joint-block">
      <h3>${esc(jg.joint.mileage)} · ${esc(jg.joint.joint_no)}</h3>
      ${jg.batches.map((bg) => {
        const c = bg.current_conclusion;
        return `<div class="batch-line">
          <div class="batch-head">
            <span class="batch-name">${esc(bg.batch.name)}（${esc(bg.batch.inspected_at)}）</span>
            ${c
              ? `<span class="badge state-${c.state}">${STATE_TEXT[c.state]}</span>
                 <span class="badge risk-${c.risk_level}">${RISK_TEXT[c.risk_level]}</span>`
              : '<span class="muted">无结论</span>'}
            <button class="btn small" data-evidence="${jg.joint.id}" data-batch="${bg.batch.id}">查看证据链</button>
          </div>
          <ol class="mini-history">
            ${bg.history.map((h) => `<li class="${h.invalidated ? "dead" : "alive"}">
              #${h.id} ${fmtTime(h.created_at)}
              ${h.invalidated ? `<span class="badge dead">已失效</span> <span class="invalidate-reason">${esc(h.invalidated_reason)}</span>` : '<span class="badge alive">当前</span>'}
            </li>`).join("")}
          </ol>
        </div>`;
      }).join("")}
    </div>`).join("");

  body.querySelectorAll("[data-evidence]").forEach((b) =>
    b.addEventListener("click", () => openEvidence(b.dataset.evidence, b.dataset.batch)));
}

/* ---------------- 封存报告列表与快照 ---------------- */

async function renderReports(reportId) {
  const reports = await api("/api/reports");
  app.innerHTML = `
    <section class="card">
      <h2>封存报告（固定批次快照，不可修改）</h2>
      ${reports.length ? `<table class="table">
        <thead><tr><th>#</th><th>批次</th><th>版本</th><th>封存时间</th><th></th></tr></thead>
        <tbody>${reports.map((r) => `
          <tr><td>${r.id}</td><td>${esc(r.batch_name)}</td><td>v${r.version}</td>
          <td>${fmtTime(r.sealed_at)}</td>
          <td><a class="btn small" href="#/reports/${r.id}">查看快照</a></td></tr>`).join("")}
        </tbody></table>` : '<p class="muted">尚无封存报告。前往“批次复核”完成门槛后封存。</p>'}
      <div id="report-detail"></div>
    </section>`;
  if (reportId) loadReport(reportId);
}

async function loadReport(id) {
  const r = await api(`/api/reports/${id}`);
  const snap = r.snapshot;
  const el = document.getElementById("report-detail");
  el.innerHTML = `
    <div class="report-box">
      <h3>报告 #${r.id} · v${r.version} · ${esc(snap.batch.name)}</h3>
      <p class="muted">封存于 ${fmtTime(r.sealed_at)} ｜ 快照生成于 ${fmtTime(snap.generated_at)}（之后的更正/补录不改变本报告内容）</p>
      <p>封存时门槛：${snap.gate.sealable ? "✅ 全部高风险接头均有当前复测或明确复核意见" : "⛔ 未满足"}</p>
      <table class="table small-text">
        <thead><tr><th>线路</th><th>里程</th><th>接头</th><th>状态</th><th>风险</th><th>最大温差</th><th>结论原因</th><th>处置</th><th>复测/复核</th></tr></thead>
        <tbody>${snap.joints.map((j) => {
          const c = j.current_conclusion;
          return `<tr>
            <td>${esc(j.line_name)}</td><td>${esc(j.mileage)}</td><td>${esc(j.joint_no)}</td>
            <td>${c ? `<span class="badge state-${c.state}">${STATE_TEXT[c.state]}</span>` : "—"}</td>
            <td>${c ? `<span class="badge risk-${c.risk_level}">${RISK_TEXT[c.risk_level]}</span>` : "—"}</td>
            <td>${c && c.max_temp_diff_k != null ? c.max_temp_diff_k + "K" : "—"}</td>
            <td>${c ? esc(c.reason) : "—"}</td>
            <td>${c ? `<ol class="disp">${c.dispositions.map((d) => `<li>${esc(d.action)}</li>`).join("")}</ol>` : "—"}</td>
            <td>${j.observations.some((o) => o.is_retest) ? "复测 " : ""}${j.reviews.length ? `人工复核×${j.reviews.length}` : ""}</td>
          </tr>`;
        }).join("")}</tbody>
      </table>
    </div>`;
  el.scrollIntoView({ behavior: "smooth" });
}

/* ---------------- 路由 ---------------- */

async function route() {
  setActiveNav();
  const hash = location.hash.replace(/^#\/?/, "");
  const [view, id] = hash.split("/");
  try {
    if (view === "batch") return await renderBatch(id ? Number(id) : null);
    if (view === "line") return await renderLine(id ? Number(id) : null);
    if (view === "reports") return await renderReports(id ? Number(id) : null);
    return await renderEntry();
  } catch (err) {
    app.innerHTML = `<section class="card"><p class="error">${esc(err.message)}</p></section>`;
  }
}

window.addEventListener("hashchange", route);
if (!location.hash) location.hash = "#/entry";
route();
