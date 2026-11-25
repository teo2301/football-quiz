// The quiz engine shared by every mode: scoreboard, progress, answers, explanation.
"use strict";
var game = null;
var clockId = null;
var TICK = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5l3.2 3L13 4.5"/></svg>';
var CROSS = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M4 4l8 8M12 4l-8 8"/></svg>';

function timerMult(t) { return {0: 1, 20: 1.25, 10: 1.5}[t] || 1; }

// opts: {mode, label, qs, timer, onFinish}
function startGame(opts) {
  game = {mode: opts.mode, label: opts.label, qs: opts.qs, timer: opts.timer || 0, onFinish: opts.onFinish,
          i: 0, answers: [], marks: [], byDiff: {1: 0, 2: 0, 3: 0}, right: 0, streak: 0, best: 0,
          missed: [], points: 0, started: Date.now(), qStarted: 0, locked: false};
  $("mname").textContent = opts.label;
  $("sbClockL").textContent = game.timer ? "Clock" : "Time";
  var segs = $("segs"); segs.innerHTML = "";
  for (var i = 0; i < game.qs.length; i++) segs.appendChild(h("i"));
  show("play");
  ask();
  stopClock();
  clockId = setInterval(tickClock, 250);
}

function stopClock() { if (clockId) { clearInterval(clockId); clockId = null; } }

function tickClock() {
  if (!game) return stopClock();
  var c = $("sbC");
  if (game.timer) {
    if (game.locked) return;
    var left = Math.max(0, game.timer - Math.floor((Date.now() - game.qStarted) / 1000));
    c.textContent = "0:" + pad2(left);
    c.classList.toggle("low", left <= 5);
    if (left <= 0) answer(null);
  } else {
    var s = Math.floor((Date.now() - game.started) / 1000);
    c.textContent = Math.floor(s / 60) + ":" + pad2(s % 60);
    c.classList.remove("low");
  }
}

function renderBoardStrip() {
  $("sbQ").textContent = (game.i + 1) + "/" + game.qs.length;
  $("sbP").textContent = String(Math.round(game.points * 10) / 10);
  var segs = $("segs").children;
  for (var i = 0; i < segs.length; i++) {
    segs[i].className = i < game.marks.length ? (game.marks[i] ? "ok" : "no") : (i === game.i ? "now" : "");
  }
}

function ask() {
  var q = game.qs[game.i];
  game.locked = false;
  game.qStarted = Date.now();
  renderBoardStrip();
  $("qtag").textContent = LEAGUES[q.l] + " · " + DIFFS[q.d];
  $("qtext").textContent = q.q;
  $("fact").classList.add("hidden");
  $("next").classList.add("hidden");
  var box = $("opts"); box.innerHTML = "";
  shuffle([q.a].concat(q.w)).forEach(function (o) {
    var b = h("button", {className: "opt"}, h("span", {className: "t"}, o));
    b.setAttribute("data-v", o);
    b.onclick = function () { answer(o); };
    box.appendChild(b);
  });
  tickClock();
}

function answer(choice) {
  if (!game || game.locked) return;
  game.locked = true;
  var q = game.qs[game.i];
  var ok = choice === q.a;
  var btns = $("opts").children;
  for (var i = 0; i < btns.length; i++) {
    var b = btns[i], v = b.getAttribute("data-v");
    b.disabled = true;
    if (v === q.a) { b.classList.add("right"); b.appendChild(h("span", {className: "st", innerHTML: TICK + "Correct"})); }
    else if (v === choice) { b.classList.add("wrong"); b.appendChild(h("span", {className: "st", innerHTML: CROSS + "Your answer"})); }
    else b.classList.add("dim");
  }
  game.answers.push({id: q.id, choice: choice});
  game.marks.push(ok);
  if (ok) {
    game.right++; game.byDiff[q.d]++; game.streak++; game.best = Math.max(game.best, game.streak);
    game.points += q.d * timerMult(game.timer);
  } else { game.streak = 0; game.missed.push({q: q, choice: choice}); }
  var seen = store.get("seen", []);
  if (seen.indexOf(q.id) < 0) { seen.push(q.id); store.set("seen", seen); }
  $("factText").innerHTML = "";
  $("factText").appendChild(h("span", {className: "verdict"},
    ok ? "Correct. " : (choice === null ? "Time's up. " : "Not quite. ")));
  $("factText").appendChild(document.createTextNode(q.x || ""));
  $("fact").classList.remove("hidden");
  var c = $("sbC"); if (game.timer) c.classList.remove("low");
  renderBoardStrip();
  $("sbQ").textContent = (game.i + 1) + "/" + game.qs.length;
  var n = $("next");
  n.textContent = game.i + 1 < game.qs.length ? "Next question" : "Full time";
  n.classList.remove("hidden");
  try { n.focus({preventScroll: true}); } catch (e) { /* old browsers */ }
}

function nextQuestion() {
  game.i++;
  if (game.i < game.qs.length) { ask(); window.scrollTo(0, 0); return; }
  stopClock();
  game.secs = Math.round((Date.now() - game.started) / 1000);
  var g = game;
  g.onFinish(g);
}

function quitGame() {
  stopClock();
  game = null;
  goHome();
}

// Result screen bits shared by modes.
function renderMissed(g) {
  var ul = $("missed"); ul.innerHTML = "";
  $("missedH").classList.toggle("hidden", !g.missed.length);
  g.missed.forEach(function (m) {
    ul.appendChild(h("li", {className: "tile"},
      h("div", null, m.q.q),
      h("div", {className: "ans"}, "✓ " + m.q.a),
      m.choice !== null ? h("div", {className: "yours"}, "✗ " + m.choice) : h("div", {className: "yours"}, "✗ Ran out of time")));
  });
}

function gridEl(marks) {
  var d = h("div", {className: "grid7"});
  for (var i = 0; i < marks.length; i++) d.appendChild(h("i", {className: marks[i] ? "" : "x"}));
  return d;
}
