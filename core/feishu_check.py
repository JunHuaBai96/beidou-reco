# -*- coding: utf-8 -*-
"""飞书接入能力检测。

只做只读探测：换 tenant_access_token → 逐个资源读 1~2 行，判定
「可接入 / 不可接入」，并给出失败原因、缺失权限、修复步骤。

设计要点：
- 每个资源独立探测，互不污染（一张表挂掉不影响其他表结论）。
- 错误码映射到中文原因 + 修复动作；未命中的错误码原样透出，不猜。
- 区分「权限不足」和「配置错误」：只有权限类失败才进 blocked（暂不能发布）。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://open.feishu.cn/open-apis"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ---------------------------------------------------------------- 资源清单
# core=True 是需求里的「四类资源」，task 为附加（出单依据）
RESOURCES = [
    {"key": "daily", "name": "每日剧单（主）", "core": True, "kind": "bitable",
     "app": "NXNSbK2sLahwQJs2Sb8c6IvTnYb", "table": "tblRRpuSJYMYHPqE",
     "wiki": "WaNHwCPlEiwRIfkUcLBceHswn9c",
     "scope": "bitable:app:readonly"},
    {"key": "yc_new", "name": "YC 一周新剧", "core": True, "kind": "bitable",
     "app": "Px5Ab0oIyaeVMYsgwaGceJPrn9f", "table": "tblPbjDnpw1e1C8V",
     "scope": "bitable:app:readonly"},
    {"key": "todo", "name": "每日必做&推荐剧单", "core": True, "kind": "bitable",
     "app": "X2zHbte8baLRTCsPQjhcTy7xnYb", "table": "tblCqkdiEkFOBzmB",
     "scope": "bitable:app:readonly"},
    {"key": "task", "name": "短剧投放任务表（出单）", "core": False, "kind": "bitable",
     "app": "L2tNbbMUCa9UNjsB7wFcZ1IJnsd", "table": "tblFKikt11z0KXml",
     "scope": "bitable:app:readonly"},
]

# code -> (分类, 原因, [修复动作])
# 分类：auth 认证 / perm 权限 / config 配置 / param 参数 / net 网络 / unknown
ERRORS = {
    99991663: ("auth", "App Secret 不正确，或应用版本尚未发布（改完权限必须重新创建版本并发布）",
               ["核对 App Secret 是否完整（32 位，无空格）",
                "开放平台 → 版本管理与发布 → 创建版本并发布"]),
    99991661: ("auth", "tenant_access_token 无效或已过期", ["重新点一次检测"]),
    99991668: ("auth", "tenant_access_token 不合法", ["重新点一次检测"]),
    10014: ("auth", "App ID 不存在", ["核对 App ID 是否为 cli_ 开头的完整串"]),
    10003: ("param", "参数错误：app_token / table_id / range 可能不正确", ["核对表 ID"]),
    91403: ("perm", "应用没有该资源的访问权限",
            ["把应用加为该文档 / 知识库的协作者（可阅读）"]),
    1254302: ("perm", "应用不是该多维表格的协作者",
              ["打开该表 → 右上角「…」→ 添加文档协作者 → 搜索应用名 → 可阅读"]),
    1254005: ("config", "表不存在，或在知识库中未正确解析",
              ["确认 table_id 正确", "知识库成员里添加该应用"]),
    131006: ("perm", "应用不在该知识库（wiki）空间中",
             ["知识库设置 → 成员管理 → 添加应用"]),
    90202: ("config", "电子表格 token 或 sheet 标识不正确", ["核对 spreadsheet token 与 sheet_id"]),
    90204: ("param", "电子表格范围不合法", ["检查工作表名与区间"]),
    1061045: ("perm", "无权限访问该电子表格", ["把应用加为该表格协作者（可阅读）"]),
    1063001: ("param", "电子表格参数错误", ["核对 sheet_id"]),
}
PERM_CODES = {v[0] for k, v in ERRORS.items() if v[0] == "perm"}


class FeishuError(Exception):
    def __init__(self, code: int, msg: str, path: str = "", http: int = 0):
        self.code = int(code or 0)
        self.msg = msg or ""
        self.path = path
        self.http = http
        super().__init__("飞书接口错误 %s: %s" % (self.code, self.msg))


def explain(code: int, msg: str) -> dict:
    code = int(code or 0)
    msg = msg or ""
    if code == 99991672:      # Access denied / Scope not granted
        scopes = re.findall(r"\[([a-zA-Z_:,. ]+)\]", msg)
        need = scopes[0].strip() if scopes else ""
        link = ""
        m = re.search(r"(https://open\.feishu\.cn/\S+)", msg)
        if m:
            link = m.group(1).rstrip('",')
        fix = []
        if need:
            fix.append("开通任一权限：" + need)
        if link:
            fix.append("点下方「一键申请」直接开通")
        fix.append("开通后必须「创建版本并发布」，管理员审核通过才生效")
        fix.append("若已开通仍报错：把应用加为该文档 / 知识库协作者（可阅读）")
        return {"category": "perm",
                "reason": "应用未开通该接口所需权限（scope 未授予）" + ("：" + need if need else ""),
                "fix": fix, "scopes": need, "link": link}
    if code == 1254302 and "No permission" in msg:
        return {"category": "perm", "reason": "应用不是该多维表格的协作者",
                "fix": ["打开该表 → 右上角「…」→ 添加文档协作者 → 搜索应用名 → 可阅读"]}
    hit = ERRORS.get(code)
    if hit:
        cat, reason, fix = hit
        return {"category": cat, "reason": reason, "fix": fix}
    if code == 0 and msg:
        return {"category": "unknown", "reason": msg, "fix": ["查看飞书开放平台该接口文档"]}
    return {"category": "unknown",
            "reason": "未收录的错误码 %s：%s" % (code, msg or "无描述"),
            "fix": ["把该错误码发我，我补进映射表"]}


def validate(app_id: str, app_secret: str) -> list:
    """L0 本地格式校验，不发请求。返回问题列表（空 = 通过）。"""
    errs = []
    a = (app_id or "").strip()
    s = (app_secret or "").strip()
    if not a:
        errs.append("未填写 App ID")
    elif not a.startswith("cli_"):
        errs.append("App ID 应以 cli_ 开头（当前：%s…）" % a[:6])
    elif len(a) < 16:
        errs.append("App ID 长度偏短（%d），可能没复制完整" % len(a))
    if not s:
        errs.append("未填写 App Secret")
    elif len(s) < 20:
        errs.append("App Secret 长度偏短（%d），可能没复制完整" % len(s))
    elif " " in s:
        errs.append("App Secret 含空格，请重新复制")
    return errs


class Client:
    """极简飞书客户端，只读。"""

    def __init__(self, app_id: str, app_secret: str, timeout: int = 20):
        self.app_id = (app_id or "").strip()
        self.app_secret = (app_secret or "").strip()
        self.timeout = timeout
        self._token = ""

    # ---- 鉴权 ----
    def token(self) -> str:
        if self._token:
            return self._token
        body = json.dumps({"app_id": self.app_id,
                           "app_secret": self.app_secret}).encode()
        req = urllib.request.Request(
            API + "/auth/v3/tenant_access_token/internal", data=body, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8",
                     "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raise FeishuError(e.code, "HTTP %s（网关拒绝）" % e.code,
                              "/auth/v3/tenant_access_token/internal", e.code)
        except Exception as e:
            raise FeishuError(-1, "网络不可达：%s" % e, "/auth/...")
        if d.get("code") != 0:
            raise FeishuError(d.get("code"), d.get("msg") or "", "/auth/...")
        self._token = d.get("tenant_access_token") or ""
        if not self._token:
            raise FeishuError(-2, "返回体里没有 tenant_access_token", "/auth/...")
        return self._token

    # ---- GET ----
    def get(self, path: str, params: dict | None = None) -> dict:
        url = API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url, headers={"Authorization": "Bearer " + self.token(),
                          "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raw = ""
            try:
                raw = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            code, msg = e.code, (raw[:400] or ("HTTP %s" % e.code))
            try:
                d0 = json.loads(raw)          # 先整体解析，成功再取字段
                code = d0.get("code") or e.code
                msg = d0.get("msg") or msg
            except Exception:
                pass
            raise FeishuError(code, msg, path, e.code)
        except Exception as e:
            raise FeishuError(-1, "网络不可达：%s" % e, path)
        if d.get("code") != 0:
            raise FeishuError(d.get("code"), d.get("msg") or "", path)
        return d.get("data") or {}

    # ---- 具体读法 ----
    def bitable_probe(self, app: str, table: str) -> dict:
        data = self.get("/bitable/v1/apps/%s/tables/%s/records" % (app, table),
                        {"page_size": 1})
        items = data.get("items") or []
        header = list((items[0].get("fields") or {}).keys()) if items else []
        total = data.get("total")
        if total is None:
            total = data.get("has_more") and 1 or len(items)
        return {"rows": int(total or 0), "header": header[:8], "sample": len(items)}

    def wiki_resolve(self, wiki_token: str, obj_type: str = "") -> str:
        data = self.get("/wiki/v2/spaces/get_node", {"token": wiki_token})
        node = data.get("node") or {}
        obj = node.get("obj_token") or ""
        if not obj:
            raise FeishuError(-3, "wiki 节点未返回 obj_token", "/wiki/...")
        return obj


def _probe(cli: Client, r: dict) -> dict:
    """探测单个资源，成功返回 {rows, header}，失败抛 FeishuError。"""
    try:
        return cli.bitable_probe(r["app"], r["table"])
    except FeishuError as e:
        # 每日剧单挂在知识库里：权限/定位问题可用 wiki 解析重试一次
        if r.get("wiki") and e.code in (91403, 1254005, 131006, 10003, -1):
            try:
                real = cli.wiki_resolve(r["wiki"])
            except FeishuError:
                raise e
            if real and real != r["app"]:
                got = cli.bitable_probe(real, r["table"])
                got["via"] = "wiki 解析"
                return got
        raise


def enrich(it: dict, app_id: str) -> dict:
    """失败项补齐「缺哪个 scope」和申请链接（部分接口只回 No permission）。"""
    if it.get("status") != "fail" or not it.get("need"):
        return it
    need = "、".join(it["need"])
    fix = it.get("fix") or []
    if not any(x.startswith("开通任一权限") for x in fix):
        it["fix"] = ["开通任一权限：" + need] + fix
    reason = it.get("reason") or "无权限"
    if need not in reason:
        it["reason"] = "%s（该表需要：%s）" % (reason, need)
    if not it.get("link") and (app_id or "").startswith("cli_"):
        it["link"] = ("https://open.feishu.cn/app/%s/auth?q=%s"
                      % (app_id, it["need"][0]))
    return it


def check(app_id: str, app_secret: str) -> dict:
    """完整检测。返回结构见 README「接入检测」。"""
    t0 = time.time()
    out = {
        "ok": False, "app": _mask(app_id), "token_ok": False,
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_ms": 0, "items": [], "ready": [], "blocked": [],
        "notes": [], "core_ok": 0, "core_total": 0,
    }
    for r in RESOURCES:
        if r["core"]:
            out["core_total"] += 1

    # L0 格式
    fmt = validate(app_id, app_secret)
    if fmt:
        out["notes"] = fmt
        for r in RESOURCES:
            out["items"].append(_item(r, "skip", None, "凭证格式不正确，未发起请求", fmt))
            out["blocked"].append(r["name"])
        out["elapsed_ms"] = int((time.time() - t0) * 1000)
        return out

    cli = Client(app_id, app_secret)

    # L1 鉴权
    try:
        cli.token()
        out["token_ok"] = True
    except FeishuError as e:
        info = explain(e.code, e.msg)
        out["notes"].append("鉴权失败：%s（code %s）" % (info["reason"], e.code))
        for r in RESOURCES:
            out["items"].append(_item(r, "fail", e, info["reason"], info["fix"],
                                      info["category"], info.get("link", "")))
            out["blocked"].append(r["name"])
        out["elapsed_ms"] = int((time.time() - t0) * 1000)
        return out

    # L2 逐资源
    for r in RESOURCES:
        st = time.time()
        try:
            got = _probe(cli, r)
            it = _item(r, "ok", None, "", [])
            it["rows"] = got.get("rows", 0)
            it["header"] = got.get("header") or []
            if got.get("via"):
                it["note"] = got["via"]
            if r["core"]:
                out["core_ok"] += 1
            out["ready"].append(r["name"])
        except FeishuError as e:
            info = explain(e.code, e.msg)
            it = _item(r, "fail", e, info["reason"], info["fix"], info["category"],
                       info.get("link", ""))
            enrich(it, app_id)
            out["blocked"].append(r["name"])
        except Exception as e:  # 任何意外都如实上报，不吞
            it = _item(r, "fail", None, "探测异常：%s: %s" % (type(e).__name__, e),
                       ["把该信息发我"], "unknown")
            out["blocked"].append(r["name"])
        it["ms"] = int((time.time() - st) * 1000)
        out["items"].append(it)

    out["ok"] = out["core_ok"] == out["core_total"] and not out["blocked"]
    out["elapsed_ms"] = int((time.time() - t0) * 1000)
    return out


def _item(r: dict, status: str, err, reason: str, fix: list,
          category: str = "", link: str = "") -> dict:
    return {
        "key": r["key"], "name": r["name"], "core": r["core"], "kind": r["kind"],
        "status": status,                      # ok / fail / skip
        "code": (err.code if err is not None else 0),
        "msg": (err.msg if err is not None else ""),
        "reason": reason, "fix": fix, "category": category,
        "need": [r["scope"]] if r.get("scope") and status == "fail" else [],
        "rows": 0, "header": [], "ms": 0, "note": "", "link": link,
    }


def _mask(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    return s[:8] + "…" + s[-4:] if len(s) > 14 else s[:6] + "…"


def summary_line(res: dict) -> str:
    """一行摘要，给状态条 / 日志用。"""
    if not res.get("app"):
        return "飞书未配置"
    if not res.get("token_ok") and res.get("blocked"):
        return "飞书鉴权失败（%s）" % (res.get("notes") or ["secret 或发布状态有误"])[0][:40]
    return "飞书 %d/%d 可接入" % (res.get("core_ok", 0), res.get("core_total", 4))
