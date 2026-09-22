@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

echo [%date% %time%] fetch...
python fetch.py
if errorlevel 1 (
  echo fetch failed, abort push.
  exit /b 1
)

rem AI 洞察(選用)。沒設金鑰、或距上次未滿 AI_MIN_INTERVAL_HOURS 就自己略過。
rem 刻意不做 exit /b:這一步失敗絕不能擋住資料抓取與發布。
echo [%date% %time%] insights...
python make_insights.py
if errorlevel 1 echo insights step failed, publishing data anyway.

rem 讓改動立刻生效:GitHub Pages 對靜態檔回 max-age=600,
rem 沒有指紋的話改完頭 10 分鐘會看到舊版。指紋用內容雜湊 -> 內容沒變就不會動 index.html。
python stamp_assets.py

echo [%date% %time%] push...
rem 必須分兩次 git add:「git add a b」在 b 不存在時整條失敗,
rem 而那正是首次 AI 成功產出之前的狀態。
git add docs/data.js
git add docs/index.html
if exist docs\insights.js git add docs/insights.js
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "data: auto update"
  git push
) else (
  echo no changes, skip push.
)

echo [%date% %time%] done.
