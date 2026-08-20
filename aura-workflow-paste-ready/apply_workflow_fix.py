from __future__ import annotations

from pathlib import Path
import shutil

ROOT = Path.cwd()
PACKAGE = Path(__file__).resolve().parent

CHANGES: dict[str, list[tuple[str, str]]] = {
    "aura/agent.py": [
        (
'''        self.config.update(current_session=self.session_id)\n        self.memory.data["conversation"] = [dict(item) for item in messages]\n        self.memory.save()\n        self.log.record("open_session", "ok", session_id=self.session_id,\n                        messages=len(messages))\n        return messages\n''',
'''        self.config.update(current_session=self.session_id)\n        self.memory.data["conversation"] = [dict(item) for item in messages]\n        self.memory.save()\n        # `current_project` is conversation state. Leaving the project from the\n        # previously open conversation here means a short follow-up such as\n        # "continue" can pick up the wrong project's plan and memories. Rebuild\n        # it from the most recent user turn in this conversation that actually\n        # names a project.\n        self.current_project = None\n        for item in reversed(messages):\n            if item.get("role") != "user":\n                continue\n            project = self.detect_project(str(item.get("text") or ""))\n            if project:\n                self.current_project = project\n                break\n        self.log.record("open_session", "ok", session_id=self.session_id,\n                        messages=len(messages))\n        return messages\n'''),
        (
'''        if not approve(["PLAN", plan, kind]):\n            self.log.record("file_plan", "declined", files=len(listed))\n            return self.PLAN_DECLINED\n        # Keep what was agreed. A plan shown once and discarded leaves the next\n''',
'''        if not approve(["PLAN", plan, kind]):\n            self.log.record("file_plan", "declined", files=len(listed))\n            return self.PLAN_DECLINED\n        # Once the user approves a first project plan, Python owns the workflow\n        # state. Do not depend on the model remembering to call\n        # `record_plan_steps` in a later round just to make the agreed plan\n        # resumable. Existing durable steps win: a later file-plan approval for\n        # an established project must not replace work already in progress.\n        if project and not self.plan_text(project):\n            try:\n                if not self.db.plan_steps(project):\n                    self.db.set_plan_steps(project, listed[:20])\n                    self.log.record("plan_steps", "ok", project=project,\n                                    steps=len(listed[:20]), agreed=True)\n            except Exception as exc:\n                self._note_bookkeeping_failure("plan_steps", project, exc)\n        # Keep what was agreed. A plan shown once and discarded leaves the next\n'''),
        (
'''            if round_index:\n                # Any second or later round replaces whatever was streamed\n                # before it. Clearing here — rather than at each individual\n                # retry — means no future retry path can reintroduce the\n                # duplicated-answer bug by forgetting to signal.\n                state("retry")\n            # A conversation that ends with Aura's own reply is not a question, and\n''',
'''            # A conversation that ends with Aura's own reply is not a question, and\n'''),
        (
'''            if response.tool_calls:\n                if response.content and token:\n                    token('\\n\\n')\n                state("working")\n''',
'''            if response.tool_calls:\n                if response.content and token:\n                    # Some models narrate before emitting a tool call. That text\n                    # is intermediate, not the final answer, so discard only\n                    # that visible stream. A normal tool continuation with no\n                    # visible text is not a retry and should not look like one.\n                    state("stream_reset")\n                state("working")\n'''),
        (
'''            if retry is not None:\n                if retry.notice and token:\n''',
'''            if retry is not None:\n                # This is the actual retry boundary. The old loop emitted\n                # `retry` for every second-or-later model round, including\n                # healthy continuation after a successful tool call.\n                state("retry")\n                if retry.notice and token:\n'''),
        (
'''        for step in steps:\n            if step["status"] == "done" or step["id"] in turn.plan_steps_recorded:\n                continue\n''',
'''        for step in steps:\n            if (step["status"] in {"done", "blocked"}\n                    or step["id"] in turn.plan_steps_recorded):\n                continue\n'''),
    ],
    "aura/web_bridge.py": [
        (
'''    def _on_agent_state(self, name: str) -> None:\n        """Forward agent states, translating the retry signal for the interface.\n\n        "retry" is not a mood: it means the reply streamed so far is being\n        thrown away, so the browser must clear it rather than append the next\n        attempt underneath.\n        """\n        if name == "retry":\n            self._push("stream_reset")\n            self._push("state", value="working")\n            return\n''',
'''    def _on_agent_state(self, name: str) -> None:\n        """Forward agent states, translating stream-discard signals for the UI.\n\n        A real gate retry and narrated text that precedes a tool call both\n        abandon the visible partial answer. Ordinary post-tool continuation does\n        neither, so it must not flash as a retry or reset an already-clean stream.\n        """\n        if name in {"retry", "stream_reset"}:\n            self._push("stream_reset")\n            self._push("state", value="working")\n            return\n'''),
        (
'''        def on_token(piece: str) -> None:\n            streamed.append(piece)\n            self._push("stream_token", text=piece)\n\n        try:\n''',
'''        def on_token(piece: str) -> None:\n            streamed.append(piece)\n            self._push("stream_token", text=piece)\n\n        def on_state(name: str) -> None:\n            if name in {"retry", "stream_reset"}:\n                streamed.clear()\n            self._on_agent_state(name)\n\n        try:\n'''),
        (
'''                    approve=self._approve_command,\n                    state=self._on_agent_state,\n                    token=on_token,\n''',
'''                    approve=self._approve_command,\n                    state=on_state,\n                    token=on_token,\n'''),
    ],
    "tests/test_aura.py": [
        (
'''        # The verification nudge used to stream only blank lines and then repeat\n        # the whole answer, so the reply appeared two or three times. The reset\n        # is emitted once per extra round, so no retry path can miss it.\n''',
'''        # The verification nudge used to stream only blank lines and then repeat\n        # the whole answer, so the reply appeared two or three times. Keep the\n        # reset for a real completion-gate retry, but ordinary continuation after\n        # a successful tool is not itself a retry.\n'''),
        (
'''        # One reset for each round after the first.\n        self.assertEqual(states.count("retry"), provider.complete.call_count - 1)\n''',
'''        # Only the validation nudge is a retry. The two post-tool continuations\n        # should move forward without flashing/resetting as failed attempts.\n        self.assertEqual(states.count("retry"), 1)\n'''),
    ],
}

# Preflight every edit before writing anything.
loaded: dict[str, str] = {}
for relative, replacements in CHANGES.items():
    target = ROOT / relative
    if not target.is_file():
        raise SystemExit(f"Missing expected file: {relative}")
    text = target.read_text(encoding="utf-8")
    loaded[relative] = text
    for index, (old, _new) in enumerate(replacements, 1):
        count = text.count(old)
        if count != 1:
            raise SystemExit(
                f"Refusing to edit {relative}: replacement {index} expected one exact "
                f"match, found {count}. Your checkout differs from the Aura version "
                "this fix was prepared for. No files were changed."
            )

new_test = ROOT / "tests/test_workflow_regressions.py"
source_test = PACKAGE / "tests/test_workflow_regressions.py"
if not source_test.is_file():
    raise SystemExit("Package is incomplete: missing tests/test_workflow_regressions.py")
if new_test.exists() and new_test.read_text(encoding="utf-8") != source_test.read_text(encoding="utf-8"):
    raise SystemExit(
        "tests/test_workflow_regressions.py already exists with different content. "
        "No files were changed."
    )

for relative, replacements in CHANGES.items():
    target = ROOT / relative
    backup = target.with_suffix(target.suffix + ".workflow-fix.bak")
    if not backup.exists():
        shutil.copy2(target, backup)
    text = loaded[relative]
    for old, new in replacements:
        text = text.replace(old, new, 1)
    target.write_text(text, encoding="utf-8")
    print(f"updated {relative}")

if not new_test.exists():
    new_test.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_test, new_test)
    print("created tests/test_workflow_regressions.py")

print("\nWorkflow fix applied successfully.")
print("Backups use the suffix .workflow-fix.bak")
print("Recommended test: python -m unittest tests.test_workflow_regressions")
