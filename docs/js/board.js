// Boards tab: Daily (today / this week), Classic (week / all time x length)
// and one board per mode (week / all time).
"use strict";
var boardState = {kind: "daily", dperiod: "today", period: "week", len: "10"};
var KINDS = {daily: "Daily", classic: "Classic", survival: "Survival", blitz: "Blitz", hilo: "Higher or lower", career: "Career path"};
var RUN_NOTES = {
  survival: "Best run per player: correct answers in a row before the first miss.",
  blitz: "Best run per player: correct answers in 60 seconds. A wrong answer costs 3 seconds.",
  hilo: "Best run per player: right higher-or-lower calls in a row.",
  career: "Best game per player: 5 players, 5 points each if you name him from his first club, down to 1."
};
var DPERIODS = {today: "Today", week: "This week"};
var PERIODS = {week: "This week", all: "All time"};
var boardSeq = 0;

function openBoard(kind, len) {
  if (kind) boardState.kind = kind;
  if (len) boardState.len = len;
  show("boards");
  loadBoard();
}

function boardChips(el, map, key, seg) {
  el.innerHTML = "";
  Object.keys(map).forEach(function (v) {
    var b = h("button", {className: seg ? "" : "chip"}, map[v]);
    b.setAttribute("aria-pressed", String(boardState[key] === v));
    b.onclick = function () { boardState[key] = v; loadBoard(); };
    el.appendChild(b);
  });
}

function row(pos, name, meta, pts, mine, badges) {
  var b = badgeRow(badges);
  return h("li", {className: "tile" + (mine ? " me" : "")},
    h("span", {className: "pos"}, pos < 4 ? ["🥇", "🥈", "🥉"][pos - 1] : String(pos)),
    h("span", {className: "nm"}, b ? h("span", {className: "nmrow"}, h("span", {className: "n"}, name), b) : h("span", {className: "n"}, name), meta),
    h("span", {className: "pts"}, pts));
}

function runMeta(kind, r) {
  if (kind === "survival") return r.score + " in a row";
  if (kind === "blitz") return r.correct + " correct of " + r.total + " answered";
  if (kind === "hilo") return r.score + " right calls in a row";
  return r.correct + " of " + r.total + " players named";
}

async function loadBoard() {
  var seq = ++boardSeq, kind = boardState.kind, daily = kind === "daily", classic = kind === "classic";
  boardChips($("bKind"), KINDS, "kind");
  if (daily) boardChips($("bPeriod"), DPERIODS, "dperiod");
  else boardChips($("bPeriod"), PERIODS, "period");
  $("bLen").classList.toggle("hidden", !classic);
  if (classic) boardChips($("bLen"), {10: "10 Qs", 20: "20 Qs", 50: "50 Qs"}, "len");
  var today = FQDaily.ukToday();
  $("bTitle").textContent = daily
    ? (boardState.dperiod === "today" ? dailyLabel(FQDaily.number(today)) : "Daily quiz · last 7 days")
    : classic ? PERIODS[boardState.period] + " · " + boardState.len + " questions"
    : KINDS[kind] + " · " + PERIODS[boardState.period].toLowerCase();
  var ul = $("lb"); ul.innerHTML = ""; $("bMsg").textContent = "Loading…";
  var me = myName().toLowerCase();
  $("bNote").textContent = daily
    ? "Ranked by correct answers, then points (1 / 2 / 3 for Easy / Medium / Hard), then who finished first. 🔥 = daily streak."
    : classic ? "Best game per player. 1 / 2 / 3 points per correct Easy / Medium / Hard answer, ×1.25 with the 20 s timer, ×1.5 with 10 s. The 50:50 halves that question's points."
    : RUN_NOTES[kind];
  try {
    var rows;
    if (daily) rows = (await api("/daily/board?period=" + boardState.dperiod)).rows;
    else if (classic) rows = (await api("/board?len=" + boardState.len + "&period=" + boardState.period)).rows;
    else rows = (await api("/board?mode=" + kind + "&period=" + boardState.period)).rows;
    if (seq !== boardSeq) return;
    $("bMsg").textContent = rows.length ? "" : (daily ? "Nobody has played yet. Be the first!" : "No scores yet. Be the first!");
    rows.forEach(function (r, i) {
      var mine = me && r.name.toLowerCase() === me, meta, pts;
      if (daily && boardState.dperiod === "today") {
        var g = gridEl(r.grid.split("").map(function (c) { return c === "1"; }));
        meta = h("span", {className: "meta"}, g, r.streak > 1 ? " 🔥 " + r.streak : "");
        pts = r.correct + "/" + r.total;
      } else if (daily) {
        meta = h("span", {className: "meta"}, r.days + (r.days === 1 ? " day" : " days") + " · " + r.points + " pts" + (r.streak > 1 ? " · 🔥 " + r.streak : ""));
        pts = String(r.correct);
      } else if (!classic) {
        meta = h("span", {className: "meta"}, runMeta(kind, r));
        pts = String(r.score);
      } else {
        meta = h("span", {className: "meta"}, r.correct + "/" + r.total + " · " + LEAGUES[r.league] + " · " + DIFFS[r.diff] + (r.timer ? " · " + r.timer + " s" : ""));
        pts = String(r.points);
      }
      ul.appendChild(row(i + 1, r.name, meta, pts, mine, r.badges));
    });
  } catch (e) {
    if (seq !== boardSeq) return;
    $("bMsg").textContent = e.status === 404 ? "The daily board isn't live yet." :
      e.status === 400 && !daily && !classic ? "This board isn't live yet." : "Couldn't reach the board. Check your connection.";
  }
}
