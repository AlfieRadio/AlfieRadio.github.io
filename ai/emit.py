"""把快取組裝成 docs/insights.js。

兩個關鍵行為:
1. **refresh_deterministic** —— 每次執行都用最新語料重算聚類的次數/共現邊、
   以及雷達的整張表,完全不呼叫 AI。所以即使 AI 產出是兩週前的,
   畫面上的數字永遠是今天的。AI 只保留它寫的文字(name/blurb/why)。
2. **write_if_changed** —— 內容沒變就不碰檔案。這讓每小時排程不會製造空 commit。
"""
from __future__ import annotations

import json
import os

from . import cache as cache_mod
from . import config
from .corpus import Corpus

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(BASE, "docs", "insights.js")

SCHEMA = 1
STALE_AFTER_HOURS = 48


# ---------------------------------------------------------------- 確定性刷新

def refresh_deterministic(cache: dict, c: Corpus) -> None:
    """用最新語料覆寫所有「本來就該由程式算」的欄位。"""
    _refresh_clusters(cache, c)
    _refresh_radar(cache, c)


def _refresh_clusters(cache: dict, c: Corpus) -> None:
    e = cache_mod.get(cache, "clusters")
    if not e or not e.get("payload"):
        return
    p = e["payload"]
    counts = {t.key: t for t in c.ranking()}
    from .generators import cluster_tags   # 與餵給模型的清單必須一致
    qualifying = {t.key for t in cluster_tags(c)}

    claimed: set[str] = set()
    groups = []
    for g in p.get("groups", []):
        tags = []
        for item in g.get("tags", []):
            k = item["tag"].lower()
            st = counts.get(k)
            if st is None or k in claimed:      # 標籤消失、或已被前一組認領
                continue
            claimed.add(k)
            tags.append({"tag": st.display, "count": st.count})
        if not tags:
            continue
        # 所有排序都必須有確定性的 tie-break,否則同分項目每次執行順序不同,
        # 內容明明沒變卻被判定「有變」→ 每小時產生一個空 commit,永遠不停。
        tags.sort(key=lambda d: (-d["count"], d["tag"]))
        keys = {t["tag"].lower() for t in tags}
        total = sum(1 for m in c.messages
                    if keys & {x.lower() for x in (m.get("hashtags") or [])})
        groups.append({"name": g.get("name", ""), "blurb": g.get("blurb", ""),
                       "tags": tags, "total": total})
    groups.sort(key=lambda g: (-g["total"], g["name"]))

    # 未歸類 = 集合差集 → 即使模型偷懶漏掉一堆,地圖仍然窮盡。
    # 注意:來源是 set,迭代順序每個行程都不同 → tie-break 用 key 鎖死。
    rest = sorted((counts[k] for k in qualifying - claimed),
                  key=lambda t: (-t.count, t.key))
    p["groups"] = groups
    p["unclustered"] = [{"tag": t.display, "count": t.count} for t in rest]
    p["edges"] = c.cooccurrence(min_w=3, top=150)
    p["tag_threshold"] = config.CLUSTER_MIN_COUNT
    p["refreshed_at"] = cache_mod.now_iso()


def _refresh_radar(cache: dict, c: Corpus) -> None:
    e = cache_mod.get(cache, "radar")
    if not e or not e.get("payload"):
        return
    p = e["payload"]
    # 保留 AI 寫的「為什麼」,其餘整張表重算
    why = {}
    for b in ("rising", "falling", "fresh"):
        for d in p.get(b, []):
            if d.get("why"):
                why[d.get("tag_key") or d.get("tag", "").lower()] = d["why"]

    table = c.trend_table()
    for b in ("rising", "falling", "fresh"):
        rows = []
        for d in table.get(b, []):
            d = dict(d)
            d["why"] = why.get(d["tag_key"])
            rows.append(d)
        p[b] = rows
    p["as_of"] = table.get("as_of", "")
    p["window"] = table.get("window", {})


# ---------------------------------------------------------------- 組裝

def build_doc(cache: dict, c: Corpus) -> dict:
    entries = cache.get("entries", {})
    models: list[str] = []

    def payload(key: str) -> dict | None:
        e = entries.get(key)
        if not e or not e.get("payload"):
            return None
        if e.get("model") and e["model"] not in models:
            models.append(e["model"])
        return e["payload"]

    stories, weekly, monthly = [], [], []
    for key in entries:
        p = payload(key)
        if p is None:
            continue
        if key.startswith("story:"):
            stories.append(p)
        elif key.startswith("week:"):
            weekly.append(p)
        elif key.startswith("month:"):
            monthly.append(p)

    # 同樣必須有 tie-break(entries 的順序會隨快取重建而變)
    stories.sort(key=lambda d: (-d.get("msg_count", 0), d.get("id", "")))
    weekly.sort(key=lambda d: d.get("period", ""), reverse=True)
    monthly.sort(key=lambda d: d.get("period", ""), reverse=True)

    return {
        "schema": SCHEMA,
        "generated_at": cache_mod.now_iso(),
        "data_through": c.data_through,
        "source_message_count": c.message_count,
        "models": models,
        "stale_after_hours": STALE_AFTER_HOURS,
        "storylines": stories,
        "digests": {"weekly": weekly, "monthly": monthly},
        "clusters": payload("clusters"),
        "radar": payload("radar"),
    }


def _serialize(doc: dict) -> str:
    return "window.TG_INSIGHTS = " + json.dumps(doc, ensure_ascii=False) + ";\n"


def write_if_changed(cache: dict, c: Corpus) -> bool:
    """只有實質內容變了才寫檔,避免每小時的空 commit。

    比較時把 generated_at 歸零 —— 否則每次執行都「不一樣」,冪等性就沒了。
    """
    doc = build_doc(cache, c)
    new = _serialize(doc)

    if os.path.exists(OUT_PATH):
        try:
            with open(OUT_PATH, encoding="utf-8") as f:
                old = f.read()
            a = json.loads(old[old.index("{"):old.rstrip().rstrip(";").rindex("}") + 1])
            b = json.loads(new[new.index("{"):new.rstrip().rstrip(";").rindex("}") + 1])
            a.pop("generated_at", None)
            b.pop("generated_at", None)
            # refreshed_at 同理:確定性刷新本身不算「內容變了」
            for d in (a, b):
                if isinstance(d.get("clusters"), dict):
                    d["clusters"].pop("refreshed_at", None)
            if json.dumps(a, sort_keys=True, ensure_ascii=False) == \
               json.dumps(b, sort_keys=True, ensure_ascii=False):
                return False
        except Exception:  # noqa: BLE001
            pass   # 舊檔壞掉就直接覆寫

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(tmp, OUT_PATH)
    return True
