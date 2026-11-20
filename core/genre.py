# -*- coding: utf-8 -*-
"""题材 / 类型判定。

类型（类型判定页口径）：
  一周内上线 -> 新剧；推广人数>1000 -> 热门；平台标爆款(sort_tag=recent_hot) -> 爆款；其余 -> 老剧
  判定顺序：爆款 > 新剧 > 热门 > 老剧

题材（11 选，可多选）：
  ABO/狼人、校园/体育、黑帮/犯罪、豪门/总裁、人外/奇幻、娱乐圈/竞技、
  穿越/系统/重生、现实向、女频、男频、男同
"""
from __future__ import annotations

import json
import os
import re
import sys

TAGS = ("ABO/狼人", "校园/体育", "黑帮/犯罪", "豪门/总裁", "人外/奇幻", "娱乐圈/竞技",
        "穿越/系统/重生", "现实向", "女频", "男频", "男同")

THEATERS = [("YourChannel", "yourchannel_drama"), ("ReelShort", "reelshort"),
            ("NetShort", "netshort"), ("DramaBox", "dramabox"),
            ("StardustTV", "stardusttv"), ("Playlet", "playlet"),
            ("KalosTV", "kalostv"), ("MoboReels", "moboreels")]
APP2NAME = {a: n for n, a in THEATERS}
NAME2APP = {n: a for n, a in THEATERS}


def norm(s: str) -> str:
    s = s or ""
    for a, b in (("\u2019", "'"), ("\u2018", "'"), ("\u2011", "-"), ("\u2010", "-"),
                 ("\uff1a", ": "), ("\uff08", "("), ("\uff09", ")"), ("\u2026", "...")):
        s = s.replace(a, b)
    return s.strip().lower()


def n3(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", norm(s))


def base_dir() -> str:
    """PyInstaller 下资源在 sys._MEIPASS。"""
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load(name: str) -> dict:
    p = os.path.join(base_dir(), "assets", name)
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


_GMAP = {n3(k): v for k, v in _load("genre_map.json").items()}
_WMAP = {n3(k): v for k, v in _load("worldview_map.json").items()}


def classify_type(pub: str, promoter: int | None, sort_tag: str) -> tuple[str, str]:
    """返回 (类型, 依据)。"""
    today = __import__("datetime").date.today()
    week_ago = (today - __import__("datetime").timedelta(days=6)).isoformat()
    if sort_tag == "recent_hot":
        return "爆款", "平台标 recent_hot"
    if pub and len(pub) >= 10 and pub[:10] >= week_ago:
        return "新剧", "上线 %s（一周内）" % pub[:10]
    if (promoter or 0) > 1000:
        return "热门", "推广 %d（>1000）" % promoter
    return "老剧", ("推广 %s" % promoter) if promoter else "推广低/无数据"


def genre_of(name: str, fallback_tags: str = "", summary: str = "") -> list:
    k = n3(name)
    tags = []
    if k in _WMAP:
        tags += [t for t in _WMAP[k] if t in TAGS]
    if k in _GMAP and _GMAP[k] in TAGS:
        tags.append(_GMAP[k])
    blob = norm(name) + " " + norm(fallback_tags) + " " + norm(summary)
    kw = []
    if re.search(r"wolf|lycan|alpha|omega|luna|mate|moon|beast|savage|pack", blob):
        kw.append("ABO/狼人")
    if re.search(r"dragon|witch|wizard|fairy|elf|vampire|magic|demon|god\b|immortal|"
                 r"cultivat|mermaid|serpent|medusa|phoenix|alien|titan|elf|curse|"
                 r"kingdom|realm|soul|spirit|guardian", blob):
        kw.append("人外/奇幻")
    if re.search(r"mafia|\bdon\b|cartel|crime|mob\b|throne|godfather|gang", blob):
        kw.append("黑帮/犯罪")
    if re.search(r"billionaire|ceo|tycoon|heir|heiress|tyrant|queen|princess|prince|"
                 r"royal|bride|wife|husband|empire|mansion|fortune|rich", blob):
        kw.append("豪门/总裁")
    if re.search(r"school|campus|college|classroom|bully|cheer|athlete|football|"
                 r"basketball|hockey|coach", blob):
        kw.append("校园/体育")
    if re.search(r"star\b|idol|celebrity|hollywood|actress|actor|singer|band\b", blob):
        kw.append("娱乐圈/竞技")
    if re.search(r"reborn|rebirth|transmigr|system\b|time.?travel|second chance|"
                 r"return(s|ed)? to (the )?past", blob):
        kw.append("穿越/系统/重生")
    if re.search(r"family|daughter|son\b|mother|father|neighbor|wedding|housewife|"
                 r"poverty|poor|village|street|home\b|divorce|pregnan|lodge|cabin|"
                 r"farm|ranch|land\b|homestead|neighbor", blob):
        kw.append("现实向")
    for t in kw:
        if t not in tags:
            tags.append(t)
    if not tags:
        tags = ["女频"]
    if not any(t in ("女频", "男频", "男同") for t in tags):
        tags.insert(0, "女频")
    return tags[:4]


# 题材 -> 默认发布组（男同/男频无专属组，按归组口径走养号）
GENRE2GROUP = {
    "人外/奇幻": "人外/奇幻",
    "现实向": "现实向",
    "ABO/狼人": "女频",
    "豪门/总裁": "女频",
    "黑帮/犯罪": "女频",
    "校园/体育": "女频",
    "娱乐圈/竞技": "女频",
    "穿越/系统/重生": "女频",
    "女频": "女频",
    "男同": "养号",
    "男频": "养号",
}


def suggest_group(tags: list, name: str = "") -> str:
    k = n3(name)
    if k in _GMAP and _GMAP[k] in ("男同", "男频"):
        return GENRE2GROUP[_GMAP[k]]
    for t in tags:
        if t in GENRE2GROUP:
            return GENRE2GROUP[t]
    return "女频"
