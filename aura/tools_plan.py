"""The plan as something a turn can pick up, rather than something it re-derives.

`PLAN.md` stays what Mat reads and edits. These tools keep the machine-readable half:
which steps exist, which are finished, and what the evidence was.

The point is what happens between turns. Before this, "continue building it" gave Aura
nothing to continue *from* — `_plan_steps` was a list on the bridge, wiped at both ends
of every turn, so each message re-derived the whole plan and often re-proposed it. A
step that survives the conversation that decided it is the difference.
"""

from __future__ import annotations

from .toolkit import tool


class PlanTools:
    """Recording a plan, and recording progress through it."""

    @tool('record_plan_steps',
          'Record the ordered steps for the current project so work can be resumed '
          'later. Use this once a plan is agreed, not to propose one. Steps already '
          'finished keep their status when the plan is re-recorded.',
          {'steps': {'type': 'array', 'minItems': 1, 'maxItems': 40,
                     'items': {'type': 'string'},
                     'description': 'One short line per step, in the order they should happen.'}},
          ['steps'])
    def _tool_record_plan_steps(self, name, args, approve, call):
        project = self.current_project
        if not project:
            raise ValueError("no project is in play, so there is nothing to plan for")
        steps = args["steps"]
        if not isinstance(steps, list):
            raise ValueError("steps must be an array of strings")
        written = self.db.set_plan_steps(project, [str(s) for s in steps])
        done = sum(1 for s in written if s["status"] == "done")
        return {"project": project, "steps": len(written), "already_done": done,
                "next": (self.db.next_plan_step(project) or {}).get("text", "")}

    @tool('update_plan_step',
          "Record progress on one step of the current project's plan: mark it done "
          'with the evidence that proves it, or blocked with the reason. Do this as '
          'you go, not at the end.',
          {'step': {'type': 'string',
                    'description': 'The step text, or enough of its beginning to identify it.'},
           'status': {'type': 'string', 'enum': ['doing', 'done', 'blocked'],
                      'description': 'doing while working on it; done when proved; '
                                     'blocked when it cannot proceed.'},
           'evidence': {'type': 'string',
                        'description': 'What proves it done, or what is blocking it.'}},
          ['step', 'status'])
    def _tool_update_plan_step(self, name, args, approve, call):
        project = self.current_project
        if not project:
            raise ValueError("no project is in play")
        wanted = str(args["step"]).strip()
        match = self._match_plan_step(project, wanted)
        if match is None:
            known = [s["text"] for s in self.db.plan_steps(project)]
            raise ValueError(
                f"no step matches {wanted!r}. The plan for {project} is: "
                + ("; ".join(known) if known else "empty — record it first"))
        updated = self.db.set_step_status(
            match["id"], str(args["status"]), str(args.get("evidence", "")))
        # Remembered so the progress gate leaves this one alone: her decision
        # outranks anything inferred from the turn.
        turn = getattr(self, "_turn_state", None)
        if turn is not None:
            turn.plan_steps_recorded.add(match["id"])
        following = self.db.next_plan_step(project)
        return {"step": updated["text"], "status": updated["status"],
                "next": (following or {}).get("text", ""),
                "remaining": sum(1 for s in self.db.plan_steps(project)
                                 if s["status"] in ("todo", "doing"))}

    def _match_plan_step(self, project: str, wanted: str) -> dict | None:
        """Find the step meant, without demanding it be quoted exactly.

        Exact text, then case-insensitive, then a prefix. A model that has to
        reproduce a sentence verbatim to record progress will simply stop
        recording progress — the same failure the edit tools showed when they
        required an exact match count.
        """
        steps = self.db.plan_steps(project)
        lowered = wanted.casefold()
        for test in (lambda s: s["text"] == wanted,
                     lambda s: s["text"].casefold() == lowered,
                     lambda s: s["text"].casefold().startswith(lowered[:40]),
                     lambda s: lowered[:40] in s["text"].casefold()):
            found = [s for s in steps if test(s)]
            if len(found) == 1:
                return found[0]
        return None
