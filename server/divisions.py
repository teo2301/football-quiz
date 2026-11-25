"""Weekly divisions with promotion and relegation (pure helpers, no DB).

Weeks run Monday 00:00 to Monday 00:00 UK time and are keyed by the Monday's
date ("2026-11-30"). There are five tiers; a player joins a table in their
tier the first time they score points in a week (groups of up to 20). When
the week is over, each table's top 3 go up a tier and the bottom 3 go down
(tables under 10 players move a third of the table each way instead, with at
least one promotion once two people played). New players start in the bottom
tier.
"""
from datetime import datetime, time, timedelta, timezone

import daily_gen

TIERS = ["Premier Division", "Championship", "League One", "League Two", "Sunday League"]
BOTTOM = len(TIERS)
GROUP_SIZE = 20
MOVE = 3


def week_key(now_utc):
    """Monday (UK date) of the week `now_utc` falls in, as YYYY-MM-DD."""
    d = daily_gen.uk_today(now_utc)
    return (d - timedelta(days=d.weekday())).isoformat()


def week_end_utc(week):
    """The instant the week ends: the next Monday 00:00 UK, in UTC.
    Clocks change on Sundays at 01:00 UTC, so Monday midnight is never ambiguous."""
    monday = datetime.strptime(week, "%Y-%m-%d").date() + timedelta(days=7)
    midnight = datetime.combine(monday, time(0), timezone.utc)
    return midnight - timedelta(hours=daily_gen.uk_offset_hours(midnight))


def moves(n):
    """(promoted, relegated) counts for a table of n players."""
    if n >= 10:
        return MOVE, MOVE
    k = n // 3
    return (max(1, k) if n >= 2 else 0), k


def zones(n, tier):
    """moves(n), but nobody goes above the top tier or below the bottom one."""
    up, down = moves(n)
    return (0 if tier == 1 else up), (0 if tier == BOTTOM else down)


def settle(rows, tier):
    """rows: player ids in final table order (best first) -> [(pid, new_tier, moved)]."""
    n = len(rows)
    up, down = zones(n, tier)
    out = []
    for i, pid in enumerate(rows):
        if i < up:
            out.append((pid, tier - 1, 1))
        elif i >= n - down:
            out.append((pid, tier + 1, -1))
        else:
            out.append((pid, tier, 0))
    return out


def tier_name(tier):
    return TIERS[tier - 1]
