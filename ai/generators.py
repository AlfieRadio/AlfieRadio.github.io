"""四個洞察產生器:敘事線、週/月報、主題聚類、趨勢雷達。

四者放同一個模組:各約百行、共用 Unit 結構與驗證慣用法,
拆成四個檔案只會把一個概念攤到四個畫面。

本檔分成三層:
  1. 輸入組裝(純函式,無網路)—— 也被 --dry-run 用來估算 token
  2. 規劃(算雜湊、問快取要不要重生)
  3. 生成(prompt + LLM + 驗證)   ← 於後續步驟加入

**PROMPT_VERSION 是「這個 kind 全部重生」的開關**:改了 prompt 就 bump,
雜湊自然全部改變,下次執行會逐批重做(受 AI_MAX_UNITS 節流保護)。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import cache as cache_mod
from . import config
from .corpus import Corpus

SCHEMA_VERSION = 1
PROMPT_VERSION = {"story": 1, "week": 1, "month": 1, "clusters": 1, "radar": 1}

# 規劃時的優先序 —— **廣度優先**:先讓四個子頁都有東西,再把敘事線做滿。
# 關鍵是 week_of_month:月報是對週報做 reduce,不先把本月的組成週做出來,
# 月報子頁會空好幾晚。clusters 只要 ~5k 字元卻是獨立子頁,不該排在 70 條敘事線後面。
PRIORITY = {
    "radar": 0,            # 最便宜,且 AI 全掛時仍 100% 正確
    "week_current": 1,     # 本週
    "week_of_month": 2,    # 本月的其餘組成週 → 解鎖本月月報
    "clusters": 3,         # 單一單元、不吃原文、獨立子頁
    "month_current": 4,    # 前置週齊了就做
    "story": 5,            # 頭牌功能,但最貴
    "week_old": 6,         # 歷史回補
    "month_old": 7,
}


@dataclass
class Unit:
    key: str                 # 快取鍵,如 story:#台積電 / week:2026-W38
    kind: str                # story | week | month | clusters | radar
    new_hash: str
    priority: int
    est_chars: int = 0
    stale: bool = False
    reason: str = ""
    meta: dict = field(default_factory=dict)


# ================================================================ 輸入組裝

def _clip(s: str, n: int) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"


def _msg_line(m: dict, text_n: int, title_n: int, with_tags: bool = False) -> str:
    parts = [f"[{m['id']}] {m['local_date']}"]
    if with_tags and m.get("hashtags"):
        parts.append(" ".join(dict.fromkeys(m["hashtags"])))
    parts.append(_clip(m.get("text", ""), text_n))
    pv = m.get("preview")
    if pv and pv.get("title"):
        parts.append(f"| 連結:{_clip(pv['title'], title_n)}")
    return " ".join(p for p in parts if p)


def _stratified(lines: list[str], budget: int,
                head_n: int = 10, tail_n: int = 60) -> tuple[list[str], bool]:
    """超過預算時,保留「最舊 head_n(起源)+ 最新 tail_n(現況)+ 中段等距抽樣」。

    直接截斷會讓模型只看到開頭或結尾,寫出的敘事會缺頭或缺尾;
    分層抽樣保住兩端,中間用等距取樣維持時序密度感。
    """
    total = sum(len(x) + 1 for x in lines)
    if total <= budget:
        return lines, False
    if len(lines) <= head_n + tail_n:
        # 則數不多但單則很長 —— 只能從中間砍
        keep, used = [], 0
        for x in lines[:head_n]:
            keep.append(x); used += len(x) + 1
        for x in reversed(lines[head_n:]):
            if used + len(x) + 1 > budget:
                break
            keep.insert(head_n, x); used += len(x) + 1
        return keep, True

    head, tail = lines[:head_n], lines[-tail_n:]
    used = sum(len(x) + 1 for x in head + tail)
    mid = lines[head_n:-tail_n]
    left = budget - used
    picked: list[str] = []
    if left > 0 and mid:
        avg = max(1, sum(len(x) + 1 for x in mid) // len(mid))
        room = max(0, left // avg)
        if room:
            step = max(1, len(mid) // room)
            for x in mid[::step]:
                if left - (len(x) + 1) < 0:
                    break
                picked.append(x); left -= len(x) + 1
    return head + picked + tail, True


def build_story_input(c: Corpus, tag_key: str) -> tuple[str, list[int], bool]:
    """某標籤的完整時間線(必要時分層抽樣)。回傳 (文字, 模型實際看到的 id, 是否抽樣)。"""
    msgs = c.tag_timeline(tag_key)
    lines = [_msg_line(m, 180, 80) for m in msgs]
    kept, sampled = _stratified(lines, config.STORY_CHAR_BUDGET)
    kept_ids = [int(x[1:x.index("]")]) for x in kept]
    return "\n".join(kept), kept_ids, sampled


def build_period_input(c: Corpus, msgs: list[dict]) -> tuple[str, list[int], bool]:
    """週/月報的訊息區塊。"""
    lines = [_msg_line(m, 140, 60, with_tags=True) for m in msgs]
    kept, sampled = _stratified(lines, config.DIGEST_CHAR_BUDGET, head_n=20, tail_n=80)
    kept_ids = [int(x[1:x.index("]")]) for x in kept]
    return "\n".join(kept), kept_ids, sampled


def cluster_tags(c: Corpus) -> list:
    """進入聚類的標籤(達門檻者)。"""
    return c.ranking(config.CLUSTER_MIN_COUNT)


def build_clusters_input(c: Corpus) -> tuple[str, list[str]]:
    """**零訊息全文** —— 只有標籤詞彙與共現配對。全案 CP 值最高的單元。"""
    stats = cluster_tags(c)
    vocab = " ".join(f"{t.display}×{t.count}" for t in stats)
    edges = c.cooccurrence(min_w=3, top=150)
    edge_txt = "\n".join(f"{e['a']} + {e['b']} 同時出現 {e['w']} 次" for e in edges)
    text = f"=== 標籤清單(共 {len(stats)} 個)===\n{vocab}\n\n=== 常見共同出現配對 ===\n{edge_txt}"
    return text, [t.key for t in stats]


def build_radar_input(c: Corpus, table: dict) -> str:
    """確定性表格 + 每個上榜標籤的幾則近期訊息,供模型推測「為什麼」。"""
    blocks = []
    for bucket, label in (("rising", "竄升"), ("falling", "退燒"), ("fresh", "新出現")):
        for d in table.get(bucket, []):
            head = (f"{d['tag']}({label}:前 7 日 {d.get('prev', 0)} → 本 7 日 {d.get('cur', d.get('count', 0))})"
                    if bucket != "fresh" else
                    f"{d['tag']}(新出現:首見 {d['first_date']},共 {d['count']} 則)")
            samples = [f"  [{m['id']}] {m['local_date']} {_clip(m.get('text', ''), 120)}"
                       for m in (c.by_id[i] for i in d.get("recent_ids", []) if i in c.by_id)]
            blocks.append(head + "\n" + "\n".join(samples))
    return "\n\n".join(blocks)


# ================================================================ 規劃

def plan(c: Corpus, cache: dict) -> list[Unit]:
    """列出所有單元、算新雜湊、問快取要不要重生。純計算,無網路、無寫入。"""
    units: list[Unit] = []
    table = c.trend_table()
    rising_keys = {d["tag_key"] for d in table.get("rising", [])}

    # ---- 趨勢雷達:最便宜,且 AI 全掛時仍 100% 正確渲染
    rounded = [[d["tag_key"], d.get("cur", d.get("count", 0)), d.get("prev", 0)]
               for b in ("rising", "falling", "fresh") for d in table.get(b, [])]
    u = Unit(key="radar", kind="radar", priority=PRIORITY["radar"],
             new_hash=cache_mod.unit_hash([PROMPT_VERSION["radar"], SCHEMA_VERSION,
                                           table.get("as_of", ""), rounded]),
             meta={"table": table})
    u.est_chars = len(build_radar_input(c, table))
    units.append(u)

    # ---- 主題聚類:只看詞彙成員,次數跳動不該讓它失效
    ctext, cvocab = build_clusters_input(c)
    prev_entry = cache_mod.get(cache, "clusters")
    prev_vocab = set()
    if prev_entry and prev_entry.get("payload"):
        p = prev_entry["payload"]
        for g in p.get("groups", []):
            prev_vocab |= {t["tag"].lower() for t in g.get("tags", [])}
        prev_vocab |= {t["tag"].lower() for t in p.get("unclustered", [])}
    units.append(Unit(
        key="clusters", kind="clusters", priority=PRIORITY["clusters"],
        new_hash=cache_mod.unit_hash([PROMPT_VERSION["clusters"], SCHEMA_VERSION,
                                      sorted(cvocab)]),
        est_chars=len(ctext),
        meta={"vocab": cvocab, "new_tag_delta": len(set(cvocab) - prev_vocab)}))

    # ---- 週報:過去的週永遠不變 → 一生只產一次
    weeks = sorted(c.weeks().keys())
    months = sorted(c.months().keys())
    current_week = weeks[-1] if weeks else None
    current_month = months[-1] if months else None
    # 本月的組成週優先做 → 才解鎖本月月報
    month_weeks = set(c.weeks_of_month(current_month)) if current_month else set()

    for w in weeks:
        msgs = c.weeks()[w]
        ids = sorted(m["id"] for m in msgs)
        text, kept, _ = build_period_input(c, msgs)
        if w == current_week:
            pr = PRIORITY["week_current"]
        elif w in month_weeks:
            pr = PRIORITY["week_of_month"]
        else:
            pr = PRIORITY["week_old"]
        units.append(Unit(
            key=f"week:{w}", kind="week", priority=pr,
            new_hash=cache_mod.unit_hash([PROMPT_VERSION["week"], SCHEMA_VERSION, w, ids]),
            est_chars=len(text), meta={"iso_week": w, "msg_count": len(msgs)}))

    # ---- 月報:對「已快取的週報」做 reduce,不吃原文
    for mo in months:
        wk = c.weeks_of_month(mo)
        wk_hashes = []
        missing = []
        for w in wk:
            e = cache_mod.get(cache, f"week:{w}")
            if e and e.get("h"):
                wk_hashes.append(e["h"])
            else:
                missing.append(w)
        units.append(Unit(
            key=f"month:{mo}", kind="month",
            priority=PRIORITY["month_current"] if mo == current_month else PRIORITY["month_old"],
            new_hash=cache_mod.unit_hash([PROMPT_VERSION["month"], SCHEMA_VERSION, mo,
                                          wk_hashes, len(c.months()[mo])]),
            est_chars=6000,
            meta={"month": mo, "weeks": wk, "missing_weeks": missing,
                  "msg_count": len(c.months()[mo])}))

    # ---- 敘事線:最貴,且需要去抖動保護
    cands = [t for t in c.ranking(config.STORY_MIN_MSGS)
             if t.key not in config.STORY_SKIP_TAGS][:config.STORY_MAX_TAGS]
    for t in cands:
        text, kept, sampled = build_story_input(c, t.key)
        units.append(Unit(
            key=f"story:{t.key}", kind="story", priority=PRIORITY["story"],
            new_hash=cache_mod.unit_hash([PROMPT_VERSION["story"], SCHEMA_VERSION,
                                          t.key, sorted(t.msg_ids)]),
            est_chars=len(text),
            meta={"tag_key": t.key, "display": t.display, "msg_count": t.count,
                  "sampled": sampled, "covered": len(kept)}))

    # ---- 問快取
    for u in units:
        entry = cache_mod.get(cache, u.key)
        u.stale, u.reason = cache_mod.is_stale(
            u.kind, entry, u.new_hash,
            new_msg_count=u.meta.get("msg_count", 0),
            rising_keys=rising_keys, unit_key=u.key,
            new_tag_delta=u.meta.get("new_tag_delta", 0))
        # 成分週還沒產生的月份先延後,避免對著空資料硬做
        if u.kind == "month" and u.meta.get("missing_weeks"):
            u.stale, u.reason = False, f"等待 {len(u.meta['missing_weeks'])} 個成分週先產生"

    return units


def select(units: list[Unit], max_units: int) -> list[Unit]:
    """挑出這次要做的:過期者依優先序排,取前 max_units 個。溢出是正常且會自癒的。"""
    stale = [u for u in units if u.stale]
    stale.sort(key=lambda u: (u.priority, -u.meta.get("msg_count", 0), u.key))
    return stale[:max_units]
