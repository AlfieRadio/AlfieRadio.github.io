"""把 data/messages.json 發布成「按月分片 + 清單」的靜態檔。

為什麼要分片(ROADMAP 階段 1+2):
  單一 docs/data.js 在資料滿一年時來到 9MB / gzip 2.45MB,而它是
  render-blocking 的 classic script —— 整頁會卡在它身上。實測在較慢的
  連線上要 60 秒以上,等同打不開。

兩個手段:
  1. 瘦身:刪掉前端可以自行推導的欄位(link / date_utc / iso_week /
     week_range / preview.domain),實測省 33.5%。
  2. 分片:每個 ISO 週一個檔。過去的週**內容永遠不變** → 檔名帶內容雜湊 →
     瀏覽器可以永久快取;每小時真正會變的只有當週那一片(~44KB)。
     用「週」而不是「月」的理由:實測這條連線約 1/5 的請求會卡住,
     而卡頓發生在**傳輸本體**(TTFB 正常),所以痛苦程度與檔案大小成正比。
     當月整片 150KB 卡到要 13 秒;當週 44KB 只要 3.7 秒。

前端只要先載入 manifest + 最近一片就能開始用,其餘在背景補。

輸出:
  docs/manifest.js          window.TG_MANIFEST = {...}
  docs/shards/YYYY-Www.js   window.TG_SHARD("YYYY-Www", [...]);

manifest 的 shards[] 每筆是「一段範圍」而非「一個月」:
from/to(日期)、i0/i1(訊息 id 範圍)、h(內容雜湊)。
前端只依賴這些範圍欄位,所以日後要換分片粒度不必動前端。

本機的 data/messages.json 維持**完整未瘦身**,它是真相來源,
fetch.py 的增量/回補邏輯(date_utc、min/max id)都靠它。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
MESSAGES_JSON = os.path.join(BASE, "data", "messages.json")
DOCS = os.path.join(BASE, "docs")
SHARD_DIR = os.path.join(DOCS, "shards")

# 前端可以自行推導的欄位。derive() 的對應關係見 docs/app.js::rehydrate()。
DROP_FIELDS = ("date_utc", "link", "iso_week", "week_range")


def trim(m: dict) -> dict:
    out = {k: v for k, v in m.items() if k not in DROP_FIELDS}
    pv = out.get("preview")
    if pv:
        # domain 可由 url 推導;url 不存在時 domain 也沒有意義
        out["preview"] = {k: v for k, v in pv.items() if k != "domain"}
    return out


def _write_if_changed(path: str, text: str) -> bool:
    """只有內容真的變了才寫 —— 每小時排程不能製造空 commit。"""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return False
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return True


def publish() -> dict:
    with open(MESSAGES_JSON, encoding="utf-8") as f:
        raw = json.load(f)

    msgs = sorted(raw.get("messages", []), key=lambda m: m["id"])
    groups: dict[str, list] = {}
    for m in msgs:
        # 直接用資料自帶的 iso_week(fetch.py::serialize 已經算好)——
        # 不要在這裡自己再推一次,多一份推導就多一個會漂移的地方。
        groups.setdefault(m["iso_week"], []).append(m)

    os.makedirs(SHARD_DIR, exist_ok=True)
    shards, written = [], []
    for mo in sorted(groups):
        items = [trim(m) for m in groups[mo]]
        body = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
        text = f'window.TG_SHARD({json.dumps(mo)},{body});\n'
        # 雜湊取自**檔案內容**,所以內容沒變 → 網址沒變 → 瀏覽器快取續用
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
        path = os.path.join(SHARD_DIR, f"{mo}.js")
        if _write_if_changed(path, text):
            written.append(mo)
        dates = [m["local_date"] for m in groups[mo]]
        ids = [m["id"] for m in groups[mo]]
        # i0/i1 是這片的訊息 id 範圍。前端靠它把「AI 洞察的引用 id」直接
        # 對應到需要的月份,只補那幾片 —— 不必為了展開兩則引用就載入整年。
        shards.append({"m": mo, "n": len(items), "from": min(dates),
                       "to": max(dates), "i0": min(ids), "i1": max(ids), "h": h,
                       "bytes": len(text.encode("utf-8"))})

    manifest = {
        "schema": 1,
        "channel": raw.get("channel", {}),
        "fetched_at": raw.get("fetched_at"),
        "tz": raw.get("tz", "Asia/Taipei"),
        "total": len(msgs),
        "shards": shards,
    }
    changed = _write_if_changed(
        os.path.join(DOCS, "manifest.js"),
        "window.TG_MANIFEST = " + json.dumps(manifest, ensure_ascii=False) + ";\n")

    # 清掉已不存在的月份(例如把資料砍短之後),否則它們會永遠留在 docs/
    # regex 同時認舊的月分片(YYYY-MM.js)與新的週分片(YYYY-Www.js),
    # 否則改粒度之後舊檔會永遠留在 docs/ 裡被一起發布。
    keep = {f"{d['m']}.js" for d in shards}
    removed = []
    for fn in os.listdir(SHARD_DIR):
        if re.fullmatch(r"\d{4}-(?:\d{2}|W\d{2})\.js", fn) and fn not in keep:
            os.remove(os.path.join(SHARD_DIR, fn))
            removed.append(fn)

    return {"shards": len(shards), "written": written, "removed": removed,
            "manifest_changed": changed, "total": len(msgs),
            "bytes": sum(d["bytes"] for d in shards)}


def main() -> int:
    r = publish()
    print(f"✓ 發布 {r['total']:,} 則 / {r['shards']} 片(按 ISO 週),"
          f"合計 {r['bytes']:,} bytes")
    if r["written"]:
        print(f"  更新分片:{', '.join(r['written'])}")
    if r["removed"]:
        print(f"  移除分片:{len(r['removed'])} 個 "
              f"({', '.join(r['removed'][:4])}{' …' if len(r['removed']) > 4 else ''})")
    if not r["written"] and not r["manifest_changed"]:
        print("  內容無變化,未寫入任何檔案。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
