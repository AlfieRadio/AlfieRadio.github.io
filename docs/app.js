// ===== 資料 =====
// 資料以「按月分片」發布(見 publish.py):manifest.js 只有清單,
// 每個月一支 shards/YYYY-MM.js。先載最新一片就能開始用,其餘在背景補。
// 舊月份內容永不變、網址帶內容雜湊 → 瀏覽器可永久快取。
const MANIFEST = window.TG_MANIFEST || null;
const DATA = MANIFEST
  ? { channel: MANIFEST.channel || {}, tz: MANIFEST.tz, fetched_at: MANIFEST.fetched_at }
  : (window.TG_DATA || { channel: {}, messages: [] });
const TZ = DATA.tz || "Asia/Taipei";
const LINK_BASE = (DATA.channel && DATA.channel.link_base) || "";

let MSGS = [];
let ALL_DATES = [];
let MIN_DATE = null;
let MAX_DATE = null;

// ---- 瘦身欄位的還原 ----
// publish.py 刪掉了前端可以自行推導的欄位(link / iso_week / week_range /
// preview.domain;date_utc 前端根本沒用到),實測省 33.5% 的體積。
// **iso_week / week_range 必須與 fetch.py::serialize() 逐則完全相同**,
// 否則週分頁會分錯組。verify_rehydrate.js 會拿 data/messages.json 全量比對。
function _dayNum(y, m, d) {
  // 一律用 Date.UTC 做日期算術:它不受瀏覽器所在時區的日光節約影響。
  return Math.floor(Date.UTC(y, m - 1, d) / 86400000);
}
function isoWeekOf(dateStr) {
  const [y, m, d] = dateStr.split("-").map(Number);
  const n = _dayNum(y, m, d);
  const dow = (n + 3) % 7;              // 1970-01-01 是週四 → 0=週一
  const mon = n - dow, thu = n - dow + 3, sun = n - dow + 6;
  const thuD = new Date(thu * 86400000);
  const isoYear = thuD.getUTCFullYear();
  const week = Math.floor((thu - _dayNum(isoYear, 1, 1)) / 7) + 1;
  const monD = new Date(mon * 86400000), sunD = new Date(sun * 86400000);
  return {
    iso_week: `${isoYear}-W${String(week).padStart(2, "0")}`,
    // 破折號是 U+2013,與 fetch.py 一致
    week_range: `${monD.getUTCMonth() + 1}/${monD.getUTCDate()}–${sunD.getUTCMonth() + 1}/${sunD.getUTCDate()}`,
  };
}
function rehydrate(m) {
  const w = isoWeekOf(m.local_date);
  m.iso_week = w.iso_week;
  m.week_range = w.week_range;
  m.link = `${LINK_BASE}/${m.id}`;
  const pv = m.preview;
  if (pv && pv.url && !pv.domain) {
    try { pv.domain = new URL(pv.url).hostname.replace(/^www\./, ""); }
    catch (e) { pv.domain = ""; }
  }
  return m;
}

// 分片載入後統一重算衍生狀態
const SHARDS = new Map();          // month -> messages(已還原)
window.TG_SHARD = function (month, items) {
  SHARDS.set(month, items.map(rehydrate));
};
function ingestShards() {
  MSGS = [].concat(...[...SHARDS.keys()].sort().map(k => SHARDS.get(k)))
           .sort((a, b) => a.id - b.id);
  ALL_DATES = [...new Set(MSGS.map(m => m.local_date))].sort();
  // 日期邊界取自 manifest 而非「已載入的資料」—— 否則歷史分片還沒載進來時,
  // 日期選擇器的 min 會卡在當月,使用者連想選舊日期都選不到。
  const mm = MANIFEST && MANIFEST.months;
  MIN_DATE = (mm && mm.length) ? mm[0].from : (ALL_DATES[0] || null);
  MAX_DATE = (mm && mm.length) ? mm[mm.length - 1].to : (ALL_DATES[ALL_DATES.length - 1] || null);
  if (typeof INS_byId !== "undefined") INS_byId = null;   // 洞察頁的 id 索引要重建
}

function addDays(dateStr, n) {
  const d = new Date(dateStr + "T00:00:00");
  d.setDate(d.getDate() + n);
  // 用本地日期欄位組字串,避免 toISOString() 轉 UTC 在 +8 時區倒退一天
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}
// 以頻道時區計算「今天 / 昨天」
const TODAY = new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).format(new Date());
const YESTERDAY = addDays(TODAY, -1);

// ===== 全域篩選狀態 =====
let rangeFrom = null;
let rangeTo = null;
let searchTerm = "";    // 全文搜尋(小寫)
let tagFilter = null;   // 單一 hashtag 篩選(小寫;null = 不限)

// ===== 小工具 =====
function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
function escapeRegExp(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
// 安全反白:先 escape 再包 <mark>
function highlight(text) {
  const esc = escapeHtml(text);
  if (!searchTerm) return esc;
  return esc.replace(new RegExp("(" + escapeRegExp(escapeHtml(searchTerm)) + ")", "gi"), "<mark>$1</mark>");
}
const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];
function fmtDate(dateStr) {
  const wd = WEEKDAYS[new Date(dateStr + "T00:00:00").getDay()];
  let tag = "";
  if (dateStr === TODAY) tag = " · 今天";
  else if (dateStr === YESTERDAY) tag = " · 昨天";
  return `${dateStr}(${wd})${tag}`;
}
function relTime(iso) {
  const then = new Date(iso).getTime();
  if (isNaN(then)) return "";
  const m = Math.round((Date.now() - then) / 60000);
  if (m < 1) return "剛剛";
  if (m < 60) return `${m} 分鐘前`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} 小時前`;
  return `${Math.round(h / 24)} 天前`;
}

// ===== 篩選 =====
function inRange(m) {
  if (rangeFrom && m.local_date < rangeFrom) return false;
  if (rangeTo && m.local_date > rangeTo) return false;
  return true;
}
function matchesSearch(m) {
  if (!searchTerm) return true;
  if ((m.text || "").toLowerCase().includes(searchTerm)) return true;
  if ((m.hashtags || []).some(t => t.toLowerCase().includes(searchTerm))) return true;
  // 純網址訊息靠預覽卡顯示新聞標題/摘要,搜尋也要涵蓋,否則「看得到、搜不到」
  const pv = m.preview;
  if (!pv) return false;
  return (pv.title || "").toLowerCase().includes(searchTerm) ||
         (pv.desc || "").toLowerCase().includes(searchTerm);
}
function hasTag(m) {
  if (!tagFilter) return true;
  return (m.hashtags || []).some(t => t.toLowerCase() === tagFilter);
}
// 結構用(排行榜):只套「範圍 + 搜尋」,不套 tag —— 排行榜是「範圍控制的工具」
function baseFiltered() { return MSGS.filter(m => inRange(m) && matchesSearch(m)); }
// 每日/週/總覽用:選了 hashtag 就「跨全部時間」(無視日期範圍),否則照範圍
function scopeFiltered() {
  if (tagFilter) return MSGS.filter(m => matchesSearch(m) && hasTag(m));
  return MSGS.filter(m => inRange(m) && matchesSearch(m));
}

// ===== #重要 =====
const IMPORTANT_TAG = "重要";
function isImportant(m) {
  return (m.hashtags || []).some(t => t.replace(/^#/, "").toLowerCase() === IMPORTANT_TAG);
}

// ===== 連結預覽卡(Telegram 已 unfurl 的標題/摘要/網域)=====
function renderPreview(pv) {
  if (!pv || !(pv.title || pv.desc)) return "";
  const href = pv.url || "";
  const domain = pv.domain || pv.site || "";
  const fav = domain
    ? `<img class="pv-fav" src="https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64" alt="" loading="lazy" onerror="this.remove()">`
    : "";
  const meta = pv.site || domain;
  return `
    <a class="preview" href="${escapeHtml(href)}" target="_blank" rel="noopener">
      <div class="pv-body">
        ${meta ? `<div class="pv-domain">${fav}${escapeHtml(meta)}</div>` : ""}
        ${pv.title ? `<div class="pv-title">${highlight(pv.title)}</div>` : ""}
        ${pv.desc ? `<div class="pv-desc">${highlight(pv.desc)}</div>` : ""}
      </div>
    </a>`;
}

// ===== 訊息卡片 =====
const LONG_TEXT = 240;
function renderMsg(m, showDate = true) {
  const tagChips = (m.hashtags || []).map(t =>
    `<span class="chip filter-chip${tagFilter === t.toLowerCase() ? " active" : ""}" data-tag="${t.toLowerCase()}">${escapeHtml(t)}</span>`
  ).join("");
  const when = showDate ? `${m.local_date} ${m.local_time}` : m.local_time;
  const text = m.text || "";
  const long = text.length > LONG_TEXT;
  const body = highlight(text);
  const textBlock = long
    ? `<div class="msg-text clamp">${body}</div><button class="msg-toggle" type="button">展開全文 ▾</button>`
    : `<div class="msg-text">${body}</div>`;
  const imp = isImportant(m);
  return `
    <div class="msg${imp ? " important" : ""}">
      <div class="msg-head">
        <span class="msg-time">${escapeHtml(when)}</span>
        ${imp ? `<span class="imp-badge">⭐ 重要</span>` : ""}
        <a class="msg-link" href="${escapeHtml(m.link)}" target="_blank" rel="noopener">在 Telegram 開啟 ↗</a>
      </div>
      ${textBlock}
      ${renderPreview(m.preview)}
      ${tagChips ? `<div class="chips">${tagChips}</div>` : ""}
    </div>`;
}

// ===== 排行計算(同一則重複 tag 只算一次,大小寫視為相同)=====
function computeRanking(msgs) {
  const map = new Map();
  for (const m of msgs) {
    const seen = new Set();
    for (const t of (m.hashtags || [])) {
      const key = t.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      if (!map.has(key)) map.set(key, { display: t, count: 0, msgs: [] });
      const e = map.get(key); e.count++; e.msgs.push(m);
    }
  }
  return [...map.values()].sort((a, b) => b.count - a.count || a.display.localeCompare(b.display));
}

// 可點的 hashtag 小標籤(每日/週的摘要)
function summaryChips(rank) {
  return `<span class="chip filter-chip${tagFilter ? "" : " active"}" data-tag="">全部</span>` +
    rank.map(r => `<span class="chip filter-chip${tagFilter === r.display.toLowerCase() ? " active" : ""}" data-tag="${r.display.toLowerCase()}">${escapeHtml(r.display)} ×${r.count}</span>`).join("");
}

// ===== 設定篩選 =====
function setTag(tag) {
  const t = (tag || "").toLowerCase();
  tagFilter = (tagFilter === t) ? null : (t || null);
  rerenderAll();
  // 標籤篩選的語意是「跨全部時間」,所以要確保歷史分片都在
  if (tagFilter) afterLoad(ensureAllShards());
}
function rerenderAll() {
  renderOverview();
  renderFilterStatus();
  syncRangeBarState();
  renderDay();
  renderRank();
  renderWeek();
  renderImportant();
  renderInsightsIfReady();
}
// 頁籤⑤(AI 洞察)由 insights-ui.js 提供,而它是延遲載入的 ——
// 還沒載進來(或根本沒有那支檔案)時,整個功能靜默不存在。
function renderInsightsIfReady() {
  if (typeof renderInsights === "function") renderInsights();
}

// 選了 hashtag → 跨全部時間,日期範圍此刻不作用 → 視覺上暫停日期控制列
function syncRangeBarState() {
  const bar = document.querySelector(".range-bar");
  if (!bar) return;
  // 兩種情況下日期範圍不作用,都要變暗:
  //   ① 選了 hashtag → 跨全部時間
  //   ② 在「AI 洞察」頁 → 內容是每日預先產生的固定期間,不受範圍影響
  const onInsights = document.querySelector('.tab.active')?.dataset.tab === "insights";
  bar.classList.toggle("suspended", !!tagFilter || onInsights);
}

// ===== 篩選狀態列 =====
function renderFilterStatus() {
  const parts = [];
  if (searchTerm) parts.push(`<span class="fs-chip">🔍 ${escapeHtml(searchTerm)} <button class="fs-x" data-clear="search">✕</button></span>`);
  if (tagFilter) parts.push(`<span class="fs-chip">🏷 ${escapeHtml(tagFilter)} <span class="fs-note">· 跨全部時間</span> <button class="fs-x" data-clear="tag">✕</button></span>`);
  document.getElementById("filter-status").innerHTML = parts.length
    ? `<span class="muted">篩選中:</span> ${parts.join(" ")} <button class="fs-clear" data-clear="all">清除全部</button>`
    : "";
}

// ===== 分頁① 每日內容(歷史回顧)=====
let selectedDay = null;
function renderDay() {
  const el = document.getElementById("tab-day");
  const base = scopeFiltered();
  const dates = [...new Set(base.map(m => m.local_date))].sort().reverse();
  if (!dates.length) { el.innerHTML = `<div class="empty">沒有符合的訊息。</div>`; return; }
  if (!selectedDay || !dates.includes(selectedDay)) selectedDay = dates[0];

  const counts = {};
  for (const m of base) counts[m.local_date] = (counts[m.local_date] || 0) + 1;

  el.innerHTML = `
    <div class="day-picker">
      <span class="range-label">跳到日期 </span>
      <input type="date" id="day-input" value="${selectedDay}" min="${dates[dates.length - 1]}" max="${dates[0]}">
    </div>
    <div class="day-layout">
      <div class="day-list">
        ${dates.map(d => `
          <div class="day-item ${d === selectedDay ? "active" : ""}" data-date="${d}">
            <span>${fmtDate(d)}</span><span class="cnt">${counts[d]}</span>
          </div>`).join("")}
      </div>
      <div id="day-content"></div>
    </div>`;

  el.querySelector("#day-input").addEventListener("change", e => {
    if (e.target.value) { selectedDay = e.target.value; renderDay(); }
  });
  el.querySelectorAll(".day-item").forEach(it => {
    it.addEventListener("click", () => { selectedDay = it.dataset.date; renderDay(); });
  });

  // 手機版:日期清單是橫向膠囊條,把選中的捲進可見範圍(不動到頁面垂直捲動)
  const activeItem = el.querySelector(".day-item.active");
  if (activeItem) activeItem.scrollIntoView({ block: "nearest", inline: "center" });

  renderDayContent(base.filter(m => m.local_date === selectedDay));
}
function renderDayContent(dayMsgs) {
  const box = document.getElementById("day-content");
  if (!dayMsgs.length) { box.innerHTML = `<div class="empty">這天沒有符合的訊息。</div>`; return; }
  const rank = computeRanking(dayMsgs);
  const shown = tagFilter ? dayMsgs.filter(hasTag) : dayMsgs;

  box.innerHTML =
    `<div class="section-title">${fmtDate(selectedDay)} ・ 共 ${shown.length} 則${tagFilter ? `(${escapeHtml(tagFilter)},跨全部時間)` : ""}</div>` +
    `<div class="day-summary">${summaryChips(rank)}</div>` +
    (shown.length
      ? shown.slice().sort((a, b) => b.id - a.id).map(m => renderMsg(m, false)).join("")
      : `<div class="empty">這天沒有「${escapeHtml(tagFilter)}」的訊息。</div>`);
}

// ===== 分頁② Hashtag 排行 =====
function renderRank() {
  const el = document.getElementById("tab-rank");
  const rank = computeRanking(baseFiltered());
  if (!rank.length) { el.innerHTML = `<div class="empty">沒有符合的 hashtag。</div>`; return; }
  const max = rank[0].count;
  el.innerHTML = `<div class="rank-hint muted">點任一 hashtag 即可篩選(每日／週整理會同步)</div>` +
    rank.map((r, i) => {
      const active = tagFilter === r.display.toLowerCase();
      // 展開明細跨全部時間(不受上方日期範圍限制),呼應「點標籤 = 跨月看某公司」
      const allMsgs = active
        ? MSGS.filter(m => matchesSearch(m) && (m.hashtags || []).some(t => t.toLowerCase() === r.display.toLowerCase()))
        : [];
      const detail = active
        ? `<div class="rank-detail">
             <div class="section-title">跨全部時間共 ${allMsgs.length} 則</div>
             ${allMsgs.sort((a, b) => b.id - a.id).map(m => renderMsg(m, true)).join("")}
           </div>`
        : "";
      return `
        <div class="rank-row${active ? " active" : ""}" data-tag="${r.display.toLowerCase()}">
          <div class="idx">${i + 1}</div>
          <div class="rank-tag">
            <span class="name">${escapeHtml(r.display)}</span>
            <div class="bar" style="width:${Math.max(2, (r.count / max) * 100)}%"></div>
          </div>
          <div class="rank-count"><b>${r.count}</b><br><span class="muted">則</span></div>
        </div>${detail}`;
    }).join("");
}

// ===== 分頁③ 週整理 =====
let selectedWeek = null;
function groupByWeek(msgs) {
  const map = new Map();
  for (const m of msgs) {
    if (!map.has(m.iso_week)) map.set(m.iso_week, { week: m.iso_week, range: m.week_range, msgs: [] });
    map.get(m.iso_week).msgs.push(m);
  }
  return [...map.values()].sort((a, b) => (a.week < b.week ? 1 : -1)); // 新 → 舊
}
function renderWeek() {
  const el = document.getElementById("tab-week");
  const weeks = groupByWeek(scopeFiltered());
  if (!weeks.length) { el.innerHTML = `<div class="empty">沒有符合的訊息。</div>`; return; }
  if (!selectedWeek || !weeks.find(w => w.week === selectedWeek)) selectedWeek = weeks[0].week;

  el.innerHTML = `
    <div class="week-tabs">
      ${weeks.map(w => `
        <button class="week-tab ${w.week === selectedWeek ? "active" : ""}" data-week="${w.week}">
          <span>${w.week}</span><small>${w.range} ・ ${w.msgs.length} 則</small>
        </button>`).join("")}
    </div>
    <div id="week-content"></div>`;

  el.querySelectorAll(".week-tab").forEach(b => {
    b.addEventListener("click", () => { selectedWeek = b.dataset.week; renderWeek(); });
  });
  renderWeekContent(weeks.find(w => w.week === selectedWeek));
}
function renderWeekContent(week) {
  const box = document.getElementById("week-content");
  const rank = computeRanking(week.msgs);
  const shown = tagFilter ? week.msgs.filter(hasTag) : week.msgs;

  const byDate = {};
  for (const m of shown) (byDate[m.local_date] ||= []).push(m);
  const days = Object.keys(byDate).sort().reverse();

  box.innerHTML =
    `<div class="section-title">${week.week}(${week.range})・共 ${shown.length} 則${tagFilter ? `(${escapeHtml(tagFilter)},跨全部時間)` : ""}</div>
     <div class="day-summary">${rank.length ? summaryChips(rank) : `<span class="muted">本週沒有 hashtag</span>`}</div>` +
    (days.length
      ? days.map(d =>
          `<div class="day-group-title">${fmtDate(d)}（${byDate[d].length} 則）</div>` +
          byDate[d].slice().sort((a, b) => b.id - a.id).map(m => renderMsg(m, false)).join("")
        ).join("")
      : `<div class="empty">本週沒有「${escapeHtml(tagFilter)}」的訊息。</div>`);
}

// ===== 分頁④ 重要(#重要 精選,跨全部時間)=====
function renderImportant() {
  const el = document.getElementById("tab-important");
  if (!el) return;
  // 自己的固定範疇:所有 #重要、跨全部時間,僅受搜尋 narrow(不受日期範圍/標籤影響)
  const msgs = MSGS.filter(m => isImportant(m) && matchesSearch(m)).sort((a, b) => b.id - a.id);
  if (!msgs.length) {
    el.innerHTML = `<div class="empty">${searchTerm ? "沒有符合搜尋的 #重要 訊息。" : "目前沒有 #重要 訊息。"}</div>`;
    return;
  }
  const byDate = {};
  for (const m of msgs) (byDate[m.local_date] ||= []).push(m);
  const days = Object.keys(byDate).sort().reverse();
  el.innerHTML =
    `<div class="section-title">⭐ #重要 精選 ・ 跨全部時間共 ${msgs.length} 則</div>` +
    days.map(d =>
      `<div class="day-group-title">${fmtDate(d)}（${byDate[d].length} 則）</div>` +
      byDate[d].map(m => renderMsg(m, false)).join("")
    ).join("");
}

// ===== 頂部總覽 =====
function renderOverview() {
  const pageTitle = (DATA.channel && DATA.channel.title) ? `${DATA.channel.title}頻道統計` : "頻道統計";
  document.getElementById("channel-title").textContent = pageTitle;
  document.title = pageTitle;

  const fa = document.getElementById("fetched-at");
  if (DATA.fetched_at) {
    // 抓取管線曾經靜默壞掉 12 小時:排程回報成功、但 fetch 那一步被 cmd 吞掉。
    // 當時「更新於 12 小時前」是灰色小字,看不出異常。3 小時當門檻 ——
    // 正常每小時跑一次,連續三次都沒成功就一定有問題。
    const stale = (Date.now() - new Date(DATA.fetched_at).getTime()) >= 3 * 3600e3;
    fa.textContent = (stale ? "⚠ 資料停更 " : "更新於 ") + relTime(DATA.fetched_at);
    fa.classList.toggle("stale-warn", stale);
    fa.title = DATA.fetched_at.replace("T", " ").slice(0, 16);
  }

  const base = scopeFiltered();
  const todayCount = MSGS.filter(m => m.local_date === TODAY).length;
  document.getElementById("overview").innerHTML = `
    <div class="stat"><b>${todayCount}</b><span>今日則數</span></div>
    <div class="stat"><b>${base.length}</b><span>符合條件</span></div>
    <div class="stat"><b>${computeRanking(base).length}</b><span>不同 hashtag</span></div>
    <div class="stat"><b>${MSGS.length}</b><span>累積總訊息</span></div>`;
  const rc = document.getElementById("range-count");
  rc.textContent = (rangeFrom || rangeTo) ? `${rangeFrom || "最早"} ~ ${rangeTo || "最新"}` : "全部";
}

// ===== 範圍控制 =====
function applyRangeInputs() {
  document.getElementById("range-from").value = rangeFrom || "";
  document.getElementById("range-to").value = rangeTo || "";
}
function initRange() {
  const from = document.getElementById("range-from");
  const to = document.getElementById("range-to");
  if (MIN_DATE) { from.min = MIN_DATE; from.max = MAX_DATE; to.min = MIN_DATE; to.max = MAX_DATE; }

  from.addEventListener("change", () => {
    rangeFrom = from.value || null;
    document.querySelectorAll(".quick button").forEach(b => b.classList.remove("active"));
    rerenderAll();
    afterLoad(ensureRangeShards());
  });
  to.addEventListener("change", () => {
    rangeTo = to.value || null;
    document.querySelectorAll(".quick button").forEach(b => b.classList.remove("active"));
    rerenderAll();
    afterLoad(ensureRangeShards());
  });
  document.querySelectorAll(".quick button").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".quick button").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const days = parseInt(btn.dataset.days, 10);
      if (days === 0 || !MAX_DATE) { rangeFrom = null; rangeTo = null; }
      else { rangeFrom = addDays(MAX_DATE, -(days - 1)); rangeTo = MAX_DATE; }
      applyRangeInputs();
      rerenderAll();
      // 「全部」= 沒有範圍界線 → 需要全量;其餘只補該範圍涵蓋的月份
      afterLoad(days === 0 ? ensureAllShards() : ensureRangeShards());
    });
  });

  if (MAX_DATE) { rangeFrom = addDays(MAX_DATE, -29); rangeTo = MAX_DATE; }
  applyRangeInputs();
}

// ===== 搜尋 =====
function initSearch() {
  const input = document.getElementById("search-input");
  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      searchTerm = input.value.trim().toLowerCase();
      rerenderAll();
    }, 150);
  });
}

// ===== 分頁切換 =====
function switchTab(name) {
  if (name === "insights") {
    // 頁籤⑤ 的資產有 300KB,而且它的引用可能指向任何月份
    ensureInsightsAssets().then(() => {
      if (typeof renderInsights !== "function") {
        // insights.js 不存在(首次部署、或 AI 從沒成功過)→ 這個頁籤不該存在。
        // 原本是在 insights-ui.js 載入時就隱藏,現在它延遲載入了,改在這裡收尾。
        const btn = document.querySelector('[data-tab="insights"]');
        if (btn) btn.hidden = true;
        switchTab("day");
        return;
      }
      renderInsightsIfReady();
      afterLoad(ensureAllShards());
    });
  }
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
  document.getElementById("tab-" + name).classList.add("active");
  syncRangeBarState();   // 切到/離開 AI 洞察頁時,日期範圍列要跟著變暗/恢復
}
document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => switchTab(tab.dataset.tab));
});

// ===== 全域委派:清除篩選、展開全文、hashtag 篩選 =====
document.addEventListener("click", e => {
  const clearBtn = e.target.closest("[data-clear]");
  if (clearBtn) {
    const what = clearBtn.dataset.clear;
    if (what === "search" || what === "all") { searchTerm = ""; document.getElementById("search-input").value = ""; }
    if (what === "tag" || what === "all") { tagFilter = null; }
    rerenderAll();
    return;
  }
  const toggle = e.target.closest(".msg-toggle");
  if (toggle) {
    const txt = toggle.previousElementSibling;
    if (txt && txt.classList.contains("msg-text")) {
      const collapsed = txt.classList.toggle("clamp");
      toggle.textContent = collapsed ? "展開全文 ▾" : "收合 ▴";
    }
    return;
  }
  const tagEl = e.target.closest("[data-tag]");
  if (tagEl) { setTag(tagEl.dataset.tag); }
});

// ===== 啟動 =====
function loadShard(rec) {
  return new Promise(resolve => {
    const s = document.createElement("script");
    // 網址帶內容雜湊:舊月份內容不變 → 網址不變 → 瀏覽器快取一直有效
    s.src = `shards/${rec.m}.js?v=${rec.h}`;
    s.onload = () => resolve(true);
    s.onerror = () => { console.warn("[shard] 載入失敗:" + rec.m); resolve(false); };
    document.head.appendChild(s);
  });
}
function loadNote(text) {
  let el = document.getElementById("load-note");
  if (!el) {
    const host = document.getElementById("fetched-at");
    if (!host) return;
    el = document.createElement("span");
    el.id = "load-note";
    el.className = "muted";
    host.after(el);
  }
  el.textContent = text ? " ・ " + text : "";
}

// ---- 分片按需載入 ----
// 一年的歷史合計約 2MB。**不要**在開站時全部抓下來:預設範圍只有 30 天,
// 抓回來的十幾片當下一則都用不到,卻會把慢速連線的頻寬吃光,
// 讓頁面「看起來還在轉」。改成用到才載,並在載入時給提示。
function shardsForRange(from, to) {
  if (!MANIFEST) return [];
  return MANIFEST.months.filter(r => (!from || r.to >= from) && (!to || r.from <= to));
}
let LOAD_CHAIN = Promise.resolve();
function ensureShards(recs, label) {
  const todo = recs.filter(r => !SHARDS.has(r.m));
  if (!todo.length) return Promise.resolve(false);
  // 串成一條鏈:使用者連續切範圍時不會有兩批載入互相覆寫 ingest 結果
  LOAD_CHAIN = LOAD_CHAIN.then(async () => {
    const need = todo.filter(r => !SHARDS.has(r.m));
    if (!need.length) return false;
    let done = 0;
    loadNote(`${label} 0/${need.length}`);
    await Promise.all(need.map(r => loadShard(r).then(() => {
      done += 1;
      loadNote(done < need.length ? `${label} ${done}/${need.length}` : "");
    })));
    ingestShards();
    loadNote("");
    return true;
  });
  return LOAD_CHAIN;
}
function ensureRangeShards() {
  return ensureShards(shardsForRange(rangeFrom, rangeTo), "載入資料…");
}
// 跨全部時間的操作(標籤篩選、AI 洞察的引用)需要全量
function ensureAllShards() {
  return ensureShards(MANIFEST ? MANIFEST.months : [], "載入全部歷史…");
}
function afterLoad(p) { p.then(changed => { if (changed) rerenderAll(); }); }

// ---- 頁籤⑤ 的資產延遲載入 ----
// insights.js 有 300KB,而它只有頁籤⑤ 用得到。放在 <head> 會擋住 app.js,
// 是首屏第二大的阻塞來源。改成第一次點開頁籤⑤ 才載。
let INSIGHTS_READY = null;
function lazyAsset(name) {
  const el = document.querySelector(`[data-lazy="${name}"]`);
  return (el && el.getAttribute("data-lazy-src")) || name;
}
function ensureInsightsAssets() {
  if (INSIGHTS_READY) return INSIGHTS_READY;
  INSIGHTS_READY = (async () => {
    // 順序不可換:insights-ui.js 頂層就讀 window.TG_INSIGHTS
    await loadScriptUrl(lazyAsset("insights.js"));
    await loadScriptUrl(lazyAsset("insights-ui.js"));
  })();
  return INSIGHTS_READY;
}
function loadScriptUrl(src) {
  return new Promise(resolve => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = () => resolve(true);
    s.onerror = () => { console.warn("[lazy] 載入失敗:" + src); resolve(false); };
    document.head.appendChild(s);
  });
}

async function boot() {
  if (!MANIFEST) {
    // 沒有 manifest(例如直接開舊版單檔 data.js)→ 沿用原本的整包載入
    SHARDS.set("all", ((window.TG_DATA || {}).messages || []).map(m => m));
    ingestShards();
    initRange(); initSearch(); rerenderAll();
    return;
  }

  // 新 → 舊。先把最新那一片載進來,頁面就能用了。
  const months = MANIFEST.months.slice().sort((a, b) => (a.m < b.m ? 1 : -1));
  if (months.length) await loadShard(months[0]);
  ingestShards();
  initRange();
  initSearch();
  rerenderAll();

  // 預設範圍是最近 30 天,跨月時還需要前一個月那一片 —— 只補這些,不多抓。
  afterLoad(ensureRangeShards());
}

boot();
