#!/bin/sh
# (Re)start the v2 preview: test leaderboard on 127.0.0.1:8193 (DB /tmp/fq-v2-test.db)
# + static/proxy server on :8197. Logs in /tmp/fq-v2-*.log. Never touches the live DB.
cd "$(dirname "$0")/.." || exit 1
pkill -f "fq-v2-preview-marker" 2>/dev/null
sleep 1
[ -f /tmp/fq-v2-test.db ] || python3 -c "import sqlite3; s=sqlite3.connect('file:/mnt/ssdstorage/share/football-quiz/data/leaderboard.db?mode=ro', uri=True); d=sqlite3.connect('/tmp/fq-v2-test.db'); s.backup(d)"
# Phase 6: the venv (gitignored) has pywebpush, so the test server can send pushes; plain python3
# still runs it, just without sending. VAPID key: ~/keys/football-quiz-vapid.json (outside git).
# PUSH_EXTRA_HOSTS lets drive_preview's fake push service (127.0.0.1:8196) receive them.
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
QUIZ_DB=/tmp/fq-v2-test.db QUESTIONS_PATH="$PWD/docs/questions.json" QUIZ_HOST=127.0.0.1 QUIZ_PORT=8193 \
  VAPID_PATH="$HOME/keys/football-quiz-vapid.json" PUSH_EXTRA_HOSTS=127.0.0.1:8196 \
  setsid nohup $PY -u server/leaderboard.py fq-v2-preview-marker > /tmp/fq-v2-api.log 2>&1 < /dev/null &
setsid nohup python3 -u tools/preview.py fq-v2-preview-marker > /tmp/fq-v2-preview.log 2>&1 < /dev/null &
sleep 1
echo "preview: http://192.168.0.69:8197"
