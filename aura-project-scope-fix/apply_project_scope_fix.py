from __future__ import annotations

import ast
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "aura" / "agent.py"
TEST = ROOT / "tests" / "test_project_scope_regressions.py"

TEST_CONTENT = r'''import tempfile
import unittest
from pathlib import Path

from aura.agent import AuraAgent
from aura.provider import LMStudioProvider, ProviderReply, ToolCall
from aura.turn import TurnState


class ProjectNameResolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace",
                               provider=LMStudioProvider(model="local-model"))
        self.addCleanup(self.agent.db.close)

    def test_called_name_beats_the_adjective_new(self):
        base, paths = self.agent._extract_artifact_contract(
            "Create a new project called shop and make a website in it")
        self.assertEqual(base, "shop")
        self.assertEqual(paths, [])

    def test_existing_ghost_new_folder_cannot_steal_explicit_project_name(self):
        self.agent.sandbox.create_folder("new")
        self.assertEqual(
            self.agent.detect_project("Create a new project called shop"),
            "shop",
        )

    def test_new_project_without_a_name_does_not_invent_project_new(self):
        base, _ = self.agent._extract_artifact_contract(
            "Create a new project for an online store")
        self.assertIsNone(base)


class ProjectScopeLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace",
                               provider=LMStudioProvider(model="local-model"))
        self.addCleanup(self.agent.db.close)
        self.turn = TurnState(expected_base="shop", build_words=True,
                              requires_mutation=True)

    def test_build_write_outside_project_is_blocked_before_execution(self):
        task_id = self.agent.tasks.start("Create a new project called shop")
        self.agent.current_task_id = task_id
        self.agent._turn_state = self.turn
        result = self.agent._execute_tool(
            ToolCall("bad", "create_file", {"path": "new/index.html", "content": "wrong"}),
            None,
        )
        self.assertFalse(result["ok"])
        self.assertIn("locked to project", result["error"].casefold())
        self.assertFalse(self.agent.sandbox.path("new/index.html").exists())

    def test_build_write_inside_project_is_allowed(self):
        task_id = self.agent.tasks.start("Create a new project called shop")
        self.agent.current_task_id = task_id
        self.agent._turn_state = self.turn
        result = self.agent._execute_tool(
            ToolCall("good", "create_file", {"path": "shop/index.html", "content": "ok"}),
            None,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(self.agent.sandbox.path("shop/index.html").is_file())

    def test_validation_must_target_the_locked_project(self):
        wrong = self.agent._project_scope_error(
            ToolCall("v1", "validate_project", {"path": "new"}), self.turn)
        right = self.agent._project_scope_error(
            ToolCall("v2", "validate_project", {"path": "shop"}), self.turn)
        self.assertIn("validate", wrong.casefold())
        self.assertEqual(right, "")

    def test_completion_evidence_omits_unrelated_project_reads(self):
        turn = TurnState(
            expected_base="shop",
            validation_succeeded=True,
            validation_scope="shop",
            validation_evidence={"valid": True, "files_seen": 10},
            verified_final_paths={"shop/index.html", "new/PLAN.md"},
        )
        self.agent.fetched_sources = []
        answer = self.agent._finish_turn(turn, ProviderReply("Built shop.", []))
        self.assertIn("`shop`", answer)
        self.assertIn("`shop/index.html`", answer)
        self.assertNotIn("new/PLAN.md", answer)


if __name__ == "__main__":
    unittest.main()
'''

REPLACEMENTS = [
(
'''    def detect_project(self, message: str) -> str | None:
        """Which project this request is about.

        Matched against folders that actually exist, so an ordinary word can
        never invent a project. Measured before this existed: "Add a contact
        page to the promo site" detected nothing, because the only shape
        recognised was "in the promo folder".
        """
        text = str(message or "").casefold()
        for name in self.workspace_projects():
            lowered = name.casefold()
            if re.search(rf"(?<![\\w-]){re.escape(lowered)}(?![\\w-])", text):
                return name
        # An explicit "the X project" or "in the X folder" is the user saying
        # which project this is, and it holds whether or not the folder exists
        # yet — a project usually gets named before it gets created. The
        # existence rule above is only there to stop a bare noun like "shopping
        # list" inventing one.
        return self._extract_artifact_contract(message)[0]
''',
'''    def detect_project(self, message: str) -> str | None:
        """Which project this request is about.

        An explicit project/folder name wins before loose mentions of existing
        workspace folders. That ordering matters when a stale folder has a name
        that is also ordinary prose: after one bad run created `new/`, the words
        "create a new project called shop" used to select `new` simply because
        that folder already existed.
        """
        explicit = self._extract_artifact_contract(message)[0]
        if explicit:
            return explicit
        text = str(message or "").casefold()
        for name in self.workspace_projects():
            lowered = name.casefold()
            if re.search(rf"(?<![\\w-]){re.escape(lowered)}(?![\\w-])", text):
                return name
        return None
'''
),
(
'''        _, expected_paths = self._extract_artifact_contract(message)
        if not self._requires_mutation(routing_request):
''',
'''        _, expected_paths = self._extract_artifact_contract(message)
        if expected_base:
            # Make the model see the same scope Python will enforce. This is not
            # the guard itself — `_project_scope_error` is — but giving the rule
            # up front avoids wasting a tool round discovering it by rejection.
            messages.insert(1, {"role": "system", "content":
                f"Project scope for this turn is `{expected_base}`. Keep project writes inside "
                f"`{expected_base}/` (or the project folder itself), and validate exactly "
                f"`{expected_base}`. Do not create or validate a second top-level project."})
        if not self._requires_mutation(routing_request):
'''
),
(
'''        missing = set(turn.missing_artifacts)
        present = [path for path in turn.expected_paths if path not in missing]
        return self._format_completion_evidence(
            response.content, turn.validation_scope,
            turn.validation_evidence,
            sorted(turn.verified_final_paths), present,
            turn.unconfirmed, list(self.fetched_sources),
            sorted(turn.measured_paths - turn.verified_final_paths),
            # Named to be changed, and not there to change.
            sorted(path for path in turn.missing_at_start
                   if turn.edit_request and self._file_exists(path)),
        )
''',
'''        missing = set(turn.missing_artifacts)
        present = [path for path in turn.expected_paths if path not in missing]
        # Evidence is a report about the target project, not a transcript of every
        # file the model happened to inspect. A live shop build read `new/PLAN.md`
        # and promoted that unrelated read into the final proof. Keep the full
        # turn state for gates, but scope the user-facing evidence when a project
        # was explicitly identified.
        report_scope = self._normalize_path(turn.expected_base or "")
        in_report_scope = lambda path: (
            not report_scope or self._scope_covers(report_scope, path))
        verified_for_report = sorted(
            path for path in turn.verified_final_paths if in_report_scope(path))
        measured_for_report = sorted(
            path for path in (turn.measured_paths - turn.verified_final_paths)
            if in_report_scope(path))
        created_instead = sorted(
            path for path in turn.missing_at_start
            if turn.edit_request and self._file_exists(path) and in_report_scope(path))
        return self._format_completion_evidence(
            response.content, turn.validation_scope,
            turn.validation_evidence,
            verified_for_report, present,
            turn.unconfirmed, list(self.fetched_sources),
            measured_for_report,
            # Named to be changed, and not there to change.
            created_instead,
        )
'''
),
(
'''        target = None
        patterns = (
            r"(?i)\\b(?:in|inside|under)\\s+(?:a\\s+|the\\s+)?([\\w.-]+)\\s+(?:folder|directory)\\b",
            r"(?i)\\b(?:in|inside|under)\\s+([\\w.-]+)\\s+with\\b",
            r"(?i)\\b([\\w.-]+)\\s+project\\b",
            # Estonian puts the folder on either side of the word for it, and
            # marks the relation with a case ending rather than a preposition.
            # "kausta promo" puts the name after it; "promo kaustas" and
            # "aura_craft projektis" put it before. Matching both directions
            # with one word list made "projektis uus leht" report the folder
            # as "uus": the case ending is what says which side to read.
            r"(?i)\\b(?:kausta|kataloogi)\\s+([\\w.-]+)",
            r"(?i)\\b([\\w.-]+)\\s+(?:kaustas|kataloogis|projektis)\\b",
        )
        for pattern in patterns:
            match = re.search(pattern, message)
            if match:
                candidate = match.group(1).strip("./")
                if candidate.casefold() not in {
                        "a", "an", "the", "this", "that", "entire", "current", "whole",
                        # Possessives and pronouns: "my project" names no folder.
                        "my", "your", "our", "his", "her", "their", "its", "some", "any"}:
                    target = candidate
                    break
''',
'''        target = None
        # Strong naming forms come first. The old generic `([word]) project`
        # pattern saw "new project" before it ever had a chance to understand
        # "called shop", which is how a project literally named `new` appeared.
        # The boolean says the user explicitly *named* the project, so even an
        # unusual but deliberate name such as "new" is allowed in that form.
        patterns = (
            (r"""(?i)\\b(?:project|folder|directory)\\s+(?:called|named)\\s+[`'"]?([\\w.-]+)[`'"]?""", True),
            (r"""(?i)\\b(?:called|named)\\s+[`'"]?([\\w.-]+)[`'"]?\\s+(?:project|folder|directory)\\b""", True),
            (r"""(?i)\\b(?:project|folder|directory)\\s*:\\s*[`'"]?([\\w.-]+)[`'"]?""", True),
            (r"(?i)\\b(?:in|inside|under)\\s+(?:a\\s+|the\\s+)?([\\w.-]+)\\s+(?:folder|directory)\\b", False),
            (r"(?i)\\b(?:in|inside|under)\\s+([\\w.-]+)\\s+with\\b", False),
            (r"(?i)\\b([\\w.-]+)\\s+project\\b", False),
            # Estonian puts the folder on either side of the word for it, and
            # marks the relation with a case ending rather than a preposition.
            # "kausta promo" puts the name after it; "promo kaustas" and
            # "aura_craft projektis" put it before. Matching both directions
            # with one word list made "projektis uus leht" report the folder
            # as "uus": the case ending is what says which side to read.
            (r"(?i)\\b(?:kausta|kataloogi)\\s+([\\w.-]+)", False),
            (r"(?i)\\b([\\w.-]+)\\s+(?:kaustas|kataloogis|projektis)\\b", False),
        )
        generic_words = {
            "a", "an", "the", "this", "that", "entire", "current", "whole",
            # Possessives and pronouns: "my project" names no folder.
            "my", "your", "our", "his", "her", "their", "its", "some", "any",
            # Descriptions of a project are not project names. Explicit
            # `called new`/`named app` still work because named forms bypass this.
            "new", "fresh", "blank", "empty", "simple", "small", "basic",
            "website", "web", "site", "app", "application",
        }
        for pattern, explicitly_named in patterns:
            match = re.search(pattern, message)
            if match:
                candidate = match.group(1).strip("./")
                if explicitly_named or candidate.casefold() not in generic_words:
                    target = candidate
                    break
'''
),
(
'''        return target, paths

    @staticmethod
    def _normalize_path(value: str) -> str:
''',
'''        return target, paths

    @classmethod
    def _project_scope_error(cls, call: ToolCall, turn: TurnState | None) -> str:
        """Reject project-build writes or validation that escape the chosen project.

        This is deliberately a pre-execution check. A completion gate can notice
        a stray `new/index.html` afterwards, but by then the ghost project already
        exists. The model gets a normal failed-tool result and can retry with the
        correct `shop/...` path without Aura changing the wrong project first.
        """
        if turn is None or not turn.expected_base or not turn.build_words:
            return ""
        scope = cls._normalize_path(turn.expected_base)
        if not scope:
            return ""
        name, args = call.name, call.arguments
        if name == "validate_project":
            requested = cls._normalize_path(str(args.get("path", ".")))
            if requested != scope:
                return (f"This turn is locked to project `{scope}`. Refusing to validate "
                        f"`{requested or '.'}`; run validate_project with path `{scope}`.")
            return ""

        paths: list[str] = []
        if name in {"create_folder", "create_file", "write_file", "append_file",
                    "replace_in_file", "apply_edits", "safe_delete_file"}:
            paths.append(str(args.get("path", "")))
        elif name == "write_files":
            paths.extend(str(item.get("path", "")) for item in (args.get("files") or [])
                         if isinstance(item, dict))
        elif name == "copy_file":
            # Copying may legitimately read a source elsewhere; only the new copy
            # changes state, so only its destination is constrained.
            paths.append(str(args.get("destination", "")))
        elif name == "move_file":
            # A move mutates both ends: the source disappears and the destination
            # appears, so a project-scoped build must keep both inside the project.
            paths.extend([str(args.get("source", "")), str(args.get("destination", ""))])
        elif name in {"create_archive", "extract_archive"}:
            paths.append(str(args.get("destination", "")))
        else:
            return ""

        for raw in paths:
            path = cls._normalize_path(raw)
            if path and not cls._scope_covers(scope, path):
                return (f"This turn is locked to project `{scope}`. Refusing {name} for "
                        f"`{path}` because it is outside `{scope}/`. Use a path inside "
                        f"the target project instead.")
        return ""

    @staticmethod
    def _normalize_path(value: str) -> str:
'''
),
(
'''        name, args = call.name, call.arguments
        try:
            service = services.get(name)
''',
'''        name, args = call.name, call.arguments
        try:
            # `execute_code` dispatches nested tools through this same method, so
            # the guard covers both ordinary model tool calls and scripted batch
            # work. `current_task_id` prevents a stale turn object from affecting
            # direct maintenance/test calls made outside an active user turn.
            turn = getattr(self, "_turn_state", None)
            if self.current_task_id and isinstance(turn, TurnState):
                scope_error = self._project_scope_error(call, turn)
                if scope_error:
                    raise ValueError(scope_error)
            service = services.get(name)
'''
),
]


def apply_replacements(source: str) -> str:
    updated = source
    for index, (old, new) in enumerate(REPLACEMENTS, 1):
        if new in updated:
            continue
        count = updated.count(old)
        if count != 1:
            raise RuntimeError(
                f"Refusing to edit agent.py: replacement {index} expected exactly one "
                f"matching source block, found {count}. Your file may differ from the "
                "version this fix was prepared for."
            )
        updated = updated.replace(old, new, 1)
    return updated


def main() -> None:
    if not AGENT.is_file():
        raise SystemExit(f"Run this from the Aura repository: missing {AGENT}")

    original = AGENT.read_text(encoding="utf-8")
    updated = apply_replacements(original)
    ast.parse(updated, filename=str(AGENT))
    ast.parse(TEST_CONTENT, filename=str(TEST))

    if updated == original and TEST.is_file() and TEST.read_text(encoding="utf-8") == TEST_CONTENT:
        print("Project-scope fix is already applied.")
        return

    backup = AGENT.with_suffix(AGENT.suffix + ".project-scope-fix.bak")
    if not backup.exists():
        shutil.copy2(AGENT, backup)

    TEST.parent.mkdir(parents=True, exist_ok=True)
    if TEST.exists() and TEST.read_text(encoding="utf-8") != TEST_CONTENT:
        test_backup = TEST.with_suffix(TEST.suffix + ".project-scope-fix.bak")
        if not test_backup.exists():
            shutil.copy2(TEST, test_backup)

    AGENT.write_text(updated, encoding="utf-8")
    TEST.write_text(TEST_CONTENT, encoding="utf-8")
    print("Applied Aura project-name/scope fix.")
    print(f"Backup: {backup}")
    print("Run: python -m unittest tests.test_project_scope_regressions")


if __name__ == "__main__":
    main()