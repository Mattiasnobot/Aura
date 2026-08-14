import base64
import io
import json
import math
import os
import subprocess
import tempfile
import threading
import time
import unittest
import wave
import zipfile
from array import array
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from aura.action_log import ActionLog
from aura.agent import AuraAgent
from aura.config import ConfigStore
from aura.commands import CommandAgent, CommandResult
from aura.http_app import create_server, existing_aura_url
from aura.graph_model import build_mind_graph
from aura.preview_server import PreviewServer
from aura.provider import (LMStudioProvider, MockProvider, ProviderContext, ProviderError,
                           ProviderReply, ToolCall)
from aura.speech import SpeechOutput
from aura.tasks import TaskJournal
from aura.validation import check_broken_assets
from aura.voice import VoiceInput
from aura.safety import SandboxViolation, WorkspaceSandbox
from aura.web_bridge import AuraWebBridge


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.box = WorkspaceSandbox(Path(self.temp.name) / "workspace")

    def tearDown(self):
        self.temp.cleanup()

    def test_rejects_escape_and_absolute_paths(self):
        with self.assertRaises(SandboxViolation):
            self.box.write_file("../escape.txt", "no")
        with self.assertRaises(SandboxViolation):
            self.box.read_file(str(Path(self.temp.name).resolve() / "outside.txt"))

    def test_file_lifecycle_and_trash(self):
        self.box.create_file("notes/a.txt", "hello")
        self.assertEqual(self.box.read_file("notes/a.txt"), "hello")
        self.box.copy_file("notes/a.txt", "notes/b.txt")
        self.box.move_file("notes/b.txt", "moved.txt")
        self.assertEqual(self.box.search_files("hello"), ["moved.txt", "notes/a.txt"])
        trash_path = self.box.safe_delete_file("moved.txt")
        self.assertTrue(trash_path.exists())
        self.assertFalse((self.box.root / "moved.txt").exists())

    def test_empty_folder_creation_is_recoverable(self):
        created = self.box.create_folder("projects/empty")
        self.assertTrue(created.is_dir())
        result = self.box.undo_last_change()
        self.assertEqual(result["undid"], "create_folder")
        self.assertFalse(created.exists())
        self.assertTrue(any(item.is_dir() for item in self.box.trash.iterdir()))

    def test_binary_import_is_recoverable(self):
        imported = self.box.import_file("assets/sample.bin", b"\x00\x01Aura")
        self.assertEqual(imported.read_bytes(), b"\x00\x01Aura")
        result = self.box.undo_last_change()
        self.assertEqual(result["undid"], "import_file")
        self.assertFalse(imported.exists())

    def test_archive_round_trip_stays_inside_workspace(self):
        self.box.write_file("project/app.py", "print('Aura')\n")
        archive = self.box.create_archive("project", "exports/project.zip")
        self.assertTrue(archive.is_file())
        extracted = self.box.extract_archive("exports/project.zip", "restored")
        self.assertIn(self.box.root / "restored" / "project" / "app.py", extracted)
        self.assertEqual(self.box.read_file("restored/project/app.py"), "print('Aura')\n")

    def test_archive_extraction_rejects_parent_traversal(self):
        malicious = self.box.root / "bad.zip"
        with zipfile.ZipFile(malicious, "w") as archive:
            archive.writestr("../escape.txt", "no")
        with self.assertRaises(ValueError):
            self.box.extract_archive("bad.zip", "restored")
        self.assertFalse((Path(self.temp.name) / "escape.txt").exists())

    def test_metadata_is_protected(self):
        with self.assertRaises(SandboxViolation):
            self.box.write_file(".aura/memory.json", "bad")

    def test_overwrite_can_be_undone(self):
        self.box.write_file("important.txt", "original")
        self.box.write_file("important.txt", "changed")
        result = self.box.undo_last_change()
        self.assertEqual(result["undid"], "write_file")
        self.assertEqual(self.box.read_file("important.txt"), "original")

    def test_new_file_undo_moves_current_version_to_trash(self):
        self.box.create_file("temporary.txt", "data")
        self.box.undo_last_change()
        self.assertFalse((self.box.root / "temporary.txt").exists())
        self.assertTrue(any(self.box.trash.iterdir()))

    def test_line_level_search(self):
        self.box.write_file("notes.txt", "alpha\nimportant TODO here\nomega\n")
        self.assertEqual(self.box.search_text("todo")[0]["line"], 2)

    def test_task_level_rollback_is_scoped(self):
        self.box.active_task_id = "task-one"
        self.box.write_file("one.txt", "one")
        self.box.write_file("two.txt", "two")
        self.box.active_task_id = "task-two"
        self.box.write_file("keep.txt", "keep")
        result = self.box.rollback_task("task-one")
        self.assertEqual(result["changes_undone"], 2)
        self.assertFalse((self.box.root / "one.txt").exists())
        self.assertFalse((self.box.root / "two.txt").exists())
        self.assertEqual(self.box.read_file("keep.txt"), "keep")

    def test_folder_delete_and_restore_from_trash(self):
        self.box.create_folder("proj")
        self.box.create_file("proj/a.txt", "A")
        self.box.create_file("proj/sub/b.txt", "B")
        trashed = self.box.safe_delete_folder("proj")
        self.assertTrue(trashed.is_dir())
        self.assertFalse((self.box.root / "proj").exists())
        items = self.box.list_trash()
        entry = next(item for item in items if item["trash_name"] == trashed.name)
        self.assertEqual(entry["kind"], "folder")
        self.assertEqual(entry["original_path"], "proj")
        restored = self.box.restore_from_trash(trashed.name)
        self.assertEqual(restored, self.box.root / "proj")
        self.assertEqual(self.box.read_file("proj/a.txt"), "A")
        self.assertEqual(self.box.read_file("proj/sub/b.txt"), "B")

    def test_move_folder_and_copy_folder_are_recoverable(self):
        self.box.create_folder("m1")
        self.box.create_file("m1/x.txt", "X")
        self.box.move_folder("m1", "m2")
        self.assertEqual(self.box.read_file("m2/x.txt"), "X")
        self.assertFalse((self.box.root / "m1").exists())
        undo_result = self.box.undo_last_change()
        self.assertEqual(undo_result["undid"], "move_folder")
        self.assertEqual(self.box.read_file("m1/x.txt"), "X")
        self.assertFalse((self.box.root / "m2").exists(), "empty destination folder must be pruned")

        self.box.create_folder("c1")
        self.box.create_file("c1/y.txt", "Y")
        self.box.copy_folder("c1", "c2")
        self.assertEqual(self.box.read_file("c2/y.txt"), "Y")
        self.assertEqual(self.box.read_file("c1/y.txt"), "Y")
        self.box.undo_last_change()
        self.assertFalse((self.box.root / "c2").exists())
        self.assertEqual(self.box.read_file("c1/y.txt"), "Y")

    def test_folder_operations_reject_existing_destination_and_non_folders(self):
        self.box.create_folder("a")
        self.box.create_folder("b")
        self.box.create_file("f.txt", "x")
        with self.assertRaises(FileExistsError):
            self.box.move_folder("a", "b")
        with self.assertRaises(FileExistsError):
            self.box.copy_folder("a", "b")
        with self.assertRaises(NotADirectoryError):
            self.box.move_folder("f.txt", "c")
        with self.assertRaises(NotADirectoryError):
            self.box.safe_delete_folder("f.txt")

    def test_folder_operations_reject_symlinked_descendants(self):
        real = self.box.root / "outside_link_target"
        real.mkdir()
        (real / "f.txt").write_text("hi", encoding="utf-8")
        self.box.create_folder("linked_parent")
        link = self.box.root / "linked_parent" / "inner"
        try:
            os.symlink(real, link, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks is not permitted in this environment")
        with self.assertRaises(SandboxViolation):
            self.box.move_folder("linked_parent", "linked_parent_moved")
        self.assertTrue((self.box.root / "linked_parent").exists())

    def test_restore_from_trash_reports_missing_or_occupied(self):
        with self.assertRaises(FileNotFoundError):
            self.box.restore_from_trash("does-not-exist")
        self.box.create_file("occupied.txt", "one")
        trashed = self.box.safe_delete_file("occupied.txt")
        self.box.create_file("occupied.txt", "two")
        with self.assertRaises(FileExistsError):
            self.box.restore_from_trash(trashed.name)

    def test_compare_files_matches_agent_tool_output(self):
        self.box.create_file("left.txt", "one\ntwo\n")
        self.box.create_file("right.txt", "one\nthree\n")
        result = self.box.compare_files("left.txt", "right.txt")
        self.assertTrue(result["different"])
        self.assertIn("-two", result["diff"])
        self.assertIn("+three", result["diff"])
        self.assertFalse(self.box.compare_files("left.txt", "left.txt")["different"])


class PreviewServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.box = WorkspaceSandbox(Path(self.temp.name) / "workspace")
        self.log = ActionLog(self.box.meta / "action-log.jsonl")
        self.server = PreviewServer(self.box, self.log)

    def tearDown(self):
        self.server.stop_if_running()
        self.temp.cleanup()

    def test_start_serves_folder_and_stop_frees_the_port(self):
        self.box.create_file("site/index.html", "<html><body>hi</body></html>")
        status = self.server.start("site")
        self.assertTrue(status["running"])
        self.assertEqual(status["path"], "site")
        with urlopen(status["url"] + "index.html", timeout=3) as response:
            self.assertIn(b"hi", response.read())
        self.assertTrue(self.server.status()["running"])
        self.server.stop()
        self.assertFalse(self.server.status()["running"])
        with self.assertRaises(URLError):
            urlopen(status["url"], timeout=2)

    def test_protected_metadata_is_never_served_even_at_workspace_root(self):
        self.box.create_file("index.html", "<html></html>")
        status = self.server.start(".")
        base = status["url"].rstrip("/")
        with self.assertRaises(HTTPError) as raised:
            urlopen(base + "/.aura/config.json", timeout=3)
        self.assertEqual(raised.exception.code, 403)
        with self.assertRaises(HTTPError) as raised_trash:
            urlopen(base + "/.aura-trash/anything", timeout=3)
        self.assertEqual(raised_trash.exception.code, 403)

    def test_starting_again_replaces_the_previous_server(self):
        self.box.create_file("a/index.html", "a")
        self.box.create_file("b/index.html", "b")
        self.server.start("a")
        second = self.server.start("b")
        self.assertEqual(second["path"], "b")
        self.assertEqual(self.server.status(), second)
        # Only one server is ever active: folder "a" is no longer reachable under any URL,
        # and the freed port (which may be reused) now serves folder "b" exclusively.
        with urlopen(second["url"] + "index.html", timeout=3) as response:
            self.assertEqual(response.read(), b"b")
        with self.assertRaises(HTTPError) as raised:
            urlopen(second["url"] + "does-not-exist-in-a-or-b.html", timeout=3)
        self.assertEqual(raised.exception.code, 404)

    def test_stop_without_a_running_server_raises_and_stop_if_running_does_not(self):
        with self.assertRaises(RuntimeError):
            self.server.stop()
        self.server.stop_if_running()  # must not raise

    def test_recent_log_reflects_requests(self):
        self.box.create_file("site/index.html", "hi")
        status = self.server.start("site")
        urlopen(status["url"] + "index.html", timeout=3).read()
        entries = self.server.recent_log(10)
        self.assertTrue(any(entry["path"] == "/index.html" and entry["status"] == 200 for entry in entries))


class CheckBrokenAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.box = WorkspaceSandbox(Path(self.temp.name) / "workspace")

    def tearDown(self):
        self.temp.cleanup()

    def test_flags_missing_local_references_and_ignores_remote_ones(self):
        self.box.create_file("site/style.css", "body {}")
        self.box.create_file(
            "site/index.html",
            '<html><head><link rel="stylesheet" href="style.css">'
            '<link rel="stylesheet" href="missing.css">'
            '<script src="https://cdn.example.com/lib.js"></script>'
            '<script src="app.js"></script></head>'
            '<body><img src="images/logo.png"></body></html>',
        )
        result = check_broken_assets(self.box, "site")
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 1)
        broken_refs = {item["reference"] for item in result["broken"]}
        self.assertEqual(broken_refs, {"missing.css", "app.js", "images/logo.png"})

    def test_parent_traversal_reference_is_reported_not_raised(self):
        self.box.create_file("site/index.html", '<html><script src="../../outside.js"></script></html>')
        result = check_broken_assets(self.box, "site")
        self.assertTrue(result["ok"])
        self.assertEqual([item["reference"] for item in result["broken"]], ["../../outside.js"])


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())

    def tearDown(self):
        self.temp.cleanup()

    def test_memory(self):
        self.agent.handle("remember my name is Maya")
        self.assertEqual(self.agent.memory.data["name"], "Maya")
        self.assertEqual(self.agent.tasks.recent(1)[0]["status"], "completed")

    def test_learns_clear_non_sensitive_statements_without_duplicates(self):
        self.agent.handle("I prefer concise answers")
        memories = self.agent.memory.profile_memories()
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0]["category"], "preference")
        self.assertEqual(memories[0]["value"], "concise answers")
        self.assertFalse(memories[0]["confirmed"])
        self.agent.handle("I prefer concise answers")
        self.assertEqual(len(self.agent.memory.profile_memories()), 1)

    def test_automatic_learning_rejects_sensitive_details(self):
        self.agent.handle("I use an API key secret for my private account")
        self.assertEqual(self.agent.memory.profile_memories(), [])

    def test_learning_preserves_dislikes_and_ignores_one_off_commands(self):
        self.agent.handle("I dislike noisy interfaces")
        self.agent.handle("I want Aura to create a temporary file")
        memories = self.agent.memory.profile_memories()
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0]["value"], "Dislikes noisy interfaces")

    def test_automatic_learning_can_be_disabled(self):
        self.agent.config.update(learn_from_conversations=False)
        self.agent.handle("I prefer very detailed answers")
        self.assertEqual(self.agent.memory.profile_memories(), [])

    def test_personal_memory_can_be_corrected_pinned_recalled_and_forgotten(self):
        item = self.agent.memory.learn_fact(
            "tool", "Python for prototypes", source="test", confidence=.8, explicit=True)
        updated = self.agent.memory.update_profile_memory(
            item["id"], value="HTML for interfaces", category="preference", pinned=True)
        self.assertTrue(updated["pinned"])
        self.assertTrue(updated["confirmed"])
        relevant = self.agent.memory.relevant_memories("build an interface")
        self.assertEqual(relevant[0]["value"], "HTML for interfaces")
        removed = self.agent.memory.forget_profile_memory(item["id"])
        self.assertEqual(removed["value"], "HTML for interfaces")
        self.assertEqual(self.agent.memory.profile_memories(), [])

    def test_relevant_memories_stamp_and_persist_last_used(self):
        item = self.agent.memory.learn_fact(
            "preference", "HTML interfaces", source="test", confidence=.9, explicit=True)
        self.assertIsNone(item["last_used"])
        recalled = self.agent.memory.relevant_memories("anything")
        self.assertEqual(recalled[0]["id"], item["id"])
        self.assertIsNotNone(recalled[0]["last_used"])
        reloaded = self.agent.memory.__class__(self.agent.memory.path)
        stored = next(m for m in reloaded.profile_memories() if m["id"] == item["id"])
        self.assertIsNotNone(stored["last_used"])

    def test_conflicting_pairs_flags_negation_and_restatement_only(self):
        memory = self.agent.memory
        concise = memory.learn_fact("preference", "Concise answers", source="t",
                                    confidence=.9, explicit=True)
        opposite = memory.learn_fact("preference", "Dislikes concise answers", source="t",
                                     confidence=.9, explicit=True)
        # Same category, unrelated subject — must never be flagged.
        memory.learn_fact("preference", "Dark colour schemes", source="t",
                          confidence=.9, explicit=True)
        # Same words, different category — also not a conflict.
        memory.learn_fact("goal", "Concise answers everywhere", source="t",
                          confidence=.9, explicit=True)

        pairs = memory.conflicting_pairs()
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["kind"], "contradicts")
        self.assertEqual({pairs[0]["a"], pairs[0]["b"]}, {concise["id"], opposite["id"]})

    def test_conflicting_pairs_reports_restatement_as_overlap(self):
        memory = self.agent.memory
        memory.learn_fact("tool", "Python for prototypes", source="t",
                          confidence=.9, explicit=True)
        memory.learn_fact("tool", "Python for quick prototypes at work", source="t",
                          confidence=.9, explicit=True)
        pairs = memory.conflicting_pairs()
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["kind"], "overlaps")

    def test_forgetting_a_memory_clears_its_conflict(self):
        memory = self.agent.memory
        first = memory.learn_fact("preference", "Concise answers", source="t",
                                  confidence=.9, explicit=True)
        memory.learn_fact("preference", "Dislikes concise answers", source="t",
                          confidence=.9, explicit=True)
        self.assertTrue(memory.conflicting_pairs())
        memory.forget_profile_memory(first["id"])
        self.assertEqual(memory.conflicting_pairs(), [])

    def test_recall_reason_explains_selection_without_being_persisted(self):
        pinned = self.agent.memory.learn_fact(
            "tool", "Rust for CLI work", source="test", confidence=.8, explicit=True)
        self.agent.memory.update_profile_memory(pinned["id"], pinned=True)
        self.agent.memory.learn_fact("preference", "Short replies", source="test",
                                     confidence=.8, explicit=True, project="aura_craft")

        recalled = self.agent.memory.relevant_memories("short replies please",
                                                       project="aura_craft")
        reasons = {item["value"]: item["recall_reason"] for item in recalled}
        self.assertIn("Pinned for stronger recall", reasons["Rust for CLI work"])
        self.assertIn("About the aura_craft project", reasons["Short replies"])
        self.assertIn("Matches your wording", reasons["Short replies"])

        # The reason is per-query context, not a stored property of the memory.
        for stored in self.agent.memory.profile_memories():
            self.assertNotIn("recall_reason", stored)

    def test_recall_reason_is_never_sent_to_the_model(self):
        provider = LMStudioProvider(model="local-model")
        messages = provider.start_messages("hello", ProviderContext(
            None, {}, [], [{"category": "preference", "value": "Short replies",
                            "recall_reason": "Matches your wording", "id": "secret-id"}]))
        blob = json.dumps(messages)
        self.assertIn("Short replies", blob)
        self.assertNotIn("recall_reason", blob)
        self.assertNotIn("secret-id", blob)

    def test_empty_update_confirms_a_memory_without_changing_it(self):
        item = self.agent.memory.learn_fact(
            "preference", "Concise answers", source="chat", confidence=.85)
        self.assertFalse(item["confirmed"])
        confirmed = self.agent.memory.update_profile_memory(item["id"])
        self.assertTrue(confirmed["confirmed"])
        self.assertEqual(confirmed["value"], "Concise answers")
        self.assertEqual(confirmed["category"], "preference")

    def test_relevant_memories_boosts_same_project_matches(self):
        self.agent.memory.learn_fact("preference", "Use TypeScript here", source="test",
                                     confidence=.8, explicit=True, project="aura_craft")
        self.agent.memory.learn_fact("preference", "Use Python here", source="test",
                                     confidence=.8, explicit=True, project="other_site")
        relevant = self.agent.memory.relevant_memories("anything", project="aura_craft")
        self.assertEqual(relevant[0]["value"], "Use TypeScript here")

    def test_agent_tags_learned_memories_with_project_from_message(self):
        self.agent.handle("I prefer TypeScript in the aura_craft project")
        memories = self.agent.memory.profile_memories()
        self.assertTrue(any(m.get("project") == "aura_craft" for m in memories))

    def test_hello_world_builder(self):
        reply = self.agent.handle("Aura, create a hello world Python app", approve=lambda _: True)
        self.assertIn("successfully", reply)
        self.assertIn("Hello from Aura!", reply)
        self.assertTrue((self.agent.sandbox.root / "hello-world" / "hello.py").exists())

    def test_command_requires_approval(self):
        result = self.agent.commands.run(["echo", "hello"])
        self.assertFalse(result.approved)

    def test_compile_validation_cannot_target_outside_workspace(self):
        self.assertFalse(self.agent.commands.is_auto_approved(
            [__import__("sys").executable, "-m", "compileall", "../outside"]
        ))

    def test_file_operation_command_is_blocked_before_approval(self):
        approve = unittest.mock.Mock(return_value=True)
        result = self.agent.commands.run(["mkdir", "project"], approve=approve)
        self.assertTrue(result.blocked)
        self.assertFalse(result.succeeded)
        approve.assert_not_called()


class TaskJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.journal = TaskJournal(Path(self.temp.name) / "tasks.jsonl")

    def tearDown(self):
        self.temp.cleanup()

    def test_only_actionable_filters_out_tool_free_tasks(self):
        chat_id = self.journal.start("hello")
        self.journal.finish(chat_id, "completed", "Hi!")
        work_id = self.journal.start("create note.txt")
        self.journal.record_tool(work_id, "write_file", {"path": "note.txt"}, {"ok": True})
        self.journal.finish(work_id, "completed", "Created note.txt.")

        all_tasks = self.journal.recent(10)
        self.assertEqual({task["task_id"] for task in all_tasks}, {chat_id, work_id})

        actionable = self.journal.recent(10, only_actionable=True)
        self.assertEqual({task["task_id"] for task in actionable}, {work_id})

    def test_recent_infers_project_from_first_mutated_path(self):
        in_folder = self.journal.start("Build the aura_craft site")
        self.journal.record_tool(in_folder, "write_file",
                                 {"path": "aura_craft/index.html"}, {"ok": True})
        self.journal.finish(in_folder, "completed", "Done.")
        backslash = self.journal.start("Build the other site")
        self.journal.record_tool(backslash, "write_file",
                                 {"destination": "other_site\\style.css"}, {"ok": True})
        self.journal.finish(backslash, "completed", "Done.")
        at_root = self.journal.start("Create note.txt")
        self.journal.record_tool(at_root, "write_file", {"path": "note.txt"}, {"ok": True})
        self.journal.finish(at_root, "completed", "Done.")

        by_id = {task["task_id"]: task for task in self.journal.recent(10)}
        self.assertEqual(by_id[in_folder]["project"], "aura_craft")
        self.assertEqual(by_id[backslash]["project"], "other_site")
        self.assertIsNone(by_id[at_root]["project"])

    def test_orphaned_running_task_becomes_interrupted_unless_active(self):
        crashed_id = self.journal.start("build a website")
        self.journal.record_tool(crashed_id, "write_file", {"path": "index.html"}, {"ok": True})
        # No finish() call — simulates the process dying mid-task.

        reported = self.journal.recent(10)[0]
        self.assertEqual(reported["status"], "interrupted")
        self.assertTrue(reported["summary"])

        still_active = self.journal.recent(10, active_task_id=crashed_id)[0]
        self.assertEqual(still_active["status"], "running")


class LMStudioProviderTests(unittest.TestCase):
    def test_provider_receives_only_relevant_personal_memory_fields(self):
        provider = LMStudioProvider(model="local-model")
        messages = provider.start_messages("Build an interface", ProviderContext(
            "Maya", {}, [], [{
                "id": "private-internal-id", "category": "preference",
                "value": "HTML for interfaces", "source": "full private source",
                "confidence": .9,
            }]
        ))
        memory_index = next(index for index, item in enumerate(messages)
                            if item.get("content", "").startswith(
                                "The user explicitly provided these relevant personal facts."))
        memory_message = messages[memory_index]["content"]
        self.assertIn("HTML for interfaces", memory_message)
        self.assertNotIn("private-internal-id", memory_message)
        self.assertNotIn("full private source", memory_message)
        current = messages[memory_index + 1]
        self.assertEqual(current["role"], "user")
        self.assertTrue(current["content"].startswith("Build an interface\n\n[Local Aura memory"))
        self.assertIn("HTML for interfaces", current["content"])
        self.assertNotIn("private-internal-id", current["content"])
        self.assertNotIn("full private source", current["content"])

    def test_memory_question_is_not_misclassified_as_workspace_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            provider.complete = unittest.mock.Mock(return_value=ProviderReply(
                "You prefer HTML interfaces.", []))
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            agent.memory.learn_fact("preference", "HTML interfaces", source="test",
                                    confidence=1, explicit=True)
            answer = agent.handle("Based on what you remember, what interface do I prefer?")
            self.assertEqual(answer, "You prefer HTML interfaces.")
            self.assertEqual(provider.complete.call_count, 1)

    def test_discovers_model_and_sends_chat(self):
        provider = LMStudioProvider()
        replies = iter([
            {"data": [{"id": "local-model"}]},
            {"choices": [{"message": {"content": "Hello from the local model."}}]},
        ])
        with patch.object(provider, "_request", side_effect=lambda *_: next(replies)) as request:
            answer = provider.reply("Hello", ProviderContext(None, {}, []))
        self.assertEqual(answer, "Hello from the local model.")
        self.assertEqual(provider.model, "local-model")
        self.assertEqual(request.call_args_list[1].args[0], "/chat/completions")

    def test_reports_no_loaded_model(self):
        provider = LMStudioProvider()
        with patch.object(provider, "available_models", return_value=[]):
            with self.assertRaises(ProviderError):
                provider.selected_model()

    def test_parses_tool_call(self):
        provider = LMStudioProvider(model="local-model")
        response = {"choices": [{"message": {"content": None, "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "write_file", "arguments": '{"path":"a.txt","content":"hi"}'},
        }]}}]}
        with patch.object(provider, "_request", return_value=response):
            turn = provider.complete([{"role": "user", "content": "create it"}], [])
        self.assertEqual(turn.tool_calls[0].name, "write_file")
        self.assertEqual(turn.tool_calls[0].arguments["path"], "a.txt")

    def test_streams_text(self):
        provider = LMStudioProvider(model="local-model")
        stream = (b'data: {"choices":[{"delta":{"content":"Hello "}}]}\n\n'
                  b'data: {"choices":[{"delta":{"content":"Aura"}}]}\n\n'
                  b'data: [DONE]\n\n')
        pieces = []
        with patch("aura.provider.urlopen", return_value=io.BytesIO(stream)):
            turn = provider.complete([{"role": "user", "content": "hello"}], on_token=pieces.append)
        self.assertEqual(turn.content, "Hello Aura")
        self.assertEqual(pieces, ["Hello ", "Aura"])

    def test_invalid_streamed_tool_call_retries_once_without_stream(self):
        provider = LMStudioProvider(model="local-model")
        broken_event = {"choices": [{"delta": {"tool_calls": [{
            "index": 0, "id": "call_1",
            "function": {"name": "create_file", "arguments": '{"path":"site/index.html"'},
        }]}}]}
        broken_stream = io.BytesIO(
            ("data: " + json.dumps(broken_event) + "\n\ndata: [DONE]\n\n").encode("utf-8"))
        recovered_response = io.BytesIO(json.dumps({"choices": [{"message": {
            "content": None,
            "tool_calls": [{"id": "call_2", "type": "function", "function": {
                "name": "create_file",
                "arguments": '{"path":"site/index.html","content":"<h1>Aura</h1>"}',
            }}],
        }}]}).encode("utf-8"))
        with patch("aura.provider.urlopen", side_effect=[broken_stream, recovered_response]) as request:
            turn = provider.complete([{"role": "user", "content": "create it"}],
                                     AuraAgent.tool_definitions(), on_token=lambda _piece: None)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(turn.tool_calls[0].name, "create_file")
        self.assertEqual(turn.tool_calls[0].arguments["path"], "site/index.html")
        retry_payload = json.loads(request.call_args_list[1].args[0].data.decode("utf-8"))
        self.assertFalse(retry_payload["stream"])

    def test_invalid_atomic_tool_call_gets_one_constrained_repair(self):
        recoveries = []
        provider = LMStudioProvider(
            model="local-model",
            on_recovery=lambda reason, status, details: recoveries.append((reason, status, details)),
        )
        invalid = {"choices": [{"message": {"content": None, "tool_calls": [{
            "id": "broken", "type": "function", "function": {
                "name": "create_file", "arguments": '{"path":"site/index.html"',
            },
        }]}}]}
        repaired = {"choices": [{"message": {"content": None, "tool_calls": [{
            "id": "fixed", "type": "function", "function": {
                "name": "create_file", "arguments": '{"path":"site/index.html","content":"OK"}',
            },
        }]}}]}
        with patch.object(provider, "_request", side_effect=[invalid, repaired]) as request:
            reply = provider.complete([{"role": "user", "content": "create it"}],
                                      AuraAgent.tool_definitions())
        self.assertEqual(request.call_count, 2)
        self.assertEqual(reply.tool_calls[0].arguments["content"], "OK")
        self.assertEqual([item[1] for item in recoveries], ["started", "ok"])
        repair_messages = request.call_args_list[1].args[1]["messages"]
        self.assertIn("invalid JSON", repair_messages[-1]["content"])

    def test_agent_runs_multi_turn_model_tool_loop(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            provider.complete = unittest.mock.Mock(side_effect=[
                ProviderReply("", [ToolCall("call_1", "write_file",
                                             {"path": "made.txt", "content": "real"})]),
                ProviderReply("", [ToolCall("call_2", "read_file", {"path": "made.txt"})]),
                ProviderReply("Created made.txt.", []),
            ])
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            answer = agent.handle("Create made.txt")
            self.assertIn("Created made.txt.", answer)
            self.assertIn("Confirmed evidence", answer)
            self.assertIn("`made.txt`", answer)
            self.assertEqual(agent.sandbox.read_file("made.txt"), "real")
            self.assertEqual(provider.complete.call_count, 3)

    def test_failed_command_is_not_counted_and_model_recovers_with_file_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            replies = iter([
                ProviderReply("", [ToolCall("1", "run_command",
                                             {"command": ["touch", "site/index.html"]})]),
                ProviderReply("", [ToolCall("2", "create_file",
                                             {"path": "site/index.html", "content": "<h1>Aura</h1>"})]),
                ProviderReply("", [ToolCall("3", "read_file", {"path": "site/index.html"})]),
                ProviderReply("", [ToolCall("4", "validate_project", {"path": "."})]),
                ProviderReply("Created and verified the site.", []),
            ])
            message_snapshots = []
            def complete(messages, *_args, **_kwargs):
                message_snapshots.append(json.loads(json.dumps(messages)))
                return next(replies)
            provider.complete = unittest.mock.Mock(side_effect=complete)
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            agent.commands.run = unittest.mock.Mock(return_value=CommandResult(
                ["touch", "site/index.html"], None, "", "program not found", True))
            answer = agent.handle("Create a website and run it")
            first_tool_result = message_snapshots[1][-1]["content"]
            self.assertIn('"ok": false', first_tool_result)
            self.assertIn("verified", answer)
            self.assertEqual(agent.sandbox.read_file("site/index.html"), "<h1>Aura</h1>")
            self.assertEqual(provider.complete.call_count, 5)

    def test_follow_up_routing_inherits_recent_build_intent(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent = AuraAgent(Path(temporary) / "workspace", provider=MockProvider())
            agent.memory.remember_message("user", "Create an AuraCraft website")
            routed = agent._routing_request("I meant Aura should run it")
            names = {item["function"]["name"] for item in agent.select_tool_definitions(routed)}
            self.assertIn("create_file", names)
            self.assertIn("write_file", names)
            self.assertIn("run_command", names)

    def test_short_greeting_does_not_inherit_previous_task(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent = AuraAgent(Path(temporary) / "workspace", provider=MockProvider())
            agent.memory.remember_message("user", "Validate the aura_craft project")
            agent.memory.remember_message("assistant", "Validation passed for aura_craft.")
            routed = agent._routing_request("Hei")
            self.assertEqual(routed, "Hei")
            self.assertEqual(agent.select_tool_definitions(routed), [])

    def test_greeting_is_immediate_and_does_not_call_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            provider.complete = unittest.mock.Mock(return_value=ProviderReply("wrong", []))
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            agent.memory.remember_message("user", "Validate the aura_craft project")
            agent.memory.remember_message("assistant", "Validation passed for aura_craft.")
            answer = agent.handle("Hei")
            self.assertEqual(answer, "Hei! Olen siin ja valmis. Mida soovid teha?")
            provider.complete.assert_not_called()

    def test_named_project_validation_scope_is_recognized(self):
        base, _ = AuraAgent._extract_artifact_contract(
            "Inspect the entire aura_craft project and validate every file")
        self.assertEqual(base, "aura_craft")
        self.assertTrue(AuraAgent._validation_satisfies("aura_craft", base, []))
        self.assertTrue(AuraAgent._validation_satisfies(".", base, []))
        self.assertFalse(AuraAgent._validation_satisfies("somewhere_else", base, []))

    def test_improve_and_polish_require_a_real_mutation(self):
        self.assertTrue(AuraAgent._requires_mutation("Improve and polish the website"))

    def test_negative_safety_clause_does_not_require_mutation(self):
        request = (
            "List every file and validate the project. Do not create, edit, move, "
            "copy, delete, or run anything."
        )
        self.assertFalse(AuraAgent._requires_mutation(request))
        self.assertTrue(AuraAgent._requires_mutation("Do not delete it; instead improve the page"))

    def test_read_only_router_excludes_negated_mutation_tools(self):
        request = (
            "List every file in aura_craft and validate the project. Do not create, edit, "
            "move, copy, delete, or run anything."
        )
        names = {item["function"]["name"] for item in AuraAgent.select_tool_definitions(request)}
        self.assertIn("list_files", names)
        self.assertIn("validate_project", names)
        self.assertNotIn("create_file", names)
        self.assertNotIn("write_file", names)
        self.assertNotIn("copy_file", names)
        self.assertNotIn("move_file", names)
        self.assertNotIn("safe_delete_file", names)
        self.assertNotIn("run_command", names)

    def test_read_only_validation_has_deterministic_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            provider.complete = unittest.mock.Mock(return_value=ProviderReply(
                "I cannot access workspace tools.", []))
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            agent.sandbox.create_file("aura_craft/index.html", "<!doctype html><html><body>OK</body></html>")
            answer = agent.handle(
                "List every file in aura_craft and validate the aura_craft project. "
                "Do not create, edit, move, copy, delete, or run anything."
            )
            self.assertIn("Validation passed for `aura_craft`", answer)
            self.assertIn("`aura_craft/index.html`", answer)
            self.assertEqual(agent.tasks.recent(1)[0]["status"], "completed")
            self.assertEqual(provider.complete.call_count, 3)

    def test_backend_verifies_final_mutation_when_model_does_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            provider.complete = unittest.mock.Mock(side_effect=[
                ProviderReply("", [ToolCall("1", "create_file",
                                             {"path": "note.txt", "content": "verified"})]),
                ProviderReply("Created note.txt.", []),
                ProviderReply("Created note.txt.", []),
                ProviderReply("Created note.txt.", []),
            ])
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            answer = agent.handle("Create note.txt")
            self.assertIn("Created note.txt.", answer)
            self.assertIn("Confirmed evidence", answer)
            self.assertNotIn("could not obtain", answer)
            task = agent.tasks.recent(1)[0]
            self.assertEqual(task["status"], "completed")
            self.assertIn("verify_final_state", task["tools"])

    def test_action_gate_corrects_false_completion(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            provider.complete = unittest.mock.Mock(side_effect=[
                ProviderReply("I cannot create files.", []),
                ProviderReply("", [ToolCall("call_1", "create_file",
                                             {"path": "gated.txt", "content": "done"})]),
                ProviderReply("", [ToolCall("call_2", "read_file", {"path": "gated.txt"})]),
                ProviderReply("Created and verified gated.txt.", []),
            ])
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            answer = agent.handle("Create gated.txt containing done")
            self.assertIn("verified", answer)
            self.assertEqual(agent.sandbox.read_file("gated.txt"), "done")
            self.assertEqual(provider.complete.call_count, 4)

    def test_validation_must_be_newer_than_last_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            provider.complete = unittest.mock.Mock(side_effect=[
                ProviderReply("", [ToolCall("1", "create_file",
                                             {"path": "project/app.py", "content": "print('ok')"})]),
                ProviderReply("", [ToolCall("2", "create_file",
                                             {"path": "project/config.json", "content": '{"ok": true}'})]),
                ProviderReply("", [ToolCall("3", "validate_project", {"path": "project"})]),
                ProviderReply("", [ToolCall("4", "write_file",
                                             {"path": "project/config.json", "content": "broken"})]),
                ProviderReply("Everything is valid.", []),
                ProviderReply("", [ToolCall("5", "validate_project", {"path": "project"})]),
                ProviderReply("", [ToolCall("6", "write_file",
                                             {"path": "project/config.json", "content": '{"fixed": true}'})]),
                ProviderReply("", [ToolCall("7", "validate_project", {"path": "project"})]),
                ProviderReply("Built and freshly validated.", []),
            ])
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            answer = agent.handle("Build in project with app.py and valid config.json")
            self.assertIn("freshly validated", answer)
            self.assertTrue(agent._validate_project("project")["valid"])
            self.assertEqual(provider.complete.call_count, 9)

    def test_tool_router_keeps_simple_create_prompt_focused(self):
        names = {tool["function"]["name"] for tool in AuraAgent.select_tool_definitions(
            "Create a file called hello.txt"
        )}
        self.assertIn("create_file", names)
        self.assertIn("read_file", names)
        self.assertNotIn("remember_preference", names)
        self.assertNotIn("safe_delete_file", names)

    def test_memory_router_never_confuses_forgetting_with_file_deletion(self):
        names = {tool["function"]["name"] for tool in AuraAgent.select_tool_definitions(
            "Forget the memory that I prefer long answers"
        )}
        self.assertIn("forget_personal_fact", names)
        self.assertIn("list_personal_memory", names)
        self.assertNotIn("safe_delete_file", names)

    def test_build_router_honors_do_not_run(self):
        names = {tool["function"]["name"] for tool in AuraAgent.select_tool_definitions(
            "Build a Python project, validate it, and do not run commands"
        )}
        self.assertEqual(names, {"list_files", "read_file", "create_file", "write_file", "validate_project"})

    def test_artifact_contract_extracts_exact_project_paths(self):
        base, paths = AuraAgent._extract_artifact_contract(
            "Build in power-check with PLAN.md, app.py, and config.json."
        )
        self.assertEqual(base, "power-check")
        self.assertEqual(paths, ["power-check/PLAN.md", "power-check/app.py", "power-check/config.json"])

    def test_agent_executes_sandboxed_tool(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent = AuraAgent(Path(temporary) / "workspace", provider=MockProvider())
            result = agent._execute_tool(ToolCall("1", "write_file",
                                                  {"path": "project/app.py", "content": "print('ok')"}), None)
            self.assertTrue(result["ok"])
            self.assertEqual(agent.sandbox.read_file("project/app.py"), "print('ok')")

    def test_unknown_tool_is_returned_as_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent = AuraAgent(Path(temporary) / "workspace", provider=MockProvider())
            result = agent._execute_tool(ToolCall("1", "not_real", {}), None)
            self.assertFalse(result["ok"])

    def test_precise_replace_tool_and_undo(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent = AuraAgent(Path(temporary) / "workspace", provider=MockProvider())
            agent.sandbox.write_file("app.py", "value = 1\n")
            result = agent._execute_tool(ToolCall("1", "replace_in_file", {
                "path": "app.py", "old_text": "value = 1", "new_text": "value = 2"
            }), None)
            self.assertTrue(result["ok"])
            self.assertEqual(agent.sandbox.read_file("app.py"), "value = 2\n")
            agent._execute_tool(ToolCall("2", "undo_last_change", {}), None)
            self.assertEqual(agent.sandbox.read_file("app.py"), "value = 1\n")

    def test_atomic_multi_edit_uses_one_recoverable_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent = AuraAgent(Path(temporary) / "workspace", provider=MockProvider())
            agent.sandbox.write_file("app.txt", "one two three")
            result = agent._execute_tool(ToolCall("1", "apply_edits", {
                "path": "app.txt", "edits": [
                    {"old_text": "one", "new_text": "ONE"},
                    {"old_text": "three", "new_text": "THREE"},
                ]}), None)
            self.assertTrue(result["ok"])
            self.assertEqual(agent.sandbox.read_file("app.txt"), "ONE two THREE")
            agent.sandbox.undo_last_change()
            self.assertEqual(agent.sandbox.read_file("app.txt"), "one two three")

    def test_safe_project_validator(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent = AuraAgent(Path(temporary) / "workspace", provider=MockProvider())
            agent.sandbox.write_file("project/app.py", "print('ok')\n")
            agent.sandbox.write_file("project/config.json", '{"ok": true}')
            agent.sandbox.write_file("project/settings.toml", "enabled = true\n")
            agent.sandbox.write_file("project/index.html", "<!doctype html><html><body><h1>OK</h1></body></html>")
            agent.sandbox.write_file("project/styles.css", "body { color: #fff; }\n")
            agent.sandbox.write_file("project/app.js", "const ready = { ok: true };\n")
            valid = agent._execute_tool(ToolCall("1", "validate_project", {"path": "project"}), None)
            self.assertTrue(valid["valid"])
            self.assertEqual(valid["checked"]["html"], 1)
            self.assertEqual(valid["checked"]["css"], 1)
            self.assertEqual(valid["checked"]["javascript"], 1)
            agent.sandbox.write_file("project/broken.py", "def broken(:\n")
            invalid = agent._execute_tool(ToolCall("2", "validate_project", {"path": "project"}), None)
            self.assertFalse(invalid["valid"])
            self.assertEqual(invalid["issues"][0]["path"], "project/broken.py")
            empty = agent._validate_project("missing")
            self.assertFalse(empty["valid"])

    def test_project_validator_rejects_broken_html_and_css(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent = AuraAgent(Path(temporary) / "workspace", provider=MockProvider())
            agent.sandbox.write_file("site/index.html", "<html><body><section></body></html>")
            agent.sandbox.write_file("site/styles.css", "body { color: red;\n")
            result = agent._validate_project("site")
            self.assertFalse(result["valid"])
            self.assertEqual({issue["path"] for issue in result["issues"]},
                             {"site/index.html", "site/styles.css"})

    def test_javascript_fallback_accepts_regular_expressions(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent = AuraAgent(Path(temporary) / "workspace", provider=MockProvider())
            agent.sandbox.write_file(
                "site/app.js", r"const pattern = /(\[[^\]]+\]|\([^)]*\))/g; console.log(pattern);" + "\n")
            with patch("aura.validation.shutil.which", return_value=None):
                result = agent._validate_project("site")
            self.assertTrue(result["valid"], result["issues"])

    def test_powerful_deep_router_exposes_advanced_build_tools(self):
        names = {tool["function"]["name"] for tool in AuraAgent.select_tool_definitions(
            "Inspect, improve, test, and compare this code project", "powerful", "deep"
        )}
        self.assertTrue({"read_many_files", "write_files", "inspect_code", "compare_files",
                         "workspace_summary", "run_command", "validate_project"}.issubset(names))
        self.assertGreaterEqual(len(AuraAgent.tool_definitions()), 34)

    def test_batch_code_diff_math_and_system_tools(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent = AuraAgent(Path(temporary) / "workspace", provider=MockProvider())
            written = agent._execute_tool(ToolCall("1", "write_files", {"files": [
                {"path": "project/one.py", "content": "import json\ndef one():\n    return 1\n"},
                {"path": "project/two.py", "content": "def one():\n    return 2\n"},
            ]}), None)
            self.assertTrue(written["ok"])
            self.assertEqual(written["count"], 2)
            read = agent._execute_tool(ToolCall("2", "read_many_files", {
                "paths": ["project/one.py", "project/two.py"]
            }), None)
            self.assertEqual(read["count"], 2)
            outline = agent._execute_tool(ToolCall("3", "inspect_code", {
                "path": "project/one.py"
            }), None)
            self.assertEqual(outline["symbols"][0]["name"], "one")
            difference = agent._execute_tool(ToolCall("4", "compare_files", {
                "left": "project/one.py", "right": "project/two.py"
            }), None)
            self.assertTrue(difference["different"])
            calculation = agent._execute_tool(ToolCall("5", "calculate", {
                "expression": "sqrt(81) + 3 * 2"
            }), None)
            self.assertEqual(calculation["result"], 15.0)
            rejected = agent._execute_tool(ToolCall("6", "calculate", {
                "expression": "__import__('os').getcwd()"
            }), None)
            self.assertFalse(rejected["ok"])
            info = agent._execute_tool(ToolCall("7", "system_info", {}), None)
            self.assertTrue(info["ok"])
            self.assertIn("workspace_disk", info)

    def test_personal_memory_tools_support_review_correction_and_forgetting(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent = AuraAgent(Path(temporary) / "workspace", provider=MockProvider())
            remembered = agent._execute_tool(ToolCall("1", "remember_personal_fact", {
                "category": "project", "value": "AuraCraft is my first Aura-built site"
            }), None)
            self.assertTrue(remembered["ok"])
            listed = agent._execute_tool(ToolCall("2", "list_personal_memory", {
                "query": "AuraCraft"
            }), None)
            self.assertEqual(listed["count"], 1)
            corrected = agent._execute_tool(ToolCall("3", "correct_personal_fact", {
                "query": "AuraCraft", "new_value": "AuraCraft is my first completed Aura project",
                "category": "project"
            }), None)
            self.assertTrue(corrected["corrected"])
            forgotten = agent._execute_tool(ToolCall("4", "forget_personal_fact", {
                "query": "first completed"
            }), None)
            self.assertTrue(forgotten["forgotten"])
            sensitive = agent._execute_tool(ToolCall("5", "remember_personal_fact", {
                "category": "personal", "value": "My API key secret is abc"
            }), None)
            self.assertFalse(sensitive["ok"])

    def test_powerful_command_policy_auto_approves_only_static_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent = AuraAgent(Path(temporary) / "workspace", provider=MockProvider())
            agent.sandbox.write_file("app.py", "print('ok')\n")
            self.assertTrue(agent.commands.is_auto_approved(
                ["python", "-m", "py_compile", "app.py"], "powerful"))
            self.assertTrue(agent.commands.is_auto_approved(
                ["node", "--check", "app.py"], "powerful"))
            self.assertFalse(agent.commands.is_auto_approved(
                ["python", "app.py"], "powerful"))
            self.assertFalse(agent.commands.is_auto_approved(
                ["python", "-m", "unittest", "discover"], "powerful"))

    def test_cancellation_stops_before_tool_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            def cancel_then_reply(*args, **kwargs):
                agent.cancel_current()
                return ProviderReply("", [ToolCall("call_1", "write_file",
                                                     {"path": "no.txt", "content": "no"})])
            provider.complete = unittest.mock.Mock(side_effect=cancel_then_reply)
            answer = agent.handle("Create no.txt")
            self.assertTrue(answer.startswith("Cancelled"))
            self.assertFalse((agent.sandbox.root / "no.txt").exists())
            self.assertEqual(agent.tasks.recent(1)[0]["status"], "cancelled")


class LocalSettingsTests(unittest.TestCase):
    def test_config_persists_known_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            store = ConfigStore(path)
            store.update(model="local-model", speak_responses=True,
                          reasoning_depth="deep", autonomy_mode="powerful",
                          learn_from_conversations=False, avatar_motion="calm",
                          avatar_intensity=40, avatar_quality="low",
                          voice_engine="pocketsphinx", voice_device="1",
                          voice_calibration_ms=700)
            loaded = ConfigStore(path)
            self.assertEqual(loaded.data["model"], "local-model")
            self.assertTrue(loaded.data["speak_responses"])
            self.assertEqual(loaded.data["reasoning_depth"], "deep")
            self.assertEqual(loaded.data["autonomy_mode"], "powerful")
            self.assertFalse(loaded.data["learn_from_conversations"])
            self.assertEqual(loaded.data["avatar_motion"], "calm")
            self.assertEqual(loaded.data["avatar_intensity"], 40)
            self.assertEqual(loaded.data["avatar_quality"], "low")
            self.assertEqual(loaded.data["voice_engine"], "pocketsphinx")
            self.assertEqual(loaded.data["voice_device"], "1")
            self.assertEqual(loaded.data["voice_calibration_ms"], 700)

    def test_disabled_speech_does_not_launch_process(self):
        speech = SpeechOutput(enabled=False)
        with patch("aura.speech.subprocess.run") as run:
            ok, _ = speech.speak("hello")
        self.assertFalse(ok)
        run.assert_not_called()

    def test_speech_pipe_uses_utf8_for_emoji(self):
        speech = SpeechOutput(enabled=True)
        process = unittest.mock.Mock(returncode=0)
        process.communicate.return_value = ("", "")
        with patch("aura.speech.subprocess.Popen", return_value=process) as popen:
            ok, _ = speech.speak("Hello 😊")
        self.assertTrue(ok)
        self.assertEqual(popen.call_args.kwargs["encoding"], "utf-8")
        spoken_xml = process.communicate.call_args.args[0]
        self.assertIn("Hello", spoken_xml)
        self.assertNotIn("😊", spoken_xml)

    def test_speech_preparation_adds_natural_pauses_and_cleans_markdown(self):
        xml = SpeechOutput.prepare_sapi_xml("**Done.** Next step!\n\n[Open it](https://example.com) 😊")
        self.assertTrue(xml.startswith("<sapi>"))
        self.assertIn('silence msec="170"', xml)
        self.assertIn('silence msec="320"', xml)
        self.assertNotIn("https://", xml)
        self.assertNotIn("😊", xml)

    def test_neural_engine_has_safe_sapi_fallback(self):
        speech = SpeechOutput(enabled=True, engine="piper")
        with patch.object(speech, "neural_available", return_value=False), \
                patch.object(speech, "_speak_sapi", return_value=(True, "fallback")) as fallback:
            result = speech.speak("hello")
        self.assertEqual(result, (True, "fallback"))
        fallback.assert_called_once_with("hello")

    def test_neural_wave_envelope_drives_real_mouth_levels(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temporary:
            path = Path(temporary.name)
        try:
            rate = 16_000
            silent = [0] * (rate // 5)
            voiced = [int(math.sin(index * math.tau * 220 / rate) * 22_000)
                      for index in range(rate // 5)]
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(rate)
                output.writeframes(array("h", silent + voiced).tobytes())
            payload = SpeechOutput.speech_cues_from_wave(path, interval_ms=50)
            self.assertEqual(payload["source"], "audio-envelope")
            self.assertGreaterEqual(payload["duration_ms"], 390)
            first_half = payload["cues"][:4]
            second_half = payload["cues"][4:]
            self.assertLess(max(cue["open"] for cue in first_half), 0.03)
            self.assertGreater(max(cue["open"] for cue in second_half), 0.5)
        finally:
            path.unlink(missing_ok=True)

    def test_sapi_fallback_exposes_phoneme_timing(self):
        cues = SpeechOutput(enabled=True, rate=0).speech_cues_from_text("Move, Aura.")
        self.assertEqual(cues["source"], "phoneme-timing")
        self.assertGreater(cues["duration_ms"], 0)
        self.assertIn("round", {cue["shape"] for cue in cues["cues"]})


class VoiceInputTests(unittest.TestCase):
    def test_microphone_meter_uses_real_pcm_energy(self):
        silence = array("h", [0] * 800).tobytes()
        speech = array("h", [14_000, -14_000] * 400).tobytes()
        self.assertEqual(VoiceInput._rms(silence), 0)
        self.assertGreater(VoiceInput._rms(speech), 0.4)
        self.assertGreater(VoiceInput._meter(VoiceInput._rms(speech)), 0.8)

    def test_whisper_cpp_is_optional_and_parses_local_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "whisper-cli.exe"
            model = Path(temporary) / "model.ggml"
            executable.write_bytes(b"local executable placeholder")
            model.write_bytes(b"local model placeholder")
            voice = VoiceInput(engine="whisper_cpp", whisper_path=executable,
                               whisper_model=model, language="en")
            completed = unittest.mock.Mock(
                returncode=0, stdout="[00:00:00.000 --> 00:00:01.000]  hello Aura\n",
                stderr="",
            )
            with patch("aura.voice.subprocess.run", return_value=completed) as run:
                result = voice._recognize_whisper(array("h", [0] * 1600).tobytes())
            self.assertEqual(result, (True, "hello Aura"))
            self.assertIn(str(model.resolve()), run.call_args.args[0])
            self.assertFalse(Path(run.call_args.args[0][4]).exists())

    def test_streaming_voice_can_emit_partial_and_finish_on_request(self):
        class Hypothesis:
            hypstr = "hello aura"

        class FakeDecoder:
            def __init__(self, **_kwargs):
                self.processed = 0

            def start_utt(self):
                return None

            def process_raw(self, _raw, _a, _b):
                self.processed += 1

            def hyp(self):
                return Hypothesis() if self.processed else None

            def end_utt(self):
                return None

        class FakeStream:
            def __init__(self, **kwargs):
                self.callback = kwargs.get("callback")

            def __enter__(self):
                raw = array("h", [12_000, -12_000] * 400).tobytes()
                if self.callback:
                    for _ in range(12):
                        self.callback(raw, 800, None, None)
                return self

            def __exit__(self, *_args):
                return False

        class FakeSoundDevice:
            RawInputStream = FakeStream

        voice = VoiceInput(calibration_ms=200, noise_floor=0.005, max_seconds=5)
        results = []
        partials = []
        levels = []
        with patch.object(voice, "_imports", return_value=(FakeSoundDevice, FakeDecoder)):
            worker = threading.Thread(target=lambda: results.append(voice.listen(
                mode="hold", on_partial=partials.append,
                on_level=lambda level, _rms: levels.append(level))))
            worker.start()
            time.sleep(0.03)
            voice.request_stop()
            worker.join(timeout=2)
        self.assertEqual(results, [(True, "hello aura")])
        self.assertIn("hello aura", partials)
        self.assertGreater(max(levels), 0.7)
        self.assertFalse(voice.active)


class MindMapTests(unittest.TestCase):
    def test_graph_uses_real_memory_tasks_tools_and_workspace_hierarchy(self):
        memory = {
            "name": "Maya",
            "preferences": {"tone": "concise"},
            "conversation": [{"role": "user", "text": "Build a clock", "time": "now"}],
            "profile_memories": [{"id": "mem-one", "category": "preference",
                                  "value": "HTML for interfaces", "confidence": .9,
                                  "confirmed": True, "pinned": True, "source": "chat"}],
        }
        tasks = [{
            "task_id": "task-one", "request": "Build a clock", "status": "completed",
            "summary": "Done", "tools": ["create_file", "validate_project"],
        }]
        nodes, edges = build_mind_graph(memory, tasks, ["clock/app.py", "notes.txt"])
        node_ids = {node.node_id for node in nodes}
        edge_pairs = {(edge.source, edge.target) for edge in edges}
        self.assertIn("aura", node_ids)
        self.assertIn("person:name", node_ids)
        self.assertIn("folder:clock", node_ids)
        self.assertIn("file:clock/app.py", node_ids)
        self.assertIn("personal:mem-one", node_ids)
        self.assertIn(("workspace", "folder:clock"), edge_pairs)
        self.assertIn(("folder:clock", "file:clock/app.py"), edge_pairs)
        self.assertIn(("personal_memory", "personal:mem-one"), edge_pairs)
        self.assertTrue(any(node.kind == "tool" and node.label == "create file" for node in nodes))

    def test_graph_caps_visible_files(self):
        files = [f"project/file-{index}.txt" for index in range(100)]
        nodes, _ = build_mind_graph({}, [], files, max_files=12)
        self.assertEqual(sum(node.kind == "file" for node in nodes), 12)
        self.assertIn("file:more", {node.node_id for node in nodes})


class WebBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.bridge = AuraWebBridge(agent=agent, speech=SpeechOutput(enabled=False))

    def tearDown(self):
        self.bridge.shutdown()
        self.temp.cleanup()

    def test_bridge_streams_structured_events_without_exposing_raw_tools(self):
        self.assertTrue(self.bridge.submit("hello")["ok"])
        events = []
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            events.extend(self.bridge.poll_events())
            if any(event.get("type") == "busy" and event.get("value") is False for event in events):
                break
            time.sleep(0.01)
        self.assertTrue(any(event.get("type") == "user_message" for event in events))
        self.assertTrue(any(event.get("type") == "reply" and "ready" in event.get("text", "")
                            for event in events))
        reply = next(event for event in events if event.get("type") == "reply")
        self.assertEqual(reply["task"]["status"], "completed")
        self.assertEqual(reply["task"]["request"], "hello")
        self.assertFalse(hasattr(self.bridge, "run_command"))

    def test_recent_tasks_excludes_chit_chat_but_keeps_real_work(self):
        self.assertTrue(self.bridge.submit("hello")["ok"])
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and self.bridge._busy:
            time.sleep(0.01)
        task_id = self.bridge.agent.tasks.start("Create note.txt")
        self.bridge.agent.tasks.record_tool(task_id, "write_file", {"path": "note.txt"}, {"ok": True})
        self.bridge.agent.tasks.finish(task_id, "completed", "Created note.txt.")

        result = self.bridge.recent_tasks(10)
        requests = [task["request"] for task in result["tasks"]]
        self.assertNotIn("hello", requests)
        self.assertIn("Create note.txt", requests)

    def test_bridge_streams_local_speech_cues_to_the_avatar(self):
        class CueSpeech:
            enabled = True

            def speak(self, _text, on_cues=None):
                if on_cues:
                    on_cues({"source": "audio-envelope", "duration_ms": 110,
                             "cues": [{"at_ms": 0, "open": 0.1},
                                      {"at_ms": 55, "open": 0.8}]})
                return True, "spoken"

            def stop(self):
                return None

        with tempfile.TemporaryDirectory() as temporary:
            agent = AuraAgent(Path(temporary) / "workspace", provider=MockProvider())
            bridge = AuraWebBridge(agent=agent, speech=CueSpeech())
            try:
                self.assertTrue(bridge.submit("hello")["ok"])
                events = []
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    events.extend(bridge.poll_events())
                    if any(event.get("type") == "speech" and not event.get("active")
                           for event in events):
                        break
                    time.sleep(0.01)
                cue_event = next(event for event in events if event.get("type") == "speech_cues")
                self.assertEqual(cue_event["source"], "audio-envelope")
                self.assertEqual(cue_event["cues"][1]["open"], 0.8)
                self.assertTrue(any(event.get("type") == "speech" and event.get("active")
                                    for event in events))
            finally:
                bridge.shutdown()

    def test_voice_session_streams_levels_partials_and_supports_hold_release(self):
        class StreamingVoice:
            def __init__(self):
                self.finished = threading.Event()

            def capabilities(self):
                return {"streaming": True, "pocketsphinx": True, "whisper_cpp": False,
                        "selected_engine": "auto", "active_engine": "pocketsphinx"}

            def devices(self):
                return [{"id": "1", "name": "Test microphone", "host": "Test", "default": True}]

            def listen(self, *, mode, on_level, on_partial, on_status):
                self.assert_mode = mode
                on_status("calibrating")
                on_status("listening")
                on_level(0.72, 0.08)
                on_partial("hello")
                self.finished.wait(1)
                on_status("processing")
                return True, "hello aura"

            def request_stop(self, *, cancel=False):
                self.cancelled = cancel
                self.finished.set()

            def stop(self):
                self.request_stop(cancel=True)

        with tempfile.TemporaryDirectory() as temporary:
            agent = AuraAgent(Path(temporary) / "workspace", provider=MockProvider())
            voice = StreamingVoice()
            speech = SpeechOutput(enabled=False)
            bridge = AuraWebBridge(agent=agent, speech=speech, voice=voice)
            try:
                with patch.object(speech, "stop", wraps=speech.stop) as stop_speech:
                    self.assertTrue(bridge.start_voice("hold")["ok"])
                    deadline = time.monotonic() + 2
                    events = []
                    while time.monotonic() < deadline:
                        events.extend(bridge.poll_events())
                        if any(event.get("type") == "voice_partial" for event in events):
                            break
                        time.sleep(0.01)
                    self.assertTrue(bridge.stop_voice(False)["ok"])
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        events.extend(bridge.poll_events())
                        if (any(event.get("type") == "voice_text" for event in events)
                                and any(event.get("type") == "busy" and not event.get("value")
                                        for event in events)):
                            break
                        time.sleep(0.01)
                    stop_speech.assert_called()
                self.assertEqual(voice.assert_mode, "hold")
                self.assertFalse(voice.cancelled)
                self.assertTrue(any(event.get("type") == "voice_level" and event["level"] == 0.72
                                    for event in events))
                self.assertTrue(any(event.get("type") == "voice_partial" and event["text"] == "hello"
                                    for event in events))
                self.assertTrue(any(event.get("type") == "voice_text" and event["text"] == "hello aura"
                                    for event in events))
            finally:
                bridge.shutdown()

    def test_voice_preview_works_without_enabling_all_spoken_replies(self):
        def preview(_text, on_cues=None):
            if on_cues:
                on_cues({"source": "audio-envelope", "duration_ms": 80,
                         "cues": [{"at_ms": 0, "open": 0.6}]})
            return True, "previewed"

        with patch.object(self.bridge.speech, "speak", side_effect=preview) as speak:
            self.assertTrue(self.bridge.preview_voice()["ok"])
            events = []
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                events.extend(self.bridge.poll_events())
                if any(event.get("type") == "speech" and not event.get("active")
                       for event in events):
                    break
                time.sleep(0.01)
        speak.assert_called_once()
        self.assertTrue(any(event.get("type") == "speech_cues" for event in events))
        self.assertFalse(self.bridge.speech.enabled)

    def test_bridge_announces_automatic_personal_learning(self):
        self.assertTrue(self.bridge.submit("I prefer HTML interfaces")["ok"])
        events = []
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            events.extend(self.bridge.poll_events())
            if any(event.get("type") == "busy" and event.get("value") is False for event in events):
                break
            time.sleep(0.01)
        learned = next(event for event in events if event.get("type") == "memory_learned")
        self.assertEqual(learned["memories"][0]["value"], "HTML interfaces")

    def test_workspace_snapshot_preview_and_import(self):
        self.bridge.agent.sandbox.write_file(
            "site/index.html", "<!doctype html><html><body><h1>Aura</h1></body></html>")
        self.bridge.agent.sandbox.import_file("site/archive.bin", b"\x00\x01")
        snapshot = self.bridge.workspace_snapshot()
        self.assertTrue(snapshot["ok"])
        by_path = {item["path"]: item for item in snapshot["files"]}
        self.assertEqual(by_path["site/index.html"]["preview_kind"], "rendered")
        self.assertEqual(by_path["site/archive.bin"]["preview_kind"], "binary")
        preview = self.bridge.preview_workspace_file("site/index.html")
        self.assertEqual(preview["kind"], "rendered")
        self.assertIn("<h1>Aura</h1>", preview["content"])
        self.assertEqual(preview["url"], "/workspace-preview/site/index.html")
        self.assertFalse(preview["scripts_enabled"])
        self.assertEqual(self.bridge.preview_workspace_file("site/archive.bin")["kind"], "binary")

        encoded = base64.b64encode(b"dragged into Aura").decode("ascii")
        first = self.bridge.import_files([{"name": "note.txt", "content": encoded}])
        second = self.bridge.import_files([{"name": "note.txt", "content": encoded}])
        self.assertEqual(first["files"], ["note.txt"])
        self.assertEqual(second["files"], ["note (2).txt"])
        self.assertEqual(self.bridge.agent.sandbox.read_file("note.txt"), "dragged into Aura")

    def test_workspace_file_and_folder_crud_round_trip(self):
        self.assertTrue(self.bridge.create_workspace_file("draft.txt", "hello")["ok"])
        self.assertEqual(self.bridge.agent.sandbox.read_file("draft.txt"), "hello")
        renamed = self.bridge.rename_workspace_item("draft.txt", "final.txt")
        self.assertEqual(renamed["path"], "final.txt")
        moved = self.bridge.move_workspace_item("final.txt", "notes/final.txt")
        self.assertEqual(moved["path"], "notes/final.txt")
        copied = self.bridge.copy_workspace_item("notes/final.txt", "notes/final-copy.txt")
        self.assertEqual(copied["path"], "notes/final-copy.txt")
        deleted = self.bridge.delete_workspace_item("notes/final-copy.txt")
        self.assertTrue(deleted["ok"])
        self.assertEqual(deleted["kind"], "file")
        actions = [event["action"] for event in self.bridge.agent.log.recent(20)]
        for expected in ("create_file", "rename_item", "move_file", "copy_file", "safe_delete_file"):
            self.assertIn(expected, actions)

    def test_workspace_folder_crud_round_trip(self):
        self.assertTrue(self.bridge.create_workspace_folder("assets")["ok"])
        self.bridge.agent.sandbox.create_file("assets/a.txt", "A")
        moved = self.bridge.move_workspace_item("assets", "media")
        self.assertEqual(moved["path"], "media")
        self.assertEqual(self.bridge.agent.sandbox.read_file("media/a.txt"), "A")
        copied = self.bridge.copy_workspace_item("media", "media-copy")
        self.assertTrue(copied["ok"])
        deleted = self.bridge.delete_workspace_item("media-copy")
        self.assertEqual(deleted["kind"], "folder")
        actions = [event["action"] for event in self.bridge.agent.log.recent(20)]
        for expected in ("create_folder", "move_folder", "copy_folder", "safe_delete_folder"):
            self.assertIn(expected, actions)

    def test_trash_list_and_restore_via_bridge(self):
        self.bridge.agent.sandbox.create_file("keep.txt", "K")
        deleted = self.bridge.delete_workspace_item("keep.txt")
        trash_name = deleted["trashed_as"]
        listed = self.bridge.list_trash()
        self.assertTrue(listed["ok"])
        self.assertTrue(any(item["trash_name"] == trash_name for item in listed["items"]))
        restored = self.bridge.restore_workspace_item(trash_name)
        self.assertEqual(restored["path"], "keep.txt")
        self.assertEqual(self.bridge.agent.sandbox.read_file("keep.txt"), "K")

        self.bridge.agent.sandbox.create_file("conflict.txt", "one")
        deleted_conflict = self.bridge.delete_workspace_item("conflict.txt")
        self.bridge.agent.sandbox.create_file("conflict.txt", "two")
        occupied = self.bridge.restore_workspace_item(deleted_conflict["trashed_as"])
        self.assertFalse(occupied["ok"])
        self.assertIn("error", occupied)

    def test_workspace_change_history_and_undo(self):
        self.bridge.create_workspace_file("history.txt", "v1")
        history = self.bridge.workspace_change_history(20)
        self.assertTrue(history["ok"])
        self.assertEqual(history["changes"][0]["operation"], "create_file")
        self.assertFalse(history["changes"][0]["undone"])
        undo = self.bridge.undo_workspace_change()
        self.assertTrue(undo["ok"])
        self.assertFalse((self.bridge.agent.sandbox.root / "history.txt").exists())
        second_undo = self.bridge.undo_workspace_change()
        self.assertFalse(second_undo["ok"])

    def test_compare_workspace_files_bridge(self):
        self.bridge.agent.sandbox.create_file("l.txt", "a\nb\n")
        self.bridge.agent.sandbox.create_file("r.txt", "a\nc\n")
        result = self.bridge.compare_workspace_files("l.txt", "r.txt")
        self.assertTrue(result["ok"])
        self.assertTrue(result["different"])

    def test_preview_server_lifecycle_via_bridge(self):
        self.assertFalse(self.bridge.preview_server_status()["running"])
        self.bridge.agent.sandbox.create_file("site/index.html", "hello preview")
        started = self.bridge.start_preview_server("site")
        self.assertTrue(started["ok"])
        self.assertTrue(started["running"])
        status = self.bridge.preview_server_status()
        self.assertTrue(status["running"])
        self.assertEqual(status["path"], "site")
        with urlopen(status["url"] + "index.html", timeout=3) as response:
            self.assertEqual(response.read(), b"hello preview")
        log = self.bridge.preview_server_log(10)
        self.assertTrue(log["ok"])
        self.assertTrue(any(entry["path"] == "/index.html" for entry in log["entries"]))
        stopped = self.bridge.stop_preview_server()
        self.assertTrue(stopped["ok"])
        self.assertFalse(self.bridge.preview_server_status()["running"])
        again = self.bridge.stop_preview_server()
        self.assertFalse(again["ok"])

    def test_check_workspace_assets_bridge(self):
        self.bridge.agent.sandbox.create_file(
            "site/index.html", '<html><script src="missing.js"></script></html>')
        result = self.bridge.check_workspace_assets("site")
        self.assertTrue(result["ok"])
        self.assertEqual([item["reference"] for item in result["broken"]], ["missing.js"])

    def test_personal_memory_bridge_is_transparent_editable_and_local(self):
        empty = self.bridge.get_personal_memory()
        self.assertTrue(empty["ok"])
        self.assertEqual(empty["count"], 0)
        added = self.bridge.add_personal_memory("preference", "Concise but warm explanations")
        self.assertTrue(added["ok"])
        memory_id = added["memory"]["id"]
        updated = self.bridge.update_personal_memory(memory_id, {
            "value": "Warm explanations with useful detail", "pinned": True
        })
        self.assertTrue(updated["memory"]["pinned"])
        self.assertTrue(updated["memory"]["confirmed"])
        graph = self.bridge.get_mind_graph()
        self.assertTrue(any(node["node_id"] == f"personal:{memory_id}" for node in graph["nodes"]))
        forgotten = self.bridge.forget_personal_memory(memory_id)
        self.assertTrue(forgotten["ok"])
        self.assertEqual(self.bridge.get_personal_memory()["count"], 0)

    def test_event_cursors_broadcast_to_multiple_tabs(self):
        cursor = self.bridge.get_bootstrap()["event_cursor"]
        self.assertTrue(self.bridge.submit("hello")["ok"])
        deadline = time.monotonic() + 3
        first = []
        while time.monotonic() < deadline:
            first = self.bridge.poll_events(cursor, 100)
            if any(event.get("type") == "busy" and event.get("value") is False for event in first):
                break
            time.sleep(0.01)
        second = self.bridge.poll_events(cursor, 100)
        self.assertTrue(any(event.get("type") == "user_message" for event in first))
        self.assertTrue(any(event.get("type") == "reply" for event in first))
        self.assertEqual(first, second)

    def test_bridge_command_approval_round_trip(self):
        result = []
        worker = threading.Thread(target=lambda: result.append(
            self.bridge._approve_command(["echo", "hello world"])))
        worker.start()
        deadline = time.monotonic() + 2
        approval = None
        while time.monotonic() < deadline and approval is None:
            approval = next((event for event in self.bridge.poll_events()
                             if event.get("type") == "approval"), None)
            time.sleep(0.01)
        self.assertIsNotNone(approval)
        self.assertTrue(self.bridge.resolve_approval(approval["approval_id"], True)["ok"])
        worker.join(timeout=2)
        self.assertEqual(result, [True])

    def test_bridge_can_reuse_only_the_identical_approval_for_current_task(self):
        result = []
        command = ["python", "tool.py", "--check"]
        worker = threading.Thread(target=lambda: result.append(self.bridge._approve_command(command)))
        worker.start()
        deadline = time.monotonic() + 2
        approval = None
        while time.monotonic() < deadline and approval is None:
            approval = next((event for event in self.bridge.poll_events()
                             if event.get("type") == "approval"), None)
            time.sleep(0.01)
        self.assertIsNotNone(approval)
        self.assertTrue(self.bridge.resolve_approval(
            approval["approval_id"], True, "exact_task")["ok"])
        worker.join(timeout=2)
        self.assertEqual(result, [True])
        self.assertTrue(self.bridge._approve_command(command))

        changed = []
        different = ["python", "tool.py", "--write"]
        second = threading.Thread(target=lambda: changed.append(self.bridge._approve_command(different)))
        second.start()
        approval = None
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and approval is None:
            approval = next((event for event in self.bridge.poll_events()
                             if event.get("type") == "approval" and event.get("command") == different), None)
            time.sleep(0.01)
        self.assertIsNotNone(approval)
        self.bridge.resolve_approval(approval["approval_id"], False)
        second.join(timeout=2)
        self.assertEqual(changed, [False])

    def test_bridge_bootstrap_graph_and_bounded_ui_state(self):
        self.bridge.agent.sandbox.write_file("project/app.py", "print('ok')")
        self.bridge.agent.log.record("test_event", "ok")
        bootstrap = self.bridge.get_bootstrap()
        self.assertEqual(bootstrap["app"], "Aura")
        self.assertEqual(bootstrap["actions"][-1]["action"], "test_event")
        graph = self.bridge.get_mind_graph()
        self.assertTrue(graph["ok"])
        self.assertTrue(any(node["node_id"] == "file:project/app.py" for node in graph["nodes"]))
        self.assertTrue(self.bridge.save_ui_state({"sidebar_width": 9999, "log_height": 1})["ok"])
        self.assertEqual(self.bridge.agent.config.data["web_sidebar_width"], 420)
        self.assertEqual(self.bridge.agent.config.data["web_log_height"], 90)

    def test_bootstrap_activity_is_limited_to_the_current_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent = AuraAgent(Path(temporary) / "workspace", provider=MockProvider())
            agent.log.record("older_process_event", "error")
            bridge = AuraWebBridge(agent=agent, speech=SpeechOutput(enabled=False))
            try:
                agent.log.record("current_process_event", "ok")
                actions = bridge.get_bootstrap()["actions"]
                self.assertEqual([item["action"] for item in actions], ["current_process_event"])
            finally:
                bridge.shutdown()

    def test_bridge_rejects_oversized_messages(self):
        result = self.bridge.submit("x" * 12_001)
        self.assertFalse(result["ok"])
        self.assertIn("12,000", result["error"])


class HTMLServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.bridge = AuraWebBridge(agent=agent, speech=SpeechOutput(enabled=False))
        self.server = create_server(self.bridge, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = self.server.origin
        with urlopen(self.base + "/", timeout=3) as response:
            self.index = response.read().decode("utf-8")
            self.cookie = response.headers["Set-Cookie"].split(";", 1)[0]
            self.index_headers = response.headers

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.bridge.shutdown()
        self.temp.cleanup()

    def call(self, method, *args):
        request = Request(
            self.base + "/api/call",
            data=json.dumps({"method": method, "args": list(args)}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Cookie": self.cookie,
                "Origin": self.base,
                "X-Aura-Client": "html-ui-v1",
            },
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            envelope = json.loads(response.read().decode("utf-8"))
        self.assertTrue(envelope["ok"])
        return envelope["result"]

    def test_serves_html_with_local_security_headers_and_health(self):
        self.assertIn("Aura Mind", self.index)
        self.assertIn('id="avatarCanvas"', self.index)
        self.assertIn('class="avatar-stage model-3d"', self.index)
        self.assertIn('src="avatar-face.js"', self.index)
        self.assertNotIn("aura-portrait-reference.png", self.index)
        self.assertIn("settingAutonomy", self.index)
        self.assertIn("settingReasoning", self.index)
        self.assertIn("allowTaskApproval", self.index)
        self.assertIn("settingLearning", self.index)
        self.assertIn("voiceTray", self.index)
        self.assertIn("settingVoiceEngine", self.index)
        self.assertIn("settingMicrophone", self.index)
        self.assertIn("calibrateMicrophone", self.index)
        self.assertIn("previewVoice", self.index)
        self.assertIn("memoryButton", self.index)
        self.assertIn("memoryModal", self.index)
        self.assertIn("default-src 'self'", self.index_headers["Content-Security-Policy"])
        self.assertEqual(self.index_headers["X-Frame-Options"], "DENY")
        with urlopen(self.base + "/health", timeout=3) as response:
            health = json.loads(response.read().decode("utf-8"))
            self.assertEqual(response.headers["X-Aura-Server"], "html-local-v1")
        self.assertEqual(health["interface"], "html")
        self.assertEqual(existing_aura_url(self.server.server_address[1]), self.base)

    def test_serves_holographic_avatar_motion_without_external_assets(self):
        with urlopen(self.base + "/app.js", timeout=3) as response:
            script = response.read().decode("utf-8")
        with urlopen(self.base + "/styles.css", timeout=3) as response:
            styles = response.read().decode("utf-8")
        with urlopen(self.base + "/avatar-face.js", timeout=3) as response:
            model = response.read().decode("utf-8")
            model_type = response.headers.get_content_type()
        self.assertIn("avatarMotion?.setSpeaking", script)
        self.assertIn("new AuraAvatar3D", script)
        self.assertNotIn("class HolographicAvatar", script)
        self.assertIn("window.location.reload()", script)
        self.assertIn("setLogMode(\"activity\")", script)
        self.assertIn("class AuraAvatar3D", model)
        self.assertIn("FEMININE HEAD", model)
        self.assertIn("STATIC MESH", model)
        self.assertIn("HAIR", model)
        self.assertIn("function drawEyes", model)
        self.assertIn("function drawMouth", model)
        self.assertIn("function faceDepth", model)
        self.assertIn("canvas.dataset.renderer='canvas-depth-projection'", model)
        self.assertIn("canvas.dataset.model='feminine-digital-human'", model)
        self.assertIn("setSpeaking(active)", model)
        self.assertIn("setSpeechCues(cues,durationMs", model)
        self.assertIn("applySettings(values={})", model)
        self.assertIn("IntersectionObserver", model)
        self.assertIn("visibilitychange", model)
        self.assertIn("prefers-reduced-motion", model)
        self.assertIn("speech_cues", script)
        self.assertIn("voice_session", script)
        self.assertIn("pointerdown", script)
        self.assertIn('callApi("stop_voice"', script)
        self.assertIn("settingAvatarIntensity", self.index)
        self.assertIn("setGaze(x,y)", model)
        self.assertNotIn("speechSynthesis", model)
        self.assertIn(".avatar-canvas", styles)
        self.assertIn(".model-3d::before", styles)
        self.assertIn("@keyframes scan-face", styles)
        self.assertEqual(model_type, "text/javascript")
        self.assertGreater(len(model), 20_000)
        self.assertFalse((Path(__file__).parents[1] / "aura" / "web" /
                          "aura-portrait-reference.png").exists())
        self.assertFalse((Path(__file__).parents[1] / "aura" / "web" /
                          "avatar3d.js").exists())
        with self.assertRaises(HTTPError) as missing_portrait:
            urlopen(self.base + "/aura-portrait-reference.png", timeout=3)
        self.assertEqual(missing_portrait.exception.code, 404)
        with self.assertRaises(HTTPError) as obsolete_renderer:
            urlopen(self.base + "/avatar3d.js", timeout=3)
        self.assertEqual(obsolete_renderer.exception.code, 404)

    def test_workspace_preview_serves_relative_assets_read_only(self):
        self.bridge.agent.sandbox.write_file(
            "site/index.html",
            '<!doctype html><html><head><link rel="stylesheet" href="style.css">'
            '</head><body><h1>Styled</h1><script src="app.js"></script></body></html>',
        )
        self.bridge.agent.sandbox.write_file("site/style.css", "h1 { color: rgb(12, 34, 56); }")
        self.bridge.agent.sandbox.write_file("site/app.js", "document.body.dataset.ran = 'yes';")
        preview = self.call("preview_workspace_file", "site/index.html")
        request = Request(self.base + preview["url"], headers={"Cookie": self.cookie})
        with urlopen(request, timeout=3) as response:
            html = response.read().decode("utf-8")
            self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
            self.assertIn("script-src 'none'", response.headers["Content-Security-Policy"])
        self.assertIn('href="style.css"', html)
        style_request = Request(self.base + "/workspace-preview/site/style.css",
                                headers={"Cookie": self.cookie})
        with urlopen(style_request, timeout=3) as response:
            self.assertIn("rgb(12, 34, 56)", response.read().decode("utf-8"))
            self.assertEqual(response.headers.get_content_type(), "text/css")
        with self.assertRaises(HTTPError) as unauthorized:
            urlopen(self.base + preview["url"], timeout=3)
        self.assertEqual(unauthorized.exception.code, 403)

    def test_workspace_mutation_methods_are_reachable_through_the_api(self):
        created = self.call("create_workspace_file", "note.txt", "hi")
        self.assertTrue(created["ok"])
        snapshot = self.call("workspace_snapshot")
        self.assertIn("note.txt", [item["path"] for item in snapshot["files"]])
        deleted = self.call("delete_workspace_item", "note.txt")
        self.assertTrue(deleted["ok"])
        listed = self.call("list_trash")
        self.assertTrue(any(item["trash_name"] == deleted["trashed_as"] for item in listed["items"]))
        restored = self.call("restore_workspace_item", deleted["trashed_as"])
        self.assertEqual(restored["path"], "note.txt")

    def test_preview_server_methods_are_reachable_through_the_api(self):
        self.bridge.agent.sandbox.create_file("site/index.html", "served live")
        started = self.call("start_preview_server", "site")
        self.assertTrue(started["running"])
        try:
            with urlopen(started["url"] + "index.html", timeout=3) as response:
                self.assertEqual(response.read(), b"served live")
        finally:
            self.assertTrue(self.call("stop_preview_server")["ok"])

    def test_headless_browser_boots_ui_and_captures_styled_preview(self):
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        ]
        browser = next((path for path in candidates if path.is_file()), None)
        if browser is None:
            self.skipTest("Chrome or Edge is not installed for the optional browser smoke test")
        self.bridge.agent.sandbox.write_file(
            "site/index.html",
            '<!doctype html><html><head><link rel="stylesheet" href="style.css">'
            '</head><body><h1>Browser verified</h1></body></html>',
        )
        self.bridge.agent.sandbox.write_file(
            "site/style.css", "body { background: rgb(18, 31, 47); } h1 { color: white; }",
        )
        screenshot = Path(self.temp.name) / "phase39-preview.png"
        profile = Path(self.temp.name) / "headless-profile"
        command = [
            str(browser), "--headless", "--disable-gpu", "--hide-scrollbars",
            "--window-size=1200,800", "--timeout=5000",
            "--no-first-run", "--no-default-browser-check",
            f"--screenshot={screenshot}", f"--user-data-dir={profile}",
            self.base + "/?preview=site/index.html",
        ]
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=creation_flags)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if screenshot.is_file() and screenshot.stat().st_size > 10_000:
                break
            if process.poll() is not None:
                break
            time.sleep(0.1)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        self.assertTrue(screenshot.is_file())
        self.assertGreater(screenshot.stat().st_size, 10_000)

    def test_rejects_api_calls_without_same_origin_session(self):
        request = Request(
            self.base + "/api/call",
            data=b'{"method":"get_bootstrap","args":[]}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=3)
        self.assertEqual(raised.exception.code, 403)

    def test_agent_can_inspect_local_http_without_command_execution(self):
        result = self.bridge.agent._execute_tool(ToolCall("http", "http_get", {
            "url": self.base + "/health", "timeout": 3
        }), None)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], 200)
        self.assertIn('"ready": true', result["content"])

    def test_authenticated_html_api_drives_bridge_and_events(self):
        bootstrap = self.call("get_bootstrap")
        self.assertEqual(bootstrap["app"], "Aura")
        self.assertIn("event_cursor", bootstrap)
        self.assertGreaterEqual(bootstrap["capabilities"]["tools"], 34)
        self.assertEqual(bootstrap["capabilities"]["personal_memories"], 0)
        self.assertTrue(self.call("workspace_snapshot")["ok"])
        learned = self.call("add_personal_memory", "interest", "local AI companions")
        self.assertTrue(learned["ok"])
        self.assertEqual(self.call("get_personal_memory")["count"], 1)
        self.assertTrue(self.call("submit", "hello")["ok"])
        events = []
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            events.extend(self.call("poll_events", 100))
            if any(event.get("type") == "busy" and event.get("value") is False for event in events):
                break
            time.sleep(0.01)
        self.assertTrue(any(event.get("type") == "reply" for event in events))
        graph = self.call("get_mind_graph")
        self.assertTrue(graph["ok"])


if __name__ == "__main__":
    unittest.main()
