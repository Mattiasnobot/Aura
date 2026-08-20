// A window whose only job is answering "is she still going, or is she stuck".
// It rides the same event stream as the main page — every tab keeps its own
// cursor — so opening it changes nothing about the work it is watching.
const $ = (id) => document.getElementById(id);
let cursor = 0, busy = false, state = "idle", project = "";
let turnStarted = null, lastEvent = Date.now(), planSteps = [], offline = false;
// Polls must not overlap. A slow request lets the next tick start before the
// cursor has moved, so both fetch the same events and everything is handled
// twice — measured as a reply logged twice, and worst exactly when the machine
// is busy, which is when this window is being looked at.
let polling = false;

async function call(method, ...args) {
  const response = await fetch("/api/call", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Aura-Client": "html-ui-v1" },
    body: JSON.stringify({ method, args }),
  });
  if (!response.ok) throw new Error(String(response.status));
  return (await response.json()).result;
}

function addActivity(text) {
  const item = document.createElement("li");
  const stamp = document.createElement("time");
  stamp.textContent = new Date().toLocaleTimeString();
  const body = document.createElement("span");
  body.textContent = text;
  item.append(stamp, body);
  $("activity").prepend(item);
  while ($("activity").children.length > 40) $("activity").lastElementChild.remove();
}

function drawPlan() {
  $("planCard").hidden = planSteps.length === 0;
  $("plan").replaceChildren(...planSteps.map((step) => {
    const item = document.createElement("li");
    item.textContent = step.label;
    item.className = step.done ? "done" : (step.active ? "now" : "");
    return item;
  }));
}

function seconds(from) {
  return Math.max(0, Math.round((Date.now() - from) / 1000));
}

function clock(total) {
  const minutes = Math.floor(total / 60);
  return minutes ? `${minutes}m ${String(total % 60).padStart(2, "0")}s` : `${total}s`;
}

function draw() {
  $("state").textContent = offline ? "NO CONTACT" : state.toUpperCase();
  $("state").className = "state" + (offline ? " offline" : "");
  $("where").innerHTML = project
    ? `on <strong>${project}</strong>`
    : "no project in play";
  $("elapsed").textContent = turnStarted ? clock(seconds(turnStarted)) : "—";

  const quiet = seconds(lastEvent);
  $("quiet").textContent = `${clock(quiet)} ago`;
  // The one number that answers the question. Thresholds chosen from the
  // measured turn lengths on this machine: a local model goes quiet for tens
  // of seconds between tool calls quite normally, so under a minute is not
  // yet news; past two, something has stopped rather than slowed.
  $("quiet").className = "n-value " + (quiet < 60 ? "fresh" : quiet < 120 ? "stale" : "stuck");
  $("verdict").textContent = offline
    ? "Aura's window is not answering. Is she still running?"
    : !busy ? "Idle — nothing is running."
    : quiet < 60 ? "Working: something happened recently."
    : quiet < 120 ? "Quiet for a while. Long steps can look like this."
    : "Nothing for over two minutes. This may be stuck — Stop is in the main window.";
}

function handle(event) {
  lastEvent = Date.now();
  switch (event.type) {
    case "user_message": turnStarted = Date.now(); busy = true; addActivity("You: " + event.text); break;
    case "busy": busy = Boolean(event.value); if (!busy) turnStarted = null; break;
    case "state": state = event.value || "idle"; break;
    case "thinking":
      // Why this window could still say "stuck": for most of a turn this model
      // emits only private reasoning, and nothing else reached here at all.
      addActivity(`thinking… ${event.tokens} tokens`);
      break;
    case "project":
      if (event.project !== undefined) project = event.project || "";
      break;
    case "log": {
      const entry = event.event || {};
      addActivity(`${entry.action || "did something"} — ${entry.status || ""}`.trim());
      break;
    }
    case "plan_started":
      planSteps = (event.steps || []).map((label) => ({ label, done: false, active: false }));
      if (planSteps[0]) planSteps[0].active = true;
      drawPlan();
      break;
    case "plan_progress": {
      const index = Number(event.index);
      planSteps.forEach((step, i) => { step.done = i < index; step.active = i === index; });
      drawPlan();
      break;
    }
    case "plan_finished": planSteps.forEach((s) => { s.done = true; s.active = false; }); drawPlan(); break;
    case "reply":
      busy = false; turnStarted = null;
      if (event.project !== undefined) project = event.project || "";
      addActivity("Aura answered.");
      break;
  }
}

async function poll() {
  if (polling) return;
  polling = true;
  try {
    const events = await call("poll_events", cursor, 120);
    offline = false;
    for (const event of events) {
      const seq = Number(event._seq) || 0;
      if (seq && seq <= cursor) continue;   // belt as well as braces
      if (seq) cursor = seq;
      handle(event);
    }
  } catch {
    offline = true;
  } finally {
    polling = false;
  }
  draw();
}

(async () => {
  try {
    const boot = await call("get_bootstrap");
    cursor = Number(boot.event_cursor) || 0;
    const capabilities = boot.capabilities || {};
    project = capabilities.project || "";
    addActivity("Watching from here.");
  } catch { offline = true; }
  draw();
  setInterval(poll, 1000);
  // Redrawn every second regardless, so the two counters keep moving even
  // while nothing at all is arriving — which is exactly when they matter.
  setInterval(draw, 1000);
})();
