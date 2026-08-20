import base64
import io
import base64
import inspect
import json
import math
import os
import queue
import re
import sqlite3
import struct
import subprocess
import tempfile
import shutil
import socket
import sys
import threading
from uuid import uuid4
from http.server import BaseHTTPRequestHandler, HTTPServer
import time
import unittest
import unittest.mock
import wave
import zlib
import zipfile
from array import array
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import package
import aura_app
from aura import __version__ as aura_version
from aura import checks
from aura import health
from aura import language
from aura import routing
from aura import services
from aura import search_service
from aura import toolkit
from aura import websearch
from aura.turn import PASS, TurnState
from aura.action_log import ActionLog
from aura.autonomy import AutonomyGuard
from aura.scheduler import Scheduler
from aura.agent import AuraAgent, TaskCancelled, TurnExpired
from aura.errors import AuraError
from aura.memory import MemoryStore
from aura.config import ConfigStore
from aura.commands import CommandResult
from aura.http_app import API_METHODS, create_server, existing_aura_url
from aura.graph_model import build_mind_graph
from aura.image_diff import UnsupportedImage, compare_images, decode_png
from aura.permissions import (ExternalReader, ExternalWriter, PermissionDenied,
                              PermissionRefused, PermissionStore,
                              is_private_address)
from aura.preview_server import PreviewServer
from aura.cloud import AnthropicProvider, OpenAIProvider
from aura.provider import (LMStudioProvider, MockProvider,
                           OpenAICompatibleProvider,
                           ProviderContext, ProviderError,
                           ProviderReply, ToolCall)
from aura.speech import SpeechOutput
from aura.store import Database
from aura.tasks import TaskJournal
from aura import code_exec
from aura import context_budget
from aura import empty_reply
from aura import tool_errors
from aura import sampling
from aura.validation import check_accessibility, check_broken_assets
from aura.voice import VoiceInput
from aura.safety import SandboxViolation, WorkspaceSandbox
from aura.screenshot import find_browser
from aura.search_index import WorkspaceIndex, tokenize
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

    def test_assets_named_only_in_a_data_file_are_checked(self):
        """The shop's real failure: two images 404, validation said no issues.

        `img.src = product.image` puts the path in the data rather than the
        code, so neither the HTML scan nor the script scan could see it.
        """
        self.box.create_file("shop/index.html", '<html><body><div id="list"></div></body></html>')
        self.box.create_file("shop/img/have.png", "x")
        self.box.create_file(
            "shop/data/products.json",
            '{"products": [{"image": "../img/have.png"}, {"image": "../assets/gone.png"},'
            ' {"image": "https://cdn.example.com/remote.png"}, {"name": "Not.A.Path"}]}')
        result = check_broken_assets(self.box, "shop")
        self.assertTrue(result["ok"])
        self.assertEqual([item["reference"] for item in result["broken"]], ["../assets/gone.png"])

    def test_data_asset_resolving_from_the_project_root_is_not_reported(self):
        """Whoever wrote the data file may have meant the path from the page.

        Reporting a path that resolves either way would flag working code, and
        a checker that cries wolf is one nobody reads.
        """
        self.box.create_file("shop/index.html", "<html></html>")
        self.box.create_file("shop/img/logo.png", "x")
        self.box.create_file("shop/data/site.json", '{"logo": "img/logo.png"}')
        self.assertEqual(check_broken_assets(self.box, "shop")["broken"], [])


class EditAnchorTests(unittest.TestCase):
    """A refused edit should leave the model better informed than before."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.agent.sandbox.write_file(
            "site/index.html",
            "\n".join(['<main>', '  <div class="product-card">',
                       '    <h2>Shoe</h2>', '  </div>', '</main>', '']))

    def tearDown(self):
        self.temp.cleanup()

    def edit(self, anchor):
        return self.agent._execute_tool(
            ToolCall("i", "replace_in_file",
                     {"path": "site/index.html", "old_text": anchor, "new_text": "X"}),
            lambda *_: True).get("error", "")

    def test_an_anchor_that_starts_wrong_still_finds_the_real_line(self):
        """The substring probe only recognises an anchor that begins correctly,
        so `<section>` written for a `<div>` used to find nothing at all."""
        message = self.edit('<section class="product-card">')
        self.assertIn("are close", message)
        self.assertIn('<div class="product-card">', message)

    def test_a_genuinely_absent_anchor_is_not_given_a_false_neighbour(self):
        message = self.edit('import React from "react";')
        self.assertIn("not in the file at all", message)
        self.assertNotIn("are close", message)

    def test_an_ambiguous_anchor_lists_where_it_matched(self):
        self.agent.sandbox.write_file("site/rows.html",
                                      "\n".join(["<p>row</p>"] * 3) + "\n")
        message = self.agent._execute_tool(
            ToolCall("i", "replace_in_file",
                     {"path": "site/rows.html", "old_text": "<p>row</p>", "new_text": "X"}),
            lambda *_: True)["error"]
        self.assertIn("found 3 matches", message)
        self.assertIn("line 1", message)

    def test_a_crlf_file_survives_a_multi_line_edit(self):
        """The model reads LF because Aura normalises, so the anchor it copies
        is LF — and the file on disk must still come back as CRLF."""
        crlf = chr(13) + chr(10)
        raw = crlf.join(["<div>", "  <p>one</p>", "  <p>two</p>",
                         "</div>", ""]).encode("utf-8")
        target = self.agent.sandbox.root / "site" / "crlf.html"
        target.write_bytes(raw)
        result = self.agent._execute_tool(
            ToolCall("i", "replace_in_file",
                     {"path": "site/crlf.html",
                      "old_text": "\n".join(["  <p>one</p>", "  <p>two</p>"]),
                      "new_text": "  <p>X</p>"}), lambda *_: True)
        self.assertTrue(result.get("ok"), result.get("error"))
        self.assertIn(crlf.encode("utf-8"), target.read_bytes())


class SilentFailureTests(unittest.TestCase):
    """Failures in the record-keeping used to leave no record."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())

    def tearDown(self):
        self.temp.cleanup()

    def errors(self, action):
        return [row for row in self.agent.log.recent(60)
                if row.get("action") == action and row.get("status") == "error"]

    def test_a_plan_step_that_cannot_be_saved_says_so(self):
        """Otherwise the durable plan simply stops advancing, which is the shape
        of the bug that took a day to find — arriving next time with no
        evidence at all."""
        self.agent.current_project = "shop"
        self.agent.sandbox.write_file("shop/index.html", "<html></html>")
        self.agent.db.set_plan_steps("shop", ["Create shop/index.html with layout"])
        def refuse(*_args, **_kwargs):
            raise RuntimeError("database is locked")
        self.agent.db.set_step_status = refuse
        turn = TurnState()
        turn.successful_tools = 1
        turn.workspace_mutation = True
        self.agent._gate_plan_progress(turn, None)
        self.assertTrue(self.errors("plan_step"))

    def test_a_plan_that_cannot_be_read_says_so(self):
        self.agent.current_project = "shop"
        def refuse(*_args, **_kwargs):
            raise RuntimeError("database is gone")
        self.agent.db.plan_steps = refuse
        turn = TurnState()
        turn.successful_tools = 1
        self.assertEqual(self.agent._gate_plan_progress(turn, None), PASS)
        self.assertTrue(self.errors("plan_steps_read"))

    def test_recording_the_failure_can_never_itself_break_the_turn(self):
        """These failures are usually the database, and the log lives in the
        same database."""
        def refuse(*_args, **_kwargs):
            raise RuntimeError("no")
        self.agent.log.record = refuse
        self.agent._note_bookkeeping_failure("plan_step", "shop", RuntimeError("x"))


class ProjectAnnouncementTests(unittest.TestCase):
    """Which project is in play has to be said before the work, not after.

    It decides which memories are recalled and which rules apply, so learning
    it only with the reply is learning it too late to say "no, not that one".
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name) / "workspace"
        (root / "shop").mkdir(parents=True)
        (root / "promo").mkdir()
        self.agent = AuraAgent(root, provider=MockProvider())
        self.bridge = AuraWebBridge(agent=self.agent, speech=SpeechOutput(enabled=False))
        # The bridge owns threads that hold the database open, so closing the
        # connection is not enough to let Windows delete the directory.
        self.addCleanup(self.bridge.shutdown)

    def test_the_interface_is_told_before_the_work_not_after(self):
        events = []
        self.bridge._push = lambda name, **fields: events.append((name, fields))
        self.bridge._work("Tee shop kausta avaleht valmis")
        names = [name for name, _ in events]
        self.assertIn("project", names)
        announced = next(fields for name, fields in events if name == "project")
        self.assertEqual(announced.get("project"), "shop")
        self.assertLess(names.index("project"), len(names) - 1)




class ForeignNameTests(unittest.TestCase):
    """An Estonian name inside an English sentence is a name, not a language.

    Asked "What's the current weather in Estonia Jõgeva", Aura answered in
    Estonian — badly enough to render "today's high" as "tänapäeva kõrge" —
    because one `õ` settled the language of the whole sentence before a single
    word was counted. She was also asked in English, and the reply is spoken
    aloud in a voice Mat does not want speaking Estonian.
    """

    def test_an_english_question_about_an_estonian_place_stays_english(self):
        for question in ("What is the current weather in Estonia Jõgeva",
                         "What is the current weather in Estonia Võru",
                         "Please read Jõgeva.txt and tell me what it says"):
            with self.subTest(question=question):
                self.assertEqual(language.detect(question), "en")

    def test_a_real_estonian_sentence_is_still_estonian(self):
        for question in ("Kas sa võiksid teha mulle veebilehe?",
                         "Tere! Kuidas sul läheb täna?",
                         "Palun tee Jõgeva kohta üks leht",
                         "Mis on ilm Jõgeval?"):
            with self.subTest(question=question):
                self.assertEqual(language.detect(question), "et")

    def test_estonian_carrying_an_english_phrase_is_still_estonian(self):
        """The reverse mistake would be just as wrong."""
        self.assertEqual(language.detect("Tee mulle üks landing page Võrus"), "et")

    def test_a_place_name_is_percent_encoded_in_the_url(self):
        """`Jõgeva` went raw onto the wire and came back empty; the model
        recovered by guessing `Jogeva`, which cost a round trip and only
        worked because the letter happened to have an ASCII cousin."""
        asked = []

        def fetch(url, timeout=10.0):
            asked.append(url)
            if "search" in url:
                return {"results": [{"latitude": 58.7, "longitude": 26.4,
                                     "name": "Jõgeva", "country": "Estonia"}]}
            return {"current": {"temperature_2m": 16.6, "weather_code": 3},
                    "daily": {"temperature_2m_max": [19.7], "temperature_2m_min": [6.7]}}

        service = services.get("get_weather")
        service.handler(fetch, {"place": "Jõgeva"})
        self.assertIn("name=J%C3%B5geva", asked[0])
        self.assertNotIn("õ", asked[0])

    def test_a_space_in_a_place_name_still_works(self):
        asked = []

        def fetch(url, timeout=10.0):
            asked.append(url)
            if "search" in url:
                return {"results": [{"latitude": 1.0, "longitude": 2.0,
                                     "name": "New York", "country": "USA"}]}
            return {"current": {}, "daily": {}}

        services.get("get_weather").handler(fetch, {"place": "New York"})
        self.assertIn("name=New%20York", asked[0])


class NetworkReachTests(unittest.TestCase):
    """What Aura may read now that the domain allowlist is gone.

    Mat removed it: naming every site by hand cost him more than it protected.
    The address check stayed, and it is a different thing — it refuses a public
    name that resolves onto this machine or the network it sits on.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())

    def test_no_tool_can_grant_itself_anything(self):
        """The rule that outlived the allowlist: reach is something Mat gives,
        never something the model asks for mid-turn."""
        offered = {item["function"]["name"] for item in self.agent.tool_definitions()}
        self.assertFalse([name for name in offered if name.startswith("grant_")])

    def test_a_name_resolving_onto_the_local_network_is_refused(self):
        """Checked on every hop rather than once, because DNS can change
        between one request and the next."""
        with patch("aura.agent.reject_unsafe_host",
                   side_effect=PermissionRefused("rebound.test resolves to a private address")):
            result = self.agent._execute_tool(
                ToolCall("1", "http_get", {"url": "https://rebound.test/"}), None)
        self.assertFalse(result["ok"])
        self.assertIn("private address", result["error"])
        self.assertEqual(self.agent.fetched_sources, [])

    def test_a_private_address_is_recognised_whatever_shape_it_arrives_in(self):
        for address in ("127.0.0.1", "192.168.1.1", "10.0.0.5", "169.254.169.254"):
            with self.subTest(address=address):
                self.assertTrue(is_private_address(address))
        self.assertFalse(is_private_address("93.184.216.34"))

    def test_the_reply_still_names_every_address_it_read(self):
        """The half of the old model that mattered most: she cites what she
        read, so an answer can be checked against its source."""
        report = AuraAgent._format_completion_evidence(
            "It is 22 degrees in Tartu.", None, None, [], [], None,
            ["https://api.open-meteo.com/v1/forecast?x=1",
             "https://api.open-meteo.com/v1/forecast?x=1"])
        self.assertIn("Read from the network:", report)
        self.assertEqual(report.count("https://api.open-meteo.com"), 1)


class NetworkStatusTests(unittest.TestCase):
    """The panel reports what she reads, not what she has been allowed to."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.bridge = AuraWebBridge(agent=self.agent, speech=SpeechOutput(enabled=False))
        self.addCleanup(self.bridge.shutdown)

    def test_she_is_online_without_anything_being_granted(self):
        status = self.bridge.network_status()["network"]
        self.assertTrue(status["online"])

    def test_the_panel_names_what_the_built_in_services_read(self):
        status = self.bridge.network_status()["network"]
        self.assertIn("api.open-meteo.com", status["domains"])
        self.assertTrue(status["services"])

    def test_revoking_everything_leaves_the_web_reachable(self):
        """Revoke clears folder grants. It never took the web away, and now
        there is nothing about the web for it to take."""
        self.bridge.revoke_all_permissions()
        self.assertTrue(self.bridge.network_status()["network"]["online"])


class EveryToolRunsTests(unittest.TestCase):
    """Call all 57 tools once and see which are actually broken.

    The unit tests above cover tools one at a time, in the shape each was
    written for. This calls every one with arguments a sensible caller would
    pass, which is a different question and catches a different fault: it found
    `list_external_folder` answering "that is a folder, not a file" about a
    folder it exists to list, because `PermissionDenied` subclasses
    `PermissionError` and a translator meant for filesystem errors had caught
    Aura's own refusal.

    Order matters and is fixed here rather than left to the registry: `forget`
    sorts before `correct`, so running in definition order deletes the memory
    before the correction creates it.
    """

    #: Tools that cannot succeed in a scratch workspace, and why. Listed rather
    #: than skipped, so "it refused" is a claim this test makes on purpose.
    REFUSES = {
        "rollback_task": "no such task",
        "undo_external_change": "nothing was ever written outside",
        "look_at_image": "the mock model has no vision",
        "search_web": "no search service is configured here",
        "read_external_file": "no folder grant",
        "write_external_file": "no folder grant",
        "list_external_folder": "no folder grant",
        "http_get": "no network in tests",
        "get_weather": "no network in tests",
    }

    #: Run before the alphabetical sweep, in this order, because they depend on
    #: each other.
    ORDERED = ("remember_personal_fact", "correct_personal_fact",
               "forget_personal_fact")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        box = self.agent.sandbox
        box.write_file("notes.txt", "first line\n")
        box.write_file("site/index.html",
                       "<html><head><title>Shop</title></head><body>"
                       "<h1>Shop</h1></body></html>")
        box.write_file("site/app.js", "const grid = document.body;\n")
        box.write_file("site/style.css", "body { margin: 0; }\n")
        # compare_files sorts before write_files, so its operands cannot be
        # something an earlier tool in the sweep happened to create.
        box.write_file("site/one.txt", "one\n")
        box.write_file("site/two.txt", "two\n")
        for name in ("shot.png", "shot2.png"):
            (box.root / name).write_bytes(self.png())
        self.agent.current_project = "site"

    @staticmethod
    def png(colour=(255, 0, 0), size=4):
        raw = b"".join(b"\x00" + bytes(colour) * size for _ in range(size))
        def chunk(tag, data):
            body = tag + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw))
                + chunk(b"IEND", b""))

    def arguments(self):
        outside = str(Path(self.temp.name) / "outside")
        return {
            "list_files": {"path": "."},
            "read_file": {"path": "site/index.html"},
            "read_many_files": {"paths": ["site/index.html", "site/app.js"]},
            "file_info": {"path": "site/index.html"},
            "append_file": {"path": "notes.txt", "content": "another line\n"},
            "write_file": {"path": "site/fresh.txt", "content": "written"},
            "create_file": {"path": "site/created.txt", "content": "created"},
            "create_folder": {"path": "site/assets"},
            "write_files": {"files": [{"path": "site/one.txt", "content": "1"},
                                      {"path": "site/two.txt", "content": "2"}]},
            "replace_in_file": {"path": "site/app.js", "old_text": "const grid",
                                "new_text": "const productGrid"},
            "apply_edits": {"path": "site/index.html",
                            "edits": [{"old_text": "<title>Shop</title>",
                                       "new_text": "<title>Shop 2</title>"}]},
            "copy_file": {"source": "notes.txt", "destination": "notes-copy.txt"},
            "move_file": {"source": "notes-copy.txt", "destination": "site/moved.txt"},
            "safe_delete_file": {"path": "site/moved.txt"},
            "create_archive": {"source": "site", "destination": "site.zip"},
            "extract_archive": {"archive": "site.zip", "destination": "unpacked"},
            "search_files": {"query": "index", "path": "."},
            "search_text": {"query": "grid", "path": "."},
            "compare_files": {"left": "site/one.txt", "right": "site/two.txt"},
            "find_relevant_files": {"query": "product grid", "path": "."},
            "workspace_summary": {},
            "inspect_code": {"path": "site/app.js"},
            "validate_project": {"path": "site"},
            "check_accessibility": {"path": "site"},
            "undo_last_change": {},
            "rollback_task": {"task_id": "no-such-task"},
            "change_history": {"limit": 5},
            "undo_external_change": {},
            "recent_tasks": {"limit": 5},
            "remember_name": {"name": "Mat"},
            "remember_lesson": {"lesson": "asset paths stay relative"},
            "remember_preference": {"key": "tone", "value": "direct"},
            "remember_personal_fact": {"category": "interest", "value": "electronics"},
            "correct_personal_fact": {"query": "electronics", "new_value": "synthesisers"},
            "forget_personal_fact": {"query": "synthesisers"},
            "list_personal_memory": {},
            "list_granted_folders": {},
            "record_plan_steps": {"steps": ["Create site/index.html", "Style it"]},
            "update_plan_step": {"step": "Style it", "status": "done",
                                 "evidence": "stylesheet written"},
            "look_at_image": {"path": "shot.png"},
            "compare_images": {"first": "shot.png", "second": "shot2.png"},
            "capture_page": {"path": "site/index.html"},
            "open_workspace_item": {"path": "site/index.html"},
            "run_command": {"command": ["python", "--version"], "timeout": 20},
            "system_info": {},
            "capability_summary": {},
            "self_check": {},
            "how_i_have_been_running": {"days": 7},
            "calculate": {"expression": "17 * 3 + 2"},
            "execute_code": {"script": "print('ran')", "purpose": "smoke"},
            "set_check": {"check": "broken_links", "every_minutes": 120},
            "set_reminder": {"text": "stretch", "in_minutes": 60},
            "search_web": {"query": "quantum mechanics"},
            "http_get": {"url": "https://example.invalid/"},
            "get_weather": {"place": "Tallinn"},
            "read_external_file": {"path": outside + "/a.txt"},
            "write_external_file": {"path": outside + "/a.txt", "content": "x"},
            "list_external_folder": {"path": outside},
        }

    def test_every_tool_has_arguments_here(self):
        """A new tool must be added to this sweep, or it is never exercised."""
        every = {item["function"]["name"] for item in AuraAgent.tool_definitions()}
        self.assertEqual(every - set(self.arguments()), set())

    def test_no_tool_raises_and_every_one_answers(self):
        args = self.arguments()
        names = self.ORDERED + tuple(
            name for name in sorted(args) if name not in self.ORDERED)
        broken = []
        for name in names:
            with patch("aura.agent.AuraAgent._http_get",
                       side_effect=RuntimeError("no network in tests")):
                result = self.agent._execute_tool(
                    ToolCall("t", name, args[name]), lambda *_: True)
            self.assertIn("ok", result, name)
            if not result.get("ok") and name not in self.REFUSES:
                broken.append(f"{name}: {result.get('error')}")
        self.assertEqual(broken, [])

    def test_a_refusal_never_describes_a_folder_as_a_file(self):
        """The bug this sweep found: `list_external_folder` exists to take a
        folder, and was told it had named one where a file belonged."""
        result = self.agent._execute_tool(
            ToolCall("t", "list_external_folder",
                     {"path": str(Path(self.temp.name) / "outside")}), lambda *_: True)
        self.assertFalse(result["ok"])
        self.assertNotIn("not a file", result["error"])
        self.assertIn("permission", result["error"].casefold())


class AskingIfSomethingIsSoundTests(unittest.TestCase):
    """Measured against the loaded model, not reasoned about.

    Twelve ordinary requests were put to gpt-oss with the real system prompt
    and the tools routing offers. Two came back wrong for reasons on this side
    of the wire rather than the model's.
    """

    def offered(self, message):
        return {item["function"]["name"] for item in
                routing.select(message, "powerful", "balanced",
                               AuraAgent.tool_definitions())}

    def test_asking_whether_something_is_broken_offers_the_tool_that_answers(self):
        """"Is anything broken in shop?" carries none of the verbs the rule
        looked for, so it came back with the read-only fallback and not the one
        tool that could have answered it."""
        for question in ("Is anything broken in shop?",
                         "What is wrong with my shop page?",
                         "Are there problems with the shop project?",
                         "Any errors in shop?"):
            with self.subTest(question=question):
                self.assertIn("validate_project", self.offered(question))

    def test_a_shell_is_not_offered_beside_the_tool_that_answers(self):
        """Asked to check the shop, the model reached past the offered
        `validate_project` for `python -m validate_project` — a shell next to a
        tool that does the same job is an invitation, and it cost a round and a
        refusal to decline it."""
        for question in ("Check the shop project for problems",
                         "Is anything broken in shop?",
                         "Validate the shop project"):
            with self.subTest(question=question):
                offered = self.offered(question)
                self.assertIn("validate_project", offered)
                self.assertNotIn("run_command", offered)

    def test_actually_running_something_still_offers_the_shell(self):
        """The narrowing must not cost the case run_command exists for."""
        for request in ("Run the tests", "Run python hello.py",
                        "Execute the build script"):
            with self.subTest(request=request):
                self.assertIn("run_command", self.offered(request))


class ExecuteCodeTests(unittest.TestCase):
    """A script the model wrote, with Aura's tools inside it.

    The point is context: reading six files one call at a time put 2,712
    characters into the conversation and left them there; the same six read
    inside a script and summarised cost 158. That is the whole reason this
    exists, and the reason it is worth the surface it adds.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        for index in range(4):
            self.agent.sandbox.write_file(f"note{index}.txt", f"line {index}\n" * 10)
        self.agent.sandbox.write_file("a.py", "old_api(1)\n")
        self.agent.sandbox.write_file("b.py", "old_api(2)\n")

    def run_script(self, script, **extra):
        return self.agent._execute_tool(
            ToolCall("c", "execute_code", {"script": script, **extra}),
            lambda *_: True)

    def test_a_script_can_read_the_workspace_through_the_real_tools(self):
        result = self.run_script(
            "from aura_tools import list_files, read_file\n"
            "names = [f for f in list_files(path='.')['files'] if f.endswith('.txt')]\n"
            "print(len(names), sum(len(read_file(path=n)['content']) for n in names))")
        self.assertEqual(result["status"], "ok", result.get("error"))
        self.assertTrue(result["output"].startswith("4 "))
        self.assertGreater(result["tool_calls"], 1)

    def test_edits_made_by_a_script_really_land(self):
        result = self.run_script(
            "from aura_tools import replace_in_file\n"
            "for name in ('a.py', 'b.py'):\n"
            "    replace_in_file(path=name, old_text='old_api', new_text='new_api')\n"
            "print('done')")
        self.assertEqual(result["status"], "ok", result.get("error"))
        self.assertIn("new_api", self.agent.sandbox.read_file("a.py"))
        self.assertIn("new_api", self.agent.sandbox.read_file("b.py"))

    def test_only_what_it_printed_comes_back(self):
        """The intermediate reads must not reach the conversation, or the tool
        has cost a process and saved nothing."""
        result = self.run_script(
            "from aura_tools import read_file\n"
            "body = read_file(path='note0.txt')['content']\n"
            "print('length', len(body))")
        self.assertEqual(result["output"].strip(), "length 70")
        self.assertNotIn("line 0", json.dumps(result))

    def test_a_withheld_tool_is_not_in_the_stub(self):
        """`run_command` carries the approval prompt, and approving a script is
        not approving every command it might run."""
        result = self.run_script(
            "from aura_tools import run_command\nprint('should not get here')")
        self.assertEqual(result["status"], "error")
        self.assertNotIn("should not get here", result.get("output", ""))

    def test_a_tool_not_on_the_list_is_refused_over_the_wire_too(self):
        """The stub is a convenience; the bridge is the enforcement."""
        bridge = code_exec.ToolBridge(lambda name, args: {"ok": True},
                                      allowed=frozenset({"read_file"}), max_calls=5)
        try:
            answer = bridge._answer({"token": bridge.token, "tool": "run_command",
                                     "arguments": {}})
        finally:
            bridge.stop()
        self.assertFalse(answer["ok"])
        self.assertIn("cannot be called from a script", answer["error"])

    def test_a_call_without_the_token_is_refused(self):
        """A loopback port has no owner, so any process on this machine could
        otherwise drive Aura's tools."""
        bridge = code_exec.ToolBridge(lambda name, args: {"ok": True},
                                      allowed=frozenset({"read_file"}), max_calls=5)
        try:
            answer = bridge._answer({"token": "guessed", "tool": "read_file",
                                     "arguments": {}})
        finally:
            bridge.stop()
        self.assertFalse(answer["ok"])
        self.assertIn("not authorised", answer["error"])

    def test_a_script_that_runs_away_is_stopped(self):
        self.agent.SCRIPT_TIMEOUT = 3.0
        result = self.run_script("while True:\n    pass")
        self.assertEqual(result["status"], "timeout")

    def test_a_script_that_prints_nothing_is_told_so(self):
        """An empty result reads as silence, which is nearly always a mistake
        rather than an answer."""
        result = self.run_script("from aura_tools import list_files\nlist_files(path='.')")
        self.assertEqual(result["status"], "no_output")
        self.assertIn("print", result["error"])

    def test_a_crash_keeps_what_it_managed_to_print(self):
        result = self.run_script("print('got this far')\nraise ValueError('no')")
        self.assertEqual(result["status"], "error")
        self.assertIn("got this far", result["output"])
        self.assertIn("ValueError", result["error"])

    def test_secrets_are_stripped_from_the_environment(self):
        environment = code_exec.safe_environment()
        for name in environment:
            self.assertNotIn("key", name.casefold())
            self.assertNotIn("token", name.casefold())
            self.assertNotIn("secret", name.casefold())

    def test_the_stub_is_valid_python(self):
        """It is generated, so nothing else would catch a broken one."""
        source = code_exec.build_stub({"read_file": 'Read a file "safely"',
                                       "write_file": "Write a file"})
        compile(source, "aura_tools.py", "exec")
        self.assertIn("def read_file(", source)
        self.assertIn("def write_file(", source)

    def test_it_is_offered_for_work_over_a_set_of_files(self):
        for request in ("Read every html file and tell me which lack a title",
                        "Replace old_api with new_api everywhere",
                        "Check all the pages for broken links"):
            with self.subTest(request=request):
                offered = {item["function"]["name"] for item in
                           routing.select(request, "powerful", "balanced",
                                          AuraAgent.tool_definitions())}
                self.assertIn("execute_code", offered)

    def test_it_is_not_offered_for_a_single_step(self):
        """A script whose body is one tool call costs a process and saves
        nothing."""
        offered = {item["function"]["name"] for item in
                   routing.select("Read shop/index.html", "powerful", "balanced",
                                  AuraAgent.tool_definitions())}
        self.assertNotIn("execute_code", offered)


class AnswerReserveTests(unittest.TestCase):
    """How much of the window to keep clear for the reply.

    The first version of this subtracted the whole answer *limit*, on the
    reasoning that output shares the window with the prompt. The first half is
    true and the conclusion is not: the limit is a ceiling the server does not
    hold back against the prompt. Measured against the loaded model — an
    18,880-token prompt succeeded with the limit at 64,000 on a 66,816-token
    window, which the old arithmetic called impossible.

    It mattered. Mat raised his profiles to 64,000 and his next turn inspected
    three files instead of five, because Aura kept compacting a conversation
    that fitted.
    """

    def test_a_cap_larger_than_the_window_does_not_swallow_it(self):
        """The setting that exposed the bug."""
        self.assertEqual(context_budget.room_for_request(66816, 64000), 62720)

    def test_a_modest_cap_is_still_respected(self):
        """A limit smaller than the default reserve is the real ceiling: the
        answer cannot exceed it, so holding more back would be pretending."""
        self.assertEqual(context_budget.answer_reserve(2048, 66816), 2048)

    def test_a_small_window_keeps_most_of_itself_for_the_conversation(self):
        reserve = context_budget.answer_reserve(64000, 8192)
        self.assertLessEqual(reserve, 8192 // context_budget.MOST_OF_WINDOW_RESERVED)
        self.assertGreater(reserve, 0)

    def test_the_reserve_grows_to_fit_the_replies_actually_seen(self):
        """Being wrong low is what truncates an answer mid-sentence, so the
        largest recent reply sets the floor rather than the average."""
        meter = context_budget.TokenMeter()
        for reply in (900, 6000, 1100):
            meter.observe_answer(reply)
        self.assertEqual(meter.typical_answer, 6000)
        self.assertGreater(context_budget.answer_reserve(64000, 66816, meter),
                           context_budget.DEFAULT_ANSWER_RESERVE)

    def test_a_nonsense_completion_is_ignored(self):
        meter = context_budget.TokenMeter()
        meter.observe_answer(0)
        meter.observe_answer("lots")
        self.assertEqual(meter.typical_answer, 0)

    def test_an_unknown_window_still_claims_nothing(self):
        self.assertEqual(context_budget.room_for_request(0, 4096), 0)


class ContextHealthTests(unittest.TestCase):
    """What Diagnostics says about the window."""

    class _Provider:
        def __init__(self, size): self.size = size
        def loaded_context(self): return self.size

    class _Agent:
        def __init__(self, size, data):
            self.provider = ContextHealthTests._Provider(size)
            self.config = type("C", (), {"data": data})()

    def test_a_small_window_is_still_warned_about(self):
        result = health._check_context(self._Agent(8000, {"sampling_by_task": True}))
        self.assertEqual(result.status, health.WARN)
        self.assertIn("only 8,000", result.detail)

    def test_a_limit_larger_than_the_window_is_called_out_as_confused(self):
        """Harmless — an answer can never reach it — but it promises something
        it cannot do, which is worth saying once."""
        # A small window rather than a huge cap: sampling clamps the limit to
        # 65,536, so on a 66,816-token window no setting can exceed it. The
        # case is real the other way round — the limit left at 64,000 while a
        # model is loaded with 32,768.
        result = health._check_context(
            self._Agent(32768, {"sampling_by_task": False, "max_tokens": 64000}))
        self.assertEqual(result.status, health.WARN)
        self.assertIn("larger than the whole window", result.detail)

    def test_mats_own_settings_now_read_as_fine(self):
        result = health._check_context(self._Agent(66816, {
            "sampling_by_task": True, "max_tokens_chat": 64000,
            "max_tokens_work": 64000, "max_tokens_code": 64000}))
        self.assertEqual(result.status, health.OK)
        self.assertIn("62,720", result.detail)


class ToolErrorTests(unittest.TestCase):
    """Every tool failure the model saw used to be the raw exception."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.agent.sandbox.write_file("shop/js/main.bundle.js", "x")
        self.agent.sandbox.write_file("shop/notes.txt", "hello")

    def tearDown(self):
        self.temp.cleanup()

    def fail(self, tool, args):
        return self.agent._execute_tool(ToolCall("id", tool, args), lambda *_: True)["error"]

    def test_a_folder_read_as_a_file_does_not_claim_a_permission_problem(self):
        """Windows raises PermissionError for a folder, so the raw message named
        the one cause that is certainly wrong — and a model that believes it is
        not allowed stops trying."""
        message = self.fail("read_file", {"path": "shop"})
        self.assertIn("is a folder", message)
        self.assertNotIn("Permission denied", message)
        self.assertIn("list_files", message)

    def test_a_near_miss_filename_is_offered(self):
        """From the log: `main_bundle.js` was asked for while `main.bundle.js`
        sat beside it, and nothing pointed at the answer."""
        message = self.fail("read_file", {"path": "shop/js/main_bundle.js"})
        self.assertIn("main.bundle.js", message)

    def test_a_genuinely_missing_file_is_not_given_a_false_suggestion(self):
        message = self.fail("read_file", {"path": "shop/absent.txt"})
        self.assertIn("does not exist", message)
        self.assertNotIn("Did you mean", message)

    def test_the_host_path_never_reaches_the_model(self):
        """The model works in workspace-relative paths, and Mat's directory
        layout is not the model's business."""
        for tool, args in (("read_file", {"path": "shop/absent.txt"}),
                           ("read_many_files", {"paths": ["shop"]}),
                           ("read_file", {"path": "shop"})):
            with self.subTest(tool=tool):
                message = self.fail(tool, args)
                self.assertNotIn(str(self.agent.sandbox.root), message)
                self.assertNotIn("C:" + chr(92) + "Users", message)

    def test_creating_a_file_that_exists_names_the_tool_that_would_work(self):
        message = self.fail("create_file", {"path": "shop/notes.txt", "content": "x"})
        self.assertIn("already exists", message)
        self.assertIn("write_file", message)

    def test_an_unrecognised_failure_is_passed_through_unchanged(self):
        """A wrong explanation is worse than a raw one."""
        self.assertEqual(
            tool_errors.explain(ValueError("something specific broke"), "read_file",
                                {"path": "x"}, self.agent.sandbox.root),
            "something specific broke")

    def test_the_log_keeps_the_raw_text_as_well(self):
        """The sentence that helps a model act is not always the one that helps
        Mat debug, so the record keeps both."""
        shown = self.fail("read_file", {"path": "shop"})
        rows = [row for row in self.agent.log.recent(20)
                if row.get("action") == "read_file" and row.get("status") == "error"]
        self.assertTrue(rows)
        self.assertIn("Errno 13", rows[0]["raw_error"])
        self.assertNotIn("Errno 13", shown)


class RunCommandGuardTests(unittest.TestCase):
    """A tool reached through a runner is the same mistake as calling it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())

    def tearDown(self):
        self.temp.cleanup()

    def run_command(self, command):
        return self.agent._tool_run_command(
            "run_command", {"command": command, "timeout": 5}, lambda c: True, None)

    def test_a_tool_run_as_a_python_module_is_refused(self):
        """The real attempt from the log, which the direct-name guard missed."""
        result = self.run_command(["python", "-m", "validate_project"])
        self.assertTrue(result.get("refused"))
        self.assertIn("one of your tools", result["error"])

    def test_a_tool_run_inside_a_shell_script_is_refused(self):
        result = self.run_command(["bash", "-lc", "validate_project shop"])
        self.assertTrue(result.get("refused"))

    def test_code_that_merely_mentions_a_tool_name_is_not_refused(self):
        """Refusing this would block real work to prevent a typo."""
        result = self.run_command(["python", "-c", "def read_file(p): pass"])
        self.assertFalse(result.get("refused"))

    def test_a_missing_program_is_named(self):
        """Fourteen of sixteen recorded command failures were an unnamed
        "[WinError 2] The system cannot find the file specified"."""
        result = self.run_command(["definitely-not-a-real-program-xyz"])
        self.assertIn("definitely-not-a-real-program-xyz", result["error"])
        self.assertIn("not installed", result["error"])
        self.assertNotIn("WinError", result["error"])


class EmptyReplyTests(unittest.TestCase):
    """The several different failures that all look like silence."""

    def test_a_reply_with_content_is_not_a_silence(self):
        self.assertEqual(empty_reply.classify(
            ProviderReply("here you go", [])).name, "none")

    def test_missing_usage_fails_open_to_the_ordinary_retry(self):
        """The mistake this test exists to prevent.

        A reply carrying no usage at all is indistinguishable from an ordinary
        quiet turn, and treating it as evidence turned a retryable stumble into
        a dead turn — nine tests caught it, which is the only reason it never
        reached Mat.
        """
        blank = ProviderReply("", [])
        self.assertFalse(blank.usage_reported)
        kind = empty_reply.classify(blank)
        self.assertNotEqual(kind.name, "never_asked")
        self.assertTrue(kind.worth_retrying)
        self.assertIn("completely empty", kind.instruction)

    def test_a_counted_request_that_counted_nothing_is_not_the_models_fault(self):
        kind = empty_reply.classify(ProviderReply(
            "", [], "", 0, 0, "", usage_reported=True))
        self.assertEqual(kind.name, "never_asked")
        self.assertFalse(kind.worth_retrying)
        self.assertEqual(kind.instruction, "")
        self.assertIn("did not reach the model", kind.explanation)

    def test_a_deliberate_stop_is_told_to_answer_directly(self):
        """The shape in Mat's log: finish_reason stop, one completion token."""
        kind = empty_reply.classify(ProviderReply(
            "", [], "stop", 3907, 1, "", usage_reported=True))
        self.assertEqual(kind.name, "chose_silence")
        self.assertTrue(kind.worth_retrying)
        self.assertIn("completely empty", kind.instruction)

    def test_thinking_the_whole_budget_away_is_a_different_fault(self):
        """gpt-oss reasons in a separate channel, so a turn can generate
        thousands of tokens and still arrive with no answer. Telling that model
        it said nothing is false as well as useless."""
        kind = empty_reply.classify(ProviderReply(
            "", [], "length", 9000, 6144, "long private reasoning",
            usage_reported=True), answer_limit=6144)
        self.assertEqual(kind.name, "thought_it_away")
        self.assertIn("thinking", kind.instruction)
        self.assertIn("6,144", kind.explanation)

    def test_the_tools_survive_a_turn_that_only_ran_out_of_room(self):
        """Taking the tools away there would throw out the turn's own work."""
        thought = empty_reply.classify(ProviderReply(
            "", [], "length", 9000, 6144, "thinking", usage_reported=True))
        silent = empty_reply.classify(ProviderReply(
            "", [], "stop", 3907, 1, "", usage_reported=True))
        self.assertEqual(thought.name, "thought_it_away")
        self.assertEqual(silent.name, "chose_silence")

    def test_it_never_raises_on_a_reply_it_does_not_recognise(self):
        class Odd:
            content = ""
            prompt_tokens = "lots"
            completion_tokens = None
        kind = empty_reply.classify(Odd())
        self.assertTrue(kind.worth_retrying)


class ContextBudgetTests(unittest.TestCase):
    """Measuring the request before sending it, rather than after it failed."""

    def test_a_short_string_never_costs_nothing(self):
        """A hundred short tool results that each estimate as zero add up to
        zero, and the check meant to catch overflow is the thing that misses it."""
        meter = context_budget.TokenMeter()
        self.assertGreater(meter.tokens_for(context_budget.request_characters(
            [{"role": "tool", "content": "ok"}])), 0)

    def test_tool_schemas_are_part_of_the_bill(self):
        """They measured as the densest content in the payload, and they are
        easy to forget because nobody types them."""
        messages = [{"role": "user", "content": "hello"}]
        tools = [{"function": {"name": "read_file", "description": "x" * 400}}]
        self.assertGreater(context_budget.request_characters(messages, tools),
                           context_budget.request_characters(messages) + 300)

    def test_the_rate_is_learned_from_what_the_model_charged(self):
        meter = context_budget.TokenMeter()
        self.assertFalse(meter.calibrated)
        meter.observe(48000, 10128)      # 48k chars cost 10k tokens: ~4.8 each
        self.assertTrue(meter.calibrated)
        self.assertAlmostEqual(meter.chars_per_token, 4.8, places=1)

    def test_it_bills_at_the_densest_rate_seen_not_the_average(self):
        """The densest observation gives the largest estimate. Overestimating
        costs a little context; underestimating costs the turn."""
        meter = context_budget.TokenMeter()
        meter.observe(60000, 10128)      # ~6.0 chars per token
        meter.observe(40000, 10128)      # ~4.0 — denser, so this one wins
        self.assertAlmostEqual(meter.chars_per_token, 4.0, places=1)

    def test_a_tiny_request_does_not_teach_it_anything(self):
        """At 35 characters the template preamble is most of the bill, so a
        short turn measures the preamble rather than the rate."""
        meter = context_budget.TokenMeter()
        meter.observe(35, 68)
        self.assertFalse(meter.calibrated)

    def test_a_nonsense_observation_is_ignored(self):
        meter = context_budget.TokenMeter()
        meter.observe(50000, 200)        # 250 chars per token is not a tokenizer
        meter.observe(2000, "many")
        self.assertFalse(meter.calibrated)


    def test_an_unknown_window_never_blocks_a_turn(self):
        call = context_budget.verdict(context_budget.TokenMeter(),
                                      [{"role": "user", "content": "x" * 500000}],
                                      None, 0, 4096)
        self.assertFalse(call["known"])
        self.assertTrue(call["fits"])
        self.assertEqual(call["over_by"], 0)

    def test_an_oversized_request_is_reported_as_not_fitting(self):
        call = context_budget.verdict(context_budget.TokenMeter(),
                                      [{"role": "user", "content": "x" * 200000}],
                                      None, 36608, 6144)
        self.assertFalse(call["fits"])
        self.assertGreater(call["over_by"], 0)

    def test_the_guard_rounds_against_the_request(self):
        call = context_budget.verdict(context_budget.TokenMeter(),
                                      [{"role": "user", "content": "x" * 40000}],
                                      None, 36608, 4096)
        self.assertGreater(call["guarded"], call["needed"])


class SamplingTests(unittest.TestCase):
    """One temperature for chatting, thinking, and writing code was one too few."""

    def test_the_kind_of_turn_comes_from_the_tools_it_was_offered(self):
        self.assertEqual(sampling.kind_for_tools(["write_file", "read_file"]), "code")
        self.assertEqual(sampling.kind_for_tools(["read_file", "validate_project"]), "work")
        self.assertEqual(sampling.kind_for_tools(["remember_name"]), "chat")
        self.assertEqual(sampling.kind_for_tools([]), "chat")

    def test_writing_wins_over_reading_when_both_are_offered(self):
        """Almost every build turn also reads. The write is what decides it."""
        self.assertEqual(
            sampling.kind_for_tools(["read_file", "search_files", "apply_edits"]), "code")

    def test_each_profile_is_cooler_than_the_one_before(self):
        heats = [sampling.for_turn(tools, {}).temperature for tools in
                 (["remember_name"], ["read_file"], ["write_file"])]
        self.assertEqual(heats, sorted(heats, reverse=True))

    def test_switching_the_profile_off_restores_the_single_pair(self):
        off = sampling.for_turn(["write_file"], {
            "sampling_by_task": False, "temperature": 0.55, "max_tokens": 3000})
        self.assertEqual((off.temperature, off.max_tokens), (0.55, 3000))

    def test_mats_own_values_are_used_when_he_has_set_them(self):
        tuned = sampling.for_turn(["write_file"], {"temperature_code": 1.0,
                                                   "max_tokens_code": 8192})
        self.assertEqual((tuned.temperature, tuned.max_tokens), (1.0, 8192))

    def test_a_nonsense_setting_falls_back_rather_than_raising(self):
        """A bad value in config.json must not cost a turn."""
        safe = sampling.for_turn(["read_file"], {"temperature_work": "warm",
                                                 "max_tokens_work": None})
        self.assertEqual((safe.temperature, safe.max_tokens), sampling.DEFAULTS["work"])

    def test_top_p_and_top_k_are_silent_until_mat_sets_them(self):
        """Blank means LM Studio's loaded value stands, which is not the same
        as sending 1.0 and 0 — that would override the slider with "no
        filtering" rather than leaving it alone."""
        quiet = sampling.for_turn(["write_file"], {})
        self.assertIsNone(quiet.top_p)
        self.assertIsNone(quiet.top_k)
        set_by_hand = sampling.for_turn(["write_file"], {"top_p": 0.9, "top_k": 40})
        self.assertEqual((set_by_hand.top_p, set_by_hand.top_k), (0.9, 40))

    def test_top_values_apply_whichever_way_temperature_is_decided(self):
        single = sampling.for_turn(["write_file"], {"sampling_by_task": False,
                                                    "top_p": 1.0, "top_k": 0})
        self.assertEqual((single.top_p, single.top_k), (1.0, 0))

    def test_a_top_value_that_is_not_a_number_falls_back_to_silence(self):
        odd = sampling.for_turn(["read_file"], {"top_p": "wide", "top_k": ""})
        self.assertIsNone(odd.top_p)
        self.assertIsNone(odd.top_k)

    def test_the_payload_carries_them_only_when_set(self):
        """The measured point: a value in the request overrides the loaded one,
        so an unset field has to be absent rather than defaulted."""
        provider = LMStudioProvider(base_url="http://127.0.0.1:1234/v1", model="m")
        captured = {}

        def fake(payload):
            captured.update(payload)
            return ProviderReply(content="ok", tool_calls=[])

        provider._complete_without_stream = fake
        provider.complete([{"role": "user", "content": "hi"}], temperature=0.5)
        self.assertNotIn("top_p", captured)
        self.assertNotIn("top_k", captured)
        self.assertEqual(captured["temperature"], 0.5)
        captured.clear()
        provider.complete([{"role": "user", "content": "hi"}],
                          temperature=0.5, top_p=0.9, top_k=40)
        self.assertEqual((captured["top_p"], captured["top_k"]), (0.9, 40))

    def test_every_routable_tool_has_a_profile(self):
        """A tool in neither set would quietly sample its turn as chat."""
        known = sampling.WRITING_TOOLS | sampling.WORKING_TOOLS
        chatting = {"remember_name", "remember_preference", "remember_personal_fact",
                    "remember_lesson", "list_personal_memory", "forget_personal_fact",
                    "correct_personal_fact", "capability_summary", "get_weather",
                    "set_reminder", "set_check", "safe_delete_file"}
        every = {item["function"]["name"] for item in AuraAgent.tool_definitions()}
        self.assertEqual(every - known - chatting, set())




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

    def test_the_memory_cap_never_evicts_a_pinned_or_confirmed_memory(self):
        # The cap used to slice the list blindly, so a memory the user had
        # deliberately pinned could disappear once 250 was reached.
        memory = self.agent.memory
        kept = memory.learn_fact("preference", "Pinned and important", source="t",
                                 confidence=.9, explicit=True)
        memory.update_profile_memory(kept["id"], pinned=True)
        for index in range(300):
            memory.learn_fact("interest", f"Casual interest number {index}",
                              source="chat", confidence=.5)
        values = {item["value"] for item in memory.profile_memories()}
        self.assertIn("Pinned and important", values)
        self.assertLessEqual(len(memory.profile_memories()), memory.MAX_MEMORIES)

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

    def test_edits_record_history_and_revert_walks_back_one_step(self):
        memory = self.agent.memory
        item = memory.learn_fact("tool", "Python for prototypes", source="t",
                                 confidence=.8, explicit=True)
        memory.update_profile_memory(item["id"], value="Rust for prototypes")
        memory.update_profile_memory(item["id"], value="Go for prototypes")

        current = next(m for m in memory.profile_memories() if m["id"] == item["id"])
        self.assertEqual(current["value"], "Go for prototypes")
        self.assertEqual([entry["value"] for entry in current["history"]],
                         ["Python for prototypes", "Rust for prototypes"])

        once = memory.revert_profile_memory(item["id"])
        self.assertEqual(once["value"], "Rust for prototypes")
        twice = memory.revert_profile_memory(item["id"])
        self.assertEqual(twice["value"], "Python for prototypes")
        # History is consumed, so reverting cannot ping-pong forever.
        self.assertEqual(twice["history"], [])
        with self.assertRaises(ValueError):
            memory.revert_profile_memory(item["id"])

    def test_confirming_without_changes_records_no_history(self):
        memory = self.agent.memory
        item = memory.learn_fact("preference", "Concise answers", source="chat",
                                 confidence=.85)
        memory.update_profile_memory(item["id"])
        memory.update_profile_memory(item["id"], pinned=True)
        stored = next(m for m in memory.profile_memories() if m["id"] == item["id"])
        self.assertEqual(stored.get("history", []), [])
        self.assertTrue(stored["pinned"])

    def test_reverting_restores_lookup_key_so_relearning_is_deduplicated(self):
        memory = self.agent.memory
        item = memory.learn_fact("tool", "Python for prototypes", source="t",
                                 confidence=.8, explicit=True)
        memory.update_profile_memory(item["id"], value="Rust for prototypes")
        memory.revert_profile_memory(item["id"])
        again = memory.learn_fact("tool", "Python for prototypes", source="t",
                                  confidence=.8, explicit=True)
        self.assertEqual(again["id"], item["id"])
        self.assertEqual(len(memory.profile_memories()), 1)

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


class VisionTests(unittest.TestCase):
    PIXEL_PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
        "IQAAAABJRU5ErkJggg==")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.agent = AuraAgent(Path(self.temp.name) / "workspace",
                               provider=LMStudioProvider(model="qwen3-vl-8b-instruct"))
        # Seed the probe cache: without it every vision_enabled() call in these
        # tests would hit a real LM Studio server, which is both slow and rude —
        # it makes the user's machine load a model. Probe behaviour itself is
        # covered by dedicated tests with a mocked prober.
        self.agent.config.update(vision_probe={"qwen3-vl-8b-instruct": True})

    def tearDown(self):
        self.temp.cleanup()

    def test_vision_guess_follows_model_name_and_user_override(self):
        self.assertTrue(LMStudioProvider.model_may_support_vision("qwen3-vl-8b-instruct"))
        self.assertTrue(LMStudioProvider.model_may_support_vision("llava-1.6"))
        self.assertFalse(LMStudioProvider.model_may_support_vision("qwen3-coder-30b"))
        self.assertFalse(LMStudioProvider.model_may_support_vision(None))

        self.assertTrue(self.agent.vision_enabled())
        self.agent.config.update(vision_mode="off")
        self.assertFalse(self.agent.vision_enabled())
        self.agent.provider.model = "qwen3-coder-30b"
        self.agent.config.update(vision_mode="on")
        self.assertTrue(self.agent.vision_enabled())

    def test_vision_is_decided_by_probing_the_server_not_the_model_name(self):
        # qwen/qwen3.5-9b reads images despite having no vision marker in its
        # name, so guessing from the name alone gets it wrong.
        self.agent.provider.model = "qwen/qwen3.5-9b"
        self.assertFalse(LMStudioProvider.model_may_support_vision("qwen/qwen3.5-9b"))
        probe = unittest.mock.Mock(return_value=True)
        self.agent.provider.probe_vision_support = probe
        self.assertTrue(self.agent.vision_enabled())
        # The answer is cached per model, so the server is asked only once.
        self.assertTrue(self.agent.vision_enabled())
        self.assertEqual(probe.call_count, 1)
        self.assertIs(self.agent.config.data["vision_probe"]["qwen/qwen3.5-9b"], True)

    def test_a_refused_probe_disables_images_and_the_override_still_wins(self):
        self.agent.provider.model = "text-only-model"
        self.agent.provider.probe_vision_support = unittest.mock.Mock(return_value=False)
        self.assertFalse(self.agent.vision_enabled())
        self.agent.config.update(vision_mode="on")
        self.assertTrue(self.agent.vision_enabled())

    def test_an_unreachable_probe_falls_back_to_the_name_heuristic(self):
        self.agent.provider.model = "some-other-vl-model"
        self.agent.provider.probe_vision_support = unittest.mock.Mock(
            side_effect=RuntimeError("server down"))
        self.assertTrue(self.agent.vision_enabled())
        # A failed probe must not be cached as an answer.
        self.assertNotIn("some-other-vl-model",
                         self.agent.config.data.get("vision_probe", {}))

    def test_image_is_encoded_under_a_journal_stripped_key(self):
        self.agent.sandbox.import_file("shot.png", self.PIXEL_PNG)
        attachment = self.agent._read_image_attachment("shot.png")
        self.assertEqual(attachment["media_type"], "image/png")
        self.assertTrue(attachment["content"].startswith("data:image/png;base64,"))
        # "content" is the key TaskJournal.record_tool drops, so the base64 blob
        # never lands in the durable task history.
        journal = TaskJournal(Path(self.temp.name) / "tasks.jsonl")
        task = journal.start("look")
        journal.record_tool(task, "look_at_image", {"path": "shot.png"}, attachment)
        self.assertNotIn("content", journal.recent(1)[0]["tool_details"][0]["result"])
        stored = journal.db._query("SELECT result FROM task_events WHERE result IS NOT NULL")
        self.assertTrue(stored)
        self.assertFalse(any("base64" in row["result"] for row in stored))

    def test_non_image_and_oversized_files_are_refused(self):
        self.agent.sandbox.write_file("notes.txt", "not an image")
        with self.assertRaises(ValueError):
            self.agent._read_image_attachment("notes.txt")
        with self.assertRaises(FileNotFoundError):
            self.agent._read_image_attachment("missing.png")

    def test_capture_page_requires_approval_before_launching_a_browser(self):
        self.agent.sandbox.write_file(
            "site/index.html", "<!doctype html><html><body><h1>Hi</h1></body></html>")
        seen: list[list[str]] = []

        def deny(command):
            seen.append(command)
            return False

        call = ToolCall("call_1", "capture_page", {"path": "site/index.html"})
        result = self.agent._execute_tool(call, deny)
        self.assertFalse(result["ok"])
        self.assertFalse(result["approved"])
        self.assertTrue(seen, "the user must be asked before a browser starts")
        self.assertIn("--headless", seen[0])
        self.assertFalse((self.agent.sandbox.root / "site" / "index-screenshot.png").exists())

    def test_every_extra_round_clears_the_previous_partial_reply(self):
        # The verification nudge used to stream only blank lines and then repeat
        # the whole answer, so the reply appeared two or three times. The reset
        # is emitted once per extra round, so no retry path can miss it.
        provider = LMStudioProvider(model="local-model")
        agent = AuraAgent(Path(self.temp.name) / "rounds", provider=provider)
        states: list[str] = []
        replies = [
            ProviderReply("", [ToolCall("c1", "create_file", {
                "path": "loop.txt", "content": "no repeats"})]),
            ProviderReply("Created loop.txt.", []),          # triggers verification nudge
            ProviderReply("", [ToolCall("c2", "read_file", {"path": "loop.txt"})]),
            ProviderReply("Created and verified loop.txt.", []),
        ]
        provider.complete = unittest.mock.Mock(side_effect=replies)
        agent.handle("Create loop.txt in the workspace and validate it",
                     state=states.append)
        # One reset for each round after the first.
        self.assertEqual(states.count("retry"), provider.complete.call_count - 1)

    def test_a_retry_tells_the_interface_to_discard_the_abandoned_reply(self):
        # The user saw the same answer two or three times whenever Aura retried,
        # because each attempt streamed a full reply and the browser appended
        # them. A retry must clear what was already streamed.
        provider = LMStudioProvider(model="local-model")
        agent = AuraAgent(Path(self.temp.name) / "retrystate", provider=provider)
        states: list[str] = []
        replies = [
            ProviderReply("I have created report.txt for you.", []),
            ProviderReply("", [ToolCall("c1", "create_file", {
                "path": "report.txt", "content": "real"})]),
            ProviderReply("Created report.txt.", []),
        ]
        provider.complete = unittest.mock.Mock(side_effect=replies)
        agent.handle("Create report.txt in the workspace", state=states.append)
        self.assertIn("retry", states)
        self.assertTrue((agent.sandbox.root / "report.txt").is_file())

    def test_external_only_work_never_triggers_a_repeat_nudge_loop(self):
        # Regression: undoing an external write re-ran the whole answer several
        # times, because the workspace artifact contract and the workspace
        # validation nudge can never be satisfied by work done outside it.
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "granted"
            target.mkdir()
            (target / "report.txt").write_text("before", encoding="utf-8")
            provider = LMStudioProvider(model="local-model")
            agent = AuraAgent(Path(self.temp.name) / "noloop", provider=provider)
            agent.permissions.grant("write_folder", target, "persistent")
            agent.external_writer.write_file(target / "report.txt", "after")

            replies = [
                ProviderReply("", [ToolCall("u1", "undo_external_change", {})]),
                ProviderReply("Rolled report.txt back and validated the result.", []),
            ]
            provider.complete = unittest.mock.Mock(side_effect=replies)
            answer = agent.handle("Undo that change to report.txt and validate it")

            self.assertNotIn("still missing", answer)
            self.assertEqual(agent.tasks.recent(1)[0]["status"], "completed")
            self.assertEqual(provider.complete.call_count, 2)
            self.assertEqual((target / "report.txt").read_text(encoding="utf-8"), "before")

    def test_naming_a_tool_in_the_request_always_offers_it(self):
        # Regression: "the granted write folder" missed the "granted folder"
        # keyword, so write_external_file was never offered and Aura appeared to
        # refuse a request that named the tool explicitly.
        request = ('Use write_external_file to replace report.txt in the granted '
                   'write folder with new text')
        offered = {d["function"]["name"] for d in
                   AuraAgent.select_tool_definitions(request, "powerful", "deep")}
        self.assertIn("write_external_file", offered)
        obscure = {d["function"]["name"] for d in
                   AuraAgent.select_tool_definitions("please run capture_page for me",
                                                     "balanced", "fast")}
        self.assertIn("capture_page", obscure)

    def test_an_external_write_satisfies_the_artifact_contract(self):
        # Regression: "replace report.txt" in a granted outside folder kept
        # failing with "required artifacts are still missing", because the
        # contract only looked inside the workspace, so Aura retried in a loop.
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "granted"
            target.mkdir()
            (target / "report.txt").write_text("before", encoding="utf-8")
            provider = LMStudioProvider(model="local-model")
            agent = AuraAgent(Path(self.temp.name) / "extwrite", provider=provider)
            agent.permissions.grant("write_folder", target, "persistent")

            replies = [
                ProviderReply("", [ToolCall("c1", "write_external_file", {
                    "path": str(target / "report.txt"), "content": "after"})]),
                ProviderReply("Replaced report.txt in the granted folder.", []),
            ]
            provider.complete = unittest.mock.Mock(side_effect=replies)
            answer = agent.handle("Replace report.txt in the granted folder with new text")

            self.assertNotIn("still missing", answer)
            self.assertEqual(agent.tasks.recent(1)[0]["status"], "completed")
            self.assertEqual((target / "report.txt").read_text(encoding="utf-8"), "after")
            # Exactly two model turns: no nagging retry loop.
            self.assertEqual(provider.complete.call_count, 2)

    def test_read_only_requests_do_not_demand_the_named_file_be_created(self):
        # Regression: "read notes.txt" made notes.txt a required deliverable, so a
        # successful read of an external or missing-in-workspace file was reported
        # as "required artifacts are still missing".
        provider = LMStudioProvider(model="local-model")
        agent = AuraAgent(Path(self.temp.name) / "readonly", provider=provider)
        provider.complete = unittest.mock.Mock(
            return_value=ProviderReply("Here is what notes.txt said.", []))
        answer = agent.handle("Read notes.txt from the granted folder and summarise it")
        self.assertNotIn("still missing", answer)
        self.assertEqual(agent.tasks.recent(1)[0]["status"], "completed")

    def test_artifact_contract_keeps_the_folder_the_user_typed(self):
        # Regression: "aura_craft/index.html" used to collapse to "index.html",
        # so the completion check looked in the workspace root, did not find it,
        # and reported a finished task as a failure.
        _, paths = AuraAgent._extract_artifact_contract(
            "Take a screenshot of aura_craft/index.html with capture_page")
        self.assertEqual(paths, ["aura_craft/index.html"])
        _, windows = AuraAgent._extract_artifact_contract(r"Fix src\app\main.py please")
        self.assertEqual(windows, [r"src\app\main.py"])
        target, combined = AuraAgent._extract_artifact_contract(
            "Create hello.py in the demo folder")
        self.assertEqual((target, combined), ("demo", ["demo/hello.py"]))
        # A resume brief names the same file bare and qualified; the completion
        # evidence used to list "shop/style.css" twice because of it.
        _, resumed = AuraAgent._extract_artifact_contract(
            "Build a small site in the shop folder with index.html, style.css and "
            "about.html.\nRequested files that still do not exist:\n"
            "- shop/style.css\n- shop/about.html")
        self.assertEqual(resumed, ["shop/index.html", "shop/style.css", "shop/about.html"])

    def test_capture_page_rejects_non_html_and_missing_pages(self):
        self.agent.sandbox.write_file("notes.txt", "plain text")
        with self.assertRaises(ValueError):
            self.agent._capture_page("notes.txt", lambda _: True)
        with self.assertRaises(FileNotFoundError):
            self.agent._capture_page("missing.html", lambda _: True)

    def test_capture_page_renders_a_real_screenshot_when_a_browser_exists(self):
        if find_browser() is None:
            self.skipTest("Chrome, Edge, or Chromium is not installed")
        self.agent.sandbox.write_file(
            "site/index.html",
            '<!doctype html><html><head><link rel="stylesheet" href="style.css"></head>'
            "<body><h1>Screenshot check</h1></body></html>")
        self.agent.sandbox.write_file(
            "site/style.css", "body { background: #123; } h1 { color: #fff; }")
        result = self.agent._capture_page("site/index.html", lambda _: True,
                                          width=640, height=400)
        self.assertTrue(result["approved"])
        self.assertEqual(result["path"], "site/index-screenshot.png")
        saved = self.agent.sandbox.path(result["path"])
        self.assertTrue(saved.is_file())
        self.assertGreater(saved.stat().st_size, 1_000)
        self.assertEqual(saved.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_a_css_change_shows_up_as_a_measured_layout_regression(self):
        if find_browser() is None:
            self.skipTest("Chrome, Edge, or Chromium is not installed")
        self.agent.sandbox.write_file(
            "site/index.html",
            '<!doctype html><html><head><link rel="stylesheet" href="style.css"></head>'
            '<body><h1>Title</h1><div class="box"></div></body></html>')
        self.agent.sandbox.write_file(
            "site/style.css", "body{background:#fff;margin:0}"
            ".box{width:200px;height:120px;background:#3366ff}")
        before = self.agent._capture_page("site/index.html", lambda _: True,
                                          width=800, height=600)
        self.agent.sandbox.move_file(before["path"], "site/before.png")

        self.agent.sandbox.write_file(
            "site/style.css", "body{background:#fff;margin:0}"
            ".box{width:200px;height:120px;background:#ff3333}")
        after = self.agent._capture_page("site/index.html", lambda _: True,
                                         width=800, height=600)

        result = compare_images(self.agent.sandbox.path("site/before.png"),
                                self.agent.sandbox.path(after["path"]))
        self.assertFalse(result["identical"])
        self.assertGreater(result["changed_pixels"], 0)
        # Only the recoloured box should move, so the changed area must be far
        # smaller than the page and must not span the whole canvas.
        self.assertLess(result["changed_percent"], 20)
        self.assertLessEqual(result["changed_region"]["width"], 260)
        self.assertLessEqual(result["changed_region"]["height"], 180)

    def test_look_at_image_tool_is_hidden_when_vision_is_off(self):
        request = "look at the screenshot image and describe it"
        with_vision = self.agent.select_tool_definitions(request, "powerful", "deep")
        self.assertIn("look_at_image",
                      [d["function"]["name"] for d in with_vision])
        self.agent.config.update(vision_mode="off")
        call = ToolCall("call_1", "look_at_image", {"path": "shot.png"})
        self.assertFalse(self.agent._execute_tool(call, None)["ok"])


class PermissionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.allowed = self.base / "allowed"
        self.secret = self.base / "secret"
        for folder in (self.allowed, self.secret):
            (folder / "sub").mkdir(parents=True)
        (self.allowed / "note.txt").write_text("inside", encoding="utf-8")
        (self.allowed / "sub" / "deep.txt").write_text("deeper", encoding="utf-8")
        (self.secret / "keys.txt").write_text("do not read", encoding="utf-8")
        self.store = PermissionStore(self.base / "permissions.json", session_id="s1")
        self.reader = ExternalReader(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_nothing_outside_the_workspace_is_readable_without_a_grant(self):
        with self.assertRaises(PermissionDenied):
            self.reader.list_files(self.allowed)
        with self.assertRaises(PermissionDenied):
            self.reader.read_file(self.allowed / "note.txt")

    def test_a_grant_covers_its_folder_and_descendants_only(self):
        self.store.grant("read_folder", self.allowed, "persistent")
        self.assertEqual(sorted(self.reader.list_files(self.allowed)),
                         ["note.txt", "sub/deep.txt"])
        self.assertEqual(self.reader.read_file(self.allowed / "sub" / "deep.txt"), "deeper")
        # A sibling folder and the parent must stay unreachable.
        with self.assertRaises(PermissionDenied):
            self.reader.read_file(self.secret / "keys.txt")
        with self.assertRaises(PermissionDenied):
            self.reader.list_files(self.base)

    def test_parent_traversal_cannot_escape_a_grant(self):
        self.store.grant("read_folder", self.allowed, "persistent")
        with self.assertRaises(PermissionDenied):
            self.reader.read_file(self.allowed / ".." / "secret" / "keys.txt")

    def test_a_symlink_inside_a_granted_folder_cannot_widen_it(self):
        link = self.allowed / "escape"
        try:
            link.symlink_to(self.secret, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("creating symlinks is not permitted on this machine")
        self.store.grant("read_folder", self.allowed, "persistent")
        with self.assertRaises(PermissionDenied):
            self.reader.read_file(link / "keys.txt")

    def test_once_grants_are_spent_and_session_grants_die_with_the_session(self):
        self.store.grant("read_folder", self.allowed, "once")
        self.store.check("read_folder", self.allowed)
        with self.assertRaises(PermissionDenied):
            self.store.check("read_folder", self.allowed)

        self.store.grant("read_folder", self.allowed, "session")
        self.assertTrue(self.store.check("read_folder", self.allowed))
        restarted = PermissionStore(self.base / "permissions.json", session_id="s2")
        with self.assertRaises(PermissionDenied):
            restarted.check("read_folder", self.allowed)

    def test_persistent_grants_survive_a_restart(self):
        self.store.grant("read_folder", self.allowed, "persistent")
        restarted = PermissionStore(self.base / "permissions.json", session_id="s2")
        self.assertTrue(restarted.check("read_folder", self.allowed))

    def test_project_grants_apply_only_to_their_project(self):
        self.store.grant("read_folder", self.allowed, "project", project="alpha")
        self.assertTrue(self.store.check("read_folder", self.allowed, project="alpha"))
        with self.assertRaises(PermissionDenied):
            self.store.check("read_folder", self.allowed, project="beta")

    def test_revoking_takes_effect_immediately_and_emergency_stop_clears_all(self):
        first = self.store.grant("read_folder", self.allowed, "persistent")
        self.store.revoke(first["id"])
        with self.assertRaises(PermissionDenied):
            self.reader.list_files(self.allowed)

        self.store.grant("read_folder", self.allowed, "persistent")
        self.store.grant("read_folder", self.allowed / "sub", "persistent")
        self.assertEqual(len(self.store.active()), 2)
        self.assertEqual(self.store.revoke_all(), 2)
        self.assertEqual(self.store.active(), [])
        with self.assertRaises(PermissionDenied):
            self.reader.list_files(self.allowed)

    def test_a_read_grant_never_implies_permission_to_write(self):
        writer = ExternalWriter(self.store, self.base / "history",
                                self.base / "external-changes.jsonl")
        self.store.grant("read_folder", self.allowed, "persistent")
        with self.assertRaises(PermissionDenied):
            writer.write_file(self.allowed / "note.txt", "overwritten")
        self.assertEqual((self.allowed / "note.txt").read_text(encoding="utf-8"), "inside")

    def test_external_writes_are_snapshotted_and_undoable(self):
        writer = ExternalWriter(self.store, self.base / "history",
                                self.base / "external-changes.jsonl")
        self.store.grant("write_folder", self.allowed, "persistent")

        overwritten = writer.write_file(self.allowed / "note.txt", "new text")
        self.assertFalse(overwritten["created"])
        self.assertEqual((self.allowed / "note.txt").read_text(encoding="utf-8"), "new text")

        created = writer.write_file(self.allowed / "fresh.txt", "brand new")
        self.assertTrue(created["created"])

        # Undo walks back newest first: the created file goes, then the
        # overwritten one returns to its original contents.
        self.assertEqual(writer.undo_last()["action"], "removed")
        self.assertFalse((self.allowed / "fresh.txt").exists())
        self.assertEqual(writer.undo_last()["action"], "restored")
        self.assertEqual((self.allowed / "note.txt").read_text(encoding="utf-8"), "inside")
        with self.assertRaises(ValueError):
            writer.undo_last()

    def test_writes_cannot_escape_the_granted_folder(self):
        writer = ExternalWriter(self.store, self.base / "history",
                                self.base / "external-changes.jsonl")
        self.store.grant("write_folder", self.allowed, "persistent")
        with self.assertRaises(PermissionDenied):
            writer.write_file(self.secret / "keys.txt", "hacked")
        with self.assertRaises(PermissionDenied):
            writer.write_file(self.allowed / ".." / "secret" / "keys.txt", "hacked")
        self.assertEqual((self.secret / "keys.txt").read_text(encoding="utf-8"),
                         "do not read")

    def test_revoking_write_access_blocks_further_writes(self):
        writer = ExternalWriter(self.store, self.base / "history",
                                self.base / "external-changes.jsonl")
        grant = self.store.grant("write_folder", self.allowed, "persistent")
        writer.write_file(self.allowed / "note.txt", "first")
        self.store.revoke(grant["id"])
        with self.assertRaises(PermissionDenied):
            writer.write_file(self.allowed / "note.txt", "second")
        self.assertEqual((self.allowed / "note.txt").read_text(encoding="utf-8"), "first")

    def test_system_and_root_locations_can_never_be_granted(self):
        root = Path(self.base.anchor or "/")
        with self.assertRaises(PermissionRefused):
            self.store.grant("read_folder", root, "persistent")
        protected = os.environ.get("SystemRoot") or "/etc"
        if Path(protected).is_dir():
            with self.assertRaises(PermissionRefused):
                self.store.grant("read_folder", protected, "persistent")

    def test_unknown_capabilities_modes_and_files_are_refused(self):
        with self.assertRaises(PermissionRefused):
            self.store.grant("delete_everything", self.allowed, "persistent")
        with self.assertRaises(PermissionRefused):
            self.store.grant("read_folder", self.allowed, "forever")
        with self.assertRaises(PermissionRefused):
            self.store.grant("read_folder", self.allowed / "note.txt", "persistent")


class AccessibilityCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.sandbox = WorkspaceSandbox(Path(self.temp.name) / "workspace")

    def tearDown(self):
        self.temp.cleanup()

    def rules(self, html, name="page.html"):
        self.sandbox.write_file(name, html)
        result = check_accessibility(self.sandbox)
        return {issue["rule"] for issue in result["issues"]}, result

    def test_clean_page_reports_nothing(self):
        found, result = self.rules(
            '<!doctype html><html lang="en"><head><title>Shop</title></head><body>'
            '<h1>Shop</h1><h2>Items</h2>'
            '<img src="a.png" alt="A blue mug">'
            '<label for="q">Search</label><input id="q" type="text">'
            '<a href="/x">Go to items</a>'
            '<button aria-label="Close">&times;</button>'
            "</body></html>")
        self.assertEqual(found, set())
        self.assertEqual(result["checked"], 1)

    def test_missing_alt_label_and_empty_link_are_caught(self):
        found, _ = self.rules(
            '<!doctype html><html lang="en"><head><title>T</title></head><body>'
            '<img src="a.png">'
            '<input id="lonely" type="text">'
            '<a href="/x"></a>'
            "</body></html>")
        self.assertEqual(found, {"img-alt", "control-label", "empty-link"})

    def test_document_level_problems_are_caught(self):
        found, _ = self.rules(
            "<!doctype html><html><head><title>  </title></head><body>"
            "<h1>A</h1><h3>Skipped</h3></body></html>")
        self.assertEqual(found, {"html-lang", "document-title", "heading-order"})

    def test_alternative_labelling_methods_are_accepted(self):
        # A wrapping <label>, an aria-label, and a hidden input all count as
        # labelled; flagging them would train the user to ignore the report.
        found, _ = self.rules(
            '<!doctype html><html lang="en"><head><title>T</title></head><body>'
            "<label>Name <input type='text'></label>"
            "<input type='email' aria-label='Email address'>"
            "<input type='hidden' name='csrf'>"
            "<input type='submit' value='Send'>"
            "<a href='/p'><img src='p.png' alt='Product'></a>"
            "</body></html>")
        self.assertEqual(found, set())

    def test_pointing_at_a_single_page_checks_that_page(self):
        # Regression: a file path used to scan nothing, and the empty issue list
        # was reported to the user as "no accessibility issues found".
        self.sandbox.write_file(
            "site/index.html",
            '<!doctype html><html lang="en"><head><title>T</title></head>'
            "<body><img src='a.png'></body></html>")
        result = check_accessibility(self.sandbox, "site/index.html")
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 1)
        self.assertEqual([i["rule"] for i in result["issues"]], ["img-alt"])

    def test_checking_nothing_is_an_error_not_a_clean_result(self):
        self.sandbox.write_file("notes.txt", "not html")
        result = check_accessibility(self.sandbox, "notes.txt")
        self.assertFalse(result["ok"])
        self.assertEqual(result["checked"], 0)
        self.assertIn("nothing was checked", result["error"])

    def test_contrast_is_explicitly_not_evaluated(self):
        _, result = self.rules(
            '<!doctype html><html lang="en"><head><title>T</title></head>'
            '<body style="color:#eee;background:#fff"><h1>Faint</h1></body></html>')
        self.assertFalse(result["contrast_checked"])
        self.assertIn("contrast", result["note"])

    def test_issues_carry_file_and_line_numbers(self):
        self.sandbox.write_file(
            "deep/page.html",
            '<!doctype html>\n<html lang="en">\n<head><title>T</title></head>\n'
            "<body>\n<img src='x.png'>\n</body>\n</html>")
        result = check_accessibility(self.sandbox, "deep")
        self.assertEqual(len(result["issues"]), 1)
        self.assertEqual(result["issues"][0]["file"], "deep/page.html")
        self.assertEqual(result["issues"][0]["line"], 5)


class ImageDiffTests(unittest.TestCase):
    @staticmethod
    def png(width, height, colour_at, *, alpha=False):
        """Build a real PNG so the decoder is tested against genuine bytes."""
        channels = 4 if alpha else 3
        rows = []
        for y in range(height):
            row = bytearray(b"\x00")
            for x in range(width):
                pixel = colour_at(x, y)
                row += bytes(pixel[:3])
                if alpha:
                    row += bytes((255,))
            rows.append(bytes(row))
        raw = b"".join(rows)

        def chunk(tag, data):
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

        colour_type = 6 if alpha else 2
        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, colour_type, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6))
                + chunk(b"IEND", b""))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, data):
        target = self.root / name
        target.write_bytes(data)
        return target

    def test_identical_images_report_no_change(self):
        flat = self.png(40, 20, lambda x, y: (10, 120, 200))
        a = self.write("a.png", flat)
        b = self.write("b.png", flat)
        result = compare_images(a, b)
        self.assertTrue(result["identical"])
        self.assertEqual(result["changed_pixels"], 0)

    def test_changed_region_is_located_precisely(self):
        base = self.png(40, 20, lambda x, y: (255, 255, 255))
        # A single 4x3 red block starting at (10, 5).
        def spotted(x, y):
            return (255, 0, 0) if 10 <= x < 14 and 5 <= y < 8 else (255, 255, 255)
        a = self.write("base.png", base)
        b = self.write("spotted.png", self.png(40, 20, spotted))
        result = compare_images(a, b)
        self.assertFalse(result["identical"])
        self.assertEqual(result["changed_pixels"], 12)
        self.assertEqual(result["changed_region"],
                         {"left": 10, "top": 5, "right": 13, "bottom": 7,
                          "width": 4, "height": 3})

    def test_size_change_is_reported_as_a_layout_difference(self):
        a = self.write("a.png", self.png(40, 20, lambda x, y: (0, 0, 0)))
        b = self.write("b.png", self.png(40, 30, lambda x, y: (0, 0, 0)))
        result = compare_images(a, b)
        self.assertFalse(result["identical"])
        self.assertEqual(result["reason"], "size")
        self.assertIn("40x20", result["summary"])

    def test_tolerance_ignores_imperceptible_noise(self):
        a = self.write("a.png", self.png(20, 10, lambda x, y: (100, 100, 100)))
        b = self.write("b.png", self.png(20, 10, lambda x, y: (104, 100, 100)))
        self.assertTrue(compare_images(a, b, tolerance=8)["identical"])
        self.assertFalse(compare_images(a, b, tolerance=0)["identical"])

    def test_rgb_and_rgba_versions_of_the_same_picture_match(self):
        colours = lambda x, y: (x * 5 % 256, y * 9 % 256, 60)
        a = self.write("rgb.png", self.png(20, 10, colours))
        b = self.write("rgba.png", self.png(20, 10, colours, alpha=True))
        self.assertTrue(compare_images(a, b)["identical"])

    def test_unsupported_and_broken_files_are_refused(self):
        self.write("not.png", b"definitely not a png")
        with self.assertRaises(UnsupportedImage):
            compare_images(self.root / "not.png", self.root / "not.png")

    def test_paeth_filtered_gradient_decodes_correctly(self):
        # A gradient makes the encoder pick non-trivial row filters, so this
        # exercises the Sub/Up/Average/Paeth reconstruction paths.
        gradient = self.png(60, 40, lambda x, y: (x * 4 % 256, y * 6 % 256, (x + y) % 256))
        a = self.write("g1.png", gradient)
        decoded = decode_png(a)
        self.assertEqual((decoded.width, decoded.height), (60, 40))
        self.assertEqual(decoded.rgb(10, 7), (40, 42, 17))
        self.assertTrue(compare_images(a, self.write("g2.png", gradient))["identical"])


class WorkspaceIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.sandbox = WorkspaceSandbox(Path(self.temp.name) / "workspace")
        self.index = WorkspaceIndex(self.sandbox)

    def tearDown(self):
        self.temp.cleanup()

    def test_tokenizer_splits_camel_case_and_snake_case_alike(self):
        self.assertEqual(tokenize("avatarFace"), ["avatar", "face"])
        self.assertEqual(tokenize("avatar_face"), ["avatar", "face"])
        self.assertEqual(tokenize("HTMLParser"), ["html", "parser"])

    def test_ranks_multi_word_query_that_substring_search_cannot_find(self):
        self.sandbox.write_file("render/avatar.js", "function drawAvatarFace() { renderMouth(); }")
        self.sandbox.write_file("docs/notes.md", "Shopping list and unrelated prose about lunch.")
        query = "avatar face render"
        # The existing exact-substring search finds nothing for this phrasing.
        self.assertEqual(self.sandbox.search_files(query), [])
        results = self.index.search(query)
        self.assertEqual(results[0]["path"], "render/avatar.js")
        self.assertIn("avatar", results[0]["matched"])

    def test_unrelated_files_score_nothing(self):
        self.sandbox.write_file("a.md", "kittens and puppies")
        self.assertEqual(self.index.search("quantum chromodynamics"), [])

    def test_index_follows_edits_and_deletions(self):
        # Same byte length before and after, so only the timestamp reveals the
        # edit — the cache must not be relying on file size alone.
        self.sandbox.write_file("note.md", "polymerase aaaa")
        self.assertTrue(self.index.search("polymerase"))
        self.sandbox.write_file("note.md", "helicopter aaaa")
        self.assertEqual(self.index.search("polymerase"), [])
        self.assertTrue(self.index.search("helicopter"))
        self.sandbox.safe_delete_file("note.md")
        self.assertEqual(self.index.search("helicopter"), [])

    def test_binary_and_oversized_files_are_skipped(self):
        self.sandbox.write_file("keep.md", "readable marker text")
        (self.sandbox.root / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n binary marker")
        self.assertEqual(self.index.refresh(), 1)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.box = WorkspaceSandbox(Path(self.temp.name) / "workspace")

    def tearDown(self):
        self.temp.cleanup()

    def _age_change(self, change_id, days):
        old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        self.box.db._execute("UPDATE changes SET time = ? WHERE id = ?", (old, change_id))

    def latest_change_id(self):
        return self.box.db.change_history(1)[0]["id"]

    def test_an_undone_change_can_never_be_undone_twice(self):
        # The whole reason for moving off flat files: an undo used to be a
        # separate row that retention could delete, resurrecting the change.
        self.box.write_file("note.txt", "first")
        self.box.write_file("note.txt", "second")
        self.box.undo_last_change()
        self.assertEqual(self.box.read_file("note.txt"), "first")
        self.box.undo_last_change()          # undoes the *creation*
        with self.assertRaises(FileNotFoundError):
            self.box.undo_last_change()      # nothing undoable is left

    def test_sweeping_clears_empty_session_rows_but_keeps_real_conversations(self):
        """Older builds wrote a session row per launch; those are not conversations."""
        database = self.box.db
        database.start_session("launched-and-never-used")
        database.add_message("spoke", "user", "hello", "2026-08-01T00:00:00+00:00")

        summary = database.sweep(self.box.history, self.box.trash)
        self.assertEqual(summary["empty_sessions_removed"], 1)
        self.assertEqual([item["id"] for item in database.sessions(10)], ["spoke"])
        self.assertTrue(database.session_messages("spoke"))

    def test_sweeping_expires_a_change_with_its_items_and_backups(self):
        self.box.write_file("keep.txt", "v1")
        self.box.write_file("keep.txt", "v2")          # this one has a backup
        old_id = self.latest_change_id()
        backups = {item for item in self.box.db.referenced_backups()}
        self.assertTrue(backups)
        self._age_change(old_id, days=90)

        summary = self.box.db.sweep(self.box.history, self.box.trash, days=30)
        self.assertGreaterEqual(summary["changes_expired"], 1)
        self.assertGreaterEqual(summary["backups_freed"], 1)
        for name in backups:
            self.assertFalse((self.box.history / name).exists())
        # Items went with their change rather than being orphaned.
        rows = self.box.db._query("SELECT * FROM change_items WHERE change_id = ?", (old_id,))
        self.assertEqual(rows, [])

    def test_a_recent_change_still_undoes_after_a_sweep(self):
        self.box.write_file("fresh.txt", "one")
        self.box.write_file("fresh.txt", "two")
        self.box.db.sweep(self.box.history, self.box.trash, days=30)
        self.box.undo_last_change()
        self.assertEqual(self.box.read_file("fresh.txt"), "one")

    def test_a_backup_used_by_an_external_change_survives_the_sweep(self):
        # history/ has two writers; sweeping must consult both indexes.
        (self.box.history / "ext_shared.bak").write_text("external", encoding="utf-8")
        self.box.db.add_external_change({
            "id": "e1", "path": str(Path(self.temp.name) / "outside.txt"),
            "backup": "ext_shared.bak", "created": False, "task_id": None,
            "time": datetime.now(timezone.utc).isoformat(), "undone": False})
        self.box.db.sweep(self.box.history, self.box.trash, days=30)
        self.assertTrue((self.box.history / "ext_shared.bak").exists())

    def test_undo_displaced_files_in_trash_are_kept_until_genuinely_old(self):
        # An undo moves the displaced file into trash without a trash row, so a
        # reference-based sweep would delete something still recoverable.
        self.box.write_file("doc.txt", "before")
        self.box.write_file("doc.txt", "after")
        self.box.undo_last_change()
        displaced = list(self.box.trash.iterdir())
        self.assertTrue(displaced)
        self.box.db.sweep(self.box.history, self.box.trash, days=30)
        self.assertTrue(all(item.exists() for item in displaced))

    def test_the_change_cap_keeps_only_the_newest_changes(self):
        for index in range(12):
            self.box.write_file(f"f{index}.txt", "x")
        self.box.db.sweep(self.box.history, self.box.trash, days=3650, max_changes=5)
        self.assertLessEqual(len(self.box.db.change_history(100)), 5)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.meta = Path(self.temp.name) / ".aura"
        self.meta.mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def write_jsonl(self, name, rows):
        (self.meta / name).write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8")

    def test_old_tombstones_become_undone_at_and_originals_are_kept(self):
        self.write_jsonl("changes.jsonl", [
            {"id": "c1", "operation": "write_file", "time": "2026-08-01T00:00:00+00:00",
             "task_id": "t1", "items": [{"path": "a.txt", "backup": "c1_0.bak"}]},
            {"id": "c2", "operation": "write_file", "time": "2026-08-02T00:00:00+00:00",
             "task_id": "t1", "items": [{"path": "b.txt", "backup": None}]},
            {"id": "u1", "operation": "undo", "undo_of": "c2",
             "time": "2026-08-03T00:00:00+00:00", "items": []},
        ])
        self.write_jsonl("action-log.jsonl", [
            {"time": "2026-08-01T00:00:00+00:00", "action": "write_file", "status": "ok",
             "path": "a.txt"}])
        self.write_jsonl("trash.jsonl", [
            {"trash_name": "x__a.txt", "original_path": "a.txt", "kind": "file",
             "deleted_at": "2026-08-01T00:00:00+00:00"}])

        db = Database(self.meta / "aura.db")
        counts = db.migrate_jsonl(self.meta)
        self.assertEqual(counts.get("changes"), 2)
        self.assertEqual(counts.get("undone"), 1)

        history = {entry["id"]: entry for entry in db.change_history(10)}
        self.assertFalse(history["c1"]["undone"])
        self.assertTrue(history["c2"]["undone"])
        self.assertEqual(db.last_undoable_change()["id"], "c1")
        self.assertEqual(db.recent_actions(5)[0]["path"], "a.txt")
        self.assertIn("x__a.txt", db.trash_entries())

        # The originals are renamed, never deleted, so the old data is provable.
        self.assertFalse((self.meta / "changes.jsonl").exists())
        self.assertTrue((self.meta / "changes.jsonl.migrated").is_file())

    def test_migration_is_skipped_when_there_is_nothing_to_import(self):
        db = Database(self.meta / "aura.db")
        self.assertEqual(db.migrate_jsonl(self.meta), {})


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())

    def tearDown(self):
        self.temp.cleanup()

    def test_a_new_conversation_keeps_the_old_one_on_disk(self):
        self.agent.handle("remember my name is Maya")
        first = self.agent.session_id
        self.assertTrue(self.agent.memory.data["conversation"])

        self.agent.new_session()
        self.assertNotEqual(self.agent.session_id, first)
        # The live context is empty, but nothing was destroyed.
        self.assertEqual(self.agent.memory.data["conversation"], [])
        kept = self.agent.db.session_messages(first)
        self.assertTrue(any("Maya" in item["text"] for item in kept))

    def test_reopening_a_conversation_restores_it_as_live_context(self):
        self.agent.handle("remember my name is Maya")
        first = self.agent.session_id
        self.agent.new_session()
        self.agent.handle("remember preference tone = terse")

        restored = self.agent.open_session(first)
        self.assertEqual(self.agent.session_id, first)
        self.assertTrue(any("Maya" in item["text"] for item in restored))
        # The provider context follows the reopened conversation.
        self.assertTrue(any("Maya" in item["text"]
                            for item in self.agent.memory.data["conversation"]))

    def test_a_conversation_is_titled_by_its_first_message(self):
        self.agent.handle("Build the invoice tool")
        listed = {item["id"]: item for item in self.agent.db.sessions(10)}
        self.assertIn("Build the invoice tool", listed[self.agent.session_id]["title"] or "")

    def test_the_session_survives_a_restart(self):
        self.agent.handle("remember my name is Maya")
        expected = self.agent.session_id
        reopened = AuraAgent(self.agent.sandbox.root, provider=MockProvider())
        self.assertEqual(reopened.session_id, expected)

    def test_opening_an_unknown_conversation_is_refused(self):
        with self.assertRaises(KeyError):
            self.agent.open_session("does-not-exist")

    def test_an_unused_session_never_appears_as_a_conversation(self):
        """Launching Aura, or clicking New and saying nothing, is not a conversation."""
        self.agent.new_session()
        self.assertEqual(self.agent.db.sessions(10), [])
        restarted = AuraAgent(self.agent.sandbox.root, provider=MockProvider())
        self.assertEqual(restarted.db.sessions(10), [])

    def test_search_finds_conversations_by_what_was_said(self):
        self.agent.handle("remember my name is Maya")
        maya = self.agent.session_id
        self.agent.new_session()
        self.agent.handle("remember preference tone = terse")
        terse = self.agent.session_id

        found = self.agent.db.search_messages("Maya")
        self.assertEqual([item["id"] for item in found], [maya])
        self.assertIn("Maya", found[0]["matches"][0]["snippet"])
        # Every word has to appear, so an unrelated extra word rules a match out.
        self.assertEqual(self.agent.db.search_messages("Maya terse"), [])
        self.assertEqual([item["id"] for item in self.agent.db.search_messages("preference")],
                         [terse])
        self.assertEqual(self.agent.db.search_messages("   "), [])

    def test_search_treats_wildcards_as_ordinary_characters(self):
        """A `%` in the box must not quietly match everything."""
        self.agent.handle("remember my name is Maya")
        self.assertEqual(self.agent.db.search_messages("%"), [])
        self.agent.handle("remember interest is 50% humidity")
        self.assertTrue(self.agent.db.search_messages("50%"))

    def test_search_can_be_limited_to_conversations_still_in_the_list(self):
        self.agent.handle("remember my name is Maya")
        archived = self.agent.session_id
        self.agent.new_session()
        self.agent.db.archive_session(archived)
        self.assertEqual(self.agent.db.search_messages("Maya"), [])
        self.assertEqual(
            [item["id"] for item in self.agent.db.search_messages("Maya", 20, True)],
            [archived])

    def test_archiving_hides_a_conversation_without_deleting_it(self):
        self.agent.handle("remember my name is Maya")
        archived_id = self.agent.session_id
        self.agent.new_session()
        self.agent.db.archive_session(archived_id)

        self.assertEqual([item["id"] for item in self.agent.db.sessions(10)], [])
        listed = self.agent.db.sessions(10, include_archived=True)
        self.assertEqual([item["id"] for item in listed], [archived_id])
        self.assertTrue(listed[0]["archived"])
        # Still openable, and restoring puts it back in the ordinary list.
        self.assertTrue(self.agent.db.session_messages(archived_id))
        self.agent.db.archive_session(archived_id, False)
        self.assertEqual([item["id"] for item in self.agent.db.sessions(10)], [archived_id])


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())

    def tearDown(self):
        self.temp.cleanup()

    def _interrupted_build(self):
        """A task that wrote one file, then stopped without finishing."""
        task_id = self.agent.tasks.start("Build a site in shop with index.html and style.css")
        self.agent.sandbox.write_file("shop/index.html", "<!doctype html><html></html>")
        self.agent.tasks.record_tool(task_id, "write_file",
                                     {"path": "shop/index.html"}, {"ok": True})
        return task_id

    def test_the_brief_reports_real_state_not_just_the_log(self):
        task_id = self._interrupted_build()
        brief = self.agent.resume_brief(task_id)
        self.assertEqual(brief["status"], "interrupted")
        self.assertIn("Build a site in shop", brief["request"])
        self.assertEqual(len(brief["completed"]), 1)
        self.assertIn("shop/index.html", brief["completed"][0])
        self.assertIn("exists", brief["completed"][0])
        # The file the request named but which was never written.
        self.assertIn("shop/style.css", brief["outstanding"])

    def test_a_step_whose_file_vanished_is_reported_as_missing(self):
        # The log says it was written; the truth is what counts on resume.
        task_id = self._interrupted_build()
        self.agent.sandbox.safe_delete_file("shop/index.html")
        brief = self.agent.resume_brief(task_id)
        self.assertIn("MISSING now", brief["completed"][0])

    def test_only_unfinished_tasks_can_be_resumed(self):
        task_id = self._interrupted_build()
        self.agent.tasks.finish(task_id, "completed", "All done.")
        with self.assertRaises(ValueError):
            self.agent.resume_brief(task_id)
        with self.assertRaises(KeyError):
            self.agent.resume_brief("no-such-task")

    def test_the_resume_request_tells_the_model_not_to_repeat_work(self):
        brief = self.agent.resume_brief(self._interrupted_build())
        request = self.agent.format_resume_request(brief)
        self.assertIn("Do not repeat work", request)
        self.assertIn("Build a site in shop", request)
        self.assertIn("shop/index.html", request)
        self.assertIn("shop/style.css", request)

    def test_reading_a_file_is_not_reported_as_completed_work(self):
        task_id = self.agent.tasks.start("Read notes.txt and summarise it")
        self.agent.tasks.record_tool(task_id, "read_file", {"path": "notes.txt"}, {"ok": True})
        brief = self.agent.resume_brief(task_id)
        self.assertEqual(brief["completed"], [])

    def test_batch_writes_count_as_completed_work(self):
        # Live test caught this: the model used write_files, whose arguments hold
        # a list rather than a single path, so the brief claimed nothing had been
        # done even though a file was on disk.
        task_id = self.agent.tasks.start("Build a site in shop with index.html and style.css")
        self.agent.sandbox.write_file("shop/index.html", "<!doctype html><html></html>")
        self.agent.tasks.record_tool(
            task_id, "write_files",
            {"files": [{"path": "shop/index.html"}]}, {"ok": True})
        brief = self.agent.resume_brief(task_id)
        self.assertEqual(len(brief["completed"]), 1)
        self.assertIn("shop/index.html", brief["completed"][0])
        self.assertIn("exists", brief["completed"][0])
        self.assertNotIn("No file was successfully changed",
                         self.agent.format_resume_request(brief))

    def test_a_failed_step_is_not_claimed_as_done(self):
        task_id = self.agent.tasks.start("Create blocked.txt")
        self.agent.tasks.record_tool(task_id, "write_file", {"path": "blocked.txt"},
                                     {"ok": False, "error": "denied"})
        self.assertEqual(self.agent.resume_brief(task_id)["completed"], [])


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

    def test_system_messages_are_merged_into_one_leading_message(self):
        # Strict chat templates (qwen3.5-9b among them) raise an error unless a
        # system message is first and alone, and Aura adds several.
        merged = LMStudioProvider.merge_system_messages([
            {"role": "system", "content": "base rules"},
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "host notes"},
            {"role": "assistant", "content": "hello"},
            {"role": "system", "content": "   "},
            {"role": "tool", "tool_call_id": "c1", "content": "{}"},
        ])
        self.assertEqual(merged[0]["role"], "system")
        self.assertEqual(merged[0]["content"], "base rules\n\nhost notes")
        self.assertEqual([m["role"] for m in merged[1:]], ["user", "assistant", "tool"])
        self.assertEqual(LMStudioProvider.merge_system_messages(
            [{"role": "user", "content": "hi"}]), [{"role": "user", "content": "hi"}])

    def test_reasoning_only_answers_are_used_instead_of_failing(self):
        # Reasoning models leave content empty and put the answer in
        # reasoning_content; treating that as nothing reported a completed job
        # as "the model returned neither text nor a tool request".
        reply = LMStudioProvider._parse_completion({"choices": [{"message": {
            "role": "assistant", "content": "",
            "reasoning_content": "The file was written successfully.",
            "tool_calls": []}}]})
        self.assertEqual(reply.content, "The file was written successfully.")
        self.assertEqual(reply.tool_calls, [])
        # Real content still wins over the thinking transcript.
        preferred = LMStudioProvider._parse_completion({"choices": [{"message": {
            "role": "assistant", "content": "Done.",
            "reasoning_content": "thinking out loud"}}]})
        self.assertEqual(preferred.content, "Done.")

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

    def test_a_follow_up_does_not_inherit_the_previous_turns_deliverables(self):
        """From the real log: three external-write attempts, then "Call
        undo_external_change to roll that back" failed demanding report.txt —
        a file that follow-up never mentioned."""
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            provider.complete = unittest.mock.Mock(
                return_value=ProviderReply("Rolled that change back.", []))
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            agent.memory.remember_message(
                "user", "Use write_external_file to replace report.txt with the text hello")
            agent.memory.remember_message("assistant", "Done, report.txt was replaced.")
            answer = agent.handle("Call undo_external_change to roll that back.")
            self.assertNotIn("still missing", answer)
            self.assertNotIn("report.txt", answer)

    def test_work_in_a_granted_folder_is_not_owed_a_workspace_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            provider.complete = unittest.mock.Mock(
                return_value=ProviderReply("I cannot reach that folder yet.", []))
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            answer = agent.handle(
                "Use write_external_file to replace report.txt in the granted write folder "
                "with the text hello")
            # Honest about what it did not do, but never claiming a workspace
            # file was owed: report.txt was never meant to live there.
            self.assertNotIn("required artifacts", answer)
            self.assertFalse((agent.sandbox.root / "report.txt").exists())

    def test_a_named_file_in_the_current_request_is_still_required(self):
        """The narrowing must not weaken the contract for the actual request."""
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            provider.complete = unittest.mock.Mock(
                return_value=ProviderReply("All done!", []))
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            answer = agent.handle("Create a file called notes.txt with the text hello")
            self.assertIn("notes.txt", answer)
            self.assertIn("Not confirmed", answer)

    def test_a_retry_names_the_tool_to_call(self):
        """The journal is unambiguous: this model ignored "create a file called
        X" three times, then obeyed "use create_file to make X" at once."""
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            replies = iter([
                ProviderReply("I have created the file for you.", []),
                ProviderReply("", [ToolCall("1", "create_file",
                                            {"path": "notes.txt", "content": "hello"})]),
                ProviderReply("notes.txt now exists.", []),
            ])
            nudges = []
            def complete(messages, *_args, **_kwargs):
                nudges.append(messages[-1].get("content", ""))
                return next(replies)
            provider.complete = unittest.mock.Mock(side_effect=complete)
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            answer = agent.handle("Create a file called notes.txt with the text hello")
            self.assertIn("create_file", nudges[1])
            self.assertIn("notes.txt", nudges[1])
            self.assertEqual(agent.sandbox.read_file("notes.txt"), "hello")
            self.assertNotIn("Not confirmed", answer)

    def test_an_empty_model_response_is_retried_before_the_turn_is_lost(self):
        """The most frequent real failure: the model answers with nothing at all.
        It used to end the turn immediately, discarding the request."""
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            replies = iter([
                ProviderReply("", []),
                ProviderReply("Here is what I found.", []),
            ])
            nudges = []
            def complete(messages, *_args, **_kwargs):
                nudges.append(messages[-1])
                return next(replies)
            provider.complete = unittest.mock.Mock(side_effect=complete)
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            answer = agent.handle("What do you think of this idea?")
            self.assertIn("Here is what I found.", answer)
            self.assertEqual(provider.complete.call_count, 2)
            self.assertIn("completely empty", nudges[-1]["content"])

    def test_work_that_succeeded_is_not_reported_as_a_failure(self):
        """From a live run: she removed a broken link, then went silent, and
        Aura said "I couldn't complete that safely" — the opposite of the truth.
        The work happened; only the closing sentence did not."""
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            replies = iter([
                ProviderReply("", [ToolCall("1", "write_file",
                                            {"path": "page.html", "content": "<h1>fixed</h1>"})]),
                ProviderReply("", []), ProviderReply("", []),
                ProviderReply("", []), ProviderReply("", []),
            ])
            provider.complete = unittest.mock.Mock(
                side_effect=lambda *a, **k: next(replies, ProviderReply("", [])))
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            answer = agent.handle("Fix the broken reference in page.html")
            self.assertNotIn("couldn’t complete", answer)
            self.assertIn("stopped before describing it", answer)
            self.assertIn("write_file", answer)
            # And it says plainly that the description is Aura's, not the model's.
            self.assertIn("Not confirmed", answer)
            self.assertEqual(agent.sandbox.read_file("page.html"), "<h1>fixed</h1>")
            self.assertEqual(agent.tasks.recent(1)[0]["status"], "completed")

    def test_a_model_that_never_answers_is_reported_plainly(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            provider.complete = unittest.mock.Mock(
                side_effect=lambda *args, **kwargs: ProviderReply("", []))
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            answer = agent.handle("What do you think of this idea?")
            self.assertIn("empty response", answer)
            self.assertIn("LM Studio", answer)
            # Two calls, not four. Every silence episode in the live log spent the
            # full budget re-asking the same question and got the same silence
            # back, at minutes a time. Silence now buys exactly one retry — the
            # one that asks differently.
            self.assertEqual(provider.complete.call_count, 2)

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
            # Two rounds, not three: Aura asks the model once, then validates
            # deterministically itself rather than spending more of the user's
            # time asking for something the backend is about to do anyway.
            self.assertEqual(provider.complete.call_count, 2)

    def test_unconfirmed_work_keeps_the_answer_and_says_what_is_unproven(self):
        # A failed gate used to raise, so the user lost the model's whole answer
        # to "I couldn't complete that safely". The answer now survives with an
        # explicit note, so nothing is presented as verified that was not.
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            provider.complete = unittest.mock.Mock(return_value=ProviderReply(
                "I created report.txt for you.", []))
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            answer = agent.handle("Create report.txt in the workspace")

            self.assertIn("I created report.txt for you.", answer)
            self.assertIn("Not confirmed", answer)
            self.assertIn("report.txt", answer)
            self.assertNotIn("Confirmed evidence", answer)

    def test_every_gate_shares_one_bounded_retry_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            provider.complete = unittest.mock.Mock(return_value=ProviderReply(
                "All done, I promise.", []))
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            agent.handle("Create report.txt in the workspace")
            # One first answer plus at most MAX_COMPLETION_RETRIES more, however
            # many gates were unhappy — four separate counters used to allow nine.
            self.assertLessEqual(provider.complete.call_count,
                                 agent.MAX_COMPLETION_RETRIES + 1)

    def test_a_question_mentioning_a_project_does_not_demand_validation(self):
        # "project" in a read-only question used to switch validation on and
        # burn retries proving something the user never asked about.
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            provider.complete = unittest.mock.Mock(return_value=ProviderReply(
                "Your project looks tidy and well organised.", []))
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            answer = agent.handle("How does my project look these days?")
            self.assertIn("tidy", answer)
            self.assertEqual(provider.complete.call_count, 1)

    def test_backend_verifies_final_mutation_when_model_does_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            provider.complete = unittest.mock.Mock(side_effect=[
                ProviderReply("", [ToolCall("1", "create_file",
                                             {"path": "note.txt", "content": "verified"})]),
                ProviderReply("Created note.txt.", []),
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
        # The plan tools ride with every build: recording steps is part of building,
        # and a tool the router never offers does not exist.
        self.assertEqual(names, {"list_files", "read_file", "create_file", "write_file",
                                 "validate_project", "record_plan_steps", "update_plan_step"})

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

    def test_spoken_text_is_punctuated_so_the_voice_can_breathe(self):
        """A stripped list item carried no terminal mark, so several of them were
        read as one unbroken sentence — the main reason speech sounded mechanical."""
        spoken = SpeechOutput.prepare_spoken_text(
            "Done. I built it.\n\nConfirmed evidence:\n"
            "- Validation passed for shop\n- Required deliverables present\n\nAnything else?")
        first, second, third = spoken.split("\n\n")
        self.assertEqual(first, "Done. I built it.")
        self.assertIn("Confirmed evidence,", second)
        self.assertIn("Validation passed for shop.", second)
        self.assertIn("Required deliverables present.", second)
        self.assertEqual(third, "Anything else?")

    def test_paths_are_spoken_as_names_not_punctuation(self):
        spoken = SpeechOutput.prepare_spoken_text("I wrote shop/index.html and style.css.")
        self.assertNotIn("/", spoken)
        self.assertIn("index dot html", spoken)
        self.assertIn("style dot css", spoken)

    def test_neural_speech_puts_real_silence_between_sentences(self):
        speech = SpeechOutput(enabled=True, engine="piper")

        class FakeChunk:
            sample_rate, sample_width, sample_channels = 22050, 2, 1
            audio_int16_bytes = b"\x01\x02" * 2205        # 0.1 s of sound

        class FakeVoice:
            def synthesize(self, text, syn_config=None):
                return [FakeChunk() for _ in re.split(r"(?<=[.!?])\s+", text.strip()) if _]

        speech._neural_voice = FakeVoice()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temporary:
            path = Path(temporary.name)
        try:
            speech._render_piper_wav("One. Two.\n\nThree.", str(path))
            with wave.open(str(path)) as handle:
                seconds = handle.getnframes() / handle.getframerate()
            # Three sentences of 0.1 s, two sentence gaps and one paragraph gap.
            expected = 0.3 + (SpeechOutput.SENTENCE_PAUSE_MS * 2
                              + SpeechOutput.PARAGRAPH_PAUSE_MS) / 1000
            self.assertAlmostEqual(seconds, expected, places=2)
        finally:
            path.unlink(missing_ok=True)

    def test_a_failed_voice_preview_says_so_instead_of_going_quiet(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        agent = AuraAgent(Path(temp.name) / "workspace", provider=MockProvider())
        speech = SpeechOutput(enabled=False)
        bridge = AuraWebBridge(agent=agent, speech=speech)
        self.addCleanup(bridge.shutdown)
        with patch.object(speech, "speak",
                          return_value=(False, "Neural speech is unavailable: no model")):
            self.assertTrue(bridge.preview_voice()["ok"])
            deadline = time.monotonic() + 3
            done = None
            while time.monotonic() < deadline and done is None:
                done = next((event for event in list(bridge.events)
                             if event.get("type") == "speech"
                             and event.get("preview") and event.get("active") is False), None)
                time.sleep(0.01)
        self.assertIsNotNone(done)
        self.assertFalse(done["spoken"])
        self.assertIn("unavailable", done["message"])

    def test_a_neural_voice_can_only_be_chosen_from_the_voices_folder(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        agent = AuraAgent(Path(temp.name) / "workspace", provider=MockProvider())
        bridge = AuraWebBridge(agent=agent, speech=SpeechOutput(enabled=False))
        self.addCleanup(bridge.shutdown)
        listed = bridge.get_voices()
        self.assertIn("neural", listed)
        settings = bridge.get_settings()
        refused = bridge.save_settings({**settings,
                                        "speech_model": "../../etc/passwd.onnx"})
        self.assertFalse(refused["ok"])
        self.assertEqual(agent.config.data["speech_model"], settings["speech_model"])

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
            # Wait for the thing that actually has to have happened — the stream
            # delivering audio — rather than for a guessed 30 milliseconds. A fixed
            # sleep is a bet on the machine's mood: too short and the test fails on a
            # busy day, too long and every run pays for it.
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not levels:
                time.sleep(0.005)
            self.assertTrue(levels, "the fake stream never delivered audio")
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

    def test_one_fact_is_one_node_even_when_stored_in_two_places(self):
        """A preference Aura also learned about you was drawn twice, in two
        branches, with no hint that it was the same thing."""
        memory = {
            "preferences": {"tone": "terse", "theme": "dark"},
            "profile_memories": [{"id": "mem", "category": "preference",
                                  "value": "tone = terse", "confidence": 1,
                                  "confirmed": True, "pinned": False, "source": "user"}],
        }
        nodes, edges = build_mind_graph(memory, [], [])
        edge_pairs = {(edge.source, edge.target) for edge in edges}
        labels = [node.label for node in nodes]
        self.assertEqual(sum("terse" in label for label in labels), 1)
        # The single node hangs under both headings, so neither view loses it.
        self.assertIn(("personal_memory", "personal:mem"), edge_pairs)
        self.assertIn(("preferences", "personal:mem"), edge_pairs)
        # An unrelated preference still gets its own node.
        self.assertTrue(any(label.startswith("theme") for label in labels))

    def test_a_task_without_a_request_is_named_rather_than_blank(self):
        nodes, _ = build_mind_graph({}, [{"task_id": "t", "request": "",
                                          "status": "error", "tools": []}], [])
        task = next(node for node in nodes if node.node_id.startswith("task:"))
        self.assertTrue(task.label.strip())
        self.assertIn("no request", task.label.casefold())

    def test_a_task_is_linked_to_the_message_that_asked_for_it(self):
        memory = {"conversation": [{"role": "user", "text": "Build a clock"},
                                   {"role": "assistant", "text": "Done."}]}
        tasks = [{"task_id": "t", "request": "build a clock!", "status": "completed",
                  "tools": []}]
        _, edges = build_mind_graph(memory, tasks, [])
        pairs = {(edge.source, edge.target) for edge in edges}
        self.assertTrue(any(source.startswith("conversation:") and target.startswith("task:")
                            for source, target in pairs))

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

    def test_the_bridge_survives_an_agent_that_logs_while_being_built(self):
        # Migration and the retention sweep log during AuraAgent.__init__, so
        # those events reach _on_log before the bridge has finished its own
        # __init__. Aura used to crash on startup with AttributeError here.
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            meta = workspace / ".aura"
            meta.mkdir(parents=True)
            (meta / "changes.jsonl").write_text(json.dumps({
                "id": "c1", "operation": "write_file", "time": "2026-08-01T00:00:00+00:00",
                "items": [{"path": "a.txt", "backup": None}]}) + "\n", encoding="utf-8")
            agent = AuraAgent(workspace, provider=MockProvider())
            bridge = AuraWebBridge(agent=agent, speech=SpeechOutput(enabled=False))
            try:
                self.assertTrue(any(event.get("type") == "log" for event in bridge.events)
                                or bridge.get_bootstrap()["app"] == "Aura")
            finally:
                bridge.shutdown()

    def test_archiving_the_live_conversation_is_refused(self):
        """Archiving what you are currently in would hide a conversation that
        keeps collecting messages, so it has to be left behind first."""
        self.bridge.agent.handle("hello")
        current = self.bridge.agent.session_id
        refused = self.bridge.archive_session(current)
        self.assertFalse(refused["ok"])
        self.assertEqual([item["id"] for item in self.bridge.list_sessions()["sessions"]],
                         [current])

        self.bridge.new_session()
        self.assertTrue(self.bridge.archive_session(current)["ok"])
        self.assertEqual(self.bridge.list_sessions()["sessions"], [])
        self.assertEqual(
            [item["id"] for item in self.bridge.list_sessions(30, True)["sessions"]],
            [current])

    def test_the_first_run_guide_appears_once_and_can_be_asked_for_again(self):
        self.assertFalse(self.bridge.get_bootstrap()["onboarded"])
        self.assertTrue(self.bridge.complete_onboarding()["ok"])
        self.assertTrue(self.bridge.get_bootstrap()["onboarded"])
        # It stays gone across a restart, and Settings can bring it back.
        restarted = AuraAgent(self.bridge.agent.sandbox.root, provider=MockProvider())
        self.assertTrue(restarted.config.data["onboarded"])
        self.bridge.restart_onboarding()
        self.assertFalse(self.bridge.get_bootstrap()["onboarded"])

    def test_skipping_the_guide_leaves_the_model_settings_untouched(self):
        """Skipping must never quietly repoint Aura at a different server."""
        before = dict(self.bridge.agent.config.data)
        self.assertTrue(self.bridge.complete_onboarding()["ok"])
        after = self.bridge.agent.config.data
        self.assertEqual(after["lm_studio_url"], before["lm_studio_url"])
        self.assertEqual(after["model"], before["model"])

    def test_diagnostics_report_describes_the_install_without_private_content(self):
        agent = self.bridge.agent
        agent.memory.set_name("Maya")
        agent.memory.learn_fact("interest", "teal paint", source="user")
        agent.handle("remember my favourite colour is teal")
        agent.log.record("connect_model", "error", error="Connection refused")

        written = self.bridge.export_diagnostics()
        self.assertTrue(written["ok"])
        text = (agent.sandbox.root / written["path"]).read_text(encoding="utf-8")
        # Useful for diagnosing: what failed, how it is configured, how big it got.
        self.assertIn("Connection refused", text)
        self.assertIn("lm_studio_url", text)
        self.assertIn("messages:", text)
        self.assertIn("None. Aura can reach only its own workspace.", text)
        # Private by construction: no conversation text, no memory content.
        self.assertNotIn("teal", text)
        self.assertNotIn("Maya", text)

    def test_exporting_a_conversation_writes_readable_markdown(self):
        self.bridge.agent.handle("remember my name is Maya")
        session_id = self.bridge.agent.session_id
        written = self.bridge.export_conversation(session_id)
        self.assertTrue(written["ok"])
        text = (self.bridge.agent.sandbox.root / written["path"]).read_text(encoding="utf-8")
        self.assertIn("remember my name is Maya", text)
        self.assertIn("**You**", text)
        self.assertIn("**Aura**", text)
        self.assertFalse(self.bridge.export_conversation("does-not-exist")["ok"])

    def test_stopping_closes_the_dialog_it_refuses(self):
        """Found by looking at the screen, not the API: Stop unblocked the
        waiting thread but left the approval card on display with nothing
        behind it, and Escape then answered a dead approval instead of closing.
        """
        waiting = queue.Queue(maxsize=1)
        with self.bridge._approval_lock:
            self.bridge._approvals["abc123"] = waiting
        self.bridge.stop()
        self.assertEqual(waiting.get_nowait(), "deny")
        closed = [event for event in self.bridge.events
                  if event.get("type") == "approval_closed"]
        self.assertEqual([event["approval_id"] for event in closed], ["abc123"])
        self.assertEqual(self.bridge._approvals, {})

    def test_an_emergency_stop_also_closes_a_waiting_dialog(self):
        waiting = queue.Queue(maxsize=1)
        with self.bridge._approval_lock:
            self.bridge._approvals["xyz789"] = waiting
        self.bridge.emergency_stop()
        self.assertEqual(waiting.get_nowait(), "deny")
        self.assertTrue(any(event.get("type") == "approval_closed"
                            and event.get("approval_id") == "xyz789"
                            for event in self.bridge.events))

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
            last_language_covered = True

            def speak(self, _text, on_cues=None, language=None):
                self.spoken_language = language
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
        # This page renders whole without scripts, so the panel stays quiet.
        self.assertFalse(preview["scripts_present"])
        self.bridge.agent.sandbox.write_file(
            "site/app.html", '<html><body><div id="list"></div>'
                             '<script src="app.js"></script></body></html>')
        # This one builds its own content, so the empty preview needs explaining.
        self.assertTrue(self.bridge.preview_workspace_file("site/app.html")["scripts_present"])
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

    def test_the_model_can_use_but_never_widen_folder_permissions(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "readme.txt").write_text("external content", encoding="utf-8")

        # Without a grant the tools fail, and no tool exists for self-granting.
        names = {d["function"]["name"] for d in self.bridge.agent.tool_definitions()}
        self.assertNotIn("grant_folder_access", names)
        denied = self.bridge.agent._execute_tool(
            ToolCall("c1", "read_external_file", {"path": str(outside / "readme.txt")}), None)
        self.assertFalse(denied["ok"])

        # The user grants it through the bridge, which is UI-only.
        granted = self.bridge.grant_folder_access(str(outside), "session")
        self.assertTrue(granted["ok"])
        allowed = self.bridge.agent._execute_tool(
            ToolCall("c2", "read_external_file", {"path": str(outside / "readme.txt")}), None)
        self.assertTrue(allowed["ok"])
        self.assertEqual(allowed["content"], "external content")

        listed = self.bridge.agent._execute_tool(
            ToolCall("c3", "list_granted_folders", {}), None)
        self.assertEqual(len(listed["folders"]), 1)

        # Emergency stop closes it again immediately.
        self.assertEqual(self.bridge.revoke_all_permissions()["revoked"], 1)
        after = self.bridge.agent._execute_tool(
            ToolCall("c4", "read_external_file", {"path": str(outside / "readme.txt")}), None)
        self.assertFalse(after["ok"])
        self.assertEqual(self.bridge.list_permissions()["active"], [])

    def test_granting_a_protected_location_is_refused_through_the_bridge(self):
        result = self.bridge.grant_folder_access(Path(self.temp.name).anchor or "/",
                                                 "session")
        self.assertFalse(result["ok"])
        self.assertEqual(self.bridge.list_permissions()["active"], [])

    def test_the_provider_choice_is_saved_and_the_key_never_comes_back(self):
        """The key is write-only from the interface. It is left out of
        get_settings and out of diagnostics, which is the only thing keeping a
        diagnostics file shareable."""
        saved = self.bridge.save_settings({**self.bridge.get_settings(),
                                           "provider": "claude",
                                           "cloud_model": "claude-sonnet-5",
                                           "anthropic_api_key": "sk-ant-secret"})
        self.assertTrue(saved.get("ok"), saved)
        settings = self.bridge.get_settings()
        self.assertEqual(settings["provider"], "claude")
        self.assertEqual(settings["cloud_model"], "claude-sonnet-5")
        self.assertNotIn("anthropic_api_key", settings)
        self.assertTrue(settings["anthropic_key_set"])
        self.assertNotIn("anthropic_api_key", AuraWebBridge.DIAGNOSTIC_SETTINGS)
        self.assertIsInstance(self.bridge.agent.provider, AnthropicProvider)

    def test_a_blank_key_field_keeps_the_stored_key(self):
        """The field opens blank because the key is never sent to the browser,
        so treating blank as "erase it" would delete the key on every save."""
        self.bridge.save_settings({**self.bridge.get_settings(), "provider": "claude",
                                   "anthropic_api_key": "sk-ant-secret"})
        self.bridge.save_settings({**self.bridge.get_settings(), "provider": "claude",
                                   "anthropic_api_key": ""})
        self.assertEqual(self.bridge.agent.config.data["anthropic_api_key"], "sk-ant-secret")
        self.assertTrue(self.bridge.get_settings()["anthropic_key_set"])

    def test_choosing_claude_without_a_key_is_refused_with_the_way_out(self):
        with unittest.mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
            result = self.bridge.save_settings({**self.bridge.get_settings(),
                                                "provider": "claude",
                                                "anthropic_api_key": ""})
        self.assertFalse(result["ok"])
        self.assertIn("ANTHROPIC_API_KEY", result["error"])

    def test_an_unknown_model_name_is_refused_rather_than_sent(self):
        result = self.bridge.save_settings({**self.bridge.get_settings(),
                                            "provider": "claude",
                                            "anthropic_api_key": "sk-ant-secret",
                                            "cloud_model": "claude-whatever"})
        self.assertFalse(result["ok"])

    def test_the_interface_is_told_where_thinking_happens(self):
        """The status line read "Local - private" unconditionally, which stops
        being true the moment the conversation is going to Anthropic."""
        self.assertEqual(self.bridge.get_bootstrap()["capabilities"]["thinking_at"], "local")
        self.bridge.save_settings({**self.bridge.get_settings(), "provider": "claude",
                                   "anthropic_api_key": "sk-ant-secret"})
        capabilities = self.bridge.get_bootstrap()["capabilities"]
        self.assertEqual(capabilities["thinking_at"], "cloud")
        self.assertEqual(capabilities["thinking_model"], "claude-opus-5")

    def test_local_settings_survive_a_trip_through_claude_and_back(self):
        """Switching away and back must not mean filling the form in again."""
        before = self.bridge.get_settings()["lm_studio_url"]
        self.bridge.save_settings({**self.bridge.get_settings(), "provider": "claude",
                                   "anthropic_api_key": "sk-ant-secret"})
        self.bridge.save_settings({**self.bridge.get_settings(), "provider": "local"})
        self.assertEqual(self.bridge.get_settings()["lm_studio_url"], before)
        self.assertIsInstance(self.bridge.agent.provider, LMStudioProvider)

    def test_gpt_is_a_third_choice_with_its_own_key(self):
        saved = self.bridge.save_settings({**self.bridge.get_settings(),
                                           "provider": "openai",
                                           "openai_model": "gpt-5",
                                           "openai_api_key": "sk-openai-secret"})
        self.assertTrue(saved.get("ok"), saved)
        settings = self.bridge.get_settings()
        self.assertEqual(settings["provider"], "openai")
        self.assertEqual(settings["openai_model"], "gpt-5")
        self.assertNotIn("openai_api_key", settings)
        self.assertTrue(settings["openai_key_set"])
        self.assertIsInstance(self.bridge.agent.provider, OpenAIProvider)
        capabilities = self.bridge.get_bootstrap()["capabilities"]
        self.assertEqual(capabilities["thinking_at"], "cloud")
        self.assertIn("api.openai.com", capabilities["thinking_label"])

    def test_the_address_survives_settings_and_is_checked_there(self):
        saved = self.bridge.save_settings({**self.bridge.get_settings(),
                                           "provider": "openai",
                                           "openai_base_url": "https://api.groq.com/openai/v1",
                                           "openai_api_key": "sk-somewhere"})
        self.assertTrue(saved.get("ok"), saved)
        self.assertEqual(self.bridge.get_settings()["openai_base_url"],
                         "https://api.groq.com/openai/v1")
        self.assertIn("api.groq.com",
                      self.bridge.get_bootstrap()["capabilities"]["thinking_label"])
        # A wrong address is a message on save, not a failure on the next message.
        refused = self.bridge.save_settings({**self.bridge.get_settings(),
                                             "openai_base_url": "not-a-url"})
        self.assertFalse(refused["ok"])

    def test_choosing_gpt_without_a_key_is_refused(self):
        with unittest.mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            result = self.bridge.save_settings({**self.bridge.get_settings(),
                                                "provider": "openai",
                                                "openai_api_key": ""})
        self.assertFalse(result["ok"])
        self.assertIn("OPENAI_API_KEY", result["error"])

    def test_the_two_keys_do_not_overwrite_each_other(self):
        """One save carries both fields, and a blank one must not wipe the key
        belonging to the provider that is not being changed."""
        self.bridge.save_settings({**self.bridge.get_settings(), "provider": "claude",
                                   "anthropic_api_key": "sk-ant-secret"})
        self.bridge.save_settings({**self.bridge.get_settings(), "provider": "openai",
                                   "openai_api_key": "sk-openai-secret"})
        config = self.bridge.agent.config.data
        self.assertEqual(config["anthropic_api_key"], "sk-ant-secret")
        self.assertEqual(config["openai_api_key"], "sk-openai-secret")

    def test_a_stored_key_can_be_taken_back(self):
        """Blank means "keep the stored key", so without this there is no way
        to withdraw a credential through the interface at all."""
        self.bridge.save_settings({**self.bridge.get_settings(), "provider": "claude",
                                   "anthropic_api_key": "sk-ant-secret"})
        with unittest.mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
            result = self.bridge.forget_cloud_key("claude")
            self.assertTrue(result["ok"])
            self.assertFalse(self.bridge.get_settings()["anthropic_key_set"])
        self.assertEqual(self.bridge.agent.config.data["anthropic_api_key"], "")
        # Staying on Claude with no key would only fail on the next message.
        self.assertEqual(self.bridge.get_settings()["provider"], "local")
        self.assertIsInstance(self.bridge.agent.provider, LMStudioProvider)

    def test_forgetting_one_key_leaves_the_other_alone(self):
        """An earlier version cleared both, which would have thrown away a
        working key in order to remove an unused one."""
        self.bridge.save_settings({**self.bridge.get_settings(), "provider": "claude",
                                   "anthropic_api_key": "sk-ant-secret",
                                   "openai_api_key": "sk-openai-secret"})
        self.bridge.forget_cloud_key("openai")
        config = self.bridge.agent.config.data
        self.assertEqual(config["openai_api_key"], "")
        self.assertEqual(config["anthropic_api_key"], "sk-ant-secret")
        # Claude was in use and had nothing removed, so it stays in use.
        self.assertEqual(config["provider"], "claude")
        self.assertIsInstance(self.bridge.agent.provider, AnthropicProvider)

    def test_vision_mode_is_settable_and_readable_through_settings(self):
        # The override existed in config but had no Settings control, so the
        # documented way to force images on or off was not actually reachable.
        self.assertEqual(self.bridge.get_settings()["vision_mode"], "auto")
        saved = self.bridge.save_settings({**self.bridge.get_settings(),
                                           "vision_mode": "off"})
        self.assertTrue(saved["ok"])
        self.assertEqual(self.bridge.get_settings()["vision_mode"], "off")
        self.assertFalse(self.bridge.agent.vision_enabled())
        # An unknown value must fall back to the safe automatic guess.
        self.bridge.save_settings({**self.bridge.get_settings(),
                                   "vision_mode": "sometimes"})
        self.assertEqual(self.bridge.get_settings()["vision_mode"], "auto")

    def test_memory_export_writes_readable_json_into_the_workspace(self):
        self.bridge.agent.memory.learn_fact("preference", "Concise answers", source="t",
                                            confidence=.9, explicit=True)
        result = self.bridge.export_personal_memory()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        exported = json.loads(self.bridge.agent.sandbox.read_file(result["path"]))
        self.assertEqual(exported["count"], 1)
        self.assertEqual(exported["memories"][0]["value"], "Concise answers")
        self.assertTrue(exported["exported"])

    def test_memory_revert_round_trip_through_the_bridge(self):
        item = self.bridge.agent.memory.learn_fact("tool", "Python for prototypes",
                                                   source="t", confidence=.9, explicit=True)
        self.assertTrue(self.bridge.update_personal_memory(
            item["id"], {"value": "Rust for prototypes"})["ok"])
        reverted = self.bridge.revert_personal_memory(item["id"])
        self.assertTrue(reverted["ok"])
        self.assertEqual(reverted["memory"]["value"], "Python for prototypes")
        # A second revert has nothing left to restore and must fail cleanly.
        again = self.bridge.revert_personal_memory(item["id"])
        self.assertFalse(again["ok"])
        self.assertIn("earlier version", again["error"])

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
            agent.config.update(seeded_checks=list(checks.DEFAULT_CHECKS))
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

    def test_the_interface_keeps_keyboard_and_screen_reader_users_oriented(self):
        with urlopen(self.base + "/app.js", timeout=3) as response:
            script = response.read().decode("utf-8")
        with urlopen(self.base + "/styles.css", timeout=3) as response:
            styles = response.read().decode("utf-8")
        # A dialog contains focus by making everything else inert, and hands
        # focus back to whatever opened it.
        self.assertIn("elements.app.inert = Boolean(top)", script)
        self.assertIn("opener.focus()", script)
        # Escape closes whichever dialog is on top, so newer dialogs are covered
        # without anyone remembering to extend a list.
        self.assertIn("closeModal(top)", script)
        # Streaming must not be announced token by token.
        self.assertNotIn('id="conversation" class="conversation" aria-live', self.index)
        self.assertIn('id="announcer"', self.index)
        self.assertIn('role="status"', self.index)
        self.assertIn(".sr-only", styles)
        self.assertIn('class="modal-x" aria-label="Close"', self.index)

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
        # Lip sync: the mouth clock is offset by how long the cue event spent in
        # transit, and polling tightens while Aura speaks so that stays small.
        self.assertIn("state.speechStarted=performance.now()-elapsed", model)
        self.assertIn("lastEventAgeMs", script)
        self.assertIn("SPEAKING_POLL_MS", script)
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

    def test_every_api_call_in_the_page_actually_exists(self):
        """The class of bug this catches: the UI calls a name nothing answers.

        A panel wired to a method that was renamed, or never registered in
        API_METHODS, fails only when a user clicks the button — which is exactly
        the kind of thing a test should find first.
        """
        script = (Path(__file__).parents[1] / "aura" / "web" / "app.js").read_text(encoding="utf-8")
        called = set(re.findall(r'callApi\(\s*"([a-z_]+)"', script))
        self.assertIn("set_check_enabled", called)
        unknown = sorted(name for name in called if name not in API_METHODS)
        self.assertEqual(unknown, [], f"the page calls unregistered methods: {unknown}")
        missing = sorted(name for name in called if not hasattr(self.bridge, name))
        self.assertEqual(missing, [], f"registered but not implemented: {missing}")

    def test_the_watch_panel_is_reachable_and_wired(self):
        self.assertIn('id="watchButton"', self.index)
        self.assertIn('id="watchModal"', self.index)
        self.assertIn('id="watchList"', self.index)
        script = (Path(__file__).parents[1] / "aura" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("openWatchPanel", script)
        self.assertIn('$("#watchButton").addEventListener', script)

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

    def test_loopback_is_accepted_by_either_name_but_nothing_else_is(self):
        """Opening the page as localhost used to leave the whole UI dead with
        'Unauthorized local request'; both loopback names are the same machine."""
        port = self.server.server_address[1]
        for origin, expected in ((f"http://localhost:{port}", True),
                                 (f"http://127.0.0.1:{port}", True),
                                 ("http://evil.example", False)):
            request = Request(
                self.base + "/api/call",
                data=b'{"method":"get_bootstrap","args":[]}',
                headers={"Content-Type": "application/json", "Cookie": self.cookie,
                         "Origin": origin, "X-Aura-Client": "html-ui-v1"},
                method="POST")
            if expected:
                with urlopen(request, timeout=3) as response:
                    self.assertTrue(json.loads(response.read().decode("utf-8"))["ok"])
            else:
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


class OnePreferenceStoreTests(unittest.TestCase):
    """A preference lived in a flat dict *and* as a profile memory, written by
    two different tools. That is what made Aura Mind draw one fact twice."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "memory.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_both_routes_now_write_the_same_single_memory(self):
        memory = MemoryStore(self.path)
        memory.set_preference("tone", "terse")
        memory.learn_fact("preference", "tone = terse", source="chat", explicit=True)
        preferences = [item for item in memory.profile_memories()
                       if item["category"] == "preference"]
        self.assertEqual(len(preferences), 1)
        self.assertEqual(memory.data["preferences"], {"tone": "terse"})

    def test_a_preference_can_now_be_forgotten_like_any_other_memory(self):
        """The old dict had no way to edit, confirm, revert, or export it."""
        memory = MemoryStore(self.path)
        memory.set_preference("theme", "dark")
        memory.forget_profile_memory(memory.profile_memories()[0]["id"])
        self.assertEqual(memory.data["preferences"], {})

    def test_an_existing_flat_preference_list_is_adopted_once(self):
        self.path.write_text(json.dumps({
            "name": "Maya",
            "preferences": {"tone": "terse", "theme": "dark"},
            "profile_memories": [],
        }), encoding="utf-8")
        memory = MemoryStore(self.path)
        adopted = {item["value"] for item in memory.profile_memories()
                   if item["category"] == "preference"}
        self.assertEqual(adopted, {"tone = terse", "theme = dark"})
        self.assertEqual(memory.data["preferences"], {"tone": "terse", "theme": "dark"})
        # Reopening must not duplicate what it already adopted.
        again = MemoryStore(self.path)
        self.assertEqual(len([item for item in again.profile_memories()
                              if item["category"] == "preference"]), 2)
        self.assertEqual(again.data["name"], "Maya")

    def test_a_preference_already_held_as_a_memory_is_not_adopted_twice(self):
        self.path.write_text(json.dumps({
            "preferences": {"tone": "terse"},
            "profile_memories": [{"id": "m1", "key": "preference:x", "category": "preference",
                                  "value": "tone = terse", "confidence": 1.0,
                                  "confirmed": True, "pinned": False, "source": "chat",
                                  "created": "2026-01-01", "updated": "2026-01-01"}],
        }), encoding="utf-8")
        memory = MemoryStore(self.path)
        self.assertEqual(len([item for item in memory.profile_memories()
                              if item["category"] == "preference"]), 1)

    def test_the_graph_no_longer_has_two_places_to_disagree(self):
        memory = MemoryStore(self.path)
        memory.set_preference("tone", "terse")
        nodes, edges = build_mind_graph(memory.data, [], [])
        labels = [node.label for node in nodes]
        self.assertEqual(sum("terse" in label for label in labels), 1)
        pairs = {(edge.source, edge.target) for edge in edges}
        node_id = next(node.node_id for node in nodes if "terse" in node.label)
        self.assertIn(("personal_memory", node_id), pairs)
        self.assertIn(("preferences", node_id), pairs)


class ErrorHierarchyTests(unittest.TestCase):
    def test_everything_aura_refuses_shares_one_root(self):
        for cls in (SandboxViolation, PermissionDenied, PermissionRefused,
                    ProviderError, UnsupportedImage, TaskCancelled):
            self.assertTrue(issubclass(cls, AuraError), cls.__name__)

    def test_the_builtin_bases_are_kept_so_existing_handlers_still_work(self):
        """The root was added beside the builtin bases, not instead of them —
        every `except ValueError` already written keeps catching what it did."""
        self.assertTrue(issubclass(SandboxViolation, ValueError))
        self.assertTrue(issubclass(PermissionRefused, ValueError))
        self.assertTrue(issubclass(PermissionDenied, PermissionError))
        self.assertTrue(issubclass(ProviderError, RuntimeError))
        with self.assertRaises(ValueError):
            raise SandboxViolation("still a ValueError")

    def test_an_unrelated_builtin_is_not_swept_up(self):
        self.assertFalse(issubclass(FileNotFoundError, AuraError))


class SchemaVersionTests(unittest.TestCase):
    def test_a_fresh_database_records_the_current_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "aura.db")
            self.assertEqual(database.schema_version(), len(Database.MIGRATIONS))

    def test_an_older_database_is_migrated_once_and_then_left_alone(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "aura.db"
            Database(path)
            # Pretend this database predates versioning, as the user's real one
            # does. Closed explicitly: sqlite3's context manager commits but
            # leaves the connection open, which on Windows holds the file.
            raw = sqlite3.connect(path)
            try:
                raw.execute("PRAGMA user_version = 0")
                raw.commit()
            finally:
                raw.close()
            applied = []
            original = Database._migrate

            def counting(self, connection):
                applied.append(True)
                return original(self, connection)

            with patch.object(Database, "_migrate", counting):
                Database(path)
                self.assertEqual(Database(path).schema_version(), len(Database.MIGRATIONS))
            self.assertEqual(len(applied), 2)   # called both times, applied only once

    def test_migrations_are_append_only(self):
        """A shipped migration must never be edited or renumbered, so the list
        may only grow; entry 1 is the baseline that existing databases match."""
        self.assertEqual(Database.MIGRATIONS[0], ())


class AutonomyGuardTests(unittest.TestCase):
    """The one piece whose job is to say no, so it is tested exhaustively."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = ConfigStore(root / "config.json")
        self.log = ActionLog(root / "aura.db")
        self.guard = AutonomyGuard(self.config, self.log)

    def tearDown(self):
        self.temp.cleanup()

    def at(self, hour, minute=0):
        return datetime(2026, 8, 16, hour, minute)

    def test_a_window_that_crosses_midnight_is_handled(self):
        """22:00–08:00 is the normal case and the one a naive comparison breaks."""
        self.config.update(quiet_hours_start="22:00", quiet_hours_end="08:00")
        for hour in (22, 23, 0, 3, 7):
            self.assertTrue(self.guard.in_quiet_hours(self.at(hour)), f"{hour}:00 should be quiet")
        for hour in (8, 12, 18, 21):
            self.assertFalse(self.guard.in_quiet_hours(self.at(hour)), f"{hour}:00 should be open")

    def test_a_daytime_window_also_works(self):
        self.config.update(quiet_hours_start="09:00", quiet_hours_end="17:00")
        self.assertTrue(self.guard.in_quiet_hours(self.at(12)))
        self.assertFalse(self.guard.in_quiet_hours(self.at(20)))
        self.assertFalse(self.guard.in_quiet_hours(self.at(3)))

    def test_an_empty_window_means_never_quiet(self):
        self.config.update(quiet_hours_start="00:00", quiet_hours_end="00:00")
        self.assertFalse(self.guard.in_quiet_hours(self.at(3)))

    def test_nonsense_hours_fall_back_instead_of_opening_the_night(self):
        self.config.update(quiet_hours_start="25:99", quiet_hours_end="")
        self.assertTrue(self.guard.in_quiet_hours(self.at(23)))
        self.assertFalse(self.guard.in_quiet_hours(self.at(12)))

    def test_pausing_refuses_everything_and_says_so(self):
        self.config.update(quiet_hours_start="00:00", quiet_hours_end="00:00")
        self.guard.pause("emergency stop")
        verdict = self.guard.may_run(self.at(12))
        self.assertFalse(verdict)
        self.assertIn("paused", verdict.reason)
        self.guard.resume()
        self.assertTrue(self.guard.may_run(self.at(12)))

    def test_the_daily_allowance_is_counted_from_the_durable_log(self):
        self.config.update(quiet_hours_start="00:00", quiet_hours_end="00:00",
                           autonomy_daily_runs=3)
        for index in range(3):
            self.guard.note_run(f"check {index}")
        verdict = self.guard.may_run(self.at(12))
        self.assertFalse(verdict)
        self.assertIn("limit of 3", verdict.reason)
        # A restart must not hand back a fresh allowance.
        restarted = AutonomyGuard(self.config, ActionLog(Path(self.temp.name) / "aura.db"))
        self.assertFalse(restarted.may_run(self.at(12)))

    def test_a_zero_cap_means_no_background_work_at_all(self):
        self.config.update(quiet_hours_start="00:00", quiet_hours_end="00:00",
                           autonomy_daily_runs=0)
        self.assertFalse(self.guard.may_run(self.at(12)))

    def test_budgets_are_clamped_however_they_are_configured(self):
        self.config.update(autonomy_run_seconds=99999, autonomy_daily_runs=99999)
        self.assertEqual(self.guard.run_seconds(), AutonomyGuard.HARD_RUN_SECONDS)
        self.assertEqual(self.guard.daily_cap(), AutonomyGuard.HARD_DAILY_CAP)
        self.config.update(autonomy_run_seconds=1)
        self.assertEqual(self.guard.run_seconds(), 10)

    def test_a_refusal_always_explains_itself(self):
        """A background run that silently does not happen is worse than one that
        says why."""
        for setup in ({"autonomy_paused": True},
                      {"autonomy_daily_runs": 0},
                      {"quiet_hours_start": "00:00", "quiet_hours_end": "23:59"}):
            self.config.update(autonomy_paused=False, autonomy_daily_runs=12,
                               quiet_hours_start="22:00", quiet_hours_end="08:00")
            self.config.update(**setup)
            verdict = self.guard.may_run(self.at(12))
            self.assertFalse(verdict, setup)
            self.assertTrue(verdict.reason.strip(), setup)

    def test_it_can_say_when_the_window_reopens(self):
        self.config.update(quiet_hours_start="22:00", quiet_hours_end="08:00")
        opening = self.guard.next_opening(self.at(23, 30))
        self.assertEqual((opening.hour, opening.minute), (8, 0))
        self.assertEqual(opening.day, 17)                    # the following morning
        self.assertIsNone(self.guard.next_opening(self.at(12)))


class ProposalTests(unittest.TestCase):
    """Nothing changes on its own. A proposal waits to be seen, then runs in
    the foreground like any other request."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        agent.config.update(quiet_hours_start="00:00", quiet_hours_end="00:00",
                            seeded_checks=list(checks.DEFAULT_CHECKS))
        self.bridge = AuraWebBridge(agent=agent, speech=SpeechOutput(enabled=False))
        self.bridge.scheduler.stop()
        self.agent = agent
        self.agent.sandbox.write_file(
            "page.html", '<html><head><title>t</title></head><body>'
                         '<img src="gone.png" alt="x"></body></html>')

    def tearDown(self):
        self.bridge.shutdown()
        self.temp.cleanup()

    def run_check(self, check="broken_links"):
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        self.agent.db.add_scheduled("check", check, every_minutes=60, next_run=past)
        return self.bridge.scheduler.tick()

    def test_a_fixable_finding_produces_a_proposal_and_changes_nothing(self):
        before = self.agent.sandbox.read_file("page.html")
        self.run_check()
        pending = self.bridge.list_proposals()["proposals"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["source"], "broken_links")
        self.assertIn("gone.png", pending[0]["request"])
        # The point of the whole phase: nothing was touched.
        self.assertEqual(self.agent.sandbox.read_file("page.html"), before)
        self.assertEqual(self.agent.sandbox.list_files(), ["page.html"])

    def test_the_proposal_is_offered_in_the_conversation(self):
        self.run_check()
        said = self.agent.memory.data["conversation"][-1]["text"]
        self.assertIn("While you were away", said)
        self.assertIn("I can fix that if you want", said)

    def test_a_repeating_check_does_not_stack_the_same_proposal(self):
        self.run_check()
        self.run_check()
        self.assertEqual(len(self.bridge.list_proposals()["proposals"]), 1)

    def test_approving_runs_it_as_an_ordinary_foreground_request(self):
        """Not a special execution path: it goes through submit, so every gate,
        approval dialog and snapshot applies as usual."""
        self.run_check()
        proposal = self.bridge.list_proposals()["proposals"][0]
        with patch.object(self.bridge, "submit", return_value={"ok": True}) as submit:
            result = self.bridge.approve_proposal(proposal["id"])
        self.assertTrue(result["ok"])
        submit.assert_called_once_with(proposal["request"])
        self.assertEqual(self.bridge.list_proposals()["proposals"], [])
        self.assertEqual(self.agent.db.proposal(proposal["id"])["status"], "approved")

    def test_a_refused_submit_leaves_the_proposal_waiting(self):
        self.run_check()
        proposal = self.bridge.list_proposals()["proposals"][0]
        with patch.object(self.bridge, "submit",
                          return_value={"ok": False, "error": "Aura is already working."}):
            result = self.bridge.approve_proposal(proposal["id"])
        self.assertFalse(result["ok"])
        self.assertEqual(len(self.bridge.list_proposals()["proposals"]), 1)

    def test_dismissing_removes_it_without_doing_anything(self):
        self.run_check()
        proposal = self.bridge.list_proposals()["proposals"][0]
        self.assertTrue(self.bridge.dismiss_proposal(proposal["id"])["ok"])
        self.assertEqual(self.bridge.list_proposals()["proposals"], [])
        self.assertEqual(self.agent.db.proposal(proposal["id"])["status"], "dismissed")
        self.assertEqual(self.agent.sandbox.list_files(), ["page.html"])

    def test_a_decided_proposal_cannot_be_decided_again(self):
        self.run_check()
        proposal = self.bridge.list_proposals()["proposals"][0]
        self.bridge.dismiss_proposal(proposal["id"])
        self.assertFalse(self.bridge.approve_proposal(proposal["id"])["ok"])
        self.assertFalse(self.bridge.dismiss_proposal(proposal["id"])["ok"])

    def test_a_finding_without_a_fix_proposes_nothing(self):
        """A repeated failure is worth knowing about; what to do about it is
        judgement Aura does not have."""
        for _ in range(3):
            self.agent.log.record("request", "error", error="LM Studio timed out")
        self.run_check("recent_failures")
        self.assertEqual(self.bridge.list_proposals()["proposals"], [])
        self.assertIn("failed 3 times", self.agent.memory.data["conversation"][-1]["text"])

    def test_no_tool_lets_the_model_approve_its_own_proposal(self):
        offered = {item["function"]["name"] for item in self.agent.tool_definitions()}
        for forbidden in ("approve_proposal", "dismiss_proposal", "list_proposals"):
            self.assertNotIn(forbidden, offered)


class RecurringCheckTests(unittest.TestCase):
    """Read-only, deterministic, and quiet unless there is something to say."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        agent.config.update(quiet_hours_start="00:00", quiet_hours_end="00:00",
                            seeded_checks=list(checks.DEFAULT_CHECKS))
        self.bridge = AuraWebBridge(agent=agent, speech=SpeechOutput(enabled=False))
        self.bridge.scheduler.stop()
        self.agent = agent

    def tearDown(self):
        self.bridge.shutdown()
        self.temp.cleanup()

    def schedule(self, check="validate_workspace", every=60):
        return self.agent._execute_tool(
            ToolCall("1", "set_check", {"check": check, "every_minutes": every}), None)

    def run_now(self):
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        self.agent.db._execute("UPDATE scheduled_tasks SET next_run = ?", (past,))
        return self.bridge.scheduler.tick()

    def test_a_clean_workspace_is_checked_and_says_nothing(self):
        """Silence is the normal outcome; a check that says 'all fine' daily
        teaches its reader to ignore it."""
        self.schedule()
        said_before = len(self.agent.memory.data["conversation"])
        self.run_now()
        self.assertEqual(len(self.agent.memory.data["conversation"]), said_before)
        task = self.agent.db.scheduled_tasks()[0]
        self.assertIn("nothing to report", task["last_outcome"])
        self.assertEqual(task["runs"], 1)

    def test_a_real_problem_is_spoken(self):
        self.agent.sandbox.write_file("broken.json", "{ not json at all")
        self.schedule()
        self.run_now()
        said = self.agent.memory.data["conversation"][-1]["text"]
        self.assertIn("While you were away", said)
        self.assertIn("Validation is failing", said)

    def test_a_broken_link_is_found(self):
        self.agent.sandbox.write_file(
            "page.html", '<html><head><title>t</title></head><body>'
                         '<img src="missing.png" alt="x"></body></html>')
        self.schedule("broken_links")
        self.run_now()
        said = self.agent.memory.data["conversation"][-1]["text"]
        self.assertIn("broken local reference", said)
        self.assertIn("missing.png", said)

    def test_a_repeated_failure_becomes_a_pattern_worth_mentioning(self):
        """One failure is an event; the same one three times is a pattern —
        exactly what the diagnostics export made visible by hand."""
        for _ in range(3):
            self.agent.log.record("request", "error", error="LM Studio timed out")
        self.schedule("recent_failures")
        self.run_now()
        said = self.agent.memory.data["conversation"][-1]["text"]
        self.assertIn("failed 3 times", said)
        self.assertIn("timed out", said)

    def test_one_failure_is_not_a_pattern(self):
        self.agent.log.record("request", "error", error="a one-off blip")
        self.schedule("recent_failures")
        self.run_now()
        self.assertNotIn("blip", str(self.agent.memory.data["conversation"]))

    def test_a_check_repeats_rather_than_retiring(self):
        self.schedule(every=30)
        self.run_now()
        still = self.bridge.list_scheduled()["scheduled"]
        self.assertEqual(len(still), 1)
        self.assertGreater(still[0]["next_run"], datetime.now(timezone.utc).isoformat())

    def test_the_same_check_is_not_scheduled_twice(self):
        self.schedule()
        second = self.schedule()
        self.assertTrue(second["already_scheduled"])
        self.assertEqual(len(self.bridge.list_scheduled()["scheduled"]), 1)

    def test_only_a_known_check_can_be_scheduled(self):
        """The vocabulary is fixed on purpose: free text here would turn a check
        into an arbitrary background agent turn."""
        refused = self.agent._execute_tool(
            ToolCall("1", "set_check", {"check": "rm -rf everything", "every_minutes": 60}), None)
        self.assertFalse(refused["ok"])
        self.assertIn("unknown check", refused["error"])
        self.assertEqual(self.agent.db.scheduled_tasks(), [])

    def test_a_check_never_writes_anything(self):
        self.agent.sandbox.write_file("keep.txt", "untouched")
        before = {path: self.agent.sandbox.read_file(path)
                  for path in self.agent.sandbox.list_files()}
        for name in checks.names():
            self.schedule(name)
        self.run_now()
        after = {path: self.agent.sandbox.read_file(path)
                 for path in self.agent.sandbox.list_files()}
        self.assertEqual(before, after)

    def test_the_user_can_see_and_cancel_what_is_scheduled(self):
        self.schedule()
        listed = self.bridge.list_scheduled()
        self.assertEqual(len(listed["scheduled"]), 1)
        self.assertTrue(any(item["name"] == "validate_workspace"
                            for item in listed["available_checks"]))
        self.assertTrue(self.bridge.cancel_scheduled(listed["scheduled"][0]["id"])["ok"])
        self.assertEqual(self.bridge.list_scheduled()["scheduled"], [])


class ProjectMemoryTests(unittest.TestCase):
    """Improvement 3: project memory, not only user memory.

    The user works on about three projects at a time. Measured with three in
    play before this existed: the project was **never** detected from an
    ordinary request, and one recalled fact in five belonged to a different
    project — matched on a shared word like "footer", which makes it misleading
    rather than merely useless.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        for name in ("promo", "aura_craft", "shop"):
            (Path(self.temp.name) / "workspace" / name).mkdir(parents=True, exist_ok=True)
        self.addCleanup(self.temp.cleanup)

    def remember(self, value, project=None, category="project", pinned=False):
        item = self.agent.memory.learn_fact(category, value, source="test")
        if project or pinned:
            self.agent.memory.update_profile_memory(item["id"], project=project or "",
                                                    pinned=pinned)
        return item

    def recalled(self, request):
        project = self.agent.project_for(request)
        return project, [item["value"] for item
                         in self.agent.memory.relevant_memories(request, 12, project=project)]

    # -------------------------------------------------------------- detection

    def test_a_project_is_recognised_from_ordinary_wording(self):
        """"in the promo folder" was the only shape understood, and nobody
        writes that."""
        for request, expected in (("Add a contact page to the promo site", "promo"),
                                  ("Update the aura_craft landing page", "aura_craft"),
                                  ("fix shop/style.css", "shop"),
                                  ("Tee promo kausta uus leht", "promo")):
            self.assertEqual(self.agent.detect_project(request), expected, request)

    def test_only_folders_that_exist_can_be_projects(self):
        """Otherwise an ordinary noun invents one."""
        self.assertIsNone(self.agent.detect_project("write a shopping list"))
        self.assertIsNone(self.agent.detect_project("promotional wording"))

    def test_the_project_is_remembered_across_the_conversation(self):
        """"Now add a footer to it" is the normal second message."""
        self.assertEqual(self.agent.project_for("Update the aura_craft landing page"),
                         "aura_craft")
        self.assertEqual(self.agent.project_for("Now add a footer to it"), "aura_craft")

    def test_naming_another_project_moves_the_conversation(self):
        self.agent.project_for("Update the aura_craft landing page")
        self.assertEqual(self.agent.project_for("now fix the promo site"), "promo")

    def test_a_new_conversation_does_not_inherit_the_project(self):
        self.agent.project_for("Update the aura_craft landing page")
        self.agent.new_session()
        self.assertIsNone(self.agent.current_project)

    # ---------------------------------------------------------------- scoping

    def test_another_projects_rule_is_kept_out(self):
        self.remember("promo pages must keep the footer contact block", "promo")
        self.remember("aura_craft is built from one shared style.css", "aura_craft")
        project, values = self.recalled("Add a footer to the aura_craft page")
        self.assertEqual(project, "aura_craft")
        self.assertTrue(any("shared style.css" in value for value in values))
        self.assertFalse(any("promo" in value for value in values))

    def test_general_facts_are_always_available(self):
        """A preference is about the user, not about one piece of work."""
        self.remember("prefers HTML interfaces", category="preference")
        self.remember("promo uses a dark palette", "promo")
        _project, values = self.recalled("Update the aura_craft landing page")
        self.assertTrue(any("HTML interfaces" in value for value in values))

    def test_a_pinned_fact_crosses_projects(self):
        """Pinning is the user saying "always", and always outranks scope."""
        self.remember("never use inline styles anywhere", "promo", pinned=True)
        _project, values = self.recalled("Update the aura_craft landing page")
        self.assertTrue(any("inline styles" in value for value in values))

    def test_with_no_project_in_play_nothing_is_hidden(self):
        """Hiding every tagged fact when the project is unknown would recall
        less than before, not more carefully."""
        self.remember("promo uses a dark palette", "promo")
        # No project named and none remembered, so the new rule must not fire.
        self.assertIsNone(self.agent.current_project)
        _project, values = self.recalled("tell me about the palette")
        self.assertTrue(any("dark palette" in value for value in values))

    # --------------------------------------------------------------- learning

    def test_a_fact_learned_while_on_a_project_is_tagged_with_it(self):
        self.agent.project_for("Update the aura_craft landing page")
        learned = self.agent.memory.learn_from_message(
            "I prefer rounded cards on every page", project=self.agent.current_project)
        self.assertTrue(learned)
        self.assertTrue(all(item.get("project") == "aura_craft" for item in learned))


class ConversationUndoTests(unittest.TestCase):
    """Improvement 5: undo everything one conversation changed.

    A single change and a single task could already be rolled back. The link
    that was missing was which conversation a task belonged to — `task_events`
    had no session id, so "this conversation" could not be expressed at all.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        agent.config.update(seeded_checks=list(checks.DEFAULT_CHECKS))
        self.agent = agent
        self.bridge = AuraWebBridge(agent=agent, speech=SpeechOutput(enabled=False))
        self.bridge.scheduler.stop()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.bridge.shutdown)

    def build(self, name, text, session_id):
        """One task in one conversation that writes one file."""
        task_id = self.agent.tasks.start(f"write {name}", session_id=session_id)
        self.agent.sandbox.active_task_id = task_id
        self.agent.sandbox.write_file(name, text)
        return task_id

    def read(self, name):
        return (Path(self.temp.name) / "workspace" / name).read_text(encoding="utf-8")

    # ------------------------------------------------------------- the link

    def test_a_task_records_the_conversation_it_belonged_to(self):
        task_id = self.build("a.txt", "first", "session-one")
        self.assertEqual(self.agent.db.tasks_for_session("session-one"), [task_id])
        self.assertEqual(self.agent.db.tasks_for_session("session-two"), [])

    def test_work_from_before_the_column_existed_is_not_claimed(self):
        """Old rows carry NULL. Matching them by timestamp would be a guess, and
        an action this destructive must never guess."""
        self.agent.tasks.start("older work")       # no session id
        self.assertEqual(self.agent.db.tasks_for_session("session-one"), [])

    # ------------------------------------------------------------- the undo

    def test_it_undoes_every_task_in_that_conversation_only(self):
        self.build("mine.txt", "from this conversation", "session-one")
        self.build("also-mine.txt", "also from it", "session-one")
        self.build("theirs.txt", "from another", "session-two")

        result = self.bridge.undo_session("session-one", confirm=True)
        self.assertTrue(result["ok"])
        self.assertEqual(result["tasks_undone"], 2)
        workspace = Path(self.temp.name) / "workspace"
        self.assertFalse((workspace / "mine.txt").exists())
        self.assertFalse((workspace / "also-mine.txt").exists())
        # The other conversation is untouched, which is the whole point.
        self.assertEqual(self.read("theirs.txt"), "from another")

    def test_the_newest_change_is_undone_first(self):
        """Oldest-first would restore an early backup and then let a later one
        overwrite it, leaving a state that never existed."""
        first = self.agent.tasks.start("write once", session_id="session-one")
        self.agent.sandbox.active_task_id = first
        self.agent.sandbox.write_file("page.txt", "version one")
        second = self.agent.tasks.start("write twice", session_id="session-one")
        self.agent.sandbox.active_task_id = second
        self.agent.sandbox.write_file("page.txt", "version two")

        self.bridge.undo_session("session-one", confirm=True)
        workspace = Path(self.temp.name) / "workspace"
        self.assertFalse((workspace / "page.txt").exists())

    # ------------------------------------------------------- it asks first

    def test_it_shows_what_it_would_touch_before_doing_anything(self):
        """"Are you sure" answers nothing; the list of files does."""
        self.build("mine.txt", "content", "session-one")
        preview = self.bridge.undo_session("session-one")
        self.assertTrue(preview["confirm_needed"])
        self.assertIn("mine.txt", preview["paths"])
        # Nothing happened yet.
        self.assertEqual(self.read("mine.txt"), "content")

    def test_a_conversation_with_nothing_to_undo_says_so_plainly(self):
        answer = self.bridge.undo_session("never-existed")
        self.assertFalse(answer["ok"])
        self.assertIn("before Aura recorded", answer["error"])

    def test_an_empty_id_is_refused(self):
        self.assertFalse(self.bridge.undo_session("")["ok"])

    # ------------------------------------------------- partial is reported

    def test_one_task_with_nothing_left_does_not_abandon_the_rest(self):
        """Stopping halfway without saying so would be the worst of both."""
        done = self.build("first.txt", "one", "session-one")
        self.build("second.txt", "two", "session-one")
        self.agent.sandbox.rollback_task(done)     # already undone by hand

        result = self.bridge.undo_session("session-one", confirm=True)
        self.assertEqual(result["tasks_undone"], 1)
        self.assertEqual(result["tasks_skipped"], 1)
        self.assertFalse((Path(self.temp.name) / "workspace" / "second.txt").exists())

    # ------------------------------------------------------ it is recoverable

    def test_what_it_removes_is_itself_recoverable(self):
        self.build("mine.txt", "content", "session-one")
        self.bridge.undo_session("session-one", confirm=True)
        trash = list((Path(self.temp.name) / "workspace" / ".aura-trash").glob("*mine.txt"))
        self.assertTrue(trash, "the current version should be kept in the trash")

    # ------------------------------------------------------- only the user

    def test_the_model_cannot_undo_a_whole_conversation(self):
        """`rollback_task` is a tool for the task in hand. A whole conversation
        is a different size of action, and nothing the model says reaches it."""
        names = {item["function"]["name"] for item in self.agent.tool_definitions()}
        self.assertNotIn("undo_session", names)
        self.assertNotIn("rollback_session", names)


class ProgressWindowTests(unittest.TestCase):
    """A second window whose only job is answering "is she still going".

    It rides the same event stream as the main page, so what is tested here is
    that it is served, that it cannot run its own script inline, and that the
    stream it needs gives every reader an independent cursor.
    """

    def setUp(self):
        self.web = Path(__file__).parents[1] / "aura" / "web"

    def test_the_window_is_served_and_shipped(self):
        from aura import http_app
        self.assertTrue((self.web / "progress.html").is_file())
        self.assertTrue((self.web / "progress.js").is_file())
        source = (Path(__file__).parents[1] / "aura" / "http_app.py").read_text(encoding="utf-8")
        self.assertIn('"/progress"', source)
        self.assertIn('"/progress.js"', source)
        # A missing file must fail the startup check rather than serve a blank
        # window that looks like Aura having nothing to say.
        self.assertIn('"progress.html", "progress.js"', source)

    def test_it_carries_no_inline_script(self):
        """The page's own Content-Security-Policy is `script-src 'self'`, which
        blocks an inline script outright — measured, as a window that never
        woke up at all."""
        page = (self.web / "progress.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>", page)
        self.assertIn('<script src="/progress.js">', page)

    def test_polls_cannot_overlap_and_double_count(self):
        """A slow request let the next tick start before the cursor moved, so
        both handled the same events — measured as one reply logged twice, and
        worst exactly when the machine is busy."""
        script = (self.web / "progress.js").read_text(encoding="utf-8")
        self.assertIn("if (polling) return;", script)
        self.assertIn("seq <= cursor", script)

    def test_every_reader_gets_its_own_cursor(self):
        """Opening the window must not steal events from the main page."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        agent = AuraAgent(Path(temp.name) / "workspace", provider=MockProvider())
        bridge = AuraWebBridge(agent=agent, speech=SpeechOutput(enabled=False))
        self.addCleanup(bridge.shutdown)
        bridge._push("state", value="working")
        first = bridge.poll_events(0, 50)
        self.assertTrue(first)
        # A second reader starting from the same place sees the same events.
        self.assertEqual([e["_seq"] for e in bridge.poll_events(0, 50)],
                         [e["_seq"] for e in first])


class SilentRetryTests(unittest.TestCase):
    """A retry after silence has to be a different question, not a louder one."""

    def test_the_retry_takes_the_tools_away(self):
        """A model given no tools cannot answer with a tool call, and plain text
        is what the empty-response gate is asking for."""
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            provider.complete = unittest.mock.Mock(
                side_effect=lambda *args, **kwargs: ProviderReply("", []))
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            agent.handle("Create a file called plan.txt in my workspace")
            first, second = provider.complete.call_args_list[:2]
            self.assertTrue(first[0][1], "the first round should offer tools")
            self.assertEqual(second[0][1], [], "the retry should offer none")

    def test_the_tools_come_back_afterwards(self):
        """One round only. A tool-less round is a nudge, not a new mode."""
        replies = [ProviderReply("", []), ProviderReply("", []), ProviderReply("done", [])]
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            provider.complete = unittest.mock.Mock(
                side_effect=lambda *args, **kwargs: replies.pop(0) if replies
                else ProviderReply("done", []))
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            agent.handle("Create a file called plan.txt in my workspace")
            calls = provider.complete.call_args_list
            self.assertEqual(calls[1][0][1], [])
            if len(calls) > 2:
                self.assertTrue(calls[2][0][1], "tools should be offered again")


class ReasoningCarriedTests(unittest.TestCase):
    """Roughly three quarters of this model's output is private reasoning, and
    all of it used to be discarded — so after a tool call it saw its own last
    turn as a bare function call with no memory of why it made it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.addCleanup(self.agent.db.close)

    def test_the_newest_turn_keeps_its_thinking(self):
        messages = []
        assistant = {"role": "assistant", "content": None}
        self.agent._carry_reasoning(messages, assistant, ProviderReply(
            "", [], reasoning="The file is missing, so I will create it."))
        self.assertEqual(assistant["reasoning_content"],
                         "The file is missing, so I will create it.")

    def test_only_the_newest_turn_keeps_it(self):
        """Carrying every round's reasoning would grow the prompt by three
        quarters each time — the very thing compaction exists to fight."""
        older = {"role": "assistant", "content": None, "reasoning_content": "old thought"}
        messages = [{"role": "user", "content": "hi"}, older]
        assistant = {"role": "assistant", "content": None}
        self.agent._carry_reasoning(messages, assistant, ProviderReply(
            "", [], reasoning="new thought"))
        self.assertNotIn("reasoning_content", older)
        self.assertEqual(assistant["reasoning_content"], "new thought")

    def test_long_thinking_keeps_its_ending(self):
        """A chain of thought ends with the decision it reached."""
        reasoning = "x" * 9000 + " therefore I will read the file."
        assistant = {"role": "assistant", "content": None}
        self.agent._carry_reasoning([], assistant, ProviderReply("", [], reasoning=reasoning))
        carried = assistant["reasoning_content"]
        self.assertLessEqual(len(carried), self.agent.REASONING_CARRIED + 1)
        self.assertTrue(carried.endswith("therefore I will read the file."))

    def test_promoted_thinking_is_not_sent_twice(self):
        """When the thinking became the answer, handing it back as reasoning as
        well would say the same thing twice."""
        assistant = {"role": "assistant", "content": "the answer"}
        self.agent._carry_reasoning([], assistant, ProviderReply("the answer", []))
        self.assertNotIn("reasoning_content", assistant)

    def test_it_can_be_switched_off(self):
        self.agent.config.update(send_reasoning_back=False)
        assistant = {"role": "assistant", "content": None}
        self.agent._carry_reasoning([], assistant, ProviderReply("", [], reasoning="thought"))
        self.assertNotIn("reasoning_content", assistant)


class MeasuredIsNotInspectedTests(unittest.TestCase):
    """Aura may not say she inspected a file she only measured.

    Watched on 2026-08-18: four `file_info` calls, a model-written table guessing
    what was in each file, and underneath it Aura's own footer reading
    "Final file state inspected". The footer vouched for the guesses.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.addCleanup(self.agent.db.close)

    def test_file_info_does_not_claim_the_contents_were_read(self):
        report = self.agent._format_completion_evidence(
            "Here is what I found.", None, None, [], [], None, None, ["notes.txt"])
        self.assertIn("Size and line count checked, contents not read", report)
        self.assertNotIn("Final file state inspected", report)

    def test_read_file_still_claims_inspection(self):
        report = self.agent._format_completion_evidence(
            "Here is what I found.", None, None, ["notes.txt"], [], None, None, [])
        self.assertIn("Final file state inspected", report)
        self.assertNotIn("contents not read", report)

    def test_a_file_both_measured_and_read_is_reported_once_as_read(self):
        report = self.agent._format_completion_evidence(
            "Done.", None, None, ["notes.txt"], [], None, None, ["notes.txt"])
        self.assertIn("Final file state inspected", report)
        self.assertNotIn("contents not read", report)

    def test_the_two_tool_sets_do_not_overlap(self):
        self.assertEqual(self.agent.CONTENT_TOOLS & self.agent.SHAPE_TOOLS, set())
        self.assertNotIn("file_info", self.agent.CONTENT_TOOLS)
        # Still verification: measuring a file proves a write landed.
        self.assertIn("file_info", self.agent.VERIFICATION_TOOLS)


class UnreadFileNoteTests(unittest.TestCase):
    """The reply that started this: a table of guessed file contents, printed
    under Aura's own "Confirmed evidence" footer."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.addCleanup(self.agent.db.close)

    def gate(self, content, measured=(), read=()):
        turn = TurnState()
        turn.measured_paths = set(measured)
        turn.verified_final_paths = set(read)
        return self.agent._gate_unread_files(turn, ProviderReply(content, []))

    def test_a_measured_file_the_answer_talks_about_is_named(self):
        result = self.gate("notes.txt probably holds the product notes.",
                           measured=["shop/notes.txt"])
        self.assertIn("never opened", result.note)
        self.assertIn("shop/notes.txt", result.note)
        self.assertFalse(result.wants_retry, "this is a note, not another round")

    def test_a_file_that_was_actually_read_is_not_named(self):
        self.assertEqual(self.gate("notes.txt holds the product notes.",
                                   measured=["shop/notes.txt"],
                                   read=["shop/notes.txt"]).note, "")

    def test_a_measured_file_the_answer_never_mentions_is_left_alone(self):
        self.assertEqual(self.gate("All four files are present.",
                                   measured=["shop/notes.txt"]).note, "")

    def test_it_does_not_depend_on_the_language_of_the_answer(self):
        """The first draft of this gate hunted for words like "probably" in two
        languages. Aura already knows what she read; she does not need to guess
        at intent from vocabulary."""
        estonian = self.gate("notes.txt sisaldab tõenäoliselt tootemärkmeid.",
                             measured=["shop/notes.txt"])
        english = self.gate("notes.txt likely contains the product notes.",
                            measured=["shop/notes.txt"])
        self.assertIn("never opened", estonian.note)
        self.assertIn("never opened", english.note)

    def test_an_empty_answer_says_nothing(self):
        self.assertEqual(self.gate("", measured=["a.txt"]).note, "")

    def test_the_gate_runs_inside_a_real_turn(self):
        self.assertIn(AuraAgent._gate_unread_files, AuraAgent.COMPLETION_GATES)


class TurnBudgetTests(unittest.TestCase):
    """A turn spends time, so time is what should bound it.

    Measured across 65 real turns: median 24s, 90th percentile 221s, worst 985s —
    one tool and sixteen minutes of thinking, ended by an HTTP timeout that arrived
    as a failure rather than as an answer.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def agent_with(self, seconds, replies):
        provider = LMStudioProvider(model="local-model")
        provider.complete = unittest.mock.Mock(
            side_effect=lambda *a, **k: replies.pop(0) if replies else ProviderReply("late", []))
        agent = AuraAgent(Path(self.temp.name) / "workspace", provider=provider)
        self.addCleanup(agent.db.close)
        agent.config.update(turn_budget_seconds=seconds)
        return agent, provider

    def test_a_turn_that_runs_long_stops_and_says_where_it_got_to(self):
        """Time passes where time actually passes — inside the model call — so the
        first round and its tool complete, and the turn expires while asking again."""
        clock = [0.0]
        replies = [ProviderReply("", [ToolCall("c1", "list_files", {"path": "."})]),
                   ProviderReply("", [ToolCall("c2", "list_files", {"path": "."})]),
                   ProviderReply("finally", [])]
        costs = iter([5, 25])          # a quick round, then one that overruns

        def generate(*_args, **_kwargs):
            clock[0] += next(costs, 25)
            return replies.pop(0) if replies else ProviderReply("late", [])

        provider = LMStudioProvider(model="local-model")
        provider.complete = unittest.mock.Mock(side_effect=generate)
        agent = AuraAgent(Path(self.temp.name) / "workspace", provider=provider)
        self.addCleanup(agent.db.close)
        agent.config.update(turn_budget_seconds=20)
        with unittest.mock.patch("aura.agent.time.monotonic", side_effect=lambda: clock[0]):
            answer = agent.handle("List the files please")
        self.assertIn("I stopped after", answer)
        self.assertIn("list_files", answer, "it should say what it managed to run")
        self.assertIn("Settings", answer)
        self.assertEqual(provider.complete.call_count, 2,
                         "the third round is never asked for")

    def test_the_clock_is_read_during_a_generation_not_only_between_rounds(self):
        """Caught by the second sweep, in this feature's own first version: the
        budget was checked at the top of each round, so a round starting one second
        inside the limit could then run for seventeen minutes. Live, it did."""
        agent, provider = self.agent_with(30, [])
        agent._turn_deadline = time.monotonic() - 1
        with self.assertRaises(TurnExpired):
            agent._check_cancelled()

    def test_a_turn_with_time_left_is_not_stopped(self):
        agent, _ = self.agent_with(30, [])
        agent._turn_deadline = time.monotonic() + 60
        agent._check_cancelled()   # must not raise

    def test_cancelling_still_wins_over_the_clock(self):
        agent, _ = self.agent_with(30, [])
        agent._turn_deadline = time.monotonic() - 1
        agent.cancel_event.set()
        with self.assertRaises(TaskCancelled):
            agent._check_cancelled()

    def test_a_silent_thinking_model_still_gets_its_clock_read(self):
        """The case that actually bit. A model emitting only private reasoning
        produces no content tokens, so a clock read only in the content callback is
        never read at all during exactly the turns that run long."""
        source = (Path(__file__).parents[1] / "aura" / "agent.py").read_text(encoding="utf-8")
        self.assertIn("on_reasoning=watch", source)
        watch = source[source.index("def watch(count: int)"):]
        self.assertIn("_check_cancelled", watch[:300])

    def test_the_wait_is_described_without_rounding_it_up(self):
        """Live on 2026-08-18 a 90-second budget reported "I stopped after 2
        minutes". Overstating the wait in the one message whose job is honesty."""
        agent, _ = self.agent_with(90, [])
        self.assertIn("90 seconds", agent._format_out_of_time(TurnState(), 90))
        self.assertIn("5 minutes", agent._format_out_of_time(TurnState(), 300))
        self.assertIn("45 seconds", agent._format_out_of_time(TurnState(), 45))

    def test_zero_means_no_limit(self):
        agent, provider = self.agent_with(0, [ProviderReply("done", [])])
        with unittest.mock.patch("aura.agent.time.monotonic", side_effect=[0, 10**9, 10**9]):
            answer = agent.handle("Say hello")
        self.assertNotIn("I stopped after", answer)

    def test_a_quick_turn_is_untouched(self):
        agent, provider = self.agent_with(300, [ProviderReply("done", [])])
        self.assertNotIn("I stopped after", agent.handle("Say hello"))

    def test_the_limit_is_reachable_from_settings(self):
        """The out-of-time message tells Mat to change it in Settings, so Settings
        has to carry it — a promise in a message is a feature."""
        html = (Path(__file__).parents[1] / "aura" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="settingTurnBudget"', html)
        # and the browser must both read it and send it back
        js = (Path(__file__).parents[1] / "aura" / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn('$("#settingTurnBudget").value = settings.turn_budget_seconds', js)
        self.assertIn("turn_budget_seconds: $(\"#settingTurnBudget\").value", js)
        agent, _ = self.agent_with(300, [])
        self.assertIn("turn_budget_seconds", agent.config.data)


class ReadingBudgetTests(unittest.TestCase):
    """Every tool caps itself; nothing capped their sum, and each later round pays
    for the whole of it again."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.addCleanup(self.agent.db.close)
        self.agent.config.update(turn_tool_characters=1000)

    def test_early_results_arrive_whole(self):
        turn = TurnState()
        payload = "x" * 800
        self.assertEqual(self.agent._within_reading_budget(payload, turn, "read_file"), payload)

    def test_results_past_the_line_are_shortened_not_dropped(self):
        turn = TurnState()
        self.agent._within_reading_budget("x" * 900, turn, "read_file")
        second = self.agent._within_reading_budget("y" * 5000, turn, "read_many_files")
        self.assertLess(len(second), 5000)
        self.assertIn("this result was shortened", second)
        self.assertTrue(second.startswith("y"), "what survives is the start of the result")

    def test_zero_means_no_limit(self):
        self.agent.config.update(turn_tool_characters=0)
        turn = TurnState()
        payload = "x" * 500000
        self.assertEqual(self.agent._within_reading_budget(payload, turn, "read_file"), payload)

    def test_the_budget_is_spent_across_the_whole_turn(self):
        turn = TurnState()
        for _ in range(4):
            self.agent._within_reading_budget("z" * 400, turn, "read_file")
        self.assertEqual(turn.tool_characters, 1600)


class WorkspaceBridgeTests(unittest.TestCase):
    """`workspace_bridge.py` is 284 lines and 21 methods, and the sweep found it
    the thinnest-covered module in the project — while being the one that moves,
    renames and deletes Mat's actual files."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.root = agent.sandbox.root
        self.bridge = AuraWebBridge(agent=agent, speech=SpeechOutput(enabled=False))

    def tearDown(self):
        self.bridge.shutdown()
        self.temp.cleanup()

    def dropped(self, name, raw=b"hello"):
        return {"name": name, "content": base64.b64encode(raw).decode("ascii")}

    # ---- importing, which is the path a drag-and-drop takes
    def test_a_dropped_file_lands_in_the_workspace(self):
        result = self.bridge.import_files([self.dropped("notes.txt", b"line\n")])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["files"], ["notes.txt"])
        self.assertEqual((self.root / "notes.txt").read_bytes(), b"line\n")

    def test_a_second_file_of_the_same_name_does_not_overwrite_the_first(self):
        self.bridge.import_files([self.dropped("notes.txt", b"first")])
        second = self.bridge.import_files([self.dropped("notes.txt", b"second")])
        self.assertEqual(second["files"], ["notes (2).txt"])
        self.assertEqual((self.root / "notes.txt").read_bytes(), b"first")

    def test_a_path_in_the_dropped_name_cannot_escape_the_workspace(self):
        result = self.bridge.import_files([self.dropped("../../escaped.txt")])
        # Only the final component is used, so it lands inside and nowhere else.
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["files"], ["escaped.txt"])
        self.assertTrue((self.root / "escaped.txt").exists())

    def test_a_name_that_is_only_dots_is_refused(self):
        self.assertFalse(self.bridge.import_files([self.dropped("..")])["ok"])

    def test_content_that_is_not_base64_is_refused_by_name(self):
        result = self.bridge.import_files([{"name": "bad.txt", "content": "not base64!!"}])
        self.assertFalse(result["ok"])
        self.assertIn("bad.txt", result["error"])

    def test_more_than_five_files_at_once_is_refused(self):
        many = [self.dropped(f"f{n}.txt") for n in range(6)]
        self.assertFalse(self.bridge.import_files(many)["ok"])

    def test_an_oversized_file_is_refused(self):
        big = b"x" * (self.bridge.MAX_IMPORT_FILE_BYTES + 1)
        result = self.bridge.import_files([self.dropped("big.bin", big)])
        self.assertFalse(result["ok"])
        self.assertFalse((self.root / "big.bin").exists())

    def test_nothing_is_written_when_one_file_in_the_batch_is_bad(self):
        """Decoding happens for the whole batch before anything is written, so a
        rejected file cannot leave half a drop on disk."""
        result = self.bridge.import_files(
            [self.dropped("good.txt"), {"name": "bad.txt", "content": "!!!"}])
        self.assertFalse(result["ok"])
        self.assertFalse((self.root / "good.txt").exists())

    # ---- creating, renaming, moving, deleting
    def test_create_rename_move_and_delete_round_trip(self):
        self.assertTrue(self.bridge.create_workspace_folder("stuff")["ok"])
        self.assertTrue(self.bridge.create_workspace_file("stuff/a.txt", "hi")["ok"])
        self.assertEqual((self.root / "stuff" / "a.txt").read_text(encoding="utf-8"), "hi")

        self.assertTrue(self.bridge.rename_workspace_item("stuff/a.txt", "b.txt")["ok"])
        self.assertTrue((self.root / "stuff" / "b.txt").exists())

        self.assertTrue(self.bridge.copy_workspace_item("stuff/b.txt", "c.txt")["ok"])
        self.assertTrue((self.root / "c.txt").exists())

        self.assertTrue(self.bridge.delete_workspace_item("c.txt")["ok"])
        self.assertFalse((self.root / "c.txt").exists())
        # Deleted, not gone: it is recoverable from the trash.
        self.assertTrue(self.bridge.list_trash()["items"])

    def test_a_deleted_file_can_be_restored(self):
        self.bridge.create_workspace_file("gone.txt", "text")
        self.bridge.delete_workspace_item("gone.txt")
        name = self.bridge.list_trash()["items"][0]["trash_name"]
        self.assertTrue(self.bridge.restore_workspace_item(name)["ok"])
        self.assertTrue((self.root / "gone.txt").exists())

    def test_a_path_typed_into_the_rename_box_cannot_walk_out(self):
        """Only the final component of a new name is used, so `../there.txt`
        renames the file inside the workspace instead of escaping it. Checked at
        the filesystem, not by trusting the return value."""
        self.bridge.create_workspace_file("here.txt", "x")
        result = self.bridge.rename_workspace_item("here.txt", "../there.txt")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["path"], "there.txt")
        self.assertTrue((self.root / "there.txt").exists())
        self.assertNotIn("there.txt", [p.name for p in self.root.parent.iterdir()])

    def test_a_snapshot_reports_what_is_really_there(self):
        self.bridge.create_workspace_file("one.txt", "1")
        self.bridge.create_workspace_folder("sub")
        snapshot = self.bridge.workspace_snapshot()
        self.assertTrue(snapshot["ok"], snapshot)
        names = {item["path"] for item in snapshot["files"]}
        self.assertIn("one.txt", names)

    def test_previewing_a_missing_file_fails_without_raising(self):
        result = self.bridge.preview_workspace_file("not-there.txt")
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("error"))


class VoiceBridgeTests(unittest.TestCase):
    """`voice_bridge.py` had exactly one mention in the whole suite. It owns the
    microphone, so its refusals matter more than its successes."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.bridge = AuraWebBridge(agent=agent, speech=SpeechOutput(enabled=False))

    def tearDown(self):
        self.bridge.shutdown()
        self.temp.cleanup()

    def test_listing_voices_and_microphones_never_raises(self):
        """These run on whatever hardware Mat happens to have, including none."""
        voices = self.bridge.get_voices()
        mics = self.bridge.get_microphones()
        self.assertIn("ok", voices)
        self.assertIn("ok", mics)
        for result in (voices, mics):
            if result.get("ok"):
                self.assertIsInstance(result.get("voices", result.get("devices", [])), list)

    def test_stopping_when_nothing_is_listening_is_harmless(self):
        result = self.bridge.stop_voice()
        self.assertIn("ok", result)

    def test_voice_does_not_start_while_a_turn_is_running(self):
        """The microphone and the model share Aura's attention; starting one
        during the other is how two answers arrive for one question."""
        self.bridge._busy = True
        try:
            result = self.bridge.start_voice()
            self.assertFalse(result.get("ok"), result)
        finally:
            self.bridge._busy = False


class SilenceCaptureTests(unittest.TestCase):
    """A silence is a reproducible failure that nothing was keeping.

    Measured against the real model: the same payload returns the same tokens every
    time, at 0.4 and at 1.2, three runs each and ~10 seconds apiece — regenerating,
    not cached. So a silence is not bad luck; it is one specific prompt that reliably
    produces a single token. Irreproducible only because the prompt was thrown away.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.addCleanup(self.agent.db.close)
        self.folder = self.agent.sandbox.meta / "silences"

    def test_the_prompt_that_went_quiet_is_written_down(self):
        self.agent.provider.last_payload = {"model": "m", "messages": [
            {"role": "user", "content": "kirjuta midagi"}]}
        name = self.agent._capture_silence()
        self.assertTrue(name.endswith(".json"))
        saved = json.loads((self.folder / name).read_text(encoding="utf-8"))
        self.assertEqual(saved["messages"][0]["content"], "kirjuta midagi")

    def test_nothing_to_capture_is_not_an_error(self):
        self.assertEqual(self.agent._capture_silence(), "")
        self.assertFalse(self.folder.exists())

    def test_only_the_newest_are_kept(self):
        for index in range(self.agent.SILENCES_KEPT + 4):
            self.agent.provider.last_payload = {"n": index}
            self.folder.mkdir(exist_ok=True)
            (self.folder / f"2020010{index:02d}T000000.json").write_text("{}", encoding="utf-8")
        self.agent._capture_silence()
        self.assertLessEqual(len(list(self.folder.glob("*.json"))),
                             self.agent.SILENCES_KEPT)

    def test_a_broken_diagnostic_never_breaks_the_turn(self):
        """Worse than no diagnostic is one that fails the thing it diagnoses."""
        self.agent.provider.last_payload = {"model": "m"}
        with unittest.mock.patch.object(type(self.agent.sandbox.meta), "mkdir",
                                        side_effect=OSError("disk full")):
            self.assertEqual(self.agent._capture_silence(), "")

    def test_the_provider_keeps_what_it_actually_sent(self):
        """After merging and tuning, not before — the first version of the probe
        that investigated this posted the unmerged messages and LM Studio refused
        them outright."""
        source = (Path(__file__).parents[1] / "aura" / "provider.py").read_text(encoding="utf-8")
        tune = source.index("payload = self._tune_payload(payload)")
        self.assertIn("self.last_payload = payload", source[tune:tune + 400])


class BareDemonstrativeTests(unittest.TestCase):
    """A long sentence containing "this" is not a follow-up.

    Live on 2026-08-18: clicking Ask Aura on the node `Mat` sent "…Use this local
    context only: Aura remembers the user's name as Mat." The bare "this" made it a
    follow-up, so it inherited "Kirjuta väga pikk essee…" from three turns earlier,
    `kirjuta` maps to `write`, and a question about a name opened a build-approval
    card listing markdown headings as files to create.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.addCleanup(self.agent.db.close)
        self.agent.memory.remember_message("user", "Kirjuta väga pikk essee Eesti ajaloost")

    def test_the_question_that_caused_this_no_longer_inherits_a_build(self):
        asked = ("Tell me what you know about \u201cMat\u201d from Aura Mind. "
                 "Use this local context only: Aura remembers the user\u2019s name as Mat.")
        routed = self.agent._routing_request(asked)
        self.assertEqual(routed, asked, "a long sentence must not pull in prior intent")
        self.assertFalse(self.agent._requires_mutation(routed))

    def test_a_short_demonstrative_still_refers_back(self):
        """"Fix this" is a whole request pointing at the last one, and must keep
        working — the fix must not cost the feature."""
        routed = self.agent._routing_request("fix this")
        self.assertIn("Kirjuta", routed)
        self.assertTrue(self.agent._requires_mutation(routed))

    def test_phrases_refer_back_at_any_length(self):
        long_follow_up = ("I meant the thing we were doing before, please carry on "
                          "from where you left off and finish the whole essay properly")
        self.assertIn("Kirjuta", self.agent._routing_request(long_follow_up))

    def test_a_long_sentence_about_something_else_is_left_alone(self):
        asked = ("Explain what this function does in the file I am looking at right "
                 "now, because the naming is confusing me quite a lot today")
        self.assertEqual(self.agent._routing_request(asked), asked)


class PlanKindTests(unittest.TestCase):
    """A plan is either files about to be written or steps about to be taken, and
    the card said "the files she would create" for both."""

    def test_the_plan_carries_which_kind_it_is(self):
        source = (Path(__file__).parents[1] / "aura" / "agent.py").read_text(encoding="utf-8")
        section = source[source.index("def _plan_files"):]
        section = section[:section.index("return plan")]
        self.assertIn('kind = "FILES" if naming_files else "STEPS"', section)
        self.assertIn('approve(["PLAN", plan, kind])', section)

    def test_the_browser_says_the_right_thing_for_steps(self):
        js = (Path(__file__).parents[1] / "aura" / "web" / "app.js").read_text(encoding="utf-8")
        section = js[js.index("function showApproval"):]
        section = section[:section.index("openModal(")]
        self.assertIn('=== "STEPS"', section)
        self.assertIn("This is the approach she would take", section)
        # and the kind marker is not printed as if it were one of the lines
        self.assertIn("steps ? -1 : undefined", section)




class SelfKnowledgeTests(unittest.TestCase):
    """Asked what could be improved about herself, Aura estimated her response time
    at "~100-500ms". Her measured median is 24 seconds and her worst was 985. She
    also asked for a tool she had used that same day, and listed three background
    projects that do not exist. She had no way to look; now she does."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # A real provider, because these are about what reaches the prompt.
        self.agent = AuraAgent(Path(self.temp.name) / "workspace",
                               provider=LMStudioProvider(model="local-model"))
        self.addCleanup(self.agent.db.close)

    def test_she_can_tell_the_time(self):
        """Her one accurate self-criticism: 3,755 characters of system prompt and
        no clock anywhere in it."""
        messages = self.agent.provider.start_messages(
            "mis kell on?", self.agent._context("mis kell on?"))
        text = " ".join(str(m.get("content") or "") for m in messages)
        self.assertIn("current local time", text)
        self.assertIn(str(datetime.now().year), text)

    def test_a_question_about_herself_reaches_the_mirror(self):
        """Caught live, from a captured silence: asked how fast she was, Aura was
        offered list_files, read_file, validate_project and run_command — and
        answered with a table headed "Real Data Only" full of invented numbers,
        having run nothing. A tool she cannot be given is a tool she does not have."""
        for asked in ("Kui kiire sa tegelikult oled ja mis sul ebaõnnestunud on?",
                      "What could be improved about you?",
                      "how fast are you really?",
                      "what went wrong lately?"):
            names = {item["function"]["name"]
                     for item in self.agent.select_tool_definitions(asked)}
            self.assertIn("how_i_have_been_running", names, asked)

    def test_ordinary_work_is_not_offered_the_mirror(self):
        names = {item["function"]["name"]
                 for item in self.agent.select_tool_definitions("Tee shop kausta avaleht")}
        self.assertNotIn("how_i_have_been_running", names)

    def test_the_report_is_measured_not_estimated(self):
        self.agent.memory.remember_message("user", "üks")
        self.agent.memory.remember_message("assistant", "kaks")
        report = self.agent._tool_how_i_have_been_running(
            "how_i_have_been_running", {"days": 7}, None, None)
        # A finished sentence, not parts. Given the parts and told not to combine
        # them, the model published a trend, a cause and 70/86 = 81% — all three
        # things the note forbade. `checks.py` settled this next door: a sentence
        # has nothing left in it to analyse.
        self.assertIn("finding", report)
        self.assertNotIn("seconds_per_turn", report)
        self.assertNotIn("failures", report)
        self.assertIn("7 days", report["finding"])

    def test_it_never_calls_the_model(self):
        """A measurement that could hallucinate is not a measurement — the rule
        checks.py already states."""
        self.agent.provider.complete = unittest.mock.Mock(
            side_effect=AssertionError("this must not reach the model"))
        self.agent._tool_how_i_have_been_running(
            "how_i_have_been_running", {"days": 3}, None, None)

    def test_the_window_is_bounded(self):
        for asked, expected in ((0, 1), (999, 30), (None, 7)):
            report = self.agent._tool_how_i_have_been_running(
                "how_i_have_been_running", {"days": asked}, None, None)
            self.assertIn(f"last {expected} days", report["finding"])

    def turn_lasting(self, seconds: int) -> None:
        """One answered turn in the log — the report reads the database, not the
        conversation held in memory."""
        started = datetime.now(timezone.utc)
        self.agent.db.add_message("s", "user", "küsimus", started.isoformat())
        self.agent.db.add_message(
            "s", "assistant", "vastus",
            (started + timedelta(seconds=seconds)).isoformat())

    def test_the_finding_says_what_the_log_cannot_show(self):
        """Named inside the finding, where it cannot be skipped, rather than in a
        note beside it — which is where it was, and was ignored."""
        self.turn_lasting(30)
        finding = self.agent._tool_how_i_have_been_running(
            "how_i_have_been_running", {"days": 7}, None, None)["finding"]
        self.assertIn("not why", finding)
        self.assertIn("getting better", finding)
        self.assertIn("30 seconds", finding)

    def test_an_empty_window_says_so_plainly(self):
        finding = self.agent._tool_how_i_have_been_running(
            "how_i_have_been_running", {"days": 7}, None, None)["finding"]
        self.assertIn("Nothing was measured", finding)




class ToolMarkupTests(unittest.TestCase):
    """A tool call written as text is not an answer, and must never become one.

    Live on 2026-08-18: asked how fast she was, Aura ran the right tool and then
    finished the turn with `<tool_call><function=undo_last_change></tool_call>` as
    her visible reply. Nothing was executed — the log shows the last real undo was
    two days earlier — but Mat was shown raw markup, and the tool it reached for
    was destructive and unrequested.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.addCleanup(self.agent.db.close)

    def test_the_exact_reply_that_caused_this(self):
        markup = "<tool_call>\n<function=undo_last_change>\n</function>\n</tool_call>"
        self.assertTrue(AuraAgent._is_tool_markup(markup))

    def test_other_markup_shapes(self):
        for text in ("[TOOL_CALL] read_file", "<function=list_files>", "<tool_call>"):
            self.assertTrue(AuraAgent._is_tool_markup(text), text)

    def test_talking_about_tool_calls_is_not_making_one(self):
        """Mat and Aura discuss her own internals constantly; a gate that fired on
        the word would be unusable."""
        prose = ("I could write <tool_call> style markup here, but LM Studio parses "
                 "the structured field instead, so it only reaches you as content "
                 "when the chat template misfires. That is what the gate catches.")
        self.assertFalse(AuraAgent._is_tool_markup(prose))
        self.assertFalse(AuraAgent._is_tool_markup("Sinu shop kaustas on neli faili."))
        self.assertFalse(AuraAgent._is_tool_markup(""))

    def test_it_is_refused_rather_than_obeyed(self):
        """The safe response is to refuse. A call arriving as prose has bypassed
        every structured path, and this one was `undo_last_change`."""
        turn = TurnState()
        verdict = self.agent._gate_tool_markup(
            turn, ProviderReply("<tool_call>\n<function=undo_last_change>\n</tool_call>", []))
        self.assertTrue(verdict.wants_retry)
        self.assertTrue(verdict.drop_tools)
        self.assertTrue(turn.emitted_tool_markup)
        self.assertNotIn("undo", verdict.instruction.casefold())

    def test_a_real_answer_passes_untouched(self):
        turn = TurnState()
        self.assertEqual(
            self.agent._gate_tool_markup(turn, ProviderReply("Neli faili.", [])).instruction, "")
        self.assertFalse(turn.emitted_tool_markup)

    def test_the_gate_runs_before_anything_reasons_about_the_reply(self):
        gates = list(AuraAgent.COMPLETION_GATES)
        self.assertEqual(gates[0], AuraAgent._gate_tool_markup)


class NothingToAnswerTests(unittest.TestCase):
    """Never ask the model to reply to Aura's own reply.

    The last of the three causes behind the silences, and the only one that was
    never the model's fault. A captured payload ending in an assistant turn
    reproduced the silence every time — `completion_tokens: 1`, `finish_reason:
    stop` — and appending one turn to it produced 879 tokens of answer instead.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.addCleanup(self.agent.db.close)

    def test_a_conversation_ending_in_aura_gets_something_to_answer(self):
        messages = [{"role": "system", "content": "You are Aura."},
                    {"role": "user", "content": "How big is the shop folder?"},
                    {"role": "assistant", "content": "Four files, about 11 KB."}]
        self.assertTrue(self.agent._ensure_something_to_answer(messages))
        self.assertEqual(messages[-1]["role"], "user")
        self.assertEqual(messages[-1]["content"], self.agent.NOTHING_TO_ANSWER)

    def test_it_must_be_a_user_turn(self):
        """A system message is refused by the chat template anywhere but the front,
        and `merge_system_messages` would hoist it there — leaving the conversation
        still ending in Aura's own reply, which is the entire problem."""
        messages = [{"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"}]
        self.agent._ensure_something_to_answer(messages)
        self.assertNotEqual(messages[-1]["role"], "system")

    def test_a_normal_conversation_is_untouched(self):
        for tail in ({"role": "user", "content": "and now?"},
                     {"role": "tool", "tool_call_id": "t1", "content": "{}"}):
            messages = [{"role": "user", "content": "hi"}, tail]
            self.assertFalse(self.agent._ensure_something_to_answer(messages))
            self.assertEqual(messages[-1], tail)

    def test_an_assistant_turn_that_called_a_tool_is_left_alone(self):
        """That conversation is not finished — the tool results come next."""
        messages = [{"role": "user", "content": "read it"},
                    {"role": "assistant", "content": None, "tool_calls": [
                        {"id": "t1", "type": "function",
                         "function": {"name": "read_file", "arguments": "{}"}}]}]
        self.assertFalse(self.agent._ensure_something_to_answer(messages))

    def test_an_empty_conversation_is_not_touched(self):
        messages = []
        self.assertFalse(self.agent._ensure_something_to_answer(messages))

    def test_the_guard_runs_before_every_request(self):
        source = (Path(__file__).parents[1] / "aura" / "agent.py").read_text(encoding="utf-8")
        section = source[source.index("for round_index in range(round_limit)"):]
        section = section[:section.index("response = self.provider.complete")]
        self.assertIn("_ensure_something_to_answer", section)


class ValidationChecksAssetsTests(unittest.TestCase):
    """A page whose stylesheet does not load is broken, whatever the parser says.

    Mat opened a finished landing page and got Times New Roman on white. Aura had
    reported "validation passed, 0 issues" and "projekt on valmis kasutamiseks" —
    truthfully, by the only measure she was using. Every link was absolute
    (`/css/style.css`) against files living in `shop/css/`, so the markup parsed
    and resolved to nothing.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.addCleanup(self.agent.db.close)
        self.agent.sandbox.write_file("shop/css/style.css", "body { color: red; }")

    def page(self, href):
        self.agent.sandbox.write_file(
            "shop/index.html",
            f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<title>Shop</title><link rel="stylesheet" href="{href}"></head>'
            f'<body><h1>Shop</h1></body></html>')

    def test_an_absolute_link_that_resolves_nowhere_fails_validation(self):
        self.page("/css/style.css")
        result = self.agent._validate_project("shop")
        self.assertFalse(result["valid"], "syntactically fine, and it cannot load its stylesheet")
        self.assertTrue(result.get("broken_references"))
        self.assertIn("style.css", " ".join(i["error"] for i in result["issues"]))

    def test_the_error_says_what_to_change(self):
        self.page("/css/style.css")
        errors = " ".join(i["error"] for i in self.agent._validate_project("shop")["issues"])
        self.assertIn("leading '/'", errors)

    def test_the_same_page_with_a_relative_link_passes(self):
        self.page("css/style.css")
        result = self.agent._validate_project("shop")
        self.assertTrue(result["valid"], result.get("issues"))
        self.assertNotIn("broken_references", result)

    def test_a_fetch_path_a_browser_cannot_resolve_fails_validation(self):
        """The shop validated clean while rendering a header and nothing else.
        Every tag resolved; the failure was a string literal inside the bundle:
        fetch('/data/products.json') against a file at shop/data/products.json."""
        self.agent.sandbox.write_file("shop/data/products.json", '[{"id": 1}]')
        self.agent.sandbox.write_file(
            "shop/js/main.bundle.js", "fetch('/data/products.json').then(r => r.json());")
        self.page("css/style.css")
        result = self.agent._validate_project("shop")
        self.assertFalse(result["valid"])
        self.assertIn("products.json", " ".join(i["error"] for i in result["issues"]))

    def test_a_relative_fetch_is_left_alone(self):
        """A relative path in a script may be joined from variables or resolved
        against a base this cannot see. A checker that cries wolf is not read."""
        self.agent.sandbox.write_file("shop/data/products.json", '[{"id": 1}]')
        self.agent.sandbox.write_file(
            "shop/js/main.bundle.js", "fetch('./data/products.json').then(r => r.json());")
        self.page("css/style.css")
        self.assertTrue(self.agent._validate_project("shop")["valid"])

    def test_urls_and_routes_are_not_mistaken_for_files(self):
        from aura.validation import _absolute_paths_in_script as paths
        self.assertEqual(paths("fetch('https://api.example.com/v1/x.json')"), [])
        self.assertEqual(paths("fetch('//cdn.example.com/lib.js')"), [])
        self.assertEqual(paths("const route = '/shop'"), [])
        self.assertEqual(paths("img.src = '/images/mug.jpg'"), ["/images/mug.jpg"])

    def test_syntax_errors_are_still_reported(self):
        """The asset check adds to validation, it does not replace it."""
        self.agent.sandbox.write_file("shop/index.html", "<html><body><div></body></html>")
        self.assertFalse(self.agent._validate_project("shop")["valid"])


class AskedToEditCreatedInsteadTests(unittest.TestCase):
    """"Change the heading in X" presupposes X. When it is not there, saying so is
    the answer — not quietly making the presupposition true.

    Found by a behavioural sweep, not by reading: asked to change the heading in a
    file that did not exist, Aura created it and reported "demo/missing.html exists.
    The heading is correctly set to 'Gone'. Final state confirmed." All true, and it
    never says the file was invented. Harmless for a page; for "move my 3pm meeting"
    it is a second meeting while the first stays put.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.addCleanup(self.agent.db.close)

    def test_the_request_that_caused_this_is_recognised_as_an_edit(self):
        self.assertTrue(self.agent._is_edit_request(
            "In demo/missing.html change the heading to 'Gone'"))

    def test_asking_for_something_new_is_not_an_edit(self):
        self.assertFalse(self.agent._is_edit_request("Create demo/new.html with a heading"))

    def test_both_verbs_together_claim_nothing(self):
        """Ambiguous rather than mistaken, so it stays quiet."""
        self.assertFalse(self.agent._is_edit_request(
            "Create a config file and then change its title"))

    def test_it_works_in_estonian(self):
        self.assertTrue(self.agent._is_edit_request("Muuda demo/page.html pealkiri"))

    def test_the_evidence_says_the_file_was_created_not_changed(self):
        report = self.agent._format_completion_evidence(
            "The heading is correctly set to 'Gone'.", None, None, [], [], None, None,
            [], ["demo/missing.html"])
        self.assertIn("Did not exist and was created, rather than changed", report)
        self.assertIn("demo/missing.html", report)

    def test_an_ordinary_edit_says_nothing_extra(self):
        report = self.agent._format_completion_evidence(
            "Done.", None, None, ["demo/page.html"], [], None, None, [], [])
        self.assertNotIn("Did not exist", report)

    def test_the_turn_records_what_was_missing_before_any_tool_ran(self):
        """Afterwards a file that was edited and one that was invented look the
        same on disk, so the fact has to be captured up front."""
        turn = TurnState(missing_at_start={"demo/missing.html"}, edit_request=True)
        self.assertEqual(turn.missing_at_start, {"demo/missing.html"})
        self.assertTrue(turn.edit_request)


class ToolIsNotACommandTests(unittest.TestCase):
    """Saying it in the prompt three times did not work.

    Section 6 says workspace capabilities are tools not commands; section 11 says
    twice more not to conclude a tool is missing from a failed command. Aura then
    asked to run `validate_project .` as a command, twice, and told Mat the tool was
    unavailable while holding it. So the refusal moved into the code.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.addCleanup(self.agent.db.close)

    def run_command(self, command):
        return self.agent._tool_run_command("run_command", {"command": command}, None, None)

    def test_a_tool_name_is_refused(self):
        result = self.run_command(["validate_project", "."])
        self.assertFalse(result["ok"])
        self.assertTrue(result["refused"])

    def test_the_refusal_says_the_tool_exists(self):
        """The failure being prevented is not the command failing — it is what she
        concluded from it. "Cannot find the executable" reads as "no such
        capability", so the refusal has to contradict that directly."""
        error = self.run_command(["validate_project", "."])["error"]
        self.assertIn("is one of your tools", error)
        self.assertIn("exists", error)

    def test_a_windows_executable_name_is_caught_too(self):
        self.assertTrue(self.run_command(["validate_project.exe", "."])["refused"])

    def test_a_full_path_to_a_tool_name_is_caught(self):
        self.assertTrue(self.run_command(["/usr/bin/read_file", "x"])["refused"])

    def test_a_real_program_is_left_alone(self):
        """The guard must not become a reason she cannot run anything."""
        result = self.run_command(["python", "-c", "print(1)"])
        self.assertNotIn("refused", result)

    def test_it_is_refused_before_approval(self):
        """Mat is never asked to authorise a mistake."""
        def approve(_command):
            raise AssertionError("approval must not be reached for a tool name")
        self.agent._tool_run_command(
            "run_command", {"command": ["validate_project", "."]}, approve, None)


class RefusedEditFeedbackTests(unittest.TestCase):
    """A refused edit should make the next attempt land.

    Nineteen edit failures in the log since the silence fixes, all match-count
    mismatches. The refusals were correct — that check is what stops a blind
    replace hitting the wrong line — but "found 3" said a guess was wrong and
    nothing about what to guess instead, so the next attempt guessed again.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.addCleanup(self.agent.db.close)
        self.agent.sandbox.write_file(
            "page.html",
            '<div class="row">one</div>\n<div class="row">two</div>\n'
            '<div class="row">three</div>\n')

    def replace(self, old, new="x", expected=1):
        return self.agent._tool_replace_in_file(
            "replace_in_file",
            {"path": "page.html", "old_text": old, "new_text": new,
             "expected_count": expected}, None, None)

    def test_too_many_matches_shows_where_they_are(self):
        with self.assertRaises(ValueError) as caught:
            self.replace('<div class="row">')
        message = str(caught.exception)
        self.assertIn("found 3 matches, not 1", message)
        self.assertIn("line 1:", message)
        self.assertIn("line 3:", message)
        self.assertIn("expected_count to 3", message)

    def test_no_match_shows_the_near_misses(self):
        with self.assertRaises(ValueError) as caught:
            self.replace('<div class="rows">one</div>')
        message = str(caught.exception)
        self.assertIn("not in the file", message)
        self.assertIn("line 1:", message)

    def test_no_match_and_nothing_close_says_so_plainly(self):
        with self.assertRaises(ValueError) as caught:
            self.replace("zzzzzzzz")
        message = str(caught.exception)
        self.assertIn("not in the file at all", message)
        self.assertNotIn("line 1:", message)

    def test_a_correct_edit_still_works(self):
        """The feedback must not become a reason nothing can be edited."""
        result = self.replace('<div class="row">one</div>', "<p>one</p>")
        self.assertEqual(result["replacements"], 1)
        self.assertIn("<p>one</p>", self.agent.sandbox.read_file("page.html"))

    def test_expected_count_still_lets_several_change_at_once(self):
        result = self.replace('<div class="row">', "<section>", expected=3)
        self.assertEqual(result["replacements"], 3)

    def test_apply_edits_says_which_edit_failed(self):
        with self.assertRaises(ValueError) as caught:
            self.agent._tool_apply_edits("apply_edits", {"path": "page.html", "edits": [
                {"old_text": '<div class="row">one</div>', "new_text": "<p>1</p>"},
                {"old_text": "nowhere", "new_text": "x"},
            ]}, None, None)
        self.assertIn("edit 2", str(caught.exception))

    def test_a_failed_batch_changes_nothing(self):
        """One recovery snapshot, all or nothing — a half-applied batch is how a
        file ends up in a state neither side expected."""
        before = self.agent.sandbox.read_file("page.html")
        with self.assertRaises(ValueError):
            self.agent._tool_apply_edits("apply_edits", {"path": "page.html", "edits": [
                {"old_text": '<div class="row">one</div>', "new_text": "<p>1</p>"},
                {"old_text": "nowhere", "new_text": "x"},
            ]}, None, None)
        self.assertEqual(self.agent.sandbox.read_file("page.html"), before)


class GateAuditTests(unittest.TestCase):
    """The nine gates as a set, rather than one at a time.

    They grew over weeks and two arrived in a single afternoon. This pins the
    properties that only make sense when you look at all of them together.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.addCleanup(self.agent.db.close)

    def test_a_note_does_not_survive_being_fixed(self):
        """Found by auditing them together. Notes accumulated across rounds, so a
        doubt raised in round one reached the final answer after round two had
        resolved it — Aura calling a file "never opened" after reading it, in the
        one section whose whole purpose is to be true."""
        source = (Path(__file__).parents[1] / "aura" / "agent.py").read_text(encoding="utf-8")
        section = source[source.index("retry = None"):]
        section = section[:section.index("for gate in self.COMPLETION_GATES")]
        self.assertIn("unconfirmed.clear()", section)

    def test_the_two_no_answer_gates_share_one_budget(self):
        """`tool_markup` and `empty_response` are the same failure wearing two
        faces — the model produced nothing usable. One allowance between them,
        or a markup retry followed by a silence retry costs two rounds to learn
        the same thing twice."""
        turn = TurnState()
        first = self.agent._gate_tool_markup(
            turn, ProviderReply("<tool_call>\n<function=read_file>\n</tool_call>", []))
        self.assertTrue(first.wants_retry)
        self.assertEqual(turn.empty_retries_used, 1)
        second = self.agent._gate_empty_response(turn, ProviderReply("", []))
        self.assertFalse(second.wants_retry, "the allowance was already spent")

    def test_note_only_gates_never_ask_for_a_round(self):
        """A note costs nothing; a retry costs a minute. The last two gates judge
        the answer rather than demanding another."""
        for gate in (AuraAgent._gate_unread_files, AuraAgent._gate_plan):
            source = inspect.getsource(gate)
            self.assertNotIn("instruction=", source, gate.__name__)

    def test_every_gate_can_pass(self):
        """A gate that cannot pass is a gate that ends every turn in a retry."""
        turn = TurnState()
        response = ProviderReply("All done.", [])
        for gate in AuraAgent.COMPLETION_GATES:
            verdict = gate(self.agent, turn, response)
            self.assertFalse(verdict.wants_retry, f"{gate.__name__} refuses a clean turn")

    def test_the_shared_budget_is_finite(self):
        turn = TurnState(retries_left=self.agent.MAX_COMPLETION_RETRIES)
        spent = 0
        while turn.spend_retry():
            spent += 1
        self.assertEqual(spent, self.agent.MAX_COMPLETION_RETRIES)


class DurablePlanStepsTests(unittest.TestCase):
    """The project becomes the unit, not the turn.

    Every frustration this week traced to one fact: nothing survived a turn.
    `_plan_steps` was a list on the bridge, wiped at both ends of `_work`, so
    "continue building it" gave Aura nothing to continue from and each message
    re-derived the plan from scratch.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace",
                               provider=LMStudioProvider(model="local-model"))
        self.addCleanup(self.agent.db.close)
        self.agent.sandbox.write_file("shop/index.html", "<html></html>")
        self.agent.current_project = "shop"

    def plan(self, *steps):
        return self.agent._tool_record_plan_steps(
            "record_plan_steps", {"steps": list(steps)}, None, None)

    def finish(self, step, status="done", evidence="validated"):
        return self.agent._tool_update_plan_step(
            "update_plan_step",
            {"step": step, "status": status, "evidence": evidence}, None, None)

    def test_a_plan_survives_the_turn_that_made_it(self):
        self.plan("Fix asset paths", "Add product grid")
        # A different agent, the same database — which is what a later turn is.
        again = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.addCleanup(again.db.close)
        self.assertEqual([s["text"] for s in again.db.plan_steps("shop")],
                         ["Fix asset paths", "Add product grid"])

    def test_progress_is_recorded_against_the_step(self):
        self.plan("Fix asset paths", "Add product grid")
        result = self.finish("Fix asset paths")
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["next"], "Add product grid")
        self.assertEqual(result["remaining"], 1)

    def test_re_planning_does_not_forget_finished_work(self):
        """A re-planned project forgetting what is done is exactly the amnesia
        this table exists to end."""
        self.plan("Fix asset paths", "Add product grid")
        self.finish("Fix asset paths")
        result = self.plan("Fix asset paths", "Add product grid", "Wire the cart")
        self.assertEqual(result["already_done"], 1)
        self.assertEqual(result["next"], "Add product grid")

    def test_a_step_can_be_named_loosely(self):
        """A model that must quote a sentence verbatim to record progress will
        stop recording progress — the lesson the edit tools already taught."""
        self.plan("Fix the absolute asset paths in every page", "Add product grid")
        self.assertEqual(self.finish("fix the absolute")["status"], "done")

    def test_an_unmatched_step_says_what_the_plan_is(self):
        self.plan("Fix asset paths")
        with self.assertRaises(ValueError) as caught:
            self.finish("something else entirely")
        self.assertIn("Fix asset paths", str(caught.exception))

    def test_a_started_step_comes_back_first(self):
        """A turn that stopped mid-step is the case this exists for."""
        self.plan("One", "Two", "Three")
        self.finish("Two", status="doing", evidence="halfway")
        self.assertEqual(self.agent.db.next_plan_step("shop")["text"], "Two")

    def test_the_next_step_reaches_the_prompt(self):
        self.plan("Fix asset paths", "Add product grid")
        self.finish("Fix asset paths")
        messages = self.agent.provider.start_messages(
            "continue", self.agent._context("continue"))
        text = " ".join(str(m.get("content") or "") for m in messages)
        self.assertIn("1 of 2 steps done", text)
        self.assertIn("The next step is: Add product grid", text)

    def test_a_finished_plan_says_so_rather_than_inviting_invention(self):
        self.plan("Only step")
        self.finish("Only step")
        messages = self.agent.provider.start_messages(
            "continue", self.agent._context("continue"))
        text = " ".join(str(m.get("content") or "") for m in messages)
        self.assertIn("Every step is finished", text)
        self.assertNotIn("The next step is", text)

    def test_a_plan_needs_a_project(self):
        self.agent.current_project = None
        with self.assertRaises(ValueError):
            self.plan("something")


class PlanProgressFromEvidenceTests(unittest.TestCase):
    """Aura moves the plan from what the gates proved, not from the model
    remembering to say so.

    The same argument `_gate_plan` already settled: its first version asked the
    model to write PLAN.md and half the runs did. This week added four more data
    points — she would not record a lesson on a message that said "remember that",
    and ignored three separate rules about tools versus commands.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace",
                               provider=LMStudioProvider(model="local-model"))
        self.addCleanup(self.agent.db.close)
        self.agent.sandbox.write_file("shop/index.html", "<html></html>")
        self.agent.current_project = "shop"
        self.agent._tool_record_plan_steps(
            "record_plan_steps", {"steps": ["Fix asset paths", "Add product grid"]},
            None, None)

    def turn(self, **fields):
        turn = TurnState(successful_tools=fields.pop("successful_tools", 2))
        for key, value in fields.items():
            setattr(turn, key, value)
        return turn

    def run_gate(self, turn):
        return self.agent._gate_plan_progress(turn, ProviderReply("done", []))

    def steps(self):
        return {s["text"]: s for s in self.agent.db.plan_steps("shop")}

    def test_validation_marks_the_step_done_with_its_evidence(self):
        self.run_gate(self.turn(workspace_mutation=True, validation_succeeded=True,
                                validation_scope="shop",
                                validation_evidence={"files_seen": 7}))
        step = self.steps()["Fix asset paths"]
        self.assertEqual(step["status"], "done")
        self.assertIn("validate_project passed", step["evidence"])
        self.assertIn("7 files", step["evidence"])

    def test_writing_without_proof_only_marks_it_started(self):
        """Marking a step finished that is not finished would be exactly the
        dishonesty everything else here exists to prevent."""
        self.run_gate(self.turn(workspace_mutation=True))
        self.assertEqual(self.steps()["Fix asset paths"]["status"], "doing")

    def test_reading_the_named_files_back_counts_as_proof(self):
        self.run_gate(self.turn(workspace_mutation=True,
                                expected_paths=["shop/index.html"],
                                verified_final_paths={"shop/index.html"}))
        step = self.steps()["Fix asset paths"]
        self.assertEqual(step["status"], "done")
        self.assertIn("read back after writing", step["evidence"])

    def test_a_missing_artifact_stops_it_counting(self):
        self.run_gate(self.turn(workspace_mutation=True,
                                verified_final_paths={"shop/index.html"},
                                missing_artifacts=["shop/missing.html"]))
        self.assertNotEqual(self.steps()["Fix asset paths"]["status"], "done")

    def test_an_explicit_call_wins(self):
        """She looked at the plan and decided; that beats anything inferred."""
        self.agent._tool_update_plan_step(
            "update_plan_step",
            {"step": "Add product grid", "status": "done", "evidence": "by hand"},
            None, None)
        self.run_gate(self.turn(workspace_mutation=True, validation_succeeded=True,
                                validation_scope="shop", tools_run=["update_plan_step"]))
        # Her step keeps her words. The gate is free to advance the others —
        # skipping the whole turn is what left the plan behind reality.
        self.assertEqual(self.steps()["Add product grid"]["evidence"], "by hand")
        self.assertEqual(self.steps()["Add product grid"]["status"], "done")

    def test_a_turn_that_did_nothing_moves_nothing(self):
        self.run_gate(self.turn(successful_tools=0, validation_succeeded=True,
                                validation_scope="shop"))
        self.assertEqual(self.steps()["Fix asset paths"]["status"], "todo")

    def test_it_never_asks_for_another_round(self):
        """Bookkeeping about finished work must not cost the user a minute."""
        verdict = self.run_gate(self.turn(workspace_mutation=True))
        self.assertFalse(verdict.wants_retry)
        self.assertEqual(verdict.note, "")

    def test_a_project_with_no_plan_is_left_alone(self):
        self.agent.current_project = "promo"
        self.run_gate(self.turn(workspace_mutation=True, validation_succeeded=True,
                                validation_scope="promo"))
        self.assertEqual(self.agent.db.plan_steps("promo"), [])

    def test_bookkeeping_cannot_break_a_finished_turn(self):
        self.agent.db.next_plan_step = lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError("database gone"))
        self.assertFalse(self.run_gate(self.turn(workspace_mutation=True)).wants_retry)


class EveryToolIsReachableTests(unittest.TestCase):
    """A tool the router never offers does not exist.

    Twice in one day: `how_i_have_been_running` was built and not routed, so Aura
    invented her own performance figures while holding the tool that measures
    them. Then `record_plan_steps` was built and not routed, so a request to plan
    and build offered six read-only tools — she searched thirty-eight times and
    described work she had no way to perform.

    Forgetting is silent, which is what makes it worth a test rather than care.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.addCleanup(self.agent.db.close)

    #: Tools reached by a path other than keyword routing, with the reason.
    NOT_ROUTED = {
        # Aura runs the whole turn; the model never asks for one of these by name.
        "conversation": "not a model-facing tool",
    }

    def test_every_registered_tool_can_be_offered_by_some_request(self):
        from aura import toolkit
        reachable = set()
        # The vocabulary of the router, drawn from its own rules rather than
        # guessed, so a new rule is covered the moment it is written.
        source = (Path(__file__).parents[1] / "aura" / "routing.py").read_text(encoding="utf-8")
        words = set(re.findall(r'includes\(([^)]*)\)', source))
        phrases = set(re.findall(r'"([^"]{3,40})"', " ".join(words)))
        for phrase in sorted(phrases):
            # Two shapes, because some rules need a verb *and* a noun together —
            # `create_folder` wants both, and a harness that only ever says one
            # word would report a routed tool as unreachable.
            for asked in (f"please {phrase} it", f"create a {phrase} now"):
                for item in self.agent.select_tool_definitions(asked):
                    reachable.add(item["function"]["name"])
        registered = set(toolkit.REGISTRY) - set(self.NOT_ROUTED)
        missing = sorted(registered - reachable)
        self.assertEqual(missing, [],
                         f"unreachable by any routing keyword: {missing}")

    def test_the_request_that_exposed_this_now_offers_what_it_needs(self):
        asked = ("In the shop project: record a plan of three steps for finishing "
                 "the product catalogue, then do the first one.")
        names = {item["function"]["name"]
                 for item in self.agent.select_tool_definitions(asked)}
        for needed in ("record_plan_steps", "update_plan_step",
                       "create_file", "write_file", "validate_project"):
            self.assertIn(needed, names, asked)


class PlanStaysLevelWithTheWorkspaceTests(unittest.TestCase):
    """The record answers to the files, not to what was said about them.

    Live: Aura recorded step one herself, then did the work for steps two and
    three — six writes and two validations — and recorded neither, so the plan
    ended up behind reality. The gate had skipped the whole turn because
    `update_plan_step` appeared in it.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace",
                               provider=LMStudioProvider(model="local-model"))
        self.addCleanup(self.agent.db.close)
        self.agent.current_project = "shop"
        self.agent._tool_record_plan_steps("record_plan_steps", {"steps": [
            "Add sample products to shop/data/products.json",
            "Update shop/js/main.bundle.js to render the list",
            "Create shop/index.html with layout"]}, None, None)

    def run_gate(self, **fields):
        turn = TurnState(successful_tools=fields.pop("successful_tools", 6),
                         workspace_mutation=fields.pop("workspace_mutation", True))
        for key, value in fields.items():
            setattr(turn, key, value)
        self.agent._turn_state = turn
        self.agent._gate_plan_progress(turn, ProviderReply("done", []))
        return self.step

    def step(self, fragment):
        found = [s for s in self.agent.db.plan_steps("shop")
                 if fragment.casefold() in s["text"].casefold()]
        self.assertEqual(len(found), 1, f"{fragment!r} matched {len(found)} steps")
        return found[0]

    def test_a_step_whose_file_exists_is_marked_done(self):
        self.agent.sandbox.write_file("shop/data/products.json", "[]")
        self.run_gate()
        step = self.step("Add sample products")
        self.assertEqual(step["status"], "done")
        self.assertIn("products.json", step["evidence"])

    def test_a_step_whose_file_is_missing_stays_open(self):
        self.agent.sandbox.write_file("shop/data/products.json", "[]")
        self.run_gate()
        self.assertNotEqual(self.step("main.bundle.js")["status"], "done")

    def test_later_steps_are_not_skipped_because_an_earlier_one_lags(self):
        """The failure this was written for: work done, only the first step
        recorded, the rest left claiming to be outstanding."""
        self.agent.sandbox.write_file("shop/index.html", "<html></html>")
        self.run_gate()
        self.assertEqual(self.step("Create shop/index.html")["status"], "done")

    def test_a_step_she_recorded_is_never_overwritten(self):
        """She looked at the plan and decided; that beats anything inferred."""
        self.agent.sandbox.write_file("shop/data/products.json", "[]")
        turn = TurnState(successful_tools=2, workspace_mutation=True)
        self.agent._turn_state = turn
        self.agent._tool_update_plan_step("update_plan_step", {
            "step": "Add sample products", "status": "blocked",
            "evidence": "waiting on real product data"}, None, None)
        self.agent._gate_plan_progress(turn, ProviderReply("done", []))
        step = self.step("Add sample products")
        self.assertEqual(step["status"], "blocked")
        self.assertIn("waiting on real", step["evidence"])

    def test_a_step_naming_no_file_still_uses_turn_evidence(self):
        self.agent._tool_record_plan_steps(
            "record_plan_steps", {"steps": ["Wire up the cart logic"]}, None, None)
        self.run_gate(validation_succeeded=True, validation_scope="shop",
                      validation_evidence={"files_seen": 4})
        self.assertEqual(self.step("cart logic")["status"], "done")

    def test_a_step_naming_its_file_from_inside_the_project_is_found(self):
        """Measured on Mat's own plan: two of three steps named their files
        project-relative — "Update js/main.bundle.js" for shop/js/main.bundle.js —
        and would otherwise have read as missing forever."""
        self.agent.sandbox.write_file("shop/js/main.bundle.js", "// code")
        self.assertTrue(self.agent._step_file_exists("shop", "js/main.bundle.js"))
        self.assertTrue(self.agent._step_file_exists("shop", "shop/js/main.bundle.js"))
        self.assertFalse(self.agent._step_file_exists("shop", "js/nowhere.js"))
        self.assertNotEqual(self.run_gate() and self.step("main.bundle.js")["status"], "todo")

    def test_a_url_is_not_a_workspace_file(self):
        self.assertEqual(
            self.agent._paths_named_in("Fetch from https://example.com/api.json"), [])
        self.assertEqual(
            self.agent._paths_named_in("Read shop/data/products.json"),
            ["shop/data/products.json"])

    def test_nothing_moves_when_no_tool_succeeded(self):
        self.agent.sandbox.write_file("shop/index.html", "<html></html>")
        self.run_gate(successful_tools=0)
        self.assertEqual(self.step("Create shop/index.html")["status"], "todo")


class LessonMemoryTests(unittest.TestCase):
    """Not learning — not forgetting.

    Aura made the same `../` path mistake four times in one afternoon, in four
    separate turns, after being corrected each time. Weights never change, so
    working with her cannot make her better at it. A correction is a fact, though,
    and facts already persist; what was missing was somewhere to put one and a
    guarantee it comes back.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace",
                               provider=LMStudioProvider(model="local-model"))
        self.addCleanup(self.agent.db.close)
        self.agent.sandbox.write_file("shop/index.html", "<html></html>")
        self.agent.current_project = "shop"

    def teach(self, lesson="Always use relative asset paths, never a leading slash."):
        return self.agent._tool_remember_lesson(
            "remember_lesson", {"lesson": lesson}, None, None)

    def test_a_lesson_is_kept_against_its_project(self):
        self.teach()
        self.assertEqual(len(self.agent.lessons_for("shop")), 1)
        self.assertIn("relative asset paths", self.agent.lessons_for("shop")[0])

    def test_it_does_not_leak_into_another_project(self):
        self.teach()
        self.assertEqual(self.agent.lessons_for("promo"), [])
        self.assertEqual(self.agent.lessons_for(None), [])

    def test_it_reaches_the_prompt(self):
        self.teach()
        messages = self.agent.provider.start_messages(
            "fix the links", self.agent._context("fix the links"))
        text = " ".join(str(m.get("content") or "") for m in messages)
        self.assertIn("relative asset paths", text)


    def test_the_same_lesson_twice_is_not_stored_twice(self):
        self.teach()
        self.teach()
        self.assertEqual(len(self.agent.lessons_for("shop")), 1)

    def test_the_prompt_asks_her_to_record_corrections(self):
        self.assertIn("remember_lesson", self.agent.provider.SYSTEM_PROMPT)


class RetryCompactionTests(unittest.TestCase):
    """A retry has to ask a smaller question than the one that just failed.

    Measured twice inside a single turn on 2026-08-18: prompt 10,982 then 11,090
    tokens, one token back each time. Aura appended an instruction and asked
    again, making the prompt larger than the one the model had just gone quiet
    on.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.addCleanup(self.agent.db.close)

    def conversation(self, results=4):
        messages = [{"role": "system", "content": "You are Aura."},
                    {"role": "user", "content": "Vaata failid üle"}]
        for n in range(results):
            messages.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": f"t{n}", "type": "function",
                 "function": {"name": "read_file", "arguments": "{}"}}]})
            messages.append({"role": "tool", "tool_call_id": f"t{n}",
                             "content": f"result {n} " + "x" * 3000})
        return messages

    def test_older_results_shrink_and_the_recent_ones_do_not(self):
        messages = self.conversation()
        before = sum(len(str(m.get("content") or "")) for m in messages)
        saved = self.agent._compact_for_retry(messages)
        after = sum(len(str(m.get("content") or "")) for m in messages)
        self.assertGreater(saved, 4000)
        self.assertLess(after, before)
        tools = [m for m in messages if m["role"] == "tool"]
        # The two most recent are what the model is answering from.
        self.assertIn("x" * 3000, tools[-1]["content"])
        self.assertIn("x" * 3000, tools[-2]["content"])
        self.assertNotIn("x" * 3000, tools[0]["content"])
        self.assertIn("trimmed to make room", tools[0]["content"])

    def test_no_tool_message_is_ever_removed(self):
        """Each `tool` message answers a `tool_call` by id. Dropping one leaves
        the conversation malformed in a way the server rejects."""
        messages = self.conversation()
        ids = [m["tool_call_id"] for m in messages if m["role"] == "tool"]
        self.agent._compact_for_retry(messages)
        self.assertEqual([m["tool_call_id"] for m in messages if m["role"] == "tool"], ids)

    def test_nothing_but_tool_results_is_touched(self):
        messages = self.conversation()
        others = [dict(m) for m in messages if m["role"] != "tool"]
        self.agent._compact_for_retry(messages)
        self.assertEqual([m for m in messages if m["role"] != "tool"], others)

    def test_a_short_conversation_is_left_alone(self):
        messages = self.conversation(results=2)
        self.assertEqual(self.agent._compact_for_retry(messages), 0)
        messages = self.conversation(results=4)
        for m in messages:
            if m["role"] == "tool":
                m["content"] = "small"
        self.assertEqual(self.agent._compact_for_retry(messages), 0)

    def test_the_retry_instruction_is_a_user_turn(self):
        """`merge_system_messages` hoists every system message to the front of the
        payload. A retry appended as `system` therefore left the conversation still
        ending on Aura's own reply — a guaranteed silence. Measured on a captured
        payload: as sent, one token back; the same words as a user turn, 354."""
        source = (Path(__file__).parents[1] / "aura" / "agent.py").read_text(encoding="utf-8")
        section = source[source.index("if retry is not None:"):]
        section = section[:section.index("continue")]
        self.assertIn('messages.append({"role": "user"', section)
        self.assertNotIn('messages.append({"role": "system"', section)

    def test_the_retry_path_compacts_before_it_appends(self):
        """The whole point: the instruction is added to a conversation that has
        already been made smaller, not to the one that just failed."""
        source = (Path(__file__).parents[1] / "aura" / "agent.py").read_text(encoding="utf-8")
        section = source[source.index("if retry is not None:"):]
        section = section[:section.index("continue")]
        self.assertLess(section.index("_compact_for_retry"),
                        section.index('messages.append({"role": "user"'))


class ThinkingHeartbeatTests(unittest.TestCase):
    """Private reasoning has to reach the interface as a sign of life.

    Measured on the raw stream: this model emits 226 `reasoning_content` deltas
    to 72 of `content`. Aura streamed only `content`, so for most of a turn
    nothing reached the browser — and the progress window, whose whole job is
    answering "is she stuck", would have said stuck while she was thinking.
    """

    def test_reasoning_is_counted_and_reported_but_never_transcribed(self):
        beats, tokens = [], []
        provider = LMStudioProvider(model="local-model")
        provider._request = lambda *a, **k: {}
        chunks = [{"choices": [{"delta": {"reasoning_content": "thought "}}]}
                  for _ in range(40)]
        chunks.append({"choices": [{"delta": {"content": "Done."},
                                    "finish_reason": "stop"}]})

        def fake_stream(payload, on_token, on_reasoning=None):
            seen = 0
            for chunk in chunks:
                delta = chunk["choices"][0]["delta"]
                if delta.get("reasoning_content"):
                    seen += 1
                    if on_reasoning and seen % 16 == 0:
                        on_reasoning(seen)
                if delta.get("content"):
                    on_token(delta["content"])
            return ProviderReply("Done.", [])

        provider._stream_completion = fake_stream
        provider.complete([{"role": "user", "content": "hi"}],
                          on_token=tokens.append, on_reasoning=beats.append)
        # Throttled: a heartbeat, not one event per token.
        self.assertEqual(beats, [16, 32])
        # And the thinking itself never enters the transcript.
        self.assertEqual(tokens, ["Done."])

    def test_the_bridge_reports_thinking_and_clears_it_afterwards(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        agent = AuraAgent(Path(temp.name) / "workspace", provider=MockProvider())
        bridge = AuraWebBridge(agent=agent, speech=SpeechOutput(enabled=False))
        self.addCleanup(bridge.shutdown)
        pushed = []
        bridge._push = lambda name, **fields: pushed.append((name, fields))
        bridge._work("tere")
        self.assertIsNone(agent.on_thinking)   # not left dangling for the next turn

    def test_both_windows_show_it(self):
        web = Path(__file__).parents[1] / "aura" / "web"
        self.assertIn('case "thinking":', (web / "app.js").read_text(encoding="utf-8"))
        self.assertIn('case "thinking":', (web / "progress.js").read_text(encoding="utf-8"))


class CommandClaimTests(unittest.TestCase):
    """A reply may not describe a command's output when none ran.

    Measured on both models on 2026-08-18: asked to build a file and run a
    command, with the command refused, the reply reported its output anyway —
    qwen3.5-9b 2 runs in 3, omnicoder 1 in 3. The system prompt already forbade
    it, which is the point: a prompt is not an enforcement mechanism.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.addCleanup(self.agent.db.close)

    def verdict(self, said, executed=0, retries=1):
        turn = TurnState()
        turn.commands_executed = executed
        turn.retries_left = retries
        response = unittest.mock.Mock()
        response.content = said
        return self.agent._gate_command_claim(turn, response)

    def test_claiming_output_with_nothing_run_asks_for_a_rewrite(self):
        verdict = self.verdict("Käivitasin käsu ja see väljastas: tere")
        self.assertTrue(verdict.wants_retry)
        self.assertIn("was not run", verdict.instruction)

    def test_the_english_wording_is_caught_too(self):
        self.assertTrue(self.verdict("I ran the command and the output was 'hello'").wants_retry)

    def test_the_wording_that_actually_appeared_is_caught(self):
        """Taken verbatim from a live reply, not invented: the sentence
        contradicts itself and the first half is the false part. The original
        phrase list was written by guessing and missed it."""
        said = ("I attempted to run `python -c \"print('tere')\"` which should output "
                "the text **\"tere\"**. The command has been executed and is awaiting "
                "user approval before completion.")
        self.assertTrue(self.verdict(said).wants_retry)

    def test_saying_what_it_would_print_is_not_a_claim(self):
        """Also from a live reply. This one is the model being exactly right,
        and an earlier scorer of mine counted it as a lie because the output
        string appeared in the text."""
        self.assertEqual(self.verdict(
            "Mis see väljastaks (kui luba oleks): tere"), PASS)
        self.assertEqual(self.verdict(
            "Pythoni käsk vajab kasutaja heakskiitu. Kas sa lubad käivitamise?"), PASS)

    def test_a_command_that_really_ran_is_never_questioned(self):
        """No attempt is made to match claims to commands. A gate that guessed
        would produce exactly the false accusations it exists to prevent."""
        self.assertEqual(self.verdict("Käivitasin käsu ja see väljastas: tere",
                                      executed=1), PASS)

    def test_offering_to_run_something_is_not_a_claim(self):
        """Nagging about a proposal would make this a nuisance rather than a
        guard, so the wording is past-tense and result-shaped on purpose."""
        for said in ("Ma võin käivitada käsu python --version, kui soovid.",
                     "You could run the tests to check this.",
                     "Käsu käivitamiseks on vaja sinu kinnitust."):
            self.assertEqual(self.verdict(said), PASS, said)

    def test_with_no_retries_left_it_is_recorded_rather_than_hidden(self):
        verdict = self.verdict("Käivitasin käsu ja see väljastas: tere", retries=0)
        self.assertFalse(verdict.wants_retry)
        self.assertIn("no command was run", verdict.note)

    def test_the_gate_is_actually_reached_through_a_real_turn(self):
        """Every other test here calls the gate directly. `COMPLETION_GATES`
        holds function objects captured at class-definition time, so a gate can
        look present and never be reached — this is the test that would notice."""
        provider = LMStudioProvider(model="local-model")
        provider.complete = unittest.mock.Mock(side_effect=[
            ProviderReply("Käivitasin käsu ja see väljastas: tere", []),
        ] + [ProviderReply("Käsku ei käivitatud, sest sa ei andnud luba.", [])] * 4)
        agent = AuraAgent(Path(self.temp.name) / "second", provider=provider)
        self.addCleanup(agent.db.close)
        answer = agent.handle("Käivita käsk ja ütle mida see väljastas.")
        # More than one call means a gate sent it back. Not pinned to an exact
        # number: other gates legitimately fire on the same turn, and pinning it
        # would make this test fail for reasons that are not its subject.
        self.assertGreater(provider.complete.call_count, 1)
        self.assertIn("ei käivitatud", answer)
        self.assertNotIn("väljastas: tere", answer)

    def test_a_refused_command_does_not_count_as_executed(self):
        """`tools_run` is not enough: a refused command still returns a
        successful tool result describing the refusal, so the tool's presence
        is not evidence that anything executed."""
        source = (Path(__file__).parents[1] / "aura" / "agent.py").read_text(encoding="utf-8")
        self.assertIn('result.get("approved")', source)
        self.assertIn("commands_executed += 1", source)


class PlanBeforeBuildTests(unittest.TestCase):
    """The stop between planning and building.

    `_plan_files` was already the stop for a build that named several files.
    Measured yesterday: the request most worth agreeing first — "look at this
    and improve it" — names none, so the card never appeared for it, and the
    plan it did produce was shown once and thrown away.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspace"
        (self.root / "shop").mkdir(parents=True)
        self.provider = LMStudioProvider(model="local-model")
        self.agent = AuraAgent(self.root, provider=self.provider)
        self.addCleanup(self.agent.db.close)
        self.agent.current_project = "shop"
        self.agent.current_request = "Vaata shop üle ja tee paremaks"
        self.shown = []

    def approve(self, request):
        self.shown.append(request)
        return True

    def draft(self, text):
        self.provider.complete = unittest.mock.Mock(
            return_value=ProviderReply(text, []))

    def test_a_project_with_no_plan_stops_even_when_no_files_are_named(self):
        """The old condition needed two named paths, so the vaguest and most
        expensive request was the one that never got agreed first."""
        self.draft("- Read the pages - open index.html\n- Add a cart page - it exists")
        plan = self.agent._plan_files("Vaata shop üle ja tee paremaks", [], True,
                                      self.approve, lambda _s: None)
        self.assertIn("Add a cart page", plan)
        self.assertEqual(self.shown[0][0], "PLAN")

    def test_the_agreed_plan_is_kept_where_the_next_turn_will_find_it(self):
        """A card is agreed once and gone. The point of the file is that the
        next turn reads it and the user can correct it in between."""
        self.draft("- Read the pages\n- Add a cart page")
        self.agent._plan_files("Vaata shop üle ja tee paremaks", [], True,
                               self.approve, lambda _s: None)
        written = (self.root / "shop" / "PLAN.md").read_text(encoding="utf-8")
        self.assertIn("Agreed with you before the work started", written)
        self.assertIn("Add a cart page", written)
        self.assertIn("Vaata shop üle ja tee paremaks", written)
        self.assertIn(written.split("## The plan")[1][:40].strip()[:10],
                      self.agent.plan_text("shop"))

    def test_the_draft_is_asked_for_in_the_users_language(self):
        """The rule lives several messages earlier and this instruction is
        English and comes last, so the last thing read before writing said
        "English" without meaning to. Both models drafted the plan in Finnish —
        the exact drift the rule exists to stop."""
        self.draft("- samm")
        self.agent._plan_files("Vaata shop üle ja tee paremaks", [], True,
                               self.approve, lambda _s: None)
        sent = self.provider.complete.call_args[0][0]
        last = sent[-1]["content"]
        self.assertEqual(sent[-1]["role"], "system")
        self.assertIn("Estonian", last)
        self.assertIn("Finnish", last)

    def test_an_english_request_is_not_told_to_write_estonian(self):
        self.draft("- step")
        self.agent._plan_files("Look at the shop and improve it", [], True,
                               self.approve, lambda _s: None)
        last = self.provider.complete.call_args[0][0][-1]["content"]
        self.assertIn("English", last)
        self.assertNotIn("Reply in Estonian", last)

    def test_declining_stops_before_anything_is_written(self):
        self.draft("- Add a cart page")
        result = self.agent._plan_files("Vaata shop üle ja tee paremaks", [], True,
                                        lambda _r: False, lambda _s: None)
        self.assertIs(result, AuraAgent.PLAN_DECLINED)
        self.assertFalse((self.root / "shop" / "PLAN.md").exists())

    def test_a_project_that_already_has_a_plan_is_not_asked_again(self):
        """Once is agreeing; every time is nagging."""
        (self.root / "shop" / "PLAN.md").write_text("# shop\n\nsettled", encoding="utf-8")
        self.draft("- something")
        plan = self.agent._plan_files("Lisa shop kausta jalus", [], True,
                                      self.approve, lambda _s: None)
        self.assertEqual(plan, "")
        self.assertEqual(self.shown, [])

    def test_an_existing_plan_is_never_replaced_by_a_new_agreement(self):
        """Reached only through the two-named-files path, since a project with
        a plan no longer stops — but the file must still be safe."""
        (self.root / "shop" / "PLAN.md").write_text("mine", encoding="utf-8")
        self.draft("- shop/a.html - one\n- shop/b.html - two")
        self.agent._plan_files("Tee shop/a.html ja shop/b.html",
                               ["shop/a.html", "shop/b.html"], True,
                               self.approve, lambda _s: None)
        self.assertEqual((self.root / "shop" / "PLAN.md").read_text(encoding="utf-8"), "mine")

    def test_nothing_usable_back_means_get_on_with_the_work(self):
        """An empty card is not worth a user's decision."""
        self.draft("   ")
        plan = self.agent._plan_files("Vaata shop üle ja tee paremaks", [], True,
                                      self.approve, lambda _s: None)
        self.assertEqual(plan, "")
        self.assertEqual(self.shown, [])

    def test_no_one_to_ask_means_no_stop(self):
        """A plan nobody can confirm would just be an extra round trip — the
        scheduler and the tests both run without an approver."""
        self.draft("- something")
        self.assertEqual(
            self.agent._plan_files("Vaata shop üle", [], True, None, lambda _s: None), "")

    def test_a_question_never_stops_to_plan(self):
        self.draft("- something")
        self.assertEqual(
            self.agent._plan_files("Mis shop kaustas on?", [], False,
                                   self.approve, lambda _s: None), "")


class ProjectPlanTests(unittest.TestCase):
    """The plan as a file in the workspace rather than a card on screen.

    Measured on four real runs before this existed: the model looked before it
    wrote every time (4/4), and wrote the PLAN.md the system prompt asks for in
    only two. The gap is what this closes — without spending a model round on
    it, because a record is bookkeeping and the retry budget belongs to the
    gates that decide whether the work was right.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "workspace"
        (self.root / "shop").mkdir(parents=True)
        self.agent = AuraAgent(self.root, provider=MockProvider())
        self.addCleanup(self.agent.db.close)

    def build_turn(self):
        """A turn that really changed the project, as the gates would see it."""
        turn = TurnState()
        turn.workspace_mutation = True
        turn.tools_run = ["read_many_files", "create_file", "create_file", "validate_project"]
        turn.expected_paths = ["shop/index.html"]
        self.agent.current_project = "shop"
        self.agent.current_request = "Tee shop kausta avaleht valmis"
        return turn

    def test_a_build_leaves_a_plan_where_the_project_is(self):
        (self.root / "shop" / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
        self.agent._gate_plan(self.build_turn(), unittest.mock.Mock())
        written = (self.root / "shop" / "PLAN.md").read_text(encoding="utf-8")
        self.assertIn("Tee shop kausta avaleht valmis", written)
        self.assertIn("`create_file`", written)
        self.assertIn("shop/index.html", written)

    def test_it_records_what_ran_and_how_often_rather_than_a_summary(self):
        """Built from the tools that actually succeeded, so it cannot claim
        work that did not happen — which a model summarising itself can."""
        written_at = self.agent._describe_turn(self.build_turn())
        self.assertIn("`create_file` ×2", written_at)
        self.assertIn("`read_many_files`", written_at)
        self.assertNotIn("write_file", written_at)

    def test_a_file_that_was_never_created_is_left_out(self):
        """`expected_paths` is what the request was *read* to mean, and the
        reading can be wrong: one live run listed `avaleht/index.html` because
        the Estonian phrase for "the X folder" was parsed off a word that was
        not a folder. A plan naming a file that does not exist is worse than
        one naming none."""
        turn = self.build_turn()
        turn.expected_paths = ["shop/index.html", "avaleht/index.html"]
        (self.root / "shop" / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
        written = self.agent._describe_turn(turn)
        files = written.split("## Files")[1].split("## Still open")[0]
        self.assertIn("shop/index.html", files)
        # Scoped to the file list: the word itself belongs in the quoted
        # request, which is reproduced verbatim on purpose.
        self.assertNotIn("avaleht", files)
        self.assertIn("avaleht", written)

    def test_an_existing_plan_is_never_overwritten(self):
        """A plan the model wrote, or one the user edited, is worth more than
        anything reconstructed after the fact."""
        (self.root / "shop" / "PLAN.md").write_text("mine, hands off", encoding="utf-8")
        self.agent._gate_plan(self.build_turn(), unittest.mock.Mock())
        self.assertEqual((self.root / "shop" / "PLAN.md").read_text(encoding="utf-8"),
                         "mine, hands off")

    def test_a_turn_that_changed_nothing_leaves_no_plan(self):
        """Demanding a record for a question would be the nagging this exists
        to prevent."""
        turn = TurnState()
        self.agent.current_project = "shop"
        self.agent._gate_plan(turn, unittest.mock.Mock())
        self.assertFalse((self.root / "shop" / "PLAN.md").exists())

    def test_no_project_means_no_plan_file(self):
        turn = self.build_turn()
        self.agent.current_project = None
        self.assertEqual(self.agent._gate_plan(turn, unittest.mock.Mock()), PASS)
        self.assertIsNone(self.agent.plan_path(None))

    def test_the_next_turn_works_from_the_plan_on_file(self):
        """The whole point of a file over a card: it survives the turn, the
        user can correct it, and she reads back what it now says."""
        (self.root / "shop" / "PLAN.md").write_text(
            "# shop\n\n## Still open\n\n- Build the cart page first.\n", encoding="utf-8")
        context = self.agent._context("Tee shop kaustas järgmine samm")
        self.assertIn("Build the cart page first", context.plan)
        system = "\n".join(
            m["content"] for m in LMStudioProvider().start_messages("Tee shop kaustas järgmine samm", context)
            if m["role"] == "system")
        self.assertIn("Build the cart page first", system)
        # It has to outrank the model's own memory of what it meant to do.
        self.assertIn("outranks your memory", system)

    def test_the_request_is_recorded_by_the_turn_not_by_the_test(self):
        """`_describe_turn` read an attribute nothing ever set, so every real
        plan would have said "Not recorded" while the test passed by setting it
        itself. The turn has to be what puts it there."""
        agent = AuraAgent(self.root, provider=MockProvider())
        self.addCleanup(agent.db.close)
        self.assertEqual(agent.current_request, "")
        agent.handle("Tee shop kausta avaleht valmis")
        self.assertEqual(agent.current_request, "Tee shop kausta avaleht valmis")

    def test_a_plan_too_large_to_carry_is_truncated_not_refused(self):
        """It rides in every request for that project, so size has a ceiling —
        but a long plan is still better than none."""
        (self.root / "shop" / "PLAN.md").write_text("x" * 20000, encoding="utf-8")
        self.assertEqual(len(self.agent.plan_text("shop")), AuraAgent.MAX_PLAN)




class ArtifactContractTests(unittest.TestCase):
    """What counts as a file Aura was asked to produce."""

    def contract(self, message):
        return AuraAgent._extract_artifact_contract(message)[1]

    def test_a_framework_in_a_stack_list_is_not_a_file(self):
        """Measured on a real request that listed a technology stack: Next.js
        and Node.js were read as two files Aura had been told to create, so the
        completion gate would have called a finished job unfinished."""
        self.assertEqual(self.contract(
            "Platvormid: Shopify, WooCommerce, custom lahendused "
            "(Next.js, React, Node.js)"), [])

    def test_ordinary_filenames_still_count(self):
        self.assertEqual(self.contract("Tee mulle index.html ja style.css"),
                         ["index.html", "style.css"])

    def test_a_path_is_always_taken_at_its_word(self):
        """The deny-list only covers bare names. Anyone who really does have a
        file called that can say so with a path, which is the rarer case."""
        self.assertEqual(self.contract("Loo fail src/next.js"), ["src/next.js"])
        self.assertEqual(self.contract("Tee ./next.js valmis"), ["./next.js"])


class SilenceAndRecallTests(unittest.TestCase):
    """The three defects measured on 2026-08-17 and fixed the same evening."""

    # --------------------------------------------- explaining the silence

    def reason(self, finish, tokens, service="LM Studio"):
        turn = TurnState()
        turn.finish_reason, turn.completion_tokens = finish, tokens
        # The advice has to name the provider actually in use, so this needs an
        # instance now rather than being read off the class.
        stand_in = type("_Agent", (), {"provider": type("_P", (), {"SERVICE": service})()})()
        return AuraAgent._empty_response_reason(stand_in, turn)

    def test_a_model_that_declines_is_not_reported_as_a_broken_server(self):
        """Measured twice: finish_reason `stop`, one completion token, a 3.7k
        prompt against a 32k budget. The model was loaded, fast and answering —
        the old message sent the user to check the one thing already cleared."""
        message = self.reason("stop", 1)
        self.assertIn("chose not to answer", message)
        self.assertNotIn("loaded in LM Studio", message)

    def test_running_out_of_room_says_so_instead(self):
        message = self.reason("length", 4096)
        self.assertIn("ran out of room", message)
        self.assertIn("Settings", message)

    def test_an_unknown_reason_keeps_the_original_advice(self):
        """With nothing reported, checking the server is still the right guess."""
        self.assertIn("loaded in LM Studio", self.reason("", 0))

    def test_the_advice_names_the_provider_actually_in_use(self):
        """It said "LM Studio" outright, which sends someone whose model is a
        cloud one off to check a program that is not involved."""
        self.assertIn("loaded in Claude", self.reason("", 0, service="Claude"))
        self.assertNotIn("LM Studio", self.reason("", 0, service="Claude"))

    def test_the_streaming_path_reports_the_reason_too(self):
        """The first capture in the wild read "(not given)" and two zeros: the
        instrument had been added to the non-streaming parser while the app
        streams. The measurement had the same shape as the bug."""
        source = (Path(__file__).parents[1] / "aura" / "provider.py").read_text(encoding="utf-8")
        streaming = source[source.index("def _stream_completion"):]
        self.assertIn("include_usage", streaming)
        self.assertIn("finish_reason", streaming)

    # ------------------------------------------------ finding Estonian words

    def test_a_word_with_estonian_letters_is_no_longer_cut_in_half(self):
        """`[a-z0-9]` made "tööruum" into "ruum" and "kõik" into nothing, so the
        most distinctively Estonian words were the ones recall could not see."""
        with tempfile.TemporaryDirectory() as temporary:
            memory = MemoryStore(Path(temporary) / "memory.json")
            memory.learn_fact("preference", "eelistab tumedaid taustu kõikjal", source="t")
            memory.learn_fact("project", "tööruumis on kolm projekti", source="t")
            self.assertTrue(memory.relevant_memories("kõikjal", 5))
            found = [item["value"] for item in memory.relevant_memories("mis on tööruumis", 5)]
            self.assertIn("tööruumis on kolm projekti", found)

    def test_the_stored_key_is_deliberately_left_alone(self):
        """It is written onto every memory, so widening it would re-identify
        them all and break deduplication against what is already held."""
        source = (Path(__file__).parents[1] / "aura" / "memory.py").read_text(encoding="utf-8")
        key = source[source.index("def _fact_key"):source.index("def _comparable_fact")]
        self.assertIn("[^a-z0-9]+", key)

    # ------------------------------------------------------ capability asks

    def test_asking_what_aura_can_do_is_not_a_build_request(self):
        """`teha` -> make sits inside "teha oskad", and before the longest match
        won, asking what she could do registered as asking her to make something."""
        for request in ("Mis sa teha oskad?", "Mida sa teha saad?"):
            self.assertFalse(AuraAgent._requires_mutation(request), request)
        self.assertTrue(AuraAgent._requires_mutation("Tee mulle veebileht"))


class SelfCheckTests(unittest.TestCase):
    """Improvement 4: one answer to "is anything broken?".

    Every part already reported itself somewhere; what was missing was a place
    that asks all of them, so the diagnosis stopped being the user's job.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())

    def tearDown(self):
        self.temp.cleanup()

    def statuses(self, report):
        return {check["name"]: check["status"] for check in report["checks"]}

    # ------------------------------------------------------------ it answers

    def test_it_reports_on_every_part_without_raising(self):
        report = health.run(self.agent)
        names = {check["name"] for check in report["checks"]}
        self.assertLessEqual({"provider", "model", "vision", "workspace",
                              "database", "speech", "voice", "search"}, names)
        self.assertIn(report["status"], {health.OK, health.WARN, health.FAIL, health.UNKNOWN})

    def test_a_check_that_breaks_reports_itself_rather_than_the_report(self):
        """The whole point is to work when things are wrong."""
        broken = unittest.mock.Mock(side_effect=RuntimeError("boom"))
        with unittest.mock.patch.object(health, "_check_workspace", broken):
            report = health.run(self.agent)
        self.assertTrue(any(check["status"] == health.UNKNOWN
                            and "could not run" in check["detail"]
                            for check in report["checks"]))

    # -------------------------------------------------- honest about not knowing

    def test_a_provider_that_is_not_a_server_is_not_given_a_verdict(self):
        """MockProvider has no server, and inventing one either way is a lie."""
        statuses = self.statuses(health.run(self.agent))
        self.assertEqual(statuses["provider"], health.UNKNOWN)
        self.assertEqual(statuses["model"], health.UNKNOWN)

    def test_no_speech_engine_is_unknown_not_unsupported(self):
        """Saying "Windows only" on Windows is the kind of confident wrong
        answer a self-check must never give."""
        detail = next(check for check in health.run(self.agent, speech=None)["checks"]
                      if check["name"] == "speech")
        self.assertEqual(detail["status"], health.UNKNOWN)
        self.assertNotIn("Windows only", detail["detail"])

    # ------------------------------------------------------------- it is honest

    def test_an_unreachable_server_fails_and_says_what_to_do(self):
        class Dead:
            base_url = "http://127.0.0.1:9/v1"

            def available_models(self):
                raise OSError("connection refused")

        self.agent.provider = Dead()
        checks_by_name = {check["name"]: check for check in health.run(self.agent)["checks"]}
        self.assertEqual(checks_by_name["provider"]["status"], health.FAIL)
        self.assertIn("LM Studio", checks_by_name["provider"]["remedy"])
        # The model question cannot be answered while the server is silent.
        self.assertEqual(checks_by_name["model"]["status"], health.UNKNOWN)

    def test_a_server_with_no_model_is_a_different_failure(self):
        """Rolling the two together produced the unhelpful "LM Studio is not
        working" when the server was fine."""
        class Empty:
            base_url = "http://127.0.0.1:1234/v1"

            def available_models(self):
                return []

            def selected_model(self):
                return None

        self.agent.provider = Empty()
        checks_by_name = {check["name"]: check for check in health.run(self.agent)["checks"]}
        self.assertEqual(checks_by_name["provider"]["status"], health.OK)
        self.assertEqual(checks_by_name["model"]["status"], health.FAIL)

    def test_an_unwritable_workspace_is_reported(self):
        missing = Path(self.temp.name) / "gone"
        self.agent.sandbox.root = missing
        result = next(check for check in health.run(self.agent)["checks"]
                      if check["name"] == "workspace")
        self.assertEqual(result["status"], health.FAIL)

    def test_the_overall_verdict_is_the_worst_one(self):
        self.assertEqual(
            health.run(self.agent)["status"],
            max((check["status"] for check in health.run(self.agent)["checks"]),
                key=lambda status: {health.OK: 0, health.UNKNOWN: 0,
                                    health.WARN: 1, health.FAIL: 2}[status]))

    # --------------------------------------------------------- it changes nothing

    def test_it_leaves_the_workspace_exactly_as_it_found_it(self):
        """One probe file is written and removed; nothing else is touched."""
        root = Path(self.temp.name) / "workspace"
        (root / "keep.txt").write_text("mine", encoding="utf-8")
        # Aura's own .aura folder is internal state, and SQLite writes its WAL
        # files there simply by being opened. The claim is about the user's
        # workspace content.
        listing = lambda: sorted(item.name for item in root.rglob("*")
                                 if item.is_file() and ".aura" not in item.parts)
        before = listing()
        health.run(self.agent)
        after = listing()
        self.assertEqual(before, after)
        self.assertFalse((root / ".aura-write-probe").exists())

    def test_the_bridge_method_itself_works(self):
        """The unit tests called health.run directly and never the method the
        button calls, so a bad log call sat behind a green suite until the
        panel was actually opened."""
        bridge = AuraWebBridge(agent=self.agent, speech=SpeechOutput(enabled=False))
        bridge.scheduler.stop()
        self.addCleanup(bridge.shutdown)
        report = bridge.self_check()
        self.assertTrue(report["ok"])
        self.assertTrue(report["checks"])
        recorded = [item for item in self.agent.db.recent_actions(20)
                    if item.get("action") == "self_check"]
        self.assertEqual(len(recorded), 1)

    def test_the_model_may_ask_but_only_to_read(self):
        names = {item["function"]["name"] for item in self.agent.tool_definitions()}
        self.assertIn("self_check", names)
        self.assertNotIn("self_check", set(toolkit.mutating_names()))


class PatternCheckTests(unittest.TestCase):
    """Improvement 1: Aura reads her own journals and says when a pattern forms.

    Measured on the real journal before writing any of this: 139 tasks, 33 of
    them failed. The most common single cause was "the model returned neither
    text nor a tool request" — eleven times, against two for the empty-response
    message that was actually being chased. Nothing looked at any of it.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())

    def tearDown(self):
        self.temp.cleanup()

    def fail(self, message, *, times=1, action="request", status="error", hours_ago=0.0):
        when = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        for _ in range(times):
            self.agent.db.add_action({"time": when.isoformat(), "action": action,
                                      "status": status, "error": message})

    def finish(self, status, *, times=1):
        for index in range(times):
            task_id = uuid4().hex[:12]
            now = datetime.now(timezone.utc).isoformat()
            self.agent.db.add_task_event({"task_id": task_id, "event": "started",
                                          "time": now, "request": f"request {index}"})
            self.agent.db.add_task_event({"task_id": task_id, "event": "finished",
                                          "time": now, "status": status})

    # -------------------------------------------------------- silence is normal

    def test_a_quiet_journal_says_nothing(self):
        for name in checks.names():
            self.assertIsNone(checks.get(name).run(self.agent), name)

    def test_two_of_the_same_thing_is_not_yet_a_pattern(self):
        """One is an event, two is a coincidence. A check that speaks too early
        teaches its reader to skip it, and then the once it matters it is
        skipped too."""
        self.fail("the model returned neither text nor a tool request", times=2)
        self.assertIsNone(checks.get("model_producing_nothing").run(self.agent))
        self.fail("the model returned neither text nor a tool request")
        self.assertIsNotNone(checks.get("model_producing_nothing").run(self.agent))

    # ------------------------------------------------------------ the window

    def test_an_old_pattern_is_not_a_current_one(self):
        """Counting over all history makes a complaint nobody can silence by
        fixing the problem, which is the definition of a nag."""
        self.fail("the model returned neither text nor a tool request",
                  times=6, hours_ago=checks.WINDOW_HOURS + 2)
        self.assertIsNone(checks.get("model_producing_nothing").run(self.agent))

    # ---------------------------------------------------- one pattern, one voice

    def test_the_generic_check_leaves_this_family_to_the_specific_one(self):
        """Two checks describing one pattern is noise, so the one that can say
        something actionable owns it."""
        self.fail("the model returned neither text nor a tool request", times=5)
        self.assertIsNone(checks.get("recent_failures").run(self.agent))
        self.assertIsNotNone(checks.get("model_producing_nothing").run(self.agent))

    def test_the_generic_check_still_reports_anything_else(self):
        self.fail("the disk is full", times=4)
        finding = checks.get("recent_failures").run(self.agent)
        self.assertIsNotNone(finding)
        self.assertIn("disk is full", finding.message)

    # --------------------------------------------------- decisions are not faults

    def test_a_declined_plan_is_not_a_failure(self):
        """Counting it as one told the user their own "no" three times back."""
        self.fail("file_plan", times=5, action="file_plan", status="declined")
        self.assertIsNone(checks.get("recent_failures").run(self.agent))

    # ---------------------------------------------------------------- the streak

    def test_a_run_of_failures_is_reported_as_a_run(self):
        """Different from "some things fail": a run says something changed now."""
        self.finish("error", times=3)
        finding = checks.get("failing_streak").run(self.agent)
        self.assertIsNotNone(finding)
        self.assertIn("3 tasks in a row", finding.message)
        self.assertIn("LM Studio", finding.message)

    def test_a_success_ends_the_run(self):
        self.finish("error", times=3)
        self.finish("completed")
        self.assertIsNone(checks.get("failing_streak").run(self.agent))

    def test_a_cancellation_neither_breaks_nor_extends_the_run(self):
        """Stopping something is the user's decision, not a fault."""
        self.finish("error", times=2)
        self.finish("cancelled")
        self.finish("error")
        self.assertIsNotNone(checks.get("failing_streak").run(self.agent))

    # -------------------------------------------------------- unkept promises

    def test_a_file_promised_and_never_written_is_named(self):
        self.fail("required artifacts are still missing: report.txt", times=3)
        finding = checks.get("unkept_promises").run(self.agent)
        self.assertIsNotNone(finding)
        self.assertIn("report.txt", finding.message)
        # It proposes understanding the cause, never quietly creating the file.
        self.assertIn("Do not create", finding.proposal)

    def test_every_pattern_check_is_read_only(self):
        """These run unattended, so none of them may change anything."""
        before = sorted(path.name for path in
                        (Path(self.temp.name) / "workspace").rglob("*") if path.is_file())
        self.fail("the model returned neither text nor a tool request", times=5)
        self.fail("required artifacts are still missing: report.txt", times=5)
        self.finish("error", times=5)
        for name in checks.names():
            checks.get(name).run(self.agent)
        after = sorted(path.name for path in
                       (Path(self.temp.name) / "workspace").rglob("*") if path.is_file())
        self.assertEqual(before, after)


class MindRelationshipTests(unittest.TestCase):
    """The one edge on the map the user owns.

    Every other relationship in Aura Mind is derived from the data and cannot
    honestly be edited. A memory's project is different: it comes from a guess
    made when the fact was learned, it was never drawn, and it could not be
    corrected.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.memory = MemoryStore(Path(self.temp.name) / "memory.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_a_project_can_be_set_corrected_and_detached(self):
        fact = self.memory.learn_fact("goal", "ship the promo site", source="test")
        self.assertIsNone(fact.get("project"))
        self.assertEqual(
            self.memory.update_profile_memory(fact["id"], project="promo")["project"], "promo")
        self.assertEqual(
            self.memory.update_profile_memory(fact["id"], project="shop")["project"], "shop")
        # Detaching has to be as easy as linking: a wrong link is worse than none.
        self.assertIsNone(self.memory.update_profile_memory(fact["id"], project="")["project"])

    def test_an_ordinary_edit_leaves_the_project_alone(self):
        fact = self.memory.learn_fact("goal", "ship the promo site", source="test")
        self.memory.update_profile_memory(fact["id"], project="promo")
        edited = self.memory.update_profile_memory(fact["id"], value="ship the promo site today")
        self.assertEqual(edited["project"], "promo")

    def test_the_project_is_actually_drawn(self):
        """It was stored and never shown, so the map knew less than Aura did."""
        memory = {"profile_memories": [
            {"id": "m1", "category": "goal", "value": "ship the promo site",
             "confirmed": True, "project": "promo"},
            {"id": "m2", "category": "preference", "value": "dark backgrounds",
             "confirmed": True},
        ]}
        nodes, edges = build_mind_graph(memory, [], [])
        by_id = {node.node_id: node for node in nodes}
        self.assertIn("project:promo", by_id)
        self.assertIn(("project:promo", "personal:m1"), [(e.source, e.target) for e in edges])
        self.assertEqual(by_id["personal:m1"].memory_id, "m1")
        self.assertEqual(by_id["personal:m1"].project, "promo")
        # A memory with no project gains no edge and no phantom node.
        self.assertIsNone(by_id["personal:m2"].project)

    # ------------------------------------------ what Aura does on her own

    SCHEDULED = [
        {"id": "c1", "kind": "check", "request": "broken_links", "every_minutes": 1440,
         "next_run": "2026-08-18T05:00:00Z", "last_outcome": "nothing to report"},
        {"id": "r1", "kind": "reminder", "request": "venita", "next_run": "2026-08-18T09:00:00Z"},
    ]
    PROPOSALS = [{"id": "p1", "request": "Fix the dead link in promo/index.html",
                  "source": "broken_links"}]

    def test_the_map_shows_what_she_watches_and_what_waits(self):
        """Everything phase 48 built was stored and never drawn: a map claiming
        to show what Aura knows and does was silent about the half she does
        without being asked."""
        nodes, edges = build_mind_graph({}, [], [], scheduled=self.SCHEDULED,
                                        proposals=self.PROPOSALS)
        kinds = {node.node_id: node.kind for node in nodes}
        self.assertEqual(kinds.get("check:c1"), "check")
        self.assertEqual(kinds.get("reminder:r1"), "reminder")
        self.assertEqual(kinds.get("proposal:p1"), "proposal")
        drawn = {(edge.source, edge.target) for edge in edges}
        self.assertIn(("watching", "check:c1"), drawn)
        self.assertIn(("watching", "reminder:r1"), drawn)
        self.assertIn(("waiting", "proposal:p1"), drawn)

    def test_a_check_says_when_it_runs_and_what_it_last_said(self):
        nodes, _edges = build_mind_graph({}, [], [], scheduled=self.SCHEDULED)
        detail = next(n.detail for n in nodes if n.node_id == "check:c1")
        self.assertIn("read-only", detail)
        self.assertIn("nothing to report", detail)

    def test_an_empty_autonomy_layer_says_so_rather_than_vanishing(self):
        """A branch that disappears when empty reads as a feature that is not
        there, which is exactly the impression this step exists to correct."""
        nodes, edges = build_mind_graph({}, [], [])
        labels = {node.node_id: node.label for node in nodes}
        self.assertIn("check:empty", labels)
        self.assertIn("proposal:empty", labels)
        drawn = {(edge.source, edge.target) for edge in edges}
        self.assertIn(("watching", "check:empty"), drawn)
        self.assertIn(("waiting", "proposal:empty"), drawn)

    def test_the_interface_can_colour_and_hide_the_new_layers(self):
        """Layers are derived from the graph, so a branch with no legend entry
        would be drawn in a default colour and could not be switched off."""
        script = (Path(__file__).parents[1] / "aura" / "web" / "app.js").read_text(encoding="utf-8")
        for layer in ('id: "watching"', 'id: "waiting"'):
            self.assertIn(layer, script)
        for kind in ("check:", "reminder:", "proposal:"):
            self.assertIn(kind, script)

    def test_only_memory_nodes_offer_editing(self):
        """Offering to edit a derived edge would be a lie about what happens."""
        memory = {"profile_memories": [
            {"id": "m1", "category": "goal", "value": "ship it", "confirmed": True,
             "project": "promo"}]}
        nodes, _edges = build_mind_graph(memory, [], ["promo/index.html"])
        for node in nodes:
            if node.kind not in {"personal_memory", "personal_memory_pinned"}:
                self.assertIsNone(node.memory_id, node.node_id)


class LivePlanTests(unittest.TestCase):
    """An approved plan used to be shown once and then vanish.

    While the build ran there was a spinner and a log, and no answer to "how far
    along is this?".
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        agent.config.update(seeded_checks=list(checks.DEFAULT_CHECKS))
        self.bridge = AuraWebBridge(agent=agent, speech=SpeechOutput(enabled=False))
        self.bridge.scheduler.stop()
        self.events = []
        self.bridge._push = lambda kind, **payload: self.events.append({"type": kind, **payload})

    def tearDown(self):
        self.bridge.shutdown()
        self.temp.cleanup()

    def kinds(self, name):
        return [event for event in self.events if event["type"] == name]

    def test_approving_a_plan_puts_it_on_screen(self):
        self.bridge._start_plan("- promo/index.html - the landing page\n"
                                "- promo/style.css - the styles")
        started = self.kinds("plan_started")
        self.assertEqual(len(started), 1)
        self.assertEqual([step["path"] for step in started[0]["steps"]],
                         ["promo/index.html", "promo/style.css"])
        self.assertTrue(all(not step["done"] for step in started[0]["steps"]))

    def test_a_step_ticks_only_when_the_file_was_really_written(self):
        """Ticking on intention rather than on result is worse than no plan."""
        self.bridge._start_plan("- promo/index.html - the landing page\n"
                                "- promo/style.css - the styles")
        # Reading it proves nothing.
        self.bridge._on_tool("read_file", {"path": "promo/index.html"}, True)
        self.assertEqual(self.kinds("plan_progress"), [])
        # A failed write proves nothing either.
        self.bridge._on_tool("create_file", {"path": "promo/index.html"}, False)
        self.assertEqual(self.kinds("plan_progress"), [])

        self.bridge._on_tool("create_file", {"path": "promo/index.html"}, True)
        progress = self.kinds("plan_progress")
        self.assertEqual(len(progress), 1)
        self.assertEqual(progress[-1]["done"], 1)
        self.assertEqual(progress[-1]["total"], 2)

    def test_a_batch_write_ticks_every_file_it_wrote(self):
        self.bridge._start_plan("- a.html - one\n- b.css - two")
        self.bridge._on_tool("write_files", {"files": [{"path": "a.html"}, {"path": "b.css"}]}, True)
        self.assertEqual(self.kinds("plan_progress")[-1]["done"], 2)

    def test_the_same_file_twice_does_not_tick_twice(self):
        self.bridge._start_plan("- a.html - one\n- b.css - two")
        self.bridge._on_tool("create_file", {"path": "a.html"}, True)
        self.bridge._on_tool("write_file", {"path": "a.html"}, True)
        self.assertEqual(len(self.kinds("plan_progress")), 1)

    def test_tool_activity_without_a_plan_is_ignored(self):
        self.bridge._on_tool("create_file", {"path": "a.html"}, True)
        self.assertEqual(self.kinds("plan_progress"), [])

    def test_a_reporting_failure_never_breaks_the_tool(self):
        """The callback is a convenience; a tool must not fail because of it."""
        agent = self.bridge.agent
        agent.on_tool = lambda *_args: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            result = agent._execute_tool(
                ToolCall("1", "list_files", {"path": "."}), None)
            self.assertTrue(result["ok"])
        finally:
            agent.on_tool = None


class BilingualSpeechTests(unittest.TestCase):
    """Aura answers in two languages and had one voice for both.

    There is no single model that reads Estonian and English: Piper publishes
    no Estonian voice at all (174 voices, 55 languages, none of them Estonian),
    and Windows ships none until the language is added. So the answer is two
    voices and an automatic switch — plus saying so when one of them is missing,
    because an English voice reading Estonian sounds like Aura is broken rather
    than like a voice that was never installed.
    """

    def voice(self, **kwargs):
        return SpeechOutput(enabled=True, engine="piper", **kwargs)

    # ------------------------------------------------------------- detection

    def test_a_reply_is_recognised_by_its_own_words(self):
        self.assertEqual(language.detect("Tööruumis on 10 faili"), "et")
        self.assertEqual(language.detect("The workspace has 10 files"), "en")
        self.assertEqual(language.detect("Ma ei saa seda teha"), "et")
        self.assertEqual(language.detect("I removed the broken link"), "en")

    def test_a_word_that_is_evidence_for_both_is_evidence_for_neither(self):
        """`on` is everywhere in Estonian and also an English preposition.

        Counted on both sides it made "Eesti pealinn on Tallinn" score one-all
        and come out English.
        """
        self.assertIn("on", language.AMBIGUOUS_MARKERS)
        self.assertEqual(language.detect("The build is on hold"), "en")

    def test_a_short_reply_falls_back_to_the_language_of_the_question(self):
        """Aura answers in the language she was addressed in.

        "Eesti pealinn on Tallinn" carries no Estonian letter and no give-away
        word, and is obvious to anyone who saw the question.
        """
        short = "Eesti pealinn on Tallinn."
        self.assertEqual(language.detect(short, default="et"), "et")
        self.assertEqual(language.detect(short, default="en"), "en")

    def test_evidence_in_the_reply_beats_the_hint(self):
        self.assertEqual(language.detect("The workspace has 10 files", default="et"), "en")
        self.assertEqual(language.detect("Kõik on tehtud", default="en"), "et")

    def test_only_the_letters_estonian_owns_settle_it(self):
        """The first version of this test asserted the wrong thing.

        It treated every dotted letter as proof of Estonian, but `ä`, `ö` and
        `ü` are shared with Finnish — which is exactly where the model drifts
        when it is asked in Estonian, so those letters were confirming the
        mistake rather than catching it.
        """
        for letter in "õšž":
            self.assertEqual(language.detect(f"x{letter}x", default="en"), "et", letter)
        for shared in "äöü":
            self.assertEqual(language.detect(f"x{shared}x", default="en"), "en", shared)

    def test_a_finnish_reply_is_named_rather_than_relabelled(self):
        """Finnish is neither answer `detect` gives, so it gets its own question.

        Folding it into "et" is how a wrong-language reply becomes invisible.
        """
        self.assertTrue(language.looks_finnish(
            "Valmis! Kaikki kolme tiedostoa luotiin onnistuneesti"))
        self.assertTrue(language.looks_finnish("perus CSS-tyylit sivuille, joka on tehty"))
        # One borrowed word proves nothing: "voi" and "niin" occur in Estonian.
        self.assertFalse(language.looks_finnish("Ma ei saa voi seda teha"))
        self.assertFalse(language.looks_finnish("Tööruumis on 10 faili"))
        self.assertFalse(language.looks_finnish("The workspace has 10 files"))

    def test_the_reply_language_is_stated_in_the_prompt(self):
        """Nothing had ever said which language to answer in."""
        provider = LMStudioProvider("http://127.0.0.1:1234/v1")
        context = ProviderContext(name="", preferences={}, recent_messages=[])
        def rule_for(message):
            return next((item["content"] for item in provider.start_messages(message, context)
                         if item["role"] == "system" and "writing in" in item["content"]), "")
        estonian = rule_for("Tee mulle veebileht")
        self.assertIn("Estonian", estonian)
        # Naming the specific drift is the point: a small model asked in
        # Estonian answers in Finnish, and saying so helps it more than a
        # general instruction does.
        self.assertIn("Finnish", estonian)
        self.assertIn("English", rule_for("Make me a web page"))

    # ------------------------------------------------------- picking a voice

    def test_each_language_gets_its_own_voice(self):
        speech = self.voice(voice="English Voice", voice_et="Eesti Hääl")
        self.assertEqual(speech.voice_for("en")[0], "English Voice")
        self.assertEqual(speech.voice_for("et")[0], "Eesti Hääl")
        self.assertTrue(speech.voice_for("et")[2])

    def test_a_missing_estonian_voice_still_speaks_but_says_so(self):
        """Silence would be a worse surprise than a wrong accent."""
        speech = self.voice(voice="English Voice")
        spoken_voice, _model, covered = speech.voice_for("et")
        self.assertEqual(spoken_voice, "English Voice")
        self.assertFalse(covered)

    def test_speaking_one_language_does_not_repoint_the_other(self):
        """The swap is for one utterance: the configured voice has to survive."""
        speech = self.voice(voice="English Voice", voice_et="Eesti Hääl")
        speech.available = staticmethod(lambda: True)
        speech._speak_sapi = lambda text, on_cues=None: (True, "spoken")
        speech.neural_available = lambda: False
        speech.speak("Tere", language="et")
        self.assertEqual(speech.last_language, "et")
        self.assertEqual(speech.voice, "English Voice")
        speech.speak("Hello", language="en")
        self.assertEqual(speech.last_language, "en")
        self.assertEqual(speech.voice, "English Voice")

    def test_the_estonian_voice_is_the_users_choice_not_the_models(self):
        """Nothing the model can call points speech anywhere."""
        with tempfile.TemporaryDirectory() as workspace:
            agent = AuraAgent(Path(workspace) / "w", provider=MockProvider())
            for definition in agent.tool_definitions():
                spec = toolkit.get(definition["function"]["name"])
                fields = set((spec.properties if spec else {}) or {})
                self.assertNotIn("voice", fields)
                self.assertNotIn("speech_voice_et", fields)


class EstonianRoutingTests(unittest.TestCase):

    def setUp(self):
        self._workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self._workspace.cleanup)

    """Tools are routed by keyword, and the keywords were English only.

    Measured on twenty ordinary requests before this existed: sixteen Estonian
    ones produced no tools at all, against none of their English translations.
    Aura then said she could not help — the same shape as the search bug, where
    a capability she had been denied read as one she lacked.
    """

    #: Deliberately mundane: the everyday things this user actually asks for.
    ORDINARY = (
        ("Loe fail notes.txt ette", "read_file"),
        ("Näita, mis failid tööruumis on", "list_files"),
        ("Tee mulle lihtne veebileht", "create_file"),
        ("Paranda see viga index.html-is", "replace_in_file"),
        ("Otsi failidest sõna TODO", "search_text"),
        ("Kustuta fail vana.txt", "safe_delete_file"),
        ("Nimeta see fail ümber", "move_file"),
        ("Võta viimane muudatus tagasi", "undo_last_change"),
        ("Mida sa minu kohta tead?", "list_personal_memory"),
        ("Tee ekraanipilt lehest", "capture_page"),
        ("Kontrolli, kas leht on ligipääsetav", "check_accessibility"),
        ("Võrdle neid kahte faili", "compare_files"),
        ("Arvuta 15% 240-st", "calculate"),
        ("Pakenda see kaust zipiks", "create_archive"),
        ("Ava tööruumi kaust", "open_workspace_item"),
        ("Tuleta mulle tunni pärast meelde", "set_reminder"),
    )

    def names(self, message, autonomy="powerful", depth="deep"):
        return {item["function"]["name"]
                for item in AuraAgent.select_tool_definitions(message, autonomy, depth)}

    def test_an_ordinary_estonian_request_gets_the_tool_it_needs(self):
        for request, expected in self.ORDINARY:
            with self.subTest(request=request):
                self.assertIn(expected, self.names(request))

    def test_no_ordinary_request_comes_back_empty_handed(self):
        for request, _ in self.ORDINARY:
            self.assertTrue(self.names(request), request)

    # ------------------------------------------------------ english is intact

    def test_english_requests_are_not_rewritten_at_all(self):
        """The regression that caught this design out.

        Hints were first inserted next to the word they explained, which split
        English phrases the rules match whole: "look it up" became
        "look create it up" and stopped matching. Worse, `loo` (create) firing
        inside *look* made a read-only request look like a build.
        """
        for request in ("look it up online", "look at notes.txt", "read notes.txt",
                        "build the page but do not run it", "delete old.txt",
                        "what can you do", "compare a.txt and b.txt"):
            self.assertEqual(language.with_english_hints(request), request, request)

    def test_no_estonian_stem_starts_an_english_routing_word(self):
        """The guard that makes the list above provable rather than plausible.

        A stem added later that collides with English fails here, instead of
        quietly turning English requests into something else.
        """
        english = set()
        for definition in AuraAgent.tool_definitions():
            english.update(re.findall(r"[a-z]{3,}",
                                      definition["function"]["description"].casefold()))
        english.update("look read write create make build show find search list delete remove "
                       "move copy rename undo revert compare check run open image page online "
                       "internet news remember forget preference file folder code".split())
        collisions = []
        for stem, hint in language.ESTONIAN_HINTS:
            for word in english:
                if word.startswith(stem.strip()) and word not in language.ENGLISH_LOOKALIKES:
                    collisions.append((stem, hint, word))
        self.assertEqual(collisions, [], f"Estonian stems firing on English: {collisions}")

    # ------------------------------------------- telling Aura about yourself

    MEMORY_TOOLS = {"remember_name", "remember_preference", "remember_personal_fact"}

    def test_an_estonian_statement_about_yourself_reaches_the_memory_tools(self):
        """Measured before this: memory tools offered zero times out of four.

        The model speaks Estonian perfectly well — it was never given the
        chance, because routing never put the tools on the table. What is worth
        keeping, and in what words, stays the model's judgement.
        """
        for request in ("Ma eelistan tumedaid taustu.",
                        "Jäta meelde, et ma kasutan VS Code-i.",
                        "Minu eesmärk on Aura valmis saada.",
                        "Mulle meeldib veebilehti ehitada."):
            offered = self.names(request)
            self.assertTrue(offered & self.MEMORY_TOOLS, request)

    def test_keeping_something_in_mind_is_not_a_reminder(self):
        """"Jäta meelde" means *keep this in mind*; Aura heard *remind me later*
        and would have turned a fact about the user into a notification."""
        keep = self.names("Jäta meelde, et ma kasutan VS Code-i.")
        self.assertTrue(keep & self.MEMORY_TOOLS)
        self.assertNotIn("set_reminder", keep)

        later = self.names("Tuleta mulle tunni pärast meelde")
        self.assertIn("set_reminder", later)
        self.assertFalse(later & self.MEMORY_TOOLS)

    def test_the_longer_stem_wins_over_one_inside_it(self):
        """`meelde` sits inside `jäta meelde`. Letting both through is not two
        meanings, it is the same words read less carefully."""
        annotated = language.with_english_hints("jäta meelde, et ma kasutan seda")
        self.assertIn("remember", annotated)
        self.assertNotIn("remind", annotated)

    def test_ordinary_work_is_not_treated_as_a_confession(self):
        """The risk this step was warned against: a memory store filling up with
        half-understood sentences, which are then recalled into context."""
        for request in ("Tee kausta promo uus leht", "Loe fail notes.txt ette",
                        "Kustuta fail vana.txt", "Käivita testid",
                        "Create folder called Mat", "list files"):
            self.assertFalse(self.names(request) & self.MEMORY_TOOLS, request)

    # ------------------------------------------- what the turn is held to

    #: These decide what Aura promised to produce and whether the turn counts as
    #: finished. Measured before the fix: ten ordinary requests differed from
    #: their English translations in thirteen of these fields.
    CONTRACT_PAIRS = (
        ("Ehita mulle veebileht kausta promo", "Build me a website in the promo folder"),
        ("Tee aura_craft projektis uus leht", "Make a new page in the aura_craft project"),
        ("Valideeri projekt ja paranda vead", "Validate the project and fix the issues"),
        ("Loe fail notes.txt ette", "Read the file notes.txt"),
        ("Näita, mis failid tööruumis on", "Show me what files are in the workspace"),
        ("Otsi failidest sõna TODO", "Search the files for the word TODO"),
        ("Võrdle neid kahte faili", "Compare those two files"),
        ("Kontrolli, kas kõik lingid töötavad", "Check whether all links work"),
        ("Loe fail väljaspool tööruumi", "Read a file outside the workspace"),
    )

    WORK_VERBS = ("list", "read", "show", "find", "search", "open", "inspect", "check",
                  "validate", "compare", "look at", "screenshot", "capture", "undo",
                  "run ", "test")

    def contract(self, message):
        if not hasattr(self, "agent"):
            self.agent = AuraAgent(Path(self._workspace.name) / "workspace",
                                   provider=MockProvider())
        annotated = language.with_english_hints(message.casefold())
        base, _paths = AuraAgent._extract_artifact_contract(message)
        return {
            "staged": any(w in annotated for w in ("build", "project", "app", "website")),
            "validation_asked": "validate" in annotated,
            "asks_for_work": (AuraAgent._requires_mutation(message)
                              or any(v in annotated for v in self.WORK_VERBS)
                              or ("?" in message
                                  and self.agent._question_needs_looking(annotated))),
            "external": AuraAgent._targets_external_location(message),
            "base": base,
        }

    def test_estonian_is_held_to_the_same_contract_as_english(self):
        for estonian, english in self.CONTRACT_PAIRS:
            with self.subTest(request=estonian):
                self.assertEqual(self.contract(estonian), self.contract(english))

    def test_a_question_about_a_file_expects_a_look(self):
        """Caught in live use, not by a test.

        "Ja mitu rida on seal esimeses failis?" names a file, asks a question,
        and uses no reading verb — so nothing insisted on a tool, and Aura
        answered "137 rida" about a file of 46 without opening it. The turn was
        recorded as completed.
        """
        for request in ("Ja mitu rida on seal esimeses failis?",
                        "How many lines are in that file?",
                        "Kui suur on style.css?"):
            self.assertTrue(self.contract(request)["asks_for_work"], request)

    def test_an_ordinary_question_is_still_just_a_question(self):
        """The first attempt counted the words *file* and *project*, which made
        "How does my project look these days?" an errand and burned the retry
        budget proving something nobody asked about."""
        for request in ("Kuidas sul läheb?", "Tere!", "Kas sa mäletad mu nime?",
                        "How does my project look these days?"):
            self.assertFalse(self.contract(request)["asks_for_work"], request)

    def test_an_estonian_read_request_still_has_to_actually_read(self):
        """The worst of the thirteen, and the quietest.

        `asks_for_work` false means `action_expected` false, so the gate that
        insists a tool actually ran never fires: Aura could answer "loe see
        fail" without opening it and nothing would object.
        """
        for request in ("Loe fail notes.txt ette", "Näita, mis failid tööruumis on",
                        "Otsi failidest sõna TODO", "Võrdle neid kahte faili"):
            self.assertTrue(self.contract(request)["asks_for_work"], request)

    def test_the_folder_is_read_off_the_right_side_of_the_word(self):
        """Estonian marks the relation with a case ending, not a preposition.

        One word list for both directions made "projektis uus leht" report the
        folder as "uus" — the word that merely came next.
        """
        self.assertEqual(AuraAgent._extract_artifact_contract("Ehita leht kausta promo")[0],
                         "promo")
        self.assertEqual(AuraAgent._extract_artifact_contract("Tee aura_craft projektis uus leht")[0],
                         "aura_craft")
        self.assertEqual(AuraAgent._extract_artifact_contract("Vaata promo kaustas ringi")[0],
                         "promo")

    def test_a_granted_folder_is_recognised_in_estonian_too(self):
        self.assertTrue(AuraAgent._targets_external_location("Loe fail väljaspool tööruumi"))
        self.assertFalse(AuraAgent._targets_external_location("Loe fail notes.txt"))

    # ------------------------------------------------------------- refusals

    def test_estonian_negation_still_withholds_the_tool(self):
        """`ära käivita` means the same as `do not run`, and must cost the same.

        This is why hints attach to their own clause: put them at the end of the
        message instead and the hint escapes the clause meant to suppress it.
        """
        self.assertIn("run_command", self.names("Käivita testid"))
        self.assertNotIn("run_command", self.names("Ära käivita testid"))
        self.assertNotIn("run_command", self.names("Ehita leht, aga ära käivita seda"))
        # The permitted half of that sentence survives the stripped half.
        self.assertIn("create_file", self.names("Ehita leht, aga ära käivita seda"))

    def test_estonian_read_only_wording_is_not_read_as_a_change(self):
        self.assertTrue(AuraAgent._requires_mutation("Tee mulle veebileht"))
        self.assertTrue(AuraAgent._requires_mutation("Kustuta fail vana.txt"))
        self.assertFalse(AuraAgent._requires_mutation("Loe fail notes.txt ette"))
        self.assertFalse(AuraAgent._requires_mutation("Ära muuda midagi, ainult loe"))

    # ------------------------------------------------------------- fallback

    def test_an_unrouted_request_can_still_look_before_answering(self):
        """An empty tool list is the worst answer: Aura cannot even check."""
        for odd in ("hmm", "aiuto per favore", "asdf qwerty"):
            offered = self.names(odd)
            self.assertEqual(offered, set(AuraAgent.FALLBACK_TOOLS), odd)

    def test_the_fallback_can_only_look_never_change(self):
        """Guessing is acceptable for reading and never for writing."""
        self.assertEqual(set(AuraAgent.FALLBACK_TOOLS) & set(toolkit.mutating_names()), set())

    def test_a_greeting_still_gets_nothing(self):
        """"Tere" is not a request to go and look at anything."""
        for greeting in ("Tere!", "hello", "hi there"):
            self.assertEqual(AuraAgent.select_tool_definitions(greeting, "powerful", "deep"), [])


class SearchServiceTests(unittest.TestCase):
    """Aura owns SearXNG's lifecycle without SearXNG living inside Aura.

    The engine is a Flask application with a large dependency tree and cannot be
    vendored into a standard-library-only core. What can be owned is when it
    starts, when it stops, and what it is configured with — and those are the
    parts that decide whether search quietly does not work.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "searxng"
        (self.root / "searx").mkdir(parents=True)
        (self.root / "searx" / "webapp.py").write_text("", encoding="utf-8")
        scripts = self.root / "venv" / ("Scripts" if os.name == "nt" else "bin")
        scripts.mkdir(parents=True)
        self.python = scripts / ("python.exe" if os.name == "nt" else "python")
        self.python.write_text("", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_it_says_what_is_missing_rather_than_just_refusing(self):
        with self.assertRaises(search_service.SearchServiceError) as empty:
            search_service.find_install("")
        self.assertIn("Settings", str(empty.exception))

        missing = Path(self.temp.name) / "nowhere"
        with self.assertRaises(search_service.SearchServiceError) as gone:
            search_service.find_install(missing)
        self.assertIn(str(missing), str(gone.exception))

        wrong = Path(self.temp.name) / "wrong"
        wrong.mkdir()
        with self.assertRaises(search_service.SearchServiceError) as shape:
            search_service.find_install(wrong)
        self.assertIn("webapp.py", str(shape.exception))

        bare = Path(self.temp.name) / "bare"
        (bare / "searx").mkdir(parents=True)
        (bare / "searx" / "webapp.py").write_text("", encoding="utf-8")
        with self.assertRaises(search_service.SearchServiceError) as venv:
            search_service.find_install(bare)
        self.assertIn("virtual environment", str(venv.exception))

    def test_a_complete_install_is_found_with_its_own_interpreter(self):
        install = search_service.find_install(self.root)
        self.assertEqual(install.root, self.root)
        self.assertEqual(install.python, self.python)

    def test_the_settings_file_turns_json_on_and_keeps_it_off_the_network(self):
        """The two things that otherwise go wrong silently.

        Without json in formats SearXNG answers every request with a web page,
        and search simply does not work. Without a loopback bind address a
        search engine started for one person answers the whole network.
        """
        install = search_service.find_install(self.root)
        written = search_service.write_settings(install, 8888, secret="abc")
        text = written.read_text(encoding="utf-8")
        self.assertIn("- json", text)
        self.assertIn('bind_address: "127.0.0.1"', text)
        self.assertIn("port: 8888", text)
        self.assertNotIn("0.0.0.0", text)

    def test_the_settings_file_is_auras_own_not_an_edit_of_theirs(self):
        install = search_service.find_install(self.root)
        theirs = self.root / "searx" / "settings.yml"
        untouched = "# the user's own file"
        theirs.write_text(untouched, encoding="utf-8")
        search_service.write_settings(install, 8888, secret="abc")
        self.assertEqual(theirs.read_text(encoding="utf-8"), untouched)
        self.assertNotEqual(install.settings_path, theirs)

    def test_a_service_someone_else_started_is_read_and_never_stopped(self):
        """Aura stopping a process it did not start would be a nasty surprise."""
        server = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        service = search_service.SearchService()
        status = service.start_native(self.root, port=server.server_port)
        self.assertTrue(status["adopted"])
        self.assertTrue(status["running"])
        service.stop()
        self.assertTrue(search_service.port_answers(server.server_port))

    def test_stopping_something_never_started_is_harmless(self):
        search_service.SearchService().stop()

    def test_a_process_that_dies_at_once_reports_why(self):
        service = search_service.SearchService()
        # A real interpreter that exits immediately, which is what a broken
        # install looks like from the outside.
        install_root = Path(self.temp.name) / "broken"
        (install_root / "searx").mkdir(parents=True)
        (install_root / "searx" / "webapp.py").write_text("", encoding="utf-8")
        scripts = install_root / "venv" / ("Scripts" if os.name == "nt" else "bin")
        scripts.mkdir(parents=True)
        shutil.copy(sys.executable, scripts / Path(sys.executable).name)
        with self.assertRaises(search_service.SearchServiceError) as died:
            service.start_native(install_root, port=_free_port())
        self.assertIn("stopped immediately", str(died.exception))
        self.assertFalse(service.running)

    def test_the_container_settings_turn_json_on(self):
        """The same trap as a native install, in a different place.

        A container carries its own settings.yml, so JSON has to be switched on
        from outside or every search comes back as a web page.
        """
        written = search_service.write_container_settings(Path(self.temp.name) / "cfg", "s3cret")
        body = written.read_text(encoding="utf-8")
        self.assertIn("- json", body)
        self.assertIn('secret_key: "s3cret"', body)
        # bind_address belongs to the native path only: a container binds inside
        # its own namespace, and the published port is what keeps it local.
        self.assertNotIn("bind_address", body)

    def test_docker_is_found_even_when_it_is_not_on_the_path(self):
        """Docker Desktop installs per-user; absent from PATH means nothing."""
        fake = Path(self.temp.name) / "local"
        binary = fake / "Programs" / "DockerDesktop" / "resources" / "bin" / "docker.exe"
        binary.parent.mkdir(parents=True)
        binary.write_text("", encoding="utf-8")
        with unittest.mock.patch.dict(os.environ, {"LOCALAPPDATA": str(fake)}), \
                unittest.mock.patch.object(search_service.shutil, "which", return_value=None):
            self.assertEqual(search_service.find_docker(), str(binary))

    def test_no_docker_at_all_says_what_to_install(self):
        with unittest.mock.patch.dict(os.environ, {"LOCALAPPDATA": self.temp.name,
                                                   "ProgramFiles": self.temp.name}), \
                unittest.mock.patch.object(search_service.shutil, "which", return_value=None), \
                unittest.mock.patch.object(search_service.Path, "is_file", lambda self: False):
            with self.assertRaises(search_service.SearchServiceError) as absent:
                search_service.find_docker()
        self.assertIn("Docker Desktop", str(absent.exception))

    def _fake_docker(self, service, image_present=True):
        """Record the commands rather than running them."""
        service.calls = []

        def run(docker, arguments):
            service.calls.append(list(arguments))
            code = 0 if (arguments[:2] != ["image", "inspect"] or image_present) else 1
            return subprocess.CompletedProcess([docker, *arguments], code, "", "")

        service._run_docker = run
        return service

    def test_the_container_is_published_to_this_machine_only(self):
        """A bare -p would answer the whole network. This is the one flag that
        decides whether a private search engine is private."""
        service = self._fake_docker(search_service.SearchService())
        with unittest.mock.patch.object(search_service, "find_docker", return_value="docker"), \
                unittest.mock.patch.object(search_service, "port_answers", return_value=False), \
                unittest.mock.patch.object(search_service, "START_TIMEOUT_SECONDS", 0.1):
            with self.assertRaises(search_service.SearchServiceError):
                service.start_docker(Path(self.temp.name) / "cfg", port=8899)
        run = next(call for call in service.calls if call[0] == "run")
        published = run[run.index("-p") + 1]
        self.assertTrue(published.startswith("127.0.0.1:"), published)
        self.assertNotIn("0.0.0.0", published)
        self.assertIn(f"{Path(self.temp.name) / 'cfg'}:/etc/searxng:ro", run)
        self.assertEqual(run[-1], search_service.DOCKER_IMAGE)

    def test_a_missing_image_is_not_downloaded_behind_the_users_back(self):
        service = self._fake_docker(search_service.SearchService(), image_present=False)
        with unittest.mock.patch.object(search_service, "find_docker", return_value="docker"), \
                unittest.mock.patch.object(search_service, "port_answers", return_value=False):
            with self.assertRaises(search_service.SearchServiceError) as missing:
                service.start_docker(Path(self.temp.name) / "cfg", port=8899)
        self.assertIn("docker pull", str(missing.exception))
        self.assertFalse(any(call[0] == "run" for call in service.calls))

    def test_stopping_removes_the_container_aura_started(self):
        service = self._fake_docker(search_service.SearchService())
        service.container, service.docker = True, "docker"
        service.stop()
        self.assertIn(["rm", "-f", search_service.CONTAINER_NAME], service.calls)
        self.assertFalse(service.container)

    def test_a_container_someone_else_runs_is_adopted_not_replaced(self):
        service = self._fake_docker(search_service.SearchService())
        with unittest.mock.patch.object(search_service, "port_answers", return_value=True):
            status = service.start_docker(Path(self.temp.name) / "cfg", port=8899)
        self.assertTrue(status["adopted"])
        self.assertEqual(service.calls, [])          # nothing was run or removed

    def test_a_startup_failure_reads_as_something_a_person_can_act_on(self):
        """Two things the live run exposed at once.

        SearXNG colours its own log output, so the raw last line arrived in the
        settings panel wearing terminal escape codes. And the failure that will
        actually happen on Windows — `searx/valkeydb.py` imports the Unix-only
        `pwd` module — reads as a missing package, sending the reader after a
        package that cannot exist there.
        """
        escape = chr(27)
        coloured = f"{escape}[31msomething broke{escape}[0m"
        self.assertEqual(search_service.explain(coloured), "something broke")
        self.assertNotIn(escape, search_service.explain(coloured))

        windows = f"{escape}[1;35mModuleNotFoundError{escape}[0m: No module named 'pwd'"
        explained = search_service.explain(windows)
        self.assertIn("does not run natively on Windows", explained)
        self.assertIn("Docker", explained)
        self.assertNotIn("ModuleNotFoundError", explained)

        # Ordinary square brackets are not escape codes and must survive.
        self.assertEqual(search_service.explain("see [above]"), "see [above]")

    def test_the_model_cannot_start_stop_or_point_the_service(self):
        """A component that launches a program is the last thing the model may aim."""
        with tempfile.TemporaryDirectory() as workspace:
            agent = AuraAgent(Path(workspace) / "w", provider=MockProvider())
            for definition in agent.tool_definitions():
                spec = toolkit.get(definition["function"]["name"])
                fields = set((spec.properties if spec else {}) or {})
                self.assertNotIn("search_install_path", fields)
                self.assertNotIn("install_path", fields)
            names = {item["function"]["name"] for item in agent.tool_definitions()}
            for forbidden in ("start_search_service", "stop_search_service",
                              "set_search_endpoint", "search_service_status"):
                self.assertNotIn(forbidden, names)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class WebSearchTests(unittest.TestCase):
    """Search through a service the user runs, reading snippets and nothing else.

    Aura still holds no search credentials. What changed is that she can read a
    SearXNG on this machine — and the interesting assertions are all about what
    she still cannot do with the results.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.payload = json.dumps({"results": [
            {"title": "Python releases", "url": "https://example.org/python",
             "content": "The latest version and its notes.", "engines": ["duckduckgo"]},
            {"title": "Duplicate", "url": "https://example.org/python",
             "content": "Same link again."},
            {"title": "Not a web link", "url": "ftp://example.org/file"},
            {"title": "Second", "url": "https://example.net/two", "content": "Another one."},
        ]}).encode("utf-8")
        self.content_type = "application/json"
        self.requests = []
        self.serve()

    def serve(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):                       # noqa: N802 - stdlib naming
                outer.requests.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", outer.content_type)
                self.send_header("Content-Length", str(len(outer.payload)))
                self.end_headers()
                self.wfile.write(outer.payload)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f"http://localhost:{self.server.server_port}"

    def tearDown(self):
        try:                                  # one test stops the server itself
            self.server.shutdown()
            self.server.server_close()
        except OSError:
            pass
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def search(self, query="python", count=5):
        return self.agent._execute_tool(
            ToolCall("1", "search_web", {"query": query, "count": count}), None)

    def enable(self):
        self.agent.config.update(search_endpoint=self.endpoint)

    def test_search_is_off_until_the_user_configures_a_service(self):
        result = self.search()
        self.assertFalse(result["ok"])
        self.assertIn("No search service is configured", result["error"])
        self.assertEqual(self.requests, [])

    def test_a_running_service_returns_clean_snippets(self):
        self.enable()
        result = self.search()
        self.assertTrue(result["ok"])
        self.assertEqual([item["url"] for item in result["results"]],
                         ["https://example.org/python", "https://example.net/two"])
        self.assertEqual(result["results"][0]["title"], "Python releases")
        self.assertEqual(result["results"][0]["source"], "duckduckgo")
        self.assertIn("format=json", self.requests[0])

    def test_a_repeated_link_and_a_non_web_link_are_dropped(self):
        self.enable()
        urls = [item["url"] for item in self.search()["results"]]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertNotIn("ftp://example.org/file", urls)

    def test_the_result_says_plainly_that_no_page_was_opened(self):
        self.enable()
        result = self.search()
        self.assertIn("did not open any of these pages", result["note"])


    def test_a_result_page_can_now_be_opened_and_that_is_the_trade(self):
        """This used to be the opposite, and the change is deliberate.

        "Snippets only" was a property of the permission model rather than a
        rule search remembered: a result link could not be fetched because no
        grant covered it. With the allowlist removed at Mat's request it is a
        promise instead — search still opens nothing itself, but nothing stops
        the model following a link it was shown. That is the cost of the trade,
        recorded here rather than left to be discovered.
        """
        self.enable()
        target = self.search()["results"][0]["url"]
        self.assertTrue(target.startswith("http"))
        self.assertNotIn(target, self.agent.fetched_sources)

    def test_the_search_itself_is_recorded_as_a_source(self):
        self.enable()
        self.agent.fetched_sources = []
        self.search()
        self.assertEqual(len(self.agent.fetched_sources), 1)
        self.assertIn("format=json", self.agent.fetched_sources[0])

    def test_html_instead_of_json_names_the_setting_to_change(self):
        """The mistake nearly everyone makes first, so the message must be exact."""
        self.enable()
        self.payload = b"<!doctype html><html><body>results</body></html>"
        self.content_type = "text/html"
        result = self.search()
        self.assertFalse(result["ok"])
        self.assertIn("settings.yml", result["error"])
        self.assertIn("formats", result["error"])

    def test_nothing_listening_says_so_rather_than_failing_obscurely(self):
        port = self.server.server_port
        self.server.shutdown()
        self.server.server_close()
        self.agent.config.update(search_endpoint=f"http://localhost:{port}")
        result = self.search()
        self.assertFalse(result["ok"])
        self.assertIn("No search service answered", result["error"])
        self.assertIn("Start it", result["error"])

    def test_snippets_are_capped_and_stripped_of_control_characters(self):
        self.enable()
        self.payload = json.dumps({"results": [
            {"title": "T" + chr(0) + "itle" + chr(10) + "with" + chr(9) + "junk",
             "url": "https://example.org/a",
             "content": "x" * 900}]}).encode("utf-8")
        first = self.search()["results"][0]
        self.assertLessEqual(len(first["snippet"]), websearch.SNIPPET_CHARS)
        self.assertNotIn(chr(0), first["title"])
        self.assertNotIn(chr(10), first["title"])
        self.assertIn("junk", first["title"])

    def test_an_empty_query_is_refused(self):
        self.enable()
        result = self.search(query="   ")
        self.assertFalse(result["ok"])
        self.assertEqual(self.requests, [])

    def test_a_turn_gets_a_search_budget(self):
        """Live, one question produced twelve near-identical searches.

        A read-only tool costs nothing per call, which is exactly why nothing
        stops it: the model keeps rephrasing until the turn budget is gone.
        """
        self.enable()
        for n in range(AuraAgent.MAX_SEARCHES_PER_TURN):
            self.assertTrue(self.search(query=f"question {n}")["ok"])
        spent = self.search(query="one more thing")
        self.assertFalse(spent["ok"])
        self.assertIn("enough", spent["error"])
        self.assertEqual(len(self.requests), AuraAgent.MAX_SEARCHES_PER_TURN)

    def test_asking_the_same_thing_twice_does_not_search_twice(self):
        self.enable()
        first = self.search(query="Estonia")
        again = self.search(query="  estonia  ")
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(again["repeat"])
        self.assertEqual(again["results"], first["results"])
        self.assertIn("already searched", again["note"])

    def test_a_repeat_does_not_spend_the_budget(self):
        """Otherwise the cheapest call would cost the same as a real one."""
        self.enable()
        for _ in range(AuraAgent.MAX_SEARCHES_PER_TURN + 3):
            self.assertTrue(self.search(query="Estonia")["ok"])
        self.assertEqual(len(self.requests), 1)

    def test_the_budget_is_per_turn_rather_than_forever(self):
        self.enable()
        for n in range(AuraAgent.MAX_SEARCHES_PER_TURN):
            self.search(query=f"question {n}")
        self.assertFalse(self.search(query="blocked")["ok"])
        self.agent.searches_this_turn = {}          # what a new turn does
        self.assertTrue(self.search(query="a fresh question")["ok"])

    def test_the_tool_is_offered_when_the_request_is_about_the_web(self):
        """The live failure this catches, in the words that produced it.

        Search worked and Aura still answered "I cannot browse the web" — true
        of that turn, because keyword routing never offered her the tool, and
        false of her. A capability nothing selects does not exist.
        """
        for request in ("Search the web for Estonia",
                        "look it up online",
                        "what is the latest news about Tallinn",
                        "Otsi veebist Estonia ja ütle, mida katked ütlevad.",
                        "guugelda seda",
                        "vaata internetist järele"):
            names = {item["function"]["name"] for item
                     in AuraAgent.select_tool_definitions(request, "powerful", "balanced")}
            self.assertIn("search_web", names, request)

    def test_the_tool_is_not_offered_for_ordinary_workspace_work(self):
        for request in ("search the workspace for TODO",
                        "read notes.txt and summarise it",
                        "build me a landing page"):
            names = {item["function"]["name"] for item
                     in AuraAgent.select_tool_definitions(request, "powerful", "balanced")}
            self.assertNotIn("search_web", names, request)

    def test_it_is_offered_even_when_no_service_is_configured(self):
        """So the refusal can say what to set up, instead of Aura denying it."""
        names = {item["function"]["name"] for item
                 in AuraAgent.select_tool_definitions("search the web for x", "careful", "fast")}
        self.assertIn("search_web", names)
        self.assertEqual(self.agent.config.data["search_endpoint"], "")

    def test_the_model_cannot_point_search_anywhere(self):
        """There is no tool for the endpoint, only a setting the user edits."""
        names = {item["function"]["name"] for item in self.agent.tool_definitions()}
        self.assertIn("search_web", names)
        for name in names:
            spec = toolkit.get(name)
            fields = set((spec.properties if spec else {}) or {})
            self.assertNotIn("endpoint", fields)
            self.assertNotIn("search_endpoint", fields)


class DefaultCheckTests(unittest.TestCase):
    """Phase 48 was fully built and nothing was ever scheduled. A default set
    only helps if it is quiet, visible, and stays off once switched off."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "workspace"

    def tearDown(self):
        self.temp.cleanup()

    def launch(self):
        """A launch: a fresh agent over the same workspace, as a restart is."""
        agent = AuraAgent(self.workspace, provider=MockProvider())
        bridge = AuraWebBridge(agent=agent, speech=SpeechOutput(enabled=False))
        bridge.scheduler.stop()
        self.addCleanup(bridge.shutdown)
        return bridge

    def watched(self, bridge):
        return {check["name"] for check in bridge.list_scheduled()["available_checks"]
                if check["enabled"]}

    def test_a_fresh_install_starts_watching_the_quiet_defaults(self):
        bridge = self.launch()
        self.assertEqual(self.watched(bridge), set(checks.DEFAULT_CHECKS))
        # Deliberately excluded: a workspace mid-edit is often briefly invalid,
        # and a check that nags during ordinary work is worse than no check.
        self.assertNotIn("validate_workspace", self.watched(bridge))
        for task in bridge.list_scheduled()["scheduled"]:
            self.assertEqual(task["every_minutes"], checks.DEFAULT_EVERY_MINUTES)

    def test_seeding_happens_once_rather_than_every_launch(self):
        self.launch()
        second = self.launch()
        self.assertEqual(len(second.list_scheduled()["scheduled"]), len(checks.DEFAULT_CHECKS))

    def test_a_default_switched_off_does_not_come_back_next_launch(self):
        first = self.launch()
        first.set_check_enabled("broken_links", False)
        self.assertNotIn("broken_links", self.watched(first))
        # Stated as intent rather than as a fixed set: the default list grows,
        # and a test that pins its exact contents fails for the wrong reason.
        after = self.watched(self.launch())
        self.assertNotIn("broken_links", after)
        self.assertEqual(after, set(checks.DEFAULT_CHECKS) - {"broken_links"})

    def test_a_default_added_later_is_offered_without_reviving_the_others(self):
        """A bare flag could only answer "all of them or none".

        So a check added to the defaults after an install either never arrived,
        or arrived dragging back the ones already switched off.
        """
        first = self.launch()
        first.set_check_enabled("broken_links", False)
        first.agent.config.update(
            seeded_checks=[name for name in checks.DEFAULT_CHECKS if name != "failing_streak"])
        watching = self.watched(self.launch())
        self.assertIn("failing_streak", watching)
        self.assertNotIn("broken_links", watching)

    def test_an_install_from_before_names_were_tracked_keeps_its_choices(self):
        """The two that install was given are treated as already offered.

        Without that, switching one off and relaunching would hand it straight
        back — the exact annoyance the seeding guard exists to prevent.
        """
        first = self.launch()
        first.agent.config.update(seeded_checks=[], default_checks_seeded=True)
        first.set_check_enabled("broken_links", False)
        watching = self.watched(self.launch())
        self.assertNotIn("broken_links", watching)
        # The ones added to the defaults since then still arrive.
        self.assertIn("failing_streak", watching)
        self.assertIn("model_producing_nothing", watching)

    def test_a_check_can_be_switched_on_and_off(self):
        bridge = self.launch()
        bridge.set_check_enabled("validate_workspace", True)
        self.assertIn("validate_workspace", self.watched(bridge))
        row = [task for task in bridge.list_scheduled()["scheduled"]
               if task["request"] == "validate_workspace"]
        self.assertEqual(len(row), 1)
        bridge.set_check_enabled("validate_workspace", True)   # twice, not doubled
        self.assertEqual(len([task for task in bridge.list_scheduled()["scheduled"]
                              if task["request"] == "validate_workspace"]), 1)
        bridge.set_check_enabled("validate_workspace", False)
        self.assertNotIn("validate_workspace", self.watched(bridge))

    def test_only_a_known_check_can_be_switched_on(self):
        bridge = self.launch()
        result = bridge.set_check_enabled("read_my_email", True)
        self.assertFalse(result["ok"])
        self.assertEqual(self.watched(bridge), set(checks.DEFAULT_CHECKS))

    def test_the_panel_shows_reminders_alongside_checks(self):
        bridge = self.launch()
        bridge.agent._execute_tool(
            ToolCall("1", "set_reminder", {"text": "stretch", "in_minutes": 30}), None)
        listing = bridge.list_scheduled()
        self.assertEqual([item["request"] for item in listing["reminders"]], ["stretch"])
        self.assertTrue(all(item["next_run"] for item in listing["reminders"]))


class ReminderTests(unittest.TestCase):
    """The first real handler: it proves 48.1 and 48.2 work together."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        agent.config.update(quiet_hours_start="00:00", quiet_hours_end="00:00",
                            seeded_checks=list(checks.DEFAULT_CHECKS))
        self.bridge = AuraWebBridge(agent=agent, speech=SpeechOutput(enabled=False))
        self.bridge.scheduler.stop()          # tick by hand, not by clock

    def tearDown(self):
        self.bridge.shutdown()
        self.temp.cleanup()

    def set_reminder(self, text="stretch", minutes=60, repeat=None):
        arguments = {"text": text, "in_minutes": minutes}
        if repeat:
            arguments["repeat_minutes"] = repeat
        return self.bridge.agent._execute_tool(ToolCall("1", "set_reminder", arguments), None)

    def make_due(self, reminder_id):
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        self.bridge.agent.db._execute(
            "UPDATE scheduled_tasks SET next_run = ? WHERE id = ?", (past, reminder_id))

    def test_a_reminder_is_delivered_as_something_aura_said(self):
        self.assertTrue(self.set_reminder("drink water", 30)["ok"])
        reminder = self.bridge.list_reminders()["reminders"][0]
        self.make_due(reminder["id"])
        self.assertEqual(self.bridge.scheduler.tick(), [reminder["id"]])
        # In the durable conversation, not only on screen: a reminder that
        # arrives while the window is shut is still there when it opens.
        said = self.bridge.agent.memory.data["conversation"][-1]
        self.assertEqual(said["role"], "assistant")
        self.assertIn("drink water", said["text"])
        self.assertTrue(any(event.get("type") == "reply" and event.get("reminder")
                            for event in self.bridge.events))

    def test_a_one_off_reminder_does_not_come_back(self):
        self.set_reminder("once", 5)
        reminder = self.bridge.list_reminders()["reminders"][0]
        self.make_due(reminder["id"])
        self.bridge.scheduler.tick()
        self.assertEqual(self.bridge.list_reminders()["reminders"], [])

    def test_a_repeating_reminder_is_rescheduled(self):
        self.set_reminder("stand up", 5, repeat=60)
        reminder = self.bridge.list_reminders()["reminders"][0]
        self.make_due(reminder["id"])
        self.bridge.scheduler.tick()
        still = self.bridge.list_reminders()["reminders"]
        self.assertEqual(len(still), 1)
        self.assertGreater(still[0]["next_run"], datetime.now(timezone.utc).isoformat())

    def test_quiet_hours_hold_a_reminder_rather_than_dropping_it(self):
        # A window built around this moment, not "00:00-23:59". The old window meant
        # "always", except that the comparison is half-open, so it excluded 23:59
        # itself — and this test failed exactly once, during a run that crossed
        # midnight. `in_quiet_hours` takes a moment precisely so time need not be
        # guessed at; the scheduler reads the clock, so the window moves instead.
        now = datetime.now()
        self.bridge.agent.config.update(
            quiet_hours_start=(now - timedelta(minutes=5)).strftime("%H:%M"),
            quiet_hours_end=(now + timedelta(hours=1)).strftime("%H:%M"))
        self.set_reminder("late", 5)
        reminder = self.bridge.list_reminders()["reminders"][0]
        self.make_due(reminder["id"])
        self.assertEqual(self.bridge.scheduler.tick(), [])
        self.assertEqual(len(self.bridge.list_reminders()["reminders"]), 1)

    def test_the_quiet_window_is_half_open(self):
        """Pinned so the boundary is a decision rather than an accident.

        A window of 00:00-23:59 reads as "always quiet" and is quiet for 1,439 of
        1,440 minutes: the end is exclusive, so 23:59 itself is not. That is correct
        for "quiet until 08:00", and surprising for a window meant to cover the day.
        """
        guard = self.bridge.agent.autonomy
        self.bridge.agent.config.update(quiet_hours_start="00:00", quiet_hours_end="23:59")
        self.assertTrue(guard.in_quiet_hours(datetime(2026, 8, 19, 23, 58)))
        self.assertFalse(guard.in_quiet_hours(datetime(2026, 8, 19, 23, 59)))
        # And the ordinary overnight window still wraps midnight correctly.
        self.bridge.agent.config.update(quiet_hours_start="22:00", quiet_hours_end="08:00")
        for hour in (22, 23, 0, 3, 7):
            self.assertTrue(guard.in_quiet_hours(datetime(2026, 8, 19, hour, 30)), hour)
        self.assertFalse(guard.in_quiet_hours(datetime(2026, 8, 19, 8, 0)))

    def test_the_user_can_cancel_one(self):
        self.set_reminder("never mind", 60)
        reminder = self.bridge.list_reminders()["reminders"][0]
        self.assertTrue(self.bridge.cancel_reminder(reminder["id"])["ok"])
        self.assertEqual(self.bridge.list_reminders()["reminders"], [])
        self.assertFalse(self.bridge.cancel_reminder("nonsense")["ok"])

    def test_the_model_cannot_schedule_anything_but_a_reminder(self):
        """The kind is hard-coded, so this tool cannot become a way to run
        background work that acts."""
        offered = {item["function"]["name"] for item in self.bridge.agent.tool_definitions()}
        self.assertIn("set_reminder", offered)
        for forbidden in ("add_scheduled", "schedule_task", "pause_autonomy"):
            self.assertNotIn(forbidden, offered)
        self.set_reminder("only kind", 10)
        kinds = {task["kind"] for task in self.bridge.agent.db.scheduled_tasks()}
        self.assertEqual(kinds, {"reminder"})

    def test_reminders_are_capped(self):
        for index in range(AuraAgent.MAX_ACTIVE_REMINDERS):
            self.assertTrue(self.set_reminder(f"one {index}", 60)["ok"])
        refused = self.set_reminder("one too many", 60)
        self.assertFalse(refused["ok"])
        self.assertIn("already", refused["error"])

    def test_an_empty_reminder_is_refused(self):
        self.assertFalse(self.set_reminder("   ", 10)["ok"])


class SchedulerTests(unittest.TestCase):
    """The loop only decides *when*; these are the rules it exists to keep."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.database = Database(root / "aura.db")
        self.config = ConfigStore(root / "config.json")
        # Open window, so only the rule under test can refuse.
        self.config.update(quiet_hours_start="00:00", quiet_hours_end="00:00")
        self.log = ActionLog(self.database)
        self.guard = AutonomyGuard(self.config, self.log)
        self.busy = False
        self.scheduler = Scheduler(self.database, self.guard, self.log,
                                   busy=lambda: self.busy, tick_seconds=0.01)
        self.ran = []
        self.scheduler.register("test", lambda task: self.ran.append(task["request"]) or "done")

    def tearDown(self):
        self.scheduler.stop()
        self.temp.cleanup()

    def due(self, request="check something", every_minutes=0):
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        return self.database.add_scheduled("test", request, every_minutes=every_minutes,
                                           next_run=past)

    def test_due_work_runs_and_records_its_outcome(self):
        task = self.due()
        self.assertEqual(self.scheduler.tick(), [task["id"]])
        self.assertEqual(self.ran, ["check something"])
        stored = self.database.scheduled_task(task["id"])
        self.assertEqual(stored["runs"], 1)
        self.assertEqual(stored["last_outcome"], "done")

    def test_nothing_runs_while_the_user_is_waiting(self):
        self.due()
        self.busy = True
        self.assertEqual(self.scheduler.tick(), [])
        self.assertEqual(self.ran, [])

    def test_a_refusal_leaves_the_work_due_instead_of_burning_it(self):
        """Quiet hours must postpone, not silently skip."""
        task = self.due()
        self.config.update(autonomy_paused=True)
        self.assertEqual(self.scheduler.tick(), [])
        self.assertEqual(self.database.scheduled_task(task["id"])["runs"], 0)
        self.config.update(autonomy_paused=False)
        self.assertEqual(self.scheduler.tick(), [task["id"]])

    def test_a_repeating_task_is_rescheduled_and_a_one_off_retires(self):
        once = self.due("once only")
        repeating = self.due("every hour", every_minutes=60)
        self.scheduler.tick()
        self.assertEqual(self.database.scheduled_task(once["id"])["enabled"], 0)
        again = self.database.scheduled_task(repeating["id"])
        self.assertEqual(again["enabled"], 1)
        self.assertGreater(again["next_run"], datetime.now(timezone.utc).isoformat())

    def test_a_failing_task_is_recorded_and_the_loop_survives(self):
        self.scheduler.register("boom", lambda task: (_ for _ in ()).throw(RuntimeError("nope")))
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        broken = self.database.add_scheduled("boom", "explode", next_run=past)
        good = self.due("still runs")
        self.scheduler.tick()
        self.assertIn("nope", self.database.scheduled_task(broken["id"])["last_outcome"])
        self.assertEqual(self.ran, ["still runs"])

    def test_an_unknown_kind_is_disabled_rather_than_retried_forever(self):
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        orphan = self.database.add_scheduled("from-a-newer-build", "?", next_run=past)
        self.scheduler.tick()
        stored = self.database.scheduled_task(orphan["id"])
        self.assertEqual(stored["enabled"], 0)
        self.assertIn("no handler", stored["last_outcome"])

    def test_every_run_spends_the_daily_allowance(self):
        self.config.update(autonomy_daily_runs=1)
        self.due("first")
        self.due("second")
        self.scheduler.tick()
        self.assertEqual(self.ran, ["first"])
        self.assertEqual(self.guard.runs_today(), 1)
        self.assertFalse(self.guard.may_run())

    def test_work_that_is_not_due_yet_is_left_alone(self):
        later = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        self.database.add_scheduled("test", "later", next_run=later)
        self.assertEqual(self.scheduler.tick(), [])

    def test_the_thread_starts_and_stops_cleanly(self):
        self.due("threaded")
        self.scheduler.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not self.ran:
            time.sleep(0.02)
        self.scheduler.stop()
        self.assertEqual(self.ran, ["threaded"])


class AutonomyControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())
        self.bridge = AuraWebBridge(agent=agent, speech=SpeechOutput(enabled=False))

    def tearDown(self):
        self.bridge.shutdown()
        self.temp.cleanup()

    def test_the_interface_can_see_what_is_allowed_and_why_not(self):
        self.bridge.agent.config.update(quiet_hours_start="00:00", quiet_hours_end="23:59")
        status = self.bridge.autonomy_status()["autonomy"]
        self.assertFalse(status["allowed"])
        self.assertIn("quiet hours", status["reason"])
        self.assertIn("quiet_hours", status)
        self.assertEqual(status["runs_today"], 0)

    def test_pausing_and_resuming_round_trip(self):
        self.assertTrue(self.bridge.pause_autonomy(True)["autonomy"]["paused"])
        self.assertTrue(self.bridge.agent.config.data["autonomy_paused"])
        self.assertFalse(self.bridge.pause_autonomy(False)["autonomy"]["paused"])

    def test_emergency_stop_also_cancels_what_is_running(self):
        """Pausing alone would let an in-flight run finish, which is not what
        anybody means by a stop control."""
        self.bridge.agent.cancel_event.clear()
        status = self.bridge.emergency_stop()["autonomy"]
        self.assertTrue(status["paused"])
        self.assertTrue(self.bridge.agent.cancel_event.is_set())
        self.assertTrue(any(event.get("action") == "emergency_stop"
                            for event in self.bridge.agent.log.recent(20)))

    def test_resuming_after_an_emergency_stop_revives_the_loop(self):
        """The stop halts the thread, so resuming has to start it again —
        otherwise the guard says yes while nothing is listening."""
        self.bridge.emergency_stop()
        self.assertFalse(self.bridge.scheduler._thread.is_alive())
        self.bridge.pause_autonomy(False)
        self.assertTrue(self.bridge.scheduler._thread.is_alive())
        self.assertTrue(self.bridge.autonomy_status()["autonomy"]["allowed"]
                        or self.bridge.agent.autonomy.in_quiet_hours())

    def test_the_scheduler_runs_nothing_it_has_not_been_taught(self):
        """It knows exactly the kinds that have been built and nothing else.

        This asserted an empty registry while 48.2 was the whole of the
        scheduler; 48.3 added reminders and 48.4 added checks, so the assertion
        moves with the code each time rather than being dropped.
        """
        self.assertEqual(set(self.bridge.scheduler.handlers), {"reminder", "check"})

    def test_the_bootstrap_carries_the_envelope(self):
        autonomy = self.bridge.get_bootstrap()["autonomy"]
        self.assertIn("paused", autonomy)
        self.assertIn("daily_cap", autonomy)

    def test_no_tool_lets_the_model_widen_its_own_envelope(self):
        offered = {item["function"]["name"] for item in self.bridge.agent.tool_definitions()}
        for forbidden in ("pause_autonomy", "emergency_stop", "autonomy_status"):
            self.assertNotIn(forbidden, offered)


class FilePlanTests(unittest.TestCase):
    """A multi-file build agrees its file list before creating anything."""

    def _agent(self, temporary, replies):
        provider = LMStudioProvider(model="local-model")
        provider.complete = unittest.mock.Mock(side_effect=lambda *a, **k: next(replies))
        return AuraAgent(Path(temporary) / "workspace", provider=provider), provider

    def test_a_multi_file_build_asks_before_writing_anything(self):
        with tempfile.TemporaryDirectory() as temporary:
            replies = iter([
                ProviderReply("site/index.html - the page\nsite/style.css - styling", []),
                ProviderReply("", [ToolCall("1", "create_file",
                                            {"path": "site/index.html", "content": "<h1>Hi</h1>"})]),
                ProviderReply("", [ToolCall("2", "create_file",
                                            {"path": "site/style.css", "content": "h1{}"})]),
                ProviderReply("Both files are in place.", []),
            ])
            agent, provider = self._agent(temporary, replies)
            asked = []
            agent.handle("Build a site in the site folder with index.html and style.css",
                         approve=lambda request: asked.append(request) or True)
            self.assertEqual(asked[0][0], "PLAN")
            self.assertIn("index.html", asked[0][1])
            self.assertIn("style.css", asked[0][1])
            self.assertTrue(agent.sandbox.path("site/index.html").is_file())
            # The plan is handed to the model as already approved.
            instructions = "".join(str(m) for call in provider.complete.call_args_list
                                   for m in call.args[0] if m.get("role") == "system")
            self.assertIn("already approved this exact file plan", instructions)

    def test_declining_the_plan_creates_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            replies = iter([ProviderReply("site/index.html - page\nsite/style.css - css", [])])
            agent, provider = self._agent(temporary, replies)
            answer = agent.handle("Build a site in the site folder with index.html and style.css",
                                  approve=lambda request: False)
            self.assertIn("stopped before creating anything", answer)
            self.assertEqual(agent.sandbox.list_files(), [])
            # One call for the plan, and nothing after it.
            self.assertEqual(provider.complete.call_count, 1)

    def test_a_single_file_request_is_not_worth_a_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            replies = iter([
                ProviderReply("", [ToolCall("1", "create_file",
                                            {"path": "notes.txt", "content": "hi"})]),
                ProviderReply("Created notes.txt.", []),
            ])
            agent, _ = self._agent(temporary, replies)
            asked = []
            agent.handle("Create a file called notes.txt with the text hi",
                         approve=lambda request: asked.append(request) or True)
            self.assertEqual(asked, [])

    def test_a_read_only_request_is_never_planned(self):
        with tempfile.TemporaryDirectory() as temporary:
            replies = iter([ProviderReply("Both files look fine.", [])])
            agent, _ = self._agent(temporary, replies)
            asked = []
            agent.handle("Read index.html and style.css and tell me what they do",
                         approve=lambda request: asked.append(request) or True)
            self.assertEqual(asked, [])

    def test_a_failed_plan_does_not_cost_the_user_their_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            calls = {"n": 0}
            def complete(*args, **kwargs):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("LM Studio timed out while generating a response.")
                return ProviderReply("I built what you asked for.", [])
            provider.complete = unittest.mock.Mock(side_effect=complete)
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            answer = agent.handle("Build a site in the site folder with index.html and style.css",
                                  approve=lambda request: True)
            self.assertIn("built what you asked", answer)


class CompletionGateTests(unittest.TestCase):
    """Each gate can now be asked its verdict on its own, which the single
    344-line function made impossible."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.agent = AuraAgent(Path(self.temp.name) / "workspace", provider=MockProvider())

    def tearDown(self):
        self.temp.cleanup()

    def _turn(self, **values):
        return TurnState(retries_left=AuraAgent.MAX_COMPLETION_RETRIES, **values)

    def test_the_gates_run_in_the_order_the_reply_depends_on(self):
        self.assertEqual([gate.__name__ for gate in AuraAgent.COMPLETION_GATES],
                         [# First of all: a reply made of tool-call markup is not an answer, so
                          # nothing after this should reason about it as though it were.
                          "_gate_tool_markup",
                          "_gate_empty_response", "_gate_artifacts", "_gate_command_claim",
                          "_gate_validation", "_gate_action", "_gate_verification",
                          # Note-only, and last but for the plan: it judges the
                          # answer Mat will actually read, after every gate that
                          # could still send the model back has had its say.
                          "_gate_unread_files",
                          # Bookkeeping from evidence the earlier gates established,
                          # so it runs after them and never asks for a round.
                          "_gate_plan_progress", "_gate_plan"])

    def test_writing_the_plan_can_never_cost_a_retry(self):
        """It is last, and it returns PASS on every path that matters.

        The first version asked the model for another round, which took a
        retry from the gates that decide whether the work was right — and an
        existing test that pins the number of model rounds caught it.
        """
        self.assertEqual(AuraAgent.COMPLETION_GATES[-1].__name__, "_gate_plan")
        source = (Path(__file__).parents[1] / "aura" / "agent.py").read_text(encoding="utf-8")
        gate = source[source.index("def _gate_plan"):source.index("def _describe_turn")]
        self.assertNotIn("instruction=", gate)
        self.assertNotIn("notice=", gate)

    def test_an_empty_answer_asks_again_and_stops_the_later_gates(self):
        turn = self._turn(expected_paths=["missing.txt"])
        verdict = self.agent._gate_empty_response(turn, ProviderReply("", []))
        self.assertTrue(verdict.wants_retry)
        self.assertIn("completely empty", verdict.instruction)
        # Later gates keep quiet rather than judging a turn that said nothing.
        self.assertEqual(self.agent._gate_artifacts(turn, ProviderReply("", [])), PASS)
        self.assertEqual(self.agent._gate_action(turn, ProviderReply("", [])), PASS)

    def test_a_missing_deliverable_asks_for_it_by_tool_name(self):
        turn = self._turn(expected_paths=["notes.txt"])
        verdict = self.agent._gate_artifacts(turn, ProviderReply("All done!", []))
        self.assertTrue(verdict.wants_retry)
        self.assertIn("create_file", verdict.instruction)
        self.assertIn("notes.txt", verdict.instruction)

    def test_a_spent_budget_turns_a_retry_into_an_honest_note(self):
        turn = self._turn(expected_paths=["notes.txt"])
        turn.retries_left = 0
        verdict = self.agent._gate_artifacts(turn, ProviderReply("All done!", []))
        self.assertFalse(verdict.wants_retry)
        self.assertIn("requested but not found", verdict.note)

    def test_work_in_a_granted_folder_owes_no_workspace_file(self):
        turn = self._turn(expected_paths=["report.txt"], external_activity=True)
        self.assertEqual(self.agent._gate_artifacts(turn, ProviderReply("Done.", [])), PASS)

    def test_one_budget_is_shared_by_every_gate(self):
        turn = self._turn()
        self.assertTrue(turn.spend_retry())
        self.assertTrue(turn.spend_retry())
        self.assertTrue(turn.spend_retry())
        self.assertFalse(turn.spend_retry())
        self.assertEqual(turn.retries_left, 0)

    def test_a_note_is_never_recorded_twice(self):
        turn = self._turn()
        turn.record_unconfirmed("same thing")
        turn.record_unconfirmed("same thing")
        self.assertEqual(turn.unconfirmed, ["same thing"])


class SingleCommandPathTests(unittest.TestCase):
    """One capability, one implementation — the phrasing must not pick which."""

    def test_a_real_model_sees_phrase_commands_as_ordinary_requests(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            replies = iter([
                ProviderReply("", [ToolCall("1", "list_files", {"path": "."})]),
                ProviderReply("The workspace is empty.", []),
            ])
            provider.complete = unittest.mock.Mock(side_effect=lambda *a, **k: next(replies))
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            answer = agent.handle("list files")
            # It went through the tool loop rather than the old shortcut, which
            # answered with a bullet list and never consulted the model.
            self.assertTrue(provider.complete.called)
            self.assertIn("workspace is empty", answer)
            self.assertNotIn("Workspace files:", answer)

    def test_remembering_a_name_goes_through_the_memory_tool(self):
        with tempfile.TemporaryDirectory() as temporary:
            provider = LMStudioProvider(model="local-model")
            replies = iter([
                ProviderReply("", [ToolCall("1", "remember_name", {"name": "Maya"})]),
                ProviderReply("I will remember that.", []),
            ])
            provider.complete = unittest.mock.Mock(side_effect=lambda *a, **k: next(replies))
            agent = AuraAgent(Path(temporary) / "workspace", provider=provider)
            agent.handle("remember my name is Maya")
            self.assertEqual(agent.memory.data["name"], "Maya")

    def test_a_provider_without_tools_still_answers_deterministically(self):
        """MockProvider cannot call tools, so the phrase matches remain its only
        route — that is why they moved here instead of being deleted."""
        with tempfile.TemporaryDirectory() as temporary:
            agent = AuraAgent(Path(temporary) / "workspace", provider=MockProvider())
            agent.sandbox.write_file("note.txt", "hi")
            self.assertIn("note.txt", agent.handle("list files"))
            agent.handle("remember my name is Maya")
            self.assertEqual(agent.memory.data["name"], "Maya")


class ToolRegistryTests(unittest.TestCase):
    """Declaration and dispatch used to be two lists that could drift apart."""

    def test_every_offered_tool_can_actually_be_dispatched(self):
        for definition in AuraAgent.tool_definitions():
            name = definition["function"]["name"]
            self.assertTrue(toolkit.get(name) or services.get(name),
                            f"{name} is offered to the model but nothing runs it")

    def test_every_registered_tool_is_offered(self):
        offered = {item["function"]["name"] for item in AuraAgent.tool_definitions()}
        self.assertEqual(set(toolkit.REGISTRY) - offered, set())

    def test_a_tool_cannot_be_declared_twice(self):
        with self.assertRaises(ValueError):
            toolkit.tool("list_files", "duplicate")(lambda *args: {})

    def test_mutating_tools_come_from_the_tools_themselves(self):
        self.assertEqual(AuraAgent.MUTATING_TOOL_NAMES,
                         toolkit.mutating_names() | {"import_file"})
        for name in ("write_file", "apply_edits", "safe_delete_file"):
            self.assertTrue(toolkit.get(name).mutating, name)
        for name in ("read_file", "list_files", "validate_project"):
            self.assertFalse(toolkit.get(name).mutating, name)

    def test_an_unknown_tool_is_still_an_ordinary_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            agent = AuraAgent(Path(temporary) / "workspace", provider=MockProvider())
            result = agent._execute_tool(ToolCall("1", "no_such_tool", {}), None)
        self.assertFalse(result["ok"])
        self.assertIn("unknown tool", result["error"])






class PackagingTests(unittest.TestCase):
    """A package must contain a runnable Aura and none of anyone's data."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "checkout"
        (self.root / "aura" / "web").mkdir(parents=True)
        (self.root / "aura" / "__init__.py").write_text('__version__ = "9.9.9"\n',
                                                        encoding="utf-8")
        for name in ("agent.py", "store.py"):
            (self.root / "aura" / name).write_text("# module\n", encoding="utf-8")
        for name in ("index.html", "app.js", "styles.css", "avatar-face.js"):
            (self.root / "aura" / "web" / name).write_text("asset", encoding="utf-8")
        (self.root / "aura_app.py").write_text("# launcher\n", encoding="utf-8")
        (self.root / "README.md").write_text("# Aura\n", encoding="utf-8")

        # The things that must never be shipped, in the places they really live.
        workspace = self.root / "aura-workspace" / ".aura"
        workspace.mkdir(parents=True)
        (workspace / "memory.json").write_text('{"name": "Maya"}', encoding="utf-8")
        (workspace / "aura.db").write_bytes(b"SQLite format 3\x00")
        (self.root / "aura-workspace" / "secret-plan.txt").write_text("mine",
                                                                     encoding="utf-8")
        (self.root / "aura-runtime.log").write_text("startup", encoding="utf-8")
        (self.root / "aura" / "__pycache__").mkdir()
        (self.root / "aura" / "__pycache__" / "agent.cpython-313.pyc").write_bytes(b"\x00")

    def tearDown(self):
        self.temp.cleanup()

    def test_a_package_carries_the_app_and_nothing_private(self):
        target = Path(self.temp.name) / "aura-test.zip"
        package.build(self.root, target)
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
        shipped = {Path(name).name for name in names}
        self.assertIn("aura_app.py", shipped)
        self.assertIn("index.html", shipped)
        self.assertIn("agent.py", shipped)
        for private in ("memory.json", "aura.db", "secret-plan.txt",
                        "aura-runtime.log", "agent.cpython-313.pyc"):
            self.assertNotIn(private, shipped)
        self.assertFalse(any("aura-workspace" in name for name in names))

    def test_packaging_stops_rather_than_ship_an_incomplete_interface(self):
        (self.root / "aura" / "web" / "app.js").unlink()
        with self.assertRaises(RuntimeError):
            package.build(self.root, Path(self.temp.name) / "broken.zip")

    def test_the_running_version_is_reported_where_it_can_be_checked(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        agent = AuraAgent(Path(temp.name) / "workspace", provider=MockProvider())
        bridge = AuraWebBridge(agent=agent, speech=SpeechOutput(enabled=False))
        self.addCleanup(bridge.shutdown)
        self.assertEqual(bridge.get_bootstrap()["version"], aura_version)
        written = bridge.export_diagnostics()
        self.assertIn(f"- Aura: {aura_version}",
                      (agent.sandbox.root / written["path"]).read_text(encoding="utf-8"))


class LauncherTests(unittest.TestCase):
    def test_an_old_python_is_explained_instead_of_crashing(self):
        """`pyw -3` can start an older interpreter than Aura was installed with."""
        with patch.object(aura_app.sys, "version_info", (3, 9, 7)):
            problem = aura_app.check_python()
        self.assertIsNotNone(problem)
        self.assertIn("3.10", problem)
        self.assertIn("3.9.7", problem)
        self.assertIsNone(aura_app.check_python())


class _Block:
    """Stand-in for a response content block, which the SDK returns as objects."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


class _Message:
    def __init__(self, content, stop_reason, input_tokens=0, output_tokens=0):
        self.content = content
        self.stop_reason = stop_reason
        self.usage = _Block(input_tokens=input_tokens, output_tokens=output_tokens)


class CloudProviderTests(unittest.TestCase):
    """Aura's interior speaks one dialect; the translation lives at the edge.

    Every test here runs without a network, a key, or the anthropic package,
    because the part that can be wrong is the translation, not the transport.
    """

    def setUp(self):
        self.provider = AnthropicProvider(api_key="not-used-offline")

    # ------------------------------------------------------------- outbound

    def test_system_messages_leave_the_conversation_and_become_a_field(self):
        system, messages = AnthropicProvider.to_anthropic([
            {"role": "system", "content": "You are Aura."},
            {"role": "system", "content": "Reply in Estonian."},
            {"role": "user", "content": "Tere"},
        ])
        self.assertEqual(system, "You are Aura.\n\nReply in Estonian.")
        self.assertEqual([m["role"] for m in messages], ["user"])

    def test_a_tool_result_becomes_a_block_in_a_user_message(self):
        """It is a role in Aura and a block here, which is the difference most
        likely to be got wrong once and then never noticed."""
        _system, messages = AnthropicProvider.to_anthropic([
            {"role": "user", "content": "Read it"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "t1", "type": "function",
                 "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}]},
            {"role": "tool", "tool_call_id": "t1", "content": "hello"},
        ])
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "user"])
        call = messages[1]["content"][0]
        self.assertEqual(call["type"], "tool_use")
        self.assertEqual(call["input"], {"path": "a.txt"})
        result = messages[2]["content"][0]
        self.assertEqual(result["tool_use_id"], "t1")

    def test_results_for_one_turn_arrive_together(self):
        """Splitting parallel results across messages teaches the model to stop
        asking for several tools at once."""
        _system, messages = AnthropicProvider.to_anthropic([
            {"role": "user", "content": "Read both"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "a", "content": "one"},
            {"role": "tool", "tool_call_id": "b", "content": "two"},
        ])
        self.assertEqual(len(messages), 3)
        self.assertEqual(len(messages[-1]["content"]), 2)

    def test_unreadable_arguments_do_not_lose_the_call(self):
        _system, messages = AnthropicProvider.to_anthropic([
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "t1", "type": "function",
                 "function": {"name": "read_file", "arguments": "{not json"}}]},
        ])
        self.assertEqual(messages[1]["content"][0]["input"], {})

    def test_an_empty_assistant_turn_is_dropped_rather_than_sent(self):
        """A message with no text and no calls has no valid form here."""
        _system, messages = AnthropicProvider.to_anthropic([
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": None},
        ])
        self.assertEqual([m["role"] for m in messages], ["user"])

    def test_an_attached_image_survives_the_translation(self):
        _system, messages = AnthropicProvider.to_anthropic([
            {"role": "user", "content": [
                {"type": "text", "text": "What is this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
            ]}])
        kinds = [block["type"] for block in messages[0]["content"]]
        self.assertEqual(kinds, ["text", "image"])
        source = messages[0]["content"][1]["source"]
        self.assertEqual(source["media_type"], "image/png")
        self.assertEqual(source["data"], "QUJD")

    def test_tools_keep_their_schema_one_level_up(self):
        converted = AnthropicProvider.to_anthropic_tools([
            {"type": "function", "function": {
                "name": "read_file", "description": "Read a file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}])
        self.assertEqual(converted[0]["name"], "read_file")
        self.assertIn("path", converted[0]["input_schema"]["properties"])
        self.assertNotIn("function", converted[0])

    # -------------------------------------------------------------- inbound

    def test_text_and_calls_come_back_in_auras_own_shape(self):
        reply = AnthropicProvider.from_anthropic(_Message(
            [_Block(type="text", text="Reading it now."),
             _Block(type="tool_use", id="t1", name="read_file", input={"path": "a.txt"})],
            "tool_use", input_tokens=120, output_tokens=8))
        self.assertEqual(reply.content, "Reading it now.")
        self.assertEqual(reply.tool_calls[0].name, "read_file")
        self.assertEqual(reply.tool_calls[0].arguments, {"path": "a.txt"})
        self.assertEqual(reply.finish_reason, "tool_calls")
        self.assertEqual((reply.prompt_tokens, reply.completion_tokens), (120, 8))

    def test_running_out_of_room_is_reported_as_length(self):
        """The empty-response instrument reads this word to tell a model that
        ran out of budget from one that chose to say nothing."""
        reply = AnthropicProvider.from_anthropic(_Message(
            [_Block(type="text", text="half a th")], "max_tokens"))
        self.assertEqual(reply.finish_reason, "length")

    def test_a_declined_request_says_so_instead_of_looking_empty(self):
        reply = AnthropicProvider.from_anthropic(_Message([], "refusal"))
        self.assertEqual(reply.finish_reason, "refusal")
        with self.assertRaises(ProviderError) as caught:
            provider = AnthropicProvider(api_key="k")
            provider.complete = lambda *a, **k: reply
            provider.reply("hello", ProviderContext(None, {}, []))
        self.assertIn("declined", str(caught.exception))

    def test_thinking_blocks_are_not_mistaken_for_the_answer(self):
        reply = AnthropicProvider.from_anthropic(_Message(
            [_Block(type="thinking", thinking=""),
             _Block(type="text", text="Done.")], "end_turn"))
        self.assertEqual(reply.content, "Done.")
        self.assertEqual(reply.finish_reason, "stop")

    # ---------------------------------------------------------------- setup

    def test_the_prompt_contract_is_shared_not_copied(self):
        """A change to who Aura is must not apply to one provider only."""
        self.assertIs(AnthropicProvider.SYSTEM_PROMPT, LMStudioProvider.SYSTEM_PROMPT)
        self.assertIs(AnthropicProvider.LANGUAGE_RULE, LMStudioProvider.LANGUAGE_RULE)
        messages = self.provider.start_messages("Palun vaata see fail üle",
                                                ProviderContext(None, {}, []))
        system, _converted = AnthropicProvider.to_anthropic(messages)
        self.assertIn("You are Aura", system)
        # The rule that stopped Aura answering in Finnish has to reach this
        # provider too, and it does so by being inherited rather than copied.
        self.assertIn("Estonian", system)

    def test_a_missing_key_is_said_plainly_and_never_guessed_at(self):
        provider = AnthropicProvider(api_key="")
        with unittest.mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}, clear=False):
            provider.api_key = ""
            try:
                import anthropic  # noqa: F401
            except ImportError:
                expected = "anthropic package"
            else:
                expected = "API key"
            with self.assertRaises(ProviderError) as caught:
                provider._client()
            self.assertIn(expected.split()[0], str(caught.exception))

    def test_the_choice_survives_a_restart(self):
        """Saving the setting is one path; being built from it on the next
        launch is the other, and only the second one matters tomorrow."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        workspace = Path(temp.name) / "workspace"
        meta = workspace / ".aura"
        meta.mkdir(parents=True)
        (meta / "config.json").write_text(json.dumps(
            {"provider": "claude", "anthropic_api_key": "sk-ant-stored",
             "cloud_model": "claude-sonnet-5"}), encoding="utf-8")
        agent = AuraAgent(workspace)
        self.addCleanup(agent.db.close)
        self.assertIsInstance(agent.provider, AnthropicProvider)
        self.assertEqual(agent.provider.selected_model(), "claude-sonnet-5")

    def test_nothing_switches_provider_on_its_own(self):
        """An unreachable local model must never become a reason to send the
        same conversation to Anthropic instead."""
        source = (Path(__file__).parents[1] / "aura" / "agent.py").read_text(encoding="utf-8")
        build = source[source.index("def _build_provider"):source.index("def _bind_provider_recovery")]
        self.assertNotIn("except", build)
        # One return per provider and nothing else, so no rescue path can hide.
        self.assertEqual(build.count("return"), 3)

    def test_what_anthropic_said_is_repeated_not_replaced(self):
        """The first real request returned a 400 saying the account was out of
        credit, and this reported "could not reach api.anthropic.com" — which
        was plainly untrue and hid the one sentence that explained the problem."""
        class _Refused(Exception):
            status_code = 400
            message = "Your credit balance is too low to access the Anthropic API."

        said = AnthropicProvider._explain(_Refused())
        self.assertIn("credit balance", said)
        self.assertNotIn("Could not reach", said)

    def test_only_the_readable_sentence_is_shown(self):
        """`.message` carries the whole raw JSON body, which is accurate and
        unreadable. Measured against the real 400 this returned."""
        class _Refused(Exception):
            status_code = 400
            body = {"type": "error", "error": {
                "type": "invalid_request_error",
                "message": "Your credit balance is too low to access the Anthropic API."}}
            message = ("Error code: 400 - {'type': 'error', 'error': {'type': "
                       "'invalid_request_error', 'message': 'Your credit balance is too "
                       "low to access the Anthropic API.'}, 'request_id': 'req_011'}")

        said = AnthropicProvider._explain(_Refused())
        self.assertEqual(said, "Anthropic refused the request: Your credit balance is "
                               "too low to access the Anthropic API.")
        self.assertNotIn("request_id", said)
        # The same sentence has to be recoverable when only the raw text exists.
        raw = type("_Raw", (Exception,), {"status_code": 400,
                                          "message": _Refused.message})()
        self.assertIn("credit balance", AnthropicProvider._explain(raw))
        self.assertNotIn("request_id", AnthropicProvider._explain(raw))

    def test_a_key_problem_is_still_named_as_one(self):
        class _Rejected(Exception):
            status_code = 401
            message = "invalid x-api-key"

        self.assertIn("API key", AnthropicProvider._explain(_Rejected()))

    def test_each_provider_says_where_it_is(self):
        """"Local - private" becomes untrue the moment a remote one is on, and
        the provider is the thing that knows — not the settings file."""
        self.assertEqual(MockProvider().describe_location(), "Local • private")
        self.assertFalse(MockProvider().is_remote())
        self.assertIn("api.anthropic.com", self.provider.describe_location())
        self.assertTrue(self.provider.is_remote())
        gpt = OpenAIProvider(api_key="sk-test")
        self.assertIn("api.openai.com", gpt.describe_location())
        self.assertTrue(gpt.is_remote())

    # ------------------------------------------------------------------ GPT

    def test_gpt_needs_no_translation_because_the_protocol_is_shared(self):
        """LM Studio serves OpenAI's own API, so the existing client works once
        it is pointed at the real address — no new dependency, no new dialect."""
        gpt = OpenAIProvider(api_key="sk-test", model="gpt-5")
        self.assertIsInstance(gpt, OpenAICompatibleProvider)
        self.assertNotIsInstance(gpt, LMStudioProvider)
        self.assertEqual(gpt.base_url, "https://api.openai.com/v1")
        self.assertEqual(gpt.AUTH_TOKEN, "sk-test")

    def test_the_address_is_a_setting_so_other_services_work(self):
        """OpenAI's chat API is spoken by a good many services. Same protocol,
        same client, different host and key — that is the whole change."""
        elsewhere = OpenAIProvider(api_key="k", base_url="https://api.groq.com/openai/v1")
        self.assertEqual(elsewhere.base_url, "https://api.groq.com/openai/v1")
        # Naming OpenAI in an error raised by somebody else's server would send
        # the user to the wrong place to fix it.
        self.assertEqual(elsewhere.SERVICE, "api.groq.com")
        self.assertNotIn("OpenAI", elsewhere.describe_location())
        self.assertIn("api.groq.com", elsewhere.describe_location())

    def test_remote_is_decided_by_the_address_not_the_class(self):
        """Pointed at a model server on this machine, the cloud provider must
        stop warning about a journey that is not happening — and pointed
        outward, the local one must stop claiming privacy."""
        here = OpenAIProvider(api_key="k", base_url="http://127.0.0.1:11434/v1")
        self.assertFalse(here.is_remote())
        self.assertEqual(here.describe_location(), "Local • private")
        away = LMStudioProvider(base_url="http://192.168.1.50:1234/v1")
        self.assertTrue(away.is_remote())
        self.assertIn("192.168.1.50", away.describe_location())
        self.assertNotIn("private", away.describe_location())

    def test_a_bad_address_is_refused_when_it_is_typed(self):
        with self.assertRaises(ValueError):
            OpenAIProvider(api_key="k", base_url="not-a-url")

    def test_a_reasoning_model_is_accommodated_from_what_it_said(self):
        """These models spell the output limit differently and refuse a
        temperature. Which models those are changes over time, so the fix is
        learned from the refusal rather than from a list of names kept here."""
        gpt = OpenAIProvider(api_key="sk-test")
        payload = {"model": "x", "temperature": 0.4, "max_tokens": 100}
        self.assertEqual(gpt._tune_payload(dict(payload)), payload)
        self.assertTrue(gpt._learn_from_refusal(
            "OpenAI returned HTTP 400. Unsupported parameter: 'max_tokens' is not "
            "supported with this model. Use 'max_completion_tokens' instead."))
        self.assertTrue(gpt._learn_from_refusal(
            "OpenAI returned HTTP 400. Unsupported value: 'temperature' does not "
            "support 0.4 with this model."))
        self.assertEqual(gpt._tune_payload(dict(payload)),
                         {"model": "x", "max_completion_tokens": 100})
        # Anything the server did not name specifically stays a real error.
        self.assertFalse(gpt._learn_from_refusal("OpenAI returned HTTP 500."))

    def test_a_refusal_is_shown_as_a_sentence_not_a_json_body(self):
        """Measured against the real 401 this returned: the useful sentence was
        buried under braces and a hundred masking asterisks."""
        body = json.dumps({"error": {
            "message": ("Incorrect API key provided: sk-proj-" + "*" * 140 +
                        "0LoA. You can find your API key at "
                        "https://platform.openai.com/account/api-keys."),
            "type": "invalid_request_error", "code": "invalid_api_key"}})
        said = OpenAICompatibleProvider._readable(body)
        self.assertTrue(said.startswith("Incorrect API key provided:"))
        self.assertNotIn("*", said)
        self.assertNotIn("invalid_request_error", said)
        # A body that is not JSON at all is still worth showing.
        self.assertEqual(OpenAICompatibleProvider._readable("plain trouble"), "plain trouble")
        self.assertEqual(OpenAICompatibleProvider._readable(""), "")

    def test_the_model_list_is_asked_for_not_hardcoded(self):
        """A name kept in this file goes stale and then fails at the worst
        moment; the key knows what it can actually use."""
        gpt = OpenAIProvider(api_key="sk-test")
        gpt._request = lambda path, payload=None: {"data": [
            {"id": "gpt-5"}, {"id": "text-embedding-3-large"}, {"id": "whisper-1"},
            {"id": "dall-e-3"}, {"id": "gpt-5-mini"}, {"id": "tts-1"}]}
        self.assertEqual(gpt.available_models(), ["gpt-5", "gpt-5-mini"])

    def test_gpt_without_a_key_says_so_before_reaching_out(self):
        gpt = OpenAIProvider(api_key="")
        with unittest.mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            gpt.AUTH_TOKEN = ""
            with self.assertRaises(ProviderError) as caught:
                gpt.complete([{"role": "user", "content": "hi"}])
        self.assertIn("API key", str(caught.exception))

if __name__ == "__main__":
    unittest.main()
