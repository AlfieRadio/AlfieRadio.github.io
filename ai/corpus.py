"""語料載入與**所有確定性計算**。

這個模組是整個 AI 洞察功能裡「數字的唯一真相來源」。
LLM 永遠不產生任何次數、日期、標籤或訊息 id —— 全部由這裡算出來,
再由 generators 併回模型寫的自由文字。這讓「幻覺出錯誤數字」在結構上不可能發生。

標籤計數語意**必須**與前端 docs/app.js 的 computeRanking() 完全一致:
  - key = tag.lower(),**保留 # 前綴**
  - 同一則訊息內重複的標籤只算一次
  - display 取「最早出現的那則訊息」裡的原始大小寫(MSGS 依 id 升序 → 確定性)
任何漂移都會讓洞察頁的數字與頁籤②打架,所以 make_insights.py --dry-run 內建對帳斷言。
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MESSAGES_JSON = os.path.join(BASE, "data", "messages.json")
DATA_JS = os.path.join(BASE, "docs", "data.js")


# ---------------------------------------------------------------- 載入

def _load_raw() -> dict:
    """優先讀本機完整原始檔;沒有就退回解析已發布的 docs/data.js。

    兩者內容一致,但 data/messages.json 是 gitignored 的本機檔,
    換機器 / 清掉 data/ 之後只剩 data.js —— 那時仍要能跑。
    """
    if os.path.exists(MESSAGES_JSON):
        with open(MESSAGES_JSON, encoding="utf-8") as f:
            return json.load(f)

    if not os.path.exists(DATA_JS):
        raise FileNotFoundError(
            "找不到 data/messages.json 也找不到 docs/data.js —— 請先跑 python fetch.py"
        )
    with open(DATA_JS, encoding="utf-8") as f:
        raw = f.read()
    # 格式固定為:window.TG_DATA = {...};\n
    start = raw.index("{")
    end = raw.rstrip().rstrip(";").rindex("}") + 1
    return json.loads(raw[start:end])


def _parse_date(s: str) -> date:
    y, m, d = (int(x) for x in s.split("-"))
    return date(y, m, d)


# ---------------------------------------------------------------- 資料結構

@dataclass
class TagStat:
    key: str            # 小寫、含 #
    display: str        # 最早出現時的原始大小寫
    count: int          # 涵蓋的不重複訊息數
    first_date: str
    last_date: str
    msg_ids: list[int] = field(default_factory=list)   # 升序


@dataclass
class Corpus:
    channel: dict
    fetched_at: str
    tz: str
    messages: list[dict]                 # 依 id 升序

    by_id: dict[int, dict] = field(default_factory=dict, repr=False)
    tags: dict[str, TagStat] = field(default_factory=dict, repr=False)
    _weeks: dict[str, list[dict]] = field(default_factory=dict, repr=False)
    _months: dict[str, list[dict]] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------ 基本

    @property
    def data_through(self) -> str:
        """語料涵蓋到哪一天(最大 local_date)。所有時間窗都以此為界,而非今天,
        以免資料落後時雷達窗口整個落在無資料區。"""
        return max(m["local_date"] for m in self.messages) if self.messages else ""

    @property
    def message_count(self) -> int:
        return len(self.messages)

    def vocabulary(self) -> set[str]:
        """所有存在的標籤 key(小寫含 #)。驗證器用來剔除模型發明的標籤。"""
        return set(self.tags.keys())

    # ------------------------------------------------------------ 排行

    def ranking(self, min_count: int = 1) -> list[TagStat]:
        """鏡像 app.js::computeRanking()。

        排序:次數多者優先,同次數以 display 排序。
        注意 JS 端 tie-break 用 localeCompare(中文規則),Python 無法逐字重現,
        因此對帳斷言只鎖「標籤→次數」的對應,不鎖名次順序。
        """
        out = [t for t in self.tags.values() if t.count >= min_count]
        out.sort(key=lambda t: (-t.count, t.display))
        return out

    def tag_timeline(self, tag_key: str) -> list[dict]:
        """某標籤的所有訊息,依 id 升序(= 時序)。"""
        st = self.tags.get(tag_key)
        if not st:
            return []
        return [self.by_id[i] for i in st.msg_ids]

    # ------------------------------------------------------------ 分期

    def weeks(self) -> dict[str, list[dict]]:
        """iso_week -> 訊息(升序)。語意對齊 app.js::groupByWeek。"""
        return self._weeks

    def months(self) -> dict[str, list[dict]]:
        """YYYY-MM -> 訊息(升序)。"""
        return self._months

    def week_label(self, iso_week: str) -> str:
        """沿用資料自帶的 week_range 欄位(如 9/15–9/21),不自行推算。"""
        msgs = self._weeks.get(iso_week) or []
        return msgs[0].get("week_range", iso_week) if msgs else iso_week

    def weeks_of_month(self, month: str) -> list[str]:
        """某月涵蓋到的 iso_week 清單(升序)。月報是對這些週報做 reduce。"""
        seen: list[str] = []
        for m in self._months.get(month, []):
            w = m["iso_week"]
            if w not in seen:
                seen.append(w)
        return sorted(seen)

    # ------------------------------------------------------------ 期間統計

    def period_tag_counts(self, msgs: list[dict]) -> Counter:
        """一組訊息的標籤次數(每則去重),語意與 ranking 一致。"""
        c: Counter = Counter()
        for m in msgs:
            for k in _msg_tag_keys(m):
                c[k] += 1
        return c

    def period_stats(self, msgs: list[dict], prev_msgs: list[dict]) -> dict:
        """給週/月報用的確定性表格:熱門、新出現、升溫、降溫。

        全部標明「已算好,請勿重算」餵給模型,模型只寫文字。
        """
        cur = self.period_tag_counts(msgs)
        prev = self.period_tag_counts(prev_msgs)
        period_start = min((m["local_date"] for m in msgs), default="")

        top = [{"tag": self.tags[k].display, "count": n}
               for k, n in cur.most_common(15) if k in self.tags]

        # 新出現 = 該標籤在全語料的首見日落在本期間內
        new = [{"tag": self.tags[k].display, "count": n}
               for k, n in cur.most_common()
               if k in self.tags and self.tags[k].first_date >= period_start][:10]

        up, down = [], []
        for k, n in cur.items():
            p = prev.get(k, 0)
            if n - p >= 2 and n >= 3 and (p == 0 or n / p >= 1.8):
                up.append({"tag": self.tags[k].display, "cur": n, "prev": p})
        for k, p in prev.items():
            n = cur.get(k, 0)
            if p >= 4 and n <= p * 0.4:
                down.append({"tag": self.tags[k].display, "cur": n, "prev": p})
        up.sort(key=lambda d: -(d["cur"] - d["prev"]))
        down.sort(key=lambda d: d["cur"] - d["prev"])

        return {"top_tags": top, "new_tags": new,
                "heat_up": up[:8], "heat_down": down[:8]}

    # ------------------------------------------------------------ 共現

    def cooccurrence(self, min_w: int = 3, top: int = 150) -> list[dict]:
        """同一則訊息內共同出現的標籤配對(對稱、去重)。餵給聚類產生器。"""
        pair: Counter = Counter()
        for m in self.messages:
            keys = sorted(_msg_tag_keys(m))
            for i in range(len(keys)):
                for j in range(i + 1, len(keys)):
                    pair[(keys[i], keys[j])] += 1
        out = [{"a": self.tags[a].display, "b": self.tags[b].display, "w": w}
               for (a, b), w in pair.most_common()
               if w >= min_w and a in self.tags and b in self.tags]
        return out[:top]

    # ------------------------------------------------------------ 趨勢

    def trend_table(self, as_of: str | None = None) -> dict:
        """滾動 7 日 vs 前 7 日。**所有算術在這裡完成,LLM 只補「為什麼」。**

        刻意不用 ISO 週:用 ISO 週的話,週一早上的雷達只有一天資料、幾乎全空。
        """
        as_of = as_of or self.data_through
        if not as_of:
            return {"as_of": "", "window": {}, "rising": [], "falling": [], "fresh": []}

        end = _parse_date(as_of)
        cur_lo, prev_hi = end - timedelta(days=6), end - timedelta(days=7)
        prev_lo = end - timedelta(days=13)
        s = lambda d: d.isoformat()  # noqa: E731

        cur_msgs = [m for m in self.messages if s(cur_lo) <= m["local_date"] <= as_of]
        prev_msgs = [m for m in self.messages if s(prev_lo) <= m["local_date"] <= s(prev_hi)]
        cur = self.period_tag_counts(cur_msgs)
        prev = self.period_tag_counts(prev_msgs)

        def recent_ids(k: str, n: int = 5) -> list[int]:
            return [m["id"] for m in cur_msgs if k in _msg_tag_keys(m)][-n:]

        rising = []
        for k, n in cur.items():
            p = prev.get(k, 0)
            ratio = (n / p) if p else float(n)
            if n >= 3 and n - p >= 2 and ratio >= 1.8:
                rising.append({"tag": self.tags[k].display, "tag_key": k, "cur": n, "prev": p,
                               "delta": n - p, "ratio": round(ratio, 2),
                               "recent_ids": recent_ids(k)})
        rising.sort(key=lambda d: -d["delta"])

        falling = []
        for k, p in prev.items():
            n = cur.get(k, 0)
            if p >= 4 and n <= p * 0.4:
                falling.append({"tag": self.tags[k].display, "tag_key": k, "cur": n, "prev": p,
                                "delta": n - p, "ratio": round((n / p) if p else 0.0, 2),
                                "recent_ids": recent_ids(k)})
        falling.sort(key=lambda d: d["delta"])

        fresh_lo = s(end - timedelta(days=13))
        fresh = [{"tag": t.display, "tag_key": t.key, "first_date": t.first_date,
                  "count": t.count, "recent_ids": t.msg_ids[-5:]}
                 for t in self.tags.values()
                 if t.first_date >= fresh_lo and t.count >= 2]
        fresh.sort(key=lambda d: (d["first_date"], -d["count"]), reverse=True)

        return {
            "as_of": as_of,
            "window": {"cur": f"{s(cur_lo)}..{as_of}", "prev": f"{s(prev_lo)}..{s(prev_hi)}"},
            "rising": rising[:12],
            "falling": falling[:8],
            "fresh": fresh[:10],
        }


# ---------------------------------------------------------------- 內部

def _msg_tag_keys(m: dict) -> set[str]:
    """一則訊息的標籤 key 集合(自動去重),對齊 app.js 的 seen Set。"""
    return {t.lower() for t in (m.get("hashtags") or [])}


def load() -> Corpus:
    raw = _load_raw()
    messages = sorted(raw.get("messages", []), key=lambda m: m["id"])

    c = Corpus(
        channel=raw.get("channel", {}),
        fetched_at=raw.get("fetched_at", ""),
        tz=raw.get("tz", "Asia/Taipei"),
        messages=messages,
    )
    c.by_id = {m["id"]: m for m in messages}

    # 依 id 升序掃一次即可同時建立 display(最早出現者)、首末日、id 清單
    tags: dict[str, TagStat] = {}
    for m in messages:
        seen: set[str] = set()
        for t in (m.get("hashtags") or []):
            k = t.lower()
            if k in seen:
                continue
            seen.add(k)
            st = tags.get(k)
            if st is None:
                st = TagStat(key=k, display=t, count=0,
                             first_date=m["local_date"], last_date=m["local_date"])
                tags[k] = st
            st.count += 1
            st.last_date = m["local_date"]
            st.msg_ids.append(m["id"])
    c.tags = tags

    weeks: dict[str, list[dict]] = defaultdict(list)
    months: dict[str, list[dict]] = defaultdict(list)
    for m in messages:
        weeks[m["iso_week"]].append(m)
        months[m["local_date"][:7]].append(m)
    c._weeks = dict(weeks)
    c._months = dict(months)
    return c
