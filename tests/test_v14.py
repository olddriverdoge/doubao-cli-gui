# -*- coding: utf-8 -*-
"""0.1.15：假完成修正、云端对账、网页桥接接口。"""
import json
import os
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import core  # noqa: E402
import server  # noqa: E402

TOKEN = "test-token-0001"


def _config(workspace: str) -> dict:
    return {
        "bin": os.path.join(workspace, "no-such-cli"),
        "binArgs": [],
        "profile": "",
        "workspace": workspace,
        "mode": "work",
        "permission": "ask_risky",
        "timeout": 20,
        "model": "",
        "reasoning": "",
        "skills": True,
        "enterprise": False,
        "project": "",
        "mcp": [],
        "expect_json": False,
        "reply_schema": "",
        "attach": [],
        "auto_approve": [],
        "show_trace": True,
        "bridge_token": TOKEN,
        "last_conversation": "",
    }


class CoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        core.SESSION_DIR = os.path.join(self.tmp, "sessions")
        self.events: list = []
        self.session = core.Session(_config(self.tmp), self.events.append)

    def test_version(self) -> None:
        self.assertEqual(core.VERSION, "0.1.15")

    def test_unsettled_run_is_stalled(self) -> None:
        """云端还在 running、本地进程已退出：不能再报“完成”。"""
        self.session.conversation_id = "38456842486920706"
        self.session.run_id = "63170744863716098"
        self.session._cloud_settle = lambda: ""
        turn = core.Turn(argv=["x"], label="send")
        turn.result = {"status": "running", "tasks": {"total": 2}}
        turn.conversation_id = self.session.conversation_id
        turn.run_id = self.session.run_id
        self.session.status = "running"
        self.session._finish(turn)
        self.assertEqual(self.session.status, "stalled")
        self.assertTrue(self.session.stalled)
        self.assertIn("未收尾", self.session.phase)
        self.assertEqual(self.session.messages[-1]["role"], "system")
        self.assertIn("续等", self.session.messages[-1]["text"])

    def test_settled_run_is_done(self) -> None:
        self.session.conversation_id = "38456842486920706"
        self.session.run_id = "63170744863716098"
        self.session._cloud_settle = lambda: "云端已经写完了"
        turn = core.Turn(argv=["x"], label="send")
        turn.result = {"status": "running", "tasks": {"total": 2}}
        self.session.status = "running"
        self.session._finish(turn)
        self.assertEqual(self.session.status, "done")
        self.assertFalse(self.session.stalled)
        self.assertEqual(self.session.messages[-1]["role"], "assistant")
        self.assertEqual(self.session.messages[-1]["text"], "云端已经写完了")

    def test_cloud_settle_needs_ids(self) -> None:
        self.session.conversation_id = ""
        self.session.run_id = ""
        self.assertEqual(self.session._cloud_settle(), "")

    def test_bridge_state_shape(self) -> None:
        self.session.conversation_id = "38456842486920706"
        self.session.run_id = "63170744863716098"
        self.session.status = "stalled"
        self.session.stalled = True
        state = self.session.bridge_state()
        for key in ("status", "phase", "conversationId", "runId", "canSend",
                    "canResume", "canStop", "workspace", "mode", "version"):
            self.assertIn(key, state)
        self.assertTrue(state["canResume"])
        self.assertTrue(state["canStop"])
        # stalled 仍可发新消息，只是会丢掉续等机会，所以 canSend 保持 True
        self.assertTrue(state["canSend"])
        self.assertEqual(state["version"], "0.1.15")

    def test_snapshot_exposes_token(self) -> None:
        self.assertEqual(self.session.snapshot()["config"]["bridgeToken"], TOKEN)


class BridgeHttpCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp()
        core.CONFIG_DIR = cls.tmp
        cls.session = core.Session(_config(cls.tmp), lambda event: None)
        cls.session.config["bin"] = os.path.join(cls.tmp, "missing-cli")
        server.Handler.session = cls.session
        server.Handler.broker = server.Broker()
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def call(self, path: str, body=None, token: str = TOKEN, method: str = "") -> tuple:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                          method=method or ("POST" if data else "GET"))
        request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("X-Doubao-Bridge", token)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    def test_ping_is_open(self) -> None:
        status, payload = self.call("/api/web/ping", token="")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["bridge"])
        self.assertEqual(payload["version"], "0.1.15")

    def test_state_requires_token(self) -> None:
        status, payload = self.call("/api/web/state", token="")
        self.assertEqual(status, 401)
        self.assertFalse(payload["ok"])

    def test_state_with_token(self) -> None:
        status, payload = self.call("/api/web/state")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["version"], "0.1.15")

    def test_cors_preflight(self) -> None:
        request = Request(f"http://127.0.0.1:{self.port}/api/web/state", method="OPTIONS")
        request.add_header("Origin", "https://www.doubao.com")
        with urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), "https://www.doubao.com")
            self.assertIn("X-Doubao-Bridge", response.headers.get("Access-Control-Allow-Headers") or "")

    def test_capture_writes_file(self) -> None:
        status, payload = self.call("/api/web/capture",
                                    {"kind": "fetch", "url": "https://www.doubao.com/api/x", "body": "{}"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"], payload)
        path = os.path.join(payload["folder"], payload["file"])
        self.assertTrue(os.path.isfile(path))
        blob = json.load(open(path, encoding="utf-8"))
        self.assertEqual(blob["kind"], "fetch")

    def test_send_empty_is_rejected(self) -> None:
        status, payload = self.call("/api/web/send", {"text": "   "})
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])

    def test_send_accepts_and_reports(self) -> None:
        status, payload = self.call("/api/web/send", {"text": "只回复两个字：收到"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["accepted"])
        # 记录被受理后立刻收尾，避免影响后续用例
        self.session.status = "idle"
        self.session.stalled = False

    def test_unknown_bridge_route(self) -> None:
        status, payload = self.call("/api/web/nope", {})
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
