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

  const state = { status: "idle", phase: "空闲", approval: null, cid: "", rendered: new Set() };
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
    state.approval = data.approval || null;
    if (data.conversationId !== undefined) {
      state.cid = data.conversationId || "";
      cidEl.textContent = state.cid ? "会话 " + state.cid.slice(-6) : "无会话";
    }
    dot.className = "dot " + (state.status === "approval" ? "approval" : state.status);
    phaseEl.textContent = state.phase || "空闲";
    const running = state.status === "running" || state.status === "approval";
    sendBtn.disabled = running;
    stopBtn.hidden = !running;
    renderApproval();
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

  // ------------------------------------------------------------ 弹层

  function openSheet(title, builder) {
    sheetTitle.textContent = title;
    sheetBody.innerHTML = "";
    builder(sheetBody);
    sheet.hidden = false;
  }

  function closeSheet() {
    sheet.hidden = true;
  }

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

      const profile = document.createElement("input");
      profile.type = "text";
      profile.value = config.profile || "";
      body.appendChild(field("profile 名", profile));

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
        });
        if (result.ok) { closeSheet(); toast("已保存"); }
      }));
      row.appendChild(button("体检", "soft", () => openDoctor()));
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
      });
    });
  }

  async function openSessions() {
    openSheet("会话", (body) => {
      const loading = document.createElement("div");
      loading.className = "hint";
      loading.textContent = "读取中…";
      body.appendChild(loading);
      body.appendChild(button("＋ 开新会话", "primary", async () => {
        const result = await post("/api/new");
        if (result.ok) { closeSheet(); toast("已开新会话"); }
      }));
      getJson("/api/sessions").then((result) => {
        loading.remove();
        if (!result.ok) {
          const failed = document.createElement("div");
          failed.className = "hint";
          failed.textContent = result.error || "读取失败";
          body.appendChild(failed);
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
          body.appendChild(node);
        });
        if (!(result.sessions || []).length) {
          const empty = document.createElement("div");
          empty.className = "hint";
          empty.textContent = "还没有历史会话。";
          body.appendChild(empty);
        }
      });
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

  // ------------------------------------------------------------ 启动

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  autosize();
  connect();
})();
