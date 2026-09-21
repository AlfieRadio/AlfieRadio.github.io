"""內容雜湊快取、執行狀態與鎖。

設計重點:
1. **已 commit 的 docs/insights.js 本身就是耐久快取。** data/ 是 gitignored 的,
   換機器或清掉 data/ 之後快取就沒了 —— 此時直接從 insights.js 重建,零成本。
   這是為什麼 schema 裡每個單元都自帶 h / model / generated_at / payload。
2. **雜湊不符 != 立刻重生。** is_stale() 會套用每種 kind 各自的去抖動策略,
   否則像 #法人(514 則)這種高流量標籤會天天重燒一次昂貴的呼叫。
3. 鎖防止每小時排程在前一次還沒跑完時疊上去。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE, "data")
CACHE_PATH = os.path.join(DATA_DIR, "insights_cache.json")
LOCK_PATH = os.path.join(DATA_DIR, "insights.lock")
INSIGHTS_JS = os.path.join(BASE, "docs", "insights.js")

CACHE_VERSION = 1
LOCK_STALE_MINUTES = 60

TW = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(TW).isoformat(timespec="seconds")


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=TW)
    except ValueError:
        return None


def age_days(iso: str | None) -> float:
    d = _parse_iso(iso)
    return 1e9 if d is None else (datetime.now(TW) - d).total_seconds() / 86400.0


def hours_since(iso: str | None) -> float:
    d = _parse_iso(iso)
    return 1e9 if d is None else (datetime.now(TW) - d).total_seconds() / 3600.0


# ---------------------------------------------------------------- 雜湊

def unit_hash(parts: list) -> str:
    """parts 的開頭**必須**是 [PROMPT_VERSION, SCHEMA_VERSION] ——
    調 prompt 時 bump 版本號,就是「這個 kind 全部重生」的開關。"""
    import hashlib
    blob = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- 載入 / 儲存

def _empty() -> dict:
    return {"cache_version": CACHE_VERSION,
            "meta": {"last_ai_run_at": None, "last_full_at": None},
            "entries": {}}


def _rebuild_from_insights_js() -> dict:
    """從已發布的 docs/insights.js 反推快取。單元都自帶 h/model/generated_at。"""
    if not os.path.exists(INSIGHTS_JS):
        return _empty()
    try:
        with open(INSIGHTS_JS, encoding="utf-8") as f:
            raw = f.read()
        doc = json.loads(raw[raw.index("{"):raw.rstrip().rstrip(";").rindex("}") + 1])
    except Exception as exc:  # noqa: BLE001
        print(f"[cache] 無法從 insights.js 重建({exc}),以空快取開始")
        return _empty()

    c = _empty()
    def put(unit: dict) -> None:
        key = unit.get("id")
        if not key or not unit.get("h"):
            return
        c["entries"][key] = {
            "h": unit["h"], "model": unit.get("model"),
            "generated_at": unit.get("generated_at"),
            "prompt_version": unit.get("pv"),
            "base_msg_count": unit.get("msg_count", 0),
            "fail_count": 0, "last_fail_at": None,
            "payload": unit,
        }

    for u in doc.get("storylines") or []:
        put(u)
    for grp in ("weekly", "monthly"):
        for u in (doc.get("digests") or {}).get(grp) or []:
            put(u)
    for k in ("clusters", "radar"):
        if isinstance(doc.get(k), dict):
            put(doc[k])
    print(f"[cache] 已從 docs/insights.js 重建 {len(c['entries'])} 個單元")
    return c


def load() -> dict:
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                c = json.load(f)
            if c.get("cache_version") == CACHE_VERSION:
                c.setdefault("meta", {}).setdefault("last_ai_run_at", None)
                c.setdefault("entries", {})
                return c
            print("[cache] cache_version 不符,改從 insights.js 重建")
        except Exception as exc:  # noqa: BLE001
            print(f"[cache] 讀取失敗({exc}),改從 insights.js 重建")
    return _rebuild_from_insights_js()


def save(cache: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CACHE_PATH)


# ---------------------------------------------------------------- 去抖動

def is_stale(kind: str, entry: dict | None, new_hash: str, *,
             new_msg_count: int = 0, rising_keys: set[str] | None = None,
             unit_key: str = "", new_tag_delta: int = 0,
             prompt_version: int | None = None) -> tuple[bool, str]:
    """回傳 (要不要重生, 原因)。原因字串會出現在 --dry-run 表格裡。"""
    if entry is None:
        return True, "尚未產生"
    if entry.get("h") == new_hash:
        return False, "雜湊相同"

    # **版本不符凌駕去抖動。**
    # 去抖動是針對「內容微幅變動」的節流(例如高流量標籤每天多幾則就重燒很浪費),
    # 但它不該攔截「人為刻意改了 prompt」。兩者混在一起會讓 PROMPT_VERSION
    # 這個「全部重生」開關對有去抖動的 kind 完全失效。
    if prompt_version is not None:
        old_pv = entry.get("prompt_version")
        if old_pv != prompt_version:
            return True, f"prompt 版本 {old_pv} → {prompt_version}"

    # 連續失敗的單元先冷卻,避免每天都在同一個壞單元上浪費呼叫
    if entry.get("fail_count", 0) >= 3 and age_days(entry.get("last_fail_at")) < 7:
        return False, f"連續失敗 {entry['fail_count']} 次,冷卻中"

    age = age_days(entry.get("generated_at"))

    if kind == "story":
        delta = new_msg_count - entry.get("base_msg_count", 0)
        if rising_keys and unit_key.split(":", 1)[-1] in rising_keys:
            return True, "出現在今日竄升榜"
        if delta >= 3:
            return True, f"新增 {delta} 則"
        if age >= 14:
            return True, f"已 {age:.0f} 天未更新"
        return False, f"僅新增 {delta} 則,未達門檻"

    if kind == "clusters":
        if new_tag_delta >= 10:
            return True, f"新達標標籤 {new_tag_delta} 個"
        if age >= 14:
            return True, f"已 {age:.0f} 天未更新"
        return False, f"新達標標籤僅 {new_tag_delta} 個"

    # week / month / radar:雜湊變就重生(穩態下每天約 2–3 個,便宜)
    return True, "內容已變動"


# ---------------------------------------------------------------- 單元讀寫

def get(cache: dict, key: str) -> dict | None:
    return cache.get("entries", {}).get(key)


def put(cache: dict, key: str, *, h: str, payload: dict, model: str | None,
        base_msg_count: int = 0, prompt_version: int | None = None) -> None:
    cache.setdefault("entries", {})[key] = {
        "h": h, "model": model, "generated_at": now_iso(),
        "prompt_version": prompt_version,
        "base_msg_count": base_msg_count, "fail_count": 0, "last_fail_at": None,
        "payload": payload,
    }


def prune(cache: dict, planned_keys: set[str]) -> list[str]:
    """刪除已不在計畫中的單元,回傳被刪掉的鍵。

    emit.build_doc 會把快取裡**每一個**單元都發布出去,所以不清理的話:
      - 把標籤加進 STORY_SKIP_TAGS 後,已產生的那條敘事線仍留在網站上
      - 標籤跌破 STORY_MIN_MSGS、或調小 STORY_MAX_TAGS 時同理
    plan() 是確定性的全量列舉,凡不在其中的就是過時單元。
    """
    gone = [k for k in cache.get("entries", {}) if k not in planned_keys]
    for k in gone:
        del cache["entries"][k]
    return gone


def mark_fail(cache: dict, key: str) -> None:
    e = cache.setdefault("entries", {}).get(key)
    if e is None:
        cache["entries"][key] = {"h": None, "model": None, "generated_at": None,
                                 "base_msg_count": 0, "fail_count": 1,
                                 "last_fail_at": now_iso(), "payload": None}
    else:
        e["fail_count"] = e.get("fail_count", 0) + 1
        e["last_fail_at"] = now_iso()


# ---------------------------------------------------------------- 鎖

def acquire_lock() -> bool:
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(LOCK_PATH):
        try:
            with open(LOCK_PATH, encoding="utf-8") as f:
                info = json.load(f)
            started = _parse_iso(info.get("started_at"))
            if started and (datetime.now(TW) - started) < timedelta(minutes=LOCK_STALE_MINUTES):
                print(f"[lock] 另一個 insights 執行中(pid={info.get('pid')},"
                      f"起於 {info.get('started_at')}),本次跳過。")
                return False
            print("[lock] 發現過期的鎖,接手。")
        except Exception:  # noqa: BLE001
            print("[lock] 鎖檔損毀,接手。")
    with open(LOCK_PATH, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "started_at": now_iso()}, f)
    return True


def release_lock() -> None:
    for _ in range(3):
        try:
            if os.path.exists(LOCK_PATH):
                os.remove(LOCK_PATH)
            return
        except OSError:
            time.sleep(0.2)
