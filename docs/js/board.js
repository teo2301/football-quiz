// Boards tab: Daily (today / this week) and Classic (week / all time x length).
"use strict";
var boardState = {kind: "daily", dperiod: "today", period: "week", len: "10"};
var KINDS = {daily: "Daily", classic: "Classic"};
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

function row(pos, name, meta, pts, mine) {
  return h("li", {className: "tile" + (mine ? " me" : "")},
    h("span", {className: "pos"}, pos < 4 ? ["🥇", "🥈", "🥉"][pos - 1] : String(pos)),
    h("span", {className: "nm"}, h("span", {className: "n"}, name), meta),
    h("span", {className: "pts"}, pts));
}

async function loadBoard() {
  var seq = ++boardSeq, daily = boardState.kind === "daily";
  boardChips($("bKind"), KINDS, "kind", true);
  if (daily) boardChips($("bPeriod"), DPERIODS, "dperiod");
  else boardChips($("bPeriod"), PERIODS, "period");
  $("bLen").classList.toggle("hidden", daily);
  if (!daily) boardChips($("bLen"), {10: "10 Qs", 20: "20 Qs", 50: "50 Qs"}, "len");
  var today = FQDaily.ukToday();
  $("bTitle").textContent = daily
    ? (boardState.dperiod === "today" ? dailyLabel(FQDaily.number(today)) : "Daily quiz · last 7 days")
    : PERIODS[boardState.period] + " · " + boardState.len + " questions";
  var ul = $("lb"); ul.innerHTML = ""; $("bMsg").textContent = "Loading…";
  var me = myName().toLowerCase();
  $("bNote").textContent = daily
    ? "Ranked by correct answers, then points (1 / 2 / 3 for Easy / Medium / Hard), then who finished first. 🔥 = daily streak."
    : "Best game per player. 1 / 2 / 3 points per correct Easy / Medium / Hard answer, ×1.25 with the 20 s timer, ×1.5 with 10 s.";
  try {
    var rows;
    if (daily) rows = (await api("/daily/board?period=" + boardState.dperiod)).rows;
    else rows = (await api("/board?len=" + boardState.len + "&period=" + boardState.period)).rows;
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
      } else {
        meta = h("span", {className: "meta"}, r.correct + "/" + r.total + " · " + LEAGUES[r.league] + " · " + DIFFS[r.diff] + (r.timer ? " · " + r.timer + " s" : ""));
        pts = String(r.points);
      }
      ul.appendChild(row(i + 1, r.name, meta, pts, mine));
    });
  } catch (e) {
    if (seq !== boardSeq) return;
    $("bMsg").textContent = e.status === 404 ? "The daily board isn't live yet." : "Couldn't reach the board. Check your connection.";
  }
}
