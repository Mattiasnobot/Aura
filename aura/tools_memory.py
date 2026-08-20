"""What Aura remembers about Mat, and how he corrects it.

Lifted out of `agent.py`, where 516 lines of handlers sat interleaved with the
turn loop and the completion gates. These are leaf code: each does one thing and
returns a result. They stay methods because they really do use `self.sandbox`,
`self.memory` and `self.config` — a mixin keeps every `self.` resolving exactly
as it did, which is what makes this a move and not a rewrite.
"""

from __future__ import annotations

from .toolkit import tool


class MemoryTools:
    """What Aura remembers about Mat, and how he corrects it."""

    @tool('remember_name', "Remember the user's preferred name.",
          {'name': {'type': 'string'}}, ['name'])
    def _tool_remember_name(self, name, args, approve, call):
        self.memory.set_name(str(args["name"]))
        result = {"remembered": True}
        return result

    @tool('remember_lesson',
          'Record a correction the user has given about how work should be done in the '
          'current project, so it is applied automatically next time. Use this whenever '
          'the user corrects your approach, points out a repeated mistake, or states a '
          'rule for this project.',
          {'lesson': {'type': 'string',
                      'description': 'The rule, in one sentence, phrased as an instruction.'}},
          ['lesson'])
    def _tool_remember_lesson(self, name, args, approve, call):
        """A correction, filed where it will come back.

        Aura repeated the same path mistake four times in one afternoon because
        nothing survived the turn it was corrected in.
        """
        lesson = str(args["lesson"]).strip()
        # Explicit and full confidence: he said it, it is not an inference.
        item = self.memory.learn_fact("lesson", lesson, source="user correction",
                                      confidence=1.0, explicit=True,
                                      project=self.current_project)
        if not item:
            return {"remembered": "", "note": "that lesson was already recorded"}
        return {"remembered": item["value"], "project": self.current_project or "",
                "applies_to": "every future turn in this project"}

    @tool('remember_preference', 'Remember one durable user preference.',
          {'key': {'type': 'string'}, 'value': {'type': 'string'}}, ['key', 'value'])
    def _tool_remember_preference(self, name, args, approve, call):
        self.memory.set_preference(str(args["key"]), str(args["value"]))
        result = {"remembered": True}
        return result

    @tool('remember_personal_fact', 'Remember one clear, non-sensitive fact the user explicitly stated about their preferences, interests, goals, projects, tools, or working style.',
          {'category': {'type': 'string', 'enum': ['goal', 'interest', 'personal', 'preference', 'project', 'tool', 'work_style']}, 'value': {'type': 'string'}}, ['category', 'value'])
    def _tool_remember_personal_fact(self, name, args, approve, call):
        item = self.memory.learn_fact(
            str(args["category"]), str(args["value"]),
            source="Explicitly remembered through Aura chat", confidence=1.0, explicit=True,
        )
        result = {"remembered": True, "memory": item}
        return result

    @tool('forget_personal_fact', "Forget one personal memory matching the user's description. Ambiguous matches are returned without deleting anything.",
          {'query': {'type': 'string'}}, ['query'])
    def _tool_forget_personal_fact(self, name, args, approve, call):
        query = str(args["query"])
        matches = self.memory.find_profile_memories(query)
        if not matches:
            raise FileNotFoundError("No personal memory matches that description")
        if len(matches) != 1:
            choices = "; ".join(str(item.get("value", "")) for item in matches[:5])
            raise ValueError(f"Memory description is ambiguous; matching facts: {choices}")
        removed = self.memory.forget_profile_memory(str(matches[0]["id"]))
        result = {"forgotten": True, "memory": removed}
        return result

    @tool('correct_personal_fact', 'Correct one unambiguous personal memory and mark the corrected value as user-confirmed.',
          {'query': {'type': 'string'}, 'new_value': {'type': 'string'}, 'category': {'type': 'string', 'enum': ['goal', 'interest', 'personal', 'preference', 'project', 'tool', 'work_style']}}, ['query', 'new_value'])
    def _tool_correct_personal_fact(self, name, args, approve, call):
        query = str(args["query"])
        matches = self.memory.find_profile_memories(query)
        if not matches:
            raise FileNotFoundError("No personal memory matches that description")
        if len(matches) != 1:
            choices = "; ".join(str(item.get("value", "")) for item in matches[:5])
            raise ValueError(f"Memory description is ambiguous; matching facts: {choices}")
        updated = self.memory.update_profile_memory(
            str(matches[0]["id"]), value=str(args["new_value"]),
            category=str(args.get("category") or matches[0].get("category", "personal")),
        )
        result = {"corrected": True, "memory": updated}
        return result

    @tool('list_personal_memory', 'Review the editable personal facts Aura currently remembers about the user.',
          {'query': {'type': 'string', 'default': ''}}, [])
    def _tool_list_personal_memory(self, name, args, approve, call):
        query = str(args.get("query", "")).strip()
        memories = (self.memory.find_profile_memories(query) if query
                    else self.memory.profile_memories())
        result = {"memories": memories[:100], "count": len(memories)}
        return result
