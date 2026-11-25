// Names, posting results (with an offline queue) and sharing.
"use strict";
var TAKEN_RE = /taken/i;
var NAME_ERR_RE = /name/i;
var pendingPost = null;     // {kind, body, onOk} waiting for a name on the result screen

function myName() { return store.get("name", ""); }

function postMsg(text, isErr, link) {
  var m = $("postMsg"); m.innerHTML = ""; m.textContent = text;
  m.classList.toggle("err", !!isErr); m.classList.remove("hidden");
  if (link) {
    var b = h("button", {className: "linkbtn"}, link.text); b.onclick = link.fn;
    m.appendChild(document.createTextNode(" ")); m.appendChild(b);
  }
}

function askName(why) {
  $("postAsk").classList.remove("hidden");
  $("nameIn").value = myName();
  if (why) postMsg(why, true); else $("postMsg").classList.add("hidden");
}

// kind: "scores" (classic) or "daily". onOk(serverReply) renders the success line.
function submitResult(kind, body, onOk) {
  pendingPost = {kind: kind, body: body, onOk: onOk};
  $("postAsk").classList.add("hidden");
  var name = myName();
  if (!name) { askName(); return; }
  sendResult(name);
}

async function sendResult(name) {
  var p = pendingPost; if (!p) return;
  var body = Object.assign({}, p.body, {name: name, player_id: playerId()});
  postMsg("Posting as " + name + "…");
  try {
    var r = await api("/" + p.kind, body);
    pendingPost = null;
    divCache = null;            // points changed: refetch the division strip
    p.onOk(r, name);
  } catch (e) {
    if (e.status === 409 && TAKEN_RE.test(e.message)) return askName("That name is taken — pick another");
    if (e.permanent && NAME_ERR_RE.test(e.message)) return askName(e.message);
    if (e.permanent || e.status === 409) { pendingPost = null; return postMsg(e.message, true); }
    queuePost(p.kind, body);
    pendingPost = null;
    postMsg("You're offline, so your score is saved and will post next time you're online.");
  }
}

function savePostName() {
  var name = tidyName($("nameIn").value);
  if (!validName(name)) { postMsg("Use 2-16 letters, numbers or spaces.", true); return; }
  store.set("name", name);
  $("postAsk").classList.add("hidden");
  sendResult(name);
}

function queuePost(kind, body) {
  var q = store.get("pending", []);
  q.push({kind: kind, body: body});
  store.set("pending", q.slice(-20));
}

// Old app versions queued bare classic entries; those still post as they are.
async function flushPending() {
  var q = store.get("pending", []);
  if (!q.length || navigator.onLine === false) return;
  var left = [];
  for (var i = 0; i < q.length; i++) {
    var e = q[i], kind = e.kind || "scores", body = e.body || e;
    try { await api("/" + kind, body); }
    catch (err) { if (!err.permanent && err.status !== 409) left.push(e); }
  }
  store.set("pending", left);
}

// Claim or change the name straight away (Me tab).
async function claimName(name) {
  try {
    await api("/name", {name: name, player_id: playerId()});
    store.set("name", name);
    return {ok: true, text: "Saved. You're " + name + " on the boards."};
  } catch (e) {
    if (e.status === 409) return {ok: false, text: "That name is taken — pick another"};
    if (e.permanent) return {ok: false, text: e.message};
    store.set("name", name);
    return {ok: true, text: "Saved on this phone. It'll be checked next time you post online."};
  }
}

async function shareText(text) {
  if (navigator.share) {
    try { await navigator.share({text: text}); return; }
    catch (e) { if (e && e.name === "AbortError") return; }
  }
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) { await navigator.clipboard.writeText(text); toast("Copied - paste it anywhere"); return; }
  } catch (e) { /* fall through */ }
  var ta = h("textarea", {value: text, style: "position:fixed;top:0;left:0;opacity:0"});
  document.body.appendChild(ta); ta.focus(); ta.select();
  var ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  document.body.removeChild(ta);
  toast(ok ? "Copied - paste it anywhere" : "Couldn't copy on this browser");
}
