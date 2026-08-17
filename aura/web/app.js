"use strict";

const $ = (selector) => document.querySelector(selector);
const elements = {
  app: $("#app"), sidebar: $("#sidebar"), sidebarResizer: $("#sidebarResizer"),
  main: $(".main-panel"), face: $("#faceWrap"), state: $("#stateLabel"),
  activity: $("#activityText"), provider: $("#providerLabel"),
  statusLine: $("#statusLine"), sideMenu: $("#sideMenu"), menuButton: $("#menuButton"),
  toggleLogLabel: $("#toggleLogLabel"),
  networkLabel: $("#networkLabel"), pauseAutonomy: $("#pauseAutonomy"), conversation: $("#conversation"),
  composer: $("#composer"), send: $("#sendButton"), stop: $("#stopButton"),
  actionPanel: $("#actionPanel"), actionLog: $("#actionLog"), logResizer: $("#logResizer"),
  logCount: $("#logCount"), toggleLog: $("#toggleLogButton"), workspacePath: $("#workspacePath"),
  activityLogTab: $("#activityLogTab"), diagnosticsLogTab: $("#diagnosticsLogTab"),
  suggestionBar: $("#suggestionBar"), voiceButton: $("#voiceButton"), gazeLayer: $("#gazeLayer"),
  voiceButtonLabel: $("#voiceButtonLabel"), voiceTray: $("#voiceTray"),
  voiceStatus: $("#voiceStatus"), voicePartial: $("#voicePartial"),
  voiceMeterFill: $("#voiceMeterFill"), voiceRetry: $("#voiceRetry"), voiceCancel: $("#voiceCancel"),
  avatarCanvas: $("#avatarCanvas"), avatarMouth: $("#avatarMouth"),
  attachButton: $("#attachButton"), filePicker: $("#filePicker"), dropOverlay: $("#dropOverlay"),
  mindView: $("#mindView"), mindCanvas: $("#mindCanvas"), mindSearch: $("#mindSearch"),
  mindSummary: $("#mindSummary"), mindDetail: $("#mindDetail"), mindActions: $("#mindActions"),
  workspaceView: $("#workspaceView"), workspaceTree: $("#workspaceTree"),
  workspaceTreeLabel: $("#workspaceTreeLabel"),
  workspaceSearch: $("#workspaceSearch"), workspaceSummary: $("#workspaceSummary"),
  workspacePreview: $("#workspacePreview"), previewPath: $("#previewPath"), previewMeta: $("#previewMeta"),
  previewAsk: $("#previewAsk"), previewOpen: $("#previewOpen"), previewCompare: $("#previewCompare"),
  settingsModal: $("#settingsModal"), tasksModal: $("#tasksModal"), memoryModal: $("#memoryModal"),
  permissionsModal: $("#permissionsModal"), sessionsModal: $("#sessionsModal"),
  welcomeModal: $("#welcomeModal"), watchModal: $("#watchModal"),
  memoryList: $("#memoryList"),
  promptModal: $("#promptModal"), promptForm: $("#promptForm"), promptTitle: $("#promptTitle"),
  promptHint: $("#promptHint"), promptInput: $("#promptInput"), promptStatus: $("#promptStatus"),
  promptConfirm: $("#promptConfirm"), historyModal: $("#historyModal"), historyList: $("#historyList"),
  previewServerModal: $("#previewServerModal"), previewServerForm: $("#previewServerForm"),
  previewServerFolder: $("#previewServerFolder"), previewServerStart: $("#previewServerStart"),
  previewServerStatus: $("#previewServerStatus"), previewServerActions: $("#previewServerActions"),
  previewServerOpen: $("#previewServerOpen"), previewServerCheckAssets: $("#previewServerCheckAssets"),
  previewServerStop: $("#previewServerStop"), previewServerLog: $("#previewServerLog"),
  approvalModal: $("#approvalModal"), approvalCommand: $("#approvalCommand"),
  toastRegion: $("#toastRegion"),
};

let initialized = false;
let polling = false;
let busy = false;
let streamMessage = null;
let logCount = 0;
let logEvents = [];
let logMode = "activity";
let currentApproval = null;
let sidebarWidth = 250;
let logHeight = 170;
let logVisible = true;
let expandedSidebarWidth = 250;
let saveTimer = null;
let eventPollTimer = null;
// Speech cues are the only latency-critical events, so polling tightens while
// Aura is speaking and relaxes again the moment she stops.
const IDLE_POLL_MS = 140;
const SPEAKING_POLL_MS = 45;
let pollIntervalMs = IDLE_POLL_MS;
let lastPollReturn = performance.now();
let lastEventAgeMs = 0;
let pollFailures = 0;
let sessionRecoveryAttempted = false;
let eventCursor = 0;
let workspaceFiles = [];
let selectedWorkspaceFile = null;
let trashMode = false;
let trashItems = [];
let diffPickActive = false;
let diffFirstFile = null;
let previewServerState = { running: false };
let selectedMindNode = null;
let dragDepth = 0;
let personalMemories = [];
let memoryCategories = [];
let memoryConflicts = [];
let capabilityState = {};
let avatarMotion = null;
let voiceActive = false;
let voicePhase = "idle";
let voiceHoldTimer = null;
let voiceHolding = false;
let voiceSuppressClick = false;
let voiceStartPromise = null;

const MUTATION_TOOLS = new Set([
  "create_folder", "create_file", "write_file", "append_file", "replace_in_file",
  "write_files", "apply_edits", "copy_file", "move_file", "safe_delete_file", "import_file",
  "create_archive", "extract_archive",
]);

async function callApi(method, ...args) {
  const response = await fetch("/api/call", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Aura-Client": "html-ui-v1" },
    body: JSON.stringify({ method, args }),
  });
  let payload;
  try {
    payload = await response.json();
  } catch (_error) {
    throw new Error("Aura's local backend returned an unreadable response.");
  }
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || `Aura's local backend returned HTTP ${response.status}.`);
  }
  return payload.result;
}

function toast(text, error = false) {
  const item = document.createElement("div");
  item.className = `toast${error ? " error" : ""}`;
  item.textContent = text;
  elements.toastRegion.append(item);
  setTimeout(() => item.remove(), 4200);
}

function escapeText(text) {
  return String(text ?? "");
}

function appendInlineMarkdown(parent, value) {
  const text = escapeText(value);
  const pattern = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > cursor) parent.append(document.createTextNode(text.slice(cursor, match.index)));
    const token = match[0];
    if (token.startsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = token.slice(2, -2);
      parent.append(strong);
    } else if (token.startsWith("`")) {
      const code = document.createElement("code");
      code.textContent = token.slice(1, -1);
      parent.append(code);
    } else {
      const parts = token.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/);
      const link = document.createElement("a");
      link.textContent = parts[1];
      link.href = parts[2];
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      parent.append(link);
    }
    cursor = match.index + token.length;
  }
  if (cursor < text.length) parent.append(document.createTextNode(text.slice(cursor)));
}

function renderMessage(body, value) {
  const lines = escapeText(value).replace(/\r\n?/g, "\n").split("\n");
  body.replaceChildren();
  body.classList.add("rich-text");
  let paragraph = [];
  let list = null;
  let listType = null;
  let codeLines = null;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    const element = document.createElement("p");
    appendInlineMarkdown(element, paragraph.join("\n"));
    body.append(element);
    paragraph = [];
  };
  const closeList = () => { list = null; listType = null; };
  const appendListItem = (type, text) => {
    flushParagraph();
    if (!list || listType !== type) {
      list = document.createElement(type);
      listType = type;
      body.append(list);
    }
    const item = document.createElement("li");
    appendInlineMarkdown(item, text);
    list.append(item);
  };
  const flushCode = () => {
    const pre = document.createElement("pre");
    const code = document.createElement("code");
    code.textContent = codeLines.join("\n");
    pre.append(code);
    body.append(pre);
    codeLines = null;
  };

  for (const line of lines) {
    if (/^\s*```/.test(line)) {
      flushParagraph(); closeList();
      if (codeLines) flushCode(); else codeLines = [];
      continue;
    }
    if (codeLines) { codeLines.push(line); continue; }
    if (!line.trim()) { flushParagraph(); closeList(); continue; }
    const heading = line.match(/^\s*(#{1,4})\s+(.+)$/);
    if (heading) {
      flushParagraph(); closeList();
      const title = document.createElement(heading[1].length <= 2 ? "h3" : "h4");
      appendInlineMarkdown(title, heading[2]);
      body.append(title);
      continue;
    }
    const bullet = line.match(/^\s*[-*•]\s+(.+)$/);
    if (bullet) { appendListItem("ul", bullet[1]); continue; }
    const numbered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (numbered) { appendListItem("ol", numbered[1]); continue; }
    const quote = line.match(/^\s*>\s?(.*)$/);
    if (quote) {
      flushParagraph(); closeList();
      const blockquote = document.createElement("blockquote");
      appendInlineMarkdown(blockquote, quote[1]);
      body.append(blockquote);
      continue;
    }
    if (/^\s*---+\s*$/.test(line)) {
      flushParagraph(); closeList(); body.append(document.createElement("hr")); continue;
    }
    closeList();
    paragraph.push(line);
  }
  flushParagraph();
  if (codeLines) flushCode();
}

function addMessage(role, text, streaming = false) {
  const article = document.createElement("article");
  const isUser = role === "user";
  article.className = `message ${isUser ? "user" : "aura"}${streaming ? " streaming" : ""}`;
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = isUser ? "You" : "A";
  const body = document.createElement("div");
  body.className = "message-body";
  if (isUser || streaming) body.textContent = escapeText(text);
  else renderMessage(body, text);
  article.append(avatar, body);
  elements.conversation.append(article);
  elements.conversation.scrollTop = elements.conversation.scrollHeight;
  return { article, body, raw: escapeText(text) };
}

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function attachTaskCard(article, task) {
  if (!article || !task || (!(task.tools || []).length && task.status === "completed")) return;
  const card = document.createElement("div");
  card.className = "message-task-card";
  const head = document.createElement("div");
  head.className = "message-task-head";
  const title = document.createElement("strong");
  title.textContent = "Task details";
  const status = document.createElement("span");
  status.textContent = task.status || "running";
  head.append(title, status);
  const tools = document.createElement("div");
  tools.className = "message-task-tools";
  for (const name of [...new Set(task.tools || [])].slice(0, 8)) {
    const chip = document.createElement("span"); chip.className = "tool-chip"; chip.textContent = name; tools.append(chip);
  }
  const actions = document.createElement("div");
  actions.className = "message-task-actions";
  const action = (label, handler) => {
    const button = document.createElement("button"); button.textContent = label; button.addEventListener("click", handler); actions.append(button);
  };
  action("Details", () => openTasks(task.task_id));
  action("Workspace", openWorkspaceExplorer);
  if (String(task.request || "").trim()) action("Repeat", () => sendMessage(task.request));
  if ((task.tools || []).some(name => MUTATION_TOOLS.has(name))) action("Undo", () => rollbackTask(task));
  card.append(head, tools, actions);
  article.append(card);
}

async function showPendingProposals() {
  // A proposal that arrived while the window was shut comes back from the
  // conversation as plain text, with no way to answer it. The buttons are what
  // make it a decision, so they are restored on load rather than only when the
  // event happens to be live.
  const result = await callApi("list_proposals");
  if (!result.ok) return;
  for (const proposal of result.proposals || []) {
    const message = addMessage("assistant", "Still waiting on you:");
    attachProposal(message?.article, proposal);
  }
}

function attachProposal(article, proposal) {
  // A proposal is a decision, so it gets buttons rather than a paragraph the
  // user has to answer in prose.
  if (!article || !proposal) return;
  const card = document.createElement("div");
  card.className = "proposal-card";
  const head = document.createElement("div");
  head.className = "proposal-head";
  head.textContent = "Nothing has been changed yet";
  const body = document.createElement("p");
  body.textContent = proposal.request;
  const actions = document.createElement("div");
  actions.className = "proposal-actions";
  const approve = document.createElement("button");
  approve.className = "control-button";
  approve.textContent = "Do it";
  approve.addEventListener("click", async () => {
    const result = await callApi("approve_proposal", proposal.id);
    if (!result.ok) return toast(result.error, true);
    card.remove();
  });
  const dismiss = document.createElement("button");
  dismiss.className = "control-button";
  dismiss.textContent = "Leave it";
  dismiss.addEventListener("click", async () => {
    const result = await callApi("dismiss_proposal", proposal.id);
    if (!result.ok) return toast(result.error, true);
    card.remove();
  });
  actions.append(approve, dismiss);
  card.append(head, body, actions);
  article.append(card);
}

function attachRecallNote(article, recalled) {
  if (!article || !(recalled || []).length) return;
  const note = document.createElement("details");
  note.className = "memory-recall-note";
  const summary = document.createElement("summary");
  summary.textContent = `Used ${recalled.length} ${recalled.length === 1 ? "memory" : "memories"}`;
  note.append(summary);
  const list = document.createElement("ul");
  for (const item of recalled.slice(0, 8)) {
    const row = document.createElement("li");
    const value = document.createElement("span"); value.textContent = item.value;
    const reason = document.createElement("em"); reason.textContent = item.recall_reason;
    row.append(value, document.createTextNode(" — "), reason);
    list.append(row);
  }
  note.append(list);
  article.append(note);
}

function setSuggestions(items) {
  elements.suggestionBar.replaceChildren();
  for (const item of items.slice(0, 5)) {
    const button = document.createElement("button");
    button.className = "suggestion-chip";
    button.textContent = item.label;
    button.addEventListener("click", () => {
      if (item.action === "workspace") openWorkspaceExplorer();
      else if (item.action === "mind") openMind();
      else if (item.action === "memory") openPersonalMemory();
      else sendMessage(item.prompt || item.label);
    });
    elements.suggestionBar.append(button);
  }
}

function updateSuggestions(text = "", task = null) {
  const lower = String(text).toLowerCase();
  if (task && (task.tools || []).some(name => MUTATION_TOOLS.has(name))) {
    setSuggestions([
      { label: "Explore changed files", action: "workspace" },
      { label: "Validate the project", prompt: "Validate the project you just changed and report any issues." },
      { label: "Show recent tasks", prompt: "Summarize your most recent task and its confirmed result." },
      { label: "Open Aura Mind", action: "mind" },
    ]);
  } else if (/hello|hei|tere|ready|what would you like/.test(lower)) {
    setSuggestions([
      { label: "Explore workspace", action: "workspace" },
      { label: "What can you do?", prompt: "What can you do for me right now?" },
      { label: "Build something", prompt: "Help me choose a useful small project to build." },
      { label: "What Aura knows", action: "memory" },
      { label: "Open Aura Mind", action: "mind" },
    ]);
  } else {
    setSuggestions([
      { label: "Explore workspace", action: "workspace" },
      { label: "Continue", prompt: "Continue from your last confirmed result." },
      { label: "Verify the result", prompt: "Verify the latest result without changing anything." },
      { label: "Open Aura Mind", action: "mind" },
    ]);
  }
}

function clearConversation() {
  elements.conversation.replaceChildren();
  streamMessage = null;
}

function setState(state) {
  const allowed = ["idle", "listening", "thinking", "working", "success", "error"];
  const name = allowed.includes(state) ? state : "idle";
  elements.face.dataset.state = name;
  elements.face.setAttribute("aria-label", `Aura is ${name}`);
  elements.state.textContent = name.toUpperCase();
  elements.voiceButton.classList.toggle("listening", name === "listening");
  avatarMotion?.setState(name);
  const activity = {
    idle: "Aura is ready.", listening: "Listening locally…", thinking: "Thinking with LM Studio…",
    working: "Working safely in the workspace…", success: "Finished successfully.",
    error: "Something needs attention.",
  };
  elements.activity.textContent = activity[name];
}

function setBusy(value) {
  busy = Boolean(value);
  elements.send.disabled = busy;
  elements.stop.disabled = !busy;
  elements.composer.disabled = busy;
  if (!busy) elements.composer.focus();
}

function setProvider(payload) {
  elements.provider.classList.toggle("offline", payload.online === false);
  const count = payload.count ? ` • ${payload.count} models` : "";
  elements.provider.textContent = `LM Studio • ${payload.label || "offline"}${count}`;
  if (payload.error) elements.provider.title = payload.error;
}

function toggleSideMenu(open) {
  const show = open === undefined ? elements.sideMenu.classList.contains("hidden") : open;
  elements.sideMenu.classList.toggle("hidden", !show);
  if (show) {
    // Anchored under the button that opened it.
    const anchor = elements.menuButton.getBoundingClientRect();
    const sidebar = elements.sidebar.getBoundingClientRect();
    elements.sideMenu.style.top = `${anchor.bottom - sidebar.top + 6}px`;
  }
  elements.menuButton.setAttribute("aria-expanded", String(show));
  if (show) elements.sideMenu.querySelector(".menu-item")?.focus();
}

function updatePowerStatus(values = {}) {
  capabilityState = { ...capabilityState, ...values };
  // One line under the face, not four stacked labels. The detail that used to
  // be shouted — tool counts, autonomy mode, memory count — is true but rarely
  // what someone is looking for, so it moved into the tooltip.
  const depth = String(capabilityState.reasoning_depth || "balanced");
  const memories = Number(capabilityState.personal_memories) || 0;
  elements.statusLine.innerHTML = "";
  const dot = document.createElement("span");
  dot.className = "dot";
  elements.statusLine.append(dot, document.createTextNode("Local • private • calm"));
  elements.statusLine.title =
    `${depth[0].toUpperCase()}${depth.slice(1)} thinking • ${capabilityState.tools || "many"} tools • `
    + `${capabilityState.autonomy_mode || "balanced"} • ${memories} ${memories === 1 ? "memory" : "memories"}`;
}

const FRIENDLY_ACTIONS = Object.freeze({
  create_folder: "Created a folder", create_file: "Created a file", write_file: "Updated a file",
  write_files: "Updated project files", append_file: "Added to a file", replace_in_file: "Edited a file",
  apply_edits: "Applied file edits", read_file: "Inspected a file", read_many_files: "Inspected project files",
  list_files: "Checked workspace files", search_files: "Searched workspace files", file_info: "Checked file details",
  inspect_code: "Inspected project code", copy_file: "Copied a file", move_file: "Moved a file",
  safe_delete_file: "Moved a file to trash", undo_last_change: "Undid a workspace change",
  rollback_task: "Rolled back task changes", validate_project: "Validated the project",
  verify_final_state: "Verified the final files", command: "Ran a local check",
  import_file: "Imported a file", learn_profile: "Learned a confirmed preference",
  provider_recovery: "Recovered an LM Studio tool response", request: "Handled a request",
  copy_folder: "Copied a folder", move_folder: "Moved a folder",
  safe_delete_folder: "Moved a folder to trash", restore_from_trash: "Restored from trash",
  rename_item: "Renamed an item",
  start_preview_server: "Started a live preview", stop_preview_server: "Stopped the live preview",
});

function logDetail(event) {
  const value = event.error || event.stderr || event.stdout || event.path || event.reason || "";
  return String(value).slice(0, 1000);
}

function createLogRow(event, diagnostic = false) {
  const row = document.createElement("div");
  row.className = "log-row";
  const time = document.createElement("span");
  const parsedTime = new Date(event.time || "");
  time.textContent = Number.isNaN(parsedTime.valueOf())
    ? "--:--:--" : parsedTime.toLocaleTimeString([], { hour12: false });
  const status = document.createElement("span");
  const statusName = String(event.status || "ok").toLowerCase();
  status.className = statusName;
  status.textContent = statusName === "ok" ? "DONE" : statusName.toUpperCase();
  const action = document.createElement("span");
  const rawAction = String(event.action || "event");
  action.textContent = diagnostic
    ? rawAction
    : (FRIENDLY_ACTIONS[rawAction] || rawAction.replaceAll("_", " ").replace(/^./, letter => letter.toUpperCase()));
  row.title = logDetail(event);
  row.append(time, status, action);
  return row;
}

function renderActionLog() {
  const diagnostic = logMode === "diagnostics";
  const visible = diagnostic
    ? logEvents
    : logEvents.filter(event => FRIENDLY_ACTIONS[event.action] || String(event.status || "ok").toLowerCase() !== "ok");
  elements.actionLog.replaceChildren();
  if (!visible.length) {
    const empty = document.createElement("div");
    empty.className = "log-empty";
    empty.textContent = diagnostic ? "No diagnostic events in this session." : "No activity yet in this session.";
    elements.actionLog.append(empty);
  } else {
    for (const event of visible) elements.actionLog.append(createLogRow(event, diagnostic));
  }
  elements.actionLog.scrollTop = elements.actionLog.scrollHeight;
  logCount = visible.length;
  elements.logCount.textContent = `${logCount} event${logCount === 1 ? "" : "s"} this session`;
}

function setLogMode(mode) {
  logMode = mode === "diagnostics" ? "diagnostics" : "activity";
  const activity = logMode === "activity";
  elements.activityLogTab.classList.toggle("active", activity);
  elements.activityLogTab.setAttribute("aria-selected", String(activity));
  elements.diagnosticsLogTab.classList.toggle("active", !activity);
  elements.diagnosticsLogTab.setAttribute("aria-selected", String(!activity));
  renderActionLog();
}

function appendLog(event) {
  logEvents.push(event);
  if (logEvents.length > 250) logEvents = logEvents.slice(-250);
  renderActionLog();
}

function setVoiceLevel(value) {
  const level = Math.max(0, Math.min(1, Number(value) || 0));
  elements.voiceMeterFill.style.width = `${Math.round(level * 100)}%`;
  const meter = elements.voiceMeterFill.parentElement;
  meter?.setAttribute("aria-valuenow", String(Math.round(level * 100)));
}

function setVoiceSession(phase, message = "", text = "") {
  voicePhase = String(phase || "idle");
  voiceActive = ["starting", "calibrating", "listening", "processing"].includes(voicePhase);
  const visible = voicePhase !== "idle";
  elements.voiceTray.classList.toggle("hidden", !visible);
  elements.voiceButton.classList.toggle("capturing", voiceActive);
  elements.voiceButton.setAttribute("aria-pressed", String(voiceActive));
  elements.voiceButtonLabel.textContent = voiceActive
    ? (voicePhase === "processing" ? "Transcribing" : "Listening") : "Voice";
  const titles = {
    starting: "Opening microphone", calibrating: "Calibrating",
    listening: "Listening", processing: "Transcribing locally",
    recognized: "Speech recognized", calibrated: "Microphone calibrated",
    cancelled: "Voice cancelled", error: "Voice needs attention",
  };
  elements.voiceStatus.textContent = titles[voicePhase] || "Ready to listen";
  if (text) {
    elements.voicePartial.textContent = text;
    elements.voicePartial.classList.add("has-text");
  } else if (message) {
    elements.voicePartial.textContent = message;
    elements.voicePartial.classList.toggle("has-text", voicePhase === "error");
  }
  elements.voiceRetry.classList.toggle("hidden", voicePhase !== "error");
  elements.voiceCancel.textContent = voiceActive ? "×" : "–";
  if (["recognized", "calibrated", "cancelled"].includes(voicePhase)) {
    setTimeout(() => {
      if (voicePhase === phase) {
        elements.voiceTray.classList.add("hidden");
        setVoiceLevel(0);
        voicePhase = "idle";
      }
    }, voicePhase === "recognized" ? 1500 : 1100);
  }
}

async function beginVoice(mode = "toggle") {
  const result = await callApi("start_voice", mode);
  if (!result.ok) {
    setVoiceSession("error", result.error);
    toast(result.error, true);
  }
  return result;
}

async function endVoice(cancel = false) {
  const result = await callApi("stop_voice", Boolean(cancel));
  if (!result.ok) toast(result.error, true);
  return result;
}

async function toggleVoice() {
  if (voiceActive) return endVoice(false);
  return beginVoice("toggle");
}

async function handleEvent(event) {
  switch (event.type) {
    case "user_message":
      addMessage("user", event.text);
      break;
    case "stream_reset":
      // Aura is retrying, so the half-written reply is void. Clear it instead
      // of letting the next attempt pile up underneath the abandoned one.
      if (streamMessage) {
        streamMessage.raw = "";
        streamMessage.body.textContent = "";
      }
      break;
    case "stream_token":
      if (!streamMessage) streamMessage = addMessage("assistant", "", true);
      streamMessage.raw += event.text;
      streamMessage.body.textContent = streamMessage.raw;
      elements.conversation.scrollTop = elements.conversation.scrollHeight;
      break;
    case "reply":
      let completedMessage;
      if (event.streamed && streamMessage) {
        completedMessage = streamMessage;
        streamMessage.article.classList.remove("streaming");
        renderMessage(streamMessage.body, event.text);
        streamMessage = null;
      } else {
        completedMessage = addMessage("assistant", event.text);
      }
      attachTaskCard(completedMessage?.article, event.task);
      attachProposal(completedMessage?.article, event.proposal);
      attachRecallNote(completedMessage?.article, event.recalled);
      updateSuggestions(event.text, event.task);
      announce(event.text);
      break;
    case "state": setState(event.value); break;
    case "busy": setBusy(event.value); break;
    case "provider": setProvider(event); break;
    case "log": appendLog(event.event); break;
    case "notice": addMessage("assistant", event.text); toast(event.text, true); break;
    case "voice_session":
      setVoiceSession(event.phase, event.message, event.text);
      break;
    case "voice_level":
      setVoiceLevel(event.level);
      break;
    case "voice_partial":
      elements.voicePartial.textContent = event.text;
      elements.voicePartial.classList.add("has-text");
      break;
    case "voice_text":
      setVoiceSession("recognized", "", event.text);
      toast(`Heard: ${event.text}`);
      break;
    case "voice_error":
      setVoiceSession("error", event.message || "Voice input stopped safely.");
      break;
    case "voice_calibration":
      $("#microphoneStatus").textContent = `Calibrated • noise floor ${Math.round((event.noise_floor || 0) * 10000) / 100}%`;
      break;
    case "speech":
      avatarMotion?.setSpeaking(Boolean(event.active));
      elements.voiceButton.classList.toggle("speaking", Boolean(event.active));
      setPollInterval(event.active ? SPEAKING_POLL_MS : IDLE_POLL_MS);
      // Silence and failure are indistinguishable to the ear, so say so.
      if (event.preview && event.active === false && event.spoken === false) {
        toast(event.message || "Aura could not speak that preview.", true);
      }
      break;
    case "speech_cues":
      // The sound started on the Python side the moment these were pushed, so
      // the mouth clock has to begin where the audio already is.
      avatarMotion?.setSpeechCues(event.cues || [], event.duration_ms || 0,
                                  event.source || "timing", lastEventAgeMs);
      break;
    case "network": renderNetworkStatus(event); break;
    case "autonomy": renderAutonomyStatus(event); break;
    case "approval": showApproval(event); break;
    case "search_service":
      renderSearchServiceStatus();
      if (event.error) toast(event.error, true);
      break;
    case "approval_closed":
      // Answered elsewhere — Stop, an emergency stop, or shutdown.
      if (!currentApproval || currentApproval === event.approval_id) {
        currentApproval = null;
        elements.approvalModal.classList.add("hidden");
      }
      break;
    case "settings_saved": toast("Settings saved locally."); break;
    case "memory_learned": {
      const learned = event.memories || [];
      capabilityState.personal_memories = (Number(capabilityState.personal_memories) || 0) + learned.length;
      updatePowerStatus();
      if (learned.length) toast(`Aura learned: ${learned[0].value}`);
      if (!elements.memoryModal.classList.contains("hidden")) await openPersonalMemory(false);
      break;
    }
    case "memory_changed":
      if (!elements.memoryModal.classList.contains("hidden")) await openPersonalMemory(false);
      break;
  }
}

function setPollInterval(milliseconds) {
  if (!eventPollTimer || pollIntervalMs === milliseconds) return;
  pollIntervalMs = milliseconds;
  clearInterval(eventPollTimer);
  eventPollTimer = setInterval(pollEvents, milliseconds);
}

async function pollEvents() {
  if (polling) return;
  polling = true;
  const sentAt = performance.now();
  try {
    const events = await callApi("poll_events", eventCursor, 120);
    pollFailures = 0;
    // An event can be pushed at any point between two polls, so by the time it
    // arrives it is already this old on average. Lip sync is the one consumer
    // that cares: without this the mouth starts a whole poll behind the sound.
    const returnedAt = performance.now();
    lastEventAgeMs = (returnedAt - lastPollReturn) / 2 + (returnedAt - sentAt) / 2;
    lastPollReturn = returnedAt;
    for (const event of events) {
      await handleEvent(event);
      eventCursor = Math.max(eventCursor, Number(event._seq) || eventCursor);
    }
  } catch (error) {
    if (String(error).includes("Unauthorized local request")) {
      if (eventPollTimer) clearInterval(eventPollTimer);
      eventPollTimer = null;
      if (!sessionRecoveryAttempted) {
        sessionRecoveryAttempted = true;
        window.location.reload();
      }
      return;
    }
    console.error(error);
    pollFailures += 1;
    if (pollFailures === 3) toast("Aura's local connection was interrupted. Retrying…", true);
  } finally {
    polling = false;
  }
}

async function sendMessage(text = elements.composer.value) {
  const message = String(text).trim();
  if (!message || busy) return;
  try {
    const result = await callApi("submit", message);
    if (!result.ok) return toast(result.error, true);
    elements.composer.value = "";
    autoSizeComposer();
  } catch (error) {
    toast(String(error), true);
  }
}

function fileRowActionButton(label, handler, danger = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.classList.toggle("danger", danger);
  button.addEventListener("click", event => { event.stopPropagation(); handler(); });
  return button;
}

function suggestedCopyPath(path) {
  const slash = path.lastIndexOf("/");
  const dot = path.lastIndexOf(".");
  const hasExt = dot > slash;
  const stem = hasExt ? path.slice(0, dot) : path;
  const ext = hasExt ? path.slice(dot) : "";
  return `${stem} (copy)${ext}`;
}

function workspaceSummaryText() {
  return `${workspaceFiles.length} local file${workspaceFiles.length === 1 ? "" : "s"} • read-only previews`;
}

function renderWorkspaceTree() {
  const query = elements.workspaceSearch.value.trim().toLowerCase();
  const groupFor = file => file.path.includes("/") ? file.path.split("/")[0] : "";
  const visible = workspaceFiles
    .filter(file => !query || file.path.toLowerCase().includes(query))
    .sort((a, b) => groupFor(a).localeCompare(groupFor(b)) || a.path.localeCompare(b.path));
  elements.workspaceTree.replaceChildren();
  if (!visible.length) {
    const empty = document.createElement("div");
    empty.className = "preview-empty";
    empty.innerHTML = `<strong>${workspaceFiles.length ? "No matching files" : "Workspace is empty"}</strong><p>${workspaceFiles.length ? "Try another search." : "Drop or import a file to begin."}</p>`;
    elements.workspaceTree.append(empty);
    return;
  }
  let folder = null;
  for (const file of visible) {
    const nextFolder = file.path.includes("/") ? file.path.split("/")[0] : "Workspace root";
    if (nextFolder !== folder) {
      folder = nextFolder;
      const label = document.createElement("div"); label.className = "file-folder-label"; label.textContent = folder; elements.workspaceTree.append(label);
    }
    const row = document.createElement("div");
    row.className = `file-row${selectedWorkspaceFile?.path === file.path ? " selected" : ""}`;
    const main = document.createElement("button");
    main.type = "button";
    main.className = "file-row-main";
    main.setAttribute("role", "treeitem");
    main.title = file.path;
    const icon = document.createElement("span"); icon.className = "file-icon";
    icon.textContent = file.preview_kind === "image" ? "▧" : file.preview_kind === "rendered" ? "◫" : file.preview_kind === "text" ? "≡" : "◇";
    const name = document.createElement("span"); name.textContent = file.path.includes("/") ? file.path.split("/").slice(1).join("/") : file.path;
    const size = document.createElement("span"); size.className = "file-size"; size.textContent = formatBytes(file.size);
    main.append(icon, name, size);
    main.addEventListener("click", () => diffPickActive ? resolveDiffPick(file) : previewWorkspaceFile(file));
    const actions = document.createElement("div"); actions.className = "file-row-actions";
    actions.append(
      fileRowActionButton("Rename", () => promptRenameItem(file)),
      fileRowActionButton("Move", () => promptMoveItem(file)),
      fileRowActionButton("Copy", () => promptCopyItem(file)),
      fileRowActionButton("Delete", () => deleteWorkspaceItem(file), true),
    );
    row.append(main, actions);
    elements.workspaceTree.append(row);
  }
}

async function loadTrash() {
  elements.workspaceSummary.textContent = "Reading trash…";
  try {
    const result = await callApi("list_trash");
    if (!result.ok) throw new Error(result.error);
    trashItems = result.items || [];
    elements.workspaceSummary.textContent = `${trashItems.length} item${trashItems.length === 1 ? "" : "s"} in trash`;
    renderTrashList();
  } catch (error) {
    elements.workspaceSummary.textContent = "Trash unavailable";
    showPreviewEmpty("Could not read trash", String(error));
  }
}

function renderTrashList() {
  elements.workspaceTree.replaceChildren();
  if (!trashItems.length) {
    const empty = document.createElement("div");
    empty.className = "preview-empty";
    empty.innerHTML = "<strong>Trash is empty</strong><p>Deleted files and folders appear here and can be restored.</p>";
    elements.workspaceTree.append(empty);
    return;
  }
  for (const item of trashItems) {
    const row = document.createElement("div");
    row.className = "file-row";
    const main = document.createElement("div");
    main.className = "file-row-main";
    main.style.cursor = "default";
    const icon = document.createElement("span"); icon.className = "file-icon";
    icon.textContent = item.kind === "folder" ? "▤" : "◇";
    const name = document.createElement("span");
    name.textContent = item.original_path || item.trash_name;
    const when = document.createElement("span"); when.className = "file-size";
    when.textContent = item.deleted_at ? new Date(item.deleted_at).toLocaleString() : "";
    main.append(icon, name, when);
    const actions = document.createElement("div"); actions.className = "file-row-actions";
    if (item.original_path) actions.append(fileRowActionButton("Restore", () => restoreWorkspaceItem(item)));
    row.append(main, actions);
    elements.workspaceTree.append(row);
  }
}

function toggleTrashView() {
  trashMode = !trashMode;
  $("#workspaceTrashToggle").textContent = trashMode ? "Files" : "Trash";
  elements.workspaceTreeLabel.textContent = trashMode ? "Trash" : "Files";
  if (trashMode) loadTrash(); else loadWorkspace(selectedWorkspaceFile?.path);
}

function promptNewFile() {
  openPrompt({
    title: "New file", confirmLabel: "Create",
    hint: "Workspace-relative path, e.g. notes/todo.md",
    onSubmit: async path => {
      const result = await callApi("create_workspace_file", path, "");
      if (result.ok) { toast(`Created ${result.path}.`); await loadWorkspace(result.path); }
      return result;
    },
  });
}

function promptNewFolder() {
  openPrompt({
    title: "New folder", confirmLabel: "Create",
    hint: "Workspace-relative path, e.g. notes/archive",
    onSubmit: async path => {
      const result = await callApi("create_workspace_folder", path);
      if (result.ok) { toast(`Created ${result.path}.`); await loadWorkspace(); }
      return result;
    },
  });
}

function promptRenameItem(file) {
  const currentName = file.path.includes("/") ? file.path.split("/").pop() : file.path;
  const parent = file.path.includes("/") ? file.path.split("/").slice(0, -1).join("/") : "the workspace root";
  openPrompt({
    title: "Rename", confirmLabel: "Rename",
    hint: `Renaming within ${parent}`,
    value: currentName,
    onSubmit: async name => {
      const result = await callApi("rename_workspace_item", file.path, name);
      if (result.ok) { toast(`Renamed to ${result.path}.`); await loadWorkspace(result.path); }
      return result;
    },
  });
}

function promptMoveItem(file) {
  openPrompt({
    title: "Move", confirmLabel: "Move",
    hint: "New workspace-relative path",
    value: file.path,
    onSubmit: async destination => {
      const result = await callApi("move_workspace_item", file.path, destination);
      if (result.ok) { toast(`Moved to ${result.path}.`); await loadWorkspace(result.path); }
      return result;
    },
  });
}

function promptCopyItem(file) {
  openPrompt({
    title: "Copy", confirmLabel: "Copy",
    hint: "Destination for the copy",
    value: suggestedCopyPath(file.path),
    onSubmit: async destination => {
      const result = await callApi("copy_workspace_item", file.path, destination);
      if (result.ok) { toast(`Copied to ${result.path}.`); await loadWorkspace(result.path); }
      return result;
    },
  });
}

async function deleteWorkspaceItem(file) {
  if (!window.confirm(`Move "${file.path}" to Aura's recoverable trash?`)) return;
  const result = await callApi("delete_workspace_item", file.path);
  if (!result.ok) return toast(result.error, true);
  toast("Moved to trash. Restore it anytime from Trash.");
  if (selectedWorkspaceFile?.path === file.path) clearWorkspacePreview();
  await loadWorkspace();
}

async function restoreWorkspaceItem(item) {
  const result = await callApi("restore_workspace_item", item.trash_name);
  if (!result.ok) return toast(result.error, true);
  toast(`Restored to ${result.path}.`);
  await loadTrash();
}

function clearWorkspacePreview() {
  selectedWorkspaceFile = null;
  elements.previewPath.textContent = "Choose a file";
  elements.previewMeta.textContent = "Safe read-only preview";
  elements.previewAsk.disabled = true;
  elements.previewOpen.disabled = true;
  elements.previewCompare.disabled = true;
  showPreviewEmpty("Select a workspace file", "Text, code, images, HTML, and SVG can be previewed locally.");
}

function armDiffPick() {
  if (!selectedWorkspaceFile) return;
  diffPickActive = true;
  diffFirstFile = selectedWorkspaceFile;
  elements.workspaceSummary.textContent = `Comparing ${diffFirstFile.path} — click another file, or Esc to cancel.`;
}

function cancelDiffPick() {
  if (!diffPickActive) return;
  diffPickActive = false;
  diffFirstFile = null;
  elements.workspaceSummary.textContent = workspaceSummaryText();
  renderWorkspaceTree();
}

async function resolveDiffPick(file) {
  const first = diffFirstFile;
  diffPickActive = false;
  diffFirstFile = null;
  elements.workspaceSummary.textContent = workspaceSummaryText();
  renderWorkspaceTree();
  if (first.path === file.path) return toast("Choose a different file to compare.", true);
  await renderDiff(first, file);
}

async function renderDiff(left, right) {
  elements.previewPath.textContent = `${left.path} ↔ ${right.path}`;
  elements.previewMeta.textContent = "Comparing files…";
  document.querySelector(".workspace-preview-panel")?.classList.add("active");
  try {
    const result = await callApi("compare_workspace_files", left.path, right.path);
    if (!result.ok) throw new Error(result.error);
    elements.previewMeta.textContent = result.different
      ? `Differences found${result.truncated ? " • diff truncated" : ""}`
      : "Files are identical";
    if (!result.different) {
      showPreviewEmpty("Files are identical", `${left.path} and ${right.path} have the same content.`);
      return;
    }
    elements.workspacePreview.replaceChildren();
    const pre = document.createElement("pre"); pre.className = "diff-view";
    for (const line of (result.diff || "").split("\n")) {
      const span = document.createElement("span");
      span.textContent = line;
      if (line.startsWith("+") && !line.startsWith("+++")) span.className = "diff-add";
      else if (line.startsWith("-") && !line.startsWith("---")) span.className = "diff-del";
      else if (line.startsWith("@@")) span.className = "diff-hunk";
      pre.append(span);
    }
    elements.workspacePreview.append(pre);
  } catch (error) {
    showPreviewEmpty("Compare failed", String(error));
  }
}

async function openHistory() {
  openModal(elements.historyModal);
  elements.historyList.textContent = "Loading…";
  try {
    const result = await callApi("workspace_change_history", 20);
    if (!result.ok) throw new Error(result.error);
    elements.historyList.replaceChildren();
    if (!result.changes.length) { elements.historyList.textContent = "No workspace changes recorded yet."; return; }
    let activeShown = false;
    for (const change of result.changes) {
      const card = document.createElement("div"); card.className = "task-card";
      const copy = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = FRIENDLY_ACTIONS[change.operation] || change.operation;
      const meta = document.createElement("p");
      const when = change.time ? new Date(change.time).toLocaleString() : "";
      meta.textContent = `${change.task_id ? `Task ${change.task_id}` : "Workspace panel"} • ${when}${change.undone ? " • undone" : ""}`;
      const paths = document.createElement("p"); paths.className = "task-summary";
      paths.textContent = (change.paths || []).join(", ") || "(no paths recorded)";
      copy.append(title, meta, paths);
      const actions = document.createElement("div"); actions.className = "task-card-actions";
      if (!change.undone && !activeShown) {
        activeShown = true;
        actions.append(fileRowActionButton("Undo", async () => {
          const undone = await callApi("undo_workspace_change");
          if (!undone.ok) return toast(undone.error, true);
          toast("Workspace change undone.");
          closeModal(elements.historyModal);
          await loadWorkspace();
        }, true));
      }
      card.append(copy, actions);
      elements.historyList.append(card);
    }
  } catch (error) {
    elements.historyList.textContent = String(error);
  }
}

function renderPreviewServerStatus() {
  if (previewServerState.running) {
    elements.previewServerStatus.className = "modal-status";
    const scope = previewServerState.path === "." ? "the whole workspace" : previewServerState.path;
    elements.previewServerStatus.textContent = `Running at ${previewServerState.url} — serving "${scope}".`;
    elements.previewServerActions.classList.remove("hidden");
    elements.previewServerStart.textContent = "Restart preview";
  } else {
    elements.previewServerStatus.className = "modal-status";
    elements.previewServerStatus.textContent = "Not running.";
    elements.previewServerActions.classList.add("hidden");
    elements.previewServerStart.textContent = "Start preview";
  }
}

function renderPreviewServerLog(entries) {
  if (!entries.length) { elements.previewServerLog.textContent = "No requests yet."; return; }
  elements.previewServerLog.textContent = entries.map(entry => {
    const time = entry.time ? new Date(entry.time * 1000).toLocaleTimeString() : "";
    return `${time}  ${entry.method} ${entry.path} → ${entry.status}`;
  }).join("\n");
}

async function refreshPreviewServer() {
  const status = await callApi("preview_server_status");
  previewServerState = status;
  renderPreviewServerStatus();
  if (status.running) {
    const log = await callApi("preview_server_log", 50);
    renderPreviewServerLog(log.entries || []);
  } else {
    elements.previewServerLog.textContent = "";
  }
}

async function openPreviewServerModal() {
  elements.previewServerFolder.value = "";
  openModal(elements.previewServerModal);
  await refreshPreviewServer();
}

async function submitPreviewServerStart(event) {
  event.preventDefault();
  const folder = elements.previewServerFolder.value.trim();
  elements.previewServerStatus.className = "modal-status";
  elements.previewServerStatus.textContent = "Starting…";
  const result = await callApi("start_preview_server", folder || ".");
  if (!result.ok) {
    elements.previewServerStatus.className = "modal-status error";
    elements.previewServerStatus.textContent = result.error;
    return;
  }
  toast(`Live preview started at ${result.url}.`);
  await refreshPreviewServer();
}

async function stopPreviewServerHandler() {
  const result = await callApi("stop_preview_server");
  if (!result.ok) return toast(result.error, true);
  toast("Live preview stopped.");
  await refreshPreviewServer();
}

function openPreviewServerInBrowser() {
  if (previewServerState.running) window.open(previewServerState.url, "_blank", "noopener");
}

async function checkPreviewAssets() {
  const folder = previewServerState.running ? previewServerState.path : (elements.previewServerFolder.value.trim() || ".");
  const result = await callApi("check_workspace_assets", folder);
  if (!result.ok) return toast(result.error, true);
  if (!result.broken.length) {
    toast(`Checked ${result.checked} HTML file${result.checked === 1 ? "" : "s"} — no broken local references found.`);
    elements.previewServerLog.textContent = `Asset check: ${result.checked} HTML file(s), no broken references.`;
    return;
  }
  toast(`${result.broken.length} broken asset reference${result.broken.length === 1 ? "" : "s"} found.`, true);
  elements.previewServerLog.textContent = "Asset check found broken references:\n" +
    result.broken.map(item => `${item.file} → ${item.reference}`).join("\n");
}

function showPreviewEmpty(title, detail) {
  elements.workspacePreview.replaceChildren();
  const empty = document.createElement("div");
  empty.className = "preview-empty";
  const icon = document.createElement("span"); icon.textContent = "◇";
  const strong = document.createElement("strong"); strong.textContent = title;
  const copy = document.createElement("p"); copy.textContent = detail;
  empty.append(icon, strong, copy); elements.workspacePreview.append(empty);
}

async function loadWorkspace(selectPath = null) {
  elements.workspaceSummary.textContent = "Reading protected workspace…";
  try {
    const result = await callApi("workspace_snapshot");
    if (!result.ok) throw new Error(result.error);
    workspaceFiles = result.files || [];
    elements.workspaceSummary.textContent = workspaceSummaryText();
    renderWorkspaceTree();
    const wanted = selectPath && workspaceFiles.find(file => file.path === selectPath);
    if (wanted) await previewWorkspaceFile(wanted);
  } catch (error) {
    elements.workspaceSummary.textContent = "Workspace unavailable";
    showPreviewEmpty("Could not read workspace", String(error));
  }
}

async function openWorkspaceExplorer(selectPath = null) {
  if (!elements.mindView.classList.contains("hidden")) closeMind();
  elements.workspaceView.classList.remove("hidden");
  elements.workspaceSearch.value = "";
  trashMode = false;
  diffPickActive = false;
  diffFirstFile = null;
  $("#workspaceTrashToggle").textContent = "Trash";
  elements.workspaceTreeLabel.textContent = "Files";
  await loadWorkspace(selectPath);
  elements.workspaceSearch.focus();
}

function closeWorkspaceExplorer() {
  elements.workspaceView.classList.add("hidden");
  document.querySelector(".workspace-preview-panel")?.classList.remove("active");
  diffPickActive = false;
  diffFirstFile = null;
  elements.composer.focus();
}

async function previewWorkspaceFile(file) {
  selectedWorkspaceFile = file;
  renderWorkspaceTree();
  elements.previewPath.textContent = file.path;
  elements.previewMeta.textContent = `${formatBytes(file.size)} • loading safe preview…`;
  elements.previewAsk.disabled = false;
  elements.previewOpen.disabled = false;
  elements.previewCompare.disabled = false;
  document.querySelector(".workspace-preview-panel")?.classList.add("active");
  showPreviewEmpty("Loading preview", file.path);
  try {
    const result = await callApi("preview_workspace_file", file.path);
    if (!result.ok) throw new Error(result.error);
    elements.previewMeta.textContent = `${formatBytes(result.size)} • ${result.kind}${result.truncated ? " • preview truncated" : ""}`;
    elements.workspacePreview.replaceChildren();
    if (result.kind === "text") {
      const pre = document.createElement("pre"); pre.textContent = result.content; elements.workspacePreview.append(pre);
    } else if (result.kind === "rendered") {
      const frame = document.createElement("iframe");
      frame.title = `Safe preview of ${file.path}`;
      frame.setAttribute("sandbox", "allow-same-origin");
      frame.setAttribute("referrerpolicy", "no-referrer");
      frame.src = result.url;
      elements.previewMeta.textContent += " • scripts off";
      elements.workspacePreview.append(frame);
    } else if (result.kind === "image") {
      const image = document.createElement("img"); image.alt = file.name; image.src = `data:${result.mime};base64,${result.content}`; elements.workspacePreview.append(image);
    } else {
      showPreviewEmpty("Preview unavailable", result.message || "This binary file stays protected in the workspace.");
    }
  } catch (error) {
    showPreviewEmpty("Preview failed", String(error));
  }
}

function bufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 32768) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
  }
  return btoa(binary);
}

async function importFiles(fileList) {
  const files = [...fileList];
  if (!files.length) return;
  if (files.length > 5) return toast("Import up to 5 files at a time.", true);
  elements.attachButton.disabled = true;
  try {
    const items = [];
    for (const file of files) {
      if (file.size > 1_500_000) throw new Error(`${file.name} exceeds the 1.5 MB local import limit.`);
      items.push({ name: file.name, content: bufferToBase64(await file.arrayBuffer()) });
    }
    const result = await callApi("import_files", items, ".");
    if (!result.ok) throw new Error(result.error);
    const report = `Imported ${result.count} file${result.count === 1 ? "" : "s"} safely into the workspace:\n${result.files.map(path => `- \`${path}\``).join("\n")}`;
    addMessage("assistant", report);
    toast("Files imported into Aura's protected workspace.");
    updateSuggestions(report, { status: "completed", tools: ["import_file"] });
    await openWorkspaceExplorer(result.files[0]);
  } catch (error) {
    toast(String(error), true);
  } finally {
    elements.attachButton.disabled = false;
    elements.filePicker.value = "";
  }
}

function autoSizeComposer() {
  elements.composer.style.height = "auto";
  elements.composer.style.height = `${Math.min(elements.composer.scrollHeight, 160)}px`;
}

// Keyboard and screen-reader focus must stay inside the open dialog. Rather
// than cycling Tab by hand, the rest of the page is made inert: it removes it
// from the tab order *and* from the accessibility tree, so a screen reader
// cannot wander into content the dialog is covering.
const modalStack = [];
const modalOpeners = new WeakMap();

function applyModalFocusContainment() {
  const top = modalStack[modalStack.length - 1] || null;
  elements.app.inert = Boolean(top);
  for (const modal of document.querySelectorAll(".modal-backdrop")) {
    modal.inert = Boolean(top) && modal !== top;
  }
}

function openModal(modal) {
  const opener = document.activeElement;
  if (opener && opener !== document.body && !opener.closest(".modal-backdrop")) {
    modalOpeners.set(modal, opener);
  }
  modal.classList.remove("hidden");
  const index = modalStack.indexOf(modal);
  if (index >= 0) modalStack.splice(index, 1);
  modalStack.push(modal);
  applyModalFocusContainment();
  modal.querySelector("input, select, textarea, button")?.focus();
}

function closeModal(modal) {
  if (modal === elements.approvalModal && currentApproval) return;
  modal.classList.add("hidden");
  const index = modalStack.indexOf(modal);
  if (index >= 0) modalStack.splice(index, 1);
  applyModalFocusContainment();
  // Back to the control that opened it, so the keyboard does not restart
  // from the top of the page after every dialog.
  const opener = modalOpeners.get(modal);
  modalOpeners.delete(modal);
  if (opener && opener.isConnected && !opener.closest(".hidden")) opener.focus();
}

let promptSubmitHandler = null;

function openPrompt({ title, hint, value = "", confirmLabel = "Confirm", onSubmit }) {
  elements.promptTitle.textContent = title;
  elements.promptHint.textContent = hint || "";
  elements.promptInput.value = value;
  elements.promptConfirm.textContent = confirmLabel;
  elements.promptStatus.className = "modal-status";
  elements.promptStatus.textContent = "";
  promptSubmitHandler = onSubmit;
  openModal(elements.promptModal);
  elements.promptInput.focus();
  elements.promptInput.select();
}

async function submitPrompt(event) {
  event.preventDefault();
  if (!promptSubmitHandler) return;
  const value = elements.promptInput.value.trim();
  if (!value) return;
  elements.promptStatus.className = "modal-status";
  elements.promptStatus.textContent = "Working…";
  try {
    const result = await promptSubmitHandler(value);
    if (!result || result.ok === false) {
      elements.promptStatus.className = "modal-status error";
      elements.promptStatus.textContent = (result && result.error) || "That did not work.";
      return;
    }
    closeModal(elements.promptModal);
  } catch (error) {
    elements.promptStatus.className = "modal-status error";
    elements.promptStatus.textContent = String(error);
  }
}

function setSelectValue(select, value) {
  const normalized = value == null ? "" : String(value);
  if (normalized && ![...select.options].some(option => option.value === normalized)) {
    select.add(new Option(normalized, normalized));
  }
  select.value = normalized;
}

function memoryCategoryLabel(value) {
  return String(value || "personal").replaceAll("_", " ").replace(/\b\w/g, letter => letter.toUpperCase());
}

function populateMemoryCategories(select, selected = "personal") {
  select.replaceChildren();
  for (const category of memoryCategories) select.add(new Option(memoryCategoryLabel(category), category));
  setSelectValue(select, selected);
}

function renderConversation(messages) {
  elements.conversation.replaceChildren();
  streamMessage = null;
  for (const item of messages || []) {
    if (item && item.role && item.text) addMessage(item.role, item.text);
  }
}

function announce(text) {
  const region = $("#announcer");
  // Re-setting identical text does not re-announce, so clear it first.
  region.textContent = "";
  window.setTimeout(() => { region.textContent = String(text || "").trim(); }, 40);
}

const WELCOME_STEPS = 3;
let welcomeStep = 1;

function showWelcomeStep(step) {
  welcomeStep = Math.min(Math.max(step, 1), WELCOME_STEPS);
  for (const section of elements.welcomeModal.querySelectorAll(".welcome-step")) {
    section.classList.toggle("hidden", Number(section.dataset.step) !== welcomeStep);
  }
  $("#welcomeStepCount").textContent = `Step ${welcomeStep} of ${WELCOME_STEPS}`;
  $("#welcomeBack").classList.toggle("hidden", welcomeStep === 1);
  $("#welcomeNext").textContent = welcomeStep === WELCOME_STEPS ? "Start using Aura" : "Next";
}

function startOnboarding(workspace) {
  $("#welcomeWorkspace").textContent =
    workspace || elements.workspacePath.textContent || "this computer";
  $("#welcomeStatus").className = "modal-status";
  $("#welcomeStatus").textContent = "";
  showWelcomeStep(1);
  openModal(elements.welcomeModal);
}

async function checkWelcomeConnection() {
  const status = $("#welcomeStatus");
  status.className = "modal-status";
  status.textContent = "Looking for LM Studio…";
  const result = await callApi("get_models", $("#welcomeUrl").value);
  if (!result.ok) {
    status.className = "modal-status error";
    // The address is the part a person can actually act on.
    status.textContent = `${result.error} Check that LM Studio is running and its local server is on.`;
    return false;
  }
  const select = $("#welcomeModel");
  select.replaceChildren(new Option("Automatic selection", ""));
  for (const model of result.models) select.add(new Option(model, model));
  setSelectValue(select, result.models.find(model => !model.toLowerCase().includes("embed")) || "");
  status.className = "modal-status";
  status.textContent = result.models.length
    ? `Connected. ${result.models.length} model(s) available.`
    : "Connected, but no model is loaded yet. Load one in LM Studio.";
  return true;
}

async function finishOnboarding(skipped = false) {
  const connected = !skipped && $("#welcomeModel").options.length > 1;
  const result = await callApi("complete_onboarding",
    connected ? $("#welcomeUrl").value : "",
    connected ? $("#welcomeModel").value : "");
  if (!result.ok) return toast(result.error, true);
  closeModal(elements.welcomeModal);
  if (!skipped) toast("Aura is ready. Ask her for something.");
}

async function startNewSession() {
  const result = await callApi("new_session");
  if (!result.ok) return toast(result.error, true);
  renderConversation([]);
  toast("Started a new conversation. The previous one is kept.");
}

async function openSessions(focus = true) {
  // Opening the panel starts from the whole list; refreshes keep what was typed.
  if (focus) { $("#sessionSearch").value = ""; openModal(elements.sessionsModal); }
  const list = $("#sessionList");
  list.textContent = "Reading local conversations…";
  const showArchived = $("#showArchivedSessions").checked;
  const query = $("#sessionSearch").value.trim();
  try {
    const result = query
      ? await callApi("search_conversations", query, showArchived)
      : await callApi("list_sessions", 30, showArchived);
    if (!result.ok) throw new Error(result.error);
    // A later keystroke may have already asked a different question.
    if ($("#sessionSearch").value.trim() !== query) return;
    list.replaceChildren();
    const usable = (result.sessions || []).filter(item => query || item.messages > 0);
    if (!usable.length) {
      const empty = document.createElement("div"); empty.className = "memory-empty";
      const title = document.createElement("strong");
      title.textContent = query ? "Nothing found" : "No conversations yet";
      const detail = document.createElement("p");
      detail.textContent = query
        ? `No conversation contains every word of “${query}”.`
        : "Say something and it will be kept here.";
      empty.append(title, detail); list.append(empty); return;
    }
    for (const session of usable) {
      const card = document.createElement("article"); card.className = "memory-item";
      const copy = document.createElement("div"); copy.className = "memory-item-copy";
      const head = document.createElement("div"); head.className = "memory-item-head";
      const badge = document.createElement("span"); badge.className = "memory-category";
      badge.textContent = session.id === result.current ? "Current"
        : query ? `${session.hits} match${session.hits === 1 ? "" : "es"}`
        : `${session.messages} messages`;
      head.append(badge);
      if (session.archived) {
        const archived = document.createElement("span"); archived.className = "memory-category";
        archived.textContent = "Archived"; head.append(archived);
      }
      const value = document.createElement("p");
      value.textContent = session.title || "Untitled conversation";
      const meta = document.createElement("small");
      const when = session.last_used || session.started
        || (session.matches && session.matches.length ? session.matches[0].time : null);
      meta.textContent = when ? new Date(when).toLocaleString() : "";
      copy.append(head, value, meta);
      for (const match of session.matches || []) {
        const hit = document.createElement("p"); hit.className = "session-hit";
        const who = document.createElement("strong");
        who.textContent = (match.role === "user" ? "You" : "Aura") + ": ";
        hit.append(who, document.createTextNode(match.snippet));
        copy.append(hit);
      }
      const actions = document.createElement("div"); actions.className = "memory-actions";
      if (session.id !== result.current) {
        const open = document.createElement("button");
        open.type = "button"; open.textContent = "Open";
        open.addEventListener("click", async () => {
          const opened = await callApi("open_session", session.id);
          if (!opened.ok) return toast(opened.error, true);
          renderConversation(opened.conversation);
          closeModal(elements.sessionsModal);
          toast("Continuing that conversation.");
        });
        actions.append(open);
        const archive = document.createElement("button");
        archive.type = "button";
        archive.textContent = session.archived ? "Restore" : "Archive";
        archive.addEventListener("click", async () => {
          const changed = await callApi("archive_session", session.id, !session.archived);
          if (!changed.ok) return toast(changed.error, true);
          await openSessions(false);
          toast(session.archived ? "Conversation restored." : "Archived. Nothing was deleted.");
        });
        actions.append(archive);
      }
      // Exporting the conversation you are in is fine — it only reads it.
      const exportButton = document.createElement("button");
      exportButton.type = "button"; exportButton.textContent = "Export";
      exportButton.addEventListener("click", async () => {
        const written = await callApi("export_conversation", session.id);
        if (!written.ok) return toast(written.error, true);
        toast(`Saved to workspace as ${written.path}.`);
      });
      actions.append(exportButton);
      card.append(copy, actions); list.append(card);
    }
  } catch (error) {
    list.textContent = String(error);
  }
}

async function renderSearchServiceStatus() {
  const target = $("#searchServiceStatus");
  if (!target) return;
  const status = await callApi("search_service_status");
  if (!status.ok) { target.textContent = status.error; return; }
  if (status.error) target.textContent = status.error;
  else if (status.running && status.adopted)
    target.textContent = `Running on ${status.endpoint} — you started it, so Aura leaves it running when it quits.`;
  else if (status.running)
    target.textContent = `Running on ${status.endpoint} in ${status.container ? "a container" : "a local process"} — Aura started it and stops it on quit.`;
  else target.textContent = "Not running. Set the folder above, or start SearXNG yourself.";
}

async function openWatchPanel(focus = true) {
  if (focus) openModal(elements.watchModal);
  const list = $("#watchList");
  list.textContent = "Reading what Aura watches…";
  try {
    const result = await callApi("list_scheduled");
    if (!result.ok) throw new Error(result.error);
    list.replaceChildren();
    for (const check of result.available_checks || []) {
      const card = document.createElement("article"); card.className = "memory-item";
      const copy = document.createElement("div"); copy.className = "memory-item-copy";
      const head = document.createElement("div"); head.className = "memory-item-head";
      const badge = document.createElement("span");
      badge.className = check.enabled ? "memory-category" : "memory-category off";
      badge.textContent = check.enabled ? "Watching" : "Off";
      head.append(badge);
      const value = document.createElement("p");
      value.textContent = check.description;
      const meta = document.createElement("small");
      meta.textContent = check.enabled && check.next_run
        ? `Next look ${new Date(check.next_run).toLocaleString()}`
        : "Not scheduled";
      copy.append(head, value, meta);
      const actions = document.createElement("div"); actions.className = "memory-actions";
      const toggle = document.createElement("button");
      toggle.className = "control-button";
      toggle.textContent = check.enabled ? "Stop watching" : "Watch this";
      toggle.addEventListener("click", async () => {
        const changed = await callApi("set_check_enabled", check.name, !check.enabled);
        if (!changed.ok) return toast(changed.error, true);
        await openWatchPanel(false);
      });
      actions.append(toggle);
      card.append(copy, actions); list.append(card);
    }
    // Reminders had no interface at all: they could be set but not seen.
    for (const reminder of result.reminders || []) {
      const card = document.createElement("article"); card.className = "memory-item";
      const copy = document.createElement("div"); copy.className = "memory-item-copy";
      const head = document.createElement("div"); head.className = "memory-item-head";
      const badge = document.createElement("span"); badge.className = "memory-category";
      badge.textContent = "Reminder";
      head.append(badge);
      const value = document.createElement("p"); value.textContent = reminder.request;
      const meta = document.createElement("small");
      meta.textContent = `Due ${new Date(reminder.next_run).toLocaleString()}`;
      copy.append(head, value, meta);
      const actions = document.createElement("div"); actions.className = "memory-actions";
      const cancel = document.createElement("button");
      cancel.className = "control-button danger"; cancel.textContent = "Cancel";
      cancel.addEventListener("click", async () => {
        const dropped = await callApi("cancel_scheduled", reminder.id);
        if (!dropped.ok) return toast(dropped.error, true);
        await openWatchPanel(false);
      });
      actions.append(cancel);
      card.append(copy, actions); list.append(card);
    }
  } catch (error) {
    list.textContent = String(error);
  }
}

async function openPermissions(focus = true) {
  if (focus) openModal(elements.permissionsModal);
  const list = $("#permissionList");
  list.textContent = "Reading local permissions…";
  try {
    const result = await callApi("list_permissions");
    if (!result.ok) throw new Error(result.error);
    $("#permissionsNote").textContent = result.note;
    list.replaceChildren();
    if (!result.active.length) {
      const empty = document.createElement("div"); empty.className = "memory-empty";
      const title = document.createElement("strong"); title.textContent = "No folders granted";
      const detail = document.createElement("p");
      detail.textContent = "Aura can only reach its own workspace until you grant a folder above.";
      empty.append(title, detail); list.append(empty); return;
    }
    for (const grant of result.active) {
      const card = document.createElement("article"); card.className = "memory-item";
      const copy = document.createElement("div"); copy.className = "memory-item-copy";
      const head = document.createElement("div"); head.className = "memory-item-head";
      const kind = document.createElement("span"); kind.className = "memory-category";
      kind.textContent = grant.mode === "persistent" ? "Until revoked"
        : grant.mode === "once" ? "Once" : grant.mode === "project" ? "Project" : "This session";
      const scope = document.createElement("span"); scope.className = "memory-confidence";
      scope.textContent = grant.capability === "reach_domain" ? "domain, and its subdomains"
        : grant.capability === "write_folder" ? "read and write" : "read only";
      head.append(kind, scope);
      const value = document.createElement("p");
      value.textContent = grant.root;
      const meta = document.createElement("small");
      const when = grant.granted_at ? new Date(grant.granted_at).toLocaleString() : "";
      meta.textContent = `Granted ${when}${grant.project ? ` • project ${grant.project}` : ""}`;
      copy.append(head, value, meta);
      const actions = document.createElement("div"); actions.className = "memory-actions";
      const revoke = document.createElement("button");
      revoke.type = "button"; revoke.textContent = "Revoke"; revoke.classList.add("danger");
      revoke.addEventListener("click", async () => {
        const outcome = await callApi("revoke_folder_access", grant.id);
        if (!outcome.ok) return toast(outcome.error, true);
        toast("Access revoked.");
        await openPermissions(false);
      });
      actions.append(revoke);
      card.append(copy, actions); list.append(card);
    }
  } catch (error) {
    list.textContent = String(error);
  }
}

async function grantFolderAccess(event) {
  event.preventDefault();
  const path = $("#permissionPath").value.trim();
  if (!path) return toast("Enter a folder path first.", true);
  const writable = $("#permissionAccess").value === "write";
  const result = await callApi("grant_folder_access", path, $("#permissionMode").value, null, writable);
  if (!result.ok) return toast(result.error, true);
  $("#permissionPath").value = "";
  toast(`Aura may now read ${result.grant.root}`);
  await openPermissions(false);
}

async function grantDomainAccess(event) {
  event.preventDefault();
  const domain = $("#domainName").value.trim();
  if (!domain) return toast("Enter a domain first.", true);
  const result = await callApi("grant_domain_access", domain, $("#domainMode").value);
  if (!result.ok) return toast(result.error, true);
  $("#domainName").value = "";
  toast(`Aura may now read ${result.grant.root}`);
  await openPermissions(false);
}

function renderAutonomyStatus(status) {
  if (!status) return;
  const paused = Boolean(status.paused);
  elements.pauseAutonomy.textContent = paused ? "Background: paused" : "Background: on";
  elements.pauseAutonomy.classList.toggle("paused", paused);
  elements.pauseAutonomy.setAttribute("aria-pressed", String(paused));
  // The reason matters more than the state: a background run that quietly does
  // not happen is worse than one that says why.
  elements.pauseAutonomy.title = status.allowed
    ? `Background work is allowed. ${status.runs_today}/${status.daily_cap} runs used today, quiet hours ${status.quiet_hours}.`
    : status.reason || "Background work is not allowed right now.";
}

function renderNetworkStatus(network) {
  if (!network) return;
  const online = Boolean(network.online);
  elements.networkLabel.textContent = online
    ? `Online • ${network.domains.length} domain${network.domains.length === 1 ? "" : "s"}`
    : "Offline • local only";
  elements.networkLabel.classList.toggle("online", online);
  elements.networkLabel.title = online
    ? `Aura may read: ${network.domains.join(", ")}`
    : "Aura cannot reach the network. Grant a domain under Permissions to change that.";
  // Name what each built-in service still needs, so enabling one is not guesswork.
  const hint = $("#domainSuggestions");
  if (!hint) return;
  const missing = (network.services || []).flatMap(service =>
    (service.domains || []).filter(domain => !network.domains.includes(domain)));
  hint.textContent = missing.length
    ? `Built-in services still need: ${[...new Set(missing)].join(", ")}`
    : "Every built-in service has the domains it needs.";
}

async function revokeAllPermissions() {
  if (!window.confirm("Revoke every folder permission outside the workspace?")) return;
  const result = await callApi("revoke_all_permissions");
  if (!result.ok) return toast(result.error, true);
  toast(result.revoked ? `Revoked ${result.revoked} permission(s).` : "Nothing to revoke.");
  await openPermissions(false);
}

async function exportPersonalMemory() {
  const result = await callApi("export_personal_memory");
  if (!result.ok) return toast(result.error, true);
  toast(`Exported ${result.count} ${result.count === 1 ? "memory" : "memories"} to ${result.path}`);
  closeModal(elements.memoryModal);
  openWorkspaceExplorer(result.path);
}

function buildMemoryCard(memory) {
  const card = document.createElement("article"); card.className = `memory-item${memory.pinned ? " pinned" : ""}`;
  const copy = document.createElement("div"); copy.className = "memory-item-copy";
  const head = document.createElement("div"); head.className = "memory-item-head";
  const category = document.createElement("span"); category.className = "memory-category"; category.textContent = memoryCategoryLabel(memory.category);
  const confidence = document.createElement("span"); confidence.className = "memory-confidence";
  confidence.textContent = memory.confirmed ? "User confirmed" : `${Math.round((Number(memory.confidence) || 0) * 100)}% confidence`;
  head.append(category, confidence);
  const value = document.createElement("p"); value.textContent = memory.value;
  const source = document.createElement("small");
  const updated = memory.updated ? new Date(memory.updated).toLocaleDateString() : "";
  const lastUsed = memory.last_used ? new Date(memory.last_used).toLocaleDateString() : "";
  source.textContent = `${memory.pinned ? "Pinned • " : ""}${memory.project ? `Project: ${memory.project} • ` : ""}${updated ? `Updated ${updated} • ` : ""}${lastUsed ? `Last used ${lastUsed} • ` : ""}Source: ${memory.source || "Aura chat"}`;
  const related = memoryConflicts
    .filter(pair => pair.a === memory.id || pair.b === memory.id)
    .map(pair => ({ kind: pair.kind, other: personalMemories.find(item => item.id === (pair.a === memory.id ? pair.b : pair.a)) }))
    .filter(entry => entry.other);
  copy.append(head, value, source);
  for (const entry of related) {
    const warning = document.createElement("small");
    warning.className = `memory-conflict ${entry.kind}`;
    warning.textContent = `${entry.kind === "contradicts" ? "May contradict" : "Overlaps with"}: ${entry.other.value}`;
    copy.append(warning);
  }
  const actions = document.createElement("div"); actions.className = "memory-actions";
  const button = (label, handler, danger = false) => {
    const control = document.createElement("button"); control.type = "button"; control.textContent = label; control.classList.toggle("danger", danger); control.addEventListener("click", handler); actions.append(control);
  };
  if (!memory.confirmed) {
    button("Confirm", async () => {
      const result = await callApi("update_personal_memory", memory.id, {});
      if (!result.ok) return toast(result.error, true);
      toast("Memory confirmed.");
    });
  }
  if (related.length === 1) {
    button("Keep this", async () => {
      const other = related[0].other;
      if (!window.confirm(`Keep this memory and forget the other one?\n\nKeeping: ${memory.value}\nForgetting: ${other.value}`)) return;
      const result = await callApi("forget_personal_memory", other.id);
      if (!result.ok) return toast(result.error, true);
      toast("Kept this memory and forgot the other.");
    });
  }
  button(memory.pinned ? "Unpin" : "Pin", async () => {
    const result = await callApi("update_personal_memory", memory.id, { pinned: !memory.pinned });
    if (!result.ok) return toast(result.error, true);
    toast(memory.pinned ? "Memory unpinned." : "Memory pinned for stronger recall.");
  });
  button("Edit", () => {
    const editor = document.createElement("div"); editor.className = "memory-editor";
    const select = document.createElement("select"); populateMemoryCategories(select, memory.category);
    const textarea = document.createElement("textarea"); textarea.maxLength = 240; textarea.value = memory.value;
    editor.append(select, textarea); copy.replaceChildren(editor);
    actions.replaceChildren();
    button("Cancel", renderPersonalMemories);
    button("Save", async () => {
      const result = await callApi("update_personal_memory", memory.id, { category: select.value, value: textarea.value });
      if (!result.ok) return toast(result.error, true);
      toast("Aura's memory was corrected.");
    });
    textarea.focus(); textarea.select();
  });
  const history = memory.history || [];
  if (history.length) {
    const previous = history[history.length - 1];
    const note = document.createElement("small");
    note.className = "memory-history";
    const when = previous.replaced ? new Date(previous.replaced).toLocaleDateString() : "";
    note.textContent = `Previously “${previous.value}”${when ? ` until ${when}` : ""}${history.length > 1 ? ` (+${history.length - 1} earlier)` : ""}`;
    copy.append(note);
    button("Revert", async () => {
      const result = await callApi("revert_personal_memory", memory.id);
      if (!result.ok) return toast(result.error, true);
      toast("Restored the earlier wording.");
    });
  }
  button("Forget", async () => {
    if (!window.confirm(`Ask Aura to forget this?\n\n${memory.value}`)) return;
    const result = await callApi("forget_personal_memory", memory.id);
    if (!result.ok) return toast(result.error, true);
    toast("Aura forgot that memory.");
  }, true);
  card.append(copy, actions);
  return card;
}

function renderPersonalMemories() {
  const query = $("#memorySearch").value.trim().toLowerCase();
  const visible = personalMemories.filter(item => !query ||
    `${item.category} ${item.topic || ""} ${item.value}`.toLowerCase().includes(query));
  elements.memoryList.replaceChildren();
  if (!visible.length) {
    const empty = document.createElement("div"); empty.className = "memory-empty";
    const title = document.createElement("strong"); title.textContent = personalMemories.length ? "No matching memories" : "Aura is still getting to know you";
    const detail = document.createElement("p"); detail.textContent = personalMemories.length
      ? "Try another search."
      : "Say things naturally, such as “I prefer concise answers” or teach Aura a fact above. Clear non-sensitive statements can be learned automatically.";
    empty.append(title, detail); elements.memoryList.append(empty); return;
  }
  const needsReview = visible.filter(item => !item.confirmed);
  const confirmedMemories = visible.filter(item => item.confirmed);
  if (needsReview.length && confirmedMemories.length) {
    const reviewLabel = document.createElement("div"); reviewLabel.className = "file-folder-label";
    reviewLabel.textContent = `Needs review (${needsReview.length})`;
    elements.memoryList.append(reviewLabel);
    for (const memory of needsReview) elements.memoryList.append(buildMemoryCard(memory));
    const confirmedLabel = document.createElement("div"); confirmedLabel.className = "file-folder-label";
    confirmedLabel.textContent = "Confirmed";
    elements.memoryList.append(confirmedLabel);
    for (const memory of confirmedMemories) elements.memoryList.append(buildMemoryCard(memory));
  } else {
    for (const memory of visible) elements.memoryList.append(buildMemoryCard(memory));
  }
}

async function openPersonalMemory(focus = true) {
  if (focus) openModal(elements.memoryModal);
  elements.memoryList.textContent = "Loading local memories…";
  try {
    const result = await callApi("get_personal_memory");
    if (!result.ok) throw new Error(result.error);
    personalMemories = result.memories || [];
    memoryCategories = result.categories || [];
    memoryConflicts = result.conflicts || [];
    $("#memoryName").textContent = result.name || "Not set";
    $("#memoryCount").textContent = String(result.count || 0);
    $("#memoryPrivacy").textContent = result.privacy;
    populateMemoryCategories($("#memoryCategory"), $("#memoryCategory").value || "preference");
    capabilityState.personal_memories = result.count || 0;
    updatePowerStatus();
    renderPersonalMemories();
  } catch (error) {
    elements.memoryList.textContent = String(error);
  }
}

async function teachAura(event) {
  event.preventDefault();
  const value = $("#memoryValue").value.trim();
  if (!value) return;
  const result = await callApi("add_personal_memory", $("#memoryCategory").value, value);
  if (!result.ok) return toast(result.error, true);
  $("#memoryValue").value = "";
  toast("Aura will remember that.");
}

async function openSettings() {
  openModal(elements.settingsModal);
  const status = $("#settingsStatus");
  status.className = "modal-status";
  status.textContent = "Loading local settings…";
  try {
    const settings = await callApi("get_settings");
    $("#settingUrl").value = settings.lm_studio_url;
    setSelectValue($("#settingModel"), settings.model || "");
    $("#settingTimeout").value = settings.timeout;
    $("#settingTemperature").value = settings.temperature;
    $("#settingTokens").value = settings.max_tokens;
    $("#settingReasoning").value = settings.reasoning_depth;
    $("#settingAutonomy").value = settings.autonomy_mode;
    $("#settingVision").value = settings.vision_mode || "auto";
    $("#settingLearning").checked = settings.learn_from_conversations;
    $("#settingSpeechEngine").value = settings.speech_engine;
    setSelectValue($("#settingVoice"), settings.speech_voice || "");
    $("#settingRate").value = settings.speech_rate;
    $("#settingVolume").value = settings.speech_volume;
    $("#settingSpeak").checked = settings.speak_responses;
    $("#settingVoiceEngine").value = settings.voice_engine || "auto";
    $("#settingVoiceLanguage").value = settings.voice_language || "en";
    $("#settingCalibration").value = settings.voice_calibration_ms ?? 500;
    $("#settingSilence").value = settings.voice_silence_ms ?? 1200;
    $("#settingVoiceMax").value = settings.voice_max_seconds ?? 25;
    $("#settingWhisperPath").value = settings.whisper_cpp_path || "";
    $("#settingWhisperModel").value = settings.whisper_model_path || "";
    $("#settingAvatarMotion").value = settings.avatar_motion || "natural";
    $("#settingAvatarQuality").value = settings.avatar_quality || "auto";
    $("#settingSearchEndpoint").value = settings.search_endpoint || "";
    $("#settingSearchInstallPath").value = settings.search_install_path || "";
    $("#settingSearchMode").value = settings.search_mode || "off";
    renderSearchServiceStatus();
    $("#settingAvatarIntensity").value = settings.avatar_intensity ?? 65;
    updateRangeOutputs();
    await refreshNeuralVoices(settings.speech_model || "");
    await refreshMicrophones(false, settings.voice_device || "");
    status.textContent = "Settings stay on this computer.";
  } catch (error) {
    status.className = "modal-status error";
    status.textContent = String(error);
  }
}

async function refreshModels() {
  const status = $("#settingsStatus");
  status.className = "modal-status";
  status.textContent = "Connecting to LM Studio…";
  const result = await callApi("get_models", $("#settingUrl").value);
  if (!result.ok) {
    status.className = "modal-status error";
    status.textContent = result.error;
    return;
  }
  const select = $("#settingModel");
  const previous = select.value;
  select.replaceChildren(new Option("Automatic selection", ""));
  for (const model of result.models) select.add(new Option(model, model));
  setSelectValue(select, previous || result.models.find(model => !model.toLowerCase().includes("embed")) || "");
  status.textContent = `Connected. ${result.models.length} model(s) available.`;
}

async function refreshVoices() {
  const status = $("#settingsStatus");
  status.textContent = "Checking installed Windows voices…";
  const result = await callApi("get_voices");
  const select = $("#settingVoice");
  const previous = select.value;
  select.replaceChildren();
  for (const voice of result.voices || []) select.add(new Option(voice, voice));
  setSelectValue(select, previous || result.voices?.[0] || "");
  status.textContent = result.voices?.length ? `Found ${result.voices.length} local voice(s).` : "No SAPI fallback voices found.";
}

async function refreshNeuralVoices(preferred = "") {
  const select = $("#settingSpeechModel");
  const result = await callApi("get_voices");
  const models = result.neural || [];
  select.replaceChildren();
  if (!models.length) {
    // Aura never downloads a voice on its own, so say where one would go.
    select.add(new Option("No neural voice in aura-voices/", ""));
    select.disabled = true;
    return;
  }
  select.disabled = false;
  for (const model of models) {
    select.add(new Option(model.split("/").pop().replace(/\.onnx$/, ""), model));
  }
  setSelectValue(select, preferred || result.neural_selected || models[0]);
}

async function refreshMicrophones(showStatus = true, preferred = null) {
  const status = $("#microphoneStatus");
  if (showStatus) status.textContent = "Checking local microphones…";
  try {
    const result = await callApi("get_microphones");
    const select = $("#settingMicrophone");
    const previous = preferred ?? select.value ?? result.selected ?? "";
    select.replaceChildren(new Option("System default microphone", ""));
    for (const device of result.devices || []) {
      const suffix = device.default ? " • default" : ` • ${device.host}`;
      select.add(new Option(`${device.name}${suffix}`, device.id));
    }
    setSelectValue(select, previous);
    const active = result.capabilities?.active_engine === "whisper_cpp" ? "Whisper.cpp" : "PocketSphinx";
    status.textContent = result.devices?.length
      ? `${result.devices.length} compatible input(s) • ${active} active`
      : "No compatible local microphone was found. Text chat remains ready.";
  } catch (error) {
    status.textContent = String(error);
  }
}

async function calibrateMicrophone() {
  const status = $("#microphoneStatus");
  status.textContent = "Calibrating — stay quiet briefly…";
  const result = await callApi("calibrate_voice", $("#settingMicrophone").value);
  if (!result.ok) {
    status.textContent = result.error;
    toast(result.error, true);
  }
}

async function previewVoice() {
  const result = await callApi("preview_voice");
  if (!result.ok) toast(result.error, true);
}

function updateRangeOutputs() {
  $("#rateOutput").textContent = $("#settingRate").value;
  $("#volumeOutput").textContent = `${$("#settingVolume").value}%`;
  $("#avatarIntensityOutput").textContent = `${$("#settingAvatarIntensity").value}%`;
  $("#calibrationOutput").textContent = `${$("#settingCalibration").value} ms`;
  $("#silenceOutput").textContent = `${$("#settingSilence").value} ms`;
}

async function saveSettings() {
  const status = $("#settingsStatus");
  status.className = "modal-status";
  status.textContent = "Saving…";
  const values = {
    lm_studio_url: $("#settingUrl").value,
    model: $("#settingModel").value || null,
    timeout: $("#settingTimeout").value,
    temperature: $("#settingTemperature").value,
    max_tokens: $("#settingTokens").value,
    reasoning_depth: $("#settingReasoning").value,
    autonomy_mode: $("#settingAutonomy").value,
    vision_mode: $("#settingVision").value,
    learn_from_conversations: $("#settingLearning").checked,
    speech_engine: $("#settingSpeechEngine").value,
    speech_voice: $("#settingVoice").value,
    speech_model: $("#settingSpeechModel").value,
    speech_rate: $("#settingRate").value,
    speech_volume: $("#settingVolume").value,
    speak_responses: $("#settingSpeak").checked,
    voice_engine: $("#settingVoiceEngine").value,
    voice_device: $("#settingMicrophone").value,
    voice_language: $("#settingVoiceLanguage").value,
    voice_calibration_ms: $("#settingCalibration").value,
    voice_silence_ms: $("#settingSilence").value,
    voice_max_seconds: $("#settingVoiceMax").value,
    whisper_cpp_path: $("#settingWhisperPath").value,
    whisper_model_path: $("#settingWhisperModel").value,
    avatar_motion: $("#settingAvatarMotion").value,
    avatar_quality: $("#settingAvatarQuality").value,
    search_endpoint: $("#settingSearchEndpoint").value,
    search_install_path: $("#settingSearchInstallPath").value,
    search_mode: $("#settingSearchMode").value,
    avatar_intensity: $("#settingAvatarIntensity").value,
  };
  const result = await callApi("save_settings", values);
  if (!result.ok) {
    status.className = "modal-status error";
    status.textContent = result.error;
    return;
  }
  updatePowerStatus({ tools: result.tools, reasoning_depth: values.reasoning_depth,
                      autonomy_mode: values.autonomy_mode });
  avatarMotion?.applySettings(result.avatar || {
    motion: values.avatar_motion, quality: values.avatar_quality,
    intensity: Number(values.avatar_intensity),
  });
  closeModal(elements.settingsModal);
}

const VERIFICATION_TOOLS = new Set([
  "validate_project", "verify_final_state", "compare_files", "check_workspace_assets",
]);

function taskEvidence(task) {
  const details = task.tool_details || [];
  const files = [];
  for (const detail of details) {
    if (!MUTATION_TOOLS.has(detail.tool)) continue;
    const path = detail.arguments?.path || detail.arguments?.destination;
    if (path && !files.includes(path)) files.push(path);
  }
  let validated = false;
  for (const detail of details) {
    if (!VERIFICATION_TOOLS.has(detail.tool)) continue;
    const result = detail.result || {};
    if (result.ok === true || result.valid === true) validated = true;
    else if (result.ok === false || result.valid === false) validated = false;
  }
  let duration = null;
  if (task.started && task.finished) {
    const ms = new Date(task.finished) - new Date(task.started);
    if (Number.isFinite(ms) && ms >= 0) duration = ms;
  }
  return { files, validated, duration };
}

function formatDuration(ms) {
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function buildTaskCard(task) {
  const card = document.createElement("div");
  card.className = "task-card";
  card.dataset.taskId = task.task_id;
  const copy = document.createElement("div");
  const title = document.createElement("strong");
  const badge = document.createElement("span");
  badge.className = `task-status ${task.status || "running"}`;
  badge.textContent = task.status || "running";
  title.append(badge, document.createTextNode(String(task.request || "Untitled task").slice(0, 160)));
  const meta = document.createElement("p");
  const finished = task.finished ? new Date(task.finished).toLocaleString()
    : (task.status === "interrupted" ? "did not finish" : "in progress");
  meta.textContent = `${task.task_id} • ${finished}`;
  const summary = document.createElement("p");
  summary.className = "task-summary";
  summary.textContent = String(task.summary || "No final summary yet.").slice(0, 420);
  const evidence = taskEvidence(task);
  const evidenceParts = [];
  if (evidence.duration !== null) evidenceParts.push(`Took ${formatDuration(evidence.duration)}`);
  if (evidence.files.length) evidenceParts.push(`${evidence.files.length} file${evidence.files.length === 1 ? "" : "s"} changed`);
  if (evidence.validated) evidenceParts.push("✓ validated");
  const evidenceRow = document.createElement("p");
  evidenceRow.textContent = evidenceParts.join(" • ");
  const fileChips = document.createElement("div"); fileChips.className = "message-task-tools";
  for (const path of evidence.files.slice(0, 6)) {
    const chip = document.createElement("span"); chip.className = "tool-chip"; chip.title = path;
    chip.textContent = path.split(/[\\/]/).pop(); fileChips.append(chip);
  }
  if (evidence.files.length > 6) {
    const chip = document.createElement("span"); chip.className = "tool-chip";
    chip.textContent = `+${evidence.files.length - 6} more`; fileChips.append(chip);
  }
  const chips = document.createElement("div"); chips.className = "message-task-tools";
  for (const name of [...new Set(task.tools || [])]) {
    const chip = document.createElement("span"); chip.className = "tool-chip"; chip.textContent = name; chips.append(chip);
  }
  copy.append(title, meta, summary);
  if (evidenceParts.length) copy.append(evidenceRow);
  if (evidence.files.length) copy.append(fileChips);
  copy.append(chips);
  const actions = document.createElement("div"); actions.className = "task-card-actions";
  const addAction = (label, handler, danger = false) => {
    const button = document.createElement("button"); button.textContent = label; button.classList.toggle("danger", danger); button.addEventListener("click", handler); actions.append(button);
  };
  const path = (task.tool_details || []).map(detail => detail.arguments?.path || detail.arguments?.destination).find(Boolean);
  addAction(path ? "Open file" : "Workspace", () => openWorkspaceExplorer(path || null));
  if (String(task.request || "").trim()) addAction("Repeat", () => { closeModal(elements.tasksModal); sendMessage(task.request); });
  if (task.status === "interrupted" || task.status === "error") {
    addAction("Resume", async () => {
      const result = await callApi("resume_task", task.task_id);
      if (!result.ok) return toast(result.error, true);
      closeModal(elements.tasksModal);
      toast("Continuing from what was already done.");
    });
  }
  if ((task.tools || []).some(name => MUTATION_TOOLS.has(name))) addAction("Undo", () => rollbackTask(task), true);
  card.append(copy, actions);
  return card;
}

async function openTasks(focusTaskId = null) {
  openModal(elements.tasksModal);
  const list = $("#taskList");
  list.textContent = "Loading…";
  try {
    const result = await callApi("recent_tasks", 12);
    list.replaceChildren();
    if (!result.tasks.length) {
      list.textContent = "No recorded tasks yet.";
      return;
    }
    // Tasks arrive newest-first; grouping by first appearance keeps each
    // project's most recently active task determining where its group sits.
    const groups = new Map();
    for (const task of result.tasks) {
      const key = task.project || "Workspace root";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(task);
    }
    for (const [project, tasks] of groups) {
      const label = document.createElement("div"); label.className = "file-folder-label"; label.textContent = project;
      list.append(label);
      for (const task of tasks) list.append(buildTaskCard(task));
    }
    if (focusTaskId) {
      const focused = [...list.children].find(card => card.dataset.taskId === focusTaskId);
      focused?.classList.add("focused");
      focused?.scrollIntoView({ block: "center" });
    }
  } catch (error) {
    list.textContent = String(error);
  }
}

async function rollbackTask(task) {
  const accepted = window.confirm(`Undo active file changes from task ${task.task_id}?\n\nCurrent versions will move to Aura's recoverable trash.`);
  if (!accepted) return;
  const result = await callApi("rollback_task", task.task_id);
  if (result.ok) {
    addMessage("assistant", result.summary);
    toast("Task changes rolled back safely.");
    closeModal(elements.tasksModal);
  } else {
    toast(result.error, true);
  }
}

async function quitAura() {
  if (!window.confirm("Quit Aura and stop its local HTML server?")) return;
  shutdownFrontend();
  try {
    const response = await fetch("/api/shutdown", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Aura-Client": "html-ui-v1" },
      body: "{}",
    });
    if (!response.ok) throw new Error("Aura could not stop cleanly.");
    document.body.innerHTML = `<main class="shutdown-screen"><div><h1>Aura is offline.</h1><p>The local server has stopped safely. You can close this tab or start Aura again from the desktop launcher.</p></div></main>`;
  } catch (error) {
    toast(String(error), true);
  }
}

function showApproval(event) {
  currentApproval = event.approval_id;
  const kind = event.command?.[0];
  const plan = kind === "PLAN";
  $("#approvalTitle").textContent = plan ? "Before Aura builds this"
    : kind === "HTTP GET" ? "Network approval"
    : kind === "OPEN" ? "Open application" : "Command approval";
  // A plan is a list to read, not a command to scan, so it keeps its line breaks
  // and the wording asks about the shape of the work rather than permission.
  $("#approvalLead").textContent = plan
    ? "These are the files she would create. Approving is cheaper than undoing:"
    : "Aura wants to use a capability that needs your permission:";
  $("#approvalNote").textContent = plan
    ? "Denying stops before anything is written, and you can describe a different list."
    : "“Allow identical for task” covers only this exact command or URL until the current task ends.";
  $("#allowTaskApproval").classList.toggle("hidden", plan);
  $("#allowApproval").textContent = plan ? "Build these" : "Allow once";
  $("#denyApproval").textContent = plan ? "Stop" : "Deny";
  elements.approvalCommand.textContent = plan
    ? event.command.slice(1).join("\n")
    : event.command.map(part => /\s/.test(part) ? JSON.stringify(part) : part).join(" ");
  openModal(elements.approvalModal);
}

async function resolveApproval(allowed, scope = "once") {
  if (!currentApproval) return;
  const approval = currentApproval;
  currentApproval = null;
  elements.approvalModal.classList.add("hidden");
  const result = await callApi("resolve_approval", approval, allowed, scope);
  if (!result.ok) toast(result.error, true);
}

function scheduleUiSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => callApi("save_ui_state", {
    sidebar_width: expandedSidebarWidth,
    log_height: logHeight,
    log_visible: logVisible,
  }).catch(console.error), 500);
}

function applyLayout() {
  const sidebarLimit = Math.max(190, Math.floor(window.innerWidth * 0.38));
  const renderedSidebar = elements.sidebar.classList.contains("collapsed")
    ? 72 : Math.min(sidebarWidth, sidebarLimit);
  const logLimit = Math.max(90, Math.floor(window.innerHeight * 0.32));
  const renderedLog = Math.min(logHeight, logLimit);
  document.documentElement.style.setProperty("--sidebar-width", `${renderedSidebar}px`);
  document.documentElement.style.setProperty("--log-height", `${renderedLog}px`);
  elements.main.classList.toggle("log-hidden", !logVisible);
  // It is a menu item now, so replacing its text would eat the icon with it.
  elements.toggleLogLabel.textContent = logVisible ? "Hide action log" : "Show action log";
}

function installResizers() {
  elements.sidebarResizer.addEventListener("pointerdown", event => {
    if (elements.sidebar.classList.contains("collapsed")) return;
    const startX = event.clientX;
    const startWidth = Math.min(sidebarWidth, Math.max(190, Math.floor(window.innerWidth * 0.38)));
    elements.sidebarResizer.classList.add("dragging");
    elements.sidebarResizer.setPointerCapture(event.pointerId);
    const move = moveEvent => {
      sidebarWidth = Math.max(190, Math.min(420, startWidth + moveEvent.clientX - startX));
      expandedSidebarWidth = sidebarWidth;
      applyLayout();
    };
    const end = () => {
      elements.sidebarResizer.classList.remove("dragging");
      elements.sidebarResizer.removeEventListener("pointermove", move);
      scheduleUiSave();
    };
    elements.sidebarResizer.addEventListener("pointermove", move);
    elements.sidebarResizer.addEventListener("pointerup", end, { once: true });
  });

  elements.logResizer.addEventListener("pointerdown", event => {
    const startY = event.clientY;
    const startHeight = Math.min(logHeight, Math.max(90, Math.floor(window.innerHeight * 0.32)));
    elements.logResizer.classList.add("dragging");
    elements.logResizer.setPointerCapture(event.pointerId);
    const move = moveEvent => {
      logHeight = Math.max(90, Math.min(420, startHeight + startY - moveEvent.clientY));
      applyLayout();
    };
    const end = () => {
      elements.logResizer.classList.remove("dragging");
      elements.logResizer.removeEventListener("pointermove", move);
      scheduleUiSave();
    };
    elements.logResizer.addEventListener("pointermove", move);
    elements.logResizer.addEventListener("pointerup", end, { once: true });
  });
}

// The legend and the filters are one control: the key that explains a colour
// is the switch that hides it, so there is nothing to learn twice.
// Memory is listed before Preferences on purpose: a fact stored in both places
// is one node, and it should be filed under the memory that can be edited.
const MIND_LAYERS = [
  { id: "identity", label: "Identity", color: "#81d69a" },
  { id: "personal_memory", label: "Memory", color: "#f0abfc" },
  { id: "preferences", label: "Preferences", color: "#f472b6" },
  { id: "conversation", label: "Conversation", color: "#60a5fa" },
  { id: "tasks", label: "Tasks", color: "#4ade80" },
  { id: "capabilities", label: "Tools", color: "#fb923c" },
  { id: "workspace", label: "Workspace", color: "#38bdf8" },
];
const hiddenMindLayers = new Set();

const NODE_COLORS = {
  aura: "#80e0d2", category: "#d96cb6", identity: "#81d69a", preference: "#f472b6",
  conversation_user: "#60a5fa", conversation_aura: "#a78bfa", task_completed: "#4ade80",
  task_error: "#f87171", task_running: "#fbbf24", tool: "#fb923c", folder: "#22d3ee",
  file: "#38bdf8", personal_memory: "#f0abfc", personal_memory_pinned: "#facc15", empty: "#64748b",
};

function renderMindLegend() {
  const legend = $("#mindLegend");
  legend.replaceChildren();
  for (const layer of MIND_LAYERS) {
    const hidden = hiddenMindLayers.has(layer.id);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "mind-legend-item" + (hidden ? " off" : "");
    button.setAttribute("aria-pressed", String(!hidden));
    const swatch = document.createElement("span");
    swatch.className = "mind-legend-dot";
    swatch.style.background = layer.color;
    button.append(swatch, document.createTextNode(layer.label));
    button.addEventListener("click", () => {
      if (hidden) hiddenMindLayers.delete(layer.id); else hiddenMindLayers.add(layer.id);
      renderMindLegend();
      mindGraph?.applyLayers();
    });
    legend.append(button);
  }
}

function installFaceInteraction() {
  let frame = null;
  document.addEventListener("pointermove", event => {
    if (frame || elements.face.offsetParent === null) return;
    frame = requestAnimationFrame(() => {
      frame = null;
      const rect = elements.face.getBoundingClientRect();
      const centerX = rect.left + rect.width / 2, centerY = rect.top + rect.height / 2;
      const distance = Math.max(Math.hypot(event.clientX - centerX, event.clientY - centerY), 1);
      const strength = Math.min(5, distance / 55);
      const x = (event.clientX - centerX) / distance * strength;
      const y = (event.clientY - centerY) / distance * strength;
      const normalizedX = Math.max(-1, Math.min(1, (event.clientX - centerX) / Math.max(rect.width * .65, 1)));
      const normalizedY = Math.max(-1, Math.min(1, (event.clientY - centerY) / Math.max(rect.height * .65, 1)));
      elements.gazeLayer?.setAttribute("transform", `translate(${(x * .72).toFixed(2)} ${(y * .72).toFixed(2)})`);
      avatarMotion?.setGaze(normalizedX, normalizedY);
    });
  });
  document.addEventListener("pointerleave", () => {
    elements.gazeLayer?.setAttribute("transform", "translate(0 0)");
    avatarMotion?.setGaze(0, 0);
  });
  const react = () => {
    elements.face.classList.remove("boop");
    requestAnimationFrame(() => elements.face.classList.add("boop"));
    setTimeout(() => elements.face.classList.remove("boop"), 500);
    avatarMotion?.pulse();
    toast("Aura is here.");
    setSuggestions([
      { label: "Talk to Aura", prompt: "What are you thinking about right now?" },
      { label: "Explore workspace", action: "workspace" },
      { label: "Open Aura Mind", action: "mind" },
    ]);
  };
  elements.face.addEventListener("click", react);
  elements.face.addEventListener("keydown", event => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); react(); }
  });
}

function updateMindActions(node) {
  selectedMindNode = node || null;
  elements.mindActions.classList.toggle("hidden", !node);
  $("#mindOpen").classList.toggle("hidden", !node?.target);
  $("#mindOpen").textContent = node?.kind === "folder" ? "Browse" : "Open";
}

function askAboutMindNode() {
  if (!selectedMindNode) return;
  const node = selectedMindNode;
  closeMind();
  sendMessage(`Tell me what you know about “${node.label}” from Aura Mind. Use this local context only: ${String(node.detail || "").slice(0, 700)}`);
}

async function openMindTarget() {
  if (!selectedMindNode?.target) return;
  const node = selectedMindNode;
  closeMind();
  await openWorkspaceExplorer(node.kind === "file" ? node.target : null);
  if (node.kind === "folder") {
    elements.workspaceSearch.value = node.target;
    renderWorkspaceTree();
  }
}

class MindGraph {
  constructor(canvas) {
    this.canvas = canvas;
    this.context = canvas.getContext("2d");
    this.nodes = [];
    this.nodeMap = new Map();
    this.edges = [];
    this.positions = new Map();
    this.zoom = 1;
    this.panX = 0;
    this.panY = 0;
    this.selected = null;
    updateMindActions(null);
    this.dragNode = null;
    this.panAnchor = null;
    this.frame = null;
    this.step = 0;
    this.search = "";
    this.resizeObserver = new ResizeObserver(() => { this.resize(); this.draw(); });
    this.resizeObserver.observe(canvas);
    this.installEvents();
  }

  load(data) {
    this.allNodes = data.nodes || [];
    const known = new Set(this.allNodes.map(node => node.node_id));
    this.allEdges = (data.edges || []).filter(edge => known.has(edge.source) && known.has(edge.target));
    this.assignLayers();
    this.selected = null;
    elements.mindDetail.textContent = "Select a node to see what Aura knows about it.";
    this.applyLayers();
  }

  assignLayers() {
    // Layers are read from the graph itself: whatever hangs off a category is
    // part of that layer.
    this.layerOf = new Map();
    const children = new Map();
    for (const edge of this.allEdges) {
      if (!children.has(edge.source)) children.set(edge.source, []);
      children.get(edge.source).push(edge.target);
    }
    const categories = new Set(MIND_LAYERS.map(layer => layer.id));
    // Whatever a category holds directly belongs to it, before anything else
    // claims it. Tools hang off both `capabilities` and the tasks that used
    // them; without this pass, hiding Tasks took the whole Tools layer with it.
    for (const layer of MIND_LAYERS) {
      this.layerOf.set(layer.id, layer.id);
      for (const child of children.get(layer.id) || []) {
        if (!categories.has(child) && !this.layerOf.has(child)) {
          this.layerOf.set(child, layer.id);
        }
      }
    }
    // Then everything deeper — folders, files — follows its parent.
    const queue = [...this.layerOf.keys()];
    while (queue.length) {
      const current = queue.shift();
      for (const child of children.get(current) || []) {
        if (categories.has(child) || this.layerOf.has(child)) continue;
        this.layerOf.set(child, this.layerOf.get(current));
        queue.push(child);
      }
    }
  }

  applyLayers() {
    const visible = new Set(this.allNodes
      .filter(node => node.node_id === "aura"
        || !hiddenMindLayers.has(this.layerOf.get(node.node_id)))
      .map(node => node.node_id));
    this.nodes = this.allNodes.filter(node => visible.has(node.node_id));
    this.nodeMap = new Map(this.nodes.map(node => [node.node_id, node]));
    this.edges = this.allEdges.filter(edge => visible.has(edge.source) && visible.has(edge.target));
    if (this.selected && !visible.has(this.selected)) {
      this.selected = null;
      updateMindActions(null);
      elements.mindDetail.textContent = "Select a node to see what Aura knows about it.";
    }
    const hidden = this.allNodes.length - this.nodes.length;
    elements.mindSummary.textContent =
      `${this.nodes.length} thoughts • ${this.edges.length} connections`
      + (hidden ? ` • ${hidden} hidden` : "");
    this.reset();
  }

  reset() {
    cancelAnimationFrame(this.frame);
    this.positions = new Map([["aura", { x: 0, y: 0, vx: 0, vy: 0 }]]);
    const children = new Map();
    for (const edge of this.edges) {
      if (!children.has(edge.source)) children.set(edge.source, []);
      children.get(edge.source).push(edge.target);
    }
    const roots = children.get("aura") || [];
    roots.forEach((id, index) => {
      const angle = -Math.PI / 2 + Math.PI * 2 * index / Math.max(1, roots.length);
      this.positions.set(id, { x: Math.cos(angle) * 230, y: Math.sin(angle) * 230, vx: 0, vy: 0 });
    });
    const queue = roots.map(id => [id, 1]);
    while (queue.length) {
      const [parent, depth] = queue.shift();
      const parentPos = this.positions.get(parent);
      const unplaced = (children.get(parent) || []).filter(id => !this.positions.has(id));
      const base = Math.atan2(parentPos.y, parentPos.x);
      const spread = Math.min(1.8, .32 * Math.max(1, unplaced.length - 1));
      unplaced.forEach((id, index) => {
        const fraction = unplaced.length === 1 ? .5 : index / (unplaced.length - 1);
        const angle = base - spread / 2 + spread * fraction;
        const distance = Math.max(85, 145 - depth * 9);
        this.positions.set(id, { x: parentPos.x + Math.cos(angle) * distance,
          y: parentPos.y + Math.sin(angle) * distance, vx: 0, vy: 0 });
        queue.push([id, depth + 1]);
      });
    }
    const extras = this.nodes.filter(node => !this.positions.has(node.node_id));
    extras.forEach((node, index) => {
      const angle = Math.PI * 2 * index / Math.max(1, extras.length);
      this.positions.set(node.node_id, { x: Math.cos(angle) * 380, y: Math.sin(angle) * 380, vx: 0, vy: 0 });
    });
    this.step = 0;
    this.resize();
    this.fit();
    this.animate();
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.round(rect.width * ratio));
    const height = Math.max(1, Math.round(rect.height * ratio));
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width;
      this.canvas.height = height;
    }
    this.context.setTransform(ratio, 0, 0, ratio, 0, 0);
  }

  animate() {
    if (elements.mindView.classList.contains("hidden") || this.step >= 110) return;
    this.physics();
    this.draw();
    this.step += 1;
    this.frame = requestAnimationFrame(() => this.animate());
  }

  physics() {
    const forces = new Map(this.nodes.map(node => [node.node_id, { x: 0, y: 0 }]));
    for (let i = 0; i < this.nodes.length; i += 1) {
      const first = this.nodes[i].node_id;
      const a = this.positions.get(first);
      for (let j = i + 1; j < this.nodes.length; j += 1) {
        const second = this.nodes[j].node_id;
        const b = this.positions.get(second);
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const distanceSq = Math.max(dx * dx + dy * dy, 100);
        const distance = Math.sqrt(distanceSq);
        const force = Math.min(2.2, 2800 / distanceSq);
        const fx = dx / distance * force;
        const fy = dy / distance * force;
        forces.get(first).x -= fx; forces.get(first).y -= fy;
        forces.get(second).x += fx; forces.get(second).y += fy;
      }
    }
    for (const edge of this.edges) {
      const a = this.positions.get(edge.source);
      const b = this.positions.get(edge.target);
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const distance = Math.max(Math.hypot(dx, dy), 1);
      const desired = edge.source === "aura" ? 215 : 115;
      const force = (distance - desired) * .0035;
      const fx = dx / distance * force;
      const fy = dy / distance * force;
      if (edge.source !== "aura") { forces.get(edge.source).x += fx; forces.get(edge.source).y += fy; }
      forces.get(edge.target).x -= fx; forces.get(edge.target).y -= fy;
    }
    for (const node of this.nodes) {
      if (node.node_id === "aura" || node.node_id === this.dragNode) continue;
      const position = this.positions.get(node.node_id);
      const force = forces.get(node.node_id);
      position.vx = Math.max(-5, Math.min(5, (position.vx + force.x) * .84));
      position.vy = Math.max(-5, Math.min(5, (position.vy + force.y) * .84));
      position.x += position.vx;
      position.y += position.vy;
    }
  }

  radius(node) { return node.kind === "aura" ? 15 : node.kind === "category" ? 10 : 6; }
  screen(position) { return { x: position.x * this.zoom + this.panX, y: position.y * this.zoom + this.panY }; }

  draw() {
    const context = this.context;
    const rect = this.canvas.getBoundingClientRect();
    context.clearRect(0, 0, rect.width, rect.height);
    const query = this.search.trim().toLowerCase();
    const matches = new Set(this.nodes.filter(node => query && `${node.label} ${node.detail}`.toLowerCase().includes(query)).map(node => node.node_id));
    context.lineWidth = Math.max(1, this.zoom);
    for (const edge of this.edges) {
      const a = this.screen(this.positions.get(edge.source));
      const b = this.screen(this.positions.get(edge.target));
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const middle = { x: (a.x + b.x) / 2 - dy * .07, y: (a.y + b.y) / 2 + dx * .07 };
      const source = this.nodeMap.get(edge.source);
      context.strokeStyle = query && !matches.has(edge.source) && !matches.has(edge.target) ? "#172033" : (NODE_COLORS[source.kind] || "#334155");
      context.globalAlpha = query ? .9 : .58;
      context.beginPath(); context.moveTo(a.x, a.y); context.quadraticCurveTo(middle.x, middle.y, b.x, b.y); context.stroke();
    }
    context.globalAlpha = 1;
    for (const node of this.nodes) {
      const point = this.screen(this.positions.get(node.node_id));
      const radius = Math.max(3.5, this.radius(node) * Math.min(this.zoom, 1.5));
      let color = NODE_COLORS[node.kind] || "#94a3b8";
      if (query && !matches.has(node.node_id)) color = "#334155";
      if (node.node_id === this.selected || matches.has(node.node_id)) {
        context.strokeStyle = "#f8fafc"; context.lineWidth = 2; context.beginPath();
        context.arc(point.x, point.y, radius + 4, 0, Math.PI * 2); context.stroke();
      }
      context.fillStyle = color; context.beginPath(); context.arc(point.x, point.y, radius, 0, Math.PI * 2); context.fill();
      const showLabel = this.zoom >= .52 || ["aura", "category"].includes(node.kind) || matches.has(node.node_id);
      if (showLabel) {
        const base = node.kind === "aura" ? 15 : node.kind === "category" ? 11 : 9;
        const size = Math.max(7, Math.round(base * Math.min(this.zoom, 1.25)));
        context.font = `${["aura", "category"].includes(node.kind) ? 650 : 400} ${size}px "Segoe UI"`;
        context.fillStyle = query && !matches.has(node.node_id) ? "#64748b" : "#f8fafc";
        context.textBaseline = "middle";
        context.fillText(node.label, point.x + radius + 4, point.y);
      }
    }
  }

  fit() {
    if (!this.positions.size) return;
    const rect = this.canvas.getBoundingClientRect();
    const points = [...this.positions.values()];
    const xs = points.map(point => point.x), ys = points.map(point => point.y);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const spanX = Math.max(maxX - minX, 200), spanY = Math.max(maxY - minY, 200);
    this.zoom = Math.max(.22, Math.min(1.35, (rect.width - 110) / spanX, (rect.height - 110) / spanY));
    this.panX = rect.width / 2 - (minX + maxX) / 2 * this.zoom;
    this.panY = rect.height / 2 - (minY + maxY) / 2 * this.zoom;
    this.draw();
  }

  nearest(x, y) {
    let best = null;
    for (const node of this.nodes) {
      const point = this.screen(this.positions.get(node.node_id));
      const distance = Math.hypot(x - point.x, y - point.y);
      const threshold = Math.max(10, this.radius(node) * this.zoom + 8);
      if (distance <= threshold && (!best || distance < best.distance)) best = { id: node.node_id, distance };
    }
    return best?.id || null;
  }

  selectNode(id) {
    this.selected = id;
    if (id) {
      const node = this.nodeMap.get(id);
      updateMindActions(node);
      const detail = node.detail.length > 500 ? `${node.detail.slice(0, 499)}…` : node.detail;
      elements.mindDetail.textContent = `${node.label} — ${detail}`;
    } else {
      updateMindActions(null);
      elements.mindDetail.textContent = "Select a node to see what Aura knows about it.";
    }
    this.draw();
  }

  installEvents() {
    this.canvas.addEventListener("pointerdown", event => {
      this.canvas.setPointerCapture(event.pointerId);
      const id = this.nearest(event.offsetX, event.offsetY);
      this.selectNode(id);
      if (id) {
        this.dragNode = id;
      } else {
        this.panAnchor = { x: event.clientX, y: event.clientY };
      }
      this.canvas.classList.add("dragging");
    });
    this.canvas.addEventListener("pointermove", event => {
      if (this.dragNode && this.dragNode !== "aura") {
        const rect = this.canvas.getBoundingClientRect();
        const position = this.positions.get(this.dragNode);
        position.x = (event.clientX - rect.left - this.panX) / this.zoom;
        position.y = (event.clientY - rect.top - this.panY) / this.zoom;
        position.vx = 0; position.vy = 0; this.draw();
      } else if (this.panAnchor) {
        this.panX += event.clientX - this.panAnchor.x;
        this.panY += event.clientY - this.panAnchor.y;
        this.panAnchor = { x: event.clientX, y: event.clientY }; this.draw();
      }
    });
    const release = () => { this.dragNode = null; this.panAnchor = null; this.canvas.classList.remove("dragging"); };
    this.canvas.addEventListener("pointerup", release);
    this.canvas.addEventListener("pointercancel", release);
    this.canvas.addEventListener("click", event => {
      const id = this.nearest(event.offsetX, event.offsetY);
      if (id && id !== this.selected) this.selectNode(id);
    });
    this.canvas.addEventListener("dblclick", event => {
      const id = this.nearest(event.offsetX, event.offsetY);
      const node = id ? this.nodeMap.get(id) : null;
      if (!node) return;
      updateMindActions(node);
      if (node.target) openMindTarget(); else askAboutMindNode();
    });
    this.canvas.addEventListener("wheel", event => {
      event.preventDefault();
      const rect = this.canvas.getBoundingClientRect();
      const x = event.clientX - rect.left, y = event.clientY - rect.top;
      const oldZoom = this.zoom;
      const next = Math.max(.2, Math.min(3, oldZoom * (event.deltaY < 0 ? 1.12 : 1 / 1.12)));
      const worldX = (x - this.panX) / oldZoom, worldY = (y - this.panY) / oldZoom;
      this.zoom = next; this.panX = x - worldX * next; this.panY = y - worldY * next; this.draw();
    }, { passive: false });
  }

  close() { cancelAnimationFrame(this.frame); }
}

const mindGraph = new MindGraph(elements.mindCanvas);

async function openMind() {
  if (!elements.workspaceView.classList.contains("hidden")) closeWorkspaceExplorer();
  elements.mindView.classList.remove("hidden");
  renderMindLegend();
  elements.mindDetail.textContent = "Reading Aura's local memory and workspace map…";
  try {
    const result = await callApi("get_mind_graph");
    if (!result.ok) throw new Error(result.error);
    requestAnimationFrame(() => mindGraph.load(result));
  } catch (error) {
    elements.mindDetail.textContent = String(error);
  }
}

function closeMind() {
  elements.mindView.classList.add("hidden");
  mindGraph.close();
  elements.composer.focus();
}

function bindControls() {
  elements.send.addEventListener("click", () => sendMessage());
  elements.stop.addEventListener("click", () => callApi("stop"));
  elements.composer.addEventListener("input", autoSizeComposer);
  elements.composer.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendMessage(); }
  });
  elements.voiceButton.addEventListener("pointerdown", event => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    elements.voiceButton.setPointerCapture?.(event.pointerId);
    clearTimeout(voiceHoldTimer);
    voiceHoldTimer = setTimeout(() => {
      voiceHolding = true;
      voiceSuppressClick = true;
      voiceStartPromise = beginVoice("hold");
    }, 260);
  });
  elements.voiceButton.addEventListener("pointerup", () => {
    clearTimeout(voiceHoldTimer);
    voiceHoldTimer = null;
    if (voiceHolding) {
      voiceHolding = false;
      Promise.resolve(voiceStartPromise).then(() => endVoice(false));
      setTimeout(() => { voiceSuppressClick = false; }, 180);
    }
  });
  elements.voiceButton.addEventListener("pointercancel", () => {
    clearTimeout(voiceHoldTimer);
    voiceHoldTimer = null;
    if (voiceHolding) endVoice(true);
    voiceHolding = false;
  });
  elements.voiceButton.addEventListener("click", () => {
    if (voiceSuppressClick) return;
    toggleVoice();
  });
  elements.voiceCancel.addEventListener("click", () => voiceActive ? endVoice(true) : setVoiceSession("idle"));
  elements.voiceRetry.addEventListener("click", () => beginVoice("toggle"));
  $("#mindButton").addEventListener("click", openMind);
  $("#memoryButton").addEventListener("click", () => openPersonalMemory());
  $("#permissionsButton").addEventListener("click", () => openPermissions());
  $("#sessionsButton").addEventListener("click", () => openSessions());
  $("#showArchivedSessions").addEventListener("change", () => openSessions(false));
  let sessionSearchTimer = null;
  $("#sessionSearch").addEventListener("input", () => {
    clearTimeout(sessionSearchTimer);
    sessionSearchTimer = setTimeout(() => openSessions(false), 220);
  });
  $("#newSessionButton").addEventListener("click", startNewSession);
  $("#permissionGrant").addEventListener("submit", grantFolderAccess);
  $("#domainGrant").addEventListener("submit", grantDomainAccess);
  $("#watchButton").addEventListener("click", () => openWatchPanel());
  elements.menuButton.addEventListener("click", () => toggleSideMenu());
  document.addEventListener("click", event => {
    // Clicking anywhere else closes it, which is what a menu is expected to do.
    if (elements.sideMenu.classList.contains("hidden")) return;
    if (elements.sideMenu.contains(event.target) || elements.menuButton.contains(event.target)) return;
    toggleSideMenu(false);
  });
  for (const item of elements.sideMenu.querySelectorAll(".menu-item")) {
    item.addEventListener("click", () => toggleSideMenu(false));
  }
  elements.pauseAutonomy.addEventListener("click", async () => {
    const paused = elements.pauseAutonomy.classList.contains("paused");
    const result = await callApi("pause_autonomy", !paused);
    if (!result.ok) return toast(result.error, true);
    renderAutonomyStatus(result.autonomy);
    toast(paused ? "Background work resumed." : "Background work paused.");
  });
  $("#emergencyStop").addEventListener("click", async () => {
    const result = await callApi("emergency_stop");
    if (!result.ok) return toast(result.error, true);
    renderAutonomyStatus(result.autonomy);
    toast("Stopped. Background work is paused and anything running was cancelled.", true);
  });
  $("#permissionRevokeAll").addEventListener("click", revokeAllPermissions);
  $("#settingsButton").addEventListener("click", openSettings);
  $("#workspaceButton").addEventListener("click", async () => {
    const result = await callApi("open_workspace"); if (!result.ok) toast(result.error, true);
  });
  $("#tasksButton").addEventListener("click", () => openTasks());
  $("#filesButton").addEventListener("click", () => openWorkspaceExplorer());
  $("#quitButton").addEventListener("click", quitAura);
  $("#clearButton").addEventListener("click", clearConversation);
  elements.toggleLog.addEventListener("click", () => { logVisible = !logVisible; applyLayout(); scheduleUiSave(); });
  elements.activityLogTab.addEventListener("click", () => setLogMode("activity"));
  elements.diagnosticsLogTab.addEventListener("click", () => setLogMode("diagnostics"));
  $("#welcomeNext").addEventListener("click", async () => {
    // Checking the connection is offered, never demanded: someone who starts
    // LM Studio afterwards must still be able to get through the guide.
    if (welcomeStep === 2 && $("#welcomeModel").options.length <= 1) await checkWelcomeConnection();
    if (welcomeStep === WELCOME_STEPS) return finishOnboarding();
    showWelcomeStep(welcomeStep + 1);
  });
  $("#welcomeBack").addEventListener("click", () => showWelcomeStep(welcomeStep - 1));
  $("#welcomeCheck").addEventListener("click", checkWelcomeConnection);
  $("#welcomeSkip").addEventListener("click", () => finishOnboarding(true));
  $("#showWelcome").addEventListener("click", async () => {
    await callApi("restart_onboarding");
    closeModal(elements.settingsModal);
    startOnboarding();
  });
  $("#exportDiagnostics").addEventListener("click", async () => {
    const written = await callApi("export_diagnostics");
    if (!written.ok) return toast(written.error, true);
    toast(`Report saved to workspace as ${written.path}.`);
  });
  $("#collapseSidebar").addEventListener("click", () => {
    const collapsed = elements.sidebar.classList.toggle("collapsed");
    const toggle = $("#collapseSidebar");
    if (collapsed) { expandedSidebarWidth = sidebarWidth; sidebarWidth = 72; toggle.textContent = "›"; }
    else { sidebarWidth = expandedSidebarWidth; toggle.textContent = "‹"; }
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
    toggle.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
    applyLayout();
  });
  $("#refreshModels").addEventListener("click", refreshModels);
  $("#refreshVoices").addEventListener("click", refreshVoices);
  $("#refreshMicrophones").addEventListener("click", () => refreshMicrophones(true));
  $("#calibrateMicrophone").addEventListener("click", calibrateMicrophone);
  $("#previewVoice").addEventListener("click", previewVoice);
  $("#saveSettings").addEventListener("click", saveSettings);
  $("#settingRate").addEventListener("input", updateRangeOutputs);
  $("#settingVolume").addEventListener("input", updateRangeOutputs);
  $("#settingAvatarIntensity").addEventListener("input", updateRangeOutputs);
  $("#settingCalibration").addEventListener("input", updateRangeOutputs);
  $("#settingSilence").addEventListener("input", updateRangeOutputs);
  $("#allowApproval").addEventListener("click", () => resolveApproval(true));
  $("#allowTaskApproval").addEventListener("click", () => resolveApproval(true, "exact_task"));
  $("#denyApproval").addEventListener("click", () => resolveApproval(false));
  $("#memoryTeach").addEventListener("submit", teachAura);
  $("#memorySearch").addEventListener("input", renderPersonalMemories);
  $("#memoryExport").addEventListener("click", exportPersonalMemory);
  $("#mindClose").addEventListener("click", closeMind);
  $("#mindFit").addEventListener("click", () => mindGraph.fit());
  $("#mindReset").addEventListener("click", () => mindGraph.reset());
  $("#mindRefresh").addEventListener("click", openMind);
  $("#mindAsk").addEventListener("click", askAboutMindNode);
  $("#mindOpen").addEventListener("click", openMindTarget);
  elements.mindSearch.addEventListener("input", () => { mindGraph.search = elements.mindSearch.value; mindGraph.draw(); });
  elements.mindSearch.addEventListener("keydown", event => {
    if (event.key !== "Enter") return;
    const query = elements.mindSearch.value.trim().toLowerCase();
    const node = query && (
      mindGraph.nodes.find(item => item.target?.toLowerCase() === query) ||
      mindGraph.nodes.find(item => item.label.toLowerCase() === query && item.target) ||
      mindGraph.nodes.find(item => item.target?.toLowerCase().includes(query)) ||
      mindGraph.nodes.find(item => item.label.toLowerCase().includes(query)) ||
      mindGraph.nodes.find(item => item.detail.toLowerCase().includes(query))
    );
    if (node) { event.preventDefault(); mindGraph.selectNode(node.node_id); }
  });
  $("#workspaceClose").addEventListener("click", closeWorkspaceExplorer);
  $("#workspaceRefresh").addEventListener("click", () => trashMode ? loadTrash() : loadWorkspace(selectedWorkspaceFile?.path));
  $("#workspaceOpenFolder").addEventListener("click", async () => {
    const result = await callApi("open_workspace"); if (!result.ok) toast(result.error, true);
  });
  $("#workspaceImport").addEventListener("click", () => elements.filePicker.click());
  $("#workspaceNewFile").addEventListener("click", promptNewFile);
  $("#workspaceNewFolder").addEventListener("click", promptNewFolder);
  $("#workspaceTrashToggle").addEventListener("click", toggleTrashView);
  $("#workspaceHistory").addEventListener("click", openHistory);
  $("#workspacePreviewServer").addEventListener("click", openPreviewServerModal);
  elements.previewServerForm.addEventListener("submit", submitPreviewServerStart);
  elements.previewServerOpen.addEventListener("click", openPreviewServerInBrowser);
  elements.previewServerCheckAssets.addEventListener("click", checkPreviewAssets);
  elements.previewServerStop.addEventListener("click", stopPreviewServerHandler);
  elements.previewCompare.addEventListener("click", armDiffPick);
  elements.promptForm.addEventListener("submit", submitPrompt);
  elements.workspaceSearch.addEventListener("input", () => trashMode ? renderTrashList() : renderWorkspaceTree());
  elements.previewAsk.addEventListener("click", () => {
    if (!selectedWorkspaceFile) return;
    const path = selectedWorkspaceFile.path;
    closeWorkspaceExplorer();
    sendMessage(`Inspect \`${path}\`, explain what it does, and suggest the most useful next improvement. Do not change it yet.`);
  });
  elements.previewOpen.addEventListener("click", async () => {
    if (!selectedWorkspaceFile) return;
    const result = await callApi("open_workspace_item", selectedWorkspaceFile.path);
    if (!result.ok) toast(result.error, true);
  });
  elements.previewPath.addEventListener("click", () => document.querySelector(".workspace-preview-panel")?.classList.remove("active"));
  elements.attachButton.addEventListener("click", () => elements.filePicker.click());
  elements.filePicker.addEventListener("change", () => importFiles(elements.filePicker.files));
  document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => closeModal($(`#${button.dataset.close}`))));
  [elements.settingsModal, elements.tasksModal, elements.memoryModal, elements.promptModal,
   elements.historyModal, elements.previewServerModal, elements.permissionsModal,
   elements.sessionsModal]
    .forEach(modal => modal.addEventListener("click", event => {
      if (event.target === modal) closeModal(modal);
    }));
  document.addEventListener("keydown", event => {
    if (event.ctrlKey && event.key.toLowerCase() === "m") { event.preventDefault(); openMind(); return; }
    if (event.ctrlKey && event.key.toLowerCase() === "l") { event.preventDefault(); clearConversation(); return; }
    if (event.ctrlKey && event.key.toLowerCase() === "o") { event.preventDefault(); openWorkspaceExplorer(); return; }
    if (event.ctrlKey && event.key === ",") { event.preventDefault(); openSettings(); return; }
    if (event.key === "Escape") {
      // Whatever was opened last is what Escape closes, so every dialog
      // behaves the same — including ones added later.
      const top = modalStack[modalStack.length - 1];
      if (currentApproval) resolveApproval(false);
      else if (diffPickActive) cancelDiffPick();
      else if (top === elements.welcomeModal) finishOnboarding(true);
      else if (top) closeModal(top);
      else if (!elements.workspaceView.classList.contains("hidden")) closeWorkspaceExplorer();
      else if (!elements.mindView.classList.contains("hidden")) closeMind();
      else if (busy) callApi("stop");
    }
  });
  window.addEventListener("resize", () => { applyLayout(); scheduleUiSave(); });
  window.addEventListener("dragenter", event => {
    if (![...(event.dataTransfer?.types || [])].includes("Files")) return;
    event.preventDefault(); dragDepth += 1; elements.dropOverlay.classList.remove("hidden");
  });
  window.addEventListener("dragover", event => {
    if ([...(event.dataTransfer?.types || [])].includes("Files")) event.preventDefault();
  });
  window.addEventListener("dragleave", event => {
    if (![...(event.dataTransfer?.types || [])].includes("Files")) return;
    dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) elements.dropOverlay.classList.add("hidden");
  });
  window.addEventListener("drop", event => {
    event.preventDefault(); dragDepth = 0; elements.dropOverlay.classList.add("hidden");
    if (event.dataTransfer?.files?.length) importFiles(event.dataTransfer.files);
  });
  installResizers();
  installFaceInteraction();
}

function shutdownFrontend() {
  if (eventPollTimer) clearInterval(eventPollTimer);
  eventPollTimer = null;
  clearTimeout(saveTimer);
  mindGraph.close();
  avatarMotion?.destroy();
}

async function initialize() {
  if (initialized) return;
  initialized = true;
  bindControls();
  try {
    const bootstrap = await callApi("get_bootstrap");
    eventCursor = Number(bootstrap.event_cursor) || 0;
    sidebarWidth = Number(bootstrap.ui.sidebar_width) || 250;
    expandedSidebarWidth = sidebarWidth;
    logHeight = Number(bootstrap.ui.log_height) || 170;
    logVisible = Boolean(bootstrap.ui.log_visible);
    avatarMotion?.applySettings(bootstrap.avatar || {});
    applyLayout();
    elements.workspacePath.textContent = bootstrap.workspace;
    elements.workspacePath.title = bootstrap.workspace;
    updatePowerStatus(bootstrap.capabilities || {});
    renderNetworkStatus(bootstrap.network);
    renderAutonomyStatus(bootstrap.autonomy);
    clearConversation();
    logEvents = [...(bootstrap.actions || [])];
    setLogMode("activity");
    if (bootstrap.conversation.length) {
      for (const item of bootstrap.conversation) addMessage(item.role, item.text);
    } else {
      addMessage("assistant", "Hello. I’m Aura, running locally. What shall we make?");
    }
    setProvider(bootstrap.provider);
    updateSuggestions(bootstrap.conversation.at(-1)?.text || "hello");
    setBusy(false);
    const requestedAvatarState = new URLSearchParams(window.location.search).get("avatarState");
    setState(["idle", "listening", "thinking", "working", "success", "error"].includes(requestedAvatarState)
      ? requestedAvatarState : "idle");
    const previewParams = new URLSearchParams(window.location.search);
    const requestedVoiceState = previewParams.get("voiceState");
    if (previewParams.get("phase41") === "verify"
        && ["starting", "calibrating", "listening", "processing", "recognized", "error"].includes(requestedVoiceState)) {
      const sample = requestedVoiceState === "error"
        ? "I couldn’t hear speech. Check the selected microphone or retry."
        : requestedVoiceState === "recognized" ? "hello Aura, let’s create something" : "";
      setVoiceSession(requestedVoiceState, sample || "Voice is processed locally.", sample);
      if (["listening", "calibrating"].includes(requestedVoiceState)) setVoiceLevel(.68);
    }
    elements.composer.focus();
    const previewPath = new URLSearchParams(window.location.search).get("preview");
    if (previewPath) await openWorkspaceExplorer(previewPath);
    if (!bootstrap.onboarded) startOnboarding(bootstrap.workspace);
    await showPendingProposals();
    await callApi("check_provider");
    eventPollTimer = setInterval(pollEvents, pollIntervalMs);
  } catch (error) {
    addMessage("assistant", `Aura's interface could not connect to the Python core: ${error}`);
    toast(String(error), true);
  }
}

window.addEventListener("beforeunload", shutdownFrontend, { once: true });
avatarMotion = new AuraAvatar3D(elements.avatarCanvas, elements.face);
initialize();
