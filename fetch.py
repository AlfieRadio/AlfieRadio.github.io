"""
從 Telegram 頻道抓訊息、解析 hashtag,輸出給統計網頁使用。

第一次執行:會回補最近 INITIAL_DAYS 天,並要求手機驗證碼登入(只需一次)。
之後執行:只抓上次之後的新訊息(增量),累積存進 data/messages.json。

用法:
    python fetch.py
"""

import os
import re
import sys
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telethon.sync import TelegramClient
from telethon.tl.functions.messages import CheckChatInviteRequest
from telethon.tl.types import ChatInviteAlready

# 在排程/管線環境(stdout 非主控台)下,Windows 預設 cp950 會讓中文/符號輸出爆掉。
# 強制 UTF-8 輸出,避免 UnicodeEncodeError 害整個抓取失敗。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

load_dotenv()

API_ID = os.getenv("TG_API_ID")
API_HASH = os.getenv("TG_API_HASH")
CHANNEL = os.getenv("TG_CHANNEL")
INITIAL_DAYS = int(os.getenv("INITIAL_DAYS", "30"))
TZNAME = os.getenv("TZ", "Asia/Taipei")

if not (API_ID and API_HASH and CHANNEL):
    print("✗ 請先把 .env.example 複製成 .env,並填好 TG_API_ID / TG_API_HASH / TG_CHANNEL")
    sys.exit(1)

API_ID = int(API_ID)
LOCAL_TZ = ZoneInfo(TZNAME)

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
DOCS_DIR = os.path.join(BASE, "docs")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(DOCS_DIR, exist_ok=True)
MESSAGES_JSON = os.path.join(DATA_DIR, "messages.json")

# hashtag:# 後面接文字/數字/底線(\w 在 Python 3 也涵蓋中文)
HASHTAG_RE = re.compile(r"#\w+", re.UNICODE)


def resolve_channel(client):
    """只用唯讀方式解析頻道。私人頻道需你的帳號『已經是成員』。

    本函式不會替你加入任何頻道(不做任何寫入型動作),確保全程唯讀。
    """
    m = re.search(r"t\.me/(?:joinchat/|\+)([\w-]+)", CHANNEL)
    if m:
        invite_hash = m.group(1)
        res = client(CheckChatInviteRequest(invite_hash))  # 唯讀:只查看邀請資訊
        if isinstance(res, ChatInviteAlready):
            return res.chat  # 你已是成員 → 直接使用
        raise SystemExit(
            "✗ 你的帳號還不是這個頻道的成員。\n"
            "  請先在 Telegram App 點邀請連結手動加入,再重新執行。\n"
            "  (本腳本不會自動加入,以確保只做唯讀、不對頻道做任何動作。)"
        )
    return client.get_entity(CHANNEL)  # 唯讀


def load_existing():
    if os.path.exists(MESSAGES_JSON):
        with open(MESSAGES_JSON, "r", encoding="utf-8") as f:
            d = json.load(f)
        return {m["id"]: m for m in d.get("messages", [])}
    return {}


def serialize(msg, channel_id):
    text = msg.message or ""
    tags = HASHTAG_RE.findall(text)
    dt_utc = msg.date  # tz-aware (UTC)
    dt_local = dt_utc.astimezone(LOCAL_TZ)
    iso = dt_local.isocalendar()  # (year, week, weekday[1=Mon])
    monday = dt_local.date() - timedelta(days=iso.weekday - 1)
    sunday = monday + timedelta(days=6)
    return {
        "id": msg.id,
        "date_utc": dt_utc.isoformat(),
        "local_date": dt_local.strftime("%Y-%m-%d"),
        "local_time": dt_local.strftime("%H:%M"),
        "iso_week": f"{iso.year}-W{iso.week:02d}",
        "week_range": f"{monday.month}/{monday.day}–{sunday.month}/{sunday.day}",
        "hashtags": tags,
        "text": text,
        "link": f"https://t.me/c/{channel_id}/{msg.id}",
    }


def main():
    session_path = os.path.join(BASE, "tg_session")
    with TelegramClient(session_path, API_ID, API_HASH) as client:
        channel = resolve_channel(client)
        cid = channel.id
        title = getattr(channel, "title", str(cid))
        print(f"✓ 頻道:{title} (id={cid})")

        existing = load_existing()
        min_id = max(existing.keys()) if existing else 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=INITIAL_DAYS)
        if min_id:
            print(f"→ 增量更新:抓取 id > {min_id} 的新訊息…")
            iterator = client.iter_messages(channel, min_id=min_id)
        else:
            print(f"→ 初次回補:抓取最近 {INITIAL_DAYS} 天…")
            iterator = client.iter_messages(channel)

        new = {}
        for msg in iterator:
            if not min_id and msg.date < cutoff:
                break  # 回補到指定天數就停
            if not (msg.message and msg.message.strip()):
                continue  # 跳過純媒體 / 系統訊息
            new[msg.id] = serialize(msg, cid)
            if len(new) % 200 == 0:
                print(f"  …已處理 {len(new)} 則")

        if not new and os.path.exists(MESSAGES_JSON):
            print("✓ 沒有新訊息,維持原狀(不重寫檔案、不觸發推送)。")
            return

        existing.update(new)
        messages = sorted(existing.values(), key=lambda m: m["id"])
        out = {
            "channel": {"id": cid, "title": title, "link_base": f"https://t.me/c/{cid}"},
            "fetched_at": datetime.now(LOCAL_TZ).isoformat(),
            "tz": TZNAME,
            "messages": messages,
        }

        with open(MESSAGES_JSON, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        with open(os.path.join(DOCS_DIR, "data.js"), "w", encoding="utf-8") as f:
            f.write("window.TG_DATA = ")
            json.dump(out, f, ensure_ascii=False)
            f.write(";\n")

        print(f"✓ 本次新增 {len(new)} 則,總計 {len(messages)} 則。")
        print(f"✓ 已更新:{MESSAGES_JSON}")
        print(f"✓ 用瀏覽器開啟 docs/index.html 即可查看統計。")


if __name__ == "__main__":
    main()
