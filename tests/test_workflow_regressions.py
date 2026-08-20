import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from aura.agent import AuraAgent
from aura.provider import LMStudioProvider, MockProvider, ProviderReply, ToolCall
from aura.turn import TurnState


class WorkflowRoundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def agent_with(self, replies):
        provider = LMStudioProvider(model="local-model")
        provider.complete = Mock(side_effect=replies)
        agent = AuraAgent(Path(self.temp.name) / "workspace", provider=provider)
        self.addCleanup(agent.db.close)
        return agent, provider

    def test_normal_tool_continuation_is_not_reported_as_a_retry(self):
        agent, provider = self.agent_with([
            ProviderReply("", [ToolCall("c1", "list_files", {"path": "."})]),
            ProviderReply("The workspace is empty.", []),
        ])
        states = []
        answer = agent.handle("List the files in the workspace", state=states.append)
        self.assertEqual(provider.complete.call_count, 2)
        self.assertNotIn("retry", states)
        self.assertIn("workspace", answer.casefold())

    def test_a_real_completion_gate_retry_is_still_reported(self):
        agent, provider = self.agent_with([
            ProviderReply("The workspace is empty.", []),
            ProviderReply("", [ToolCall("c1", "list_files", {"path": "."})]),
            ProviderReply("The workspace is empty.", []),
        ])
        states = []
        agent.handle("List the files in the workspace", state=states.append)
        self.assertEqual(provider.complete.call_count, 3)
        self.assertEqual(states.count("retry"), 1)

    def test_narration_before_a_tool_call_discards_only_that_stream(self):
        agent, provider = self.agent_with([
            ProviderReply("I will check that now.",
                          [ToolCall("c1", "list_files", {"path": "."})]),
            ProviderReply("The workspace is empty.", []),
        ])
        states = []
        agent.handle("List the files in the workspace", state=states.append,
                     token=lambda _piece: None)
        self.assertEqual(provider.complete.call_count, 2)
        self.assertIn("stream_reset", states)
        self.assertNotIn("retry", states)


class WorkflowPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_opening_a_conversation_restores_its_project(self):
        agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.addCleanup(agent.db.close)
        agent.sandbox.create_folder("shop")
        agent.sandbox.create_folder("promo")

        first = agent.session_id
        agent._remember("user", "Let's work on the shop project")
        agent.project_for("Let's work on the shop project")
        self.assertEqual(agent.current_project, "shop")

        agent.new_session()
        agent._remember("user", "Now work on the promo project")
        agent.project_for("Now work on the promo project")
        self.assertEqual(agent.current_project, "promo")

        agent.open_session(first)
        self.assertEqual(agent.current_project, "shop")

    def test_approved_first_plan_is_durable_without_model_bookkeeping(self):
        provider = LMStudioProvider(model="local-model")
        provider.complete = Mock(return_value=ProviderReply(
            "shop/index.html - landing page\nshop/style.css - styles", []))
        agent = AuraAgent(Path(self.temp.name) / "planned", provider=provider)
        self.addCleanup(agent.db.close)
        agent.sandbox.create_folder("shop")
        agent.current_project = "shop"
        agent.current_request = "Build the shop pages"

        plan = agent._plan_files(
            "Build shop/index.html and shop/style.css",
            ["shop/index.html", "shop/style.css"], True,
            lambda _command: True, lambda _state: None)

        self.assertIn("shop/index.html", plan)
        steps = agent.db.plan_steps("shop")
        self.assertEqual([step["text"] for step in steps], [
            "shop/index.html - landing page",
            "shop/style.css - styles",
        ])

    def test_blocked_step_stays_blocked_on_a_later_turn(self):
        agent = AuraAgent(Path(self.temp.name) / "blocked", provider=MockProvider())
        self.addCleanup(agent.db.close)
        agent.sandbox.create_folder("shop")
        agent.current_project = "shop"
        step = agent.db.set_plan_steps("shop", ["Wait for the external API"])[0]
        agent.db.set_step_status(step["id"], "blocked", "waiting for credentials")

        turn = TurnState(successful_tools=1, workspace_mutation=True,
                         validation_succeeded=True, validation_scope="shop",
                         validation_evidence={"files_seen": 1})
        agent._gate_plan_progress(turn, ProviderReply("done", []))

        saved = agent.db.plan_steps("shop")[0]
        self.assertEqual(saved["status"], "blocked")
        self.assertEqual(saved["evidence"], "waiting for credentials")


if __name__ == "__main__":
    unittest.main()
