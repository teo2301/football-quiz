// Survival, Blitz, Higher or lower and Career path.
"use strict";
var STATS = null, CAREERS = null, CAREER_FORMS = null;
var hilo = null, career = null;
var MIN_GAP = 0.1;          // higher or lower only pairs numbers at least 10% apart
var CAREER_LEN = 5;

function loadJSON(name) {
  return fetch(name).then(function (r) { if (!r.ok) throw new Error(name); return r.json(); });
}
function needStats() {
  if (STATS) return Promise.resolve(STATS);
  return loadJSON("stats.json").then(function (d) { STATS = d; return d; });
}
function needCareers() {
  if (CAREERS) return Promise.resolve(CAREERS);
  return loadJSON("careers.json").then(function (d) {
    CAREERS = d.players; CAREER_FORMS = FQMatch.buildForms(CAREERS); return CAREERS;
  });
}

// Personal bests per mode, kept on this phone.
function bests() { return store.get("bests", {}); }
function saveBest(mode, score) {
  var b = bests(), isNew = !(b[mode] >= score) && score > 0;
  if (isNew) { b[mode] = score; store.set("bests", b); }
  return isNew;
}

function postRun(mode, answers, label) {
  submitResult("scores", {mode: mode, answers: answers}, function (r, name) {
    postMsg("Posted as " + name + ": " + label(r) + " · #" + r.rank_week + " this week · #" + r.rank_all + " all time.",
            false, {text: "View board", fn: function () { openBoard(mode); }});
  });
}

function runResult(label, big, text, isBest) {
  resultScreen();
  $("rLabel").textContent = label;
  $("final").textContent = big;
  $("verdict").textContent = text + (isBest ? " New personal best!" : "");
}

// ---------------------------------------------------------------- survival

// Easy to start, then Medium, then Medium and Hard mixed - all shuffled, unseen first.
function survivalQueue() {
  var seen = {};
  store.get("seen", []).forEach(function (id) { seen[id] = 1; });
  function pool(d) {
    var p = BANK.filter(function (q) { return q.d === d; });
    return shuffle(p.filter(function (q) { return !seen[q.id]; })).concat(shuffle(p.filter(function (q) { return seen[q.id]; })));
  }
  var e = pool(1), m = pool(2), hd = pool(3), out = e.splice(0, 5).concat(m.splice(0, 10));
  var rest = shuffle(m.concat(hd));
  rest.sort(function (a, b) { return (seen[a.id] ? 1 : 0) - (seen[b.id] ? 1 : 0); });
  return out.concat(rest, e);
}

function startSurvival() {
  if (!BANK.length) return;
  lastStart = startSurvival;
  startGame({mode: "survival", label: "Survival", qs: survivalQueue(), endless: true, suddenDeath: true,
             onFinish: finishSurvival});
}

function finishSurvival(g) {
  var n = g.right, best = saveBest("survival", n);
  runResult("Survival", String(n), n === 0 ? "Out first ball. Shake it off." :
    (n === 1 ? "One right before the red card." : n + " in a row before the red card.") +
    (n >= 20 ? " Iron man." : n >= 10 ? " Proper staying power." : ""), best);
  renderMissed(g);
  show("done");
  achCheck({k: "run"});
  if (!g.answers.length) return;
  postRun("survival", g.answers, function (r) { return r.score + " in a row"; });
}

// ---------------------------------------------------------------- blitz

function startBlitz() {
  if (!BANK.length) return;
  lastStart = startBlitz;
  var qs = shuffle(BANK.filter(function (q) { return q.d < 3; })).slice(0, 80);
  startGame({mode: "blitz", label: "Blitz · 60 seconds", qs: qs, endless: true, clockSecs: 60, onFinish: finishBlitz});
}

function finishBlitz(g) {
  var n = g.right, best = saveBest("blitz", n), tot = g.answers.length;
  runResult("Blitz · time's up", String(n), (n === 1 ? "1 correct" : n + " correct") + " from " + tot + " answered." +
    (n >= 20 ? " Lightning." : n >= 12 ? " Sharp." : ""), best);
  renderMissed(g);
  show("done");
  achCheck({k: "run"});
  if (!tot) { postMsg("Answer at least one question to get on the board."); return; }
  postRun("blitz", g.answers, function (r) { return r.score + " correct"; });
}

// ---------------------------------------------------------------- higher or lower

function fmtStat(it, hidden) {
  var cat = STATS.cats[it.c];
  if (hidden) return "?";
  if (cat.money) return "£" + it.v + "m";
  var s = String(it.v).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return (cat.approx ? "about " : "") + s;
}
function unitOf(it) { var c = STATS.cats[it.c]; return c.money ? "transfer fee" : c.approx ? "capacity" : c.unit; }

function hiloPair() {
  var items = STATS.items;
  for (var tries = 0; tries < 200; tries++) {
    var a = items[Math.floor(Math.random() * items.length)];
    var cands = items.filter(function (b) {
      return b.c === a.c && b !== a && !hilo.used[b.id] && Math.abs(a.v - b.v) / Math.max(a.v, b.v) >= MIN_GAP;
    });
    if (cands.length) return {a: a, b: cands[Math.floor(Math.random() * cands.length)]};
  }
  return null;
}

function startHilo() {
  needStats().then(function () {
    lastStart = startHilo;
    hilo = {answers: [], used: {}, streak: 0, over: false, pair: null, log: []};
    show("hilo");
    hiloAsk();
  }).catch(function () { toast("Couldn't load the numbers. Check your connection."); });
}

function statCard(it, el, hidden, cls) {
  el.className = "card statcard" + (cls ? " " + cls : "");
  el.innerHTML = "";
  el.appendChild(h("span", {className: "tag"}, STATS.cats[it.c].name));
  el.appendChild(h("p", {className: "who"}, it.n));
  el.appendChild(h("p", {className: "sub"}, it.d));
  var approx = !hidden && STATS.cats[it.c].approx;
  el.appendChild(h("p", {className: "num"}, approx ? h("small", null, "about") : null, approx ? fmtStat(it).replace(/^about /, "") : fmtStat(it, hidden)));
  el.appendChild(h("p", {className: "unit"}, hidden ? "Higher or lower?" : unitOf(it)));
}

function hiloAsk() {
  var p = hiloPair();
  if (!p) { hilo.over = true; return finishHilo(); }
  hilo.pair = p;
  $("hiStreak").textContent = hilo.streak;
  $("hiBest").textContent = bests().hilo || 0;
  $("hiCat").textContent = STATS.cats[p.a.c].name;
  statCard(p.a, $("hiA"), false);
  statCard(p.b, $("hiB"), true);
  $("hiAsk").textContent = "Does " + p.b.n + " have a higher or lower " + (STATS.cats[p.b.c].money ? "fee" : STATS.cats[p.b.c].approx ? "capacity" : "number") + "?";
  $("hiBtns").classList.remove("hidden");
  $("hiNext").classList.add("hidden");
  $("hiNote").textContent = "";
  window.scrollTo(0, 0);
}

function hiloGuess(g) {
  if (!hilo || !hilo.pair || hilo.locked) return;
  hilo.locked = true;
  var a = hilo.pair.a, b = hilo.pair.b;
  var ok = b.v === a.v || (b.v > a.v) === (g === "hi");
  hilo.used[b.id] = 1;
  hilo.answers.push({a: a.id, b: b.id, g: g});
  hilo.log.push({a: a, b: b, ok: ok});
  if (ok) hilo.streak++; else hilo.over = true;
  statCard(b, $("hiB"), false, ok ? "good" : "bad");
  answerFeel(ok);
  $("hiStreak").textContent = hilo.streak;
  $("hiAsk").textContent = (ok ? "Yes! " : "No. ") + b.n + ": " + fmtStat(b) + (b.v > a.v ? ", higher than " : ", lower than ") + fmtStat(a) + ".";
  $("hiNote").textContent = b.note + ".";
  $("hiBtns").classList.add("hidden");
  $("hiNext").textContent = ok ? "Next" : "Full time";
  $("hiNext").classList.remove("hidden");
}

function hiloNext() {
  if (!hilo) return;
  hilo.locked = false;
  if (hilo.over) return finishHilo();
  hiloAsk();
}

function finishHilo() {
  var hl = hilo; hilo = null;
  sfx("whistle");
  var n = hl.streak, best = saveBest("hilo", n);
  runResult("Higher or lower", String(n), (n === 1 ? "1 right call" : n + " right calls") + " in a row." +
    (n >= 15 ? " Walking almanac." : n >= 8 ? " Strong instincts." : ""), best);
  var ul = $("missed"); ul.innerHTML = "";
  var miss = hl.log.filter(function (x) { return !x.ok; })[0];
  $("missedH").textContent = "Where it ended";
  $("missedH").classList.toggle("hidden", !miss);
  if (miss) ul.appendChild(h("li", {className: "tile"},
    h("div", null, miss.b.n + " (" + miss.b.d + ") vs " + miss.a.n + " (" + miss.a.d + ")"),
    h("div", {className: "ans"}, "✓ " + fmtStat(miss.b) + " vs " + fmtStat(miss.a) + " · " + STATS.cats[miss.b.c].name),
    h("div", {className: "yours"}, "✗ You said " + (miss.b.v > miss.a.v ? "lower" : "higher"))));
  show("done");
  achCheck({k: "run"});
  if (!hl.answers.length) return;
  postRun("hilo", hl.answers, function (r) { return r.score + " in a row"; });
}

// ---------------------------------------------------------------- career path

function careerClues(p) {
  return p.clubs.map(function (c) { return {k: "club", t: c}; })
    .concat([{k: "Nationality", t: p.nat}, {k: "Position", t: p.pos}]);
}

function startCareer() {
  needCareers().then(function () {
    lastStart = startCareer;
    var seen = store.get("cseen", []), s = {};
    seen.forEach(function (id) { s[id] = 1; });
    var pool = shuffle(CAREERS.filter(function (p) { return !s[p.id]; })).concat(shuffle(CAREERS.filter(function (p) { return s[p.id]; })));
    career = {players: pool.slice(0, CAREER_LEN), i: 0, clue: 1, points: 0, answers: [], log: [], done: false};
    show("career");
    careerAsk();
  }).catch(function () { toast("Couldn't load the players. Check your connection."); });
}

function careerRender() {
  var c = career, p = c.players[c.i], clues = careerClues(p);
  $("cNum").textContent = (c.i + 1) + "/" + CAREER_LEN;
  $("cPts").textContent = c.points;
  $("cWorth").textContent = c.done ? "–" : String(FQMatch.points(c.clue));
  var ol = $("cPath"); ol.innerHTML = "";
  var shown = c.done ? clues.length : c.clue, clubNo = 0;
  for (var i = 0; i < clues.length; i++) {
    var cl = clues[i];
    if (cl.k === "club") clubNo++;
    if (i >= shown) continue;
    var loan = cl.k === "club" && / \(loan\)$/.test(cl.t);
    ol.appendChild(h("li", {className: cl.k === "club" ? "club" : "info"},
      h("span", {className: "dot"}, cl.k === "club" ? String(clubNo) : ""),
      h("span", {className: "nm"}, cl.k === "club" ? cl.t.replace(/ \(loan\)$/, "") : cl.k + ": " + cl.t),
      loan ? h("span", {className: "loan"}, "loan") : null));
  }
  var left = clues.length - shown;
  $("cLeft").textContent = c.done ? "" : left ? left + (left === 1 ? " more clue" : " more clues") : "No more clues";
  $("cClue").disabled = !left || c.done;
  $("cAsk").classList.toggle("hidden", c.done);
  $("cRes").classList.toggle("hidden", !c.done);
}

function careerAsk() {
  career.clue = 1; career.done = false; career.wrong = [];
  $("cGuess").value = "";
  $("cMsg").textContent = "";
  careerRender();
  window.scrollTo(0, 0);
}

function careerSettle(ok, guess) {
  var c = career, p = c.players[c.i];
  var pts = ok ? FQMatch.points(c.clue) : 0;
  c.points += pts; c.done = true;
  c.answers.push({id: p.id, guess: guess, clues: c.clue});
  c.log.push({p: p, pts: pts, clue: c.clue});
  answerFeel(ok);
  var cs = store.get("cseen", []); if (cs.indexOf(p.id) < 0) { cs.push(p.id); store.set("cseen", cs); }
  $("cResName").textContent = p.n;
  $("cResLine").textContent = ok ? "Got him with " + c.clue + (c.clue === 1 ? " clue" : " clues") + ": +" + pts + (pts === 1 ? " point" : " points") : "Not this time: 0 points";
  $("cResLine").className = ok ? "good" : "bad";
  $("cNext").textContent = c.i + 1 < CAREER_LEN ? "Next player" : "Full time";
  careerRender();
  try { $("cNext").focus({preventScroll: true}); } catch (e) { /* old browsers */ }
}

function careerGuess() {
  var c = career;
  if (!c || c.done) return;
  var g = $("cGuess").value.trim();
  if (!g) { $("cGuess").focus(); return; }
  var p = c.players[c.i];
  if (FQMatch.isMatch(g, CAREER_FORMS[p.id])) return careerSettle(true, g);
  var total = careerClues(p).length;
  if (c.clue >= total) return careerSettle(false, g);
  c.clue++;
  $("cMsg").textContent = "Not " + g + ". Here's another clue.";
  $("cGuess").value = "";
  careerRender();
}

function careerClue() {
  var c = career;
  if (!c || c.done) return;
  if (c.clue < careerClues(c.players[c.i]).length) { c.clue++; $("cMsg").textContent = ""; careerRender(); }
}

function careerGiveUp() { if (career && !career.done) careerSettle(false, null); }

function careerNext() {
  var c = career;
  if (!c) return;
  if (c.i + 1 < CAREER_LEN) { c.i++; careerAsk(); return; }
  career = null;
  sfx("whistle");
  var best = saveBest("career", c.points), right = c.log.filter(function (x) { return x.pts; }).length;
  runResult("Career path", c.points + "/" + CAREER_LEN * 5, right + " of " + CAREER_LEN + " players named." +
    (c.points >= 20 ? " Scout of the year." : c.points >= 12 ? " Knows the game." : ""), best);
  var ul = $("missed"); ul.innerHTML = "";
  $("missedH").textContent = "Your players";
  $("missedH").classList.remove("hidden");
  c.log.forEach(function (x) {
    ul.appendChild(h("li", {className: "tile"},
      h("div", {className: x.pts ? "ans" : "yours"}, (x.pts ? "✓ " : "✗ ") + x.p.n + (x.pts ? " · +" + x.pts : " · 0")),
      h("div", {className: "path"}, x.p.clubs.join(" → "))));
  });
  show("done");
  achCheck({k: "career", firstClub: c.log.some(function (x) { return x.pts && x.clue === 1; })});
  if (c.points === CAREER_LEN * 5) celebrate("Goal!", "Every player from his first club");
  postRun("career", c.answers, function (r) { return r.score + " pts"; });
}
