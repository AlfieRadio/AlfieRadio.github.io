// ===== 資料 =====
const DATA = window.TG_DATA || { channel: {}, messages: [] };
const MSGS = (DATA.messages || []).slice().sort((a, b) => a.id - b.id);

// 全部出現過的日期(由小到大)
const ALL_DATES = [...new Set(MSGS.map(m => m.local_date))].sort();
const MIN_DATE = ALL_DATES[0] || null;
const MAX_DATE = ALL_DATES[ALL_DATES.length - 1] || null;

// 全域日期範圍狀態(字串 YYYY-MM-DD;null = 不限)
let rangeFrom = null;
let rangeTo = null;

// ===== 小工具 =====
function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
function addDays(dateStr, n) {
  const d = new Date(dateStr + "T00:00:00");
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}
function inRange(m) {
  if (rangeFrom && m.local_date < rangeFrom) return false;
  if (rangeTo && m.local_date > rangeTo) return false;
  return true;
}
function filtered() { return MSGS.filter(inRange); }

// 把訊息渲染成一張卡片
function renderMsg(m) {
  const chips = (m.hashtags || []).map(t => `<span class="chip">${escapeHtml(t)}</span>`).join("");
  return `
    <div class="msg">
      <div class="msg-head">
        <span class="msg-time">${escapeHtml(m.local_date)} ${escapeHtml(m.local_time)}</span>
        <a class="msg-link" href="${escapeHtml(m.link)}" target="_blank" rel="noopener">在 Telegram 開啟 ↗</a>
      </div>
      <div class="msg-text">${escapeHtml(m.text)}</div>
      ${chips ? `<div class="chips">${chips}</div>` : ""}
    </div>`;
}

// 計算 hashtag 排行(同一則訊息重複 tag 只算一次,大小寫視為相同)
function computeRanking(msgs) {
  const map = new Map();
  for (const m of msgs) {
    const seen = new Set();
    for (const t of (m.hashtags || [])) {
      const key = t.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      if (!map.has(key)) map.set(key, { display: t, count: 0, msgs: [] });
      const e = map.get(key);
      e.count++;
      e.msgs.push(m);
    }
  }
  return [...map.values()].sort((a, b) => b.count - a.count || a.display.localeCompare(b.display));
}

// ===== 分頁① Hashtag 排行 =====
function renderRank() {
  const el = document.getElementById("tab-rank");
  const msgs = filtered();
  const rank = computeRanking(msgs);
  if (!rank.length) { el.innerHTML = `<div class="empty">這個範圍內沒有任何 hashtag。</div>`; return; }

  const max = rank[0].count;
  el.innerHTML = rank.map((r, i) => `
    <div class="rank-row" data-key="${i}">
      <div class="idx">${i + 1}</div>
      <div class="rank-tag">
        <span class="name">${escapeHtml(r.display)}</span>
        <div class="bar" style="width:${Math.max(2, (r.count / max) * 100)}%"></div>
      </div>
      <div class="rank-count"><b>${r.count}</b><br><span class="muted">則</span></div>
    </div>
    <div class="rank-detail" data-detail="${i}">
      ${r.msgs.slice().sort((a, b) => b.id - a.id).map(renderMsg).join("")}
    </div>
  `).join("");

  el.querySelectorAll(".rank-row").forEach(row => {
    row.addEventListener("click", () => row.classList.toggle("open"));
  });
}

// ===== 分頁② 歷史回顧(每日) =====
let selectedDay = null;
function renderDay() {
  const el = document.getElementById("tab-day");
  const msgs = filtered();
  const dates = [...new Set(msgs.map(m => m.local_date))].sort().reverse();
  if (!dates.length) { el.innerHTML = `<div class="empty">這個範圍內沒有訊息。</div>`; return; }

  if (!selectedDay || !dates.includes(selectedDay)) selectedDay = dates[0];

  const counts = {};
  for (const m of msgs) counts[m.local_date] = (counts[m.local_date] || 0) + 1;

  el.innerHTML = `
    <div class="day-picker">
      <span class="range-label">跳到日期 </span>
      <input type="date" id="day-input" value="${selectedDay}"
             min="${dates[dates.length - 1]}" max="${dates[0]}">
    </div>
    <div class="day-layout">
      <div class="day-list">
        ${dates.map(d => `
          <div class="day-item ${d === selectedDay ? "active" : ""}" data-date="${d}">
            <span>${d}</span><span class="cnt">${counts[d]} 則</span>
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

  renderDayContent(msgs.filter(m => m.local_date === selectedDay));
}
function renderDayContent(dayMsgs) {
  const box = document.getElementById("day-content");
  if (!dayMsgs.length) { box.innerHTML = `<div class="empty">這天沒有訊息。</div>`; return; }
  const rank = computeRanking(dayMsgs);
  const summary = rank.length
    ? `<div class="day-summary">${rank.map(r => `<span class="chip">${escapeHtml(r.display)} ×${r.count}</span>`).join("")}</div>`
    : "";
  box.innerHTML =
    `<div class="section-title">${selectedDay} ・ 共 ${dayMsgs.length} 則</div>` +
    summary +
    dayMsgs.slice().sort((a, b) => b.id - a.id).map(renderMsg).join("");
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
  const weeks = groupByWeek(filtered());
  if (!weeks.length) { el.innerHTML = `<div class="empty">這個範圍內沒有訊息。</div>`; return; }

  if (!selectedWeek || !weeks.find(w => w.week === selectedWeek)) selectedWeek = weeks[0].week;

  el.innerHTML = `
    <div class="week-tabs">
      ${weeks.map(w => `
        <button class="week-tab ${w.week === selectedWeek ? "active" : ""}" data-week="${w.week}">
          <span>${w.week}</span><small>${w.range}・${w.msgs.length} 則</small>
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
  const rankHtml = rank.length
    ? rank.map(r => `<span class="chip">${escapeHtml(r.display)} ×${r.count}</span>`).join("")
    : `<span class="muted">本週沒有 hashtag</span>`;

  // 依日期分組(該週內,新 → 舊)
  const byDate = {};
  for (const m of week.msgs) (byDate[m.local_date] ||= []).push(m);
  const days = Object.keys(byDate).sort().reverse();

  box.innerHTML =
    `<div class="section-title">${week.week}(${week.range})・共 ${week.msgs.length} 則</div>
     <div class="day-summary">${rankHtml}</div>` +
    days.map(d =>
      `<div class="day-group-title">${d}（${byDate[d].length} 則）</div>` +
      byDate[d].slice().sort((a, b) => b.id - a.id).map(renderMsg).join("")
    ).join("");
}

// ===== 頂部總覽 + 範圍控制 =====
function renderOverview() {
  const pageTitle = (DATA.channel && DATA.channel.title) ? `${DATA.channel.title}頻道統計` : "頻道統計";
  document.getElementById("channel-title").textContent = pageTitle;
  document.title = pageTitle;
  if (DATA.fetched_at) {
    document.getElementById("fetched-at").textContent =
      "更新於 " + DATA.fetched_at.replace("T", " ").slice(0, 16);
  }
  const msgs = filtered();
  const tagCount = computeRanking(msgs).length;
  document.getElementById("overview").innerHTML = `
    <div class="stat"><b>${msgs.length}</b><span>訊息(範圍內)</span></div>
    <div class="stat"><b>${tagCount}</b><span>不同 hashtag</span></div>
    <div class="stat"><b>${MSGS.length}</b><span>累積總訊息</span></div>
    <div class="stat"><b>${ALL_DATES.length}</b><span>累積天數</span></div>`;
  const rc = document.getElementById("range-count");
  rc.textContent = (rangeFrom || rangeTo)
    ? `${rangeFrom || "最早"} ~ ${rangeTo || "最新"}` : "全部";
}

function applyRangeInputs() {
  document.getElementById("range-from").value = rangeFrom || "";
  document.getElementById("range-to").value = rangeTo || "";
}
function rerenderAll() {
  renderOverview();
  renderRank();
  renderDay();
  renderWeek();
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

  // 預設:近 30 天
  if (MAX_DATE) { rangeFrom = addDays(MAX_DATE, -29); rangeTo = MAX_DATE; }
  applyRangeInputs();
}

// ===== 分頁切換 =====
document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById("tab-" + tab.dataset.tab).classList.add("active");
  });
});

// ===== 啟動 =====
initRange();
rerenderAll();
