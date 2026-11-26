# -*- coding: utf-8 -*-
"""0.1.12 新增能力测试：过程面板、请求级参数、receipt 解析、会话分页。"""
import io
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import core


def make_runner(**kwargs):
    config = dict(core.DEFAULT_CONFIG)
    config.update(
        {
            "bin": "doubao",
            "binArgs": [],
            "profile": "",
            "workspace": "/tmp/w",
            "mode": "work",
            "permission": "ask_risky",
        }
    )
    config.update(kwargs)
    return core.Runner(config, lambda *args: None)


class RequestFlags(unittest.TestCase):
    def test_all_on(self):
        runner = make_runner(
            project="proj-1",
            enterprise=True,
            mcp=["conn-1", "conn-2"],
            skills=False,
            expect_json=True,
            reply_schema="/tmp/schema.json",
            attach=["/tmp/a.txt", "/tmp/b.png"],
        )
        flags = runner._request_flags()
        for expected in ("--project", "proj-1", "--enterprise-knowledge", "--mcp", "conn-1", "conn-2",
                         "--no-skills", "--expect-json", "--reply-schema", "/tmp/schema.json",
                         "--attach", "/tmp/a.txt", "/tmp/b.png"):
            self.assertIn(expected, flags)

    def test_attach_repeats(self):
        runner = make_runner(attach=["/a", "/b"])
        flags = runner._request_flags()
        self.assertEqual(flags.count("--attach"), 2)

    def test_chat_drops_work_only(self):
        runner = make_runner(mode="chat", project="p", enterprise=True, mcp=["m"], skills=False)
        self.assertEqual(runner._request_flags(), [])

    def test_chat_keeps_attach(self):
        runner = make_runner(mode="chat", attach=["/tmp/a.pdf"], skills=False)
        self.assertEqual(runner._request_flags(), ["--attach", "/tmp/a.pdf"])

    def test_default_is_empty(self):
        self.assertEqual(make_runner()._request_flags(), [])

    def test_create_carries_project(self):
        argv = make_runner(project="p1").build_create("hi")
        self.assertIn("--project", argv)
        self.assertIn("p1", argv)

    def test_send_carries_attach(self):
        argv = make_runner(attach=["/tmp/x.pdf"]).build_send("123456789012", "hi")
        self.assertIn("--attach", argv)


class Receipt(unittest.TestCase):
    def test_parse_fields(self):
        data = {
            "conversationId": "100",
            "runId": "200",
            "thinking": "在想步骤",
            "answer": "最终答复",
            "handoffs": [
                {"taskId": "111111111", "appendScene": 2, "completed": True},
                {"taskId": "222222222", "completed": False},
            ],
            "completed": True,
        }
        out = core.parse_receipt(data)
        self.assertEqual(out["thinking"], "在想步骤")
        self.assertEqual(out["answer"], "最终答复")
        self.assertEqual(len(out["steps"]), 2)
        self.assertTrue(out["steps"][0]["completed"])
        self.assertTrue(out["completed"])

    def test_as_text_shapes(self):
        self.assertEqual(core._as_text([{"text": "a"}, {"content": "b"}, "c"]), "abc")
        self.assertEqual(core._as_text({"text": "d"}), "d")
        self.assertEqual(core._as_text(None), "")

    def test_latest_receipt_picks_newest(self):
        root = tempfile.mkdtemp()
        old_env = os.environ.get("DOUBAO_CLI_CONFIG_DIR")
        os.environ["DOUBAO_CLI_CONFIG_DIR"] = root
        try:
            folder = os.path.join(root, "turns", "scope1")
            os.makedirs(folder)
            first = os.path.join(folder, "123456789012-1.json")
            second = os.path.join(folder, "123456789012-2.json")
            with io.open(first, "w", encoding="utf-8") as handle:
                handle.write('{"thinking": "one"}')
            with io.open(second, "w", encoding="utf-8") as handle:
                handle.write('{"thinking": "two"}')
            os.utime(first, (1, 1))
            data = core.latest_receipt("123456789012")
            self.assertEqual(data["thinking"], "two")
        finally:
            if old_env is None:
                os.environ.pop("DOUBAO_CLI_CONFIG_DIR", None)
            else:
                os.environ["DOUBAO_CLI_CONFIG_DIR"] = old_env

    def test_latest_receipt_missing(self):
        self.assertIsNone(core.latest_receipt(""))


class SessionMove(unittest.TestCase):
    def test_move_ok(self):
        argv, error = core.build_session_action("doubao", "termux", "move", "123456789012", "proj-a")
        self.assertEqual(error, "")
        self.assertIn("proj-a", argv)
        self.assertIn("move", argv)

    def test_move_requires_target(self):
        argv, error = core.build_session_action("doubao", "", "move", "123456789012", "")
        self.assertIsNone(argv)
        self.assertTrue(error)

    def test_move_none(self):
        argv, error = core.build_session_action("doubao", "", "move", "123456789012", "none")
        self.assertEqual(error, "")
        self.assertIn("none", argv)


class ServerParts(unittest.TestCase):
    def test_cookie_login_requires_file(self):
        try:
            import server
        except Exception as exc:  # 依赖缺失时跳过，不误判
            self.skipTest(str(exc))
        result = server._login_cookie({"bin": "doubao", "binArgs": []}, "", "")
        self.assertFalse(result.get("ok"))
        result = server._login_cookie({"bin": "doubao", "binArgs": []}, "/definitely/not/here.txt", "")
        self.assertFalse(result.get("ok"))
        self.assertIn("不存在", result.get("error") or "")


class FrontEnd(unittest.TestCase):
    def test_index_has_trace(self):
        html = io.open(os.path.join(ROOT, "web", "index.html"), "r", encoding="utf-8").read()
        for token in ('id="trace"', 'id="trace-think"', 'id="trace-steps"', 'id="attach"', 'id="pd-open"'):
            self.assertIn(token, html)

    def test_app_has_trace(self):
        js = io.open(os.path.join(ROOT, "web", "app.js"), "r", encoding="utf-8").read()
        for token in ("renderTrace", 'payload.type === "trace"', "/api/login/cookie",
                      "&archived=1", '"move"', "attach: attachPaths", "timeout: Number(timeout.value)"):
            self.assertIn(token, js)

    def test_css_has_trace(self):
        css = io.open(os.path.join(ROOT, "web", "style.css"), "r", encoding="utf-8").read()
        self.assertIn(".trace-body", css)
        self.assertIn(".trace-think", css)


class TraceRuntime(unittest.TestCase):
    def test_trace_event_fires(self):
        import json
        import time

        root = tempfile.mkdtemp()
        old_cli = os.environ.get("DOUBAO_CLI_CONFIG_DIR")
        old_sessions = core.SESSION_DIR
        os.environ["DOUBAO_CLI_CONFIG_DIR"] = root
        core.SESSION_DIR = os.path.join(root, "gui-sessions")
        try:
            conversation_id = "38456840786070018"
            folder = os.path.join(root, "turns", "scope")
            os.makedirs(folder)
            path = os.path.join(folder, conversation_id + "-1.json")
            with io.open(path, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "conversationId": conversation_id,
                    "runId": "1",
                    "thinking": "先读文件再改代码",
                    "handoffs": [{"taskId": "123456789", "completed": False}],
                }, ensure_ascii=False))

            events = []
            config = dict(core.DEFAULT_CONFIG)
            config.update({"bin": "mock", "binArgs": [], "profile": "", "workspace": root,
                           "mode": "work", "permission": "ask_risky"})
            session = core.Session(config, lambda event: events.append(event))
            session.conversation_id = conversation_id
            session.status = "running"
            time.sleep(2.4)
            traces = [event for event in events if event.get("type") == "trace"]
            self.assertTrue(traces, "没有收到 trace 事件")
            self.assertEqual(traces[-1]["data"]["thinking"], "先读文件再改代码")
            self.assertEqual(len(traces[-1]["data"]["steps"]), 1)
        finally:
            core.SESSION_DIR = old_sessions
            if old_cli is None:
                os.environ.pop("DOUBAO_CLI_CONFIG_DIR", None)
            else:
                os.environ["DOUBAO_CLI_CONFIG_DIR"] = old_cli


if __name__ == "__main__":
    unittest.main(verbosity=0)
