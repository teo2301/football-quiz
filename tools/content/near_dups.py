#!/usr/bin/env python3
"""Near-duplicate check for a batch of new questions against the bank (and each other).

    python3 tools/content/near_dups.py NEW.py-or-json [--all]

Scores each new question against every existing one: word overlap (Jaccard on content
words) plus a bump when the answers are the same. Prints anything above the threshold
so a human (or the writer) can drop or rewrite it."""
import json, os, re, sys, unicodedata, importlib.util
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
STOP = set("""a an the of in to for and or on at by with from who which what whose how many much was were is are
did does do has had have be been this that these those his her their its it as into after before during than
then first last only one two three season seasons club clubs team teams player players league game games goal goals
scored score won win winning final finals cup year years s which's""".split())


def words(t):
    t = unicodedata.normalize("NFKD", t.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return {w for w in re.findall(r"[a-z0-9]+", t) if w not in STOP and len(w) > 1}


def score(a, b):
    wa, wb = words(a["q"]), words(b["q"])
    j = len(wa & wb) / max(1, len(wa | wb))
    if a["a"].casefold() == b["a"].casefold():
        j += 0.25
    return j


def load_new(path):
    if path.endswith(".json"):
        return json.load(open(path, encoding="utf-8"))
    spec = importlib.util.spec_from_file_location("batch", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return [{"l": l, "q": q, "a": a, "w": w, "x": x, "d": d} for (l, d, q, a, w, x) in m.NEW]


def main():
    new = load_new(sys.argv[1])
    bank = json.load(open(os.path.join(ROOT, "docs", "questions.json"), encoding="utf-8"))
    thr = 0.0 if "--all" in sys.argv else 0.42
    hits = 0
    for i, n in enumerate(new):
        pool = [(score(n, b), b.get("id", "?"), b) for b in bank] + \
               [(score(n, m), "new#%d" % k, m) for k, m in enumerate(new) if k != i]
        s, bid, b = max(pool, key=lambda t: t[0])
        if s >= thr:
            hits += 1
            print("%.2f  new#%d %s\n      %s %s\n" % (s, i, n["q"] + " [" + n["a"] + "]", bid, b["q"] + " [" + b["a"] + "]"))
    print(hits, "possible near-duplicates of", len(new))


if __name__ == "__main__":
    main()
