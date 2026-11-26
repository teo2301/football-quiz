#!/usr/bin/env python3
"""Leaderboard API for the Football Quiz (standard library only).

The quiz itself is static (GitHub Pages); this tiny service stores scores so
everyone sees the same board. It runs in the `football-quiz-scores` container
on 127.0.0.1:8093 and is reached publicly through Tailscale Funnel on :10000.

    POST /api/scores   classic quiz. New shape:
                       {name, player_id, total, league, diff, timer, answers: [{id, choice, fifty?}]}
                       Old shape (still accepted): {name, total, right: {"1": n, ...}, league, diff, timer, streak}
                       Other modes: {mode: survival|blitz|hilo|career, name, player_id, answers}
                       (see modes.py for each mode's answers)
    GET  /api/board    ?len=10|20|50 &period=week|all   -> best game per player, top 25
                       ?mode=survival|blitz|hilo|career &period=week|all
    POST /api/name     {name, player_id}                 -> claim / rename
    GET  /api/daily    ?date=YYYY-MM-DD                  -> {date, number, ids}
    POST /api/daily    {name, player_id, date, answers}  -> one attempt per player per day
    GET  /api/daily/board ?period=today|week
    GET  /api/health

  Phase 3 (social):
    POST /api/challenges        {name, player_id, league, diff, timer, answers} -> {code}  (a finished classic quiz)
    GET  /api/challenge         ?code= &player_id=   -> questions, their score, plays; your side-by-side once played
    POST /api/challenge/play    {code, name, player_id, answers}  -> one go per player, same questions in order
    GET  /api/h2h               ?player_id=          -> head-to-head record against everyone you've challenged
    POST /api/leagues           {name, player_id, league_name}   -> {code} (6 characters, no I/O/0/1)
    POST /api/leagues/join      {name, player_id, code}
    POST /api/leagues/leave     {player_id, code}
    GET  /api/leagues           ?player_id=          -> your leagues
    GET  /api/league            ?code= &player_id= &period=week|all -> league table (points from every mode)
    GET  /api/division          ?player_id=          -> your weekly division table (promotion / relegation)

  Phase 5 (question quality):
    POST /api/report            {player_id, id, reason: wrong|outdated|typo|other, note?}  -> {ok}
                                one report per player per question (a second one updates it); own rate limit

  Phase 6 (push notifications, see push.py):
    GET    /api/push/key        -> {key} the VAPID public key (503 if push isn't set up on the server)
    POST   /api/push            {player_id, subscription: PushSubscription JSON, types: [daily|challenge|division]}
                                -> stores/updates it (keyed by endpoint); own rate limit
    DELETE /api/push            {endpoint}  -> forget it (also done when the push service says 404/410)

  Phase 4 (achievements):
    POST /api/badges            {player_id, badges: [id, ...]}  -> badges are earned in the app; the server
                                keeps the union and boards/tables show each player's best three as `badges`

Points are computed HERE: 1/2/3 per correct Easy/Medium/Hard answer, x1.25
with the 20 s timer, x1.5 with 10 s; the one question you used the 50/50 on
scores half. With the new shape the server marks every
answer itself against questions.json, so made-up points are ignored. A name
belongs to the first player_id (a random id the app keeps in localStorage)
that uses it; nobody else can post under it.
"""

import json
import os
import random
import re
import sqlite3
import sys
import threading
import time
import unicodedata
import urllib.parse
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import achievements  # noqa: E402
import daily_gen  # noqa: E402
import divisions  # noqa: E402
import modes  # noqa: E402
import push  # noqa: E402

DB = os.environ.get("QUIZ_DB", "/data/leaderboard.db")
QUESTIONS_PATH = os.environ.get("QUESTIONS_PATH") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "docs", "questions.json")
DATA_DIR = os.path.dirname(os.path.abspath(QUESTIONS_PATH))
STATS_PATH = os.environ.get("STATS_PATH") or os.path.join(DATA_DIR, "stats.json")
CAREERS_PATH = os.environ.get("CAREERS_PATH") or os.path.join(DATA_DIR, "careers.json")
ORIGINS = {o.strip() for o in os.environ.get(
    "QUIZ_ORIGINS",
    "https://teo2301.github.io,https://riccioserver.tail0d3eb6.ts.net:8445,http://127.0.0.1:8097,http://localhost:8097",
).split(",") if o.strip()}
LENGTHS = {10, 20, 50}
LEAGUES = {"all", "pl", "ll", "sa", "eu", "in"}
RUN_MODES = {"survival", "blitz", "hilo", "career"}
TIMER_MULT = {0: 1.0, 20: 1.25, 10: 1.5}
NAME_RE = re.compile(r"^[\w .'\-]{2,16}$", re.UNICODE)
PID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
RATE = (20, 600)            # at most 20 posts per IP per 10 minutes
MAX_BODY = 65536           # a long survival run is ~60 bytes per answer
TAKEN = "That name is taken — pick another"

_hits = defaultdict(deque)
_lock = threading.Lock()


class Conflict(ValueError):
    """Becomes HTTP 409."""


class NotFound(Exception):
    """Becomes HTTP 404 (unknown challenge or league code)."""


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
        c.execute("""CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY,
            player_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            score REAL NOT NULL,
            correct INTEGER NOT NULL,
            total INTEGER NOT NULL,
            created_utc TEXT NOT NULL)""")
        c.execute("CREATE INDEX IF NOT EXISTS runs_board ON runs(mode, created_utc)")
        # Phase 3: every post's points (all modes) for leagues and divisions; week = UK Monday.
        c.execute("""CREATE TABLE IF NOT EXISTS points_log (
            id INTEGER PRIMARY KEY,
            player_id TEXT NOT NULL,
            mode TEXT NOT NULL,
            points REAL NOT NULL,
            week TEXT NOT NULL,
            created_utc TEXT NOT NULL)""")
        c.execute("CREATE INDEX IF NOT EXISTS points_week ON points_log(week, player_id)")
        c.execute("CREATE INDEX IF NOT EXISTS points_player ON points_log(player_id)")
        c.execute("""CREATE TABLE IF NOT EXISTS challenges (
            code TEXT PRIMARY KEY,
            creator_pid TEXT NOT NULL,
            ids TEXT NOT NULL,
            choices TEXT NOT NULL,
            fifty INTEGER NOT NULL,
            league TEXT NOT NULL,
            diff INTEGER NOT NULL,
            timer INTEGER NOT NULL,
            correct INTEGER NOT NULL,
            points REAL NOT NULL,
            grid TEXT NOT NULL,
            created_utc TEXT NOT NULL)""")
        c.execute("CREATE INDEX IF NOT EXISTS challenges_creator ON challenges(creator_pid)")
        c.execute("""CREATE TABLE IF NOT EXISTS challenge_plays (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL,
            player_id TEXT NOT NULL,
            choices TEXT NOT NULL,
            correct INTEGER NOT NULL,
            points REAL NOT NULL,
            grid TEXT NOT NULL,
            created_utc TEXT NOT NULL,
            UNIQUE(code, player_id))""")
        c.execute("CREATE INDEX IF NOT EXISTS challenge_plays_player ON challenge_plays(player_id)")
        c.execute("""CREATE TABLE IF NOT EXISTS leagues (
            id INTEGER PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            owner_pid TEXT NOT NULL,
            created_utc TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS league_members (
            league_id INTEGER NOT NULL,
            player_id TEXT NOT NULL,
            joined_utc TEXT NOT NULL,
            PRIMARY KEY(league_id, player_id))""")
        c.execute("CREATE INDEX IF NOT EXISTS league_members_player ON league_members(player_id)")
        c.execute("""CREATE TABLE IF NOT EXISTS divisions (
            week TEXT NOT NULL,
            player_id TEXT NOT NULL,
            tier INTEGER NOT NULL,
            grp INTEGER NOT NULL,
            joined_utc TEXT NOT NULL,
            moved INTEGER,
            PRIMARY KEY(week, player_id))""")
        c.execute("CREATE INDEX IF NOT EXISTS divisions_table ON divisions(week, tier, grp)")
        c.execute("""CREATE TABLE IF NOT EXISTS player_tier (
            player_id TEXT PRIMARY KEY,
            tier INTEGER NOT NULL,
            updated_utc TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS player_badges (
            player_id TEXT PRIMARY KEY,
            badges TEXT NOT NULL,
            updated_utc TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS division_weeks (
            week TEXT PRIMARY KEY,
            settled_utc TEXT NOT NULL)""")
        # Phase 5: players flag a question (wrong answer / outdated / typo). One open
        # report per player per question; question_stats.py lists them, nothing is auto-edited.
        c.execute("""CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY,
            player_id TEXT NOT NULL,
            qid TEXT NOT NULL,
            reason TEXT NOT NULL,
            note TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_utc TEXT NOT NULL,
            UNIQUE(player_id, qid))""")
        c.execute("CREATE INDEX IF NOT EXISTS reports_status ON reports(status, qid)")
        # Phase 6: Web Push subscriptions (one row per browser) + what's been sent (no double sends).
        c.execute("""CREATE TABLE IF NOT EXISTS push_subs (
            endpoint TEXT PRIMARY KEY,
            player_id TEXT NOT NULL,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            types TEXT NOT NULL,
            created_utc TEXT NOT NULL,
            updated_utc TEXT NOT NULL)""")
        c.execute("CREATE INDEX IF NOT EXISTS push_subs_player ON push_subs(player_id)")
        c.execute("""CREATE TABLE IF NOT EXISTS push_log (
            kind TEXT NOT NULL,
            ref TEXT NOT NULL,
            player_id TEXT NOT NULL,
            sent_utc TEXT NOT NULL,
            PRIMARY KEY (kind, ref, player_id))""")


def clock():
    """Now, in UTC. Tests replace this to fake the date (division rollover)."""
    return datetime.now(timezone.utc)


def now_utc():
    return clock().strftime("%Y-%m-%dT%H:%M:%SZ")


def limited(ip, rate=RATE, kind=""):
    now = time.monotonic()
    with _lock:
        q = _hits[kind + ip]
        while q and now - q[0] > rate[1]:
            q.popleft()
        if len(q) >= rate[0]:
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


def score_classic(answers, total, league, diff, timer):
    """Mark a classic quiz -> (points, correct, longest_streak, marks, fifty_index or -1)."""
    def check(q):
        if (league != "all" and q["l"] != league) or (diff and q["d"] != diff):
            raise ValueError("question doesn't match the quiz settings")
    by, streak, marks = mark(answers, total, check)
    used = [i for i, a in enumerate(answers) if a.get("fifty")]
    if len(used) > 1:
        raise ValueError("the 50/50 can only be used once per quiz")
    fifty = 0.0
    if used and marks[used[0]][1]:
        fifty = bank()[0][marks[used[0]][0]]["d"] / 2      # that question scores half
    points = round((by[1] + 2 * by[2] + 3 * by[3] - fifty) * TIMER_MULT[timer], 1)
    return points, sum(by.values()), streak, marks, (used[0] if used else -1)


def add_score(data):
    mode = data.get("mode", "classic")
    if mode in RUN_MODES:
        return add_run(data, mode)
    if mode != "classic":
        raise ValueError("bad mode")
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
        points, correct, streak, marks, _ = score_classic(data["answers"], total, league, diff, timer)
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
    settle_weeks()
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
        if pid:
            log_points(c, pid, "classic", points, now)
    return {"ok": True, "points": points, "correct": correct, "streak": streak,
            "rank_week": rank(name, total, "week"), "rank_all": rank(name, total, "all")}


def since(period):
    if period == "week":
        return (clock() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
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


# ---------------------------------------------------------------- other modes

def add_run(data, mode):
    """Survival / blitz / higher or lower / career path: marked here, best run per player on the board."""
    name = clean_name(data.get("name"))
    pid = clean_pid(data.get("player_id"))
    answers = data.get("answers")
    if mode in ("survival", "blitz"):
        hi = modes.SURVIVAL_MAX if mode == "survival" else modes.BLITZ_MAX
        modes.as_list(answers, 1, hi)
        _, _, marks = mark(answers, len(answers))
        score, correct, total = (modes.survival if mode == "survival" else modes.blitz)(marks)
        qmarks = marks
    elif mode == "hilo":
        score, correct, total, qmarks = modes.hilo(answers, STATS_PATH)
    else:
        score, correct, total, qmarks = modes.career(answers, CAREERS_PATH)
    settle_weeks()
    now = now_utc()
    with db() as c:
        bind_name(c, name, pid)
        c.execute("INSERT INTO runs (player_id, mode, score, correct, total, created_utc) VALUES (?,?,?,?,?,?)",
                  (pid, mode, score, correct, total, now))
        log_answers(c, pid, qmarks, mode, now)
        log_points(c, pid, mode, score, now)
    return {"ok": True, "mode": mode, "score": score, "correct": correct, "total": total,
            "rank_week": run_rank(pid, mode, "week"), "rank_all": run_rank(pid, mode, "all")}


def run_board(mode, period, limit=25):
    sql = """SELECT player_id, name, score, correct, total, created_utc FROM (
               SELECT r.*, p.name, ROW_NUMBER() OVER (PARTITION BY r.player_id
                                                      ORDER BY r.score DESC, r.created_utc ASC) AS rn
               FROM runs r JOIN players p ON p.player_id = r.player_id
               WHERE r.mode = ? AND r.created_utc >= ?)
             WHERE rn = 1 ORDER BY score DESC, created_utc ASC LIMIT ?"""
    with db() as c:
        return [dict(r) for r in c.execute(sql, (mode, since(period), limit))]


def run_rank(pid, mode, period):
    for i, r in enumerate(run_board(mode, period, limit=100000), 1):
        if r["player_id"] == pid:
            return i
    return None


def public_run_board(mode, period):
    rows = with_badges(run_board(mode, period))
    for r in rows:
        del r["player_id"]
    return {"mode": mode, "rows": rows}


# ---------------------------------------------------------------- daily

def parse_date(s):
    try:
        return date.fromisoformat(str(s))
    except ValueError:
        raise ValueError("bad date")


def daily_ids(data_date):
    today = daily_gen.uk_today(clock())
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
    today = daily_gen.uk_today(clock())
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
    settle_weeks()
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
        log_points(c, pid, "daily", points, now)
        streak = streak_for(c, pid, d)
    rows = daily_board("today", d)
    pos = next((i for i, r in enumerate(rows, 1) if r["player_id"] == pid), None)
    return {"ok": True, "date": d.isoformat(), "number": daily_gen.number(d), "correct": correct,
            "total": len(ids), "points": points, "grid": grid, "streak": streak, "rank_today": pos,
            "players_today": len(rows)}


def daily_board(period, d=None, limit=None):
    d = d or daily_gen.uk_today(clock())
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
    rows = with_badges(daily_board(period, limit=50))
    for r in rows:
        del r["player_id"]                  # it's what lets you post as yourself
    today = daily_gen.uk_today(clock())
    return {"date": today.isoformat(), "number": daily_gen.number(today), "rows": rows}


# ---------------------------------------------------------------- weekly points + divisions

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no I/O/0/1: easy to read out and type
CODE_RE = re.compile(r"^[A-HJ-NP-Z2-9]{6}$")
CREATE_RATE = (10, 3600)    # new challenges + leagues: at most 10 per IP per hour
_rng = random.SystemRandom()
_div_lock = threading.Lock()
_settled = {"week": None}

TABLE_SQL = """SELECT d.player_id, COALESCE(p.name, '?') AS name, COALESCE(SUM(l.points), 0) AS points
               FROM divisions d
               LEFT JOIN players p ON p.player_id = d.player_id
               LEFT JOIN points_log l ON l.player_id = d.player_id AND l.week = d.week
               WHERE d.week = ? AND d.tier = ? AND d.grp = ?
               GROUP BY d.player_id ORDER BY points DESC, d.joined_utc ASC"""


def log_points(c, pid, mode, points, now):
    """Record a post's points for leagues/divisions; the first points of a week put
    the player in a table in their tier (call settle_weeks() before the transaction)."""
    if not points or points <= 0:
        return
    week = divisions.week_key(clock())
    c.execute("INSERT INTO points_log (player_id, mode, points, week, created_utc) VALUES (?,?,?,?,?)",
              (pid, mode, points, week, now))
    if c.execute("SELECT 1 FROM divisions WHERE week = ? AND player_id = ?", (week, pid)).fetchone():
        return
    row = c.execute("SELECT tier FROM player_tier WHERE player_id = ?", (pid,)).fetchone()
    tier = row["tier"] if row else divisions.BOTTOM
    g = c.execute("""SELECT grp, COUNT(*) AS n FROM divisions WHERE week = ? AND tier = ?
                     GROUP BY grp HAVING n < ? ORDER BY grp LIMIT 1""", (week, tier, divisions.GROUP_SIZE)).fetchone()
    grp = g["grp"] if g else c.execute("SELECT COALESCE(MAX(grp), 0) + 1 FROM divisions WHERE week = ? AND tier = ?",
                                        (week, tier)).fetchone()[0]
    c.execute("INSERT INTO divisions (week, player_id, tier, grp, joined_utc) VALUES (?,?,?,?,?)",
              (week, pid, tier, grp, now))


def settle_weeks():
    """Lazy Monday rollover: apply promotion/relegation for every finished week not yet settled.
    No cron: the first request of a new week does it (cheap no-op after that)."""
    week = divisions.week_key(clock())
    if _settled["week"] == week:
        return
    with _div_lock:
        if _settled["week"] == week:
            return
        c = db()
        try:
            c.execute("BEGIN IMMEDIATE")
            weeks = [r[0] for r in c.execute(
                """SELECT DISTINCT week FROM divisions WHERE week < ?
                   AND week NOT IN (SELECT week FROM division_weeks) ORDER BY week""", (week,))]
            now = now_utc()
            for w in weeks:
                for t in c.execute("SELECT DISTINCT tier, grp FROM divisions WHERE week = ?", (w,)).fetchall():
                    order = [r["player_id"] for r in c.execute(TABLE_SQL, (w, t["tier"], t["grp"]))]
                    for pid, new_tier, moved in divisions.settle(order, t["tier"]):
                        c.execute("UPDATE divisions SET moved = ? WHERE week = ? AND player_id = ?", (moved, w, pid))
                        c.execute("""INSERT INTO player_tier (player_id, tier, updated_utc) VALUES (?,?,?)
                                     ON CONFLICT(player_id) DO UPDATE SET tier = excluded.tier,
                                     updated_utc = excluded.updated_utc""", (pid, new_tier, now))
                c.execute("INSERT INTO division_weeks (week, settled_utc) VALUES (?,?)", (w, now))
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()
        _settled["week"] = week


def division_view(pid):
    """Your table this week (+ last week's result) for the home strip and the Leagues tab."""
    settle_weeks()
    now = clock()
    week = divisions.week_key(now)
    prev = (date.fromisoformat(week) - timedelta(days=7)).isoformat()
    with db() as c:
        me = c.execute("SELECT tier, grp FROM divisions WHERE week = ? AND player_id = ?", (week, pid)).fetchone()
        tr = c.execute("SELECT tier FROM player_tier WHERE player_id = ?", (pid,)).fetchone()
        tier = me["tier"] if me else (tr["tier"] if tr else divisions.BOTTOM)
        out = {"week": week, "tier": tier, "tier_name": divisions.tier_name(tier), "tiers": divisions.TIERS,
               "in_table": bool(me), "rank": None, "size": 0, "up": 0, "down": 0, "rows": [],
               "secs_left": max(0, int((divisions.week_end_utc(week) - now).total_seconds())), "last": None}
        if me:
            rows = add_badges(c, [dict(r) for r in c.execute(TABLE_SQL, (week, tier, me["grp"]))])
            up, down = divisions.zones(len(rows), tier)
            for i, r in enumerate(rows, 1):
                r["me"] = r.pop("player_id") == pid
                r["points"] = round(r["points"], 1)
                if r["me"]:
                    out["rank"] = i
            out.update(size=len(rows), up=up, down=down, rows=rows)
        last = c.execute("SELECT tier, moved FROM divisions WHERE week = ? AND player_id = ? AND moved IS NOT NULL",
                         (prev, pid)).fetchone()
        if last:
            out["last"] = {"week": prev, "from": last["tier"], "from_name": divisions.tier_name(last["tier"]),
                           "moved": last["moved"]}
    return out


# ---------------------------------------------------------------- codes

def new_code(c, table):
    for _ in range(50):
        code = "".join(_rng.choice(CODE_ALPHABET) for _ in range(6))
        if not c.execute("SELECT 1 FROM %s WHERE code = ?" % table, (code,)).fetchone():
            return code
    raise ValueError("couldn't make a code - try again")


def clean_code(raw):
    code = re.sub(r"[\s\-]", "", str(raw or "")).upper()
    if not CODE_RE.match(code):
        raise ValueError("Codes are 6 letters and numbers - check it and try again")
    return code


# ---------------------------------------------------------------- challenges

H2H_SQL = """SELECT ch.creator_pid AS a, pl.player_id AS b, ch.points AS ap, ch.correct AS ac,
                    pl.points AS bp, pl.correct AS bc
             FROM challenge_plays pl JOIN challenges ch ON ch.code = pl.code
             WHERE ch.creator_pid = ? OR pl.player_id = ?"""


def h2h_records(c, pid):
    """{opponent_pid: [won, drawn, lost]} from pid's point of view. Higher points win, then more correct."""
    rec = {}
    for r in c.execute(H2H_SQL, (pid, pid)):
        mine_a = r["a"] == pid
        me, them = ((r["ap"], r["ac"]), (r["bp"], r["bc"])) if mine_a else ((r["bp"], r["bc"]), (r["ap"], r["ac"]))
        k = 0 if me > them else 1 if me == them else 2
        rec.setdefault(r["b"] if mine_a else r["a"], [0, 0, 0])[k] += 1
    return rec


def h2h_list(pid):
    with db() as c:
        rec = h2h_records(c, pid)
        names = {r["player_id"]: r["name"] for r in c.execute(
            "SELECT player_id, name FROM players WHERE player_id IN (%s)" % ",".join("?" * len(rec)), list(rec))} if rec else {}
    rows = [{"name": names.get(o, "?"), "won": w, "drawn": d, "lost": lo} for o, (w, d, lo) in rec.items()]
    rows.sort(key=lambda r: (-(r["won"] + r["drawn"] + r["lost"]), r["name"].lower()))
    return {"rows": rows}


def quiz_settings(data):
    league = data.get("league")
    if league not in LEAGUES:
        raise ValueError("bad league")
    diff = as_int(data.get("diff", 0), 0, 3, "diff")
    timer = as_int(data.get("timer", 0), 0, 20, "timer")
    if timer not in TIMER_MULT:
        raise ValueError("bad timer")
    return league, diff, timer


def create_challenge(data):
    """Store a finished classic quiz (question ids + your answers) under a 6-character code."""
    name, pid = clean_name(data.get("name")), clean_pid(data.get("player_id"))
    league, diff, timer = quiz_settings(data)
    answers = data.get("answers")
    if not isinstance(answers, list) or not 5 <= len(answers) <= 50:
        raise ValueError("A challenge needs a quiz of 5 to 50 questions")
    points, correct, _, marks, fifty = score_classic(answers, len(answers), league, diff, timer)
    ids = [a["id"] for a in answers]
    choices = [a.get("choice") for a in answers]
    grid = "".join("1" if ok else "0" for _, ok in marks)
    with db() as c:
        bind_name(c, name, pid)
        code = new_code(c, "challenges")
        c.execute("""INSERT INTO challenges (code, creator_pid, ids, choices, fifty, league, diff, timer, correct,
                     points, grid, created_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (code, pid, json.dumps(ids), json.dumps(choices, ensure_ascii=False), fifty, league, diff, timer,
                   correct, points, grid, now_utc()))
    return {"ok": True, "code": code, "correct": correct, "total": len(ids), "points": points}


def get_challenge(code, pid=None):
    code = clean_code(code)
    with db() as c:
        ch = c.execute("""SELECT ch.*, p.name FROM challenges ch LEFT JOIN players p ON p.player_id = ch.creator_pid
                          WHERE ch.code = ?""", (code,)).fetchone()
        if not ch:
            raise NotFound("No challenge with that code - check it and try again")
        ids = json.loads(ch["ids"])
        out = {"code": code, "from": ch["name"] or "?", "ids": ids, "total": len(ids), "league": ch["league"],
               "diff": ch["diff"], "timer": ch["timer"], "created_utc": ch["created_utc"],
               "their": {"correct": ch["correct"], "points": ch["points"]},
               "mine": pid == ch["creator_pid"], "result": None}
        out["plays"] = [dict(r) for r in c.execute(
            """SELECT p.name, pl.correct, pl.points, pl.grid FROM challenge_plays pl
               LEFT JOIN players p ON p.player_id = pl.player_id WHERE pl.code = ?
               ORDER BY pl.points DESC, pl.correct DESC, pl.created_utc ASC LIMIT 50""", (code,))]
        if out["mine"]:
            out["their"].update(grid=ch["grid"], choices=json.loads(ch["choices"]))
        elif pid:
            pl = c.execute("SELECT * FROM challenge_plays WHERE code = ? AND player_id = ?", (code, pid)).fetchone()
            if pl:
                me, them = (pl["points"], pl["correct"]), (ch["points"], ch["correct"])
                w, d, lo = h2h_records(c, pid).get(ch["creator_pid"], [0, 0, 0])
                out["result"] = {
                    "you": {"correct": pl["correct"], "points": pl["points"], "grid": pl["grid"],
                            "choices": json.loads(pl["choices"])},
                    "them": {"name": ch["name"] or "?", "correct": ch["correct"], "points": ch["points"],
                             "grid": ch["grid"], "choices": json.loads(ch["choices"])},
                    "outcome": "win" if me > them else "draw" if me == them else "loss",
                    "h2h": {"won": w, "drawn": d, "lost": lo},
                    "tiers": {"you": current_tier(c, pid), "them": current_tier(c, ch["creator_pid"])}}
    return out


def play_challenge(data):
    """A friend's go at a challenge: the same questions in the same order, one go each."""
    code = clean_code(data.get("code"))
    name, pid = clean_name(data.get("name")), clean_pid(data.get("player_id"))
    with db() as c:
        ch = c.execute("SELECT * FROM challenges WHERE code = ?", (code,)).fetchone()
    if not ch:
        raise NotFound("No challenge with that code - check it and try again")
    if ch["creator_pid"] == pid:
        raise Conflict("That's your own challenge - send it to a mate")
    ids = json.loads(ch["ids"])
    answers = data.get("answers")
    if not isinstance(answers, list) or [a.get("id") if isinstance(a, dict) else None for a in answers] != ids:
        raise ValueError("Those aren't the challenge questions - reload the app")
    points, correct, _, marks, _ = score_classic(answers, len(ids), ch["league"], ch["diff"], ch["timer"])
    grid = "".join("1" if ok else "0" for _, ok in marks)
    settle_weeks()
    now = now_utc()
    with db() as c:
        bind_name(c, name, pid)
        try:
            c.execute("""INSERT INTO challenge_plays (code, player_id, choices, correct, points, grid, created_utc)
                         VALUES (?,?,?,?,?,?,?)""",
                      (code, pid, json.dumps([a.get("choice") for a in answers], ensure_ascii=False),
                       correct, points, grid, now))
        except sqlite3.IntegrityError:
            raise Conflict("You've already played this challenge")
        log_answers(c, pid, marks, "challenge", now)
        log_points(c, pid, "challenge", points, now)
    if (points, correct) > (ch["points"], ch["correct"]):
        push.send_async(push_challenge_beaten, code, ch["creator_pid"], pid, name, correct, points,
                        ch["correct"], len(ids))
    out = get_challenge(code, pid)
    out["ok"] = True
    return out


def push_challenge_beaten(code, creator, pid, name, correct, points, their_correct, total):
    if not claim_push("challenge", code + ":" + pid, creator):
        return None
    return push.send_to(db, creator, "challenge", {
        "title": "%s beat your challenge" % name,
        "body": "%d/%d against your %d/%d. Fancy a rematch?" % (correct, total, their_correct, total),
        "url": "./#c=" + code, "tag": "challenge-" + code}, ttl=24 * 3600)


# ---------------------------------------------------------------- private leagues

LEAGUE_NAME_RE = re.compile(r"^[\w .'&\-]{3,24}$", re.UNICODE)
MAX_LEAGUES = 12        # per player
MAX_MEMBERS = 50        # per league


def clean_league_name(raw):
    name = " ".join(str(raw or "").split())
    if not LEAGUE_NAME_RE.match(name):
        raise ValueError("League names are 3-24 letters, numbers, spaces or . ' & -")
    if is_rude(name):
        raise ValueError("Please pick a different league name")
    return name


def _league_count(c, pid):
    return c.execute("SELECT COUNT(*) FROM league_members WHERE player_id = ?", (pid,)).fetchone()[0]


def create_league(data):
    name, pid = clean_name(data.get("name")), clean_pid(data.get("player_id"))
    lname = clean_league_name(data.get("league_name"))
    now = now_utc()
    with db() as c:
        bind_name(c, name, pid)
        if _league_count(c, pid) >= MAX_LEAGUES:
            raise ValueError("You're in %d leagues already - leave one first" % MAX_LEAGUES)
        code = new_code(c, "leagues")
        cur = c.execute("INSERT INTO leagues (code, name, owner_pid, created_utc) VALUES (?,?,?,?)",
                        (code, lname, pid, now))
        c.execute("INSERT INTO league_members (league_id, player_id, joined_utc) VALUES (?,?,?)",
                  (cur.lastrowid, pid, now))
    return {"ok": True, "code": code, "name": lname}


def join_league(data):
    name, pid, code = clean_name(data.get("name")), clean_pid(data.get("player_id")), clean_code(data.get("code"))
    with db() as c:
        lg = c.execute("SELECT id, name FROM leagues WHERE code = ?", (code,)).fetchone()
        if not lg:
            raise NotFound("No league with that code - check it and try again")
        bind_name(c, name, pid)
        if c.execute("SELECT 1 FROM league_members WHERE league_id = ? AND player_id = ?", (lg["id"], pid)).fetchone():
            return {"ok": True, "code": code, "name": lg["name"], "already": True}
        if _league_count(c, pid) >= MAX_LEAGUES:
            raise ValueError("You're in %d leagues already - leave one first" % MAX_LEAGUES)
        if c.execute("SELECT COUNT(*) FROM league_members WHERE league_id = ?", (lg["id"],)).fetchone()[0] >= MAX_MEMBERS:
            raise ValueError("That league is full (%d players)" % MAX_MEMBERS)
        c.execute("INSERT INTO league_members (league_id, player_id, joined_utc) VALUES (?,?,?)",
                  (lg["id"], pid, now_utc()))
    return {"ok": True, "code": code, "name": lg["name"], "already": False}


def leave_league(data):
    pid, code = clean_pid(data.get("player_id")), clean_code(data.get("code"))
    with db() as c:
        c.execute("DELETE FROM league_members WHERE player_id = ? AND league_id = (SELECT id FROM leagues WHERE code = ?)",
                  (pid, code))
    return {"ok": True}


def league_rows(c, league_id, week):
    """Members with this week's and all-time points (all modes), best this week first."""
    rows = [dict(r) for r in c.execute(
        """SELECT m.player_id, COALESCE(p.name, '?') AS name,
                  COALESCE(SUM(CASE WHEN l.week = ? THEN l.points END), 0) AS week,
                  COALESCE(SUM(l.points), 0) AS total, m.joined_utc
           FROM league_members m LEFT JOIN players p ON p.player_id = m.player_id
           LEFT JOIN points_log l ON l.player_id = m.player_id
           WHERE m.league_id = ? GROUP BY m.player_id""", (week, league_id))]
    for r in rows:
        r["week"], r["total"] = round(r["week"], 1), round(r["total"], 1)
    return rows


def _sorted(rows, period):
    a, b = ("week", "total") if period == "week" else ("total", "week")
    return sorted(rows, key=lambda r: (-r[a], -r[b], r["joined_utc"]))


def my_leagues(pid):
    week = divisions.week_key(clock())
    out = []
    with db() as c:
        for lg in c.execute("""SELECT l.id, l.code, l.name FROM leagues l JOIN league_members m ON m.league_id = l.id
                               WHERE m.player_id = ? ORDER BY m.joined_utc""", (pid,)).fetchall():
            rows = _sorted(league_rows(c, lg["id"], week), "week")
            pos = next((i for i, r in enumerate(rows, 1) if r["player_id"] == pid), None)
            leader = rows[0]["name"] if rows and rows[0]["week"] > 0 else None
            out.append({"code": lg["code"], "name": lg["name"], "members": len(rows), "rank_week": pos,
                        "leader": leader})
    return {"rows": out}


def league_view(code, pid, period):
    code = clean_code(code)
    week = divisions.week_key(clock())
    with db() as c:
        lg = c.execute("SELECT * FROM leagues WHERE code = ?", (code,)).fetchone()
        if not lg:
            raise NotFound("No league with that code - check it and try again")
        rows = add_badges(c, _sorted(league_rows(c, lg["id"], week), period))
    member = any(r["player_id"] == pid for r in rows)
    for r in rows:
        r["me"] = r.pop("player_id") == pid
        del r["joined_utc"]
    return {"code": code, "name": lg["name"], "period": period, "week": week, "members": len(rows),
            "member": member, "owner": lg["owner_pid"] == pid, "rows": rows}


# ---------------------------------------------------------------- achievements

BADGE_RATE = (30, 600)      # own budget, so syncing badges never eats into score posts


def set_badges(data):
    pid = clean_pid(data.get("player_id"))
    new = achievements.clean(data.get("badges"))
    with db() as c:
        row = c.execute("SELECT badges FROM player_badges WHERE player_id = ?", (pid,)).fetchone()
        have = achievements.clean(json.loads(row["badges"])) if row else []
        merged = achievements.clean(have + new)
        c.execute("""INSERT INTO player_badges (player_id, badges, updated_utc) VALUES (?,?,?)
                     ON CONFLICT(player_id) DO UPDATE SET badges = excluded.badges,
                     updated_utc = excluded.updated_utc""", (pid, json.dumps(merged), now_utc()))
    return {"ok": True, "badges": merged}


def badge_map(c, pids):
    """{player_id: best three badge ids} for the given players."""
    pids = [p for p in set(pids) if p]
    out = {}
    for i in range(0, len(pids), 500):
        chunk = pids[i:i + 500]
        for r in c.execute("SELECT player_id, badges FROM player_badges WHERE player_id IN (%s)"
                           % ",".join("?" * len(chunk)), chunk):
            try:
                out[r["player_id"]] = achievements.clean(json.loads(r["badges"]))[:achievements.SHOW]
            except (ValueError, TypeError):
                pass
    return out


def add_badges(c, rows, key="player_id"):
    """Adds `badges` (maybe empty) to each row, keyed by player_id, or by name for the classic board."""
    if key == "name":
        ids = {r["name_lc"]: r["player_id"] for r in c.execute(
            "SELECT player_id, name_lc FROM players WHERE name_lc IN (%s)" % ",".join("?" * len(rows)),
            [r["name"].lower() for r in rows])} if rows else {}
        pids = [ids.get(r["name"].lower()) for r in rows]
    else:
        pids = [r.get(key) for r in rows]
    bm = badge_map(c, pids)
    for r, p in zip(rows, pids):
        r["badges"] = bm.get(p, [])
    return rows


def with_badges(rows, key="player_id"):
    with db() as c:
        return add_badges(c, rows, key)


def current_tier(c, pid):
    week = divisions.week_key(clock())
    r = c.execute("SELECT tier FROM divisions WHERE week = ? AND player_id = ?", (week, pid)).fetchone()
    if not r:
        r = c.execute("SELECT tier FROM player_tier WHERE player_id = ?", (pid,)).fetchone()
    return r["tier"] if r else divisions.BOTTOM


# ---------------------------------------------------------------- reports

REPORT_RATE = (10, 3600)    # at most 10 reports per IP per hour (own budget)
REPORT_REASONS = ("wrong", "outdated", "typo", "other")
NOTE_MAX = 300


def clean_note(raw):
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ValueError("bad note")
    s = "".join(ch for ch in raw.replace("\t", " ") if ch == "\n" or unicodedata.category(ch)[0] != "C")
    return re.sub(r"[ \t]+", " ", s).strip()[:NOTE_MAX]


def add_report(data):
    pid = clean_pid(data.get("player_id"))
    qid = data.get("id")
    by_id, _ = bank()
    if not isinstance(qid, str) or qid not in by_id:
        raise ValueError("unknown question")
    reason = data.get("reason")
    if reason not in REPORT_REASONS:
        raise ValueError("bad reason")
    note = clean_note(data.get("note"))
    if reason == "other" and not note:
        raise ValueError("say what's wrong in the note")
    with db() as c:
        c.execute("""INSERT INTO reports (player_id, qid, reason, note, status, created_utc) VALUES (?,?,?,?,'open',?)
                     ON CONFLICT(player_id, qid) DO UPDATE SET reason = excluded.reason, note = excluded.note,
                     status = 'open', created_utc = excluded.created_utc""", (pid, qid, reason, note, now_utc()))
    return {"ok": True}


# ---------------------------------------------------------------- push notifications

PUSH_RATE = (30, 600)       # subscribe / change / delete: own budget


def claim_push(kind, ref, pid):
    """True the first time (kind, ref, pid) is claimed - so nobody gets the same notification twice."""
    with db() as c:
        try:
            c.execute("INSERT INTO push_log (kind, ref, player_id, sent_utc) VALUES (?,?,?,?)",
                      (kind, ref, pid, now_utc()))
            return True
        except sqlite3.IntegrityError:
            return False


def push_key():
    key = push.public_key()
    if not key:
        raise Unavailable("Notifications aren't set up on the server yet")
    return {"key": key}


def add_push(data):
    pid = clean_pid(data.get("player_id"))
    endpoint, p256dh, auth = push.clean_sub(data.get("subscription"))
    types = push.clean_types(data.get("types", list(push.TYPES)))
    now = now_utc()
    with db() as c:
        if not types:
            c.execute("DELETE FROM push_subs WHERE endpoint = ?", (endpoint,))
            return {"ok": True, "types": []}
        c.execute("""INSERT INTO push_subs (endpoint, player_id, p256dh, auth, types, created_utc, updated_utc)
                     VALUES (?,?,?,?,?,?,?)
                     ON CONFLICT(endpoint) DO UPDATE SET player_id = excluded.player_id, p256dh = excluded.p256dh,
                     auth = excluded.auth, types = excluded.types, updated_utc = excluded.updated_utc""",
                  (endpoint, pid, p256dh, auth, ",".join(types), now, now))
        extra = [r[0] for r in c.execute("SELECT endpoint FROM push_subs WHERE player_id = ? "
                                         "ORDER BY updated_utc DESC, created_utc DESC LIMIT -1 OFFSET ?",
                                         (pid, push.MAX_SUBS))]
        c.executemany("DELETE FROM push_subs WHERE endpoint = ?", [(e,) for e in extra])
    return {"ok": True, "types": types}


def delete_push(data):
    endpoint = data.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        raise ValueError("bad endpoint")
    with db() as c:
        c.execute("DELETE FROM push_subs WHERE endpoint = ?", (endpoint,))
    return {"ok": True}


class Unavailable(Exception):
    """Becomes HTTP 503."""


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
        if code >= 400 and code != 404:     # why a request was refused, for the container log
            sys.stderr.write("%s %s %s -> %d %s\n" % (self._ip(), self.command, self.path.split("?")[0], code,
                                                     obj.get("error", "")))
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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = dict(urllib.parse.parse_qsl(query))
        pid = params.get("player_id")
        try:
            pid = clean_pid(pid) if pid else None
            if path == "/api/challenge":
                return self._send(200, get_challenge(params.get("code"), pid))
            if path == "/api/league":
                period = params.get("period", "week")
                if period not in ("week", "all"):
                    return self._send(400, {"error": "bad period"})
                return self._send(200, league_view(params.get("code"), pid, period))
            if path in ("/api/leagues", "/api/division", "/api/h2h"):
                if not pid:
                    return self._send(400, {"error": "bad player_id"})
                fn = {"/api/leagues": my_leagues, "/api/division": division_view, "/api/h2h": h2h_list}[path]
                return self._send(200, fn(pid))
            if path == "/api/health":
                return self._send(200, {"ok": True})
            if path == "/api/push/key":
                return self._send(200, push_key())
            if path == "/api/board" and params.get("mode", "classic") != "classic":
                period = params.get("period", "week")
                if params["mode"] not in RUN_MODES or period not in ("week", "all"):
                    return self._send(400, {"error": "bad mode or period"})
                return self._send(200, public_run_board(params["mode"], period))
            if path == "/api/board":
                try:
                    total = int(params.get("len", "10"))
                except ValueError:
                    total = 0
                period = params.get("period", "week")
                if total not in LENGTHS or period not in ("week", "all"):
                    return self._send(400, {"error": "bad len or period"})
                return self._send(200, {"rows": with_badges(board(total, period), "name")})
            if path == "/api/daily":
                d = parse_date(params["date"]) if "date" in params else daily_gen.uk_today(clock())
                return self._send(200, daily_ids(d))
            if path == "/api/daily/board":
                period = params.get("period", "today")
                if period not in ("today", "week"):
                    return self._send(400, {"error": "bad period"})
                return self._send(200, public_board(period))
        except NotFound as e:
            return self._send(404, {"error": str(e)})
        except Unavailable as e:
            return self._send(503, {"error": str(e)})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        route = {"/api/scores": add_score, "/api/daily": add_daily, "/api/name": set_name,
                 "/api/challenges": create_challenge, "/api/challenge/play": play_challenge,
                 "/api/leagues": create_league, "/api/leagues/join": join_league,
                 "/api/leagues/leave": leave_league, "/api/badges": set_badges,
                 "/api/report": add_report, "/api/push": add_push}.get(path)
        return self._body(path, route)

    def do_DELETE(self):
        path = self.path.split("?")[0]
        return self._body(path, {"/api/push": delete_push}.get(path))

    def _body(self, path, route):
        if not route:
            return self._send(404, {"error": "not found"})
        if path == "/api/push":
            if limited(self._ip(), PUSH_RATE, "push:"):
                return self._send(429, {"error": "Too many changes - try again in a few minutes."})
        elif path == "/api/badges":
            if limited(self._ip(), BADGE_RATE, "badges:"):
                return self._send(429, {"error": "Too many updates - try again in a few minutes."})
        elif path == "/api/report":
            if limited(self._ip(), REPORT_RATE, "report:"):
                return self._send(429, {"error": "Thanks - that's a lot of reports. Try again later."})
        elif limited(self._ip()):
            return self._send(429, {"error": "Too many scores - try again in a few minutes."})
        if path in ("/api/challenges", "/api/leagues") and limited(self._ip(), CREATE_RATE, "create:"):
            return self._send(429, {"error": "Too many new challenges or leagues - try again later."})
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
        except NotFound as e:
            return self._send(404, {"error": str(e)})
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, {"error": str(e)})

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self._ip(), fmt % args))


if __name__ == "__main__":
    init()
    host = os.environ.get("QUIZ_HOST", "0.0.0.0")
    port = int(os.environ.get("QUIZ_PORT", "8000"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()
