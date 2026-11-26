#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doubao-cli-gui 端到端测试：真起 HTTP 服务，用 urllib 打接口、用 SSE 读事件。

不截图、不依赖浏览器渲染，纯控制台验收。
"""

import http.client
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "server.py")
MOCK = os.path.join(ROOT, "tests", "mock_doubao.py")

RESULTS = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    RESULTS.append((label, bool(condition), detail))
    mark = "✓" if condition else "✗"
    print(f"  {mark} {label}" + (f"  ({detail})" if detail and not condition else ""))
    return bool(condition)


def free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


class Stream(threading.Thread):
    """极简 SSE 客户端。"""

    daemon = True

    def __init__(self, port: int) -> None:
        super().__init__(daemon=True)
        self.port = port
        self.events: queue.Queue = queue.Queue()
        self.buffer = []
        self.error = None

    def run(self) -> None:
        try:
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=300)
            connection.request("GET", "/api/events")
            response = connection.getresponse()
            if response.status != 200:
                self.error = f"HTTP {response.status}"
                return
            while True:
                line = response.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                if text.startswith("data:"):
                    try:
                        self.events.put(json.loads(text[5:].strip()))
                    except Exception:
                        pass
        except Exception as exc:  # 服务被关闭时正常抛错
            self.error = str(exc)

    def wait(self, predicate, timeout: float = 30.0, label: str = ""):
        """在已缓冲和后续事件里找一个满足条件的；不匹配的留在缓冲里，供后面的断言用。"""
        for event in list(self.buffer):
            if predicate(event):
                self.buffer.remove(event)
                return event
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                event = self.events.get(timeout=0.2)
            except queue.Empty:
                continue
            if predicate(event):
                return event
            self.buffer.append(event)
        return None

    def drain(self) -> None:
        self.buffer.clear()
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                return


class Harness:
    def __init__(self, extra_env: dict | None = None) -> None:
        self.temp = tempfile.mkdtemp(prefix="gui-test-")
        self.port = free_port()
        self.proc = None
        self.extra_env = extra_env or {}

    def __enter__(self) -> "Harness":
        config_dir = os.path.join(self.temp, "cfg", "doubao-cli-gui")
        os.makedirs(config_dir, exist_ok=True)
        workspace = os.path.join(self.temp, "ws")
        os.makedirs(workspace, exist_ok=True)
        binary = os.path.join(self.temp, "bin", "doubao")
        os.makedirs(os.path.dirname(binary), exist_ok=True)
        shutil.copy(MOCK, binary)
        os.chmod(binary, 0o755)
        extra = {}
        if os.name == "nt":
            # Windows 不能直接执行无扩展名的 .py，用「解释器 + 脚本」的形式喂进去
            extra = {"binArgs": [binary]}
            binary = sys.executable
        with open(os.path.join(config_dir, "config.json"), "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "bin": binary,
                    "profile": "",
                    "workspace": workspace,
                    "mode": "work",
                    "permission": "ask_risky",
                    "timeout": 60,
                    **extra,
                },
                handle,
            )
        env = dict(os.environ)
        env["XDG_CONFIG_HOME"] = os.path.join(self.temp, "cfg")
        env["HOME"] = self.temp
        env.pop("MOCK_SLEEP", None)
        env.update(self.extra_env)
        self.proc = subprocess.Popen(
            [sys.executable, SERVER, "--port", str(self.port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        for _ in range(150):
            try:
                api(self.port, "GET", "/api/state", timeout=2)
                return self
            except Exception:
                if self.proc.poll() is not None:
                    output = self.proc.stdout.read().decode("utf-8", "replace") if self.proc.stdout else ""
                    raise RuntimeError(f"服务启动失败：{output[-800:]}")
                time.sleep(0.1)
        raise RuntimeError("服务启动超时")

    def __exit__(self, *args) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        shutil.rmtree(self.temp, ignore_errors=True)


def api(port: int, method: str, path: str, payload: dict | None = None, timeout: float = 60):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        if path.endswith(".webmanifest") or path.endswith(".png") or path.endswith(".html"):
            return body
        return json.loads(body or "{}")


def fetch(port: int, path: str):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, response.headers.get("Content-Type"), response.read()


# ---------------------------------------------------------------- 场景


def scenario_static_and_queries() -> None:
    print("\n[1] 静态资源 / 首屏状态 / 只读查询")
    with Harness() as harness:
        port = harness.port
        status, ctype, body = fetch(port, "/")
        check("首页 200", status == 200, str(status))
        check("首页是 HTML", b"<!doctype html>" in body.lower())
        check("首页含标题", "豆包 CLI".encode() in body)

        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        connection.request("GET", "/api/events")
        response = connection.getresponse()
        first_line = response.readline().decode("utf-8", "replace")
        check("SSE 端点可连", response.status == 200 and "text/event-stream" in (response.getheader("Content-Type") or ""), str(response.status))
        check("SSE 发 retry 前缀", first_line.startswith("retry:"), first_line.strip())
        connection.close()

        status, ctype, body = fetch(port, "/manifest.webmanifest")
        manifest = json.loads(body.decode("utf-8"))
        check("manifest 可解析", manifest.get("short_name") == "豆包CLI")
        check("manifest 声明 standalone", manifest.get("display") == "standalone")

        status, _, _ = fetch(port, "/icon-192.png")
        check("192 图标存在", status == 200, str(status))
        status, _, _ = fetch(port, "/icon-512.png")
        check("512 图标存在", status == 200, str(status))

        state = api(port, "GET", "/api/state")["data"]
        check("初始 status=idle", state["status"] == "idle", state["status"])
        check("初始无消息", state["messages"] == [])
        check("配置带出工作区", state["config"]["workspace"].endswith("ws"))
        check("权限标签齐全", set(state["permissions"]) == {"ask_risky", "ask_always", "always_allow"})

        sessions = api(port, "GET", "/api/sessions")
        check("sessions 查询成功", sessions.get("ok") is True, str(sessions.get("error")))
        check("sessions 返回 2 条", len(sessions.get("sessions") or []) == 2)
        check("sessions 带置顶标记", (sessions.get("sessions") or [{}])[0].get("pinned") is True)

        models = api(port, "GET", "/api/models")
        check("models 查询成功", models.get("ok") is True)
        first_model = (models.get("models") or [{}])[0]
        check("models 返回结构化条目", first_model.get("id") == "doubao-seed-2-0-pro", str(first_model))
        check("models 带选中标记", first_model.get("selected") is True)
        check("models 带推理档位", models.get("reasoning") == "high")

        doctor = api(port, "GET", "/api/doctor")
        check("doctor 全绿", doctor.get("ok") is True, json.dumps(doctor.get("checks"), ensure_ascii=False))

        bad = api(port, "POST", "/api/switch", {"id": "abc"})
        check("非法会话 ID 被拒", bad.get("ok") is False)

        good = api(port, "POST", "/api/switch", {"id": "38450000000000002"})
        check("合法会话 ID 可切换", good.get("ok") is True)
        state = api(port, "GET", "/api/state")["data"]
        check("切换后会话号生效", state["conversationId"] == "38450000000000002")


def scenario_approval_flow() -> None:
    print("\n[2] 主流程：发送 → 三次审批 → 拿到回复")
    with Harness() as harness:
        port = harness.port
        stream = Stream(port)
        stream.start()
        snapshot = stream.wait(lambda event: event["type"] == "snapshot", 10, "snapshot")
        check("SSE 首帧是 snapshot", snapshot is not None, stream.error or "")

        sent = api(port, "POST", "/api/send", {"text": "帮我审计一下项目结构"})
        check("send 返回 ok", sent.get("ok") is True, str(sent.get("error")))

        user = stream.wait(lambda event: event["type"] == "message" and event["data"]["role"] == "user", 10)
        check("收到用户消息回显", user is not None)

        busy = api(port, "POST", "/api/send", {"text": "再来一条"})
        check("运行中拒绝并发发送", busy.get("ok") is False, str(busy.get("error")))

        first = stream.wait(lambda event: event["type"] == "status" and event["data"]["status"] == "approval", 15)
        check("进入待审批状态", first is not None)
        if first:
            approval = first["data"]["approval"] or {}
            check("审批带 tool", approval.get("tool") == "local.workspace.apply", str(approval.get("tool")))
            check("审批带 operation", approval.get("operation", "").startswith("ensure_dir"), str(approval.get("operation")))
            check("审批带 risk", bool(approval.get("risk")), str(approval.get("risk")))

        approved = api(port, "POST", "/api/approve", {"allow": True, "scope": "once"})
        check("允许一次成功", approved.get("ok") is True, str(approved.get("error")))

        second = stream.wait(lambda event: event["type"] == "status" and event["data"]["status"] == "approval", 15)
        check("进入第二次审批", second is not None)
        if second:
            check(
                "第二次是 write_file",
                (second["data"]["approval"] or {}).get("operation", "").startswith("write_file"),
                str((second["data"]["approval"] or {}).get("operation")),
            )
        api(port, "POST", "/api/approve", {"allow": True, "scope": "once"})

        third = stream.wait(lambda event: event["type"] == "status" and event["data"]["status"] == "approval", 15)
        check("进入第三次审批", third is not None)
        if third:
            check("第三次是 Bash", (third["data"]["approval"] or {}).get("tool") == "Bash", str((third["data"]["approval"] or {}).get("tool")))
        api(port, "POST", "/api/approve", {"allow": True, "scope": "once"})

        final = stream.wait(lambda event: event["type"] == "status" and event["data"]["status"] in ("done", "error"), 20)
        check("最终回到终态", final is not None)
        if final:
            check("终态是 done", final["data"]["status"] == "done", final["data"]["status"])
            check("阶段是完成", final["data"]["phase"] == "完成", final["data"]["phase"])
            check("会话 ID 已回填", final["data"]["conversationId"] == "38450000000000001", final["data"]["conversationId"])

        answer = stream.wait(lambda event: event["type"] == "message" and event["data"]["role"] == "assistant", 10)
        check("收到助手回复", answer is not None)
        if answer:
            check("回复含 mock 标记", "mock" in answer["data"]["text"], answer["data"]["text"][:60])

        state = api(port, "GET", "/api/state")["data"]
        roles = [message["role"] for message in state["messages"]]
        check("消息序列 = user/若干system/assistant", roles[0] == "user" and roles[-1] == "assistant", str(roles))

        history = os.path.join(harness.temp, "cfg", "doubao-cli-gui", "sessions", "38450000000000001.json")
        check("会话已落盘", os.path.isfile(history))
        if os.path.isfile(history):
            with open(history, "r", encoding="utf-8") as handle:
                check("落盘含助手回复", any(item["role"] == "assistant" for item in json.load(handle)["messages"]))

        restarted = api(port, "GET", "/api/state")["data"]
        check("重连看到的会话相同", restarted["conversationId"] == "38450000000000001")


def scenario_deny_and_resume() -> None:
    print("\n[3] 拒绝审批 → 失败提示 → 续等")
    with Harness() as harness:
        port = harness.port
        stream = Stream(port)
        stream.start()
        stream.wait(lambda event: event["type"] == "snapshot", 10)

        api(port, "POST", "/api/send", {"text": "写个文件"})
        waiting = stream.wait(lambda event: event["type"] == "status" and event["data"]["status"] == "approval", 15)
        check("进入审批", waiting is not None)
        denied = api(port, "POST", "/api/approve", {"allow": False, "scope": "once"})
        check("拒绝返回 ok", denied.get("ok") is True)

        final = stream.wait(lambda event: event["type"] == "status" and event["data"]["status"] in ("done", "error"), 20)
        check("拒绝后进入终态", final is not None)
        if final:
            check("终态是 error", final["data"]["status"] == "error", final["data"]["status"])
        failure = stream.wait(lambda event: event["type"] == "message" and event["data"]["role"] == "error", 10)
        check("有错误消息", failure is not None)
        if failure:
            check("错误文案有内容", len(failure["data"]["text"]) > 0)

        state = api(port, "GET", "/api/state")["data"]
        check("拒绝后仍保留会话 ID", state["conversationId"] == "38450000000000001", state["conversationId"])

        resumed = api(port, "POST", "/api/resume", {})
        check("续等启动成功", resumed.get("ok") is True, str(resumed.get("error")))
        result = stream.wait(lambda event: event["type"] == "message" and event["data"]["role"] == "assistant", 20)
        check("续等拿到回复", result is not None)
        if result:
            check("续等回复是 wait 分支", "续等" in result["data"]["text"], result["data"]["text"][:40])


def scenario_config_and_stop() -> None:
    print("\n[4] 配置更新 / 中止")
    with Harness(extra_env={"MOCK_SLEEP": "2"}) as harness:
        port = harness.port
        stream = Stream(port)
        stream.start()
        stream.wait(lambda event: event["type"] == "snapshot", 10)

        updated = api(port, "POST", "/api/config", {"permission": "always_allow", "mode": "chat"})
        check("配置更新成功", updated.get("ok") is True, str(updated.get("error")))
        check("权限写入快照", updated["data"]["config"]["permission"] == "always_allow")
        check("模式写入快照", updated["data"]["config"]["mode"] == "chat")

        broken = api(port, "POST", "/api/config", {"workspace": "/no/such/dir"})
        check("非法工作区被拒", broken.get("ok") is False)

        tuned = api(port, "POST", "/api/config", {"model": "doubao-seed-2-0-pro", "reasoning": "high"})
        check("模型写入成功", tuned.get("ok") is True, str(tuned.get("error")))
        check("模型进入快照", tuned["data"]["config"]["model"] == "doubao-seed-2-0-pro")
        check("推理档位进入快照", tuned["data"]["config"]["reasoning"] == "high")
        check("快照带版本号", tuned["data"].get("version"))

        bad_reasoning = api(port, "POST", "/api/config", {"reasoning": "ultra"})
        check("非法推理档位被拒", bad_reasoning.get("ok") is False)

        api(port, "POST", "/api/send", {"text": "长任务"})
        stream.wait(lambda event: event["type"] == "status" and event["data"]["status"] == "approval", 15)
        stopped = api(port, "POST", "/api/stop", {})
        check("中止返回 ok", stopped.get("ok") is True, str(stopped.get("error")))
        after = stream.wait(lambda event: event["type"] == "status" and event["data"]["status"] in ("done", "error"), 20)
        check("中止后进入终态", after is not None)

        no_task = api(port, "POST", "/api/stop", {})
        check("空跑中止返回失败", no_task.get("ok") is False)


def scenario_session_actions() -> None:
    print("\n[5] 会话操作")
    with Harness() as harness:
        port = harness.port
        renamed = api(port, "POST", "/api/session/action", {"action": "rename", "id": "38450000000000001", "value": "改名了"})
        check("rename 成功", renamed.get("ok") is True, str(renamed.get("error")))
        pinned = api(port, "POST", "/api/session/action", {"action": "pin", "id": "38450000000000002"})
        check("pin 成功", pinned.get("ok") is True, str(pinned.get("error")))
        archived = api(port, "POST", "/api/session/action", {"action": "archive", "id": "38450000000000002"})
        check("archive 成功", archived.get("ok") is True, str(archived.get("error")))

        bad_id = api(port, "POST", "/api/session/action", {"action": "pin", "id": "abc"})
        check("非法 ID 被拒", bad_id.get("ok") is False)
        empty = api(port, "POST", "/api/session/action", {"action": "rename", "id": "38450000000000001", "value": ""})
        check("空标题被拒", empty.get("ok") is False)
        unknown = api(port, "POST", "/api/session/action", {"action": "delete", "id": "38450000000000001"})
        check("未知操作被拒", unknown.get("ok") is False)


def main() -> int:
    print("doubao-cli-gui 端到端测试")
    print(f"服务端 {SERVER}")
    for scenario in (scenario_static_and_queries, scenario_approval_flow, scenario_deny_and_resume, scenario_config_and_stop, scenario_session_actions):
        try:
            scenario()
        except Exception as exc:
            check(f"{scenario.__name__} 未抛异常", False, f"{type(exc).__name__}: {exc}")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n结果 {passed}/{total} 通过")
    if passed != total:
        print("失败项：")
        for label, ok, detail in RESULTS:
            if not ok:
                print(f"  - {label} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
