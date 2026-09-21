"""供應商備援鏈(通用,與洞察內容無關)。

移植自 E:\\Tom\\AI Coding\\ETF_TEST\\src\\commentary.py:119-255 —— 那段已實戰驗證,
且與 ETF 領域完全無關。四處修改:

1. **解耦 prompt**:原本的 generate_commentary(results) 改成 generate(system, user),
   變成通用工具;回傳 (文字, 模型標籤) 以便記錄哪個模型產出哪個單元。
2. **原生 JSON 模式**:Gemini 的 responseMimeType/responseSchema 是可靠度最大的
   單一提升,而且免費。OpenRouter 用 response_format(不支援的模型會靜默忽略,
   由 jsonout 的抽取與修復重試接手)。
3. **付費閘門**:AI_ALLOW_PAID != "1" 時直接把 claude 踢出備援鏈。
   預設關閉 —— 失控迴圈也不可能產生帳單。
4. **temperature 0.4**(原 0.7):這是結構化抽取,不是散文創作。

保留不動的關鍵行為:
  * 每次嘗試各自 try/except —— 單一供應商拋錯不會中斷整條鏈
    (這是原專案早期的真實 bug:整個迴圈共用一個 try,第一家拋錯備援就輪不到)
  * Gemini finishReason == MAX_TOKENS 時**連非空文字都丟棄** ——
    寧可換下一家拿完整的,也不要半截的。對 JSON 而言截斷比慢更致命。
"""
from __future__ import annotations

import time

import requests

from . import config

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


# 這次執行中已確定「每日配額用盡」的模型。單次執行內有效,不落地。
# 免費層的每日額度很小(實測 gemini-3.5-flash 僅 20 次/日),回補上百個單元時
# 必然用完;若不記住,後面每個單元都會再撞一次牆並白等 9 秒重試。
_exhausted_today: set = set()


def _is_daily_quota(resp) -> bool:
    """區分「每日配額耗盡」與「每分鐘太快」—— 兩者的正確反應完全相反。

    每分鐘限制 → 等幾秒再試是對的。
    每日配額   → 要等到隔天才重置,重試純屬浪費,該立刻換下一個模型。
    """
    try:
        for d in resp.json().get("error", {}).get("details", []):
            for v in d.get("violations", []):
                if "PerDay" in (v.get("quotaId") or ""):
                    return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _post_with_retry(url, headers, payload, model_label: str = ""):
    last_err = None
    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload,
                                 timeout=config.HTTP_TIMEOUT)
        except requests.RequestException as exc:
            last_err = f"連線失敗:{exc}"
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            break
        if resp.ok:
            return resp
        last_err = f"{resp.status_code}: {resp.text[:200]}"
        if resp.status_code == 429 and _is_daily_quota(resp):
            if model_label:
                _exhausted_today.add(model_label)
                print(f"  [llm] {model_label} 今日免費配額已用盡,本次執行不再嘗試")
            return None
        if resp.status_code in (429, 500, 502, 503, 529) and attempt < 2:
            time.sleep(3 * (attempt + 1))
            continue
        break
    print(f"  [llm] API 失敗:{last_err}")
    return None


def _call_claude(system, user, model, json_schema=None,
                 max_tokens=8192, temperature=0.4):
    messages = [{"role": "user", "content": user}]
    if json_schema is not None:
        # 預填 "{" 逼模型直接吐 JSON,回應前面再補回來
        messages.append({"role": "assistant", "content": "{"})
    resp = _post_with_retry(
        ANTHROPIC_URL,
        {"x-api-key": config.ANTHROPIC_API_KEY,
         "anthropic-version": "2023-06-01",
         "content-type": "application/json"},
        {"model": model, "max_tokens": max_tokens, "temperature": temperature,
         "system": system, "messages": messages},
        model_label=model,
    )
    if resp is None:
        return None
    blocks = resp.json().get("content", [])
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
    if text and json_schema is not None:
        text = "{" + text
    return text or None


def _call_gemini(system, user, model, json_schema=None,
                 max_tokens=8192, temperature=0.4):
    gen = {"temperature": temperature, "maxOutputTokens": max_tokens}
    # 負數 = 完全不送 thinkingConfig。逃生門:若某個世代不接受這個參數,
    # 在 .env 設 GEMINI_THINKING_BUDGET=-1 就能繞過,不必改程式。
    if config.GEMINI_THINKING_BUDGET >= 0:
        gen["thinkingConfig"] = {"thinkingBudget": config.GEMINI_THINKING_BUDGET}
    if json_schema is not None:
        # 原生 JSON 模式:免費,且是整條鏈裡可靠度提升最大的一招
        gen["responseMimeType"] = "application/json"
        gen["responseSchema"] = json_schema

    resp = _post_with_retry(
        GEMINI_URL.format(model=model),
        {"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"},
        {"contents": [{"parts": [{"text": system + "\n\n" + user}]}],
         "generationConfig": gen},
        model_label=model,
    )
    if resp is None:
        return None
    cand = (resp.json().get("candidates") or [{}])[0]
    parts = cand.get("content", {}).get("parts", []) or []
    text = "".join(p.get("text", "") for p in parts).strip()
    # thinking 吃光額度時正文會被從中間截斷。半截 JSON 必定解析失敗,
    # 不如直接判失敗換下一家拿完整的。
    if cand.get("finishReason") == "MAX_TOKENS":
        print(f"  [llm] {model} 被 MAX_TOKENS 截斷({len(text)} 字),改用備援")
        return None
    return text or None


def _call_openrouter(system, user, model, json_schema=None,
                     max_tokens=8192, temperature=0.4):
    payload = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}]}
    if json_schema is not None:
        # 不支援的模型會靜默忽略這個欄位 —— 由 jsonout 的抽取與修復重試接手
        payload["response_format"] = {"type": "json_object"}
    resp = _post_with_retry(
        OPENROUTER_URL,
        {"Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
         "Content-Type": "application/json"},
        payload,
        model_label=model,
    )
    if resp is None:
        return None
    choices = resp.json().get("choices") or [{}]
    return (choices[0].get("message", {}).get("content") or "").strip() or None


# 每組 = (是否有金鑰, 呼叫函式, [依序嘗試的模型])
# 同組內多模型 = 同一把金鑰的免費降級備援
_PROVIDERS = {
    "gemini": (lambda: bool(config.GEMINI_API_KEY), _call_gemini,
               [config.GEMINI_MODEL, config.GEMINI_MODEL_FALLBACK]),
    "openrouter": (lambda: bool(config.OPENROUTER_API_KEY), _call_openrouter,
                   config.OPENROUTER_MODELS),
    "claude": (lambda: bool(config.ANTHROPIC_API_KEY) and config.AI_ALLOW_PAID == "1",
               _call_claude, [config.CLAUDE_MODEL]),
}

# 免費的在前,付費的殿後
_DEFAULT_ORDER = ["gemini", "openrouter", "claude"]


def attempt_chain():
    """攤平成 (標籤, 呼叫函式, 模型) 的有序清單,只保留有金鑰(且被允許)的供應商。"""
    order = [config.AI_PROVIDER] + [p for p in _DEFAULT_ORDER if p != config.AI_PROVIDER]
    chain = []
    for name in order:
        spec = _PROVIDERS.get(name)
        if not spec:
            continue
        has_key, call, models = spec
        if not has_key():
            continue
        seen = set()
        for model in models:
            if model and model not in seen:
                seen.add(model)
                chain.append((f"{name}:{model}", call, model))
    return chain


def generate(system: str, user: str, *, json_schema=None,
             max_tokens: int = 8192, temperature: float = 0.4,
             on_attempt=None):
    """依序嘗試備援鏈。回傳 (文字, 模型標籤);整條鏈失敗回 (None, None)。

    on_attempt(label, text) 可回傳「修復後的結果」或 None ——
    讓呼叫端在同一個供應商上做一次 JSON 修復重試,再決定要不要換下一家。
    """
    chain = attempt_chain()
    if not chain:
        print("  [llm] 沒有任何可用的供應商金鑰(請在 .env 設 GEMINI_API_KEY)")
        return None, None

    for label, call, model in chain:
        if model in _exhausted_today:
            continue          # 今日配額已用盡,不必再撞一次
        try:
            text = call(system, user, model, json_schema, max_tokens, temperature)
        except Exception as exc:  # noqa: BLE001
            print(f"  [llm] {label} 例外:{exc}")
            text = None
        if text and on_attempt is not None:
            handled = on_attempt(label, call, model, system, user, text)
            if handled is not None:
                return handled, label
            text = None          # 修復也失敗 → 換下一家
        elif text:
            return text, label
        print(f"  [llm] {label} 無可用輸出,改用下一個備援")
    return None, None
