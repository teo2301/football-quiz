#!/usr/bin/env python3
"""Check the game data.  python3 validate.py   (run from the repo root)

questions.json: unique ids, 3 distinct wrong answers, answer not among them, valid league/difficulty.
stats.json:     higher-or-lower items: unique ids, known category, positive number, a source note.
careers.json:   career-path players: unique ids, 2+ clubs, nationality/position, names/aliases unambiguous.
"""
import json, sys, collections, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "server"))
import modes  # noqa: E402

bad = []

# ---------------------------------------------------------------- questions
Q = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "docs/questions.json"))
ids = collections.Counter(q["id"] for q in Q)
bad += [f"duplicate id {i}" for i, n in ids.items() if n > 1]
texts = collections.Counter(q["q"] for q in Q)
bad += [f"duplicate question: {t}" for t, n in texts.items() if n > 1]
for q in Q:
    opts = [q["a"], *q["w"]]
    if len(q["w"]) != 3: bad.append(f"{q['id']}: needs 3 wrong answers")
    if len({o.casefold() for o in opts}) != 4: bad.append(f"{q['id']}: repeated option")
    if q["l"] not in {"pl", "ll", "sa", "eu", "in"}: bad.append(f"{q['id']}: bad league")
    if q["d"] not in (1, 2, 3): bad.append(f"{q['id']}: bad difficulty")
    if not q.get("x"): bad.append(f"{q['id']}: no explanation")

# ---------------------------------------------------------------- stats (higher or lower)
S = json.load(open("docs/stats.json", encoding="utf-8"))
cats = S["cats"]
sids = collections.Counter(i["id"] for i in S["items"])
bad += [f"stats: duplicate id {i}" for i, n in sids.items() if n > 1]
same = collections.Counter((i["c"], i["n"], i["d"]) for i in S["items"])
bad += [f"stats: duplicate item {k}" for k, n in same.items() if n > 1]
for i in S["items"]:
    if i["c"] not in cats: bad.append(f"stats {i['id']}: unknown category {i['c']}")
    if isinstance(i["v"], bool) or not isinstance(i["v"], (int, float)) or i["v"] <= 0: bad.append(f"stats {i['id']}: bad value")
    for k in ("n", "d", "note"):
        if not str(i.get(k) or "").strip(): bad.append(f"stats {i['id']}: missing {k}")
per_cat = collections.Counter(i["c"] for i in S["items"])
for c in cats:
    vals = [i["v"] for i in S["items"] if i["c"] == c]
    # the game only pairs numbers at least 10% apart; each category needs plenty of those
    pairs = sum(1 for a in vals for b in vals if a < b and (b - a) / b >= 0.1)
    if per_cat[c] < 8 or pairs < 20: bad.append(f"stats: category {c} too small ({per_cat[c]} items, {pairs} pairs)")

# ---------------------------------------------------------------- careers
C = json.load(open("docs/careers.json", encoding="utf-8"))["players"]
cids = collections.Counter(p["id"] for p in C)
bad += [f"careers: duplicate id {i}" for i, n in cids.items() if n > 1]
owner = collections.defaultdict(set)
for p in C:
    for k in [modes.key(p["n"])] + [modes.key(a) for a in p.get("al", [])]:
        owner[k].add(p["n"])
    if len(p["clubs"]) < 2: bad.append(f"careers {p['id']}: needs 2+ clubs")
    if p["pos"] not in ("Goalkeeper", "Defender", "Midfielder", "Forward"): bad.append(f"careers {p['id']}: bad position")
    if not p.get("nat"): bad.append(f"careers {p['id']}: no nationality")
    for a, b in zip(p["clubs"], p["clubs"][1:]):
        if a == b: bad.append(f"careers {p['id']}: {a} twice in a row")
    for c in p["clubs"]:
        if not c.strip() or ("(" in c and not c.endswith(" (loan)")): bad.append(f"careers {p['id']}: odd club '{c}'")
bad += [f"careers: '{k}' is a name or alias of {sorted(v)}" for k, v in owner.items() if len(v) > 1]
forms = modes.build_forms(C)
for p in C:
    if not modes.is_match(p["n"], forms[p["id"]]): bad.append(f"careers {p['id']}: own name doesn't match")

print("\n".join(bad) or "OK")
print(len(Q), "questions:", dict(collections.Counter(q["l"] for q in Q)), "difficulty", dict(sorted(collections.Counter(q["d"] for q in Q).items())))
print(len(S["items"]), "higher-or-lower items:", dict(per_cat))
print(len(C), "career-path players")
sys.exit(1 if bad else 0)
