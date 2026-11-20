# -*- coding: utf-8 -*-
"""北斗短剧素材推荐助手 · 桌面版入口。

启动本地服务并自动打开浏览器；所有数据通过用户自己的北斗 API Key 拉取。
用法：
  BeidouReco.exe                    正常启动（默认 127.0.0.1:18765）
  BeidouReco.exe --selftest         无界面自检，结果写入 selftest_report.json
  BeidouReco.exe --port 18800       指定端口
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core import genre as G
from core import reco
from core.mcp import DEFAULT_URL, Mcp, McpError
from core.sources import Feishu

APP_NAME = "北斗短剧素材推荐助手"
VERSION = "1.0.0"
DEFAULT_PORT = 18765

APP_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "BeidouReco")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
CACHE_PATH = os.path.join(APP_DIR, "cache.json")
os.makedirs(APP_DIR, exist_ok=True)


def res_dir() -> str:
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------- 配置
def load_config() -> dict:
    cfg = {"api_key": "", "feishu_app_id": "", "feishu_app_secret": "",
           "mcp_url": DEFAULT_URL, "port": DEFAULT_PORT}
    try:
        cfg.update(json.load(open(CONFIG_PATH, encoding="utf-8")))
    except Exception:
        pass
    if os.environ.get("BEIDOU_API_KEY"):
        cfg["api_key"] = os.environ["BEIDOU_API_KEY"]
    return cfg


def save_config(cfg: dict):
    keep = {k: cfg.get(k, "") for k in
            ("api_key", "feishu_app_id", "feishu_app_secret", "mcp_url", "port")}
    json.dump(keep, open(CONFIG_PATH, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 状态
class State:
    def __init__(self):
        self.cfg = load_config()
        self.ctx = None
        self.ctx_at = 0.0
        self.lock = threading.Lock()
        self.job = {"state": "idle", "log": [], "error": "", "started": 0}

    def mcp(self, force_key: str = "") -> Mcp:
        key = force_key or self.cfg.get("api_key") or ""
        return Mcp(self.cfg.get("mcp_url") or DEFAULT_URL, key)

    def feishu(self) -> Feishu:
        return Feishu(self.cfg.get("feishu_app_id"), self.cfg.get("feishu_app_secret"))

    # ---- 缓存 ----
    def load_cache(self, max_age: int = 1800) -> bool:
        try:
            st = os.path.getmtime(CACHE_PATH)
            if time.time() - st > max_age:
                return False
            d = json.load(open(CACHE_PATH, encoding="utf-8"))
            if not d.get("groups"):
                return False
            self.ctx = d
            self.ctx_at = st
            return True
        except Exception:
            return False

    def dump_cache(self):
        try:
            d = {k: v for k, v in self.ctx.items() if k != "_mcp"}
            json.dump(d, open(CACHE_PATH, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass

    def refresh(self, log=print) -> dict:
        mcp = self.mcp()
        mcp.initialize(force=True)
        ctx = reco.build_context(mcp, self.feishu(), log=log)
        ctx["_mcp"] = mcp
        self.ctx = ctx
        self.ctx_at = time.time()
        return ctx


S = State()


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "BeidouReco/1.0"

    def log_message(self, fmt, *args):  # 静音
        try:
            S.job["log"].append("[http] " + (fmt % args))
            del S.job["log"][:-300]
        except Exception:
            pass

    # ---- 工具 ----
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _static(self, path: str, ctype: str):
        p = os.path.join(res_dir(), "web", path)
        try:
            data = open(p, "rb").read()
        except Exception:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        self._send(200, data, ctype)

    # ---- 路由 ----
    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            self._static("index.html", "text/html; charset=utf-8")
        elif p == "/static/app.js":
            self._static("app.js", "application/javascript; charset=utf-8")
        elif p == "/static/style.css":
            self._static("style.css", "text/css; charset=utf-8")
        elif p == "/api/status":
            self._json(self._status())
        elif p == "/api/context":
            self._json(self._context_payload())
        elif p == "/api/health":
            self._json({"ok": True, "version": VERSION})
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):
        p = self.path.split("?")[0]
        try:
            if p == "/api/settings":
                self._json(self._settings(self._body()))
            elif p == "/api/test":
                self._json(self._test(self._body()))
            elif p == "/api/refresh":
                self._json(self._refresh())
            elif p == "/api/recommend":
                self._json(self._recommend(self._body()))
            elif p == "/api/assign":
                self._json(self._assign(self._body()))
            elif p == "/api/export":
                self._json(self._export(self._body()))
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
        except McpError as e:
            self._json({"ok": False, "error": str(e)}, 200)
        except Exception as e:
            traceback.print_exc()
            self._json({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}, 200)

    # ---- 业务 ----
    def _settings(self, body: dict) -> dict:
        for k in ("api_key", "feishu_app_id", "feishu_app_secret"):
            if k in body:
                S.cfg[k] = (body.get(k) or "").strip()
        save_config(S.cfg)
        return {"ok": True, "masked": mask_key(S.cfg.get("api_key", ""))}

    def _test(self, body: dict) -> dict:
        key = (body.get("api_key") or S.cfg.get("api_key") or "").strip()
        if not key:
            return {"ok": False, "error": "请先填写 API Key"}
        try:
            m = Mcp(S.cfg.get("mcp_url") or DEFAULT_URL, key)
            m.initialize(force=True)
            info = m.body("get_api_fb_user_info", {}) if _has_tool(m, "get_api_fb_user_info") else {}
            groups = m.groups()
            return {"ok": True, "groups": len(groups),
                    "user": (info.get("data") or {}).get("nickname") or "",
                    "masked": mask_key(key)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _refresh(self) -> dict:
        with S.lock:
            if S.job["state"] == "running":
                return {"ok": True, "state": "running"}
            S.job = {"state": "running", "log": [], "error": "", "started": time.time()}

        def run():
            def log(msg):
                S.job["log"].append(str(msg))
                del S.job["log"][:-400]
            try:
                ctx = S.refresh(log=log)
                S.dump_cache()
                S.job["state"] = "done"
                S.job["summary"] = _summary(ctx)
                log("数据拉取完成")
            except Exception as e:
                traceback.print_exc()
                S.job["state"] = "error"
                S.job["error"] = "%s: %s" % (type(e).__name__, e)
                S.job["log"].append("失败：" + S.job["error"])

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True, "state": "running"}

    def _status(self) -> dict:
        out = dict(S.job)
        out["has_ctx"] = bool(S.ctx)
        out["ctx_age"] = int(time.time() - S.ctx_at) if S.ctx_at else None
        out["cfg"] = {"masked": mask_key(S.cfg.get("api_key", "")),
                      "feishu": bool(S.cfg.get("feishu_app_id") and S.cfg.get("feishu_app_secret")),
                      "mcp_url": S.cfg.get("mcp_url") or DEFAULT_URL}
        if S.ctx:
            out["summary"] = _summary(S.ctx)
        return out

    def _context_payload(self) -> dict:
        if not S.ctx and not S.load_cache():
            return {"ok": False, "error": "还没有数据，请先拉取"}
        return {"ok": True, "summary": _summary(S.ctx), "log": S.job.get("log", [])[-80:]}

    def _recommend(self, body: dict) -> dict:
        if not S.ctx and not S.load_cache(24 * 3600):
            return {"ok": False, "error": "请先拉取数据"}
        if not S.ctx.get("_mcp"):
            S.ctx["_mcp"] = S.mcp()
        per = int(body.get("per_group") or reco.MAX_PER_GROUP)
        res = reco.recommend(S.ctx, want_per_group=per)
        return {"ok": True, **res}

    def _assign(self, body: dict) -> dict:
        names = body.get("names") or []
        if isinstance(names, str):
            names = [x for x in __import__("re").split(r"[\n,，、;；]+", names) if x.strip()]
        if not names:
            return {"ok": False, "error": "请填写至少一个剧名"}
        if not S.ctx and not S.load_cache(24 * 3600):
            return {"ok": False, "error": "请先拉取数据"}
        if not S.ctx.get("_mcp"):
            S.ctx["_mcp"] = S.mcp()
        return {"ok": True, **reco.assign(S.ctx, names[:30])}

    def _export(self, body: dict) -> dict:
        rows = body.get("rows") or []
        cols = body.get("cols") or ["name", "group", "pub", "type", "tags",
                                    "theater", "promoter", "reason", "priority"]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["剧目名称", "所属分组", "上线时间", "剧目类型", "题材类型",
                    "剧场", "推广", "推荐理由", "优先级"])
        for r in rows:
            w.writerow([_fmt(r.get(c)) for c in cols])
        return {"ok": True, "csv": buf.getvalue()}


def _fmt(v):
    if isinstance(v, list):
        return "、".join(map(str, v))
    return "" if v is None else str(v)


def _has_tool(m: Mcp, name: str) -> bool:
    try:
        return any(t.get("name") == name for t in m.tools())
    except Exception:
        return False


def mask_key(k: str) -> str:
    if not k:
        return ""
    return k[:6] + "…" + k[-4:] if len(k) > 12 else "已设置"


def _summary(ctx: dict) -> dict:
    groups = []
    for g in ctx.get("groups") or []:
        groups.append({"name": g.get("name"), "pages": g.get("pages"),
                       "have": g.get("have"), "eps": g.get("eps"),
                       "per_page": g.get("per_page")})
    return {"generated": ctx.get("generated"), "sources": ctx.get("sources"),
            "errors": ctx.get("errors"), "groups": groups}


# ---------------------------------------------------------------- 启动
def port_free(port: int) -> bool:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def open_browser(url: str):
    try:
        webbrowser.open(url)
    except Exception:
        pass


def serve(port: int, do_open: bool = True):
    for p in [port, port + 1, port + 2]:
        if port_free(p):
            port = p
            break
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d/" % port
    print("%s v%s 已启动：%s" % (APP_NAME, VERSION, url), flush=True)
    if do_open:
        threading.Timer(0.8, open_browser, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


def selftest() -> int:
    report = {"app": APP_NAME, "version": VERSION, "steps": [], "ok": False}
    try:
        m = S.mcp()
        if not S.cfg.get("api_key"):
            raise RuntimeError("未配置 API Key")
        m.initialize(force=True)
        groups = m.groups()
        report["steps"].append({"step": "connect", "ok": True, "groups": len(groups)})
        S.refresh(log=lambda s: report["steps"].append({"step": "pull", "msg": str(s)}))
        S.dump_cache()
        rep = reco.recommend(S.ctx)
        report["recommend_rows"] = len(rep["rows"])
        report["recommend_sample"] = [
            {k: r[k] for k in ("group", "name", "type", "priority")} for r in rep["rows"][:5]]
        asg = reco.assign(S.ctx, ["The Mafia's Forced Bride", "The Elf Queen's Cursed Crown"])
        report["assign_rows"] = len(asg["rows"])
        report["assign_sample"] = asg["rows"][:2]
        report["ok"] = report["recommend_rows"] > 0 and report["assign_rows"] == 2
    except Exception as e:
        report["error"] = "%s: %s" % (type(e).__name__, e)
        report["trace"] = traceback.format_exc()[-1500:]
    out = os.path.join(os.getcwd(), "selftest_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("selftest -> %s ok=%s" % (out, report["ok"]), flush=True)
    return 0 if report["ok"] else 1


def main():
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--version", action="store_true")
    a = ap.parse_args()
    if a.version:
        print(VERSION)
        return
    try:
        S.load_cache()
    except Exception:
        pass
    if a.selftest:
        sys.exit(selftest())
    port = a.port or int(S.cfg.get("port") or DEFAULT_PORT)
    if not port_free(port):
        # 已有一个实例在跑，直接打开页面
        open_browser("http://127.0.0.1:%d/" % port)
        return
    serve(port, do_open=not a.no_browser)


if __name__ == "__main__":
    main()
