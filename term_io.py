#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨平台终端层。

上层（core.Runner）只关心四件事：读一段字节、写一段字节、进程还在不在、能不能掐掉。
本模块把它拆成两个实现：

- POSIX（Linux / Android-Termux / macOS）：标准库 pty，stdin+stderr 走伪终端、
  stdout 单独走管道，用来收 `--json` 回执（保真度最高）。
- Windows：ConPTY（pywinpty）。ConPTY 只有一个合并流，stdout 无法单独走管道，
  因此 `merged = True`，上层需要从合并流里提取 JSON 回执。

两条实现对外接口一致，UI 层（server.py + web/）完全是 HTTP/SSE，不感知终端差异。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import List, Optional

IS_WIN = os.name == "nt"


class Channel:
    """一条可读流。read() 阻塞到有数据或对端关闭；关闭时返回 b""。"""

    def __init__(self, kind: str, reader) -> None:
        self.kind = kind  # "err" | "out" | "merged"
        self._reader = reader
        self.closed = False

    def read(self) -> bytes:
        try:
            data = self._reader()
        except (EOFError, OSError, ValueError):
            data = b""
        if isinstance(data, str):
            data = data.encode("utf-8", "replace")
        if not data:
            self.closed = True
        return data or b""


class Terminal:
    """终端句柄的公共接口。"""

    merged = False

    def __init__(self) -> None:
        self.channels: List[Channel] = []

    def write(self, data: bytes) -> bool:
        raise NotImplementedError

    def poll(self) -> Optional[int]:
        raise NotImplementedError

    def terminate(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    @property
    def pid(self) -> Optional[int]:
        return None


class PosixTerminal(Terminal):
    """标准库 pty：stdin/stderr 一个伪终端，stdout 一个管道。"""

    merged = False

    def __init__(self, argv: List[str], cwd: Optional[str] = None, env: Optional[dict] = None) -> None:
        super().__init__()
        import fcntl
        import pty
        import struct
        import termios

        self.master, slave = pty.openpty()
        try:
            size = shutil.get_terminal_size()
            fcntl.ioctl(
                slave,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", max(size.lines, 24), max(size.columns, 80), 0, 0),
            )
        except Exception:
            pass
        self.proc = subprocess.Popen(
            argv,
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=slave,
            close_fds=True,
            env=env,
            cwd=cwd,
            start_new_session=True,
        )
        os.close(slave)
        self.channels = [Channel("err", lambda: os.read(self.master, 4096))]
        if self.proc.stdout is not None:
            self.channels.append(Channel("out", lambda: os.read(self.proc.stdout.fileno(), 65536)))

    def write(self, data: bytes) -> bool:
        try:
            os.write(self.master, data)
            return True
        except OSError:
            return False

    def poll(self) -> Optional[int]:
        return self.proc.poll()

    def terminate(self) -> None:
        try:
            self.proc.terminate()
        except Exception:
            pass

    def close(self) -> None:
        try:
            os.close(self.master)
        except Exception:
            pass
        try:
            if self.proc.stdout is not None:
                self.proc.stdout.close()
        except Exception:
            pass

    @property
    def pid(self) -> Optional[int]:
        return self.proc.pid


class WindowsTerminal(Terminal):
    """ConPTY（pywinpty）：stdin/stdout/stderr 合并成一条流。"""

    merged = True

    def __init__(self, argv: List[str], cwd: Optional[str] = None, env: Optional[dict] = None) -> None:
        super().__init__()
        try:
            from winpty import PtyProcess
        except ImportError as exc:  # 缺依赖时给出可执行的修法
            raise RuntimeError(
                "Windows 上需要 pywinpty（ConPTY）才能提供 TTY：pip install pywinpty"
            ) from exc

        size = shutil.get_terminal_size()
        # 宽度要给足：CLI 的审批提示是单行长文本，ConPTY 按列数硬折行会把
        # “…? [y/N]” 拆到两行，甚至把 “[y/N]” 本身拆开，上层就认不出来了。
        self.proc = PtyProcess.spawn(
            argv,
            cwd=cwd or None,
            env=env,
            dimensions=(max(size.lines, 40), max(size.columns, 200)),
        )
        self.channels = [Channel("merged", lambda: self.proc.read(4096))]

    def write(self, data: bytes) -> bool:
        try:
            self.proc.write(data.decode("utf-8", "replace"))
            return True
        except Exception:
            return False

    def poll(self) -> Optional[int]:
        try:
            if self.proc.isalive():
                return None
            return self.proc.exitstatus
        except Exception:
            return None

    def terminate(self) -> None:
        try:
            self.proc.terminate()
        except Exception:
            pass

    def close(self) -> None:
        try:
            self.proc.close()
        except Exception:
            pass

    @property
    def pid(self) -> Optional[int]:
        try:
            return self.proc.pid
        except Exception:
            return None


def spawn(argv: List[str], cwd: Optional[str] = None, env: Optional[dict] = None) -> Terminal:
    """按当前平台挑实现。argv[0] 必须是可直接执行的文件（不要传 .cmd 脚本）。"""
    if IS_WIN:
        return WindowsTerminal(argv, cwd=cwd, env=env)
    return PosixTerminal(argv, cwd=cwd, env=env)


def child_env(base: Optional[dict] = None) -> dict:
    """子进程环境：干净的终端能力宣告。"""
    env = dict(os.environ if base is None else base)
    env["TERM"] = "xterm-256color"
    env.pop("NO_COLOR", None)
    return env
