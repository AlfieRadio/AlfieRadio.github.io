// ===== 資料 =====
const DATA = window.TG_DATA || { channel: {}, messages: [] };
const MSGS = (DATA.messages || []).slice().sort((a, b) => a.id - b.id);
const TZ = DATA.tz || "Asia/Taipei";

const ALL_DATES = [...new Set(MSGS.map(m => m.local_date))].sort();
const MIN_DATE = ALL_DATES[0] || null;
const MAX_DATE = ALL_DATES[ALL_DATES.length - 1] || null;

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
// 結構用(日期清單/週清單/排行):只套「範圍 + 搜尋」,不套 tag
function baseFiltered() { return MSGS.filter(m => inRange(m) && matchesSearch(m)); }

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
  return `
    <div class="msg">
      <div class="msg-head">
        <span class="msg-time">${escapeHtml(when)}</span>
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
}
function rerenderAll() {
  renderOverview();
  renderFilterStatus();
  renderDay();
  renderRank();
  renderWeek();
}

// ===== 篩選狀態列 =====
function renderFilterStatus() {
  const parts = [];
  if (searchTerm) parts.push(`<span class="fs-chip">🔍 ${escapeHtml(searchTerm)} <button class="fs-x" data-clear="search">✕</button></span>`);
  if (tagFilter) parts.push(`<span class="fs-chip">🏷 ${escapeHtml(tagFilter)} <button class="fs-x" data-clear="tag">✕</button></span>`);
  document.getElementById("filter-status").innerHTML = parts.length
    ? `<span class="muted">篩選中:</span> ${parts.join(" ")} <button class="fs-clear" data-clear="all">清除全部</button>`
    : "";
}

// ===== 分頁① 每日內容(歷史回顧)=====
let selectedDay = null;
function renderDay() {
  const el = document.getElementById("tab-day");
  const base = baseFiltered();
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
    `<div class="section-title">${fmtDate(selectedDay)} ・ 共 ${dayMsgs.length} 則${tagFilter ? `,篩選後 ${shown.length} 則` : ""}</div>` +
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
      const detail = active
        ? `<div class="rank-detail">${r.msgs.slice().sort((a, b) => b.id - a.id).map(m => renderMsg(m, true)).join("")}</div>`
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
  const weeks = groupByWeek(baseFiltered());
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
    `<div class="section-title">${week.week}(${week.range})・共 ${week.msgs.length} 則${tagFilter ? `,篩選後 ${shown.length} 則` : ""}</div>
     <div class="day-summary">${rank.length ? summaryChips(rank) : `<span class="muted">本週沒有 hashtag</span>`}</div>` +
    (days.length
      ? days.map(d =>
          `<div class="day-group-title">${fmtDate(d)}（${byDate[d].length} 則）</div>` +
          byDate[d].slice().sort((a, b) => b.id - a.id).map(m => renderMsg(m, false)).join("")
        ).join("")
      : `<div class="empty">本週沒有「${escapeHtml(tagFilter)}」的訊息。</div>`);
}

// ===== 頂部總覽 =====
function renderOverview() {
  const pageTitle = (DATA.channel && DATA.channel.title) ? `${DATA.channel.title}頻道統計` : "頻道統計";
  document.getElementById("channel-title").textContent = pageTitle;
  document.title = pageTitle;

  const fa = document.getElementById("fetched-at");
  if (DATA.fetched_at) {
    fa.textContent = "更新於 " + relTime(DATA.fetched_at);
    fa.title = DATA.fetched_at.replace("T", " ").slice(0, 16);
  }

  const base = baseFiltered();
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
  });
  to.addEventListener("change", () => {
    rangeTo = to.value || null;
    document.querySelectorAll(".quick button").forEach(b => b.classList.remove("active"));
    rerenderAll();
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
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
  document.getElementById("tab-" + name).classList.add("active");
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
initRange();
initSearch();
rerenderAll();
