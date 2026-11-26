# -*- coding: utf-8 -*-
"""0.1.16：登录界面补全——粘贴 Cookie、MFA 提示、二维码存图。"""
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


def _config(workspace: str) -> dict:
    return {
        "bin": os.path.join(workspace, "no-such-cli"),
        "binArgs": [],
        "profile": "termux",
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
        "bridge_token": "test-token-0001",
        "last_conversation": "",
    }


class CookiePayloadCase(unittest.TestCase):
    def test_version(self) -> None:
        self.assertEqual(core.VERSION, "0.1.16")

    def test_raw_header(self) -> None:
        self.assertEqual(server._cookie_payload("a=1; b=2"), "a=1; b=2")

    def test_strips_cookie_prefix(self) -> None:
        self.assertEqual(server._cookie_payload("Cookie: a=1; b=2"), "a=1; b=2")

    def test_dict_json(self) -> None:
        self.assertEqual(server._cookie_payload('{"cookie": "a=1; b=2"}'), "a=1; b=2")

    def test_array_json(self) -> None:
        blob = json.dumps([{"name": "sessionid", "value": "abc"}, {"name": "ttwid", "value": "def"}])
        self.assertEqual(server._cookie_payload(blob), "sessionid=abc; ttwid=def")

    def test_blank(self) -> None:
        self.assertEqual(server._cookie_payload("   "), "")

    def test_newlines_folded(self) -> None:
        self.assertEqual(server._cookie_payload("a=1;\n b=2;\n"), "a=1; b=2;")

    def test_mfa_hint(self) -> None:
        self.assertIn("2156", server._login_hint("errorCode= 2156, QR status check failed"))
        self.assertEqual(server._login_hint("别的错"), "别的错")


class PasteCookieCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        core.CONFIG_DIR = self.tmp

    def test_rejects_non_cookie(self) -> None:
        out = server._login_cookie(_config(self.tmp), text="这不是 cookie")
        self.assertFalse(out["ok"])
        self.assertIn("不像 Cookie", out["error"])

    def test_writes_pasted_cookie_to_file(self) -> None:
        out = server._login_cookie(_config(self.tmp), text="sessionid=abc; ttwid=def")
        self.assertFalse(out["ok"])          # 没有真 CLI，登录当然失败
        self.assertIn("error", out)
        target = os.path.join(self.tmp, "login-cookie.txt")
        self.assertTrue(os.path.isfile(target))
        self.assertEqual(open(target, encoding="utf-8").read().strip(), "sessionid=abc; ttwid=def")
        self.assertEqual(out["source"], "text")

    def test_missing_path_still_errors(self) -> None:
        out = server._login_cookie(_config(self.tmp))
        self.assertFalse(out["ok"])
        self.assertIn("粘贴", out["error"])


class LoginHttpCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp()
        core.CONFIG_DIR = cls.tmp
        cls.session = core.Session(_config(cls.tmp), lambda event: None)
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

    def post(self, path: str, body) -> tuple:
        data = json.dumps(body).encode("utf-8")
        request = Request("http://127.0.0.1:%d%s" % (self.port, path), data=data, method="POST")
        request.add_header("Content-Type", "application/json")
        try:
            with urlopen(request, timeout=15) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    def test_paste_text_route(self) -> None:
        status, payload = self.post("/api/login/cookie",
                                    {"text": "sessionid=abc; odin_tt=def", "profile": "termux"})
        self.assertEqual(status, 200)
        self.assertTrue("ok" in payload)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["source"], "text")
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "login-cookie.txt")))

    def test_bad_text_route(self) -> None:
        status, payload = self.post("/api/login/cookie", {"text": "hello world"})
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertIn("Cookie", payload["error"])


class FrontEndCase(unittest.TestCase):
    def test_panel_has_paste_and_save_qr(self) -> None:
        js = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
        self.assertIn("粘贴 Cookie", js)
        self.assertIn("saveQrBtn", js)
        self.assertIn("function saveQr", js)
        self.assertIn("text: text, path: path", js)
        css = open(os.path.join(ROOT, "web", "style.css"), encoding="utf-8").read()
        self.assertIn(".field textarea", css)


if __name__ == "__main__":
    unittest.main(verbosity=2)
