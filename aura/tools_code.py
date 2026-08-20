"""The `execute_code` tool: several tool calls and the logic between them, in one turn.

What this is for is context. Reading ten files one call at a time costs 4,710
tokens of a 66,816-token window and leaves them there for the rest of the
conversation; the same ten read inside a script cost whatever the script prints.
The turns it collapses are the ones with a shape — read many, filter, act on
some — which are exactly the turns Aura runs out of room on.

**Which tools a script may call.** Everything that reads or edits the workspace,
and nothing that reaches outside it or asks Mat a question. `run_command` is
excluded on purpose: it is the one tool that stops and asks for approval, and a
script calling it would turn a considered "yes" into something Mat approves once
and cannot see again. A script that genuinely needs a shell can use `subprocess`
directly and take that up with him, which is at least honest about what it is.

`execute_code` cannot call itself either, for the ordinary reason.
"""

from __future__ import annotations

from . import code_exec, services, toolkit
from .toolkit import tool

#: Not callable from a script, each for its own reason.
#: - `run_command` carries the approval prompt, and approving a script is not
#:   approving every command it might run.
#: - `execute_code` would recurse.
#: - the external-file tools reach outside the workspace on a folder grant Mat
#:   gave for a task he was watching.
#: - `open_workspace_item` and `capture_page` put something on his screen.
WITHHELD = frozenset({
    "execute_code", "run_command",
    "read_external_file", "write_external_file", "list_external_folder",
    "open_workspace_item", "capture_page", "look_at_image",
    "set_reminder", "set_check",
})


def script_tools() -> dict[str, str]:
    """Tool name to one-line description, for the generated stub.

    Services are included alongside the ordinary tools. They live in a separate
    registry only because they were added without touching the tool loop, which
    is a fact about how they were built rather than a reason a script should not
    call one — `get_weather` in a loop over towns is precisely this tool's case.
    """
    available = {name: spec.description
                 for name, spec in toolkit.REGISTRY.items()}
    available.update({service.name: service.description
                      for service in services.services()})
    return {name: description for name, description in available.items()
            if name not in WITHHELD}


class CodeTools:
    """Running a script the model wrote, with Aura's own tools inside it."""

    #: Long enough for real work over many files, short enough that a runaway
    #: script is noticed in the same minute rather than the next one.
    SCRIPT_TIMEOUT = 120.0
    SCRIPT_TOOL_CALLS = 50

    @tool('execute_code',
          'Run a Python script that calls your own tools, for work that needs '
          'several steps with logic between them — reading many files and '
          'filtering them, making the same edit across a set, or working through '
          'search results one at a time. Import them with "from aura_tools import read_file, '
          'write_file, search_text". Only what the script prints comes back to '
          'you, so print your conclusion; the intermediate results never reach '
          'the conversation, which is the reason to use this. Use an ordinary '
          'tool call for anything that is one step.',
          {'script': {'type': 'string',
                      'description': 'Python source. Print the result you want to read.'},
           'purpose': {'type': 'string',
                       'description': 'One line on what it does, shown to the user.'}},
          ['script'], mutating=True)
    def _tool_execute_code(self, name, args, approve, call):
        script = str(args.get("script", ""))
        if not script.strip():
            raise ValueError("script cannot be empty")
        purpose = str(args.get("purpose", "")).strip()

        def dispatch(tool_name: str, arguments: dict) -> dict:
            # The same path a direct tool call takes, deliberately: whatever
            # approvals, snapshots and logging apply there apply here, because
            # it is not a second implementation.
            from .provider import ToolCall
            return self._execute_tool(
                ToolCall(f"{call.id if call else 'script'}:{tool_name}",
                         tool_name, arguments), approve)

        result = code_exec.run_script(
            script, dispatch=dispatch, tools=script_tools(),
            workspace=self.sandbox.root,
            timeout=self.SCRIPT_TIMEOUT, max_calls=self.SCRIPT_TOOL_CALLS)

        # `outcome`, not `status`: the log's own second argument is called that,
        # and passing both is a TypeError rather than a merge.
        self.log.record("execute_code", "ok" if result.status == "ok" else "error",
                        purpose=purpose or None, outcome=result.status,
                        tool_calls=result.tool_calls,
                        tools_used=sorted(set(result.tools_used)),
                        seconds=round(result.seconds, 1),
                        characters=len(script))
        payload = {"status": result.status, "output": result.output,
                   "tool_calls": result.tool_calls,
                   "tools_used": sorted(set(result.tools_used)),
                   "seconds": round(result.seconds, 1)}
        if result.status != "ok":
            payload["ok"] = False
            payload["error"] = result.error or f"the script ended: {result.status}"
        return payload
