"""從 LLM 回應裡挖出 JSON。

免費模型的兩大失敗模式:
  1. 用 ```json 圍籬包起來
  2. 前面加「好的,以下是分析結果:」、後面再補一段心得

所以不能直接 json.loads,要先把真正的 JSON 物件切出來。
切法**必須**追蹤字串狀態 —— AI 寫的中文裡出現大括號很正常
(例:{"detail": "獲利 } 成長"}),天真的計數法會在字串中途就歸零,
切出半截 JSON。
"""
from __future__ import annotations

import json
import re


def clip(s, n: int) -> str:
    s = ("" if s is None else str(s)).replace("\n", " ").strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"


def _balanced_slice(s: str) -> str | None:
    """從第一個 { 掃到與之配對的 },略過字串內與跳脫後的括號。"""
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    return None


_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def extract_json(raw: str | None) -> dict | None:
    """回傳 dict;失敗回 None(由呼叫端決定要修復重試還是換供應商)。"""
    if not raw:
        return None
    text = _FENCE.sub("", raw.strip())

    blob = _balanced_slice(text)
    if blob is None:
        return None

    try:
        out = json.loads(blob)
    except json.JSONDecodeError:
        # 常見於被截斷或多打逗號的輸出,做一次容錯再試
        try:
            out = json.loads(_TRAILING_COMMA.sub(r"\1", blob))
        except json.JSONDecodeError:
            return None

    return out if isinstance(out, dict) else None
