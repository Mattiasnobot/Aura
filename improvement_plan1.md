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

## 3. Project memory, not only user memory

**Opinion, with less evidence.** Aura remembers things about *the user*. But she
works inside projects, and "aura_craft uses this structure" is a different kind
of fact from "Mattias prefers HTML interfaces". Memories already carry a
`project` field; it is not used as a scope.

The difference shows on the second visit to a project, where today she starts
from nothing.

**Open question for the user:** how often several projects are actually in play.
That decision is theirs more than mine.

## 4. A self-check

**Small, and there are now a lot of moving parts.** One command that answers: is
LM Studio reachable, is a model loaded, does vision work, does the voice work, is
the workspace writable, is the database healthy. Turns "something is broken" into
one click.

## 5. Undoing a whole conversation

Today a single change or one task can be rolled back. *"Undo everything you did
in this conversation"* is the natural next step, and the data already supports
it — sessions and changes are both recorded, and changes carry their task id.

---

## Order

**2 → 1 → 4 → 5 → 3.** Items 2 and 1 are done; **4 is next.**

Item 2 first because it repairs what is measurably broken. Item 1 next because it
is the one that makes her feel independent, and it rides on the phase 48
scheduler. Items 4 and 5 are small. Item 3 is the largest and the least certain.
