// Achievements (earned from this phone's own history, synced so boards can show them),
// haptics, sound effects and the perfect-game celebration.
"use strict";

// ---------------------------------------------------------------- feel: haptics + sounds

var feel = Object.assign({haptics: true, sound: false}, store.get("feel", {}));
function setFeel(k, v) { feel[k] = v; store.set("feel", feel); }
function canVibrate() { return typeof navigator.vibrate === "function"; }

// Short buzz when right, double buzz when wrong.
function buzz(ok) {
  if (!feel.haptics || !canVibrate()) return;
  // Chrome refuses (and logs) vibrations before the first real tap
  if (navigator.userActivation && !navigator.userActivation.hasBeenActive) return;
  try { navigator.vibrate(ok ? 25 : [45, 70, 45]); } catch (e) { /* not allowed yet */ }
}

// A tiny tick on every 3D button press (replaced by the answer buzz when there is one).
function tick() {
  if (!feel.haptics || !canVibrate()) return;
  if (navigator.userActivation && !navigator.userActivation.hasBeenActive) return;
  try { navigator.vibrate(8); } catch (e) { /* not allowed */ }
}

var SOUNDS = {cheer: "sounds/cheer.mp3", groan: "sounds/groan.mp3", whistle: "sounds/whistle.mp3"};
var VOLUME = {cheer: 0.55, groan: 0.5, whistle: 0.45};
var audioCache = {};
function sfx(name) {
  if (!feel.sound || !SOUNDS[name]) return;
  try {
    var a = audioCache[name];
    if (!a) { a = new Audio(SOUNDS[name]); a.preload = "auto"; audioCache[name] = a; }
    a.volume = VOLUME[name];
    a.currentTime = 0;
    var p = a.play();
    if (p && p.catch) p.catch(function () { /* autoplay blocked: stay quiet */ });
  } catch (e) { /* no audio */ }
}
function answerFeel(ok, quiet) { buzz(ok); if (!quiet) sfx(ok ? "cheer" : "groan"); }

// ---------------------------------------------------------------- achievements

var LEAGUE_GOAL = 50;
function achHave() { return store.get("ach", {}); }
function tallies() { return store.get("tally", {all: 0}); }

// Every correct answer in a quiz mode counts towards its league badge and the 500 club.
function tallyCorrect(q) {
  var t = tallies();
  t.all = (t.all || 0) + 1;
  t[q.l] = (t[q.l] || 0) + 1;
  store.set("tally", t);
}

var achQueue = [], achShowing = false, achSyncTimer = null;

function award(ids, quiet) {
  var have = achHave(), got = [];
  ids.forEach(function (id) {
    if (!FQAch.BY[id] || have[id]) return;
    have[id] = Date.now(); got.push(id);
  });
  if (!got.length) return;
  store.set("ach", have);
  if (!quiet) got.forEach(function (id) { achQueue.push(id); });
  // a tick later, so a goal celebration started by the same result gets to show first
  if (!achShowing) { achShowing = true; setTimeout(nextAchToast, 60); }
  scheduleBadgeSync();
}

// Things that can be read off the stored history at any time.
function standingAchs() {
  var out = [], s = Math.max(store.get("dbest", 0), dailyStreak());
  [[3, "streak3"], [7, "streak7"], [30, "streak30"], [100, "streak100"]].forEach(function (x) { if (s >= x[0]) out.push(x[1]); });
  var t = tallies();
  ["pl", "ll", "sa", "eu", "in"].forEach(function (l) { if ((t[l] || 0) >= LEAGUE_GOAL) out.push("lg_" + l); });
  if ((t.all || 0) >= 500) out.push("correct500");
  if (store.get("seen", []).length >= 500) out.push("seen500");
  var b = bests();
  if (b.survival >= 15) out.push("surv15");
  if (b.survival >= 30) out.push("surv30");
  if (b.blitz >= 10) out.push("blitz10");
  if (b.blitz >= 20) out.push("blitz20");
  if (b.hilo >= 10) out.push("hilo10");
  if (b.career >= 20) out.push("scout");
  var d = store.get("div", null);
  if (d && d.tier === 1) out.push("topflight");
  if (d && d.last && d.last.moved > 0) out.push("promoted");
  return out;
}

// ev: {k: "daily"|"classic"|"career"|"chal"|"run"|"duo", ...}
function achCheck(ev) {
  var ids = standingAchs();
  if (ev) {
    if (ev.k === "daily" && ev.correct === ev.total) ids.push("cleansheet");
    if (ev.k === "classic" && ev.correct === ev.total) {
      if (ev.total >= 10) ids.push("perfect10");
      if (ev.total >= 20) ids.push("perfect20");
      if (ev.total >= 50) ids.push("perfect50");
      if (ev.timer === 10 && ev.total >= 10) ids.push("icecool");
    }
    if (ev.k === "career" && ev.firstClub) ids.push("spotted");
    if (ev.k === "chal" && ev.res && ev.res.outcome === "win") {
      ids.push("chalwin");
      var tr = ev.res.tiers;
      if (tr && tr.them < tr.you) ids.push("giant");
    }
    if (ev.k === "duo" && ev.res) {
      if (ev.res.outcome === "win") ids.push("duowin");
      if (ev.res.carry) ids.push("carry");
      if (ev.res.flawless) ids.push("flawless");
    }
  }
  award(ids, false);
}

function nextAchToast() {
  // wait for a goal celebration to finish first
  if (!$("goal").classList.contains("hidden")) { achShowing = true; setTimeout(nextAchToast, 400); return; }
  var id = achQueue.shift(), el = $("achToast");
  if (!id) { achShowing = false; el.classList.remove("in"); return; }
  achShowing = true;
  var a = FQAch.BY[id];
  var ic = $("atIc"); ic.className = "badge big t" + a.t + (a.ic.charCodeAt(0) < 128 ? " txt" : ""); ic.textContent = a.ic;
  $("atName").textContent = a.name;
  $("atDesc").textContent = a.desc;
  el.classList.remove("hidden");
  el.classList.remove("in");
  void el.offsetWidth;            // restart the slide-in
  el.classList.add("in");
  setTimeout(function () {
    el.classList.remove("in");
    setTimeout(nextAchToast, 350);
  }, 3400);
}

function badgeEl(id, big) {
  var a = FQAch.BY[id];
  if (!a) return null;
  return h("span", {className: "badge t" + a.t + (big ? " big" : "") + (a.ic.charCodeAt(0) < 128 ? " txt" : ""),
                    title: a.name, "aria-label": a.name}, a.ic);
}

// Up to three badge roundels for a board row.
function badgeRow(ids) {
  if (!ids || !ids.length) return null;
  var s = h("span", {className: "bdgs"});
  ids.slice(0, 3).forEach(function (id) { var b = badgeEl(id); if (b) s.appendChild(b); });
  return s.children.length ? s : null;
}

// ---------------------------------------------------------------- sync to the server

function badgeKey(ids) { return ids.slice().sort().join(","); }
function scheduleBadgeSync() {
  clearTimeout(achSyncTimer);
  achSyncTimer = setTimeout(syncBadges, 1500);
}
async function syncBadges() {
  var ids = Object.keys(achHave());
  if (!ids.length || badgeKey(ids) === store.get("achSynced", "")) return;
  if (navigator.onLine === false) return;
  try {
    await api("/badges", {player_id: playerId(), badges: FQAch.rank(ids)}, 6000);
    store.set("achSynced", badgeKey(ids));
  } catch (e) { /* try again next launch / when back online */ }
}

// First launch with this version: credit what the history already shows, without a flood of toasts.
function achInit() {
  if (!store.get("achInit", false)) { award(standingAchs(), true); store.set("achInit", true); }
  syncBadges();
}

// ---------------------------------------------------------------- Me tab

function renderAchGrid() {
  var have = achHave(), box = $("achGrid"), n = 0;
  box.innerHTML = "";
  FQAch.LIST.forEach(function (a) {
    var got = !!have[a.id];
    if (got) n++;
    var tile = h("div", {className: "ach tile" + (got ? " got" : "")},
      badgeEl(a.id, true), h("b", null, a.name), h("span", null, a.desc));
    tile.setAttribute("aria-label", a.name + (got ? ", unlocked" : ", locked") + ". " + a.desc);
    box.appendChild(tile);
  });
  $("achCount").textContent = n + " of " + FQAch.LIST.length;
}

function renderFeel() {
  var hp = $("setHaptics"), sd = $("setSound");
  hp.setAttribute("aria-checked", String(!!feel.haptics));
  sd.setAttribute("aria-checked", String(!!feel.sound));
  $("hapticsRow").classList.toggle("hidden", !canVibrate());
}

function wireFeel() {
  $("setHaptics").onclick = function () { setFeel("haptics", !feel.haptics); renderFeel(); if (feel.haptics) buzz(true); };
  $("setSound").onclick = function () { setFeel("sound", !feel.sound); renderFeel(); sfx("whistle"); };
}

// ---------------------------------------------------------------- goal celebration

var CONFETTI = ["#f2c230", "#f4f7f1", "#e8590c", "#4cc77e", "#7fd1ff"];
function celebrate(text, sub) {
  var o = $("goal");
  $("goalText").textContent = text || "Goal!";
  $("goalSub").textContent = sub || "Perfect score";
  var box = $("confetti"); box.innerHTML = "";
  var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!still) {
    for (var i = 0; i < 44; i++) {
      var c = h("i", {style: "left:" + (Math.random() * 100).toFixed(1) + "%;background:" + CONFETTI[i % CONFETTI.length] +
        ";-webkit-animation-delay:" + (Math.random() * .5).toFixed(2) + "s;animation-delay:" + (Math.random() * .5).toFixed(2) + "s" +
        ";-webkit-animation-duration:" + (1.6 + Math.random() * 1.2).toFixed(2) + "s;animation-duration:" + (1.6 + Math.random() * 1.2).toFixed(2) + "s" +
        ";width:" + (6 + Math.round(Math.random() * 6)) + "px"});
      if (i % 3 === 0) c.className = "r";
      box.appendChild(c);
    }
  }
  o.classList.remove("hidden", "go");
  void o.offsetWidth;
  o.classList.add("go");
  buzz(true);
  sfx("cheer");
  clearTimeout(celebrate.t);
  celebrate.t = setTimeout(endCelebrate, still ? 1800 : 3000);
}
function endCelebrate() {
  clearTimeout(celebrate.t);
  var o = $("goal"); o.classList.remove("go"); o.classList.add("hidden");
  $("confetti").innerHTML = "";
}
