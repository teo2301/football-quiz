// Report a question (answer screen): wrong answer / out of date / typo / something else + note.
// Posts to /api/report; offline it waits in the pending queue like scores do.
"use strict";
var reportQ = null;
var REPORT_REASONS = [["wrong", "Wrong answer"], ["outdated", "Out of date"], ["typo", "Typo"], ["other", "Something else"]];

function reportedIds() { return store.get("reported", []); }

// Called by answer(): show the link under the explanation for this question.
function showReportLink(q) {
  reportQ = q;
  var b = $("reportBtn"), done = reportedIds().indexOf(q.id) >= 0;
  b.textContent = done ? "Reported - thanks" : "Something wrong with this question?";
  b.disabled = done;
  b.classList.remove("hidden");
}
function hideReportLink() { $("reportBtn").classList.add("hidden"); }

function openReport() {
  if (!reportQ) return;
  $("rpQ").textContent = reportQ.q;
  $("rpA").textContent = "Our answer: " + reportQ.a;
  $("rpNote").value = "";
  msg("rpMsg", "");
  pickReason(null);
  $("toast").classList.add("hidden");
  $("reportSheet").classList.remove("hidden");
}
function closeReport() { $("reportSheet").classList.add("hidden"); }

function pickReason(r) {
  var bs = $("rpWhy").children;
  for (var i = 0; i < bs.length; i++) bs[i].setAttribute("aria-pressed", bs[i].getAttribute("data-r") === r ? "true" : "false");
  $("rpWhy").setAttribute("data-r", r || "");
  $("rpNote").placeholder = r === "other" ? "What's wrong? (needed)" : "Anything to add? (optional)";
}

async function sendReport() {
  var reason = $("rpWhy").getAttribute("data-r"), note = $("rpNote").value.trim();
  if (!reason) return msg("rpMsg", "Pick what's wrong first.", true);
  if (reason === "other" && !note) return msg("rpMsg", "Tell us what's wrong in the box.", true);
  var body = {player_id: playerId(), id: reportQ.id, reason: reason, note: note.slice(0, 300)};
  $("rpSend").disabled = true;
  var queued = false;
  try { await api("/report", body); }
  catch (e) {
    if (e.permanent) { $("rpSend").disabled = false; return msg("rpMsg", e.message, true); }
    queuePost("report", body); queued = true;          // offline or busy: sent later
  }
  $("rpSend").disabled = false;
  var ids = reportedIds(); ids.push(reportQ.id); store.set("reported", ids.slice(-500));
  closeReport();
  showReportLink(reportQ);
  toast(queued ? "Saved - we'll send it when you're back online" : "Thanks - we'll check it");
}

function wireReport() {
  var box = $("rpWhy");
  REPORT_REASONS.forEach(function (r) {
    var b = h("button", {className: "chip", type: "button"}, r[1]);
    b.setAttribute("data-r", r[0]);
    b.onclick = function () { pickReason(r[0]); msg("rpMsg", ""); };
    box.appendChild(b);
  });
  $("reportBtn").onclick = openReport;
  $("rpSend").onclick = sendReport;
  $("rpCancel").onclick = closeReport;
  $("reportSheet").addEventListener("click", function (e) { if (e.target === this) closeReport(); });
}
