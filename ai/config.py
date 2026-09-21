"""AI 洞察的設定,全部由環境變數讀取(.env,已 gitignored)。

金鑰**只存本機**,永不進 git、不設 GitHub Secret ——
AI 步驟跑在本機每小時排程裡,雲端 workflow 只負責把產出的靜態檔發布上線。

LLM 區塊移植自 E:\\Tom\\AI Coding\\ETF_TEST\\src\\config.py:73-100(實戰驗證過的備援組合)。
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

# 必須在讀任何 os.getenv 之前載入 .env(與 fetch.py:32 同慣例)
load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# ---------------------------------------------------------------- 供應商
# 指定誰先打;其餘仍按預設順序接在後面(只調順序,不排除任何一家)
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini")

# Gemini(主力,免費):金鑰 https://aistudio.google.com/apikey
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# 主模型:官方文件(2026-09 查證)列為一般文字任務的建議預設。
# 注意 Google 的免費額度已不在公開文件列出,要看自己的儀表板:
#   https://aistudio.google.com/rate-limit
# 若你的免費層對這個模型額度很緊,在 .env 覆寫成 lite 版即可。
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
# 同金鑰的降級備援:刻意選不同世代的 lite —— 不同模型有各自的每日額度,
# 主模型額度用盡時才有退路;lite 也較不會被 thinking 吃光輸出。
GEMINI_MODEL_FALLBACK = os.getenv("GEMINI_MODEL_FALLBACK", "gemini-3.5-flash-lite")
# thinking 模型的「思考」與正文共用 maxOutputTokens;不設上限時思考會膨脹到把正文截斷,
# 而半截的 JSON 必定解析失敗。0=關閉思考、負數=不送這個參數(某些世代不接受)。
GEMINI_THINKING_BUDGET = _int("GEMINI_THINKING_BUDGET", 2048)

# OpenRouter(免費第二供應商):與 Google 完全獨立,Google 整批掛掉仍有救。
# 免費金鑰 https://openrouter.ai/keys;可逗號分隔多個模型 → 同層內依序備援。
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL", "z-ai/glm-4.5-air:free,openai/gpt-oss-120b:free"
)
OPENROUTER_MODELS = [m.strip() for m in OPENROUTER_MODEL.split(",") if m.strip()]

# Claude(選用付費備援)。**預設不啟用** —— 必須同時設 ANTHROPIC_API_KEY
# 且 AI_ALLOW_PAID=1 才會進備援鏈。這道閘門讓失控迴圈也不可能產生帳單。
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5")
AI_ALLOW_PAID = os.getenv("AI_ALLOW_PAID", "0")

# ---------------------------------------------------------------- 節流 / 預算
# 20 而非 24:保證每個日曆日都找得到執行窗,不會每天延後而漏掉一天
AI_MIN_INTERVAL_HOURS = _int("AI_MIN_INTERVAL_HOURS", 20)
# 單次執行最多處理幾個單元 —— 最壞情況的硬天花板
AI_MAX_UNITS = _int("AI_MAX_UNITS", 12)
# 每次呼叫之間的間隔秒數,避開免費層的速率限制
AI_CALL_SLEEP = _int("AI_CALL_SLEEP", 3)

# ---------------------------------------------------------------- 產生器參數
STORY_MIN_MSGS = _int("STORY_MIN_MSGS", 12)      # 少於這個則數不值得做敘事線
STORY_MAX_TAGS = _int("STORY_MAX_TAGS", 80)      # 敘事線候選上限(先小後大)
STORY_CHAR_BUDGET = _int("STORY_CHAR_BUDGET", 24000)
DIGEST_CHAR_BUDGET = _int("DIGEST_CHAR_BUDGET", 32000)
CLUSTER_MIN_COUNT = _int("CLUSTER_MIN_COUNT", 5)  # 進入聚類的標籤門檻

# 不做敘事線的標籤:功能性/結構性標籤,寫成敘事沒有意義
STORY_SKIP_TAGS = {
    t.strip().lower()
    for t in os.getenv("STORY_SKIP_TAGS", "#重要,#ai整理,#盤點,#焦點").split(",")
    if t.strip()
}

HTTP_TIMEOUT = _int("AI_HTTP_TIMEOUT", 90)
