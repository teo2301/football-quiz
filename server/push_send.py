#!/usr/bin/env python3
"""Send the scheduled push notifications (Phase 6). Run it inside the scores container:

    docker exec football-quiz-scores python -u /app/push_send.py daily     # "Today's quiz is up"
    docker exec football-quiz-scores python -u /app/push_send.py reminder  # 18:30 nudge if today's daily isn't played
    docker exec football-quiz-scores python -u /app/push_send.py monday    # last week's promotion/relegation
    docker exec football-quiz-scores python -u /app/push_send.py test NAME # one test notification to a player

Suggested cron (server clock is UK time):
    0 9 * * *  docker exec football-quiz-scores python -u /app/push_send.py daily  >> /tmp/fq-push.log 2>&1
    5 9 * * 1  docker exec football-quiz-scores python -u /app/push_send.py monday >> /tmp/fq-push.log 2>&1

Safe to re-run: push_log remembers who has been sent what, so nobody gets a notification twice.
`daily` skips players who have already played today, and does nothing before daily quiz #1
(use --force to send anyway, for testing). `monday` settles the weeks first (same as the app's
lazy rollover), then tells everyone who was in a table last week how it ended.
--dry-run prints who would get what without sending or logging anything.
"""

import argparse
import sys
from datetime import date, timedelta

import daily_gen
import divisions
import leaderboard as lb
import push


def _targets(c, kind):
    return [r[0] for r in c.execute(
        "SELECT DISTINCT player_id FROM push_subs WHERE ',' || types || ',' LIKE ?", ("%," + kind + ",%",))]


def _deliver(kind, ref, jobs, dry):
    """jobs: [(pid, payload, ttl)] -> counts."""
    return _deliver_as(kind, kind, ref, jobs, dry)


def _deliver_as(kind, sub_type, ref, jobs, dry):
    """kind = the push_log key (once per player per ref); sub_type = which subscription switch it goes to."""
    n = {"players": 0, "ok": 0, "gone": 0, "error": 0, "skipped": 0}
    for pid, payload, ttl in jobs:
        if dry:
            print("would send %s to %s: %s - %s" % (kind, pid[:8], payload["title"], payload["body"]))
            n["players"] += 1
            continue
        if not lb.claim_push(kind, ref, pid):
            n["skipped"] += 1
            continue
        r = push.send_to(lb.db, pid, sub_type, payload, ttl)
        n["players"] += 1
        for k in ("ok", "gone", "error"):
            n[k] += r[k]
    return n


def daily(force=False, dry=False):
    today = daily_gen.uk_today(lb.clock())
    number = daily_gen.number(today)
    if number < 1 and not force:
        return {"skipped": "daily quiz #1 hasn't started yet (%s)" % today.isoformat()}
    jobs = []
    with lb.db() as c:
        played = {r[0] for r in c.execute("SELECT player_id FROM daily WHERE date = ?", (today.isoformat(),))}
        for pid in _targets(c, "daily"):
            if pid in played:
                continue
            streak = lb.streak_for(c, pid, today - timedelta(days=1))
            body = ("Keep your %d-day streak going." % streak if streak >= 2
                    else "7 questions, the same for everyone. How many can you get?")
            jobs.append((pid, {"title": "Football Quiz #%d is up" % number if number >= 1 else "Today's quiz is up",
                               "body": body, "url": "./#daily", "tag": "daily"}, 10 * 3600))
    return _deliver("daily", today.isoformat(), jobs, dry)


def reminder_targets(c, today):
    """Evening nudge recipients: daily pushes switched on, today's daily not played, not nudged today yet."""
    played = {r[0] for r in c.execute("SELECT player_id FROM daily WHERE date = ?", (today.isoformat(),))}
    nudged = {r[0] for r in c.execute("SELECT player_id FROM push_log WHERE kind = 'reminder' AND ref = ?",
                                      (today.isoformat(),))}
    return sorted(p for p in _targets(c, "daily") if p not in played and p not in nudged)


def reminder(force=False, dry=False):
    """Evening nudge (cron ~18:30 UK) for players who haven't played today's daily yet.
    Same opt-in as the morning push (the "daily" switch); at most once per player per day."""
    today = daily_gen.uk_today(lb.clock())
    number = daily_gen.number(today)
    if number < 1 and not force:
        return {"skipped": "daily quiz #1 hasn't started yet (%s)" % today.isoformat()}
    jobs = []
    with lb.db() as c:
        for pid in reminder_targets(c, today):
            streak = lb.streak_for(c, pid, today - timedelta(days=1))
            body = ("Don't lose your %d-day streak. " % streak if streak >= 2 else "") + "You haven't played today's 7 yet. Two minutes, same questions for everyone."
            jobs.append((pid, {"title": "Football Quiz #%d closes at midnight" % max(number, 1), "body": body, "url": "./#daily", "tag": "daily"},
                         5 * 3600))
    return _deliver_as("reminder", "daily", today.isoformat(), jobs, dry)


def monday(dry=False):
    lb.settle_weeks()
    week = divisions.week_key(lb.clock())
    prev = (date.fromisoformat(week) - timedelta(days=7)).isoformat()
    jobs = []
    with lb.db() as c:
        wanted = set(_targets(c, "division"))
        for r in c.execute("SELECT player_id, tier, moved FROM divisions WHERE week = ? AND moved IS NOT NULL", (prev,)):
            if r["player_id"] not in wanted:
                continue
            was = divisions.tier_name(r["tier"])
            if r["moved"] > 0:
                title, body = "Promoted!", "You're up from %s to %s this week." % (was, divisions.tier_name(r["tier"] - 1))
            elif r["moved"] < 0:
                title, body = "Relegated", "You've dropped from %s to %s. Win it back this week." % (
                    was, divisions.tier_name(r["tier"] + 1))
            else:
                title, body = "You stayed in %s" % was, "New week, new table. Push for promotion."
            jobs.append((r["player_id"], {"title": title, "body": body, "url": "./#leagues", "tag": "division"},
                         24 * 3600))
    return _deliver("division", prev, jobs, dry)


def test(name, dry=False):
    with lb.db() as c:
        row = c.execute("SELECT player_id FROM players WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if not row:
        return {"error": "no player called %s" % name}
    payload = {"title": "Football Quiz", "body": "Test notification - it works.", "url": "./", "tag": "test"}
    if dry:
        return {"would_send_to": row[0][:8]}
    out = {"ok": 0, "gone": 0, "error": 0}
    for kind in push.TYPES:     # whichever types they have on; each subscription once
        r = push.send_to(lb.db, row[0], kind, payload, 600)
        if r["ok"] or r["gone"] or r["error"]:
            return r
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("what", choices=("daily", "reminder", "monday", "test"))
    ap.add_argument("name", nargs="?")
    ap.add_argument("--force", action="store_true", help="daily/reminder: send even before daily quiz #1")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    lb.init()
    if not a.dry_run and not push.can_send():
        print("push isn't available (pywebpush missing or no VAPID key at %s)" % push.VAPID_PATH, file=sys.stderr)
        return 2
    if a.what == "daily":
        print(daily(a.force, a.dry_run))
    elif a.what == "reminder":
        print(reminder(a.force, a.dry_run))
    elif a.what == "monday":
        print(monday(a.dry_run))
    else:
        if not a.name:
            ap.error("test needs a player name")
        print(test(a.name, a.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
