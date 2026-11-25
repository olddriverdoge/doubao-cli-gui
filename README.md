# doubao-cli-gui

社区版 `doubao-cli` 的手机图形界面：本地起一个 HTTP 服务，浏览器打开就能像官方客户端一样收发消息、逐条审批工具调用。零第三方依赖（Python 标准库 + HTML/CSS/JS），专为 Termux + Android 调优。

## 它是什么

一个**前端壳**，不是独立客户端。后端仍然是 Termux 里已经装好的 `doubao-cli`：

```
浏览器 / PWA
   │  HTTP + SSE
   ▼
server.py ── 会话状态、事件广播、静态页面
   │  fork
   ▼
doubao headless sessions create|send|wait …（stdin/stderr 接 pty）
   │
   ▼
豆包服务端
```

- 每一次"发送"= 在 Termux 里跑一条 CLI 命令
- 每一个"允许/拒绝"= 往 CLI 的 pty 写 `y` / `n`
- 回复 = CLI 在 stdout 打出的 `--json` 回执里取 `reply.text`

所以它需要 Termux 里具备：node 20+、`doubao-cli`、有效的登录 profile。

## 安装（Termux 两条命令）

```bash
# 一键安装：自己装 python/node、装 CLI、铺代码、挂命令和桌面快捷方式
curl -fsSL https://raw.githubusercontent.com/olddriverdoge/doubao-cli-gui/main/install.sh | bash

# 一键运行：起服务并打开界面
doubao-gui run
```

第一条可以重复跑，等于升级到最新代码；`DOUBAO_GUI_HOME`、`DOUBAO_GUI_BRANCH`、`DOUBAO_GUI_CLI_VERSION` 可覆盖默认值。

装完以后的常用命令：

```bash
doubao-gui start     # 后台起服务（默认 127.0.0.1:8765）
doubao-gui status    # 看有没有在跑
doubao-gui logs      # 跟日志
doubao-gui restart   # 改完代码重载
```

如果还没登录，装完执行：

```bash
doubao headless login --profile termux
```

`install.sh` 做这些事：缺 python/node 就 `pkg install`、缺 CLI 就 `npm i -g`、把代码铺到 `~/doubao-cli-gui`、在 `$PREFIX/bin` 放 `doubao-gui`、在 `~/.shortcuts/` 生成 `豆包CLI.sh`、最后自检起停一次。

## 使用

```bash
doubao-gui run       # 起服务 + 打开浏览器
doubao-gui start     # 只起服务
doubao-gui status
doubao-gui stop
doubao-gui logs
DOUBAO_GUI_PORT=8766 doubao-gui start   # 换端口
```

拿到 App 感的三种做法：

1. **PWA**：浏览器打开 `http://127.0.0.1:8765` → 菜单「添加到主屏幕」。有独立图标、全屏无地址栏，服务在后台由 `doubao-gui` 维持。
2. **Termux:Widget**：装 Termux:Widget 后，桌面小组件里点「豆包CLI」，一步起服务并跳转。
3. 想让它开机自启：把 `doubao-gui run` 写进 `~/.termux/boot/`（需 Termux:Boot）。

界面上的能力：流式消息、逐条审批（允许一次 / 本轮都允许 / 拒绝）、中止当前轮、续等（不重发消息）、切换历史会话、改工作区与权限模式、体检、落盘到本地历史。

## 权限模式

| 界面 | CLI 参数 | 行为 |
|---|---|---|
| 风险操作时确认 | `--permission AskOnRisk` | 只有写文件、跑命令这类才弹 |
| 始终确认 | `--permission AlwaysAsk` | 每个工具调用都弹 |
| 允许所有操作 | `--permission FullAccess` | 不弹，全放行 |

默认「风险操作时确认」，且不做任何静默自动放行——审批一定经过你的手。嫌烦就用审批卡上的「本轮都允许」，它只对当前这一轮生效，轮次结束自动还原。

注意 `chat` 模式：它走云端、不碰本机文件，所以发消息时不会带本地运行时参数——这是 CLI 的硬约束（`--mode chat` 与 `--workspace` / `--permission` / `--runtime` 互斥），本项目已按模式自动切换。

## 常见问题

**审批一直不弹 / 一发送就失败**
社区 CLI 用 `Boolean(process.stdin.isTTY && process.stderr.isTTY)` 判断要不要走交互审批；非 TTY 会**静默拒绝**所有审批。本项目已经把 stdin/stderr 接到 pty 解决，改代码时别把这两个接到管道。

**出现 `terminated`**
那是 Node 的 undici 在 SSE 断连时抛的，**不等于服务端取消了任务**。点「续等」，不要重发同一条消息，否则会多起一轮。

**登录失效**
`doubao headless status --profile termux --json` 看 `loggedIn`。失败就重新 `doubao headless login --profile termux`。

**后台被杀**
Termux 要 `termux-wake-lock`，并在系统设置里关掉 Termux 的电池优化。本项目启动时若检测到 `termux-wake-lock` 会自动调用。

**端口被占**
`DOUBAO_GUI_PORT=8766 doubao-gui start`。

## Android APK

`android/` 是独立 Gradle 工程：Java 17 + compileSdk/targetSdk 35 + WebView 壳 + Chaquopy 内嵌 Python 3.13 + Starlette。

它的定位不是"把 CLI 打进安装包"，而是**原生外壳 + 网关**：

- App 内 Python 起 `127.0.0.1:8790`，把打包进 APK 的前端页面发出去
- `/api/*` 反代到 Termux 里的 `127.0.0.1:8765`，SSE 一起转发，所以前端代码同一套
- Termux 服务没起时，界面不白屏：显示"服务未启动"，点一下通过 Termux 的 `RUN_COMMAND` 接口拉起 `doubao-gui start`

为什么不把 `doubao-cli` 塞进 APK：它是 Node 程序，运行要 node 20+ 和已登录的 profile，而登录态绑设备、协议是逆向的。App 沙箱里没有这套运行时，硬塞等于重打包 node + 重做登录，收益不抵成本。

构建完全在 GitHub Actions 里做（`.github/workflows/build-apk.yml`）：push 到 main 或手动触发，跑完在 Actions 的 Artifacts 下 `doubao-cli-gui-debug-apk`。debug 签名，可直接侧载；minSdk 24，只带 arm64-v8a（Chaquopy 17 的 Python 3.13 已无 32 位制品）。

装完第一次打开会停在「Termux 服务未启动」，三件事各做一次就通了：

1. Termux 里 `bash ~/doubao-cli-gui/install.sh`（有 `doubao-gui` 命令）
2. `allow-external-apps=true` 写进 `~/.termux/termux.properties`（Termux 设置界面里**没有**这个开关）
3. 系统设置给「豆包 CLI」授予「在 Termux 中执行命令」权限（首次打开 App 会弹一次）

之后点界面上的「启动服务」，App 会通过 `RUN_COMMAND` 拉起 `doubao-gui start`，3 秒内自动接上。

### ZeroTermux / 衍生版

ZeroTermux 没有设置界面里的开关，直接写配置文件，一条命令搞定：

```bash
mkdir -p ~/.termux
grep -q '^allow-external-apps=true' ~/.termux/termux.properties 2>/dev/null || echo 'allow-external-apps=true' >> ~/.termux/termux.properties
termux-reload-settings 2>/dev/null || echo "没有 termux-reload-settings，就完全退出 Termux 再打开"
```

APK 不再硬编码 `com.termux`，而是按 `com.termux.RUN_COMMAND` 这个 action 去找当前设备上真正声明了它的那个 App，所以 ZeroTermux 换了包名也能命中。

如果它连 `RunCommandService` 都没保留，点「启动服务」会提示 `termux-missing`：这时用界面上的「复制启动命令」，去 Termux 里粘贴 `doubao-gui start`。一样能用，只是少了「一键」。

本地构建需要 JDK 17、Android SDK 35、Python 3.13：

```bash
cd android
cp -r ../web app/src/main/python/web
gradle assembleDebug
```

## 开发与测试

```bash
python3 -m py_compile core.py server.py
python3 tests/test_api.py        # 真起服务 + 假 CLI，纯控制台验收
python3 tools/make_icons.py      # 重新生成 PWA 图标
```

`tests/mock_doubao.py` 复刻了 CLI 的审批协议与 JSON 回执，所以整套测试不需要 npm、不需要网络、不需要真的登录。

## 边界

- 只监听 `127.0.0.1`，不做鉴权、不做局域网暴露。想远程访问请自己套 SSH 端口转发。
- 豆包写文件的范围就是「工作区」，界面不做文件管理，避免误删。
- 协议是社区版逆向出来的，豆包一改前端就可能失灵——先升级 `doubao-cli` 再排查。

## License

MIT
