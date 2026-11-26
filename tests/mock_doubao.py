#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试用假 doubao CLI：复刻 headless 会话的审批与输出协议。

真 CLI 的关键行为：
1) 在 stderr（必须是 TTY）打印 `[local runtime: ...] Approve ...? [y/N] `，然后读一行；
2) 结束时在 stdout 打印最外层 JSON 回执。
本脚本按同一协议工作，供离线端到端测试使用。

环境变量：
  MOCK_SLEEP     每个审批之间/结束前的等待秒数，默认 0.05
  MOCK_SKIP      设为 1 时跳过审批直接返回
  MOCK_STATUS    回执里的 status，默认 completed
  MOCK_ANSWER    覆盖回复文本
"""

import json
import os
import sys
import time

CID = "38450000000000001"
RID = "63060000000000001"

SLEEP = float(os.environ.get("MOCK_SLEEP") or "0.05")
SKIP = os.environ.get("MOCK_SKIP") == "1"
STATUS = os.environ.get("MOCK_STATUS") or "completed"

# 登录相关：设了 MOCK_LOGIN_MARKER 才有登录态概念（未设时兼容老用例，恒为已登录）
MARK = os.environ.get("MOCK_LOGIN_MARKER") or ""
FORCE_LOGIN = os.environ.get("MOCK_LOGGED_IN")
LOGIN_SECONDS = float(os.environ.get("MOCK_LOGIN_SECONDS") or "0.3")

BLOCK_CHARS = {(0, 0): " ", (1, 0): "\u2580", (0, 1): "\u2584", (1, 1): "\u2588"}


def logged_in() -> bool:
    if FORCE_LOGIN is not None:
        return FORCE_LOGIN == "1"
    if not MARK:
        return True
    return os.path.exists(MARK)


def qr_grid(seed: int) -> list:
    """21x21 模块矩阵：三个角是定位图案，其余按 seed 填不同花纹。"""
    n = 21
    grid = [[0] * n for _ in range(n)]
    for oy, ox in ((0, 0), (0, n - 7), (n - 7, 0)):
        for y in range(7):
            for x in range(7):
                edge = y in (0, 6) or x in (0, 6)
                inner = 2 <= y <= 4 and 2 <= x <= 4
                grid[oy + y][ox + x] = 1 if (edge or inner) else 0
    for i in range(7, 14):
        grid[i][i] = 1 if seed % 2 else 0
        grid[i][n - 1 - i] = 1
        grid[7][i] = 1
    return grid


def qr_art(seed: int) -> str:
    """按 CLI 的半块字符画法输出：一行字符 = 两行模块。"""
    grid = qr_grid(seed)
    grid.append([0] * len(grid[0]))
    lines = []
    for row in range(0, len(grid), 2):
        cells = (BLOCK_CHARS[(grid[row][c], grid[row + 1][c])] for c in range(len(grid[0])))
        lines.append("".join(cells))
    return "\n".join(lines)




def err(text: str) -> None:
    sys.stderr.write(text)
    sys.stderr.flush()


def ask(prompt: str) -> bool:
    err(prompt)
    line = sys.stdin.readline()
    return line.strip().lower() in ("y", "yes")


def out(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def receipt(answer: str) -> None:
    conf = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    directory = os.path.join(conf, "doubao-cli", "turns", "mock")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, f"{CID}-{RID}.json"), "w", encoding="utf-8") as handle:
        json.dump({"conversationId": CID, "runId": RID, "answer": answer, "status": "running"}, handle, ensure_ascii=False)


VALUE_FLAGS = {
    "--mode", "--model", "--reasoning", "--timeout", "--workspace",
    "--permission", "--profile", "--run", "--limit", "--mcp", "--project",
}


def positionals(argv: list) -> list:
    """跳过选项及其取值，剩下的就是位置参数。"""
    result = []
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        if token == "--":
            continue
        if token in VALUE_FLAGS:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        result.append(token)
    return result


def parse_workspace(argv: list) -> str:
    for index, item in enumerate(argv):
        if item in ("--workspace", "-C") and index + 1 < len(argv):
            return argv[index + 1]
    return "/tmp"


def parse_message(argv: list) -> str:
    row = positionals(argv)
    if "sessions" in row:
        index = row.index("sessions")
        row = row[index + 1 :]
    # [create, <message>] / [send, <conversation-id>, <message>]
    return row[-1] if len(row) >= 2 else ""


def main(argv: list) -> int:
    workspace = parse_workspace(argv)

    if "sessions" in argv and "list" in argv:
        out({
            "sessions": [
                {"id": CID, "title": "接管审计（mock）", "pinned": True, "archived": False},
                {"id": "38450000000000002", "title": "脚手架冒烟", "pinned": False, "archived": False},
            ],
            "currentId": CID,
        })
        return 0
    if "sessions" in argv and any(item in argv for item in ("rename", "pin", "unpin", "archive", "unarchive")):
        action = next(item for item in argv if item in ("rename", "pin", "unpin", "archive", "unarchive"))
        index = argv.index(action)
        cid = argv[index + 1] if index + 1 < len(argv) else ""
        title = argv[index + 2] if action == "rename" and index + 2 < len(argv) else ""
        out({"ok": True, "action": action, "conversationId": cid, "title": title})
        return 0
    if "models" in argv:
        out({
            "models": [
                {"id": "doubao-seed-2-0-pro", "name": "doubao-seed-2-0-pro", "selected": True},
                {"id": "doubao-seed-2-0-lite", "name": "doubao-seed-2-0-lite", "selected": False},
                {"id": "9", "name": "自动", "selected": False},
            ],
            "reasoning": "high",
        })
        return 0
    if "model" in argv and "select" in argv:
        pos = argv.index("select")
        out({"model": argv[pos + 1] if pos + 1 < len(argv) else "", "changed": True})
        return 0
    if "status" in argv:
        active = logged_in()
        out({"loggedIn": active, "accountId": "1342919234290264" if active else ""})
        return 0
    if "runtimes" in argv:
        out({"runtimes": [{"name": "local", "available": True, "engine": "node-http-sse", "hostPlatform": "android"}]})
        return 0

    if "logout" in argv:
        if MARK and os.path.exists(MARK):
            os.remove(MARK)
        out({"ok": True, "loggedIn": False})
        return 0

    if "login" in argv:
        sys.stdout.write("请用豆包 App 扫码登录（mock）\n")
        sys.stdout.write(qr_art(1) + "\n")
        sys.stdout.flush()
        time.sleep(LOGIN_SECONDS)
        sys.stdout.write("二维码已刷新（mock）\n" + qr_art(2) + "\n")
        sys.stdout.flush()
        time.sleep(LOGIN_SECONDS)
        if MARK:
            directory = os.path.dirname(MARK)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(MARK, "w", encoding="utf-8") as handle:
                handle.write("1")
        out({"loggedIn": True, "accountId": "1342919234290264"})
        return 0

    if "wait" in argv:
        pos = argv.index("wait")
        cid = argv[pos + 1] if pos + 1 < len(argv) else CID
        err("[mock] waiting run\n")
        time.sleep(SLEEP * 4)
        out({"platform": "headless", "conversationId": cid, "runId": RID, "status": "completed", "accepted": True, "reply": {"text": "续等拿到结果（mock）。"}})
        return 0

    message = parse_message(argv)
    err("[mock] accepted, provisioning local runtime\n")
    time.sleep(SLEEP)

    if not SKIP:
        steps = [
            ("local.workspace.apply", "ensure_dir", "docs", "ensured docs/"),
            ("local.workspace.apply", "write_file", "docs/TAKEOVER.md", "writing docs/TAKEOVER.md"),
            ("Bash", "ls -la src", "src", "listing src"),
        ]
        for tool, operation, target, note in steps:
            if not ask(f"\n[local runtime: AskOnRisk; approval-required] Approve {tool}: {operation} ({target}); cwd {workspace}? [y/N] "):
                out({"conversationId": CID, "runId": RID, "status": "failed", "error": "denied"})
                return 1
            err(f"[mock] {note}\n")
            receipt(f"步骤完成：{note}")
            time.sleep(SLEEP)

    answer = os.environ.get("MOCK_ANSWER") or (
        f"接管审计完成（mock）。\n收到消息：{message[:40]}\n"
        "已确认仓库根是项目目录，docs/TAKEOVER.md 已生成。"
    )
    out(
        {
            "platform": "headless",
            "mode": "work",
            "conversationId": CID,
            "runId": RID,
            "status": STATUS,
            "accepted": True,
            "reply": {"text": answer},
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
