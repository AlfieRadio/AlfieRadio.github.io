# 頻道 Hashtag 統計

從 Telegram 頻道(含私人頻道)定期抓訊息、解析 hashtag,產生一個**公開的純靜態統計網頁**:

> 🌐 線上版:**<https://alfieradio.github.io/>**(GitHub Pages,自動更新)

功能:**① 每日內容**(選日期回顧當天訊息)、**② Hashtag 排行**(點擊可篩選)、
**③ 週整理**、**④ ⭐ 重要**、**⑤ 🧠 AI 洞察**、**全文搜尋**(含連結預覽卡的標題/摘要)、
**日期範圍篩選**、**連結預覽卡**(網址訊息直接顯示新聞標題與摘要,來自 Telegram 已抓好的預覽,全程唯讀)。

抓取**全程唯讀**:只讀訊息,絕不發送、修改、刪除頻道任何內容。

---

## 一、第一次設定(只做一次)

### 1. 安裝套件
```powershell
pip install -r requirements.txt
```

### 2. 申請 API 憑證
登入 <https://my.telegram.org> → **API development tools** → 建立一個 App,
取得 `api_id` 和 `api_hash`。

### 3. 設定 .env
把 `.env.example` 複製成 `.env`,填入你的值:
```
TG_API_ID=你的api_id
TG_API_HASH=你的api_hash
TG_CHANNEL=https://t.me/+3j-gRC37UBo0MjM1
INITIAL_DAYS=30
TZ=Asia/Taipei
```
> ⚠ `.env` 含個人機密,不要外流(已在 .gitignore 排除)。

### 4. 第一次執行(會要求手機驗證碼登入)
```powershell
python fetch.py
```
- 第一次會問你的手機號碼 → 輸入 Telegram 傳來的驗證碼(若有兩步驗證再輸入密碼)。
- 登入狀態會存在 `tg_session.session`,之後不用再登入。
- 會回補最近 `INITIAL_DAYS` 天的訊息。

### 5. 看結果
用瀏覽器開啟 **`docs/index.html`**(直接點兩下即可),或部署後看線上版。

---

## 二、之後的更新

### 自動(已設定)
Windows 工作排程「AlfieRadio Hashtag Update」**每小時**跑一次 `run_daily.bat`:
抓新訊息 → `docs/data.js` 有變更才 commit + push → GitHub Pages 自動重建(約 1–2 分鐘)。
沒有新訊息就什麼都不做(不寫檔、不 push)。

### 手動
```powershell
python fetch.py
```
或點兩下 **`run_daily.bat`**(會順便 commit + push)。

### 往回補更早的歷史
```powershell
$env:FETCH_OLDER_DAYS=30; python fetch.py
```
以現有最舊訊息為起點,再往前抓 N 天(一次性,跑完記得清掉環境變數)。

---

---

## 三、AI 洞察(頁籤⑤,選用)

把累積的 hashtag 與連結交給 AI,產生**有時序、有因果**的整理:

| 子頁 | 內容 |
|------|------|
| 趨勢雷達 | 最近 7 日竄升/退燒/新出現的標籤 + AI 解釋原因 |
| 敘事線 | 單一標籤跨月份的時間線:發生什麼、轉折在哪、目前狀態 |
| 週月報 | 每週/每月的主軸、重點事件、熱度變化 |
| 主題地圖 | 把數百個零散標籤歸納成上層主題 |

**設計原則:所有數字、日期、標籤、訊息連結都由程式計算,AI 只負責文字。**
因此不可能出現「AI 編造的統計數字」。每段 AI 文字都以斜體標示,並附上可展開的原始訊息引用。

### 設定
在 `.env` 填入免費金鑰即可(不填就不會有頁籤⑤,其餘功能完全不受影響):
```
GEMINI_API_KEY=你的金鑰      # https://aistudio.google.com/apikey
OPENROUTER_API_KEY=          # 選用,免費的第二供應商備援
```
金鑰**只存在本機**,不進 git、不需要 GitHub Secret。

### 指令
```powershell
python make_insights.py --dry-run    # 看這次會做什麼、花多少(零網路)
python make_insights.py --selftest   # 驗證器自我測試(零網路)
python make_insights.py              # 正常執行(每天最多一次,只算有變動的)
```
首次建立約 113 個單元,靠每日排程分 3–4 晚自動跑完;之後每天約 12 次呼叫。
免費額度綽綽有餘。

---

## 檔案說明
| 檔案 | 用途 |
|------|------|
| `fetch.py` | 抓訊息、解析 hashtag、輸出資料(唯讀) |
| `.env` | 你的 API 憑證與頻道設定(自己建立,不進 git) |
| `data/messages.json` | 累積的訊息原始檔(不進 git) |
| `docs/` | 公開網頁:`index.html` / `app.js` / `styles.css` / `data.js` / `insights*.js` |
| `make_insights.py` | AI 洞察產生器(選用;沒金鑰就自動略過) |
| `ai/` | 洞察的語料計算、快取、供應商備援鏈、四個產生器 |
| `docs/data.js` | 給網頁讀的資料(每次 fetch 自動產生) |
| `run_daily.bat` | 一鍵更新 + 推送 |
| `DEPLOY.md` | GitHub Pages 部署步驟與隱私備忘 |
| `ROADMAP.md` | 長期擴展路線(分片 / 全文搜尋存檔庫) |
