// The quiz engine shared by every mode: scoreboard, progress, answers, explanation.
"use strict";
var game = null;
var clockId = null;
var TICK = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5l3.2 3L13 4.5"/></svg>';
var CROSS = '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M4 4l8 8M12 4l-8 8"/></svg>';

function timerMult(t) { return {0: 1, 20: 1.25, 10: 1.5}[t] || 1; }

var autoNextId = null;
var BLITZ_PENALTY = 3000;   // a wrong answer in Blitz costs 3 seconds

// opts: {mode, label, qs, timer, onFinish,
//        endless: no fixed length (survival/blitz: no progress bar, counts instead),
//        suddenDeath: the first wrong answer ends it (survival),
//        clockSecs: one clock for the whole game, no explanations, auto-advance (blitz),
//        fifty: offer the 50/50 lifeline once (classic)}
function startGame(opts) {
  game = {mode: opts.mode, label: opts.label, qs: opts.qs, timer: opts.timer || 0, onFinish: opts.onFinish,
          endless: !!opts.endless, suddenDeath: !!opts.suddenDeath, clockSecs: opts.clockSecs || 0,
          fifty: opts.fifty ? "ready" : null, fiftyOn: -1, over: false,
          i: 0, answers: [], marks: [], byDiff: {1: 0, 2: 0, 3: 0}, right: 0, streak: 0, best: 0,
          missed: [], points: 0, started: Date.now(), qStarted: 0, locked: false};
  game.deadline = game.started + game.clockSecs * 1000;
  $("mname").textContent = opts.label;
  $("sbClockL").textContent = game.timer || game.clockSecs ? "Clock" : "Time";
  $("sbQL").textContent = "Question";
  $("sbPL").textContent = game.endless ? "Correct" : "Points";
  $("play").classList.toggle("blitz", !!game.clockSecs);
  var segs = $("segs"); segs.innerHTML = "";
  segs.classList.toggle("hidden", game.endless);
  if (!game.endless) for (var i = 0; i < game.qs.length; i++) segs.appendChild(h("i"));
  show("play");
  ask();
  stopClock();
  clockId = setInterval(tickClock, 250);
}

function stopClock() {
  if (clockId) { clearInterval(clockId); clockId = null; }
  if (autoNextId) { clearTimeout(autoNextId); autoNextId = null; }
}

function tickClock() {
  if (!game) return stopClock();
  var c = $("sbC");
  if (game.clockSecs) {
    var ms = Math.max(0, game.deadline - Date.now()), secs = Math.ceil(ms / 1000);
    c.textContent = Math.floor(secs / 60) + ":" + pad2(secs % 60);
    c.classList.toggle("low", secs <= 10);
    if (ms <= 0) finishGame();
  } else if (game.timer) {
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
  $("sbQ").textContent = game.endless ? String(game.i + 1) : (game.i + 1) + "/" + game.qs.length;
  $("sbP").textContent = game.endless ? String(game.right) : String(Math.round(game.points * 10) / 10);
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
  var ff = $("fifty");
  ff.classList.toggle("hidden", !game.fifty);
  ff.disabled = game.fifty !== "ready";
  ff.textContent = game.fifty === "ready" ? "50:50" : "50:50 used";
  var box = $("opts"); box.innerHTML = "";
  shuffle([q.a].concat(q.w)).forEach(function (o) {
    var b = h("button", {className: "opt"}, h("span", {className: "t"}, o));
    b.setAttribute("data-v", o);
    b.onclick = function () { answer(o); };
    box.appendChild(b);
  });
  tickClock();
}

// Lifeline: knock out two wrong answers; this question then scores half.
function useFifty() {
  if (!game || game.locked || game.fifty !== "ready") return;
  var q = game.qs[game.i];
  game.fifty = "used"; game.fiftyOn = game.i;
  var btns = [].slice.call($("opts").children).filter(function (b) { return b.getAttribute("data-v") !== q.a; });
  shuffle(btns).slice(0, 2).forEach(function (b) { b.disabled = true; b.classList.add("dim", "out"); });
  $("fifty").disabled = true; $("fifty").textContent = "50:50 used";
}

function answer(choice) {
  if (!game || game.locked) return;
  game.locked = true;
  $("fifty").disabled = true;
  var q = game.qs[game.i];
  var ok = choice === q.a, half = game.fiftyOn === game.i;
  var btns = $("opts").children;
  for (var i = 0; i < btns.length; i++) {
    var b = btns[i], v = b.getAttribute("data-v");
    b.disabled = true;
    if (v === q.a) { b.classList.add("right"); b.appendChild(h("span", {className: "st", innerHTML: TICK + "Correct"})); }
    else if (v === choice) { b.classList.add("wrong"); b.appendChild(h("span", {className: "st", innerHTML: CROSS + "Your answer"})); }
    else b.classList.add("dim");
  }
  var a = {id: q.id, choice: choice};
  if (half) a.fifty = true;
  game.answers.push(a);
  game.marks.push(ok);
  if (ok) {
    game.right++; game.byDiff[q.d]++; game.streak++; game.best = Math.max(game.best, game.streak);
    game.points += q.d * timerMult(game.timer) * (half ? 0.5 : 1);
  } else {
    game.streak = 0; game.missed.push({q: q, choice: choice});
    if (game.suddenDeath) game.over = true;
    if (game.clockSecs) game.deadline -= BLITZ_PENALTY;
  }
  if (ok) tallyCorrect(q);
  answerFeel(ok, !!game.clockSecs);      // Blitz is too quick for crowd noise: buzz only
  var seen = store.get("seen", []);
  if (seen.indexOf(q.id) < 0) { seen.push(q.id); store.set("seen", seen); }
  renderBoardStrip();
  if (game.clockSecs) {           // Blitz: no explanation, straight on
    tickClock();
    if (game) autoNextId = setTimeout(function () { autoNextId = null; if (game) nextQuestion(); }, ok ? 380 : 650);
    return;
  }
  $("factText").innerHTML = "";
  $("factText").appendChild(h("span", {className: "verdict"},
    ok ? (half ? "Correct, for half points. " : "Correct. ") : (choice === null ? "Time's up. " : "Not quite. ")));
  $("factText").appendChild(document.createTextNode(q.x || ""));
  $("fact").classList.remove("hidden");
  var c = $("sbC"); if (game.timer) c.classList.remove("low");
  var n = $("next");
  n.textContent = game.over || game.i + 1 >= game.qs.length ? "Full time" : "Next question";
  n.classList.remove("hidden");
  try { n.focus({preventScroll: true}); } catch (e) { /* old browsers */ }
}

function nextQuestion() {
  if (!game) return;
  game.i++;
  if (!game.over && game.i < game.qs.length) { ask(); window.scrollTo(0, 0); return; }
  finishGame();
}

function finishGame() {
  if (!game) return;
  stopClock();
  game.secs = Math.round((Date.now() - game.started) / 1000);
  var g = game;
  game = null;
  sfx("whistle");
  g.onFinish(g);
}

function quitGame() {
  stopClock();
  game = null; hilo = null; career = null;
  goHome();
}

// Result screen bits shared by modes.
function renderMissed(g) {
  var ul = $("missed"); ul.innerHTML = "";
  $("missedH").textContent = "What you missed";
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
