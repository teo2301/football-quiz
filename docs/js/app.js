// Wiring. Loaded last.
"use strict";
(function () {
  var tabs = document.querySelectorAll("#tabbar button");
  for (var i = 0; i < tabs.length; i++) tabs[i].onclick = function () {
    var t = this.getAttribute("data-tab");
    if (t === "home") return goHome();
    if (t === "boards") return openBoard();
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
  $("again").onclick = startClassic;
  $("menu").onclick = goHome;
  $("postBtn").onclick = savePostName;
  $("nameIn").addEventListener("keydown", function (e) { if (e.key === "Enter") savePostName(); });
  $("meSave").onclick = saveMeName;
  $("meName").addEventListener("keydown", function (e) { if (e.key === "Enter") saveMeName(); });
  window.addEventListener("online", flushPending);
  setInterval(tickCountdown, 1000);

  playerId();
  renderHome();
  loadBank().then(function () { renderHome(); flushPending(); }).catch(function () {
    $("subtitle").textContent = "Couldn't load the questions. Check your connection and reload.";
  });
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(function () {});
})();
