const $ = s => document.querySelector(s);
const TARGETS = ["99", "120", "200m"];
const MODES = ["fastest", "easiest"];
const EFFORT = { afk: ["afk", "info"], low: ["low effort", ""], high: ["click-heavy", "warn"] };
const isMethods = () => MODES.includes(state.window);
const usesHours = () => state.window === "manual" || isMethods();
function hrs(h) {
  if (h == null) return "–";
  if (h < 1) return Math.max(1, Math.round(h * 60)) + " min";
  return nf.format(h < 10 ? Math.round(h * 10) / 10 : Math.round(h)) + " h";
}
const PERIODS = [["day", "Today"], ["week", "This week"], ["month", "This month"], ["year", "This year"]];
// RS3's own skill groupings.
const CATEGORY = Object.fromEntries([
  ["combat", "Attack Strength Defence Ranged Prayer Magic Hitpoints Summoning Necromancy"],
  ["gathering", "Mining Fishing Woodcutting Farming Hunter Divination Archaeology"],
  ["artisan", "Smithing Crafting Fletching Herblore Runecraft Cooking Construction Firemaking"],
  ["support", "Agility Thieving Slayer Dungeoneering"], ["elite", "Invention"],
].flatMap(([c, names]) => names.split(" ").map(n => [n, c])));
// Group colour for a skill id (Overall uses the page accent). Returns a #rrggbb hex.
function skillColor(sid) {
  const name = state.data?.skills.find(s => s.id === sid)?.name;
  return (sid && CATEGORY[name] ? css(`--c-${CATEGORY[name]}`) : css("--accent")) || "#1f6b63";
}
const LIST_MAX = 8;
const DIFFICULTY = { 0: "Novice", 1: "Intermediate", 2: "Experienced", 3: "Master", 4: "Grandmaster", 250: "Special" };
const EVENT_TAGS = [
  [/levelup|xp_milestone/, "Skill", "gold"], [/quest|qp_milestone/, "Quest", "info"],
  [/npc_defeat|npc_kill|pvp_kill/, "Kills", "warn"], [/item_drop|item_found|wof_drop/, "Loot", ""],
  [/finished_trail/, "Clue", "info"], [/dungeons/, "Dungeoneering", "gold"], [/music/, "Music", "info"],
];
const store = {
  get(k, d) { try { const v = localStorage.getItem("rsxp." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("rsxp." + k, JSON.stringify(v)); } catch {} },
};
const state = {
  pid: store.get("pid", null), target: store.get("target", "120"), window: store.get("window", "fastest"),
  period: store.get("period", "week"), sort: { key: "id", dir: 1 }, skill: 0, data: null, charts: {},
  activities: [], quests: null, questTab: "ready", training: {}, events: [], openLists: { quests: false, events: false },
};

// ---------- formatting ----------
const nf = new Intl.NumberFormat("en-GB");
function short(n) {
  if (n == null) return "–";
  const a = Math.abs(n);
  if (a >= 1e9) return (n / 1e9).toFixed(2) + "b";
  if (a >= 1e6) return (n / 1e6).toFixed(a >= 1e8 ? 0 : 1) + "m";
  if (a >= 1e4) return (n / 1e3).toFixed(0) + "k";
  return nf.format(Math.round(n));
}
function eta(days) {
  if (days === 0) return "done";
  if (days == null || !isFinite(days)) return "–";
  if (days >= 365 * 50) return "50+ yr";   // means "barely trained lately", not a real plan
  if (days >= 365) return (days / 365).toFixed(1) + " yr";
  if (days >= 61) return (days / 30.44).toFixed(1) + " mo";
  if (days >= 14) return (days / 7).toFixed(1) + " wk";
  if (days >= 1) return Math.round(days) + " d";
  return Math.max(1, Math.round(days * 24)) + " h";
}
function etaDate(days) {
  if (!days || !isFinite(days) || days > 365 * 200) return "";
  return new Date(Date.now() + days * 864e5).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
function ago(ts) {
  if (!ts) return "never";
  const s = Date.now() / 1000 - ts;
  if (s < 90) return "just now";
  if (s < 5400) return Math.round(s / 60) + " min ago";
  if (s < 172800) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " days ago";
}
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function monthLabel(ym, i) { // "2026-01" -> "Jan '26" on January and the first bar, else "Feb"
  const [y, m] = ym.split("-").map(Number);
  return m === 1 || i === 0 ? `${MONTHS[m - 1]} '${String(y).slice(2)}` : MONTHS[m - 1];
}
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const periodLabel = () => PERIODS.find(p => p[0] === state.period)[1];

// ---------- api ----------
const SERVER = "http://127.0.0.1:8765/";
// Demo mode: `python -m rsxp export-demo` sets window.RSXP_STATIC and writes the API's
// answers as JSON files, so the page runs on GitHub Pages with no server.
const STATIC = window.RSXP_STATIC || null;
const staticCache = {};
async function staticJson(name) {
  if (!staticCache[name]) staticCache[name] = fetch(`${STATIC.base}${name}.json`).then(r => {
    if (!r.ok) throw new Error(`Demo data file ${name}.json is missing.`);
    return r.json();
  });
  return structuredClone(await staticCache[name]);
}
// Mirrors training.multiplier() in Python: boosts scale every rate for a skill.
function applyBoosts(plan, q, cfg) {
  const list = k => (q.get(k) || "").split(",").filter(Boolean);
  const b = { avatar: Number(q.get("avatar")) || 0, outfits: list("outfits"), portables: list("portables"),
              double: q.get("double") === "true" };
  for (const s of plan.skills) {
    let mult = 1 + b.avatar / 100;
    const o = cfg.boosts.outfits[s.name], pt = cfg.boosts.portables[s.name];
    if (o && b.outfits.includes(s.name)) mult *= 1 + o.percent / 100;
    if (pt && b.portables.includes(s.name)) mult *= 1 + pt.percent / 100;
    if (b.double) mult *= 1 + cfg.boosts.double.percent / 100;
    s.multiplier = mult;
    for (const g of s.segments) { g.xp_hr = Math.round(g.base_xp_hr * mult); g.hours = g.xp / (g.base_xp_hr * mult); }
    if (s.hours != null) s.hours = s.segments.reduce((a, g) => a + g.hours, 0);
    if (s.now) s.now.boosted_xp_hr = Math.round(s.now.xp_hr * mult);
  }
  const open = plan.skills.filter(s => s.remaining > 0);
  plan.total_hours = open.some(s => s.hours == null) ? null : open.reduce((a, s) => a + s.hours, 0);
  plan.boosts = b;
  return plan;
}
async function staticApi(path, opts) {
  if (opts.method && opts.method !== "GET")
    throw new Error("This is a read-only demo. Run the planner on your own PC to track players and update stats.");
  const u = new URL(path, "http://demo"), p = u.pathname, q = u.searchParams;
  if (p === "/api/players") return staticJson("players");
  if (p === "/api/training/boosts") return (await staticJson("boosts")).boosts;
  if (p === "/api/training/creatures") return staticJson("creatures");
  if (p.endsWith("/summary")) return staticJson(`summary-${q.get("window")}`);
  if (/\/skills\/\d+$/.test(p)) return staticJson(`skill-${p.split("/").pop()}`);
  if (p.endsWith("/training")) {
    const plan = await staticJson(`training-${q.get("target")}-${q.get("mode")}-${q.get("expensive") === "true" ? "all" : "std"}`);
    return applyBoosts(plan, q, await staticJson("boosts"));
  }
  for (const name of ["events", "activities", "quests"]) if (p.endsWith("/" + name)) return staticJson(name);
  throw new Error("Not available in the demo: " + p);
}
async function api(path, opts = {}) {
  if (STATIC) return staticApi(path, opts);
  let r;
  try {
    r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  } catch {
    throw new Error("Can't reach the planner server. Start it by double-clicking start_planner.bat " +
      "(or run: python -m rsxp serve), then reload this page.");
  }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : r.statusText);
  return body;
}
function notice(msg, err = false) {
  const n = $("#notice");
  n.hidden = !msg; n.className = "notice" + (err ? " err" : ""); n.textContent = msg || "";
}

// ---------- manual pace ----------
const manualKey = () => "manual." + state.pid;
const manual = () => store.get(manualKey(), {});
const hours = () => Number(store.get("hours", 10)) || 0;
const manualRate = sid => (Number(manual()[sid]) || 0) * hours() / 7; // XP per day

// ---------- skills table ----------
function rowModel(s) {
  const t = s.targets && s.targets[state.target];
  let etaDays = t ? t.eta_days : null, route = null;
  if (t && state.window === "manual") etaDays = t.remaining === 0 ? 0 : (manualRate(s.id) > 0 ? t.remaining / manualRate(s.id) : null);
  if (t && isMethods()) {
    route = state.training[state.window]?.skills.find(x => x.id === s.id) || null;
    etaDays = t.remaining === 0 ? 0 : (route && route.hours != null && hours() > 0 ? route.hours / hours() * 7 : null);
  }
  return { ...s, t, route, gain: s.gains[state.period], pct: t ? t.pct : -1, remaining: t ? t.remaining : -1, eta: etaDays,
           rankSort: s.rank > 0 ? -s.rank : -Infinity };
}
function sorted(rows) {
  const { key, dir } = state.sort;
  const v = r => (r[key] == null ? (key === "eta" ? Infinity : -Infinity) : r[key]);
  const overall = rows.filter(r => r.id === 0), rest = rows.filter(r => r.id !== 0);
  return overall.concat(rest.sort((a, b) => (v(a) > v(b) ? 1 : v(a) < v(b) ? -1 : a.id - b.id) * dir));
}
function renderTable() {
  const d = state.data, isManual = state.window === "manual";
  const rows = sorted(d.skills.map(rowModel));
  $("#th-pace").textContent = isManual ? "Your XP/hr" : isMethods() ? "Method now" : "XP/day";
  $("#th-pace").classList.toggle("r", !isMethods());
  $("#th-goal").textContent = "To " + state.target;
  $("#th-gain").textContent = periodLabel() + (d.estimated[state.period] ? "*" : "");
  $("#th-gain").title = d.estimated[state.period] ? "Partly from imported monthly history and Adventurer's Log milestones, so approximate" : "";
  $("#table-note").innerHTML = '<span class="legend"><b class="c-combat">combat</b><b class="c-gathering">gathering</b>'
    + '<b class="c-artisan">artisan</b><b class="c-support">support</b><b class="c-elite">elite</b></span>'
    + ' &nbsp;·&nbsp; gold bar = there &nbsp;·&nbsp; (99) = real level, hiscores cap it'
    + (usesHours() ? ` &nbsp;·&nbsp; eta = all ${hours()} h/wk on that one skill` : "")
    + (d.estimated[state.period] ? " &nbsp;·&nbsp; * before tracking started: from imported monthly history and Adventurer's Log milestones, so approximate" : "");
  $("#rows").innerHTML = rows.map(r => {
    const t = r.t, done = t && t.remaining === 0;
    const now = r.route && r.route.now;
    const pace = r.id === 0 ? "" : isManual
      ? (done ? "" : `<input type="number" min="0" step="1000" data-xphr="${r.id}" value="${manual()[r.id] || ""}" placeholder="xp/hr" aria-label="${esc(r.name)} XP per hour">`)
      : isMethods()
      ? (done || !now ? "" : `<span class="method" title="${esc(now.name)} · ${short(now.boosted_xp_hr)} XP/hr · ${hrs(r.route.hours)} to ${state.target}">${esc(now.name)}</span> <span class="dim num">${short(now.boosted_xp_hr)}/h</span>`)
      : (r.rate_per_day ? short(r.rate_per_day) : "–");
    return `<tr data-id="${r.id}" class="${r.id === state.skill ? "sel" : ""} ${r.id === 0 ? "overall" : "c-" + (CATEGORY[r.name] || "support")}">
      <td class="sk">${esc(r.name)}</td>
      <td class="r num">${r.virtual_level ?? r.level ?? "–"}${r.level && r.virtual_level && r.virtual_level > r.level ? `<span class="dim"> (${r.level})</span>` : ""}</td>
      <td class="r num" title="${r.xp != null ? nf.format(r.xp) : ""}">${short(r.xp)}</td>
      <td class="r num">${r.rank > 0 ? nf.format(r.rank) : '<span class="dim">–</span>'}</td>
      <td class="r num">${r.gain ? "+" + short(r.gain) : "<span class='dim'>0</span>"}</td>
      <td>${t ? `<span class="bar ${done ? "done" : ""}"><i style="width:${t.pct}%"></i></span><span class="pct num">${done ? "" : t.pct.toFixed(1) + "%"}</span>${done ? ' <span class="pill">done</span>' : ""}` : ""}</td>
      <td class="r num">${t && !done ? short(t.remaining) : ""}</td>
      <td class="${isMethods() ? "" : "r "}num">${pace}</td>
      <td class="r num" title="${etaDate(r.eta)}">${t && !done ? eta(r.eta) : ""}</td>
    </tr>`;
  }).join("");
  renderGoal(rows);
  renderNext(rows);
}
// The skills nearest the goal, to fill the space under the by-skill chart.
function renderNext(rows) {
  const near = rows.filter(r => r.t && r.t.remaining > 0).sort((a, b) => a.t.remaining - b.t.remaining).slice(0, 6);
  $("#next-title").textContent = `Closest to ${state.target}`;
  $("#next").innerHTML = near.length ? near.map(r => `<li class="c-${CATEGORY[r.name] || "support"}" data-id="${r.id}">
      <span class="sk-name">${esc(r.name)}</span><span class="bar"><i style="width:${r.t.pct}%"></i></span>
      <span class="num">${short(r.t.remaining)}</span><span class="num dim" title="${etaDate(r.eta)}">${eta(r.eta)}</span></li>`).join("")
    : `<li class="empty">Every skill is at ${state.target}.</li>`;
}
function renderGoal(rows) {
  const open = rows.filter(r => r.t && r.t.remaining > 0);
  const remaining = open.reduce((a, r) => a + r.t.remaining, 0);
  const noRate = open.filter(r => r.eta == null);
  const slowest = open.filter(r => r.eta != null).sort((a, b) => b.eta - a.eta)[0];
  if (!open.length) { $("#goal").innerHTML = `All 29 skills are at ${state.target}. Gz!`; return; }
  const pace = state.window === "manual" ? "at your XP/hr"
    : typeof state.window === "number" ? `at your ${state.window === 365 ? "last-year" : `last-${state.window}-days`} pace` : "";
  const bits = [`At ${state.target}: <b>${29 - open.length}</b> of 29 skills`, `Still to go: <b>${short(remaining)}</b> XP`];
  const plan = isMethods() && state.training[state.window];
  if (plan && plan.total_hours != null)
    bits.push(`Training time: <b>~${hrs(plan.total_hours)}</b> with the ${state.window} methods${hours() > 0 ? ` (${eta(plan.total_hours / hours() * 7)} at ${hours()} h a week)` : ""}`);
  else if (!usesHours() && noRate.length)
    bits.push(`No recent XP in ${noRate.map(r => r.name).slice(0, 3).join(", ")}${noRate.length > 3 ? " and more" : ""}, so no finish date`);
  else if (!usesHours() && slowest)
    bits.push(`Longest wait: <span title="${nf.format(Math.round(slowest.eta))} days">${esc(slowest.name)}, ${eta(slowest.eta)}</span> ${pace}`);
  $("#goal").innerHTML = bits.join(" &nbsp;·&nbsp; ");
}
function renderKpis() {
  const d = state.data, o = d.skills[0], q = state.quests;
  const maxed = d.skills.filter(s => s.id && s.targets && s.targets["99"].remaining === 0).length;
  $("#totals").innerHTML = [
    `total <b>${o.level != null ? nf.format(o.level) : "–"}</b>`,
    `<b>${o.xp != null ? nf.format(o.xp) : "–"}</b> xp`,
    o.rank > 0 ? `rank <b>${nf.format(o.rank)}</b>` : "",
    maxed === 29 ? "<b>maxed</b>" : `<b>${maxed}</b>/29 99s`,
    q && q.qp != null ? `<b>${q.qp}</b>/${q.qp_max} qp` : "",
  ].filter(Boolean).map(x => `<span>${x}</span>`).join("");
  const since = d.history_starts ? new Date(d.history_starts * 1000).toLocaleDateString(undefined, { month: "short", year: "numeric" }) : null;
  const g = p => (o.gains[p] == null ? "–" : "+" + short(o.gains[p]));
  const star = p => (d.partial[p] && since ? `<sup title="history starts ${since}">*</sup>` : d.estimated[p] ? `<sup title="partly from imported monthly history and Adventurer's Log milestones, so approximate">*</sup>` : "");
  const cell = (lbl, p, tip) => `<div title="${tip}"><dt>${lbl}</dt><dd>${g(p)}${star(p)}</dd></div>`;
  $("#kpis").innerHTML = cell("today", "day", "since 00:00 UTC") + cell("this week", "week", "since the Wednesday reset")
    + cell("this month", "month", "") + cell("this year", "year", "");
}
function renderStatus() {
  const p = state.data.player;
  if (STATIC) {
    const when = new Date(state.data.now * 1000).toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" });
    $("#status").textContent = `demo · read-only snapshot from ${when}`;
    return;
  }
  const next = p.next_check_at ? Math.max(0, Math.round((p.next_check_at - Date.now() / 1000) / 60)) : null;
  $("#status").textContent = p.status === "not_found"
    ? "hiscores can't find this name, trying again tomorrow"
    : `checked ${ago(p.last_checked_at)}${next != null ? ` · next in ${next} min` : ""}`;
  $("#status").title = "Hiscores only change when you log out or hop worlds.";
}

// ---------- charts ----------
// Prints each bar's value at its tip, so small bars stay readable next to big ones.
const valueLabels = {
  id: "valueLabels",
  afterDatasetsDraw(chart) {
    const { ctx } = chart, horiz = chart.options.indexAxis === "y";
    ctx.save();
    ctx.fillStyle = css("--muted"); ctx.font = `500 12px ${css("--f-ui")}`;
    ctx.textAlign = horiz ? "left" : "center"; ctx.textBaseline = horiz ? "middle" : "bottom";
    chart.getDatasetMeta(0).data.forEach((el, i) => {
      const v = chart.data.datasets[0].data[i];
      if (v) ctx.fillText(short(v), horiz ? el.x + 6 : el.x, horiz ? el.y : el.y - 4);
    });
    ctx.restore();
  },
};
function chartBase() {
  Chart.defaults.color = css("--muted");
  Chart.defaults.borderColor = css("--grid");
  Chart.defaults.font.family = css("--f-ui");
  Chart.defaults.font.size = 13;
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.titleFont = { size: 13, weight: "600" };
  Chart.defaults.plugins.tooltip.bodyFont = { size: 13 };
}
function replaceChart(key, canvas, config) {
  if (state.charts[key]) state.charts[key].destroy();
  state.charts[key] = new Chart(canvas, config);
}
function renderBySkill() {
  if (!window.Chart || !state.data) return;
  const rows = state.data.skills.filter(s => s.id && s.gains[state.period] > 0)
    .sort((a, b) => b.gains[state.period] - a.gains[state.period]);
  $("#by-skill-title").textContent = `${periodLabel()}, by skill${state.data.estimated[state.period] ? "*" : ""}`;
  $("#by-skill-empty").hidden = rows.length > 0;
  $("#by-skill-box").hidden = !rows.length;
  if (!rows.length) return;
  $("#by-skill-box").style.height = Math.max(120, rows.length * 26 + 20) + "px";
  replaceChart("bySkill", $("#c-by-skill"), {
    type: "bar",
    data: { labels: rows.map(r => r.name), datasets: [{ data: rows.map(r => r.gains[state.period]),
      backgroundColor: rows.map(r => skillColor(r.id)), borderColor: css("--ink"),
      borderWidth: rows.map(r => (r.id === state.skill ? 2 : 0)), borderSkipped: false,
      borderRadius: 0, barPercentage: 0.72, categoryPercentage: 0.9 }] },
    options: {
      indexAxis: "y", maintainAspectRatio: false, layout: { padding: { right: 56 } },
      plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => "+" + nf.format(c.raw) + " XP" } } },
      scales: { x: { display: false, beginAtZero: true }, y: { grid: { display: false }, ticks: { color: rows.map(r => skillColor(r.id)), font: { weight: "600" }, autoSkip: false } } },
      onClick: (_, els) => { if (els.length) loadSkill(rows[els[0].index].id); },
      onHover: (e, els) => { e.native.target.style.cursor = els.length ? "pointer" : "default"; },
    },
    plugins: [valueLabels],
  });
}
async function loadSkill(sid) {
  state.skill = sid;
  document.querySelectorAll("#rows tr").forEach(tr => tr.classList.toggle("sel", Number(tr.dataset.id) === sid));
  renderRoutes();
  const h = await api(`/api/players/${state.pid}/skills/${sid}`);
  $("#skill-title").textContent = h.skill;
  if (!window.Chart) { $(".charts").innerHTML = '<p class="empty">Charts need an internet connection to load Chart.js.</p>'; return; }
  renderBySkill();
  const accent = skillColor(sid), info = accent + "66", good = css("--good");
  $("#key-sofar").style.background = info;
  $("#skill-title").style.color = sid ? accent : "";

  // Short histories read better per day; switch to months once there are a few.
  const byDay = (h.monthly || []).length < 3 && (h.daily || []).length > 0;
  const bins = byDay ? h.daily : h.monthly;
  $("#per-title").textContent = byDay ? "Per day" : "Per month";
  const binLabel = (b, i) => byDay
    ? (([y, m, d]) => (d === "01" || i === 0 ? `${Number(d)} ${MONTHS[m - 1]}` : String(Number(d))))(b.day.split("-"))
    : monthLabel(b.month, i);
  replaceChart("monthly", $("#c-monthly"), {
    type: "bar",
    data: { labels: bins.map(binLabel), datasets: [{
      data: bins.map(m => m.gain), backgroundColor: bins.map(m => (m.partial ? info : accent)),
      borderRadius: 0, minBarLength: 2, barPercentage: 0.7 }] },
    options: { maintainAspectRatio: false, layout: { padding: { top: 22 } },
      plugins: { legend: { display: false }, tooltip: { callbacks: {
        title: i => { const b = bins[i[0].dataIndex];
          if (byDay) { const [y, mo, d] = b.day.split("-"); return `${Number(d)} ${MONTHS[mo - 1]} ${y}${b.partial ? " (so far)" : ""}`; }
          const [y, mo] = b.month.split("-"); return `${MONTHS[mo - 1]} ${y}${b.partial ? " (so far)" : ""}`; },
        label: c => "+" + nf.format(c.raw) + " XP" } } },
      scales: { y: { beginAtZero: true, ticks: { callback: v => short(v), maxTicksLimit: 6 } },
        x: { grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, color: css("--ink") } } } },
    plugins: [valueLabels],
  });

  const pts = h.history.map(([t, x]) => ({ x: t * 1000, y: x }));
  const goal = sid ? state.data.skills.find(s => s.id === sid)?.targets?.[state.target]?.target_xp : null;
  const top = pts.length ? pts[pts.length - 1].y : 0, low = pts.length ? pts[0].y : 0;
  const showGoal = goal && goal > top && goal - top <= Math.max(top - low, top * 0.25) * 1.5;
  $("#history-note").textContent = goal && !showGoal && goal > top ? `${state.target} is at ${short(goal)}, above this chart` : "";
  const sets = [{ label: "Total XP", data: pts, borderColor: accent, backgroundColor: accent, fill: false,
    pointRadius: pts.map((_, i) => (i === pts.length - 1 ? 3.5 : 0)), pointHoverRadius: 4, borderWidth: 2, tension: 0, stepped: false }];
  if (showGoal && pts.length)
    sets.push({ label: `${state.target}`, data: [{ x: pts[0].x, y: goal }, { x: pts[pts.length - 1].x, y: goal }],
      borderColor: good, borderDash: [3, 3], pointRadius: 0, borderWidth: 1.5 });
  const minX = pts.length ? pts[0].x : 0, maxX = pts.length ? pts[pts.length - 1].x : 0;
  // month ticks for long histories, day ticks (every 1-7 days) for short ones
  const DAYMS = 864e5, shortSpan = maxX - minX < 60 * DAYMS;
  const monthTicks = [];
  if (shortSpan) {
    const step = Math.max(1, Math.ceil((maxX - minX) / DAYMS / 8));
    for (let t = Math.ceil(minX / DAYMS) * DAYMS; t <= maxX; t += step * DAYMS) monthTicks.push(t);
  } else {
    for (let d = new Date(minX); d.getTime() <= maxX; d = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1))) {
      const start = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
      if (start >= minX) monthTicks.push(start);
    }
  }
  replaceChart("history", $("#c-history"), {
    type: "line", data: { datasets: sets },
    options: { maintainAspectRatio: false, parsing: false,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      plugins: { legend: { display: sets.length > 1, labels: { boxWidth: 14 } },
        tooltip: { callbacks: { title: i => new Date(i[0].raw.x).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }),
          label: c => `${c.dataset.label}: ${nf.format(Math.round(c.raw.y))}` } } },
      scales: {
        x: { type: "linear", min: minX, max: maxX, grid: { display: false },
          afterBuildTicks: axis => { axis.ticks = monthTicks.map(v => ({ value: v })); },
          ticks: { maxRotation: 0, autoSkip: true, color: css("--ink"), callback: v => {
            const d = new Date(v);
            if (shortSpan) return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`;
            return d.getUTCMonth() === 0 ? `Jan '${String(d.getUTCFullYear()).slice(2)}` : MONTHS[d.getUTCMonth()]; } } },
        y: { ticks: { callback: v => short(v), maxTicksLimit: 6 } } } },
  });
}

// ---------- training routes ----------
const boosts = () => {
  const b = store.get("boosts", {});
  return { avatar: b.avatar || 0, outfits: Array.isArray(b.outfits) ? b.outfits : [],
           portables: Array.isArray(b.portables) ? b.portables : [], double: !!b.double, expensive: !!b.expensive };
};
function boostQuery() {
  const b = boosts();
  return `avatar=${b.avatar}&outfits=${encodeURIComponent(b.outfits.join(","))}`
    + `&portables=${encodeURIComponent(b.portables.join(","))}&double=${b.double}&expensive=${b.expensive}`;
}
// Only skills that actually have an outfit or a portable get a row, in skill-tab order.
async function renderKit() {
  const cfg = await api("/api/training/boosts");
  const real = t => Object.fromEntries(Object.entries(cfg[t] || {}).filter(([k]) => !k.startsWith("_")));
  state.kit = { outfits: real("outfits"), portables: real("portables") };
  const order = ["Attack","Defence","Strength","Hitpoints","Ranged","Prayer","Magic","Cooking","Woodcutting","Fletching",
    "Fishing","Firemaking","Crafting","Smithing","Mining","Herblore","Agility","Thieving","Slayer","Farming","Runecraft",
    "Hunter","Construction","Summoning","Dungeoneering","Divination","Invention","Archaeology","Necromancy"];
  const box = (kind, skill, v) => v ? `<label><input type="checkbox" data-kit="${kind}" data-skill="${skill}"> ${esc(v.item)} <span class="dim">+${v.percent}%</span></label>` : "";
  $("#kit").innerHTML = order.filter(n => state.kit.outfits[n] || state.kit.portables[n]).map(n =>
    `<div class="kit-row c-${CATEGORY[n]}"><span class="sk-name">${n}</span>${box("outfits", n, state.kit.outfits[n])}${box("portables", n, state.kit.portables[n])}</div>`).join("");
  renderBoostControls();
}
function renderBoostControls() {
  const b = boosts();
  $("#b-avatar").value = String(b.avatar || 0);
  ["double", "expensive"].forEach(k => { $("#b-" + k).checked = !!b[k]; });
  document.querySelectorAll("[data-kit]").forEach(cb => { cb.checked = b[cb.dataset.kit].includes(cb.dataset.skill); });
  const no = b.outfits.length, np = b.portables.length;
  $("#kit-count").textContent = no || np ? `(${no} outfit${no === 1 ? "" : "s"}, ${np} portable${np === 1 ? "" : "s"})` : "(none)";
}
async function loadTraining() {
  const target = state.target;
  const [fastest, easiest] = await Promise.all(MODES.map(m =>
    api(`/api/players/${state.pid}/training?target=${target}&mode=${m}&${boostQuery()}`)));
  if (target === state.target) state.training = { fastest, easiest };
}
function methodTags(g) {
  const tags = [];
  if (g.source === "guide") tags.push('<span class="tag" title="Rate from a third-party guide; the wiki has no hourly figure yet">(guide rate)</span>');
  if (g.source === "estimate") tags.push('<span class="tag warn" title="No published rate; rough estimate">(rough guess)</span>');
  if (g.expensive) tags.push('<span class="tag warn">(Pricey Option)</span>');
  return tags.join(" ");
}
function renderRoutes() {
  const tr = state.training;
  if (!tr.fastest) return;
  const sid = state.skill;
  const label = { fastest: "Fastest", easiest: "Easiest" };
  const week = h => (hours() > 0 && h ? `, ${eta(h / hours() * 7)} at ${hours()} h/wk` : "");
  if (!sid) {
    $("#route-title").textContent = `Training to ${state.target}`;
    $("#route-title").className = "";
    $("#routes").innerHTML = MODES.map(m => {
      const p = tr[m], open = p.skills.filter(s => s.remaining > 0).sort((a, b) => (b.hours ?? 0) - (a.hours ?? 0));
      return `<div class="route"><h3>${label[m]} <small>${hrs(p.total_hours)} in all${week(p.total_hours)}</small></h3>
        ${open.length ? `<table class="route-sum"><thead><tr><th>Skill</th><th>Start with</th><th class="r">Hours</th></tr></thead><tbody>
        ${open.map(s => `<tr class="c-${CATEGORY[s.name] || "support"}"><td class="sk">${esc(s.name)}</td><td class="dim"><span class="method">${esc(s.now?.name ?? "No method listed")}</span></td><td class="r num">${hrs(s.hours)}</td></tr>`).join("")}
        </tbody></table>` : `<p class="empty">Every skill is at ${state.target}.</p>`}
        <p class="hint">Pick a skill in the table for its level-by-level route.</p></div>`;
    }).join("");
    return;
  }
  const name = state.data.skills.find(s => s.id === sid)?.name ?? "";
  $("#route-title").textContent = `${name} to ${state.target}`;
  $("#route-title").className = "sk-title c-" + (CATEGORY[name] || "support");
  $("#routes").innerHTML = MODES.map(m => {
    const s = tr[m].skills.find(x => x.id === sid);
    if (!s) return `<div class="route"><p class="empty">No Hiscores data for ${esc(name)}.</p></div>`;
    if (!s.remaining) return `<div class="route"><h3>${label[m]}</h3><p class="empty">Already there.</p></div>`;
    const steps = s.segments.map(g => {
      const [eff, cls] = EFFORT[g.effort] || [g.effort, ""];
      const to = g.to_level ?? state.target;
      const rate = g.xp_hr !== g.base_xp_hr ? `${short(g.xp_hr)} XP/hr <span class="dim">(base ${short(g.base_xp_hr)})</span>` : `${short(g.xp_hr)} XP/hr`;
      return `<div class="step c-${CATEGORY[name] || "support"}"><span class="lv">${g.from_level}–${to}</span>
        <span class="what"><span>${esc(g.method)} ${methodTags(g)}</span>
          <span class="meta">${rate}, ${short(g.xp)} XP, <span class="pill ${cls}">${eff}</span></span>
          ${g.requires ? `<span class="meta">Needs ${esc(g.requires)}</span>` : ""}
          ${g.note ? `<span class="meta">${esc(g.note)}</span>` : ""}</span>
        <span class="h">${hrs(g.hours)}</span></div>`;
    }).join("");
    const pricey = (s.other_methods || []).filter(o => o.xp_hr && o.expensive && !o.locked);
    const unrated = (s.other_methods || []).filter(o => !pricey.includes(o));
    const rates = pricey.map(o => o.xp_hr);
    const others = unrated.map(o => `<li><span class="o-name">${esc(o.name)}<span class="o-lvl">lvl ${o.level}${o.slayer ? `, ${o.slayer} Slayer` : ""}</span></span>
      <span class="o-note">${esc(o.locked || o.note || "no published hourly rate")}</span></li>`).join("")
      + (pricey.length ? `<li><span class="o-name">Pricey Options<span class="o-lvl">${pricey.length} method${pricey.length > 1 ? "s" : ""}</span></span>
      <span class="o-note">${pricey.map(o => esc(o.name)).join(", ")} (${short(Math.min(...rates))}${pricey.length > 1 ? `–${short(Math.max(...rates))}` : ""} xp/hr). Tick Pricey Options above to include ${pricey.length > 1 ? "them" : "it"}.</span></li>` : "");
    return `<div class="route"><h3>${label[m]} <small>${hrs(s.hours)}${week(s.hours)}</small></h3>${steps ||
      `<p class="empty">No method in methods.json for level ${s.missing_method_at}.</p>`}
      ${others ? `<div class="others"><h4>Other options, not in the hours above</h4><ul>${others}</ul></div>` : ""}</div>`;
  }).join("") + creaturePick(sid, name);
}
// Combat skills: pick a creature and how many you kill an hour; XP/hr = kills x XP per kill x boosts.
function creaturePick(sid, name) {
  const cr = state.creatures, s = state.training.fastest?.skills.find(x => x.id === sid);
  if (!cr || !cr.skills.includes(name) || !s || !s.remaining) return "";
  const slayer = state.data.skills.find(x => x.name === "Slayer")?.level ?? 1;
  const share = name === "Hitpoints" ? 1 / 3 : 1;   // Constitution gets about a third of combat XP
  const pick = store.get("pick." + name, {});
  const c = cr.list.find(x => x.name === pick.name && !(x.slayer > slayer));
  const kills = Number(pick.kills) || 0;
  const rate = c && kills ? c.kill * share * kills * s.multiplier : 0;
  const h = rate ? s.remaining / rate : null;
  const opts = cr.list.map(x => {
    const why = x.slayer > slayer ? `, needs ${x.slayer} Slayer` : "";
    return `<option value="${esc(x.name)}" ${why ? "disabled" : ""} ${c === x ? "selected" : ""}>${esc(x.name)} (${nf.format(Math.round(x.kill * share))} XP/kill${why})</option>`;
  }).join("");
  return `<div class="route pick"><h3>Your pick <small>a creature you choose, at your kill rate</small></h3>
    <div class="pick-row" data-skill="${esc(name)}"><select id="pick-creature" aria-label="Creature"><option value="">Choose a creature…</option>${opts}</select>
      <label><input id="pick-kills" type="number" min="0" step="1" value="${kills || ""}" placeholder="kills"> kills an hour</label></div>
    ${c && (c.requires || c.slayer) ? `<span class="meta">Needs ${esc([c.slayer && `${c.slayer} Slayer`, c.requires].filter(Boolean).join(", "))}</span>` : ""}
    ${rate ? `<p class="pick-out"><b>${short(rate)}</b> XP/hr${s.multiplier !== 1 ? ` (with boosts)` : ""}, so <b>${hrs(h)}</b> to ${state.target}${hours() > 0 ? `, or ${eta(h / hours() * 7)} at ${hours()} h a week` : ""}</p>`
      : `<p class="dim">Choose a creature and enter how many you kill an hour.</p>`}</div>`;
}

// ---------- activities, quests, events ----------
function renderActivities() {
  $("#act-gain").textContent = periodLabel();
  const rows = [...state.activities].sort((a, b) => (b.name === "RuneScore") - (a.name === "RuneScore") || a.name.localeCompare(b.name));
  $("#activities").innerHTML = rows.length ? rows.map(a => `<tr>
      <td>${esc(a.name)}</td>
      <td class="r num">${nf.format(a.score)}</td>
      <td class="r num">${a.rank > 0 ? nf.format(a.rank) : '<span class="dim">–</span>'}</td>
      <td class="r num">${a.gains[state.period] ? "+" + nf.format(a.gains[state.period]) : '<span class="dim">0</span>'}</td>
    </tr>`).join("")
    : '<tr><td colspan="4" class="empty">No scores yet.</td></tr>';
}
function renderQuests() {
  const q = state.quests;
  if (!q || !q.total) {
    $("#qp").innerHTML = '<p class="empty" style="padding:0">Quest data appears after the next check (every 6 hours).</p>';
    $("#quest-tabs").innerHTML = ""; $("#quests").innerHTML = ""; return;
  }
  $("#quests-checked").textContent = STATIC && q.checked_at
    ? new Date(q.checked_at * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })
    : ago(q.checked_at);
  $("#qp").innerHTML = `<div><span class="big num">${q.qp}</span> <span class="dim">of ${q.qp_max} quest points</span></div>
    <span class="bar"><i style="width:${(q.qp / q.qp_max) * 100}%"></i></span>
    <div class="chips"><span>${q.counts.COMPLETED} done</span><span>${q.counts.STARTED} started</span><span>${q.counts.NOT_STARTED} not started</span></div>`;
  const groups = {
    ready: ["Can start", q.open.filter(x => x.status === "NOT_STARTED" && x.eligible)],
    started: ["Started", q.open.filter(x => x.status === "STARTED")],
    locked: ["Locked", q.open.filter(x => x.status === "NOT_STARTED" && !x.eligible)],
    recent: ["Finished lately", q.recent],
  };
  $("#quest-tabs").innerHTML = Object.entries(groups).map(([k, [l, xs]]) =>
    `<button type="button" data-tab="${k}" aria-pressed="${k === state.questTab}">${l} (${xs.length})</button>`).join("");
  const [, list] = groups[state.questTab];
  const shown = state.openLists.quests ? list : list.slice(0, LIST_MAX);
  $("#quests").innerHTML = list.length ? shown.map(x => `<li><div class="row"><span>${esc(x.title)}</span>
      <span class="meta">${x.qp ? x.qp + " QP" : ""}</span></div>
      <span class="meta">${DIFFICULTY[x.difficulty] ?? ""}${x.members ? " · members" : " · free"}${x.completed_seen_at ? " · completed " + ago(x.completed_seen_at) : ""}</span></li>`).join("")
      + moreLink("quests", list.length)
    : `<li class="empty">${state.questTab === "recent" ? "Nothing finished since tracking started." : "None."}</li>`;
}
// "84000000XP in Smithing" -> "84,000,000 XP in Smithing"; other 5+ digit numbers get commas too.
const eventText = s => String(s)
  .replace(/(\d+)\s*XP\b/gi, (_, n) => nf.format(Number(n)) + " XP")
  .replace(/\b\d{5,}\b/g, n => nf.format(Number(n)));
function eventTag(type) {
  const hit = EVENT_TAGS.find(([re]) => re.test(type));
  return hit ? `<span class="pill ${hit[2]}">${hit[1]}</span>` : '<span class="pill info">Event</span>';
}
async function loadEvents() {
  state.events = await api(`/api/players/${state.pid}/events?limit=30`);
  renderEvents();
}
function moreLink(key, total) {
  if (total <= LIST_MAX) return "";
  return `<li class="more"><button type="button" data-more="${key}">${state.openLists[key] ? "show fewer" : `show all ${total}`}</button></li>`;
}
function renderEvents() {
  const ev = state.events || [];
  const shown = state.openLists.events ? ev : ev.slice(0, LIST_MAX);
  $("#events").innerHTML = ev.length
    ? shown.map(e => `<li><div class="row"><span title="${esc(eventText(e.details))}">${esc(eventText(e.text))}</span>${eventTag(e.type)}</div>
        <time datetime="${new Date(e.date * 1000).toISOString()}">${new Date(e.date * 1000).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" })}</time></li>`).join("") + moreLink("events", ev.length)
    : `<li class="empty">Nothing logged. The Adventurer's Log needs membership and event logging switched on in-game.</li>`;
}

// ---------- controls ----------
function seg(el, items, current, onPick) {
  el.innerHTML = items.map(([v, l]) => `<button type="button" data-v="${v}" aria-pressed="${String(v) === String(current)}">${l}</button>`).join("");
  el.onclick = e => {
    const b = e.target.closest("button"); if (!b) return;
    const raw = b.dataset.v, v = isNaN(raw) ? raw : Number(raw);
    el.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
    onPick(v);
  };
}
function renderControls() {
  seg($("#seg-target"), TARGETS.map(t => [t, t]), state.target, async v => {
    state.target = v; store.set("target", v);
    try { await loadTraining(); } catch (e) { notice(e.message, true); }
    renderTable(); loadSkill(state.skill);
  });
  $("#pace").value = String(state.window);
  $("#pace").onchange = e => {
    const v = isNaN(e.target.value) ? e.target.value : Number(e.target.value);
    state.window = v; store.set("window", v); $("#hours-ctl").hidden = !usesHours(); loadSummary();
  };
  seg($("#seg-period"), PERIODS, state.period, v => { state.period = v; store.set("period", v); renderTable(); renderBySkill(); renderActivities(); });
  $("#hours-ctl").hidden = !usesHours();
  $("#hours").value = hours();
}
async function loadSummary() {
  const w = typeof state.window === "number" ? state.window : 30;
  state.data = await api(`/api/players/${state.pid}/summary?window=${w}`);
  renderKpis(); renderStatus(); renderTable();
}
async function loadExtras() {
  [state.activities, state.quests] = await Promise.all([
    api(`/api/players/${state.pid}/activities`), api(`/api/players/${state.pid}/quests`)]);
  renderActivities(); renderQuests(); renderKpis();
}
async function loadPlayers(selectId) {
  const ps = await api("/api/players");
  const sel = $("#player");
  if (!ps.length) {
    sel.hidden = true; $("#refresh").hidden = true;
    $("#kpis").innerHTML = ""; $("#totals").textContent = "";
    $("#goal").textContent = "Nobody tracked yet. Type a display name in the box at the top and press Track.";
    return false;
  }
  sel.hidden = false; $("#refresh").hidden = false;
  sel.innerHTML = ps.map(p => `<option value="${p.id}">${esc(p.display_name)}</option>`).join("");
  const want = selectId ?? state.pid;
  state.pid = ps.some(p => p.id === want) ? want : ps[0].id;
  sel.value = state.pid; store.set("pid", state.pid);
  return true;
}
async function loadAll() {
  try {
    if (document.fonts) await document.fonts.ready;   // charts measure text, so wait for Barlow
    if (window.Chart) chartBase();
    state.training = {};
    await loadTraining();
    await loadSummary();
    await Promise.all([loadSkill(state.skill), loadEvents(), loadExtras()]);
  } catch (e) { notice(e.message, true); }
}

$("#player").onchange = e => { state.pid = Number(e.target.value); store.set("pid", state.pid); state.skill = 0; loadAll(); };
$("#rows").addEventListener("click", e => {
  if (e.target.closest("input")) return;
  const tr = e.target.closest("tr"); if (tr) loadSkill(Number(tr.dataset.id)).catch(err => notice(err.message, true));
});
$("#rows").addEventListener("change", e => {
  const id = e.target.dataset.xphr; if (!id) return;
  const m = manual(); m[id] = Number(e.target.value) || 0; store.set(manualKey(), m); renderTable();
});
$("#hours").onchange = e => { store.set("hours", Number(e.target.value) || 0); renderTable(); renderRoutes(); };
$("#boosts").addEventListener("change", async () => {
  const picked = kind => [...document.querySelectorAll(`[data-kit="${kind}"]:checked`)].map(cb => cb.dataset.skill);
  store.set("boosts", { avatar: Number($("#b-avatar").value), outfits: picked("outfits"), portables: picked("portables"),
    double: $("#b-double").checked, expensive: $("#b-expensive").checked });
  renderBoostControls();
  try { await loadTraining(); renderTable(); renderRoutes(); } catch (e) { notice(e.message, true); }
});
$("#boosts").addEventListener("click", e => {
  const b = e.target.closest("[data-kit-all]"); if (!b) return;
  const which = b.dataset.kitAll;
  document.querySelectorAll("[data-kit]").forEach(cb => {
    if (which === "none") cb.checked = false; else if (cb.dataset.kit === which) cb.checked = true;
  });
  $("#boosts").dispatchEvent(new Event("change"));
});
$("#next").onclick = e => { const li = e.target.closest("li[data-id]"); if (li) loadSkill(Number(li.dataset.id)).catch(err => notice(err.message, true)); };
$("#routes").addEventListener("change", e => {
  const row = e.target.closest(".pick-row"); if (!row) return;
  store.set("pick." + row.dataset.skill, { name: $("#pick-creature").value, kills: Number($("#pick-kills").value) || 0 });
  renderRoutes();
});
$("#quest-tabs").onclick = e => { const b = e.target.closest("button"); if (b) { state.questTab = b.dataset.tab; state.openLists.quests = false; renderQuests(); } };
document.addEventListener("click", e => {
  const b = e.target.closest("[data-more]"); if (!b) return;
  const key = b.dataset.more; state.openLists[key] = !state.openLists[key];
  key === "quests" ? renderQuests() : renderEvents();
});
// theme: auto -> light -> dark -> auto
function applyTheme(t) {
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
  $("#theme").textContent = "theme: " + (t || "auto");
  if (state.data && window.Chart) { chartBase(); loadSkill(state.skill); }
}
$("#theme").onclick = () => {
  const next = { auto: "light", light: "dark", dark: "auto" }[store.get("theme", "auto")] || "auto";
  store.set("theme", next); applyTheme(next);
};
applyTheme(store.get("theme", "auto"));
document.querySelectorAll("th[data-sort]").forEach(th => th.onclick = () => {
  const key = th.dataset.sort;
  state.sort = { key, dir: state.sort.key === key ? -state.sort.dir : (["id", "eta"].includes(key) ? 1 : -1) };
  renderTable();
});
$("#refresh").onclick = async () => {
  const b = $("#refresh"); b.disabled = true; b.textContent = "updating…";
  try {
    const r = await api(`/api/players/${state.pid}/refresh`, { method: "POST" });
    if (!r.polled) notice(`Checked less than 5 minutes ago. You can update again in ${Math.ceil(r.retry_in / 60)} min.`);
    else if (r.status === "unavailable") notice("Hiscores didn't respond. The collector will try again shortly.", true);
    else if (r.status === "not_found") notice("Hiscores can't find this name. Check the spelling or a recent name change.", true);
    else notice("");
    await loadAll();
  } catch (e) { notice(e.message, true); }
  b.disabled = false; b.textContent = "update";
};
$("#add-form").onsubmit = async e => {
  e.preventDefault();
  const name = $("#add-name").value.trim(); if (!name) return;
  notice(`Looking up ${name} on the Hiscores…`);
  try {
    const p = await api("/api/players", { method: "POST", body: JSON.stringify({ name }) });
    $("#add-name").value = "";
    notice(p.status === "not_found" ? `${p.display_name} isn't on the RuneScape Hiscores. They'll be checked again daily.` : `Now tracking ${p.display_name}.`, p.status === "not_found");
    await loadPlayers(p.id); state.skill = 0; await loadAll();
  } catch (err) { notice(err.message, true); }
};
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (state.data) { chartBase(); loadSkill(state.skill); } });

(async () => {
  // Opened straight from disk: the API only exists on the local server, so go there.
  if (location.protocol === "file:" && !STATIC) {
    notice(`This page runs from the planner server. Opening ${SERVER} …`);
    location.replace(SERVER);
    return;
  }
  if (STATIC) {
    $("#refresh").classList.add("demo-off"); $("#add-form").classList.add("demo-off");
    // "auto": on <owner>.github.io/<repo>/ link back to github.com/<owner>/<repo>, so no
    // account name has to be written into the published files.
    let repo = STATIC.repo;
    if (repo === "auto" && location.hostname.endsWith(".github.io")) {
      const name = location.pathname.split("/").filter(Boolean)[0];
      repo = name ? `https://github.com/${location.hostname.split(".")[0]}/${name}` : null;
    } else if (repo === "auto") repo = null;
    // append, don't replace: the (hidden) update/track controls must stay in the page
    if (repo) $(".actions").insertAdjacentHTML("beforeend", `<a href="${esc(repo)}">Source on GitHub</a>`);
  }
  renderControls();
  renderBoostControls();
  renderKit().catch(e => notice(e.message, true));
  api("/api/training/creatures").then(c => { state.creatures = c; if (state.data) renderRoutes(); }).catch(e => notice(e.message, true));
  try {
    const want = decodeURIComponent(location.hash.slice(1)).toLowerCase();
    if (want) state.skill = Math.max(0, ["overall","attack","defence","strength","hitpoints","ranged","prayer","magic",
      "cooking","woodcutting","fletching","fishing","firemaking","crafting","smithing","mining","herblore","agility",
      "thieving","slayer","farming","runecraft","hunter","construction","summoning","dungeoneering","divination",
      "invention","archaeology","necromancy"].indexOf(want));
    if (await loadPlayers()) await loadAll();
  } catch (e) { notice(e.message, true); }
  if (!STATIC) setInterval(() => state.pid && loadSummary().then(loadExtras).catch(() => {}), 5 * 60 * 1000);
})();
