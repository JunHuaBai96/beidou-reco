# -*- coding: utf-8 -*-
"""外部数据源：运营看板、上新监控面板、飞书四表（可选，需要飞书应用凭证）。

看板与监控面板是公开页面，无需鉴权；飞书四表需要 tenant_access_token，
用户可在设置里填飞书自建应用 app_id / app_secret（只读权限即可），
不填则自动跳过，推荐逻辑退回 MCP + 看板 + 监控面板三源。
"""
from __future__ import annotations

import json
import re
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
FEISHU_SHEET = ("RQU0sPdILhnQ2ctGKiFct2nyncG", "NVudqH", "A1:I2000")  # 分销推广剧单


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
    def __init__(self, app_id: str = "", app_secret: str = ""):
        self.app_id = (app_id or "").strip()
        self.app_secret = (app_secret or "").strip()
        self._token = None
        self.error = ""

    @property
    def enabled(self) -> bool:
        return bool(self.app_id and self.app_secret)

    def token(self) -> str:
        if self._token:
            return self._token
        body = json.dumps({"app_id": self.app_id,
                           "app_secret": self.app_secret}).encode()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=body, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read().decode())
        if d.get("code") != 0:
            raise RuntimeError("飞书鉴权失败：%s" % d.get("msg"))
        self._token = d["tenant_access_token"]
        return self._token

    def _api(self, path: str, params: dict | None = None) -> dict:
        url = "https://open.feishu.cn/open-apis" + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url, headers={"Authorization": "Bearer " + self.token()})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read().decode())
        if d.get("code") != 0:
            raise RuntimeError("飞书接口 %s 失败：%s" % (path, d.get("msg")))
        return d.get("data") or {}

    def records(self, app_token: str, table_id: str) -> list:
        rows, pt = [], None
        while True:
            p = {"page_size": 500}
            if pt:
                p["page_token"] = pt
            data = self._api("/bitable/v1/apps/%s/tables/%s/records" % (app_token, table_id), p)
            for it in data.get("items") or []:
                f = it.get("fields") or {}
                row = {k: _flat(v) for k, v in f.items()}
                row["_id"] = it.get("record_id")
                rows.append(row)
            if not data.get("has_more"):
                break
            pt = data.get("page_token")
        return rows

    def sheet(self) -> list:
        token, sid, rng = FEISHU_SHEET
        data = self._api("/sheets/v2/spreadsheets/%s/values/%s"
                         % (token, urllib.parse.quote("%s!%s" % (sid, rng))))
        vals = ((data.get("valueRange") or {}).get("values")) or []
        if not vals:
            return []
        head = [_flat(x) for x in vals[0]]
        out = []
        for v in vals[1:]:
            out.append({head[i]: _flat(v[i]) if i < len(v) else ""
                        for i in range(len(head))})
        return out

    def all_tables(self) -> dict:
        if not self.enabled:
            return {}
        out = {}
        for key, (app, tbl) in FEISHU_TABLES.items():
            try:
                out[key] = self.records(app, tbl)
            except Exception as e:
                self.error = "%s: %s" % (key, e)
                out[key] = []
        try:
            out["dist"] = self.sheet()
        except Exception as e:
            self.error = "%s: %s" % (self.error, e)
            out["dist"] = []
        return out


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
