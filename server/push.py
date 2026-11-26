"""Web Push for the Football Quiz (Phase 6).

Standard Web Push with VAPID: the app subscribes in the browser (Chrome, also
inside the Android TWA), POSTs the subscription to /api/push, and the server
sends three kinds of notification, each switched on or off by the player:

    daily      "Today's quiz is up"            push_send.py daily  (cron, ~09:00 UK)
    challenge  "<friend> beat your challenge"  sent straight away from POST /api/challenge/play
    division   Monday promotion/relegation     push_send.py monday (cron, Monday morning)

Sending needs `pywebpush` (the only non-stdlib dependency; see Dockerfile +
requirements.txt). Without it - or without the VAPID key file - storing
subscriptions still works and sending is simply skipped, so the stdlib-only
container keeps running until it's rebuilt.

The VAPID keypair lives OUTSIDE git: ~/keys/football-quiz-vapid.json on the
host, mounted read-only at /keys/vapid.json in the container (VAPID_PATH).
    {"public_key": <base64url uncompressed P-256 point>, "private_pem": <PKCS8 PEM>,
     "subject": <https://host with no path, or mailto:> (py_vapid rejects a path)}

Subscriptions that the push service answers with 404/410 are deleted.
Endpoints must be on a known push service (no sending to arbitrary URLs).
"""

import json
import os
import threading
import urllib.parse

TYPES = ("daily", "challenge", "division")
VAPID_PATH = os.environ.get("VAPID_PATH", "/keys/vapid.json")
PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "push.services.mozilla.com",
              "notify.windows.com", "push.apple.com")
# Tests only: extra "host:port" endpoints allowed over plain http (a local fake push service).
EXTRA_HOSTS = {h.strip() for h in os.environ.get("PUSH_EXTRA_HOSTS", "").split(",") if h.strip()}
MAX_SUBS = 5            # per player (oldest dropped)
TIMEOUT = 10

_vapid = {"mtime": None, "data": None, "key": None}
_lock = threading.Lock()


def _load():
    """The VAPID file, reloaded if it changes. None if missing/unreadable."""
    with _lock:
        try:
            mtime = os.stat(VAPID_PATH).st_mtime
        except OSError:
            _vapid.update(mtime=None, data=None, key=None)
            return None
        if mtime != _vapid["mtime"]:
            try:
                with open(VAPID_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data.get("public_key"), str) or not isinstance(data.get("private_pem"), str):
                    raise ValueError
            except (OSError, ValueError):
                data = None
            _vapid.update(mtime=mtime, data=data, key=None)
        return _vapid["data"]


def public_key():
    d = _load()
    return d["public_key"] if d else None


def _signer():
    """(Vapid instance, claims subject) or None when we can't send."""
    d = _load()
    if not d:
        return None
    try:
        from py_vapid import Vapid
    except ImportError:
        return None
    with _lock:
        if _vapid["key"] is None:
            _vapid["key"] = Vapid.from_pem(d["private_pem"].encode())
        return _vapid["key"], d.get("subject") or "https://teo2301.github.io"


def can_send():
    try:
        import pywebpush  # noqa: F401
    except ImportError:
        return False
    return _signer() is not None


def _host_ok(endpoint):
    u = urllib.parse.urlsplit(endpoint)
    if u.scheme == "http":
        return u.netloc in EXTRA_HOSTS
    if u.scheme != "https" or not u.hostname or u.username or u.password:
        return False
    if u.netloc in EXTRA_HOSTS:
        return True
    h = u.hostname.lower()
    return any(h == s or h.endswith("." + s) for s in PUSH_HOSTS)


def _b64ok(s, lo, hi):
    return isinstance(s, str) and lo <= len(s) <= hi and all(
        ch.isalnum() or ch in "-_=" for ch in s)


def clean_sub(raw):
    """A PushSubscription.toJSON() -> (endpoint, p256dh, auth). ValueError if it isn't one."""
    if not isinstance(raw, dict):
        raise ValueError("bad subscription")
    ep, keys = raw.get("endpoint"), raw.get("keys")
    if not isinstance(ep, str) or not 10 < len(ep) <= 1024 or not _host_ok(ep):
        raise ValueError("bad subscription endpoint")
    if not isinstance(keys, dict) or not _b64ok(keys.get("p256dh"), 80, 100) or not _b64ok(keys.get("auth"), 16, 32):
        raise ValueError("bad subscription keys")
    return ep, keys["p256dh"], keys["auth"]


def clean_types(raw):
    """["daily", ...] or {"daily": true, ...} -> sorted list of known types."""
    if isinstance(raw, dict):
        raw = [k for k, v in raw.items() if v is True]
    if not isinstance(raw, list) or not all(isinstance(t, str) for t in raw):
        raise ValueError("bad types")
    bad = [t for t in raw if t not in TYPES]
    if bad:
        raise ValueError("unknown notification type")
    return [t for t in TYPES if t in raw]


def send_one(endpoint, p256dh, auth, payload, ttl=6 * 3600):
    """-> "ok", "gone" (404/410: delete the subscription) or "error"."""
    signer = _signer()
    if not signer:
        return "error"
    from pywebpush import WebPushException, webpush
    key, subject = signer
    try:
        webpush({"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
                data=json.dumps(payload, ensure_ascii=False), vapid_private_key=key,
                vapid_claims={"sub": subject}, ttl=ttl, timeout=TIMEOUT)
        return "ok"
    except WebPushException as e:
        code = getattr(e.response, "status_code", None)
        return "gone" if code in (404, 410) else "error"
    except Exception as e:      # network trouble, bad keys: never take the server down
        import sys
        sys.stderr.write("push to %s failed: %r\n" % (urllib.parse.urlsplit(endpoint).netloc, e))
        return "error"


def send_to(db, pid, kind, payload, ttl=6 * 3600):
    """Send to every subscription of player `pid` that has `kind` switched on.
    `db` is a function returning a new sqlite connection. Returns {"ok": n, "gone": n, "error": n}."""
    out = {"ok": 0, "gone": 0, "error": 0}
    if not can_send():
        return out
    c = db()
    try:
        subs = [tuple(r) for r in c.execute(
            "SELECT endpoint, p256dh, auth, types FROM push_subs WHERE player_id = ?", (pid,))]
    finally:
        c.close()
    subs = [s for s in subs if kind in s[3].split(",")]
    if not subs:
        return out
    gone = []
    for ep, p, a, _ in subs:          # no DB transaction held while we talk to the push service
        r = send_one(ep, p, a, payload, ttl)
        out[r] += 1
        if r == "gone":
            gone.append(ep)
    if gone:
        with db() as c:
            c.executemany("DELETE FROM push_subs WHERE endpoint = ?", [(e,) for e in gone])
    return out


def send_async(fn, *args):
    """Fire-and-forget (the challenge push), so the player's request never waits on Google."""
    t = threading.Thread(target=_quiet, args=(fn,) + args, daemon=True)
    t.start()
    return t


def _quiet(fn, *args):
    try:
        fn(*args)
    except Exception as e:      # logged, never raised into the request
        import sys
        sys.stderr.write("push failed: %r\n" % (e,))
