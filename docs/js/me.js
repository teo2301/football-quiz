// Me tab: name, record, privacy link.
"use strict";
function renderMe() {
  $("meName").value = myName();
  $("meMsg").classList.add("hidden");
  var log = dailyLog(), st = store.get("stats", {played: 0, right: 0, asked: 0, best: 0});
  $("mDaily").textContent = Object.keys(log).length;
  $("mDStreak").textContent = dailyStreak();
  $("mDBest").textContent = Math.max(store.get("dbest", 0), dailyStreak());
  $("mPlayed").textContent = st.played;
  $("mPct").textContent = st.asked ? Math.round(100 * st.right / st.asked) + "%" : "–";
  $("mSeen").textContent = store.get("seen", []).length;
  var b = bests();
  $("bSurvival").textContent = b.survival || "–";
  $("bBlitz").textContent = b.blitz || "–";
  $("bHilo").textContent = b.hilo || "–";
  $("bCareer").textContent = b.career || "–";
  renderAchGrid();
  renderFeel();
  renderPush();
  $("version").textContent = "Football Quiz v2 · " + BANK.length + " questions";
}

async function saveMeName() {
  var name = tidyName($("meName").value), m = $("meMsg");
  m.classList.remove("hidden");
  if (!validName(name)) { m.textContent = "Use 2-16 letters, numbers or spaces."; m.classList.add("err"); return; }
  $("meSave").disabled = true;
  var r = await claimName(name);
  $("meSave").disabled = false;
  m.textContent = r.text; m.classList.toggle("err", !r.ok);
}

// Android browser visitors (not the installed app) get a "Get the Android app" tile on home.
(function () {
  var inApp = (document.referrer || "").indexOf("android-app://") === 0 ||
    (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches);
  if (/Android/i.test(navigator.userAgent || "") && !inApp) {
    var t = document.getElementById("getApp");
    if (t) t.classList.remove("hidden");
  }
})();
