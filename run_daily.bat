@echo off
rem ============================================================
rem  ASCII ONLY. DO NOT PUT NON-ASCII CHARACTERS IN THIS FILE.
rem  cmd.exe reads batch files by byte offset. After "chcp 65001"
rem  multi-byte UTF-8 text gets split mid-character and the tail
rem  is executed as a command. It breaks SILENTLY and the task
rem  still reports exit code 0. See docs note in CLAUDE.md.
rem  (This exact bug ate "python fetch.py" for 12h on 2026-09-22.)
rem  Rationale for each step is documented in CLAUDE.md, not here.
rem ============================================================
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

if not exist data mkdir data
if exist "data\run_daily.log" for %%A in ("data\run_daily.log") do if %%~zA GTR 2000000 del "data\run_daily.log"
call :main >> "data\run_daily.log" 2>&1
exit /b %errorlevel%

:main
echo ==================================================
echo [%date% %time%] start  cwd=%CD%  user=%USERNAME%
where python
python -V

echo [%date% %time%] STEP fetch
python fetch.py
if errorlevel 1 (
  echo fetch failed, abort push.
  exit /b 1
)

rem Optional. Skips itself when no API key or within AI_MIN_INTERVAL_HOURS.
rem No "exit /b" here on purpose: AI must never block data publishing.
echo [%date% %time%] STEP insights
python make_insights.py
if errorlevel 1 echo insights step failed, publishing data anyway.

rem Content-hash fingerprints on css/js so Pages max-age=600 cannot serve stale files.
echo [%date% %time%] STEP stamp
python stamp_assets.py

echo [%date% %time%] STEP push
rem Two separate "git add" calls: "git add a b" fails entirely if b is absent.
git add docs/manifest.js
git add docs/shards
git add docs/index.html
if exist docs\insights.js git add docs/insights.js
git diff --cached --quiet
if errorlevel 1 (
  git commit -m "data: auto update"
  git push
) else (
  echo no changes, skip push.
)

echo [%date% %time%] STEP done
exit /b 0
