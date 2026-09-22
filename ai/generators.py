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
PROMPT_VERSION = {"story": 2, "week": 2, "month": 3, "clusters": 3, "radar": 1}

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
    """進入主題地圖的標籤:達門檻、且**是主題**。

    非主題標籤會把分組灌水並污染(見 config.NON_TOPIC_TAGS 的說明),
    所以在餵給模型之前就濾掉;emit 算「未歸類」時也要用同一份清單,
    否則它們會從未歸類那一區冒回來。
    """
    return [t for t in c.ranking(config.CLUSTER_MIN_COUNT)
            if t.key not in config.NON_TOPIC_TAGS]


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


_SKELETON_CACHE: dict[str, str] = {}


def _week_skeleton(c: Corpus, w: str) -> str:
    """窗外那些週沒有 AI 週報,改用 Python 算好的統計當月報的 reduce 輸入。

    這是「月報不再硬依賴週報」的關鍵。原本 _month_input() 只吃已快取的週報
    payload,所以一旦停掉歷史週報,歷史月份會永遠卡在「等待成分週」、一篇都
    產不出來。骨架讓月報照樣有事實可寫,而且**零 AI 呼叫**。

    裡面每個數字都來自 corpus.period_stats(),維持「LLM 永不產生數字」的原則。
    以行程內的字典記憶結果:plan() 與 _month_input() 都會要同一批週,而一個
    行程裡 Corpus 不會變。
    """
    if w in _SKELETON_CACHE:
        return _SKELETON_CACHE[w]
    weeks = c.weeks()
    msgs = weeks.get(w) or []
    ks = sorted(weeks.keys())
    i = ks.index(w) if w in ks else 0
    st = c.period_stats(msgs, weeks[ks[i - 1]] if i > 0 else [])

    parts = [f"【{c.week_label(w)}】共 {len(msgs)} 則(本週為統計摘要,無逐則內容)"]
    if st["top_tags"]:
        parts.append("  熱門:" + " ".join(f"{d['tag']}×{d['count']}" for d in st["top_tags"][:10]))
    if st["new_tags"]:
        parts.append("  新出現:" + " ".join(d["tag"] for d in st["new_tags"][:8]))
    if st["heat_up"]:
        parts.append("  升溫:" + " ".join(f"{d['tag']} {d['prev']}→{d['cur']}" for d in st["heat_up"][:6]))
    if st["heat_down"]:
        parts.append("  降溫:" + " ".join(f"{d['tag']} {d['prev']}→{d['cur']}" for d in st["heat_down"][:6]))

    _SKELETON_CACHE[w] = "\n".join(parts)
    return _SKELETON_CACHE[w]


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
    # 只有最近 DIGEST_WEEK_WINDOW 週會「新增」AI 週報;更舊的靠月報涵蓋。
    week_window = set(weeks[-config.DIGEST_WEEK_WINDOW:])

    for w in weeks:
        # 窗外的週:已經有快取就繼續列舉(**否則 cache.prune() 會把它刪掉**,
        # 網站上既有的歷史週報會憑空消失);沒有就跳過,不再新增。
        if w not in week_window and not cache_mod.get(cache, f"week:{w}"):
            continue
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
            elif w in week_window:
                missing.append(w)          # 窗內、還沒產出 → 等它先做
            else:
                # 窗外:不會有 AI 週報,改用確定性骨架。雜湊取骨架內容,
                # 這樣該週的資料變動時,月報仍會正確地被判定過期。
                wk_hashes.append(cache_mod.unit_hash(["wkskel", w, _week_skeleton(c, w)]))
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
             if t.key not in config.NON_TOPIC_TAGS][:config.STORY_MAX_TAGS]
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
            new_tag_delta=u.meta.get("new_tag_delta", 0),
            prompt_version=PROMPT_VERSION[u.kind])
        # 成分週還沒產生的月份先延後,避免對著空資料硬做
        if u.kind == "month" and u.meta.get("missing_weeks"):
            u.stale, u.reason = False, f"等待 {len(u.meta['missing_weeks'])} 個成分週先產生"

    return units


def select(units: list[Unit], max_units: int) -> list[Unit]:
    """挑出這次要做的:過期者依優先序排,取前 max_units 個。溢出是正常且會自癒的。"""
    stale = [u for u in units if u.stale]
    stale.sort(key=lambda u: (u.priority, -u.meta.get("msg_count", 0), u.key))
    return stale[:max_units]


# ================================================================ 驗證 + 組裝
#
# 鐵則:LLM 交回來的 dict 只提供「文字」與一個列舉值;
# 所有 id / 日期 / 次數 / 標籤一律在這裡用語料重新填,或以白名單過濾。
# 因此模型幻覺出來的 id、標籤、數字沒有任何一條能抵達前端。

def _norm_tags(c: Corpus, raw, limit: int, exclude: str = "") -> list:
    """把模型寫的標籤正規化成語料裡的正式寫法;不存在的剔除。

    模型很自然會寫「記憶體」而不是「#記憶體」,而 vocabulary 的鍵含 #。
    不先補 # 就比對,會把**正確**的標籤全部當成幻覺剔除 ——
    白名單要擋的是編造,不是不同寫法。
    """
    out = []
    for t in (raw or []):
        if not isinstance(t, str):
            continue
        k = t.strip().lower()
        if not k:
            continue
        if not k.startswith("#"):
            k = "#" + k
        st = c.tags.get(k)
        if st is None or st.key == exclude or st.display in out:
            continue
        out.append(st.display)
        if len(out) >= limit:
            break
    return out


def _ids_filter(raw, allowed: set, limit: int = 4) -> list:
    out = []
    for x in (raw or []):
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if i in allowed and i not in out:
            out.append(i)
        if len(out) >= limit:
            break
    return out


def _story_payload(u: Unit, c: Corpus, ai: dict, covered: list,
                   sampled: bool, model):
    allowed = set(covered)
    dates = {c.by_id[i]["local_date"] for i in covered if i in c.by_id}
    st = c.tags[u.meta["tag_key"]]

    beats = []
    for b in (ai.get("beats") or []):
        ids = _ids_filter(b.get("msg_ids"), allowed)
        if not ids:
            continue                      # 沒有有效引用的 beat 一律丟棄
        d = str(b.get("date") or "")
        if d not in dates:                # 日期必須來自被引用的訊息
            d = min(c.by_id[i]["local_date"] for i in ids)
        beats.append({"date": d,
                      "title": _clip(b.get("title"), 40),
                      "detail": _clip(b.get("detail"), 160),
                      "msg_ids": ids})
    if not beats:
        return None                       # 結構性欄位空掉 → 整單拒絕
    beats.sort(key=lambda b: b["date"])

    state = ai.get("state")
    if state not in ("升溫", "降溫", "持平"):
        state = "持平"
    related = _norm_tags(c, ai.get("related_tags"), 6, exclude=u.meta["tag_key"])

    return {
        "id": u.key, "tag": st.display, "tag_key": st.key,
        "pv": PROMPT_VERSION["story"], "h": u.new_hash, "model": model, "generated_at": cache_mod.now_iso(),
        "msg_count": st.count, "first_date": st.first_date, "last_date": st.last_date,
        "sampled": sampled, "covered_ids": covered,
        "summary": _clip(ai.get("summary"), 80),
        "state": state, "beats": beats[:7],
        "outlook": _clip(ai.get("outlook"), 100),
        "related_tags": related,
    }


def _period_payload(u: Unit, c: Corpus, ai: dict, msgs: list, stats: dict,
                    label: str, model):
    allowed = {m["id"] for m in msgs}
    themes = []
    for t in (ai.get("themes") or []):
        themes.append({"title": _clip(t.get("title"), 30),
                       "detail": _clip(t.get("detail"), 160),
                       "tags": _norm_tags(c, t.get("tags"), 6),
                       "msg_ids": _ids_filter(t.get("msg_ids"), allowed, 6)})
    themes = [t for t in themes if t["title"]]
    events = []
    for e in (ai.get("key_events") or []):
        ids = _ids_filter(e.get("msg_ids"), allowed, 4)
        events.append({"date": str(e.get("date") or ""),
                       "title": _clip(e.get("title"), 50), "msg_ids": ids})
    events = [e for e in events if e["title"]]
    if not themes and not events:
        return None

    return {
        "id": u.key, "period": u.meta.get("iso_week") or u.meta.get("month"),
        "pv": PROMPT_VERSION[u.kind], "label": label, "h": u.new_hash, "model": model,
        "generated_at": cache_mod.now_iso(), "msg_count": len(msgs),
        "top_tags": stats.get("top_tags", []), "new_tags": stats.get("new_tags", []),
        "heat_up": stats.get("heat_up", []), "heat_down": stats.get("heat_down", []),
        "one_liner": _clip(ai.get("one_liner"), 70),
        "themes": themes[:6], "key_events": events[:8],
    }


def _clusters_payload(u: Unit, c: Corpus, ai: dict, model):
    # **必須正規化**:模型常寫「記憶體」而非「#記憶體」,若原樣存下,
    # emit._refresh_clusters 用含 # 的鍵查表會全部查無 → 整組被清空 → 地圖變 0 組,
    # 而且上游會回報「成功」(payload 結構合法),是會無聲炸掉的那種失敗。
    allowed = {t.key for t in cluster_tags(c)}
    groups = []
    for g in (ai.get("groups") or []):
        if not g.get("name"):
            continue
        names = _norm_tags(c, g.get("tags"), 999)
        names = [n for n in names if n.lower() in allowed]   # 非主題標籤不得混入
        if not names:
            continue
        groups.append({"name": _clip(g.get("name"), 20),
                       "blurb": _clip(g.get("blurb"), 80),
                       "tags": [{"tag": n, "count": 0} for n in names], "total": 0})
    if not groups:
        return None
    # 次數 / total / unclustered / edges 全部交給 emit.refresh_deterministic 填
    return {"id": u.key, "pv": PROMPT_VERSION["clusters"], "h": u.new_hash, "model": model,
            "generated_at": cache_mod.now_iso(),
            "groups": groups, "unclustered": [], "edges": [],
            "tag_threshold": config.CLUSTER_MIN_COUNT, "refreshed_at": None}


def _radar_payload(u: Unit, c: Corpus, ai: dict, model):
    table = u.meta["table"]
    why = ai.get("why") if isinstance(ai.get("why"), dict) else {}
    out = {"id": u.key, "pv": PROMPT_VERSION["radar"], "h": u.new_hash, "model": model,
           "generated_at": cache_mod.now_iso(),
           "as_of": table.get("as_of", ""), "window": table.get("window", {}),
           "note": _clip(ai.get("note"), 80) or None}
    for b in ("rising", "falling", "fresh"):
        rows = []
        for d in table.get(b, []):
            d = dict(d)
            w = why.get(d["tag_key"]) or why.get(d["tag"])
            d["why"] = _clip(w, 60) if isinstance(w, str) else None
            rows.append(d)
        out[b] = rows
    return out


# ================================================================ Offline stub
# 讓整條 validate -> cache -> emit 路徑在零網路下跑完,產出真的 insights.js
# 供 UI 開發與回歸測試使用。文字一律標 [示範],不會被誤認成真的分析。

def _stub(u: Unit, c: Corpus, covered: list, msgs: list) -> dict:
    if u.kind == "story":
        picks = covered[:: max(1, len(covered) // 4)][:4] or covered[:1]
        return {"summary": "[示範] " + u.meta["display"] + " 的現況摘要,共 "
                           + str(u.meta["msg_count"]) + " 則。",
                "state": "升溫",
                "beats": [{"date": c.by_id[i]["local_date"],
                           "title": "[示範] 事件 " + str(n + 1),
                           "detail": _clip(c.by_id[i].get("text", ""), 120),
                           "msg_ids": [i]}
                          for n, i in enumerate(picks)],
                "outlook": "[示範] 後續觀察方向。",
                "related_tags": []}
    if u.kind in ("week", "month"):
        ids = [m["id"] for m in msgs]
        return {"one_liner": "[示範] 本期共 " + str(len(msgs)) + " 則訊息。",
                "themes": [{"title": "[示範] 主軸一", "detail": "[示範] 說明文字。",
                            "tags": [], "msg_ids": ids[:3]}],
                "key_events": [{"date": msgs[0]["local_date"] if msgs else "",
                                "title": "[示範] 重點事件", "msg_ids": ids[:2]}]}
    if u.kind == "clusters":
        tops = cluster_tags(c)[:24]
        size = max(1, len(tops) // 4)
        return {"groups": [{"name": "[示範] 主題 " + str(i + 1),
                            "blurb": "[示範] 這組標籤的共同性。",
                            "tags": [t.display for t in tops[i * size:(i + 1) * size]]}
                           for i in range(4)]}
    return {"why": {d["tag_key"]: "[示範] 竄升原因推測。"
                    for d in u.meta["table"].get("rising", [])[:5]},
            "note": "[示範] 整體觀察。"}


# ================================================================ 生成入口

def generate_unit(u: Unit, c: Corpus, cache: dict, offline: bool = False):
    """產生一個單元並寫進快取。回傳模型標籤代表成功,None 代表失敗(由呼叫端計數)。

    失敗時**不會**動到既有 payload —— 壞掉的一天絕不劣化網站。
    """
    covered = []
    msgs = []
    stats = {}
    sampled = False
    label = "[示範資料]" if offline else None

    # ---- 組輸入
    if u.kind == "story":
        text, covered, sampled = build_story_input(c, u.meta["tag_key"])
    elif u.kind == "week":
        msgs = c.weeks()[u.meta["iso_week"]]
        text, covered, _ = build_period_input(c, msgs)
        stats = c.period_stats(msgs, _prev_period_msgs(c, u))
    elif u.kind == "month":
        msgs = c.months()[u.meta["month"]]
        text = _month_input(c, u, cache)
        covered = [m["id"] for m in msgs]
        stats = c.period_stats(msgs, _prev_period_msgs(c, u))
    elif u.kind == "clusters":
        text, _ = build_clusters_input(c)
    else:
        text = build_radar_input(c, u.meta["table"])

    # ---- 取得 AI 輸出
    if offline:
        ai = _stub(u, c, covered, msgs)
    else:
        from . import llm_task
        ai, label = llm_task.run(u, text)
        if ai is None:
            return None

    # ---- 驗證 + 組裝
    if u.kind == "story":
        p = _story_payload(u, c, ai, covered, sampled, label)
    elif u.kind in ("week", "month"):
        if u.kind == "week":
            lab = c.week_label(u.meta["iso_week"])
        else:
            lab = u.meta["month"][:4] + " 年 " + str(int(u.meta["month"][5:])) + " 月"
        p = _period_payload(u, c, ai, msgs, stats, lab, label)
    elif u.kind == "clusters":
        p = _clusters_payload(u, c, ai, label)
    else:
        p = _radar_payload(u, c, ai, label)

    if p is None:
        return None
    cache_mod.put(cache, u.key, h=u.new_hash, payload=p, model=label,
                  base_msg_count=u.meta.get("msg_count", 0),
                  prompt_version=PROMPT_VERSION[u.kind])
    return label


def _prev_period_msgs(c: Corpus, u: Unit) -> list:
    """前一期的訊息,用來算升溫/降溫。"""
    if u.kind == "week":
        ks = sorted(c.weeks().keys())
        i = ks.index(u.meta["iso_week"])
        return c.weeks()[ks[i - 1]] if i > 0 else []
    ks = sorted(c.months().keys())
    i = ks.index(u.meta["month"])
    return c.months()[ks[i - 1]] if i > 0 else []


def _month_input(c: Corpus, u: Unit, cache: dict) -> str:
    """月報 = 對各週做 reduce,不吃原文(一個月 ~110k 字元塞不進去)。

    每一週依序嘗試兩種來源:
      1. 有 AI 週報快取 → 用它的 one_liner / themes / key_events(原行為)
      2. 沒有(= 窗外的歷史週)→ 用 _week_skeleton() 的確定性統計摘要
    第 2 條讓歷史月份不必先產 52 份週報就能寫出月報。
    """
    blocks = []
    for w in u.meta["weeks"]:
        e = cache_mod.get(cache, "week:" + w)
        p = (e or {}).get("payload")
        if not p:
            blocks.append(_week_skeleton(c, w))
            continue
        lines = ["【" + p.get("label", w) + "】" + (p.get("one_liner") or "")]
        for t in p.get("themes", []):
            lines.append("  主軸:" + str(t.get("title")) + " —— " + str(t.get("detail")))
        for ev in p.get("key_events", []):
            lines.append("  事件:" + str(ev.get("date")) + " " + str(ev.get("title")))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
