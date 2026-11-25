"""Daily quiz picker. MUST stay identical to docs/js/daily-gen.js
(server/test_leaderboard.py checks both give the same ids).

Every UK date gets 7 questions: 2 Easy, 3 Medium, 2 Hard (2 Hard a day needs
at least 121 Hard questions for the 60-day no-repeat rule; the test checks it). Each day is picked
with mulberry32 seeded by the date as YYYYMMDD, from the questions not used in
the previous 60 days, walking forward from SEQ_START so the history is the same
everywhere. Pools are sorted by id so file order doesn't matter.
"""
from datetime import date, datetime, timedelta, timezone

SEQ_START = date(2026, 11, 1)   # history starts here (gives a pre-launch preview)
EPOCH = date(2026, 11, 30)      # number = days since this, so 2026-12-01 is #1
PLAN = (1, 1, 2, 2, 2, 3, 3)
NO_REPEAT = 60
M = 0xFFFFFFFF


def mulberry32(seed):
    a = seed & M

    def nxt():
        nonlocal a
        a = (a + 0x6D2B79F5) & M
        t = ((a ^ (a >> 15)) * (1 | a)) & M
        t = ((t + (((t ^ (t >> 7)) * (61 | t)) & M)) & M) ^ t
        return (t ^ (t >> 14)) & M
    return nxt


def uk_offset_hours(now_utc):
    """1 during British Summer Time, else 0 (last Sunday of March/October, 01:00 UTC)."""
    y = now_utc.year

    def last_sunday(month):
        d = date(y, month, 31)
        return d - timedelta(days=(d.weekday() + 1) % 7)
    start = datetime.combine(last_sunday(3), datetime.min.time(), timezone.utc) + timedelta(hours=1)
    end = datetime.combine(last_sunday(10), datetime.min.time(), timezone.utc) + timedelta(hours=1)
    return 1 if start <= now_utc < end else 0


def uk_today(now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    return (now_utc + timedelta(hours=uk_offset_hours(now_utc))).date()


def number(d):
    return (d - EPOCH).days


class DailyPicker:
    def __init__(self, bank):
        self.pools = {k: sorted(q["id"] for q in bank if q["d"] == k) for k in (1, 2, 3)}
        self.days = []          # days[i] = ids for SEQ_START + i

    def ids(self, d):
        idx = (d - SEQ_START).days
        if idx < 0:
            raise ValueError("date before the daily quiz started")
        while len(self.days) <= idx:
            self._next()
        return self.days[idx]

    def _next(self):
        i = len(self.days)
        d = SEQ_START + timedelta(days=i)
        rng = mulberry32(d.year * 10000 + d.month * 100 + d.day)
        recent = set()
        for past in self.days[max(0, i - NO_REPEAT):i]:
            recent.update(past)
        picks = []
        for k in PLAN:
            avail = [q for q in self.pools[k] if q not in recent and q not in picks]
            if not avail:
                avail = [q for q in self.pools[k] if q not in picks]
            picks.append(avail[rng() % len(avail)])
        self.days.append(picks)
