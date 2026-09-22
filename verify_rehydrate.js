// 驗證 docs/app.js::rehydrate() 還原出來的欄位,與 fetch.py::serialize() 寫進
// data/messages.json 的原始值**逐則完全相同**。
// publish.py 刪掉這些欄位來省 33.5% 體積,一旦還原邏輯有偏差,週分頁會分錯組。
// 作法與 make_insights.py 的對帳斷言一致:直接從 app.js 原始碼抽出函式來跑。
const fs = require("fs");
const src = fs.readFileSync("docs/app.js", "utf8");

function grab(name) {
  const i = src.indexOf("function " + name + "(");
  if (i < 0) throw new Error("找不到 " + name);
  let d = 0, started = false;
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") { d++; started = true; }
    else if (src[j] === "}") { d--; if (started && d === 0) return src.slice(i, j + 1); }
  }
  throw new Error("括號不平衡:" + name);
}

const raw = JSON.parse(fs.readFileSync("data/messages.json", "utf8"));
const LINK_BASE = (raw.channel || {}).link_base || "";
const code = grab("_dayNum") + "\n" + grab("isoWeekOf") + "\n" + grab("rehydrate")
           + "\nreturn { isoWeekOf, rehydrate };";
const { rehydrate } = new Function("LINK_BASE", code)(LINK_BASE);

let bad = 0, checked = 0;
const show = [];
for (const m of raw.messages) {
  const want = { iso_week: m.iso_week, week_range: m.week_range, link: m.link,
                 domain: m.preview ? m.preview.domain : undefined };
  const got = rehydrate(JSON.parse(JSON.stringify(
    Object.fromEntries(Object.entries(m).filter(([k]) =>
      !["iso_week", "week_range", "link", "date_utc"].includes(k))))));
  if (got.preview) delete got.preview.domain, rehydrate(got);
  const g = { iso_week: got.iso_week, week_range: got.week_range, link: got.link,
              domain: got.preview ? got.preview.domain : undefined };
  checked++;
  for (const k of Object.keys(want)) {
    if (want[k] !== g[k]) {
      bad++;
      if (show.length < 8) show.push(`${m.id} ${m.local_date} ${k}: 期望 ${JSON.stringify(want[k])} 得到 ${JSON.stringify(g[k])}`);
      break;
    }
  }
}
console.log(`比對 ${checked} 則,不符 ${bad} 則`);
show.forEach(s => console.log("  " + s));
process.exit(bad ? 1 : 0);
