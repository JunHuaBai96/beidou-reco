# -*- coding: utf-8 -*-
"""推荐引擎：Mode A 按发布组推荐、Mode B 剧名定组表。"""
from __future__ import annotations

import datetime
import json
import re

from . import genre as G

STOCK_LOW = 150          # 单组剩余集数低于此视为紧张
PROMO_HOT = 1000         # 推广人数 > 1000 = 热门
MAX_PER_GROUP = 4        # 每组最多推荐条数
ISOLATED = ("停用",)      # 隔离区不出推荐

GROUP_ORDER_HINT = ["弃用组", "现实向", "女频", "潜力", "热门", "养号", "人外/奇幻", "新号组"]


def base_group(name: str) -> str:
    """白俊华|女频 -> 女频；弃用组 -> 弃用组。"""
    return (name or "").split("|")[-1].strip()


# ---------------------------------------------------------------- 上下文
def build_context(mcp, feishu=None, log=print) -> dict:
    ctx = {"generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "sources": {}, "errors": []}

    # 1) 发布组 + 组内主页数（group_list 的 page_count 不可靠，实时数）
    groups = mcp.groups()
    for g in groups:
        g["pages"] = mcp.page_count(g["id"])
    ctx["groups"] = groups
    ctx["sources"]["发布组"] = "%d 组" % len(groups)
    log("发布组：%s" % "、".join("%s(%d 主页)" % (g["name"], g["pages"]) for g in groups))

    # 2) 各素材库库存
    libs = {}
    for g in groups:
        cat = g.get("cat")
        if cat is None:
            continue
        rows = mcp.library(cat)
        have = [r for r in rows if (r.get("available_episodes") or 0) > 0]
        libs[str(cat)] = {
            "total": len(rows),
            "eps": sum(int(r.get("available_episodes") or 0) for r in have),
            "items": [{"name": r.get("serial_name") or r.get("title") or "",
                       "eps": int(r.get("available_episodes") or 0),
                       "total_eps": r.get("episode_count") or r.get("total_episodes") or 0}
                      for r in rows],
        }
    ctx["libs"] = libs
    for g in groups:
        lib = libs.get(str(g.get("cat"))) or {}
        g["have"] = len([x for x in lib.get("items", []) if x["eps"] > 0])
        g["eps"] = lib.get("eps", 0)
        g["per_page"] = round(g["eps"] / g["pages"], 1) if g["pages"] else 0
    ctx["sources"]["素材库"] = "、".join("%s %d集" % (g["name"], g["eps"]) for g in groups)

    # 3) 出单（按剧聚合，含所属组）
    orders = {}
    try:
        for r in mcp.orders():
            name = (r.get("serial_name") or "").strip()
            if not name:
                continue
            k = G.n3(name)
            o = orders.setdefault(k, {"name": name, "count": 0, "amount": 0.0,
                                      "groups": set(), "last": "", "app": ""})
            o["count"] += int(r.get("order_count") or 0)
            o["amount"] += float(r.get("sum_order_amount") or 0)
            if r.get("group_name"):
                o["groups"].add(r["group_name"])
            lt = (r.get("last_order_at") or "")[:10]
            if lt > o["last"]:
                o["last"] = lt
                o["app"] = r.get("app_id") or o["app"]
    except Exception as e:
        ctx["errors"].append("出单数据：%s" % e)
    for o in orders.values():
        o["groups"] = sorted(o["groups"])
    ctx["orders"] = orders
    ctx["sources"]["出单"] = "%d 部剧有订单" % len(orders)

    # 4) 剧场广场（YC 全量 + 其余头部 300）
    plaza = {}
    for en, app in G.THEATERS:
        try:
            plaza[app] = mcp.plaza(app, max_pages=(25 if app == "yourchannel_drama" else 3),
                                   order_field=None if app == "yourchannel_drama" else "total_income")
        except Exception as e:
            plaza[app] = []
            ctx["errors"].append("广场 %s：%s" % (en, e))
            log("广场 %s 失败：%s" % (en, e))
    ctx["plaza"] = plaza
    ctx["sources"]["剧目广场"] = "、".join("%s %d" % (G.APP2NAME.get(a, a), len(v))
                                     for a, v in plaza.items())
    log("广场：%s" % ctx["sources"]["剧目广场"])

    # 5) 看板 / 监控面板
    from . import sources as S
    try:
        ctx["dashboard"] = S.fetch_dashboard()
        ctx["sources"]["运营看板"] = "生成 %s" % ctx["dashboard"].get("generated")
    except Exception as e:
        ctx["dashboard"] = {}
        ctx["errors"].append("看板：%s" % e)
    try:
        ctx["monitor"] = S.fetch_monitor()
        ctx["sources"]["上新监控"] = "%d 平台" % len(ctx["monitor"])
    except Exception as e:
        ctx["monitor"] = {}
        ctx["errors"].append("监控面板：%s" % e)

    # 6) 飞书四表（可选）
    ctx["feishu"] = {}
    ctx["feishu_status"] = {"configured": False, "ok": False, "ready": [],
                            "blocked": [], "core_ok": 0, "core_total": 4,
                            "items": [], "error": ""}
    if feishu is not None and getattr(feishu, "enabled", False):
        try:
            ctx["feishu"] = feishu.all_tables()
            ctx["feishu_status"] = feishu.status_summary()
            fs = ctx["feishu_status"]
            ctx["sources"]["飞书四表"] = "可接入 %d/%d" % (fs["core_ok"], fs["core_total"])
            if fs.get("blocked"):
                ctx["errors"].append("飞书未接入：" + "、".join(fs["blocked"]))
                log("飞书未接入：%s" % "、".join(fs["blocked"]))
        except Exception as e:
            ctx["errors"].append("飞书：%s" % e)
            log("飞书拉取失败：%s" % e)
    else:
        ctx["sources"]["飞书四表"] = "未配置（跳过）"
    return ctx


# ---------------------------------------------------------------- 索引
def _plaza_index(plaza: dict) -> dict:
    idx = {}
    for app, rows in plaza.items():
        for x in rows:
            k = G.n3(x.get("title"))
            if not k:
                continue
            idx.setdefault(k, []).append({
                "title": x.get("title"), "app": app,
                "promoter": int(x.get("promoter_number") or 0),
                "pub": (x.get("publish_at") or "")[:10],
                "tag": x.get("sort_tag") or "",
                "ep": x.get("episode_count") or 0,
                "income": float(x.get("total_income") or 0),
            })
    return idx


def _best(rows: list, app: str | None = None) -> dict | None:
    if not rows:
        return None
    if app:
        same = [r for r in rows if r["app"] == app]
        if same:
            rows = same
    return max(rows, key=lambda r: (r["promoter"], r["income"]))


def _lib_index(ctx) -> dict:
    idx = {}
    by_cat = ctx.get("libs") or {}
    for g in ctx.get("groups") or []:
        lib = by_cat.get(str(g.get("cat"))) or {}
        for it in lib.get("items", []):
            idx.setdefault(G.n3(it["name"]), []).append(
                {"group": g["name"], "eps": it["eps"], "total": it["total_eps"]})
    return idx


# ---------------------------------------------------------------- Mode A
def recommend(ctx, want_per_group: int = MAX_PER_GROUP) -> dict:
    pidx = _plaza_index(ctx.get("plaza") or {})
    lidx = _lib_index(ctx)
    orders = ctx.get("orders") or {}
    fs = ctx.get("feishu") or {}
    dash = ctx.get("dashboard") or {}

    # 昨日出单（看板）与今日出单
    def dash_orders(day_key):
        d = dash.get(day_key) or {}
        out = {}
        for x in d.get("dramas") or []:
            k = G.n3(x.get("serial_name"))
            if k:
                out[k] = {"count": int(x.get("order_count") or 0),
                          "amount": float(x.get("sum_order_amount") or 0),
                          "group": x.get("group_name") or ""}
        return out

    y_orders = dash_orders("yesterday")
    t_orders = dash_orders("today_data")

    groups = [g for g in (ctx.get("groups") or []) if g["name"] not in ISOLATED]
    need = sorted(groups, key=lambda g: (g["per_page"] if g["pages"] else 999,
                                         -len(g["name"])))
    group_names = [g["name"] for g in groups]
    tight_by_name = {base_group(g["name"]): g["name"] for g in groups}

    cands = {}

    def add(name, src, rank=0, note="", theater="", app=None, promoter=None,
            pub="", tags_hint="", ep=None):
        name = (name or "").strip()
        if not name or len(name) < 3:
            return
        k = G.n3(name)
        c = cands.get(k)
        if c is None:
            c = cands[k] = {"name": name, "src": [], "rank": rank, "notes": [],
                            "theater": theater, "app": app, "promoter": promoter,
                            "pub": pub, "tags_hint": tags_hint, "ep": ep}
        else:
            if rank and (not c["rank"] or rank < c["rank"]):
                c["rank"] = rank
            if promoter and not c["promoter"]:
                c["promoter"] = promoter
            if pub and not c["pub"]:
                c["pub"] = pub
            if theater and not c["theater"]:
                c["theater"] = theater
            if app and not c["app"]:
                c["app"] = app
            if ep and not c["ep"]:
                c["ep"] = ep
        if src not in c["src"]:
            c["src"].append(src)
        if note and note not in c["notes"]:
            c["notes"].append(note)

    # 4.1 每日必做
    for r in fs.get("todo") or []:
        nm = r.get("剧名") or r.get("剧目") or ""
        add(nm, "必做推荐", 0, str(r.get("推荐理由") or ""),
            str(r.get("剧场") or ""), None, None,
            str(r.get("剧上架日期") or "")[:10])
    # 4.2 每日剧单
    for r in fs.get("daily") or []:
        nm = r.get("剧名（热门版单）") or r.get("剧名") or ""
        add(nm, "每日剧单", int(r.get("排名") or 0), str(r.get("备注") or ""),
            str(r.get("剧场") or ""), None, None, str(r.get("上线时间") or "")[:10])
    # 4.3 YC 一周新剧
    for r in fs.get("yc_new") or []:
        lang = str(r.get("语言") or "")
        if lang and "英文" not in lang and "英语" not in lang:
            continue
        add(r.get("剧名") or "", "YC新剧", 0, "",
            "YourChannel", "yourchannel_drama",
            int(r.get("推广达人数") or 0) or None, str(r.get("上线时间") or "")[:10],
            ep=r.get("集数"))
    # 4.4 分销剧单（有热度才收）
    for r in fs.get("dist") or []:
        heat = str(r.get("投放热度值") or "").replace(",", "")
        try:
            heat = int(float(heat))
        except Exception:
            heat = 0
        if heat <= 0:
            continue
        add(r.get("剧名") or "", "分销热度", 0,
            "热度%s｜%s" % (r.get("投放热度值"), r.get("短剧类型") or ""),
            "YourChannel", "yourchannel_drama", None,
            str(r.get("更新日期") or "")[:10], tags_hint=str(r.get("题材标签") or ""))

    # 4.5 监控面板新剧（近 7 天，补冷门剧场）
    mon = ctx.get("monitor") or {}
    for en, app in G.THEATERS:
        rows = mon.get(en) or []
        for it in rows:
            if it["date"] >= _week_ago():
                add(it["title"], "上新监控", 0, "", en, app, None, "20%s" % it["date"])

    # 4.6 看板昨日/今日出单剧补充
    for k, o in list(y_orders.items()) + list(t_orders.items()):
        row = next((r for r in dash.get("year_rank") or []
                    if G.n3(r.get("serial_name")) == k), None)
        if row:
            add(row.get("serial_name"), "看板出单", 0, "", row.get("app_name"), row.get("app_id"))

    # 4.7 已有出单的剧（MCP 订单，90 天）
    for k, o in orders.items():
        if o["count"] > 0:
            add(o["name"], "历史出单", 0, "", G.APP2NAME.get(o.get("app"), ""), o.get("app"))

    # 4.8 库内剧（用于「已出单缺素材」判断，不需要全部进候选）

    # ---- 补全元数据 ----
    stat = {"plaza_hit": 0, "plaza_miss": 0}
    for k, c in cands.items():
        rows = pidx.get(k)
        best = _best(rows, c["app"])
        if best:
            stat["plaza_hit"] += 1
            c["promoter"] = c["promoter"] or best["promoter"]
            c["pub"] = c["pub"] or best["pub"]
            c["tag"] = best["tag"]
            c["ep"] = c["ep"] or best["ep"]
            c["theater"] = c["theater"] or G.APP2NAME.get(best["app"], best["app"])
        else:
            stat["plaza_miss"] += 1
            c["tag"] = c.get("tag", "")
        c["orders"] = orders.get(k)
        c["y_order"] = y_orders.get(k)
        c["t_order"] = t_orders.get(k)
        c["stock"] = lidx.get(k) or []
        if (not c.get("ep") or int(c["ep"] or 0) < 5):
            totals = [int(x.get("total") or 0) for x in c["stock"]]
            if totals and max(totals) > 0:
                c["ep"] = max(totals)
        c["tags"] = G.genre_of(c["name"], c.get("tags_hint", ""))
        c["type"], c["type_why"] = G.classify_type(
            c["pub"], c["promoter"], c.get("tag", ""))
        c["natural_group"] = G.suggest_group(c["tags"], c["name"])
        c["score"] = _score(c)
        c["fits"] = _fits(c)

    # ---- 分组分配：先定每部剧最合适的组，再按紧张度轮流补 ----
    used = set()
    plan = []
    for c in cands.values():
        order = sorted(c["fits"].items(), key=lambda kv: -kv[1])
        c["best_group"] = order[0][0] if order and order[0][1] > 0 else c["natural_group"]

    picked_by = {}
    for g in need:                      # 第一轮：只吃「最合适就是它」的候选
        gb = base_group(g["name"])
        pool = [c for c in cands.values() if c["best_group"] == gb]
        pool.sort(key=lambda c: -c["score"])
        picked = []
        for c in pool:
            if G.n3(c["name"]) in used:
                continue
            if len(picked) >= want_per_group:
                break
            picked.append(c)
            used.add(G.n3(c["name"]))
        picked_by[g["name"]] = picked

    for g in need:                      # 第二轮：不够 2 部的组，用兼容候选补位
        gb = base_group(g["name"])
        picked = picked_by[g["name"]]
        if len(picked) >= min(2, want_per_group):
            continue
        rest = []
        for c in cands.values():
            if G.n3(c["name"]) in used or c["fits"].get(gb, 0) <= 0:
                continue
            owner = c["best_group"]
            # 不抢还没吃饱的原属组
            if owner in tight_by_name and len(picked_by.get(tight_by_name[owner], [])) < 2:
                continue
            rest.append(c)
        rest.sort(key=lambda c: (-c["fits"].get(gb, 0), -c["score"]))
        for c in rest:
            if len(picked) >= min(2, want_per_group):
                break
            c["moved"] = True
            picked.append(c)
            used.add(G.n3(c["name"]))

    for g in need:
        for c in picked_by[g["name"]]:
            plan.append((g, c))

    rows = []
    for g, c in plan:
        rows.append(_row(g, c, ctx))

    # 优先级/理由
    for r in rows:
        r["priority"] = _priority(r, ctx)
        r["reason"] = _reason(r, ctx)

    # 排序：组紧张度 -> 优先级 -> 分数
    pr = {"高": 0, "中": 1, "低": 2}
    tight = {g["name"]: (g["per_page"] if g["pages"] else 999) for g in groups}
    rows.sort(key=lambda r: (tight.get(r["group"], 999), pr[r["priority"]], -r["_score"]))

    summary = {
        "generated": ctx.get("generated"),
        "sources": ctx.get("sources"),
        "errors": ctx.get("errors"),
        "feishu": ctx.get("feishu_status"),
        "groups": [{"name": g["name"], "pages": g["pages"], "have": g["have"],
                    "eps": g["eps"], "per_page": g["per_page"]} for g in need],
        "stats": stat,
        "dash": {
            "generated": dash.get("generated"),
            "yesterday": _totals(dash.get("yesterday")),
            "today": _totals(dash.get("today_data")),
        },
    }
    return {"summary": summary, "rows": rows}


def _today_iso() -> str:
    return datetime.date.today().isoformat()


def _week_ago(days: int = 6) -> str:
    d = datetime.date.today() - datetime.timedelta(days=days)
    return d.strftime("%m-%d")


def _totals(day) -> dict:
    if not day:
        return {}
    t = day.get("totals") or {}
    return {"date": day.get("date"), "order_count": t.get("order_count"),
            "amount": round(float(t.get("sum_order_amount") or 0), 2),
            "dramas": len(day.get("dramas") or [])}


def _score(c) -> float:
    s = 0.0
    if c["rank"]:
        s += max(0, 12 - c["rank"])
    if c["notes"]:
        s += 2
    if c["promoter"] and c["promoter"] > PROMO_HOT:
        s += 4
    if c["type"] == "新剧":
        s += 3
    if c["type"] == "爆款":
        s += 5
    o = c.get("orders") or {}
    if o.get("count"):
        s += min(8, 2 + o["count"])
        if not any(x["eps"] > 0 for x in c.get("stock") or []):
            s += 4
    y = c.get("y_order") or {}
    if y.get("count"):
        s += min(6, y["count"] * 2)
    if any(x["eps"] > 0 for x in c.get("stock") or []):
        s -= 3
    if c.get("moved"):
        s -= 5
    return round(s, 1)


def _fits(c) -> dict:
    """每部剧对各发布组的适配度（0-6），0 表示不适配。

    口径：题材决定主组；爆款/过千进热门；验证过（出单/过千）进潜力；
    长剧(>=55集)进弃用组走量；男频/男同/系统/校园进养号。
    """
    t = set(c.get("tags") or [])
    ep = int(c.get("ep") or 0)
    pro = int(c.get("promoter") or 0)
    oc = int((c.get("orders") or {}).get("count") or 0)
    typ = c.get("type")
    female = any(x in t for x in ("女频", "豪门/总裁", "ABO/狼人", "黑帮/犯罪",
                                  "校园/体育", "娱乐圈/竞技", "穿越/系统/重生"))
    male = any(x in t for x in ("男频", "男同"))
    f = {}
    if "人外/奇幻" in t:
        f["人外/奇幻"] = 5
    if "现实向" in t:
        f["现实向"] = 5
    if female:
        f["女频"] = 6 if "豪门/总裁" in t else 5
    if typ == "爆款" or pro > PROMO_HOT:
        f["热门"] = 6 if typ == "爆款" else 5
    if oc >= 3:
        f["热门"] = max(f.get("热门", 0), 5)
    if pro > PROMO_HOT or oc > 0:
        f["潜力"] = 4 if female else 3
    elif female and 40 <= ep <= 80:
        f["潜力"] = 2
    if ep >= 55:
        f["弃用组"] = 4 if ep >= 60 else 3
    if male or "穿越/系统/重生" in t:
        f["养号"] = 5
    if "校园/体育" in t:
        f["养号"] = max(f.get("养号", 0), 4)
    return f


def _priority(r, ctx) -> str:
    c = r["_c"]
    has_stock = any(x["eps"] > 0 for x in c.get("stock") or [])
    o = (c.get("orders") or {}).get("count") or 0
    y = (c.get("y_order") or {}).get("count") or 0
    # 高：出单多且断料 / 昨日看板多单 / 剧单前 5 且没出过单
    if (not has_stock and o >= 10) or y >= 2 or (c["rank"] and c["rank"] <= 5 and not o):
        return "高"
    if (not has_stock and o > 0) or (c["promoter"] or 0) > PROMO_HOT or c["type"] in ("新剧", "爆款"):
        return "中"
    return "低"


def _row(g, c, ctx) -> dict:
    return {
        "group": g["name"], "group_pages": g["pages"], "group_eps": g["eps"],
        "name": c["name"], "pub": c["pub"] or "—",
        "type": c["type"], "type_why": c["type_why"],
        "tags": c["tags"], "theater": c["theater"] or "—",
        "promoter": c["promoter"] if c["promoter"] is not None else "—",
        "ep": c.get("ep") or "—", "src": c["src"],
        "_c": c, "_score": c["score"],
        "orders": (c.get("orders") or {}).get("count") or 0,
        "order_amount": round(float((c.get("orders") or {}).get("amount") or 0), 2),
        "order_groups": (c.get("orders") or {}).get("groups") or [],
        "y_order": (c.get("y_order") or {}).get("count") or 0,
    }


def _reason(r, ctx) -> str:
    c = r["_c"]
    parts = []
    if c.get("natural_group") == base_group(r["group"]):
        parts.append("归组 %s（题材对口）" % r["group"])
    elif c.get("moved"):
        parts.append("归组 %s（补该组缺口）" % r["group"])
    else:
        parts.append("归组 %s（体量/热度适配）" % r["group"])
    src = "、".join(r["src"][:3])
    head = "来源 %s" % src
    if c["rank"]:
        head += " #%d" % c["rank"]
    parts.append(head)
    meta = []
    if r["ep"] and r["ep"] != "—":
        meta.append("%s 集" % r["ep"])
    if r["promoter"] != "—":
        meta.append("推广 %s" % r["promoter"])
    if c["pub"]:
        meta.append("上线 %s" % c["pub"])
    if meta:
        parts.append("，".join(meta))
    if r["order_amount"] or r["orders"]:
        gs = "/".join(r["order_groups"][:2]) or "—"
        parts.append("已出单 %d 单 ¥%.2f（%s）" % (r["orders"], r["order_amount"], gs))
    if r["y_order"]:
        parts.append("昨日看板 %d 单" % r["y_order"])
    stock_g = [x for x in c.get("stock") or [] if x["eps"] > 0]
    if stock_g:
        parts.append("已有素材 %s" % "、".join("%s %d集" % (x["group"], x["eps"]) for x in stock_g[:2]))
    elif c.get("stock"):
        parts.append("在库但集数已用完")
    else:
        parts.append("无素材")
    if c["notes"]:
        parts.append("备注：%s" % "；".join(c["notes"])[:60])
    if c.get("tags_hint"):
        parts.append("分销标签：%s" % c["tags_hint"][:40])
    tight = next((g for g in (ctx.get("groups") or []) if g["name"] == r["group"]), None)
    if tight:
        parts.append("该组 %d 主页/%d 集（%s 集/页）" %
                     (tight["pages"], tight["eps"], tight["per_page"]))
    return "；".join(parts)


# ---------------------------------------------------------------- Mode B
def assign(ctx, names: list, log=print) -> dict:
    pidx = _plaza_index(ctx.get("plaza") or {})
    lidx = _lib_index(ctx)
    groups = [g for g in (ctx.get("groups") or []) if g["name"] not in ISOLATED]
    tight = {base_group(g["name"]): (g["per_page"], g["eps"], g["pages"]) for g in groups}
    rows, unresolved = [], []

    for raw in names:
        name = (raw or "").strip().strip("、,，;；")
        if not name:
            continue
        hits = pidx.get(G.n3(name))
        if not hits:
            # 逐个剧场搜索；只保留同名/高相似结果，避免串剧
            from difflib import SequenceMatcher
            qk = G.n3(name)
            for en, app in G.THEATERS:
                for q in (name, name.replace("\u2019", "'"), name[:30]):
                    try:
                        found = ctx["_mcp"].search_drama(q, app)
                    except Exception:
                        found = []
                    keep = []
                    for x in found or []:
                        tk = G.n3(x.get("title"))
                        if not tk:
                            continue
                        sim = SequenceMatcher(None, qk, tk).ratio()
                        if tk == qk or qk in tk or tk in qk or sim >= 0.86:
                            keep.append({"title": x.get("title"), "app": app,
                                         "promoter": int(x.get("promoter_number") or 0),
                                         "pub": (x.get("publish_at") or "")[:10],
                                         "tag": x.get("sort_tag") or "",
                                         "ep": x.get("episode_count") or 0,
                                         "income": 0, "exact": tk == qk, "sim": sim})
                    if keep:
                        exact = [k for k in keep if k["exact"]]
                        hits = exact or keep
                        break
                if hits:
                    break
            if hits:
                hits.sort(key=lambda h: (h.get("exact", False), h["sim"], h["promoter"]),
                          reverse=True)
        if not hits:
            unresolved.append(name)
            rows.append({"name": name, "pub": "待上架（全剧场无收录）",
                         "type": "未上架", "type_why": "8 剧场精确+模糊均无记录",
                         "tags": G.genre_of(name), "theater": "—", "promoter": "—",
                         "group": G.suggest_group(G.genre_of(name), name),
                         "priority": "暂缓", "reason": "待上架：四源表与广场均无收录，上架后按真实数据复核",
                         "ep": "—"})
            continue
        best = _best(hits)
        tags = G.genre_of(name)
        typ, why = G.classify_type(best["pub"], best["promoter"], best["tag"])
        if "人外/奇幻" in tags:
            grp = "人外/奇幻"
        elif "现实向" in tags:
            grp = "现实向"
        else:
            grp = "女频"
        stock = lidx.get(G.n3(name)) or []
        stock_txt = ("已有素材 " + "、".join("%s %d集" % (x["group"], x["eps"])
                                       for x in stock if x["eps"] > 0)) if any(
            x["eps"] > 0 for x in stock) else ("在库但集数用完" if stock else "无素材")
        tp, eps, pages = tight.get(grp, (999, 0, 0))
        oc = int(((ctx.get("orders") or {}).get(G.n3(name)) or {}).get("count") or 0)
        used_up = bool(stock) and not any(x["eps"] > 0 for x in stock)
        if (best["promoter"] > PROMO_HOT or typ == "爆款") or (used_up and oc > 0):
            pri = "高"
        elif tp < 15 or typ == "新剧" or oc > 0:
            pri = "中"
        else:
            pri = "低"
        note = ""
        if best["promoter"] == 0 and best["pub"] >= _today_iso():
            note = "（广场推广计数为 0，可能为今日重新上架，历史推广需回看原上架日）"
        reason = "%s：%s；%s 集；%s；该组 %d 主页/%d 集（%s 集/页）%s" % (
            typ, why, best["ep"] or "—", stock_txt, pages, eps, tp, note)
        rows.append({"name": best["title"] or name, "pub": best["pub"] or "—",
                     "type": typ, "type_why": why, "tags": tags,
                     "theater": G.APP2NAME.get(best["app"], best["app"]),
                     "promoter": best["promoter"], "group": grp,
                     "priority": pri, "reason": reason, "ep": best["ep"] or "—"})

    rows.sort(key=lambda r: ({"高": 0, "中": 1, "低": 2, "暂缓": 3}.get(r["priority"], 4),
                             tight.get(r["group"], (999,))[0] if isinstance(
                                 tight.get(r["group"]), tuple) else 999))
    return {"rows": rows, "unresolved": unresolved}
