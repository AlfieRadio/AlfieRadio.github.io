"""把 docs/index.html 引用的靜態檔加上內容指紋 query string。

為什麼需要:GitHub Pages 對 /docs 的靜態檔回 `Cache-Control: max-age=600`。
改完的頭 10 分鐘內開站,瀏覽器會拿舊檔,看起來就像「改了沒上」。
指紋用**內容雜湊**(不是時間戳),所以內容沒變時 index.html 就不會變,
不會替每小時排程製造空 commit。
"""
import hashlib
import pathlib
import re
import sys

# 排程用 cp950 主控台,不強制 UTF-8 會在印中文時崩潰(與 fetch.py 同)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DOCS = pathlib.Path(__file__).parent / "docs"
# 分片(shards/*.js)不在這裡:它們的版本由 manifest.js 內的內容雜湊帶,
# 見 publish.py。這裡只管 index.html 直接引用的檔案。
ASSETS = ["styles.css", "manifest.js", "insights.js", "insights-ui.js", "app.js"]

PRELOAD_ID = "preload-latest-shard"


def _preload_latest_shard(html: str) -> str:
    """把「最新一片」的網址預先寫進 HTML,讓瀏覽器立刻開始抓。

    沒有這行的話關鍵路徑是:文件 → manifest.js →(這時才知道網址)→ 分片,
    在高延遲的連線上白白多一趟來回。分片網址帶內容雜湊,所以每次發布都要更新。

    preload 的 as="script" 與稍後 app.js 動態插入的 <script src> 是同一個網址,
    瀏覽器會直接用 preload 的結果,不會下載兩次。
    """
    man = DOCS / "manifest.js"
    tag = ""
    if man.exists():
        txt = man.read_text(encoding="utf-8")
        # manifest 是 json.dumps 的預設分隔符,鍵值之間有空白:{"m": "2026-09", ...}
        months = re.findall(r'\{\s*"m":\s*"(\d{4}-W?\d{2})".*?"h":\s*"([0-9a-f]+)"', txt)
        if months:
            m, h = months[-1]      # manifest 的 shards 是升序,最後一個就是當週
            tag = (f'<link rel="preload" as="script" id="{PRELOAD_ID}" '
                   f'href="shards/{m}.js?v={h}">')

    # 先把舊的整行移除(含縮排與換行)再插新的,否則每次發布都會多累積一行
    pat = re.compile(r'[ \t]*<link rel="preload"[^>]*id="'
                     + PRELOAD_ID + r'"[^>]*>\r?\n')
    html = pat.sub("", html)
    if tag:
        nl = "\r\n" if "\r\n" in html else "\n"
        html = html.replace('<link rel="stylesheet"',
                            tag + nl + '  <link rel="stylesheet"', 1)
    return html


def main() -> int:
    idx = DOCS / "index.html"
    with idx.open(encoding="utf-8", newline="") as fh:
        html = original = fh.read()

    for name in ASSETS:
        f = DOCS / name
        if not f.exists():
            continue
        h = hashlib.sha1(f.read_bytes()).hexdigest()[:8]
        html = re.sub(
            # 同時吃 href="x.css" 與 href="x.css?v=舊指紋"。
            # data-lazy-src 是「延遲載入」的資產(見 index.html 的說明):
            # 瀏覽器不會自動抓,但一樣要帶內容指紋,否則改版後會拿到舊檔。
            rf'((?:src|href|data-lazy-src)=")({re.escape(name)})(\?v=[0-9a-f]+)?(")',
            rf'\g<1>\g<2>?v={h}\g<4>',
            html,
        )

    html = _preload_latest_shard(html)

    if html != original:
        with idx.open("w", encoding="utf-8", newline="") as fh:
            fh.write(html)
        print("index.html: 資產指紋已更新")
    else:
        print("index.html: 指紋無變化")
    return 0


if __name__ == "__main__":
    sys.exit(main())
