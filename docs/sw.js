// Offline support. Bump VERSION after changing any app file or the file list.
// The data files (questions, stats, careers) are network-first so new content shows up without a bump.
const VERSION = "fq-v8";
const DATA = ["/questions.json", "/stats.json", "/careers.json"];
const FILES = ["./", "index.html", "app.css", "privacy.html", "questions.json", "stats.json", "careers.json", "manifest.json",
  "icon-192.png", "icon-512.png", "sounds/cheer.mp3", "sounds/groan.mp3", "sounds/whistle.mp3",
  "js/daily-gen.js", "js/match.js", "js/ach-defs.js", "js/core.js", "js/achievements.js", "js/quiz.js", "js/social.js", "js/report.js", "js/modes.js", "js/games.js", "js/compete.js", "js/board.js", "js/me.js", "js/app.js",
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
