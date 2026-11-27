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
import achievements as A  # noqa: E402
import daily_gen  # noqa: E402
import divisions as D  # noqa: E402
import leaderboard as L  # noqa: E402
import modes as M  # noqa: E402
import push as P  # noqa: E402
import push_send as PS  # noqa: E402

BANK = json.load(open(os.environ["QUESTIONS_PATH"], encoding="utf-8"))
BY_ID = {q["id"]: q for q in BANK}
STATS = json.load(open(os.path.join(ROOT, "docs", "stats.json"), encoding="utf-8"))
CAREERS = json.load(open(os.path.join(ROOT, "docs", "careers.json"), encoding="utf-8"))["players"]


REAL_CLOCK = L.clock


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
        L._settled["week"] = None
        L.clock = REAL_CLOCK
        P.send_async = lambda fn, *a: P._quiet(fn, *a)  # no stray threads racing the next test's fresh DB


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
        self.assertEqual(set(b["rows"][0]), {"name", "score", "correct", "total", "created_utc", "badges"})

def fake_clock(dt):
    L.clock = lambda: dt


def classic_body(n=10, right=None, league="all", name="Maker", player=None, timer=0):
    qs = [q for q in BANK if league == "all" or q["l"] == league][:n]
    right = right if right is not None else [True] * n
    return {"name": name, "player_id": player or pid(), "league": league, "diff": 0, "timer": timer,
            "answers": answers(qs, right)}, qs


class Challenges(Base):
    def test_round_trip_and_head_to_head(self):
        a, b = pid(), pid()
        body, qs = classic_body(right=[True] * 7 + [False] * 3, player=a, name="Alice")
        r = L.create_challenge(body)
        code = r["code"]
        self.assertRegex(code, r"^[A-HJ-NP-Z2-9]{6}$")
        self.assertEqual(r["correct"], 7)
        g = L.get_challenge(code)
        self.assertEqual((g["from"], g["ids"], g["result"]), ("Alice", [q["id"] for q in qs], None))
        self.assertNotIn("choices", g["their"])          # no peeking at their answers before you play
        self.assertNotIn("grid", g["their"])
        # friend plays the identical set, better
        res = L.play_challenge({"code": code.lower(), "name": "Bob", "player_id": b,
                                "answers": answers(qs, [True] * 9 + [False])})
        self.assertEqual(res["result"]["outcome"], "win")
        self.assertEqual(res["result"]["you"]["grid"], "1111111110")
        self.assertEqual(res["result"]["them"]["grid"], "1111111000")
        self.assertEqual(res["result"]["h2h"], {"won": 1, "drawn": 0, "lost": 0})
        self.assertEqual([p["name"] for p in res["plays"]], ["Bob"])
        # one go each; can't play your own
        with self.assertRaises(L.Conflict):
            L.play_challenge({"code": code, "name": "Bob", "player_id": b, "answers": answers(qs, [True] * 10)})
        with self.assertRaises(L.Conflict):
            L.play_challenge({"code": code, "name": "Alice", "player_id": a, "answers": answers(qs, [True] * 10)})
        # Bob challenges back, Alice draws: record from each side
        body2, qs2 = classic_body(n=5, right=[True, False, True, False, True], player=b, name="Bob", league="sa")
        code2 = L.create_challenge(body2)["code"]
        L.play_challenge({"code": code2, "name": "Alice", "player_id": a,
                          "answers": answers(qs2, [True, False, True, False, True])})
        self.assertEqual(L.h2h_list(a)["rows"], [{"name": "Bob", "won": 0, "drawn": 1, "lost": 1}])
        self.assertEqual(L.h2h_list(b)["rows"], [{"name": "Alice", "won": 1, "drawn": 1, "lost": 0}])
        # creator sees their own answers + who played
        mine = L.get_challenge(code, a)
        self.assertTrue(mine["mine"])
        self.assertEqual(mine["their"]["grid"], "1111111000")

    def test_must_be_the_same_questions_in_order(self):
        body, qs = classic_body()
        code = L.create_challenge(body)["code"]
        with self.assertRaises(ValueError):
            L.play_challenge({"code": code, "name": "Cheat", "player_id": pid(),
                              "answers": answers(list(reversed(qs)), [True] * 10)})
        with self.assertRaises(L.NotFound):
            L.play_challenge({"code": "ZZZZZZ", "name": "Lost", "player_id": pid(), "answers": []})
        with self.assertRaises(ValueError):
            L.get_challenge("BAD!")

    def test_forged_creator_points_ignored(self):
        body, qs = classic_body(right=[False] * 10)
        body["points"], body["correct"] = 999, 10
        r = L.create_challenge(body)
        self.assertEqual((r["correct"], r["points"]), (0, 0))

    def test_settings_must_match_and_timer_counts(self):
        body, _ = classic_body(league="pl")
        body["league"] = "ll"
        with self.assertRaises(ValueError):
            L.create_challenge(body)
        body, qs = classic_body(league="pl", timer=10)
        r = L.create_challenge(body)
        self.assertAlmostEqual(r["points"], round(sum(q["d"] for q in qs) * 1.5, 1))


class Leagues(Base):
    def test_create_join_leave_and_table(self):
        a, b, c = pid(), pid(), pid()
        r = L.create_league({"name": "Alice", "player_id": a, "league_name": "Sunday  Lads"})
        code = r["code"]
        self.assertEqual(r["name"], "Sunday Lads")
        self.assertRegex(code, r"^[A-HJ-NP-Z2-9]{6}$")
        self.assertFalse(L.join_league({"name": "Bob", "player_id": b, "code": code.lower()})["already"])
        self.assertTrue(L.join_league({"name": "Bob", "player_id": b, "code": code})["already"])
        L.join_league({"name": "Cat", "player_id": c, "code": code})
        # points from different modes all count
        L.add_score(classic_body(player=b, name="Bob")[0] | {"total": 10})                 # classic
        L.add_score({"mode": "survival", "name": "Alice", "player_id": a,
                     "answers": answers(BANK[:4], [True] * 3 + [False])})                     # 3
        t = L.league_view(code, a, "week")
        self.assertEqual([x["name"] for x in t["rows"]], ["Bob", "Alice", "Cat"])
        self.assertEqual(t["rows"][1], {"name": "Alice", "week": 3, "total": 3, "me": True, "badges": []})
        self.assertTrue(t["member"] and t["owner"])
        self.assertEqual(L.my_leagues(b)["rows"][0]["rank_week"], 1)
        L.leave_league({"player_id": c, "code": code})
        self.assertEqual(L.league_view(code, c, "all")["members"], 2)
        self.assertEqual(L.my_leagues(c)["rows"], [])
        with self.assertRaises(L.NotFound):
            L.join_league({"name": "Dan", "player_id": pid(), "code": "ZZZZZZ"})

    def test_league_name_rules(self):
        for bad in ("x", "<script>", "Shit FC", "a" * 25):
            with self.assertRaises(ValueError):
                L.create_league({"name": "Alice", "player_id": pid(), "league_name": bad})

    def test_league_cap(self):
        me = pid()
        for i in range(L.MAX_LEAGUES):
            L.create_league({"name": "Busy", "player_id": me, "league_name": "League %d" % i})
        with self.assertRaises(ValueError):
            L.create_league({"name": "Busy", "player_id": me, "league_name": "One too many"})


class Divisions(Base):
    def post_points(self, player, name, n):
        """n points via survival (1 per correct answer)."""
        L.add_score({"mode": "survival", "name": name, "player_id": player,
                     "answers": answers(BANK[:n + 1], [True] * n + [False])})

    def test_week_boundaries_are_uk_monday(self):
        from datetime import datetime as dt
        self.assertEqual(D.week_key(dt(2026, 11, 29, 23, 59, tzinfo=timezone.utc)), "2026-11-23")   # Sun, GMT
        self.assertEqual(D.week_key(dt(2026, 11, 30, 0, 0, tzinfo=timezone.utc)), "2026-11-30")
        self.assertEqual(D.week_key(dt(2026, 6, 28, 23, 30, tzinfo=timezone.utc)), "2026-06-29")    # Mon 00:30 BST
        self.assertEqual(D.week_end_utc("2026-06-22"), dt(2026, 6, 28, 23, 0, tzinfo=timezone.utc))
        self.assertEqual(D.week_end_utc("2026-11-23"), dt(2026, 11, 30, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(D.moves(20), (3, 3))
        self.assertEqual(D.moves(6), (2, 2))
        self.assertEqual(D.moves(2), (1, 0))
        self.assertEqual(D.moves(1), (0, 0))

    def test_new_players_start_bottom_and_groups_of_20(self):
        fake_clock(datetime(2026, 12, 2, 12, 0, tzinfo=timezone.utc))
        ids = [pid() for _ in range(23)]
        for i, p in enumerate(ids):
            self.post_points(p, "P%02d" % i, 1 + i % 5)
        v = L.division_view(ids[0])
        self.assertEqual((v["tier"], v["tier_name"], v["size"]), (5, "Sunday League", 20))
        self.assertEqual(L.division_view(ids[22])["size"], 3)
        fresh = L.division_view(pid())
        self.assertFalse(fresh["in_table"])
        self.assertEqual(fresh["tier"], 5)
        self.assertEqual(fresh["secs_left"], int((datetime(2026, 12, 7, 0, 0, tzinfo=timezone.utc) -
                                                  datetime(2026, 12, 2, 12, 0, tzinfo=timezone.utc)).total_seconds()))

    def test_rollover_on_first_request_of_the_week(self):
        week1 = datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc)       # Tue 1 Dec
        fake_clock(week1)
        ids = [pid() for _ in range(12)]
        for i, p in enumerate(ids):                     # P00 scores most, P11 least
            self.post_points(p, "P%02d" % i, 24 - 2 * i)
        v = L.division_view(ids[3])
        self.assertEqual((v["rank"], v["size"], v["up"], v["down"]), (4, 12, 3, 0))     # bottom tier: no drop
        # Sunday 23:59 UK: still week 1, nothing settled
        fake_clock(datetime(2026, 12, 6, 23, 59, tzinfo=timezone.utc))
        self.assertEqual(L.division_view(ids[0])["tier"], 5)
        with L.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM division_weeks").fetchone()[0], 0)
        # Monday 00:00 UK: the first request settles week 1
        fake_clock(datetime(2026, 12, 7, 0, 0, 5, tzinfo=timezone.utc))
        v0 = L.division_view(ids[0])
        self.assertEqual((v0["tier"], v0["tier_name"], v0["in_table"]), (4, "League Two", False))
        self.assertEqual(v0["last"], {"week": "2026-11-30", "from": 5, "from_name": "Sunday League", "moved": 1})
        self.assertEqual(L.division_view(ids[3])["tier"], 5)
        self.assertEqual(L.division_view(ids[3])["last"]["moved"], 0)
        # promoted players play into League Two; a newcomer goes to the bottom
        for i in (0, 1, 2):
            self.post_points(ids[i], "P%02d" % i, 5 - i)
        newbie = pid()
        self.post_points(newbie, "Newbie", 9)
        self.assertEqual(L.division_view(ids[2])["size"], 3)
        self.assertEqual(L.division_view(ids[2])["rows"][0]["name"], "P00")
        self.assertEqual(L.division_view(newbie)["tier"], 5)
        # settled once only, even with more requests
        with L.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM division_weeks").fetchone()[0], 1)
        # week 2 -> 3: in League Two (3 players) top 1 up, bottom 1 down; skipped-week player keeps their tier
        fake_clock(datetime(2026, 12, 15, 9, 0, tzinfo=timezone.utc))
        self.assertEqual(L.division_view(ids[0])["tier"], 3)
        self.assertEqual(L.division_view(ids[1])["tier"], 4)
        self.assertEqual(L.division_view(ids[2])["tier"], 5)
        self.assertEqual(L.division_view(ids[2])["last"]["moved"], -1)
        self.assertEqual(L.division_view(ids[5])["tier"], 5)
        self.assertIsNone(L.division_view(ids[5])["last"])     # didn't play last week

    def test_relegation_from_a_middle_tier(self):
        fake_clock(datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc))
        ids = [pid() for _ in range(10)]
        with L.db() as c:
            for p in ids:
                c.execute("INSERT INTO player_tier VALUES (?, 3, '')", (p,))
        for i, p in enumerate(ids):
            self.post_points(p, "M%02d" % i, 20 - i)
        fake_clock(datetime(2026, 12, 8, 10, 0, tzinfo=timezone.utc))
        tiers = [L.division_view(p)["tier"] for p in ids]
        self.assertEqual(tiers, [2, 2, 2, 3, 3, 3, 3, 4, 4, 4])

    def test_zero_point_posts_dont_join(self):
        p = pid()
        L.add_score({"mode": "survival", "name": "Zero", "player_id": p, "answers": answers(BANK[:1], [False])})
        self.assertFalse(L.division_view(p)["in_table"])


class Badges(Base):
    def test_store_merge_and_rank(self):
        me = pid()
        r = L.set_badges({"player_id": me, "badges": ["streak3", "bogus", "perfect10", "streak3"]})
        self.assertEqual(r["badges"], ["streak3", "perfect10"])          # unknown dropped, bronze in list order
        r = L.set_badges({"player_id": me, "badges": ["giant"]})           # union: nothing is ever lost
        self.assertEqual(r["badges"], ["giant", "streak3", "perfect10"])
        with self.assertRaises(ValueError):
            L.set_badges({"player_id": me, "badges": "giant"})
        with self.assertRaises(ValueError):
            L.set_badges({"player_id": "nope", "badges": []})

    def test_boards_show_best_three(self):
        a, b = pid(), pid()
        L.add_score({"mode": "survival", "name": "Badger", "player_id": a,
                     "answers": answers(BANK[:4], [True] * 3 + [False])})
        L.add_score({"mode": "survival", "name": "Plain", "player_id": b,
                     "answers": answers(BANK[:3], [True] * 2 + [False])})
        L.set_badges({"player_id": a, "badges": ["streak3", "perfect10", "surv15", "streak30", "giant"]})
        rows = L.public_run_board("survival", "week")["rows"]
        self.assertEqual([(r["name"], r["badges"]) for r in rows],
                         [("Badger", ["streak30", "giant", "surv15"]), ("Plain", [])])
        self.assertNotIn("player_id", rows[0])
        # classic board (keyed by name) and the daily board
        body, _ = classic_body(player=a, name="Badger")
        body["total"] = 10
        L.add_score(body)
        self.assertEqual(L.with_badges(L.board(10, "week"), "name")[0]["badges"], ["streak30", "giant", "surv15"])
        ids = L.bank()[1].ids(daily_gen.uk_today())
        L.add_daily({"name": "Badger", "player_id": a, "date": daily_gen.uk_today().isoformat(),
                     "answers": [{"id": i, "choice": BY_ID[i]["a"]} for i in ids]})
        self.assertEqual(L.public_board("today")["rows"][0]["badges"], ["streak30", "giant", "surv15"])
        # division + league tables
        self.assertEqual(L.division_view(a)["rows"][0]["badges"], ["streak30", "giant", "surv15"])
        code = L.create_league({"name": "Badger", "player_id": a, "league_name": "Badge League"})["code"]
        self.assertEqual(L.league_view(code, a, "week")["rows"][0]["badges"], ["streak30", "giant", "surv15"])

    def test_challenge_result_has_tiers(self):
        a, b = pid(), pid()
        body, qs = classic_body(right=[True] * 5 + [False] * 5, player=a, name="Top")
        code = L.create_challenge(body)["code"]
        with L.db() as c:
            c.execute("INSERT INTO player_tier (player_id, tier, updated_utc) VALUES (?,?,?)", (a, 2, L.now_utc()))
        res = L.play_challenge({"code": code, "name": "Under", "player_id": b, "answers": answers(qs, [True] * 10)})
        self.assertEqual(res["result"]["outcome"], "win")
        self.assertEqual(res["result"]["tiers"], {"you": D.BOTTOM, "them": 2})

    def test_python_and_js_lists_match(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        js = "const A=require(%s);console.log(JSON.stringify(A.LIST.map(a=>[a.id,a.t])))" % json.dumps(
            os.path.join(ROOT, "docs", "js", "ach-defs.js"))
        out = json.loads(subprocess.check_output([node, "-e", js]))
        self.assertEqual(out, [list(x) for x in A.LIST])
        self.assertGreaterEqual(len(out), 25)

    def test_js_rank_matches_server(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        ids = ["perfect10", "lg_in", "streak3", "topflight", "seen500", "giant", "x"]
        js = "const A=require(%s);console.log(JSON.stringify(A.rank(%s)))" % (
            json.dumps(os.path.join(ROOT, "docs", "js", "ach-defs.js")), json.dumps(ids))
        self.assertEqual(json.loads(subprocess.check_output([node, "-e", js])), A.clean(ids))


class Reports(Base):
    def test_report_stored_and_updated_not_duplicated(self):
        me, q = pid(), BANK[0]
        self.assertEqual(L.add_report({"player_id": me, "id": q["id"], "reason": "typo", "note": "  spelt\x00 wrong  "}),
                         {"ok": True})
        L.add_report({"player_id": me, "id": q["id"], "reason": "outdated"})
        L.add_report({"player_id": pid(), "id": q["id"], "reason": "wrong", "note": "x" * 900})
        with L.db() as c:
            rows = c.execute("SELECT reason, note, status FROM reports ORDER BY id").fetchall()
        self.assertEqual([tuple(r) for r in rows], [("outdated", "", "open"), ("wrong", "x" * L.NOTE_MAX, "open")])

    def test_report_rejects_bad_input(self):
        q = BANK[0]["id"]
        for bad in ({"player_id": "nope", "id": q, "reason": "typo"},
                    {"player_id": pid(), "id": "zz999", "reason": "typo"},
                    {"player_id": pid(), "id": q, "reason": "boring"},
                    {"player_id": pid(), "id": q, "reason": "other"},          # "other" needs a note
                    {"player_id": pid(), "id": q, "reason": "typo", "note": 5}):
            with self.assertRaises(ValueError):
                L.add_report(bad)
        self.assertEqual(L.clean_note("a\tb\u200b\n c"), "a b\n c")

    def test_question_stats_flags_misgrading_and_reports(self):
        import question_stats as QS
        easy = next(q for q in BANK if q["d"] == 1)
        hard = next(q for q in BANK if q["d"] == 3)
        few = [q for q in BANK if q["d"] == 1][1]
        rows = ([(easy["id"], int(i < 7)) for i in range(20)]       # 35% on an Easy -> flagged
                + [(hard["id"], int(i < 18)) for i in range(20)]    # 90% on a Hard -> flagged
                + [(few["id"], 0) for _ in range(19)])              # too few answers to judge
        med = next(q for q in BANK if q["d"] == 2)
        rows += [(med["id"], 0) for _ in range(30)]                 # Medium: never judged
        with L.db() as c:
            c.executemany("INSERT INTO answers (player_id, qid, correct, mode, created_utc) VALUES ('p',?,?,'classic','t')", rows)
        L.add_report({"player_id": pid(), "id": med["id"], "reason": "wrong", "note": "It was the other one"})
        L.add_report({"player_id": pid(), "id": med["id"], "reason": "typo"})
        s = QS.summary(os.environ["QUIZ_DB"], os.environ["QUESTIONS_PATH"])
        flagged = {m["id"]: m for m in s["misgraded"]}
        self.assertEqual(set(flagged), {easy["id"], hard["id"]})
        self.assertEqual(flagged[easy["id"]]["correct_rate"], 0.35)
        self.assertEqual([(r["id"], r["count"]) for r in s["reports"]], [(med["id"], 2)])
        txt = QS.as_text(s, short=True)
        self.assertIn("2 open reports on 1 question", txt)
        self.assertIn("It was the other one", txt)
        self.assertEqual(QS.resolve(os.environ["QUIZ_DB"], med["id"]), 2)
        self.assertEqual(QS.summary(os.environ["QUIZ_DB"], os.environ["QUESTIONS_PATH"])["reports"], [])

    def test_weekly_report_dry_run(self):
        L.add_report({"player_id": pid(), "id": BANK[0]["id"], "reason": "outdated", "note": "He retired"})
        env = dict(os.environ, DRY_RUN="1", NOTIFY="/bin/false")
        out = subprocess.run(["sh", os.path.join(ROOT, "tools", "weekly_report.sh")], env=env,
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("1 open report on 1 question", out.stdout)
        self.assertIn("He retired", out.stdout)


try:
    import pywebpush  # noqa: F401
    import http_ece
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    HAVE_PUSH = True
except ImportError:         # system python: storage tests still run; sending needs the venv
    HAVE_PUSH = False


def b64u(b):
    import base64
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class FakePushService:
    """A local stand-in for FCM: /ok/... -> 201, /gone/... -> 410, /down/... -> 500. Records every POST."""
    def __init__(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        got = self.got = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                got.append((self.path, dict(self.headers), body))
                code = 201 if self.path.startswith("/ok") else 410 if self.path.startswith("/gone") else 500
                self.send_response(code)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.host = "127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def sub(self, kind="ok"):
        """A real-looking subscription (fresh P-256 key + auth secret) pointing at us; keeps the private key."""
        if HAVE_PUSH:
            k = ec.generate_private_key(ec.SECP256R1())
            pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        else:
            k, pub = None, b"\x04" + os.urandom(64)
        auth = os.urandom(16)
        s = {"endpoint": "http://%s/%s/%s" % (self.host, kind, uuid.uuid4().hex),
             "keys": {"p256dh": b64u(pub), "auth": b64u(auth)}}
        return s, (k, auth)

    def decrypt(self, i, secret):
        k, auth = secret
        return json.loads(http_ece.decrypt(self.got[i][2], private_key=k, auth_secret=auth, version="aes128gcm"))


class Push(Base):
    @classmethod
    def setUpClass(cls):
        cls.fake = FakePushService()
        P.EXTRA_HOSTS.add(cls.fake.host)
        cls.vapid = os.path.join(TMP, "vapid.json")
        if HAVE_PUSH:
            k = ec.generate_private_key(ec.SECP256R1())
            pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
            pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()).decode()
        else:
            pub, pem = b"\x04" + os.urandom(64), "-----BEGIN PRIVATE KEY-----\n-----END PRIVATE KEY-----\n"
        cls.pubkey = b64u(pub)
        with open(cls.vapid, "w") as f:
            json.dump({"public_key": cls.pubkey, "private_pem": pem, "subject": "https://example.org"}, f)

    @classmethod
    def tearDownClass(cls):
        cls.fake.srv.shutdown()
        P.EXTRA_HOSTS.discard(cls.fake.host)

    def setUp(self):
        super().setUp()
        P.VAPID_PATH = self.vapid
        self.fake.got.clear()

    def subscribe(self, player, kind="ok", types=None):
        s, secret = self.fake.sub(kind)
        L.add_push({"player_id": player, "subscription": s, "types": types if types is not None else list(P.TYPES)})
        return s, secret

    def test_only_real_push_services(self):
        good = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc:def", "keys": self.fake.sub()[0]["keys"]}
        self.assertEqual(P.clean_sub(good)[0], good["endpoint"])
        moz = dict(good, endpoint="https://updates.push.services.mozilla.com/wpush/v2/x")
        self.assertTrue(P.clean_sub(moz))
        for ep in ("http://fcm.googleapis.com/x", "https://example.com/push", "https://fcm.googleapis.com.evil.io/x",
                   "https://user@fcm.googleapis.com/x", "file:///etc/passwd", "http://127.0.0.1:1/ok/x", ""):
            with self.assertRaises(ValueError, msg=ep):
                P.clean_sub(dict(good, endpoint=ep))
        for keys in ({}, {"p256dh": "x", "auth": "y"}, {"p256dh": good["keys"]["p256dh"], "auth": "a b c d e f g h i j"}):
            with self.assertRaises(ValueError):
                P.clean_sub(dict(good, keys=keys))
        with self.assertRaises(ValueError):
            P.clean_types(["daily", "spam"])
        self.assertEqual(P.clean_types({"division": True, "daily": True, "challenge": False}), ["daily", "division"])

    def test_store_update_cap_and_delete(self):
        me = pid()
        s, _ = self.subscribe(me, types=["daily"])
        self.assertEqual(L.add_push({"player_id": me, "subscription": s, "types": ["daily", "challenge"]})["types"],
                         ["daily", "challenge"])
        with L.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*), MAX(types) FROM push_subs").fetchone()[:], (1, "daily,challenge"))
        for _ in range(P.MAX_SUBS + 2):
            self.subscribe(me)
        with L.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM push_subs WHERE player_id = ?", (me,)).fetchone()[0],
                             P.MAX_SUBS)
        s2, _ = self.subscribe(me)
        L.add_push({"player_id": me, "subscription": s2, "types": []})      # all off = forgotten
        L.delete_push({"endpoint": s2["endpoint"]})
        with L.db() as c:
            self.assertFalse(c.execute("SELECT 1 FROM push_subs WHERE endpoint = ?", (s2["endpoint"],)).fetchone())
        with self.assertRaises(ValueError):
            L.add_push({"player_id": "nope", "subscription": s2})

    def test_http_routes(self):
        h = Http("test_routes")
        import threading
        from http.server import ThreadingHTTPServer
        srv = ThreadingHTTPServer(("127.0.0.1", 0), type("Q", (L.Handler,), {"log_message": lambda *a: None}))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        h.port = srv.server_address[1]
        try:
            L._hits.clear()
            self.assertEqual(h.call("/push/key"), (200, {"key": self.pubkey}))
            P.VAPID_PATH = os.path.join(TMP, "missing.json")
            self.assertEqual(h.call("/push/key")[0], 503)
            P.VAPID_PATH = self.vapid
            me = pid()
            s, _ = self.fake.sub()
            self.assertEqual(h.call("/push", {"player_id": me, "subscription": s, "types": ["daily"]}),
                             (200, {"ok": True, "types": ["daily"]}))
            self.assertEqual(h.call("/push", {"player_id": me, "subscription": {"endpoint": "https://x.io/1"}})[0], 400)
            import urllib.request
            req = urllib.request.Request("http://127.0.0.1:%d/api/push" % h.port, method="DELETE",
                                         data=json.dumps({"endpoint": s["endpoint"]}).encode(),
                                         headers={"Content-Type": "application/json", "X-Forwarded-For": "10.0.0.9"})
            with urllib.request.urlopen(req) as r:
                self.assertEqual(json.loads(r.read()), {"ok": True})
            with L.db() as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM push_subs").fetchone()[0], 0)
            req = urllib.request.Request("http://127.0.0.1:%d/api/push" % h.port, method="OPTIONS",
                                         headers={"Origin": "https://teo2301.github.io"})
            with urllib.request.urlopen(req) as r:
                self.assertIn("DELETE", r.headers["Access-Control-Allow-Methods"])
            for i in range(L.PUSH_RATE[0]):
                h.call("/push", {"player_id": me, "subscription": s, "types": ["daily"]}, ip="10.0.0.7")
            self.assertEqual(h.call("/push", {"player_id": me, "subscription": s}, ip="10.0.0.7")[0], 429)
            self.assertEqual(h.call("/scores", {}, ip="10.0.0.7")[0], 400)     # scores budget untouched
        finally:
            srv.shutdown()

    @unittest.skipUnless(HAVE_PUSH, "pywebpush not installed (run with .venv/bin/python)")
    def test_challenge_beaten_pushes_the_creator_once(self):
        a, b, c3 = pid(), pid(), pid()
        _, secret = self.subscribe(a)
        self.subscribe(a, types=["daily"])              # this one doesn't want challenge pushes
        body, qs = classic_body(right=[True] * 5 + [False] * 5, player=a, name="Setter")
        code = L.create_challenge(body)["code"]
        L.play_challenge({"code": code, "name": "Loser", "player_id": c3, "answers": answers(qs, [True] * 3 + [False] * 7)})
        self.assertEqual(self.fake.got, [])             # didn't beat it: nothing sent
        L.play_challenge({"code": code, "name": "Winner", "player_id": b, "answers": answers(qs, [True] * 8 + [False] * 2)})
        self.assertEqual(len(self.fake.got), 1)
        msg = self.fake.decrypt(0, secret)
        self.assertEqual(msg["title"], "Winner beat your challenge")
        self.assertEqual(msg["body"], "8/10 against your 5/10. Fancy a rematch?")
        self.assertEqual(msg["url"], "./#c=" + code)
        hdr = {k.lower(): v for k, v in self.fake.got[0][1].items()}
        self.assertTrue(hdr["authorization"].startswith("vapid t="))
        self.assertEqual(hdr["ttl"], str(24 * 3600))
        self.assertFalse(L.claim_push("challenge", code + ":" + b, a))   # logged: never twice

    @unittest.skipUnless(HAVE_PUSH, "pywebpush not installed (run with .venv/bin/python)")
    def test_daily_send_skips_players_dedupes_and_drops_dead_subs(self):
        fake_clock(datetime(2026, 12, 3, 9, 0, tzinfo=timezone.utc))
        keen, played, quiet, dead = pid(), pid(), pid(), pid()
        _, s_keen = self.subscribe(keen)
        self.subscribe(played)
        self.subscribe(quiet, types=["challenge"])
        dsub, _ = self.subscribe(dead, kind="gone")
        for day in (1, 2):                              # keen has a 2-day streak going into today
            with L.db() as c:
                c.execute("INSERT INTO daily (player_id, date, number, correct, total, points, grid, created_utc)"
                          " VALUES (?,?,?,?,?,?,?,?)", (keen, "2026-12-0%d" % day, day, 5, 7, 9, "1111100", L.now_utc()))
        d = daily_gen.uk_today(L.clock())
        L.add_daily({"name": "Played", "player_id": played, "date": d.isoformat(),
                     "answers": [{"id": i, "choice": BY_ID[i]["a"]} for i in L.daily_ids(d)["ids"]]})
        r = PS.daily()
        self.assertEqual((r["players"], r["ok"], r["gone"]), (2, 1, 1))
        msg = self.fake.decrypt([g[0][:3] for g in self.fake.got].index("/ok"), s_keen)
        self.assertEqual(msg["title"], "Football Quiz #%d is up" % daily_gen.number(d))
        self.assertEqual(msg["body"], "Keep your 2-day streak going.")
        self.assertEqual(msg["url"], "./#daily")
        with L.db() as c:
            self.assertFalse(c.execute("SELECT 1 FROM push_subs WHERE endpoint = ?", (dsub["endpoint"],)).fetchone())
        self.assertEqual(PS.daily()["players"], 0)      # re-run: nobody twice
        fake_clock(datetime(2026, 11, 26, 9, 0, tzinfo=timezone.utc))
        self.assertIn("skipped", PS.daily())            # before daily #1
        self.assertEqual(PS.daily(force=True)["players"], 2)    # keen + played (played 3 Dec, not today)

    @unittest.skipUnless(HAVE_PUSH, "pywebpush not installed (run with .venv/bin/python)")
    def test_monday_division_results(self):
        fake_clock(datetime(2026, 12, 2, 12, 0, tzinfo=timezone.utc))
        ids = [pid() for _ in range(6)]
        secrets = {}
        for i, p in enumerate(ids):
            Divisions.post_points(None, p, "D%d" % i, 10 - i)
            secrets[p] = self.subscribe(p, types=["division"])[1]
        fake_clock(datetime(2026, 12, 7, 9, 5, tzinfo=timezone.utc))
        r = PS.monday()
        self.assertEqual(r["ok"], 6)
        got = {}
        for i, (path, _, _) in enumerate(self.fake.got):
            for p, sec in secrets.items():
                try:
                    got[p] = self.fake.decrypt(i, sec)
                except Exception:
                    pass
        self.assertEqual(got[ids[0]]["title"], "Promoted!")
        self.assertEqual(got[ids[0]]["body"], "You're up from Sunday League to League Two this week.")
        self.assertEqual(got[ids[2]]["title"], "You stayed in Sunday League")    # bottom tier: nobody relegated
        self.assertEqual(PS.monday()["players"], 0)

    def test_evening_reminder_recipients(self):
        fake_clock(datetime(2026, 12, 3, 18, 30, tzinfo=timezone.utc))
        keen, played, off, nudged = pid(), pid(), pid(), pid()
        self.subscribe(keen)
        self.subscribe(played)
        self.subscribe(off, types=["challenge", "division"])     # daily switch off = opted out
        self.subscribe(nudged, types=["daily"])
        d = daily_gen.uk_today(L.clock())
        L.add_daily({"name": "Played", "player_id": played, "date": d.isoformat(),
                     "answers": [{"id": i, "choice": BY_ID[i]["a"]} for i in L.daily_ids(d)["ids"]]})
        self.assertTrue(L.claim_push("reminder", d.isoformat(), nudged))   # already nudged today
        with L.db() as c:
            self.assertEqual(PS.reminder_targets(c, d), [keen])
        r = PS.reminder(dry=True)
        self.assertEqual(r["players"], 1)
        with L.db() as c:                               # a dry run logs nothing
            self.assertEqual(PS.reminder_targets(c, d), [keen])
        fake_clock(datetime(2026, 11, 26, 18, 30, tzinfo=timezone.utc))
        self.assertIn("skipped", PS.reminder(dry=True))  # before daily #1
        self.assertEqual(PS.reminder(force=True, dry=True)["players"], 3)  # another day: keen, played, nudged

    @unittest.skipUnless(HAVE_PUSH, "pywebpush not installed (run with .venv/bin/python)")
    def test_evening_reminder_sends_once(self):
        fake_clock(datetime(2026, 12, 3, 18, 30, tzinfo=timezone.utc))
        keen = pid()
        _, secret = self.subscribe(keen, types=["daily"])
        r = PS.reminder()
        self.assertEqual((r["players"], r["ok"]), (1, 1))
        msg = self.fake.decrypt(0, secret)
        self.assertIn("closes at midnight", msg["title"])
        self.assertEqual(msg["url"], "./#daily")
        self.assertEqual(PS.reminder()["players"], 0)   # at most one per player per day

    def test_sending_without_pywebpush_or_key_is_a_no_op(self):
        P.VAPID_PATH = os.path.join(TMP, "missing.json")
        me = pid()
        self.subscribe(me)
        self.assertEqual(P.send_to(L.db, me, "daily", {"title": "x"}), {"ok": 0, "gone": 0, "error": 0})
        self.assertEqual(self.fake.got, [])


class Http(Base):
    """The real handler on a random port: routes, status codes, the creation rate limit."""
    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        class Quiet(L.Handler):
            def log_message(self, *a):
                pass
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        super().setUp()
        L._hits.clear()

    def call(self, path, body=None, ip="10.0.0.1"):
        import urllib.request
        import urllib.error
        req = urllib.request.Request("http://127.0.0.1:%d/api%s" % (self.port, path),
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Forwarded-For": ip, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def test_routes(self):
        me = pid()
        st, r = self.call("/leagues", {"name": "Web", "player_id": me, "league_name": "Web League"})
        self.assertEqual(st, 200)
        self.assertEqual(self.call("/league?code=%s&player_id=%s" % (r["code"], me))[1]["rows"][0]["name"], "Web")
        self.assertEqual(self.call("/leagues?player_id=" + me)[1]["rows"][0]["code"], r["code"])
        self.assertEqual(self.call("/league?code=ZZZZZZ")[0], 404)
        self.assertEqual(self.call("/challenge?code=ZZZZZZ")[0], 404)
        self.assertEqual(self.call("/division?player_id=nope")[0], 400)
        self.assertEqual(self.call("/division")[0], 400)
        self.assertEqual(self.call("/division?player_id=" + me)[1]["tier_name"], "Sunday League")
        self.assertEqual(self.call("/h2h?player_id=" + me)[1], {"rows": []})
        body, qs = classic_body(player=me, name="Web")
        st, ch = self.call("/challenges", body)
        self.assertEqual(st, 200)
        st, g = self.call("/challenge?code=" + ch["code"])
        self.assertEqual((st, g["total"]), (200, 10))
        st, e = self.call("/challenge/play", {"code": ch["code"], "name": "Web", "player_id": me,
                                              "answers": body["answers"]})
        self.assertEqual(st, 409)

    def test_badges_route_has_its_own_limit(self):
        me = pid()
        for _ in range(L.RATE[0]):          # use up the score budget
            L.limited("10.0.0.7")
        st, r = self.call("/badges", {"player_id": me, "badges": ["streak3"]}, ip="10.0.0.7")
        self.assertEqual((st, r["badges"]), (200, ["streak3"]))
        for _ in range(L.BADGE_RATE[0] - 1):
            self.call("/badges", {"player_id": me, "badges": []}, ip="10.0.0.7")
        self.assertEqual(self.call("/badges", {"player_id": me, "badges": []}, ip="10.0.0.7")[0], 429)
        st, b = self.call("/board?len=10&period=week")
        self.assertEqual((st, b["rows"]), (200, []))

    def test_report_route_and_its_own_limit(self):
        me, q = pid(), BANK[0]["id"]
        for _ in range(L.RATE[0]):          # a used-up score budget doesn't block reports
            L.limited("10.0.0.8")
        st, r = self.call("/report", {"player_id": me, "id": q, "reason": "typo"}, ip="10.0.0.8")
        self.assertEqual((st, r), (200, {"ok": True}))
        self.assertEqual(self.call("/report", {"player_id": me, "id": q, "reason": "nah"}, ip="10.0.0.8")[0], 400)
        for _ in range(L.REPORT_RATE[0] - 2):
            self.call("/report", {"player_id": me, "id": q, "reason": "typo"}, ip="10.0.0.8")
        self.assertEqual(self.call("/report", {"player_id": me, "id": q, "reason": "typo"}, ip="10.0.0.8")[0], 429)
        # and reports don't eat into another IP's score budget
        self.assertEqual(self.call("/name", {"name": "Reporter", "player_id": me}, ip="10.0.0.11")[0], 200)

    def test_creation_rate_limit_per_ip(self):
        me = pid()
        for i in range(L.CREATE_RATE[0]):
            self.assertEqual(self.call("/leagues", {"name": "Spam", "player_id": me,
                                                    "league_name": "Spam %d" % i}, ip="10.0.0.9")[0], 200)
        st, e = self.call("/leagues", {"name": "Spam", "player_id": me, "league_name": "Spam x"}, ip="10.0.0.9")
        self.assertEqual(st, 429)
        # another IP is fine, and joining isn't a creation
        self.assertEqual(self.call("/leagues", {"name": "Other", "player_id": pid(), "league_name": "Fine"},
                                   ip="10.0.0.10")[0], 200)


class Content(unittest.TestCase):
    def test_validate_passes(self):
        out = subprocess.run([sys.executable, os.path.join(ROOT, "validate.py")], cwd=ROOT,
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_enough_hard_for_two_a_day(self):
        hard = sum(1 for q in BANK if q["d"] == 3)
        self.assertGreaterEqual(hard, 2 * daily_gen.NO_REPEAT + 1)
        self.assertEqual(sorted(daily_gen.PLAN), [1, 1, 2, 2, 2, 3, 3])

    def test_launch_content_target(self):
        # Phase 5: ~1,200 classic questions, every league well stocked at every level, all explained
        self.assertGreaterEqual(len(BANK), 1200)
        for lg in ("pl", "ll", "sa", "eu", "in"):
            for d in (1, 2, 3):
                n = sum(1 for q in BANK if q["l"] == lg and q["d"] == d)
                self.assertGreaterEqual(n, 35, (lg, d, n))
        self.assertEqual([q["id"] for q in BANK if not q.get("x")], [])
        for lg in ("pl", "ll", "sa", "eu", "in"):   # ids run on per league with no gaps
            nums = sorted(int(q["id"][2:]) for q in BANK if q["l"] == lg)
            self.assertEqual(nums, list(range(1, len(nums) + 1)), lg)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
