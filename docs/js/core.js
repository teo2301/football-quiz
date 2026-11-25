// Shared helpers. Plain ES2017 (iOS 12): no ?. ?? or catch-without-binding.
"use strict";
var LEAGUES = {all: "All", pl: "Premier League", ll: "La Liga", sa: "Serie A", eu: "Europe", in: "International"};
var DIFFS = {0: "Mixed", 1: "Easy", 2: "Medium", 3: "Hard"};
var LENGTHS = {10: "10", 20: "20", 50: "50"};
var TIMERS = {0: "Off", 20: "20 s", 10: "10 s"};
var SITE = "https://teo2301.github.io/football-quiz/";

// Leaderboard API: the preview server proxies /api; everywhere else it's the
// home server, public via Tailscale Funnel.
var API = location.port === "8197" ? "/api" : "https://riccioserver.tail0d3eb6.ts.net:10000/api";

var store = {
  get: function (k, d) {
    try { var v = localStorage.getItem("fq_" + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; }
  },
  set: function (k, v) { try { localStorage.setItem("fq_" + k, JSON.stringify(v)); } catch (e) { /* private mode */ } }
};

var settings = Object.assign({league: "all", diff: "0", len: "10", timer: "0"}, store.get("settings", {}));
var BANK = [], BY_ID = {}, PICKER = null;
var $ = function (id) { return document.getElementById(id); };

// h("div", {className: "x"}, child, "text", ...)
function h(tag, props) {
  var el = document.createElement(tag);
  if (props) for (var k in props) {
    if (k === "style") el.setAttribute("style", props[k]);
    else if (k.slice(0, 5) === "data-" || k.slice(0, 5) === "aria-") el.setAttribute(k, props[k]);
    else el[k] = props[k];
  }
  for (var i = 2; i < arguments.length; i++) {
    var c = arguments[i];
    if (c == null || c === false) continue;
    el.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
  }
  return el;
}

function uuid4() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  var b = new Uint8Array(16);
  if (window.crypto && crypto.getRandomValues) crypto.getRandomValues(b);
  else for (var i = 0; i < 16; i++) b[i] = Math.floor(Math.random() * 256);
  b[6] = (b[6] & 15) | 64; b[8] = (b[8] & 63) | 128;
  var s = "";
  for (var j = 0; j < 16; j++) { s += (b[j] + 256).toString(16).slice(1); if (j === 3 || j === 5 || j === 7 || j === 9) s += "-"; }
  return s;
}
function playerId() {
  var id = store.get("pid", "");
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(id)) { id = uuid4(); store.set("pid", id); }
  return id;
}

function shuffle(a) {
  for (var i = a.length - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)); var t = a[i]; a[i] = a[j]; a[j] = t; }
  return a;
}

function withTimeout(p, ms) {
  return new Promise(function (res, rej) {
    var t = setTimeout(function () { rej(new Error("timeout")); }, ms);
    p.then(function (v) { clearTimeout(t); res(v); }, function (e) { clearTimeout(t); rej(e); });
  });
}

// GET/POST JSON. Errors carry .status (0 = network) and .permanent (4xx: don't retry).
async function api(path, body, ms) {
  var opts = body ? {method: "POST", body: JSON.stringify(body)} : {};
  var res;
  try { res = await withTimeout(fetch(API + path, opts), ms || 8000); }
  catch (e) { var ne = new Error("offline"); ne.status = 0; throw ne; }
  var data = {};
  try { data = await res.json(); } catch (e) { data = {}; }
  if (!res.ok) {
    var err = new Error(data.error || "Server error");
    err.status = res.status; err.permanent = res.status >= 400 && res.status < 500 && res.status !== 429;
    throw err;
  }
  return data;
}

var toastTimer = null;
function toast(text) {
  var t = $("toast"); t.textContent = text; t.classList.remove("hidden");
  clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.classList.add("hidden"); }, 2600);
}

var SCREENS = ["home", "setup", "play", "done", "boards", "leaguesTab", "me"];
var TAB_OF = {home: "home", setup: "home", play: "home", done: "home", boards: "boards", leaguesTab: "leaguesTab", me: "me"};
function show(id) {
  SCREENS.forEach(function (s) { $(s).classList.toggle("hidden", s !== id); });
  document.body.classList.toggle("playing", id === "play");
  var tab = TAB_OF[id];
  var btns = document.querySelectorAll("#tabbar button");
  for (var i = 0; i < btns.length; i++) {
    if (btns[i].getAttribute("data-tab") === tab) btns[i].setAttribute("aria-current", "page");
    else btns[i].removeAttribute("aria-current");
  }
  window.scrollTo(0, 0);
}

function pad2(n) { return (n < 10 ? "0" : "") + n; }
function hms(ms) {
  var s = Math.max(0, Math.floor(ms / 1000));
  return Math.floor(s / 3600) + ":" + pad2(Math.floor(s / 60) % 60) + ":" + pad2(s % 60);
}
function ordinal(n) {
  var s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

function validName(n) { return n.length >= 2 && n.length <= 16 && !/[<>"\\\/{}@#%^&*+=~`|;:!?,()\[\]$]/.test(n); }
function tidyName(n) { return String(n || "").trim().replace(/\s+/g, " "); }

function loadBank() {
  return fetch("questions.json").then(function (r) { return r.json(); }).then(function (q) {
    BANK = q; BY_ID = {};
    for (var i = 0; i < q.length; i++) BY_ID[q[i].id] = q[i];
    PICKER = new FQDaily.Picker(q);
  });
}
