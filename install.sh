#!/usr/bin/env bash
# doubao-cli-gui 在 Termux 里的一键安装。
# 用法：bash install.sh
set -u

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${DOUBAO_GUI_HOME:-$HOME/doubao-cli-gui}"
SHORTCUT_DIR="$HOME/.shortcuts"
SHORTCUT="$SHORTCUT_DIR/豆包CLI.sh"

say() { printf '%s\n' "$*"; }
warn() { printf '! %s\n' "$*"; }
die() { printf 'x %s\n' "$*"; exit 1; }

say "doubao-cli-gui 安装"
say "  源目录 $SRC"
say "  目标   $DEST"
say ""

# 1. 环境检查
command -v python3 >/dev/null 2>&1 || die "缺 python3：pkg install python"
PY_OK="$(python3 -c 'import sys;print("%d%02d"%sys.version_info[:2])')"
[ "$PY_OK" -ge 308 ] || die "python 版本过低（需要 3.8+，当前 $(python3 -V 2>&1)）"
say "✓ python $(python3 -V 2>&1 | awk '{print $2}')"

command -v node >/dev/null 2>&1 || die "缺 node：pkg install nodejs"
NODE_MAJOR="$(node -v | sed 's/^v//' | cut -d. -f1)"
[ "$NODE_MAJOR" -ge 20 ] || warn "node 版本偏低（$NODE_MAJOR），社区 CLI 建议 20+"
say "✓ node $(node -v)"

# 2. CLI 与登录态
if command -v doubao >/dev/null 2>&1; then
  say "✓ doubao $(doubao --version 2>/dev/null || echo '(版本未取到)')"
else
  warn "没装 doubao-cli，开始安装（约 1 分钟）"
  npm i -g doubao-cli@0.23.3 || die "npm 安装失败，检查网络或换镜像：npm config set registry https://registry.npmmirror.com"
fi

PROFILE="${DOUBAO_GUI_PROFILE:-termux}"
if doubao headless status --json 2>/dev/null | grep -q '"loggedIn":true'; then
  say "✓ 已登录（默认 profile）"
elif doubao headless status --profile "$PROFILE" --json 2>/dev/null | grep -q '"loggedIn":true'; then
  say "✓ 已登录（profile $PROFILE）"
else
  warn "没检测到登录态。装完请执行：doubao headless login --profile $PROFILE"
fi

# 3. 复制文件
if [ "$SRC" != "$DEST" ]; then
  mkdir -p "$DEST"
  for item in core.py server.py web scripts tools; do
    [ -e "$SRC/$item" ] || continue
    rm -rf "${DEST:?}/$item"
    cp -R "$SRC/$item" "$DEST/"
  done
  cp "$SRC/README.md" "$DEST/" 2>/dev/null || true
  cp "$SRC/LICENSE" "$DEST/" 2>/dev/null || true
  say "✓ 已复制到 $DEST"
else
  say "✓ 项目已在 $DEST，跳过复制"
fi

chmod +x "$DEST/scripts/doubao-gui"

# 4. 命令入口
BIN_DIR="${PREFIX:-$HOME/.local}/bin"
mkdir -p "$BIN_DIR"
cat >"$BIN_DIR/doubao-gui" <<EOF
#!/usr/bin/env bash
exec bash "$DEST/scripts/doubao-gui" "\$@"
EOF
chmod +x "$BIN_DIR/doubao-gui"
say "✓ 命令已装：$BIN_DIR/doubao-gui"

# 5. 桌面快捷方式（Termux:Widget）
mkdir -p "$SHORTCUT_DIR/tasks"
chmod 700 "$SHORTCUT_DIR"
cat >"$SHORTCUT" <<EOF
#!/usr/bin/env bash
# 由 install.sh 生成：点图标 → 起服务 → 打开界面
bash "$DEST/scripts/doubao-gui" run
EOF
chmod +x "$SHORTCUT"
say "✓ 快捷方式：$SHORTCUT"

# 6. 自检
if bash "$DEST/scripts/doubao-gui" start >/dev/null 2>&1; then
  say "✓ 服务能起来（已在后台运行）"
  bash "$DEST/scripts/doubao-gui" stop >/dev/null 2>&1
else
  warn "服务启动失败，执行 doubao-gui logs 看原因"
fi

say ""
say "装好了。接下来："
say "  1) doubao-gui run      起服务并打开界面"
say "  2) 浏览器菜单「添加到主屏幕」→ 有独立图标、全屏无地址栏"
say "  3) 装 Termux:Widget 后，桌面小组件里能看到「豆包CLI」"
say ""
say "常用：doubao-gui start|stop|status|open|logs"
