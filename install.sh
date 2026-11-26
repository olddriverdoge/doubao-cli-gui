#!/usr/bin/env bash
# doubao-cli-gui 的 Termux 安装器。
#
# 远程一键（推荐）：
#   curl -fsSL https://raw.githubusercontent.com/olddriverdoge/doubao-cli-gui/main/install.sh | bash
#
# 本地（已 clone 或已解包）：
#   bash install.sh
#
# 更新流程（在安装目录里）：
#   git fetch origin main && git reset --hard origin/main
#   bash install.sh          # 会校准工作区、校验版本、重启并探活
#
# 参数：
#   --check       只体检：报代码版本、服务版本、工作区漂移，不动任何文件
#   --force       文件与仓库不一致时直接覆盖，不保留备份
#   --no-restart  装完不重启服务
#
# 环境变量：
#   DOUBAO_GUI_HOME    安装目录，默认 ~/doubao-cli-gui
#   DOUBAO_GUI_BRANCH  分支，默认 main
#   DOUBAO_GUI_REPO    owner/repo，默认 olddriverdoge/doubao-cli-gui
#   DOUBAO_GUI_TOKEN   私有仓库才需要（公开仓库不用）
#   DOUBAO_GUI_PORT    端口，默认 8765

# sh install.sh 时 BASH_SOURCE 会触发 Bad substitution，先切回 bash
if [ -z "${BASH_VERSION:-}" ]; then
  if command -v bash >/dev/null 2>&1; then
    exec bash "$0" "$@"
  fi
  echo "这个脚本需要 bash：pkg install bash"; exit 1
fi
set -u

REPO="${DOUBAO_GUI_REPO:-olddriverdoge/doubao-cli-gui}"
BRANCH="${DOUBAO_GUI_BRANCH:-main}"
DEST="${DOUBAO_GUI_HOME:-$HOME/doubao-cli-gui}"
SHORTCUT_DIR="$HOME/.shortcuts"
SHORTCUT="$SHORTCUT_DIR/豆包CLI.sh"
CLI_VERSION="${DOUBAO_GUI_CLI_VERSION:-0.23.3}"
PORT="${DOUBAO_GUI_PORT:-8765}"

FORCE=0
CHECK_ONLY=0
NO_RESTART=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --check) CHECK_ONLY=1 ;;
    --no-restart) NO_RESTART=1 ;;
    -h|--help)
      sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//' | sed '/^$/d'
      exit 0
      ;;
  esac
done

say() { printf '%s\n' "$*"; }
warn() { printf '! %s\n' "$*"; }
die() { printf 'x %s\n' "$*"; exit 1; }

TMP_DIR=""
cleanup() { [ -n "$TMP_DIR" ] && rm -rf "$TMP_DIR"; }
trap cleanup EXIT INT TERM

# 从 stdin 的 core.py 内容里取版本号
sv() { sed -n 's/^VERSION *= *"\([^"]*\)".*/\1/p' | head -n 1; }

# 安装目录若是 git 仓库，列出 HEAD 里的全部文件
tree_files() {
  [ -d "$DEST/.git" ] || return 1
  command -v git >/dev/null 2>&1 || return 1
  ( cd "$DEST" && git rev-parse --verify HEAD >/dev/null 2>&1 ) || return 1
  ( cd "$DEST" && git ls-tree -r --name-only HEAD )
}

same_blob() {
  local rel="$1"
  [ -f "$DEST/$rel" ] || return 1
  if command -v cmp >/dev/null 2>&1; then
    ( cd "$DEST" && git show "HEAD:$rel" ) 2>/dev/null | cmp -s - "$DEST/$rel"
    return $?
  fi
  ( cd "$DEST" && git show "HEAD:$rel" ) 2>/dev/null \
    | python3 -c 'import pathlib,sys; d=sys.stdin.buffer.read(); sys.exit(0 if pathlib.Path(sys.argv[1]).read_bytes()==d else 1)' "$DEST/$rel"
}

# 把工作区校准到 HEAD：未被 git 接管的同路径旧文件也会被覆盖，原文件先备份
sync_worktree() {
  local list listfile rel backup fixed=0 failed=0
  list="$(tree_files)" || { warn "· 不是 git 仓库或缺 git，跳过工作区校准"; return 0; }
  [ -n "$list" ] || return 0
  listfile="$(mktemp 2>/dev/null || echo "/tmp/dclg-list.$$")"
  printf '%s\n' "$list" > "$listfile"
  backup="$DEST/.install-backup/$(date +%Y%m%d-%H%M%S)"
  while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    case "$rel" in .install-backup/*|.git/*) continue ;; esac
    same_blob "$rel" && continue
    if [ -f "$DEST/$rel" ] && [ "$FORCE" = "0" ]; then
      mkdir -p "$backup/$(dirname "$rel")" 2>/dev/null
      cp "$DEST/$rel" "$backup/$rel" 2>/dev/null || true
    fi
    mkdir -p "$(dirname "$DEST/$rel")" 2>/dev/null
    if ( cd "$DEST" && git show "HEAD:$rel" ) >"$DEST/$rel" 2>/dev/null; then
      fixed=$((fixed + 1))
      printf '  · 已更新 %s\n' "$rel"
    else
      failed=$((failed + 1))
      warn "× 写不回 $rel"
    fi
  done < "$listfile"
  rm -f "$listfile"
  if [ "$fixed" -gt 0 ]; then
    warn "· 有 $fixed 个文件与仓库不一致，已按 HEAD 覆盖"
    [ "$FORCE" = "0" ] && say "  原文件备份在 $backup"
  else
    say "✓ 工作区与仓库一致"
  fi
  [ "$failed" -eq 0 ] || return 1
}

probe_version() {
  python3 - "$PORT" <<'PY' 2>/dev/null
import json
import sys
import urllib.request

try:
    with urllib.request.urlopen("http://127.0.0.1:%s/api/state" % sys.argv[1], timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))
    print((payload.get("data") or {}).get("version") or "")
except Exception:
    print("")
PY
}

if [ "$CHECK_ONLY" = "1" ]; then
  say "doubao-cli-gui 体检"
  [ -d "$DEST" ] || die "没有这个目录：$DEST"
  CODE_VER="$(sv < "$DEST/core.py" 2>/dev/null)"
  say "代码版本：${CODE_VER:-读不到}"
  if tree_files >/dev/null 2>&1; then
    HEAD_VER="$( ( cd "$DEST" && git show HEAD:core.py ) 2>/dev/null | sv )"
    say "仓库版本：${HEAD_VER:-读不到}"
    [ "$CODE_VER" = "$HEAD_VER" ] || warn "工作区与仓库版本不一致，跑 bash install.sh 校准"
    DRIFT="$( ( cd "$DEST" && git status --porcelain ) 2>/dev/null | head -n 5 )"
    if [ -n "$DRIFT" ]; then
      warn "有未提交改动："
      printf '%s\n' "$DRIFT"
    fi
  else
    warn "不是 git 仓库，无法对比仓库版本"
  fi
  LIVE_VER="$(probe_version)"
  say "服务版本：${LIVE_VER:--（没响应）}"
  if [ -n "$LIVE_VER" ] && [ "$CODE_VER" = "$LIVE_VER" ]; then
    say "✓ 一致"
  else
    warn "不一致：服务可能是旧进程，执行 doubao-gui restart"
  fi
  exit 0
fi

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
  say "· CLI 侧没有登录态（没关系：界面里用 Cookie 登录照样能用）"
fi

# ---------------------------------------------------------------- 4. 铺文件
# 在 clone 目录里跑时 SRC 与 DEST 是同一个目录，rm -rf 后再 cp 会把源删掉，
# 所以跳过复制，改由 sync_worktree 把工作区校准到 HEAD。
if [ "$SRC" = "$DEST" ]; then
  say "· 代码已在 $DEST，走工作区校准"
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

chmod +x "$DEST/scripts/doubao-gui" 2>/dev/null || true

# ---------------------------------------------------------------- 4.5 校准与版本校验
sync_worktree || warn "· 工作区校准有失败项，先看上面的警告"

CODE_VER="$(sv < "$DEST/core.py" 2>/dev/null)"
[ -n "$CODE_VER" ] || die "读不到 core.py 里的版本号，文件可能损坏，用远端安装器重装"
if tree_files >/dev/null 2>&1; then
  HEAD_VER="$( ( cd "$DEST" && git show HEAD:core.py ) 2>/dev/null | sv )"
  [ "$CODE_VER" = "$HEAD_VER" ] || die "版本仍不一致（工作区 $CODE_VER / 仓库 $HEAD_VER），手动执行：cd $DEST && git checkout -f HEAD -- ."
fi
say "✓ 代码版本 $CODE_VER"

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

# ---------------------------------------------------------------- 7. 重启与探活
if [ "$NO_RESTART" = "1" ]; then
  say "· 按 --no-restart 跳过重启，服务仍是旧进程"
else
  bash "$DEST/scripts/doubao-gui" stop >/dev/null 2>&1
  pkill -f "server.py --port $PORT" >/dev/null 2>&1 || true
  sleep 1
  if bash "$DEST/scripts/doubao-gui" start >/dev/null 2>&1; then
    sleep 1
    LIVE_VER="$(probe_version)"
    if [ "$LIVE_VER" = "$CODE_VER" ]; then
      say "✓ 服务在跑，线上版本 $LIVE_VER"
    elif [ -n "$LIVE_VER" ]; then
      warn "线上版本 $LIVE_VER 与代码 $CODE_VER 不一致，端口 $PORT 上还有旧进程"
      warn "  处理：doubao-gui stop；确认没进程后 doubao-gui start"
    else
      warn "服务起来了但 /api/state 没响应，看 doubao-gui logs"
    fi
  else
    warn "服务没起来，装完执行 doubao-gui logs 看原因"
  fi
fi

say ""
say "装好了。"
say "  起服务：doubao-gui start"
say "  一键用：doubao-gui run      # 起服务 + 打开界面"
say "  看状态：doubao-gui status"
say "  看版本：doubao-gui version   # 代码版本 vs 线上版本"
say "  做体检：bash install.sh --check"
say "  看日志：doubao-gui logs"
say ""
say "浏览器里打开 http://127.0.0.1:${PORT} ，菜单「添加到主屏幕」可当独立图标用。"
