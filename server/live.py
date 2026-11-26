"""Live 1v1 matches, the ranked 1v1 league and 2v2 rooms (standard library only; identical in every quiz).

Two players answer the same questions at the same time. The server owns the clock: it
sends the questions one at a time, marks every answer, and only reveals the right answer
once the question is closed. Clients poll GET /api/live/state about once a second.

    POST /api/live/queue    {name, player_id}        quick match: pairs you with whoever is waiting (call it again
                                                     every ~2 s while waiting = heartbeat) -> {status, code?}
    POST /api/live/cancel   {player_id}              leave the queue
    POST /api/live/create   {name, player_id}        a friendly match for a mate -> {code}
    POST /api/live/join     {name, player_id, code}  -> {code}
    GET  /api/live/state    ?code= &player_id=       the match as you should see it right now
    POST /api/live/answer   {code, player_id, idx, choice}
    GET  /api/live/league   ?period=week|all &player_id=   ranked table (3 for a win, 1 for a draw)

Ranked = quick match. Friendly (code) matches show a head-to-head but don't touch the table.
A match lives in memory while it's played and is written to live_matches when it ends, so a
server restart only loses matches in progress.
"""

import json
import random
import re
import threading
import time
from datetime import date, timedelta

N_QUESTIONS = 10
Q_SECS = 15.0            # time to answer one question
REVEAL_SECS = 3.0        # answer shown before the next question
START_SECS = 3.0         # "get ready" after both players are in
QUEUE_STALE = 8.0        # a waiting player who hasn't pinged for this long is dropped
SEEN_STALE = 12.0        # a player who hasn't polled for this long in a live match has left
WAIT_FRIEND = 600.0      # an unjoined friendly match is dropped after 10 minutes
KEEP_DONE = 900.0        # finished matches stay readable for 15 minutes
PAIR_CAP = 3             # the same two players: only their first 3 ranked results a week count
BASE_PTS, SPEED_PTS = 10, 5

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_RE = re.compile(r"^[A-HJ-NP-Z2-9]{6}$")

L = None                  # the leaderboard module (bind() sets it; tests patch things on it)
_lock = threading.RLock()
_matches = {}             # code -> match dict
_queue = {}               # player_id -> {"name", "seen"}
_by_player = {}           # player_id -> code of their current match
_rng = random.SystemRandom()


def bind(module):
    global L
    L = module


def init_db(c):
    c.execute("""CREATE TABLE IF NOT EXISTS live_matches (
        id INTEGER PRIMARY KEY,
        code TEXT NOT NULL,
        p1 TEXT NOT NULL,
        p2 TEXT NOT NULL,
        pts1 INTEGER NOT NULL,
        pts2 INTEGER NOT NULL,
        correct1 INTEGER NOT NULL,
        correct2 INTEGER NOT NULL,
        outcome INTEGER NOT NULL,          -- 1 = p1 won, 0 = draw, 2 = p2 won
        ranked INTEGER NOT NULL,
        forfeit INTEGER NOT NULL,
        week TEXT NOT NULL,
        created_utc TEXT NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS live_week ON live_matches(week, ranked)")
    c.execute("CREATE INDEX IF NOT EXISTS live_p1 ON live_matches(p1)")
    c.execute("CREATE INDEX IF NOT EXISTS live_p2 ON live_matches(p2)")
    init_db2(c)


# ---------------------------------------------------------------- match state

def _new_code():
    for _ in range(50):
        code = "".join(_rng.choice(CODE_ALPHABET) for _ in range(6))
        if code not in _matches:
            return code
    raise ValueError("couldn't make a code - try again")


def _pick_questions():
    by_id, _ = L.bank()
    pool = list(by_id.values())
    if len(pool) < N_QUESTIONS:
        raise ValueError("not enough questions on the server")
    # a spread of difficulty: 3 easy, 4 medium, 3 hard where the bank allows it
    out, used = [], set()
    for d, n in ((1, 3), (2, 4), (3, 3)):
        cand = [q for q in pool if q["d"] == d]
        _rng.shuffle(cand)
        for q in cand[:n]:
            out.append(q["id"]); used.add(q["id"])
    rest = [q["id"] for q in pool if q["id"] not in used]
    _rng.shuffle(rest)
    out += rest[:N_QUESTIONS - len(out)]
    _rng.shuffle(out)
    return out[:N_QUESTIONS]


def _make(p1, n1, ranked, code=None):
    code = code or _new_code()
    m = {"code": code, "ranked": ranked, "p": [p1, None], "names": [n1, None], "ids": [], "state": "waiting",
         "made": time.monotonic(), "start": None, "cur": 0, "opened": None, "closed": None,
         "ans": [{}, {}],            # per player: idx -> (choice, t_ms, pts)
         "seen": [time.monotonic(), 0.0], "result": None, "done_at": None, "forfeit": False}
    _matches[code] = m
    _by_player[p1] = code
    return m


def _fill(m, p2, n2):
    m["p"][1], m["names"][1] = p2, n2
    m["ids"] = _pick_questions()
    now = time.monotonic()
    m["state"] = "live"
    m["start"] = now + START_SECS
    m["opened"] = m["start"]
    m["seen"][1] = now
    _by_player[p2] = m["code"]


def _side(m, pid):
    if pid == m["p"][0]:
        return 0
    if pid == m["p"][1]:
        return 1
    return None


def _score(m, s):
    return sum(a[2] for a in m["ans"][s].values())


def _correct(m, s):
    return sum(1 for a in m["ans"][s].values() if a[2] > 0)


def _tick(m, now):
    """Move the clock on: close a question when both answered or time's up, reveal, open the next."""
    if m["state"] != "live":
        return
    # a player who stopped polling has left
    for s in (0, 1):
        if now - m["seen"][s] > SEEN_STALE and m["start"] and now > m["start"] + SEEN_STALE:
            return _finish(m, now, forfeit_by=s)
    while m["state"] == "live":
        if m["cur"] >= N_QUESTIONS:
            return _finish(m, now)
        if m["closed"] is None:
            both = m["cur"] in m["ans"][0] and m["cur"] in m["ans"][1]
            if both:
                m["closed"] = max(m["ans"][0][m["cur"]][3], m["ans"][1][m["cur"]][3])
            elif now >= m["opened"] + Q_SECS:
                m["closed"] = m["opened"] + Q_SECS
            else:
                return
        if now >= m["closed"] + REVEAL_SECS:
            m["opened"] = m["closed"] + REVEAL_SECS
            m["closed"] = None
            m["cur"] += 1
        else:
            return


def _finish(m, now, forfeit_by=None):
    if m["state"] == "done":
        return
    m["state"] = "done"
    m["done_at"] = now
    m["forfeit"] = forfeit_by is not None
    p1, p2 = _score(m, 0), _score(m, 1)
    if forfeit_by is not None:
        outcome = 2 if forfeit_by == 0 else 1
    else:
        outcome = 1 if p1 > p2 else 2 if p2 > p1 else 0
    m["result"] = {"outcome": outcome}
    try:
        _save(m, p1, p2, outcome)
    except Exception as e:      # never lose the match screen over a DB hiccup
        import sys
        sys.stderr.write("live: couldn't save %s: %s\n" % (m["code"], e))


def _save(m, p1, p2, outcome):
    L.settle_weeks()
    now = L.now_utc()
    week = L.divisions.week_key(L.clock())
    c1, c2 = _correct(m, 0), _correct(m, 1)
    with L.db() as c:
        c.execute("""INSERT INTO live_matches (code, p1, p2, pts1, pts2, correct1, correct2, outcome, ranked,
                     forfeit, week, created_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (m["code"], m["p"][0], m["p"][1], p1, p2, c1, c2, outcome, int(m["ranked"]), int(m["forfeit"]),
                   week, now))
        if m["ranked"] and not m["forfeit"]:
            # ranked games also count towards the weekly divisions, 1 point per correct answer
            for s, k in ((0, c1), (1, c2)):
                L.log_points(c, m["p"][s], "live", k, now)
        for s in (0, 1):
            L.log_answers(c, m["p"][s], [(m["ids"][i], a[2] > 0) for i, a in sorted(m["ans"][s].items())],
                          "live", now)


def _sweep(now):
    """Drop stale queue entries, abandoned friendly matches and old finished ones."""
    for pid in [p for p, e in _queue.items() if now - e["seen"] > QUEUE_STALE]:
        del _queue[pid]
    for code in list(_matches):
        m = _matches[code]
        old = (m["state"] == "waiting" and now - m["made"] > WAIT_FRIEND) or \
              (m["state"] == "done" and now - m["done_at"] > KEEP_DONE)
        if old:
            for pid in m["p"]:
                if pid and _by_player.get(pid) == code:
                    del _by_player[pid]
            del _matches[code]


def _current(pid):
    """The player's unfinished match, if any."""
    code = _by_player.get(pid)
    m = _matches.get(code) if code else None
    return m if m and m["state"] != "done" else None


# ---------------------------------------------------------------- entry points

def queue(data):
    name, pid = L.clean_name(data.get("name")), L.clean_pid(data.get("player_id"))
    with L.db() as c:
        L.bind_name(c, name, pid)
    now = time.monotonic()
    with _lock:
        _sweep(now)
        m = _current(pid)
        if m:
            return {"status": "matched", "code": m["code"]}
        _queue[pid] = {"name": name, "seen": now}
        for other, e in list(_queue.items()):
            if other != pid and now - e["seen"] <= QUEUE_STALE:
                del _queue[pid], _queue[other]
                m = _make(other, e["name"], True)       # whoever waited longest is player 1
                _fill(m, pid, name)
                return {"status": "matched", "code": m["code"]}
        return {"status": "waiting"}


def cancel(data):
    pid = L.clean_pid(data.get("player_id"))
    with _lock:
        _queue.pop(pid, None)
        m = _current(pid)
        if m and m["state"] == "waiting":                # cancelling your own unjoined friendly match
            del _matches[m["code"]]
            _by_player.pop(pid, None)
    return {"ok": True}


def create(data):
    name, pid = L.clean_name(data.get("name")), L.clean_pid(data.get("player_id"))
    with L.db() as c:
        L.bind_name(c, name, pid)
    with _lock:
        _sweep(time.monotonic())
        m = _current(pid)
        if m and m["state"] == "waiting":
            return {"ok": True, "code": m["code"]}
        if m:
            raise L.Conflict("You're already in a live match")
        _queue.pop(pid, None)
        return {"ok": True, "code": _make(pid, name, False)["code"]}


def join(data):
    name, pid = L.clean_name(data.get("name")), L.clean_pid(data.get("player_id"))
    code = re.sub(r"[\s\-]", "", str(data.get("code") or "")).upper()
    if not CODE_RE.match(code):
        raise ValueError("Codes are 6 letters and numbers - check it and try again")
    with L.db() as c:
        L.bind_name(c, name, pid)
    with _lock:
        _sweep(time.monotonic())
        m = _matches.get(code)
        if not m:
            raise L.NotFound("No live match with that code - it may have expired")
        if _side(m, pid) is not None:
            return {"ok": True, "code": code}
        if m["state"] != "waiting":
            raise L.Conflict("That match is already full")
        mine = _current(pid)
        if mine:
            raise L.Conflict("Finish your other live match first")
        _queue.pop(pid, None)
        _fill(m, pid, name)
        return {"ok": True, "code": code}


def answer(data):
    pid = L.clean_pid(data.get("player_id"))
    code = str(data.get("code") or "").upper()
    idx, choice = data.get("idx"), data.get("choice")
    if isinstance(idx, bool) or not isinstance(idx, int):
        raise ValueError("bad idx")
    if choice is not None and not isinstance(choice, str):
        raise ValueError("bad choice")
    now = time.monotonic()
    with _lock:
        m = _matches.get(code)
        s = _side(m, pid) if m else None
        if s is None:
            raise L.NotFound("No such match")
        m["seen"][s] = now
        _tick(m, now)
        if m["state"] != "live" or idx != m["cur"] or m["closed"] is not None or now < m["opened"]:
            return {"ok": False, "late": True}          # too late (or too early): not counted
        if idx in m["ans"][s]:
            return {"ok": True}
        q = L.bank()[0][m["ids"][idx]]
        left = max(0.0, m["opened"] + Q_SECS - now)
        ok = choice == q["a"]
        pts = BASE_PTS + int(round(SPEED_PTS * left / Q_SECS)) if ok else 0
        m["ans"][s][idx] = (choice, int(round((now - m["opened"]) * 1000)), pts, now)
        _tick(m, now)
        return {"ok": True}


def _opts(code, q):
    """Same shuffled order for both players, different per match."""
    o = [q["a"]] + list(q["w"])
    random.Random(code + q["id"]).shuffle(o)
    return o


def _review(m, by_id):
    out = []
    for i, qid in enumerate(m["ids"]):
        q = by_id[qid]
        row = {"q": q["q"], "a": q["a"], "x": q.get("x", "")}
        for s, key in ((0, "p1"), (1, "p2")):
            a = m["ans"][s].get(i)
            row[key] = {"choice": a[0] if a else None, "pts": a[2] if a else 0}
        out.append(row)
    return out


def _h2h(c, me, them):
    w = d = lo = 0
    for r in c.execute("""SELECT p1, outcome FROM live_matches WHERE (p1 = ? AND p2 = ?) OR (p1 = ? AND p2 = ?)""",
                       (me, them, them, me)):
        if r["outcome"] == 0:
            d += 1
        elif (r["outcome"] == 1) == (r["p1"] == me):
            w += 1
        else:
            lo += 1
    return {"won": w, "drawn": d, "lost": lo}


def state(code, pid):
    code = str(code or "").upper()
    now = time.monotonic()
    with _lock:
        m = _matches.get(code)
        s = _side(m, pid) if m else None
        if s is None:
            raise L.NotFound("That live match has ended or doesn't exist")
        m["seen"][s] = now
        _tick(m, now)
        by_id = L.bank()[0]
        o = 1 - s
        out = {"code": code, "ranked": m["ranked"], "status": m["state"], "n": N_QUESTIONS,
               "you": {"name": m["names"][s]}, "opp": {"name": m["names"][o]} if m["p"][o] else None}
        if m["state"] == "waiting":
            return out
        out["score"] = {"you": _score(m, s), "opp": _score(m, o)}
        out["correct"] = {"you": _correct(m, s), "opp": _correct(m, o)}
        if m["state"] == "live":
            if now < m["start"]:
                out.update(phase="countdown", ms_left=int((m["start"] - now) * 1000), cur=0)
                return out
            cur = m["cur"]
            q = by_id[m["ids"][cur]]
            out.update(cur=cur, you_answered=cur in m["ans"][s], opp_answered=cur in m["ans"][o])
            if m["closed"] is None:
                out.update(phase="question", ms_left=int((m["opened"] + Q_SECS - now) * 1000),
                           q={"id": q["id"], "q": q["q"], "opts": _opts(code, q), "d": q["d"], "img": q.get("img")})
            else:
                mine, theirs = m["ans"][s].get(cur), m["ans"][o].get(cur)
                out.update(phase="reveal", ms_left=int((m["closed"] + REVEAL_SECS - now) * 1000),
                           q={"id": q["id"], "q": q["q"], "opts": _opts(code, q), "d": q["d"], "img": q.get("img")},
                           reveal={"a": q["a"], "x": q.get("x", ""),
                                   "you": {"choice": mine[0] if mine else None, "pts": mine[2] if mine else 0},
                                   "opp": {"choice": theirs[0] if theirs else None, "pts": theirs[2] if theirs else 0}})
            return out
        # done
        me_pid, op_pid = m["p"][s], m["p"][o]
        outcome = m["result"]["outcome"]
        won = outcome == 0 and "draw" or ((outcome == 1) == (s == 0) and "win" or "loss")
        rev = _review(m, by_id)
        if s == 1:
            rev = [{"q": r["q"], "a": r["a"], "x": r["x"], "p1": r["p2"], "p2": r["p1"]} for r in rev]
        with L.db() as c:
            h2h = _h2h(c, me_pid, op_pid)
        out.update(phase="done", result={"outcome": won, "forfeit": m["forfeit"], "h2h": h2h,
                                         "review": [{"q": r["q"], "a": r["a"], "x": r["x"], "you": r["p1"],
                                                     "opp": r["p2"]} for r in rev]})
        return out


# ---------------------------------------------------------------- the ranked league

def _table(rows, pid, limit=25):
    """rows: [(p1, p2, pts1, pts2, outcome)] already limited by the pair cap -> sorted table."""
    t = {}

    def rec(p):
        return t.setdefault(p, {"player_id": p, "P": 0, "W": 0, "D": 0, "L": 0, "gd": 0, "pts": 0})
    for p1, p2, a, b, out in rows:
        for me, mine, theirs, res in ((p1, a, b, out), (p2, b, a, {1: 2, 2: 1, 0: 0}[out])):
            r = rec(me)
            r["P"] += 1
            r["gd"] += mine - theirs
            if res == 1:
                r["W"] += 1; r["pts"] += 3
            elif res == 0:
                r["D"] += 1; r["pts"] += 1
            else:
                r["L"] += 1
    order = sorted(t.values(), key=lambda r: (-r["pts"], -r["gd"], -r["W"], r["P"], r["player_id"]))
    for i, r in enumerate(order):
        r["rank"] = i + 1
    return order


def league(period, pid):
    if period not in ("week", "all"):
        raise ValueError("bad period")
    L.settle_weeks()
    week = L.divisions.week_key(L.clock())
    with L.db() as c:
        if period == "week":
            cur = c.execute("""SELECT p1, p2, pts1, pts2, outcome FROM live_matches WHERE ranked = 1 AND forfeit = 0
                               AND week = ? ORDER BY id""", (week,))
        else:
            cur = c.execute("""SELECT p1, p2, pts1, pts2, outcome, week FROM live_matches WHERE ranked = 1
                               AND forfeit = 0 ORDER BY id""")
        seen, rows = {}, []
        for r in cur:
            key = (tuple(sorted((r["p1"], r["p2"]))), week if period == "week" else r["week"])
            seen[key] = seen.get(key, 0) + 1
            if seen[key] <= PAIR_CAP:
                rows.append((r["p1"], r["p2"], r["pts1"], r["pts2"], r["outcome"]))
        table = _table(rows, pid)
        names = {}
        want = [r["player_id"] for r in table[:25]] + ([pid] if pid else [])
        if want:
            for r in c.execute("SELECT player_id, name FROM players WHERE player_id IN (%s)" % ",".join("?" * len(want)),
                               want):
                names[r["player_id"]] = r["name"]
        mine = next((r for r in table if r["player_id"] == pid), None)
        top = [dict(r, name=names.get(r["player_id"], "?"), mine=r["player_id"] == pid) for r in table[:25]]
        for r in top:
            del r["player_id"]
        you = dict(mine, name=names.get(pid, "?")) if mine else None
        if you:
            del you["player_id"]
        week_end = date.fromisoformat(week) + timedelta(days=7)
    return {"period": period, "rows": top, "you": you, "week": week, "ends": week_end.isoformat(),
            "players": len(table)}


# ================================================================ 2v2 rooms
#
# Four seats in two teams (seats 0-1 = team 0, seats 2-3 = team 1). Same question for all four,
# 15 s each, quicker right answers score more, team score = sum. Rooms are made with a code
# (shared as a #r=CODE link) or by Quick match, which drops you in a public room and fills the
# empty seats with bots after QUICK_FILL seconds. A player who goes quiet for 2 questions gets a
# bot stand-in until they come back. Clients long-poll:
#
#     GET  /api/live/room     ?code= &player_id= [&v=N]   the room as you see it; with v it waits
#                                                         (up to HOLD s) until the room moves past v
#     POST /api/live/room/create  {name, player_id}               -> {code}
#     POST /api/live/room/join    {name, player_id, code, team?}  -> {code, seat}
#     POST /api/live/room/quick   {name, player_id}               -> {code, seat}
#     POST /api/live/room/seat    {player_id, code, team}         switch team in the lobby
#     POST /api/live/room/start   {player_id, code}               host only; empty seats become bots
#     POST /api/live/room/answer  {player_id, code, idx, choice}
#     POST /api/live/room/leave   {player_id, code}
#     POST /api/live/room/rematch {player_id, code}               back to the lobby, same room
#
# Results go to live_games + live_players when a game ends; humans get 1 division point per
# right answer (mode "live2"). Bots are never on any board.

R_SEATS = 4
R_COUNT = 3.0             # 3-2-1 before the first question
R_Q = 15.0
R_REVEAL = 4.0
QUICK_FILL = 30.0         # a public room starts with bots this long after it opened
HOLD = 25.0               # longest a long-poll waits
MAX_HOLDS = 300           # at most this many long-polls waiting at once
LOBBY_GONE = 45.0         # a lobby player not heard from for this long loses their seat
STANDIN_AFTER = 2         # questions missed without being seen -> a bot takes over
ROOM_IDLE = 1800.0        # rooms nobody has touched for 30 minutes are dropped
DIFF_MULT = {1: 1.0, 2: 1.25, 3: 1.5}
BOT_ACC = {1: 0.75, 2: 0.55, 3: 0.35}
BOT_SECS = (3.0, 12.0)
BOT_NAMES = ["Ace", "Rook", "Nova", "Blaze", "Echo", "Pixel", "Turbo", "Comet", "Sparky", "Zippy", "Bolt", "Dash"]

_rooms = {}               # code -> room dict
_room_of = {}             # player_id -> code of the room they sit in
_cond = threading.Condition(_lock)
_wall = time.monotonic    # real clock for long-poll holds (tests swap `time` for a fake one)
_holds = [0]


def init_db2(c):
    c.execute("""CREATE TABLE IF NOT EXISTS live_games (
        id INTEGER PRIMARY KEY,
        code TEXT NOT NULL,
        public INTEGER NOT NULL,
        score0 INTEGER NOT NULL,
        score1 INTEGER NOT NULL,
        winner INTEGER NOT NULL,           -- 0 / 1 = that team, -1 = draw
        n INTEGER NOT NULL,
        week TEXT NOT NULL,
        created_utc TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS live_players (
        game_id INTEGER NOT NULL,
        seat INTEGER NOT NULL,
        team INTEGER NOT NULL,
        player_id TEXT,                    -- NULL for bots
        name TEXT NOT NULL,
        pts INTEGER NOT NULL,
        correct INTEGER NOT NULL,
        bot INTEGER NOT NULL,
        standin INTEGER NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS live_players_pid ON live_players(player_id)")


def _bot_tag():
    return getattr(L, "LIVE_BOT_TAG", "Bot")


def _team(seat):
    return 0 if seat < 2 else 1


def _humans(r):
    return [i for i, s in enumerate(r["seats"]) if s and not s["bot"] and not s["left"]]


def _bump(r):
    r["v"] += 1
    _cond.notify_all()


def _room_new(pid, name, public, now):
    code = None
    for _ in range(50):
        c = "".join(_rng.choice(CODE_ALPHABET) for _ in range(6))
        if c not in _rooms and c not in _matches:
            code = c
            break
    if not code:
        raise ValueError("couldn't make a code - try again")
    r = {"code": code, "public": public, "host": pid, "seats": [None] * R_SEATS, "phase": "lobby", "v": 1,
         "made": now, "touched": now, "fill_at": now + QUICK_FILL if public else None, "round": 0,
         "ids": [], "cur": 0, "opened": None, "closed": None, "start": None, "ans": [{} for _ in range(R_SEATS)],
         "plan": {}, "result": None, "done_at": None}
    _rooms[code] = r
    return r


def _seat_human(r, seat, pid, name, now):
    r["seats"][seat] = {"pid": pid, "name": name, "bot": False, "standin": False, "left": False,
                        "seen": now, "missed": 0}
    _room_of[pid] = r["code"]


def _free_seat(r, team=None):
    teams = [team] if team in (0, 1) else sorted((0, 1), key=lambda t: sum(
        1 for i in (2 * t, 2 * t + 1) if r["seats"][i]))
    for t in teams:
        for i in (2 * t, 2 * t + 1):
            if r["seats"][i] is None:
                return i
    return None


def _seat_of(r, pid):
    for i, s in enumerate(r["seats"]):
        if s and not s["bot"] and not s["left"] and s["pid"] == pid:
            return i
    return None


def _drop_seat(r, i):
    s = r["seats"][i]
    if s and not s["bot"] and _room_of.get(s["pid"]) == r["code"]:
        del _room_of[s["pid"]]
    r["seats"][i] = None
    if s and r["host"] == s["pid"]:
        hs = _humans(r)
        r["host"] = r["seats"][hs[0]]["pid"] if hs else None


def _kill_room(r):
    for i in range(R_SEATS):
        s = r["seats"][i]
        if s and not s["bot"] and _room_of.get(s["pid"]) == r["code"]:
            del _room_of[s["pid"]]
    _rooms.pop(r["code"], None)


def _room_sweep(now):
    for code in list(_rooms):
        r = _rooms[code]
        if now - r["touched"] > ROOM_IDLE or (r["phase"] == "done" and now - r["done_at"] > KEEP_DONE):
            _kill_room(r)


def _leave_lobby_rooms(pid, now):
    """Leaving one lobby for another is fine; leaving a game in progress isn't."""
    code = _room_of.get(pid)
    r = _rooms.get(code) if code else None
    if not r:
        _room_of.pop(pid, None)
        return
    i = _seat_of(r, pid)
    if i is None:
        _room_of.pop(pid, None)
        return
    if r["phase"] in ("countdown", "question", "reveal"):
        raise L.Conflict("Finish your other 2v2 game first")
    _drop_seat(r, i)
    if not _humans(r):
        _kill_room(r)
    else:
        _bump(r)


def _room_start(r, now):
    used = set(s["name"] for s in r["seats"] if s)
    names = [n for n in BOT_NAMES if "%s %s" % (_bot_tag(), n) not in used]
    _rng.shuffle(names)
    for i in range(R_SEATS):
        if r["seats"][i] is None:
            r["seats"][i] = {"pid": None, "name": "%s %s" % (_bot_tag(), names.pop()), "bot": True,
                             "standin": False, "left": False, "seen": now, "missed": 0}
    r["ids"] = _pick_questions()
    r["ans"] = [{} for _ in range(R_SEATS)]
    r["cur"], r["closed"], r["result"], r["done_at"] = 0, None, None, None
    r["start"] = now + R_COUNT
    r["opened"] = r["start"]
    r["phase"] = "countdown"
    r["round"] += 1
    _plan_bots(r)
    _bump(r)


def _auto(s):
    return s["bot"] or s["standin"]


def _plan_bots(r):
    """When the bots (and stand-ins) answer the current question, and what."""
    q = L.bank()[0][r["ids"][r["cur"]]]
    r["plan"] = {}
    for i, s in enumerate(r["seats"]):
        if s and _auto(s):
            r["plan"][i] = _bot_pick(r, q)


def _bot_pick(r, q):
    t = r["opened"] + _rng.uniform(*BOT_SECS)
    choice = q["a"] if _rng.random() < BOT_ACC.get(q["d"], 0.5) else _rng.choice(list(q["w"]))
    return (t, choice)


def _r_pts(q, t, opened):
    left = max(0.0, opened + R_Q - t)
    return int(round((100 + 100 * left / R_Q) * DIFF_MULT.get(q["d"], 1.0)))


def _r_record(r, i, choice, t, by_bot):
    q = L.bank()[0][r["ids"][r["cur"]]]
    pts = _r_pts(q, t, r["opened"]) if choice == q["a"] else 0
    r["ans"][i][r["cur"]] = (choice, int(round((t - r["opened"]) * 1000)), pts, t, by_bot)


def _room_tick(r, now):
    """Move the room's clock on to `now`, replaying bot answers and deadlines in order."""
    if r["phase"] == "lobby":
        if r["public"] and r["fill_at"] is not None and now >= r["fill_at"] and _humans(r):
            _room_start(r, r["fill_at"])
        else:
            for i in _humans(r):
                if now - r["seats"][i]["seen"] > LOBBY_GONE:
                    _drop_seat(r, i)
                    _bump(r)
            if not _humans(r):
                _kill_room(r)
            return
    while r["phase"] in ("countdown", "question", "reveal"):
        if r["phase"] == "countdown":
            if now < r["start"]:
                return
            r["phase"] = "question"
            _bump(r)
        if r["phase"] == "question":
            cur, end = r["cur"], r["opened"] + R_Q
            for i, (t, choice) in sorted(r["plan"].items(), key=lambda x: x[1][0]):
                s = r["seats"][i]
                if t <= min(now, end) and cur not in r["ans"][i] and s and _auto(s):
                    _r_record(r, i, choice, t, True)
                    _bump(r)
            if all(cur in r["ans"][i] for i in range(R_SEATS)):
                r["closed"] = max(r["ans"][i][cur][3] for i in range(R_SEATS))
            elif now >= end:
                r["closed"] = end
            else:
                return
            _check_quiet(r)
            r["phase"] = "reveal"
            _bump(r)
        if r["phase"] == "reveal":
            if now < r["closed"] + R_REVEAL:
                return
            nxt = r["closed"] + R_REVEAL
            if r["cur"] + 1 >= N_QUESTIONS:
                _room_finish(r, nxt)
                return
            r["cur"] += 1
            r["opened"], r["closed"] = nxt, None
            r["phase"] = "question"
            _plan_bots(r)
            _bump(r)


def _check_quiet(r):
    """A human who neither answered nor polled during this question gets closer to a stand-in."""
    cur = r["cur"]
    for i in _humans(r):
        s = r["seats"][i]
        a = r["ans"][i].get(cur)
        if (a and not a[4]) or s["seen"] >= r["opened"]:
            s["missed"] = 0
        else:
            s["missed"] += 1
            if s["missed"] >= STANDIN_AFTER and not s["standin"]:
                s["standin"] = True


def _team_scores(r):
    sc, tm = [0, 0], [0, 0]
    for i in range(R_SEATS):
        for k in range(N_QUESTIONS if r["phase"] == "done" else r["cur"] + (r["closed"] is not None)):
            a = r["ans"][i].get(k)
            sc[_team(i)] += a[2] if a else 0
            tm[_team(i)] += a[1] if a else int(R_Q * 1000)
    return sc, tm


def _seat_stats(r, i):
    a = r["ans"][i].values()
    return sum(x[2] for x in a), sum(1 for x in a if x[2] > 0), sum(x[1] for x in a if x[2] > 0)


def _room_finish(r, at):
    r["phase"] = "done"
    r["done_at"] = at
    sc, tm = _team_scores(r)
    winner = 0 if sc[0] > sc[1] else 1 if sc[1] > sc[0] else (0 if tm[0] < tm[1] else 1 if tm[1] < tm[0] else -1)
    stats = [_seat_stats(r, i) for i in range(R_SEATS)]
    mvp = sorted(range(R_SEATS), key=lambda i: (-stats[i][0], stats[i][2], i))[0]
    r["result"] = {"scores": sc, "times": tm, "winner": winner, "mvp": mvp, "stats": stats}
    _bump(r)
    try:
        _room_save(r)
    except Exception as e:      # never lose the result screen over a DB hiccup
        import sys
        sys.stderr.write("live: couldn't save room %s: %s\n" % (r["code"], e))


def _room_save(r):
    L.settle_weeks()
    now = L.now_utc()
    res = r["result"]
    with L.db() as c:
        cur = c.execute("""INSERT INTO live_games (code, public, score0, score1, winner, n, week, created_utc)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (r["code"], int(r["public"]), res["scores"][0], res["scores"][1], res["winner"], N_QUESTIONS,
                         L.divisions.week_key(L.clock()), now))
        gid = cur.lastrowid
        for i, s in enumerate(r["seats"]):
            human = not s["bot"]
            mine = [(r["ids"][k], a[2] > 0) for k, a in sorted(r["ans"][i].items()) if not a[4]]
            c.execute("""INSERT INTO live_players (game_id, seat, team, player_id, name, pts, correct, bot, standin)
                         VALUES (?,?,?,?,?,?,?,?,?)""",
                      (gid, i, _team(i), s["pid"] if human else None, s["name"], res["stats"][i][0],
                       res["stats"][i][1], int(s["bot"]), int(s["standin"] or s["left"])))
            if human:
                L.log_points(c, s["pid"], "live2", sum(1 for _, ok in mine if ok), now)
                L.log_answers(c, s["pid"], mine, "live2", now)


def _r_q(r, q):
    return {"id": q["id"], "q": q["q"], "opts": _opts(r["code"] + str(r["round"]), q), "d": q["d"],
            "img": q.get("img")}


def _room_view(r, me, now):
    by_id = L.bank()[0]
    seats = []
    for i, s in enumerate(r["seats"]):
        if not s:
            seats.append(None)
            continue
        row = {"name": s["name"], "bot": s["bot"], "standin": bool(s["standin"] or s["left"]), "you": i == me,
               "host": not s["bot"] and s["pid"] == r["host"], "team": _team(i)}
        if r["phase"] != "lobby":
            row["pts"] = sum(a[2] for k, a in r["ans"][i].items()
                             if r["phase"] == "done" or k < r["cur"] or r["closed"] is not None)
            row["answered"] = r["cur"] in r["ans"][i]
        seats.append(row)
    out = {"code": r["code"], "public": r["public"], "phase": r["phase"], "v": r["v"], "you": me,
           "team": _team(me), "host": r["seats"][me]["pid"] == r["host"], "n": N_QUESTIONS, "seats": seats}
    if r["phase"] == "lobby":
        if r["public"] and r["fill_at"] is not None:
            out["ms_left"] = max(0, int((r["fill_at"] - now) * 1000))
        return out
    sc, _ = _team_scores(r)
    out["scores"] = sc
    out["cur"] = r["cur"]
    if r["phase"] == "countdown":
        out["ms_left"] = max(0, int((r["start"] - now) * 1000))
        return out
    if r["phase"] in ("question", "reveal"):
        q = by_id[r["ids"][r["cur"]]]
        out["q"] = _r_q(r, q)
        if r["phase"] == "question":
            out["ms_left"] = max(0, int((r["opened"] + R_Q - now) * 1000))
            a = r["ans"][me].get(r["cur"])
            out["mine"] = a[0] if a else None
        else:
            out["ms_left"] = max(0, int((r["closed"] + R_REVEAL - now) * 1000))
            picks = []
            for i in range(R_SEATS):
                a = r["ans"][i].get(r["cur"])
                picks.append({"choice": a[0] if a else None, "pts": a[2] if a else 0, "ms": a[1] if a else None})
            out["reveal"] = {"a": q["a"], "x": q.get("x", ""), "picks": picks}
        return out
    res = r["result"]
    t = _team(me)
    st = res["stats"]
    mates = [i for i in range(R_SEATS) if _team(i) == t]
    outcome = "draw" if res["winner"] == -1 else ("win" if res["winner"] == t else "loss")
    review = []
    for k, qid in enumerate(r["ids"]):
        q = by_id[qid]
        picks = []
        for i in range(R_SEATS):
            a = r["ans"][i].get(k)
            picks.append({"choice": a[0] if a else None, "pts": a[2] if a else 0})
        review.append({"q": q["q"], "a": q["a"], "x": q.get("x", ""), "picks": picks})
    out["result"] = {"outcome": outcome, "winner": res["winner"], "scores": res["scores"], "mvp": res["mvp"],
                     "tiebreak": res["scores"][0] == res["scores"][1] and res["winner"] != -1,
                     "correct": [s[1] for s in st], "pts": [s[0] for s in st],
                     "carry": outcome == "win" and res["mvp"] == me,
                     "flawless": all(st[i][1] == N_QUESTIONS for i in mates),
                     "review": review}
    return out


def _room_for(code, pid, now):
    code = str(code or "").upper()
    r = _rooms.get(code)
    i = _seat_of(r, pid) if r else None
    if i is None:
        raise L.NotFound("That room has closed or doesn't exist")
    s = r["seats"][i]
    r["touched"] = now
    s["seen"] = now
    if s["standin"] and not s["left"]:
        s["standin"] = False                    # welcome back: you have your seat again
        s["missed"] = 0
        r["plan"].pop(i, None)
        _bump(r)
    return r, i


def _who(data):
    name, pid = L.clean_name(data.get("name")), L.clean_pid(data.get("player_id"))
    with L.db() as c:
        L.bind_name(c, name, pid)
    return name, pid


def _code(data):
    code = re.sub(r"[\s\-]", "", str(data.get("code") or "")).upper()
    if not CODE_RE.match(code):
        raise ValueError("Codes are 6 letters and numbers - check it and try again")
    return code


def room_create(data):
    name, pid = _who(data)
    now = time.monotonic()
    with _lock:
        _room_sweep(now)
        _leave_lobby_rooms(pid, now)
        r = _room_new(pid, name, False, now)
        _seat_human(r, 0, pid, name, now)
        return {"ok": True, "code": r["code"], "seat": 0}


def room_join(data):
    name, pid = _who(data)
    code = _code(data)
    team = data.get("team")
    now = time.monotonic()
    with _lock:
        _room_sweep(now)
        r = _rooms.get(code)
        if not r:
            raise L.NotFound("No 2v2 room with that code - it may have closed")
        i = _seat_of(r, pid)
        if i is not None:
            return {"ok": True, "code": code, "seat": i}
        _room_tick(r, now)
        if code not in _rooms:
            raise L.NotFound("No 2v2 room with that code - it may have closed")
        if r["phase"] != "lobby":
            raise L.Conflict("That game has already started")
        i = _free_seat(r, team if team in (0, 1) else None)
        if i is None and team in (0, 1):
            i = _free_seat(r)
        if i is None:
            raise L.Conflict("That room is full")
        _leave_lobby_rooms(pid, now)
        _seat_human(r, i, pid, name, now)
        r["touched"] = now
        _bump(r)
        return {"ok": True, "code": code, "seat": i}


def room_quick(data):
    name, pid = _who(data)
    now = time.monotonic()
    with _lock:
        _room_sweep(now)
        code = _room_of.get(pid)
        mine = _rooms.get(code) if code else None
        if mine and mine["public"] and mine["phase"] == "lobby" and _seat_of(mine, pid) is not None:
            return {"ok": True, "code": mine["code"], "seat": _seat_of(mine, pid)}
        _leave_lobby_rooms(pid, now)
        for r in sorted(_rooms.values(), key=lambda x: x["made"]):
            if r["public"] and r["phase"] == "lobby":
                _room_tick(r, now)
            if r["code"] in _rooms and r["public"] and r["phase"] == "lobby" and _free_seat(r) is not None:
                i = _free_seat(r)
                _seat_human(r, i, pid, name, now)
                r["touched"] = now
                if len(_humans(r)) == R_SEATS:
                    _room_start(r, now)
                else:
                    _bump(r)
                return {"ok": True, "code": r["code"], "seat": i}
        r = _room_new(pid, name, True, now)
        _seat_human(r, 0, pid, name, now)
        return {"ok": True, "code": r["code"], "seat": 0}


def room_seat(data):
    pid = L.clean_pid(data.get("player_id"))
    team = data.get("team")
    if team not in (0, 1) or isinstance(team, bool):
        raise ValueError("bad team")
    now = time.monotonic()
    with _lock:
        r, i = _room_for(data.get("code"), pid, now)
        if r["phase"] != "lobby":
            raise L.Conflict("The game has started")
        if _team(i) == team:
            return {"ok": True, "seat": i}
        j = _free_seat(r, team)
        if j is None:
            raise L.Conflict("That team is full")
        r["seats"][j], r["seats"][i] = r["seats"][i], None
        _bump(r)
        return {"ok": True, "seat": j}


def room_start(data):
    pid = L.clean_pid(data.get("player_id"))
    now = time.monotonic()
    with _lock:
        r, i = _room_for(data.get("code"), pid, now)
        _room_tick(r, now)
        if r["phase"] != "lobby":
            return {"ok": True}
        if r["host"] != pid:
            raise L.Conflict("Only the host can start the game")
        if not r["public"]:
            for t in (0, 1):
                if not any(r["seats"][k] for k in (2 * t, 2 * t + 1)):
                    raise L.Conflict("Each team needs at least one player - send the link to a mate")
        _room_start(r, now)
        return {"ok": True}


def room_answer(data):
    pid = L.clean_pid(data.get("player_id"))
    idx, choice = data.get("idx"), data.get("choice")
    if isinstance(idx, bool) or not isinstance(idx, int):
        raise ValueError("bad idx")
    if choice is not None and not isinstance(choice, str):
        raise ValueError("bad choice")
    now = time.monotonic()
    with _lock:
        r, i = _room_for(data.get("code"), pid, now)
        _room_tick(r, now)
        if r["phase"] != "question" or idx != r["cur"]:
            return {"ok": False, "late": True}
        if idx in r["ans"][i]:
            return {"ok": False, "dup": True}
        _r_record(r, i, choice, now, False)
        _bump(r)
        _room_tick(r, now)
        return {"ok": True}


def room_leave(data):
    pid = L.clean_pid(data.get("player_id"))
    now = time.monotonic()
    with _lock:
        code = str(data.get("code") or "").upper()
        r = _rooms.get(code)
        i = _seat_of(r, pid) if r else None
        if i is None:
            return {"ok": True}
        _room_tick(r, now)
        if r["phase"] in ("countdown", "question", "reveal"):
            s = r["seats"][i]
            s["left"] = s["standin"] = True     # a bot plays the rest of the game for you
            if r["phase"] == "question" and r["cur"] not in r["ans"][i]:
                r["plan"][i] = _bot_pick(r, L.bank()[0][r["ids"][r["cur"]]])
            _room_of.pop(pid, None)
            if r["host"] == pid:
                hs = _humans(r)
                r["host"] = r["seats"][hs[0]]["pid"] if hs else None
        else:
            _drop_seat(r, i)
            if not _humans(r):
                _kill_room(r)
        _bump(r)
        return {"ok": True}


def room_rematch(data):
    pid = L.clean_pid(data.get("player_id"))
    now = time.monotonic()
    with _lock:
        r, i = _room_for(data.get("code"), pid, now)
        _room_tick(r, now)
        if r["phase"] == "lobby":
            return {"ok": True}
        if r["phase"] != "done":
            raise L.Conflict("The game isn't over yet")
        for k in range(R_SEATS):
            s = r["seats"][k]
            if s and (s["bot"] or s["left"] or s["standin"]):
                if s["left"] or s["standin"]:
                    _drop_seat(r, k)
                r["seats"][k] = None
        if r["host"] is None or _seat_of(r, r["host"]) is None:
            r["host"] = pid
        r["phase"], r["result"], r["ids"], r["plan"] = "lobby", None, [], {}
        r["made"] = now
        r["fill_at"] = now + QUICK_FILL if r["public"] else None
        for k in _humans(r):
            r["seats"][k]["missed"] = 0
        _bump(r)
        return {"ok": True}


def room_state(code, pid, v=None, hold=HOLD):
    """The room as `pid` sees it. With v (the version they already have) it waits for a change."""
    end = _wall() + max(0.0, min(hold, HOLD))
    with _cond:
        now = time.monotonic()
        r, i = _room_for(code, pid, now)
        _room_tick(r, now)
        if v is None or r["v"] != v or _holds[0] >= MAX_HOLDS:
            return _room_view(r, i, now)
        _holds[0] += 1
        try:
            while True:
                left = end - _wall()
                if left <= 0:
                    break
                _cond.wait(min(0.25, left))
                now = time.monotonic()
                r = _rooms.get(r["code"])
                if not r or _seat_of(r, pid) is None:
                    return {"phase": "gone"}           # left (or the room closed) while waiting
                _room_tick(r, now)
                if r["v"] != v:
                    break
            r, i = _room_for(code, pid, time.monotonic())
            return _room_view(r, i, time.monotonic())
        finally:
            _holds[0] -= 1


ROOM_POST = {"/api/live/room/create": room_create, "/api/live/room/join": room_join,
             "/api/live/room/quick": room_quick, "/api/live/room/seat": room_seat,
             "/api/live/room/start": room_start, "/api/live/room/answer": room_answer,
             "/api/live/room/leave": room_leave, "/api/live/room/rematch": room_rematch}


def room_get(params, pid):
    """GET /api/live/room for the server's router."""
    if not pid:
        raise ValueError("bad player_id")
    v = params.get("v")
    try:
        v = int(v) if v not in (None, "") else None
    except ValueError:
        raise ValueError("bad v")
    return room_state(params.get("code"), pid, v)
