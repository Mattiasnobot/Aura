# Improvement plan 1

Five improvements, ordered by how much evidence there is for them rather than by
how interesting they are. Written 2026-08-16, after phases 47–51.

The evidence throughout is Aura's own diagnostics export and task journal on the
real machine, not impressions.

---

## 1. She notices patterns in her own data — **done 2026-08-17**

**Evidence: strongest.** The most useful thing built in this project was the
diagnostics report — it turned a vague "it breaks sometimes" into nine dated
failures with causes. But it only helps when the user exports it and someone
reads it.

The extension is that Aura reads her own journals and says something when a
pattern forms: *"the last three builds failed on the same thing"*, *"this
request has returned an empty response three times — check the model is still
loaded"*.

Needs no new capability. The data is already there; nothing looks at it.

**Depends on:** phase 48's scheduler, so it can run without being asked.

**Built, and the measurement came first.** The real journal held **139 tasks, 33 of them
failed** — and the most common single cause was not the one being chased:

| times | failure |
|---|---|
| **11** | the model returned neither text nor a tool request |
| 6 | the model did not perform the requested workspace action |
| 4 + 3 | a promised file was never written |
| 2 | the model kept returning an empty response |

Those top three are one problem — the model produced nothing usable — and counting them
separately is what hid how big it was.

Three checks were added beside `recent_failures`, all read-only and all silent unless something
is actually forming:

- **`model_producing_nothing`** aggregates that family and says what to do about it — check the
  model is still loaded. This is, almost word for word, the example this item was written with.
- **`failing_streak`** reports *runs*, which are a different claim from counts. "Some things
  fail" is something the user already knows; "the last three in a row failed" says something
  changed just now.
- **`unkept_promises`** names a file that keeps being promised and never written, and proposes
  finding out why — explicitly *not* creating it.

**Three judgements that decide whether this is useful or merely noisy**, each of which was got
wrong first and then measured:

1. **A window, not all of history.** The first version reported "19 failures" — true, and
   spread over two days, most of them long since fixed. A complaint nobody can silence by
   fixing the problem is the definition of a nag. Bounded to 48 hours, the same journal reports
   4.
2. **One pattern, one voice.** `recent_failures` and `model_producing_nothing` both fired on the
   same failures, so the user would have been told twice. The generic check now leaves that
   family to the specific one that can say something actionable about it.
3. **A decision is not a fault.** Declined plans and cancellations were being counted as
   failures, so Aura reported the user's own "no" back to them three times. Excluded.

**Seeding had to learn names.** `default_checks_seeded` was a bare flag, which can only answer
"all of them or none" — so a default added later either never arrived on an existing install,
or arrived dragging back the ones already switched off. It now records *which* checks have been
offered, with the pre-existing installs treated as having been given the original two.

**Verified live on the real install**: the two new defaults appeared without duplicating the
two already there, and forcing one due produced, unprompted in the conversation:
*"While you were away — 4 times in the last two days the model answered with nothing Aura could
use — no text and no tool call. That is usually the model rather than the request: check it is
still loaded, or try a smaller one."*

## 2. She gets better at the thing she does most — **done 2026-08-16**

**Evidence: strong, and specific.** The journal is unambiguous about where she
fails, and it is not knowledge — it is tool calling. Three attempts at "Create a
file called X" produced *no tool events at all*; the next attempt, worded "Use
create_file to make X", called it immediately. A later shop-site build needed
seven tool calls and one retry.

**The change: plan before doing.** For a build that names more than one file,
Aura first writes the file list — each path with a one-line purpose — the user
confirms it, and only then does she create them **one file per call**.

This attacks the measured failure directly: a small, concrete call succeeds
where a large vague one does not. It also gives the user a place to correct the
shape of the work before any file exists, which is cheaper than undoing it after.

**Built and verified against the real model.** "Build a small landing page in the
promo folder with index.html, style.css and about.html" produced a plan card
first — three paths, one line each — and after approval ran exactly the intended
shape:

    list_files → read_file
    create_file → read_file      (index.html)
    create_file → read_file      (style.css)
    create_file → read_file      (about.html)
    validate_project → completed

One call per file, each read back, then validated. No artifact-contract retry
was needed, where the same class of request previously failed three times in a
row until it was reworded by hand.

Scope, deliberately narrow: only a build that names **two or more** files, and
only when there is someone present to approve it. A single file is not worth the
round trip, a read-only request is never planned, and a plan that fails to
generate is swallowed so it can never cost the user their request.

The approval card was reused rather than a new panel invented; it only changes
its wording — "Before Aura builds this", *Build these* / *Stop* — and keeps the
line breaks, because a plan is a list to read rather than a command to scan.

## 3. Project memory, not only user memory — **done 2026-08-17**

**Opinion, with less evidence.** Aura remembers things about *the user*. But she
works inside projects, and "aura_craft uses this structure" is a different kind
of fact from "Mattias prefers HTML interfaces". Memories already carry a
`project` field; it is not used as a scope.

The difference shows on the second visit to a project, where today she starts
from nothing.

**Open question for the user:** how often several projects are actually in play.
That decision is theirs more than mine.

**Answered: about three at a time**, depending on what ideas come up and what people ask for.
Three is the number at which this stops being tidiness — with one project nothing can be
confused; with three, facts cross.

**Measured with three projects in play, before anything was built:**

- The project was **never detected** from ordinary wording. "Add a contact page to the promo
  site" produced nothing, because the only shapes understood were "in the promo folder" and
  "the promo project" — and nobody writes those.
- **One recalled fact in five belonged to a different project**, matched on a shared word like
  *footer*. Not merely unhelpful: applying promo's footer rule to aura_craft work is worse than
  recalling nothing.
- The follow-up was the worst case. *"Now add a footer to it"* named no project, so the whole
  scope was lost mid-conversation.

Three changes, and the scoring in `relevant_memories` was already there waiting for a project
to be passed in:

- **A project is a folder that exists**, matched anywhere in the wording, so an ordinary noun
  cannot invent one — *"write a shopping list"* is not the `shop` project. An explicit "the X
  project" is still honoured even when the folder does not exist yet, because a project is
  usually named before it is created.
- **It is remembered across the conversation** and cleared when a new one starts.
- **Another project's fact is held back**, unless it is pinned — pinning is the user saying
  *always*, and always outranks scope. General facts about the user are never scoped away.

Facts are now filed under the project **before** learning rather than after, since the first
message of a conversation is exactly the one that names it.

**The sticky project is shown**, in the status line as *"Local · private · on promo"*. State
that silently decides which memories are recalled must be visible, or "why did she use the
promo rule here?" has no answer on screen.

**Verified live**: after 0 of 5 requests before, all three probe requests detect the right
project with zero cross-project recall, and in the running app *"Mis on promo kaustas?"* set the
status line to **on promo**, which then survived a follow-up that never named it.

### A real bug this turned up, in live use rather than in a test

Asked *"Ja mitu rida on seal esimeses failis?"*, Aura answered **"137 rida"** about a file of
47 — **without opening it**. The task journal shows no tool at all for that turn, and the turn
was recorded as *completed*.

It is the same family as the morning's `asks_for_work` fix, in a shape the measurement set did
not contain: a question that needs reading but uses no reading verb. The first fix for it was
too loose — counting the words *file* and *project* made *"How does my project look these
days?"* an errand and burned the retry budget, which an existing test caught immediately. What
actually separates them is whether the question asks for a **fact**: a named file, a real
project folder, or a count or size.

Live afterwards, the same question called `file_info` and answered **47**, with *"Confirmed
evidence: Final file state inspected"*. That 47 is correct: the file has no trailing newline,
so `wc -l` reports 46 while an editor shows 47.

## 4. A self-check — **done 2026-08-17**

**Small, and there are now a lot of moving parts.** One command that answers: is
LM Studio reachable, is a model loaded, does vision work, does the voice work, is
the workspace writable, is the database healthy. Turns "something is broken" into
one click.

**Built as `aura/health.py`**, reached from **More → Is anything broken?**, and offered to the
model as a read-only `self_check` tool so Aura can answer the question when it is asked of her.
Eight checks: the model server, the loaded model, images, the workspace, storage, speech out,
voice in, and web search.

Three rules decided the shape:

- **Nothing changes.** The single exception is the workspace check, which writes one probe file
  and removes it, because "can Aura write here?" has no honest answer that avoids trying. A
  test asserts the workspace is left exactly as found.
- **Nothing can hang.** A self-check that freezes is worse than none.
- **"I cannot tell" is a real answer.** A provider that is not a server gets `unknown`, not a
  verdict — inventing either one would be a guess dressed as a diagnosis.

Two questions were deliberately separated that used to be one: *is the server there* and *is a
model loaded*. They fail independently and need different answers, and rolling them together
produced the unhelpful "LM Studio is not working" when the server was fine.

**Two bugs came out of building it, and only one was findable by unit tests.**

1. `with sqlite3.connect(...)` manages the transaction, **not** the connection. Every
   self-check leaked a handle, and on Windows that kept the WAL files locked. Caught because
   nine tests failed to clean up their temporary folder.
2. `self_check` passed `status` both positionally and as a keyword to `log.record`, so the
   method raised the moment it was actually called. **The suite was green**: the tests called
   `health.run` directly and never the method behind the button. Found by opening the panel.
   There is now a test for the bridge method itself.

**Verified live**: all eight report `ok` on the real install — including things that had never
been visible in one place before, such as *"Piper is ready (en_US-lessac-medium.onnx)"*,
*"pocketsphinx, microphone available"*, and *"Answering on http://127.0.0.1:8888"*. Status is
carried in the text as well as the colour, since an icon alone is not readable by a screen
reader; measured in the running page at 4.91–11.62:1.

## 5. Undoing a whole conversation — **done 2026-08-17**

Today a single change or one task can be rolled back. *"Undo everything you did
in this conversation"* is the natural next step, and the data already supports
it — sessions and changes are both recorded, and changes carry their task id.

**The data did not quite support it.** Changes carry a task id and messages carry a session id,
but nothing joined the two: `task_events` had no session, so "this conversation" could not be
expressed at all. That needed **migration 4** — and it is the first one that actually changes a
column, which is exactly the case the note on migration 2 said the mechanism existed for. No
`CREATE TABLE IF NOT EXISTS` can add a column to a table already holding someone's history.

**Old rows are deliberately not backfilled.** A conversation from before this reports that it
cannot be undone, rather than matching tasks by timestamp. Guessing is the one thing an action
this destructive must never do, and a near-miss would undo somebody else's work.

**Two decisions about how it behaves:**

- **Newest task first.** Oldest-first would restore an early backup and then let a later one
  overwrite it, leaving the workspace in a state that never existed. A test pins the order by
  writing the same file twice.
- **A failure does not abandon the rest.** The conversation is undone as far as it can be and
  what could not be is named. Stopping halfway in silence would be the worst of both.

**It asks first, and the asking is a list rather than a warning.** "Are you sure?" answers
nothing; the names of the files that would come back is what a person actually needs. The
current versions go to the workspace trash, so the undo is itself recoverable.

**Only the user reaches it.** `rollback_task` remains a tool the model may call for the task in
hand; a whole conversation is a different size of action, and no tool exposes it.

**Verified live.** The real database migrated to v4 with all 406 task rows intact — after the
same migration was rehearsed on a copy first. Aura then built `undo-test/index.html` and
`undo-test/style.css` in a fresh conversation, and undoing it through the real handler showed
both paths in the confirmation, removed the folder, left both versions in `.aura-trash`, and
reported honestly: *"Undid 2 change(s) across 1 task(s), 1 task(s) had nothing to undo."* The
`promo/` work from other conversations was untouched.

**One robustness gap this exposed.** Every migration until now was empty, so re-running one was
free. `ADD COLUMN` has no `IF NOT EXISTS`, so a database whose `user_version` was ever reset
would have refused to open at all. Applying an already-applied column is now treated as
success.

---

## Order

**2 → 1 → 4 → 5 → 3.** **All five are done**, finished 2026-08-17.

Item 2 first because it repairs what is measurably broken. Item 1 next because it
is the one that makes her feel independent, and it rides on the phase 48
scheduler. Items 4 and 5 are small. Item 3 is the largest and the least certain.
