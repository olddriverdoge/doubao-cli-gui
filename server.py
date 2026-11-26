#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doubao-cli-gui 的 HTTP 层：静态页面 + REST + SSE。

只依赖标准库。默认绑 127.0.0.1，手机本机访问；SSE 负责把后端事件推给页面。
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import core

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


class Broker:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.queues: list = []

    def subscribe(self) -> queue.Queue:
        target: queue.Queue = queue.Queue(maxsize=500)
        with self.lock:
            self.queues.append(target)
        return target

    def unsubscribe(self, target: queue.Queue) -> None:
        with self.lock:
            if target in self.queues:
                self.queues.remove(target)

    def publish(self, event: dict) -> None:
        with self.lock:
            targets = list(self.queues)
        for target in targets:
            try:
                target.put_nowait(event)
            except queue.Full:
                pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    session: core.Session
    broker: Broker

    def log_message(self, fmt: str, *args) -> None:  # 静音访问日志
        return

    # -- 通用响应

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception:
            return {}

    # -- GET

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/events":
            self._sse()
            return
        if path == "/api/state":
            self._json({"ok": True, "data": self.session.snapshot()})
            return
        if path == "/api/sessions":
            self._json(self._list_sessions())
            return
        if path == "/api/models":
            self._json(self._list_models())
            return
        if path == "/api/doctor":
            self._json(self._doctor())
            return
        self._static(path)

    def _static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        root = os.path.realpath(WEB_DIR)
        target = os.path.realpath(os.path.join(root, path.lstrip("/")))
        if not (target == root or target.startswith(root + os.sep)):
            self._not_found()
            return
        if not os.path.isfile(target):
            self._not_found()
            return
        extension = os.path.splitext(target)[1].lower()
        mime = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
            ".webmanifest": "application/manifest+json; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(extension, "application/octet-stream")
        try:
            with open(target, "rb") as handle:
                body = handle.read()
        except OSError:
            self._not_found()
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _not_found(self) -> None:
        body = b"not found"
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self) -> None:
        target = self.broker.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True

        def send(event: dict) -> None:
            payload = json.dumps(event, ensure_ascii=False)
            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()

        try:
            self.wfile.write(b"retry: 1500\n\n")
            self.wfile.flush()
            send({"type": "snapshot", "data": self.session.snapshot()})
            while True:
                try:
                    event = target.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                send(event)
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            pass
        finally:
            self.broker.unsubscribe(target)

    # -- POST

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        payload = self._read_json()
        if path == "/api/send":
            self._json(self.session.send(str(payload.get("text") or "")))
        elif path == "/api/approve":
            self._json(
                self.session.approve(
                    bool(payload.get("allow")),
                    str(payload.get("scope") or "once"),
                )
            )
        elif path == "/api/stop":
            self._json(self.session.stop())
        elif path == "/api/resume":
            self._json(self.session.resume())
        elif path == "/api/switch":
            self._json(self.session.switch(str(payload.get("id") or "")))
        elif path == "/api/new":
            self._json(self.session.new_session())
        elif path == "/api/config":
            self._json(self._update_config(payload))
        elif path == "/api/session/action":
            self._json(self._session_action(payload))
        elif path == "/api/shutdown":
            self._json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._json({"ok": False, "error": "未知接口"}, 404)

    def _update_config(self, payload: dict) -> dict:
        config = self.session.config
        changed = False
        if "workspace" in payload:
            workspace = os.path.abspath(os.path.expanduser(str(payload["workspace"] or "")))
            if not os.path.isdir(workspace):
                return {"ok": False, "error": "目录不存在"}
            config["workspace"] = workspace
            changed = True
        if "permission" in payload:
            value = str(payload["permission"])
            if value not in core.PERM_LABELS:
                return {"ok": False, "error": "权限模式不合法"}
            config["permission"] = value
            changed = True
        if "mode" in payload:
            value = str(payload["mode"])
            if value not in ("work", "chat", "goal"):
                return {"ok": False, "error": "模式不合法"}
            config["mode"] = value
            changed = True
        if "profile" in payload:
            config["profile"] = str(payload["profile"] or "")
            changed = True
        if "model" in payload:
            config["model"] = str(payload["model"] or "").strip()
            changed = True
        if "reasoning" in payload:
            value = str(payload["reasoning"] or "").strip()
            if value and value not in core.REASONING_LEVELS:
                return {"ok": False, "error": "推理档位不合法"}
            config["reasoning"] = value
            changed = True
        if changed:
            core.save_config(config)
            self.session.emit({"type": "snapshot", "data": self.session.snapshot()})
        return {"ok": True, "data": self.session.snapshot()}

    def _session_action(self, payload: dict) -> dict:
        config = self.session.config
        argv, error = core.build_session_action(
            config["bin"],
            config.get("profile") or "",
            str(payload.get("action") or ""),
            str(payload.get("id") or ""),
            str(payload.get("value") or ""),
            list(config.get("binArgs") or []),
        )
        if error:
            return {"ok": False, "error": error}
        code, stdout, stderr = self._cli(argv)
        if code != 0:
            return {"ok": False, "error": (stderr or stdout or "操作失败").strip()[:200]}
        return {"ok": True, "detail": (stdout or "").strip()[:200]}

    # -- 只读查询

    def _cli(self, argv: list, timeout: int = 25) -> tuple:
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            return result.returncode, result.stdout, result.stderr
        except Exception as exc:
            return 1, "", str(exc)

    def _base_argv(self, *args: str) -> list:
        config = self.session.config
        argv = [config["bin"]] + [str(item) for item in (config.get("binArgs") or [])] + ["headless"] + list(args)
        if config.get("profile"):
            argv += ["--profile", str(config["profile"])]
        return argv

    def _list_sessions(self) -> dict:
        code, stdout, stderr = self._cli(self._base_argv("sessions", "list", "--limit", "10", "--json"))
        if code != 0:
            return {"ok": False, "error": (stderr or stdout or "查询失败").strip()[:200]}
        try:
            data = json.loads(stdout or "{}")
        except Exception:
            return {"ok": False, "error": "返回格式无法解析"}
        rows = []
        for row in data.get("sessions") or []:
            rows.append(
                {
                    "id": str(row.get("id") or row.get("conversationId") or ""),
                    "title": row.get("title") or "未命名",
                    "updatedAt": row.get("updatedAt") or "",
                    "pinned": bool(row.get("pinned")),
                    "archived": row.get("archived") is True,
                }
            )
        return {"ok": True, "sessions": rows, "currentId": self.session.conversation_id}

    def _list_models(self) -> dict:
        code, stdout, stderr = self._cli(self._base_argv("models", "--json"))
        if code != 0:
            return {"ok": False, "error": (stderr or stdout or "查询失败").strip()[:200]}
        try:
            data = json.loads(stdout or "{}")
        except Exception:
            return {"ok": False, "error": "返回格式无法解析"}
        rows = []
        for row in data.get("models") or data.get("items") or []:
            if isinstance(row, str):
                rows.append({"id": row, "name": row, "selected": False})
            else:
                rows.append(
                    {
                        "id": str(row.get("id") or row.get("name") or ""),
                        "name": str(row.get("name") or row.get("id") or ""),
                        "selected": bool(row.get("selected")),
                    }
                )
        return {"ok": True, "models": rows, "reasoning": data.get("reasoning") or ""}

    def _doctor(self) -> dict:
        config = self.session.config
        checks = []
        checks.append({"name": "工作区存在", "ok": os.path.isdir(config.get("workspace") or ""), "detail": config.get("workspace")})
        code, stdout, stderr = self._cli(self._base_argv("status", "--json"))
        if code == 0:
            try:
                data = json.loads(stdout or "{}")
                checks.append({"name": "账号已登录", "ok": bool(data.get("loggedIn")), "detail": f"accountId {data.get('accountId') or '-'}"})
            except Exception:
                checks.append({"name": "账号已登录", "ok": False, "detail": "返回格式无法解析"})
        else:
            checks.append({"name": "账号已登录", "ok": False, "detail": (stderr or stdout or "查询失败").strip()[:120]})
        code, stdout, stderr = self._cli(self._base_argv("runtimes", "--json"))
        if code == 0:
            try:
                data = json.loads(stdout or "{}")
                rows = data.get("runtimes") or data.get("items") or []
                local = next((row for row in rows if (row.get("name") or row.get("id")) == "local"), None)
                if local:
                    checks.append(
                        {
                            "name": "本地执行通道",
                            "ok": bool(local.get("available", True)),
                            "detail": f"{local.get('engine') or ''} / {local.get('hostPlatform') or ''}".strip(" /"),
                        }
                    )
                else:
                    checks.append({"name": "本地执行通道", "ok": False, "detail": "runtimes 里没有 local"})
            except Exception:
                checks.append({"name": "本地执行通道", "ok": False, "detail": "返回格式无法解析"})
        else:
            checks.append({"name": "本地执行通道", "ok": False, "detail": (stderr or stdout or "查询失败").strip()[:120]})
        return {"ok": all(item["ok"] for item in checks), "checks": checks}


def _entry_from_cmd(path: str) -> str:
    """从 npm 生成的 .cmd 里抠出真正的入口脚本。

    npm 在 Windows 上生成的 `doubao.cmd` 形如：
        "%_prog%"  "%dp0%\\node_modules\\@bytedance\\doubao-cli\\scripts\\run.cjs" %*
    ConPTY 不能直接起 .cmd，所以把它拆成 `node.exe + 入口脚本`。
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return ""
    match = re.search(r'%dp0%\\([^"]+\.(?:cjs|mjs|js))', text)
    if not match:
        return ""
    return os.path.join(os.path.dirname(os.path.abspath(path)), match.group(1).replace("/", os.sep))


def resolve_bin(config: dict) -> tuple:
    """把配置里的 bin 解成一个能直接 spawn 的 (bin, binArgs)。"""
    configured = str(config.get("bin") or "doubao")
    bin_args = [str(item) for item in (config.get("binArgs") or [])]
    path = shutil.which(configured) or (configured if os.path.isfile(configured) else "")
    if not path:
        return "", [], ""
    if os.name == "nt" and path.lower().endswith((".cmd", ".bat")) and not bin_args:
        entry = _entry_from_cmd(path)
        if entry and os.path.isfile(entry):
            node = os.path.join(os.path.dirname(path), "node.exe")
            if not os.path.isfile(node):
                node = shutil.which("node") or ""
            if node:
                return node, [entry], ""
    return path, bin_args, ""


def serve(host: str, port: int, open_url: bool, bin_override: str = "", bin_args_override=None) -> int:
    config = core.load_config()
    if bin_override:
        config["bin"] = bin_override
        config["binArgs"] = list(bin_args_override or [])
    bin_path, bin_args, _ = resolve_bin(config)
    if not bin_path:
        sys.stderr.write(
            "找不到 doubao 命令。先安装并登录 CLI：\n"
            "  社区版（Termux / Linux / macOS）：npm i -g doubao-cli@0.23.3\n"
            "  官方版（Windows / macOS / Linux）：npm i -g @bytedance/doubao-cli\n"
            "然后 doubao headless login --profile termux\n"
            "也可以显式指定：--bin <node.exe> --bin-arg <cli入口脚本>\n"
        )
        return 2
    config["bin"] = bin_path
    config["binArgs"] = bin_args

    broker = Broker()
    session = core.Session(config, broker.publish)
    Handler.session = session
    Handler.broker = broker

    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError as exc:
        sys.stderr.write(f"端口 {port} 起不来：{exc}\n换个端口：--port {port + 1}\n")
        return 1
    server.daemon_threads = True

    address = f"http://{host}:{port}"
    print("doubao-cli-gui 已启动")
    print(f"  地址    {address}")
    print(f"  工作区  {config.get('workspace')}")
    print(f"  profile {config.get('profile') or 'default'} · 权限 {config.get('permission')}")
    print("  Ctrl+C 停止")

    if open_url:
        opener = shutil.which("termux-open-url")
        try:
            if opener:
                subprocess.Popen([opener, address])
            else:
                webbrowser.open(address)
        except Exception:
            pass

    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description="doubao-cli-gui")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="启动后用浏览器打开")
    parser.add_argument("--bin", default="", help="CLI 可执行文件路径（默认读配置里的 bin）")
    parser.add_argument("--bin-arg", action="append", default=[], help="bin 前面的固定参数，可重复，例如入口脚本")
    args = parser.parse_args(argv)
    return serve(args.host, args.port, args.open, args.bin, args.bin_arg)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
