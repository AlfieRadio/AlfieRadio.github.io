#!/usr/bin/env python
"""AI 洞察產生器 CLI。

設計原則:
  * **永不 raise、永遠 exit 0**(除非 --strict)—— 這支腳本掛掉絕不能擋住資料抓取與發布。
  * 每個單元各自 try/except —— 一個壞標籤不會中斷整批。
  * 只有 payload 真的變了才寫 docs/insights.js —— 冪等,不製造每小時的空 commit。

常用:
    python make_insights.py --dry-run      # 零網路零寫入,看這次會做什麼、花多少
    python make_insights.py --only radar --force
    python make_insights.py                # 正常執行(受 20 小時閘門節流)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai import cache as cache_mod          # noqa: E402
from ai import config, corpus, generators  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- 對帳斷言

_PARITY_JS = r"""
const fs = require("fs");
const path = process.argv[2], appPath = process.argv[3];

// 載入 data.js(它是 window.TG_DATA = {...};)
global.window = {};
new Function("window", fs.readFileSync(path, "utf8"))(global.window);
const MSGS = (global.window.TG_DATA.messages || []).slice().sort((a, b) => a.id - b.id);

// 從 app.js 原始碼裡「抽出真正的 computeRanking 函式」再執行 ——
// 重寫一份比對等於自己跟自己比,證明不了任何事。
const src = fs.readFileSync(appPath, "utf8");
const start = src.indexOf("function computeRanking");
if (start < 0) { console.error("找不到 computeRanking"); process.exit(2); }
let depth = 0, end = -1, seen = false;
for (let i = start; i < src.length; i++) {
  if (src[i] === "{") { depth++; seen = true; }
  else if (src[i] === "}") { depth--; if (seen && depth === 0) { end = i + 1; break; } }
}
const fn = new Function(src.slice(start, end) + "; return computeRanking;")();
const out = {};
for (const r of fn(MSGS)) out[r.display] = r.count;
process.stdout.write(JSON.stringify(out));
"""


def verify_parity(c: corpus.Corpus) -> bool:
    """確認 Python 的標籤計數與前端 app.js::computeRanking 逐一相符。

    這是最可能出現的靜默 bug:# 前綴、大小寫、每則去重任一處漂移,
    洞察頁的數字就會與頁籤②打架,而且不會有任何錯誤訊息。
    """
    data_js = os.path.join(BASE, "docs", "data.js")
    app_js = os.path.join(BASE, "docs", "app.js")
    if not (os.path.exists(data_js) and os.path.exists(app_js)):
        print("  ⚠ 找不到 docs/data.js 或 docs/app.js,跳過對帳")
        return True

    tmp = os.path.join(tempfile.gettempdir(), "_parity_check.js")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(_PARITY_JS)
    try:
        # 必須明指 utf-8:預設會用系統編碼(Windows 上是 cp950),
        # node 吐回的中文標籤就會炸成 UnicodeDecodeError。
        r = subprocess.run(["node", tmp, data_js, app_js],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
    except FileNotFoundError:
        print("  ⚠ 沒有 node,跳過對帳(建議安裝以啟用這道防線)")
        return True
    except subprocess.TimeoutExpired:
        print("  ⚠ 對帳逾時,跳過")
        return True
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    if r.returncode != 0 or not (r.stdout or "").strip():
        print(f"  ⚠ 對帳腳本無輸出({(r.stderr or '').strip()[:200]}),跳過")
        return True

    try:
        js = json.loads(r.stdout)
    except json.JSONDecodeError as exc:
        print(f"  ⚠ 對帳輸出非 JSON({exc}),跳過")
        return True
    py = {t.display: t.count for t in c.ranking()}

    only_js = set(js) - set(py)
    only_py = set(py) - set(js)
    diff = {k: (py[k], js[k]) for k in set(py) & set(js) if py[k] != js[k]}

    if not (only_js or only_py or diff):
        print(f"  ✓ 對帳通過:{len(py)} 個標籤的計數與 app.js::computeRanking 完全一致")
        return True

    print(f"  ✗ 對帳失敗!Python {len(py)} 個 / JS {len(js)} 個")
    for k in list(only_py)[:5]:
        print(f"      只有 Python 有:{k} ×{py[k]}")
    for k in list(only_js)[:5]:
        print(f"      只有 JS 有:{k} ×{js[k]}")
    for k, (a, b) in list(diff.items())[:5]:
        print(f"      次數不符:{k}  Python={a}  JS={b}")
    return False


# ---------------------------------------------------------------- 報表

def _pad(s: str, n: int) -> str:
    """CJK 全形字在等寬終端佔 2 格,用字元數對齊會跑版。"""
    w = sum(2 if ord(ch) > 0x2E7F else 1 for ch in s)
    return s + " " * max(0, n - w)


def print_plan(units: list, picked: list, c: corpus.Corpus) -> None:
    print(f"\n語料:{c.message_count} 則訊息 / {len(c.tags)} 個標籤 / 涵蓋到 {c.data_through}")
    print(f"單元總數 {len(units)},其中過期 {sum(1 for u in units if u.stale)} 個,"
          f"本次選用 {len(picked)} 個(上限 {config.AI_MAX_UNITS})")

    # 先完整列出「這次真的會做的」—— 這才是使用者要看的
    print(f"\n★ 本次選用({len(picked)} 個,依優先序)")
    print(f"  {_pad('單元', 26)} {'輸入字元':>9}  原因")
    print("  " + "-" * 72)
    for u in picked:
        print(f"  {_pad(u.key, 26)} {u.est_chars:>9,}  {u.reason}")

    # 其餘只做分類統計,不逐一列出
    rest = [u for u in units if u not in picked]
    if rest:
        print(f"\n○ 其餘 {len(rest)} 個")
        for kind in ("radar", "clusters", "week", "month", "story"):
            us = [u for u in rest if u.kind == kind]
            if not us:
                continue
            queued = [u for u in us if u.stale]
            fresh = [u for u in us if not u.stale and "等待" not in u.reason]
            waiting = [u for u in us if not u.stale and "等待" in u.reason]
            bits = []
            if queued:
                bits.append(f"{len(queued)} 個排隊中")
            if fresh:
                bits.append(f"{len(fresh)} 個已最新")
            if waiting:
                bits.append(f"{len(waiting)} 個等待前置單元")
            print(f"  {_pad(kind, 12)} {' / '.join(bits)}")

    tot = sum(u.est_chars for u in picked)
    print(f"\n本次預估輸入 ≈ {tot:,} 字元(約 {tot // 1000}k tokens),"
          f"呼叫 {len(picked)} 次,預計耗時 ≈ {len(picked) * (config.AI_CALL_SLEEP + 6)} 秒")
    if not picked:
        print("  → 沒有需要重生的單元。")


# ---------------------------------------------------------------- 主流程

def main() -> int:
    ap = argparse.ArgumentParser(description="AI 洞察產生器")
    ap.add_argument("--dry-run", action="store_true", help="零網路零寫入,只印出計畫")
    ap.add_argument("--force", action="store_true", help="忽略 20 小時閘門")
    ap.add_argument("--full", action="store_true", help="允許大量重生(解除雪崩保護)")
    ap.add_argument("--offline", action="store_true", help="用 fixture 取代 LLM 呼叫")
    ap.add_argument("--only", help="只處理這個單元鍵,如 radar 或 week:2026-W38")
    ap.add_argument("--max", type=int, default=None, help="覆寫本次單元上限")
    ap.add_argument("--strict", action="store_true", help="出錯時回傳非 0(給手動除錯用)")
    ap.add_argument("--no-verify", action="store_true", help="跳過對帳斷言")
    args = ap.parse_args()

    try:
        c = corpus.load()
    except Exception as exc:  # noqa: BLE001
        print(f"[fatal] 無法載入語料:{exc}")
        return 1 if args.strict else 0

    if not args.no_verify:
        print("對帳檢查…")
        if not verify_parity(c) and args.strict:
            return 1

    cache = cache_mod.load()
    units = generators.plan(c, cache)

    if args.only:
        units = [u for u in units if u.key == args.only]
        for u in units:
            u.stale, u.reason = True, "--only 指定"
        if not units:
            print(f"[warn] 找不到單元 {args.only}")
            return 0

    max_units = args.max if args.max is not None else config.AI_MAX_UNITS
    picked = generators.select(units, max_units)

    if args.dry_run:
        print_plan(units, picked, c)
        return 0

    # ---- 以下為真實執行路徑
    if not args.force and not args.only:
        h = cache_mod.hours_since(cache.get("meta", {}).get("last_ai_run_at"))
        if h < config.AI_MIN_INTERVAL_HOURS:
            print(f"距上次執行僅 {h:.1f} 小時(門檻 {config.AI_MIN_INTERVAL_HOURS}),本次略過。")
            return 0

    stale_n = sum(1 for u in units if u.stale)
    if stale_n > 3 * max_units and not (args.full or args.only):
        print(f"[warn] 過期單元 {stale_n} 個,遠超單次上限 —— 疑似大量回補。"
              f"確認無誤請加 --full。本次不動作。")
        return 0

    if not hasattr(generators, "generate_unit"):
        print("[info] 生成層尚未實作(目前只完成語料/快取/規劃)。請用 --dry-run 檢視計畫。")
        return 0

    if not cache_mod.acquire_lock():
        return 0

    changed = 0
    stats: dict[str, int] = {}
    try:
        for u in picked:
            try:
                ok = generators.generate_unit(u, c, cache, offline=args.offline)
                if ok:
                    changed += 1
                    stats[ok] = stats.get(ok, 0) + 1
                else:
                    cache_mod.mark_fail(cache, u.key)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                cache_mod.mark_fail(cache, u.key)
            time.sleep(config.AI_CALL_SLEEP)

        from ai import emit
        emit.refresh_deterministic(cache, c)      # 無 AI:刷新聚類次數/共現、雷達表
        wrote = emit.write_if_changed(cache, c)

        cache["meta"]["last_ai_run_at"] = cache_mod.now_iso()
        cache_mod.save(cache)
        print(f"\ncalls={len(picked)} 成功={changed} providers={stats} "
              f"skipped={len(units) - len(picked)} "
              f"insights.js={'已更新' if wrote else '無變化'}")
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 1 if args.strict else 0
    finally:
        cache_mod.release_lock()

    return 0


if __name__ == "__main__":
    sys.exit(main())
