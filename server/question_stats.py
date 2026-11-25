#!/usr/bin/env python3
"""Question quality summary for the Football Quiz (standard library only).

    python3 server/question_stats.py [--db PATH] [--questions PATH] [--min N] [--json]
    python3 server/question_stats.py --resolve QID [--db PATH]     # mark a question's open reports done

Lists, for manual re-grading (nothing is ever edited automatically):
  * questions whose real correct rate disagrees with their difficulty, from the `answers`
    table: Easy answered right by fewer than 40%, Hard by more than 85% (at least --min
    answers, default 20);
  * open player reports (POST /api/report), grouped by question, with reasons and notes.

The database is opened READ-ONLY unless --resolve is given.
Defaults: --db $QUIZ_DB or data/leaderboard.db, --questions docs/questions.json (repo-relative).
"""
import argparse
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIFF = {1: "Easy", 2: "Medium", 3: "Hard"}
EASY_BELOW = 0.40
HARD_ABOVE = 0.85
REASON = {"wrong": "wrong answer", "outdated": "outdated", "typo": "typo", "other": "other"}


def connect(path, write=False):
    if not os.path.exists(path):
        sys.exit("no database at %s" % path)
    if write:
        return sqlite3.connect(path, timeout=10)
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=10)


def has_table(c, name):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def misgraded(c, by_id, min_n):
    """[(qid, difficulty, n, rate, suggestion)] sorted worst first."""
    if not has_table(c, "answers"):
        return []
    out = []
    for qid, n, right in c.execute("SELECT qid, COUNT(*), SUM(correct) FROM answers GROUP BY qid HAVING COUNT(*) >= ?",
                                   (min_n,)):
        q = by_id.get(qid)
        if not q:
            continue
        rate = right / n
        if q["d"] == 1 and rate < EASY_BELOW:
            out.append((qid, q["d"], n, rate, "Medium or Hard?", EASY_BELOW - rate))
        elif q["d"] == 3 and rate > HARD_ABOVE:
            out.append((qid, q["d"], n, rate, "Medium or Easy?", rate - HARD_ABOVE))
    out.sort(key=lambda r: -r[5])
    return [r[:5] for r in out]


def open_reports(c):
    """{qid: [(reason, note, created_utc)]} newest first, questions with most reports first."""
    if not has_table(c, "reports"):
        return []
    by = {}
    for qid, reason, note, ts in c.execute(
            "SELECT qid, reason, note, created_utc FROM reports WHERE status = 'open' ORDER BY created_utc DESC"):
        by.setdefault(qid, []).append((reason, note, ts))
    return sorted(by.items(), key=lambda kv: (-len(kv[1]), kv[0]))


def summary(db_path, questions_path, min_n=20):
    with open(questions_path, encoding="utf-8") as f:
        by_id = {q["id"]: q for q in json.load(f)}
    c = connect(db_path)
    try:
        total = c.execute("SELECT COUNT(*) FROM answers").fetchone()[0] if has_table(c, "answers") else 0
        return {"answers": total, "questions": len(by_id),
                "misgraded": [{"id": qid, "difficulty": DIFF[d], "answers": n, "correct_rate": round(rate, 3),
                               "suggest": sug, "q": by_id[qid]["q"], "a": by_id[qid]["a"]}
                              for qid, d, n, rate, sug in misgraded(c, by_id, min_n)],
                "reports": [{"id": qid, "count": len(rs), "q": (by_id.get(qid) or {}).get("q", "(question removed)"),
                             "a": (by_id.get(qid) or {}).get("a", ""),
                             "items": [{"reason": r, "note": n, "at": ts} for r, n, ts in rs]}
                            for qid, rs in open_reports(c)]}
    finally:
        c.close()


def as_text(s, min_n=20, short=False):
    """Plain text. short=True keeps it phone-notification sized (used by tools/weekly_report.sh)."""
    lines = []
    lines.append("%d open report%s on %d question%s; %d misgraded (of %d answers logged)." % (
        sum(r["count"] for r in s["reports"]), "" if sum(r["count"] for r in s["reports"]) == 1 else "s",
        len(s["reports"]), "" if len(s["reports"]) == 1 else "s", len(s["misgraded"]), s["answers"]))
    cap = 8 if short else None
    if s["reports"]:
        lines.append("")
        lines.append("REPORTS")
        for r in s["reports"][:cap]:
            reasons = {}
            for it in r["items"]:
                reasons[REASON.get(it["reason"], it["reason"])] = reasons.get(REASON.get(it["reason"], it["reason"]), 0) + 1
            lines.append("%s (x%d: %s) %s  [answer: %s]" % (
                r["id"], r["count"], ", ".join("%s %d" % kv for kv in sorted(reasons.items())),
                r["q"] if not short else r["q"][:90], r["a"]))
            for it in r["items"][:3 if short else None]:
                if it["note"]:
                    lines.append('    "%s"' % (it["note"][:120] if short else it["note"]))
        if cap and len(s["reports"]) > cap:
            lines.append("... and %d more (run server/question_stats.py)" % (len(s["reports"]) - cap))
    if s["misgraded"]:
        lines.append("")
        lines.append("MISGRADED (Easy < %d%%, Hard > %d%%, %d+ answers)" % (EASY_BELOW * 100, HARD_ABOVE * 100, min_n))
        for m in s["misgraded"][:cap]:
            lines.append("%s %s %d%% of %d -> %s  %s" % (m["id"], m["difficulty"], round(m["correct_rate"] * 100),
                                                       m["answers"], m["suggest"], m["q"] if not short else m["q"][:90]))
        if cap and len(s["misgraded"]) > cap:
            lines.append("... and %d more" % (len(s["misgraded"]) - cap))
    return "\n".join(lines)


def resolve(db_path, qid):
    c = connect(db_path, write=True)
    with c:
        n = c.execute("UPDATE reports SET status = 'done' WHERE qid = ? AND status = 'open'", (qid,)).rowcount
    c.close()
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=os.environ.get("QUIZ_DB") or os.path.join(ROOT, "data", "leaderboard.db"))
    ap.add_argument("--questions", default=os.path.join(ROOT, "docs", "questions.json"))
    ap.add_argument("--min", type=int, default=20, help="answers needed before a question is judged (default 20)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--short", action="store_true", help="notification-sized text")
    ap.add_argument("--resolve", metavar="QID", help="mark this question's open reports as done")
    a = ap.parse_args(argv)
    if a.resolve:
        print("%d report(s) marked done for %s" % (resolve(a.db, a.resolve), a.resolve))
        return 0
    s = summary(a.db, a.questions, a.min)
    print(json.dumps(s, ensure_ascii=False, indent=1) if a.json else as_text(s, a.min, a.short))
    return 0


if __name__ == "__main__":
    sys.exit(main())
