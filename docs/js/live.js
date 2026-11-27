// Live 1v1 matches, the ranked 1v1 league, 2v2 Online rooms and games vs bots (online + offline). Identical in every quiz
// (server side: server/live.py).
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
  lvSyncLevel();
  lvOfflineHint(navigator.onLine === false);
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
  } catch (e) {
    $(msgId).textContent = "Couldn't reach the server. Check your connection.";
    if (listId === "lvTable" && curScreen === "live") lvOfflineHint(true);
  }
}

// ---------------------------------------------------------------- finding a match

async function lvFind() {
  try { await ensureName("Your opponent will see this name."); } catch (e) { return; }
  $("lvFind").disabled = true;
  lvPanel("lvWait");
  $("lvWaitTitle").textContent = "Finding an opponent…";
  $("lvWaitSub").textContent = "Stay on this screen. We'll start as soon as someone else is looking.";
  $("lvCodeBox").classList.add("hidden"); $("lvShare").classList.add("hidden");
  lv = {ranked: true, searching: true, netFails: 0};
  lvQueuePing();
}
async function lvQueuePing() {
  if (!lv || !lv.searching) return;
  try {
    var r = await api("/live/queue", {name: myName(), player_id: playerId()});
    if (!lv || !lv.searching) return;
    if (r.status === "matched") return lvStart(r.code, true);
    lv.netFails = 0;
  } catch (e) {
    if (e.permanent) { lv = null; lvPanel("lvLobby"); $("lvFind").disabled = false; return msg("lvMsg", e.message, true); }
    if (lv && e.status === 0 && ++lv.netFails >= 3) {
      lv = null; lvPanel("lvLobby"); $("lvFind").disabled = false; lvOfflineHint(true);
      return msg("lvMsg", "Can't reach the server. Play a bot instead: it works offline.", true);
    }
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
  } catch (e) { lvNetErr(e); }
}
function lvNetErr(e) {
  if (e.status === 0 || e.status >= 500) { lvOfflineHint(true); return msg("lvMsg", "Live matches need a connection. You can still play a bot offline.", true); }
  msg("lvMsg", e.message, true);
}
async function lvJoin() {
  var code = tidyCode($("lvCode").value);
  if (!CODE_RE.test(code)) return msg("lvMsg", "Codes are 6 letters and numbers.", true);
  try { await ensureName("Your mate will see this name."); } catch (e) { return; }
  try {
    var r = await api("/live/join", {name: myName(), player_id: playerId(), code: code});
    lvStart(r.code, false);
  } catch (e) { lvNetErr(e); }
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
  if (me.local) {
    st = lvLocalState(me.local);
    lvRender(st);
    if (st.status !== "done") me.timer = setTimeout(lvPoll, 200);
    return;
  }
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
  $("lvKind").textContent = st.bot ? "vs bot · " + LV_LEVEL_NAME[st.bot] + (st.local ? " · offline" : "") : st.ranked ? "Ranked" : "Friendly";
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
  if (lv.local) { lvLocalAnswer(choice, idx); clearTimeout(lv.timer); return lvPoll(); }
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
  $("lvResLabel").textContent = (st.bot ? "vs bot · " + LV_LEVEL_NAME[st.bot] : st.ranked ? "Ranked" : "Friendly") + " · full time";
  $("lvResBig").textContent = r.outcome === "win" ? "You win" : r.outcome === "draw" ? "Draw" : "You lose";
  $("lvResYou").textContent = st.you.name; $("lvResOpp").textContent = st.opp.name;
  $("lvResYouPts").textContent = st.score.you; $("lvResOppPts").textContent = st.score.opp;
  $("lvResLine").textContent = st.correct.you + "/" + st.n + " right v " + st.correct.opp + "/" + st.n;
  var note = r.forfeit ? (r.outcome === "win" ? "Your opponent left the match. " : "You left the match. ") : "";
  if (st.bot) note += "Bot games don't count for the league" + (st.local ? " (played offline)." : ".");
  else if (st.ranked && !r.forfeit) note += "League: +" + (r.outcome === "win" ? 3 : r.outcome === "draw" ? 1 : 0) + " points. ";
  else if (!st.ranked) note += "Friendly matches don't count for the league. ";
  var hh = r.h2h;
  $("lvResNote").textContent = note + (hh ? "Head to head v " + st.opp.name + ": won " + hh.won + ", drawn " + hh.drawn + ", lost " + hh.lost + "." : "");
  $("lvAgain").textContent = st.bot ? "Play the bot again" : st.ranked ? "Find another match" : "Back to live";
  lvLast = {size: 1, code: st.code, ranked: st.ranked, bot: st.bot, local: !!st.local, outcome: r.outcome,
            us: st.score.you, them: st.score.opp, usName: st.you.name, themName: st.opp.name,
            line: st.correct.you + "/" + st.n + " right"};
  if (st.bot && r.outcome === "win" && !me.celebrated) { me.celebrated = true; celebrate("You win!", "Beat the " + LV_LEVEL_NAME[st.bot] + " bot"); }
  var ul = $("lvReview"); ul.innerHTML = "";
  r.review.forEach(function (q, i) {
    var pick = function (who, a) { return h("div", {className: "pk " + (a.pts ? "ok" : "no")}, who + ": " + (a.choice == null ? "no answer" : a.choice) + (a.pts ? " +" + a.pts : "")); };
    ul.appendChild(h("li", {className: "rev"},
      h("div", {className: "qh"}, h("span", {className: "qn"}, String(i + 1)), h("span", {className: "qt"}, lvThumb(q), q.q)),
      h("div", {className: "ans"}, "Answer: " + q.a),
      pick("You", q.you), pick(st.opp.name, q.opp)));
  });
  lv = null;
}

// flag thumbnail for picture questions in the reviews (quizzes without pictures have no qThumb)
function lvThumb(q) { return typeof qThumb === "function" ? qThumb(q) : null; }

function lvQuit() {
  if (!lv) return openLive();
  if (!confirm("Leave this match? You'll forfeit it.")) return;
  lvStop(); lv = null; goHome();
}

function handleLiveHash() {
  var m = /^#([lr])=([A-Za-z0-9\-]{6,7})$/.exec(location.hash);
  if (!m) return false;
  var code = tidyCode(m[2]);
  try { history.replaceState(null, "", location.pathname + location.search); } catch (e) { location.hash = ""; }
  if (!CODE_RE.test(code)) return false;
  if (m[1] === "r") openDuo(code); else openLive(code);
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
  $("lvAgain").onclick = function () {
    var x = lvLast;
    if (x && x.bot) return x.local ? lvLocalStart(x.bot) : lvBot(1);
    openLive(); if ($("lvAgain").textContent.indexOf("Find") === 0) lvFind();
  };
  $("lvShareRes").onclick = lvShareResult;
  $("lvBot1").onclick = function () { lvBot(1); };
  $("lvBot2").onclick = function () { lvBot(2); };
  window.addEventListener("offline", function () { if (curScreen === "live") lvOfflineHint(true); });
  window.addEventListener("online", function () { if (curScreen === "live" && !lv) lvOfflineHint(false); });
  $("lvHome").onclick = goHome;
  $("lvTabPlay").onclick = function () { openLive(); };
  window.addEventListener("hashchange", handleLiveHash);
  wireDuo();
}


// ================================================================ 2v2 Online
// Rooms of two teams of two. The client long-polls GET /live/room?v=N: the server answers as soon as
// the room changes (or after ~25 s), so every phone sees the same phase within a moment. If long
// polls keep failing it drops to plain polling every 1.5 s for a while.

var du = null;              // {code, v, st, q, picked, deadline, total, fails, short, stopped}
var DU_TEAMS = ["Yellows", "Blues"];

function duPanel(id) {
  ["duHub", "duRoom", "duGame", "duDone"].forEach(function (p) { $(p).classList.toggle("hidden", p !== id); });
  document.body.classList.toggle("playing", id === "duGame");
}
function duStop() {
  if (du) { du.stopped = true; clearTimeout(du.timer); }
  du = null;
}

function openDuo(code) {
  duStop();
  show("duo"); duPanel("duHub");
  $("duCode").value = code || ""; msg("duMsg", "");
  $("duQuick").disabled = false; $("duCreate").disabled = false;
  if (code) duJoin();
}

function duErr(e) {
  msg("duMsg", e.status === 0 ? "2v2 needs a connection. Tap Play vs bots to play a 1v1 offline." : e.message, true);
}
async function duEnter(path, body, why) {
  try { await ensureName(why); } catch (e) { return; }
  body.name = myName(); body.player_id = playerId();
  $("duQuick").disabled = true; $("duCreate").disabled = true;
  try {
    var r = await api(path, body);
    duBegin(r.code);
  } catch (e) { duErr(e); }
  $("duQuick").disabled = false; $("duCreate").disabled = false;
}
function duQuick() { duEnter("/live/room/quick", {}, "The other players will see this name."); }
function duCreate() { duEnter("/live/room/create", {}, "Your mates will see this name."); }
function duJoin() {
  var code = tidyCode($("duCode").value);
  if (!CODE_RE.test(code)) return msg("duMsg", "Room codes are 6 letters and numbers.", true);
  duEnter("/live/room/join", {code: code}, "Your mates will see this name.");
}

function duBegin(code) {
  duStop();
  du = {code: code, v: null, st: null, q: null, picked: null, deadline: 0, fails: 0, short: 0, stopped: false, celebrated: false};
  duPoll();
}

async function duPoll() {
  var me = du; if (!me || me.stopped) return;
  if (curScreen !== "duo") return duLeave(true);     // went to another tab: give the seat up
  var st = null, longPoll = me.v != null && !me.short;
  try {
    st = await api("/live/room?code=" + me.code + "&player_id=" + playerId() + (longPoll ? "&v=" + me.v : ""), null,
                   longPoll ? 32000 : 8000);
    me.fails = 0;
  } catch (e) {
    if (du !== me || me.stopped) return;
    if (e.permanent) { duStop(); openDuo(); return toast(e.status === 404 ? "That room has closed" : e.message); }
    me.fails++;
    if (me.fails >= 12) { duStop(); openDuo(); return toast("Lost connection to the room"); }
    me.short = 5;                                   // a few plain polls, then try long-polling again
  }
  if (du !== me || me.stopped) return;
  var same = st && st.v === me.v;
  if (st) { me.v = st.v; duRender(st); }
  if (me.short) me.short--;
  var wait = !st || me.short ? 1500 : same ? 400 : 0;
  me.timer = setTimeout(duPoll, wait);
}

function duMates(st) {
  // seat indexes: your team first (you first), then the other team
  var t = st.team, mine = [2 * t, 2 * t + 1], them = [2 * (1 - t), 2 * (1 - t) + 1];
  if (mine[1] === st.you) mine.reverse();
  return {mine: mine, them: them};
}
// "Bot ⚽ Nova" -> "Nova", "Sam Jones" -> "Sam"
function duShort(s) {
  var w = s.name.split(" ");
  return s.you ? "You" : s.bot ? w[w.length - 1] : w[0];
}
function duTeamName(st, seats) {
  var n = seats.map(function (i) { var s = st.seats[i]; return s ? duShort(s) : null; }).filter(Boolean);
  return n.join(" & ") || "–";
}

function duRender(st) {
  if (st.phase === "gone") { duStop(); openDuo(); return toast("You're no longer in that room"); }
  du.st = st;
  if (st.phase === "lobby") return duLobby(st);
  if (st.phase === "done") return duDone(st);
  duGame(st);
}

// ---------------------------------------------------------------- lobby

function duLobby(st) {
  if (du.wasPlaying) { du.wasPlaying = false; du.celebrated = false; }
  duPanel("duRoom");
  du.q = null;
  $("duRoomTitle").textContent = st.public ? "Quick match" : "Room " + st.code;
  var box = $("duTeams"); box.innerHTML = "";
  [0, 1].forEach(function (t) {
    var col = h("div", {className: "duteam t" + "AB"[t] + (st.team === t ? " mine" : "")},
      h("div", {className: "dth"}, h("b", null, DU_TEAMS[t]), st.team === t ? h("span", null, "Your team") : null));
    [2 * t, 2 * t + 1].forEach(function (i) {
      var s = st.seats[i];
      if (s) {
        col.appendChild(h("div", {className: "duseat" + (s.you ? " you" : "") + (s.bot ? " bot" : "")},
          h("span", {className: "av"}, s.bot ? "🤖" : s.name.charAt(0).toUpperCase()),
          h("span", {className: "nm"}, s.name, s.host ? h("small", null, "Host") : null)));
      } else {
        var b = h("button", {className: "duseat empty", type: "button"}, h("span", {className: "av"}, "+"),
          h("span", {className: "nm"}, st.team === t ? "Empty seat" : "Move here"));
        if (st.team !== t) b.onclick = function () { duSeat(t); };
        else b.disabled = true;
        col.appendChild(b);
      }
    });
    box.appendChild(col);
  });
  $("duShareBox").classList.toggle("hidden", st.public);
  $("duRoomCode").textContent = st.code;
  var humans = st.seats.filter(function (s) { return s && !s.bot; }).length;
  var hostName = "";
  st.seats.forEach(function (s) { if (s && s.host) hostName = s.name; });
  if (st.public) {
    du.deadline = Date.now() + (st.ms_left || 0);
    duLobbyTick();
  } else {
    $("duRoomNote").textContent = humans < 2 ? "Send the link to your mates. Tap a seat on the other team to switch." :
      "Tap an empty seat on the other team to switch sides.";
  }
  $("duStart").classList.toggle("hidden", !st.host);
  $("duStart").disabled = false;
  $("duStartNote").textContent = st.host ? (humans < 4 ? "Empty seats are filled by bots when you start." : "Everyone's here.")
    : "Waiting for " + (hostName || "the host") + " to start…";
}
function duLobbyTick() {
  if (!du || !du.st || du.st.phase !== "lobby" || !du.st.public) return;
  var left = Math.max(0, Math.ceil((du.deadline - Date.now()) / 1000));
  $("duRoomNote").textContent = left > 0 ? "Looking for players… bots fill the empty seats in " + left + " s" : "Starting…";
}

async function duSeat(t) {
  try { await api("/live/room/seat", {code: du.code, player_id: playerId(), team: t}); }
  catch (e) { toast(e.message); }
}
async function duStart() {
  $("duStart").disabled = true;
  try { await api("/live/room/start", {code: du.code, player_id: playerId()}); }
  catch (e) { $("duStart").disabled = false; toast(e.status === 0 ? "No connection" : e.message); }
}
function duShare() {
  shareLink("🤝 " + (myName() || "A mate") + " wants you in a 2v2 on " + document.title + ". Room " + du.code, APP_URL + "#r=" + du.code);
}
async function duLeave(silent) {
  var me = du;
  duStop();
  if (me) { try { await api("/live/room/leave", {code: me.code, player_id: playerId()}); } catch (e) { /* the room times out */ } }
  if (!silent) openDuo();
}

// ---------------------------------------------------------------- the game

function duGame(st) {
  var me = du;
  me.wasPlaying = true;
  duPanel("duGame");
  var sides = duMates(st);
  $("duUsName").textContent = duTeamName(st, sides.mine);
  $("duThemName").textContent = duTeamName(st, sides.them);
  $("duUsPts").textContent = st.scores[st.team]; $("duThemPts").textContent = st.scores[1 - st.team];
  $("duUsPts").parentNode.className = "t" + "AB"[st.team]; $("duThemPts").parentNode.className = "t" + "AB"[1 - st.team];
  $("duKind").textContent = st.bots ? "2v2 · vs bots · " + LV_LEVEL_NAME[st.bots] : st.public ? "2v2 · Quick match" : "2v2 · Room " + st.code;
  // the four players, with a tick once they've answered
  var pl = $("duPlayers"); pl.innerHTML = "";
  sides.mine.concat(sides.them).forEach(function (i, k) {
    var s = st.seats[i];
    if (!s) return;
    var done = st.phase === "question" && s.answered;
    pl.appendChild(h("span", {className: "dup t" + "AB"[s.team] + (k < 2 ? " us" : " them") + (done ? " in" : "") + (s.standin ? " sub" : "")},
      h("i", null, done ? "✓" : s.bot ? "🤖" : s.name.charAt(0).toUpperCase()),
      h("span", null, duShort(s)), s.standin ? h("em", null, "bot") : null));
  });
  var segs = $("duSegs");
  if (segs.children.length !== st.n) { segs.innerHTML = ""; for (var i = 0; i < st.n; i++) segs.appendChild(h("i")); }
  for (var k = 0; k < st.n; k++) segs.children[k].className = k < st.cur ? "ok" : k === st.cur && st.phase !== "countdown" ? "now" : "";
  me.deadline = Date.now() + (st.ms_left || 0);
  if (st.phase === "countdown") {
    me.phase = "countdown"; me.q = null;
    $("duQCard").classList.add("hidden"); $("duOpts").innerHTML = ""; $("duFact").classList.add("hidden");
    $("duCount").classList.remove("hidden"); $("duStatus").textContent = "";
    duTick();
    return;
  }
  $("duCount").classList.add("hidden");
  $("duQCard").classList.remove("hidden");
  var q = st.q, key = st.cur + ":" + q.id;
  if (me.q !== key) {
    me.q = key; me.picked = null; me.total = st.phase === "question" ? st.ms_left : 0;
    $("duQN").textContent = "Question " + (st.cur + 1) + " of " + st.n + " · " + ["", "Easy", "Medium", "Hard"][q.d];
    $("duQText").textContent = q.q;
    var im = $("duImg");
    if (q.img) { im.src = q.img; im.classList.remove("hidden"); } else im.classList.add("hidden");
    var box = $("duOpts"); box.innerHTML = "";
    q.opts.forEach(function (o) {
      var b = h("button", {className: "opt"}, h("span", {className: "t"}, o));
      b.setAttribute("data-v", o);
      b.onclick = function () { duAnswer(o, st.cur); };
      box.appendChild(b);
    });
    $("duFact").classList.add("hidden");
  }
  me.phase = st.phase;
  if (st.phase === "question") {
    if (st.mine != null && me.picked == null) duMark(st.mine);
    var waiting = st.seats.filter(function (s) { return s && !s.answered; }).length;
    $("duStatus").textContent = st.mine != null ? (waiting ? "Locked in. Waiting for " + waiting + " more…" : "Everyone's in…")
      : "Quicker right answers score more";
  } else {
    duReveal(st, sides);
  }
  duTick();
}

function duMark(choice) {
  du.picked = choice;
  var btns = $("duOpts").children;
  for (var i = 0; i < btns.length; i++) {
    var on = btns[i].getAttribute("data-v") === choice;
    btns[i].disabled = true; btns[i].classList.toggle("picked", on); btns[i].classList.toggle("dim", !on);
  }
}

function duReveal(st, sides) {
  var r = st.reveal, btns = $("duOpts").children, mine = r.picks[st.you];
  for (var j = 0; j < btns.length; j++) {
    var b = btns[j], v = b.getAttribute("data-v");
    b.disabled = true; b.classList.remove("picked");
    var old = b.querySelector(".who"); if (old) old.parentNode.removeChild(old);
    var who = h("span", {className: "who"});
    sides.mine.concat(sides.them).forEach(function (i, k) {
      var s = st.seats[i];
      if (s && r.picks[i].choice === v) who.appendChild(h("i", {className: "t" + "AB"[s.team], title: s.name}, duShort(s)));
    });
    b.classList.toggle("right", v === r.a);
    b.classList.toggle("wrong", v !== r.a && mine.choice === v);
    b.classList.toggle("dim", v !== r.a && mine.choice !== v);
    if (who.children.length) b.appendChild(who);
  }
  var team = 0;
  sides.mine.forEach(function (i) { team += r.picks[i].pts; });
  $("duStatus").textContent = (mine.choice == null ? "Out of time" : mine.pts ? "Correct! +" + mine.pts : "Not this time") +
    (team ? " · team +" + team : "");
  if (mine.pts && du.revealed !== st.cur) { du.revealed = st.cur; answerFeel(true, true); }
  $("duFactText").textContent = r.x; $("duFact").classList.toggle("hidden", !r.x);
}

function duTick() {
  if (!du || du.stopped || !du.st) return;
  var ms = Math.max(0, du.deadline - Date.now()), left = Math.ceil(ms / 1000);
  if (du.st.phase === "lobby") return duLobbyTick();
  if (du.phase === "countdown") { $("duCountN").textContent = Math.max(1, left); $("duClock").textContent = "–"; $("duBar").style.width = "100%"; return; }
  $("duClock").textContent = du.phase === "reveal" ? "–" : left;
  $("duClock").classList.toggle("low", du.phase === "question" && left <= 5);
  $("duBar").style.width = du.phase === "question" ? Math.min(100, ms / 150) + "%" : "0%";
}
setInterval(duTick, 200);

async function duAnswer(choice, idx) {
  if (!du || du.picked != null || du.phase !== "question") return;
  duMark(choice);
  $("duStatus").textContent = "Locked in…";
  try { await api("/live/room/answer", {code: du.code, player_id: playerId(), idx: idx, choice: choice}, 6000); }
  catch (e) { /* the next poll shows where things stand */ }
}

// ---------------------------------------------------------------- result

function duDone(st) {
  var me = du;
  duPanel("duDone");
  var r = st.result, sides = duMates(st);
  $("duRes").className = "card result dures " + r.outcome;
  $("duResLabel").textContent = (st.bots ? "2v2 vs bots · " + LV_LEVEL_NAME[st.bots] : st.public ? "Quick match" : "2v2") + " · full time";
  $("duResBig").textContent = r.outcome === "win" ? "You win" : r.outcome === "draw" ? "Draw" : "You lose";
  $("duResUs").textContent = duTeamName(st, sides.mine); $("duResThem").textContent = duTeamName(st, sides.them);
  $("duResUsPts").textContent = r.scores[st.team]; $("duResThemPts").textContent = r.scores[1 - st.team];
  $("duResUsPts").parentNode.className = "side t" + "AB"[st.team]; $("duResThemPts").parentNode.className = "side t" + "AB"[1 - st.team];
  var mvp = st.seats[r.mvp];
  $("duResLine").textContent = (r.tiebreak ? "Level on points: the quicker team wins. " : "") +
    (r.carry ? "You carried the team: top scorer!" : "Top scorer: " + (mvp ? mvp.name : "?") + " (" + r.pts[r.mvp] + ")");
  var ul = $("duScores"); ul.innerHTML = "";
  [0, 1, 2, 3].sort(function (a, b) { return r.pts[b] - r.pts[a]; }).forEach(function (i, k) {
    var s = st.seats[i]; if (!s) return;
    ul.appendChild(h("li", {className: "tile duline t" + "AB"[s.team] + (s.you ? " me" : "")},
      h("span", {className: "pos"}, i === r.mvp ? "⭐" : String(k + 1)),
      h("span", {className: "nm"}, h("span", {className: "n"}, s.you ? s.name + " (you)" : s.name),
        h("span", {className: "meta"}, DU_TEAMS[s.team] + " · " + r.correct[i] + "/" + st.n + " right" + (s.bot ? " · bot" : s.standin ? " · bot stood in" : ""))),
      h("span", {className: "pts"}, r.pts[i])));
  });
  var rv = $("duReview"); rv.innerHTML = "";
  r.review.forEach(function (q, n) {
    var li = h("li", {className: "rev"},
      h("div", {className: "qh"}, h("span", {className: "qn"}, String(n + 1)), h("span", {className: "qt"}, lvThumb(q), q.q)),
      h("div", {className: "ans"}, "Answer: " + q.a));
    sides.mine.concat(sides.them).forEach(function (i) {
      var s = st.seats[i], a = q.picks[i]; if (!s) return;
      li.appendChild(h("div", {className: "pk " + (a.pts ? "ok" : "no")}, (s.you ? "You" : s.name) + ": " +
        (a.choice == null ? "no answer" : a.choice) + (a.pts ? " +" + a.pts : "")));
    });
    rv.appendChild(li);
  });
  $("duAgain").disabled = false;
  $("duAgain").textContent = st.bots ? "Play again" : "Rematch";
  lvLast = {size: 2, code: st.code, isPublic: st.public, bot: st.bots, outcome: r.outcome,
            us: r.scores[st.team], them: r.scores[1 - st.team], usName: duTeamName(st, sides.mine),
            themName: duTeamName(st, sides.them), line: r.correct[st.you] + "/" + st.n + " right"};
  if (!me.celebrated) {
    me.celebrated = true;
    if (r.outcome === "win") celebrate("You win!", st.bots ? "Beat the bots" : r.carry ? "Top scorer in a 2v2 win" : "2v2 victory");
    if (!st.bots) achCheck({k: "duo", res: r});      // bot games don't earn badges
  }
}
async function duRematch() {
  $("duAgain").disabled = true;
  try { await api("/live/room/rematch", {code: du.code, player_id: playerId()}); }
  catch (e) { $("duAgain").disabled = false; toast(e.status === 404 ? "That room has closed" : e.message); }
}
function duQuit() {
  if (!confirm("Leave this game? A bot will play the rest for you.")) return;
  duLeave(true); goHome();
}

function wireDuo() {
  $("duBack").onclick = function () { duStop(); goHome(); };
  $("duQuick").onclick = duQuick;
  $("duCreate").onclick = duCreate;
  $("duJoin").onclick = duJoin;
  $("duCode").addEventListener("keydown", function (e) { if (e.key === "Enter") duJoin(); });
  $("duRoomBack").onclick = function () { duLeave(); };
  $("duShare").onclick = duShare;
  $("duStart").onclick = duStart;
  $("duQuit").onclick = duQuit;
  $("duAgain").onclick = duRematch;
  $("duLeave").onclick = function () { duLeave(true); goHome(); };
  $("duShareRes").onclick = lvShareResult;
  $("duBots").onclick = function () { lvBot(2); };
}


// ================================================================ Play vs bots
// Online: POST /live/bot (1v1) or /live/room/bots (2v2 with 3 bots); the server runs the bots and
// nothing counts for the league or divisions. Offline (or when the server can't be reached) a 1v1
// runs right here on the phone: same 10 questions x 15 s, same scoring (10 + up to 5 for speed),
// with a bot that answers like the server's (LV_BOT = live.py BOT_LEVELS). It produces the same
// state objects as GET /live/state, so lvRender/lvDone draw it unchanged.
// Per-quiz bot names: LIVE_BOT_TAG / LIVE_BOT_NAMES globals (core.js), like leaderboard.py.

var LV_N = 10, LV_Q_MS = 15000, LV_REVEAL_MS = 3000, LV_START_MS = 3000;
var LV_BOT = {
  easy: {acc: {1: 0.65, 2: 0.5, 3: 0.35}, secs: [5.0, 13.0]},
  medium: {acc: {1: 0.85, 2: 0.7, 3: 0.55}, secs: [3.0, 11.0]},
  hard: {acc: {1: 0.97, 2: 0.9, 3: 0.8}, secs: [1.5, 6.5]}
};
var LV_LEVEL_NAME = {easy: "Easy", medium: "Medium", hard: "Hard"};
var lvLast = null;          // the match on the results screen: {kind, code, bot, size, local}

function lvLevel() { var v = store.get("botLevel", "medium"); return LV_BOT[v] ? v : "medium"; }
function lvSyncLevel() {
  var box = $("lvLevel"), lv0 = lvLevel();
  if (!box.children.length) {
    ["easy", "medium", "hard"].forEach(function (k) {
      var b = h("button", {type: "button"}, LV_LEVEL_NAME[k]);
      b.setAttribute("data-l", k);
      b.onclick = function () { store.set("botLevel", k); lvSyncLevel(); };
      box.appendChild(b);
    });
  }
  for (var i = 0; i < box.children.length; i++) {
    box.children[i].setAttribute("aria-pressed", box.children[i].getAttribute("data-l") === lv0 ? "true" : "false");
  }
}
// "can't reach the server": put the bot card first and say it works offline
function lvOfflineHint(on) {
  var card = $("lvBotCard"), lobby = $("lvLobby");
  $("lvBotOff").classList.toggle("hidden", !on);
  card.classList.toggle("hot", !!on);
  if (on && lobby.children[1] !== card) lobby.insertBefore(card, lobby.children[1]);
}
function lvBotName() {
  var tag = typeof LIVE_BOT_TAG === "string" ? LIVE_BOT_TAG : "Bot";
  var names = typeof LIVE_BOT_NAMES !== "undefined" && LIVE_BOT_NAMES.length ? LIVE_BOT_NAMES : ["Ace", "Nova", "Bolt", "Echo"];
  return tag + " " + names[Math.floor(Math.random() * names.length)];
}

async function lvBot(size) {
  var level = lvLevel();
  msg("lvMsg", ""); msg("duMsg", "");
  // known to be offline (no network, or the server just failed): straight to the on-phone game
  var offline = navigator.onLine === false || !$("lvBotOff").classList.contains("hidden");
  if (!offline) {
    try { await ensureName("Shown on the scoreboard."); } catch (e) { return; }
    if (myName()) {
      var btns = ["lvBot1", "lvBot2", "duBots"];
      btns.forEach(function (id) { $(id).disabled = true; });
      try {
        var body = {name: myName(), player_id: playerId(), level: level};
        if (size === 2) {
          var r2 = await api("/live/room/bots", body);
          btns.forEach(function (id) { $(id).disabled = false; });
          duStop(); show("duo"); duBegin(r2.code);
          return;
        }
        var r1 = await api("/live/bot", body);
        btns.forEach(function (id) { $(id).disabled = false; });
        if (curScreen !== "live") { show("live"); }
        lvStart(r1.code, false);
        lv.bot = level;
        return;
      } catch (e) {
        btns.forEach(function (id) { $(id).disabled = false; });
        if (e.permanent) return msg(curScreen === "duo" ? "duMsg" : "lvMsg", e.message, true);
      }
      toast(size === 2 ? "Can't reach the server: playing a 1v1 bot offline" : "Can't reach the server: playing offline");
    }
  }
  lvLocalStart(level);
}

// ---------------------------------------------------------------- the offline match

function lvLocalPick() {
  var pool = BANK.filter(function (q) { return q && q.id && q.a && q.w && q.w.length >= 3; });
  if (pool.length < LV_N) return null;
  var out = [], used = {};
  [[1, 3], [2, 4], [3, 3]].forEach(function (dn) {
    shuffle(pool.filter(function (q) { return q.d === dn[0]; })).slice(0, dn[1]).forEach(function (q) { out.push(q); used[q.id] = 1; });
  });
  out = out.concat(shuffle(pool.filter(function (q) { return !used[q.id]; })).slice(0, LV_N - out.length));
  return shuffle(out).slice(0, LV_N);
}

function lvLocalStart(level) {
  var qs = lvLocalPick();
  if (!qs) {
    if (curScreen !== "live") openLive();
    return msg("lvMsg", "The questions haven't downloaded yet. Open the app once with a connection.", true);
  }
  lvStop();
  if (curScreen !== "live") { show("live"); }
  var now = Date.now();
  lv = {code: "", ranked: false, bot: level, q: null, answered: false, deadline: 0, fails: 0, seq: 0, phase: "",
        local: {qs: qs, level: level, bot: lvBotName(), start: now + LV_START_MS, opened: now + LV_START_MS, closed: null,
                cur: 0, ans: [{}, {}], plan: {}, opts: {}, done: false}};
  lvPoll();
}

function lvLocalBot(m, now) {
  var cur = m.cur;
  if (cur >= LV_N || m.ans[1][cur] || now < m.opened) return;
  var q = m.qs[cur], cfg = LV_BOT[m.level] || LV_BOT.medium;
  if (!m.plan[cur]) {
    var ok = Math.random() < (cfg.acc[q.d] || 0.5);
    m.plan[cur] = {t: m.opened + 1000 * (cfg.secs[0] + Math.random() * (cfg.secs[1] - cfg.secs[0])),
                   choice: ok ? q.a : q.w[Math.floor(Math.random() * q.w.length)]};
  }
  var p = m.plan[cur];
  if (p.t <= now && p.t <= m.opened + LV_Q_MS) lvLocalRecord(m, 1, p.choice, p.t);
}
function lvLocalRecord(m, s, choice, t) {
  var q = m.qs[m.cur], left = Math.max(0, m.opened + LV_Q_MS - t);
  var pts = choice === q.a ? 10 + Math.round(5 * left / LV_Q_MS) : 0;
  m.ans[s][m.cur] = {choice: choice, pts: pts, t: t};
}
// the server's _tick, on the phone's clock
function lvLocalTick(m, now) {
  while (!m.done) {
    if (now < m.start) return;
    if (m.cur >= LV_N) { m.done = true; return; }
    if (m.closed == null) {
      lvLocalBot(m, now);
      var a = m.ans[0][m.cur], b = m.ans[1][m.cur];
      if (a && b) m.closed = Math.max(a.t, b.t);
      else if (now >= m.opened + LV_Q_MS) m.closed = m.opened + LV_Q_MS;
      else return;
    }
    if (now < m.closed + LV_REVEAL_MS) return;
    m.opened = m.closed + LV_REVEAL_MS; m.closed = null; m.cur++;
  }
}
function lvLocalSum(m, s, key) {
  var n = 0;
  for (var k in m.ans[s]) n += key === "pts" ? m.ans[s][k].pts : (m.ans[s][k].pts > 0 ? 1 : 0);
  return n;
}
function lvLocalQ(m, k) {
  var q = m.qs[k];
  if (!m.opts[k]) m.opts[k] = shuffle([q.a].concat(q.w.slice(0, 3)));
  return {id: q.id, q: q.q, opts: m.opts[k], d: q.d, img: q.img};
}
function lvLocalPick2(a) { return {choice: a ? a.choice : null, pts: a ? a.pts : 0}; }
function lvLocalState(m) {
  var now = Date.now();
  lvLocalTick(m, now);
  var st = {code: "", ranked: false, bot: m.level, local: true, status: m.done ? "done" : "live", n: LV_N,
            you: {name: myName() || "You"}, opp: {name: m.bot},
            score: {you: lvLocalSum(m, 0, "pts"), opp: lvLocalSum(m, 1, "pts")},
            correct: {you: lvLocalSum(m, 0), opp: lvLocalSum(m, 1)}};
  if (m.done) {
    var o = st.score.you > st.score.opp ? "win" : st.score.you < st.score.opp ? "loss" : "draw";
    st.phase = "done";
    st.result = {outcome: o, forfeit: false, h2h: null, review: m.qs.map(function (q, k) {
      return {q: q.q, a: q.a, x: q.x || "", img: q.img, you: lvLocalPick2(m.ans[0][k]), opp: lvLocalPick2(m.ans[1][k])};
    })};
    return st;
  }
  if (now < m.start) { st.phase = "countdown"; st.ms_left = m.start - now; st.cur = 0; return st; }
  var cur = m.cur, q = m.qs[cur];
  st.cur = cur; st.q = lvLocalQ(m, cur);
  st.you_answered = !!m.ans[0][cur]; st.opp_answered = !!m.ans[1][cur];
  if (m.closed == null) { st.phase = "question"; st.ms_left = m.opened + LV_Q_MS - now; return st; }
  st.phase = "reveal"; st.ms_left = m.closed + LV_REVEAL_MS - now;
  st.reveal = {a: q.a, x: q.x || "", you: lvLocalPick2(m.ans[0][cur]), opp: lvLocalPick2(m.ans[1][cur])};
  return st;
}
function lvLocalAnswer(choice, idx) {
  var m = lv.local, now = Date.now();
  lvLocalTick(m, now);
  if (!m.done && idx === m.cur && m.closed == null && now >= m.opened && !m.ans[0][idx]) lvLocalRecord(m, 0, choice, now);
  lvLocalTick(m, now);
}

// ---------------------------------------------------------------- sharing a result

function lvModeText(x) {
  if (x.size === 2) return x.bot ? "2v2 vs bots (" + LV_LEVEL_NAME[x.bot] + ")" : x.isPublic ? "2v2 Quick match" : "2v2 room";
  return x.bot ? "1v1 vs bot (" + LV_LEVEL_NAME[x.bot] + ")" : x.ranked ? "Ranked live 1v1" : "Friendly live 1v1";
}
function lvShareResult() {
  var x = lvLast; if (!x) return;
  var verdict = x.outcome === "win" ? "Won" : x.outcome === "draw" ? "Drew" : "Lost";
  var text = (x.size === 2 ? "🤝 " : "⚔️ ") + verdict + " " + x.us + " – " + x.them + " (" + x.usName + " v " + x.themName + ")" +
    (x.line ? " · " + x.line : "") + " · " + lvModeText(x) + " on " + document.title + ".";
  var url = APP_URL;
  if (!x.bot && x.code && x.size === 2 && !x.isPublic) { url = APP_URL + "#r=" + x.code; text += " Room " + x.code + ", join us:"; }
  else if (!x.bot && x.code && x.size !== 2 && !x.ranked) { url = APP_URL + "#l=" + x.code; text += " Code " + x.code + ":"; }
  else text += " Fancy a game?";
  shareLink(text, url);
}
