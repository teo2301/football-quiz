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
import modes as M  # noqa: E402

BANK = json.load(open(os.environ["QUESTIONS_PATH"], encoding="utf-8"))
BY_ID = {q["id"]: q for q in BANK}
STATS = json.load(open(os.path.join(ROOT, "docs", "stats.json"), encoding="utf-8"))
CAREERS = json.load(open(os.path.join(ROOT, "docs", "careers.json"), encoding="utf-8"))["players"]


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


class FiftyFifty(Base):
    me = str(uuid.uuid4())

    def classic(self, qs, right, fifty_at=(), **kw):
        ans = answers(qs, right)
        for i in fifty_at:
            ans[i]["fifty"] = True
        body = {"name": kw.get("name", "Half Time"), "player_id": kw.get("pid") or self.me, "total": 10,
                "league": "all", "diff": 3, "timer": kw.get("timer", 0), "answers": ans, "mode": "classic"}
        return L.add_score(body)

    def test_fifty_halves_that_question(self):
        qs = [q for q in BANK if q["d"] == 3][:10]
        self.assertEqual(self.classic(qs, [True] * 10)["points"], 30)
        self.assertEqual(self.classic(qs, [True] * 10, fifty_at=[4])["points"], 28.5)
        self.assertEqual(self.classic(qs, [True] * 10, fifty_at=[4], timer=10)["points"], round(28.5 * 1.5, 1))
        # a 50/50 on a question you then got wrong costs nothing extra
        self.assertEqual(self.classic(qs, [True] * 9 + [False], fifty_at=[9])["points"], 27)

    def test_only_one_fifty(self):
        qs = [q for q in BANK if q["d"] == 3][:10]
        with self.assertRaises(ValueError):
            self.classic(qs, [True] * 10, fifty_at=[1, 2])

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            L.add_score({"mode": "cheat", "name": "Abc", "player_id": pid(), "answers": []})


class Runs(Base):
    def post_run(self, mode, ans, name="Runner", player=None):
        return L.add_score({"mode": mode, "name": name, "player_id": player or pid(), "answers": ans})

    def test_survival_scoring_and_board(self):
        me = pid()
        r = self.post_run("survival", answers(BANK[:13], [True] * 12 + [False]), player=me)
        self.assertEqual((r["score"], r["correct"], r["total"], r["rank_all"]), (12, 12, 13, 1))
        r2 = self.post_run("survival", answers(BANK[20:26], [True] * 6), name="Other")    # ran out of questions / all right
        self.assertEqual((r2["score"], r2["rank_week"]), (6, 2))
        self.post_run("survival", answers(BANK[30:33], [True, True, False]), player=me)    # worse run: best stays
        b = L.public_run_board("survival", "week")
        self.assertEqual([(x["name"], x["score"]) for x in b["rows"]], [("Runner", 12), ("Other", 6)])
        self.assertNotIn("player_id", b["rows"][0])
        with L.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM answers WHERE mode='survival'").fetchone()[0], 22)

    def test_survival_rejects_play_on_after_a_miss(self):
        with self.assertRaises(ValueError):
            self.post_run("survival", answers(BANK[:5], [True, False, True, True, True]))
        with self.assertRaises(ValueError):    # repeated question
            self.post_run("survival", answers([BANK[0], BANK[0]], [True, True]))

    def test_blitz(self):
        r = self.post_run("blitz", answers(BANK[:20], [True, False] * 10))
        self.assertEqual((r["score"], r["correct"], r["total"]), (10, 10, 20))
        with self.assertRaises(ValueError):
            self.post_run("blitz", answers(BANK[:M.BLITZ_MAX + 1], [True] * (M.BLITZ_MAX + 1)))
        with self.assertRaises(ValueError):
            self.post_run("blitz", [])

    def hilo_pairs(self, n):
        by_cat = {}
        for i in STATS["items"]:
            by_cat.setdefault(i["c"], []).append(i)
        items = by_cat["caps"]
        return [(items[0], items[k]) for k in range(1, n + 1)]

    def call(self, a, b, right=True):
        hi = b["v"] > a["v"]
        return {"a": a["id"], "b": b["id"], "g": ("hi" if hi else "lo") if right else ("lo" if hi else "hi")}

    def test_hilo(self):
        pairs = [(a, b) for a, b in self.hilo_pairs(12) if a["v"] != b["v"]][:6]
        ans = [self.call(a, b) for a, b in pairs[:5]] + [self.call(*pairs[5], right=False)]
        r = self.post_run("hilo", ans)
        self.assertEqual((r["score"], r["total"]), (5, 6))
        with self.assertRaises(ValueError):    # kept going after a wrong call
            self.post_run("hilo", [self.call(*pairs[0], right=False), self.call(*pairs[1])])
        with self.assertRaises(ValueError):    # same b twice
            self.post_run("hilo", [self.call(*pairs[0]), self.call(*pairs[0])])
        other = next(i for i in STATS["items"] if i["c"] == "stadiums")
        with self.assertRaises(ValueError):    # different categories
            self.post_run("hilo", [{"a": pairs[0][0]["id"], "b": other["id"], "g": "hi"}])

    def test_hilo_equal_values_count_either_way(self):
        by = {}
        for i in STATS["items"]:
            by.setdefault((i["c"], i["v"]), []).append(i)
        a, b = next(v for v in by.values() if len(v) > 1)[:2]
        self.assertEqual(self.post_run("hilo", [{"a": a["id"], "b": b["id"], "g": "lo"}])["score"], 1)

    def test_career_points_and_matching(self):
        by_name = {p["n"]: p for p in CAREERS}
        ans = [{"id": by_name["Mesut Özil"]["id"], "guess": "mesut ozil", "clues": 1},          # 5
               {"id": by_name["Robin van Persie"]["id"], "guess": "Van Persie", "clues": 2},    # 4
               {"id": by_name["Zlatan Ibrahimović"]["id"], "guess": "Ibrahimovic", "clues": 3},  # 3
               {"id": by_name["Thierry Henry"]["id"], "guess": "Thiery Henri", "clues": 9 - 1},  # typo x2 -> 1
               {"id": by_name["Wayne Rooney"]["id"], "guess": "Gerrard", "clues": 2}]           # wrong -> 0
        r = self.post_run("career", ans)
        self.assertEqual((r["score"], r["correct"], r["total"]), (5 + 4 + 3 + 1, 4, 5))
        with self.assertRaises(ValueError):
            self.post_run("career", ans[:4])
        with self.assertRaises(ValueError):    # more clues than the player has
            self.post_run("career", ans[:4] + [{"id": ans[4]["id"], "guess": "x", "clues": 99}])

    def test_career_forms(self):
        forms = M.build_forms(CAREERS)
        by_name = {p["n"]: p["id"] for p in CAREERS}
        ok = lambda n, g: M.is_match(g, forms[by_name[n]])
        self.assertTrue(ok("Cristiano Ronaldo", "Cristiano"))
        self.assertTrue(ok("Cristiano Ronaldo", "CR7"))
        self.assertFalse(ok("Cristiano Ronaldo", "Ronaldo"))      # that's R9's
        self.assertTrue(ok("Ronaldo Nazário", "ronaldo"))
        self.assertFalse(ok("Yaya Touré", "Toure"))               # two Tourés
        self.assertTrue(ok("Yaya Touré", "Yaya Toure"))
        self.assertFalse(ok("Yaya Touré", "Kolo Toure"))
        self.assertFalse(ok("Ashley Cole", "Cole"))
        self.assertTrue(ok("Petr Čech", "Cech"))
        self.assertTrue(ok("Ole Gunnar Solskjær", "solskjaer"))
        self.assertTrue(ok("Shaun Wright-Phillips", "wright phillips"))
        self.assertTrue(ok("İlkay Gündoğan", "gundogan"))
        self.assertFalse(ok("Kaká", "Kaku"))                      # 4 letters: exact only
        self.assertFalse(ok("Lionel Messi", ""))

    def test_python_and_js_match_the_same(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        probes = ["Cristiano", "ronaldo", "Toure", "yaya toure", "Ozil", "van persie", "Ibrahimovic", "Thiery Henri",
                  "cech", "Solskjaer", "wright-phillips", "GUNDOGAN", "Kaka", "kaku", "de bruyne", "Kevin De Bruine",
                  "Hasselbank", "N'Golo", "Raul", "iniesta", "Di Maria", "del piero", "Lukaku", "  "]
        players = [p for p in CAREERS]
        forms = M.build_forms(players)
        py = {p["id"]: [M.is_match(g, forms[p["id"]]) for g in probes] for p in players}
        js = ("const F=require(%s);const ps=require(%s).players;const f=F.buildForms(ps);const g=%s;"
              "const o={};ps.forEach(p=>{o[p.id]=g.map(x=>F.isMatch(x,f[p.id]))});console.log(JSON.stringify(o))") % (
            json.dumps(os.path.join(ROOT, "docs", "js", "match.js")),
            json.dumps(os.path.join(ROOT, "docs", "careers.json")), json.dumps(probes))
        out = json.loads(subprocess.check_output([node, "-e", js]))
        self.assertEqual(out, py)
        self.assertEqual({k: sorted(v) for k, v in forms.items()},
                         {k: sorted(v) for k, v in json.loads(subprocess.check_output([node, "-e",
                          "const F=require(%s);const f=F.buildForms(require(%s).players);const o={};"
                          "Object.keys(f).forEach(k=>o[k]=Object.keys(f[k]));console.log(JSON.stringify(o))" % (
                              json.dumps(os.path.join(ROOT, "docs", "js", "match.js")),
                              json.dumps(os.path.join(ROOT, "docs", "careers.json")))])).items()})

    def test_run_board_http_shape(self):
        self.post_run("blitz", answers(BANK[:3], [True] * 3))
        b = L.public_run_board("blitz", "all")
        self.assertEqual(set(b["rows"][0]), {"name", "score", "correct", "total", "created_utc"})


class Content(unittest.TestCase):
    def test_validate_passes(self):
        out = subprocess.run([sys.executable, os.path.join(ROOT, "validate.py")], cwd=ROOT,
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_enough_hard_for_two_a_day(self):
        hard = sum(1 for q in BANK if q["d"] == 3)
        self.assertGreaterEqual(hard, 2 * daily_gen.NO_REPEAT + 1)
        self.assertEqual(sorted(daily_gen.PLAN), [1, 1, 2, 2, 2, 3, 3])


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
