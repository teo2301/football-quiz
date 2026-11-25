// Career-path guess matching. MUST stay identical to server/modes.py
// (server/test_leaderboard.py runs both over the same guesses).
var FQMatch = (function () {
  "use strict";
  var SPECIAL = {"ø": "o", "ß": "ss", "ı": "i", "ł": "l", "đ": "d", "æ": "ae", "œ": "oe", "ð": "d", "þ": "th"};

  function words(s) {
    s = String(s == null ? "" : s).toLowerCase();
    if (s.normalize) s = s.normalize("NFD");
    s = s.replace(/[̀-ͯ]/g, "");
    var out = "";
    for (var i = 0; i < s.length; i++) { var ch = s.charAt(i); out += SPECIAL.hasOwnProperty(ch) ? SPECIAL[ch] : ch; }
    out = out.replace(/['’`]/g, "");
    return out.split(/[^a-z0-9]+/).filter(function (w) { return w; });
  }
  function key(s) { return words(s).join(""); }

  function autoForms(name) {
    var w = words(name), out = [w.join("")];
    if (w.length > 1) {
      out.push(w.slice(1).join(""));
      if (w[w.length - 1].length >= 4) out.push(w[w.length - 1]);
    }
    return out;
  }

  // id -> {key: true}. Explicit names/aliases always count; automatic short
  // forms are dropped when another player could claim them too.
  function buildForms(players) {
    var explicit = {}, auto = {}, owners = {}, res = {};
    function own(k, id) { if (!owners[k]) owners[k] = {}; owners[k][id] = 1; }
    players.forEach(function (p) {
      var ex = {}; ex[key(p.n)] = 1;
      (p.al || []).forEach(function (a) { ex[key(a)] = 1; });
      var au = {};
      autoForms(p.n).forEach(function (k) { if (!ex[k]) au[k] = 1; });
      explicit[p.id] = ex; auto[p.id] = au;
      Object.keys(ex).forEach(function (k) { own(k, p.id); });
      Object.keys(au).forEach(function (k) { own(k, p.id); });
    });
    players.forEach(function (p) {
      var f = {};
      Object.keys(explicit[p.id]).forEach(function (k) { f[k] = 1; });
      Object.keys(auto[p.id]).forEach(function (k) { if (Object.keys(owners[k]).length === 1) f[k] = 1; });
      res[p.id] = f;
    });
    return res;
  }

  function lev(a, b, cap) {
    if (Math.abs(a.length - b.length) > cap) return cap + 1;
    var prev = [], i, j;
    for (j = 0; j <= b.length; j++) prev.push(j);
    for (i = 1; i <= a.length; i++) {
      var cur = [i], best = i;
      for (j = 1; j <= b.length; j++) {
        cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a.charAt(i - 1) !== b.charAt(j - 1) ? 1 : 0));
        if (cur[j] < best) best = cur[j];
      }
      if (best > cap) return cap + 1;
      prev = cur;
    }
    return prev[b.length];
  }

  function isMatch(guess, forms) {
    var g = key(guess);
    if (!g) return false;
    var ks = Object.keys(forms);
    for (var i = 0; i < ks.length; i++) {
      var f = ks[i];
      if (g === f) return true;
      var cap = f.length >= 9 ? 2 : f.length >= 5 ? 1 : 0;
      if (cap && lev(g, f, cap) <= cap) return true;
    }
    return false;
  }

  function points(clue) { return Math.max(1, 6 - clue); }

  return {words: words, key: key, buildForms: buildForms, isMatch: isMatch, points: points, lev: lev};
})();
if (typeof module !== "undefined") module.exports = FQMatch;
