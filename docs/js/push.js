// Push notifications (Phase 6). Plain ES2017 (iOS 12): no ?. ?? or catch-without-binding.
// Never asks on first launch: the offer comes after a finished daily quiz, and the
// switches live on the Me tab. The browser permission prompt only ever follows a tap.
"use strict";
var PUSH_TYPES = [
  {id: "daily", name: "Today's quiz is up", desc: "Once a day, around 9am"},
  {id: "challenge", name: "A mate beat your challenge", desc: "Straight away, so you can hit back"},
  {id: "division", name: "Promotion and relegation", desc: "Mondays: how your week ended"}
];
var ALL_PUSH = ["daily", "challenge", "division"];
var pushState = Object.assign({on: false, types: [], asked: false, endpoint: "", key: ""}, store.get("push", {}));
var pushBusy = false;

function pushSupported() {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}
function pushSave() { store.set("push", pushState); }
function pushPerm() { return pushSupported() ? Notification.permission : "unsupported"; }

function b64urlBytes(s) {
  var b = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4));
  var out = new Uint8Array(b.length);
  for (var i = 0; i < b.length; i++) out[i] = b.charCodeAt(i);
  return out;
}

function askPermission() {
  // Older Chrome/Safari used a callback instead of a promise.
  return new Promise(function (res) {
    var p = Notification.requestPermission(res);
    if (p && p.then) p.then(res, function () { res("default"); });
  });
}

async function pushSubscription(key) {
  var reg = await withTimeout(navigator.serviceWorker.ready, 8000);
  var sub = await reg.pushManager.getSubscription();
  if (sub && key && pushState.key && pushState.key !== key) {   // the server's key changed: start again
    try { await sub.unsubscribe(); } catch (e) { /* gone already */ }
    sub = null;
  }
  // A browser without a working push service can hang here for ever: give up after 20 s.
  if (!sub) sub = await withTimeout(reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: b64urlBytes(key)}), 20000);
  return sub;
}

// Switch notifications on with these types. Resolves {ok, text}.
async function pushOn(types) {
  if (!pushSupported()) return {ok: false, text: "This browser can't show notifications."};
  var perm = Notification.permission === "granted" ? "granted" : await askPermission();
  if (perm !== "granted") {
    return {ok: false, text: perm === "denied"
      ? "Notifications are blocked for this site. Allow them in your browser's site settings, then try again."
      : "No problem - you can switch them on here any time."};
  }
  try {
    var key = (await api("/push/key")).key;
    var sub = await pushSubscription(key);
    await api("/push", {player_id: playerId(), subscription: sub.toJSON(), types: types});
    pushState.on = true; pushState.types = types; pushState.endpoint = sub.endpoint; pushState.key = key;
    pushState.asked = true; pushSave();
    return {ok: true, text: ""};
  } catch (e) {
    return {ok: false, text: e.status === 503 ? "Notifications aren't available yet - try again later." :
      e.status === 0 ? "You're offline. Try again when you're connected." :
      e.status ? (e.message || "Couldn't switch them on.") : "This browser couldn't set up notifications."};
  }
}

async function pushOff() {
  var ep = pushState.endpoint;
  try {
    var reg = await withTimeout(navigator.serviceWorker.ready, 8000);
    var sub = await reg.pushManager.getSubscription();
    if (sub) { ep = sub.endpoint; await sub.unsubscribe(); }
  } catch (e) { /* nothing to undo */ }
  pushState.on = false; pushState.types = []; pushState.endpoint = ""; pushSave();
  if (ep) {
    try { await api("/push", {endpoint: ep}, 8000, "DELETE"); }
    catch (e) { store.set("pushGone", ep); }      // tell the server next time we're online
  }
  return {ok: true, text: ""};
}

async function pushSetType(id, want) {
  var types = ALL_PUSH.filter(function (t) {
    return t === id ? want : pushState.on && pushState.types.indexOf(t) >= 0;
  });
  if (!types.length) return pushOff();
  return pushOn(types);
}

// On start: keep the server's copy in step with the browser (it can drop or renew a subscription).
async function pushRefresh() {
  var gone = store.get("pushGone", "");
  if (gone) {
    try { await api("/push", {endpoint: gone}, 8000, "DELETE"); store.set("pushGone", ""); } catch (e) { /* later */ }
  }
  if (!pushState.on || !pushSupported()) return;
  if (Notification.permission !== "granted") {      // revoked in the browser settings
    pushState.on = false; pushState.types = []; pushSave();
    if (pushState.endpoint) { store.set("pushGone", pushState.endpoint); pushState.endpoint = ""; pushSave(); }
    return;
  }
  try {
    var reg = await withTimeout(navigator.serviceWorker.ready, 8000);
    var sub = await reg.pushManager.getSubscription();
    if (sub && sub.endpoint === pushState.endpoint) return;
    await pushOn(pushState.types.length ? pushState.types : ALL_PUSH);
  } catch (e) { /* try again next launch */ }
}

// ---------------------------------------------------------------- the offer after a daily quiz

function pushOfferWanted() {
  return pushSupported() && !pushState.on && !pushState.asked && Notification.permission === "default";
}

function showPushOffer() {
  var box = $("pushAsk");
  box.classList.toggle("hidden", !pushOfferWanted());
  $("paMsg").classList.add("hidden");
  $("paYes").disabled = false;
}

async function pushOfferYes() {
  $("paYes").disabled = true;
  var r = await pushOn(ALL_PUSH);
  pushState.asked = true; pushSave();
  if (r.ok) {
    $("pushAsk").classList.add("hidden");
    toast("Notifications on. Change them in the Me tab.");
  } else {
    $("paMsg").textContent = r.text; $("paMsg").classList.remove("hidden");
    $("paYes").disabled = false;
  }
}

function pushOfferNo() {
  pushState.asked = true; pushSave();
  $("pushAsk").classList.add("hidden");
  toast("No worries. You can switch them on in the Me tab.");
}

// ---------------------------------------------------------------- Me tab

function renderPush(msg, err) {
  var list = $("pushList"), note = $("pushNote"), m = $("pushMsg");
  list.innerHTML = "";
  var perm = pushPerm();
  if (perm === "unsupported") {
    note.textContent = "This browser can't show notifications. On an iPhone, add the quiz to your Home Screen first (iOS 16.4 or later).";
    note.classList.remove("hidden"); list.classList.add("hidden"); m.classList.add("hidden");
    return;
  }
  list.classList.remove("hidden");
  var blocked = perm === "denied";
  PUSH_TYPES.forEach(function (t) {
    var on = pushState.on && pushState.types.indexOf(t.id) >= 0;
    var sw = h("button", {className: "switch", type: "button", "aria-label": t.name, "aria-checked": String(on),
                          disabled: pushBusy || blocked});
    sw.setAttribute("role", "switch");
    sw.onclick = function () { togglePush(t.id, !on); };
    list.appendChild(h("div", {className: "setrow"},
      h("span", {className: "stx"}, h("b", null, t.name), h("span", null, t.desc)), sw));
  });
  note.textContent = blocked ? "Notifications are blocked for this site. Allow them in your browser's site settings to use these." :
    "Only the ones you switch on. Nothing else, ever.";
  note.classList.remove("hidden");
  m.textContent = msg || ""; m.classList.toggle("hidden", !msg); m.classList.toggle("err", !!err);
}

async function togglePush(id, want) {
  if (pushBusy) return;
  pushBusy = true; renderPush();
  var r = await pushSetType(id, want);
  pushBusy = false;
  renderPush(r.ok ? "" : r.text, !r.ok);
}

function wirePush() {
  $("paYes").onclick = pushOfferYes;
  $("paNo").onclick = pushOfferNo;
  if ("serviceWorker" in navigator) {
    // A tapped notification when the app is already open: the service worker sends the hash here.
    navigator.serviceWorker.addEventListener("message", function (e) {
      var d = e.data || {};
      if (d.type === "open" && typeof d.hash === "string") {
        if (location.hash === d.hash) handleHash(); else location.hash = d.hash;
      }
    });
  }
  pushRefresh();
}
