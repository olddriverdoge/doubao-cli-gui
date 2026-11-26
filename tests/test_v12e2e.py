# -*- coding: utf-8 -*-
"""0.1.12 端到端验收：真起服务 + mock CLI，走读历史回填、列表参数、Cookie 登录。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_api


class Flow(unittest.TestCase):
    def test_read_and_switch(self):
        with test_api.Harness() as harness:
            state = test_api.api(harness.port, "GET", "/api/state")
            config = state["data"]["config"]
            self.assertIn("showTrace", config)
            self.assertIn("timeout", config)

            listing = test_api.api(harness.port, "GET", "/api/sessions?limit=50&archived=1")
            self.assertTrue(listing["ok"], listing)
            self.assertEqual(listing["limit"], 50)
            self.assertTrue(listing["archived"])

            read = test_api.api(harness.port, "GET", "/api/session/read?id=38456840786070018")
            self.assertTrue(read["ok"], read)
            self.assertGreaterEqual(len(read["messages"]), 2)
            self.assertIn("历史里的回答", [row["text"] for row in read["messages"]])

            switched = test_api.api(harness.port, "POST", "/api/switch", {"id": "38456840786070018"})
            self.assertTrue(switched["ok"], switched)
            after = test_api.api(harness.port, "GET", "/api/state")
            texts = [row["text"] for row in after["data"]["messages"]]
            self.assertIn("历史里的回答", texts)

    def test_config_fields(self):
        with test_api.Harness() as harness:
            saved = test_api.api(
                harness.port,
                "POST",
                "/api/config",
                {"timeout": 120, "skills": False, "project": "p1", "enterprise": True, "show_trace": False},
            )
            self.assertTrue(saved["ok"], saved)
            config = saved["data"]["config"]
            self.assertEqual(config["timeout"], 120)
            self.assertFalse(config["skills"])
            self.assertEqual(config["project"], "p1")
            self.assertTrue(config["enterprise"])
            self.assertFalse(config["showTrace"])

            bad = test_api.api(harness.port, "POST", "/api/config", {"timeout": 5})
            self.assertFalse(bad["ok"])

    def test_cookie_and_move(self):
        with test_api.Harness() as harness:
            missing = test_api.api(harness.port, "POST", "/api/login/cookie", {"path": "/definitely/not/here"})
            self.assertFalse(missing["ok"])
            self.assertIn("不存在", missing.get("error") or "")

            moved = test_api.api(
                harness.port,
                "POST",
                "/api/session/action",
                {"action": "move", "id": "38456840786070018", "value": "proj-a"},
            )
            self.assertTrue(moved["ok"], moved)
            empty = test_api.api(
                harness.port,
                "POST",
                "/api/session/action",
                {"action": "move", "id": "38456840786070018", "value": ""},
            )
            self.assertFalse(empty["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
