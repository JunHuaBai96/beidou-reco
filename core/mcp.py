# -*- coding: utf-8 -*-
"""北斗矩阵发布 MCP 客户端（标准 JSON-RPC over HTTP）。

网关：http://gw-.../mcp-servers/beidou-matrix-publish
鉴权：headers 里 x-api-key = 用户自己的 API Key（bd_ 开头）
注意：
  - 必须用 {"jsonrpc":"2.0","method":"tools/call"} 信封，裸 {"tool","args"} 会返回 202 空体
  - 工具返回的 content[0].text 是「# 标题\n\n"{json}"」包装，需要二次解包
  - 串行调用，429/504 退避重试
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

DEFAULT_URL = ("http://gw-da5tlpem1hktoldq1bc0-w2wqzerrpavaetkkal-cn-beijing"
               ".alicloudapi.com/mcp-servers/beidou-matrix-publish")


class McpError(Exception):
    pass


def _unwrap(text: str):
    """把 '# 标题\n\n"{...}"' 解成 dict。"""
    if not text:
        return None
    s = text.find('"')
    e = text.rfind('"')
    if s < 0 or e <= s:
        return None
    try:
        x = json.loads(text[s:e + 1])
    except Exception:
        return None
    n = 0
    while isinstance(x, str) and n < 5:
        try:
            x = json.loads(x)
        except Exception:
            return None
        n += 1
    return x if isinstance(x, dict) else None


class Mcp:
    def __init__(self, url: str = DEFAULT_URL, api_key: str = ""):
        self.url = (url or DEFAULT_URL).strip()
        self.api_key = (api_key or "").strip()
        self._lock = threading.Lock()
        self._inited = False
        self.calls = 0

    # ---------- 基础通信 ----------
    def _post(self, payload: dict, timeout: int = 90) -> dict:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(self.url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        req.add_header("x-api-key", self.api_key)
        req.add_header("Accept-Encoding", "identity")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
        if not raw.strip():
            raise McpError("网关返回空响应（检查 API Key 是否有效）")
        return json.loads(raw)

    def _rpc(self, method: str, params: dict, timeout: int = 60) -> dict:
        o = self._post({"jsonrpc": "2.0", "id": 1, "method": method,
                        "params": params or {}}, timeout=timeout)
        if "error" in o:
            raise McpError(str(o["error"])[:300])
        return o.get("result") or {}

    def initialize(self, force: bool = False) -> dict:
        if self._inited and not force:
            return {"ok": True}
        if not self.api_key:
            raise McpError("未填写 API Key")
        self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "beidou-reco", "version": "1.0.0"},
        }, timeout=30)
        self._inited = True
        return {"ok": True}

    def tools(self) -> list:
        self.initialize()
        return (self._rpc("tools/list", {}) or {}).get("tools") or []

    # ---------- 业务调用 ----------
    def call(self, tool: str, args: dict | None = None, tries: int = 4,
             timeout: int = 90) -> dict:
        """返回工具业务 body（一般含 data/total/code）。"""
        args = args or {}
        with self._lock:
            self.initialize()
            last = None
            for i in range(tries):
                self.calls += 1
                try:
                    res = self._rpc("tools/call", {"name": tool, "arguments": args},
                                    timeout=timeout)
                except urllib.error.HTTPError as e:
                    last = "HTTP %s" % e.code
                    time.sleep(min(2 + i * 2, 8))
                    continue
                except Exception as e:  # 网络/超时
                    last = repr(e)
                    time.sleep(min(2 + i * 2, 8))
                    continue
                content = res.get("content") or []
                txt = content[0].get("text", "") if content else ""
                if res.get("isError"):
                    raise McpError(txt[:300] or "工具调用失败")
                out = _unwrap(txt)
                if out is None:
                    last = "返回无法解析: " + txt[:160]
                    time.sleep(1 + i)
                    continue
                if out.get("code") == 429:
                    last = "429 限流"
                    time.sleep(3 + i * 3)
                    continue
                return out
            raise McpError("调用 %s 失败：%s" % (tool, last))

    # ---------- 便捷封装 ----------
    def body(self, tool: str, args: dict | None = None, **kw):
        return (self.call(tool, args, **kw) or {}).get("body") or {}

    def groups(self) -> list:
        rows, page = [], 1
        while True:
            b = self.body("get_api_fb_group_list", {"page": page, "page_size": 50})
            d = b.get("data") or []
            rows.extend(d)
            if len(d) < 50 or page >= 5:
                break
            page += 1
        return [{"id": g.get("id"), "name": g.get("name"),
                 "cat": g.get("category_id"), "pages": g.get("page_count") or 0,
                 "status": g.get("status")} for g in rows]

    def library(self, category_id: int, page_size: int = 50) -> list:
        rows, page = [], 1
        while True:
            b = self.body("get_api_fb_serial_list",
                          {"category_id": category_id, "page_size": page_size, "page": page})
            d = b.get("data") or []
            rows.extend(d)
            if len(d) < page_size or page >= 40:
                break
            page += 1
        return rows

    def page_count(self, group_id: int) -> int:
        try:
            b = self.body("get_api_fb_page_list",
                          {"group_id": group_id, "page": 1, "page_size": 1})
            return int(b.get("total") or 0)
        except Exception:
            return 0

    def orders(self, days: int = 30, page_size: int = 100) -> list:
        """按剧聚合的订单（MCP 自带聚合），含 group_name / order_count / amount。"""
        rows, page = [], 1
        while True:
            b = self.body("get_api_fb_order_sett_list",
                          {"page": page, "page_size": page_size, "sort_by": "order_count",
                           "order": "desc"})
            d = b.get("data") or []
            rows.extend(d)
            if len(d) < page_size or page >= 12:
                break
            page += 1
        return rows

    def report_groups(self, start: str, end: str) -> list:
        b = self.body("get_api_fb_report_group_list",
                      {"start_date": start, "end_date": end})
        return b.get("data") or []

    def plaza(self, app_id: str, page_size: int = 100, max_pages: int = 25,
              order_field: str | None = None) -> list:
        out, page = [], 1
        while page <= max_pages:
            args = {"app_id": app_id, "page_size": page_size, "page_num": page}
            if order_field:
                args.update({"order_field": order_field, "order_dir": "desc"})
            b = self.body("get_api_fb_task_page", args)
            d = b.get("data") or []
            if not d:
                break
            out.extend(d)
            tc = (b.get("page") or {}).get("total_count") or 0
            if len(d) < page_size or (tc and page * page_size >= tc):
                break
            page += 1
        return out

    def search_drama(self, title: str, app_id: str, page_size: int = 20) -> list:
        b = self.body("get_api_fb_task_page",
                      {"app_id": app_id, "title": title, "page_size": page_size,
                       "page_num": 1}, tries=2)
        return b.get("data") or []
