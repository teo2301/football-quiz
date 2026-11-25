"""Server-side marking for the Phase 2 modes: survival, blitz, higher or lower
and career path. Classic and daily stay in leaderboard.py.

The guess matcher MUST stay identical to docs/js/match.js
(server/test_leaderboard.py runs both over the same guesses).
"""
import json
import os
import re
import threading
import unicodedata

# ---------------------------------------------------------------- data files

_cache = {}
_cache_lock = threading.Lock()


def load(path, build=None):
    """JSON file, reloaded when it changes; `build(data)` derives extra lookups once per load."""
    with _cache_lock:
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            raise ValueError("game data unavailable on the server")
        hit = _cache.get(path)
        if not hit or hit[0] != mtime:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            hit = (mtime, build(data) if build else data)
            _cache[path] = hit
        return hit[1]


# ---------------------------------------------------------------- name matching

# Letters that NFD doesn't split into base + accent.
SPECIAL = {"ø": "o", "ß": "ss", "ı": "i", "ł": "l", "đ": "d", "æ": "ae", "œ": "oe", "ð": "d", "þ": "th"}
COMBINING = re.compile("[̀-ͯ]")


def words(s):
    """'Mesut Özil' -> ['mesut', 'ozil']; apostrophes vanish, other punctuation splits."""
    s = COMBINING.sub("", unicodedata.normalize("NFD", str(s or "").lower()))
    s = "".join(SPECIAL.get(ch, ch) for ch in s)
    s = re.sub(r"['’`]", "", s)
    return [w for w in re.split(r"[^a-z0-9]+", s) if w]


def key(s):
    return "".join(words(s))


def auto_forms(name):
    """Full name, the name without its first word ('van persie'), and the surname if 4+ letters."""
    w = words(name)
    out = ["".join(w)]
    if len(w) > 1:
        out.append("".join(w[1:]))
        if len(w[-1]) >= 4:
            out.append(w[-1])
    return out


def build_forms(players):
    """id -> set of accepted keys. Explicit names/aliases always count; automatic
    short forms are dropped when another player could claim them too."""
    explicit, auto, owners = {}, {}, {}
    for p in players:
        ex = {key(p["n"])} | {key(a) for a in p.get("al", [])}
        au = set(auto_forms(p["n"])) - ex
        explicit[p["id"]], auto[p["id"]] = ex, au
        for k in ex | au:
            owners.setdefault(k, set()).add(p["id"])
    return {pid: explicit[pid] | {k for k in auto[pid] if len(owners[k]) == 1} for pid in explicit}


def lev(a, b, cap):
    """Levenshtein distance, giving up (returns cap + 1) once it must exceed cap."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        best = cur[0]
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
            best = min(best, cur[j])
        if best > cap:
            return cap + 1
        prev = cur
    return prev[len(b)]


def is_match(guess, forms):
    g = key(guess)
    if not g:
        return False
    for f in forms:
        if g == f:
            return True
        cap = 2 if len(f) >= 9 else 1 if len(f) >= 5 else 0
        if cap and lev(g, f, cap) <= cap:
            return True
    return False


def career_points(clue):
    """Guessed after `clue` clues were showing (1 = first club only): 5, 4, 3, 2, then 1."""
    return max(1, 6 - clue)


def clue_count(p):
    return len(p["clubs"]) + 2          # + nationality + position


# ---------------------------------------------------------------- marking

SURVIVAL_MAX = 400
BLITZ_MAX = 80          # 60 seconds; a wrong answer costs 3 of them
CAREER_LEN = 5


def as_list(v, lo, hi):
    if not isinstance(v, list) or not lo <= len(v) <= hi:
        raise ValueError("bad answers")
    return v


def survival(marks):
    """marks from leaderboard.mark(): every answer right except, at most, the last."""
    if any(not ok for _, ok in marks[:-1]):
        raise ValueError("a survival run ends at the first wrong answer")
    correct = sum(1 for _, ok in marks if ok)
    return correct, correct, len(marks)


def blitz(marks):
    correct = sum(1 for _, ok in marks if ok)
    return correct, correct, len(marks)


def stats_index(doc):
    return {"by_id": {i["id"]: i for i in doc["items"]}}


def hilo(answers, stats_path):
    """[{a, b, g: 'hi'|'lo'}]: is b's number higher or lower than a's? Same category,
    each b used once; the run ends at the first wrong call."""
    by_id = load(stats_path, stats_index)["by_id"]
    as_list(answers, 1, len(by_id))
    seen, marks = set(), []
    for x in answers:
        if not isinstance(x, dict):
            raise ValueError("bad answers")
        a, b = by_id.get(x.get("a")), by_id.get(x.get("b"))
        if not a or not b or a is b or a["c"] != b["c"] or b["id"] in seen or x.get("g") not in ("hi", "lo"):
            raise ValueError("unknown or repeated pair - reload the app")
        seen.add(b["id"])
        ok = b["v"] == a["v"] or (b["v"] > a["v"]) == (x["g"] == "hi")
        marks.append((b["id"], ok))
    score, correct, total = survival(marks)
    return score, correct, total, marks


def careers_index(doc):
    players = doc["players"]
    return {"by_id": {p["id"]: p for p in players}, "forms": build_forms(players)}


def career(answers, careers_path):
    """[{id, guess, clues}] for 5 different players -> points 5..1 per right guess."""
    idx = load(careers_path, careers_index)
    as_list(answers, CAREER_LEN, CAREER_LEN)
    seen, marks, points = set(), [], 0
    for x in answers:
        if not isinstance(x, dict):
            raise ValueError("bad answers")
        p = idx["by_id"].get(x.get("id"))
        if not p or p["id"] in seen:
            raise ValueError("unknown or repeated player - reload the app")
        seen.add(p["id"])
        clues, guess = x.get("clues"), x.get("guess")
        if isinstance(clues, bool) or not isinstance(clues, int) or not 1 <= clues <= clue_count(p):
            raise ValueError("bad clues")
        if guess is not None and (not isinstance(guess, str) or len(guess) > 60):
            raise ValueError("bad guess")
        ok = bool(guess) and is_match(guess, idx["forms"][p["id"]])
        if ok:
            points += career_points(clues)
        marks.append((p["id"], ok))
    return points, sum(1 for _, ok in marks if ok), len(marks), marks
