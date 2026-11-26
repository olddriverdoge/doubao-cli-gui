#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""0.1.11：待回答（pending）展示 + 断线自动接管。"""

import os
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✓ {label}")
    else:
        FAILURES.append(label)
        print(f"  ✗ {label}" + (f"  ({detail})" if detail else ""))


import core

core.SESSION_DIR = tempfile.mkdtemp(prefix="gui-stall-")
core.AUTO_RESUME_DELAY = 0.05
core.save_config = lambda config: None


def make_session():
    events = []
    session = core.Session({"bin": sys.executable, "binArgs": [], "workspace": ROOT, "timeout": 30}, events.append)
    session.messages = []
    return session, events


def make_turn(**kwargs):
    base = {
        "conversation_id": "38456840786070018",
        "run_id": "63158974732086274",
        "result": {},
        "error": None,
        "phase": "",
        "exit_code": 0,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


print("pending 解析")
view = core.pending_view([
    {"kind": "input", "clarifyId": "c1", "questions": [{"title": "选哪个方案？", "options": [{"title": "A"}, "B"]}]},
    {"kind": "approval", "scene": 7, "items": [{"title": "允许写文件"}]},
])
check("input 解析出问题文本", bool(view and view["items"][0]["questions"][0]["text"]))
check("选项压成纯文本", view["items"][0]["questions"][0]["options"] == ["A", "B"], str(view["items"][0]["questions"][0]["options"]))
check("approval 解析出选项", view["items"][1]["options"] == ["允许写文件"], str(view["items"][1]["options"]))
check("空 pending 返回 None", core.pending_view([]) is None)
check("畸形 pending 不炸", core.pending_view([None, 1, {"kind": "x"}]) is None)

print("\nwaiting_input → 待回答")
session, events = make_session()
session._finish(make_turn(result={"status": "waiting_input", "pending": [
    {"kind": "input", "questions": [{"title": "要不要继续？"}]},
]}))
check("状态是 input", session.status == "input", session.status)
check("phase 提示等你回答", "等你回答" in session.phase, session.phase)
check("pending 存下来了", bool(session.pending))
check("消息要求回豆包 App", any("豆包 App" in m.get("text", "") for m in session.messages))
check("状态事件带 pending", any(e.get("type") == "status" and (e.get("data") or {}).get("pending") for e in events))
check("快照带 pending/stalled", "pending" in session.snapshot() and "stalled" in session.snapshot())

print("\n僵局判定（重试耗尽）")
session, events = make_session()
session.auto_resume = core.AUTO_RESUME_LIMIT
session._finish(make_turn(result={"status": "running", "tasks": {"total": 0}, "progress": "", "pending": []}))
check("判为 stalled", session.status == "stalled", session.status)
check("phase 说明需接管", "接管" in session.phase, session.phase)
check("stalled 标记为真", session.stalled is True)
check("提示不要重发", any("不要重发" in m.get("text", "") for m in session.messages))

print("\n自动接管（同一 runId，不重发）")
session, events = make_session()
calls = []
session.resume = lambda auto=False: (calls.append(auto) or {"ok": True})
session._finish(make_turn(result={"status": "running", "tasks": {"total": 0}, "progress": "", "pending": []}))
check("第一次自动接管计数", session.auto_resume == 1, str(session.auto_resume))
check("phase 显示 1/3", "自动接管 1/3" in session.phase, session.phase)
check("消息说明不重发", any("不重发" in m.get("text", "") for m in session.messages))
time.sleep(0.3)
check("后台真的调了 resume(auto=True)", calls == [True], str(calls))

print("\n超时错误也走接管")
session, events = make_session()
session.auto_resume = core.AUTO_RESUME_LIMIT
session._finish(make_turn(result={"status": "running"}, error="timeout"))
check("超时不是 failed 而是 stalled", session.status == "stalled", session.status)
check("超时文案说不要重发", any("不要重发" in m.get("text", "") for m in session.messages))

print("\n正常完成不受影响")
session, events = make_session()
session._finish(make_turn(result={"status": "completed", "reply": {"text": "做完了"}}))
check("状态 done", session.status == "done", session.status)
check("回复写进消息", any(m.get("text") == "做完了" for m in session.messages))
check("completed 不触发接管", session.stalled is False)

print("\n有进度的 running 不当僵局")
session, events = make_session()
session._finish(make_turn(result={"status": "running", "tasks": {"total": 0}, "progress": "还在跑"}))
check("有 progress 走未确认完成", session.status == "done", session.status)
check("不标 stalled", session.stalled is False)

print()
if FAILURES:
    print(f"失败 {len(FAILURES)} 项：{FAILURES}")
    sys.exit(1)
print("全部通过")
