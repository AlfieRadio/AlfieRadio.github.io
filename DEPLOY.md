# 部署:Organization 公開網頁(GitHub Pages,免費、無密碼)

目標:做一個公開網址 `https://<org>.github.io/<repo>/`,任何人都能直接看。
repo 放在一個「中性名稱的 Organization」底下,你的**個人 GitHub 帳號不會出現在對外的地方**;
commit 也用中性身分 `data-bot`,不含你的真名/email。

---

## A. 建立一個免費 Organization(只有你能做)

1. 登入 GitHub → 開 <https://github.com/account/organizations/new>
2. 方案選 **Free**。
3. Organization 名稱取一個**中性、跟你無關**的名字(這個名字會出現在網址裡,例如 `news-hashtag`)。
4. 建好後 → 進 Organization → **People** → 找到你自己 → 把可見性(visibility)設為 **Private**
   (預設通常就是 Private;確認一下,訪客就不會看到「擁有者是你」)。

> 你**不需要**改自己的 GitHub 使用者名稱。對外露出的是 Organization 名稱,不是你的帳號。

---

## B. 建立公開 repo 並推上去

把 **org 名稱**和想要的 **repo 名稱**告訴我,我用一行指令幫你建公開 repo + 推上去:
```powershell
gh repo create <org>/<repo> --public --source=. --remote=origin --push
```
手動等效作法:在該 org 底下新建一個 **Public** repo,然後:
```powershell
git remote add origin https://github.com/<org>/<repo>.git
git branch -M main
git push -u origin main
```

---

## C. 開啟 GitHub Pages

1. 進 repo → **Settings** → 左側 **Pages**。
2. **Source** 選 **Deploy from a branch**。
3. **Branch** 選 `main`,資料夾選 **`/docs`** → **Save**。
4. 等一兩分鐘,頁面上會出現網址:`https://<org>.github.io/<repo>/`
   這就是你的公開統計網頁,直接發給任何人都能看。

---

## D. 自動更新(目前設定:每小時)

`run_daily.bat` 會:抓新訊息 → `docs/data.js` 有變更才 commit → `git push`(沒變更就跳過)。
GitHub 一收到更新,Pages 會自動重建,網站就是最新的。

Windows 工作排程器已設定「**AlfieRadio Hashtag Update**」:每小時跑一次 `run_daily.bat`,
使用電池時也跑、錯過排程會補跑。要改頻率直接在工作排程器裡調整觸發程序即可。

> 讓排程能無人值守 push:第一次手動 `git push` 時,Windows 的 Git Credential Manager 會跳一次登入,
> 登入後會記住,之後排程就能自動推。

---

## 安全 / 隱私備忘
- repo 是**公開**的:`docs/data.js` 的頻道全文會公開在網路上(這是你選擇的)。
- **絕不要**把 `.env`、`*.session`、`data/` 推上去——已被 `.gitignore` 擋住。
- commit 身分已設成中性 `data-bot <data-bot@example.com>`,不含你的真名/email;
  想換別的顯示名稱,跟我說即可。
- 訊息裡的 `t.me/c/...` 連結只有頻道成員點得開,外部訪客點不開(正常,內文已直接顯示在頁面)。
