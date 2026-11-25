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

CHROME = os.path.expanduser("~/.cache/ms-playwright/chromium-1228/chrome-linux64/chrome")
URL = "http://127.0.0.1:8197/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "preview-shots")
PORT = 9333


class Tab:
    def __init__(self, ws, tag):
        self.ws, self.tag, self.i, self.errors, self.waiting = ws, tag, 0, [], {}

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
            reader = asyncio.ensure_future(tab.reader())
            for m in ("Runtime.enable", "Log.enable", "Page.enable", "Network.enable"):
                await tab.send(m)
            # each player gets its own 20-posts-per-10-minutes budget on the test API (preview.py
            # passes this on as the client IP; everything else here comes from 127.0.0.1)
            await tab.send("Network.setExtraHTTPHeaders", {"headers": {
                "X-Preview-Client": "10.97.0.%d" % (int(time.time()) % 200 + (1 if scheme == "light" else 2))}})
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
    shared = {}
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
    for k, v in errs.items():
        print(k, "console errors/warnings:", v or "none")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "both"))
