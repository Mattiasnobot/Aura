"""Turning a raw exception into something the model can actually do next.

Every tool failure Aura has ever shown the model is `str(exc)`. That is one line
in `_execute_tool`, and it is why the log reads the way it does:

    [Errno 13] Permission denied: 'C:\\Users\\Mattias\\...\\aura-workspace\\shop'
    [Errno 2] No such file or directory: 'C:\\Users\\...\\shop\\js\\main_bundle.js'

Both are misleading in the same expensive way. The first is not a permission
problem at all — `shop` is a folder, and Windows raises `PermissionError` rather
than `IsADirectoryError` when you open one, so the message names the one cause
that is definitely wrong. The second is a typo for `main.bundle.js`, sitting in
the very folder being read, and nothing said so.

Neither tells the model what to do, and an agent that cannot tell "you used the
wrong tool" from "you are not allowed" concludes it lacks a capability it has.
Aura then spends the rest of the turn working around a door that was never
locked.

The shape is taken from `error_classifier.py` in NousResearch's hermes-agent
(MIT, © 2025 Nous Research): classify the failure, and let the classification
name the recovery rather than leaving the caller to parse a string. Theirs sorts
API failures into retry / rotate / fall back / compress; Aura has no providers to
rotate, so the recovery here is which tool to reach for instead.

Two rules throughout. Host paths never leave this module — the model thinks in
workspace-relative paths and an absolute one is both confusing and a detail of
Mat's machine. And an exception this does not recognise is passed through
unchanged, because a wrong explanation is worse than a raw one.
"""

from __future__ import annotations

import difflib
from pathlib import Path

#: What to reach for when a tool was used on the wrong kind of thing. Keyed by
#: the tool that failed, because "read a folder" and "write a folder" want
#: different advice.
_FOLDER_ADVICE = {
    "read_file": "Use list_files to see what is inside it, then read a file by name.",
    "read_many_files": "list_files first, then pass the individual file paths.",
    "read_external_file": "Use list_external_folder to see what is inside it.",
    "apply_edits": "Name the file to edit, not the folder containing it.",
    "replace_in_file": "Name the file to edit, not the folder containing it.",
    "write_file": "Name a file inside it, not the folder itself.",
    "file_info": "Use list_files for a folder; file_info measures one file.",
}

#: Exceptions Aura raises deliberately, whose message is already the answer.
#: Matched by name rather than by import so this module keeps no dependency on
#: the ones that raise them — and so a new refusal type is one line, not a
#: circular import waiting to happen.
_AURA_REFUSALS = {"PermissionDenied", "PermissionRefused", "SandboxViolation",
                  "AuraError"}

_EXISTS_ADVICE = {
    "create_file": "Use write_file to replace it, or apply_edits to change part of it.",
    "create_folder": "It is already there — carry on and put files in it.",
}


def explain(exception: BaseException, tool: str, arguments: dict,
            workspace_root: Path | str | None = None) -> str:
    """The most useful true sentence about this failure.

    Falls through to `str(exception)` whenever the cause is not recognised,
    which is the behaviour every tool had before this existed.
    """
    root = Path(workspace_root) if workspace_root else None
    raw = _without_host_paths(str(exception), root)
    named = _named_path(arguments)

    # Aura's own refusals are already the considered sentence — they say which
    # capability is missing and where. Rewriting one is how `list_external_folder`
    # came to answer "that is a folder, not a file" about a folder it exists to
    # list: `PermissionDenied` subclasses `PermissionError`, so the branch below
    # caught a message that was never about the filesystem at all.
    if type(exception).__name__ in _AURA_REFUSALS:
        return raw

    if isinstance(exception, FileExistsError):
        advice = _EXISTS_ADVICE.get(tool, "")
        return f"{named or raw} already exists. {advice}".strip()

    if isinstance(exception, IsADirectoryError):
        return _folder_message(named or raw, tool)

    # Windows raises PermissionError for a folder opened as a file, so the
    # errno alone would send the model looking for a permissions problem that
    # does not exist. Ask the filesystem which it is before saying anything.
    if isinstance(exception, PermissionError):
        if named and root and (root / named).is_dir():
            return _folder_message(named, tool)
        # Not every PermissionError is about a file. Aura raises the same class
        # when a domain has not been granted, and rewriting *that* as "the file
        # is locked" throws away a security decision and tells the model to
        # retry something it must not. Only a failure carrying a real filename
        # came from the filesystem; anything else is passed through untouched.
        if getattr(exception, "filename", None):
            return (f"{named or 'That path'} could not be opened — it is locked or "
                    f"in use by another program. Close it and try again, or work "
                    f"on a different file.")
        return raw

    if isinstance(exception, FileNotFoundError):
        if not named:
            return raw
        suggestion = _did_you_mean(named, root)
        if suggestion:
            return (f"{named} does not exist. Did you mean {suggestion}? "
                    f"Use list_files to be sure.")
        return (f"{named} does not exist in the workspace. Use list_files to see "
                f"what is there before reading or editing it.")

    if isinstance(exception, UnicodeDecodeError):
        return (f"{named or 'That file'} is not text — it cannot be read or "
                f"edited as characters. file_info will report its type and size.")

    return raw


def _folder_message(path: str, tool: str) -> str:
    advice = _FOLDER_ADVICE.get(tool, "Name a file rather than a folder.")
    return f"{path} is a folder, not a file. {advice}"


#: The argument names that carry a path, in the order a tool is likely to mean
#: them. `paths` is last because a list names several and the message names one.
_PATH_KEYS = ("path", "file", "relative", "target", "source", "destination",
              # Tools that compare or unpack name their operands differently,
              # and missing one meant the message fell back to the raw
              # exception — the very thing this module exists to replace.
              # Found by calling every tool once: `compare_files` uses
              # `left`/`right` and got no explanation at all.
              "left", "right", "first", "second", "archive", "paths")


def _named_path(arguments: dict) -> str:
    for key in _PATH_KEYS:
        value = (arguments or {}).get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (list, tuple)) and value:
            first = value[0]
            if isinstance(first, str) and first.strip():
                return first.strip()
    return ""


def _did_you_mean(path: str, root: Path | None) -> str:
    """A real neighbouring filename, when the miss looks like a typo.

    From the log: `shop/js/main_bundle.js` was asked for while
    `shop/js/main.bundle.js` sat beside it. One underscore, a failed turn, and
    nothing pointing at the answer.
    """
    if not root:
        return ""
    try:
        folder = (root / path).parent
        if not folder.is_dir():
            return ""
        names = [item.name for item in folder.iterdir() if item.is_file()]
    except OSError:
        return ""
    close = difflib.get_close_matches(Path(path).name, names, n=1, cutoff=0.7)
    if not close:
        return ""
    relative = Path(path).parent / close[0]
    return relative.as_posix()


def _without_host_paths(message: str, root: Path | None) -> str:
    """Strip Mat's directory layout out of anything shown to the model."""
    if not root:
        return message
    for form in {str(root), str(root).replace("\\", "/"),
                 str(root).replace("\\", "\\\\")}:
        if form:
            message = message.replace(form + "\\\\", "").replace(form + "\\", "")
            message = message.replace(form + "/", "")
            message = message.replace(form, "the workspace")
    # An escaped path in a repr leaves its separators doubled, and stripping the
    # root off the front leaves the rest starting with one. Both read as noise:
    # "'\\site\\\\one.txt'" is the same file as "site/one.txt" and harder to act on.
    message = message.replace("\\\\", "/").replace("\\", "/")
    return message.replace("'/", "'").replace('"/', '"')
