// Daily quiz picker. MUST stay identical to server/daily_gen.py
// (server/test_leaderboard.py checks both give the same ids).
// 7 a day: 2 Easy, 4 Medium, 1 Hard, none repeated within 60 days.
var FQDaily = (function () {
  "use strict";
  var SEQ_START = Date.UTC(2026, 10, 1);   // 2026-11-01: history starts here
  var EPOCH = Date.UTC(2026, 10, 30);      // number = days since this (2026-12-01 = #1)
  var PLAN = [1, 1, 2, 2, 2, 2, 3];
  var NO_REPEAT = 60;
  var DAY = 86400000;

  function mulberry32(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return (t ^ (t >>> 14)) >>> 0;
    };
  }

  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function iso(ms) { var d = new Date(ms); return d.getUTCFullYear() + "-" + pad(d.getUTCMonth() + 1) + "-" + pad(d.getUTCDate()); }
  function parse(s) { var p = s.split("-"); return Date.UTC(+p[0], +p[1] - 1, +p[2]); }

  function lastSunday(y, month) {           // month 0-based; returns UTC ms of 01:00 that day
    var d = new Date(Date.UTC(y, month + 1, 0));
    return Date.UTC(y, month, d.getUTCDate() - d.getUTCDay(), 1);
  }
  function ukOffsetHours(nowMs) {
    var y = new Date(nowMs).getUTCFullYear();
    return nowMs >= lastSunday(y, 2) && nowMs < lastSunday(y, 9) ? 1 : 0;
  }
  function ukToday(nowMs) {
    if (nowMs === undefined) nowMs = Date.now();
    return iso(nowMs + ukOffsetHours(nowMs) * 3600000);
  }
  // Milliseconds until the next UK midnight.
  function msToReset(nowMs) {
    if (nowMs === undefined) nowMs = Date.now();
    var off = ukOffsetHours(nowMs) * 3600000;
    var local = nowMs + off;
    var next = (Math.floor(local / DAY) + 1) * DAY;
    var ms = next - local;
    // A clock change at 01:00 UTC never lands on midnight, so this is exact enough.
    return ms;
  }
  function number(dateStr) { return Math.round((parse(dateStr) - EPOCH) / DAY); }
  function addDays(dateStr, n) { return iso(parse(dateStr) + n * DAY); }

  function Picker(bank) {
    this.pools = {1: [], 2: [], 3: []};
    for (var i = 0; i < bank.length; i++) if (this.pools[bank[i].d]) this.pools[bank[i].d].push(bank[i].id);
    for (var k in this.pools) this.pools[k].sort(function (x, y) { return x < y ? -1 : x > y ? 1 : 0; });
    this.days = [];
  }
  Picker.prototype.ids = function (dateStr) {
    var idx = Math.round((parse(dateStr) - SEQ_START) / DAY);
    if (idx < 0) throw new Error("date before the daily quiz started");
    while (this.days.length <= idx) this._next();
    return this.days[idx];
  };
  Picker.prototype._next = function () {
    var i = this.days.length, ms = SEQ_START + i * DAY, d = new Date(ms);
    var rng = mulberry32(d.getUTCFullYear() * 10000 + (d.getUTCMonth() + 1) * 100 + d.getUTCDate());
    var recent = {}, picks = [];
    for (var j = Math.max(0, i - NO_REPEAT); j < i; j++)
      for (var m = 0; m < this.days[j].length; m++) recent[this.days[j][m]] = 1;
    for (var p = 0; p < PLAN.length; p++) {
      var pool = this.pools[PLAN[p]];
      var avail = pool.filter(function (q) { return !recent[q] && picks.indexOf(q) < 0; });
      if (!avail.length) avail = pool.filter(function (q) { return picks.indexOf(q) < 0; });
      picks.push(avail[rng() % avail.length]);
    }
    this.days.push(picks);
  };

  return {Picker: Picker, ukToday: ukToday, msToReset: msToReset, number: number,
          addDays: addDays, mulberry32: mulberry32, PLAN: PLAN};
})();
if (typeof module !== "undefined") module.exports = FQDaily;
