// Home screen, the daily quiz and Classic mode.
"use strict";

var ICONS = {
  classic: '<path d="M4 6h16M4 12h16M4 18h10"/>',
  survival: '<path d="M12 21s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 11c0 5.6-7 10-7 10z"/>',
  blitz: '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5M9.5 2.5h5"/>',
  hilo: '<path d="M7 20V5M3.5 8.5L7 5l3.5 3.5M17 4v15M13.5 15.5L17 19l3.5-3.5"/>',
  career: '<circle cx="5" cy="18" r="2"/><circle cx="12" cy="6" r="2"/><circle cx="19" cy="15" r="2"/><path d="M6.4 16.4l4.3-8.6M13.6 7.4l4.2 6"/>',
  challenge: '<path d="M14.5 4l5.5 5.5-9 9H5.5V13z"/><path d="M4 20l3-3"/>'
};
// `go` names a function so modes defined in later files (games.js) can be wired here.
var MODES = [
  {id: "classic", name: "Classic", desc: "Pick a league, level and timer", go: "openSetup"},
  {id: "survival", name: "Survival", desc: "One wrong and you're out", go: "startSurvival"},
  {id: "blitz", name: "Blitz", desc: "60 seconds, as many as you can", go: "startBlitz"},
  {id: "hilo", name: "Higher or lower", desc: "Goals, caps, fees and crowds", go: "startHilo"},
  {id: "career", name: "Career path", desc: "Name the player from his clubs", go: "startCareer"},
  {id: "challenge", name: "Challenge", desc: "Send a mate your exact quiz", go: "openChallengeHub"}
];
var lastStart = null;       // what "Play again" on the result screen does

function goHome() { renderHome(); show("home"); }

// ---------------------------------------------------------------- home

function renderModes() {
  var box = $("modes"); if (box.children.length) return;
  MODES.forEach(function (m) {
    var on = !!m.go;
    var b = h("button", {className: "mode tile" + (on ? " active" : ""), disabled: !on},
      h("span", {className: "ic", innerHTML: '<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + ICONS[m.id] + "</svg>"}),
      on ? null : h("span", {className: "soon"}, "Soon"),
      h("b", null, m.name), h("span", null, m.desc));
    b.setAttribute("data-mode", m.id);
    if (on) b.onclick = function () { window[m.go](); };
    else b.setAttribute("aria-label", m.name + ", coming soon");
    box.appendChild(b);
  });
}

function renderHome() {
  renderModes();
  $("subtitle").textContent = "1999 to today" + (BANK.length ? " · " + BANK.length + " questions" : "");
  var st = store.get("stats", {played: 0, right: 0, asked: 0, best: 0});
  $("sPlayed").textContent = st.played;
  $("sPct").textContent = st.asked ? Math.round(100 * st.right / st.asked) + "%" : "–";
  $("sStreak").textContent = st.best;
  var seen = store.get("seen", []).length;
  $("seenNote").textContent = BANK.length ? "You've seen " + seen + " of " + BANK.length + " questions. Classic serves unseen ones first." : "";
  renderDailyCard();
  refreshDivStrip();
}

// ---------------------------------------------------------------- daily

function dailyLog() { return store.get("daily", {}); }
function dailyLabel(n) { return n > 0 ? "Daily quiz #" + n : "Daily quiz · preview"; }

function dailyStreak() {
  var log = dailyLog(), d = FQDaily.ukToday();
  if (!log[d]) d = FQDaily.addDays(d, -1);
  var n = 0;
  while (log[d]) { n++; d = FQDaily.addDays(d, -1); }
  return n;
}

function renderDailyCard() {
  var today = FQDaily.ukToday(), n = FQDaily.number(today), rec = dailyLog()[today];
  $("dLabel").textContent = dailyLabel(n);
  var s = dailyStreak();
  $("dFlame").textContent = s ? "🔥 " + s + (s === 1 ? " day" : " days") : "";
  $("dTodo").classList.toggle("hidden", !!rec);
  $("dDone").classList.toggle("hidden", !rec);
  $("dPlay").disabled = !PICKER;
  if (rec) {
    $("dScore").textContent = rec.correct + "/" + rec.total;
    var g = gridEl(rec.grid.split("").map(function (c) { return c === "1"; }));
    var old = $("dGrid"); g.id = "dGrid"; old.parentNode.replaceChild(g, old);
    $("dRank").textContent = rec.rank ? ordinal(rec.rank) + " of " + rec.players + " today so far" : "";
    $("dRank").classList.toggle("hidden", !rec.rank);
  }
  tickCountdown();
}

var countdownDay = null;
function tickCountdown() {
  var t = hms(FQDaily.msToReset());
  $("dCount1").textContent = t; $("dCount2").textContent = t;
  var d = FQDaily.ukToday();
  if (countdownDay && countdownDay !== d && !$("home").classList.contains("hidden")) { countdownDay = d; renderDailyCard(); }
  countdownDay = d;
}

async function dailyIds(date) {
  var local = PICKER.ids(date);
  var cache = store.get("dailyIds", {});
  try {
    var r = await api("/daily?date=" + date, null, 3000);
    var ok = r.ids && r.ids.length === local.length && r.ids.every(function (id) { return BY_ID[id]; });
    if (ok) { cache = {}; cache[date] = r.ids; store.set("dailyIds", cache); return r.ids; }
  } catch (e) { /* offline: fall back */ }
  if (cache[date] && cache[date].every(function (id) { return BY_ID[id]; })) return cache[date];
  return local;
}

async function startDaily() {
  var date = FQDaily.ukToday();
  if (dailyLog()[date]) { renderDailyCard(); return; }
  $("dPlay").disabled = true;
  var ids = await dailyIds(date);
  $("dPlay").disabled = false;
  startGame({mode: "daily", label: dailyLabel(FQDaily.number(date)), timer: 0,
             qs: ids.map(function (id) { return BY_ID[id]; }),
             onFinish: function (g) { finishDaily(g, date); }});
}

function shareDailyText(rec) {
  var n = rec.n > 0 ? "#" + rec.n : "(preview)";
  var squares = rec.grid.split("").map(function (c) { return c === "1" ? "🟩" : "🟥"; }).join("");
  var s = dailyStreak();
  return "Football Quiz " + n + " ⚽ " + rec.correct + "/" + rec.total + "\n" + squares +
         (s > 1 ? "\n🔥 " + s + " day streak" : "") + "\n" + SITE;
}

function finishDaily(g, date) {
  var rec = {n: FQDaily.number(date), correct: g.right, total: g.qs.length, points: g.points,
             grid: g.marks.map(function (m) { return m ? "1" : "0"; }).join("")};
  var log = dailyLog(); log[date] = rec;
  // keep ~a year of history
  var keys = Object.keys(log).sort(); while (keys.length > 400) delete log[keys.shift()];
  store.set("daily", log);
  var s = dailyStreak();
  if (s > store.get("dbest", 0)) store.set("dbest", s);

  $("rLabel").textContent = dailyLabel(rec.n);
  $("final").textContent = rec.correct + "/" + rec.total;
  $("verdict").textContent = verdict(rec.correct / rec.total);
  var grid = gridEl(g.marks); grid.id = "rGrid"; var old = $("rGrid"); old.parentNode.replaceChild(grid, old);
  $("rFlame").textContent = "🔥 " + s + (s === 1 ? " day streak - come back tomorrow" : " day streak");
  $("rFlame").classList.remove("hidden");
  $("rShare").classList.remove("hidden");
  $("rShare").onclick = function () { shareText(shareDailyText(rec)); };
  $("again").classList.add("hidden");
  $("chBox").classList.add("hidden");
  renderMissed(g);
  show("done");
  submitResult("daily", {date: date, answers: g.answers}, function (r, name) {
    var l = dailyLog(); if (l[date]) { l[date].rank = r.rank_today; l[date].players = r.players_today; store.set("daily", l); }
    postMsg("Posted as " + name + ": " + ordinal(r.rank_today) + " of " + r.players_today + " today · 🔥 " + r.streak + " day streak.",
            false, {text: "Daily board", fn: function () { openBoard("daily"); }});
  });
}

// Reset the shared result screen for a non-daily game.
function resultScreen() {
  $("rGrid").classList.add("hidden");
  $("rFlame").classList.add("hidden");
  $("rShare").classList.add("hidden");
  $("again").classList.remove("hidden");
  $("chBox").classList.add("hidden");
  $("missedH").textContent = "What you missed";
  $("postMsg").classList.add("hidden");
  $("postAsk").classList.add("hidden");
}

function verdict(pct) {
  return pct === 1 ? "Perfect. Ballon d'Or material." :
    pct >= .8 ? "World class." : pct >= .6 ? "Solid Champions League level." :
    pct >= .4 ? "Mid-table. Room to improve." : "Relegation form. Try again!";
}

// ---------------------------------------------------------------- classic

function chips(el, map, key) {
  el.innerHTML = "";
  Object.keys(map).forEach(function (v) {
    var b = h("button", {className: "chip"}, map[v]);
    b.setAttribute("aria-pressed", String(settings[key] === v));
    b.onclick = function () { settings[key] = v; store.set("settings", settings); renderSetup(); };
    el.appendChild(b);
  });
}

function pool() {
  return BANK.filter(function (q) {
    return (settings.league === "all" || q.l === settings.league) && (settings.diff === "0" || String(q.d) === settings.diff);
  });
}

function renderSetup() {
  chips($("leagues"), LEAGUES, "league");
  chips($("diffs"), DIFFS, "diff");
  chips($("lengths"), LENGTHS, "len");
  chips($("timers"), TIMERS, "timer");
  var p = pool(), seen = {};
  store.get("seen", []).forEach(function (id) { seen[id] = 1; });
  var fresh = p.filter(function (q) { return !seen[q.id]; }).length;
  $("pool").textContent = p.length ? p.length + " questions match · " + fresh + " you haven't seen yet" : "No questions match these settings.";
  $("start").disabled = !p.length;
}

function openSetup() { renderSetup(); show("setup"); }

function startClassic() {
  var seen = {};
  store.get("seen", []).forEach(function (id) { seen[id] = 1; });
  var p = pool();
  // Unseen questions first, so repeats only start once a pool is exhausted.
  var picked = shuffle(p.filter(function (q) { return !seen[q.id]; }))
    .concat(shuffle(p.filter(function (q) { return seen[q.id]; }))).slice(0, Number(settings.len));
  lastStart = startClassic;
  startGame({mode: "classic", label: "Classic · " + LEAGUES[settings.league], qs: picked,
             timer: Number(settings.timer), fifty: true, onFinish: finishClassic});
}

function finishClassic(g) {
  var n = g.qs.length, r = g.right;
  var st = store.get("stats", {played: 0, right: 0, asked: 0, best: 0});
  st.played++; st.right += r; st.asked += n; st.best = Math.max(st.best, g.best);
  store.set("stats", st);
  $("rLabel").textContent = "Full time";
  $("final").textContent = r + "/" + n;
  $("verdict").textContent = verdict(r / n) + " " + (Math.round(g.points * 10) / 10) + " pts." +
    (g.fiftyOn >= 0 ? " (50:50 used.)" : "");
  resultScreen();
  offerChallenge(g);
  renderMissed(g);
  show("done");
  // Only whole quizzes of 10/20/50 count; a short pool (e.g. Hard + one league) doesn't.
  if (!([10, 20, 50].indexOf(n) >= 0 && n === Number(settings.len))) {
    postMsg("Leaderboard scores need a full 10, 20 or 50-question quiz.");
    return;
  }
  // `right` and `streak` are for the old server; the new one marks `answers` itself.
  var body = {total: n, right: g.byDiff, league: settings.league, diff: Number(settings.diff),
              timer: Number(settings.timer), streak: g.best, answers: g.answers, mode: "classic"};
  submitResult("scores", body, function (res, name) {
    postMsg("Posted as " + name + ": " + res.points + " pts · #" + res.rank_week + " this week · #" + res.rank_all + " all time.",
            false, {text: "View board", fn: function () { openBoard("classic", String(n)); }});
  });
}
