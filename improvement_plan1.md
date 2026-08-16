# Improvement plan 1

Five improvements, ordered by how much evidence there is for them rather than by
how interesting they are. Written 2026-08-16, after phases 47–51.

The evidence throughout is Aura's own diagnostics export and task journal on the
real machine, not impressions.

---

## 1. She notices patterns in her own data

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

**2 → 1 → 4 → 5 → 3.**

Item 2 first because it repairs what is measurably broken. Item 1 next because it
is the one that makes her feel independent, and it rides on the phase 48
scheduler. Items 4 and 5 are small. Item 3 is the largest and the least certain.
