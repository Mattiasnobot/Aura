import tempfile
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
