#!/usr/bin/env python3
"""Check questions.json: unique ids, 3 distinct wrong answers, answer not among them, valid league/difficulty."""
import json, sys, collections
Q = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "docs/questions.json"))
bad = []
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
print("\n".join(bad) or "OK")
print(len(Q), "questions:", dict(collections.Counter(q["l"] for q in Q)), "difficulty", dict(sorted(collections.Counter(q["d"] for q in Q).items())))
sys.exit(1 if bad else 0)
