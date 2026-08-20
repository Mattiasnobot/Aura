"""The machine itself, commands, and things scheduled for later.

Lifted out of `agent.py`, where 516 lines of handlers sat interleaved with the
turn loop and the completion gates. These are leaf code: each does one thing and
returns a result. They stay methods because they really do use `self.sandbox`,
`self.memory` and `self.config` — a mixin keeps every `self.` resolving exactly
as it did, which is what makes this a move and not a rewrite.
"""

from __future__ import annotations

from . import checks
import pathlib

from . import toolkit
from .toolkit import tool
from datetime import datetime, timedelta, timezone
import os
import platform
import shutil


class SystemTools:
    """The machine itself, commands, and things scheduled for later."""

    @tool('run_command', 'Run an actual program, test, build, or project runtime inside the workspace. Commands use a direct argument array with no shell. Never use this for file/folder operations; create_file and write_file create parent folders. Use python for Python; unsafe commands require approval.',
          {'command': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 1}, 'timeout': {'type': 'number', 'minimum': 1, 'maximum': 60, 'default': 15}}, ['command'])
    def _tool_run_command(self, name, args, approve, call):
        command = args["command"]
        if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
            raise ValueError("command must be an array of strings")
        # Refused before approval, so Mat is never asked to authorise a mistake.
        # The message names the tool, because the failure this prevents is not the
        # command failing — it is what she concluded from the failure. "The system
        # cannot find the executable" reads as "this capability does not exist".
        program = pathlib.PurePath(command[0]).name.casefold() if command else ""
        program = program.removesuffix(".exe")
        wrapped = _tool_invoked_as_program(command)
        if toolkit.get(program) is not None:
            wrapped = program
        if wrapped:
            return {"ok": False, "refused": True, "tool": wrapped,
                    "error": (f"{wrapped!r} is one of your tools, not a program on this "
                              f"machine. Call the {wrapped} tool directly. It exists and "
                              f"is available — a command failing says nothing about which "
                              f"tools you have.")}
        timeout = max(1.0, min(float(args.get("timeout", 15)), 60.0))
        run = self.commands.run(
            command, approve=approve, timeout=timeout,
            autonomy=str(self.config.data.get("autonomy_mode", "balanced")),
        )
        result = {"approved": run.approved, "returncode": run.returncode,
                  "stdout": run.stdout[-20_000:], "stderr": run.stderr[-20_000:],
                  "timed_out": run.timed_out, "blocked": run.blocked}
        result["ok"] = run.succeeded
        if not run.succeeded:
            if run.blocked:
                reason = run.stderr or "Command is blocked by Aura's workspace policy."
            elif not run.approved:
                reason = "Command was not approved."
            elif run.timed_out:
                reason = "Command timed out."
            elif run.returncode is None:
                reason = run.stderr or "Command could not be started."
            else:
                reason = run.stderr.strip() or f"Command exited with code {run.returncode}."
            result["error"] = reason
        return result

    @tool('system_info', 'Inspect non-sensitive local runtime facts such as OS, Python, CPU count, and workspace disk space.',
          {}, [])
    def _tool_system_info(self, name, args, approve, call):
        disk = shutil.disk_usage(self.sandbox.root)
        result = {"os": platform.platform(), "python": platform.python_version(),
                  "architecture": platform.machine(), "cpu_count": os.cpu_count(),
                  "workspace": str(self.sandbox.root),
                  "workspace_disk": {"total": disk.total, "used": disk.used, "free": disk.free}}
        return result

    @tool('capability_summary', "List Aura's currently available tools and autonomy policy.",
          {}, [])
    def _tool_capability_summary(self, name, args, approve, call):
        result = {"tools": [item["function"]["name"] for item in self.tool_definitions()],
                  "tool_count": len(self.tool_definitions()),
                  "reasoning_depth": self.config.data.get("reasoning_depth"),
                  "autonomy_mode": self.config.data.get("autonomy_mode"),
                  "workspace_only": True,
                  "approval_policy": "Safe local tools are automatic; executable code, external HTTP, and desktop launches ask first."}
        return result

    @tool('self_check',
          'Check whether everything Aura depends on is working: the model server, the '
          'loaded model, images, the workspace, storage, speech, voice input, and search. '
          'Read-only. Use it when the user asks whether something is broken.',
          {}, [])
    def _tool_self_check(self, name, args, approve, call):
        from . import health
        return health.run(self)

    @tool('how_i_have_been_running',
          'Measure your own recent behaviour from Aura\'s action log: how long turns '
          'really take, which tools you actually use, and what has been failing. Use '
          'this before answering any question about your own speed, habits or '
          'shortcomings, instead of estimating.',
          {'days': {'type': 'integer', 'description': 'How many days back to look, 1-30.',
                    'default': 7}})
    def _tool_how_i_have_been_running(self, name, args, approve, call):
        """Facts about Aura, for Aura.

        Written because she was asked what could be improved about herself and
        answered "~100-500ms for simple queries" — measured, her median turn is 24
        seconds and her worst was 985. She also asked for the ability to read several
        files at once, which she had used earlier that day, and listed three
        background projects that do not exist.

        None of that was dishonesty. She had no way to look. Everything she was
        guessing at was already in `aura.db`, so this hands it over.

        Deterministic and model-free, for the reason `checks.py` gives: a measurement
        that could hallucinate is not a measurement.
        """
        # `or 7` would turn an explicit 0 into a week, because zero is falsy.
        asked = args.get("days")
        days = max(1, min(int(asked if asked is not None else 7), 30))
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        return self._behaviour_report(since, days)

    #: What each internal name actually means. Handed `request` and `empty_response`
    #: as bare identifiers, Aura glossed them herself — "API-limiid" on a model with
    #: no API, and "the tool restarted" for a silence. The invention filled a vacuum
    #: left by a label that means nothing outside this codebase.
    FAILURE_NAMES = {
        "request": "a turn that ended without a usable answer",
        "empty_response": "the model returning nothing at all",
        "command": "a command that would not run",
        "tool_markup": "the model writing a tool call out as text",
        "wrong_language": "an answer that came back in the wrong language",
        "search_service": "the local search service being unreachable",
        "read_file": "a file that could not be read",
        "create_file": "a file that could not be written",
        "read_many_files": "a batch of files that could not be read",
    }

    @classmethod
    def _plainly(cls, name: str) -> str:
        known = cls.FAILURE_NAMES.get(name)
        return f"{known} (`{name}`)" if known else f"`{name}`"

    def _behaviour_report(self, since: str, days: int) -> dict:
        import statistics
        rows = self.db._query(
            "SELECT time, role FROM messages WHERE time > ? ORDER BY time", (since,))
        # A turn is a user message answered by the next assistant message.
        waits: list[float] = []
        for first, second in zip(rows, rows[1:]):
            if first["role"] != "user" or second["role"] != "assistant":
                continue
            try:
                started = datetime.fromisoformat(first["time"])
                ended = datetime.fromisoformat(second["time"])
            except ValueError:
                continue
            waits.append((ended - started).total_seconds())
        waits.sort()

        def at(fraction: float) -> int:
            return int(waits[min(len(waits) - 1, int(len(waits) * fraction))]) if waits else 0

        counted: dict[str, int] = {}
        failures: dict[str, int] = {}
        for row in self.db._query(
                "SELECT action, status FROM actions WHERE time > ?", (since,)):
            action, status = str(row["action"]), str(row["status"])
            if status == "error":
                failures[action] = failures.get(action, 0) + 1
            elif action not in {"request", "new_session"}:
                counted[action] = counted.get(action, 0) + 1
        busiest = sorted(counted, key=lambda k: -counted[k])[:8]

        if not waits:
            return {"finding": f"Nothing was measured in the last {days} days — "
                               f"there are no completed turns in that window.",
                    "note": "Say this as it stands."}

        slow = sum(1 for wait in waits if wait > 120)
        ranked = sorted(failures.items(), key=lambda kv: -kv[1])
        worst_kind = (f"The most common failure was {self._plainly(ranked[0][0])}, "
                      f"{ranked[0][1]} times" if ranked else "Nothing failed")
        others = ", ".join(f"{self._plainly(name)} {count}"
                           for name, count in ranked[1:4])

        # Composed here rather than handed over as parts. Every number appears once,
        # already attached to the thing it measures, so there is nothing left to
        # divide, extrapolate or attribute a cause to.
        finding = (
            f"Over the last {days} days I answered {len(waits)} turns. "
            # "the slowest tenth" was rendered as "95th percentile" — an invented
            # precision. Phrased as a count of turns, there is no percentile to name.
            f"Half my turns finished within {at(0.5)} seconds; one turn in ten took "
            f"{at(0.9)} seconds or more, and the worst single turn took "
            f"{int(waits[-1])} seconds. {slow} turns took longer than two minutes. "
            f"The tools I actually used most were "
            + ", ".join(f"`{name}` {counted[name]} times" for name in busiest[:5])
            + f". {worst_kind}"
            + (f", then {others}" if others else "")
            + ". This is everything the log shows about it: it records what "
              "happened, not why, and not whether any of it is getting better "
              "or worse."
        )

        return {
            "finding": finding,
            # The first version said only "say the numbers". She said them, correctly,
            # and then added a fortnight-long trend and an "API limit" as the cause —
            # neither of which is in this data. Naming what is absent turned out to
            # matter more than praising what is present.
            # One instruction, and it asks for relaying rather than analysis. The
            # previous note listed what not to infer and was ignored on every point.
            "note": ("Say the finding above in the user's language, as it stands. "
                     "It is already complete — add no percentages, no trends, no "
                     "causes and no numbers that are not in it."),
        }

    @tool('calculate', 'Evaluate arithmetic and common math functions locally without running code.',
          {'expression': {'type': 'string'}}, ['expression'])
    def _tool_calculate(self, name, args, approve, call):
        expression = str(args["expression"])
        result = {"expression": expression, "result": self._calculate(expression)}
        return result

    @tool('set_check',
          "Watch something in the workspace on a schedule and speak only when there is "
          "something worth saying. Read-only: a check never changes anything.",
          {'check': {'type': 'string', 'enum': checks.names(),
                     'description': 'Which check to run'},
           'every_minutes': {'type': 'integer', 'minimum': 15, 'maximum': 20160,
                             'description': 'How often, at least every 15 minutes'}},
          ['check', 'every_minutes'])
    def _tool_set_check(self, name, args, approve, call):
        wanted = str(args.get("check", "")).strip()
        if checks.get(wanted) is None:
            raise ValueError(f"unknown check {wanted!r}; choose one of {', '.join(checks.names())}")
        every = max(15, min(int(args.get("every_minutes", 1440)), 20160))
        existing = [task for task in self.db.scheduled_tasks(include_disabled=False)
                    if task.get("kind") == "check" and task.get("request") == wanted]
        if existing:
            return {"check": wanted, "already_scheduled": True,
                    "next_run": existing[0]["next_run"]}
        due = datetime.now(timezone.utc) + timedelta(minutes=every)
        task = self.db.add_scheduled("check", wanted, every_minutes=every,
                                     next_run=due.isoformat())
        return {"check": wanted, "every_minutes": every, "next_run": task["next_run"]}

    @tool('set_reminder',
          "Remind the user about something later. Give the delay in minutes from now — "
          "convert 'tomorrow morning' or 'in an hour' yourself. A reminder only shows a "
          "message; it cannot change anything, and it waits for quiet hours.",
          {'text': {'type': 'string', 'description': 'What to remind the user about'},
           'in_minutes': {'type': 'integer', 'minimum': 1, 'maximum': 20160,
                          'description': 'Delay from now, up to two weeks'},
           'repeat_minutes': {'type': 'integer', 'minimum': 5, 'maximum': 20160,
                              'description': 'Optional: repeat every N minutes'}},
          ['text', 'in_minutes'])
    def _tool_set_reminder(self, name, args, approve, call):
        text = str(args.get("text", "")).strip()
        if not text:
            raise ValueError("a reminder needs something to say")
        active = [task for task in self.db.scheduled_tasks(include_disabled=False)
                  if task.get("kind") == "reminder"]
        if len(active) >= self.MAX_ACTIVE_REMINDERS:
            raise ValueError(
                f"there are already {len(active)} reminders waiting; cancel one first")
        delay = max(1, min(int(args.get("in_minutes", 60)), 20160))
        repeat = int(args.get("repeat_minutes") or 0)
        due = datetime.now(timezone.utc) + timedelta(minutes=delay)
        task = self.db.add_scheduled("reminder", text[:400], every_minutes=repeat,
                                     next_run=due.isoformat())
        return {"reminder": text[:400], "due": task["next_run"],
                "in_minutes": delay, "repeats_every_minutes": repeat or None}


#: Programs that run something *else* named in their arguments. A tool name
#: reached through one of these is the same mistake as calling it directly, and
#: it is the form the model actually used: `python -m validate_project`, and a
#: bash script whose body was a note saying the script did not exist.
_RUNNERS = {"python", "python3", "py", "node", "bash", "sh", "cmd", "powershell", "pwsh"}


def _tool_invoked_as_program(command: list[str]) -> str:
    """A tool name being run as though it were a program, or "".

    Deliberately narrow. Only the module after `-m`, or the first word of a
    script body, counts — a tool name merely *appearing* inside a longer script
    may well be a function the model is legitimately defining, and refusing that
    would block real work to prevent a typo.
    """
    if not command:
        return ""
    runner = pathlib.PurePath(command[0]).name.casefold().removesuffix(".exe")
    if runner not in _RUNNERS:
        return ""
    for index, part in enumerate(command[1:], start=1):
        if part == "-m" and index + 1 < len(command):
            candidate = command[index + 1].strip()
            if toolkit.get(candidate) is not None:
                return candidate
        if part in {"-c", "-lc", "-Command"} and index + 1 < len(command):
            body = command[index + 1].strip()
            first = body.split(None, 1)[0] if body else ""
            if toolkit.get(first) is not None:
                return first
    return ""
