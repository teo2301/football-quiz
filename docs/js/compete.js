// Challenges (send a mate your exact quiz), private leagues and weekly divisions.
// Plain ES2017 (iOS 12): no ?. ?? or catch-without-binding.
"use strict";

// Links point at the live site, except in the preview, which links to itself.
var APP_URL = location.port === "8197" ? location.origin + "/" : SITE;
var CODE_RE = /^[A-HJ-NP-Z2-9]{6}$/;
var lastClassic = null;     // the classic game just finished, for "Challenge a mate"
var chal = null;            // the challenge being played / shown
var openLeague = null;      // code of the league on screen
var leaguePeriod = "week";
var divCache = null;        // {data, at}

function tidyCode(v) { return String(v || "").replace(/[\s\-]/g, "").toUpperCase(); }
function plural(n, one, many) { return n + " " + (n === 1 ? one : (many || one + "s")); }
function quizMeta(c) {
  return plural(c.total, "question") + " · " + leagueName(c.league) + " · " + DIFFS[c.diff] +
    (c.timer ? " · " + c.timer + " s timer" : " · no timer");
}
function leagueName(l) { return l === "all" ? "All competitions" : LEAGUES[l]; }
function fmtPts(p) { return String(Math.round(p * 10) / 10); }
function msg(id, text, isErr) {
  var m = $(id); m.textContent = text || ""; m.classList.toggle("err", !!isErr); m.classList.toggle("hidden", !text);
}

// ---------------------------------------------------------------- name sheet

// Resolves with the name once there is one (asks first if needed); rejects on Cancel.
var nameWait = null;
function ensureName(why) {
  if (myName()) return Promise.resolve(myName());
  return new Promise(function (res, rej) {
    nameWait = {res: res, rej: rej};
    $("nsWhy").textContent = why || "Your mates will see it in tables and challenges.";
    $("nsName").value = ""; msg("nsMsg", "");
    $("nameSheet").classList.remove("hidden");
    setTimeout(function () { try { $("nsName").focus(); } catch (e) { /* ok */ } }, 50);
  });
}
async function nameSheetSave() {
  var name = tidyName($("nsName").value);
  if (!validName(name)) return msg("nsMsg", "Use 2-16 letters, numbers or spaces.", true);
  $("nsSave").disabled = true;
  var r = await claimName(name);
  $("nsSave").disabled = false;
  if (!r.ok) return msg("nsMsg", r.text, true);
  $("nameSheet").classList.add("hidden");
  var w = nameWait; nameWait = null;
  if (w) w.res(name);
}
function nameSheetCancel() {
  $("nameSheet").classList.add("hidden");
  var w = nameWait; nameWait = null;
  if (w) w.rej(new Error("cancelled"));
}

// ---------------------------------------------------------------- local challenge history

function chalLog() { return store.get("chals", []); }
function chalRemember(entry) {
  var log = chalLog().filter(function (e) { return e.code !== entry.code; });
  log.unshift(entry);
  store.set("chals", log.slice(0, 30));
}

async function shareLink(text, url) {
  if (navigator.share) {
    try { await navigator.share({text: text, url: url}); return; }
    catch (e) { if (e && e.name === "AbortError") return; }
  }
  await shareText(text + "\n" + url);
}
function challengeUrl(code) { return APP_URL + "#c=" + code; }
function challengeText(c) {
  return "⚽ " + (myName() || "A mate") + " challenges you on Football Quiz: " + c.correct + "/" + c.total +
    " on " + leagueName(c.league) + ". Can you beat it? Code " + c.code;
}

// ---------------------------------------------------------------- create (classic result screen)

function offerChallenge(g) {
  var n = g.qs.length;
  lastClassic = n >= 5 ? {answers: g.answers, league: settings.league, diff: Number(settings.diff),
                          timer: Number(settings.timer), total: n, correct: g.right, code: null} : null;
  $("chBox").classList.toggle("hidden", !lastClassic);
  $("rChallenge").disabled = false;
  $("rChallenge").textContent = "Challenge a mate";
  msg("chMsg", "");
}

async function makeChallenge() {
  var c = lastClassic; if (!c) return;
  if (c.code) return shareLink(challengeText(c), challengeUrl(c.code));
  try { await ensureName("Your mate will see who the challenge is from."); } catch (e) { return; }
  $("rChallenge").disabled = true;
  msg("chMsg", "Making your challenge…");
  try {
    var r = await api("/challenges", {name: myName(), player_id: playerId(), league: c.league, diff: c.diff,
                                      timer: c.timer, answers: c.answers});
    c.code = r.code;
    chalRemember({code: r.code, role: "made", total: c.total, league: c.league, you: r.correct, at: Date.now()});
    $("rChallenge").textContent = "Share challenge " + r.code;
    msg("chMsg", "Challenge " + r.code + " is ready. Your mate plays the same " + c.total + " questions.");
    shareLink(challengeText(c), challengeUrl(r.code));
  } catch (e) {
    msg("chMsg", e.status === 0 ? "Challenges need a connection - try again when you're online." : e.message, true);
  }
  $("rChallenge").disabled = false;
}

// ---------------------------------------------------------------- hub

function openChallengeHub() {
  $("chCode").value = ""; msg("chGoMsg", "");
  show("chal");
  renderChalList();
  loadH2h();
}

function renderChalList() {
  var ul = $("chList"); ul.innerHTML = "";
  var log = chalLog();
  $("chListMsg").textContent = log.length ? "" : "Nothing yet. Challenges you send or play show up here.";
  log.forEach(function (e) {
    var meta = (e.role === "made" ? "You sent it · " : "From " + (e.from || "a mate") + " · ") +
      plural(e.total, "question") + " · " + leagueName(e.league);
    var res = e.role === "made" ? (e.you != null ? e.you + "/" + e.total : "") :
      (e.you != null ? e.you + "–" + e.them : "");
    var li = row(0, "Code " + e.code, h("span", {className: "meta"}, meta), res, false);
    li.querySelector(".pos").textContent = e.role === "made" ? "↗" : "↘";
    li.classList.add("tap");
    li.onclick = function () { openChallenge(e.code); };
    ul.appendChild(li);
  });
}

async function loadH2h() {
  var ul = $("h2hList"); ul.innerHTML = ""; $("h2hMsg").textContent = "Loading…";
  try {
    var r = await api("/h2h?player_id=" + playerId());
    $("h2hMsg").textContent = r.rows.length ? "" : "No results yet. Win, draw or lose a challenge to start a record.";
    r.rows.forEach(function (x, i) {
      var meta = h("span", {className: "meta"}, "Won " + x.won + " · drawn " + x.drawn + " · lost " + x.lost);
      var li = row(i + 1, "v " + x.name, meta, x.won + "–" + x.lost, false);
      li.querySelector(".pos").textContent = "⚔";
      ul.appendChild(li);
    });
  } catch (e) { $("h2hMsg").textContent = "Couldn't reach the server. Check your connection."; }
}

function goCode() {
  var code = tidyCode($("chCode").value);
  if (!CODE_RE.test(code)) return msg("chGoMsg", "Codes are 6 letters and numbers, like K7QF2M.", true);
  openChallenge(code);
}

// ---------------------------------------------------------------- open / play

async function openChallenge(code) {
  chal = null;
  show("chIntro");
  $("ciCode").textContent = "Challenge " + code;
  $("ciFrom").textContent = "Loading…"; $("ciSub").textContent = "";
  $("ciMeta").textContent = ""; $("ciTarget").classList.add("hidden");
  $("ciPlay").classList.add("hidden"); msg("ciMsg", "");
  var r;
  try { r = await api("/challenge?code=" + code + "&player_id=" + playerId()); }
  catch (e) {
    $("ciFrom").textContent = e.status === 404 ? "Not found" : "Offline";
    msg("ciMsg", e.status === 0 ? "Challenges need a connection. Try again when you're online." : e.message, true);
    return;
  }
  chal = r;
  if (r.mine || r.result) return showVs(r);
  $("ciFrom").textContent = r.from;
  $("ciSub").textContent = "challenges you";
  $("ciTarget").classList.remove("hidden");
  $("ciScore").textContent = r.their.correct + "/" + r.total;
  $("ciPts").textContent = fmtPts(r.their.points) + " pts";
  $("ciMeta").textContent = quizMeta(r) + ". Same questions, same order, one go. You get one 50:50.";
  var missing = r.ids.filter(function (id) { return !BY_ID[id]; }).length;
  if (!BANK.length || missing) {
    msg("ciMsg", "This challenge uses questions your app doesn't have yet. Reload the app and try again.", true);
    return;
  }
  $("ciPlay").classList.remove("hidden");
}

async function playChallenge() {
  var c = chal; if (!c) return;
  try { await ensureName("So " + c.from + " can see how you did."); } catch (e) { return; }
  lastStart = null;
  startGame({mode: "challenge", label: "Challenge · v " + c.from, qs: c.ids.map(function (id) { return BY_ID[id]; }),
             timer: c.timer, fifty: true, onFinish: function (g) { finishChallenge(g, c); }});
}

async function finishChallenge(g, c) {
  var body = {code: c.code, name: myName(), player_id: playerId(), answers: g.answers};
  chalRemember({code: c.code, role: "played", from: c.from, total: c.total, league: c.league,
                you: g.right, them: c.their.correct, at: Date.now()});
  showVs(null, {g: g, c: c});
  if (g.right === g.qs.length) celebrate("Goal!", g.right + " out of " + g.right);
  msg("vsMsg", "Sending your score…");
  try {
    var r = await api("/challenge/play", body);
    chal = r; showVs(r);
  } catch (e) {
    if (e.status === 0 || !e.permanent && e.status !== 409) {
      queuePost("challenge/play", body);
      msg("vsMsg", "You're offline. Your score is saved and will be sent next time you're online.");
    } else if (e.status === 409) {
      try { chal = await api("/challenge?code=" + c.code + "&player_id=" + playerId()); showVs(chal); }
      catch (e2) { msg("vsMsg", e.message, true); }
    } else msg("vsMsg", e.message, true);
  }
}

// ---------------------------------------------------------------- side by side

function markIcon(ok) { return h("span", {className: "mk " + (ok ? "ok" : "no"), innerHTML: ok ? TICK : CROSS}); }

// r: server reply (mine or played). local: {g, c} straight after playing, before the server answers.
function showVs(r, local) {
  show("vs");
  msg("vsMsg", "");
  var mine = r && r.mine, res = r && r.result;
  var c = r || local.c;
  $("vsLabel").textContent = "Challenge " + c.code + " · " + quizMeta(c);
  $("vsPlays").classList.toggle("hidden", !mine);
  var you, them, themName;
  if (mine) {
    you = {correct: r.their.correct, points: r.their.points, grid: r.their.grid};
    var best = r.plays[0];
    themName = best ? (r.plays.length > 1 ? "Best: " + best.name : best.name) : "Waiting…";
    them = best || null;
  } else if (res) {
    you = res.you; them = res.them; themName = res.them.name;
  } else {
    you = {correct: local.g.right, points: local.g.points,
           grid: local.g.marks.map(function (m) { return m ? "1" : "0"; }).join("")};
    them = {correct: c.their.correct, points: c.their.points}; themName = c.from;
  }
  $("vsAName").textContent = "You";
  $("vsA").textContent = you.correct + "/" + c.total;
  $("vsAP").textContent = fmtPts(you.points) + " pts";
  $("vsBName").textContent = themName;
  $("vsB").textContent = them ? them.correct + "/" + c.total : "–";
  $("vsBP").textContent = them ? fmtPts(them.points) + " pts" : "";
  var v = "", hh = "";
  if (mine) {
    v = r.plays.length ? plural(r.plays.length, "mate has", "mates have") + " played it." : "Nobody has played it yet. Send it to a mate!";
  } else if (res) {
    v = res.outcome === "win" ? "You win! 🏆" : res.outcome === "draw" ? "A draw. Honours even." : themName + " wins this one.";
    achCheck({k: "chal", res: res});
    hh = "Head to head v " + themName + ": won " + res.h2h.won + ", drawn " + res.h2h.drawn + ", lost " + res.h2h.lost;
  } else {
    var a = [you.points, you.correct], b = [them.points, them.correct];
    v = a[0] > b[0] || a[0] === b[0] && a[1] > b[1] ? "You win! 🏆" : a[0] === b[0] && a[1] === b[1] ? "A draw. Honours even." : themName + " wins this one.";
  }
  $("vsVerdict").textContent = v;
  $("vsH2h").textContent = hh; $("vsH2h").classList.toggle("hidden", !hh);
  if (mine) {
    var ul = $("vsPlayList"); ul.innerHTML = "";
    r.plays.forEach(function (p, i) {
      var win = p.points > r.their.points || p.points === r.their.points && p.correct > r.their.correct;
      var draw = p.points === r.their.points && p.correct === r.their.correct;
      var g = gridEl(p.grid.split("").map(function (x) { return x === "1"; }));
      ul.appendChild(row(i + 1, p.name, h("span", {className: "meta"}, g, win ? "beat you" : draw ? "drew" : "you won"),
                         p.correct + "/" + c.total, false));
    });
    $("vsShare").onclick = function () {
      shareLink(challengeText({correct: r.their.correct, total: c.total, league: c.league, code: c.code}), challengeUrl(c.code));
    };
  }
  // question by question: you | them
  var list = $("vsQList"); list.innerHTML = "";
  var tg = them && them.grid ? them.grid : null;
  var yourChoices = res ? res.you.choices : mine ? r.their.choices : local.g.answers.map(function (x) { return x.choice; });
  c.ids.forEach(function (id, i) {
    var q = BY_ID[id]; if (!q) return;
    var yo = you.grid.charAt(i) === "1";
    var li = h("li", {className: "tile"},
      h("div", {className: "qh"}, h("span", {className: "qn"}, String(i + 1)), h("span", {className: "qt"}, q.q),
        h("span", {className: "mks"}, markIcon(yo), tg ? markIcon(tg.charAt(i) === "1") : h("span", {className: "mk none"}, "·"))),
      h("div", {className: "ans"}, "✓ " + q.a),
      !yo ? h("div", {className: "yours"}, yourChoices[i] == null ? "✗ You ran out of time" : "✗ You said " + yourChoices[i]) : null,
      res && tg.charAt(i) !== "1" ? h("div", {className: "theirs"}, res.them.choices[i] == null ? "✗ " + themName + " ran out of time" : "✗ " + themName + " said " + res.them.choices[i]) : null);
    list.appendChild(li);
  });
  $("vsQs").classList.toggle("hidden", !c.ids.length);
  var legend = tg ? "Left: you · right: " + (mine ? themName.replace(/^Best: /, "") : themName) : "Left: you";
  $("vsQs").querySelector(".sec").textContent = "Question by question · " + legend;
  window.scrollTo(0, 0);
}

// ---------------------------------------------------------------- divisions

function timeLeft(secs) {
  var d = Math.floor(secs / 86400), hr = Math.floor(secs / 3600);
  return d >= 1 ? plural(d, "day") + " left" : hr >= 1 ? plural(hr, "hour") + " left" : "under an hour left";
}

function divZone(d) {
  if (!d.in_table) return "";
  var r = d.rank;
  if (d.up && r <= d.up) return "in the promotion places";
  if (d.down && r > d.size - d.down) return "in the drop zone";
  if (d.up) return plural(r - d.up, "place") + " off promotion";
  if (d.tier === 1) return r === 1 ? "top of the pile" : plural(r - 1, "place") + " off top spot";
  return "";
}

function divLine(d) {
  var secs = Math.max(0, d.secs_left - Math.floor((Date.now() - d.fetched) / 1000));
  if (!d.in_table) return ["Play any mode to join this week's table", timeLeft(secs)];
  var z = divZone(d);
  return [ordinal(d.rank) + (z ? " · " + z : ""), timeLeft(secs)];
}

async function loadDivision(force) {
  var c = store.get("div", null);
  if (!force && divCache && Date.now() - divCache.at < 60000) return divCache.data;
  try {
    var d = await api("/division?player_id=" + playerId(), null, 5000);
    d.fetched = Date.now();
    divCache = {data: d, at: Date.now()};
    store.set("div", d);
    achCheck();
    return d;
  } catch (e) {
    return c && c.week ? c : null;      // last known (offline)
  }
}

function renderDivStrip(d) {
  var el = $("divStrip");
  if (!d || !myName()) { el.classList.add("hidden"); return; }
  var l = divLine(d);
  $("divTier").textContent = d.tier_name;
  $("divLine").textContent = " · " + l[0] + " · " + l[1];
  el.classList.toggle("zup", d.in_table && d.up > 0 && d.rank <= d.up);
  el.classList.toggle("zdown", d.in_table && d.down > 0 && d.rank > d.size - d.down);
  el.classList.remove("hidden");
}

function refreshDivStrip() {
  if (!myName()) { renderDivStrip(null); return; }
  var c = store.get("div", null);
  if (c && c.week) renderDivStrip(c);
  loadDivision().then(function (d) {
    if (!$("home").classList.contains("hidden")) renderDivStrip(d);
  });
}

function lastWeekText(d) {
  var l = d.last; if (!l) return "";
  var key = "divSeen";
  if (store.get(key, "") === l.week + ":" + playerId()) return "";
  return l.moved > 0 ? "Promoted from " + l.from_name + " last week! ⬆" :
    l.moved < 0 ? "Relegated from " + l.from_name + " last week. Bounce back! ⬇" :
    "Survived in " + l.from_name + " last week.";
}

function renderDivCard(d) {
  if (!d) {
    $("dvTier").textContent = "Divisions";
    $("dvLine").textContent = "Couldn't reach the server. Your division shows up when you're online.";
    $("dvLeft").textContent = ""; $("dvTable").innerHTML = ""; $("dvLadder").innerHTML = "";
    $("dvLast").classList.add("hidden");
    return;
  }
  var l = divLine(d);
  $("dvTier").textContent = d.tier_name;
  $("dvLeft").textContent = l[1];
  $("dvLine").textContent = d.in_table ? ordinal(d.rank) + " of " + d.size + (divZone(d) ? " · " + divZone(d) : "") :
    (myName() ? "Play any mode to join this week's table." : "Pick a name and play any mode to join a table.");
  var lad = $("dvLadder"); lad.innerHTML = "";
  for (var t = 1; t <= d.tiers.length; t++) {
    lad.appendChild(h("span", {className: t === d.tier ? "on" : "", title: d.tiers[t - 1]}));
  }
  var lw = lastWeekText(d);
  $("dvLast").textContent = lw; $("dvLast").classList.toggle("hidden", !lw);
  if (lw) store.set("divSeen", d.last.week + ":" + playerId());
  var ul = $("dvTable"); ul.innerHTML = "";
  d.rows.forEach(function (r, i) {
    var pos = i + 1, up = d.up && pos <= d.up, dn = d.down && pos > d.size - d.down;
    var li = row(pos, r.name, h("span", {className: "meta"}, up ? "▲ promotion" : dn ? "▼ relegation" : ""), fmtPts(r.points), r.me, r.badges);
    if (up) li.classList.add("zup");
    if (dn) li.classList.add("zdown");
    ul.appendChild(li);
  });
}

// ---------------------------------------------------------------- leagues tab

var leaguesSeq = 0;
async function openLeaguesTab(joinCode) {
  show("leaguesTab");
  msg("lgFormMsg", "");
  $("lgInvite").classList.add("hidden");
  if (joinCode) showInvite(joinCode);
  $("lgName").value = ""; $("lgCode").value = "";
  var seq = ++leaguesSeq;
  var c = store.get("div", null);
  if (c && c.week) renderDivCard(c);
  loadDivision(true).then(function (d) { if (seq === leaguesSeq) renderDivCard(d); });
  var ul = $("lgList"); ul.innerHTML = ""; $("lgMsg").textContent = "Loading…";
  try {
    var r = await api("/leagues?player_id=" + playerId());
    if (seq !== leaguesSeq) return;
    $("lgMsg").textContent = r.rows.length ? "" : "You're not in a private league yet. Start one for your mates, or join with a code.";
    r.rows.forEach(function (lg) {
      var meta = plural(lg.members, "player") + (lg.rank_week ? " · you're " + ordinal(lg.rank_week) + " this week" : "");
      var li = row(0, lg.name, h("span", {className: "meta"}, meta), "›", false);
      li.querySelector(".pos").textContent = "🏆";
      li.classList.add("tap");
      li.onclick = function () { showLeague(lg.code); };
      ul.appendChild(li);
    });
  } catch (e) {
    if (seq === leaguesSeq) $("lgMsg").textContent = "Couldn't reach the server. Check your connection.";
  }
}

async function showInvite(code) {
  var box = $("lgInvite");
  try {
    var r = await api("/league?code=" + code + "&player_id=" + playerId());
    if (r.member) { showLeague(code); return; }
    $("lgInviteName").textContent = r.name;
    $("lgInviteMeta").textContent = "You've been invited · " + plural(r.members, "player") + " · code " + r.code;
    $("lgInviteJoin").onclick = function () { joinLeague(code); };
    box.classList.remove("hidden");
    window.scrollTo(0, box.offsetTop - 20);
  } catch (e) {
    msg("lgFormMsg", e.status === 404 ? "That invite link's league doesn't exist any more." : "Couldn't open the invite. Check your connection.", true);
  }
}

async function joinLeague(code) {
  code = tidyCode(code || $("lgCode").value);
  if (!CODE_RE.test(code)) return msg("lgFormMsg", "Codes are 6 letters and numbers, like K7QF2M.", true);
  try { await ensureName("So your league mates know who you are."); } catch (e) { return; }
  msg("lgFormMsg", "Joining…");
  try {
    var r = await api("/leagues/join", {code: code, name: myName(), player_id: playerId()});
    $("lgCode").value = "";
    toast(r.already ? "You're already in " + r.name : "You've joined " + r.name);
    showLeague(r.code);
  } catch (e) {
    msg("lgFormMsg", e.status === 0 ? "Leagues need a connection - try again when you're online." : e.message, true);
  }
}

async function createLeague() {
  var name = $("lgName").value.trim().replace(/\s+/g, " ");
  if (name.length < 3) return msg("lgFormMsg", "Give your league a name (3-24 characters).", true);
  try { await ensureName("So your league mates know who you are."); } catch (e) { return; }
  $("lgCreate").disabled = true;
  msg("lgFormMsg", "Creating…");
  try {
    var r = await api("/leagues", {league_name: name, name: myName(), player_id: playerId()});
    $("lgName").value = "";
    msg("lgFormMsg", "");
    showLeague(r.code);
    toast("League created. Tap Invite to send your mates the code.");
  } catch (e) {
    msg("lgFormMsg", e.status === 0 ? "Leagues need a connection - try again when you're online." : e.message, true);
  }
  $("lgCreate").disabled = false;
}

// ---------------------------------------------------------------- one league

var leagueSeq = 0;
async function showLeague(code, period) {
  openLeague = code;
  if (period) leaguePeriod = period;
  show("league");
  $("lCode").textContent = code;
  var pc = $("lPeriod"); pc.innerHTML = "";
  [["week", "This week"], ["all", "All time"]].forEach(function (p) {
    var b = h("button", {className: "chip"}, p[1]);
    b.setAttribute("aria-pressed", String(leaguePeriod === p[0]));
    b.onclick = function () { showLeague(code, p[0]); };
    pc.appendChild(b);
  });
  var ul = $("lTable"); ul.innerHTML = ""; $("lMsg").textContent = "Loading…";
  var seq = ++leagueSeq;
  try {
    var r = await api("/league?code=" + code + "&period=" + leaguePeriod + "&player_id=" + playerId());
    if (seq !== leagueSeq) return;
    $("lName").textContent = r.name;
    $("lLeave").classList.toggle("hidden", !r.member);
    $("lInvite").onclick = function () {
      shareLink("⚽ Join my Football Quiz league “" + r.name + "”. Code " + r.code, APP_URL + "#j=" + r.code);
    };
    $("lMsg").textContent = "";
    var key = leaguePeriod === "week" ? "week" : "total", other = leaguePeriod === "week" ? "total" : "week";
    r.rows.forEach(function (x, i) {
      var meta = h("span", {className: "meta"}, (other === "total" ? "All time " : "This week ") + fmtPts(x[other]));
      ul.appendChild(row(i + 1, x.name, meta, fmtPts(x[key]), x.me, x.badges));
    });
    if (!r.rows.length) $("lMsg").textContent = "Nobody here yet.";
  } catch (e) {
    if (seq === leagueSeq) $("lMsg").textContent = e.status === 404 ? e.message : "Couldn't reach the server. Check your connection.";
  }
}

async function leaveLeague() {
  var code = openLeague; if (!code) return;
  if (!window.confirm("Leave this league? You can rejoin with the code.")) return;
  try {
    await api("/leagues/leave", {code: code, player_id: playerId()});
    toast("You've left the league");
    openLeaguesTab();
  } catch (e) { toast(e.status === 0 ? "You're offline" : e.message); }
}

// ---------------------------------------------------------------- links (#c=CODE, #j=CODE)

function handleHash() {
  var m = /^#([cj])=([A-Za-z0-9\-]{6,7})$/.exec(location.hash);
  if (!m) return false;
  var code = tidyCode(m[2]);
  try { history.replaceState(null, "", location.pathname + location.search); } catch (e) { location.hash = ""; }
  if (!CODE_RE.test(code)) return false;
  if (m[1] === "c") openChallenge(code); else openLeaguesTab(code);
  return true;
}

function wireCompete() {
  $("rChallenge").onclick = makeChallenge;
  $("chalBack").onclick = goHome;
  $("chGo").onclick = goCode;
  $("chCode").addEventListener("keydown", function (e) { if (e.key === "Enter") goCode(); });
  $("chClassic").onclick = function () { openSetup(); };
  $("ciBack").onclick = goHome;
  $("ciPlay").onclick = playChallenge;
  $("vsHome").onclick = goHome;
  $("divStrip").onclick = function () { openLeaguesTab(); };
  $("lgJoin").onclick = function () { joinLeague(); };
  $("lgCode").addEventListener("keydown", function (e) { if (e.key === "Enter") joinLeague(); });
  $("lgCreate").onclick = createLeague;
  $("lgName").addEventListener("keydown", function (e) { if (e.key === "Enter") createLeague(); });
  $("lgInviteNo").onclick = function () { $("lgInvite").classList.add("hidden"); };
  $("lBack").onclick = function () { openLeaguesTab(); };
  $("lLeave").onclick = leaveLeague;
  $("nsSave").onclick = nameSheetSave;
  $("nsName").addEventListener("keydown", function (e) { if (e.key === "Enter") nameSheetSave(); });
  $("nsCancel").onclick = nameSheetCancel;
  window.addEventListener("hashchange", handleHash);
}
