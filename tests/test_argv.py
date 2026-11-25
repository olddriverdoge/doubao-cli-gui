#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯逻辑断言：命令行是怎么拼的、审批提示是怎么解析的。

不上服务、不起子进程，直接对着 community CLI 的真实契约核参数位置。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import core  # noqa: E402

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✓ {label}")
    else:
        FAILURES.append(label)
        print(f"  ✗ {label}" + (f"  ({detail})" if detail else ""))


def make_config(**overrides):
    config = dict(core.DEFAULT_CONFIG)
    config.update({"bin": "doubao", "workspace": "/tmp/ws", "profile": "termux", "mode": "work", "permission": "ask_risky"})
    config.update(overrides)
    return config


def runner(**overrides):
    return core.Runner(make_config(**overrides), lambda *_: None)


print("参数拼装")
work = runner(mode="work", permission="ask_risky")
create = work.build_create("看看这个项目")
check("create 以 sessions create 开头", create[:4] == ["doubao", "headless", "sessions", "create"], str(create))
check("work 模式带本地运行时", create[create.index("--runtime") + 1] == "local")
check("工作区透传", create[create.index("--workspace") + 1] == "/tmp/ws")
check("权限映射 AskOnRisk", create[create.index("--permission") + 1] == "AskOnRisk")
check("profile 透传", create[create.index("--profile") + 1] == "termux")
check("带 --wait 与 --json", "--wait" in create and "--json" in create)
check("消息是最后一个位置参数", create[-1] == "看看这个项目", str(create[-3:]))

chat = runner(mode="chat")
chat_argv = chat.build_create("随便聊聊")
check("chat 带 --mode chat", chat_argv[chat_argv.index("--mode") + 1] == "chat")
check("chat 不带 --runtime", "--runtime" not in chat_argv, str(chat_argv))
check("chat 不带 --workspace", "--workspace" not in chat_argv, str(chat_argv))
check("chat 不带 --permission", "--permission" not in chat_argv, str(chat_argv))

full = runner(permission="always_allow").build_create("跑一下")
check("always_allow 映射 FullAccess", full[full.index("--permission") + 1] == "FullAccess")

send = work.build_send("38450000000000001", "继续")
check("send 以 sessions send 开头", send[:5] == ["doubao", "headless", "sessions", "send", "38450000000000001"], str(send))
check("send 消息在最后", send[-1] == "继续")
check("send 也带本地运行时", send[send.index("--runtime") + 1] == "local")

dash = work.build_create("-开头像参数的消息")
check("短横线开头的消息有 -- 保护", dash[-2:] == ["--", "-开头像参数的消息"], str(dash[-3:]))

noload = runner(profile="").build_create("hi")
check("空 profile 不拼 --profile", "--profile" not in noload)

print("\n审批提示解析")
probe = runner()
text = "\n[local runtime: AskOnRisk; approval-required] Approve local.workspace.apply: ensure_dir (docs); cwd /tmp/ws? [y/N] "
info = probe._parse_approval(text)
check("tool 解析正确", info["tool"] == "local.workspace.apply", str(info))
check("operation 去掉 cwd 尾巴", info["operation"] == "ensure_dir (docs)", str(info["operation"]))
check("risk 解析正确", info["risk"] == "approval-required", str(info))
check("permission 解析正确", info["permission"] == "AskOnRisk", str(info))

bash_text = "\n[local runtime: AskOnRisk; command-execution] Approve Bash: ls -la src; cwd /tmp/ws? [y/N] "
bash_info = probe._parse_approval(bash_text)
check("Bash 的 tool", bash_info["tool"] == "Bash", str(bash_info))
check("Bash 的 operation", bash_info["operation"] == "ls -la src", str(bash_info))

full_text = "\n[local runtime: FullAccess] Enable FullAccess for this turn? Commands can read, modify or delete files accessible to this user, inherit environment credentials, and send tool results to Doubao. There is no OS sandbox; cwd /tmp/ws. [y/N] "
full_info = probe._parse_approval(full_text)
check("FullAccess 门被识别", full_info["full_access"] is True, str(full_info))
check("FullAccess 不带 risk", full_info["risk"] == "", str(full_info))

check("没有提示时返回空", probe._parse_approval("[mock] ensured docs\n")["tool"] == "")

print("\nJSON 回执解析")
payload = probe._extract_json('noise\n{"platform":"headless","conversationId":"38450000000000001","runId":"63060000000000001","status":"completed","reply":{"text":"好了"}}\n')
check("取到最外层对象", payload and payload["conversationId"] == "38450000000000001", str(payload))
check("回复文本取 reply.text", core.Runner._reply_text(payload) == "好了", str(core.Runner._reply_text(payload)))
check("空输出返回 None", probe._extract_json("") is None)
check("裸字符串不崩", core.Runner._reply_text({"reply": "纯字符串"}) == "纯字符串")

print("")
if FAILURES:
    print(f"失败 {len(FAILURES)} 项：{FAILURES}")
    sys.exit(1)
print("全部通过")
