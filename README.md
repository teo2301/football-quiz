# Football Quiz

General-knowledge football quiz, 1999 to today, mostly Premier League, La Liga and Serie A
(plus Europe and international). It's an installable PWA that works offline. Pure static files,
with no backend.

- `docs/` is the whole site (GitHub Pages serves this folder)
- `docs/questions.json` is the question bank. Each entry has `id`, `l` (pl/ll/sa/eu/in),
  `d` (1-3), `q`, `a` (the right answer), `w` (3 wrong answers) and `x` (the fact shown afterwards).
- `python3 validate.py` checks the bank (unique ids, 4 distinct options, ...)
- After editing `index.html`, bump `VERSION` in `docs/sw.js` so installed copies update.
  Question changes come through without a bump.

Live: https://teo2301.github.io/football-quiz/

Home server copy: `docker compose up -d` serves it on :8097
(tailnet HTTPS: https://riccioserver.tail0d3eb6.ts.net:8445).
