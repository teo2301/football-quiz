#!/usr/bin/env python3
"""Tests for leaderboard.py against a throwaway DB.  python3 server/test_leaderboard.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="fq-test-")
os.environ["QUIZ_DB"] = os.path.join(TMP, "test.db")
os.environ["QUESTIONS_PATH"] = os.path.join(ROOT, "docs", "questions.json")
sys.path.insert(0, HERE)
import daily_gen  # noqa: E402
import leaderboard as L  # noqa: E402

BANK = json.load(open(os.environ["QUESTIONS_PATH"], encoding="utf-8"))
BY_ID = {q["id"]: q for q in BANK}


def pid():
    return str(uuid.uuid4())


def answers(qs, right):
    """right = list of bools; wrong answers pick the first wrong option."""
    return [{"id": q["id"], "choice": q["a"] if ok else q["w"][0]} for q, ok in zip(qs, right)]


class Base(unittest.TestCase):
    def setUp(self):
        if os.path.exists(os.environ["QUIZ_DB"]):
            os.remove(os.environ["QUIZ_DB"])
        L.init()


class Classic(Base):
    def test_old_shape_still_accepted(self):
        r = L.add_score({"name": "Old Client", "total": 10, "right": {"1": 2, "2": 3, "3": 1},
                         "league": "all", "diff": 0, "timer": 0, "streak": 4})
        self.assertEqual(r["points"], 2 + 6 + 3)
        self.assertEqual(r["rank_week"], 1)
        self.assertEqual(L.board(10, "week")[0]["name"], "Old Client")

    def test_new_shape_server_recomputes_forged_points(self):
        qs = [q for q in BANK if q["l"] == "pl"][:10]
        right = [True, False] * 5
        forged = {"name": "Forger", "player_id": pid(), "total": 10, "league": "pl", "diff": 0, "timer": 10,
                  "right": {"1": 0, "2": 0, "3": 10}, "streak": 10, "points": 999,
                  "answers": answers(qs, right)}
        r = L.add_score(forged)
        expect = sum(q["d"] for q, ok in zip(qs, right) if ok) * 1.5
        self.assertEqual(r["correct"], 5)
        self.assertEqual(r["streak"], 1)
        self.assertAlmostEqual(r["points"], round(expect, 1))
        with L.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*), SUM(correct) FROM answers WHERE mode='classic'").fetchone()[:], (10, 5))

    def test_new_shape_rejects_mismatched_questions(self):
        qs = [q for q in BANK if q["l"] == "ll"][:10]
        with self.assertRaises(ValueError):
            L.add_score({"name": "Sneaky", "player_id": pid(), "total": 10, "league": "pl", "diff": 0,
                         "timer": 0, "answers": answers(qs, [True] * 10)})
        with self.assertRaises(ValueError):    # same question 10 times
            L.add_score({"name": "Sneaky", "player_id": pid(), "total": 10, "league": "all", "diff": 0,
                         "timer": 0, "answers": answers([qs[0]] * 10, [True] * 10)})


class Names(Base):
    def test_name_squatting_rejected(self):
        me, other = pid(), pid()
        L.set_name({"name": "Matteo", "player_id": me})
        with self.assertRaises(L.Conflict) as cm:
            L.set_name({"name": "matteo", "player_id": other})
        self.assertIn("taken", str(cm.exception))
        qs = BANK[:10]
        with self.assertRaises(L.Conflict):
            L.add_score({"name": "MATTEO", "player_id": other, "total": 10, "league": "all", "diff": 0,
                         "timer": 0, "answers": answers(qs, [True] * 10)})
        # an old client (no player_id) can't post under a claimed name either
        with self.assertRaises(ValueError):
            L.add_score({"name": "Matteo", "total": 10, "right": {"1": 1}, "league": "all",
                         "diff": 0, "timer": 0, "streak": 1})
        # but the owner can
        L.add_score({"name": "Matteo", "player_id": me, "total": 10, "league": "all", "diff": 0,
                     "timer": 0, "answers": answers(qs, [True] * 10)})

    def test_rename_same_device(self):
        me = pid()
        L.set_name({"name": "Teo", "player_id": me})
        L.set_name({"name": "Teo Two", "player_id": me})
        L.set_name({"name": "Teo", "player_id": pid()})    # old name is free again
        with L.db() as c:
            self.assertEqual(c.execute("SELECT name FROM players WHERE player_id=?", (me,)).fetchone()[0], "Teo Two")

    def test_profanity_rejected(self):
        for bad in ["fuck", "Sh1t head", "c4zz0", "Puttana", "Gilipollas", "fuuuuck", "ass", "hijo de puta"]:
            with self.assertRaises(ValueError, msg=bad):
                L.clean_name(bad)
        for ok in ["Cazorla", "Cassano", "Dickson", "Scunthorpe fan", "Nigeria", "Matteo", "Luca 7"]:
            self.assertEqual(L.clean_name(ok), ok)

    def test_no_bank_option_is_flagged(self):
        opts = {o for q in BANK for o in [q["a"]] + q["w"]}
        self.assertEqual([o for o in opts if L.is_rude(o)], [])


class Daily(Base):
    def post(self, player, right, d=None, name="Daily Dan"):
        d = d or daily_gen.uk_today()
        ids = L.bank()[1].ids(d)
        return L.add_daily({"name": name, "player_id": player, "date": d.isoformat(),
                            "answers": answers([BY_ID[i] for i in ids], right)})

    def test_one_attempt_per_day(self):
        me = pid()
        r = self.post(me, [True, True, False, True, True, True, False])
        self.assertEqual((r["correct"], r["total"], r["grid"], r["streak"], r["rank_today"]),
                         (5, 7, "1101110", 1, 1))
        with self.assertRaises(L.Conflict):
            self.post(me, [True] * 7)
        b = L.public_board("today")
        self.assertEqual(len(b["rows"]), 1)
        self.assertNotIn("player_id", b["rows"][0])

    def test_streak_and_week(self):
        me = pid()
        today = daily_gen.uk_today()
        self.post(me, [True] * 7, today - timedelta(days=1))
        r = self.post(me, [False] * 7, today)
        self.assertEqual(r["streak"], 2)
        week = L.public_board("week")["rows"][0]
        self.assertEqual((week["correct"], week["days"], week["streak"]), (7, 2, 2))

    def test_wrong_ids_or_old_date_rejected(self):
        d = daily_gen.uk_today()
        with self.assertRaises(ValueError):
            L.add_daily({"name": "X Y", "player_id": pid(), "date": d.isoformat(),
                         "answers": answers(BANK[:7], [True] * 7)})
        with self.assertRaises(ValueError):
            self.post(pid(), [True] * 7, d - timedelta(days=5))

    def test_no_repeats_within_60_days_and_mix(self):
        p = daily_gen.DailyPicker(BANK)
        start = date(2026, 12, 1)
        days = [p.ids(start + timedelta(days=i)) for i in range(400)]
        for i, ids in enumerate(days):
            self.assertEqual([BY_ID[q]["d"] for q in ids], list(daily_gen.PLAN))
            window = {q for past in days[max(0, i - 60):i] for q in past}
            self.assertFalse(window & set(ids), f"repeat on day {i}")
            self.assertEqual(len(set(ids)), 7)

    def test_python_and_js_pick_the_same(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        dates = [(date(2026, 12, 1) + timedelta(days=i)).isoformat() for i in range(30)]
        dates += ["2026-11-01", "2027-03-28", "2027-10-31", "2028-02-29"]
        js = ("const D=require(%s);const b=require(%s);const p=new D.Picker(b);"
              "console.log(JSON.stringify(%s.map(d=>[d,D.number(d),p.ids(d)])))") % (
            json.dumps(os.path.join(ROOT, "docs", "js", "daily-gen.js")),
            json.dumps(os.environ["QUESTIONS_PATH"]), json.dumps(dates))
        out = json.loads(subprocess.check_output([node, "-e", js]))
        p = daily_gen.DailyPicker(BANK)
        py = [[d, daily_gen.number(date.fromisoformat(d)), p.ids(date.fromisoformat(d))] for d in dates]
        self.assertEqual(out, py)
        self.assertEqual(out[0][1], 1)          # 2026-12-01 is #1

    def test_uk_date_both_sides(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        # around both 2027 clock changes and midnight
        moments = ["2027-03-27T23:30:00", "2027-03-28T00:30:00", "2027-03-28T23:30:00",
                   "2027-10-30T22:59:00", "2027-10-30T23:01:00", "2027-10-31T00:30:00", "2027-07-01T23:30:00"]
        ms = [int(datetime.fromisoformat(m).replace(tzinfo=timezone.utc).timestamp() * 1000) for m in moments]
        js = "const D=require(%s);console.log(JSON.stringify(%s.map(m=>D.ukToday(m))))" % (
            json.dumps(os.path.join(ROOT, "docs", "js", "daily-gen.js")), json.dumps(ms))
        out = json.loads(subprocess.check_output([node, "-e", js]))
        py = [daily_gen.uk_today(datetime.fromisoformat(m).replace(tzinfo=timezone.utc)).isoformat() for m in moments]
        self.assertEqual(out, py)
        self.assertEqual(py, ["2027-03-27", "2027-03-28", "2027-03-29", "2027-10-30", "2027-10-31",
                              "2027-10-31", "2027-07-02"])


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
