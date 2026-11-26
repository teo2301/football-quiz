"""Live 1v1 + ranked league tests. Identical in every quiz. Run: python3 test_live.py"""
import os
import sys
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


if __name__ == "__main__":
    unittest.main()
