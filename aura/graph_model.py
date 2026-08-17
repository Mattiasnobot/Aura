from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True)
class MindNode:
    node_id: str
    label: str
    kind: str
    detail: str
    target: str | None = None
    #: Set on memory nodes only, so the interface can edit the one relationship
    #: here that is not derived from the data.
    memory_id: str | None = None
    project: str | None = None


@dataclass(frozen=True)
class MindEdge:
    source: str
    target: str


def _shorten(value: object, limit: int = 38) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _comparable(value: object) -> str:
    """A loose form for spotting the same fact written two ways.

    `tone = terse` in preferences and `tone: terse` learned in conversation are
    one fact, and the map should not claim Aura holds two.
    """
    return " ".join(
        "".join(character if character.isalnum() or character.isspace() else " "
                for character in str(value).casefold()).split()
    )


def build_mind_graph(memory: dict, tasks: list[dict], files: list[str],
                     max_files: int = 60) -> tuple[list[MindNode], list[MindEdge]]:
    """Build a bounded graph from Aura's real local state without reading file contents."""
    nodes: dict[str, MindNode] = {}
    edges: list[MindEdge] = []
    edge_keys: set[tuple[str, str]] = set()

    def add(node_id: str, label: str, kind: str, detail: str,
            target: str | None = None, memory_id: str | None = None,
            project: str | None = None) -> None:
        nodes.setdefault(node_id, MindNode(node_id, label, kind, detail, target,
                                           memory_id, project))

    def link(source: str, target: str) -> None:
        key = (source, target)
        if source in nodes and target in nodes and key not in edge_keys:
            edge_keys.add(key)
            edges.append(MindEdge(source, target))

    conversations = list(memory.get("conversation", []))
    preferences = dict(memory.get("preferences", {}))
    personal_memories = list(memory.get("profile_memories", []))
    add(
        "aura", "Aura", "aura",
        f"Local AI companion\n{len(files)} workspace files • {len(tasks)} recent tasks • "
        f"{len(preferences)} preferences • {len(personal_memories)} personal memories",
    )

    categories = [
        ("identity", "Identity"),
        ("preferences", "Preferences"),
        ("personal_memory", "What I know about you"),
        ("conversation", "Recent conversation"),
        ("tasks", "Recent tasks"),
        ("capabilities", "Tools used"),
        ("workspace", "Workspace"),
    ]
    for node_id, label in categories:
        add(node_id, label, "category", label)
        link("aura", node_id)

    name = str(memory.get("name") or "").strip()
    if name:
        add("person:name", name, "identity", f"Aura remembers the user's name as {name}.")
    else:
        add("person:name", "Name not set", "empty", "Aura has not been told the user's name yet.")
    link("identity", "person:name")

    remembered: dict[str, str] = {}
    if personal_memories:
        ordered_memories = sorted(
            personal_memories,
            key=lambda item: (bool(item.get("pinned")), str(item.get("updated", ""))),
            reverse=True,
        )
        for index, item in enumerate(ordered_memories[:24]):
            value = str(item.get("value", ""))
            category = str(item.get("category", "personal")).replace("_", " ")
            confidence = round(float(item.get("confidence", 0)) * 100)
            status = "user confirmed" if item.get("confirmed") else f"learned • {confidence}% confidence"
            node_id = f"personal:{item.get('id', index)}"
            kind = "personal_memory_pinned" if item.get("pinned") else "personal_memory"
            belongs_to = str(item.get("project") or "").strip()
            add(
                node_id, _shorten(value, 42), kind,
                f"{category.title()} • {status}\n{value}\n\nSource: {item.get('source', '')}\n"
                f"Updated: {item.get('updated', '')}"
                + (f"\nProject: {belongs_to}" if belongs_to else ""),
                memory_id=str(item.get("id", "")) or None,
                project=belongs_to or None,
            )
            link("personal_memory", node_id)
            if belongs_to:
                # Until now this was stored and never drawn: a fact tied to a
                # piece of work hung under Memory like any other, so the map
                # showed less than Aura actually knew.
                project_id = f"project:{_comparable(belongs_to)}"
                add(project_id, _shorten(belongs_to, 28), "project",
                    f"Project\n{belongs_to}")
                link(project_id, node_id)
            remembered[_comparable(value)] = node_id
    else:
        add(
            "personal:empty", "Still getting to know you", "empty",
            "Clear non-sensitive facts you share will appear here, and you can edit or forget them anytime.",
        )
        link("personal_memory", "personal:empty")

    if preferences:
        for index, (key, value) in enumerate(sorted(preferences.items())[:16]):
            # One fact, one node. A preference Aura also learned in conversation
            # is hung under both headings rather than drawn twice.
            existing = remembered.get(_comparable(f"{key} {value}"))
            if existing:
                link("preferences", existing)
                continue
            node_id = f"preference:{index}"
            add(
                node_id,
                f"{_shorten(key, 18)}: {_shorten(value, 24)}",
                "preference",
                f"Saved preference\n{key} = {value}",
            )
            link("preferences", node_id)
    else:
        add(
            "preference:empty", "No preferences yet", "empty",
            "Ask Aura to remember a preference and it will appear here.",
        )
        link("preferences", "preference:empty")

    recent_conversation = conversations[-10:]
    asked: dict[str, str] = {}
    if recent_conversation:
        previous_id: str | None = None
        for index, message in enumerate(recent_conversation):
            role = "Aura" if message.get("role") == "assistant" else "You"
            message_text = str(message.get("text", ""))
            node_id = f"conversation:{index}"
            if role == "You":
                asked.setdefault(_comparable(message_text), node_id)
            kind = "conversation_aura" if role == "Aura" else "conversation_user"
            add(
                node_id, f"{role}: {_shorten(message_text)}", kind,
                f"{role}\n{message_text}\n\n{message.get('time', '')}",
            )
            link("conversation", node_id)
            if previous_id:
                link(previous_id, node_id)
            previous_id = node_id
    else:
        add("conversation:empty", "No conversation yet", "empty", "New messages will appear here.")
        link("conversation", "conversation:empty")

    tool_nodes: dict[str, str] = {}
    if tasks:
        for index, task in enumerate(tasks[:10]):
            status = str(task.get("status") or "running").casefold()
            kind = "task_completed" if status == "completed" else (
                "task_error" if status in {"error", "cancelled"} else "task_running")
            # An empty request used to draw a nameless circle: the key exists,
            # so the dict default never applied.
            request = str(task.get("request") or "").strip()
            label = _shorten(request) if request else "Task with no request recorded"
            node_id = f"task:{index}:{task.get('task_id', index)}"
            add(
                node_id, label, kind,
                f"Status: {status}\nTask: {task.get('task_id', '')}\n\n"
                f"{request or 'No request text was recorded for this task.'}\n\n"
                f"{task.get('summary', '')}",
            )
            link("tasks", node_id)
            # The message that started it, rather than the same words twice.
            origin = asked.get(_comparable(request)) if request else None
            if origin:
                link(origin, node_id)
            for tool in dict.fromkeys(str(item) for item in task.get("tools", []) if item):
                if tool not in tool_nodes and len(tool_nodes) < 20:
                    tool_id = f"tool:{len(tool_nodes)}"
                    tool_nodes[tool] = tool_id
                    add(tool_id, tool.replace("_", " "), "tool", f"Aura capability: {tool}")
                    link("capabilities", tool_id)
                if tool in tool_nodes:
                    link(node_id, tool_nodes[tool])
    else:
        add("task:empty", "No tasks yet", "empty", "Completed Aura tasks will appear here.")
        link("tasks", "task:empty")

    if not tool_nodes:
        add("tool:empty", "No tools used yet", "empty", "Tools appear after Aura acts on a task.")
        link("capabilities", "tool:empty")

    visible_files = sorted(files)[:max_files]
    for file_path in visible_files:
        parts = PurePosixPath(file_path).parts
        parent_id = "workspace"
        accumulated: list[str] = []
        for part in parts[:-1]:
            accumulated.append(part)
            relative = "/".join(accumulated)
            folder_id = f"folder:{relative}"
            add(folder_id, part, "folder", f"Workspace folder\n{relative}", relative)
            link(parent_id, folder_id)
            parent_id = folder_id
        file_id = f"file:{file_path}"
        add(file_id, parts[-1], "file", f"Workspace file\n{file_path}", file_path)
        link(parent_id, file_id)
    if not visible_files:
        add("file:empty", "Workspace is empty", "empty", "Projects and files will appear here.")
        link("workspace", "file:empty")
    elif len(files) > len(visible_files):
        hidden = len(files) - len(visible_files)
        add(
            "file:more", f"+ {hidden} more files", "empty",
            f"The map is capped for readability. {hidden} additional files are still safely stored.",
        )
        link("workspace", "file:more")

    return list(nodes.values()), edges
