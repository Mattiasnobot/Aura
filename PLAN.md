# Aura MVP Plan

1. Scaffold a dependency-light Python application with separate UI, agent, safety, memory, and provider layers.
2. Implement a strict `./aura-workspace` sandbox, recoverable trash, file tools, and auditable action logging.
3. Implement command execution constrained to the workspace, with an auto-approved allowlist and UI approval for everything else.
4. Build a lightweight interface with chat, animated face states, action log, approval prompts, and an optional voice-input hook. **Complete**
5. Add an offline `MockProvider`, lightweight JSON memory, and builder behavior for a hello-world Python app.
6. Add documentation, an example build flow, and automated tests for safety and core behavior.
7. Run the tests and smoke checks, fix failures, and document launch instructions.
8. Connect LM Studio as the default provider with automatic model discovery. **Complete**
9. Add a guarded multi-turn tool loop so the model can use every file, memory, and command capability. **Complete**
10. Add in-app LM Studio model selection and general project-building instructions. **Complete**
11. Persist provider and speech settings in protected local metadata. **Complete**
12. Stream LM Studio output into the desktop chat and preserve tool calls across rounds. **Complete**
13. Add local Windows speech output and remove online speech recognition from the default voice path. **Complete**
14. Add startup diagnostics, desktop launcher, workspace controls, and expanded regression tests. **Complete**
15. Add protected mutation snapshots, single-step undo, and precise line-level file tools. **Complete**
16. Increase project context/output capacity and multi-step endurance; benchmark available models for a responsive default. **Complete**
17. Add persistent task journaling, user cancellation, atomic multi-edit, workspace/change inspection, and recent-task UI. **Complete**
18. Add intent-based tool routing plus action and verification completion gates that detect and correct false model claims. **Complete**
19. Replace basic speech synthesis with paced native SAPI output, selectable voices, speech-friendly text cleanup, and overlap cancellation. **Complete**
20. Install and integrate Piper neural TTS with a local Lessac medium voice, warm model reuse, and automatic SAPI fallback. **Complete**
21. Add staged project delivery, safe built-in validation, automatic workspace context, and task-scoped rollback. **Complete**
22. Add deterministic artifact contracts and freshness-aware validation that remains invalid after any later mutation until revalidated. **Complete**
23. Harden the desktop shell with responsive split panes, scalable face geometry, a multiline composer, persistent layout, collapsible logs, shortcuts, DPI awareness, and a resilient event loop. **Complete**
24. Add Aura Mind: a bounded, force-directed knowledge graph sourced from local identity, preferences, conversation, tasks, tool usage, folders, and files, with search, selection, drag, pan, zoom, refresh, and persistent window geometry. **Complete**
25. Build the HTML/CSS/JavaScript interface with a narrow event bridge, streaming chat, responsive panels, animated face, custom approvals/settings/tasks, and a Canvas Aura Mind. **Complete**
26. Replace every Tkinter and native WebView path with a single browser interface served by a standard-library localhost service. **Complete**
27. Secure the browser API with loopback-only binding, a per-process cookie, same-origin checks, a strict method allowlist, request limits, restrictive headers, double-launch reuse, and an explicit Quit control. **Complete**
28. Audit Aura end to end: add path-aware and automatic final verification, real web-file validation, recoverable empty-folder creation, shell file-operation blocking, persistent action history, safe rich chat rendering, local-time logs, and narrow-window/accessibility polish. **Complete**
29. Add the interactive core: pointer-aware face reactions and speech animation, adaptive suggestion chips, rich task cards, cursor-safe multi-tab events, an integrated workspace explorer with sandboxed previews, recoverable drag-and-drop imports, and actionable Aura Mind nodes. **Complete**
30. Add the power layer: selectable Fast/Balanced/Deep reasoning endurance, Careful/Balanced/Powerful autonomy, a 34-tool catalog, batch I/O, code outlines, diffs, safe math, system facts, bounded HTTP inspection, guarded ZIP handling, broader recovery behavior, and exact-only per-task approval reuse. **Complete**
31. Add careful personal learning: structured non-sensitive memories, explicit-statement extraction, sensitive/transient filtering, relevant recall, user confirmation, pin/edit/forget controls, visible learning events, an opt-out setting, memory tools, and a dedicated Aura Mind branch. **Complete**
32. Rebuild the avatar as a lightweight holographic point-cloud portrait with eye tracking, idle breathing, state-specific signals, scan effects, reduced-motion support, and varied mouth articulation throughout local speech playback. **Complete**
33. Humanize the holographic portrait with a rounded anatomical skull and chin, balanced eye and mouth proportions, irises, cheek/nose/philtrum landmarks, surface-depth lighting, synchronized full-eye blinks, micro-saccades, head sway, and restrained speech articulation. **Complete**
34. Establish Aura's feminine visual identity with a softer oval facial profile, tapered jaw and neck, arched brows, almond eyes, finer nose and cheek geometry, naturally shaped lips, and a softly animated center-parted holographic hair silhouette. **Complete**
35. Rebuild the portrait to match the three-quarter digital-human reference: asymmetric projected anatomy, sculpted nose/eye/ear/jaw/neck contours, a dense connected triangular surface, depth-weighted luminous nodes, cyan edge glow, and mesh motion integrated with gaze, speech, breathing, and task states. **Complete**
36. Temporarily stabilize the avatar with the verified reference portrait while rejecting visibly corrupted generated-image exports. **Complete — superseded by phase 37**
37. Remove the portrait dependency and build Aura's model from scratch as native WebGL geometry: a continuous human head and jaw surface, shaped facial depth, open eye sockets, dimensional eyes and pupils, nose and brow contours, animated lips and jaw, ears, tapered neck, perspective, normals, depth-tested wire topology, speech/blink/gaze/breathing animation, state lighting, and automated asset checks. **Complete**
38. Adapt the user's stronger feminine face design into Aura as a compact local renderer: preserve the improved silhouette, dense depth topology, hair, detailed eyes, nose, lips, ears, and neck; remove the standalone HUD, external fonts, browser speech, and demo controls; connect gaze, speech, blink, nod, and error motion to Aura's existing interaction contract; and validate the live result at sidebar scale. **Complete**

## Post-audit roadmap

Audit basis: live browser inspection of chat, avatar, settings, memory, recent tasks, workspace explorer, rendered preview, voice input, Aura Mind, action history, and the supporting Python/JavaScript code. Aura is already a capable local-first MVP; the next goal is to make its results consistently truthful, visually verifiable, resumable, and comfortable to use for long-running work.

### Priority order

- **P0 — Trust and correctness:** Aura must accurately show and verify what it built before gaining more autonomy.
- **P1 — Companion experience:** face, voice, projects, and tasks should feel coherent and dependable.
- **P2 — Deeper intelligence:** stronger memory, multimodal reasoning, scoped OS access, and proactive behavior.
- **P3 — Release quality:** accessibility, performance, packaging, diagnostics, and onboarding.

39. **Reliability and truth pass — Complete (P0)**
   - Fix rendered HTML previews so relative CSS, JavaScript, images, and links resolve safely inside the workspace. Keep previews sandboxed and block outside-network access by default.
   - Separate friendly current-task activity from technical diagnostics; add task/session filters and collapse historical failures.
   - Harden malformed LM Studio tool-call repair, retry reporting, and deterministic fallback behavior. Make completion claims cite actual files, checks, and execution evidence.
   - Remove the obsolete avatar renderer still embedded in `app.js` and correct stale accessibility labels and status text.
   - Add real browser end-to-end and screenshot checks for startup, chat, avatar, approvals, modals, task cards, voice fallback, and styled workspace previews.
   - **Gate passed:** AuraCraft rendered with its real stylesheet in Aura's protected browser frame; scripts stayed disabled; current-session Activity and Diagnostics were verified; malformed streamed and atomic tool calls have bounded recovery; completion evidence is deterministic; the browser console was clean; all 82 automated tests and startup/LM Studio diagnostics passed.

40. **Embodiment v2 — Complete (P1)**
   - Refine the face with softer eye contact, independent iris gaze, more natural blink timing, cleaner eye/nose/mouth topology, restrained head movement, and state-specific expression and lighting.
   - Drive mouth shapes from the generated audio envelope or phoneme timing instead of a generic speaking loop.
   - Add avatar motion/intensity controls, reduced-motion behavior, adaptive frame rate, off-screen pause, and a lower-detail mode for slower computers.
   - **Gate passed:** all six application states have distinct live color/expression behavior; Piper speech drives the mouth from the generated WAV envelope and SAPI has phoneme timing; independent gaze, variable blinks, restrained movement, reduced-motion, adaptive frame rate, off-screen pause, and persisted motion/intensity/detail controls were verified in the live browser with a clean console; all 85 automated tests pass.

41. **Voice v2 — Complete (P1)**
   - Add hold-to-talk, cancel, retry, microphone/device selection, input calibration, a live level meter, partial transcript, and clearer timeout feedback.
   - Offer an optional stronger local recognizer such as Whisper.cpp or Vosk while preserving the dependency-light fallback.
   - Add voice preview in Settings, interruption/barge-in support, and reliable speech cancellation.
   - **Gate passed:** click-to-talk and hold/release both use a cancelable local streaming session; the UI shows calibration, live PCM level, partial words, local processing, recognized text, retry, and cancel states; microphone selection/calibration and voice preview are available in Settings; beginning to talk interrupts Aura’s output; PocketSphinx is the working fallback and Whisper.cpp is an optional stronger engine; all 90 tests and the live browser settings/listening/error visual checks pass with a clean console.

42. **Project workspace v2 — Mostly complete (P1)**
   - New file/folder, rename, move, copy, delete-to-trash, restore, diff, and history controls exist directly in the workspace UI. **Verified live in browser 2026-08-14.**
   - A managed local preview server with start/stop/status/log controls exists (`aura/preview_server.py`, the "Live preview server" modal). **Verified live in browser 2026-08-14.**
   - Every operation is scoped, recoverable, and attributed via task-scoped rollback plus the trash/history controls.
   - **Remaining:** `check_workspace_assets` only crawls HTML statically for broken local references (`aura/validation.py`); there is no automated check for responsive layouts or captured browser console errors. This gap is carried forward into phase 43.
   - **Gate:** partially met — Aura can build, launch, inspect, revise, and restore a small site; visual/responsive/console validation is not yet automated.

43. **Durable goals and task engine — Complete (P1)**
   - **Step 1 complete 2026-08-14:** `TaskJournal.recent()` (`aura/tasks.py`) now takes
     `only_actionable` (drops tool-free tasks — chit-chat no longer shows up as a task)
     and `active_task_id` (a task left "running" by a crash or restart, other than the
     one this process is actually working on, is reclassified as `interrupted` with an
     explanatory summary instead of appearing stuck forever). `web_bridge.recent_tasks()`
     — the "Recent tasks" panel — uses both; the internal per-reply task lookup does not,
     so inline chat-bubble task cards keep their existing correlation behavior. Verified
     live: chit-chat ("cool you", "Hello", "Who are you?") no longer appears in Recent
     tasks, and a real build (create a file) still shows up there with full detail and
     working Undo. Covered by new `TaskJournalTests` and a `WebBridgeTests` case.
   - **Step 2 complete 2026-08-14:** `TaskJournal.recent()` now sets `task["project"]`,
     inferred from the top-level workspace folder of the first mutated file/folder path
     recorded in that task's `tool_details` (handles both `/` and `\` separators; a task
     that only touched the workspace root gets `project: null`). The "Recent tasks"
     modal (`openTasks()` in `aura/web/app.js`) groups cards under a project header,
     reusing the same `.file-folder-label` style the workspace explorer already uses for
     its own folder groups, so a task built inside e.g. `aura_craft/` is grouped under
     an "aura_craft" heading instead of a flat list. Covered by a new `TaskJournalTests`
     case (posix path, backslash path, root-level file); verified live after a restart —
     no console errors, existing task renders under a "Workspace root" header correctly.
   - **Step 3 complete 2026-08-14:** Recent-tasks cards now show duration (from
     `started`/`finished`), a "files changed" count with per-file chips, and a
     "✓ validated" mark — all computed client-side in `buildTaskCard()`/`taskEvidence()`
     (`aura/web/app.js`) purely from data the journal already records
     (`tool_details[].arguments.path/destination` against the existing `MUTATION_TOOLS`
     set, and `result.ok`/`result.valid` against a new `VERIFICATION_TOOLS` set covering
     `validate_project`, `verify_final_state`, `compare_files`, `check_workspace_assets`).
     No backend or journal-format change was needed. Verified live: a completed task
     shows "Took 3m 23s • 1 file changed • ✓ validated" plus a file chip, no console
     errors.
   - **Still planned:** "current step" while a task is actively running (would need
     live per-step event streaming, not just post-hoc journal review), retry tracking,
     and true cross-restart resumption that re-enters an interrupted multi-step build
     with its prior context rather than just reporting that it was interrupted. None of
     these have an existing data model to build on yet; resumption in particular
     (safely replaying/continuing an LM Studio tool loop without duplicate side effects
     or stale approvals) is significant enough to warrant its own dedicated
     implementation pass rather than being folded into this one.
   - **Step 4 complete 2026-08-15:** an interrupted task can be resumed. Interrupted and
     errored cards gain a **Resume** button; `resume_task` builds a brief and submits it
     as an ordinary new task.
     **Resume means re-planning from verified state, not replaying the old conversation.**
     Re-sending the previous turns would repeat side effects — a second `create_file`, a
     doubled `append_file` — and would carry stale command approvals across a restart.
     Instead `resume_brief()` reports the original request, every mutating step that
     succeeded, and **what each of those paths looks like on disk right now**, plus the
     requested files that still do not exist. Because it is a new task, anything needing
     permission is asked for again.
     A step the journal claims succeeded is reported as `MISSING now` when the file is
     gone — the brief describes reality, not the log. Read-only steps and failed steps are
     never presented as completed work. Seven tests cover it.
   - **Live proof of the gate:** Aura was killed mid-build after writing one of three
     requested files. On restart the task showed as INTERRUPTED, **Resume** ran
     `list_files` → `write_files` → `validate_project`, and finished with
     "index.html (already existed), style.css (newly created), about.html (newly created)"
     — resumed from the correct point, nothing duplicated, and the history explains what
     changed and why.
   - **A real bug the live run caught:** the model used `write_files`, whose arguments
     hold a list rather than a single `path`, so the first brief claimed "no file was
     successfully changed" while `index.html` sat on disk. Batch arguments are now read
     properly, with a regression test.
   - **Gate:** an interrupted multi-step build resumes from the correct checkpoint and its history explains what changed and why. *(Met.)*

44. **Memory v2 — Complete (P2)**
   - **Step 1 complete 2026-08-14:** `MemoryStore.relevant_memories()` (`aura/memory.py`)
     now stamps and persists `last_used` on every memory it actually selects into a
     request's context (distinct from `updated`, which only changes when the memory's
     content is edited), under the existing lock so it's safe alongside concurrent
     writes. New memories start with `last_used: None`. The "What Aura knows" card
     (`aura/web/app.js`) shows it next to Updated/Source. No new bridge plumbing was
     needed — `get_personal_memory` already returns full memory dicts. Verified live: a
     real chat turn stamped `last_used` on the two general-category memories that
     matched and left the other two untouched, both in `memory.json` on disk and in the
     "What Aura knows" panel; covered by a new `AgentTests` case.
   - **Step 2 complete 2026-08-14:** `learn_fact()`/`learn_from_message()`
     (`aura/memory.py`) accept an optional `project`, stored on the memory item
     (existing memories without one just read as `project: None` — no migration
     needed). `AuraAgent._context()`/`handle()` (`aura/agent.py`) derive it from the
     message text via the already-existing `_extract_artifact_contract()` (the same
     folder-name heuristic phase 43 already relies on elsewhere) and pass it through
     automatically. `relevant_memories()` gives a same-project memory a score boost so
     it's preferred when relevant. The "What Aura knows" card shows `Project: x` when
     set. Verified live: "I prefer TypeScript in the aura_craft project" was learned
     and tagged `project: aura_craft` in `memory.json` and in the UI, with no console
     errors; covered by two new `AgentTests` cases (direct store-level boost, and
     agent-level tagging from a real message).
   - **Step 3 complete 2026-08-15:** "What Aura knows" now separates auto-learned
     memories awaiting review from confirmed ones. `renderPersonalMemories()`
     (`aura/web/app.js`) was split so each card is built by `buildMemoryCard()`, and the
     list groups into "Needs review (n)" / "Confirmed" headers — reusing the same
     `.file-folder-label` style already used by the workspace explorer and phase 43's
     task grouping — but only when both kinds are present, so a uniform list stays
     flat. Unconfirmed cards get a one-click **Confirm** action that reuses the
     existing `update_personal_memory` bridge method with an empty payload;
     `update_profile_memory()` already sets `confirmed: True` while preserving the
     existing value and category, so no backend change was needed. That contract is
     now locked by a new `AgentTests` case. Verified live: an auto-learned memory
     appeared under "Needs review (1)" at 92% confidence, Confirm moved it into
     "Confirmed" with its text unchanged and the headers correctly disappearing once
     nothing needed review, with no console errors.
   - **Step 4 complete 2026-08-15:** "why Aura recalled this" is now shown per reply.
     `relevant_memories()` keeps the rationale it already computes while scoring
     (pinned / same project / keyword match / general preference) and returns it as
     `recall_reason`. Because the reason is per-query rather than a property of the
     memory, it is attached only to the returned copies and never written back by the
     `last_used` save — locked by a test that asserts no stored memory ever gains the
     field. `AuraAgent._context()` records the selection as `last_recalled`, and
     `web_bridge._work()` sends a value/category/reason triple on the existing `reply`
     event (carried on the reply itself rather than as a separate event, so there is no
     ordering dependency). `attachRecallNote()` (`aura/web/app.js`) renders a collapsed
     "Used n memories" disclosure under the reply. The model context is unaffected:
     `LMStudioProvider.start_messages()` already allow-lists only `category` and
     `value`, which a second new test now pins down so neither `recall_reason` nor
     internal ids can ever leak into a prompt. Verified live: a preference question
     produced "Used 3 memories" listing two "Matches your wording" and one "General
     preference Aura always considers" — matching exactly the facts the reply used —
     with no console errors.
   - **Step 5 complete 2026-08-15:** `MemoryStore.conflicting_pairs()`
     (`aura/memory.py`) reports memories in the same category that may contradict
     (`kind: "contradicts"`) or restate (`kind: "overlaps"`) each other, and
     `get_personal_memory` returns them so each affected card in "What Aura knows"
     shows "May contradict:" / "Overlaps with:" naming the other memory. The check is
     deliberately conservative — it uses only significant-word overlap (≥50% of the
     smaller fact, at least two shared words) plus the explicit `Dislikes ` negation
     marker that `learn_from_message()` already produces, and never guesses at meaning.
     Like `recall_reason`, it is computed on read rather than stored, so forgetting one
     side clears the other's flag with no stale state — verified both by test and live
     in the browser. Three tests cover it, including the negative cases that matter
     most: unrelated facts in the same category and identical wording in different
     categories are never flagged.
   - **Step 6 complete 2026-08-15:** the local workspace index is in, as
     `aura/search_index.py` — a dependency-free BM25 ranker, chosen deliberately over
     an embedding model so the project stays dependency-light (Mattias's call). Its
     tokenizer splits camelCase and snake_case alike so `avatarFace`, `avatar_face`
     and a plain "avatar face" query all meet, and the file path is indexed alongside
     the content so filename matches still rank. Documents are cached per file and
     re-read only when size or `st_mtime_ns` moves — nanosecond precision
     specifically so a same-length edit inside one coarse timestamp tick cannot go
     unnoticed, which a test pins down using before/after content of identical length.
     Binary/oversized files are skipped. Exposed to the model as a new
     `find_relevant_files` tool (39 total now, routed on read/find/search-style
     requests) and described so the model prefers it when it does not know the exact
     wording, and `search_files`/`search_text` when it needs an exact string.
     **Honest limitation:** this matches words, not meaning — it will not find
     synonyms the way embeddings would, and the tool description says so. Five tests
     cover it, including one asserting a multi-word query that `search_files` returns
     nothing for. Verified live: the model chose the tool unprompted, ranked the right
     files, and explained each match; no console errors.
   - **Step 7 complete 2026-08-15:** memories now keep an edit history.
     `update_profile_memory()` records the previous value/category (capped at
     `MAX_HISTORY = 5`) but only when something actually changed, so the one-click
     Confirm from step 3 and a plain pin/unpin leave no misleading history entry —
     covered by its own test. `revert_profile_memory()` restores the most recent
     earlier wording and *consumes* that entry, so repeated reverts walk further back
     rather than toggling between two versions, and it recomputes the dedup `key` so
     re-learning the restored fact still matches the existing memory instead of
     creating a duplicate (also its own test — this was the subtle failure mode worth
     pinning down). Exposed via a new `revert_personal_memory` bridge method and
     `/api/call` entry; the card shows `Previously “…”` with a **Revert** action, and
     only when history exists. Verified live end to end: edit → history line and Revert
     appear → revert restores the original wording and both disappear, no console
     errors.
   - **Step 8 complete 2026-08-15:** export and one-click conflict resolution finish
     the phase. `export_personal_memory()` writes every stored memory as readable JSON
     into the workspace (`aura-memory-export-<timestamp>.json`) through the normal
     sandbox, so the export is snapshotted and recoverable like any other file; the
     **Export** button then opens the workspace explorer on the new file. Writing
     server-side rather than triggering a browser download was chosen so the result is
     inspectable and actually verifiable. On a conflicted card a **Keep this** action
     forgets the other memory behind a confirmation naming both sides. Two bridge
     tests cover the export contents and a revert round trip including the clean
     failure when nothing is left to restore.
     **Verification note:** the accept path of *Keep this* could not be exercised in
     the automated browser, which blocks native dialogs — what was verified is that
     the dialog names the right pair and that declining deletes nothing. The delete
     itself reuses `forget_personal_memory`, already covered by existing tests.
   - Phase 44 goal met: preferences and project decisions are recalled with visible
     provenance (recall reasons, project tags, last-used), reviewable before they are
     trusted, contradiction-aware, revertible, exportable, and searchable across the
     workspace — all without adding a dependency.
   - **Gate:** relevant preferences and project decisions are recalled with visible provenance while unrelated or sensitive details are not guessed. *(Met.)*
   - Sensitive-info opt-in, editing, and forgetting already existed before this phase
     (`_is_sensitive`, `update_profile_memory`, `forget_profile_memory`) — only export
     is still missing from that bullet.
   - **Gate:** relevant preferences and project decisions are recalled with visible provenance while unrelated or sensitive details are not guessed.

45. **Multimodal and visual reasoning — Complete (P2)**
   - **Step 1 complete 2026-08-15:** Aura can genuinely see workspace images. A new
     `look_at_image` tool base64-encodes a workspace image (PNG/JPEG/GIF/WebP/BMP,
     4 MB cap) and the tool loop then appends a real OpenAI-format multimodal `user`
     turn carrying `image_url`, because a tool result is plain text and cannot carry an
     image. The payload is returned under the `content` key specifically because
     `TaskJournal.record_tool()` already strips that key — so a base64 blob never
     enters the durable history; a test asserts this, and the live journal was checked
     afterwards and contains no base64.
   - **Capability detection: the name guess was replaced by an actual probe
     (2026-08-15).** LM Studio's `/v1/models` returns only an id — no capability
     metadata whatsoever — so this started as a name heuristic. That heuristic was then
     proven wrong in practice: `qwen/qwen3.5-9b` reads images correctly despite having
     no vision marker in its name. `auto` mode now sends a 1x1 PNG to the server once
     per model and caches the answer in `vision_probe`, falling back to the name
     heuristic only when the probe cannot reach the server. **Honest limit:** the probe
     shows whether the server *accepts* image content, not whether the model
     understands pictures well. `vision_mode` (`auto`/`on`/`off`) still overrides
     everything, and when vision is off the tool is stripped from the offered set
     rather than advertised and failing.
     **Test-hygiene bug this introduced and fixed:** with a live probe in
     `vision_enabled()`, the vision tests began calling the real LM Studio server —
     making the suite take 131 s instead of 19 s and causing the user's machine to
     start loading a model. `VisionTests` now seeds the probe cache, and probe
     behaviour is covered by dedicated tests with a mocked prober. A test suite must
     never reach out to a live service.
   - **Verified live against `qwen3-vl-8b-instruct`,** including a control image whose
     filename revealed nothing about its contents: Aura described `asset-01.png` as
     split horizontally into two equal halves, yellow above black — exactly the
     generated image. A first test with a three-bar red/green/blue image was also
     described correctly in order.
   - **Observed limitation:** the local model does not always choose the tool. In one
     run it answered a "look at X.png" question by calling `list_files` instead, even
     though `look_at_image` was correctly offered (confirmed by inspecting the routed
     tool set). The tool description was strengthened to say that pixels are only
     visible through this tool, but reliable selection still depends on the model.
   - **Step 2 complete 2026-08-15:** Aura can now see her *own* rendered output. A new
     dependency-free `aura/screenshot.py` drives an installed Chromium-based browser
     (Chrome/Edge/Chromium) in headless mode — the same recipe the phase-39 smoke test
     already used, promoted into a real module — and a `capture_page` tool renders a
     workspace HTML page and imports the PNG back into the workspace through the normal
     sandbox, so it is snapshotted and recoverable.
     **Safety:** the page is served from a short-lived local `PreviewServer`, so a
     capture can only ever target this machine's workspace — the model cannot aim it at
     an outside address — and launching the browser goes through the ordinary approval
     dialog, which shows the exact command. A test asserts that denying approval both
     blocks the launch and writes no file. If no browser is installed the tool fails
     with a clear message and the real-capture test skips.
   - **Verified live:** `capture_page` → screenshot saved → `look_at_image` → Aura
     described `aura_craft/index.html` as a modern landing page, having earlier
     described its bright blue navigation bar from the same pipeline. **This meets the
     "inspect its own rendered result" half of the gate.** One honest caveat: the
     model's description length varies between runs — one run was detailed and
     accurate, a repeat was terse — which is model output variance, not a pipeline
     fault; both runs ran the full tool chain.
   - **Bug found and fixed while verifying (pre-existing, phase 41):** the artifact
     contract stripped the folder from a path the user typed, so
     "screenshot aura_craft/index.html" became a requirement for `index.html` at the
     workspace root, which does not exist — and a fully successful task was reported as
     *"required artifacts are still missing"*. `_extract_artifact_contract()` now keeps
     the folder for both `/` and `\` paths, with a regression test. The same request
     that previously failed now completes and confirms
     `aura_craft/index.html` as present.
   - **Step 3 complete 2026-08-15:** visual differences are now *measured*, not
     guessed. `aura/image_diff.py` contains a standard-library PNG decoder (8-bit
     grey/RGB/RGBA, non-interlaced, all five row filters including Paeth) and a
     `compare_images` tool reporting changed-pixel count, percentage, and the exact
     bounding box of the change — deliberately deterministic, so the answer is evidence
     rather than a model's impression of two pictures. Differing dimensions are
     reported as their own kind of layout difference. Unsupported PNG variants raise a
     clear error instead of guessing.
     **Performance:** rows are first compared as raw bytes and only differing rows are
     walked pixel by pixel, which keeps a real 1200x800 Chrome screenshot at ~0.3 s to
     decode and ~0.6 s to compare.
   - **Verified live and arithmetically:** two 400x300 images with a button shifted by
     (30, 20) were compared through the UI. The tool reported 7 000 changed pixels,
     5.833%, in a 130x90 region at (50, 40) — which matches hand calculation exactly
     (two 100x70 boxes overlapping in 70x50 gives 7 000 differing pixels), and Aura
     relayed those measured numbers faithfully. An integration test additionally
     captures a page, edits its CSS, re-captures, and asserts the diff is localised to
     the recoloured element.
   - **Gate:** Aura can use a supplied visual reference, inspect its own rendered result, and explain evidence-based differences before finishing. *(Met — reference comparison and self-capture both verified live, with the explanation backed by pixel measurements.)*
   - **Step 4 complete 2026-08-15:** `check_accessibility` (`aura/validation.py`) reports
     markup-level problems using the standard-library `HTMLParser` already used for
     asset checking: images without `alt`, form controls with no label / `aria-label` /
     wrapping `<label>`, empty links and buttons, a missing `lang` or `<title>`, and
     skipped heading levels. Alternative labelling methods (wrapping label, aria, hidden
     and submit inputs, a link whose content is an `alt`-bearing image) are accepted, so
     the report stays trustworthy instead of noisy.
     **Colour contrast is deliberately not evaluated** — deciding it needs the resolved
     CSS cascade, and a confident wrong answer would be worse than none. The result says
     so explicitly via `contrast_checked: false`, and a test pins that down.
     Verified against the real workspace: 3 true findings, 0 false positives — the
     `aura_craft` contact form uses `placeholder` as a substitute for labels.
   - **Bug found and fixed while verifying:** pointing the check at a single page
     (`aura_craft/index.html`) scanned *nothing*, because `list_files()` on a file
     yields no entries — and the empty issue list was then reported to the user as
     "No structural accessibility issues found". A false all-clear is worse than an
     error, so a shared `_html_files()` helper now accepts a file or a folder, and a
     zero-page run returns `ok: false` with an explicit "nothing was checked" message.
     The same helper removes the identical latent trap from `check_broken_assets`.
     Two regression tests cover it.
   - **Model reliability caveat (same pattern as step 1):** on two attempts the local
     model answered an accessibility question *without calling the tool*, once
     asserting "No critical accessibility issues found" that it had never measured —
     Aura's own missing-action guard caught that and retried. The tool was confirmed to
     be in the offered set both times, so this is model tool-selection behaviour, not
     routing. With an explicit instruction it ran correctly and reported all three real
     issues with their line numbers.
   - Responsive checks are possible by composition — `capture_page` takes explicit
     width and height, so two captures at different widths can be compared — but there
     is no dedicated single-call tool for it.
   - **Gate:** Aura can use a supplied visual reference, inspect its own rendered result, and explain evidence-based differences before finishing.

46. **Scoped autonomy and OS bridge — Steps 1 and 2 complete (P2)**
   - **Step 1 complete 2026-08-15:** the permission foundation, built before any
     capability that needs it. `aura/permissions.py` holds a durable, revocable grant
     registry (`once` / `session` / `project` / `persistent`) stored in
     `.aura/permissions.json`, plus a read-only `ExternalReader`. Nothing outside
     `aura-workspace` is reachable without a grant.
   - **The model cannot widen its own access — this is the design, not a setting.**
     There is deliberately no `grant_folder_access` *tool*; granting exists only as a
     bridge method driven by the new **Permissions** panel, so the user chooses the
     folder. The model only gets `list_granted_folders`, `list_external_folder`, and
     `read_external_file`. A test asserts the granting tool is absent from the agent's
     catalogue.
   - **Containment properties, each with an adversarial test:** grants store an
     absolute, resolved root and cover only that folder and its descendants (siblings
     and the parent stay unreachable); `..` cannot escape; a symlink planted inside a
     granted folder cannot widen it, because the path is re-resolved *after* the grant
     check; `once` grants are spent on use; `session` grants are bound to the process
     id and die on restart; filesystem roots and system/credential locations
     (`SystemRoot`, `ProgramFiles`, `~/.ssh`, `~/.aws`, credential stores, `/etc`, …)
     are refused outright rather than left to a confirmation click. Revoking takes
     effect immediately and **Revoke all** is the emergency stop.
   - **Verified live:** granting a folder, `list_granted_folders` and
     `read_external_file` returning its contents, a restart correctly dropping a
     session grant, and revoke-all closing access again. Every grant and revocation is
     written to the action log.
   - **Audit nuance worth knowing:** the tool record itself does not store what was
     read (the payload uses the `content` key that `record_tool` strips), so the
     durable tool history shows *which* external path was accessed, not its bytes. If
     Aura then quotes that content in her reply, the reply is stored like any other
     answer — so external content can still appear in history by that route.
   - **Second pre-existing bug found and fixed:** the artifact contract also treated
     files named in *read-only* requests as required deliverables, so "read notes.txt
     from the granted folder" failed with "required artifacts are still missing" even
     though the read succeeded — and an external file can never be inside the
     workspace. The contract is now skipped entirely when the request needs no
     mutation, reusing the existing `_requires_mutation()` judgement; the folder scope
     is kept, because validation reporting still uses it. Narrowing that fix was
     necessary: clearing the folder too broke an existing validation-scope test.
   - **Step 2 complete 2026-08-15:** write access to granted folders, kept recoverable.
     `ExternalWriter` snapshots the previous version into Aura's history before every
     write and journals each change to `.aura/external-changes.jsonl`, so
     `undo_external_change` can restore it — a write outside the workspace loses the
     sandbox, so it must not lose undo as well. Nothing here deletes anything.
     **`write_folder` is a separate grant:** a read grant never implies writing, and
     choosing "Read and write" in the Permissions panel creates two visible, separately
     revocable entries. Four more adversarial tests cover it (read grant cannot write,
     writes cannot escape the folder, revocation stops writes immediately, undo walks
     back correctly). Verified live: granting, overwriting `report.txt` through the
     model, and rolling it back.
   - **Three bugs surfaced by real use, all fixed and pinned by tests:**
     1. **Tools named in a request were not always offered.** "the granted *write*
        folder" missed the `granted folder` keyword, so `write_external_file` was never
        in the toolset and Aura looked like it was refusing. Routing now always offers
        any tool the user names outright, which helps every tool, not just this one.
     2. **The workspace artifact contract can never be satisfied by external work,**
        so it nagged forever. It is now skipped once a task has genuinely operated
        outside the workspace, and the workspace validation nudge is skipped too when
        no workspace file was touched — validating the workspace proves nothing about
        a file written into a granted folder.
     3. **Retries visibly repeated the whole answer.** Every retry re-streamed a full
        reply and the browser appended it, so the user saw the same text two or three
        times. The agent now emits a `retry` state, the bridge turns it into a
        `stream_reset`, and the interface clears the abandoned reply first. **This was
        not specific to external folders — it affected ordinary workspace work and
        plain questions too, and it was reported from real use, not found by a test.**
        The first attempt at this fix was wrong in an instructive way: signalling at
        each individual retry site missed a fourth path — the post-mutation
        verification retry, which emitted only blank lines and so repeated the answer
        with nothing to explain why. The signal now fires once at the top of every
        round after the first, so no current or future retry path can omit it, and
        that silent branch also gained a visible line. The final answer is always
        re-rendered from the `reply` event, so clearing partial text can never lose it.
   - **Two provider-compatibility bugs found when switching model (2026-08-15).**
     Moving from `qwen3-vl-8b-instruct` to `qwen/qwen3.5-9b` failed instantly, and the
     cause was Aura's, not the model's:
     1. **Multiple system messages.** Aura adds system guidance in several places (base
        prompt, host notes, recalled memories, mid-run corrections), and strict chat
        templates reject a system message that is not first and alone — LM Studio
        returned HTTP 400 from the template's own `raise_exception`.
        `LMStudioProvider.merge_system_messages()` now folds them all into one leading
        message before every request, which keeps the same instructions and works with
        permissive templates too.
     2. **Reasoning models.** `qwen3.5-9b` puts its answer in `reasoning_content` and
        leaves `content` empty, so a turn that had genuinely done the work was reported
        as *"the model returned neither text nor a tool request"*. Both the streaming
        and non-streaming parsers now fall back to `reasoning_content` when there is no
        content and no tool call. The thinking is never streamed to the chat — it is a
        last-resort answer, not narration.
     Verified end to end on the new model: file created, validation passed, task
     completed, one clean answer. Two tests cover the merge and the fallback.
   - **Settings gained the missing `vision_mode` control.** The override existed in
     config and was documented, but had no interface control, so the documented way to
     force images on or off was not actually reachable. Now a "Images" selector
     (automatic / always / never) sits next to Autonomy, with a test covering save,
     read-back, and the fallback for an unknown value.
   - **Clipboard support was built and then deliberately removed (2026-08-15).** It
     worked, was permission-gated, and passed its tests, but Mattias judged it
     unnecessary and it was reverted at his request. Two things are worth keeping from
     it: a real bug it exposed, and a reason not to add capabilities speculatively.
     The bug: the first `ctypes` clipboard reader **segfaulted the whole process**,
     because an undeclared `restype` truncated the 64-bit clipboard HANDLE to 32 bits.
     No unit test would have caught that — only running it did. The reason: today's
     sessions showed the local model choosing tools *worse* as the catalogue grew, so
     unused tools are not free. The catalogue is back to 48.
     The permission registry keeps its small generalisation — `PATH_CAPABILITIES`
     separates folder-scoped grants from capability-only ones — as a documented
     extension point, though nothing uses the non-path branch today.
   - **Still planned for this phase:** notifications, app launch, screen capture of
     other windows, and managed process controls. Each must go through this same grant
     registry — and each should be added only when there is a real use for it, not to
     complete the list.
   - Add revocable, user-selected folder mounts beyond `aura-workspace`, plus opt-in clipboard, notifications, app launch, screen capture, and managed process controls.
   - Introduce a permissions center with one-time, session, project, and persistent grants plus a readable audit trail and emergency stop.
   - Preserve the safe workspace as the default and require narrow approval for broader access.
   - **Gate:** every external capability is off by default, visibly scoped, revocable, logged, and incapable of silently broadening its own access.

47. **Network and tool extensibility — Mostly complete (P2)**
   - **Offline by default, per-domain grants.** `reach_domain` joins the folder capabilities as a third, host-scoped kind of grant. Aura reaches nothing outside `localhost` until the user names a domain under **Permissions**; a grant covers that host and its subdomains, so a redirect to `www.` does not become a second question. There is deliberately **no tool** for granting a domain, exactly as with folders: the model can use a grant, never ask for one, so nothing it reads on the network can talk Aura into reaching further.
   - **Two refusals that no dialog can override.** A domain that is, or resolves to, a loopback/private/link-local/reserved address is refused at grant time — a public name pointing at `192.168.x.x` or `169.254.169.254` would otherwise let an innocuous-looking approval reach the user's own network. The address is re-resolved on **every request and every redirect hop**, because DNS can change between the grant and the use.
   - **Source reporting.** Every URL actually fetched in a turn is recorded and listed under **Read from the network** in the reply, so an answer that left the machine says exactly where it went.
   - **Online/offline indicator** in the sidebar: "Offline • local only" until a grant exists, then "Online • N domains" with the list on hover. The Permissions panel names which domains the built-in services still need.
   - **Extension interface** (`aura/services.py`): a service declares its tool, its domains, and a handler that is *given* a fetch already bound to `reach_domain`. Adding one is a new module plus a `register()` call — the tool list and the tool loop are untouched. The first service is keyless weather via Open-Meteo. **Verified live 2026-08-16:** with both domains granted, "What is the weather in Tartu right now?" called `get_weather` unprompted and answered from real data with both addresses cited.
   - **Not done:** general web search, which needs a third-party API key. Per the user's decision, no key handling was added.
   - **Gate:** met — an approved lookup works, cites what it read, and Aura is fully functional with no grants at all.

48. **Proactive companion — Complete (P2)**

   The original entry put three things on one line that carry very different risk, and building them together would have smuggled the third in behind the first two:

   1. **reminders** — Aura says something at an agreed time; touches the interface only;
   2. **recurring checks** — she reads, validates, and reports; changes nothing;
   3. **acting while you are away** — she changes files with nobody watching.

   **Decided with the user (2026-08-16): a scheduled run may read and prepare, and must ask before changing anything.** This is not caution for its own sake. The whole project rests on a grant being given deliberately, never widened by the model, and phase 43 settled that approvals do not carry across a restart or into a resumed task. Unattended mutation breaks exactly that: the approval would have to be given in advance, for a situation nobody has seen yet. So you come back not to a changed workspace but to *"I found three broken links and the fix is ready — apply it?"* — still proactive, honest about who decides.

   A distinction worth writing down, because it could easily be misread: the user has often told Claude to work autonomously. That was about Claude working while the user was present and able to interrupt. Aura acting on the machine while nobody is there is a different thing, and is not covered by it.

   - **48.1 — The safety envelope, first. Done 2026-08-16.** `aura/autonomy.py` holds an `AutonomyGuard` that only ever *decides*: quiet hours (22:00–08:00 by default), a per-run time budget, a daily cap, and a pause. It runs nothing itself, which keeps the one component whose job is to say no small enough to test exhaustively — 10 tests, including the window that crosses midnight, nonsense hours falling back rather than opening the night, and a restart not handing back a fresh allowance (the count is read from the durable log, not held in memory).
     - **A real bug caught by its own test:** `int(value or 12)` is the obvious way to read the daily cap and it is wrong — a deliberate cap of **0**, meaning *no background work at all*, would have been silently turned into the default of 12. In a component whose entire purpose is refusing, that is the worst possible place for it. Now read explicitly, with the reason written next to the code.
     - **Emergency stop cancels, it does not only pause.** Pausing alone would let an in-flight run finish, which is not what anybody means by a stop control. It pauses, cancels the running task, denies pending approvals, and silences speech.
     - **Every refusal carries a reason**, because a background run that quietly does not happen is worse than one that says why. The sidebar control shows the state and the reason on hover.
     - **No tool exposes any of this to the model** — same rule as folders and domains: it can be used, never widened from the inside. Asserted by a test.
     - Verified live: the pause toggle round-trips against the server, and the stop control leaves `paused: true` with the running task cancelled.
   - **48.2 — The schedule store and the loop. Done 2026-08-16.** A `scheduled_tasks` table in `aura.db` and `aura/scheduler.py`: one thread that wakes, asks the guard, claims what is due, and dispatches to a handler registry. Deliberately dull — whether anything may run is 48.1's answer and what a kind of work *does* belongs to its handler, so this file only decides *when*. Nothing is registered yet, which is asserted by a test: the kinds arrive in 48.3 and 48.4.
     - Four rules, each with a test: **never while the user is waiting**; **a refusal postpones rather than consumes** (quiet hours must not silently skip a run); **a crash in one task never stops the loop** and is recorded as that task's outcome; **an unknown kind is disabled rather than retried forever**, so a row written by a newer build cannot burn the daily allowance on nothing.
     - **A gap found while wiring it:** emergency stop halts the thread, and resuming only cleared the guard — the guard would have said yes while nothing was listening. Resuming now restarts the loop, with a test.
     - **An honest note on the migration.** This was billed as the first real use of the schema versioning from 50.5, and it is less than that: `CREATE TABLE IF NOT EXISTS` runs on every open, so a *new table* reaches an old database without any migration at all. Migration 2 earns its place by making the version number mean something rather than by moving data. The mechanism becomes load-bearing the first time a **column** changes, which no `IF NOT EXISTS` can do. The comment in `store.py` says so, so nobody later mistakes it for more than it is.
     - Verified on the live database: version 0 → 2, `scheduled_tasks` present, 22 messages and 45 changes untouched.
   - **48.3 — Reminders. Done 2026-08-16.** The first real handler, and the proof that 48.1 and 48.2 work together. A `set_reminder` tool the model may call, a handler that says the reminder **as Aura, into the durable conversation** rather than only on screen — so one that arrives while the window is shut is still there when it opens — plus `list_reminders`, `cancel_reminder`, and a cap of 20 waiting.
     - **The tool hard-codes the kind.** It is the one piece of background work the model can create, and it can only ever create a reminder: nothing it writes can become work that acts. A test asserts the only kind in the table is `reminder` and that no scheduling or autonomy control is offered as a tool.
     - **A design flaw the tests exposed:** `stop()` left its flag set, so a stopped scheduler silently ignored a manual `tick()` while still looking alive. The flag is now cleared once the thread is gone, leaving the object at rest rather than permanently refusing.
     - **One existing test changed, on purpose and in the open.** `test_the_scheduler_runs_nothing_it_has_not_been_taught` asserted an empty registry, which was 48.2's truth; 48.3 added the first handler, so the assertion was rewritten to the new truth — the registry holds exactly `{"reminder"}` — rather than deleted.
     - Verified end to end with the real model: "Remind me in 1 minute to check the redesign" → `set_reminder` → the row appeared with the right due time → the scheduler delivered it a minute later as *"Reminder: Check the redesign"*, spent one allowance, and retired the one-off.
   - **48.4 — Recurring checks. Done 2026-08-16.** `aura/checks.py` holds a small registry of named, deterministic, read-only functions over state Aura already has: `validate_workspace`, `broken_links`, and `recent_failures`. A check is **backend code, not a model turn**, so it costs nothing, cannot hallucinate, and cannot decide to do something else.
     - **The vocabulary is fixed on purpose.** `set_check` takes a name from the registry, never free text. Free text would quietly turn "a check" into "an arbitrary agent turn in the background", which is precisely what 48.5 exists to put behind a proposal. A test schedules `"rm -rf everything"` and asserts it is refused and nothing is written.
     - **Silence is the normal outcome.** A check returns nothing when there is nothing worth saying, and the run is recorded as `nothing to report`. A daily check that says "all fine" forever trains its reader to skip it, and then the once it matters it gets skipped too.
     - **A design fix the tests forced:** validation reports "project contains no files" as a failure, which is a fair answer to an explicit request and pure noise as a background check. An empty workspace now stays quiet.
     - **`recent_failures` is the first piece of improvement 1** — the pattern the diagnostics export made visible by hand, noticed continuously. It speaks only when the same failure has happened three times, because one failure is an event and three of the same are a pattern.
     - Verified live on the real workspace: a planted page with two dead references produced *"While you were away — 2 broken local references: check-demo.html points at does-not-exist.css (and 1 more)"*, and the check rescheduled itself rather than retiring. Demo file and check removed afterwards.
   - **48.5 — Proposals instead of unattended changes. Done 2026-08-16.** A check now returns a `Finding`: something worth saying, and optionally something worth doing. The second half is **stored, never run** — a `proposals` table (migration 3, on the same honest terms as 2) holding what it would do and why, and it waits.
     - **Approving is not a special execution path.** It goes through `submit`, the same entry point as anything the user types, so every completion gate, approval dialog, and recoverable snapshot applies unchanged. A test asserts `submit` is what gets called, with the proposal's own words.
     - **A repeating check does not stack the same proposal** every time it runs, and a decided proposal cannot be decided twice.
     - **A finding may carry no proposal at all.** `recent_failures` reports a pattern and offers nothing, because what to do about a repeated failure is judgement Aura does not have. Guessing there would be worse than staying quiet.
     - **No tool exposes approval to the model** — it cannot raise, approve, or dismiss its own proposal.
     - **A gap the live run exposed:** the card with its buttons was only built from the live event, so a proposal that arrived while the window was shut came back as plain text with no way to answer it — precisely the case background work exists for. Pending proposals are now restored with their buttons on load. A second, smaller one: the card was appended to the message grid without a column and collapsed to 34px wide.
     - Verified live end to end: a planted broken reference produced *"While you were away — 1 broken local reference… I can fix that if you want"*, the file was **untouched** while the proposal waited, and *Leave it* recorded a dismissal and still changed nothing.
     - **Then the approve path was run for real, and it found a bug no unit test could have.** Aura read the file, removed the dead `<link>`, and the workspace went to **zero broken references across all six HTML files** — the fix genuinely worked. But the model then produced empty responses until the budget ran out, and the reply said *"I couldn't complete that safely: the model kept returning an empty response."* **The work succeeded and the report claimed failure** — the exact inversion the honest-reporting rule exists to prevent, and the same shape as the phase 42 bug where a raising gate threw away a good answer.
       - Fixed: an empty final response no longer raises **when tools have already succeeded**. Aura writes the summary herself from the recorded actions, and adds a *Not confirmed* line saying the description was assembled rather than written by the model. It only raises when nothing was accomplished, which is the case the message was actually meant for.

   - **48.6 — Defaults that are actually on, and somewhere to see them. Done 2026-08-17.** Everything above was built and **nothing was ever scheduled**: the live database held zero active jobs, so Aura could watch and never did. Capability without a default is not a feature.
     - **Two checks are switched on once for a new install:** `broken_links` and `recent_failures`, daily, first look two hours after launch. `validate_workspace` is deliberately left off — a workspace mid-edit is often briefly invalid, and a check that nags during ordinary work is worse than no check.
     - **Seeded once, guarded by a config flag.** The classic way a helpful default becomes an annoyance is coming back after being switched off, so a test launches, switches `broken_links` off, launches again, and asserts it stayed off.
     - **"What Aura watches"** in the More menu lists every check with its next look and an on/off button, and — for the first time — **the reminders**, which until now could be set but never seen or cancelled.
     - **Only the user toggles.** `set_check_enabled` is a bridge method with no tool behind it, so the model still cannot widen what runs on its own.
     - Six tests for seeding and toggling, plus one that is worth more than the panel: **every `callApi("…")` name in the page must exist in `API_METHODS` and on the bridge** — 69 of them — which catches the whole class of "the button calls something nothing answers".
     - Verified live: the real install seeded two rows on launch; the panel rendered all three checks; toggling `validate_workspace` on and off through the actual buttons worked both ways; Escape closed the dialog and released `inert`; and forcing one seeded row due proved the end-to-end path — the scheduler ran `broken_links`, recorded *"nothing to report"*, stayed silent, and rescheduled for tomorrow.
     - One thing the screenshot caught that the tests could not: the **Off** badge wore the same green as **Watching**, so the colour said on while the word said off. Muted, then measured in the running page: 6.48:1.
       - Re-run against the real model on a second planted page: status **completed**, reply *"The fix is complete. fix-demo2.html no longer contains the broken image reference"*, and the file was correct. Both demo pages and their records were removed afterwards.
   - **Notifications stay inside the app** (also decided with the user): the conversation, the activity log, and a badge on the window. No OS toast, so none of the phase 46 notification work is pulled in and no new permission is asked of Windows.
   - **Gate:** a scheduled check runs on time inside its budget and quiet hours, reports what it found, and — when it wants to change something — produces a proposal that does nothing until approved; pause and emergency stop are provable while a run is in flight.

49. **UX consolidation and release readiness — Complete (P3)**
   - **Done — conversation sessions.** Every message is kept in `aura.db` against a session id, while `memory.data["conversation"]` stays the current session's view so the provider context, bootstrap, and Aura Mind read it unchanged. **New** starts a fresh conversation without destroying the old one; **Conversations** lists them, titled by their first message; opening one restores it as the live context. A session row is written on the first message, so launching Aura and saying nothing leaves no empty conversation behind. **Archive** hides a conversation and **Show archived** brings it back; the live conversation is refused, since archiving what is still collecting messages would hide it mid-use. Conversations are user content and are not touched by the 30-day recovery sweep.
   - **Done — search and export.** The search box matches every typed word against the same message across all conversations and returns the matching lines; `%`, `_`, and `\` are escaped, so a wildcard cannot silently match everything. Export writes one conversation into the workspace as Markdown, next to the existing memory export.
   - **Done — diagnostics export.** **Export report** in the log panel writes one Markdown file describing the machine, settings, storage counts and size, retention sweeps, granted folders, recent tasks, and recent failures. It reports counts and failures only — conversation text, memory content, and file contents are excluded by construction, with a test asserting they stay out — so the file can be handed to someone else. The retention sweep now also clears session rows with no messages, left behind by builds that wrote one per launch.
   - **Done — first-run guide.** Three steps on the first launch: what Aura is and where her workspace is, connecting LM Studio with a live model check, and what to know before starting (permissions, undo, what asks first, the diagnostics report). Checking the connection is offered but never required, so someone who starts LM Studio afterwards can still get through; **Skip for now** dismisses it without touching a single setting, and **Settings → Show first-run guide** brings it back.
   - **Done — accessibility.** Dialogs now contain focus by making the rest of the page `inert`, which removes it from both the tab order and the accessibility tree, and focus returns to the control that opened the dialog. Escape closes whichever dialog is topmost instead of a hand-maintained list — Conversations, Permissions, and the first-run guide could not be closed with Escape at all before this. The conversation is no longer a live region (streaming re-announced every token); finished replies go once to a dedicated status region. Close buttons carry labels, the sidebar toggle reports its state, and small helper text was measured in the running page and raised to at least 4.5:1.
   - **Done — packaging.** `python package.py` builds `dist/aura-<version>.zip` from an explicit include list, excludes personal data by name and suffix, and then re-opens the finished archive and refuses to hand over anything private that slipped through. The launcher now explains an unsupported Python instead of failing with a syntax error deep inside a module, since `pyw -3` can pick an older interpreter than Aura was installed with. The version is reported at `/health`, in the bootstrap, and in the diagnostics report.
   - Still open here: Aura Mind refinement.
   - **Done — Aura Mind.** The legend and the filters are one control: each layer (Identity, Memory, Preferences, Conversation, Tasks, Tools, Workspace) is a labelled colour key that switches its branch off, and the header reports how much is hidden. Layers are derived from the graph rather than a hand-kept table, with direct children of a category claimed first — tools hang off both `capabilities` and the tasks that used them, and without that, hiding Tasks took the entire Tools layer with it. Three misleading things were fixed: a fact held both as a preference and as a learned memory was drawn as two unrelated nodes and is now one node under both headings; a task with an empty request rendered as a nameless circle, because the key existed and the dict default never applied; and a task is now linked to the message that asked for it instead of repeating the same words in two branches.
   - **Done — relationship editing.** Every edge on this map is derived from the data, with one exception: the `project` a memory belongs to, which comes from a guess made when the fact was learned. It was **stored and never drawn**, so a fact tied to a piece of work hung under Memory like any other and the map showed less than Aura actually knew. It is now drawn as its own node, and a selected memory offers a project field — the only node kind that does, because offering to edit a derived edge would be a lie about what happens next. An empty box detaches rather than being refused: a wrong link is worse than no link, so removing one has to be as easy as adding one. An ordinary edit to the memory's text leaves the project alone.
   - **Done — live task plan.** The approved plan used to be shown once, in the dialog that asked for it, and then vanish — leaving a spinner and an action log to answer "how far along is this?". It now stays on screen and ticks off. The tick comes from `create_file`/`write_file`/`write_files`/`append_file` **succeeding**, never from the model saying it would: a plan that advanced on intention would be worse than no plan, since it would show finished work that does not exist. Reading a planned file, or failing to write it, moves nothing. The strip stays visible when the turn ends and says *"2 of 3 written"* when that is what happened, rather than rounding up.
     - The agent gained an `on_tool` callback beside the existing `approve`/`state`/`token` seams — there was no way for the interface to learn that a tool had finished. A failure inside that callback can never break the tool that triggered it, which has its own test.
     - Verified live end to end: a three-file build showed **0 of 3** on approval, went to **1 of 3** at the moment `faas49/index.html` appeared on disk, and reached **3 of 3**. Contrast measured in the running page: 4.91–15.35:1.
     - Relationship editing verified on the real graph: linking a memory to `aura_craft` created the project node and its edge, the field appeared for the memory node and **not** for a derived one, and detaching removed both. The test memory was put back as it was found.
   - Add sticky modal actions, keyboard focus handling, screen-reader summaries, contrast/reduced-motion checks, diagnostics export, first-run onboarding, and dependable packaging/updating.
   - **Gate:** a new user can install, connect LM Studio, choose voice and permissions, complete a first project, understand failures, and recover without opening source files.

50. **Backend structure — Complete (P1)**

   Measured before proposing anything, so this is about what the code does rather than how it looks:

   | | |
   |---|---|
   | `agent.py` | 1861 lines · `_tool_conversation` **344 lines / 70 locals** · `_execute_tool` **335 lines / 46 branches** |
   | `web_bridge.py` | 1340 lines · **64** HTTP-exposed methods · **162** references to `self.agent` |
   | tests | 254, mostly through public entry points — this is what makes the work safe |

   **The rule for every step: no existing test may change.** If a test has to be edited, behaviour drifted; that is a failure of the step, not a success of the refactor.

   - **50.1 — Tool registry. Done.** `_execute_tool` went from **335 lines to 34**, `tool_definitions` from **166 to 7**; the 46 branches became 46 handlers declared beside their own schema in `aura/toolkit.py`. The conversion was done by script rather than by hand, so no body was re-typed and nothing could be mistranscribed. `MUTATING_TOOL_NAMES` now derives from the tools that declare themselves mutating instead of being a second hand-kept list — it disagreed with the registry by one entry when compared. **254 existing tests passed unmodified** (0 deletions in the test file), plus 5 new ones asserting that every offered tool can be dispatched and every registered tool is offered. `tool_definitions()` declares 48 tools in one place and `_execute_tool` dispatches them in another, so the two can silently drift. `services.py` already demonstrates the fix: declare name, schema, and handler together and register. `_execute_tool(ToolCall, approve)` keeps its exact signature — 29 tests call it directly and it is a deliberate seam.
   - **50.2 — The phrase ladder leaves the model's path. Done.** `handle()` matched `"list files"`, `"read file "`, `"remember my name is "`, and `"remember preference "` *before* the model, so with a real model those phrasings never reached the tools that already do exactly that: one capability, two implementations, and the wording decided which ran. They now live in `_reply_without_tools`, reached only by a provider that cannot call tools at all — which is not a hiding place but the honest condition, since `MockProvider` has no other route. `build_hello_world` and the `isinstance(self.provider, MockProvider)` check moved with them, out of the production path. **Verified against the real model, not just the suite:** "list files" called `list_files` and answered properly (one retry — the shortcut used to be instant, so this costs a round), and in a scratch workspace "remember my name is Maya" called `remember_name` and stored it. 259 existing tests unchanged, 3 added.
   - **Correction to this plan's own reasoning.** It claimed the duplicated Aura Mind node came from the prefix path writing to `preferences` while the tool path wrote to `profile_memories`. That was wrong, and checking it took one probe: `remember_preference` and `remember_personal_fact` are **both tools**, and they write to the two different stores with no prefix ladder involved. Removing the ladder was still worth doing for the reason above, but it fixes nothing about the dual store. The actual cause is now 50.6.
   - **50.3 — `TurnState` and explicit gates. Done.** `_tool_conversation` went from **344 lines and 70 locals to 156 lines and 38**; the completion logic is five named gates of 11–45 lines each, plus `_run_one_tool` for the accounting a tool result proves. `aura/turn.py` holds `TurnState` (the facts of the turn) and `GateResult` (a verdict: ask again with this instruction, or record this as unproven). The loop is now: ask, execute, let the gates decide — with the order they run in stated in one line instead of implied by three hundred lines of control flow. Two confusable sets were separated while doing it: `MUTATING_TOOL_NAMES` (recoverable file mutations, from the registry) and `STATE_CHANGING_TOOLS` (anything whose success means the turn did what was asked, including undo and remember). **Verified against the real model:** "build a small site in the shop folder … then validate it" created both files, passed validation, and reported the evidence. 262 existing tests unchanged; 7 added that ask a single gate for its verdict — something the one long function made impossible to do at all.
   - **What this bought, concretely.** A gate can no longer read or write another gate's working variables, because it only sees `TurnState` and returns a verdict. The empty-response case, which used to `raise` from the middle of the function while the shared budget sat unused beside it, is now simply the first gate in the list.
   - **50.3 (original entry) — The one that matters.** Three bugs already came out of those 70 shared locals: the repeated answer needed `state("retry")` at four separate sites and one was missed; the artifact contract leaked in from the previous turn because `expected_paths` was set at the top and read 300 lines later; an empty completion raised immediately while `retries_left` sat unused beside it. A `TurnState` dataclass holds what the turn actually did, and each check becomes a gate returning *ok* / *retry with this instruction* / *report unconfirmed*. The loop becomes: ask, execute, let the gates decide. The value is not tidiness — it is that "did I forget to signal somewhere" stops being expressible.
   - **50.4 — Split the bridge. Done.** `web_bridge.py` went from **1340 lines to 586**, with `settings_bridge.py` (200), `workspace_bridge.py` (283), `voice_bridge.py` (192), and `memory_bridge.py` (139) beside it. They are mixed into `AuraWebBridge` rather than delegated to, which is the point: all 64 method names stay exactly where the HTTP layer already calls them, so no call site and no test moved. Extraction left 51 now-unused imports behind across the five files; those were removed too. The tests caught the one real mistake — `quote` was used in the workspace module but imported only in the original — which is precisely the failure mode this kind of move has.
   - **50.5 — `errors.py` and schema versioning. Done.** Aura already had seven deliberate exception types, each on a sensible builtin base; what was missing was a way to say "an error Aura raised", so callers wrote `(PermissionRefused, OSError, ValueError)` and hoped the list was complete. `AuraError` was added **beside** each builtin base rather than replacing it — `SandboxViolation` is still a `ValueError`, `PermissionDenied` still a `PermissionError` — so every handler already written keeps catching exactly what it did, and nothing had to be re-raised. Adoption at call sites can now happen one site at a time instead of all at once. `store.py` gained `PRAGMA user_version` with an ordered, append-only migration list and a documented rule (never edit a shipped migration, never renumber): `CREATE TABLE IF NOT EXISTS` brings a *new* database to the current shape but says nothing about changing one that already holds the user's data. **Migrated a copy of the real database first:** version 0 → 1 with 6 sessions, 18 messages, and 42 changes intact.
   - **50.5 (original entry).** One `AuraError` hierarchy instead of callers catching ad-hoc mixes of `ValueError`/`RuntimeError`/`PermissionError`/`KeyError`. And `PRAGMA user_version` with an ordered migration list, because `store.py` only does `CREATE TABLE IF NOT EXISTS` and has no answer for changing a schema that already has the user's data in it.
   - **50.6 — One store for a preference. Done.** `set_preference` now writes an ordinary profile memory (`"key = value"`, category `preference`), so both tools that could record a preference land in the same place — the one that can be edited, confirmed, reverted, and exported. `data["preferences"]` survives as a *derived* dict, rebuilt inside `save()` rather than at each of the five sites a memory can change, because a view refreshed on every write cannot go stale and forgetting one call site is precisely the failure this phase exists to remove. An existing flat list is adopted into memories once on load, matched by meaning so reopening never duplicates it. **Checked on copies of the real `memory.json` first:** the untouched file migrates to an identical state, and the same file seeded with a legacy `{"tone": "terse", "editor": "vs code"}` adopts both, keeps the other three memories, and adds nothing on a second open. Live: memories intact, the graph builds, and no preference appears twice.
   - **50.6 (original entry).** `memory.data["preferences"]` (a flat key/value dict, written by `set_preference` and the `remember_preference` tool) and `profile_memories` with category `preference` (written by `remember_personal_fact`, and the only one that is editable, revertable, confirmable, and exportable) hold the same kind of fact in two places. That is what made Aura Mind draw one preference as two unrelated nodes, and phase 49 papered over it in the graph rather than in the data. The fix is to make `preference` a profile memory everywhere and migrate the existing dict, keeping a read path for anything that still expects the old shape. Deferred out of 50.2 because it changes stored data and needs a migration, not because it is optional.
   - **Deliberately not touched:** the safety model. Sandbox, permissions, and the refusals around them are the strongest part of the project.
   - **Gate:** every step lands separately with the suite green and unmodified, and phases 50.2 and 50.3 are additionally exercised in the running app.

51. **Interface design system — Mostly complete (P2)**

   Same method as phase 50: measure first, then change how something is expressed rather than how it looks.

   | | before | after |
   |---|---|---|
   | distinct hex colours | 215 | 120 |
   | distinct radii | 18 | 12 |
   | distinct gaps | 13 | 7 |
   | token uses | 87 | 549 |

   The starting measurement is the argument for the whole phase: **215 colours across 280 uses**, meaning most shades were invented once and never reused. Two borders differing by one step in one channel each appeared five times.

   - **Colour becomes role.** 96 neutral blue-greys collapsed onto seven tokens — four surfaces, two borders, four text levels — plus named meaning (`--accent`, `--ok`, `--warn`, `--danger`, `--info`). Old names (`--panel`, `--line`, `--muted`) stayed as aliases so unmigrated rules keep working; this did not have to be one enormous step.
   - **Shape, spacing, and type become scales.** 276 declarations moved onto three radii, a seven-step spacing scale, and a seven-step type scale. The steps were taken from what the layout already used, so the density barely moved — **honestly, a handful of values shifted by up to 2px to land on a step**, most visibly the sidebar, which reads slightly calmer for it. 8px text disappeared; the smallest real text is now 9px.
   - **One base for small control buttons.** Memory cards, task cards, file rows, and the workspace toolbar each carried their own copy of the same four declarations, and each restated the same three colours for their destructive variant. They share one base now, and the variant says only what differs. Verified by computed style: a control button resolves to exactly what each site declared separately before, so nothing moved visually — 25 buttons in the tasks panel now resolve to three shapes on one radius.
   - **Two real contrast failures found on the way**, in the workspace explorer — a view the phase 49 accessibility pass never opened. File sizes were **2.04:1**, using a *border* colour as text; three toolbar buttons sat at 4.49:1. Both fixed, then re-measured: **738 text nodes across six views, none below 4.5:1**.
   - **Deliberately untouched:** the avatar's per-state colours, gradients, and rgba washes. Those are Aura's expressions, not interface furniture, and tokenising them would have taken her character away. Roughly 120 raw colours and 27 raw pixel values remain, mostly where a number means something specific — an icon's size, the avatar frame — and those should stay raw.
   - **Not verified by eye:** the browser pane stopped compositing during the final button change, so the last state was checked through computed styles, the contrast audit, and the suite rather than a screenshot.
   - **Gate:** met for colour, shape, spacing, and control buttons; the sidebar and composer still carry bespoke rules that could join the system later.

### Recommended execution sequence

Complete phase 39 first. Then develop phases 40–43 as the next cohesive release: Aura can see, speak, manage a project, and prove its work. Add phases 44–48 only after those trust foundations are stable. Phase 49 is the consolidation and release gate.

## Acceptance criteria

- Starts locally with `python aura_app.py` or `Start Aura.bat` and opens the HTML interface in a normal browser without Tkinter, pywebview, login, cloud service, API key, or frontend dependency installation.
- File operations cannot escape `aura-workspace`, including through `..`, absolute paths, or symlinks.
- Deletes are moved to `.aura-trash` rather than permanently removed.
- Commands always run with the workspace as their working directory, capture output, and time out.
- Safe commands can run automatically; other commands require an explicit UI confirmation.
- “Aura, create a hello world Python app” plans, writes, validates, runs, and reports the result.
- Memory and action logs persist locally.
- The localhost service rejects unauthenticated browser API calls and is not exposed to the network.
- Final validation covers the requested project scope and cannot be invalidated by a later unverified mutation.

## Whole-project review — 2026-08-15

A deliberate hunt for defects across the project, not tied to one phase.

**Fixed**

- **`ActionLog.recent()` read the entire log on every call.** The activity panel
  refreshes through it, so cost grew linearly with a file that never shrank:
  measured 147 ms at 22 MB. It now seeks to the tail and discards the partial first
  line — 14 ms at the same size, and effectively flat as the file grows.
- **The action log had no size cap at all.** Now capped at 4 MB, trimmed to 2 MB, and
  always cut on a line boundary.
- **`TaskJournal._trim_if_large` cut mid-line.** Slicing by character count left a
  half-written JSON object as the first record, which every later read silently
  discarded. It now trims on a line boundary. Three tests cover both logs, including
  asserting that every surviving line still parses.
- Removed two genuinely unused imports (`urlopen`, `CommandAgent`).

**Found, deliberately not fixed — needs a retention decision**

- **`.aura/changes.jsonl` and `.aura/history/*.bak` grow without bound,** as does
  `external-changes.jsonl`. Capping them is not a free win like the audit log: those
  files *are* the undo history, so trimming silently reduces how far
  `undo_last_change` and `rollback_task` can reach, and would orphan backup blobs in
  `history/`. That is a policy choice (how many days or changes stay recoverable, and
  deleting the matching `.bak` files with them), so it is recorded here rather than
  decided unilaterally. Current sizes are small — the concern is months of daily use.

**Checked and sound**

- `pyflakes` clean apart from an intentional `# noqa` availability check in `speech.py`.
- Config, memory, and permissions all save atomically via temp-file + replace, and all
  four persistent stores survive a corrupted file without crashing (verified by test).
- Every `subprocess.run` passes a timeout; the one `Popen` is terminated in a `finally`.
- Worker threads are daemons; both HTTP servers are closed on shutdown.
- `aura_diagnostics.py` runs clean against the live setup.

## Local storage moved to SQLite — 2026-08-15

The retention question from the project review turned out to be a correctness problem, not
a disk-space one, so the storage moved rather than being patched.

**Why.** `_restore_change()` recorded an undo by *appending a tombstone row*, and
`undo_last_change()` treated a change as undone only while that row existed. Trimming
`changes.jsonl` by age or size could delete a tombstone and make an already-undone change
undoable again — Aura would move the current, correct file to trash and copy a stale backup
over it. A change, its undo record, and its backups had to expire together across two index
files and a blob folder; hand-rolling that on flat files would have been fragile code thrown
away by any later migration.

**What changed.** `aura/store.py` holds one `aura.db` (stdlib `sqlite3`, no dependency, an
embedded file rather than the "database server" the README rules out). Five journals moved
into it: the action log, task events, workspace changes, the trash index, and external
changes. `config.json`, `memory.json`, and `permissions.json` deliberately stayed JSON —
small, hand-editable, and memory export is a user-facing feature.

**The bug is now unrepresentable.** An undo is `undone_at` on the change itself, not a
separate row, so there is nothing that retention could delete to resurrect a change.
`change_items` cascade with their change.

**Retention.** `Database.sweep()` expires changes older than 30 days or beyond the newest
500, in one transaction, then deletes only backups that *neither* recovery table still
references — `history/` has two writers. `.aura-trash` is swept by age alone, because an
undo moves the displaced file there without recording a row, so a reference-based sweep
would delete something still recoverable. Revoked permission grants are dropped after 90
days. It runs once per launch, wrapped so it can never block startup.

**Public APIs were kept identical** so the pre-existing tests could pass unchanged — that,
not new tests, is the evidence behaviour was preserved. Only tests that asserted the old
*file format* were rewritten to query the database instead.

**Three bugs found while doing it.**
1. The first version kept one open connection, so Windows could not delete the workspace
   and 153 tests errored. Connections are now opened per operation, which also removes the
   cross-thread question entirely.
2. `ORDER BY` inside a `UNION` arm applies to the whole compound select in SQLite; the
   expiry query became two plain statements.
3. **Aura would not start.** The agent now logs during construction (migration, sweep), and
   those events reached the bridge's `_on_log` before `__init__` had set `_closing`.
   Caught by launching the real app, not by the suite. `_closing` is now initialised before
   the agent is built, with a regression test.

**Migration.** On first run the JSONL files are imported in one transaction, old tombstones
folded into `undone_at`, and the originals renamed `*.jsonl.migrated` — kept, not deleted.
Verified on a copy of the real workspace first: 152 actions, 279 task events, 33 changes,
2 tombstones converted, and an explicit check that no backup a surviving record still needed
was deleted. Then verified live: Recent tasks, Workspace history, and a real undo through
the interface.

**Also fixed:** the 250-memory cap evicted the oldest entry even when the user had pinned or
confirmed it. It now only drops memories the user never vouched for.

## Completion gates unified — 2026-08-15

Every confusing failure in a full day of real use traced back to one place: the four
completion gates (artifacts, validation, action/mutation, verification). They were four
ad-hoc implementations of one question — *did the model actually do what was asked, and
can Aura prove it?* — with four counters, four message styles, and inconsistent escalation.

**What changed**

- **A failed gate no longer destroys the answer.** It used to `raise`, so the user saw
  "I couldn't complete that safely: required artifacts are still missing" and lost the
  model's entire reply — twice today when the work had in fact been done, just looked for
  in the wrong place. The reply is now kept and a **Not confirmed** section names precisely
  what could not be proven. The rule that matters is unchanged: Aura never presents
  unverified work as verified.
- **One retry budget of three**, shared by every gate, replacing four counters that
  together allowed nine extra rounds — each of which re-answered from scratch.
**What the journal said about "required artifacts are still missing" (9 occurrences)**

Read back from `task_events`, the nine failures were three different things, only two of
which were Aura's fault:

1. **The contract inherited the previous turn's filenames.** It was extracted from the
   routing request, which prepends the previous message. "Call undo_external_change to roll
   that back" therefore demanded `report.txt` — a file that follow-up never mentioned.
   Deliverables now come from the current message; the folder still comes from the
   inherited text, because it only scopes validation reporting.
2. **A granted-folder request was owed a workspace file.** "Use write_external_file to
   replace report.txt in the granted write folder" produces nothing inside the workspace,
   so the contract could only fail — it did, three times in a row, until the request was
   rephrased by hand. A request that names an external tool, an absolute path, or a granted
   folder no longer carries a workspace contract at all.
3. **The model simply did not call a tool.** Three attempts at "Create a file called
   loop-checkver2.txt … and validate it" produced *no tool events whatsoever*; the very
   next attempt, worded "Use create_file to make loop-checkver2.txt", called it
   immediately. Aura cannot fix the model, but it can stop asking the way that fails: the
   retries now name the tool ("Call the tool create_file once for each of these paths")
   instead of saying "use the relevant tool", which is the phrasing that reliably came back
   as prose.

- **An empty completion now costs one retry instead of the whole turn.** The diagnostics
  report made this measurable: "the model returned neither text nor a tool request" was the
  single most frequent failure on the real machine, and it was the one case that raised
  immediately while a retry budget sat unused beside it. Aura now asks plainly for an
  answer or one tool call, and only gives up — naming LM Studio as the thing to check —
  once the shared budget is gone.
- **Validation only when there is something to validate.** "build", "project", "app",
  "website" now require an actual workspace mutation; an explicit "validate" still stands
  alone. "How does my project look?" used to demand validation because it contains
  "project".
- **The validation gate asks the model once**, then validates deterministically itself
  instead of spending more of the user's time on something the backend is about to do —
  one round fewer than before, not more.
- **`action_expected` narrowed.** "The router offered tools" was far too weak a reason to
  insist one must have run, since the router offers tools for almost any wording.

**Three real bugs surfaced while writing the tests for it**

1. **Aura claimed deliverables were present without checking.** The evidence line echoed
   the *requested* paths unconditionally; that was only ever harmless because a missing
   file raised before reaching it. Turning the raise into a report would have turned this
   into an outright false claim.
2. **"my project" was parsed as a folder named `my`.** The artifact contract's exclusion
   list covered "the" and "this" but no possessives, so Aura went looking for `my/`.
3. **The list_files fallback replaced the reply** instead of adding to it, so a good
   conversational answer was thrown away and returned as a bare file listing whenever the
   model chose not to call a tool. It now augments the answer.

Verified live: "How does my project look these days?" now completes in one round with a
real answer, where before it burned the retry budget and returned a file listing.

### Known: a rare unreproducible test error

Twice on 2026-08-15 a full `unittest discover` run reported a single error that did not
recur. Seven consecutive clean runs followed the second one, and the failing test name
scrolled past before it could be captured, so it remains unidentified. Both sightings
happened while Aura and a headless browser were also running, which points at a timing
sensitivity in one of the browser- or port-dependent tests rather than a logic fault —
but that is a hypothesis, not a finding. If it recurs, capture it with
`python -m unittest discover -v > run.log 2>&1` and grep the log for `ERROR:`/`FAIL:`.


## Web search, on the user's own terms — 2026-08-17

Aura had no search at all, and the stated reason was honest: a general engine means an API
key, and she holds no credentials. **A SearXNG the user runs answers that without changing
the answer** — the index belongs to a service on their own machine, and Aura only reads it.

- **`aura/websearch.py`** builds the query, parses the JSON, and cleans the results. It opens
  no socket of its own: the caller passes in a fetch that has already been through the
  permission checks.
- **Snippets only, and not by promise.** The tool returns titles, links, and the engine's own
  excerpt, and never opens a result page — because a result URL is an ungranted domain, so
  `_http_get` refuses it. A test performs the fetch and asserts the refusal, which is the
  difference between a rule the code remembers and a property it cannot violate.
- **She does not claim to have read what she links to.** `NOT_READ` says so inside the tool
  result. The citation is the search itself, which she did read; the pages behind the links
  are not cited, because she did not open them.
- **Loopback needs no grant**, which is pre-existing and is exactly right here: the service is
  one the user started. Pointing it at a public instance works too, and then the ordinary
  domain grant applies without any special case.
- **The failure that will actually happen has its own message.** SearXNG serves HTML only by
  default, so the first attempt returns a web page. Saying "unreadable response" would send
  the user hunting in the wrong place; it names `settings.yml` and `search: formats:` instead.

**Two bugs the live run found, neither of which any unit test would have.**

1. **The tool existed and was never offered.** `select_tool_definitions` routes on English
   keywords, and `search_web` was in none of them, so Aura answered *"Ma ei saa otse veebis
   lehitada"* — **true of that turn and false of her** — and then invented plausible Estonian
   sources that were in no result. The reply was honest about a capability it had been denied.
   Fixed with a routing rule including Estonian (`veebist`, `internetist`, `netist`,
   `guugelda`), following the `ilm`/`meelde` precedent already in that function. Worth saying
   plainly: **that router is English-only apart from a handful of words, and the user writes in
   Estonian.** This fixed search; it did not fix the router.
2. **No budget, so twelve searches for one question.** Once routing worked, the model
   rephrased the same query twelve times before the turn budget ran out. A read-only tool
   costs nothing per call, which is precisely why nothing stops it. `MAX_SEARCHES_PER_TURN = 5`,
   and an identical query returns the previous results without spending the budget or asking
   the engine again.

**Verified live**, against a stand-in SearXNG on localhost because none was installed: the
Settings field rejected `not a url` and stored a real one; Aura called the tool, summarised
the three snippets correctly in Estonian, invented nothing, and cited the one address she read.
**What that run did not prove:** the budget never engaged, because the model made a single
search that time — the cap is covered by tests, not by having been seen to fire live. And a
stand-in is not a real engine, so the JSON quirks of an actual SearXNG remain untested.

## Aura starts the search engine — 2026-08-17

"Can we integrate SearXNG into Aura, so that starting Aura starts the search server too?"

**The honest half of the answer first: SearXNG cannot go inside Aura.** It is a Flask
application with roughly twenty dependencies, and a standard-library-only core is the promise
the whole project is built on. What *can* be owned is its **lifecycle**, which turns out to be
where the real value was anyway.

- **`aura/search_service.py`** finds the install, writes its settings, starts it, waits until
  the port actually answers, and stops it when Aura quits. Waiting matters: returning early
  would hand back a working-looking setting and a first search that fails.
- **Aura writes the settings file, so the two silent failures cannot happen.** `json` is always
  in `formats` — without it SearXNG answers every request with a web page — and `bind_address`
  is always `127.0.0.1`. It is Aura's own `aura-settings.yml`, regenerated each launch, never
  an edit of the user's `settings.yml`; a test writes theirs and asserts it is untouched.
- **An instance it did not start, it does not stop.** If the port already answers, the service
  is adopted: read, used, and left running on the way out.
- **A fixed command shape.** The user configures *where* SearXNG is, never *what runs*. There
  is no tool for the path, for starting, or for stopping — a component that launches a program
  is the last thing the model may aim, and a test asserts no such tool exists.
- **Failure never blocks startup.** Bring-up is on its own thread, the reason lands under
  Settings, and Aura opens with search off.

**What the live run settled, and it is the important part: SearXNG does not run on Windows.**
The install went through — after two Windows-specific detours, a `searxng.conf:socket` filename
NTFS cannot represent, and a `setup.py` that imports the package it is building — and then the
process died on import: `searx/valkeydb.py` imports `pwd`, which is Unix-only. This is not a
configuration problem and no flag fixes it. Docker or WSL is the only route, and neither is
installed on this machine.

So the native supervisor is verified in every part that can be verified here: it found the
install, wrote correct settings, launched the process, caught the immediate death, and reported
it. It has never been seen to bring a real *native* SearXNG up, because nothing on this machine
can run one.

**Then Docker Desktop was installed, and the rest was finished properly.** `search_mode` is now
`off` / `docker` / `folder`, and the Docker route does the whole job on Windows:

- Aura writes the settings mounted into the container, because the image carries its own
  `settings.yml` — the same JSON trap as a native install, one directory further away.
- The port is published as `127.0.0.1:8888:8080`, never a bare `-p`. That single flag is what
  decides whether a private search engine is private, so a test asserts it.
- The image is never pulled automatically. A few hundred megabytes fetched unasked is not
  something a chat window decides; Aura names the `docker pull` command instead.
- **`docker` was not on PATH although Docker Desktop was installed and running** — it lives
  under `%LOCALAPPDATA%\Programs\DockerDesktop`. `find_docker` looks there too, because
  "absent from PATH" would otherwise be reported as "Docker is not installed", which was false.

**Verified live, end to end, against the real engine.** With the manual container removed and
port 8888 closed, Aura was restarted: it started the container itself, filled in its own
endpoint, and answered *"Eesti pealinn on Tallinn. Rahvaarv on ligi 452 563 elanikku (andmed
2026. aastast)"* citing the search it read. The figure was then checked against the engine's
own output — it appears verbatim in a Vikipeedia snippet, year included, so nothing was
invented. Quitting Aura removed the container: `docker ps -a` shows no `aura-searxng`.

Two smaller things the same run exposed, both invisible to unit tests: SearXNG colours its log
output, so terminal escape codes arrived intact in the settings panel; and the raw
`ModuleNotFoundError: No module named 'pwd'` reads as a missing package, sending the reader
after something that cannot exist on Windows. Both now go through `explain()`, which strips the
escapes and names the actual problem.


## The router learns Estonian — 2026-08-17

Left open by the search work, in its own words: *"that router is English-only apart from a
handful of words, and the user writes in Estonian. This fixed search; it did not fix the
router."*

**Measured first, because the size of it was not obvious.** Twenty ordinary requests, each in
Estonian and in English, through `select_tool_definitions`:

| | requests with **no tools at all** |
|---|---|
| Estonian | **16 / 20** |
| English | 0 / 20 |

So for this user Aura was, most of the time, a chatbot with no hands — and the failure was
invisible, because a model with no tools does not report an error. It says it cannot help. The
four Estonian requests that did work worked by accident: *meelde*, *testid* and *zipiks*
contain fragments of English.

**`aura/language.py`** annotates Estonian stems with the English words the existing rules
already match, so not one routing rule was duplicated or rewritten:

    "loe fail notes.txt"  ->  "loe fail notes.txt read file"

Stems are matched at the start of a word and allowed to run on, because Estonian inflects by
suffix — `fail` catches *failid*, *failist*, *failide*.

**Two design mistakes, both caught by tests rather than by reasoning.**

1. **Hints were first inserted inline, next to the word they explained.** That broke English:
   some rules match whole phrases, so "look it up" became "look create it up" and stopped
   matching — `loo` (create) fires inside *look*. Worse, it made a read-only English request
   register as a build. Hints now attach at the end of **their own clause**.
2. **Collecting them at the end of the message would have been worse**, and less visibly: a
   hint would escape the negative clause meant to suppress it, so "ehita leht, aga ära käivita
   seda" would have offered the run tool it had just been forbidden. Per clause, commas
   included, is the only placement that survives both. `NEGATIONS` teaches the existing
   clause-stripper *ära / ärge / mitte / ilma*, and a test asserts the build half of that
   sentence survives while the run half does not.

A collision test scans every tool description for English words an Estonian stem would fire on.
It found `fail`→*failed* and `ava`→*available* on top of `loo`→*look*; all three are recorded
in `ENGLISH_LOOKALIKES`, and a stem added later that collides now fails that test rather than
quietly rewriting English requests.

**A second English-only chokepoint was found on the way.** `_requires_mutation` decides whether
a request changes anything, and it too matched English verbs only — so "tee mulle veebileht"
was not even considered a build. It goes through the same annotation now.

**The empty tool list was its own bug**, separate from language. When nothing matched, the
model got *nothing*, so Aura could not even look before saying she could not help.
`FALLBACK_TOOLS` offers six read-only tools in that case — guessing is acceptable for reading
and never for writing, and a test asserts no mutating tool can appear there. A greeting is the
one case where nothing remains the right answer, and `_is_greeting` now recognises Estonian
too: *tere hommikust* and a bare *hommik* were being routed as ordinary requests.

**Verified live**, against the real model: *"Näita, mis failid tööruumis on, ja loe kõige
väiksem neist ette"* — a request that produced zero tools an hour earlier — called
`list_files`, `workspace_summary` and `read_file`, and answered with the contents of `test.ts`.
That it really is the smallest file was then checked independently; it is, at 36 bytes.

Negation is covered by tests rather than by a live run: proving it live would have meant
letting Aura build files unattended, which is not something to do while nobody is watching.


## Two languages, two voices — 2026-08-17

"The speech model should be able to read both Estonian and English."

**It cannot, because no such model is installed and none is easily available.** Checked rather
than assumed:

- **Piper publishes no Estonian voice at all.** 174 voices, 55 languages; Finnish, Latvian and
  Russian are there, Estonian is not. espeak-ng, which Piper uses to phonemize, *does* know
  Estonian (`urj/et`) — but a phonemizer is not a voice.
- **Windows had only Hazel and Zira**, both English. So every Estonian reply was being read
  aloud by an English voice, which sounds like a fault in Aura rather than a missing voice.
- **TartuNLP**, the chosen source, has no light option: the TTS worker is TensorFlow + HDF5
  behind RabbitMQ; the Neurokone app is TFLite and its weights are not published standalone;
  and `tartuNLP/XTTS-v2-est` on HuggingFace is PyTorch/Coqui, roughly 2 GB of model on top of
  ~2.5 GB of Torch, under a "license:other". `coqui-tts` does support Python 3.13, so the route
  is open — it is the size and the CPU latency that need deciding, not the compatibility.

So the answer is **two voices and an automatic switch**, which is needed whichever Estonian
voice is eventually installed. That part is built, and it is the part that makes any of the
above actually get used.

**`language.detect(text, default)`** decides per reply, never per sentence: Aura's answers are
Estonian prose wrapped around English filenames, tool names and code, and switching voice
mid-sentence would sound worse than one voice throughout.

Three findings from building it, each caught by a probe rather than by reasoning:

1. **A word that is evidence for both languages is evidence for neither.** `on` is everywhere
   in Estonian and is also an English preposition; counted on both sides it made *"Eesti
   pealinn on Tallinn"* score one-all and come out English. `AMBIGUOUS_MARKERS` is now the
   intersection of the two lists, subtracted from both.
2. **Some replies carry no signal at all.** *"Meeldetuletus: venita"* has no Estonian letter and
   no function word. Rather than inventing a second word list, detection falls back to the
   hundred Estonian stems the tool router already knows.
3. **The best signal was not in the reply.** *"Eesti pealinn on Tallinn"* is four words with no
   giveaway — and completely obvious to anyone who saw the question. Aura answers in the
   language she is addressed in, so the bridge detects the *request* and passes it as the
   default. Evidence in the reply still wins over the hint; a test asserts both directions.

**Being honest about the gap matters as much as the switch.** With no Estonian voice installed
Aura still speaks — silence would be a worse surprise than a wrong accent — and pushes a
`speech_language` event so the interface can say *"No Estonian voice is installed — that reply
was read by the English voice"*. The alternative is leaving the user to conclude Aura is
broken.

The voice swap is per utterance and restored afterwards, so a reply in the other language
cannot quietly repoint the configured voice. A test covers exactly that.

**Verified live**: *"Vasta ühe lühikese lausega eesti keeles: kuidas läheb?"* produced *"Hea!
Olen siin ja valmis aitama."*, was detected as Estonian, and raised the missing-voice notice.
An English reply in the same session raised nothing, which is the half that keeps the notice
worth reading.

**Decided 2026-08-17: no Estonian voice is installed, and that is deliberate.** The options
were put with their real costs — XTTS-v2-est at roughly 5 GB with an unmeasured CPU latency,
espeak-ng at 10 MB and robotic — and the answer was to leave it. Nothing here downloads a
voice. The switch is in place, so the day an Estonian SAPI voice or a Piper model exists on
this machine, choosing it in Settings is the whole job.

One correction recorded rather than quietly dropped: "Windows' own Estonian voice" was offered
as an option before checking whether Windows offers a local Estonian speech voice at all. The
user then checked, and **it does not** — so that option never existed, and the recommendation
was wrong. Only Hazel and Zira are available on this machine.

That leaves exactly two routes to Estonian speech, both weighed and both declined for now:
XTTS-v2-est at roughly 5 GB with unmeasured CPU latency, or espeak-ng at 10 MB and robotic.


## The rest of the English-only checks — 2026-08-17

Routing and mutation detection were fixed earlier the same day. A grep for raw `casefold()`
comparisons found six more, and they were the ones that matter most: they decide **what Aura
promised to produce** and **whether the turn counts as finished**.

Measured the same way, ten Estonian requests against their English translations:
**thirteen field mismatches**. In order of how quietly they fail:

- **`asks_for_work` was false for six of ten Estonian read requests.** That sets
  `action_expected` false, which switches off the gate insisting a tool actually ran. Aura
  could answer *"loe see fail ette"* without opening the file and nothing would object — the
  same family as the phase 42 and 48.5 bugs, where a finished job and an unstarted one became
  indistinguishable in the report.
- **The staged-delivery instruction never fired** on an Estonian build request, so multi-file
  work skipped the plan/implement/validate/repair discipline entirely.
- **`validation_asked` never fired**, so "valideeri projekt" was not treated as asking for
  validation.
- **`_targets_external_location` missed "väljaspool tööruumi"**, which means a request aimed at
  a granted folder was held to a workspace contract it could never satisfy.
- **No folder was ever extracted**, so validation was never scoped to the right project.

Five of these are vocabulary and go through the same annotation as everything else — one
annotated copy of the routing request, computed once and read by every keyword test below it.

**The sixth is grammar, and annotation cannot fix it.** English marks the relation with a
preposition ("in the promo folder"); Estonian marks it with a case ending, and the folder name
sits on either side depending which ending is used — *kausta promo*, *promo kaustas*,
*aura_craft projektis*. Two patterns, one per direction. The first attempt used one word list
for both and reported the folder of "projektis uus leht" as **"uus"** — the word that merely
came next. A test now pins all three shapes.

Also added: `mida`, `kuidas`, `milline`. The memory-question regex asks for
*what/which/how … know about me*, and the Estonian half of that sentence had no interrogative
it recognised.

After: **zero mismatches** across the same ten pairs, with a test that compares the whole
contract for each pair rather than individual fields — so a future English-only check shows up
as a failing test instead of as Estonian quietly getting a weaker promise.

**Verified live**: *"Loe fail test.ts ette ja ütle, mis seal sees on"* called `read_file` and
came back with *"Confirmed evidence: Final file state inspected: test.ts"* — that line is the
verification gate, and it is exactly what `action_expected` had been switching off.


## Aura answered in Finnish — 2026-08-17

Spotted on screen, not in a test: *"Valmis! Kaikki kolme tiedostoa luotiin onnistuneesti"*,
and a file plan whose descriptions read *"pääsivu HTML-dokumentaatio"*, *"perus CSS-tyylit
sivuille"*. That is Finnish.

**The cause was an absence.** The system prompt said nothing whatsoever about which language to
answer in — so a 9B model asked in Estonian drifted to the neighbouring language that has far
more training data behind it. `LANGUAGE_RULE` is inserted in `start_messages`, which every path
into the model goes through, including the file plan whose descriptions had drifted too. It
names Finnish explicitly, because naming the specific attractor helps a small model more than a
general instruction does.

**A correction to my own earlier work.** The bilingual speech test asserted that every dotted
letter proved Estonian. It does not: `ä`, `ö` and `ü` are shared with Finnish, so that test was
confirming the mistake rather than catching it. Only `õ`, `š` and `ž` are Estonian's alone.

`looks_finnish()` is deliberately its own question rather than a third answer from `detect()`.
Folding Finnish into "et" is exactly how a wrong-language reply becomes invisible; instead the
bridge records `wrong_language` in the log and says so once, so the pattern is visible in
diagnostics rather than merely endured.

**Verified live**: *"Loetle, mis failid tööruumis on, ja ütle iga kohta üks lause"* came back as
clean Estonian throughout — *"peamine esitlusleht HTML-failina"*, *"Kodulehe CSS-stiilid ja
disainielemendid"* — where the same shape of request had produced Finnish an hour earlier.

## Reliability: the empty response, measured — 2026-08-17

The recurring failure is *"I couldn't complete that safely: the model kept returning an empty
response"*, seen three times across two days.

**It is now measured rather than only reported.** `ProviderReply` carries `finish_reason` and
the token counts, and the empty-response gate records them. `finish_reason` is the question that
was never asked: a model that ran out of budget mid-answer and a model that chose to say nothing
produce the same empty string and need opposite fixes.

**What the probing ruled in and out**, eight requests through the real provider against the real
model:

- Fresh context, five ordinary Estonian requests: **5/5 answered**, every one with a tool call,
  1231–2321 prompt tokens.
- With twelve and twenty-four turns of the real conversation replayed: answered both times.
- `max_tokens` is 32768 and nothing came back with `finish_reason: length`.

So **it did not reproduce in eight attempts**, and no hypothesis is confirmed. The honest state
is that the next occurrence will be recorded with its reason instead of being unexplained.

**One thing worth noting that did show up.** With the longest history the model answered a
"list the files" request from context, with `finish_reason: stop` and **no tool call** — it
talked about files instead of looking at them. That is the failure `action_expected` exists to
catch, and it is caught now that the gate finally fires for Estonian requests.

**A false alarm, recorded because it nearly became a report.** My first probe sent the messages
straight to LM Studio and got HTTP 400 on every single request — the model's template refuses
more than one system message. That looked like a serious bug in Aura until checking showed
`merge_system_messages` is applied on the real path and folds them into one. The defect was in
the probe, not the product.


## Which model to run — measured, 2026-08-17

Nearly every failure on 2026-08-17 was model behaviour rather than Aura's code: empty replies,
Finnish drift, answering without calling a tool, a line count invented rather than read. The
obvious question was whether a bigger model would simply remove them.

**Six models, six requests, one provider call each** — Aura's real system prompt, real language
rule, real selected tool list. It measures the model's **first decision**: reach for a tool or
answer from nothing, which is exactly where the failures happened. No tool is executed, so the
workspace is untouched, and the content of an answer is not scored — only whether the model
went and looked.

| model | correct tool decisions | seconds |
|---|---|---|
| **qwen/qwen3.5-9b** (in use) | **6 / 6** | 41 |
| meta-llama-3.1-8b-instruct | 5 / 6 | 125 |
| mistralai/ministral-3-14b-reasoning | 4 / 6 | 110 |
| qwen3-14b-claude-4.5-opus-distill | 4 / 6 | 195 |
| google/gemma-4-12b-qat | 4 / 4 answered | 103 |
| qwen3-coder-30b-a3b-instruct | no answer | — |

**The model already in use won, and not narrowly.** Six correct decisions out of six, and
4–10 seconds each after loading, against 100–195 seconds for every other candidate.

**Two results needed correcting before they could be reported.** The 30B and two of gemma's
requests came back as `ProviderError`, which reads like a model that cannot call tools. Asking
for the actual message showed **timeouts** — 300 seconds, including load. That is still a real
finding for a companion that answers while you wait, but it is slowness, not incapability, and
reporting it as the latter would have been wrong.

The one blemish on the current model needed the same care. Scored 5/6 on language, and the
failing case turned out to be the **reasoning preamble** in English while the tool call itself
was correct — and reasoning is never shown in the chat. Counting it against the model would
have been counting something the user cannot see.

**The conclusion is a negative one, which makes it worth recording.** Swapping the model is not
where the remaining reliability lives. The guards built today — the routing fixes, the language
rule, the completion gates, the search budget — are doing the work that a larger model was
supposed to make unnecessary, and the larger models on this machine are too slow to be a
companion at all.

**Limits of this, stated plainly:** six requests, one run each, single-turn only. A difference
of one is noise. It says nothing about how a model behaves over a long multi-step build, which
is where a different kind of failure lives.


## Keep the router, or send every tool — measured, 2026-08-17

Three separate bugs on 2026-08-17 came from one place: the keyword router decided for the model
and decided wrongly. Estonian requests got **no tools at all** sixteen times out of twenty,
`search_web` existed but was in no rule, and the completion checks used the same English
vocabulary. None of that could happen if every tool were simply offered every time. So: does
the router help, or only get in the way?

**Six requests, both ways, three runs each — thirty-six calls in all.** Three runs because a
single one cannot tell a real difference from the model having a bad moment.

| | correct | median prompt | median time |
|---|---|---|---|
| router's selection | **16 / 18** | **1624 tokens** | 7.0s |
| all 53 tools | **16 / 18** | 6490 tokens | 7.7s |

**A dead heat on correctness, and the router costs a quarter of the prompt.** The predicted
cost held up exactly: 1989 tokens against 6497 on the same request.

**And the two failures were the same case in both columns**, which makes them a fact about the
model rather than about routing: asked *"Otsi veebist, mis on Eesti pealinn"*, it answered from
its own knowledge instead of searching, two runs out of three, whichever tools it was given.

**That is my test case being poor rather than the model being wrong.** Estonia's capital is
something a model knows; declining to search for it is sensible. Scoring it as a failure said
more about how I wrote the case than about anything measured. The honest reading is that the
two conditions are indistinguishable at 18/18.

**So the router stays.** Removing it buys nothing measurable and costs four times the prompt on
every request, every retry, and every step of a multi-step task.

**What the measurement did settle is a smaller change.** The router's real defect is not that
it selects narrowly — it is that when it matches *nothing*, the model gets nothing. That is now
softened with six read-only tools, chosen because guessing is acceptable for reading and never
for writing. Since offering everything turns out not to hurt the decision, the unrouted case
could be given the full catalogue instead, and the 4× cost would be paid only in the rare
situation where the router has already failed.

**The reason that is not simply done:** it would hand the *widest* capability to the request we
understood *least*. Mutations stay recoverable and dangerous actions still ask first, so it is
defensible — but it is a safety-shaped decision rather than a performance one, and it belongs
to the user.
