# Publishing the revamp (3D buttons + 2v2 Online), Football Quiz

None of this has been run yet. Only do it after Matteo has approved the preview (http://192.168.0.69:8197).
It adds no new dependencies, ports, containers or cron jobs. The APK doesn't need rebuilding, because the TWA loads the live site.

Branch `revamp` lives in the worktree `/mnt/ssdstorage/share/football-quiz-v2`. The live site and API are served from the
**main worktree** `/mnt/ssdstorage/share/football-quiz` (branch `main`, bind-mounted into `football-quiz-scores`).

## 0. Final checks (in the revamp worktree)

    cd /mnt/ssdstorage/share/football-quiz-v2
    git status                                    # clean, on revamp
    .venv/bin/python server/test_leaderboard.py   # 60 OK
    python3 server/test_live.py                   # 18 OK
    node tools/lint_es2017.js docs/js/*.js docs/sw.js
    python3 validate.py
    git merge-base --is-ancestor main revamp && echo fast-forward OK

## 1. Clear the uncommitted live-1v1 files out of the main worktree

The main worktree still has the live 1v1 + ranked league as **uncommitted** changes (server/live.py, docs/js/live.js,
server/test_live.py, plus hooks in leaderboard.py, app.js, core.js, modes.js, index.html, app.css, sw.js, weekly_report.sh).
The running API already uses them. `revamp` contains all of them: commit 5438cae has byte-identical copies, and the 2v2 work is built on top.
Stash them rather than delete them:

    cd /mnt/ssdstorage/share/football-quiz
    git status --short
    git stash push -u -m "live 1v1 WIP before revamp merge"
    git status --short                            # must be empty (data/ is gitignored and stays)

## 2. Fast-forward main and restart the API

    git merge --ff-only revamp
    docker compose restart football-quiz-scores   # ./server + ./docs are bind-mounted; no rebuild (no new deps)
    sleep 3; curl -s http://127.0.0.1:8093/api/health
    docker logs --tail 20 football-quiz-scores

The new tables `live_games` and `live_players` are created on start. Nothing existing changes.

## 3. Long-poll through the public Funnel (must hold 25 s)

    tools/longpoll_check.sh https://riccioserver.tail0d3eb6.ts.net:10000/api PUBLIC

Expect `long-poll held 25 s (curl exit 0)`. If Funnel cuts it earlier, set `HOLD = 15.0` in server/live.py
(also in world-quiz, the files stay identical), rerun `python3 server/test_live.py`, commit, and restart the container.
The client falls back to plain polling either way, so a cut is slower but not broken.

## 4. Push → GitHub Pages

    git push                                      # origin = git@github-quiz:teo2301/football-quiz.git, main
    # wait ~1 min, then:
    curl -s https://teo2301.github.io/football-quiz/sw.js | head -3    # VERSION fq-v14 (or later)

## 5. Smoke test on the phone (on 4G, outside the tailnet)

- Reopen the app twice so the new SW takes over. The buttons should be 3D, and Play should show the "2v2 Online" tile.
- Create a room and open the #r= link on a second device. Start with two humans and two bots, then play to the results.
- Quick match: on your own you should get bots after about 30 s.
- Ranked 1v1 and Classic / Daily still work. Old installed copies keep working, because the old API shapes are unchanged.

## Undo

    cd /mnt/ssdstorage/share/football-quiz
    git reset --hard 3efe17f && git push --force-with-lease     # back to the pre-revamp site
    git stash pop                                               # brings the 1v1 WIP back
    docker compose restart football-quiz-scores

The two extra tables are harmless if you leave them.
