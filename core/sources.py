# -*- coding: utf-8 -*-
"""外部数据源：运营看板、上新监控面板、飞书四表（可选，需要飞书应用凭证）。

看板与监控面板是公开页面，无需鉴权；飞书四表需要 tenant_access_token，
用户可在设置里填飞书自建应用 app_id / app_secret（只读权限即可），
不填则自动跳过，推荐逻辑退回 MCP + 看板 + 监控面板三源。
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

DASHBOARD_URL = "https://drama-dashboard.app.workbuddy.host/?sharecode=Y3P4I557RB"
MONITOR_URL = "https://e6efc0c913ac4c26b646abfbb6e6b0e6.app.codebuddy.work"

FEISHU_TABLES = {
    "daily": ("NXNSbK2sLahwQJs2Sb8c6IvTnYb", "tblRRpuSJYMYHPqE"),   # 每日剧单（主）
    "yc_new": ("Px5Ab0oIyaeVMYsgwaGceJPrn9f", "tblPbjDnpw1e1C8V"),  # YC 一周新剧
    "todo": ("X2zHbte8baLRTCsPQjhcTy7xnYb", "tblCqkdiEkFOBzmB"),    # 每日必做&推荐剧单
    "task": ("L2tNbbMUCa9UNjsB7wFcZ1IJnsd", "tblFKikt11z0KXml"),    # 短剧投放任务表（出单）
}


def _get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# ---------------- 运营看板 ----------------
def fetch_dashboard() -> dict:
    """返回 {generated, today, yesterday:{orders,dramas}, today_data:{...}, rank90}"""
    html = _get(DASHBOARD_URL, 40)
    m = re.search(r"window\.__DRAMA__\s*=\s*(\{.*?\});?\s*</script>", html, re.S)
    if not m:
        raise RuntimeError("看板页面结构变化，未找到数据块")
    data = json.loads(m.group(1))
    daily = data.get("daily") or []
    days = sorted(daily, key=lambda x: x.get("date") or "")
    out = {"generated": data.get("generated_bj"), "today": data.get("today"),
           "year_rank": (data.get("drama_rank") or [])[:20], "days": days}
    if days:
        out["today_data"] = days[-1]
        out["yesterday"] = days[-2] if len(days) > 1 else None
    return out


# ---------------- 上新监控面板 ----------------
def fetch_monitor() -> dict:
    """返回 {platform: [{date,title}]}，只保留近 14 天。"""
    html = _get(MONITOR_URL, 40)
    starts = [m.start() for m in re.finditer(r'<div class="platform-card ', html)]
    if not starts:
        raise RuntimeError("监控面板结构变化，未找到平台卡片")
    starts.append(len(html))
    out = {}
    for a, b in zip(starts, starts[1:]):
        blk = html[a:b]
        nm = re.search(r'class="name">([^<]+)</span>', blk)
        if not nm:
            continue
        items = []
        for d, t in re.findall(
                r'class="date drama-date[^"]*">([0-9\-]+)</div>\s*'
                r'<div class="title">(.*?)</div>', blk, re.S):
            items.append({"date": d.strip(),
                          "title": re.sub(r"<[^>]+>", "", t).strip()})
        out[nm.group(1).strip()] = items
    return out


# ---------------- 飞书（可选） ----------------
class Feishu:
    """飞书四表（只读）。

    与 v1.0 的差别：失败不再被静默吞掉。``self.status`` 保留逐资源结论
    （ok / fail + 错误码 + 中文原因 + 修复动作 + 缺失 scope），
    供界面与推荐理由如实标注数据源降级。
    """

    def __init__(self, app_id: str = "", app_secret: str = ""):
        self.app_id = (app_id or "").strip()
        self.app_secret = (app_secret or "").strip()
        self._cli = None
        self.error = ""
        self.status = {}
        self.last_check = None

    @property
    def enabled(self) -> bool:
        return bool(self.app_id and self.app_secret)

    def _client(self):
        from . import feishu_check as FC
        if self._cli is None:
            self._cli = FC.Client(self.app_id, self.app_secret)
        return self._cli

    def token(self) -> str:
        return self._client().token()

    # ---- 全量读取 ----
    def records(self, app_token: str, table_id: str) -> list:
        cli = self._client()
        rows, pt = [], None
        while True:
            p = {"page_size": 500}
            if pt:
                p["page_token"] = pt
            data = cli.get(
                "/bitable/v1/apps/%s/tables/%s/records" % (app_token, table_id), p)
            for it in data.get("items") or []:
                f = it.get("fields") or {}
                row = {k: _flat(v) for k, v in f.items()}
                row["_id"] = it.get("record_id")
                rows.append(row)
            if not data.get("has_more"):
                break
            pt = data.get("page_token")
        return rows

    def all_tables(self) -> dict:
        """拉四表；逐资源记状态，单张失败不影响其他表。"""
        if not self.enabled:
            return {}
        from . import feishu_check as FC
        out, self.error = {}, ""
        for r in FC.RESOURCES:
            st = time.time()
            try:
                rows = self.records(r["app"], r["table"])
                out[r["key"]] = rows
                self.status[r["key"]] = {
                    "key": r["key"], "name": r["name"], "core": r["core"],
                    "status": "ok", "code": 0, "msg": "", "category": "",
                    "reason": "", "fix": [], "need": [], "rows": len(rows),
                    "ms": int((time.time() - st) * 1000),
                }
            except FC.FeishuError as e:
                info = FC.explain(e.code, e.msg)
                out[r["key"]] = []
                st_item = FC.enrich({
                    "key": r["key"], "name": r["name"], "core": r["core"],
                    "status": "fail", "code": e.code, "msg": e.msg,
                    "category": info["category"], "reason": info["reason"],
                    "fix": info["fix"], "link": info.get("link", ""),
                    "need": [r["scope"]] if r.get("scope") else [],
                    "rows": 0, "ms": int((time.time() - st) * 1000),
                }, self.app_id)
                self.status[r["key"]] = st_item
                self.error = (self.error + "；" if self.error else "") + \
                    "%s：%s" % (r["name"], info["reason"])
            except Exception as e:
                out[r["key"]] = []
                self.status[r["key"]] = {
                    "key": r["key"], "name": r["name"], "core": r["core"],
                    "status": "fail", "code": -1, "msg": str(e),
                    "category": "unknown", "reason": "异常：%s" % e,
                    "fix": ["把该信息发我"], "need": [], "rows": 0,
                    "ms": int((time.time() - st) * 1000),
                }
        return out

    def status_summary(self) -> dict:
        """给上下文/界面用的轻量摘要。"""
        items = list(self.status.values())
        if not items:
            return {"ok": False, "configured": self.enabled, "ready": [],
                    "blocked": [], "core_ok": 0, "core_total": 4, "items": []}
        core = [x for x in items if x.get("core")]
        return {
            "ok": all(x["status"] == "ok" for x in core) and not
                  [x for x in items if x["status"] != "ok"],
            "configured": True,
            "ready": [x["name"] for x in items if x["status"] == "ok"],
            "blocked": [x["name"] for x in items if x["status"] != "ok"],
            "core_ok": len([x for x in core if x["status"] == "ok"]),
            "core_total": len(core),
            "error": self.error,
            "items": items,
        }

    def check(self) -> dict:
        """完整接入检测（发请求，逐资源探测）。"""
        from . import feishu_check as FC
        self.last_check = FC.check(self.app_id, self.app_secret)
        return self.last_check


def _flat(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, list):
        if not v:
            return ""
        if isinstance(v[0], str):
            return "/".join(v)
        parts = []
        for x in v:
            if isinstance(x, dict):
                parts.append(str(x.get("text") or x.get("name") or x.get("value") or ""))
            else:
                parts.append(str(x))
        return "/".join([p for p in parts if p])
    if isinstance(v, dict):
        for k in ("text", "value", "name", "date"):
            if k in v:
                return _flat(v[k])
        return json.dumps(v, ensure_ascii=False)
    return str(v)
