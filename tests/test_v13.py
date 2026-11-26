# -*- coding: utf-8 -*-
"""0.1.13：把 CLI 的原始输出按行转到界面（“原始输出”面板）。"""
import io
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import core


def make_turn():
    return core.Turn(argv=["doubao"], label="send")


class EmitLines(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.runner = core.Runner(
            dict(core.DEFAULT_CONFIG), lambda kind, payload: self.events.append((kind, payload))
        )

    def texts(self):
        return [payload["text"] for kind, payload in self.events if kind == "log"]

    def test_split_lines(self):
        turn = make_turn()
        self.runner._emit_log(turn, "第一行\n第二行\n半")
        self.assertEqual(self.texts(), ["第一行", "第二行"])
        self.runner._emit_log(turn, "行尾\n")
        self.assertEqual(self.texts()[-1], "半行尾")

    def test_skip_json_and_rules(self):
        turn = make_turn()
        self.runner._emit_log(turn, '{"platform": "headless", "runId": "1"}\n-----------\n\n真正日志\n')
        self.assertEqual(self.texts(), ["真正日志"])

    def test_ansi(self):
        turn = make_turn()
        self.runner._emit_log(turn, "\x1b[32m✓ 已批准\x1b[0m\n")
        self.assertEqual(self.texts(), ["✓ 已批准"])

    def test_long_line_clip(self):
        turn = make_turn()
        self.runner._emit_log(turn, "x" * 900 + "\n")
        self.assertEqual(len(self.texts()[0]), 400)

    def test_buffer_guard(self):
        turn = make_turn()
        for _ in range(3):
            self.runner._emit_log(turn, "y" * 1500)
        self.assertLessEqual(len(turn.log_buf), 4000)


class SessionLogs(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.old_sessions = core.SESSION_DIR
        core.SESSION_DIR = os.path.join(self.root, "gui-sessions")
        self.events = []
        config = dict(core.DEFAULT_CONFIG)
        config.update(
            {"bin": "mock", "binArgs": [], "profile": "", "workspace": self.root,
             "mode": "work", "permission": "ask_risky"}
        )
        self.session = core.Session(config, lambda event: self.events.append(event))

    def tearDown(self):
        core.SESSION_DIR = self.old_sessions

    def log_events(self):
        return [event for event in self.events if event.get("type") == "log"]

    def test_event_shape(self):
        self.session._on_runner("log", {"text": "tool: bash ls"})
        self.assertEqual(self.session.logs[-1], "tool: bash ls")
        event = self.log_events()[-1]
        self.assertEqual(event["data"]["text"], "tool: bash ls")
        self.assertEqual(event["data"]["seq"], 1)

    def test_ring_limit(self):
        for index in range(500):
            self.session._on_runner("log", {"text": "line %d" % index})
        self.assertLessEqual(len(self.session.logs), 400)
        self.assertEqual(self.session.logs[-1], "line 499")
        self.assertEqual(self.session.log_seq, 500)

    def test_snapshot(self):
        self.session._on_runner("log", {"text": "a"})
        snap = self.session.snapshot()
        self.assertEqual(snap["logs"], ["a"])
        self.assertEqual(snap["logSeq"], 1)
        self.assertEqual(snap["version"], "0.1.13")

    def test_empty_text_ignored(self):
        before = self.session.log_seq
        self.session._on_runner("log", {"text": ""})
        self.session._on_runner("log", None)
        self.assertEqual(self.session.log_seq, before)


class SourceWiring(unittest.TestCase):
    def test_clear_points(self):
        source = io.open(os.path.join(ROOT, "core.py"), encoding="utf-8").read()
        self.assertGreaterEqual(source.count("self.logs = []"), 3)
        self.assertIn("def _emit_log", source)
        self.assertIn('self.on_event("log"', source)
        self.assertIn('"logSeq": self.log_seq', source)


class Frontend(unittest.TestCase):
    def test_html(self):
        html = io.open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
        self.assertIn('id="trace-raw"', html)
        self.assertIn('id="trace-raw-count"', html)

    def test_js(self):
        script = io.open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
        self.assertIn('payload.type === "log"', script)
        self.assertIn("state.logs.push", script)
        self.assertIn("state.logs.slice(-200)", script)

    def test_css(self):
        css = io.open(os.path.join(ROOT, "web", "style.css"), encoding="utf-8").read()
        self.assertIn(".trace-raw", css)


if __name__ == "__main__":
    unittest.main(verbosity=0)
