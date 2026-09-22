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
# 主模型。**選擇依據是實測,不是版本號大小**(2026-09-21 以本專案金鑰實測):
#   gemini-3.8 / 3.7 / 3.6-flash → 一律 503「high demand」,免費層拿不到
#   gemini-3.5-flash / 3.5-flash-lite / 3.1-flash-lite → 正常可用
#   gemini-2.5-flash → 404「no longer available to new users」(已對新金鑰下架)
# 硬打不到的模型會讓每個單元白白重試 3 次(退避 3s+6s),113 個單元就是十幾分鐘空轉,
# 所以主力直接設成實測可用的最高階者。
# 免費額度看自己的儀表板:https://aistudio.google.com/rate-limit
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
# 同金鑰的降級備援:不同模型有各自的每日額度,主模型額度用盡時才有退路;
# lite 也較不會被 thinking 吃光輸出。
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

# 只有「最近這麼多週」會產生 AI 週報;更舊的期間靠月報涵蓋。
#
# 這是回補歷史資料的成本開關:一年的歷史 = 52 個週報單元,但沒人會去翻
# 一年前的某一週,而月報已經足夠。窗外的週改用 generators._week_skeleton()
# 合成的**確定性統計摘要**餵給月報,零 AI 呼叫。
#
# 注意:窗外但**已經有快取**的週報不會被刪(見 generators.plan() 的規劃條件)。
# 調小這個值只會「不再新增」,不會讓網站上既有的週報消失。
DIGEST_WEEK_WINDOW = _int("DIGEST_WEEK_WINDOW", 12)

# **非主題標籤** —— 敘事線與主題地圖都會排除。
#
# 判準不是「重不重要」,而是**它是「主題」還是「格式/單元標記」**:
#   #法人   每天 4.2 則,每則講的是不同公司的法人新聞
#   #盤勢   當日盤感一句話,常只是情緒反應
#   #盤點   每日固定模板的交易帳損益日誌(連載,不是演進)
#   #Ai整理 / #AI主動式ETF整理   幾乎只有固定樣板文字
#   #新高   貼在「別的股票」上的屬性標記,不是主體
#   #焦點   「這則很重要」的標記,底下主題彼此無關
#   #阿非電台 / #輔導室   節目單元標記
#
# 為什麼兩邊都要排除:
#   敘事線 —— 主體不連續,硬寫因果時間線會**逼模型編造連續性**。
#             這種「假敘事」驗證器擋不到(它只擋假數字/假 id/假標籤)。
#   主題地圖 —— 它們會把分組**灌水並污染**:實測【總經、政策與市場動態】
#             1730 則裡有 1140 則(66%)來自這些標籤;而 #Ai整理 更因為
#             名字含 "Ai" 被誤分到「AI與高效能運算」,但它其實是
#             NotebookLM 摘要標記,與 AI 硬體無關。
#
# 它們仍正常出現在排行、每日內容、週月報與趨勢雷達 —— 那些地方它們是真實資料。
NON_TOPIC_TAGS = {
    t.strip().lower()
    for t in os.getenv(
        "NON_TOPIC_TAGS",
        "#重要,#ai整理,#盤點,#焦點,#法人,#盤勢,#新高,#AI主動式ETF整理,#阿非電台,#輔導室",
    ).split(",")
    if t.strip()
}
# 舊名保留,避免其他地方還在引用
STORY_SKIP_TAGS = NON_TOPIC_TAGS

HTTP_TIMEOUT = _int("AI_HTTP_TIMEOUT", 90)
