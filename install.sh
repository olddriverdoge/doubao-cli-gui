#!/usr/bin/env bash
# doubao-cli-gui 的 Termux 安装器。
#
# 远程一键（推荐）：
#   curl -fsSL https://raw.githubusercontent.com/olddriverdoge/doubao-cli-gui/main/install.sh | bash
#
# 本地（已 clone 或已解包）：
#   bash install.sh
#
# 环境变量：
#   DOUBAO_GUI_HOME    安装目录，默认 ~/doubao-cli-gui
#   DOUBAO_GUI_BRANCH  分支，默认 main
#   DOUBAO_GUI_REPO    owner/repo，默认 olddriverdoge/doubao-cli-gui
#   DOUBAO_GUI_TOKEN   私有仓库才需要（公开仓库不用）
set -u

REPO="${DOUBAO_GUI_REPO:-olddriverdoge/doubao-cli-gui}"
BRANCH="${DOUBAO_GUI_BRANCH:-main}"
DEST="${DOUBAO_GUI_HOME:-$HOME/doubao-cli-gui}"
SHORTCUT_DIR="$HOME/.shortcuts"
SHORTCUT="$SHORTCUT_DIR/豆包CLI.sh"
CLI_VERSION="${DOUBAO_GUI_CLI_VERSION:-0.23.3}"

say() { printf '%s\n' "$*"; }
warn() { printf '! %s\n' "$*"; }
die() { printf 'x %s\n' "$*"; exit 1; }

TMP_DIR=""
cleanup() { [ -n "$TMP_DIR" ] && rm -rf "$TMP_DIR"; }
trap cleanup EXIT INT TERM

say "doubao-cli-gui 安装"

# ---------------------------------------------------------------- 1. 取代码
SELF="${BASH_SOURCE[0]:-}"
SRC=""
if [ -n "$SELF" ] && [ -f "$SELF" ]; then
  CAND="$(cd "$(dirname "$SELF")" && pwd)"
  if [ -f "$CAND/core.py" ] && [ -f "$CAND/server.py" ]; then
    SRC="$CAND"
  fi
fi

if [ -z "$SRC" ]; then
  say "· 代码不在本地，从 GitHub 拉取 $REPO@$BRANCH"
  command -v curl >/dev/null 2>&1 || { command -v pkg >/dev/null 2>&1 && pkg install -y curl >/dev/null 2>&1; }
  command -v curl >/dev/null 2>&1 || die "缺 curl：pkg install curl"
  TMP_DIR="$(mktemp -d)"
  TAR="$TMP_DIR/src.tgz"
  if [ -n "${DOUBAO_GUI_TOKEN:-}" ]; then
    URL="https://api.github.com/repos/$REPO/tarball/$BRANCH"
    curl -fsSL -H "Authorization: Bearer $DOUBAO_GUI_TOKEN" "$URL" -o "$TAR" || die "下载失败（token 无效或分支不存在）"
  else
    URL="https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH"
    curl -fsSL "$URL" -o "$TAR" || die "下载失败：仓库不存在 / 未公开 / 分支不对"
  fi
  tar -xzf "$TAR" -C "$TMP_DIR" || die "解包失败"
  SRC="$(find "$TMP_DIR" -maxdepth 1 -mindepth 1 -type d | head -n 1)"
  [ -n "$SRC" ] && [ -f "$SRC/core.py" ] || die "解包后找不到 core.py"
fi

# ---------------------------------------------------------------- 2. 环境
if ! command -v python3 >/dev/null 2>&1; then
  if command -v pkg >/dev/null 2>&1; then
    warn "缺 python3，自动安装"
    pkg install -y python >/dev/null 2>&1 || true
  fi
fi
command -v python3 >/dev/null 2>&1 || die "缺 python3：pkg install python"
PY_VER="$(python3 -c 'import sys; print("%d%02d" % sys.version_info[:2])')"
[ "$PY_VER" -ge 308 ] || die "python 太老（需要 3.8+，当前 $(python3 -V 2>&1)）"
say "✓ python $(python3 -V 2>&1 | awk '{print $2}')"

if ! command -v node >/dev/null 2>&1; then
  if command -v pkg >/dev/null 2>&1; then
    warn "缺 node，自动安装（约 1 分钟）"
    pkg install -y nodejs >/dev/null 2>&1 || true
  fi
fi
command -v node >/dev/null 2>&1 || die "缺 node：pkg install nodejs"
NODE_MAJOR="$(node -v | sed 's/^v//' | cut -d. -f1)"
[ "$NODE_MAJOR" -ge 20 ] || warn "node $NODE_MAJOR 偏低，社区 CLI 建议 20+；不行就 pkg install nodejs-lts"
say "✓ node $(node -v)"

# ---------------------------------------------------------------- 3. CLI
if command -v doubao >/dev/null 2>&1; then
  say "✓ doubao $(doubao --version 2>/dev/null || echo '(版本未取到)')"
else
  warn "没装 doubao-cli，开始安装 doubao-cli@$CLI_VERSION"
  npm i -g "doubao-cli@$CLI_VERSION" || die "npm 安装失败。换镜像：npm config set registry https://registry.npmmirror.com"
fi

PROFILE="${DOUBAO_GUI_PROFILE:-termux}"
if doubao headless status --json 2>/dev/null | grep -q '"loggedIn":true'; then
  say "✓ 已登录（默认 profile）"
elif doubao headless status --profile "$PROFILE" --json 2>/dev/null | grep -q '"loggedIn":true'; then
  say "✓ 已登录（profile $PROFILE）"
else
  warn "没检测到登录态，装完执行：doubao headless login --profile $PROFILE"
fi

# ---------------------------------------------------------------- 4. 铺文件
# 在 clone 目录里跑本脚本时 SRC 与 DEST 是同一个目录，先 rm -rf 再 cp 会把源删掉，
# 所以这种情况直接跳过复制（代码本来就在目标目录里）。
if [ "$SRC" = "$DEST" ]; then
  say "· 代码已在 $DEST，跳过复制"
else
  mkdir -p "$DEST"
  for item in core.py server.py web scripts tools; do
    [ -e "$SRC/$item" ] || continue
    rm -rf "${DEST:?}/$item"
    cp -R "$SRC/$item" "$DEST/" || die "复制 $item 失败"
  done
  cp "$SRC/README.md" "$DEST/" 2>/dev/null || true
  cp "$SRC/LICENSE" "$DEST/" 2>/dev/null || true
  cp "$SRC/install.sh" "$DEST/" 2>/dev/null || true
fi
[ -f "$DEST/core.py" ] && [ -f "$DEST/server.py" ] || die "代码不完整（缺 core.py/server.py），用远端安装器重装：curl -fsSL https://raw.githubusercontent.com/olddriverdoge/doubao-cli-gui/main/install.sh | bash"
say "✓ 代码已铺到 $DEST"

chmod +x "$DEST/scripts/doubao-gui"

# ---------------------------------------------------------------- 5. 命令
BIN_DIR="${PREFIX:-$HOME/.local}/bin"
mkdir -p "$BIN_DIR"
cat >"$BIN_DIR/doubao-gui" <<EOF
#!/usr/bin/env bash
exec bash "$DEST/scripts/doubao-gui" "\$@"
EOF
chmod +x "$BIN_DIR/doubao-gui"
say "✓ 命令已装：$BIN_DIR/doubao-gui"

# ---------------------------------------------------------------- 6. 快捷方式
mkdir -p "$SHORTCUT_DIR/tasks"
chmod 700 "$SHORTCUT_DIR" 2>/dev/null || true
cat >"$SHORTCUT" <<EOF
#!/usr/bin/env bash
# 由 install.sh 生成：点图标 → 起服务 → 打开界面
bash "$DEST/scripts/doubao-gui" run
EOF
chmod +x "$SHORTCUT"
say "✓ Termux:Widget 快捷方式：$SHORTCUT"

# ---------------------------------------------------------------- 7. 自检
if bash "$DEST/scripts/doubao-gui" start >/dev/null 2>&1; then
  say "✓ 服务自检通过（已临时起停一次）"
  bash "$DEST/scripts/doubao-gui" stop >/dev/null 2>&1
else
  warn "服务没起来，装完执行 doubao-gui logs 看原因"
fi

say ""
say "装好了。"
say "  起服务：doubao-gui start"
say "  一键用：doubao-gui run      # 起服务 + 打开界面"
say "  看状态：doubao-gui status"
say "  看日志：doubao-gui logs"
say ""
say "浏览器里打开 http://127.0.0.1:${DOUBAO_GUI_PORT:-8765} ，菜单「添加到主屏幕」可当独立图标用。"
