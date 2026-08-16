# Aura

Aura is a local-first AI companion with one interface: HTML, CSS, and JavaScript in your normal web browser. A small local Python service provides the secure connection to LM Studio, workspace tools, approvals, memory, speech, and logs. Aura does not use Tkinter, pywebview, a cloud service, a login, or a database server.

## Run

Requires Python 3.10 or newer and a normal web browser. No frontend package installation is needed.

```powershell
python aura_app.py
```

On Windows, double-click `Start Aura.bat`. It launches Aura with `.venv\Scripts\pythonw.exe` when a local `.venv` is present in the project folder (needed for the optional voice packages in `requirements-voice.txt` and `requirements-neural-voice.txt`), and only falls back to a system-wide Python install if no `.venv` exists. Likewise, running `python aura_app.py` directly assumes that interpreter has those packages installed — activate `.venv` first if you set one up. Aura opens the HTML interface automatically at `http://127.0.0.1:8765`. If that port is occupied, it uses the next free port through `8774`; the current address is recorded in `aura-workspace/.aura/web-url.txt`. Starting Aura again reopens the running interface instead of creating a duplicate backend.

Use **Quit Aura** in the sidebar to stop the local service. Closing a browser tab leaves Aura running, so an active task is not accidentally killed and the page can be reopened.

Launch and recovery history is written to `aura-runtime.log`. A full traceback is saved to `aura-startup-error.log` only when an interface fails, so a desktop-launch problem is no longer silent.

## LM Studio setup

1. Open LM Studio and download/load a chat model.
2. Open **Developer** and start the Local Server (default port `1234`).
3. Start Aura. It discovers the first model exposed by LM Studio and uses it for chat.

Use **Settings** to switch between models exposed by the server and persist the server URL, model, timeout, temperature, thinking depth, autonomy, local speech preference, and Aura's avatar motion, intensity, and rendering quality. **Deep + Powerful** is the default: it gives Aura longer multi-step runs and the broad tool catalog while preserving approval boundaries for executable code, external network access, and desktop launches. Aura streams generated text into the chat as LM Studio produces it.

No cloud login, API key, or internet connection is used for chat. Aura connects to `http://127.0.0.1:1234/v1` by default.

Optional configuration:

```powershell
$env:AURA_LM_STUDIO_URL = "http://127.0.0.1:1234/v1"
$env:AURA_LM_STUDIO_MODEL = "your-model-identifier"
$env:AURA_LM_STUDIO_TIMEOUT = "180"
python aura_app.py
```

Leave `AURA_LM_STUDIO_MODEL` unset for automatic model discovery through `/v1/models`.

Environment variables provide initial provider defaults. Values saved through Settings are stored locally at `aura-workspace/.aura/config.json`.

Try:

- `Aura, create a hello world Python app`
- `list files`
- `read file hello-world/hello.py`
- `remember my name is Maya`
- `remember preference tone = concise`
- `Create a small calculator website in a calculator folder, then check the files.`
- `Find every workspace file that mentions TODO.`
- `Rename notes/draft.txt to notes/finished.txt.`
- `Run the tests and explain any failures.`
- `Inspect this project deeply, outline the code, compare related files, fix the problems, and verify everything.`
- `Create a ZIP backup of the calculator folder.`
- `Check the response from http://127.0.0.1:3000 and diagnose the page.`

LM Studio can now select 48 local tools. The expanded catalog includes batch file reads and writes, code outlines, bounded diffs, relevance-ranked file discovery, looking at workspace images with a vision-capable model, approval-gated page screenshots, deterministic image comparison, structural accessibility checks, reading and recoverably writing user-granted folders outside the workspace, safe arithmetic, non-sensitive system information, local/external HTTP inspection, recoverable ZIP creation and guarded extraction, validation, transparent personal learning, memory correction/forgetting, and command execution. For a build request Aura silently plans, inspects, creates files, validates, runs approved checks, repairs errors, and reports confirmed results across as many as 48 tool rounds in Deep mode.

The approval dialog offers **Allow once** and **Allow identical for task**. The latter remembers only the exact same command or URL until that one task ends; a changed argument or destination asks again.

Aura keeps protected snapshots before file and folder mutations. Ask `undo the last file change` to restore the previous version; displaced files and folders remain recoverable in `.aura-trash`. The agent also has real empty-folder creation, line-range reading, matching-line search, exact replacement, append, and file-information tools for safer work on larger projects.

## Desktop controls

Aura's browser frame is responsive: drag either divider to resize the sidebar, conversation, or action log. Aura's active avatar is the locally adapted feminine digital-human renderer in `aura/web/avatar-face.js`, based on the improved face supplied by the user. It contains no photograph, cloud renderer, external font, browser speech engine, or frontend package. Thousands of depth-positioned points and connected lines form a shaped forehead, jaw, cheeks, nose, nostrils, eyebrows, eyelids, irises, pupils, articulated lips, ears, neck, shoulders, and layered hair. Perspective projection gives her independent iris gaze, softer pointer tracking, natural variable blinks, restrained head motion, and distinct idle, listening, thinking, working, success, and error expressions. The renderer pauses off-screen, adapts detail and frame rate to load, and honors the operating system's reduced-motion preference. The message composer supports multiple lines, and panel preferences persist between launches.

With Piper, mouth opening follows the amplitude envelope measured directly from Aura's locally generated WAV. The Windows SAPI fallback uses local phoneme-timing estimates because SAPI does not expose its output samples. Nothing is sent away from the computer. Motion style, intensity, and automatic/high/lower detail can be changed under **Settings → Presence**.

- **Enter** sends a message; **Shift+Enter** inserts a new line.
- **Escape** stops the current task.
- **Ctrl+L** clears the visible conversation.
- **Ctrl+M** opens Aura Mind.
- **Ctrl+O** opens the safe workspace.
- **Ctrl+,** opens Settings.
- **Hide/Show action log** gives the conversation more room. **Activity** shows friendly events from the current Aura session, while **Diagnostics** exposes the corresponding technical names and details; the durable audit history is still preserved locally in `aura.db`.
- Use the **+** button or drag up to five files anywhere over Aura to copy them into the protected workspace.

### Conversations

**New** starts a fresh conversation and **Conversations** lists the earlier ones, each named after its first message and kept locally in `aura.db`. Opening one restores it as Aura's live context, so she continues where that conversation left off. **Clear** only empties the view; starting a new conversation never deletes an old one, and a launch you never spoke in is not kept as one. **Archive** hides a conversation from the list without deleting it — tick **Show archived** to see it again and **Restore** it. The current conversation cannot be archived; start a new one first.

### Interactive workspace

Select **Workspace** in the sidebar to open Aura's built-in local explorer. It provides file filtering, sizes, safe text/code previews, sandboxed HTML/SVG rendering, image previews, and direct **Ask Aura** and **Open** actions. Rendered pages receive a protected read-only workspace URL, so their relative stylesheets, images, fonts, and links work correctly. Scripts, forms, outside connections, and embedded frames remain disabled. Imported files use recoverable workspace snapshots and are renamed safely instead of overwriting an existing file.

Replies can include compact task cards with tools used and buttons for **Details**, **Workspace**, **Repeat**, and recoverable **Undo**. Suggested-action chips below the conversation adapt to greetings, build results, and ordinary chat.

### Aura Mind

Select **Aura Mind** in the sidebar to open a living visual map inspired by a knowledge graph. It uses Aura's actual local state: remembered identity and preferences, recent conversation, task outcomes, tools used, workspace folders, and files. Nothing is uploaded or inferred from an external service.

Drag nodes to rearrange them, drag empty space to pan, use the mouse wheel to zoom, search to highlight matching knowledge, and select a node for details. Selected nodes can now be sent back to Aura as conversational context; file and folder nodes can open directly in the workspace explorer. **Refresh** reads the latest safe local state, **Fit** frames the current graph, and **Reset** rebuilds its force-directed layout. The graph caps displayed files for readability but never deletes or changes workspace data.

### Personal learning

Select **What Aura knows** to review Aura's structured understanding of you. Aura can learn only clear, non-sensitive first-person statements such as `I prefer concise answers`, `I use Python for prototypes`, or `My goal is to finish AuraCraft`. New learned facts produce a visible notification and appear with category, source, confidence, confirmation state, and update date.

Every personal memory can be edited, pinned for stronger recall, or forgotten. Manual additions are marked as user-confirmed. Relevant memories are selected for each request instead of sending the whole profile to the model, and internal IDs, confidence metadata, and source text are not included in LM Studio context. Automatic conversational learning can be disabled at any time in **Settings**; manual memory controls continue to work.

Aura deliberately refuses to learn credentials and filters health, exact contact/location, religion, politics, sexuality, and similar sensitive traits from automatic personal memory. All accepted memories remain in the protected local JSON store and also appear as a dedicated branch in Aura Mind.

## Advanced agent behavior

- Aura routes each request to a focused subset of 48 tools. Deep + Powerful mode adds the broader inspection, batch, comparison, validation, and personal-memory set to complex requests without loading every irrelevant schema.
- Thinking depth controls the multi-step ceiling: Fast 16 rounds, Balanced 30, Deep 48.
- Actionable requests cannot be marked complete until at least one relevant tool succeeds.
- File mutations trigger a verification requirement: Aura must read back the final state or run a successful validation command.
- If a local model repeatedly skips the final read-back, Aura performs a separate deterministic filesystem verification instead of adding an ambiguous warning.
- When Aura cannot prove the work was done, it still shows the reply and adds a **Not confirmed** section naming exactly what is unproven, rather than discarding the answer.
- All completion gates share one retry budget of three extra rounds, so a stubborn request cannot spin.
- Successful mutations and builds end with deterministic **Confirmed evidence** covering final files and fresh validation results instead of relying only on the model's wording.
- If LM Studio returns a partial streamed tool call or invalid atomic JSON, Aura retries through a bounded non-streaming repair turn and records the recovery in Diagnostics.
- If the model falsely says an available action is impossible, Aura detects the missing work, corrects the model, and retries within the bounded tool loop.
- Multi-replacement edits are atomic and produce one recovery snapshot.
- Every request is recorded in `aura-workspace/.aura/tasks.jsonl` with its outcome and redacted tool history.
- Use **Recent tasks** to inspect outcomes and **Stop** to cancel further tool execution.
- A task interrupted by a restart shows as **Interrupted** and offers **Resume**. Aura continues from what is verifiably on disk rather than replaying the old conversation, so nothing is done twice and any permission is asked for again.
- Explicit filenames and target folders become a deterministic artifact contract. Aura checks every exact path before completion.
- Validation is freshness-aware: any later file mutation invalidates an earlier successful validation.
- `validate_project` checks every project file without executing project code: Python, JSON, TOML, HTML structure, CSS/JavaScript/TypeScript structure, XML/SVG, and UTF-8 text.
- Each mutation is tagged with its task ID. Use **Rollback task** to recover every active file change from one task while leaving other tasks untouched.
- Build requests receive a compact workspace snapshot and follow inspect → plan → implement → validate → repair → verify stages.

## Voice

Aura uses Piper with the local `en_US-lessac-medium` neural model for substantially more natural speech. The model remains loaded between replies to reduce latency. Windows SAPI remains an automatic fallback and can be selected manually in **Settings**, alongside rate, volume, and fallback-voice controls. Aura cleans Markdown, links, code blocks, and emoji into speech-friendly text, streams mouth cues to the avatar, and stops older speech when a newer reply begins. Speech is generated and played entirely on this computer.

The neural runtime is recorded in `requirements-neural-voice.txt`; its model and configuration are stored in `aura-voices/`. If Piper or its model is unavailable, Aura continues speaking through SAPI instead of failing.

Microphone input is offline-first and now supports two natural interaction styles: click **Voice** once for automatic end-of-speech detection, or hold it while talking and release to send. Aura shows a live input meter, partial PocketSphinx transcription, calibration/listening/processing states, retry and cancel controls, and immediately stops her own speech when you begin talking. **Settings → Voice input** provides compatible microphone selection, room calibration, language, timeout, end-of-speech timing, and recognizer choice. The active local installation includes streaming PocketSphinx and SoundDevice; text chat remains available if audio hardware fails.

Whisper.cpp is supported as an optional stronger recognizer. Select it in Settings and provide the local executable and GGML/GGUF model paths; Automatic mode prefers it when both are available and otherwise uses PocketSphinx. Aura never downloads a model silently and never sends microphone audio over the network.

## Diagnostics

```powershell
python aura_diagnostics.py
```

This checks Python, the local HTML service and assets, the LM Studio server, and model discovery.

## Safety model

- User files are constrained to `./aura-workspace`.
- Absolute paths, `..` traversal, symlinks, and Aura metadata paths are rejected.
- Deleted files move to `aura-workspace/.aura-trash`.
- Commands use argument arrays (no command shell), run with the workspace as their working directory, capture output, and time out.
- Version checks, Python `compileall`/`py_compile`/`json.tool`, and Node syntax checks can be auto-approved when they only target workspace paths. Project runtimes, test suites, package scripts/installers, external HTTP requests, and desktop launches require visible confirmation.
- Actions, tasks, workspace changes, trash records, and conversations live in `aura-workspace/.aura/aura.db`; settings, personal memory, and permissions stay as readable JSON beside it.
- The local interface answers on `127.0.0.1` and `localhost` only, and every API call additionally needs the session cookie and Aura's own client header.
- Recovery is kept for 30 days or 500 changes, whichever ends first. Expiring a change removes its backups in the same transaction, and Aura never deletes a backup another record still needs.

## Tests

```powershell
python -m unittest discover -v
```

The suite currently contains 187 checks, including real PCM level metering, streaming partial/final voice sessions, hold/release, speech interruption, optional Whisper.cpp parsing, voice preview, audio-envelope/phoneme cues, and an isolated Chrome/Edge launch that captures a styled preview. If neither browser is installed, only that optional browser smoke check is skipped.

## Project map

- `aura/web/` — the only UI: HTML/CSS/JavaScript, animated face, and Canvas Aura Mind
- `aura/http_app.py` — authenticated localhost server and narrow browser API
- `aura/web_bridge.py` — structured events and UI-facing operations
- `aura/graph_model.py` — dependency-free local Aura Mind data model
- `aura/validation.py` — safe multi-format project validation
- `aura/search_index.py` — dependency-free BM25 ranking for workspace file discovery
- `aura/screenshot.py` — headless Chromium page capture, no extra packages
- `aura/image_diff.py` — standard-library PNG decoding and pixel comparison
- `aura/permissions.py` — revocable grants for folders outside the safe workspace
- `aura/agent.py` — LM Studio tool loop, request routing, and project builder
- `aura/safety.py` — sandboxed file agent and recoverable deletion
- `aura/commands.py` — command policy, execution, capture, timeout
- `aura/provider.py` — LM Studio connection, model discovery, provider interface
- `aura/config.py` — persistent local settings
- `aura/speech.py` — local Piper neural speech with Windows SAPI fallback
- `aura/memory.py` — JSON preferences and recent conversation
- `aura/store.py` — one local SQLite file behind the journals and recovery records
- `aura/action_log.py` — persistent audit trail

Generated project files remain inside `aura-workspace`; use **Open workspace** to inspect them in Explorer. **Clear** only clears the visible chat and does not erase memory or files.
