"""Achievement ids (badges) a player can show on the boards.

They are earned and computed in the app from the player's own history
(docs/js/ach-defs.js); the server only stores which ones a player has and
shows the best three next to their name. MUST match docs/js/ach-defs.js
(server/test_leaderboard.py checks ids, order and tiers).
"""

# (id, tier) in display order; tier 3 = gold, 2 = silver, 1 = bronze.
LIST = [
    ("streak3", 1), ("streak7", 2), ("streak30", 3), ("streak100", 3), ("cleansheet", 2),
    ("perfect10", 1), ("perfect20", 2), ("perfect50", 3), ("icecool", 2),
    ("surv15", 2), ("surv30", 3), ("blitz10", 1), ("blitz20", 3), ("hilo10", 2), ("spotted", 1), ("scout", 3),
    ("lg_pl", 2), ("lg_ll", 2), ("lg_sa", 2), ("lg_eu", 2), ("lg_in", 2),
    ("chalwin", 1), ("giant", 3), ("duowin", 1), ("carry", 2), ("flawless", 3), ("promoted", 2), ("topflight", 3),
    ("correct500", 2), ("seen500", 2),
]
TIER = {k: t for k, t in LIST}
ORDER = {k: i for i, (k, _) in enumerate(LIST)}
SHOW = 3        # badges shown next to a name


def clean(raw):
    """Known ids only, deduplicated, best first."""
    if not isinstance(raw, list) or len(raw) > 100:
        raise ValueError("bad badges")
    ids = {b for b in raw if isinstance(b, str) and b in TIER}
    return sorted(ids, key=lambda b: (-TIER[b], ORDER[b]))
