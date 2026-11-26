"""Live 1v1, ranked league and 2v2 room tests. Identical in every quiz. Run: python3 test_live.py"""
import os
import random
import sys
import threading
import time
import tempfile
import unittest
import uuid

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
os.environ.setdefault("QUESTIONS_PATH", os.path.join(ROOT, "docs", "questions.json"))
os.environ["QUIZ_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import leaderboard as L  # noqa: E402
import live as V  # noqa: E402


class FakeTime:
    t = 1000.0

    @classmethod
    def monotonic(cls):
        return cls.t


V.time = FakeTime


def pid():
    return str(uuid.uuid4())


class Live(unittest.TestCase):
    def setUp(self):
        if os.path.exists(os.environ["QUIZ_DB"]):
            os.remove(os.environ["QUIZ_DB"])
        L.init()
        L._settled["week"] = None
        V._matches.clear(); V._queue.clear(); V._by_player.clear()
        FakeTime.t = 1000.0
        self.a, self.b = pid(), pid()

    def pair(self):
        r = V.queue({"name": "Ann", "player_id": self.a})
        self.assertEqual(r["status"], "waiting")
        FakeTime.t += 1
        r = V.queue({"name": "Bob", "player_id": self.b})
        self.assertEqual(r["status"], "matched")
        # the waiting player finds the match on their next ping
        self.assertEqual(V.queue({"name": "Ann", "player_id": self.a})["code"], r["code"])
        return r["code"]

    def play(self, code, a_right=True, b_right=True, ms=0):
        FakeTime.t += V.START_SECS + 0.1
        for i in range(V.N_QUESTIONS):
            st = V.state(code, self.a)
            self.assertEqual(st["phase"], "question")
            q = L.bank()[0][st["q"]["id"]]
            self.assertNotIn("a", st["q"])
            for p, ok in ((self.a, a_right), (self.b, b_right)):
                V.answer({"code": code, "player_id": p, "idx": i, "choice": q["a"] if ok else q["w"][0]})
            st = V.state(code, self.a)
            self.assertEqual(st["phase"], "reveal")
            self.assertEqual(st["reveal"]["a"], q["a"])
            FakeTime.t += V.REVEAL_SECS + 0.1
            V.state(code, self.b)

    def test_quick_match_and_win(self):
        code = self.pair()
        self.play(code, True, False)
        st = V.state(code, self.a)
        self.assertEqual(st["status"], "done")
        self.assertEqual(st["result"]["outcome"], "win")
        self.assertEqual(V.state(code, self.b)["result"]["outcome"], "loss")
        self.assertEqual(st["correct"], {"you": 10, "opp": 0})
        t = V.league("week", self.a)
        self.assertEqual(t["rows"][0]["name"], "Ann")
        self.assertEqual(t["rows"][0]["pts"], 3)
        self.assertEqual(t["you"]["rank"], 1)
        self.assertEqual(V.league("all", self.b)["you"]["pts"], 0)

    def test_draw(self):
        code = self.pair()
        self.play(code, True, True)
        self.assertEqual(V.state(code, self.a)["result"]["outcome"], "draw")
        self.assertEqual(V.league("week", self.a)["rows"][0]["pts"], 1)

    def test_timeout_and_late_answer(self):
        code = self.pair()
        FakeTime.t += V.START_SECS + 0.5
        for _ in range(3):
            FakeTime.t += 5
            V.state(code, self.a); V.state(code, self.b)
        r = V.answer({"code": code, "player_id": self.a, "idx": 0, "choice": "x"})
        self.assertTrue(r.get("late"))
        st = V.state(code, self.a)
        self.assertEqual(st["phase"], "reveal")
        self.assertIsNone(st["reveal"]["you"]["choice"])

    def test_friendly_not_ranked_and_h2h(self):
        code = V.create({"name": "Ann", "player_id": self.a})["code"]
        self.assertEqual(V.state(code, self.a)["status"], "waiting")
        V.join({"name": "Bob", "player_id": self.b, "code": code})
        self.play(code, False, True)
        r = V.state(code, self.a)["result"]
        self.assertEqual(r["outcome"], "loss")
        self.assertEqual(r["h2h"], {"won": 0, "drawn": 0, "lost": 1})
        self.assertEqual(V.league("all", self.a)["rows"], [])

    def test_forfeit_when_opponent_leaves(self):
        code = self.pair()
        FakeTime.t += V.START_SECS + 0.1
        for _ in range(4):
            FakeTime.t += 5
            V.state(code, self.a)             # only Ann keeps polling
        st = V.state(code, self.a)
        self.assertEqual(st["status"], "done")
        self.assertTrue(st["result"]["forfeit"])
        self.assertEqual(st["result"]["outcome"], "win")
        self.assertEqual(V.league("all", self.a)["rows"], [])   # forfeits don't count

    def test_outsider_cannot_read_and_pair_cap(self):
        code = self.pair()
        with self.assertRaises(L.NotFound):
            V.state(code, pid())
        with self.assertRaises(L.Conflict):
            V.join({"name": "Cy", "player_id": pid(), "code": code})
        self.play(code, True, False)
        for _ in range(4):
            V._by_player.clear()
            code = self.pair()
            self.play(code, True, False)
        self.assertEqual(V.league("week", self.a)["you"]["pts"], 3 * V.PAIR_CAP)

    def test_queue_stale_and_cancel(self):
        V.queue({"name": "Ann", "player_id": self.a})
        FakeTime.t += V.QUEUE_STALE + 1
        self.assertEqual(V.queue({"name": "Bob", "player_id": self.b})["status"], "waiting")
        V.cancel({"player_id": self.b})
        self.assertEqual(V.queue({"name": "Ann", "player_id": self.a})["status"], "waiting")


class Rooms(unittest.TestCase):
    def setUp(self):
        if os.path.exists(os.environ["QUIZ_DB"]):
            os.remove(os.environ["QUIZ_DB"])
        L.init()
        L._settled["week"] = None
        V._matches.clear(); V._queue.clear(); V._by_player.clear()
        V._rooms.clear(); V._room_of.clear()
        V._rng = random.Random(7)
        FakeTime.t = 1000.0
        self.p = [pid() for _ in range(4)]
        self.names = ["Ann", "Bob", "Cy", "Di"]

    def st(self, code, k, **kw):
        return V.room_state(code, self.p[k], **kw)

    def room4(self):
        code = V.room_create({"name": "Ann", "player_id": self.p[0]})["code"]
        seats = [0]
        for k in (1, 2, 3):
            seats.append(V.room_join({"name": self.names[k], "player_id": self.p[k], "code": code})["seat"])
        return code, seats

    def q(self, code, k=0):
        st = self.st(code, k)
        self.assertEqual(st["phase"], "question")
        self.assertNotIn("a", st["q"])
        self.assertNotIn("reveal", st)
        return st, L.bank()[0][st["q"]["id"]]

    def test_create_join_seat_start(self):
        code = V.room_create({"name": "Ann", "player_id": self.p[0]})["code"]
        st = self.st(code, 0)
        self.assertEqual(st["phase"], "lobby")
        self.assertTrue(st["host"])
        self.assertEqual(st["seats"][0]["name"], "Ann")
        # Ann alone: a private room needs a player on each team
        with self.assertRaises(L.Conflict):
            V.room_start({"player_id": self.p[0], "code": code})
        # joins fill the emptier team
        self.assertEqual(V.room_join({"name": "Bob", "player_id": self.p[1], "code": code})["seat"], 2)
        self.assertEqual(V.room_join({"name": "Cy", "player_id": self.p[2], "code": code.lower()})["seat"], 1)
        # rejoining is harmless; switching team moves you to a free seat there
        self.assertEqual(V.room_join({"name": "Cy", "player_id": self.p[2], "code": code})["seat"], 1)
        self.assertEqual(V.room_seat({"player_id": self.p[2], "code": code, "team": 1})["seat"], 3)
        self.assertIsNone(self.st(code, 0)["seats"][1])
        with self.assertRaises(L.Conflict):
            V.room_seat({"player_id": self.p[0], "code": code, "team": 1})       # team 1 full
        with self.assertRaises(L.Conflict):
            V.room_start({"player_id": self.p[1], "code": code})                 # not the host
        with self.assertRaises(L.NotFound):
            V.room_state(code, pid())                                            # outsiders can't look
        with self.assertRaises(ValueError):
            V.room_join({"name": "Zed", "player_id": pid(), "code": "NOPE"})
        V.room_start({"player_id": self.p[0], "code": code})
        st = self.st(code, 1)
        self.assertEqual(st["phase"], "countdown")
        self.assertTrue(st["seats"][1]["bot"])
        self.assertTrue(st["seats"][1]["name"].startswith(V._bot_tag()))
        with self.assertRaises(L.Conflict):
            V.room_join({"name": "Di", "player_id": self.p[3], "code": code})   # already started

    def test_full_game_scoring_and_results(self):
        code, seats = self.room4()
        self.assertEqual(sorted(seats), [0, 1, 2, 3])
        team = {k: V._team(seats[k]) for k in range(4)}
        V.room_start({"player_id": self.p[0], "code": code})
        FakeTime.t += V.R_COUNT + 0.01
        for i in range(V.N_QUESTIONS):
            st, q = self.q(code, 1)
            self.assertEqual(st["cur"], i)
            # team 0 answers right (one fast, one slow), team 1 wrong; all four answered -> early reveal
            for k in range(4):
                FakeTime.t += 1
                ok = team[k] == 0
                r = V.room_answer({"code": code, "player_id": self.p[k], "idx": i,
                                   "choice": q["a"] if ok else q["w"][0]})
                self.assertTrue(r["ok"])
                if k < 3:
                    self.assertEqual(self.st(code, 0)["phase"], "question")
            self.assertEqual(V.room_answer({"code": code, "player_id": self.p[0], "idx": i, "choice": q["a"]}),
                             {"ok": False, "late": True})
            st = self.st(code, 2)
            self.assertEqual(st["phase"], "reveal")
            self.assertEqual(st["reveal"]["a"], q["a"])
            right = [p for p in st["reveal"]["picks"] if p["pts"]]
            self.assertEqual(len(right), 2)
            for p in right:
                self.assertGreater(p["pts"], 100 * V.DIFF_MULT[q["d"]])
            FakeTime.t += V.R_REVEAL + 0.01
        st = self.st(code, 0)
        self.assertEqual(st["phase"], "done")
        res = st["result"]
        self.assertEqual(res["winner"], 0)
        self.assertEqual(res["outcome"], "win" if team[0] == 0 else "loss")
        self.assertEqual(res["scores"][1], 0)
        self.assertEqual(res["scores"][0], sum(res["pts"][:2]))
        self.assertTrue(res["flawless"] if team[0] == 0 else not res["flawless"])
        self.assertEqual(len(res["review"]), V.N_QUESTIONS)
        # whoever answered first on team 0 every time is the MVP, and "carried" the win
        mvp = res["mvp"]
        self.assertEqual(V._team(mvp), 0)
        carriers = [k for k in range(4) if self.st(code, k)["result"]["carry"]]
        self.assertEqual([seats[k] for k in carriers], [mvp])
        with L.db() as c:
            g = c.execute("SELECT * FROM live_games").fetchall()
            self.assertEqual(len(g), 1)
            self.assertEqual(g[0]["winner"], 0)
            rows = c.execute("SELECT * FROM live_players ORDER BY seat").fetchall()
            self.assertEqual([r["bot"] for r in rows], [0, 0, 0, 0])
            pts = {r["player_id"]: r["points"] for r in c.execute("SELECT * FROM points_log WHERE mode='live2'")}
            self.assertEqual(sorted(pts.values()), [10, 10])
            self.assertEqual(c.execute("SELECT COUNT(*) FROM answers WHERE mode='live2'").fetchone()[0], 40)

    def test_timer_expiry_late_and_duplicate(self):
        code, _ = self.room4()
        V.room_start({"player_id": self.p[0], "code": code})
        self.assertEqual(V.room_answer({"code": code, "player_id": self.p[0], "idx": 0, "choice": "x"}),
                         {"ok": False, "late": True})                         # before the question opens
        FakeTime.t += V.R_COUNT + 0.01
        st, q = self.q(code)
        self.assertTrue(V.room_answer({"code": code, "player_id": self.p[0], "idx": 0, "choice": q["a"]})["ok"])
        self.assertEqual(V.room_answer({"code": code, "player_id": self.p[0], "idx": 0, "choice": q["w"][0]}),
                         {"ok": False, "dup": True})
        self.assertTrue(self.st(code, 1)["seats"][0]["answered"])
        self.assertEqual(self.st(code, 1)["seats"][0]["pts"], 0)                # hidden until the reveal
        FakeTime.t += V.R_Q
        st = self.st(code, 1)
        self.assertEqual(st["phase"], "reveal")
        self.assertIsNone(st["reveal"]["picks"][1]["choice"])
        self.assertGreater(st["reveal"]["picks"][0]["pts"], 0)
        r = V.room_answer({"code": code, "player_id": self.p[1], "idx": 0, "choice": q["a"]})
        self.assertTrue(r.get("late"))
        FakeTime.t += V.R_REVEAL
        self.assertEqual(self.st(code, 1)["cur"], 1)

    def run_bots(self, code, k=0, right=True, watch=()):
        """Play to the end, only player k answering (instantly); players in `watch` just look on."""
        for _ in range(200):
            for w in watch:
                self.st(code, w)
            st = self.st(code, k)
            if st["phase"] == "done":
                return st
            if st["phase"] == "question" and st["mine"] is None:
                q = L.bank()[0][st["q"]["id"]]
                V.room_answer({"code": code, "player_id": self.p[k], "idx": st["cur"],
                               "choice": q["a"] if right else q["w"][0]})
            FakeTime.t += 1
        self.fail("game never ended")

    def test_quick_match_fills_with_bots(self):
        a = V.room_quick({"name": "Ann", "player_id": self.p[0]})
        b = V.room_quick({"name": "Bob", "player_id": self.p[1]})
        self.assertEqual(a["code"], b["code"])
        self.assertEqual(b["seat"], 2)                                         # other team
        st = self.st(a["code"], 0)
        self.assertEqual(st["phase"], "lobby")
        self.assertTrue(st["public"])
        self.assertLessEqual(st["ms_left"], V.QUICK_FILL * 1000)
        FakeTime.t += V.QUICK_FILL + 0.1
        st = self.st(a["code"], 1)
        self.assertIn(st["phase"], ("countdown", "question"))
        self.assertEqual([s["bot"] for s in st["seats"]], [False, True, False, True])
        # a newcomer gets a fresh room rather than the running game
        c = V.room_quick({"name": "Cy", "player_id": self.p[2]})
        self.assertNotEqual(c["code"], a["code"])
        # Ann answers everything right; Bob goes quiet and his bot stand-in takes over after 2 questions
        st = self.run_bots(a["code"], 0)
        self.assertEqual(st["result"]["outcome"], "win")
        self.assertTrue(st["seats"][2]["standin"])
        self.assertNotEqual(st["seats"][1]["name"], st["seats"][3]["name"])
        with L.db() as c:
            rows = c.execute("SELECT * FROM live_players ORDER BY seat").fetchall()
            self.assertEqual([r["bot"] for r in rows], [0, 1, 0, 1])
            self.assertEqual([r["player_id"] is None for r in rows], [False, True, False, True])
            self.assertEqual(rows[2]["standin"], 1)
            bot_pts = c.execute("SELECT COUNT(*) FROM points_log WHERE player_id IS NULL").fetchone()[0]
            self.assertEqual(bot_pts, 0)
            ann = c.execute("SELECT points FROM points_log WHERE player_id=? AND mode='live2'",
                            (self.p[0],)).fetchone()
            self.assertEqual(ann["points"], 10)
            # Bob only gets credit for answers he gave himself (none)
            self.assertIsNone(c.execute("SELECT 1 FROM points_log WHERE player_id=?", (self.p[1],)).fetchone())

    def test_quick_host_can_start_early_and_four_humans_start_at_once(self):
        a = V.room_quick({"name": "Ann", "player_id": self.p[0]})
        V.room_start({"player_id": self.p[0], "code": a["code"]})
        self.assertEqual(self.st(a["code"], 0)["phase"], "countdown")
        V._rooms.clear(); V._room_of.clear()
        codes = set(V.room_quick({"name": self.names[k], "player_id": self.p[k]})["code"] for k in range(4))
        self.assertEqual(len(codes), 1)
        self.assertEqual(self.st(codes.pop(), 3)["phase"], "countdown")

    def test_standin_comes_back(self):
        code = V.room_create({"name": "Ann", "player_id": self.p[0]})["code"]
        V.room_join({"name": "Bob", "player_id": self.p[1], "code": code})
        V.room_start({"player_id": self.p[0], "code": code})
        FakeTime.t += V.R_COUNT + 0.01
        for _ in range(2):              # Bob silent for two whole questions
            FakeTime.t += V.R_Q + 0.01
            self.st(code, 0)
            FakeTime.t += V.R_REVEAL + 0.01
            self.st(code, 0)
        self.assertTrue(self.st(code, 0)["seats"][2]["standin"])
        st = self.st(code, 1)           # Bob is back
        self.assertFalse(st["seats"][2]["standin"])

    def test_leave_and_rematch(self):
        code, seats = self.room4()
        V.room_start({"player_id": self.p[0], "code": code})
        V.room_leave({"player_id": self.p[3], "code": code})
        with self.assertRaises(L.NotFound):
            self.st(code, 3)
        st = self.st(code, 0)
        self.assertTrue(st["seats"][seats[3]]["standin"])
        self.run_bots(code, 0, watch=(1, 2))
        V.room_rematch({"player_id": self.p[1], "code": code})
        st = self.st(code, 2)
        self.assertEqual(st["phase"], "lobby")
        self.assertIsNone(st["seats"][seats[3]])                 # the leaver's seat is free again
        self.assertEqual(sum(1 for s in st["seats"] if s), 3)
        self.assertTrue(self.st(code, 0)["host"])                # Ann is still host
        V.room_join({"name": "Di", "player_id": self.p[3], "code": code})
        V.room_start({"player_id": self.p[0], "code": code})
        self.assertEqual(self.st(code, 3)["phase"], "countdown")

    def test_lobby_drops_quiet_players_and_lobby_hop(self):
        code = V.room_create({"name": "Ann", "player_id": self.p[0]})["code"]
        V.room_join({"name": "Bob", "player_id": self.p[1], "code": code})
        FakeTime.t += V.LOBBY_GONE + 1
        st = self.st(code, 0)
        self.assertIsNone(st["seats"][2])
        # leaving a lobby for another room is fine; the empty room disappears
        V.room_create({"name": "Ann", "player_id": self.p[0]})
        self.assertNotIn(code, V._rooms)

    def test_long_poll_wakes_on_change(self):
        code = V.room_create({"name": "Ann", "player_id": self.p[0]})["code"]
        v = self.st(code, 0)["v"]
        # nothing happens: returns the same version after the hold
        t0 = time.time()
        self.assertEqual(V.room_state(code, self.p[0], v=v, hold=0.3)["v"], v)
        self.assertGreaterEqual(time.time() - t0, 0.25)
        got = {}

        def wait():
            got["st"] = V.room_state(code, self.p[0], v=v, hold=5)
            got["t"] = time.time()
        th = threading.Thread(target=wait)
        th.start()
        time.sleep(0.2)
        t1 = time.time()
        V.room_join({"name": "Bob", "player_id": self.p[1], "code": code})
        th.join(3)
        self.assertFalse(th.is_alive())
        self.assertGreater(got["st"]["v"], v)
        self.assertEqual(got["st"]["seats"][2]["name"], "Bob")
        self.assertLess(got["t"] - t1, 1.0)

    def test_long_poll_after_leaving(self):
        code = V.room_create({"name": "Ann", "player_id": self.p[0]})["code"]
        V.room_join({"name": "Bob", "player_id": self.p[1], "code": code})
        v = self.st(code, 1)["v"]
        got = {}
        th = threading.Thread(target=lambda: got.setdefault("st", V.room_state(code, self.p[1], v=v, hold=5)))
        th.start()
        time.sleep(0.2)
        V.room_leave({"player_id": self.p[1], "code": code})
        th.join(3)
        self.assertEqual(got["st"], {"phase": "gone"})

    def test_routes_registered(self):
        for path in ("create", "join", "quick", "seat", "start", "answer", "leave", "rematch"):
            self.assertIn("/api/live/room/" + path, V.ROOM_POST)
        with self.assertRaises(ValueError):
            V.room_get({"code": "ABCDEF"}, None)


if __name__ == "__main__":
    unittest.main()
