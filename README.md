# 頻道 Hashtag 統計

從 Telegram 頻道(含私人頻道)定期抓訊息,解析 hashtag,產生一個離線統計網頁:
**① Hashtag 出現次數排行**、**② 歷史回顧(選日期看當天內容)**、**③ 週整理(分頁)**。

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
用瀏覽器開啟 **`web/index.html`**(直接點兩下即可)。

---

## 二、之後每天更新

直接再跑一次就好,只會抓「上次之後」的新訊息(增量、很快):
```powershell
python fetch.py
```
或點兩下 **`run_daily.bat`**。

---

## 三、設定每天自動跑(Windows 工作排程器)

1. 開「工作排程器」→ 建立基本工作
2. 觸發程序:每天、選一個時間(例如晚上 11:00)
3. 動作:啟動程式 → 程式選 `run_daily.bat`(完整路徑)
4. 完成。之後每天會自動更新資料,你開 `web/index.html` 就是最新的。

> 自動排程能成立的前提是第 4 步已經登入過一次(session 已存在)。

---

## 檔案說明
| 檔案 | 用途 |
|------|------|
| `fetch.py` | 抓訊息、解析 hashtag、輸出資料 |
| `.env` | 你的 api 憑證與頻道設定(自己建立) |
| `data/messages.json` | 累積的訊息資料(原始檔) |
| `web/index.html` | 統計網頁(用瀏覽器開) |
| `web/data.js` | 給網頁讀的資料(每次 fetch 自動產生) |
| `run_daily.bat` | 一鍵更新 |
