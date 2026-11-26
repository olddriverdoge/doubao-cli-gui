# -*- coding: utf-8 -*-
"""0.1.13 端到端：发一条消息后，CLI 原始输出应出现在 /api/state 的 logs 里。"""
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_api


def wait_idle(port, seconds=12):
    deadline = time.time() + seconds
    data = {}
    while time.time() < deadline:
        data = test_api.api(port, "GET", "/api/state")["data"]
        if data.get("status") in ("idle", "done"):
            return data
        time.sleep(0.2)
    return data


class RawLogFlow(unittest.TestCase):
    def test_logs_after_send(self):
        with test_api.Harness(extra_env={"MOCK_SKIP": "1", "MOCK_SLEEP": "0.02"}) as harness:
            sent = test_api.api(harness.port, "POST", "/api/send", {"text": "跑一下看看"})
            self.assertTrue(sent["ok"], sent)

            data = wait_idle(harness.port)
            logs = data.get("logs") or []
            self.assertTrue(logs, "没有收到原始输出")
            self.assertIn("[mock]", "\n".join(logs))
            self.assertLessEqual(len(logs), 400)
            self.assertGreaterEqual(int(data.get("logSeq") or 0), 1)

    def test_json_receipt_not_in_logs(self):
        with test_api.Harness(extra_env={"MOCK_SKIP": "1", "MOCK_SLEEP": "0.02"}) as harness:
            self.assertTrue(test_api.api(harness.port, "POST", "/api/send", {"text": "看回执"})["ok"])
            data = wait_idle(harness.port)
            logs = data.get("logs") or []
            self.assertTrue(logs, "没有收到原始输出")
            for line in logs:
                self.assertFalse(line.lstrip().startswith("{"), "JSON 回执不该进日志：" + line)
                self.assertNotIn('"platform": "headless"', line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
