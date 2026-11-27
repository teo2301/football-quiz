// Offline support. Bump VERSION after changing any app file or the file list.
// The data files (questions, stats, careers) are network-first so new content shows up without a bump.
const VERSION = "fq-v17";
const DATA = ["/questions.json", "/stats.json", "/careers.json"];
const FILES = ["./", "index.html", "app.css", "privacy.html", "questions.json", "stats.json", "careers.json", "manifest.json",
  "icon-192.png", "icon-512.png", "badge-96.png", "sounds/cheer.mp3", "sounds/groan.mp3", "sounds/whistle.mp3",
  "js/daily-gen.js", "js/match.js", "js/ach-defs.js", "js/core.js", "js/achievements.js", "js/quiz.js", "js/social.js", "js/report.js", "js/modes.js", "js/games.js", "js/compete.js", "js/live.js", "js/board.js", "js/push.js", "js/me.js", "js/app.js",
  "fonts/inter-latin.woff2", "fonts/inter-latin-ext.woff2", "fonts/oswald-latin.woff2", "fonts/oswald-latin-ext.woff2"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== VERSION).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.indexOf("/api/") >= 0) return;
  if (DATA.some(d => url.pathname.endsWith(d))) {
    e.respondWith(fetch(e.request).then(r => {
      const copy = r.clone(); caches.open(VERSION).then(c => c.put(e.request, copy)); return r;
    }).catch(() => caches.match(e.request)));
    return;
  }
  e.respondWith(caches.match(e.request, {ignoreSearch: true}).then(hit => hit || fetch(e.request)));
});

// Push notifications (Phase 6). Payload: {title, body, url, tag} from server/push.py.
self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = {body: e.data ? e.data.text() : ""}; }
  e.waitUntil(self.registration.showNotification(d.title || "Football Quiz", {
    body: d.body || "", icon: "icon-192.png", badge: "badge-96.png", tag: d.tag || "fq",
    data: {url: d.url || "./"}
  }));
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = new URL((e.notification.data && e.notification.data.url) || "./", self.registration.scope);
  e.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then(list => {
    const open = list.filter(c => c.url.indexOf(self.registration.scope) === 0);
    if (open.length) {                     // the app is open: bring it forward and let it route the hash
      open[0].postMessage({type: "open", hash: url.hash});
      return open[0].focus();
    }
    return self.clients.openWindow(url.href);
  }));
});
