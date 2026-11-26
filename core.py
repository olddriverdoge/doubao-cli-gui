#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doubao-cli-gui 后端核心。

把社区版 doubao-cli 的 headless 会话跑在一个伪终端里：stdin / stderr 必须是 TTY
（CLI 用 `process.stdin.isTTY && process.stderr.isTTY` 决定是否弹审批），
stdout 在 POSIX 上单独走管道，用来收 `--json` 回执。

伪终端由 `term_io` 提供：POSIX 用标准库 pty，Windows 用 ConPTY（pywinpty）。
本文件不直接 import pty / termios / fcntl / select，保证两个平台同一套逻辑。

上层（HTTP / SSE）只通过 `emit(dict)` 拿事件，不关心命令细节。
"""

from __future__ import annotations

import glob
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import term_io

VERSION = "0.1.14"

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "doubao-cli-gui"
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
SESSION_DIR = os.path.join(CONFIG_DIR, "sessions")

DEFAULT_CONFIG = {
    "bin": "doubao",
    # 有些平台（尤其 Windows）上 `doubao` 只是个 .cmd 脚本，直接当 argv[0] 起不来。
    # 这里允许把它换成“解释器 + 脚本”的形式，例如：
    #   bin = node.exe / python.exe
    #   binArgs = ["...\node_modules\@bytedance\doubao-cli\dist\index.js"]
    "binArgs": [],
    "profile": "termux",
    "workspace": "",
    "mode": "work",
    "permission": "ask_risky",
    "timeout": 600,
    "model": "",
    "reasoning": "",
    # 默认不自动放行任何审批：GUI 负责把每个审批交给用户确认，
    # 想省事就用审批卡上的“本轮都允许”。
    "auto_approve": [],
    # 0.1.12：请求级选项，只作用于本轮消息，不改 CLI 的默认
    "skills": True,
    "enterprise": False,
    "project": "",
    "mcp": [],
    "expect_json": False,
    "reply_schema": "",
    "attach": [],
    # 0.1.12：过程面板
    "show_trace": True,
}

PERM_TO_CLI = {
    "ask_risky": "AskOnRisk",
    "ask_always": "AlwaysAsk",
    "always_allow": "FullAccess",
    "AskOnRisk": "AskOnRisk",
    "AlwaysAsk": "AlwaysAsk",
    "FullAccess": "FullAccess",
}
REASONING_LEVELS = ("low", "medium", "high", "xhigh", "max")

SESSION_MUTATIONS = ("rename", "pin", "unpin", "archive", "unarchive", "move")
SESSION_ID_RE = re.compile(r"^[1-9]\d{11,23}$")

PERM_LABELS = {
    "ask_risky": "风险操作时确认",
    "ask_always": "始终确认",
    "always_allow": "允许所有操作",
}

ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
OPEN_RE = re.compile(r"\[y/N\]\s*$")
APPROVE_RE = re.compile(r"\[local runtime: (.*?); (.*?)\] (Approve|Deny) (.+?)\? \[y/N\]", re.S)


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _unwrap(text: str) -> str:
    """把终端硬折行还原成一行。

    折行是终端按列宽自动插的换行，不是内容里的换行，而且折行处不补空格
    （ConPTY 上甚至会把 "[y/N]" 拆成 "[" + 换行 + "y/N]"）。审批提示是单行
    文本，所以匹配前先压掉换行；这个方法只用于“识别提示”，不改动要展示的原文。
    """
    return text.replace("\r\n", "").replace("\r", "").replace("\n", "")


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            config.update(json.load(handle))
    except FileNotFoundError:
        pass
    except Exception:
        pass
    if not config.get("workspace"):
        config["workspace"] = os.getcwd()
    return config


def build_session_action(
    bin_name: str,
    profile: str,
    action: str,
    conversation_id: str,
    value: str = "",
    bin_args: Optional[List[str]] = None,
) -> tuple:
    """构造 sessions rename/pin/... 的命令，参数非法时返回 (None, 错误)。"""
    action = (action or "").strip()
    conversation_id = (conversation_id or "").strip()
    value = (value or "").strip()
    if action not in SESSION_MUTATIONS:
        return None, "不支持的操作"
    if not SESSION_ID_RE.match(conversation_id):
        return None, "会话 ID 不合法"
    if action == "rename":
        if not value:
            return None, "标题不能为空"
        if len(value) > 20:
            return None, "标题最多 20 个字"
    elif action == "move":
        if not value:
            return None, "要指定项目 ID / 名称，或输入 none 清空"
        if len(value) > 64:
            return None, "项目名太长"
    elif value:
        return None, "该操作不接受额外参数"
    argv = [bin_name] + [str(item) for item in (bin_args or [])] + ["headless", "sessions", action, conversation_id]
    if value:
        argv.append(value)
    if profile:
        argv += ["--profile", str(profile)]
    return argv, ""


def save_config(config: dict) -> None:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        temporary = CONFIG_PATH + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, CONFIG_PATH)
    except Exception:
        pass


# ---------------------------------------------------------------- pty 运行器


@dataclass
class Turn:
    argv: List[str]
    label: str
    proc: Optional[subprocess.Popen] = None
    term: Optional[term_io.Terminal] = None
    conversation_id: str = ""
    run_id: str = ""
    started_at: float = 0.0
    phase: str = "提交中"
    raw_err: str = ""
    stdout_buf: str = ""
    merged_buf: str = ""
    result: Optional[dict] = None
    error: str = ""
    exit_code: Optional[int] = None
    approval: Optional[dict] = None
    finished: bool = False
    sent: bool = False
    accepted: bool = False
    merged_stream: bool = False
    log_buf: str = ""


class Runner:
    """一次 headless 会话调用的生命周期。"""

    def __init__(self, config: dict, on_event: Callable[[str, object], None]):
        self.config = config
        self.on_event = on_event
        self.turn: Optional[Turn] = None
        self.lock = threading.Lock()

    # -- 命令构建

    def _bin_argv(self, *args: str) -> List[str]:
        """argv[0] 是可执行文件；binArgs 允许把 "解释器 + 脚本" 拼在前面。

        Windows 上 npm 装出来的 `doubao` 是 .cmd 脚本，ConPTY 直接起不来，
        所以支持配置成 `node.exe <cli.js>` 这种形式。
        """
        bin_path = str(self.config["bin"])
        bin_args = [str(item) for item in (self.config.get("binArgs") or [])]
        base = os.path.basename(bin_path).lower()
        if not bin_args and base in ("python.exe", "python", "python3", "py", "python3.exe", "py.exe"):
            raise RuntimeError(
                "配置的 bin 是 Python 解释器但没有 binArgs，命令行会变成 `python headless ...`。"
                "请用 --bin-arg 指定要运行的脚本（例如 --bin-arg tests/mock_doubao.py）。"
            )
        return [bin_path] + bin_args + list(args)

    def _local_flags(self) -> List[str]:
        """本地运行时相关参数。

        headless 的 chat 模式走云端，明确禁止带 --runtime / --workspace /
        --permission（CLI 会报 “requires --mode work”），所以按 mode 分开拼。
        """
        config = self.config
        if str(config.get("mode") or "work") == "chat":
            return []
        return [
            "--runtime", "local",
            "--workspace", config.get("workspace") or os.getcwd(),
            "--permission", PERM_TO_CLI.get(str(config.get("permission")), "AskOnRisk"),
        ]

    def _profile_flags(self) -> List[str]:
        profile = self.config.get("profile")
        return ["--profile", str(profile)] if profile else []

    def _model_flags(self) -> List[str]:
        flags: List[str] = []
        model = str(self.config.get("model") or "").strip()
        if model:
            flags += ["--model", model]
        reasoning = str(self.config.get("reasoning") or "").strip()
        if reasoning in REASONING_LEVELS:
            flags += ["--reasoning", reasoning]
        return flags

    def _wait_flags(self) -> List[str]:
        return ["--wait", "--timeout", str(int(self.config.get("timeout") or 600)), "--json"]

    def _request_flags(self) -> List[str]:
        """本轮请求级选项：附件、项目、企业知识、技能开关、MCP、结构化回复。

        这些只对该条消息生效，不写进 CLI 的默认配置。
        """
        config = self.config
        flags: List[str] = []
        for path in config.get("attach") or []:
            path = str(path or "").strip()
            if path:
                flags += ["--attach", path]
        if str(config.get("mode") or "work") == "chat":
            # chat 走云端：项目 / 企业知识 / 技能 / MCP / 权限这些一律不能带，
            # CLI 会直接报 “requires --mode work”。附件例外，它跟模式无关。
            return flags
        project = str(config.get("project") or "").strip()
        if project:
            flags += ["--project", project]
        if config.get("enterprise"):
            flags.append("--enterprise-knowledge")
        for connector in config.get("mcp") or []:
            connector = str(connector or "").strip()
            if connector:
                flags += ["--mcp", connector]
        if config.get("skills") is False:
            flags.append("--no-skills")
        if config.get("expect_json"):
            flags.append("--expect-json")
            schema = str(config.get("reply_schema") or "").strip()
            if schema:
                flags += ["--reply-schema", schema]
        return flags

    def build_create(self, message: str) -> List[str]:
        argv = self._bin_argv("headless", "sessions", "create")
        argv += ["--mode", str(self.config.get("mode") or "work")]
        argv += self._local_flags()
        argv += self._request_flags()
        argv += self._model_flags()
        argv += self._profile_flags()
        argv += self._wait_flags()
        if message.startswith("-"):
            argv.append("--")  # 防止以短横线开头的消息被当成选项
        argv.append(message)
        return argv

    def build_send(self, conversation_id: str, message: str) -> List[str]:
        argv = self._bin_argv("headless", "sessions", "send", conversation_id)
        argv += self._local_flags()
        argv += self._request_flags()
        argv += self._model_flags()
        argv += self._profile_flags()
        argv += self._wait_flags()
        if message.startswith("-"):
            argv.append("--")
        argv.append(message)
        return argv

    # -- 启动与收尾

    def start(self, argv: List[str], label: str) -> Optional[Turn]:
        with self.lock:
            if self.turn and not self.turn.finished:
                return None
            self.turn = Turn(argv=argv, label=label)
        turn = self.turn
        try:
            term = term_io.spawn(argv, env=term_io.child_env())
            turn.term = term
            turn.proc = getattr(term, "proc", None)
            turn.merged_stream = bool(term.merged)
            turn.started_at = time.time()
        except FileNotFoundError:
            turn.error = f"找不到命令：{argv[0]}"
            turn.finished = True
            turn.phase = "失败"
            self.on_event("turn_end", turn)
            return turn
        except Exception as exc:
            turn.error = f"启动失败：{exc}"
            turn.finished = True
            turn.phase = "失败"
            self.on_event("turn_end", turn)
            return turn

        threading.Thread(target=self._pump, args=(turn,), daemon=True).start()
        self.on_event("turn_start", turn)
        return turn

    def _emit_log(self, turn: Turn, text: str) -> None:
        """把 CLI 的原始输出按行转给界面。

        JSON 回执和分隔线不转，避免刷屏；截断到 400 字，防止单行日志把界面撑爆。
        """
        turn.log_buf += text
        while "\n" in turn.log_buf:
            line, turn.log_buf = turn.log_buf.split("\n", 1)
            line = strip_ansi(line).strip("\r").rstrip()
            if not line or not line.strip():
                continue
            body = line.lstrip()
            if body.startswith("{") and (
                    '"platform"' in body or '"runId"' in body or '"conversationId"' in body):
                continue
            if not line.strip(" -=*#"):
                continue
            self.on_event("log", {"text": line[-400:]})
        if len(turn.log_buf) > 4000:
            turn.log_buf = turn.log_buf[-2000:]

    def _pump(self, turn: Turn) -> None:
        """读循环：每条流一个阻塞读线程，主循环只消费队列。

        Windows 的 ConPTY 句柄塞不进 select，所以两个平台统一走「线程 + 队列」，
        好处是 POSIX 上的 select 依赖被彻底删掉，逻辑只剩一份。
        """
        term = turn.term
        if term is None:
            self._finalize(turn)
            return
        events: "queue.Queue" = queue.Queue()
        stop_flag = threading.Event()
        err_text = ""
        consumed = 0

        def ingest(kind: str, chunk: bytes) -> None:
            nonlocal err_text, consumed
            text = chunk.decode("utf-8", "replace")
            if kind in ("err", "merged"):
                err_text += text
                turn.raw_err = (turn.raw_err + strip_ansi(text))[-4000:]
                clean = strip_ansi(err_text)
                # 终端的硬折行会在句子中间插换行（ConPTY 尤其明显，可能把
                # "[y/N]" 拆成 "[" + 换行 + "y/N]"），匹配前先把折行还原。
                flow = _unwrap(clean)
                if turn.approval is None and OPEN_RE.search(flow[consumed:]):
                    turn.approval = self._parse_approval(flow)
                    turn.phase = "待审批"
                    consumed = len(flow)
                    self.on_event("approval", turn)
            if kind == "out":
                turn.stdout_buf += text
            elif kind == "merged":
                turn.merged_buf += text
            self._emit_log(turn, text)

        def reader(channel) -> None:
            while not stop_flag.is_set():
                data = channel.read()
                events.put((channel.kind, data))
                if not data:
                    return

        for channel in term.channels:
            threading.Thread(target=reader, args=(channel,), daemon=True).start()

        open_kinds = {channel.kind for channel in term.channels}
        while True:
            try:
                kind, chunk = events.get(timeout=0.2)
            except queue.Empty:
                if term.poll() is not None:
                    break
                continue
            if not chunk:
                open_kinds.discard(kind)
                if not open_kinds and term.poll() is not None:
                    break
                continue
            ingest(kind, chunk)

        # 收尾：把进程退出瞬间还压在队列里的尾巴读掉，避免丢 JSON 回执
        deadline = time.time() + 0.5
        while time.time() < deadline:
            try:
                kind, chunk = events.get(timeout=0.05)
            except queue.Empty:
                if term.poll() is not None:
                    break
                continue
            if chunk:
                ingest(kind, chunk)

        stop_flag.set()
        turn.exit_code = term.poll()
        if not turn.finished:
            self._finalize(turn)

    def _finalize(self, turn: Turn) -> None:
        turn.finished = True
        if turn.term is not None:
            try:
                turn.term.close()
            except Exception:
                pass
        payload = self._extract_json(turn.stdout_buf)
        if payload is None and turn.merged_stream:
            # ConPTY 只有一个合并流，回执和提示混在一起，只能从尾巴里抠 JSON
            payload = self._extract_json(turn.merged_buf)
        if payload is not None:
            turn.result = payload
            turn.conversation_id = str(payload.get("conversationId") or turn.conversation_id or "")
            turn.run_id = str(payload.get("runId") or turn.run_id or "")
        text = self._reply_text(payload)
        if text:
            turn.phase = "完成"
        elif payload is not None:
            status = str(payload.get("status") or "")
            turn.phase = {"failed": "失败", "cancelled": "已中止"}.get(status, "未拿到回复")
        elif turn.error:
            turn.phase = "失败"
        else:
            turn.phase = "结束"
        if payload and payload.get("error") and not turn.error:
            turn.error = str(payload["error"])
        if turn.exit_code not in (0, None) and not turn.error:
            tail = turn.raw_err.strip().splitlines()
            turn.error = tail[-1][:200] if tail else f"命令退出码 {turn.exit_code}"
        self.on_event("turn_end", turn)

    @staticmethod
    def _extract_json(text: str) -> Optional[dict]:
        if not text.strip():
            return None
        text = strip_ansi(text).strip()
        # 同上：折行会把 JSON 字符串里插进真实换行，JSON 就不合法了
        text = _unwrap(text).strip()
        try:
            value = json.loads(text)
            if isinstance(value, dict):
                return value
        except Exception:
            pass
        candidates: List[dict] = []
        for start, char in enumerate(text):
            if char != "{":
                continue
            depth = 0
            in_str = False
            escaped = False
            for pos in range(start, len(text)):
                ch = text[pos]
                if in_str:
                    if escaped:
                        escaped = False
                    elif ch == "\\":
                        escaped = True
                    elif ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(text[start : pos + 1])
                        except Exception:
                            break
                        if isinstance(obj, dict):
                            candidates.append(obj)
                        break
        for obj in candidates:
            if any(key in obj for key in ("conversationId", "runId", "platform", "status")):
                return obj
        return candidates[0] if candidates else None

    @staticmethod
    def _reply_text(payload: Optional[dict]) -> str:
        if not payload:
            return ""
        reply = payload.get("reply")
        if isinstance(reply, dict):
            text = reply.get("text") or ""
            if text:
                return text
        if isinstance(reply, str):
            return reply
        for key in ("answer", "text", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""

    def _parse_approval(self, clean: str) -> dict:
        tail = clean[-2000:]
        match = None
        for match in APPROVE_RE.finditer(tail):
            pass
        info = {
            "tool": "",
            "operation": "",
            "risk": "",
            "permission": "",
            "full_access": False,
        }
        if match:
            info["permission"] = match.group(1)
            info["risk"] = match.group(2)
            head = match.group(4).strip().split(";")[0].strip()
            if ":" in head:
                info["tool"], info["operation"] = [part.strip() for part in head.split(":", 1)]
            else:
                info["tool"] = head
                info["operation"] = head
        else:
            if "Enable FullAccess" in tail or "FullAccess for this turn" in tail:
                info["full_access"] = True
                info["tool"] = "FullAccess"
                info["operation"] = "本轮请求完全访问权限"
            elif "Approve " in tail:
                fragment = tail[tail.rfind("Approve ") + len("Approve ") :]
                info["operation"] = fragment.split(";")[0].strip()
                info["tool"] = info["operation"].split(":")[0].strip()
        return info

    def approve(self, allowed: bool) -> bool:
        turn = self.turn
        if not turn or turn.finished or turn.approval is None or turn.term is None:
            return False
        # Windows 的 ConPTY 用 CRLF 交回车；POSIX 上多一个 \r 可能被当成两次回车
        enter = b"\r\n" if term_io.IS_WIN else b"\n"
        if not turn.term.write((b"y" if allowed else b"n") + enter):
            return False
        turn.approval = None
        turn.phase = "执行中"
        return True

    def stop(self) -> bool:
        turn = self.turn
        if not turn or turn.finished or turn.term is None:
            return False
        if turn.term.poll() is not None:
            return False
        try:
            turn.term.terminate()
        except Exception:
            return False
        turn.phase = "已中止"
        self.on_event("turn_update", turn)
        return True


# ---------------------------------------------------------------- 会话状态


def now_ms() -> int:
    return int(time.time() * 1000)


TRACE_INTERVAL = 1.2
ROLE_MAP = {"user": "user", "assistant": "assistant", "bot": "assistant", "system": "system"}


def _as_text(value) -> str:
    """receipt 里的 thinking / answer 可能是字符串，也可能是块数组。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return "".join(parts)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("content") or "")
    return ""


def cli_turns_dir() -> str:
    """CLI 的 receipt 目录：默认 ~/.config/doubao-cli/turns，可用环境变量覆盖。"""
    root = os.environ.get("DOUBAO_CLI_CONFIG_DIR") or os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "doubao-cli"
    )
    return os.path.join(root, "turns")


def latest_receipt(conversation_id: str) -> Optional[dict]:
    """同一会话同一时间只有一个 run，取 mtime 最新的那份 receipt。"""
    if not conversation_id:
        return None
    paths = glob.glob(os.path.join(cli_turns_dir(), "*", "%s-*.json" % conversation_id))
    if not paths:
        return None
    try:
        path = max(paths, key=lambda item: os.path.getmtime(item))
    except OSError:
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    data["_path"] = path
    return data


def parse_receipt(data: dict) -> dict:
    steps = []
    for item in data.get("handoffs") or []:
        if not isinstance(item, dict):
            continue
        steps.append(
            {
                "id": str(item.get("taskId") or ""),
                "scene": item.get("appendScene"),
                "completed": bool(item.get("completed")),
            }
        )
    return {
        "conversationId": str(data.get("conversationId") or ""),
        "runId": str(data.get("runId") or ""),
        "thinking": _as_text(data.get("thinking")),
        "answer": _as_text(data.get("answer")),
        "steps": steps[-12:],
        "completed": bool(data.get("completed")),
        "at": now_ms(),
    }


def read_conversation(config: dict, conversation_id: str, limit: int = 60) -> Optional[list]:
    """用 CLI 的 sessions read 回读服务端历史，GUI 不再只有本地那点记录。"""
    if not conversation_id:
        return None
    argv = [str(config["bin"])] + [str(item) for item in (config.get("binArgs") or [])]
    argv += ["headless", "sessions", "read", conversation_id, "--limit", str(int(limit)), "--json"]
    if config.get("profile"):
        argv += ["--profile", str(config["profile"])]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=40)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads((result.stdout or "").strip() or "{}")
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    rows = data.get("messages")
    if not isinstance(rows, list):
        inner = data.get("result")
        rows = inner.get("messages") if isinstance(inner, dict) else None
    if not isinstance(rows, list):
        return None
    messages = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or row.get("content") or "").strip()
        if not text:
            continue
        role = ROLE_MAP.get(str(row.get("role") or "").lower(), "assistant")
        messages.append(
            {
                "id": str(row.get("messageId") or row.get("id") or uuid.uuid4().hex[:12]),
                "role": role,
                "text": text,
                "ts": 0,
            }
        )
    return messages


AUTO_RESUME_LIMIT = 3
AUTO_RESUME_DELAY = 2.0


def pending_view(pending) -> Optional[dict]:
    """把 CLI 的 pending 数组收敛成前端能直接渲染的结构。"""
    if not isinstance(pending, list) or not pending:
        return None
    items: List[dict] = []
    for entry in pending[:4]:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or "")
        if kind == "input":
            questions = []
            for question in entry.get("questions") or []:
                if not isinstance(question, dict):
                    continue
                text = question.get("title") or question.get("text") or question.get("question") or ""
                options = []
                for option in question.get("options") or []:
                    if isinstance(option, str):
                        options.append(option)
                    elif isinstance(option, dict):
                        value = option.get("title") or option.get("text") or option.get("label") or option.get("value")
                        if value:
                            options.append(str(value))
                questions.append({"text": str(text or ""), "options": options})
            items.append({"kind": "input", "questions": questions, "clarifyId": entry.get("clarifyId")})
        elif kind == "approval":
            options = []
            for option in entry.get("items") or []:
                if isinstance(option, str):
                    options.append(option)
                elif isinstance(option, dict):
                    value = option.get("title") or option.get("text") or option.get("label")
                    if value:
                        options.append(str(value))
            items.append({"kind": "approval", "scene": entry.get("scene"), "options": options})
    return {"items": items} if items else None


def idle_turn(payload: dict) -> bool:
    """服务端没确认到任何任务节点，也没有进度文本 → 多半是本地执行器断线。"""
    tasks = payload.get("tasks") or {}
    try:
        total = int(tasks.get("total") or 0)
    except (TypeError, ValueError):
        total = 0
    return total == 0 and not str(payload.get("progress") or "").strip()


class Session:
    """一个对话的状态机，对外只发事件。"""

    def __init__(self, config: dict, emit: Callable[[dict], None]):
        self.config = config
        self.emit = emit
        self.runner = Runner(config, self._on_runner)
        self.messages: List[dict] = []
        self.status = "idle"
        self.phase = "空闲"
        self.conversation_id = str(config.get("last_conversation") or "")
        self.run_id = ""
        self.started_at = 0.0
        self.approval: Optional[dict] = None
        self.auto_backup: Optional[list] = None
        self.pending: Optional[dict] = None
        self.stalled = False
        self.auto_resume = 0
        self.trace: Optional[dict] = None
        self._trace_key = ""
        self.logs: List[str] = []
        self.log_seq = 0
        self._load_history()
        threading.Thread(target=self._ticker, daemon=True).start()
        threading.Thread(target=self._trace_ticker, daemon=True).start()

    # -- 持久化

    def _history_path(self) -> str:
        name = self.conversation_id or "draft"
        return os.path.join(SESSION_DIR, f"{name}.json")

    def _load_history(self) -> None:
        path = self._history_path()
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self.messages = list(data.get("messages") or [])
            self.conversation_id = str(data.get("conversationId") or self.conversation_id or "")
        except Exception:
            self.messages = []

    def _save_history(self) -> None:
        try:
            os.makedirs(SESSION_DIR, exist_ok=True)
            path = self._history_path()
            temporary = path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(
                    {"conversationId": self.conversation_id, "messages": self.messages[-200:]},
                    handle,
                    ensure_ascii=False,
                )
            os.replace(temporary, path)
        except Exception:
            pass

    # -- 快照

    def snapshot(self) -> dict:
        return {
            "status": self.status,
            "phase": self.phase,
            "conversationId": self.conversation_id,
            "runId": self.run_id,
            "startedAt": self.started_at,
            "elapsed": int(time.time() - self.started_at) if self.started_at else 0,
            "approval": self.approval,
            "pending": self.pending,
            "stalled": self.stalled,
            "trace": self.trace,
            "logs": self.logs[-200:],
            "logSeq": self.log_seq,
            "messages": self.messages,
            "version": VERSION,
            "config": {
                "bin": self.config.get("bin"),
                "binArgs": list(self.config.get("binArgs") or []),
                "workspace": self.config.get("workspace"),
                "model": self.config.get("model"),
                "reasoning": self.config.get("reasoning"),
                "permission": self.config.get("permission"),
                "permissionLabel": PERM_LABELS.get(self.config.get("permission"), ""),
                "mode": self.config.get("mode"),
                "profile": self.config.get("profile"),
                "timeout": int(self.config.get("timeout") or 600),
                "skills": self.config.get("skills", True),
                "enterprise": bool(self.config.get("enterprise")),
                "project": self.config.get("project") or "",
                "mcp": list(self.config.get("mcp") or []),
                "expectJson": bool(self.config.get("expect_json")),
                "replySchema": self.config.get("reply_schema") or "",
                "attach": list(self.config.get("attach") or []),
                "showTrace": self.config.get("show_trace", True),
                "bridgeToken": self.config.get("bridge_token") or "",
            },
            "permissions": PERM_LABELS,
        }

    def _cloud_settle(self) -> str:
        """问云端这一轮到底结束了没有。

        CLI 进程退出不等于 run 收尾。云端还报 running 时官方页面会一直转圈，
        所以结束判定以 sessions status 为准，不靠本地猜。
        """
        if not (self.conversation_id and self.run_id):
            return ""
        argv = self.runner._bin_argv("headless", "sessions", "status", self.conversation_id)
        argv += ["--run", self.run_id]
        if self.config.get("profile"):
            argv += ["--profile", str(self.config["profile"])]
        argv += ["--json"]
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=8)
        except Exception:
            return ""
        if result.returncode != 0:
            return ""
        try:
            payload = json.loads(result.stdout)
        except Exception:
            return ""
        if str(payload.get("status") or "") != "completed":
            return ""
        return Runner._reply_text(payload)

    def bridge_state(self) -> dict:
        """给网页桥接用的精简状态：够画一条浮动条，不夹带整份历史。"""
        return {
            "status": self.status,
            "phase": self.phase,
            "conversationId": self.conversation_id,
            "runId": self.run_id,
            "stalled": self.stalled,
            "approval": self.approval,
            "pending": self.pending,
            "canSend": self.status not in ("running", "approval"),
            "canResume": bool(self.conversation_id and self.run_id),
            "canStop": self.status in ("running", "approval", "stalled"),
            "workspace": self.config.get("workspace"),
            "mode": self.config.get("mode"),
            "permission": self.config.get("permission"),
            "model": self.config.get("model"),
            "profile": self.config.get("profile"),
            "timeout": int(self.config.get("timeout") or 600),
            "version": VERSION,
            "messageCount": len(self.messages),
            "logs": self.logs[-40:],
        }

    def _status_event(self) -> dict:
        return {
            "type": "status",
            "data": {
                "status": self.status,
                "phase": self.phase,
                "conversationId": self.conversation_id,
                "runId": self.run_id,
                "startedAt": self.started_at,
                "approval": self.approval,
                "pending": self.pending,
                "stalled": self.stalled,
            },
        }

    def _add_message(self, role: str, text: str) -> dict:
        message = {"id": uuid.uuid4().hex[:12], "role": role, "text": text, "ts": now_ms()}
        self.messages.append(message)
        self.messages = self.messages[-200:]
        self._save_history()
        self.emit({"type": "message", "data": message})
        return message

    def _notice(self, text: str) -> None:
        self._add_message("system", text)

    # -- 发送

    def send(self, text: str) -> dict:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "消息不能为空"}
        if self.status in ("running", "approval"):
            return {"ok": False, "error": "上一轮还没结束"}
        self.pending = None
        self.stalled = False
        self.auto_resume = 0
        self.trace = None
        self._trace_key = ""
        self.logs = []
        self.log_seq = 0
        self._add_message("user", text)
        self.status = "running"
        self.phase = "提交中"
        self.started_at = time.time()
        self.approval = None
        self.emit(self._status_event())
        try:
            if self.conversation_id:
                argv = self.runner.build_send(self.conversation_id, text)
                label = "send"
            else:
                argv = self.runner.build_create(text)
                label = "create"
        except RuntimeError as exc:
            # 命令拼不出来（例如 bin 指向解释器却漏了 binArgs）：干净地回到空闲，
            # 把原因交给界面提示，而不是让异常把连接打断。
            if (self.messages and self.messages[-1].get("role") == "user"
                    and self.messages[-1].get("text") == text):
                self.messages.pop()
                self._save_history()
            self.status = "idle"
            self.phase = "空闲"
            self.started_at = None
            self.emit(self._status_event())
            return {"ok": False, "error": str(exc)}
        turn = self.runner.start(argv, label)
        if self.config.get("attach"):
            # 附件只作用于这一条消息，发完就清掉，避免下一轮又偷偷带上
            self.config["attach"] = []
            save_config(self.config)
        if turn is None:
            self.status = "idle"
            self.phase = "空闲"
            self.started_at = 0.0
            self.emit(self._status_event())
            return {"ok": False, "error": "已有任务在跑"}
        return {"ok": True, "conversationId": self.conversation_id}

    def approve(self, allowed: bool, scope: str = "once") -> dict:
        if self.approval is None:
            return {"ok": False, "error": "没有待审批的请求"}
        detail = self.approval
        self.approval = None
        if allowed and scope == "turn" and self.auto_backup is None:
            self.auto_backup = list(self.config.get("auto_approve") or [])
            self.config["auto_approve"] = ["*"]
        elif scope == "always":
            self.config["auto_approve"] = ["*"]
            save_config(self.config)
        if not self.runner.approve(allowed):
            return {"ok": False, "error": "审批已失效"}
        self._notice(("已允许" if allowed else "已拒绝") + " · " + (detail.get("tool") or "操作"))
        self.status = "running"
        self.phase = "执行中"
        self.emit(self._status_event())
        return {"ok": True}

    def stop(self) -> dict:
        """先让服务端把这一轮标记为停止，再收掉本地执行器。

        只 terminate 本地进程的话，界面上写着“已中止”，服务端那一轮可能还在跑。
        """
        server_note = ""
        if self.conversation_id:
            argv = self.runner._bin_argv("headless", "sessions", "stop", self.conversation_id)
            if self.run_id:
                argv += ["--run", self.run_id]
            argv += ["--json"]
            try:
                result = subprocess.run(argv, capture_output=True, text=True, timeout=20)
                if result.returncode == 0:
                    server_note = "服务端已标记停止"
            except Exception:
                server_note = ""
        if not self.runner.stop():
            if server_note:
                self.status = "done"
                self.phase = "已中止"
                self._notice(server_note)
                self.emit(self._status_event())
                return {"ok": True}
            return {"ok": False, "error": "没有正在运行的任务"}
        self.stalled = False
        self._notice(server_note or "已请求中止当前轮")
        return {"ok": True}

    def switch(self, conversation_id: str) -> dict:
        conversation_id = (conversation_id or "").strip()
        if not re.fullmatch(r"\d{12,24}", conversation_id):
            return {"ok": False, "error": "会话 ID 应为 12–24 位数字"}
        if self.status in ("running", "approval"):
            return {"ok": False, "error": "有任务在跑，先中止"}
        self.conversation_id = conversation_id
        self.run_id = ""
        self.status = "idle"
        self.phase = "空闲"
        self.approval = None
        self.logs = []
        self.log_seq = 0
        self.config["last_conversation"] = conversation_id
        save_config(self.config)
        self._load_history()
        self._sync_history()
        self._cli_open(conversation_id)
        self.emit({"type": "snapshot", "data": self.snapshot()})
        return {"ok": True}

    def new_session(self) -> dict:
        if self.status in ("running", "approval"):
            return {"ok": False, "error": "有任务在跑，先中止"}
        self.conversation_id = ""
        self.run_id = ""
        self.status = "idle"
        self.phase = "空闲"
        self.approval = None
        self.messages = []
        self.logs = []
        self.log_seq = 0
        self.emit({"type": "snapshot", "data": self.snapshot()})
        return {"ok": True}

    # -- Runner 回调

    def _on_runner(self, kind: str, payload) -> None:
        if kind == "log":
            text = str((payload or {}).get("text") or "") if isinstance(payload, dict) else ""
            if text:
                self.logs.append(text)
                self.logs = self.logs[-400:]
                self.log_seq += 1
                self.emit({"type": "log", "data": {"seq": self.log_seq, "text": text}})
            return
        turn = payload
        if kind == "turn_start":
            self.conversation_id = turn.conversation_id or self.conversation_id
            self.status = "running"
            self.phase = "提交中"
            self.emit(self._status_event())
        elif kind == "approval":
            self.approval = turn.approval
            self.status = "approval"
            self.phase = "待审批"
            self.emit(self._status_event())
            self._maybe_auto_approve()
        elif kind == "turn_update":
            self.phase = turn.phase
            self.emit(self._status_event())
        elif kind == "turn_end":
            self._finish(turn)

    def _maybe_auto_approve(self) -> None:
        approval = self.approval
        if not approval:
            return
        tool = approval.get("tool") or ""
        operation = approval.get("operation") or ""
        head = operation.split("(")[0].strip()
        key = f"{tool}:{head}"
        allowed = set(self.config.get("auto_approve") or [])
        if "*" not in allowed and key not in allowed:
            return
        self.approval = None
        self.runner.approve(True)
        self._notice(f"自动批准 · {tool} · {head}")
        self.emit(self._status_event())

    def _finish(self, turn: Turn) -> None:
        self.started_at = 0.0
        self.approval = None
        if turn.conversation_id:
            self.conversation_id = turn.conversation_id
            self.config["last_conversation"] = turn.conversation_id
            save_config(self.config)
        if turn.run_id:
            self.run_id = turn.run_id
        if self.auto_backup is not None:
            self.config["auto_approve"] = self.auto_backup
            self.auto_backup = None
        payload = turn.result or {}
        status = str(payload.get("status") or "")
        pending = pending_view(payload.get("pending"))
        if pending or status == "waiting_input":
            self._finish_input(pending)
            return
        text = Runner._reply_text(payload)
        if text:
            self.status = "done"
            self.phase = "完成"
            self.stalled = False
            self.pending = None
            self._add_message("assistant", text)
        elif turn.error:
            message = turn.error
            lowered = message.lower()
            # 只有“等不到/连接断”才接管；权限被拒、参数错误这类重试也没用，直接判失败
            recoverable = any(key in lowered for key in ("timeout", "terminated", "disconnect", "econnreset", "socket", "closed"))
            if "terminated" in lowered:
                message = "连接中断（terminated）。任务仍在服务端：要用「续等」接管，不要重发"
            elif "timeout" in lowered:
                message = "等待超时，任务没被取消：要用「续等」接管同一轮，不要重发"
            if recoverable and self.conversation_id and self.run_id and self.auto_resume < AUTO_RESUME_LIMIT:
                self._schedule_auto_resume(message)
            elif recoverable and self.conversation_id and self.run_id:
                self.status = "stalled"
                self.phase = "执行器断线 · 需接管"
                self.stalled = True
                self._add_message("system", message)
            else:
                self.status = "error"
                self.phase = "失败"
                self.stalled = False
                self._add_message("error", message)
        elif status in ("running", "unknown") and idle_turn(payload):
            # 服务端没建任务节点、也没有进度：本地执行器已经断线（CLI 进程退出或超时）
            if self.conversation_id and self.run_id and self.auto_resume < AUTO_RESUME_LIMIT:
                self._schedule_auto_resume("服务端没确认到任务进度，本地执行器疑似断线")
            else:
                self.status = "stalled"
                self.phase = "执行器断线 · 需接管"
                self.stalled = True
                self._add_message(
                    "system",
                    "本地执行器已断线（服务端没确认到任何任务节点）。\n"
                    "点「续等」用同一个 runId 接管，不要重发这条消息；如果 App 里有待确认卡片，先在 App 点掉。",
                )
        elif status in ("running", "unknown"):
            # 云端还报 running 就说明这一轮没收尾：官方页面会一直显示“运行中”。
            # 记成 done 会让用户以为结束了，不去续等，页面就永远转圈。
            settled = self._cloud_settle()
            if settled:
                self.status = "done"
                self.phase = "完成"
                self.stalled = False
                self.pending = None
                self._add_message("assistant", settled)
            else:
                self.status = "stalled"
                self.phase = "执行器断线 · 云端未收尾"
                self.stalled = True
                self._add_message(
                    "system",
                    "云端仍显示这一轮在运行，本地执行器已经退出。\n"
                    "官方页面会一直转圈：点「续等」用同一 runId 接管收尾，不要重发。",
                )
        else:
            self.status = "done"
            self.phase = turn.phase
            self.stalled = False
            self.pending = None
            self._add_message("system", f"本轮结束 · {turn.phase}")
        self._save_history()
        self.emit(self._status_event())

    def _finish_input(self, pending: Optional[dict]) -> None:
        """agent 在等用户回答：展示问题，回答只能回豆包 App。"""
        self.pending = pending
        self.stalled = False
        self.status = "input"
        self.phase = "等你回答"
        lines: List[str] = []
        for item in (pending or {}).get("items") or []:
            if item.get("kind") == "input":
                for question in item.get("questions") or []:
                    text = (question.get("text") or "（CLI 没带出问题文本）").strip()
                    lines.append("· " + text)
                    for option in question.get("options") or []:
                        lines.append("    - " + str(option))
            elif item.get("kind") == "approval":
                lines.append("· 需要你确认的操作：" + ("、".join(item.get("options") or []) or "（选项未带出）"))
        body = "\n".join(lines) or "（CLI 只给出了状态，没有带出问题内容）"
        self._add_message("system", "agent 在等你回答，这条要回豆包 App 点：\n" + body)
        self._save_history()
        self.emit(self._status_event())

    def _schedule_auto_resume(self, reason: str) -> None:
        """超时/断线后自动用同一 runId 接管，绝不重发消息。"""
        self.auto_resume += 1
        attempt = self.auto_resume
        self.status = "stalled"
        self.phase = f"执行器断线 · 自动接管 {attempt}/{AUTO_RESUME_LIMIT}"
        self.stalled = True
        self._add_message("system", f"{reason}。自动接管第 {attempt} 次（不重发消息）")
        self._save_history()
        self.emit(self._status_event())

        def worker() -> None:
            time.sleep(AUTO_RESUME_DELAY)
            result = self.resume(auto=True)
            if not result.get("ok"):
                self._notice(f"自动接管没起来：{result.get('error') or '未知原因'}")

        threading.Thread(target=worker, daemon=True).start()

    def _ticker(self) -> None:
        """每秒推进 elapsed，并让前端刷新计时。"""
        while True:
            time.sleep(1)
            if self.started_at:
                self.emit({"type": "tick", "data": {"elapsed": int(time.time() - self.started_at)}})

    def _cli_open(self, conversation_id: str) -> None:
        """让 CLI 的“当前会话”也跟着切，否则 CLI 侧和 GUI 侧会各说各话。"""
        try:
            argv = self.runner._bin_argv("headless", "sessions", "open", conversation_id)
            if self.config.get("profile"):
                argv += ["--profile", str(self.config["profile"])]
            argv += ["--json"]
            subprocess.run(argv, capture_output=True, text=True, timeout=15)
        except Exception:
            pass

    def _sync_history(self) -> None:
        """切到别的会话时，用服务端历史覆盖本地那份（本地只记 GUI 自己发过的）。"""
        if not self.conversation_id:
            return
        remote = read_conversation(self.config, self.conversation_id)
        if not remote:
            return
        self.messages = remote[-120:]
        self._save_history()

    def _trace_ticker(self) -> None:
        """轮询 CLI 的 receipt：里面的 thinking / handoffs 就是 agent 的思考与步骤。

        CLI 的终端只在审批时输出文字，思考流不进 stderr，但会随 checkpoint
        写进 receipt，所以这里按会话 ID 找最新的那份回执来喂过程面板。
        """
        while True:
            time.sleep(TRACE_INTERVAL)
            try:
                if self.status not in ("running", "approval", "input", "stalled"):
                    continue
                if not self.conversation_id:
                    continue
                data = latest_receipt(self.conversation_id)
                if not data:
                    continue
                trace = parse_receipt(data)
                key = "%d|%d|%s" % (len(trace["thinking"]), len(trace["steps"]), trace["completed"])
                if key == self._trace_key:
                    continue
                self._trace_key = key
                self.trace = trace
                self.emit({"type": "trace", "data": trace})
            except Exception:
                continue

    # -- 续等

    def resume(self, auto: bool = False) -> dict:
        if self.status in ("running", "approval"):
            return {"ok": False, "error": "当前有任务在跑"}
        if not auto:
            self.auto_resume = 0
        if not self.conversation_id:
            return {"ok": False, "error": "还没有会话 ID"}
        argv = self.runner._bin_argv("headless", "sessions", "wait", self.conversation_id)
        if self.run_id:
            argv += ["--run", self.run_id]
        if self.config.get("profile"):
            argv += ["--profile", str(self.config["profile"])]
        argv += ["--timeout", str(int(self.config.get("timeout") or 600)), "--json"]
        self.status = "running"
        self.phase = "续等" if not auto else "自动接管"
        self.stalled = False
        self.pending = None
        self.started_at = time.time()
        self.emit(self._status_event())
        turn = self.runner.start(argv, "wait")
        if turn is None:
            return {"ok": False, "error": "已有任务在跑"}
        return {"ok": True}
