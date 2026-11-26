# football-quiz-scores: the stdlib leaderboard + pywebpush for push notifications (Phase 6).
# The code itself is bind-mounted at /app (see docker-compose.yml), so code changes only need a
# restart; rebuild (docker compose build football-quiz-scores) only when requirements.txt changes.
FROM python:3.13-alpine
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt
USER 1000:1000
ENV PYTHONDONTWRITEBYTECODE=1 VAPID_PATH=/keys/vapid.json
CMD ["python", "-u", "/app/leaderboard.py"]
