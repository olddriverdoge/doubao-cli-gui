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
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import core
import login_qr

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

    # -- 网页桥接

    def _cors(self) -> None:
        """只有 /api/web/* 开放跨域：网页脚本在 doubao.com 上直连本机 8765。"""
        if not urlparse(self.path).path.startswith("/api/web/"):
            return
        origin = self.headers.get("Origin") or "*"
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Doubao-Bridge")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _bridge_token(self) -> str:
        return str(self.session.config.get("bridge_token") or "")

    def _bridge_guard(self) -> bool:
        """写操作必须带 token：8765 一旦开放跨域，任何网页都能打过来。"""
        token = self._bridge_token()
        if not token:
            self._json({"ok": False, "error": "服务端还没生成 bridge token，重启一次 doubao-gui"}, 503)
            return False
        if (self.headers.get("X-Doubao-Bridge") or "").strip() != token:
            self._json({"ok": False, "error": "token 不对"}, 401)
            return False
        return True

    def _web_capture(self, payload: dict) -> dict:
        """落盘网页侧样本：请求体、SSE 帧、授权体。

        帧结构只能从真实样本反推，所以先存盘再对齐，不靠猜。
        """
        folder = os.path.join(core.CONFIG_DIR, "web-capture")
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        kind = re.sub(r"[^a-z0-9_-]+", "-", str(payload.get("kind") or "misc").lower())[:40] or "misc"
        name = f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}-{uuid.uuid4().hex[:6]}.json"
        try:
            with open(os.path.join(folder, name), "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False)[:200000])
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "file": name, "folder": folder}

    def _web_send(self, payload: dict) -> dict:
        """网页发来的消息交给本地 CLI 执行。

        Runner.start() 是非阻塞的，所以这里立刻返回受理结果；conversationId
        在新会话里要等 CLI 打印出来，页面侧靠轮询 /api/web/state 补。
        """
        text = str(payload.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "消息不能为空"}
        session = self.session
        if session.status in ("running", "approval"):
            return {"ok": False, "busy": True, "error": "上一轮还没结束，排队请稍后"}
        if payload.get("fresh"):
            # 页面开了新会话：chat 会话不能接 work 任务（CLI 会报 requires --mode work）
            session.new_session()
        cid = str(payload.get("conversationId") or "").strip()
        if cid and cid != session.conversation_id:
            switched = session.switch(cid)
            if not switched.get("ok"):
                return switched
        changed = False
        for key, value in (
            ("mode", payload.get("mode")),
            ("workspace", payload.get("workspace")),
            ("permission", payload.get("permission")),
            ("model", payload.get("model")),
            ("timeout", payload.get("timeout")),
        ):
            if value in (None, ""):
                continue
            session.config[key] = int(value) if key == "timeout" else str(value)
            changed = True
        if changed:
            core.save_config(session.config)
        result = session.send(text)
        if not result.get("ok"):
            return result
        snap = session.bridge_state()
        return {
            "ok": True,
            "accepted": True,
            "conversationId": snap["conversationId"],
            "runId": snap["runId"],
            "status": snap["status"],
            "pendingCid": not bool(snap["conversationId"]),
        }

    def _web_route(self, path: str, payload: dict) -> None:
        if path == "/api/web/capture":
            if not self._bridge_guard():
                return
            self._json(self._web_capture(payload))
            return
        if not self._bridge_guard():
            return
        if path == "/api/web/send":
            self._json(self._web_send(payload))
        elif path == "/api/web/stop":
            self._json(self.session.stop())
        elif path == "/api/web/approve":
            self._json(
                self.session.approve(
                    bool(payload.get("allow")),
                    str(payload.get("scope") or "once"),
                )
            )
        elif path == "/api/web/resume":
            self._json(self.session.resume())
        else:
            self._json({"ok": False, "error": "未知的桥接接口"}, 404)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
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
        if path == "/api/web/ping":
            self._json({
                "ok": True,
                "bridge": True,
                "version": core.VERSION,
                "tokenRequired": bool(self._bridge_token()),
                "capture": True,
            })
            return
        if path == "/api/web/state":
            if not self._bridge_guard():
                return
            self._json({"ok": True, "data": self.session.bridge_state()})
            return
        if path == "/api/events":
            self._sse()
            return
        if path == "/api/state":
            self._json({"ok": True, "data": self.session.snapshot()})
            return
        if path == "/api/sessions":
            query = parse_qs(urlparse(self.path).query)
            try:
                limit = int((query.get("limit") or ["20"])[0])
            except ValueError:
                limit = 20
            archived = (query.get("archived") or ["0"])[0].lower() in ("1", "true", "yes")
            self._json(self._list_sessions(limit, archived))
            return
        if path == "/api/session/read":
            query = parse_qs(urlparse(self.path).query)
            conversation_id = (query.get("id") or [""])[0]
            try:
                limit = int((query.get("limit") or ["60"])[0])
            except ValueError:
                limit = 60
            self._json(self._session_read(conversation_id, limit))
            return
        if path == "/api/models":
            self._json(self._list_models())
            return
        if path == "/api/doctor":
            self._json(self._doctor())
            return
        if path == "/api/login":
            _login_ensure_probe(self.session.config)
            self._json({"ok": True, **_login_snapshot()})
            return
        if path == "/api/release":
            force = "force=1" in (urlparse(self.path).query or "")
            self._json(_release_check(force=force))
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
        if path.startswith("/api/web/"):
            self._web_route(path, payload)
            return
        if path == "/api/send":
            try:
                result = self.session.send(str(payload.get("text") or ""))
            except Exception as exc:  # 兜底：接口异常也要回结构化错误
                result = {"ok": False, "error": str(exc)}
            self._json(result)
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
        elif path == "/api/login":
            try:
                seconds = int(payload.get("timeout") or 180)
            except Exception:
                seconds = 180
            profile = str(payload.get("profile") or "").strip()
            refresh = bool(payload.get("refresh"))
            self._json({"ok": True, **_login_arm(self.session.config, seconds, profile, refresh)})
        elif path == "/api/login/cookie":
            self._json(
                _login_cookie(
                    self.session.config,
                    str(payload.get("path") or ""),
                    str(payload.get("profile") or ""),
                )
            )
        elif path == "/api/login/cancel":
            self._json(_login_cancel())
        elif path == "/api/logout":
            self._json(_logout(self.session.config))
        elif path == "/api/shutdown":
            self._json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._json({"ok": False, "error": "未知接口"}, 404)

    def _model_name(self, value: str) -> str:
        """CLI 的 --model 只认名称（如“自动”），前端可能传数字 id（如 9），这里换回来。"""
        if not value or not value.isdigit():
            return value
        result = self._list_models()
        if not result.get("ok"):
            return value
        for row in result.get("models") or []:
            if str(row.get("id")) == value:
                return str(row.get("name") or value)
        return value

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
            config["model"] = self._model_name(str(payload["model"] or "").strip())
            changed = True
        if "reasoning" in payload:
            value = str(payload["reasoning"] or "").strip()
            if value and value not in core.REASONING_LEVELS:
                return {"ok": False, "error": "推理档位不合法"}
            config["reasoning"] = value
            changed = True
        if "timeout" in payload:
            try:
                value = int(payload["timeout"])
            except (TypeError, ValueError):
                return {"ok": False, "error": "超时必须是整数秒"}
            if not 30 <= value <= 3600:
                return {"ok": False, "error": "超时范围 30–3600 秒"}
            config["timeout"] = value
            changed = True
        if "skills" in payload:
            config["skills"] = bool(payload["skills"])
            changed = True
        if "enterprise" in payload:
            config["enterprise"] = bool(payload["enterprise"])
            changed = True
        if "project" in payload:
            config["project"] = str(payload["project"] or "").strip()
            changed = True
        if "mcp" in payload:
            config["mcp"] = [str(item) for item in (payload["mcp"] or []) if str(item).strip()]
            changed = True
        if "expect_json" in payload:
            config["expect_json"] = bool(payload["expect_json"])
            changed = True
        if "reply_schema" in payload:
            config["reply_schema"] = str(payload["reply_schema"] or "").strip()
            changed = True
        if "attach" in payload:
            config["attach"] = [str(item) for item in (payload["attach"] or []) if str(item).strip()]
            changed = True
        if "show_trace" in payload:
            config["show_trace"] = bool(payload["show_trace"])
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

    def _session_read(self, conversation_id: str, limit: int = 60) -> dict:
        conversation_id = (conversation_id or "").strip()
        if not conversation_id:
            return {"ok": False, "error": "缺少会话 ID"}
        limit = max(1, min(int(limit or 60), 100))
        messages = core.read_conversation(self.session.config, conversation_id, limit)
        if messages is None:
            return {"ok": False, "error": "读取会话失败"}
        return {"ok": True, "messages": messages, "conversationId": conversation_id}

    def _list_sessions(self, limit: int = 20, archived: bool = False) -> dict:
        limit = max(1, min(int(limit or 20), 100))
        args = ["sessions", "list", "--limit", str(limit)]
        if archived:
            args.append("--archived")
        args.append("--json")
        code, stdout, stderr = self._cli(self._base_argv(*args))
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
        return {
            "ok": True,
            "sessions": rows,
            "currentId": self.session.conversation_id,
            "limit": limit,
            "archived": archived,
        }

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
        config = self.session.config
        current = str(config.get("model") or "").strip()
        if current:
            names = {row["name"] for row in rows}
            mapped = next((row["name"] for row in rows if str(row["id"]) == current), "")
            if mapped and mapped != current and current not in names:
                config["model"] = mapped
                core.save_config(config)
                self.session.emit({"type": "snapshot", "data": self.session.snapshot()})
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


# -- 扫码登录

RELEASE_API = os.environ.get("DOUBAO_GUI_RELEASE_API") or (
    "https://api.github.com/repos/olddriverdoge/doubao-cli-gui/releases/latest"
)
RELEASE_URL = "https://github.com/olddriverdoge/doubao-cli-gui/releases"
RELEASE_TTL = float(os.environ.get("DOUBAO_GUI_RELEASE_TTL") or "60")
RELEASE_LOCK = threading.Lock()
RELEASE_CACHE: dict = {}


def _version_key(text: str) -> tuple:
    """把 v0.1.9 / 0.1.9-beta 之类压成可比较的数字元组。"""
    return tuple(int(item) for item in re.findall(r"\d+", text or "")[:4])


def _release_fetch() -> dict:
    request = Request(RELEASE_API)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "doubao-cli-gui")
    with urlopen(request, timeout=8) as response:
        body = response.read().decode("utf-8", "replace")
    return json.loads(body or "{}")


def _release_payload(data: dict, now: float) -> dict:
    current = str(core.VERSION)
    latest = str(data.get("tag_name") or data.get("name") or "").strip()
    assets = []
    for item in data.get("assets") or []:
        if not isinstance(item, dict):
            continue
        assets.append(
            {
                "name": str(item.get("name") or ""),
                "size": item.get("size") or 0,
                "url": str(item.get("browser_download_url") or ""),
            }
        )
    return {
        "ok": True,
        "current": current,
        "latest": latest,
        "latestName": str(data.get("name") or ""),
        "publishedAt": str(data.get("published_at") or ""),
        "url": str(data.get("html_url") or RELEASE_URL),
        "notes": str(data.get("body") or "")[:800],
        "hasUpdate": bool(_version_key(latest) > _version_key(current)),
        "assets": assets[:6],
        "checkedAt": int(now),
    }


def _release_check(force: bool = False) -> dict:
    now = time.time()
    with RELEASE_LOCK:
        cached = RELEASE_CACHE.get("payload")
        if cached and not force and now - float(RELEASE_CACHE.get("at") or 0) < RELEASE_TTL:
            return dict(cached, cached=True)
    try:
        payload = _release_payload(_release_fetch(), now)
    except HTTPError as exc:
        if int(getattr(exc, "code", 0) or 0) == 404:
            payload = {
                "ok": True,
                "current": str(core.VERSION),
                "latest": "",
                "latestName": "",
                "publishedAt": "",
                "url": RELEASE_URL,
                "notes": "",
                "hasUpdate": False,
                "assets": [],
                "empty": True,
                "hint": "仓库还没发布 Release，先去 Releases 页看看，或者直接 git pull",
                "checkedAt": int(now),
            }
        else:
            payload = {
                "ok": False,
                "error": f"取不到 Release：{exc}",
                "current": str(core.VERSION),
                "url": RELEASE_URL,
                "checkedAt": int(now),
            }
    except (URLError, json.JSONDecodeError, OSError, ValueError) as exc:
        payload = {
            "ok": False,
            "error": f"取不到 Release：{exc}",
            "current": str(core.VERSION),
            "url": RELEASE_URL,
            "checkedAt": int(now),
        }
    with RELEASE_LOCK:
        RELEASE_CACHE["payload"] = payload
        RELEASE_CACHE["at"] = now
    return dict(payload, cached=False)


LOGIN_LOCK = threading.Lock()
LOGIN = {
    "state": "idle",
    "qr": "",
    "qrAt": 0.0,
    "detail": "",
    "accountId": "",
    "loggedIn": False,
    "probed": False,
    "profile": "",
    "proc": None,
    "startedAt": 0.0,
}


def _login_snapshot() -> dict:
    with LOGIN_LOCK:
        now = time.time()
        return {
            "state": LOGIN["state"],
            "qr": LOGIN["qr"],
            "qrAge": int(now - LOGIN["qrAt"]) if LOGIN["qrAt"] else 0,
            "detail": LOGIN["detail"],
            "accountId": LOGIN["accountId"],
            "loggedIn": bool(LOGIN["loggedIn"]),
            "probed": bool(LOGIN["probed"]),
            "profile": LOGIN["profile"],
            "elapsed": int(now - LOGIN["startedAt"]) if LOGIN["startedAt"] else 0,
        }


def _login_tail(text: str) -> str:
    """从登录输出里挑一句人话当失败原因（跳过字符画）。"""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if any(ch in line for ch in "\u2580\u2584\u2588"):
            continue
        rows.append(line)
    return (rows[-1] if rows else "")[:200]


def _login_status(config: dict) -> tuple:
    """跑一次 headless status，返回 (loggedIn, accountId)。"""
    argv = [str(config["bin"])] + [str(item) for item in (config.get("binArgs") or [])]
    argv += ["headless", "status", "--json"]
    if config.get("profile"):
        argv += ["--profile", str(config["profile"])]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=25)
        data = json.loads((result.stdout or "").strip() or "{}")
        return bool(data.get("loggedIn")), str(data.get("accountId") or "")
    except Exception:
        return False, ""


def _login_probe(config: dict) -> None:
    """启动后在后台问一次登录态，不拖慢首屏。"""
    logged_in, account = _login_status(config)
    with LOGIN_LOCK:
        LOGIN["probed"] = True
        LOGIN["loggedIn"] = logged_in
        LOGIN["accountId"] = account or ""
        LOGIN["profile"] = str(config.get("profile") or "")
        if logged_in and LOGIN["state"] == "idle":
            LOGIN["detail"] = "已登录"


def _login_ensure_probe(config: dict) -> None:
    with LOGIN_LOCK:
        if LOGIN["probed"] or LOGIN["proc"] is not None:
            return
        LOGIN["probed"] = True  # 先占位，避免并发重复探测
    threading.Thread(target=_login_probe, args=(config,), daemon=True).start()


def _login_pump(proc: subprocess.Popen, config: dict) -> None:
    buffer = ""
    last_sig = None
    try:
        while True:
            line = proc.stdout.readline() if proc.stdout else b""
            if not line:
                break
            buffer += line.decode("utf-8", errors="replace")
            if len(buffer) > 60000:
                buffer = buffer[-60000:]
            # 只有字符画有变化才重算，避免每次 readline 都跑一遍 PNG 编码
            sig = (len(buffer), buffer.count("\u2588"), buffer.count("\u2580"), buffer.count("\u2584"))
            if sig == last_sig:
                continue
            last_sig = sig
            # 终端会把多张码/进度行堆在一起：只取最近一段连续的字符画行
            block_chars = set(" \u2588\u2580\u2584")
            rows = []
            for line in reversed(buffer.splitlines()):
                if len(line) >= 21 and all(ch in block_chars for ch in line):
                    rows.append(line)
                elif rows:
                    break
            url = ""
            if rows:
                candidates = ["\n".join(reversed(rows))]
                for size in (11, 12, 21, 22, 25, 26):
                    if len(rows) > size:
                        candidates.append("\n".join(reversed(rows[-size:])))
                for text in candidates:
                    url = login_qr.data_url(text)
                    if url:
                        break
            if url:
                with LOGIN_LOCK:
                    if url != LOGIN["qr"]:
                        LOGIN["qr"] = url
                        LOGIN["qrAt"] = time.time()
                        LOGIN["detail"] = "等待手机扫码确认"
    except Exception:
        pass
    try:
        code = proc.wait()
    except Exception:
        code = 1
    logged_in, account = _login_status(config) if code == 0 else (False, "")
    detail = _login_tail(buffer)
    with LOGIN_LOCK:
        if LOGIN.get("proc") is not proc:
            return  # 已被刷新或取消接管，不要覆盖新状态
        LOGIN["proc"] = None
        LOGIN["state"] = "ok" if code == 0 else "failed"
        LOGIN["loggedIn"] = logged_in
        if account:
            LOGIN["accountId"] = account
        if code == 0:
            LOGIN["detail"] = "已登录" if logged_in else "扫码流程结束，但状态仍未登录"
            LOGIN["qr"] = ""
            LOGIN["qrAt"] = 0.0
        else:
            LOGIN["detail"] = detail or "登录未完成"


def _login_cookie(config: dict, path: str, profile: str = "") -> dict:
    """用 Cookie 文件登录。

    新设备扫码基本一定会撞 MFA（errorCode 2156），Cookie 是唯一可靠的路，
    所以单给一个入口，不走二维码那条链路。
    """
    path = os.path.abspath(os.path.expanduser((path or "").strip()))
    if not path:
        return {"ok": False, "error": "请填写 Cookie 文件路径", **_login_snapshot()}
    if not os.path.isfile(path):
        return {"ok": False, "error": "Cookie 文件不存在：" + path, **_login_snapshot()}
    with LOGIN_LOCK:
        proc = LOGIN.get("proc")
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
    if profile:
        config["profile"] = profile
        core.save_config(config)
    argv = [str(config["bin"])] + [str(item) for item in (config.get("binArgs") or [])]
    argv += ["headless", "login", "--cookie-file", path, "--json"]
    if config.get("profile"):
        argv += ["--profile", str(config["profile"])]
    detail = ""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=90)
        code = result.returncode
        detail = _login_tail((result.stdout or "") + (result.stderr or "")) if code != 0 else ""
    except Exception as exc:
        code = 1
        detail = str(exc)
    logged_in, account = _login_status(config)
    with LOGIN_LOCK:
        LOGIN.update(
            {
                "state": "ok" if logged_in else "failed",
                "qr": "",
                "qrAt": 0.0,
                "proc": None,
                "loggedIn": bool(logged_in),
                "accountId": account or "",
                "probed": True,
                "detail": "" if logged_in else (detail or "Cookie 未能登录"),
            }
        )
    snapshot = _login_snapshot()
    if not logged_in:
        return {"ok": False, "error": detail or "Cookie 未能登录", **snapshot}
    return {"ok": True, **snapshot}


def _login_arm(config: dict, seconds: int, profile: str = "", refresh: bool = False) -> dict:
    """起一个 headless login 进程后立即返回，二维码由后台线程抓给页面轮询。"""
    if profile:
        config["profile"] = profile
        core.save_config(config)
        with LOGIN_LOCK:
            LOGIN["profile"] = profile
    with LOGIN_LOCK:
        proc = LOGIN.get("proc")
        if proc is not None and proc.poll() is None and not refresh:
            return _login_snapshot()
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
    argv = [str(config["bin"])] + [str(item) for item in (config.get("binArgs") or [])]
    argv += ["headless", "login", "--timeout", str(max(30, int(seconds)))]
    if config.get("profile"):
        argv += ["--profile", str(config["profile"])]
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except Exception as exc:
        with LOGIN_LOCK:
            LOGIN.update({"state": "failed", "detail": str(exc), "qr": "", "qrAt": 0.0,
                          "proc": None, "loggedIn": False, "probed": True})
        return _login_snapshot()
    with LOGIN_LOCK:
        LOGIN.update({
            "state": "waiting", "qr": "", "qrAt": 0.0, "detail": "正在向 CLI 申请二维码…",
            "accountId": "", "loggedIn": False, "proc": proc,
            "startedAt": time.time(), "probed": True,
        })
    threading.Thread(target=_login_pump, args=(proc, config), daemon=True).start()
    return _login_snapshot()


def _login_cancel() -> dict:
    with LOGIN_LOCK:
        proc = LOGIN.get("proc")
        LOGIN.update({"state": "idle", "qr": "", "qrAt": 0.0, "detail": "", "proc": None,
                      "accountId": "", "startedAt": 0.0})
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
    return {"ok": True, **_login_snapshot()}


def _logout(config: dict) -> dict:
    """退出登录：先停掉还在跑的登录进程，再执行 headless logout。"""
    with LOGIN_LOCK:
        proc = LOGIN.get("proc")
        LOGIN.update({"state": "idle", "qr": "", "qrAt": 0.0, "proc": None, "startedAt": 0.0})
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
    argv = [str(config["bin"])] + [str(item) for item in (config.get("binArgs") or [])]
    argv += ["headless", "logout"]
    if config.get("profile"):
        argv += ["--profile", str(config["profile"])]
    detail = ""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            detail = _login_tail((result.stdout or "") + (result.stderr or "")) or "退出登录失败"
    except Exception as exc:
        detail = str(exc)
    with LOGIN_LOCK:
        LOGIN["loggedIn"] = False
        LOGIN["accountId"] = ""
        LOGIN["probed"] = True
        LOGIN["detail"] = "已退出登录" if not detail else detail
    return {"ok": not detail, "error": detail, **_login_snapshot()}


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

    token = str(config.get("bridge_token") or "")
    if not token:
        token = secrets.token_hex(8)
        config["bridge_token"] = token
        core.save_config(config)

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
    print(f"  桥接    {config.get('bridge_token')}（网页脚本里填这个）")
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
