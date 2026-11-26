#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型参数测试：CLI 只认名称，GUI 不能把数字 id 直接塞给 --model。"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from test_api import Harness, RESULTS, api, check  # noqa: E402


def scenario_model() -> None:
    print("\n[1] /api/models 与模型配置")
    with Harness() as harness:
        port = harness.port
        models = api(port, "GET", "/api/models", timeout=15)
        check("models 查询成功", models.get("ok") is True, str(models.get("error")))
        rows = models.get("models") or []
        check("返回结构化条目", bool(rows) and rows[0].get("id") == "doubao-seed-2-0-pro", str(rows[:1]))
        ids = [str(row.get("id")) for row in rows]
        check("含数字 id 的模型", "9" in ids, str(ids))
        names = [str(row.get("name")) for row in rows]
        check("数字 id 对应名称", "自动" in names, str(names))

        saved = api(port, "POST", "/api/config", {"model": "9"}, timeout=20)
        check("保存数字 id 不报错", saved.get("ok") is True, str(saved.get("error")))
        value = ((saved.get("data") or {}).get("config") or {}).get("model")
        check("数字 id 换成名称", value == "自动", str(value))

        again = api(port, "POST", "/api/config", {"model": "自动"}, timeout=20)
        value = ((again.get("data") or {}).get("config") or {}).get("model")
        check("名称原样保存", value == "自动", str(value))

        cleared = api(port, "POST", "/api/config", {"model": ""}, timeout=20)
        value = ((cleared.get("data") or {}).get("config") or {}).get("model")
        check("可以清空模型", value == "", str(value))

        unknown = api(port, "POST", "/api/config", {"model": "9999"}, timeout=20)
        value = ((unknown.get("data") or {}).get("config") or {}).get("model")
        check("未知数字 id 原样保留", value == "9999", str(value))


if __name__ == "__main__":
    scenario_model()
    total = len(RESULTS)
    failed = [row for row in RESULTS if not row[1]]
    print(f"\n结果：{total - len(failed)}/{total} 通过")
    for label, _, detail in failed:
        print("  ✗", label, detail)
    sys.exit(1 if failed else 0)
