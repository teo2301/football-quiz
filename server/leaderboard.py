#!/usr/bin/env python3
"""Leaderboard API for the Football Quiz (standard library only).

The quiz itself is static (GitHub Pages); this tiny service stores scores so
everyone sees the same board. It runs in the `football-quiz-scores` container
on 127.0.0.1:8093 and is reached publicly through Tailscale Funnel on :10000.

    POST /api/scores   {name, total, right: {"1": n, "2": n, "3": n}, league, diff, timer, streak}
    GET  /api/board    ?len=10|20|50 &period=week|all   -> best game per player, top 25
    GET  /api/health

Points are computed HERE, not trusted from the browser: 1/2/3 per correct
Easy/Medium/Hard answer, x1.25 with the 20 s timer, x1.5 with 10 s. Anyone can
still post a made-up result (there are no accounts) - this is a casual board
for friends; the checks just keep the data sane and stop flooding.
"""

import json
import os
import re
import sqlite3
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB = os.environ.get("QUIZ_DB", "/data/leaderboard.db")
ORIGINS = {o.strip() for o in os.environ.get(
    "QUIZ_ORIGINS",
    "https://teo2301.github.io,https://riccioserver.tail0d3eb6.ts.net:8445,http://127.0.0.1:8097,http://localhost:8097",
).split(",") if o.strip()}
LENGTHS = {10, 20, 50}
LEAGUES = {"all", "pl", "ll", "sa", "eu", "in"}
TIMER_MULT = {0: 1.0, 20: 1.25, 10: 1.5}
NAME_RE = re.compile(r"^[\w .'\-]{2,16}$", re.UNICODE)
RATE = (20, 600)            # at most 20 posts per IP per 10 minutes
MAX_BODY = 2048

_hits = defaultdict(deque)
_lock = threading.Lock()


def db():
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init():
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


def clean_name(raw):
    name = " ".join(str(raw or "").split())
    if not NAME_RE.match(name):
        raise ValueError("Name must be 2-16 letters, numbers, spaces or . ' -")
    return name


def as_int(v, lo, hi, field):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"bad {field}")
    return v


def add_score(data):
    name = clean_name(data.get("name"))
    total = data.get("total")
    if total not in LENGTHS:
        raise ValueError("bad total")
    right = data.get("right") or {}
    if not isinstance(right, dict):
        raise ValueError("bad right")
    by = {d: as_int(right.get(str(d), 0), 0, total, "right") for d in (1, 2, 3)}
    correct = sum(by.values())
    if correct > total:
        raise ValueError("more right answers than questions")
    timer = as_int(data.get("timer", 0), 0, 20, "timer")
    if timer not in TIMER_MULT:
        raise ValueError("bad timer")
    league = data.get("league")
    if league not in LEAGUES:
        raise ValueError("bad league")
    diff = as_int(data.get("diff", 0), 0, 3, "diff")
    streak = as_int(data.get("streak", 0), 0, total, "streak")
    if streak > correct:
        raise ValueError("bad streak")
    points = round((by[1] + 2 * by[2] + 3 * by[3]) * TIMER_MULT[timer], 1)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db() as c:
        c.execute("INSERT INTO scores (name, points, correct, total, league, diff, timer, streak, created_utc)"
                  " VALUES (?,?,?,?,?,?,?,?,?)",
                  (name, points, correct, total, league, diff, timer, streak, now))
    return {"ok": True, "points": points, "correct": correct,
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


class Handler(BaseHTTPRequestHandler):
    server_version = "quiz-scores"
    sys_version = ""

    def _cors(self):
        origin = self.headers.get("Origin", "")
        if origin in ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
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
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?")[0] != "/api/scores":
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
            return self._send(200, add_score(data))
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, {"error": str(e)})

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self._ip(), fmt % args))


if __name__ == "__main__":
    init()
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
