"""Live 1v1 matches + the ranked 1v1 league (standard library only; identical in both quizzes).

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
