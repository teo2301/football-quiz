#!/usr/bin/env python3
"""Leaderboard API for the Football Quiz (standard library only).

The quiz itself is static (GitHub Pages); this tiny service stores scores so
everyone sees the same board. It runs in the `football-quiz-scores` container
on 127.0.0.1:8093 and is reached publicly through Tailscale Funnel on :10000.

    POST /api/scores   classic quiz. New shape:
                       {name, player_id, total, league, diff, timer, answers: [{id, choice}]}
                       Old shape (still accepted): {name, total, right: {"1": n, ...}, league, diff, timer, streak}
    GET  /api/board    ?len=10|20|50 &period=week|all   -> best game per player, top 25
    POST /api/name     {name, player_id}                 -> claim / rename
    GET  /api/daily    ?date=YYYY-MM-DD                  -> {date, number, ids}
    POST /api/daily    {name, player_id, date, answers}  -> one attempt per player per day
    GET  /api/daily/board ?period=today|week
    GET  /api/health

Points are computed HERE: 1/2/3 per correct Easy/Medium/Hard answer, x1.25
with the 20 s timer, x1.5 with 10 s. With the new shape the server marks every
answer itself against questions.json, so made-up points are ignored. A name
belongs to the first player_id (a random id the app keeps in localStorage)
that uses it; nobody else can post under it.
"""

import json
import os
import re
import sqlite3
import sys
import threading
import time
import unicodedata
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_gen  # noqa: E402

DB = os.environ.get("QUIZ_DB", "/data/leaderboard.db")
QUESTIONS_PATH = os.environ.get("QUESTIONS_PATH") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "docs", "questions.json")
ORIGINS = {o.strip() for o in os.environ.get(
    "QUIZ_ORIGINS",
    "https://teo2301.github.io,https://riccioserver.tail0d3eb6.ts.net:8445,http://127.0.0.1:8097,http://localhost:8097",
).split(",") if o.strip()}
LENGTHS = {10, 20, 50}
LEAGUES = {"all", "pl", "ll", "sa", "eu", "in"}
TIMER_MULT = {0: 1.0, 20: 1.25, 10: 1.5}
NAME_RE = re.compile(r"^[\w .'\-]{2,16}$", re.UNICODE)
PID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
RATE = (20, 600)            # at most 20 posts per IP per 10 minutes
MAX_BODY = 16384
TAKEN = "That name is taken — pick another"

_hits = defaultdict(deque)
_lock = threading.Lock()


class Conflict(ValueError):
    """Becomes HTTP 409."""


# ---------------------------------------------------------------- questions

_bank = {"mtime": None, "by_id": {}, "picker": None}
_bank_lock = threading.Lock()


def bank():
    """questions.json, reloaded when the file changes. Missing file = new-shape posts fail."""
    with _bank_lock:
        try:
            mtime = os.stat(QUESTIONS_PATH).st_mtime
        except OSError:
            raise ValueError("questions unavailable on the server")
        if mtime != _bank["mtime"]:
            with open(QUESTIONS_PATH, encoding="utf-8") as f:
                qs = json.load(f)
            _bank.update(mtime=mtime, by_id={q["id"]: q for q in qs}, picker=daily_gen.DailyPicker(qs))
        return _bank["by_id"], _bank["picker"]


# ---------------------------------------------------------------- database

def db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init():
    # CREATE ... IF NOT EXISTS only: existing tables and rows are never touched.
    with db() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("""CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            points REAL NOT NULL,
            correct INTEGER NOT NULL,
            total INTEGER NOT NULL,
            league TEXT NOT NULL,
            diff INTEGER NOT NULL,
            timer INTEGER NOT NULL,
            streak INTEGER NOT NULL,
            created_utc TEXT NOT NULL)""")
        c.execute("CREATE INDEX IF NOT EXISTS scores_board ON scores(total, created_utc)")
        c.execute("""CREATE TABLE IF NOT EXISTS players (
            player_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            name_lc TEXT NOT NULL UNIQUE,
            created_utc TEXT NOT NULL,
            updated_utc TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS answers (
            id INTEGER PRIMARY KEY,
            player_id TEXT,
            qid TEXT NOT NULL,
            correct INTEGER NOT NULL,
            mode TEXT NOT NULL,
            created_utc TEXT NOT NULL)""")
        c.execute("CREATE INDEX IF NOT EXISTS answers_qid ON answers(qid)")
        c.execute("""CREATE TABLE IF NOT EXISTS daily (
            id INTEGER PRIMARY KEY,
            player_id TEXT NOT NULL,
            date TEXT NOT NULL,
            number INTEGER NOT NULL,
            correct INTEGER NOT NULL,
            total INTEGER NOT NULL,
            points REAL NOT NULL,
            grid TEXT NOT NULL,
            created_utc TEXT NOT NULL,
            UNIQUE(player_id, date))""")
        c.execute("CREATE INDEX IF NOT EXISTS daily_date ON daily(date)")


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def limited(ip):
    now = time.monotonic()
    with _lock:
        q = _hits[ip]
        while q and now - q[0] > RATE[1]:
            q.popleft()
        if len(q) >= RATE[0]:
            return True
        q.append(now)
        if len(_hits) > 5000:               # bound memory
            for k in [k for k, v in _hits.items() if not v][:2500]:
                del _hits[k]
        return False


# ---------------------------------------------------------------- names

# Names are normalised first: lower case, accents stripped, leetspeak undone
# ("5h1t" -> "shit"), and runs of 3+ letters cut to 2 ("fuuuuck" -> "fuuck").
# BAD_SUBSTR is then matched anywhere in the letters-only name; words of 4+
# letters are also tried with every double letter cut to one ("puttanna", "fuuuck").
BAD_SUBSTR = """
fuck fck shit cunt bitch wank twat nigger nigga faggot retard whore slut bollock bastard
hitler pussy dildo paedo
cazzo minchia stronz puttan vaffancul merda coglion frocio porcodio porcamadonna
mierda cabron pendej gilipoll maricon joder hijoput putamadre
""".split()
# Too short / too common inside real names to match anywhere: whole words only.
BAD_WORDS = """ass arse tit tits dick cock fag fags cum rape rapist nazi pedo puta culo cono polla
zorra figa troia verga chinga porco wanker""".split()
# Real names that contain a bad substring.
ALLOW = ["scunthorpe", "penistone", "cockburn", "hitchcock"]
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b",
                      "9": "g", "@": "a", "$": "s", "!": "i", "|": "i", "€": "e", "+": "t"})


def _fold(s):
    s = unicodedata.normalize("NFKD", s.lower().translate(LEET))
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _runs(s, keep):
    return re.sub(r"([a-z])\1{%d,}" % keep, lambda m: m.group(1) * keep, s)


# (minus the ones that would then hit real names: "cazo" in Cazorla, "niger" in Nigeria)
_SUBSTR_1 = [w for w in {_runs(b, 1) for b in BAD_SUBSTR} if len(w) >= 4]
_SUBSTR_1 = [w for w in _SUBSTR_1 if w not in ("cazo", "niger")]


def is_rude(name):
    f = _fold(name)
    for ok in ALLOW:
        f = f.replace(ok, " ")
    words = [_runs(w, 2) for w in re.split(r"[^a-z]+", f) if w]
    if any(w in BAD_WORDS or _runs(w, 1) in BAD_WORDS for w in words):
        return True
    joined = _runs(re.sub(r"[^a-z]", "", f), 2)
    if any(b in joined for b in BAD_SUBSTR):
        return True
    single = _runs(joined, 1)
    return any(b in single for b in _SUBSTR_1)


def clean_name(raw):
    name = " ".join(str(raw or "").split())
    if not NAME_RE.match(name):
        raise ValueError("Name must be 2-16 letters, numbers, spaces or . ' -")
    if is_rude(name):
        raise ValueError("Please pick a different name — that one isn't allowed")
    return name


def clean_pid(raw):
    pid = str(raw or "").lower()
    if not PID_RE.match(pid):
        raise ValueError("bad player_id")
    return pid


def bind_name(c, name, pid):
    """Claim `name` for `pid` (or rename pid). Raises Conflict if someone else owns it."""
    lc = name.lower()
    row = c.execute("SELECT player_id FROM players WHERE name_lc = ?", (lc,)).fetchone()
    now = now_utc()
    if row:
        if row["player_id"] != pid:
            raise Conflict(TAKEN)
        c.execute("UPDATE players SET name = ?, updated_utc = ? WHERE player_id = ?", (name, now, pid))
        return
    if c.execute("SELECT 1 FROM players WHERE player_id = ?", (pid,)).fetchone():
        c.execute("UPDATE players SET name = ?, name_lc = ?, updated_utc = ? WHERE player_id = ?",
                  (name, lc, now, pid))
    else:
        c.execute("INSERT INTO players (player_id, name, name_lc, created_utc, updated_utc) VALUES (?,?,?,?,?)",
                  (pid, name, lc, now, now))


def set_name(data):
    name, pid = clean_name(data.get("name")), clean_pid(data.get("player_id"))
    with db() as c:
        bind_name(c, name, pid)
    return {"ok": True, "name": name}


# ---------------------------------------------------------------- scoring

def as_int(v, lo, hi, field):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"bad {field}")
    return v


def mark(answers, expected_len, check=None):
    """Mark [{id, choice}] against the bank -> (by_difficulty, longest_streak, [(qid, ok)])."""
    if not isinstance(answers, list) or len(answers) != expected_len:
        raise ValueError("bad answers")
    by_id, _ = bank()
    by, run, best, marks, seen = {1: 0, 2: 0, 3: 0}, 0, 0, [], set()
    for a in answers:
        if not isinstance(a, dict):
            raise ValueError("bad answers")
        qid, choice = a.get("id"), a.get("choice")
        q = by_id.get(qid) if isinstance(qid, str) else None
        if q is None or qid in seen:
            raise ValueError("unknown or repeated question - reload the app")
        if choice is not None and not isinstance(choice, str):
            raise ValueError("bad answers")
        if check:
            check(q)
        seen.add(qid)
        ok = choice == q["a"]
        if ok:
            by[q["d"]] += 1
            run += 1
            best = max(best, run)
        else:
            run = 0
        marks.append((qid, ok))
    return by, best, marks


def log_answers(c, pid, marks, mode, now):
    c.executemany("INSERT INTO answers (player_id, qid, correct, mode, created_utc) VALUES (?,?,?,?,?)",
                  [(pid, q, int(ok), mode, now) for q, ok in marks])


def add_score(data):
    name = clean_name(data.get("name"))
    pid = clean_pid(data["player_id"]) if data.get("player_id") is not None else None
    total = data.get("total")
    if total not in LENGTHS:
        raise ValueError("bad total")
    timer = as_int(data.get("timer", 0), 0, 20, "timer")
    if timer not in TIMER_MULT:
        raise ValueError("bad timer")
    league = data.get("league")
    if league not in LEAGUES:
        raise ValueError("bad league")
    diff = as_int(data.get("diff", 0), 0, 3, "diff")
    marks = None
    if "answers" in data:
        def check(q):
            if (league != "all" and q["l"] != league) or (diff and q["d"] != diff):
                raise ValueError("question doesn't match the quiz settings")
        by, streak, marks = mark(data["answers"], total, check)
        correct = sum(by.values())
    else:                                       # old clients
        right = data.get("right") or {}
        if not isinstance(right, dict):
            raise ValueError("bad right")
        by = {d: as_int(right.get(str(d), 0), 0, total, "right") for d in (1, 2, 3)}
        correct = sum(by.values())
        if correct > total:
            raise ValueError("more right answers than questions")
        streak = as_int(data.get("streak", 0), 0, total, "streak")
        if streak > correct:
            raise ValueError("bad streak")
    points = round((by[1] + 2 * by[2] + 3 * by[3]) * TIMER_MULT[timer], 1)
    now = now_utc()
    with db() as c:
        if pid:
            bind_name(c, name, pid)
        elif c.execute("SELECT 1 FROM players WHERE name_lc = ?", (name.lower(),)).fetchone():
            raise ValueError(TAKEN)             # old client posting a claimed name (400 = it stops retrying)
        c.execute("INSERT INTO scores (name, points, correct, total, league, diff, timer, streak, created_utc)"
                  " VALUES (?,?,?,?,?,?,?,?,?)",
                  (name, points, correct, total, league, diff, timer, streak, now))
        if marks:
            log_answers(c, pid, marks, "classic", now)
    return {"ok": True, "points": points, "correct": correct, "streak": streak,
            "rank_week": rank(name, total, "week"), "rank_all": rank(name, total, "all")}


def since(period):
    if period == "week":
        return (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return ""


def board(total, period, limit=25):
    # Best game per player (names compared case-insensitively); ties go to
    # whoever got there first.
    sql = """SELECT name, points, correct, total, league, diff, timer, streak, created_utc FROM (
               SELECT *, ROW_NUMBER() OVER (PARTITION BY lower(name)
                                            ORDER BY points DESC, created_utc ASC) AS rn
               FROM scores WHERE total = ? AND created_utc >= ?)
             WHERE rn = 1 ORDER BY points DESC, created_utc ASC LIMIT ?"""
    with db() as c:
        return [dict(r) for r in c.execute(sql, (total, since(period), limit))]


def rank(name, total, period):
    rows = board(total, period, limit=100000)
    for i, r in enumerate(rows, 1):
        if r["name"].lower() == name.lower():
            return i
    return None


# ---------------------------------------------------------------- daily

def parse_date(s):
    try:
        return date.fromisoformat(str(s))
    except ValueError:
        raise ValueError("bad date")


def daily_ids(data_date):
    today = daily_gen.uk_today()
    if not daily_gen.SEQ_START <= data_date <= today + timedelta(days=1):
        raise ValueError("no daily quiz for that date")
    _, picker = bank()
    return {"date": data_date.isoformat(), "number": daily_gen.number(data_date),
            "ids": picker.ids(data_date)}


def streak_for(c, pid, upto):
    dates = {r[0] for r in c.execute(
        "SELECT date FROM daily WHERE player_id = ? AND date <= ? ORDER BY date DESC LIMIT 400",
        (pid, upto.isoformat()))}
    n, d = 0, upto
    while d.isoformat() in dates:
        n += 1
        d -= timedelta(days=1)
    return n


def add_daily(data):
    name = clean_name(data.get("name"))
    pid = clean_pid(data.get("player_id"))
    d = parse_date(data.get("date"))
    today = daily_gen.uk_today()
    if d not in (today, today - timedelta(days=1)):
        raise ValueError("That daily quiz has closed")
    _, picker = bank()
    ids = picker.ids(d)
    answers = data.get("answers")
    if not isinstance(answers, list) or [a.get("id") if isinstance(a, dict) else None for a in answers] != ids:
        raise ValueError("Today's questions have changed - reload the app")
    by, _, marks = mark(answers, len(ids))
    correct = sum(by.values())
    points = by[1] + 2 * by[2] + 3 * by[3]
    grid = "".join("1" if ok else "0" for _, ok in marks)
    now = now_utc()
    with db() as c:
        bind_name(c, name, pid)
        try:
            c.execute("INSERT INTO daily (player_id, date, number, correct, total, points, grid, created_utc)"
                      " VALUES (?,?,?,?,?,?,?,?)",
                      (pid, d.isoformat(), daily_gen.number(d), correct, len(ids), points, grid, now))
        except sqlite3.IntegrityError:
            raise Conflict("You've already played this daily quiz - come back tomorrow")
        log_answers(c, pid, marks, "daily", now)
        streak = streak_for(c, pid, d)
    rows = daily_board("today", d)
    pos = next((i for i, r in enumerate(rows, 1) if r["player_id"] == pid), None)
    return {"ok": True, "date": d.isoformat(), "number": daily_gen.number(d), "correct": correct,
            "total": len(ids), "points": points, "grid": grid, "streak": streak, "rank_today": pos,
            "players_today": len(rows)}


def daily_board(period, d=None, limit=None):
    d = d or daily_gen.uk_today()
    with db() as c:
        if period == "today":
            rows = [dict(r) for r in c.execute(
                """SELECT d.player_id, p.name, d.correct, d.total, d.points, d.grid FROM daily d
                   JOIN players p ON p.player_id = d.player_id WHERE d.date = ?
                   ORDER BY d.correct DESC, d.points DESC, d.created_utc ASC""", (d.isoformat(),))]
        else:
            start = (d - timedelta(days=6)).isoformat()
            rows = [dict(r) for r in c.execute(
                """SELECT d.player_id, p.name, SUM(d.correct) AS correct, SUM(d.total) AS total,
                          SUM(d.points) AS points, COUNT(*) AS days FROM daily d
                   JOIN players p ON p.player_id = d.player_id WHERE d.date BETWEEN ? AND ?
                   GROUP BY d.player_id ORDER BY correct DESC, points DESC, MIN(d.created_utc) ASC""",
                (start, d.isoformat()))]
        rows = rows[:limit] if limit else rows
        for r in rows:
            r["streak"] = streak_for(c, r["player_id"], d)
    return rows


def public_board(period):
    rows = daily_board(period, limit=50)
    for r in rows:
        del r["player_id"]                  # it's what lets you post as yourself
    today = daily_gen.uk_today()
    return {"date": today.isoformat(), "number": daily_gen.number(today), "rows": rows}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "quiz-scores"
    sys_version = ""

    def _cors(self):
        origin = self.headers.get("Origin", "")
        if origin in ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _ip(self):
        # Only tailscaled can reach us (bound to 127.0.0.1), and it sets XFF.
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        try:
            if path == "/api/health":
                return self._send(200, {"ok": True})
            if path == "/api/board":
                try:
                    total = int(params.get("len", "10"))
                except ValueError:
                    total = 0
                period = params.get("period", "week")
                if total not in LENGTHS or period not in ("week", "all"):
                    return self._send(400, {"error": "bad len or period"})
                return self._send(200, {"rows": board(total, period)})
            if path == "/api/daily":
                d = parse_date(params["date"]) if "date" in params else daily_gen.uk_today()
                return self._send(200, daily_ids(d))
            if path == "/api/daily/board":
                period = params.get("period", "today")
                if period not in ("today", "week"):
                    return self._send(400, {"error": "bad period"})
                return self._send(200, public_board(period))
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        route = {"/api/scores": add_score, "/api/daily": add_daily, "/api/name": set_name}.get(self.path.split("?")[0])
        if not route:
            return self._send(404, {"error": "not found"})
        if limited(self._ip()):
            return self._send(429, {"error": "Too many scores - try again in a few minutes."})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_BODY:
                raise ValueError("bad body")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("bad body")
            return self._send(200, route(data))
        except Conflict as e:
            return self._send(409, {"error": str(e)})
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, {"error": str(e)})

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self._ip(), fmt % args))


if __name__ == "__main__":
    init()
    host = os.environ.get("QUIZ_HOST", "0.0.0.0")
    port = int(os.environ.get("QUIZ_PORT", "8000"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()
