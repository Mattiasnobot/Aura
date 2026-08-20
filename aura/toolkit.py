"""One place per tool: its name, its schema, and the code that runs it.

Before this, `tool_definitions()` declared 48 tools in one method and
`_execute_tool` dispatched them in a 46-branch chain somewhere else, so the two
could drift apart with nothing to notice. A tool is now declared where it is
implemented, and both the model-facing schema and the dispatch table are derived
from the same declaration.

Usage inside `AuraAgent`:

    @tool("list_files", "List files recursively inside a workspace folder.",
          {"path": {**PATH, "default": "."}})
    def _tool_list_files(self, args, approve):
        return {"files": self.sandbox.list_files(str(args.get("path", ".")))}

The handler returns the result payload only. Logging, task recording, and error
handling stay in one wrapper around every tool, which is what keeps a new tool
from quietly skipping the audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


#: The schema fragment nearly every workspace tool repeats.
PATH = {"type": "string",
        "description": "Workspace-relative path; never absolute and never use .."}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    properties: dict
    required: tuple[str, ...]
    handler: Callable
    #: Tools that mutate the workspace, used by the completion gates.
    mutating: bool = False

    def definition(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": dict(self.properties),
                           "required": list(self.required),
                           "additionalProperties": False}}}


#: Declaration order is preserved so the model sees a stable tool list.
REGISTRY: dict[str, ToolSpec] = {}


def tool(name: str, description: str, properties: dict | None = None,
         required: list[str] | None = None, *, mutating: bool = False) -> Callable:
    """Declare a tool and register its handler in one place."""

    def decorate(handler: Callable) -> Callable:
        if name in REGISTRY:
            raise ValueError(f"Tool {name!r} is declared twice.")
        REGISTRY[name] = ToolSpec(name=name, description=description,
                                  properties=dict(properties or {}),
                                  required=tuple(required or ()),
                                  handler=handler, mutating=mutating)
        return handler

    return decorate


def definitions() -> list[dict]:
    return [spec.definition() for spec in REGISTRY.values()]


def get(name: str) -> ToolSpec | None:
    return REGISTRY.get(str(name))


def mutating_names() -> set[str]:
    return {spec.name for spec in REGISTRY.values() if spec.mutating}
