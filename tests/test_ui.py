#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""界面层的静态回归断言。

重点是那条 [hidden] 规则：.sheet/.approval/.offline 都写了 display:flex，
一旦丢了这条规则，hidden 就遮不住，会出现“底部一块空白、关不掉”的遮罩。
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✓ {label}")
    else:
        FAILURES.append(label)
        print(f"  ✗ {label}" + (f"  ({detail})" if detail else ""))


def read(name):
    with open(os.path.join(WEB, name), "r", encoding="utf-8") as handle:
        return handle.read()


css = read("style.css")
html = read("index.html")
js = read("app.js")

print("隐藏与弹层")
check("[hidden] 强制 display:none 规则存在", bool(re.search(r"\[hidden\]\s*\{[^}]*display\s*:\s*none\s*!important", css)))
check("sheet 初始 hidden", re.search(r'id="sheet"[^>]*hidden', html) is not None)
check("approval 初始 hidden", re.search(r'id="approval"[^>]*hidden', html) is not None)
check("offline 初始 hidden", re.search(r'id="offline"[^>]*hidden', html) is not None)
check("stop 按钮初始 hidden", re.search(r'id="stop"[^>]*hidden', html) is not None)
check("存在关闭弹层的函数", "function closeSheet" in js)
check("关闭时会清空内容", re.search(r"function closeSheet\(\)\s*\{[^}]*sheetBody\.innerHTML\s*=\s*\"\"", js) is not None)
check("返回键钩子 __sheetOpen", "window.__sheetOpen" in js)
check("返回键钩子 __closeSheet", "window.__closeSheet" in js)

print("\n离线与审批")
check("offline 卡片有启动按钮", 'id="boot"' in html)
check("offline 卡片有重试按钮", 'id="retry"' in html)
check("原生模式探测走 /app/available", '"/app/available"' in js)
check("审批三个动作齐全", all(token in html for token in ('id="ap-deny"', 'id="ap-once"', 'id="ap-turn"')))
check("审批卡默认不显示风险文本", 'id="ap-risk"' in html and 'id="ap-op"' in html)

print("")
if FAILURES:
    print(f"失败 {len(FAILURES)} 项：{FAILURES}")
    sys.exit(1)
print("全部通过")
