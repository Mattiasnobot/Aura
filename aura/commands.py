from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .action_log import ActionLog
from .safety import WorkspaceSandbox


@dataclass
class CommandResult:
    command: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    approved: bool
    timed_out: bool = False
    blocked: bool = False

    @property
    def succeeded(self) -> bool:
        """A command is successful only when it was approved, finished, and exited cleanly."""
        return self.approved and not self.timed_out and self.returncode == 0


class CommandAgent:
    FILE_OPERATION_COMMANDS = {
        "cat", "copy", "cp", "del", "erase", "mkdir", "move", "mv", "rd", "rm",
        "rmdir", "touch", "type",
    }

    def __init__(self, sandbox: WorkspaceSandbox, log: ActionLog) -> None:
        self.sandbox = sandbox
        self.log = log

    def _workspace_targets(self, values: list[str]) -> bool:
        targets = [value for value in values if value and not value.startswith("-")]
        if not targets:
            return False
        try:
            for target in targets:
                self.sandbox.path(target)
        except (ValueError, OSError):
            return False
        return True

    def is_auto_approved(self, command: list[str], autonomy: str = "balanced") -> bool:
        """Approve only commands that cannot execute workspace code.

        Powerful mode broadens static inspection and validation. Programs,
        package scripts, installers, and project runtimes still go through the
        UI because merely running them can reach outside the workspace.
        """
        if not command:
            return False
        executable = Path(command[0]).name.casefold()
        python_names = {"python", "python.exe", "python3", "python3.exe", Path(sys.executable).name.casefold()}
        args = command[1:]
        version_names = python_names | {"node", "node.exe", "npm", "npm.cmd", "git", "git.exe"}
        if executable in version_names and args in (["--version"], ["-V"]):
            return True
        if executable not in python_names:
            if autonomy == "powerful" and executable in {"node", "node.exe"} and len(args) == 2 and args[0] == "--check":
                return self._workspace_targets(args[1:])
            return False
        if len(args) >= 3 and args[:2] == ["-m", "compileall"]:
            targets = [arg for arg in args[2:] if arg != "-q"]
            return bool(targets) and self._workspace_targets(targets)
        if autonomy == "powerful" and len(args) >= 3 and args[:2] == ["-m", "py_compile"]:
            return self._workspace_targets(args[2:])
        if autonomy == "powerful" and len(args) == 3 and args[:2] == ["-m", "json.tool"]:
            return self._workspace_targets(args[2:])
        return False

    def run(
        self,
        command: list[str],
        approve: Callable[[list[str]], bool] | None = None,
        timeout: float = 15,
        autonomy: str = "balanced",
    ) -> CommandResult:
        if not command:
            error = "Command cannot be empty."
            self.log.record("command", "blocked", command=command, error=error)
            return CommandResult(command, None, "", error, False, blocked=True)
        executable = Path(command[0]).name.casefold()
        if executable in self.FILE_OPERATION_COMMANDS:
            error = ("Workspace file and folder operations must use Aura's file tools; "
                     f"{executable!r} is not allowed through run_command.")
            self.log.record("command", "blocked", command=command, error=error)
            return CommandResult(command, None, "", error, False, blocked=True)
        approved = self.is_auto_approved(command, autonomy) or bool(approve and approve(command))
        if not approved:
            self.log.record("command", "denied", command=command)
            return CommandResult(command, None, "", "User approval required or denied.", False)
        resolved = list(command)
        python_aliases = {"python", "python.exe", "python3", "python3.exe"}
        if resolved and Path(resolved[0]).name.casefold() in python_aliases and not getattr(sys, "frozen", False):
            resolved[0] = sys.executable
        self.log.record("command", "started", command=command, resolved_command=resolved)
        try:
            completed = subprocess.run(
                resolved,
                cwd=self.sandbox.root,
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=timeout,
                shell=False,
                check=False,
            )
            status = "ok" if completed.returncode == 0 else "error"
            self.log.record("command", status, command=command, resolved_command=resolved,
                            returncode=completed.returncode,
                            stdout=completed.stdout[-4000:], stderr=completed.stderr[-4000:])
            return CommandResult(command, completed.returncode, completed.stdout, completed.stderr, True)
        except subprocess.TimeoutExpired as exc:
            self.log.record("command", "timeout", command=command, timeout=timeout)
            stdout = exc.stdout or ""
            stderr = exc.stderr or "Command timed out."
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            return CommandResult(command, None, stdout, stderr, True, True)
        except OSError as exc:
            # "[WinError 2] The system cannot find the file specified" names
            # nothing — not the program, not the cause — and a model reading it
            # concludes the capability is missing rather than the executable.
            # Fourteen of the sixteen recorded command failures were this.
            error = self._missing_program(resolved, exc)
            self.log.record("command", "error", command=command,
                            error=error, raw_error=str(exc))
            return CommandResult(command, None, "", error, True)

    #: Unix habits that simply are not present on a stock Windows box. Named so
    #: the model stops reaching for them rather than retrying with a flag change.
    WINDOWS_HAS_NO = {
        "bash": "There is no bash on this machine.",
        "sh": "There is no sh on this machine.",
        "zsh": "There is no zsh on this machine.",
        "grep": "Use the search_text tool instead of grep.",
        "sed": "Use apply_edits or replace_in_file instead of sed.",
        "awk": "Use read_file and work on the text instead of awk.",
        "find": "Use search_files instead of find.",
        "ls": "Use the list_files tool instead of ls.",
        "which": "Use system_info instead of which.",
        "apply_patch": "Use the apply_edits tool; there is no apply_patch here.",
        "patch": "Use the apply_edits tool; there is no patch program here.",
    }

    def _missing_program(self, resolved: list[str], exc: OSError) -> str:
        """Say which program was missing, and what to use instead."""
        if getattr(exc, "winerror", None) != 2 and not isinstance(exc, FileNotFoundError):
            return str(exc)
        program = Path(resolved[0]).name.casefold().removesuffix(".exe") if resolved else ""
        known = self.WINDOWS_HAS_NO.get(program)
        if known:
            return f"{program!r} is not installed on this machine. {known}"
        return (f"{program!r} is not installed on this machine, or is not on PATH. "
                f"This says nothing about your tools — those are always available. "
                f"Use one of them, or a program you have confirmed exists.")
