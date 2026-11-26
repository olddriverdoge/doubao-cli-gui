(function () {
  "use strict";

  const log = document.getElementById("log");
  const input = document.getElementById("input");
  const sendBtn = document.getElementById("send");
  const stopBtn = document.getElementById("stop");
  const dot = document.getElementById("dot");
  const phaseEl = document.getElementById("phase");
  const timerEl = document.getElementById("timer");
  const cidEl = document.getElementById("cid");
  const approvalBox = document.getElementById("approval");
  const pendingBox = document.getElementById("pending");
  const pdBody = document.getElementById("pd-body");
  const pdResume = document.getElementById("pd-resume");
  const offlineBox = document.getElementById("offline");
  const bootBtn = document.getElementById("boot");
  const openTermuxBtn = document.getElementById("opentermux");
  const copyCmdBtn = document.getElementById("copycmd");
  const retryBtn = document.getElementById("retry");
  const apRisk = document.getElementById("ap-risk");
  const apOp = document.getElementById("ap-op");
  const apMeta = document.getElementById("ap-meta");
  const apOnce = document.getElementById("ap-once");
  const apTurn = document.getElementById("ap-turn");
  const apDeny = document.getElementById("ap-deny");
  const sheet = document.getElementById("sheet");
  const sheetTitle = document.getElementById("sheet-title");
  const sheetBody = document.getElementById("sheet-body");
  const toastEl = document.getElementById("toast");
  const traceBox = document.getElementById("trace");
  const traceToggle = document.getElementById("trace-toggle");
  const traceLine = document.getElementById("trace-line");
  const traceBody = document.getElementById("trace-body");
  const traceThink = document.getElementById("trace-think");
  const traceSteps = document.getElementById("trace-steps");
  const traceCaret = document.getElementById("trace-caret");
  const attachBtn = document.getElementById("attach");
  const pdOpenBtn = document.getElementById("pd-open");

  let traceOpen = false;
  try { traceOpen = localStorage.getItem("traceOpen") === "1"; } catch (error) { traceOpen = false; }

  const state = { status: "idle", phase: "空闲", approval: null, pending: null, stalled: false, trace: null, cid: "", rendered: new Set(), offline: false, appVersion: "", attach: [] };
  let nativeMode = false;
  let toastTimer = 0;

  // ------------------------------------------------------------ 工具

  function toast(text) {
    toastEl.textContent = text;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.hidden = true; }, 2200);
  }

  async function post(path, body) {
    try {
      const response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      const data = await response.json();
      if (!data.ok && data.error) toast(data.error);
      return data;
    } catch (error) {
      toast("请求失败：" + error.message);
      return { ok: false, error: error.message };
    }
  }

  async function getJson(path) {
    try {
      const response = await fetch(path);
      return await response.json();
    } catch (error) {
      return { ok: false, error: error.message };
    }
  }

  function escapeHtml(text) {
    return text.replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  }

  function renderText(raw) {
    let text = escapeHtml(raw || "");
    const blocks = [];
    text = text.replace(/```([^\n]*)\n([\s\S]*?)```/g, (_, lang, code) => {
      blocks.push('<pre><code>' + code.replace(/\n$/, "") + "</code></pre>");
      return "\u0000" + (blocks.length - 1) + "\u0000";
    });
    text = text.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    text = text.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/^#{1,6}\s+(.+)$/gm, "<h4>$1</h4>");
    text = text.replace(/^\s*(?:[-*•])\s+(.+)$/gm, '<span class="li">• $1</span>');
    text = text.replace(/^\s*(\d+)\.\s+(.+)$/gm, '<span class="li">$1. $2</span>');
    text = text.replace(/\n/g, "<br>");
    text = text.replace(/\u0000(\d+)\u0000/g, (_, index) => blocks[Number(index)]);
    return text;
  }

  function formatTime(ms) {
    if (!ms) return "";
    const date = new Date(ms);
    return String(date.getHours()).padStart(2, "0") + ":" + String(date.getMinutes()).padStart(2, "0");
  }

  // ------------------------------------------------------------ 渲染

  function nearBottom() {
    return log.scrollHeight - log.scrollTop - log.clientHeight < 80;
  }

  function scrollDown(force) {
    if (force || nearBottom()) log.scrollTop = log.scrollHeight;
  }

  function addMessage(message, force) {
    if (!message || state.rendered.has(message.id)) return;
    state.rendered.add(message.id);
    const node = document.createElement("div");
    node.className = "msg " + (message.role || "assistant");
    let html = renderText(message.text);
    if (message.role === "user" || message.role === "assistant") {
      html += '<span class="stamp">' + formatTime(message.ts) + "</span>";
    }
    node.innerHTML = html;
    if (message.role === "user" || message.role === "assistant") {
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "copy";
      copy.textContent = "复制";
      copy.addEventListener("click", () => {
        if (navigator.clipboard) {
          navigator.clipboard.writeText(message.text || "").then(() => toast("已复制")).catch(() => toast("复制失败"));
        } else {
          toast("这个环境没有剪贴板");
        }
      });
      node.appendChild(copy);
    }
    log.appendChild(node);
    scrollDown(force);
  }

  function renderMessages(messages) {
    log.innerHTML = "";
    state.rendered.clear();
    (messages || []).forEach((message) => addMessage(message, false));
    scrollDown(true);
  }

  function applyStatus(data) {
    state.status = data.status || "idle";
    state.phase = data.phase || "";
    state.offline = !!data.offline;
    state.approval = data.approval || null;
    state.pending = data.pending || null;
    state.stalled = !!data.stalled;
    if (data.trace !== undefined) state.trace = data.trace || null;
    if (data.conversationId !== undefined) {
      state.cid = data.conversationId || "";
      cidEl.textContent = state.cid ? "会话 " + state.cid.slice(-6) : "无会话";
    }
    const busy = state.status === "running" || state.status === "approval";
    const attention = busy || state.status === "stalled" || state.status === "input";
    dot.className = "dot " + (attention ? "approval" : state.status);
    phaseEl.textContent = state.phase || "空闲";
    const resumeChip = document.querySelector('.quick [data-act="resume"]');
    if (resumeChip) resumeChip.textContent = state.stalled ? "⟳ 重新接管" : "⟳ 续等";
    sendBtn.disabled = busy;
    stopBtn.hidden = !busy;
    renderApproval();
    renderPending();
    renderTrace(true);
    syncOffline();
  }

  function syncOffline() {
    offlineBox.hidden = !(nativeMode && state.offline);
  }

  function renderApproval() {
    const approval = state.approval;
    if (!approval) {
      approvalBox.hidden = true;
      return;
    }
    approvalBox.hidden = false;
    apRisk.textContent = [approval.permission, approval.risk].filter(Boolean).join(" · ");
    apOp.innerHTML =
      "<strong>" + escapeHtml(approval.tool || "操作") + "</strong>" +
      (approval.operation ? " · " + escapeHtml(approval.operation) : "");
    apMeta.textContent = approval.full_access ? "本轮请求完全访问权限" : "cwd " + (approval.workspace || state.cid || "");
    apTurn.hidden = !!approval.full_access;
  }

  function renderPending() {
    const pending = state.pending;
    if (!pending || !(pending.items || []).length) {
      pendingBox.hidden = true;
      return;
    }
    pendingBox.hidden = false;
    const rows = [];
    (pending.items || []).forEach((item) => {
      if (item.kind === "input") {
        (item.questions || []).forEach((question) => {
          rows.push("<div>· " + escapeHtml(question.text || "（问题文本没带出来）") + "</div>");
          (question.options || []).forEach((option) => {
            rows.push('<div class="pending-option">- ' + escapeHtml(option) + "</div>");
          });
        });
      } else {
        rows.push("<div>· 需要确认：" + escapeHtml((item.options || []).join("、") || "（选项没带出来）") + "</div>");
      }
    });
    pdBody.innerHTML = rows.join("");
  }

  function traceSummary(trace) {
    if (!trace) return "";
    const parts = [];
    if (trace.thinking) parts.push("思考 " + trace.thinking.length + " 字");
    const steps = trace.steps || [];
    if (steps.length) {
      const done = steps.filter((step) => step.completed).length;
      parts.push("步骤 " + done + "/" + steps.length);
    }
    if (trace.completed) parts.push("已完成");
    return parts.join(" · ");
  }

  function renderTrace(forceScroll) {
    const trace = state.trace;
    const steps = (trace && trace.steps) || [];
    const visible = !!trace && (state.status !== "idle" || !!trace.thinking || steps.length > 0);
    if (!visible) {
      traceBox.hidden = true;
      return;
    }
    traceBox.hidden = false;
    traceLine.textContent = traceSummary(trace) || state.phase || "执行中";
    traceCaret.textContent = traceOpen ? "▴" : "▾";
    traceBody.hidden = !traceOpen;

    const thinking = (trace.thinking || "").trim();
    const shown = thinking || "（这一步没有输出思考文本）";
    if (traceThink.textContent !== shown) {
      const atBottom = traceThink.scrollHeight - traceThink.scrollTop - traceThink.clientHeight < 24;
      traceThink.textContent = shown;
      if (atBottom || forceScroll) traceThink.scrollTop = traceThink.scrollHeight;
    }

    const rows = steps.map((step) => {
      const mark = step.completed ? '<span class="on">✓</span>' : '<span class="off">•</span>';
      const label = "task " + (step.id ? step.id.slice(-6) : "?") + (step.scene ? " · " + step.scene : "");
      return '<div class="trace-step">' + mark + escapeHtml(label) + "</div>";
    });
    traceSteps.innerHTML = rows.join("") || '<div class="trace-step"><span class="off">•</span>还在准备</div>';
  }

  traceToggle.addEventListener("click", () => {
    traceOpen = !traceOpen;
    try { localStorage.setItem("traceOpen", traceOpen ? "1" : "0"); } catch (error) { /* 忽略 */ }
    renderTrace(false);
  });

  // ------------------------------------------------------------ SSE

  function connect() {
    const stream = new EventSource("/api/events");
    stream.onmessage = (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (error) {
        return;
      }
      if (payload.type === "snapshot") {
        applyStatus(payload.data || {});
        renderMessages((payload.data || {}).messages);
      } else if (payload.type === "message") {
        addMessage(payload.data, true);
      } else if (payload.type === "status") {
        applyStatus(payload.data || {});
      } else if (payload.type === "trace") {
        state.trace = payload.data || null;
        renderTrace(true);
      } else if (payload.type === "tick") {
        const seconds = Number(payload.data && payload.data.elapsed) || 0;
        timerEl.textContent = seconds ? Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0") : "";
        if (seconds === 0) timerEl.textContent = "";
      }
    };
    stream.onerror = () => {
      phaseEl.textContent = "重连中…";
      dot.className = "dot error";
    };
  }

  // ------------------------------------------------------------ 交互

  function autosize() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, Math.round(window.innerHeight * 0.34)) + "px";
  }

  async function submit() {
    const text = input.value.trim();
    if (!text) return;
    if (sendBtn.disabled) { toast("上一轮还没结束"); return; }
    input.value = "";
    autosize();
    const result = await post("/api/send", { text });
    if (!result.ok && result.error) input.value = text;
    else if (state.attach.length) {
      state.attach = [];
      attachBtn.textContent = "＋";
      attachBtn.classList.remove("has");
    }
  }

  sendBtn.addEventListener("click", submit);
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      submit();
    }
  });

  stopBtn.addEventListener("click", () => post("/api/stop"));

  apOnce.addEventListener("click", () => post("/api/approve", { allow: true, scope: "once" }));
  apTurn.addEventListener("click", () => post("/api/approve", { allow: true, scope: "turn" }));
  apDeny.addEventListener("click", () => post("/api/approve", { allow: false, scope: "once" }));
  let attachPaths = [];
  function openAttach() {
    openSheet("本轮附件", (body) => {
      const hint = document.createElement("div");
      hint.className = "hint";
      hint.textContent = "每行一个绝对路径（Termux 里是 /data/data/com.termux/files/home/…）。只作用于下一条消息，发完自动清空。";
      body.appendChild(hint);
      const area = document.createElement("textarea");
      area.rows = 4;
      area.value = (state.attach.length ? state.attach : []).join("\n");
      area.placeholder = "/data/data/com.termux/files/home/report.pdf";
      body.appendChild(field("附件路径", area));
      const row = document.createElement("div");
      row.className = "row";
      row.appendChild(button("保存", "primary", async () => {
        attachPaths = area.value.split("\n").map((item) => item.trim()).filter(Boolean);
        const result = await post("/api/config", { attach: attachPaths });
        if (!result.ok) return;
        state.attach = attachPaths;
        attachBtn.textContent = attachPaths.length ? "＋" + attachPaths.length : "＋";
        attachBtn.classList.toggle("has", attachPaths.length > 0);
        closeSheet();
        toast(attachPaths.length ? "已挂 " + attachPaths.length + " 个附件" : "已清空附件");
      }));
      row.appendChild(button("清空", "ghost", async () => {
        attachPaths = [];
        const result = await post("/api/config", { attach: [] });
        if (!result.ok) return;
        state.attach = [];
        attachBtn.textContent = "＋";
        attachBtn.classList.remove("has");
        closeSheet();
        toast("已清空附件");
      }));
      body.appendChild(row);
    });
  }
  attachBtn.addEventListener("click", openAttach);

  pdOpenBtn.addEventListener("click", () => {
    if (!state.cid) { toast("还没有会话 ID"); return; }
    window.open("https://www.doubao.com/chat/" + state.cid, "_blank", "noopener");
  });

  pdResume.addEventListener("click", () => {
    post("/api/resume").then((result) => {
      if (result && result.ok) toast("已接管这一轮（不重发）");
      else toast((result && result.error) || "接管失败");
    });
  });

  // ------------------------------------------------------------ 弹层

  function openSheet(title, builder) {
    sheetTitle.textContent = title;
    sheetBody.innerHTML = "";
    builder(sheetBody);
    sheet.hidden = false;
  }

  function closeSheet() {
    sheet.hidden = true;
    sheetBody.innerHTML = "";
  }

  // 给 Android 返回键用：面板开着就先关面板
  window.__sheetOpen = function () {
    return !sheet.hidden;
  };
  window.__closeSheet = closeSheet;

  sheet.addEventListener("click", (event) => {
    if (event.target.hasAttribute("data-close")) closeSheet();
  });

  function field(label, control) {
    const wrap = document.createElement("div");
    wrap.className = "field";
    const caption = document.createElement("label");
    caption.textContent = label;
    wrap.appendChild(caption);
    wrap.appendChild(control);
    return wrap;
  }

  function button(text, cls, handler) {
    const node = document.createElement("button");
    node.className = "btn " + (cls || "");
    node.textContent = text;
    node.addEventListener("click", handler);
    return node;
  }

  // ------------------------------------------------------------ 登录

  let loginPoll = 0;
  let loginMiss = 0;

  function loginPanel(body, readProfile) {
    const wrap = document.createElement("div");
    wrap.className = "login";

    const status = document.createElement("div");
    status.className = "login-status";

    const line = document.createElement("div");
    line.className = "hint";

    const qr = document.createElement("img");
    qr.className = "qr";
    qr.alt = "登录二维码";
    qr.hidden = true;

    const qrTip = document.createElement("div");
    qrTip.className = "hint qr-tip";
    qrTip.hidden = true;

    const profile = document.createElement("input");
    profile.type = "text";
    profile.placeholder = "profile 名称，留空用默认";
    const profileField = field("登录 profile（可留空）", profile);

    const cookie = document.createElement("input");
    cookie.type = "text";
    cookie.placeholder = "Cookie 文件绝对路径（新设备扫码会撞 MFA，建议走这条）";
    const cookieField = field("Cookie 文件", cookie);
    const cookieRow = document.createElement("div");
    cookieRow.className = "row";
    const cookieBtn = button("用 Cookie 登录", "soft", () => loginCookie());
    cookieRow.appendChild(cookieBtn);

    const primaryRow = document.createElement("div");
    primaryRow.className = "row";
    const startBtn = button("扫码登录", "primary", () => startLogin());
    startBtn.disabled = true;
    const cancelBtn = button("取消", "ghost", () => cancelLogin());
    cancelBtn.hidden = true;
    primaryRow.appendChild(startBtn);
    primaryRow.appendChild(cancelBtn);

    const secondRow = document.createElement("div");
    secondRow.className = "row";
    const logoutBtn = button("退出登录", "danger", () => logout());
    logoutBtn.hidden = true;
    secondRow.appendChild(logoutBtn);

    wrap.appendChild(status);
    wrap.appendChild(line);
    wrap.appendChild(qr);
    wrap.appendChild(qrTip);
    if (!readProfile) wrap.appendChild(profileField);
    wrap.appendChild(cookieField);
    wrap.appendChild(cookieRow);
    wrap.appendChild(primaryRow);
    wrap.appendChild(secondRow);
    body.appendChild(wrap);

    let waiting = false;

    function render(result) {
      const data = result || {};
      const stateName = data.state || "idle";
      const loggedIn = !!data.loggedIn || stateName === "ok";
      status.textContent = (loggedIn ? "已登录" : data.probed ? "未登录" : "检测中…")
        + (data.accountId ? " · " + data.accountId : "")
        + (data.profile ? " · profile " + data.profile : "");

      if (!waiting && !readProfile && data.profile) profile.value = data.profile;
      profile.disabled = waiting;

      if (loggedIn && stateName !== "waiting") {
        waiting = false;
        line.textContent = "这台机器已登录，可以直接派任务。";
        qr.hidden = true;
        qrTip.hidden = true;
        cancelBtn.hidden = true;
        logoutBtn.hidden = false;
        startBtn.disabled = false;
        startBtn.textContent = "重新登录";
        return;
      }

      logoutBtn.hidden = true;

      if (stateName === "waiting") {
        waiting = true;
        line.textContent = "请用豆包 App 扫码确认（已等 " + (data.elapsed || 0) + " 秒）"
          + (data.detail ? " · " + data.detail : "");
        if (data.qr) {
          qr.src = data.qr;
          qr.hidden = false;
          const age = data.qrAge || 0;
          if (age >= 90) {
            qrTip.textContent = "二维码已等 " + age + " 秒，可能过期了，建议刷新";
            qrTip.hidden = false;
          } else {
            qrTip.textContent = "";
            qrTip.hidden = true;
          }
          startBtn.textContent = "刷新二维码";
          startBtn.disabled = false;
        } else {
          qr.hidden = true;
          qrTip.hidden = true;
          startBtn.textContent = "正在生成…";
          startBtn.disabled = true;
        }
        cancelBtn.hidden = false;
        cancelBtn.disabled = false;
        return;
      }

      waiting = false;
      qr.hidden = true;
      qrTip.hidden = true;
      cancelBtn.hidden = true;
      startBtn.disabled = false;
      startBtn.textContent = data.probed ? "扫码登录" : "检测中…";
      if (stateName === "failed") {
        line.textContent = "登录没成功：" + (data.detail || "未知原因");
      } else if (data.detail && data.detail !== "已登录") {
        line.textContent = data.detail;
      } else {
        line.textContent = "点「扫码登录」用豆包 App 扫一下，成功后这台机器就能真正执行任务。";
      }
    }

    function poll() {
      clearTimeout(loginPoll);
      loginPoll = setTimeout(async () => {
        const result = await getJson("/api/login");
        if (!result || result.ok === false) {
          loginMiss += 1;
          if (loginMiss >= 5) {
            loginMiss = 0;
            line.textContent = "和本地服务失去联系了，刷新页面或重开 Doubao GUI 再试。";
            return;
          }
        } else {
          loginMiss = 0;
        }
        render(result);
        if (result && result.state === "waiting") poll();
      }, 2000);
    }

    async function startLogin() {
      clearTimeout(loginPoll);
      qr.hidden = true;
      qrTip.hidden = true;
      startBtn.disabled = true;
      startBtn.textContent = "正在生成…";
      line.textContent = "正在向 CLI 申请二维码…";
      const force = waiting;
      const name = (readProfile ? readProfile() : profile.value).trim();
      const result = await post("/api/login", { timeout: 180, profile: name, refresh: force });
      render(result);
      if (result && result.state === "waiting") poll();
      else if (result && result.ok === false) toast(result.error || "申请二维码失败");
    }

    async function loginCookie() {
      const path = cookie.value.trim();
      if (!path) { toast("先填 Cookie 文件路径"); return; }
      cookieBtn.disabled = true;
      cookieBtn.textContent = "登录中…";
      const name = (readProfile ? readProfile() : profile.value).trim();
      const result = await post("/api/login/cookie", { path: path, profile: name });
      cookieBtn.disabled = false;
      cookieBtn.textContent = "用 Cookie 登录";
      render(result);
      if (result && result.ok) toast("已登录");
      else toast((result && result.error) || "Cookie 登录失败");
    }

    async function cancelLogin() {
      clearTimeout(loginPoll);
      cancelBtn.disabled = true;
      const result = await post("/api/login/cancel", {});
      render(result);
      toast("已取消这次扫码");
    }

    async function logout() {
      clearTimeout(loginPoll);
      logoutBtn.disabled = true;
      const result = await post("/api/logout", {});
      logoutBtn.disabled = false;
      render(result);
      if (result && result.ok === false) toast(result.error || "退出登录失败");
      else toast("已退出登录");
    }

    getJson("/api/login").then((result) => {
      render(result);
      if (result && result.state === "waiting") poll();
    });
  }

  async function openSettings() {
    const snapshot = await getJson("/api/state");
    const data = (snapshot && snapshot.data) || {};
    const config = data.config || {};
    openSheet("设置", (body) => {
      const workspace = document.createElement("input");
      workspace.type = "text";
      workspace.value = config.workspace || "";
      body.appendChild(field("工作区（CLI 的 -C 目录）", workspace));

      const permission = document.createElement("select");
      const labels = data.permissions || { ask_risky: "风险操作时确认", ask_always: "始终确认", always_allow: "允许所有操作" };
      Object.keys(labels).forEach((key) => {
        const option = document.createElement("option");
        option.value = key;
        option.textContent = labels[key];
        if (key === config.permission) option.selected = true;
        permission.appendChild(option);
      });
      body.appendChild(field("权限模式", permission));

      const mode = document.createElement("select");
      [["work", "work · 执行任务"], ["chat", "chat · 纯对话"], ["goal", "goal · 目标模式"]].forEach(([value, label]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        if (value === config.mode) option.selected = true;
        mode.appendChild(option);
      });
      body.appendChild(field("会话模式", mode));

      const model = document.createElement("select");
      const currentModel = config.model || "";
      model.appendChild(new Option("默认（跟随 CLI）", ""));
      if (currentModel) model.appendChild(new Option(currentModel, currentModel, true, true));
      body.appendChild(field("模型 --model", model));
      getJson("/api/models").then((result) => {
        if (!result || !result.ok) return;
        (result.models || []).forEach((item) => {
          if (!item) return;
          const id = item.id || "";
          const name = item.name || id;
          if (!name) return;
          if (Array.from(model.options).some((option) => option.value === name)) return;
          const label = name !== id ? name + " · " + id : name;
          model.appendChild(new Option(label, name));
          if (config.model === id && id !== name) model.value = name;
        });
      });

      const reasoning = document.createElement("select");
      [["", "默认"], ["low", "low · 省"], ["medium", "medium"], ["high", "high"], ["xhigh", "xhigh"], ["max", "max"]].forEach(([value, label]) => {
        const option = new Option(label, value);
        if ((config.reasoning || "") === value) option.selected = true;
        reasoning.appendChild(option);
      });
      body.appendChild(field("推理档位 --reasoning", reasoning));

      const timeout = document.createElement("input");
      timeout.type = "number";
      timeout.min = "30";
      timeout.max = "3600";
      timeout.value = String(config.timeout || 600);
      body.appendChild(field("等待超时（秒）", timeout));

      const skills = document.createElement("input");
      skills.type = "checkbox";
      skills.checked = config.skills !== false;
      const skillsLabel = document.createElement("label");
      skillsLabel.className = "hint";
      skillsLabel.appendChild(skills);
      skillsLabel.appendChild(document.createTextNode(" 启用默认技能（关掉 = --no-skills）"));
      body.appendChild(skillsLabel);

      const enterprise = document.createElement("input");
      enterprise.type = "checkbox";
      enterprise.checked = !!config.enterprise;
      const entLabel = document.createElement("label");
      entLabel.className = "hint";
      entLabel.appendChild(enterprise);
      entLabel.appendChild(document.createTextNode(" 带上企业知识（仅 work 模式）"));
      body.appendChild(entLabel);

      const project = document.createElement("input");
      project.type = "text";
      project.value = config.project || "";
      project.placeholder = "项目 ID / 名称，留空不绑";
      body.appendChild(field("项目 --project", project));

      const mcp = document.createElement("input");
      mcp.type = "text";
      mcp.value = (config.mcp || []).join(",");
      mcp.placeholder = "connector id，逗号分隔";
      body.appendChild(field("MCP --mcp", mcp));

      const expectJson = document.createElement("input");
      expectJson.type = "checkbox";
      expectJson.checked = !!config.expectJson;
      const ejLabel = document.createElement("label");
      ejLabel.className = "hint";
      ejLabel.appendChild(expectJson);
      ejLabel.appendChild(document.createTextNode(" 要求 JSON 回复（--expect-json）"));
      body.appendChild(ejLabel);

      const schema = document.createElement("input");
      schema.type = "text";
      schema.value = config.replySchema || "";
      schema.placeholder = "JSON schema 文件路径（可选）";
      body.appendChild(field("回复 schema", schema));

      const showTrace = document.createElement("input");
      showTrace.type = "checkbox";
      showTrace.checked = config.showTrace !== false;
      const stLabel = document.createElement("label");
      stLabel.className = "hint";
      stLabel.appendChild(showTrace);
      stLabel.appendChild(document.createTextNode(" 显示过程面板"));
      body.appendChild(stLabel);

      const profile = document.createElement("input");
      profile.type = "text";
      profile.value = config.profile || "";
      body.appendChild(field("profile 名", profile));

      loginPanel(body, () => profile.value);

      const hint = document.createElement("div");
      hint.className = "hint";
      hint.textContent = "权限模式对应 CLI 的 AskOnRisk / AlwaysAsk / FullAccess。改完对下一条消息生效。";
      body.appendChild(hint);

      const row = document.createElement("div");
      row.className = "row";
      row.appendChild(button("保存", "primary", async () => {
        const result = await post("/api/config", {
          workspace: workspace.value,
          permission: permission.value,
          mode: mode.value,
          profile: profile.value,
          model: model.value,
          reasoning: reasoning.value,
          timeout: Number(timeout.value) || 600,
          skills: skills.checked,
          enterprise: enterprise.checked,
          project: project.value,
          mcp: mcp.value.split(",").map((item) => item.trim()).filter(Boolean),
          expect_json: expectJson.checked,
          reply_schema: schema.value,
          show_trace: showTrace.checked,
        });
        if (result.ok) { closeSheet(); toast("已保存"); }
      }));
      row.appendChild(button("体检", "soft", () => openDoctor()));
      row.appendChild(button("检查更新", "ghost", () => openRelease()));
      body.appendChild(row);

      const danger = document.createElement("div");
      danger.className = "row";
      danger.appendChild(button("停止服务", "danger", async () => {
        if (!confirm("停止后页面会断开，需要重新启动。继续？")) return;
        await post("/api/shutdown");
        closeSheet();
        toast("服务已停止");
      }));
      body.appendChild(danger);

      const foot = document.createElement("div");
      foot.className = "hint";
      foot.textContent = "服务只监听 127.0.0.1，同一 Wi-Fi 下的其他设备访问不到。";
      const parts = [];
      if (state.appVersion) parts.push("App v" + state.appVersion);
      if (data.version) parts.push("服务 v" + data.version);
      if (parts.length) foot.textContent += " · " + parts.join(" · ");
      body.appendChild(foot);
    });
  }

  async function openDoctor() {
    openSheet("体检", (body) => {
      const loading = document.createElement("div");
      loading.className = "hint";
      loading.textContent = "检查中…";
      body.appendChild(loading);
      body.appendChild(button("返回设置", "ghost", () => openSettings()));
      getJson("/api/doctor").then((result) => {
        loading.remove();
        if (!result.ok && !result.checks) {
          const failed = document.createElement("div");
          failed.className = "hint";
          failed.textContent = result.error || "检查失败";
          body.insertBefore(failed, body.firstChild);
          return;
        }
        (result.checks || []).forEach((item) => {
          const row = document.createElement("div");
          row.className = "check";
          const mark = document.createElement("span");
          mark.className = item.ok ? "ok" : "no";
          mark.textContent = item.ok ? "✓" : "✗";
          const text = document.createElement("div");
          text.innerHTML = escapeHtml(item.name) + '<div class="d">' + escapeHtml(item.detail || "") + "</div>";
          row.appendChild(mark);
          row.appendChild(text);
          body.insertBefore(row, body.firstChild);
        });
        if ((result.checks || []).some((item) => !item.ok)) loginPanel(body);
      });
    });
  }

  async function openRelease() {
    openSheet("GitHub Release", (body) => {
      const loading = document.createElement("div");
      loading.className = "hint";
      loading.textContent = "正在查 GitHub 上的最新 Release…";
      body.appendChild(loading);

      const stateLine = document.createElement("div");
      stateLine.className = "hint";
      body.appendChild(stateLine);

      const notes = document.createElement("div");
      notes.className = "release-notes";
      notes.hidden = true;
      body.appendChild(notes);

      const links = document.createElement("div");
      links.className = "row";
      body.appendChild(links);

      const command = document.createElement("div");
      command.className = "hint mono";
      command.hidden = true;
      body.appendChild(command);

      body.appendChild(button("返回设置", "ghost", () => openSettings()));

      getJson("/api/release").then((result) => {
        loading.remove();
        if (!result || result.ok === false) {
          stateLine.textContent = "检查失败：" + ((result && result.error) || "网络不可用");
          const open = document.createElement("a");
          open.className = "btn ghost";
          open.href = (result && result.url) || "https://github.com/olddriverdoge/doubao-cli-gui/releases";
          open.target = "_blank";
          open.rel = "noopener";
          open.textContent = "在 GitHub 打开 Releases";
          links.appendChild(open);
          return;
        }
        if (result.empty) {
          stateLine.textContent = result.hint || "仓库还没发布 Release（当前 " + result.current + "）";
        } else {
          stateLine.textContent = result.hasUpdate
            ? "有新版本 " + result.latest + "（当前 " + result.current + "）"
            : "已是最新（当前 " + result.current + " · 最新 " + (result.latest || "无") + "）";
          if (result.hasUpdate) stateLine.className = "hint release-new";
        }
        if (result.publishedAt) {
          const when = document.createElement("div");
          when.className = "hint";
          when.textContent = "发布于 " + String(result.publishedAt).replace("T", " ").replace("Z", " UTC");
          body.insertBefore(when, stateLine.nextSibling);
        }
        if (result.notes) {
          notes.textContent = result.notes;
          notes.hidden = false;
        }
        const open = document.createElement("a");
        open.className = "btn ghost";
        open.href = result.url || "https://github.com/olddriverdoge/doubao-cli-gui/releases";
        open.target = "_blank";
        open.rel = "noopener";
        open.textContent = "打开 Release";
        links.appendChild(open);
        (result.assets || []).forEach((asset) => {
          const link = document.createElement("a");
          link.className = "btn ghost";
          link.href = asset.url || "#";
          link.textContent = "下载 " + (asset.name || "附件");
          links.appendChild(link);
        });
        if (result.hasUpdate) {
          command.textContent = "Termux 升级：cd ~/doubao-cli-gui && git pull --ff-only && bash install.sh";
          command.hidden = false;
        }
      });
    });
  }

  async function openSessions() {
    openSheet("会话", (body) => {
      let limit = 20;
      let archived = false;

      const bar = document.createElement("div");
      bar.className = "row";
      const limitSel = document.createElement("select");
      [10, 20, 50, 100].forEach((value) => {
        const option = new Option(value + " 条", String(value));
        if (value === limit) option.selected = true;
        limitSel.appendChild(option);
      });
      const archLabel = document.createElement("label");
      archLabel.className = "hint";
      const archBox = document.createElement("input");
      archBox.type = "checkbox";
      archLabel.appendChild(archBox);
      archLabel.appendChild(document.createTextNode(" 看归档"));
      bar.appendChild(limitSel);
      bar.appendChild(archLabel);
      body.appendChild(bar);

      const list = document.createElement("div");
      body.appendChild(list);

      body.appendChild(button("＋ 开新会话", "primary", async () => {
        const result = await post("/api/new");
        if (result.ok) { closeSheet(); toast("已开新会话"); }
      }));

      async function load() {
        list.innerHTML = '<div class="hint">读取中…</div>';
        const result = await getJson("/api/sessions?limit=" + limit + (archived ? "&archived=1" : ""));
        list.innerHTML = "";
        if (!result.ok) {
          list.innerHTML = '<div class="hint">' + escapeHtml(result.error || "读取失败") + "</div>";
          return;
        }
        (result.sessions || []).forEach((row) => {
          const node = document.createElement("button");
          node.className = "sess" + (row.id === state.cid ? " active" : "");
          node.innerHTML = '<span class="t">' + escapeHtml(row.title) + '</span><span class="i">' + escapeHtml(row.id) + "</span>";
          node.addEventListener("click", async () => {
            const switched = await post("/api/switch", { id: row.id });
            if (switched.ok) { closeSheet(); toast("已切换"); }
          });
          list.appendChild(node);

          const tools = document.createElement("div");
          tools.className = "sess-tools";
          const act = async (action, value, done) => {
            const result = await post("/api/session/action", { action: action, id: row.id, value: value || "" });
            if (!result.ok) return toast(result.error || "操作失败");
            toast(done);
            load();
          };
          tools.appendChild(button(row.pinned ? "取消置顶" : "置顶", "mini", () => act(row.pinned ? "unpin" : "pin", "", "已更新")));
          tools.appendChild(button("重命名", "mini", () => {
            const title = prompt("新的标题（最多 20 字）", row.title);
            if (!title) return;
            act("rename", title.trim(), "已重命名");
          }));
          tools.appendChild(button(row.archived ? "取消归档" : "归档", "mini", () => act(row.archived ? "unarchive" : "archive", "", "已更新")));
          tools.appendChild(button("移动", "mini", () => {
            const target = prompt("移到哪个项目？（项目 ID 或名称，输入 none 清空）", "");
            if (!target) return;
            act("move", target.trim(), "已移动");
          }));
          list.appendChild(tools);
        });
        if (!(result.sessions || []).length) {
          list.innerHTML = '<div class="hint">没有会话。</div>';
        }
      }

      limitSel.addEventListener("change", () => {
        limit = Number(limitSel.value) || 20;
        load();
      });
      archBox.addEventListener("change", () => {
        archived = archBox.checked;
        load();
      });
      load();
    });
  }

  async function openFiles() {
    const snapshot = await getJson("/api/state");
    const workspace = ((snapshot && snapshot.data && snapshot.data.config) || {}).workspace || "(未设置)";
    openSheet("工作区", (body) => {
      const row = document.createElement("div");
      row.className = "sess";
      row.innerHTML = '<span class="i">豆包读写范围</span><span class="t mono">' + escapeHtml(workspace) + "</span>";
      body.appendChild(row);

      const hint = document.createElement("div");
      hint.className = "hint";
      hint.textContent =
        "所有文件操作都在这个目录里，CLI 收到的路径都相对它。想看文件就在 Termux 里 ls / cat，或者用手机文件管理器打开这个路径。";
      body.appendChild(hint);

      if (!navigator.clipboard) return;
      body.appendChild(button("复制路径", "soft", () => {
        navigator.clipboard.writeText(workspace).then(() => toast("已复制")).catch(() => toast("复制失败"));
      }));
    });
  }

  document.querySelector(".quick").addEventListener("click", (event) => {
    const act = event.target.getAttribute("data-act");
    if (!act) return;
    if (act === "new") post("/api/new").then((result) => result.ok && toast("已开新会话"));
    else if (act === "resume") post("/api/resume").then((result) => result.ok && toast("续等中"));
    else if (act === "sessions") openSessions();
    else if (act === "settings") openSettings();
    else if (act === "files") openFiles();
  });

  document.getElementById("menu").addEventListener("click", openSettings);

  function bootHint(detail) {
    if (detail === "termux-missing") return "没找到 Termux 的 RUN_COMMAND 服务，用「复制启动命令」手动跑";
    if (detail === "permission-denied") return "去系统设置给「豆包 CLI」授予「在 Termux 中执行命令」权限";
    if (detail === "no-java") return "浏览器版不需要启动服务";
    if (detail && String(detail).indexOf("error") === 0) return "Termux 拒绝执行：先确认 allow-external-apps=true 并重启 Termux";
    return "启动请求没成功：" + (detail || "未知原因");
  }

  const BOOT_COMMAND = "doubao-gui start";

  copyCmdBtn.addEventListener("click", () => {
    const done = () => toast("已复制，去 Termux 粘贴执行");
    if (navigator.clipboard) {
      navigator.clipboard.writeText(BOOT_COMMAND).then(done).catch(() => toast(BOOT_COMMAND));
    } else {
      toast(BOOT_COMMAND);
    }
  });

  openTermuxBtn.addEventListener("click", async () => {
    const result = await post("/app/open-termux", {});
    if (!result || !result.ok) toast("没打开：" + ((result && result.detail) || "未知原因"));
  });

  async function waitForUpstream(attempt) {
    if (attempt > 12) {
      toast("还没起来，去 Termux 跑 doubao-gui logs 看看");
      bootBtn.disabled = false;
      bootBtn.textContent = "启动服务";
      return;
    }
    const status = await getJson("/app/status");
    if (status && status.ok) {
      bootBtn.disabled = false;
      bootBtn.textContent = "启动服务";
      toast("已连上");
      return;
    }
    setTimeout(() => waitForUpstream(attempt + 1), 1200);
  }

  bootBtn.addEventListener("click", async () => {
    bootBtn.disabled = true;
    bootBtn.textContent = "启动中…";
    const result = await post("/app/boot", {});
    if (result && result.ok) {
      toast("已通知 Termux 启动，等它一会儿");
      waitForUpstream(0);
    } else {
      toast(bootHint(result && result.detail));
      bootBtn.disabled = false;
      bootBtn.textContent = "启动服务";
    }
  });

  retryBtn.addEventListener("click", () => location.reload());

  // ------------------------------------------------------------ 启动

  // APK 壳里才有这个接口；浏览器版拿不到，也就不会出现“启动服务”那一条
  fetch("/app/available")
    .then((response) => (response.ok ? response.json() : null))
    .then((data) => {
      nativeMode = !!(data && data.ok);
      state.appVersion = (data && data.appVersion) || "";
      syncOffline();
    })
    .catch(() => {});

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  getJson("/api/state").then((snapshot) => {
    const data = (snapshot && snapshot.data) || {};
    const config = data.config || {};
    state.attach = config.attach || [];
    state.trace = data.trace || null;
    attachBtn.textContent = state.attach.length ? "＋" + state.attach.length : "＋";
    attachBtn.classList.toggle("has", state.attach.length > 0);
    renderTrace(false);
  });

  autosize();
  connect();
})();
