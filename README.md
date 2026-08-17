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

With Piper, mouth opening follows the amplitude envelope measured directly from Aura's locally generated WAV. Audio starts on the Python side the moment those cues are pushed, but they only reach the page on its next poll, so the mouth clock is offset by however long the event actually spent in transit and polling tightens from 140 ms to 45 ms while Aura speaks; between cues the jaw is interpolated rather than stepped. Measured in the running app, the delivery delay fell from 140–280 ms to about 30 ms. The Windows SAPI fallback uses local phoneme-timing estimates because SAPI does not expose its output samples. Nothing is sent away from the computer. Motion style, intensity, and automatic/high/lower detail can be changed under **Settings → Presence**.

On the first launch a short guide explains what Aura is, helps connect LM Studio and pick a model, and points out where permissions, undo, and the diagnostics report live. It can be skipped without changing any setting, and reopened later from **Settings → Show first-run guide**.

Aura is usable from the keyboard alone. An open dialog keeps focus inside itself and returns it to the control that opened it, **Escape** closes whichever dialog is on top, and finished replies are announced once to a screen reader rather than repeated on every streamed word. Small helper text meets the 4.5:1 contrast ratio, and the avatar honours the system's reduced-motion setting.

- **Enter** sends a message; **Shift+Enter** inserts a new line.
- **Escape** stops the current task.
- **Ctrl+L** clears the visible conversation.
- **Ctrl+M** opens Aura Mind.
- **Ctrl+O** opens the safe workspace.
- **Ctrl+,** opens Settings.
- **Hide/Show action log** gives the conversation more room. **Activity** shows friendly events from the current Aura session, while **Diagnostics** exposes the corresponding technical names and details; the durable audit history is still preserved locally in `aura.db`.
- **Export report** writes a diagnostics file into the workspace: this machine, the settings in use, storage sizes and row counts, retention sweeps, granted folders, recent tasks, and everything that recently failed. Conversation text, personal memories, and file contents are deliberately left out, so the report can be shared when asking for help. Nothing is uploaded.
- Use the **+** button or drag up to five files anywhere over Aura to copy them into the protected workspace.

### Conversations

**New** starts a fresh conversation and **Conversations** lists the earlier ones, each named after its first message and kept locally in `aura.db`. Opening one restores it as Aura's live context, so she continues where that conversation left off. **Clear** only empties the view; starting a new conversation never deletes an old one, and a launch you never spoke in is not kept as one.

The search box looks through everything said in every conversation and shows the matching lines, so a conversation is recognisable without opening it; every word typed has to appear in the same message, and `%` or `_` search for themselves. **Export** writes one conversation into the workspace as readable Markdown. **Archive** hides a conversation from the list without deleting it — tick **Show archived** to see it again and **Restore** it. The current conversation cannot be archived; start a new one first.

### Interactive workspace

Select **Workspace** in the sidebar to open Aura's built-in local explorer. It provides file filtering, sizes, safe text/code previews, sandboxed HTML/SVG rendering, image previews, and direct **Ask Aura** and **Open** actions. Rendered pages receive a protected read-only workspace URL, so their relative stylesheets, images, fonts, and links work correctly. Scripts, forms, outside connections, and embedded frames remain disabled. Imported files use recoverable workspace snapshots and are renamed safely instead of overwriting an existing file.

Replies can include compact task cards with tools used and buttons for **Details**, **Workspace**, **Repeat**, and recoverable **Undo**. Suggested-action chips below the conversation adapt to greetings, build results, and ordinary chat.

### Aura Mind

Select **Aura Mind** in the sidebar to open a living visual map inspired by a knowledge graph. It uses Aura's actual local state: remembered identity and preferences, recent conversation, task outcomes, tools used, workspace folders, and files. Nothing is uploaded or inferred from an external service.

The legend along the bottom doubles as the filter: each layer — Identity, Memory, Preferences, Conversation, Tasks, Tools, Workspace — can be switched off to concentrate on the rest, and the header reports how much is hidden. A fact stored both as a preference and as something Aura learned about you is drawn once and hung under both headings rather than twice, a task with no recorded request is named instead of appearing as a blank circle, and a task is linked to the message that asked for it.

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

Spoken replies are punctuated before they are synthesised, because a neural voice takes its entire sense of rhythm from punctuation: a stripped bullet list carried no terminal marks at all and was read as one unbroken sentence. Each sentence is now synthesised separately and assembled with a real breath between sentences and a longer pause between paragraphs, and file paths are spoken as names — `shop/index.html` becomes "shop, index dot html" rather than a string of punctuation. Any Piper voice placed in `aura-voices/` (the `.onnx` and its `.onnx.json` together) can be selected under **Settings → Neural voice**; Aura never downloads one by itself, and nothing outside that folder can be chosen.

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
- Version checks, Python `compileall`/`py_compile`/`json.tool`, and Node syntax checks can be auto-approved when they only target workspace paths. Project runtimes, test suites, package scripts/installers, and desktop launches require visible confirmation.
- Aura is offline until you say otherwise. Apart from `localhost`, she cannot reach any address without a `reach_domain` grant you create under **Permissions**, and there is no tool that lets her ask for one — as with folders, she can use a grant but never request it. A grant covers the named host and its subdomains.
- A domain that is, or resolves to, a loopback, private, link-local, or reserved address is refused outright, whatever you type: a public name can point at your own router, and a dialog showing only the name gives you no way to see that. The address is checked again on every request and every redirect, because DNS can change in between.
- Whatever Aura reads over the network is listed under **Read from the network** in her reply, so an answer that left the machine names its sources.
- Actions, tasks, workspace changes, trash records, and conversations live in `aura-workspace/.aura/aura.db`; settings, personal memory, and permissions stay as readable JSON beside it.
- The local interface answers on `127.0.0.1` and `localhost` only, and every API call additionally needs the session cookie and Aura's own client header.
- Recovery is kept for 30 days or 500 changes, whichever ends first. Expiring a change removes its backups in the same transaction, and Aura never deletes a backup another record still needs.

## Undoing a whole conversation

**Conversations → Undo its changes** puts back every file that conversation changed, newest
task first. It shows the list of files before doing anything, and the versions it removes go to
the workspace trash, so the undo is itself recoverable.

A conversation that happened before Aura recorded which conversation each task belonged to
reports that it cannot be undone this way, rather than matching by timestamp — a near-miss
would undo somebody else's work. Single changes and single tasks can still be rolled back as
before.

## Is anything broken?

**More → Is anything broken?** runs one pass over everything Aura depends on — the model
server, the loaded model, images, the workspace, storage, speech, voice input, and web search —
and says which of them is not working and what to do about it. Aura can run it herself too; the
`self_check` tool is read-only.

Nothing in it changes anything, apart from writing and deleting a single probe file in the
workspace, because whether Aura can write there has no honest answer that avoids trying.

## Language

Aura chooses which tools to offer by reading the request, and those keywords were English only.
Measured on twenty ordinary requests, sixteen Estonian ones produced **no tools at all** — and
a model with no tools does not report a problem, it says it cannot help.

`aura/language.py` annotates Estonian stems with the English words the rules already match, so
the rules themselves are unchanged and adding another language means adding words in one place.
Hints attach to the end of the clause they were found in, which is what keeps "ehita leht, aga
ära käivita seda" from offering the very tool it forbids: Estonian negation (*ära, ärge, mitte,
ilma*) strips the clause and the hint with it.

When nothing matches at all, Aura is offered six read-only tools rather than none, so she can
look before answering. Guessing is acceptable for reading and never for writing — a test
asserts no tool that changes anything can appear in that fallback.

### Speaking two languages

Aura answers in whichever language you write in, so speech picks a voice per reply.
`language.detect` reads the whole reply rather than each sentence — Estonian prose around
English filenames is normal, and switching voice mid-sentence sounds worse than one voice
throughout — and falls back to the language of your *request* when a short reply gives nothing
away.

**There is no Estonian voice in the box.** Piper publishes none at all (174 voices, 55
languages, no Estonian), and Windows ships none until you add the language. Set one under
**Settings → Speech → Estonian voice** once you have one. Until then Aura still reads Estonian
aloud with the English voice and tells you that is what happened, because an English voice
reading Estonian sounds like a fault rather than a missing voice.

## Network services

Optional capabilities that live outside this machine are declared in `aura/services.py`. A service names the tool the model sees, the domains it needs, and a handler that receives a fetch already bound to the `reach_domain` check — so it cannot open its own connection or reach past what you granted. Adding one means writing a module and calling `register()`; the tool list and the tool loop stay untouched.

The one shipped service is keyless weather via Open-Meteo. Grant `geocoding-api.open-meteo.com` and `api.open-meteo.com` under **Permissions** and Aura can answer "what is the weather in Tartu right now?", citing both addresses she read. Until then the tool exists but refuses, naming the domains it would need.

### Web search

Aura still holds no search credentials. What she can do is read a [SearXNG](https://docs.searxng.org/) you run yourself: put its address in **Settings → Search** (empty means search is off) and `search_web` reads its results. Because SearXNG's own default is HTML only, add `json` under `search: formats:` in its `settings.yml` — if you forget, Aura says exactly that rather than "unreadable response".

Three things are worth knowing about how this is built:

- **Snippets only.** Aura reads titles, links, and the excerpt the engine produced, and never opens a result page. That is not a promise she keeps but a property of the permission model: a result URL is a domain you have not granted, so fetching it is refused like any other. A test asserts it.
- **She does not claim to have read what she links to.** The tool result says so in as many words, because a reply that upgrades "a snippet said" into "I read that page" is a small lie that compounds.
- **Five searches per turn.** A read-only tool costs nothing per call, which is why nothing stops it: the first live run produced twelve near-identical searches for one question. After the fifth, the tool tells the model to answer from what it already has.

Search runs on a loopback address, so it needs no domain grant — the service is one you started on your own machine. Pointing it at a public instance instead works too, and then the ordinary grant applies.

#### Aura starting SearXNG for you

SearXNG is not bundled: it is a Flask application with a large dependency tree, and Aura's core is standard library only. What Aura does own is its **lifecycle**. Give it the folder in **Settings → Search**, leave *Start the search engine when Aura starts* on, and Aura launches SearXNG on startup, waits until it actually answers, and stops it when you quit.

Owning the lifecycle removes the two things that otherwise fail quietly. Aura writes its own `aura-settings.yml` on every launch — never touching your `settings.yml` — so `json` is always among the formats, and `bind_address` is always `127.0.0.1`, because an engine you started for yourself should not answer the rest of the network.

If something is already listening on the port, Aura **adopts** it: it reads that instance and does not stop it on the way out, since it did not start it.

**Windows needs Docker.** SearXNG's `searx/valkeydb.py` imports `pwd`, a Unix-only module, so it does not import natively on Windows at all — a `pip install` there produces a service that cannot start. Aura says exactly this in Settings rather than reporting a missing package.

Set **Search engine** to *Docker* and Aura runs the official `searxng/searxng` image itself: it writes the mounted settings, starts the container on launch, waits until it answers, and removes it when you quit. The port is published to `127.0.0.1` only, never `0.0.0.0`, so the engine answers this machine and nothing else. The image is **not** downloaded automatically — if it is missing, Aura tells you to run `docker pull searxng/searxng` once, because fetching a few hundred megabytes unasked is not a chat window's decision.

Docker Desktop installs per-user and often leaves `docker` off `PATH`; Aura looks in its usual per-user location too, so "not on PATH" does not mean "not installed".

A failing engine never blocks Aura: startup happens on its own thread, the reason appears under **Settings → Search**, and Aura opens normally with search switched off.

## Packaging

```bash
python package.py
```

Writes `dist/aura-<version>.zip` containing the launcher, the `aura` package, and the documentation — and nothing else. The workspace, conversations, personal memory, permissions, undo history, and logs are excluded by name and by suffix, and the finished archive is re-opened and checked before it is handed over, so a package can be shared without leaking anything local. The running version is reported at `/health` and in the diagnostics report, so an update can be confirmed without opening a file.

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
- `aura/permissions.py` — revocable grants for folders and domains outside the safe workspace
- `aura/services.py` — registry for optional network services, each bound to the domains it declares
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
