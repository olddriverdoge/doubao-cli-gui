#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 APK 里的网关：静态页、/api/* 反代、SSE 透传、离线兜底。

做法：真起两个进程（Termux 侧服务 + 网关），全部用 HTTP 断言，不截图。
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from test_api import Harness, Stream, api  # noqa: E402

PY_DIR = os.path.join(ROOT, "android", "app", "src", "main", "python")
FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✓ {label}")
    else:
        FAILURES.append(label)
        print(f"  ✗ {label}" + (f"  ({detail})" if detail else ""))


def free_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class Gateway:
    def __init__(self, upstream_port, port=None):
        self.upstream_port = upstream_port
        self.port = port or free_port()
        self.proc = None

    def __enter__(self):
        env = dict(os.environ)
        env["DOUBAO_GUI_UPSTREAM"] = f"http://127.0.0.1:{self.upstream_port}"
        env["PYTHONPATH"] = PY_DIR
        self.proc = subprocess.Popen(
            [sys.executable, "-c", f"import gateway; gateway.serve({self.port})"],
            cwd=PY_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        for _ in range(200):
            try:
                get(self.port, "/app/available")
                return self
            except Exception:
                if self.proc.poll() is not None:
                    output = self.proc.stdout.read().decode("utf-8", "replace") if self.proc.stdout else ""
                    raise RuntimeError("网关起不来：" + output[-800:])
                time.sleep(0.1)
        raise RuntimeError("网关启动超时")

    def __exit__(self, *args):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def get(port, path, timeout=10):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def main():
    print("APK 网关测试")

    print("\n[1] 离线兜底（Termux 服务没起）")
    dead = free_port()
    with Gateway(dead) as gateway:
        status, body = get(gateway.port, "/")
        check("静态首页仍可打开", status == 200 and "豆包 CLI".encode() in body, str(status))

        request = urllib.request.Request(f"http://127.0.0.1:{gateway.port}/api/state")
        try:
            urllib.request.urlopen(request, timeout=10)
            check("离线时 /api/state 应报错", False, "居然 200")
        except urllib.error.HTTPError as error:
            payload = json.loads(error.read().decode("utf-8"))
            check("离线时 /api/state 返回 503", error.code == 503, str(error.code))
            check("带 offline 标记", payload.get("offline") is True, str(payload))

        available = json.loads(get(gateway.port, "/app/available")[1])
        check("/app/available 声明原生模式", available.get("ok") is True)
        status_payload = json.loads(get(gateway.port, "/app/status")[1])
        check("/app/status 报告未连上", status_payload.get("ok") is False)

        stream = Stream(gateway.port)
        stream.start()
        snapshot = stream.wait(lambda event: event["type"] == "snapshot", 10)
        check("离线 SSE 给快照", snapshot is not None)
        if snapshot:
            check("快照标记 offline", snapshot["data"].get("offline") is True, str(snapshot["data"].get("phase")))
            check("快照文案是未启动", snapshot["data"].get("phase") == "Termux 服务未启动", str(snapshot["data"].get("phase")))

        boot = api(gateway.port, "POST", "/app/boot", {}, timeout=10)
        check("桌面环境 boot 返回 no-java", str(boot.get("detail", "")).startswith("no-java"), str(boot.get("detail")))

    print("\n[2] 在线：反代与 SSE")
    with Harness() as core:
        with Gateway(core.port) as gateway:
            upstream_state = api(core.port, "GET", "/api/state")["data"]
            proxied = api(gateway.port, "GET", "/api/state")["data"]
            check("/api/state 透传一致", proxied["status"] == upstream_state["status"] and proxied["phase"] == upstream_state["phase"])
            check("config 透传", proxied["config"]["workspace"] == upstream_state["config"]["workspace"])

            status_payload = json.loads(get(gateway.port, "/app/status")[1])
            check("/app/status 报告已连上", status_payload.get("ok") is True, str(status_payload.get("ok")))

            sessions = api(gateway.port, "GET", "/api/sessions")
            check("/api/sessions 透传", sessions.get("ok") is True and len(sessions.get("sessions") or []) == 2)

            stream = Stream(gateway.port)
            stream.start()
            snapshot = stream.wait(lambda event: event["type"] == "snapshot", 10)
            check("SSE 首帧透传", snapshot is not None and not snapshot["data"].get("offline"), str(snapshot and snapshot["data"].get("phase")))

            sent = api(gateway.port, "POST", "/api/send", {"text": "走网关卡"})
            check("通过网关发送成功", sent.get("ok") is True, str(sent.get("error")))

            waiting = stream.wait(lambda event: event["type"] == "status" and event["data"]["status"] == "approval", 15)
            check("网关能收到审批事件", waiting is not None)
            if waiting:
                approval = waiting["data"]["approval"] or {}
                check("审批内容透传", approval.get("tool") == "local.workspace.apply", str(approval.get("tool")))

            approved = api(gateway.port, "POST", "/api/approve", {"allow": True, "scope": "turn"})
            check("通过网关审批成功", approved.get("ok") is True, str(approved.get("error")))

            final = stream.wait(lambda event: event["type"] == "status" and event["data"]["status"] in ("done", "error"), 20)
            check("网关收到终态", final is not None and final["data"]["status"] == "done", str(final and final["data"]["status"]))

            answer = stream.wait(lambda event: event["type"] == "message" and event["data"]["role"] == "assistant", 15)
            check("网关收到助手回复", answer is not None)
            if answer:
                check("回复内容正确", "mock" in answer["data"]["text"], answer["data"]["text"][:40])

            check("首页与静态资源可读", get(gateway.port, "/style.css")[0] == 200)
            check("manifest 可读", get(gateway.port, "/manifest.webmanifest")[0] == 200)

    print("")
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项：{FAILURES}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
