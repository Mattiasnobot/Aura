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

43. **Durable goals and task engine — In progress (P1)**
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
   - **Gate:** an interrupted multi-step build resumes from the correct checkpoint and its history explains what changed and why. *(Not yet met — Steps 1–3 make task history honest, visible, organized by project, and evidence-backed; nothing yet resumes an interrupted build.)*

44. **Memory v2 — In progress (P2)**
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
   - **Still planned:** a local semantic workspace index (would need an embedding or
     TF-IDF-style approach — a real dependency/architecture decision, not yet made);
     merge/history controls (conflicts are now detected and shown, but resolving one
     still means editing or forgetting a memory by hand); export.
   - Sensitive-info opt-in, editing, and forgetting already existed before this phase
     (`_is_sensitive`, `update_profile_memory`, `forget_profile_memory`) — only export
     is still missing from that bullet.
   - **Gate:** relevant preferences and project decisions are recalled with visible provenance while unrelated or sensitive details are not guessed.

45. **Multimodal and visual reasoning — Planned (P2)**
   - Allow images and screenshots to be attached to LM Studio vision-capable models instead of only storing them in the workspace.
   - Let Aura compare references with rendered output, inspect screenshots, detect layout regressions, and run basic responsive/accessibility checks.
   - Detect model capabilities automatically and hide unsupported controls.
   - **Gate:** Aura can use a supplied visual reference, inspect its own rendered result, and explain evidence-based differences before finishing.

46. **Scoped autonomy and OS bridge — Planned (P2)**
   - Add revocable, user-selected folder mounts beyond `aura-workspace`, plus opt-in clipboard, notifications, app launch, screen capture, and managed process controls.
   - Introduce a permissions center with one-time, session, project, and persistent grants plus a readable audit trail and emergency stop.
   - Preserve the safe workspace as the default and require narrow approval for broader access.
   - **Gate:** every external capability is off by default, visibly scoped, revocable, logged, and incapable of silently broadening its own access.

47. **Network and tool extensibility — Planned (P2)**
   - Add permission-scoped search, weather, and per-domain browsing with a clear online/offline indicator and source reporting.
   - Create a documented tool/provider extension interface so future local services and integrations do not require changes to the core agent.
   - **Gate:** Aura can perform an approved online lookup, cite what it used, and remain fully functional in local-only mode.

48. **Proactive companion — Planned (P2)**
   - Add queued and scheduled work, reminders, recurring checks, quiet hours, and three autonomy modes: suggest, ask, and act-within-grants.
   - Surface proposals before execution, enforce retry/time/cost budgets, and provide pause and emergency-stop controls.
   - **Gate:** a scheduled local task runs only within its saved permissions and produces a concise notification and audit record.

49. **UX consolidation and release readiness — Planned (P3)**
   - Add named conversation/project sessions with search, archive, export, and a true new-conversation flow.
   - Refine Aura Mind with filters, a legend, project/task/memory layers, relationship editing, and the live task plan; remove duplicated or misleading nodes.
   - Add sticky modal actions, keyboard focus handling, screen-reader summaries, contrast/reduced-motion checks, diagnostics export, first-run onboarding, and dependable packaging/updating.
   - **Gate:** a new user can install, connect LM Studio, choose voice and permissions, complete a first project, understand failures, and recover without opening source files.

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
