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


def main() -> int:
    idx = DOCS / "index.html"
    with idx.open(encoding="utf-8", newline="") as fh:
        html = original = fh.read()

    for name in ASSETS:
        f = DOCS / name
        if not f.exists():
            continue
        h = hashlib.sha1(f.read_bytes()).hexdigest()[:8]
        # 同時吃 href="x.css" 與 href="x.css?v=舊指紋"
        html = re.sub(
            rf'((?:src|href)=")({re.escape(name)})(\?v=[0-9a-f]+)?(")',
            rf'\g<1>\g<2>?v={h}\g<4>',
            html,
        )

    if html != original:
        idx.write_text(html, encoding="utf-8", newline="\n")
        print("index.html: 資產指紋已更新")
    else:
        print("index.html: 指紋無變化")
    return 0


if __name__ == "__main__":
    sys.exit(main())
