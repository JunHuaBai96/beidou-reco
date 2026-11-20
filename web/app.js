/* 北斗短剧素材推荐助手 · 前端逻辑（无框架） */
const $ = (s) => document.querySelector(s);
const state = { ctxReady: false, recoRows: [], assignRows: [], poll: null };

const api = {
  async get(p) { const r = await fetch(p); return r.json(); },
  async post(p, b) {
    const r = await fetch(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b || {}) });
    return r.json();
  },
};

/* ---------- 状态 ---------- */
function setPill(text, cls) {
  const el = $("#statusPill");
  el.textContent = text;
  el.className = "pill " + cls;
}
function showProgress(on) { $("#progress").classList.toggle("hidden", !on); }
function setBar(pct) { $("#barFill").style.width = Math.max(5, Math.min(100, pct)) + "%"; }
function logLine(s) {
  const el = $("#log");
  el.textContent += (el.textContent ? "\n" : "") + s;
  el.scrollTop = el.scrollHeight;
}

async function refreshStatus() {
  const s = await api.get("/api/status");
  const masked = s.cfg && s.cfg.masked;
  if (!masked) { $("#setupBanner").classList.remove("hidden"); setPill("未配置", "bad"); }
  else { $("#setupBanner").classList.add("hidden"); setPill("已配置 " + masked, "ok"); }
  if (s.has_ctx && s.ctx_age != null) {
    state.ctxReady = true;
    $("#ctxInfo").textContent = "数据：" + (s.summary?.generated || "已加载") + "（" + Math.round(s.ctx_age / 60) + " 分钟前）";
  }
  if (s.summary) renderGroups(s.summary.groups || []);
  if (s.state === "running") {
    showProgress(true); setPill("拉取中…", "run");
    $("#log").textContent = "";
    (s.log || []).forEach(logLine);
    setBar(Math.min(90, 10 + (s.log || []).length * 3));
    if (!state.poll) state.poll = setInterval(async () => {
      const st = await api.get("/api/status");
      $("#log").textContent = "";
      (st.log || []).forEach(logLine);
      setBar(Math.min(95, 10 + (st.log || []).length * 3));
      if (st.state !== "running") {
        clearInterval(state.poll); state.poll = null;
        setBar(100);
        if (st.state === "done") { setPill("数据就绪", "ok"); state.ctxReady = true; }
        else { setPill("拉取失败", "bad"); }
        if (st.summary) renderGroups(st.summary.groups || []);
        $("#ctxInfo").textContent = "数据：" + (st.summary?.generated || "");
        setTimeout(() => showProgress(false), 1200);
      }
    }, 1500);
  }
}

/* ---------- 分组卡片 ---------- */
function ratioClass(v) { return v < 4 ? "hi" : (v < 12 ? "mid" : "lo"); }
function ratioText(v) { return v < 4 ? "极缺" : (v < 12 ? "偏紧" : "充足"); }

function renderGroups(groups) {
  const box = $("#groups");
  if (!groups.length) { box.innerHTML = ""; return; }
  groups.sort((a, b) => (a.per_page || 999) - (b.per_page || 999));
  box.innerHTML = groups.map(g => `
    <div class="gcard">
      <div class="gname"><span>${esc(g.name)}</span>
        <span class="tagx ${g.name.includes("停用") ? "mid" : ratioClass(g.per_page || 0)}">${g.name.includes("停用") ? "隔离区" : (g.pages ? ratioText(g.per_page) : "无主页")}</span></div>
      <div class="ratio">${g.pages ? g.per_page : "—"}<span class="muted" style="font-size:11px"> 集/页</span></div>
      <div class="grow"><span>主页 ${g.pages}</span><span>有货 ${g.have} 部</span></div>
      <div class="grow"><span>剩余 ${g.eps} 集</span></div>
    </div>`).join("");
}

/* ---------- 表格 ---------- */
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const tagsHtml = (t) => (t || []).map(x => `<span class="gtag">${esc(x)}</span>`).join(" ");

function renderReco(data) {
  const rows = data.rows || [];
  state.recoRows = rows;
  const s = data.summary || {};
  const d = s.dash || {};
  $("#recoMeta").innerHTML =
    `数据时间 ${esc(s.generated || "")}；数据源：${Object.entries(s.sources || {}).map(([k, v]) => `${esc(k)} ${esc(v)}`).join(" · ")}` +
    (d.yesterday ? `<br/>看板昨日 ${esc(d.yesterday.date)}：${d.yesterday.order_count} 单 ¥${d.yesterday.amount}；今日：${d.today?.order_count ?? "-"} 单 ¥${d.today?.amount ?? "-"}` : "") +
    (s.errors && s.errors.length ? `<br/><span style="color:var(--warn)">注意：${esc(s.errors.join("；"))}</span>` : "");

  const t = $("#tblReco");
  if (!rows.length) { t.innerHTML = `<tbody><tr><td class="empty">暂无推荐，请先拉取数据</td></tr></tbody>`; return; }
  t.innerHTML = `<thead><tr>
      <th>所属分组</th><th>剧目名称</th><th>上线时间</th><th>剧目类型</th>
      <th>题材类型</th><th>剧场</th><th>推广</th><th>推荐理由</th><th>优先级</th>
    </tr></thead><tbody>` + rows.map(r => `<tr>
      <td><span class="gtag">${esc(r.group)}</span></td>
      <td class="name">${esc(r.name)}${r.ep && r.ep !== "—" ? `<div class="muted">${esc(r.ep)} 集</div>` : ""}</td>
      <td>${esc(r.pub)}</td>
      <td>${esc(r.type)}<div class="muted">${esc(r.type_why)}</div></td>
      <td>${tagsHtml(r.tags)}</td>
      <td>${esc(r.theater)}</td>
      <td>${esc(r.promoter)}</td>
      <td class="reason">${esc(r.reason)}</td>
      <td><span class="pri ${esc(r.priority)}">${esc(r.priority)}</span></td>
    </tr>`).join("") + `</tbody>`;
}

function renderAssign(data) {
  const rows = data.rows || [];
  state.assignRows = rows;
  $("#assignInfo").textContent = rows.length
    ? `共 ${rows.length} 部` + (data.unresolved && data.unresolved.length ? `；未收录 ${data.unresolved.length} 部：${data.unresolved.join("、")}` : "")
    : "";
  const t = $("#tblAssign");
  if (!rows.length) { t.innerHTML = ""; return; }
  t.innerHTML = `<thead><tr><th>剧目</th><th>上线</th><th>类型</th><th>题材</th>
      <th>剧场</th><th>推广</th><th>推荐理由</th><th>优先级</th></tr></thead><tbody>` +
    rows.map(r => `<tr>
      <td class="name">${esc(r.name)}<div class="muted">→ ${esc(r.group)}</div></td>
      <td>${esc(r.pub)}</td>
      <td>${esc(r.type)}<div class="muted">${esc(r.type_why || "")}</div></td>
      <td>${tagsHtml(r.tags)}</td>
      <td>${esc(r.theater)}</td>
      <td>${esc(r.promoter)}</td>
      <td class="reason">${esc("归组：" + r.group + "；" + r.reason)}</td>
      <td><span class="pri ${esc(r.priority)}">${esc(r.priority)}</span></td>
    </tr>`).join("") + `</tbody>`;
}

/* ---------- 导出 / 复制 ---------- */
function toMarkdown(rows, mode) {
  if (!rows.length) return "";
  if (mode === "reco") {
    const head = ["所属分组", "剧目名称", "上线时间", "剧目类型", "题材", "剧场", "推广", "推荐理由", "优先级"];
    return "| " + head.join(" | ") + " |\n|" + head.map(() => "---").join("|") + "|\n" +
      rows.map(r => "| " + [r.group, r.name, r.pub, r.type, (r.tags || []).join("、"), r.theater, r.promoter, r.reason, r.priority]
        .map(x => String(x ?? "").replace(/\|/g, "/")).join(" | ") + " |").join("\n");
  }
  const head = ["剧目", "上线", "类型", "题材", "剧场", "推广", "推荐理由", "优先级"];
  return "| " + head.join(" | ") + " |\n|" + head.map(() => "---").join("|") + "|\n" +
    rows.map(r => "| " + [r.name, r.pub, r.type, (r.tags || []).join("、"), r.theater, r.promoter,
      "归组：" + r.group + "；" + r.reason, r.priority].map(x => String(x ?? "").replace(/\|/g, "/")).join(" | ") + " |").join("\n");
}

async function exportCsv(mode) {
  const rows = mode === "reco" ? state.recoRows : state.assignRows;
  const cols = mode === "reco"
    ? ["group", "name", "pub", "type", "tags", "theater", "promoter", "reason", "priority"]
    : ["name", "group", "pub", "type", "tags", "theater", "promoter", "reason", "priority"];
  const res = await api.post("/api/export", { rows, cols });
  const blob = new Blob(["\ufeff" + res.csv], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = (mode === "reco" ? "按组推荐_" : "剧名定组_") + new Date().toISOString().slice(0, 10) + ".csv";
  a.click();
}

async function copyTable(mode) {
  const txt = toMarkdown(mode === "reco" ? state.recoRows : state.assignRows, mode);
  try { await navigator.clipboard.writeText(txt); flash("已复制到剪贴板"); }
  catch (e) { flash("复制失败，请用导出 CSV"); }
}
function flash(msg) {
  const el = $("#ctxInfo");
  const old = el.textContent;
  el.textContent = msg;
  setTimeout(() => { el.textContent = old; }, 1800);
}

/* ---------- 设置 ---------- */
function openModal() {
  $("#modal").classList.remove("hidden");
}
async function saveSettings() {
  const body = { api_key: $("#apiKey").value.trim(), feishu_app_id: $("#fsId").value.trim(), feishu_app_secret: $("#fsSecret").value.trim() };
  const r = await api.post("/api/settings", body);
  if (r.ok) { $("#modal").classList.add("hidden"); refreshStatus(); }
}
async function testConn() {
  $("#testMsg").textContent = "测试中…";
  const r = await api.post("/api/test", { api_key: $("#apiKey").value.trim() });
  $("#testMsg").textContent = r.ok ? `连接成功：${r.groups} 个发布组${r.user ? "，账号 " + r.user : ""}` : "失败：" + r.error;
}

/* ---------- 事件 ---------- */
document.addEventListener("DOMContentLoaded", async () => {
  const st = await api.get("/api/status");
  if (st.cfg?.masked) $("#apiKey").placeholder = st.cfg.masked;

  $("#btnSettings").onclick = openModal;
  $("#btnSetup").onclick = openModal;
  $("#btnCancel").onclick = () => $("#modal").classList.add("hidden");
  $("#btnSave").onclick = saveSettings;
  $("#btnTest").onclick = testConn;
  $("#btnRefresh").onclick = async () => {
    const r = await api.post("/api/refresh", {});
    if (r.ok) { showProgress(true); $("#log").textContent = "开始拉取…"; setPill("拉取中…", "run"); refreshStatus(); }
    else alert(r.error || "启动失败");
  };
  $("#btnRun").onclick = async () => {
    if (!state.ctxReady) { const ok = confirm("还没拉取数据，是否现在拉取？"); if (ok) return $("#btnRefresh").click(); return; }
    $("#btnRun").disabled = true; $("#btnRun").textContent = "生成中…";
    const r = await api.post("/api/recommend", { per_group: parseInt($("#perGroup").value, 10) });
    $("#btnRun").disabled = false; $("#btnRun").textContent = "生成推荐";
    if (r.ok) renderReco(r); else alert(r.error || "生成失败");
  };
  $("#btnAssign").onclick = async () => {
    const names = $("#names").value;
    if (!names.trim()) return alert("请先填写剧名");
    $("#btnAssign").disabled = true; $("#btnAssign").textContent = "生成中…";
    const r = await api.post("/api/assign", { names });
    $("#btnAssign").disabled = false; $("#btnAssign").textContent = "生成定组表";
    if (r.ok) renderAssign(r); else alert(r.error || "生成失败");
  };
  $("#btnExport1").onclick = () => exportCsv("reco");
  $("#btnExport2").onclick = () => exportCsv("assign");
  $("#btnCopy1").onclick = () => copyTable("reco");
  $("#btnCopy2").onclick = () => copyTable("assign");
  document.querySelectorAll(".tab").forEach(b => b.onclick = () => {
    document.querySelectorAll(".tab").forEach(x => x.classList.toggle("active", x === b));
    $("#tab-reco").classList.toggle("hidden", b.dataset.tab !== "reco");
    $("#tab-assign").classList.toggle("hidden", b.dataset.tab !== "assign");
  });

  await refreshStatus();
  if (state.ctxReady) {
    const r = await api.post("/api/recommend", { per_group: 3 });
    if (r.ok) renderReco(r);
  }
});
