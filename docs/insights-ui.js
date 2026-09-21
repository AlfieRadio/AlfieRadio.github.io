/* AI 洞察(頁籤⑤)的所有渲染。
 *
 * 載入順序:data.js → insights.js → insights-ui.js → app.js
 * 本檔在頂層只「定義」,不「呼叫」—— 載入當下 app.js 尚未執行,
 * MSGS / renderMsg / escapeHtml 都還不存在。全部延遲到渲染或點擊時才用。
 *
 * 命名一律加 INS_ 前綴:classic script 的頂層 const 共用同一個全域語彙環境,
 * 與 app.js 撞名會直接 Identifier already declared,整頁陣亡。
 *
 * 降級原則:insights.js 缺席或 schema 不符 → 隱藏頁籤⑤,網站與沒有這功能時完全一樣。
 */

const INS_DATA = window.TG_INSIGHTS || null;

let INS_sub = "radar";        // radar | story | digest | cluster
let INS_story = null;         // 目前選的 tag_key
let INS_period = null;        // 目前選的 week/month id
let INS_digestMode = "weekly";
let INS_byId = null;          // 延遲建立
let INS_lastKey = "";         // memo:避免搜尋框每次按鍵都重畫這一頁

const INS_SUBS = [
  ["radar", "趨勢雷達"],
  ["story", "敘事線"],
  ["digest", "週月報"],
  ["cluster", "主題地圖"],
];


/* ---------------------------------------------------------------- 小工具 */

function insMsgById(id) {
  if (!INS_byId) INS_byId = new Map(MSGS.map(m => [m.id, m]));
  return INS_byId.get(id);
}

/** AI 文字一律走 highlight():它會先 escapeHtml 再套搜尋反白,安全且與全站一致。 */
function insText(s) {
  return highlight(s || "");
}

function insTagChip(tag, extra) {
  const key = (tag || "").toLowerCase();
  return `<span class="chip filter-chip${tagFilter === key ? " active" : ""}" data-tag="${escapeHtml(key)}">${escapeHtml(tag)}${extra || ""}</span>`;
}

/** 引用按鈕 + 展開容器。點擊後才把訊息卡渲染進來(避免一次塞幾百張卡)。 */
function insCite(ids) {
  if (!ids || !ids.length) return "";
  return `<button class="ins-cite" data-msgids="${ids.join(",")}">引用 ${ids.length} 則 ▾</button>`
       + `<div class="ins-cite-box" hidden></div>`;
}

function insUnitHead(u) {
  const when = u && u.generated_at ? relTime(u.generated_at) : "";
  const model = u && u.model ? escapeHtml(u.model) : "";
  if (!when && !model) return "";
  return `<div class="ins-head">${when ? `更新於 ${escapeHtml(when)}` : ""}`
       + `${model ? `<span class="ins-model">${model}</span>` : ""}</div>`;
}

function insEmpty(msg) {
  return `<div class="empty">${escapeHtml(msg)}</div>`;
}


/* ---------------------------------------------------------------- 子頁:趨勢雷達 */

function insRenderRadar() {
  const r = INS_DATA.radar;
  if (!r) return insEmpty("趨勢雷達尚未產生。");

  const row = (d, kind) => {
    const isFresh = kind === "fresh";
    // 數字欄只有 70px:長字串(如日期)會硬換行,所以 fresh 用短徽章,日期移到內文
    const delta = isFresh ? "NEW" : (d.delta > 0 ? `+${d.delta}` : `${d.delta}`);
    const cls = isFresh ? "new" : (d.delta > 0 ? "up" : "down");
    const nums = isFresh
      ? `首見 ${d.first_date} ・ 共 ${d.count} 則`
      : `前 7 日 ${d.prev} → 本 7 日 ${d.cur}`;
    return `<div class="radar-row">
      <div class="radar-tag">${insTagChip(d.tag)}</div>
      <div class="radar-delta ${cls}">${escapeHtml(delta)}</div>
      <div class="radar-body">
        <div class="radar-nums">${escapeHtml(nums)}</div>
        ${d.why ? `<div class="ins-why">${insText(d.why)}</div>` : ""}
        ${insCite(d.recent_ids)}
      </div>
    </div>`;
  };

  const block = (key, title, hint) => {
    const rows = r[key] || [];
    if (!rows.length) return "";
    return `<div class="section-title">${title}<span class="fs-note"> · ${hint}</span></div>`
         + rows.map(d => row(d, key)).join("");
  };

  const w = r.window || {};
  return insUnitHead(r)
    + `<div class="ins-card ins-intro">
         <b>以 ${escapeHtml(r.as_of || "")} 為界的滾動 7 日比較</b>
         <div class="muted">本期 ${escapeHtml(w.cur || "")} ・ 前期 ${escapeHtml(w.prev || "")}</div>
         ${r.note ? `<div class="ins-why">${insText(r.note)}</div>` : ""}
         <div class="muted" style="margin-top:6px">次數與漲跌由程式計算;僅「原因」為 AI 推測。</div>
       </div>`
    + block("rising", "🔥 竄升", "本期明顯變多")
    + block("fresh", "🌱 新出現", "近兩週首次出現")
    + block("falling", "❄️ 退燒", "本期明顯變少");
}


/* ---------------------------------------------------------------- 子頁:敘事線 */

function insRenderStory() {
  const list = INS_DATA.storylines || [];
  if (!list.length) return insEmpty("敘事線尚未產生。");

  // 搜尋框可在此頁內縮小範圍
  const hit = list.filter(s => {
    if (!searchTerm) return true;
    return (s.tag + " " + (s.summary || "") + " " +
            (s.beats || []).map(b => b.title + b.detail).join(" ")).toLowerCase().includes(searchTerm);
  });
  if (!hit.length) return insEmpty("沒有符合搜尋的敘事線。");

  const cur = hit.find(s => s.tag_key === INS_story) || hit[0];

  const rail = hit.map(s =>
    `<div class="day-item${s === cur ? " active" : ""}" data-ins-story="${escapeHtml(s.tag_key)}">
       <span>${escapeHtml(s.tag)}</span><span class="cnt">${s.msg_count}</span>
     </div>`).join("");

  const beats = (cur.beats || []).map(b =>
    `<div class="ins-beat">
       <div class="ins-beat-date">${escapeHtml(b.date)}</div>
       <div class="ins-beat-title">${insText(b.title)}</div>
       <div class="ins-beat-detail">${insText(b.detail)}</div>
       ${insCite(b.msg_ids)}
     </div>`).join("");

  const stateCls = cur.state === "升溫" ? "up" : (cur.state === "降溫" ? "down" : "flat");

  const body = `
    ${insUnitHead(cur)}
    <div class="ins-card">
      <div class="ins-story-head">
        <b class="ins-story-tag">${escapeHtml(cur.tag)}</b>
        <span class="ins-state ${stateCls}">${escapeHtml(cur.state || "")}</span>
        <span class="muted">${cur.msg_count} 則 ・ ${escapeHtml(cur.first_date)} ～ ${escapeHtml(cur.last_date)}</span>
        <button class="ins-goto" data-ins-goto="rank" data-ins-tag="${escapeHtml(cur.tag_key)}">在排行榜看全部 →</button>
      </div>
      <div class="ins-summary">${insText(cur.summary)}</div>
      ${cur.sampled ? `<div class="muted" style="margin-top:6px">※ 訊息量較大,中段經抽樣後交給 AI(模型實際讀了 ${(cur.covered_ids || []).length} 則)。</div>` : ""}
    </div>
    <div class="section-title">時間線</div>
    <div class="ins-beats">${beats || insEmpty("沒有可用的時間線。")}</div>
    ${cur.outlook ? `<div class="section-title">後續觀察</div><div class="ins-card"><div class="ins-why">${insText(cur.outlook)}</div></div>` : ""}
    ${(cur.related_tags || []).length ? `<div class="section-title">相關標籤</div><div class="chips">${cur.related_tags.map(t => insTagChip(t)).join("")}</div>` : ""}
  `;

  return `<div class="day-layout story-layout">
            <div class="day-list">${rail}</div>
            <div>${body}</div>
          </div>`;
}


/* ---------------------------------------------------------------- 子頁:週/月報 */

function insRenderDigest() {
  const d = INS_DATA.digests || {};
  const list = (INS_digestMode === "weekly" ? d.weekly : d.monthly) || [];

  const modeTabs = `<div class="week-tabs">
      <button class="week-tab${INS_digestMode === "weekly" ? " active" : ""}" data-ins-dmode="weekly">週報<small>${(d.weekly || []).length} 期</small></button>
      <button class="week-tab${INS_digestMode === "monthly" ? " active" : ""}" data-ins-dmode="monthly">月報<small>${(d.monthly || []).length} 期</small></button>
    </div>`;

  if (!list.length) return modeTabs + insEmpty("這個期間的整理尚未產生。");

  const cur = list.find(x => x.id === INS_period) || list[0];
  const picker = `<div class="week-tabs">` + list.map(x =>
    `<button class="week-tab${x === cur ? " active" : ""}" data-ins-period="${escapeHtml(x.id)}">${escapeHtml(x.label || x.period)}<small>${x.msg_count} 則</small></button>`
  ).join("") + `</div>`;

  const tagTable = (rows, title, fmt) => {
    if (!rows || !rows.length) return "";
    return `<div class="ins-minitable"><div class="ins-minihead">${title}</div>`
         + rows.map(r => `<div class="ins-minirow">${insTagChip(r.tag)}<span class="muted">${escapeHtml(fmt(r))}</span></div>`).join("")
         + `</div>`;
  };

  const themes = (cur.themes || []).map(t =>
    `<div class="ins-card">
       <b>${insText(t.title)}</b>
       <div class="ins-detail">${insText(t.detail)}</div>
       ${(t.tags || []).length ? `<div class="chips">${t.tags.map(x => insTagChip(x)).join("")}</div>` : ""}
       ${insCite(t.msg_ids)}
     </div>`).join("");

  const events = (cur.key_events || []).map(e =>
    `<div class="ins-beat">
       <div class="ins-beat-date">${escapeHtml(e.date)}</div>
       <div class="ins-beat-title">${insText(e.title)}</div>
       ${insCite(e.msg_ids)}
     </div>`).join("");

  const jump = cur.period && cur.period.indexOf("-W") > 0
    ? `<button class="ins-goto" data-ins-week="${escapeHtml(cur.period)}">到週整理 →</button>` : "";

  return modeTabs + picker + insUnitHead(cur) + `
    <div class="ins-card">
      <div class="ins-story-head">
        <b>${escapeHtml(cur.label || cur.period)}</b>
        <span class="muted">${cur.msg_count} 則</span>${jump}
      </div>
      <div class="ins-summary">${insText(cur.one_liner)}</div>
    </div>
    <div class="ins-tables">
      ${tagTable(cur.top_tags, "熱門標籤", r => "×" + r.count)}
      ${tagTable(cur.new_tags, "新出現", r => "×" + r.count)}
      ${tagTable(cur.heat_up, "升溫", r => r.prev + " → " + r.cur)}
      ${tagTable(cur.heat_down, "降溫", r => r.prev + " → " + r.cur)}
    </div>
    ${themes ? `<div class="section-title">主軸</div>${themes}` : ""}
    ${events ? `<div class="section-title">重點事件</div><div class="ins-beats">${events}</div>` : ""}
  `;
}


/* ---------------------------------------------------------------- 子頁:主題地圖 */

function insRenderCluster() {
  const cl = INS_DATA.clusters;
  if (!cl || !(cl.groups || []).length) return insEmpty("主題地圖尚未產生。");

  const groups = cl.groups.map(g =>
    `<div class="ins-card cluster-group">
       <div class="ins-story-head">
         <b>${insText(g.name)}</b>
         <span class="muted">${g.total} 則 ・ ${(g.tags || []).length} 個標籤</span>
       </div>
       ${g.blurb ? `<div class="ins-why">${insText(g.blurb)}</div>` : ""}
       <div class="chips">${(g.tags || []).map(t => insTagChip(t.tag, ` ×${t.count}`)).join("")}</div>
     </div>`).join("");

  const rest = cl.unclustered || [];
  const restBlock = rest.length
    ? `<div class="section-title">未歸類<span class="fs-note"> · ${rest.length} 個(由程式以集合差集算出,保證窮盡)</span></div>
       <div class="chips">${rest.slice(0, 80).map(t => insTagChip(t.tag, ` ×${t.count}`)).join("")}</div>
       ${rest.length > 80 ? `<div class="muted" style="margin-top:6px">…另有 ${rest.length - 80} 個較少出現的標籤。</div>` : ""}`
    : "";

  return insUnitHead(cl)
    + `<div class="muted" style="margin-bottom:10px">門檻:出現 ${cl.tag_threshold} 次以上的標籤。分組由 AI 命名,次數由程式計算。</div>`
    + groups + restBlock;
}


/* ---------------------------------------------------------------- 主渲染 */

function renderInsights() {
  const el = document.getElementById("tab-insights");
  if (!el) return;

  const btn = document.querySelector('[data-tab="insights"]');

  // 降級①②:沒有 insights.js,或 schema 不符 → 隱藏頁籤,網站回到原樣
  if (!INS_DATA || INS_DATA.schema !== 1) {
    if (INS_DATA && INS_DATA.schema !== 1) {
      console.warn("[insights] schema 不符(預期 1,實際 " + INS_DATA.schema + "),忽略。");
    }
    if (btn) btn.hidden = true;
    el.innerHTML = "";
    return;
  }
  if (btn) btn.hidden = false;

  // memo:rerenderAll() 會被搜尋框每次按鍵觸發,不能讓這頁跟著整個重畫
  const key = [INS_sub, INS_story, INS_period, INS_digestMode, searchTerm, tagFilter].join("|");
  if (key === INS_lastKey) return;
  INS_lastKey = key;

  const nav = `<div class="week-tabs ins-subnav">` + INS_SUBS.map(([k, label]) =>
    `<button class="week-tab${INS_sub === k ? " active" : ""}" data-ins-sub="${k}">${label}</button>`
  ).join("") + `</div>`;

  // 降級③:資料過舊 → 照常顯示,但明說可能未涵蓋最新訊息
  let banner = "";
  const gen = Date.parse(INS_DATA.generated_at || "");
  if (gen) {
    const hrs = (Date.now() - gen) / 3600000;
    if (hrs > (INS_DATA.stale_after_hours || 48)) {
      banner = `<div class="ins-stale">AI 洞察最後更新於 ${escapeHtml(relTime(INS_DATA.generated_at))},`
             + `可能尚未涵蓋最新訊息(資料已到 ${escapeHtml(INS_DATA.data_through || "")})。</div>`;
    }
  }

  let body;
  if (INS_sub === "story") body = insRenderStory();
  else if (INS_sub === "digest") body = insRenderDigest();
  else if (INS_sub === "cluster") body = insRenderCluster();
  else body = insRenderRadar();

  el.innerHTML = nav + banner + body;
}


/* ---------------------------------------------------------------- 事件
 * 自己掛一個 listener,不動 app.js 既有的委派。
 * 而我們吐出的 [data-tag] chip 會被 app.js 既有委派的 setTag() 分支免費接住。
 */
document.addEventListener("click", e => {
  const sub = e.target.closest("[data-ins-sub]");
  if (sub) { INS_sub = sub.dataset.insSub; INS_lastKey = ""; renderInsights(); return; }

  const st = e.target.closest("[data-ins-story]");
  if (st) { INS_story = st.dataset.insStory; INS_lastKey = ""; renderInsights(); return; }

  const dm = e.target.closest("[data-ins-dmode]");
  if (dm) { INS_digestMode = dm.dataset.insDmode; INS_period = null; INS_lastKey = ""; renderInsights(); return; }

  const pd = e.target.closest("[data-ins-period]");
  if (pd) { INS_period = pd.dataset.insPeriod; INS_lastKey = ""; renderInsights(); return; }

  // 跳到排行榜並套用該標籤。
  // 屬性刻意叫 data-ins-tag 而非 data-tag:後者會被 app.js 的委派也接走,
  // 兩邊各跑一次 setTag(toggle 語意)就等於設了又取消,淨效果為零。
  const goto = e.target.closest("[data-ins-goto]");
  if (goto) {
    const t = goto.dataset.insTag;
    if (t && tagFilter !== t) setTag(t);     // setTag 內部會 rerenderAll
    switchTab(goto.dataset.insGoto);
    return;
  }

  // 跳到週整理的該週
  const wk = e.target.closest("[data-ins-week]");
  if (wk) {
    selectedWeek = wk.dataset.insWeek;
    renderWeek();
    switchTab("week");
    return;
  }

  // 展開引用的原始訊息
  const cite = e.target.closest(".ins-cite");
  if (cite) {
    const box = cite.nextElementSibling;
    if (!box) return;
    if (!box.hidden) { box.hidden = true; cite.textContent = cite.textContent.replace("▴", "▾"); return; }
    if (!box.innerHTML) {
      const ids = (cite.dataset.msgids || "").split(",").map(Number);
      // .filter(Boolean) 是對驗證漏網 id 的第二道防線
      box.innerHTML = ids.map(insMsgById).filter(Boolean).map(m => renderMsg(m, true)).join("")
                   || insEmpty("找不到對應訊息。");
    }
    box.hidden = false;
    cite.textContent = cite.textContent.replace("▾", "▴");
  }
});
