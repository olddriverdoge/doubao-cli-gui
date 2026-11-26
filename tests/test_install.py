#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安装脚本的静态回归：必须是“远程可跑”的，而不是只认 git clone 后的目录。"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"  ✓ {label}")
    else:
        FAILURES.append(label)
        print(f"  ✗ {label}" + (f"  ({detail})" if detail else ""))


def read(path):
    with open(os.path.join(ROOT, path), "r", encoding="utf-8") as handle:
        return handle.read()


install = read("install.sh")
runner = read("scripts/doubao-gui")
readme = read("README.md")

print("语法")
bash = shutil.which("bash")
for name, text in (("install.sh", install), ("scripts/doubao-gui", runner)):
    path = os.path.join(ROOT, name)
    if not bash:
        print(f"  · {name} 跳过 bash -n（本机没有 bash，Termux 上是主路径）")
        continue
    result = subprocess.run([bash, "-n", path], capture_output=True, text=True)
    check(f"{name} 通过 bash -n", result.returncode == 0, result.stderr.strip())

print("\n远程自举")
check("装着 codeload 兜底", "codeload.github.com" in install)
check("装了也从本地读", "SRC=\"$CAND\"" in install)
check("能识别 pipe 运行（BASH_SOURCE 为空）", 'SELF="${BASH_SOURCE[0]:-}"' in install)
check("解包后校验 core.py", "core.py" in install and "解包后找不到" in install)
check("临时目录会清理", "trap cleanup EXIT" in install)

print("\n依赖自动补")
check("缺 python 自动 pkg install", "pkg install -y python" in install)
check("缺 node 自动 pkg install", "pkg install -y nodejs" in install)
check("CLI 版本可覆盖", "DOUBAO_GUI_CLI_VERSION" in install)
check("登录态检测带 profile", 'status --profile "$PROFILE" --json' in install)

print("\n命令入口")
check("装 doubao-gui 到 PREFIX/bin", "BIN_DIR=\"${PREFIX:-$HOME/.local}/bin\"" in install)
check("生成 Termux:Widget 快捷方式", ".shortcuts" in install)
check("安装完自检起停", "start >/dev/null 2>&1" in install)

print("\nREADME 一键命令")
check("README 有远程安装命令", "install.sh | bash" in readme)
check("README 有运行命令", "doubao-gui run" in readme)

print("")
if FAILURES:
    print(f"失败 {len(FAILURES)} 项：{FAILURES}")
    sys.exit(1)
print("全部通过")
