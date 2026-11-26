// Live 1v1 matches + the ranked 1v1 league. Identical in every quiz (server side: server/live.py).
// Plain ES2017 (iOS 12): no ?. ?? or catch-without-binding.
"use strict";

var lv = null;              // {code, timer, seq, q, answered, deadline, ranked, polls, fails}
var lvQueueTimer = null;
var lvPeriod = "week";

function lvStop() {
  if (lv) { clearTimeout(lv.timer); lv.stopped = true; }
  clearTimeout(lvQueueTimer); lvQueueTimer = null;
}
function lvPanel(id) {
  ["lvLobby", "lvWait", "lvMatch", "lvDone"].forEach(function (p) { $(p).classList.toggle("hidden", p !== id); });
  document.body.classList.toggle("playing", id === "lvMatch");
}

// ---------------------------------------------------------------- lobby

function openLive(code) {
  lvStop(); lv = null;
  show("live"); lvPanel("lvLobby");
  $("lvCode").value = code || ""; msg("lvMsg", "");
  $("lvFind").disabled = false;
  loadLiveLeague("lvTable", "lvTableMsg", lvPeriod);
  var chips = $("lvPeriod");
  if (!chips.children.length) {
    [["week", "This week"], ["all", "All time"]].forEach(function (p) {
      var b = h("button", {className: "chip", type: "button"}, p[1]);
      b.setAttribute("data-p", p[0]);
      b.onclick = function () { lvPeriod = p[0]; syncLvChips(); loadLiveLeague("lvTable", "lvTableMsg", lvPeriod); };
      chips.appendChild(b);
    });
  }
  syncLvChips();
  if (code) lvJoin();
}
function syncLvChips() {
  var cs = $("lvPeriod").children;
  for (var i = 0; i < cs.length; i++) cs[i].setAttribute("aria-pressed", cs[i].getAttribute("data-p") === lvPeriod ? "true" : "false");
}

async function loadLiveLeague(listId, msgId, period, compact) {
  var ul = $(listId); ul.innerHTML = ""; $(msgId).textContent = "Loading…";
  try {
    var r = await api("/live/league?period=" + period + "&player_id=" + playerId());
    $(msgId).textContent = r.rows.length ? "" : "No ranked matches yet. Be the first on the table.";
    r.rows.slice(0, compact ? 5 : 25).forEach(function (t) {
      ul.appendChild(row(t.rank, t.name, h("span", {className: "meta"},
        t.P + " played · " + t.W + "W " + t.D + "D " + t.L + "L · " + (t.gd > 0 ? "+" : "") + t.gd), t.pts, t.mine));
    });
    if (r.you && r.you.rank > (compact ? 5 : 25)) {
      var t = r.you;
      ul.appendChild(row(t.rank, t.name, h("span", {className: "meta"}, t.P + " played · " + t.W + "W " + t.D + "D " + t.L + "L"), t.pts, true));
    }
    if (r.you == null && r.rows.length) $(msgId).textContent = "Play a ranked match to get on the table.";
  } catch (e) { $(msgId).textContent = "Couldn't reach the server. Check your connection."; }
}

// ---------------------------------------------------------------- finding a match

async function lvFind() {
  try { await ensureName("Your opponent will see this name."); } catch (e) { return; }
  $("lvFind").disabled = true;
  lvPanel("lvWait");
  $("lvWaitTitle").textContent = "Finding an opponent…";
  $("lvWaitSub").textContent = "Stay on this screen. We'll start as soon as someone else is looking.";
  $("lvCodeBox").classList.add("hidden"); $("lvShare").classList.add("hidden");
  lv = {ranked: true, searching: true};
  lvQueuePing();
}
async function lvQueuePing() {
  if (!lv || !lv.searching) return;
  try {
    var r = await api("/live/queue", {name: myName(), player_id: playerId()});
    if (!lv || !lv.searching) return;
    if (r.status === "matched") return lvStart(r.code, true);
  } catch (e) {
    if (e.permanent) { lv = null; lvPanel("lvLobby"); $("lvFind").disabled = false; return msg("lvMsg", e.message, true); }
  }
  lvQueueTimer = setTimeout(lvQueuePing, 2000);
}
async function lvCancel() {
  var wasFriendly = lv && lv.code && !lv.ranked;
  lvStop(); lv = null;
  try { await api("/live/cancel", {player_id: playerId()}); } catch (e) { /* it expires on its own */ }
  openLive();
  if (wasFriendly) toast("Match cancelled");
}

async function lvCreate() {
  try { await ensureName("Your mate will see this name."); } catch (e) { return; }
  try {
    var r = await api("/live/create", {name: myName(), player_id: playerId()});
    lvPanel("lvWait");
    $("lvWaitTitle").textContent = "Waiting for your mate…";
    $("lvWaitSub").textContent = "Send them the code. The match starts the moment they join. Friendly matches don't count for the league.";
    $("lvCodeBox").classList.remove("hidden"); $("lvShare").classList.remove("hidden");
    $("lvWaitCode").textContent = r.code;
    lvStart(r.code, false);
  } catch (e) { msg("lvMsg", e.status === 0 ? "Live matches need a connection." : e.message, true); }
}
async function lvJoin() {
  var code = tidyCode($("lvCode").value);
  if (!CODE_RE.test(code)) return msg("lvMsg", "Codes are 6 letters and numbers.", true);
  try { await ensureName("Your mate will see this name."); } catch (e) { return; }
  try {
    var r = await api("/live/join", {name: myName(), player_id: playerId(), code: code});
    lvStart(r.code, false);
  } catch (e) { msg("lvMsg", e.status === 0 ? "Live matches need a connection." : e.message, true); }
}
function lvShare() {
  var code = $("lvWaitCode").textContent;
  shareLink("⚔️ " + (myName() || "A mate") + " wants a live 1v1 on " + document.title + ". Code " + code, APP_URL + "#l=" + code);
}

// ---------------------------------------------------------------- the match

function lvStart(code, ranked) {
  clearTimeout(lvQueueTimer);
  lv = {code: code, ranked: ranked, q: null, answered: false, deadline: 0, fails: 0, seq: 0, phase: ""};
  lvPoll();
}
async function lvPoll() {
  var me = lv; if (!me || me.stopped) return;
  var st = null;
  try {
    st = await api("/live/state?code=" + me.code + "&player_id=" + playerId(), null, 6000);
    me.fails = 0;
  } catch (e) {
    if (e.permanent) { lvStop(); lv = null; openLive(); return toast(e.message); }
    me.fails++;
    if (me.fails >= 8) { lvStop(); lv = null; openLive(); return toast("Lost connection to the match"); }
  }
  if (lv !== me || me.stopped) return;
  if (st) lvRender(st);
  if (!st || st.status !== "done") me.timer = setTimeout(lvPoll, st && st.phase === "question" && st.ms_left < 3000 ? 450 : 750);
}

function lvRender(st) {
  var me = lv;
  if (st.status === "waiting") {
    me.ranked = st.ranked;
    lvPanel("lvWait");
    $("lvWaitTitle").textContent = "Waiting for your mate…";
    $("lvCodeBox").classList.remove("hidden"); $("lvShare").classList.remove("hidden");
    $("lvWaitCode").textContent = me.code;
    return;
  }
  if (st.status === "done") return lvDone(st);
  lvPanel("lvMatch");
  me.ranked = st.ranked;
  $("lvYouName").textContent = st.you.name; $("lvOppName").textContent = st.opp.name;
  $("lvYouPts").textContent = st.score.you; $("lvOppPts").textContent = st.score.opp;
  $("lvKind").textContent = st.ranked ? "Ranked" : "Friendly";
  var segs = $("lvSegs");
  if (segs.children.length !== st.n) { segs.innerHTML = ""; for (var i = 0; i < st.n; i++) segs.appendChild(h("i")); }
  for (var k = 0; k < st.n; k++) segs.children[k].className = k < (st.cur || 0) ? "ok" : k === st.cur ? "now" : "";
  if (st.phase === "countdown") {
    me.deadline = Date.now() + st.ms_left; me.phase = "countdown";
    $("lvQCard").classList.add("hidden"); $("lvOpts").innerHTML = ""; $("lvFact").classList.add("hidden");
    $("lvStatus").textContent = "Get ready…"; lvTick();
    return;
  }
  $("lvQCard").classList.remove("hidden");
  var q = st.q;
  if (me.q !== q.id) {
    me.q = q.id; me.answered = false;
    $("lvQN").textContent = "Question " + (st.cur + 1) + " of " + st.n;
    $("lvQText").textContent = q.q;
    var im = $("lvImg");
    if (q.img) { im.src = q.img; im.classList.remove("hidden"); } else im.classList.add("hidden");
    var box = $("lvOpts"); box.innerHTML = "";
    q.opts.forEach(function (o) {
      var b = h("button", {className: "opt"}, h("span", {className: "t"}, o));
      b.setAttribute("data-v", o);
      b.onclick = function () { lvAnswer(o, st.cur); };
      box.appendChild(b);
    });
    $("lvFact").classList.add("hidden");
  }
  me.phase = st.phase;
  me.deadline = Date.now() + st.ms_left;
  if (st.phase === "question") {
    if (st.you_answered && !me.answered) me.answered = true;
    $("lvStatus").textContent = st.you_answered
      ? (st.opp_answered ? "Both answered…" : "Locked in. Waiting for " + st.opp.name + "…")
      : (st.opp_answered ? st.opp.name + " has answered!" : "Your move");
  } else {
    var r = st.reveal, btns = $("lvOpts").children;
    for (var j = 0; j < btns.length; j++) {
      var b = btns[j], v = b.getAttribute("data-v");
      b.disabled = true; b.classList.remove("picked");
      var old = b.querySelector(".st"); if (old) old.parentNode.removeChild(old);
      var tags = [];
      if (r.you.choice === v) tags.push("You" + (r.you.pts ? " +" + r.you.pts : ""));
      if (r.opp.choice === v) tags.push(st.opp.name + (r.opp.pts ? " +" + r.opp.pts : ""));
      b.classList.toggle("right", v === r.a);
      b.classList.toggle("wrong", v !== r.a && r.you.choice === v);
      b.classList.toggle("dim", v !== r.a && r.you.choice !== v);
      if (tags.length) b.appendChild(h("span", {className: "st"}, tags.join(" · ")));
    }
    $("lvStatus").textContent = r.you.choice == null ? "Out of time" : r.you.pts ? "Correct! +" + r.you.pts : "Not this time";
    $("lvFactText").textContent = r.x; $("lvFact").classList.toggle("hidden", !r.x);
  }
  lvTick();
}

function lvTick() {
  if (!lv || lv.stopped) return;
  var left = Math.max(0, Math.ceil((lv.deadline - Date.now()) / 1000));
  $("lvClock").textContent = lv.phase === "reveal" ? "–" : left;
  $("lvClock").classList.toggle("low", lv.phase === "question" && left <= 5);
}
setInterval(lvTick, 250);

async function lvAnswer(choice, idx) {
  if (!lv || lv.answered || lv.phase !== "question") return;
  lv.answered = true;
  var btns = $("lvOpts").children;
  for (var i = 0; i < btns.length; i++) {
    var on = btns[i].getAttribute("data-v") === choice;
    btns[i].disabled = true; btns[i].classList.toggle("picked", on); btns[i].classList.toggle("dim", !on);
  }
  $("lvStatus").textContent = "Locked in…";
  try { await api("/live/answer", {code: lv.code, player_id: playerId(), idx: idx, choice: choice}, 6000); }
  catch (e) { /* the next poll shows where things stand */ }
  if (lv) { clearTimeout(lv.timer); lvPoll(); }
}

// ---------------------------------------------------------------- result

function lvDone(st) {
  var me = lv; lvStop();
  lvPanel("lvDone");
  var r = st.result;
  $("lvRes").className = "card result lvres " + r.outcome;
  $("lvResLabel").textContent = (st.ranked ? "Ranked" : "Friendly") + " · full time";
  $("lvResBig").textContent = r.outcome === "win" ? "You win" : r.outcome === "draw" ? "Draw" : "You lose";
  $("lvResYou").textContent = st.you.name; $("lvResOpp").textContent = st.opp.name;
  $("lvResYouPts").textContent = st.score.you; $("lvResOppPts").textContent = st.score.opp;
  $("lvResLine").textContent = st.correct.you + "/" + st.n + " right v " + st.correct.opp + "/" + st.n;
  var note = r.forfeit ? (r.outcome === "win" ? "Your opponent left the match. " : "You left the match. ") : "";
  if (st.ranked && !r.forfeit) note += "League: +" + (r.outcome === "win" ? 3 : r.outcome === "draw" ? 1 : 0) + " points. ";
  else if (!st.ranked) note += "Friendly matches don't count for the league. ";
  var hh = r.h2h;
  $("lvResNote").textContent = note + "Head to head v " + st.opp.name + ": won " + hh.won + ", drawn " + hh.drawn + ", lost " + hh.lost + ".";
  $("lvAgain").textContent = st.ranked ? "Find another match" : "Back to live";
  var ul = $("lvReview"); ul.innerHTML = "";
  r.review.forEach(function (q, i) {
    var pick = function (who, a) { return h("div", {className: "pk " + (a.pts ? "ok" : "no")}, who + ": " + (a.choice == null ? "no answer" : a.choice) + (a.pts ? " +" + a.pts : "")); };
    ul.appendChild(h("li", {className: "rev"},
      h("div", {className: "qh"}, h("span", {className: "qn"}, String(i + 1)), h("span", {className: "qt"}, q.q)),
      h("div", {className: "ans"}, "Answer: " + q.a),
      pick("You", q.you), pick(st.opp.name, q.opp)));
  });
  lv = null;
}

function lvQuit() {
  if (!lv) return openLive();
  if (!confirm("Leave this match? You'll forfeit it.")) return;
  lvStop(); lv = null; goHome();
}

function handleLiveHash() {
  var m = /^#l=([A-Za-z0-9\-]{6,7})$/.exec(location.hash);
  if (!m) return false;
  var code = tidyCode(m[1]);
  try { history.replaceState(null, "", location.pathname + location.search); } catch (e) { location.hash = ""; }
  if (!CODE_RE.test(code)) return false;
  openLive(code);
  return true;
}

// The Leagues tab shows the top of the live league too.
var openLeaguesTabBase = openLeaguesTab;
openLeaguesTab = function (joinCode) {
  var p = openLeaguesTabBase(joinCode);
  loadLiveLeague("lvTabTable", "lvTabMsg", "week", true);
  return p;
};

function wireLive() {
  $("lvFind").onclick = lvFind;
  $("lvCancel").onclick = lvCancel;
  $("lvCreate").onclick = lvCreate;
  $("lvJoin").onclick = lvJoin;
  $("lvCode").addEventListener("keydown", function (e) { if (e.key === "Enter") lvJoin(); });
  $("lvShare").onclick = lvShare;
  $("lvBack").onclick = function () { lvStop(); lv = null; goHome(); };
  $("lvQuit").onclick = lvQuit;
  $("lvAgain").onclick = function () { openLive(); if ($("lvAgain").textContent.indexOf("Find") === 0) lvFind(); };
  $("lvHome").onclick = goHome;
  $("lvTabPlay").onclick = function () { openLive(); };
  window.addEventListener("hashchange", handleLiveHash);
}
