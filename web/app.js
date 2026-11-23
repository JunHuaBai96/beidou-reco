/* 北斗短剧素材推荐助手 · 前端逻辑（无框架） */
const $ = (s) => document.querySelector(s);
const state = { ctxReady: false, recoRows: [], assignRows: [], poll: null, exited: false };

const api = {
  async get(p, timeoutMs) { return req("GET", p, null, timeoutMs); },
  async post(p, b, timeoutMs) { return req("POST", p, b, timeoutMs); },
};

/* 统一请求：超时保护 + 非 JSON 响应明确报错（否则按钮会卡在「生成中…」） */
async function req(method, p, body, timeoutMs) {
  const hasBody = body !== undefined && body !== null;
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs || 90000);
  try {
    const r = await fetch(p, {
      method,
      headers: hasBody ? { "Content-Type": "application/json" } : undefined,
      body: hasBody ? JSON.stringify(body) : undefined,
      signal: ctl.signal,
    });
    const txt = await r.text();
    let data;
    try { data = JSON.parse(txt); }
    catch (e) { throw new Error("服务返回异常（HTTP " + r.status + "）：" + txt.slice(0, 120)); }
    return data;
  } catch (e) {
    if (e.name === "AbortError") throw new Error("请求超时，请重试");
    if (e instanceof TypeError) throw new Error("无法连接本机服务（可能已退出），请重新启动程序");
    throw e;
  } finally { clearTimeout(t); }
}

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
  renderFsPill(s);
  if (s.feishu_check) state.lastFsCheck = s.feishu_check;
  const masked = s.cfg && s.cfg.masked;
  if (!masked) { $("#setupBanner").classList.remove("hidden"); setPill("未配置", "bad"); }
  else {
    $("#setupBanner").classList.add("hidden");
    const legacy = s.cfg.key_is_legacy ? "（旧版配置）" : "";
    setPill("已配置 " + masked + legacy, legacy ? "warn" : "ok");
    const pill = $("#statusPill");
    pill.title = "北斗 Key 绑定版本：" + (s.cfg.key_bound_version || "未知")
      + "\n当前程序版本见左上角；旧版配置仍可用，重新保存 Key 即更新绑定版本";
  }
  fetch("/api/health").then(r => r.json()).then(h => {
    if (h && h.version) $("#verTag").textContent = "v" + h.version;
  }).catch(() => {});
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
        else {
          setPill("拉取失败", "bad");
          // 拉取失败但本机还有旧数据时，仍允许推荐（降级用旧数据）
          if (st.has_ctx && st.summary) {
            state.ctxReady = true;
            renderGroups(st.summary.groups || []);
            $("#ctxInfo").textContent = "拉取失败，正在使用上次缓存数据（可重试拉取）";
            flash("拉取失败，已回退到缓存数据");
          }
        }
        if (st.summary && st.state === "done") renderGroups(st.summary.groups || []);
        $("#ctxInfo").textContent = st.state === "done"
          ? "数据：" + (st.summary?.generated || "")
          : $("#ctxInfo").textContent;
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

/* 每组条数：读取输入并夹在 1~20，回写到控件（非法输入回退 3） */
function perGroupVal() {
  const el = $("#perGroup");
  let n = parseInt(el.value, 10);
  if (!Number.isFinite(n)) n = 3;
  n = Math.max(1, Math.min(20, n));
  el.value = n;
  return n;
}

function renderReco(data) {
  const rows = data.rows || [];
  state.recoRows = rows;
  const s = data.summary || {};
  const d = s.dash || {};
  const cnt = {};
  rows.forEach(r => { cnt[r.group] = (cnt[r.group] || 0) + 1; });
  const cntText = Object.entries(cnt)
    .map(([g, n]) => `${g.split("|").pop()} ${n}`).join(" · ");
  $("#recoMeta").innerHTML =
    `数据时间 ${esc(s.generated || "")}；每组条数：目标 ${esc(s.per_group ?? "-")}（实际 ${esc(cntText || "-")}）` +
    `；数据源：${Object.entries(s.sources || {}).map(([k, v]) => `${esc(k)} ${esc(v)}`).join(" · ")}` +
    (d.yesterday ? `<br/>看板昨日 ${esc(d.yesterday.date)}：${d.yesterday.order_count} 单 ¥${d.yesterday.amount}；今日：${d.today?.order_count ?? "-"} 单 ¥${d.today?.amount ?? "-"}` : "") +
    (s.feishu && s.feishu.configured && (s.feishu.core_ok || 0) < (s.feishu.core_total || 4)
      ? `<br/><span style="color:var(--warn)">飞书接入 ${s.feishu.core_ok}/${s.feishu.core_total}，未接入：${esc((s.feishu.blocked || []).join("、"))}（这些源未参与加权，建议去设置里检测修复）</span>` : "") +
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
function showQuitOverlay() {
  $("#quitOverlay").classList.remove("hidden");
  setPill("已退出", "idle");
  showProgress(false);
}
function dead() { return state.exited; }
function openModal() {
  if (state.exited) return;
  $("#modal").classList.remove("hidden");
  if (state.lastFsCheck) renderFsResult(state.lastFsCheck);
}
function closeModal() {
  $("#modal").classList.add("hidden");
  $("#testMsg").textContent = "";
}
async function saveSettings() {
  // 留空 = 不修改（避免把已保存的 Key / Secret 清掉）
  const body = { feishu_app_id: $("#fsId").value.trim() };
  const key = $("#apiKey").value.trim();
  if (key) body.api_key = key;
  const sec = $("#fsSecret").value.trim();
  if (sec) body.feishu_app_secret = sec;
  let r;
  try { r = await api.post("/api/settings", body); }
  catch (e) { alert("保存失败：" + (e.message || e)); return; }
  if (r.ok) {
    $("#fsSecret").value = "";
    if (r.feishu_configured) $("#fsSecret").placeholder = "已保存，留空则不修改";
    state.lastFsCheck = null;
    $("#fsResult").classList.add("hidden");
    $("#modal").classList.add("hidden");
    refreshStatus();
  }
}
async function testConn() {
  $("#testMsg").textContent = "测试中…";
  let r;
  try { r = await api.post("/api/test", { api_key: $("#apiKey").value.trim() }, 30000); }
  catch (e) { r = { ok: false, error: e.message || String(e) }; }
  $("#testMsg").textContent = r.ok ? `连接成功：${r.groups} 个发布组${r.user ? "，账号 " + r.user : ""}` : "失败：" + r.error;
}

/* ---------- 飞书接入检测 ---------- */
async function checkFeishu() {
  const btn = $("#btnFsCheck");
  const box = $("#fsResult");
  const appId = $("#fsId").value.trim();
  const secret = $("#fsSecret").value.trim();
  box.classList.remove("hidden");
  box.innerHTML = `<p class="muted">正在逐个探测四类资源……</p>`;
  btn.disabled = true; btn.textContent = "检测中…";
  try {
    const r = await api.post("/api/feishu-check", { app_id: appId, app_secret: secret });
    state.lastFsCheck = r;
    renderFsResult(r);
    refreshStatus();
  } catch (e) {
    box.innerHTML = `<p class="fs-bad">检测请求失败：${esc(e.message || e)}</p>`;
  }
  btn.disabled = false; btn.textContent = "检测接入能力";
}

function fsTag(st) {
  if (st === "ok") return `<span class="fs-ok">可接入</span>`;
  if (st === "skip") return `<span class="fs-skip">未检测</span>`;
  return `<span class="fs-bad">不可接入</span>`;
}

function renderFsResult(r) {
  const box = $("#fsResult");
  box.classList.remove("hidden");
  if (!r || !r.configured) {
    box.innerHTML = `<p class="fs-warn">还没填写 App ID / Secret，无从检测。</p>` +
      ((r && r.notes) || []).map(n => `<p class="fs-warn">· ${esc(n)}</p>`).join("");
    return;
  }
  const coreOk = r.core_ok || 0, coreAll = r.core_total || 4;
  const good = !!r.ok;
  const head = `<div class="fs-head ${good ? "ok" : "bad"}">
      <strong>${good ? "四类资源全部可接入" : "核心资源可接入 " + coreOk + "/" + coreAll}</strong>
      <span>耗时 ${r.elapsed_ms || 0} ms${r.app ? " · 应用 " + esc(r.app) : ""}</span>
    </div>`;
  const notes = (r.notes || []).length
    ? `<ul class="fs-notes">${r.notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul>` : "";
  const rows = (r.items || []).map(it => `<tr>
      <td>${esc(it.name)}${it.core ? "" : `<div class="muted">附加</div>`}</td>
      <td>${fsTag(it.status)}</td>
      <td>${it.status === "ok"
        ? `<span class="muted">${it.rows ? it.rows + " 行" : "已连通"}${it.note ? "（" + esc(it.note) + "）" : ""}</span>`
        : `<div class="fs-reason">${esc(it.reason || "失败")}</div>${it.code ? `<div class="muted">code ${it.code}</div>` : ""}`}</td>
      <td>${(it.need || []).map(x => `<code>${esc(x)}</code>`).join(" ") || `<span class="muted">—</span>`}</td>
      <td class="fs-fix">${(it.fix || []).map(x => esc(x)).join("<br/>") || `<span class="muted">—</span>`}${
        it.link ? `<br/><a class="fs-link" href="${esc(it.link)}" target="_blank" rel="noreferrer">一键申请权限 →</a>` : ""}</td>
    </tr>`).join("");
  const blocked = (r.blocked || []).filter(Boolean);
  const blockLine = blocked.length
    ? `<p class="fs-warn"><strong>因权限/配置不足暂不能发布的项目：</strong>${blocked.map(esc).join("、")}</p>`
    : (r.ok ? `<p class="fs-ok-line">四类资源均可用，飞书数据已参与推荐加权。</p>`
            : `<p class="fs-warn">尚未完成检测：请先修正上面的凭证格式问题再点一次。</p>`);
  box.innerHTML = head + notes +
    `<div class="fs-tablewrap"><table class="fs-table"><thead><tr>
       <th>资源</th><th>状态</th><th>结论 / 失败原因</th><th>缺失权限</th><th>修复动作</th>
     </tr></thead><tbody>${rows}</tbody></table></div>` + blockLine;
}

function renderFsPill(st) {
  const el = $("#fsPill");
  if (!el) return;
  const cfg = st.cfg && st.cfg.feishu;
  const ck = st.feishu_check || (st.summary && st.summary.feishu) || st.feishu_status;
  if (!cfg) {
    el.className = "pill idle"; el.textContent = "飞书未配置";
    el.title = "未填写 App ID / Secret，按 MCP+看板+监控三源运行";
    return;
  }
  if (!ck || !ck.items || !ck.items.length) {
    el.className = "pill run"; el.textContent = "飞书待检测";
    el.title = "进设置点「检测接入能力」";
    return;
  }
  const okN = ck.core_ok || 0, all = ck.core_total || 4;
  el.className = "pill " + (okN === all ? "ok" : "bad");
  el.textContent = `飞书 ${okN}/${all}`;
  el.title = (ck.blocked || []).join("、") || "全部可接入";
}

/* ---------- 事件 ---------- */
document.addEventListener("DOMContentLoaded", async () => {
  const st = await api.get("/api/status");
  if (st.cfg?.masked) $("#apiKey").placeholder = st.cfg.masked;
  if (st.cfg?.feishu_app_id) $("#fsId").value = st.cfg.feishu_app_id;
  if (st.cfg?.feishu) $("#fsSecret").placeholder = "已保存，留空则不修改";

  $("#btnSettings").onclick = openModal;
  $("#btnSetup").onclick = openModal;
  $("#btnCancel").onclick = closeModal;
  // 每组条数：输入即校验（1~20），失焦/回车归一到合法值
  $("#perGroup").addEventListener("change", () => perGroupVal());
  $("#perGroup").addEventListener("blur", () => perGroupVal());
  // 退出程序：确认 → 通知服务端关停 → 显示已退出遮罩
  $("#btnQuit").onclick = async () => {
    if (state.exited) return;
    if (!confirm("确定退出程序？\n本机服务将停止，未拉取完的数据会中断。")) return;
    state.exited = true;
    if (state.poll) { clearInterval(state.poll); state.poll = null; }
    try { await api.post("/api/shutdown", {}); } catch (e) { /* 服务可能已先行退出 */ }
    showQuitOverlay();
  };
  // 取消/重置交互：Esc 或点击遮罩也能关闭，关闭不残留任何状态
  $("#modal").addEventListener("click", (e) => { if (e.target === $("#modal")) closeModal(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("#modal").classList.contains("hidden")) closeModal();
  });
  $("#btnSave").onclick = saveSettings;
  $("#btnTest").onclick = testConn;
  $("#btnFsCheck").onclick = checkFeishu;
  $("#btnRefresh").onclick = async () => {
    if (dead()) return;
    if (state.poll) { flash("正在拉取中，请稍候…"); return; }
    try {
      const r = await api.post("/api/refresh", {});
      if (r.ok) { showProgress(true); $("#log").textContent = "开始拉取…"; setPill("拉取中…", "run"); refreshStatus(); }
      else alert(r.error || "启动失败");
    } catch (e) { alert("拉取请求失败：" + (e.message || e)); }
  };
  $("#btnRun").onclick = async () => {
    const btn = $("#btnRun");
    if (dead()) return;
    if (btn.disabled) return;  // 防重入
    if (!state.ctxReady) {
      if (state.poll) { flash("正在拉取数据，完成后即可生成推荐"); return; }
      if (confirm("还没拉取数据，是否现在拉取？")) $("#btnRefresh").click();
      return;
    }
    btn.disabled = true; btn.textContent = "生成中…";
    try {
      let r;
      try {
        r = await api.post("/api/recommend", { per_group: perGroupVal() }, 120000);
      } catch (e) { r = { ok: false, error: e.message || String(e) }; }
      if (r.ok) {
        try { renderReco(r); }
        catch (e) { alert("推荐结果渲染失败：" + (e.message || e)); }
      } else alert(r.error || "生成失败");
    } finally {
      btn.disabled = false; btn.textContent = "生成推荐";  // 任何路径都恢复可点
    }
  };
  $("#btnAssign").onclick = async () => {
    const btn = $("#btnAssign");
    if (dead()) return;
    if (btn.disabled) return;  // 防重入
    const names = $("#names").value;
    if (!names.trim()) return alert("请先填写剧名");
    btn.disabled = true; btn.textContent = "生成中…";
    try {
      let r;
      try {
        r = await api.post("/api/assign", { names }, 180000);
      } catch (e) { r = { ok: false, error: e.message || String(e) }; }
      if (r.ok) {
        try { renderAssign(r); }
        catch (e) { alert("定组结果渲染失败：" + (e.message || e)); }
      } else alert(r.error || "生成失败");
    } finally {
      btn.disabled = false; btn.textContent = "生成定组表";  // 任何路径都恢复可点
    }
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
    try {
      const r = await api.post("/api/recommend", { per_group: perGroupVal() });
      if (r.ok) renderReco(r);
    } catch (e) { /* 首屏推荐失败不打断后续交互，可手动重试 */ }
  }
});
