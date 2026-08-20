"""Undoing things: the tools that put the workspace back.

Lifted out of `agent.py`, where 516 lines of handlers sat interleaved with the
turn loop and the completion gates. These are leaf code: each does one thing and
returns a result. They stay methods because they really do use `self.sandbox`,
`self.memory` and `self.config` — a mixin keeps every `self.` resolving exactly
as it did, which is what makes this a move and not a rewrite.
"""

from __future__ import annotations

from .toolkit import tool


class RecoveryTools:
    """Undoing things: the tools that put the workspace back."""

    @tool('undo_last_change', "Undo Aura's most recent file mutation using its protected snapshot history.",
          {}, [])
    def _tool_undo_last_change(self, name, args, approve, call):
        result = self.sandbox.undo_last_change()
        return result

    @tool('rollback_task', 'Undo every still-active file mutation belonging to a specific Aura task ID.',
          {'task_id': {'type': 'string'}}, ['task_id'])
    def _tool_rollback_task(self, name, args, approve, call):
        result = self.sandbox.rollback_task(str(args["task_id"]))
        return result

    @tool('change_history', 'List recent recoverable workspace mutations and whether they were undone.',
          {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20}}, [])
    def _tool_change_history(self, name, args, approve, call):
        result = {"changes": self.sandbox.change_history(int(args.get("limit", 20)))}
        return result

    @tool('undo_external_change', "Undo Aura's most recent write outside the workspace, restoring the previous version or removing a file it created.",
          {}, [])
    def _tool_undo_external_change(self, name, args, approve, call):
        result = self.external_writer.undo_last()
        return result

    @tool('recent_tasks', "Review Aura's recent persistent task outcomes and tools used.",
          {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20, 'default': 5}}, [])
    def _tool_recent_tasks(self, name, args, approve, call):
        result = {"tasks": self.tasks.recent(max(1, min(int(args.get("limit", 5)), 20)))}
        return result
