#!/usr/bin/env python3
"""Drive the preview (:8197) in headless Chromium at 390x844, light + dark,
and save screenshots to preview-shots/. Prints console errors.

    python3 tools/drive_preview.py [light|dark|both]
"""
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import websockets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

CHROME = os.path.expanduser("~/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome")
URL = "http://127.0.0.1:8197/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "preview-shots")
PORT = 9333


class Tab:
    def __init__(self, ws, tag):
        self.ws, self.tag, self.i, self.errors, self.waiting = ws, tag, 0, [], {}
        self.sw_reg = None

    async def reader(self):
        async for raw in self.ws:
            m = json.loads(raw)
            if "id" in m and m["id"] in self.waiting:
                self.waiting.pop(m["id"]).set_result(m)
            meth, p = m.get("method", ""), m.get("params", {})
            if meth == "Runtime.exceptionThrown":
                self.errors.append("EXC " + json.dumps(p["exceptionDetails"])[:400])
            elif meth == "Runtime.consoleAPICalled" and p.get("type") in ("error", "warning"):
                self.errors.append("CON %s %s" % (p["type"], [a.get("value", a.get("description")) for a in p["args"]][:3]))
            elif meth == "ServiceWorker.workerRegistrationUpdated":
                for reg in p.get("registrations", []):
                    if not reg.get("isDeleted"):
                        self.sw_reg = reg["registrationId"]
            elif meth == "Log.entryAdded" and p["entry"]["level"] in ("error", "warning"):
                self.errors.append("LOG " + p["entry"]["text"][:300] + " " + p["entry"].get("url", ""))

    async def send(self, method, params=None):
        self.i += 1
        fut = asyncio.get_event_loop().create_future()
        self.waiting[self.i] = fut
        await self.ws.send(json.dumps({"id": self.i, "method": method, "params": params or {}}))
        m = await asyncio.wait_for(fut, 30)
        if "error" in m:
            raise RuntimeError(m["error"])
        return m.get("result", {})

    async def js(self, expr):
        r = await self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            raise RuntimeError("JS error: " + json.dumps(r["exceptionDetails"])[:500])
        return r.get("result", {}).get("value")

    async def wait(self, expr, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if await self.js(expr):
                return True
            await asyncio.sleep(0.15)
        raise RuntimeError("timeout waiting for " + expr)

    async def shot(self, name, full=True):
        await asyncio.sleep(0.35)
        params = {"format": "png"}
        if full:
            hgt = await self.js("Math.max(document.documentElement.scrollHeight, 844)")
            params["clip"] = {"x": 0, "y": 0, "width": 390, "height": hgt, "scale": 1}
            params["captureBeyondViewport"] = True
        r = await self.send("Page.captureScreenshot", params)
        path = os.path.join(OUT, "%s-%s.png" % (self.tag, name))
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["data"]))
        print("shot", path)

    async def goto(self, url):
        await self.send("Page.navigate", {"url": url})
        await asyncio.sleep(0.5)
        await self.wait("document.readyState === 'complete'")


# ---------------------------------------------------------------- push (Phase 6)
# Headless Chromium has no push service (pushManager.subscribe hangs), so the page gets a fake
# PushSubscription pointing at a local fake push service on :8196 (preview_start.sh allows it via
# PUSH_EXTRA_HOSTS). The keys are real, so the server's pywebpush encrypts to it as usual.
PUSH_PORT = 8196
PUSHED = []


class FakePush(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        PUSHED.append((self.path, len(body), self.headers.get("Authorization", "")[:10]))
        self.send_response(201 if self.path.startswith("/ok") else 410)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


def fake_sub():
    import base64
    import uuid
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    pub = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"endpoint": "http://127.0.0.1:%d/ok/%s" % (PUSH_PORT, uuid.uuid4().hex),
            "keys": {"p256dh": b64(pub), "auth": b64(os.urandom(16))}}


FAKE_PUSH_JS = """(function () {
  if (!window.PushManager) return;
  var S = %s;
  function Sub() { this.endpoint = S.endpoint; this.expirationTime = null; }
  Sub.prototype.toJSON = function () { return {endpoint: S.endpoint, expirationTime: null, keys: S.keys}; };
  Sub.prototype.unsubscribe = function () { localStorage.removeItem("__fakesub"); return Promise.resolve(true); };
  PushManager.prototype.getSubscription = function () {
    return Promise.resolve(localStorage.getItem("__fakesub") === "1" ? new Sub() : null); };
  PushManager.prototype.subscribe = function () { localStorage.setItem("__fakesub", "1"); return Promise.resolve(new Sub()); };
})();"""


def db_rows(sql, args=()):
    import sqlite3
    c = sqlite3.connect("file:/tmp/fq-v2-test.db?mode=ro", uri=True)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


async def set_notif_perm(tab, setting):
    await tab.send("Browser.setPermission", {"permission": {"name": "notifications"}, "setting": setting,
                                             "origin": tab.base.rstrip("/")})


async def push_flow(tab, scheme, name, shared):
    """On the daily result: the offer. Light says yes (permission granted), dark says not now."""
    assert await tab.js("Notification.permission") == "default", "permission was asked before the daily"
    await tab.wait(VISIBLE % "pushAsk")
    await tab.js("document.getElementById('pushAsk').scrollIntoView({block: 'center'})")
    await tab.shot("63-push-offer", full=False)
    pid = await tab.js("playerId()")
    if scheme == "dark":
        await tab.js("document.getElementById('paNo').click()")
        await tab.wait("document.getElementById('pushAsk').classList.contains('hidden')")
        assert not db_rows("SELECT 1 FROM push_subs WHERE player_id = ?", (pid,))
        await tab.shot("64-push-not-now", full=False)
        return
    await set_notif_perm(tab, "granted")         # = the player tapping Allow on the browser's prompt
    await tab.js("document.getElementById('paYes').click()")
    await tab.wait("document.getElementById('pushAsk').classList.contains('hidden')", 15)
    await tab.shot("64-push-on-toast", full=False)
    rows = db_rows("SELECT types, endpoint FROM push_subs WHERE player_id = ?", (pid,))
    assert rows and rows[0][0] == "daily,challenge,division", rows
    # the server really sends to it (push_send.py test, against the test DB, through pywebpush)
    n = len(PUSHED)
    env = dict(os.environ, QUIZ_DB="/tmp/fq-v2-test.db", VAPID_PATH=os.path.expanduser("~/keys/football-quiz-vapid.json"),
               PUSH_EXTRA_HOSTS="127.0.0.1:%d" % PUSH_PORT,
               QUESTIONS_PATH=os.path.join(os.path.dirname(OUT), "docs", "questions.json"))
    root = os.path.dirname(os.path.abspath(OUT))
    out = subprocess.run([os.path.join(root, ".venv", "bin", "python"), os.path.join(root, "server", "push_send.py"),
                          "test", name], env=env, capture_output=True, text=True, timeout=60)
    print(scheme, "push_send test:", out.stdout.strip(), out.stderr.strip()[-300:])
    assert len(PUSHED) == n + 1 and PUSHED[-1][0].startswith("/ok") and PUSHED[-1][2].startswith("vapid"), PUSHED
    # what the phone would show: hand the service worker a push event and record what it asks to show.
    # Headless Chromium has no push service (ServiceWorker.deliverPushMessage is dropped) and keeps no
    # notifications, so: attach to the worker, wrap showNotification, dispatch a PushEvent by hand.
    targets = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % tab.port))
    sw_url = [t for t in targets if t["type"] == "service_worker" and t["url"].startswith(tab.base)][0]["webSocketDebuggerUrl"]
    async with websockets.connect(sw_url, max_size=10_000_000) as sws:
        sw = Tab(sws, scheme + "-sw")
        sreader = asyncio.ensure_future(sw.reader())
        await sw.send("Runtime.enable")
        await sw.js("self.__shown = []; var _sn = self.registration.showNotification.bind(self.registration);"
                    "self.registration.showNotification = function (t, o) {"
                    "  self.__shown.push([t, o.body, o.tag, o.data.url, o.badge, o.icon]); return _sn(t, o); };"
                    "var ev = new PushEvent('push', {data: %s});"
                    "ev.waitUntil = function (p) { self.__wait = p; };"   # untrusted events can't waitUntil
                    "self.dispatchEvent(ev); true" % json.dumps(json.dumps(
                        {"title": "Football Quiz #3 is up", "body": "Keep your 2-day streak going.",
                         "url": "./#daily", "tag": "daily"})))
        await sw.wait("self.__shown.length > 0", 10)
        note = await sw.js("self.__shown[0]")
        sreader.cancel()
        tab.errors += sw.errors
    print(scheme, "service worker showed:", note)
    assert note[:4] == ["Football Quiz #3 is up", "Keep your 2-day streak going.", "daily", "./#daily"], note
    shared["push_pid"] = pid


async def push_me(tab, scheme):
    """Me tab notification switches (+ blocked state in dark)."""
    await tab.js("document.querySelector('[data-tab=me]').click()")
    await tab.js("document.getElementById('pushList').scrollIntoView({block: 'center'})")
    await tab.shot("65-me-notifications", full=False)
    pid = await tab.js("playerId()")
    sw = "document.querySelectorAll('#pushList .switch')[%d]"
    if scheme == "light":
        await tab.js((sw % 2) + ".click()")                 # promotion/relegation off (challenge stays on)
        await tab.wait((sw % 2) + ".getAttribute('aria-checked') === 'false' && !" + (sw % 2) + ".disabled", 10)
        assert db_rows("SELECT types FROM push_subs WHERE player_id = ?", (pid,))[0][0] == "daily,challenge"
        await tab.shot("66-me-notifications-changed", full=False)
        # a notification tap with the app open lands on the right tab
        await tab.js("location.hash = '#leagues'")
        await tab.wait(VISIBLE % "leaguesTab")
        await tab.js("location.hash = '#daily'")
        await tab.wait(VISIBLE % "home")
    else:
        await set_notif_perm(tab, "granted")              # = Allow on the browser's prompt
        await tab.js((sw % 0) + ".click()")                 # off -> on, daily only
        await tab.wait((sw % 0) + ".getAttribute('aria-checked') === 'true' && !" + (sw % 0) + ".disabled", 10)
        assert db_rows("SELECT types FROM push_subs WHERE player_id = ?", (pid,))[0][0] == "daily"
        await tab.js((sw % 0) + ".click()")                 # and off again = deleted on the server
        await tab.wait((sw % 0) + ".getAttribute('aria-checked') === 'false' && !" + (sw % 0) + ".disabled", 10)
        assert not db_rows("SELECT 1 FROM push_subs WHERE player_id = ?", (pid,))
        await set_notif_perm(tab, "denied")
        await tab.js("renderMe()")
        await tab.js("document.getElementById('pushList').scrollIntoView({block: 'center'})")
        assert await tab.js("[].every.call(document.querySelectorAll('#pushList .switch'), function (b) { return b.disabled })")
        assert "blocked" in await tab.js("document.getElementById('pushNote').textContent")
        await tab.shot("66-me-notifications-blocked", full=False)
    await tab.js("document.querySelector('[data-tab=home]').click()")


async def report_flow(tab):
    """On an answered question: open the report sheet, try 'Something else' with no note, then send a typo report."""
    await tab.wait(VISIBLE % "reportBtn")
    await tab.js("document.getElementById('reportBtn').click()")
    await tab.wait(VISIBLE % "reportSheet")
    await tab.shot("60-report-sheet", full=False)
    await tab.js("document.querySelector('#rpWhy [data-r=other]').click();document.getElementById('rpSend').click()")
    await tab.wait("/box/.test(document.getElementById('rpMsg').textContent)")
    await tab.shot("61-report-needs-note", full=False)
    await tab.js("document.querySelector('#rpWhy [data-r=typo]').click();"
                 "document.getElementById('rpNote').value='Preview test report - please ignore';"
                 "document.getElementById('rpSend').click()")
    await tab.wait("document.getElementById('reportSheet').classList.contains('hidden') && document.getElementById('reportBtn').disabled")
    await tab.shot("62-report-sent", full=False)


async def play(tab, rights, snap_first=None, fifty_at=None, auto=False, report_at=None):
    """Answer the current game: rights[i] True = pick the right answer.
    fifty_at: use the 50:50 before answering that question. auto: the game moves on by itself (blitz)."""
    for i, ok in enumerate(rights):
        await tab.wait("!!game && !game.locked && document.querySelectorAll('#opts .opt').length === 4")
        pick = ("game.qs[game.i].a" if ok else "game.qs[game.i].w[0]")
        if i == fifty_at:
            await tab.js("document.getElementById('fifty').click()")
            await tab.shot((snap_first or "x") + "-fifty", full=False)
        if snap_first is not None and i == 0:
            await tab.shot(snap_first + "-question", full=False)
        await tab.js("(function(){var v=%s;var b=[].slice.call(document.querySelectorAll('#opts .opt')).filter(function(x){return x.getAttribute('data-v')===v})[0];b.click();return true})()" % pick)
        if snap_first is not None and i in (0, 1):
            await tab.shot("%s-answer%d-%s" % (snap_first, i + 1, "right" if ok else "wrong"), full=False)
        if i == report_at:
            await report_flow(tab)
        if auto:
            await tab.wait("!game || game.answers.length === %d && !game.locked" % (i + 1))
        else:
            await tab.js("document.getElementById('next').click()")


async def wait_posted(tab):
    await tab.wait("/Posted as/.test(document.getElementById('postMsg').textContent)", 10)


async def new_modes(tab):
    """Survival, Blitz, Higher or lower, Career path; each ends on its result screen and posts."""
    tile = "document.querySelector('[data-mode=%s]').click()"
    await tab.js("document.getElementById('menu').click()")
    # Survival: 4 right then out
    await tab.js(tile % "survival")
    await play(tab, [True, True, True, True, False], snap_first="20-survival")
    await tab.wait("!document.getElementById('done').classList.contains('hidden')")
    await wait_posted(tab)
    await tab.shot("21-survival-result")
    # Blitz: a few answers, then run the clock out
    await tab.js("document.getElementById('menu').click()")
    await tab.js(tile % "blitz")
    await play(tab, [True, True, False, True, True, True], auto=True)
    await tab.shot("22-blitz-playing", full=False)
    await tab.js("game.deadline = Date.now(); true")
    await tab.wait("!document.getElementById('done').classList.contains('hidden')")
    await wait_posted(tab)
    await tab.shot("23-blitz-result")
    # Higher or lower: 3 right calls then a wrong one
    await tab.js("document.getElementById('menu').click()")
    await tab.js(tile % "hilo")
    await tab.wait("!!hilo && !!hilo.pair && !document.getElementById('hilo').classList.contains('hidden')")
    await tab.shot("24-hilo-question", full=False)
    right = "(hilo.pair.b.v > hilo.pair.a.v ? 'hiBtn' : 'loBtn')"
    wrong = "(hilo.pair.b.v > hilo.pair.a.v ? 'loBtn' : 'hiBtn')"
    for k in range(4):
        await tab.wait("!!hilo && !hilo.locked")
        await tab.js("document.getElementById(%s).click()" % (right if k < 3 else wrong))
        if k == 0:
            await tab.shot("25-hilo-right", full=False)
        if k == 3:
            await tab.shot("26-hilo-wrong", full=False)
        await tab.js("document.getElementById('hiNext').click()")
    await tab.wait("!document.getElementById('done').classList.contains('hidden')")
    await wait_posted(tab)
    await tab.shot("27-hilo-result")
    # Career path: wrong guess, then right; clue+right; give up; typo'd right; right at once
    await tab.js("document.getElementById('menu').click()")
    await tab.js(tile % "career")
    await tab.wait("!!career && !document.getElementById('career').classList.contains('hidden')")
    await tab.shot("28-career-clue1", full=False)
    name = "career.players[career.i].n"
    guess = "(function(g){var i=document.getElementById('cGuess');i.value=g;document.getElementById('cGuessBtn').click();return true})(%s)"
    await tab.js(guess % "'Nobody Atall'")
    await tab.shot("29-career-wrong", full=False)
    await tab.js(guess % name)
    await tab.shot("30-career-right")
    await tab.js("document.getElementById('cNext').click()")
    await tab.js("document.getElementById('cClue').click()")
    await tab.js(guess % name.replace(".n", ".n.toLowerCase()"))
    await tab.js("document.getElementById('cNext').click()")
    await tab.js("document.getElementById('cGiveUp').click()")
    await tab.shot("31-career-gaveup")
    await tab.js("document.getElementById('cNext').click()")
    await tab.js(guess % (name + ".split(' ').slice(-1)[0]"))
    await tab.js("document.getElementById('cNext').click()")
    await tab.js(guess % name)
    await tab.js("document.getElementById('cNext').click()")
    await tab.wait("!document.getElementById('done').classList.contains('hidden')")
    await wait_posted(tab)
    await tab.shot("32-career-result")


async def achievements_and_feel(tab):
    """Phase 4: haptics/sound settings, a perfect Classic (goal celebration + achievement toast),
    the Me grid, badges on the board, the reduced-motion celebration."""
    # record vibrations instead of buzzing
    await tab.js("window.__buzz=[];navigator.vibrate=function(p){window.__buzz.push(p);return true};true")
    # a real (CDP) tap on the Me tab: browsers only allow vibration after the user has tapped
    xy = await tab.js("(function(){var r=document.querySelector('[data-tab=me]').getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2]})()")
    for t in ("mousePressed", "mouseReleased"):
        await tab.send("Input.dispatchMouseEvent", {"type": t, "x": xy[0], "y": xy[1], "button": "left", "clickCount": 1})
    await tab.wait(VISIBLE % "me")
    await tab.js("document.getElementById('setSound').click()")
    assert await tab.js("feel.sound === true && feel.haptics === true"), "sound toggle / haptics default"
    await tab.js("window.scrollTo(0, document.getElementById('setSound').getBoundingClientRect().top + scrollY - 500)")
    await tab.shot("50-me-settings", full=False)
    for f in ("cheer", "groan", "whistle"):
        st = await tab.js("fetch('sounds/%s.mp3').then(function(r){return r.status + ' ' + r.headers.get('content-type')})" % f)
        print(tab.tag, "sound", f, st)
    # a perfect Classic: 10 from 10
    await tab.js("document.querySelector('[data-tab=home]').click()")
    await tab.js("document.querySelector('[data-mode=classic]').click()")
    await tab.js("[].slice.call(document.querySelectorAll('#lengths .chip')).filter(function(b){return b.textContent==='10'})[0].click()")
    await tab.js("document.getElementById('start').click()")
    await play(tab, [True] * 9 + [False])
    buzz = await tab.js("JSON.stringify(window.__buzz.slice(-2))")
    print(tab.tag, "vibrate calls (right, wrong):", buzz)
    await tab.js("document.getElementById('again').click()")
    await play(tab, [True] * 10)
    await tab.wait(VISIBLE % "goal")
    await asyncio.sleep(0.5)
    await tab.shot("51-goal", full=False)
    await tab.wait("document.getElementById('achToast').classList.contains('in')", 8)
    await asyncio.sleep(0.4)
    await tab.shot("52-ach-toast", full=False)
    got = await tab.js("Object.keys(achHave())")
    print(tab.tag, "achievements:", got)
    assert "perfect10" in got, got
    await tab.wait("!!store.get('achSynced', '')", 10)
    await tab.js("document.querySelector('[data-tab=me]').click()")
    await tab.js("window.scrollTo(0, document.getElementById('achGrid').getBoundingClientRect().top + scrollY - 80)")
    await tab.shot("53-me-achievements", full=False)
    await tab.js("document.querySelector('[data-tab=boards]').click()")
    await tab.js("document.querySelectorAll('#bKind button')[1].click()")
    await tab.wait(LOADED % "bMsg")
    await tab.wait("document.querySelectorAll('#lb .badge').length > 0", 8)
    await tab.shot("54-board-badges")
    await tab.send("Emulation.setEmulatedMedia", {"features": [
        {"name": "prefers-color-scheme", "value": tab.tag}, {"name": "prefers-reduced-motion", "value": "reduce"}]})
    await tab.js("celebrate('Goal!', '7 out of 7'); true")
    await tab.shot("55-goal-reduced-motion", full=False)
    n = await tab.js("document.querySelectorAll('#confetti i').length")
    print(tab.tag, "reduced motion confetti pieces:", n)
    await tab.js("document.getElementById('goal').click()")
    await tab.send("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": tab.tag}]})


LOADED = "document.getElementById('%s').textContent !== 'Loading…'"
VISIBLE = "!document.getElementById('%s').classList.contains('hidden')"


async def social_maker(tab, shared):
    """Light run: send a challenge from the classic result, start a private league."""
    await tab.js("document.getElementById('rChallenge').click()")
    await tab.wait("!!lastClassic && !!lastClassic.code", 10)
    shared["challenge"] = await tab.js("lastClassic.code")
    await tab.shot("06b-challenge-made")
    await tab.js("document.querySelector('[data-tab=leaguesTab]').click()")
    await tab.wait(LOADED % "lgMsg")
    await tab.js("document.getElementById('lgName').value='Preview FC';document.getElementById('lgCreate').click()")
    await tab.wait(VISIBLE % "league")
    await tab.wait(LOADED % "lMsg")
    shared["league"] = await tab.js("document.getElementById('lCode').textContent")
    await tab.shot("40-league-created")


async def social_player(tab, shared):
    """Dark run: open the challenge link, play it, then the league invite link, join."""
    await tab.goto(tab.base + "#c=" + shared["challenge"])
    await tab.wait("BANK.length > 0")
    await tab.wait(VISIBLE % "ciPlay")
    await tab.shot("41-challenge-invite")
    await tab.js("document.getElementById('ciPlay').click()")
    await play(tab, [True, True, False, True, True, True, False, True, True, True], snap_first="42-challenge")
    await tab.wait(VISIBLE % "vs")
    await tab.wait("!!chal && !!chal.result", 10)
    await tab.shot("43-challenge-vs")
    await tab.goto(tab.base + "#j=" + shared["league"])
    await tab.wait(VISIBLE % "lgInvite")
    await tab.shot("44-league-invite")
    await tab.js("document.getElementById('lgInviteJoin').click()")
    await tab.wait(VISIBLE % "league")
    await tab.wait("document.querySelectorAll('#lTable li').length >= 2")
    await tab.shot("45-league-table")
    await tab.js("document.querySelectorAll('#lPeriod .chip')[1].click()")
    await tab.wait("document.querySelectorAll('#lTable li').length >= 2")
    await tab.shot("46-league-alltime")


async def run(scheme, name, taken_name=None, shared=None, prof=None):
    keep = prof is not None
    prof = prof or tempfile.mkdtemp(prefix="fq-chrome-")
    port = PORT + (1 if scheme == "dark" else 0)
    proc = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=%d" % port, "--user-data-dir=" + prof,
                             "--no-sandbox", "--no-first-run", "--hide-scrollbars", "--disable-gpu", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen("http://127.0.0.1:%d/json" % port))
                ws_url = [t for t in tabs if t["type"] == "page"][0]["webSocketDebuggerUrl"]
                break
            except Exception:
                time.sleep(0.2)
        async with websockets.connect(ws_url, max_size=100_000_000) as ws:
            tab = Tab(ws, scheme)
            tab.base = URL
            tab.port = port
            reader = asyncio.ensure_future(tab.reader())
            for m in ("Runtime.enable", "Log.enable", "Page.enable", "Network.enable"):
                await tab.send(m)
            # each player gets its own 20-posts-per-10-minutes budget on the test API (preview.py
            # passes this on as the client IP; everything else here comes from 127.0.0.1)
            await tab.send("Network.setExtraHTTPHeaders", {"headers": {
                "X-Preview-Client": "10.97.0.%d" % (int(time.time()) % 200 + (1 if scheme == "light" else 2))}})
            await tab.send("Page.addScriptToEvaluateOnNewDocument", {"source": FAKE_PUSH_JS % json.dumps(shared["sub_" + scheme])})
            await tab.send("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True})
            await tab.send("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": scheme}]})
            await tab.goto(tab.base)
            await tab.wait("BANK.length > 0 && document.fonts.status === 'loaded'")
            if shared.get("creator_view"):     # reopen the light profile: the sender's view of their challenge
                await tab.goto(tab.base + "#c=" + shared["challenge"])
                await tab.wait("!!chal && chal.mine && chal.plays.length > 0", 10)
                await tab.shot("47-challenge-sender-view")
                await tab.js("document.getElementById('vsHome').click()")
                await tab.wait("!document.getElementById('divStrip').classList.contains('hidden')")
                await tab.shot("48-home-strip")
                reader.cancel()
                return tab.errors
            fonts = await tab.js("Array.from(document.fonts).filter(function(f){return f.status==='loaded'}).map(function(f){return f.family+' '+f.weight})")
            print(scheme, "fonts loaded:", fonts)
            await tab.shot("01-home")
            if scheme == "dark":    # before having a name: hub + the name sheet
                await tab.js("document.querySelector('[data-mode=challenge]').click()")
                await tab.wait(LOADED % "h2hMsg")
                await tab.shot("35-challenge-hub-empty")
                await tab.js("document.querySelector('[data-tab=leaguesTab]').click()")
                await tab.wait(LOADED % "lgMsg")
                await tab.shot("36-leagues-noname")
                await tab.js("document.getElementById('lgName').value='Nameless';document.getElementById('lgCreate').click()")
                await tab.wait(VISIBLE % "nameSheet")
                await tab.shot("37-name-sheet", full=False)
                await tab.js("document.getElementById('nsCancel').click()")
                await tab.js("document.querySelector('[data-tab=home]').click()")

            # Classic: 10 questions, right/wrong mix, first answers captured
            await tab.js("document.querySelector('.mode.active').click()")
            await tab.shot("02-classic-setup")
            if scheme == "dark":    # timer on in dark run, to see the clock
                await tab.js("[].slice.call(document.querySelectorAll('#timers .chip')).filter(function(b){return b.textContent==='20 s'})[0].click()")
            await tab.js("document.getElementById('start').click()")
            await play(tab, [False, True, True, False, True, True, True, False, True, True], snap_first="03-classic", fifty_at=2)
            await tab.wait("!document.getElementById('done').classList.contains('hidden')")
            await tab.shot("04-classic-result-askname")
            assert await tab.js("document.getElementById('pushAsk').classList.contains('hidden')"), "push offer after classic"
            if taken_name:      # name already claimed by the light run -> must be refused
                await tab.js("document.getElementById('nameIn').value=%s;document.getElementById('postBtn').click()" % json.dumps(taken_name))
                await tab.wait("/taken/.test(document.getElementById('postMsg').textContent)")
                await tab.shot("05-name-taken")
            await tab.js("document.getElementById('nameIn').value=%s;document.getElementById('postBtn').click()" % json.dumps(name))
            await tab.wait("/Posted as/.test(document.getElementById('postMsg').textContent)", 10)
            await tab.shot("06-classic-posted")
            if scheme == "light":
                await social_maker(tab, shared)
            else:
                await social_player(tab, shared)

            # Daily
            await tab.js("document.getElementById('menu').click()")
            await tab.js("document.getElementById('dPlay').click()")
            await play(tab, [True, True, False, True, True, False, True], snap_first="07-daily", report_at=2)
            await tab.wait("/Posted as/.test(document.getElementById('postMsg').textContent)", 10)
            await tab.shot("08-daily-result")
            await push_flow(tab, scheme, name, shared)
            await tab.js("document.getElementById('menu').click()")
            await tab.shot("09-home-daily-done")
            await new_modes(tab)

            # Boards
            await tab.js("document.querySelector('[data-tab=boards]').click()")
            await tab.wait("document.getElementById('bMsg').textContent !== 'Loading…'")
            await tab.shot("10-board-daily-today")
            await tab.js("document.querySelectorAll('#bPeriod .chip')[1].click()")
            await tab.wait("document.getElementById('bMsg').textContent !== 'Loading…'")
            await tab.shot("11-board-daily-week")
            await tab.js("document.querySelectorAll('#bKind button')[1].click()")
            await tab.wait("document.getElementById('bMsg').textContent !== 'Loading…'")
            await tab.shot("12-board-classic")
            for k, kind in enumerate(("survival", "blitz", "hilo", "career")):
                await tab.js("[].slice.call(document.querySelectorAll('#bKind button'))[%d].click()" % (k + 2))
                await tab.wait("document.getElementById('bMsg').textContent !== 'Loading…'")
                await tab.shot("12%s-board-%s" % ("bcde"[k], kind))
            await tab.js("document.querySelector('[data-tab=leaguesTab]').click()")
            await tab.wait(LOADED % "lgMsg")
            await tab.wait("document.querySelectorAll('#dvTable li').length > 0")
            await tab.shot("13-leagues")
            await tab.js("document.querySelector('[data-tab=home]').click()")
            await tab.wait(VISIBLE % "divStrip")
            await tab.shot("13b-home-strip", full=False)
            await tab.js("document.querySelector('[data-mode=challenge]').click()")
            await tab.wait(LOADED % "h2hMsg")
            await tab.shot("13c-challenge-hub")
            await tab.js("document.querySelector('[data-tab=me]').click()")
            await tab.shot("14-me")
            await achievements_and_feel(tab)
            await push_me(tab, scheme)
            await tab.goto(tab.base + "privacy.html")
            await tab.shot("15-privacy")
            # Reload the app: daily must still say done (one attempt) and SW must be active
            await tab.goto(tab.base)
            await tab.wait("BANK.length > 0")
            sw = await tab.js("navigator.serviceWorker.getRegistration().then(function(r){return !!(r && r.active)})")
            done = await tab.js("!document.getElementById('dDone').classList.contains('hidden')")
            print(scheme, "service worker active:", sw, "| daily shows done after reload:", done)
            reader.cancel()
            return tab.errors
    finally:
        proc.terminate()
        proc.wait()
        if not keep:
            shutil.rmtree(prof, ignore_errors=True)


async def main(which):
    os.makedirs(OUT, exist_ok=True)
    errs, tag = {}, time.strftime("%H%M%S")      # fresh browser = fresh player id, so fresh names
    shared = {"sub_light": fake_sub(), "sub_dark": fake_sub()}
    srv = ThreadingHTTPServer(("127.0.0.1", PUSH_PORT), FakePush)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    light_prof = tempfile.mkdtemp(prefix="fq-chrome-")
    try:
        # light sends a challenge + makes a league; dark (another player) plays it and joins;
        # then the light profile reopens to see the sender's side.
        errs["light"] = await run("light", "Light " + tag, shared=shared, prof=light_prof)
        if which == "both":
            errs["dark"] = await run("dark", "Dark " + tag, taken_name="light " + tag, shared=shared)
            shared["creator_view"] = True
            errs["light-sender"] = await run("light", "Light " + tag, shared=shared, prof=light_prof)
    finally:
        shutil.rmtree(light_prof, ignore_errors=True)
    srv.shutdown()
    print("fake push service received:", PUSHED)
    if which == "both":     # the push_send test + "Dark beat your challenge" (sent when dark played it)
        mine = [x for x in PUSHED if x[0] == "/" + shared["sub_light"]["endpoint"].split("/", 3)[3]]
        print("pushes to the light player:", len(mine), "(expect 2)")
    for k, v in errs.items():
        print(k, "console errors/warnings:", v or "none")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "both"))
