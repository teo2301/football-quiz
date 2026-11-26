// Wiring. Loaded last.
"use strict";
(function () {
  var tabs = document.querySelectorAll("#tabbar button");
  for (var i = 0; i < tabs.length; i++) tabs[i].onclick = function () {
    var t = this.getAttribute("data-tab");
    if (t === "home") return goHome();
    if (t === "boards") return openBoard();
    if (t === "leaguesTab") return openLeaguesTab();
    if (t === "me") renderMe();
    show(t);
  };
  $("dPlay").onclick = startDaily;
  $("dShare").onclick = function () {
    var rec = dailyLog()[FQDaily.ukToday()];
    if (rec) shareText(shareDailyText(rec));
  };
  $("dBoard").onclick = function () { boardState.dperiod = "today"; openBoard("daily"); };
  $("setupBack").onclick = goHome;
  $("start").onclick = startClassic;
  $("next").onclick = nextQuestion;
  $("quit").onclick = quitGame;
  $("again").onclick = function () { (lastStart || startClassic)(); };
  $("fifty").onclick = useFifty;
  $("hiBtn").onclick = function () { hiloGuess("hi"); };
  $("loBtn").onclick = function () { hiloGuess("lo"); };
  $("hiNext").onclick = hiloNext;
  $("hiQuit").onclick = quitGame;
  $("cGuessBtn").onclick = careerGuess;
  $("cGuess").addEventListener("keydown", function (e) { if (e.key === "Enter") careerGuess(); });
  $("cClue").onclick = careerClue;
  $("cGiveUp").onclick = careerGiveUp;
  $("cNext").onclick = careerNext;
  $("cQuit").onclick = quitGame;
  $("menu").onclick = goHome;
  $("postBtn").onclick = savePostName;
  $("nameIn").addEventListener("keydown", function (e) { if (e.key === "Enter") savePostName(); });
  $("meSave").onclick = saveMeName;
  $("meName").addEventListener("keydown", function (e) { if (e.key === "Enter") saveMeName(); });
  window.addEventListener("online", function () { flushPending(); syncBadges(); });
  wireFeel();
  wireAppFeel();
  $("goal").onclick = endCelebrate;
  wireCompete();
  wireReport();
  wirePush();
  setInterval(tickCountdown, 1000);

  playerId();
  achInit();
  renderHome();
  loadBank().then(function () { renderHome(); flushPending(); handleHash(); }).catch(function () {
    $("subtitle").textContent = "Couldn't load the questions. Check your connection and reload.";
    handleHash();
  });
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(function () {});
})();

// Native-app touches: :active on iOS, haptic tick + release bounce on 3D buttons,
// app-bar shadow once the page scrolls, screen animation classes cleaned up.
function wireAppFeel() {
  var PRESS = ".btn,.chip,.seg button,.mode,.opt,.iconbtn,.back,.lifeline,.divstrip,.getapp,.lb li.tap,.tabbar button,.switch";
  var BOUNCE = ".btn,.chip,.seg button,.iconbtn,.lifeline,.divstrip";
  document.addEventListener("touchstart", function () {}, {passive: true});
  document.addEventListener("click", function (e) {
    var t = e.target && e.target.closest ? e.target.closest(PRESS) : null;
    if (!t || t.disabled) return;
    tick();
    if (!t.matches(BOUNCE)) return;
    t.classList.remove("boing");
    void t.offsetWidth;
    t.classList.add("boing");
  }, true);
  document.addEventListener("animationend", function (e) {
    var c = e.target.classList;
    if (!c) return;
    if (c.contains("boing")) c.remove("boing");
    if (e.animationName === "inFwd" || e.animationName === "inBack" || e.animationName === "inFade") c.remove("in-fwd", "in-back", "in-fade");
  });
  var ticking = false;
  window.addEventListener("scroll", function () {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(function () {
      ticking = false;
      document.body.classList.toggle("scrolled", window.pageYOffset > 4);
    });
  }, {passive: true});
}
