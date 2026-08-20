# Aura — what is worth improving

A full sweep of 2026-08-18: every Python module, every function, the browser files,
the test suite, the config, and 410 rows of Aura's own action log.

Everything below is **measured, not guessed**. Where a number appears, the command that
produced it is reproducible from the repository. Items are numbered so you can say "do 3
and 9" instead of asking again.

**The shape of the thing:** 13,102 lines across 37 modules in `aura/`, 7,539 lines of
tests, 3,530 lines of `app.js`, 729 of CSS, 450 of HTML. 578 tests, all green.

---

## 1. Correctness — where Aura says things that are not so

### 1.1 Aura vouches for files she never opened ⭐ *the one real bug*

`VERIFICATION_TOOLS` at `aura/agent.py:1409` counts **`file_info`** as verification:

```python
VERIFICATION_TOOLS = {"read_file", "read_many_files", "file_info", "inspect_code"}
```

But `_tool_file_info` (`agent.py:1796`) returns only `bytes`, `lines`, `modified` — metadata.
It never reads the content. Knowing `notes.txt` is 231 lines is not knowing what is in it.

Watched happening live on 18 August. The model ran four `file_info` calls, published a table
headed **"Kõige tõenäolisem sisu"** guessing what was inside each file, and Aura printed
underneath it:

```
Confirmed evidence:
- Final file state inspected: shop/PLAN.md, shop/index.html, shop/notes.txt, shop/style.css.
```

Aura's own footer vouched for a table of invention. This is worse than the silence was:
silence tells you nothing, this tells you something false in Aura's voice.

**Fix:** separate the two ideas. `file_info` confirms a file *exists and how big it is*; only
`read_file`, `read_many_files` and `inspect_code` confirm *content*. The evidence footer then
says what is actually true. **Small — one set, one message, a few tests.**

### 1.2 Nothing catches speculation about unread files

Related but distinct: no gate notices when a reply speculates about content it never read.
The "Not confirmed" machinery already exists and would be the right home.

**Fix:** when a reply discusses files whose content was never read, add the note. **Medium,
and lower confidence than 1.1** — detecting speculation means matching words in Estonian and
English, which invites false positives. Worth doing only after 1.1.

---

## 2. The wait — where your time goes

Measured across 65 real turns:

| median | 75th | 90th | worst |
|---|---|---|---|
| 24s | 84s | 221s | **985s** |

**Twelve of 65 turns made you wait over two minutes.** The 985-second turn ran *one* tool and
spent the other sixteen minutes thinking.

### 2.1 A turn has no wall-clock budget ⭐

`reasoning_depth` is `deep`, which permits **48 rounds** (`ROUND_LIMITS`, `agent.py:66`). At 22
tokens a second there is effectively no ceiling on a turn — only the 900-second HTTP timeout,
which arrives as a failure rather than as an answer.

**Fix:** a deadline you set. When it passes, Aura stops, keeps what the tools actually
established, and says so plainly — she already does exactly this when the model goes quiet
mid-task, so the honest-report path exists. **Medium, and the one you would feel daily.**

### 2.2 One tool call can contribute 250,000 characters

Per-tool caps exist and are sensible on their own — `read_many_files` allows 20 files, 300
lines each, and refuses past 250,000 characters (`agent.py:1776–1788`). But 250,000 characters
is roughly 60,000 tokens, and **nothing budgets the turn as a whole**. Compaction only runs on
a retry, by which point the round has already been paid for.

**Fix:** a per-turn character budget across all tool results, spent down as tools run.
**Medium.**

---

## 3. Structure — what makes changes expensive

### 3.1 `agent.py` is 2,590 lines and 126 functions ⭐

It imports 18 of the 37 modules. Roughly 700 of those lines are the 52 `_tool_*` handlers,
which are pure leaf functions reached through the `@tool` registry — they have no reason to
live beside the turn loop.

**Fix:** move the handlers to `aura/tools_*.py`, grouped by subject (files, memory, web,
system). Mechanical, testable, and it takes the biggest file in the project down by a quarter.
**Medium, low risk.**

### 3.2 The five functions that are too long to hold in your head

| lines | branches | where |
|---|---|---|
| 241 | 41 | `graph_model.py:43` `build_mind_graph` |
| 202 | 34 | `agent.py:731` `_tool_conversation` |
| 193 | 60 | `settings_bridge.py:139` `save_settings` |
| 130 | 42 | `routing.py:51` `select` |
| 114 | 40 | `voice.py:200` `listen` |

`save_settings` has the worst density — 60 branches in 193 lines — though it is well tested
(19 test references). `build_mind_graph` is the longest single function in the project.
**Small each, worth doing one at a time when you next touch one.**

### 3.3 `app.js` is 3,530 lines in one file

136 top-level functions, one class, and `bindControls()` alone is **292 lines**. There is no
module system in play — it is a single classic script.

**Fix:** split by panel (conversation, workspace, mind, settings, voice) using ES modules.
Note this changes `index.html` to `type="module"`, so it wants care with the CSP.
**Medium.**

### 3.4 `web_bridge.py` is 981 lines and 52 functions

The second god object, and the one every browser request passes through.
**Medium.**

### 3.5 38 broad `except Exception` handlers

Ten of them in `agent.py`. None are bare `except:` — that hygiene is already good — but a
broad catch around a wide block hides the bug you have not met yet.
**Small, ongoing.**

---

## 4. The browser side

### 4.1 137 literal hex colours in 99 rules

There are 46 design tokens on `:root`, and 99 rules ignore them.

Worth being precise: **there is no theme system today** — no `data-theme`, no
`prefers-color-scheme`, no theme setting — so nothing is broken right now. This is the bill
that comes due the day you want a light mode or a different accent: 99 rules to find by hand.
**Small if done now, large if done later.**

### 4.2 `progress.html` and `index.html` share no styling

The progress window was built quickly and carries its own look. If it is staying, it should
inherit the tokens.
**Small.**

---

## 5. Tests

578 tests, 76 classes, 1,638 assertions, 126 seconds to run. Healthy. Three specific weaknesses:

### 5.1 32 tests read project source code as text

They assert on the *shape of the source* rather than on behaviour, so reformatting can break
them. Sometimes that is exactly right — I added one today deliberately, to pin that compaction
happens *before* the retry instruction is appended, which is an ordering no behavioural test
can see. But 32 is more than that argument justifies.
**Small, ongoing: convert the ones that are really behavioural.**

### 5.2 Two modules are barely covered

| module | lines | functions | test references |
|---|---|---|---|
| `workspace_bridge.py` | 284 | 21 | 2 |
| `voice_bridge.py` | 193 | 13 | 1 |

Both are user-facing paths — importing files, and starting the microphone.
**Medium.**

### 5.3 16 `time.sleep` calls in the tests

The usual source of a suite that is slow now and flaky later.
**Small.**

---

## 6. Checked and found fine — do not spend time here

So these stop being open questions:

- **DOM wiring is sound.** 241 ids, **zero duplicates**, and **zero** ids that JavaScript
  looks for but HTML does not define.
- **Accessibility is genuinely good.** 83 `aria-*` attributes, 38 `role=`, 44 `<label>`,
  `lang` set, every input labelled, every button named, no unlabelled images.
- **No XSS surface.** 230 `textContent` assignments against 4 `innerHTML`, and all four are
  static strings or a number.
- **No inline event handlers** — the CSP holds.
- **No import cycles** anywhere in `aura/`.
- **No bare `except:`** in 181 handlers.
- **Tool calls do not thrash.** Exactly **one** repeated identical call in 65 turns.
- **Language detection is not a problem.** `wrong_language` has fired **once**, ever.
- **The old gate failures are extinct.** All 31 failures of 14–15 August were missing
  artifacts or unperformed actions. Not one has recurred since 15 August.

---

## Suggested order

1. **1.1** — a real bug where Aura misleads you, and the smallest fix on the list.
2. **2.1** — the turn deadline; the item you would feel every day.
3. **3.1** — lift the tool handlers out of `agent.py`; makes everything after it cheaper.
4. **5.2** — cover `workspace_bridge` and `voice_bridge` before touching them.
5. **4.1** — collect the colours while the file is still 729 lines.

Everything else is worth doing when you are already in that file, not as a trip of its own.

---

# Second pass — 2026-08-18, after the work

## Done

**1.1** `file_info` split from the content tools; the footer no longer vouches for files
that were only measured. **1.2** A gate names files the answer discusses but never opened.
**2.1** A turn deadline, settable in Settings. **2.2** A per-turn reading budget.
**3.1** 50 tool handlers out of `agent.py` into six topic modules (2,676 → 2,244 lines).
**3.2** `build_mind_graph` split into eight layers. **4.1** 137 literal colours → 37.
**4.2** The progress window shares the main token vocabulary. **5.2** 16 new tests for the
two thinnest-covered modules. **5.3** The one real sleep-and-hope fixed.

**617 tests green** (was 578).

## Still open, in the order I would take them

1. **`app.js` → ES modules** (3,532 lines, `bindControls()` 292). The one item I stopped
   short of: it converts the whole interface at once and changes script timing under the
   CSP. Worth its own change with its own live verification.
2. **`web_bridge.py`, 981 lines / 52 methods.** Same mechanical shape as the tool split
   that worked; simply ran out of room.
3. **`save_settings`, 198 lines / 62 branches** — now the worst density in the project, and
   marginally worse than before because the turn-budget field was added to it. It parses,
   validates and writes in one run; the three want separating.
4. **`_tool_conversation`, 213 lines** — the turn loop, now the longest thing in `agent.py`.
   Harder than it looks: the round loop, the gate pass and the retry handling are genuinely
   entangled. Only worth doing with care.
5. **10 of 52 tools are never named in a test:** `append_file`, `capability_summary`,
   `change_history`, `compare_images`, `extract_archive`, `find_relevant_files`,
   `list_external_folder`, `recent_tasks`, `rollback_task`, `search_files`. Two of those —
   `rollback_task` and `change_history` — are recovery paths, which is where a gap matters
   most.
6. **38 `except Exception` handlers**, ten in `agent.py`. Each needs its own judgement.
7. **35 tests read project source as text.** Some are right (ordering a behavioural test
   cannot see); most are not.
8. **`routing.select` 130 lines / 42 branches**, `voice.listen` 114 / 40,
   `provider._stream_completion` 113 / 34.

## New, found only by running the thing

9. **A deadline cannot stop a tool that is already running.** `run_command` with a long
   timeout will still outlast the budget, because the clock is read between tokens and a
   subprocess emits none. Rare, but the budget quietly does not apply there.
10. **`empty_retries_used` is spent per turn, not per round.** One silence early in a long
    turn uses the allowance for the whole turn. Probably right — but it is a decision that
    was never actually decided.

---

# Diagnosis of the cold silence — 2026-08-18, started

**Ruled out: the tool block.** The theory was that too many schemas in front of a 9B model
was causing it. Measured on the exact request that went quiet:

| request | tools sent | schema size |
|---|---|---|
| the one that went quiet | 9 | 942 tokens |
| "Tere" | 0 | 0 |
| a web search | 10 | 1,107 tokens |
| *all 53 tools, if they were ever sent at once* | 53 | 5,787 tokens |

Routing is doing its job — 9 tools, not 53. **Not the cause.**

**Ruled out: automatic resending.** Three identical turns in the log looked like Aura
resubmitting a request on her own. Tested by sending once and then not touching the browser
for five minutes: **submitted exactly once.** `sendMessage` has no retry path. The earlier
triple was my own doing during testing.

**Two instrument failures of mine, corrected:**
- The "seventeen-minute hang" never happened. I read `offsetParent` on the Stop button, but
  that button is always on screen — `setBusy` disables it rather than hiding it. The turn
  finished in 4m16s.
- A "90-second budget overshooting to 120 seconds" was measured against a server holding its
  startup config; my edits to `config.json` never reached the running process. Settings
  writes do. Re-measured properly: **60s budget, stopped at 60.14s.**

**Still open — where the diagnosis goes next.** The silence at 15:32 had `tools_run=0` and a
7,349-token prompt, and the prompt is mostly *conversation history*: 12,105 characters of it,
including Aura's own "While you were away" digests and out-of-time notices fed back as
assistant turns. The next thing to measure is whether that history — rather than its size —
is what stops the model cold: the same message against a fresh session versus a loaded one.

## Diagnosis, continued — history ruled out, and the wall named

**Ruled out: conversation history.** The same request, the same nine tools, the same system
prompts, sent straight to LM Studio with only the history varied. Five runs each:

| history | prompt | silent | tokens back |
|---|---|---|---|
| none | 2,774 | **0/5** | 65, and a correct `read_file` call |
| half | 3,046 | **0/5** | 85 |
| all of it | 3,296 | **0/5** | 86 |

Fifteen runs, fifteen answers. History is not what stops it.

**And the model is deterministic**, which changes what the silence *is*. The same payload
returns the same tokens every time — tested at temperature 0.4 and 1.2, three runs each,
about ten seconds apiece, so it is genuinely regenerating rather than serving a cache. Even
adding a character changed nothing.

So a silence is **not flakiness**. It is one specific prompt that reliably produces a single
token — which means it is reproducible in principle, and has been irreproducible in practice
for the dullest possible reason: *nothing was keeping the prompt.* Every `empty_response`
record holds token counts and tool names, and never the thing needed to replay it.

**Built:** the provider keeps the exact payload it last sent — after merging and tuning, which
is what actually goes out — and a silence writes it to `.aura/silences/`, ten kept. The next
one is reproducible, and then it can be bisected: drop the role, drop the plan, drop the
memory block, and find which part stops the model cold.

**One more probe bug worth recording.** The first version of the history probe posted
`start_messages` straight to LM Studio and got HTTP 400: *"System message must be at the
beginning."* Aura sends six separate system messages and `merge_system_messages` folds them
into one inside `complete()` — the probe had bypassed it. Aura was fine; the instrument was
wrong. That is the third instrument failure today, which is itself the pattern worth watching.

## Cold-silence diagnosis — measurements, 2026-08-18 evening

**56 controlled calls against the live model. The silence was never reproduced.**

Ruled out, each by holding everything else constant:

| hypothesis | test | result |
|---|---|---|
| the tool schema block | measured what routing actually sends | 9 tools / 942 tokens, not 53 — **not the cause** |
| Aura resending on her own | sent once, no browser contact for 5 minutes | submitted exactly once — **not the cause** |
| prompt size | 3.6k → 13.4k tokens, tool list fixed | 22/24 correct, 0 silences, non-monotonic — **not the cause** |
| shape of the history | padded with tool-call/result pairs and carried reasoning | 24/24 correct, 0 silences — **not the cause** |
| streaming | same payload streamed and not, 4 runs each | identical to the byte — **not the cause** |

**What was explained instead: tool *count*, not prompt length.**

- 53 tools at 8,100 tokens → the model called **no tool** and answered from memory
- 10 tools at 8,938 and 13,405 tokens → correct tool every time

So `routing.py` earns its 208 lines, but for a reason unrelated to token budgets. It
also means retry compaction is a **latency** measure, not a correctness one — the wait
roughly doubles from 3.6k to 13.4k tokens (15s → 33s) while accuracy holds.

**Where it stands.** Synthetic reproduction is exhausted; more guessing would be
guessing. `_capture_silence` (`agent.py:1190`) is now live and writes the exact failing
payload to `.aura/silences/`. Because the model is deterministic, that payload will
reproduce the silence on demand — turning an unreproducible event into a fixed test
case. Nothing more to do until a real one happens.

Worth stating: several changes landed after the last observed silence (15:32), including
the reasoning carried back into history. It is possible the cause is already gone. That
would also be answered by the capture staying empty.

## Open for next session — 2026-08-19, ~00:00

**A midnight-boundary flake.** `test_quiet_hours_hold_a_reminder_rather_than_dropping_it`
failed once during a full run that crossed 00:00, and passes in isolation immediately
after. Nothing touched today goes near the scheduler, so this is pre-existing and only
reachable for a moment each night — which is exactly when unattended work runs. Worth
finding before autonomy is widened.

**The self-report is measured now, but still decorated.** Asked how fast she is, Aura
ran `how_i_have_been_running` and reported real figures — median 41s, worst 985s, the
true tool counts. She then added a fourteen-day trend and "API limit" as the cause,
neither of which exists in that data, and divided failures by turns to produce "102%".
The tool's note now names what the data does *not* contain; whether that is enough is
unmeasured — it went in after the last live run.

**Two captured silences** are waiting in `.aura/silences/`. Deterministic model, so
they reproduce on demand. That is the thread to pull next.

## The silent killer, caught — 2026-08-19

Three causes, not one. All three found by replaying captured payloads rather than
reasoning about them, after six hypotheses and 56 controlled calls had failed.

**1. A 4,096-token context window.** The model was loaded with 4,096 of a possible
262,144. LM Studio silently truncated longer conversations, and when the truncation
removed the last user message the chat template answered
`raise_exception('No user query found in messages.')` with HTTP 400 — which Aura
recorded as an empty response. Every earlier silence was above that line: 6,394 ·
7,349 · 7,484 · 7,631 · 11,090.

**2. Aura asking the model to answer her own reply.** A payload ending in an
assistant turn — no tool calls, nothing outstanding — reproduced the silence every
single time. It is the correct response: there is no question there. Appending one
user turn to the identical payload produced 879 tokens of answer.

```
as captured    completion=1     content=0      SILENT
with the guard completion=879   content=1856   SPOKE
```

**3. The reporting itself.** A rejected request and a model choosing silence looked
identical, because Aura only recorded "nothing came back". That misdiagnosis is what
cost a day — the instrument was the defect, not the model.

Fixes: the context length is read from LM Studio and shown in Diagnostics with a
warning below 16k; a template rejection now explains itself and says what to change;
and `_ensure_something_to_answer` never sends a conversation that ends with Aura's
own words.

Left open: the exact path that produces an assistant-final payload was not traced —
the guard is defensive and catches it wherever it comes from, and logs
`nothing_to_answer` each time, which will name the path from real use.
