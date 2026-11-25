// Achievement definitions. The ids and tiers MUST match server/achievements.py
// (server/test_leaderboard.py checks). Tier 3 = gold, 2 = silver, 1 = bronze;
// boards show a player's best three (highest tier first, then this order).
// `ic` is an emoji, or 2-3 capital letters drawn in the scoreboard font.
var FQAch = (function () {
  "use strict";
  var LIST = [
    {id: "streak3", g: "Daily", ic: "⚽", t: 1, name: "Hat-trick", desc: "Play the daily quiz 3 days in a row"},
    {id: "streak7", g: "Daily", ic: "📅", t: 2, name: "Week in, week out", desc: "A 7-day daily streak"},
    {id: "streak30", g: "Daily", ic: "📆", t: 3, name: "Ever-present", desc: "A 30-day daily streak"},
    {id: "streak100", g: "Daily", ic: "👑", t: 3, name: "Club legend", desc: "A 100-day daily streak"},
    {id: "cleansheet", g: "Daily", ic: "🧤", t: 2, name: "Clean sheet", desc: "Get all 7 right in a daily quiz"},
    {id: "perfect10", g: "Classic", ic: "10", t: 1, name: "Perfect 10", desc: "10 out of 10 in a Classic quiz"},
    {id: "perfect20", g: "Classic", ic: "🎯", t: 2, name: "Twenty from twenty", desc: "20 out of 20 in a Classic quiz"},
    {id: "perfect50", g: "Classic", ic: "🏆", t: 3, name: "Immortal", desc: "50 out of 50 in a Classic quiz"},
    {id: "icecool", g: "Classic", ic: "🧊", t: 2, name: "Ice cool", desc: "A perfect Classic quiz on the 10-second timer"},
    {id: "surv15", g: "Modes", ic: "💪", t: 2, name: "Survivor", desc: "15 in a row in Survival"},
    {id: "surv30", g: "Modes", ic: "🦁", t: 3, name: "Iron man", desc: "30 in a row in Survival"},
    {id: "blitz10", g: "Modes", ic: "⚡", t: 1, name: "Quick feet", desc: "10 correct in a Blitz"},
    {id: "blitz20", g: "Modes", ic: "🚀", t: 3, name: "Lightning", desc: "20 correct in a Blitz"},
    {id: "hilo10", g: "Modes", ic: "🔢", t: 2, name: "Walking almanac", desc: "10 right calls in a row in Higher or lower"},
    {id: "spotted", g: "Modes", ic: "👀", t: 1, name: "Spotted early", desc: "Name a career-path player from his first club"},
    {id: "scout", g: "Modes", ic: "🔭", t: 3, name: "Scout of the year", desc: "20 points or more in a Career path game"},
    {id: "lg_pl", g: "Leagues", ic: "PL", t: 2, name: "Premier League expert", desc: "50 correct Premier League answers"},
    {id: "lg_ll", g: "Leagues", ic: "LL", t: 2, name: "La Liga expert", desc: "50 correct La Liga answers"},
    {id: "lg_sa", g: "Leagues", ic: "SA", t: 2, name: "Serie A expert", desc: "50 correct Serie A answers"},
    {id: "lg_eu", g: "Leagues", ic: "EU", t: 2, name: "European nights", desc: "50 correct Europe answers"},
    {id: "lg_in", g: "Leagues", ic: "INT", t: 2, name: "International duty", desc: "50 correct International answers"},
    {id: "chalwin", g: "Compete", ic: "🥊", t: 1, name: "Bragging rights", desc: "Win a challenge"},
    {id: "giant", g: "Compete", ic: "💥", t: 3, name: "Giant killer", desc: "Beat a mate from a higher division in a challenge"},
    {id: "promoted", g: "Compete", ic: "🎉", t: 2, name: "Promoted", desc: "Finish a week in the promotion places"},
    {id: "topflight", g: "Compete", ic: "💎", t: 3, name: "Top flight", desc: "Reach the Premier Division"},
    {id: "correct500", g: "Milestones", ic: "💯", t: 2, name: "500 club", desc: "500 correct answers in all"},
    {id: "seen500", g: "Milestones", ic: "📚", t: 2, name: "Encyclopedia", desc: "See 500 different questions"}
  ];
  var BY = {};
  LIST.forEach(function (a, i) { a.i = i; BY[a.id] = a; });

  // Best first: higher tier, then list order.
  function rank(ids) {
    return ids.filter(function (id) { return BY[id]; }).sort(function (a, b) {
      return BY[b].t - BY[a].t || BY[a].i - BY[b].i;
    });
  }
  return {LIST: LIST, BY: BY, rank: rank};
})();
if (typeof module !== "undefined") module.exports = FQAch;
