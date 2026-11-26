#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登录/登出流程端到端测试：真起服务，用 mock CLI 的字符码验证二维码抠图与状态机。"""

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from test_api import Harness, RESULTS, api, check  # noqa: E402


def wait_for(fn, timeout=25.0, interval=0.2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            value = fn()
        except Exception:
            value = None
        if value:
            return value
        time.sleep(interval)
    return None


def snapshot(port):
    return api(port, "GET", "/api/login", timeout=10)


def scenario_login() -> None:
    print("\n[1] 未登录 -> 扫码 -> 刷新 -> 取消 -> 登录成功 -> 退出登录")
    mark = os.path.join(tempfile.mkdtemp(prefix="gui-mark-"), "logged-in")
    with Harness({"MOCK_LOGIN_MARKER": mark, "MOCK_LOGIN_SECONDS": "0.25"}) as harness:
        port = harness.port

        first = wait_for(lambda: (snapshot(port) if snapshot(port).get("probed") else None))
        check("首屏会探测登录态", bool(first), str(first))
        check("未登录时 loggedIn 为 False", bool(first) and first.get("loggedIn") is False)
        check("首屏状态是 idle", bool(first) and first.get("state") == "idle")
        check("未登录时不出二维码", bool(first) and first.get("qr") == "")

        started = time.time()
        armed = api(port, "POST", "/api/login", {"timeout": 60})
        check("点登录立刻返回", armed.get("state") == "waiting", str(armed.get("state")))
        check("arm 不阻塞请求", time.time() - started < 5, f"{time.time() - started:.2f}s")

        got = wait_for(lambda: (snapshot(port) if snapshot(port).get("qr") else None), timeout=15)
        check("拿到二维码", bool(got))
        check("二维码是 PNG data URL", bool(got) and str(got.get("qr", "")).startswith("data:image/png;base64,"))
        check("等待中有计时", bool(got) and isinstance(got.get("elapsed"), int))
        first_qr = (got or {}).get("qr", "")

        refreshed = api(port, "POST", "/api/login", {"timeout": 60, "refresh": True})
        check("刷新后仍是 waiting", refreshed.get("state") == "waiting", str(refreshed.get("state")))
        renewed = wait_for(
            lambda: (snapshot(port) if snapshot(port).get("qr") and snapshot(port).get("qr") != first_qr else None),
            timeout=15,
        )
        check("刷新换了新码", bool(renewed))

        cancelled = api(port, "POST", "/api/login/cancel", {})
        check("取消回 idle", cancelled.get("state") == "idle", str(cancelled.get("state")))
        check("取消清掉二维码", cancelled.get("qr") == "")
        time.sleep(0.7)
        settled = snapshot(port)
        check("取消后不被后台进程翻成 failed", settled.get("state") == "idle", str(settled.get("state")))

        api(port, "POST", "/api/login", {"timeout": 60})
        done = wait_for(
            lambda: (snapshot(port) if snapshot(port).get("state") in ("ok", "failed") else None),
            timeout=30,
        )
        check("扫码流程有终态", bool(done), str((done or {}).get("state")))
        check("登录成功", bool(done) and done.get("state") == "ok", str((done or {}).get("state")))
        check("成功后 loggedIn 为 True", bool(done) and done.get("loggedIn") is True)
        check("成功后有账号", bool(done) and bool(done.get("accountId")))
        check("成功后二维码收起", bool(done) and done.get("qr") == "")
        check("marker 已写入", os.path.exists(mark))

        out = api(port, "POST", "/api/logout", {})
        check("退出登录返回 ok", out.get("ok") is True, str(out.get("error")))
        check("退出后 loggedIn 为 False", out.get("loggedIn") is False)
        check("退出后账号清空", out.get("accountId") == "")
        check("marker 已删除", not os.path.exists(mark))
        check("刷新快照仍是未登录", snapshot(port).get("loggedIn") is False)


def scenario_profile() -> None:
    print("\n[2] 登录时带上 profile 会写进配置")
    with Harness({}) as harness:
        port = harness.port
        api(port, "POST", "/api/login", {"timeout": 60, "profile": "测试档"})
        state = api(port, "GET", "/api/state", timeout=10)
        config = (state.get("data") or {}).get("config") or {}
        check("配置记下 profile", config.get("profile") == "测试档", str(config.get("profile")))
        api(port, "POST", "/api/login/cancel", {})


if __name__ == "__main__":
    scenario_login()
    scenario_profile()
    total = len(RESULTS)
    failed = [row for row in RESULTS if not row[1]]
    print(f"\n结果：{total - len(failed)}/{total} 通过")
    for label, _, detail in failed:
        print("  ✗", label, detail)
    sys.exit(1 if failed else 0)
