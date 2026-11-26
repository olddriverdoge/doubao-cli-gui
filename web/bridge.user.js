// ==UserScript==
// @name         豆包网页 · 本地 CLI 桥接
// @namespace    doubao-cli-gui
// @version      0.1.14
// @description  把网页上的发送/停止/授权转到本机 doubao-cli；视觉仍用官方页面。默认只观察（探针），采样齐了再开接管。
// @author       doubao-cli-gui
// @match        https://www.doubao.com/*
// @match        https://doubao.com/*
// @match        https://*.doubao.com/*
// @grant        none
// @run-at       document-start
// ==/UserScript==

(function () {
  'use strict';

  var BASE = 'http://127.0.0.1:8765';
  var K = { token: 'dbg.bridge.token', mode: 'dbg.bridge.mode', rule: 'dbg.bridge.rule' };

  function get(key, dflt) {
    try { var v = localStorage.getItem(key); return v === null ? dflt : v; } catch (e) { return dflt; }
  }
  function set(key, value) {
    try { localStorage.setItem(key, String(value)); } catch (e) {}
  }
  function mode() { return get(K.mode, 'probe'); }
  function token() { return get(K.token, ''); }

  // 发送类请求的猜测规则；采样到真实端点后可在设置里改写
  var DEFAULT_RULES = [
    '/api/.*(chat|message|conversation).*(send|create|submit|completion)',
    '/api/chat/completion',
    '/send_message',
    '/(chat|conversation)/(send|create)'
  ];
  function rules() {
    var raw = get(K.rule, '');
    if (!raw) return DEFAULT_RULES;
    try { var arr = JSON.parse(raw); return Array.isArray(arr) && arr.length ? arr : DEFAULT_RULES; } catch (e) { return DEFAULT_RULES; }
  }
  function matchSend(url) {
    return rules().some(function (pat) {
      try { return new RegExp(pat, 'i').test(url); } catch (e) { return false; }
    });
  }

  // ---------- 和本机 GUI 通信 ----------

  function rpc(path, opts) {
    opts = opts || {};
    var headers = { 'Content-Type': 'application/json' };
    if (token()) headers['X-Doubao-Bridge'] = token();
    return fetch(BASE + path, {
      method: opts.method || 'GET',
      headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      mode: 'cors',
      cache: 'no-store',
      credentials: 'omit'
    }).then(function (res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    });
  }

  // ---------- 采样：请求体、响应片段、SSE 帧 ----------

  var buffer = [];
  var captured = 0;

  function capture(kind, data) {
    if (mode() !== 'probe') return;
    captured += 1;
    buffer.push(Object.assign({ kind: kind, at: new Date().toISOString() }, data));
    if (buffer.length >= 20) flush();
  }

  function flush() {
    if (!buffer.length) return;
    var items = buffer.splice(0, buffer.length);
    rpc('/api/web/capture', { method: 'POST', body: { kind: 'batch', items: items } }).catch(function () {});
  }
  setInterval(flush, 6000);

  function bodyText(body) {
    if (typeof body === 'string') return body.slice(0, 3000);
    if (body instanceof FormData) {
      var pairs = [];
      try {
        body.forEach(function (v, k) { pairs.push(k + '=' + (typeof v === 'string' ? v : '[file]')); });
      } catch (e) {}
      return pairs.join('&').slice(0, 3000);
    }
    return null;
  }

  // fetch
  var rawFetch = window.fetch;
  if (typeof rawFetch === 'function') {
    window.fetch = function (input, init) {
      var url = typeof input === 'string' ? input : (input && input.url) || '';
      var method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
      var body = init ? bodyText(init.body) : null;
      var hit = method !== 'GET' && matchSend(url);
      var watch = hit || /\/api\//.test(url);

      if (watch) capture('fetch', { url: url, method: method, send: hit, body: body });

      var promise = rawFetch.apply(this, arguments);

      if (watch && method !== 'GET' && promise && promise.then) {
        promise.then(function (res) {
          try {
            var copy = res.clone();
            var type = res.headers.get('content-type') || '';
            if (type.indexOf('event-stream') >= 0 || type.indexOf('text/plain') >= 0) {
              // 流式响应：读前若干字节，够看帧结构
              var reader = copy.body && copy.body.getReader ? copy.body.getReader() : null;
              if (reader) {
                var chunks = [];
                var total = 0;
                var pump = function () {
                  reader.read().then(function (step) {
                    if (step.done || total > 40000) {
                      capture('sse', { url: url, status: res.status, frames: chunks.join('').slice(0, 20000) });
                      return;
                    }
                    total += (step.value && step.value.length) || 0;
                    if (step.value) chunks.push(new TextDecoder('utf-8').decode(step.value));
                    pump();
                  }).catch(function () {});
                };
                pump();
              }
            } else {
              copy.text().then(function (t) {
                capture('resp', { url: url, status: res.status, text: t.slice(0, 6000) });
              }).catch(function () {});
            }
          } catch (e) {}
        }).catch(function () {});
      }
      return promise;
    };
  }

  // XHR
  var XO = XMLHttpRequest.prototype.open;
  var XS = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__dbg = { method: String(method || 'GET').toUpperCase(), url: String(url || '') };
    return XO.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    var info = this.__dbg;
    if (info && /\/api\//.test(info.url)) {
      var hit = info.method !== 'GET' && matchSend(info.url);
      capture('xhr', { url: info.url, method: info.method, send: hit, body: bodyText(body) });
      var self = this;
      this.addEventListener('load', function () {
        try {
          capture('xhr-resp', { url: info.url, status: self.status, text: String(self.responseText || '').slice(0, 6000) });
        } catch (e) {}
      });
    }
    return XS.apply(this, arguments);
  };

  // EventSource
  var RawES = window.EventSource;
  if (typeof RawES === 'function') {
    window.EventSource = function (url, cfg) {
      var es = new RawES(url, cfg);
      try {
        var seen = 0;
        var frames = [];
        es.addEventListener('message', function (ev) {
          if (seen >= 40) return;
          seen += 1;
          frames.push(String(ev.data || '').slice(0, 800));
          if (seen === 40) capture('es', { url: String(url), frames: frames.join('\n') });
        });
      } catch (e) {}
      return es;
    };
    window.EventSource.prototype = RawES.prototype;
  }

  // ---------- 浮动条 ----------

  var state = null;
  var bar = null;
  var timer = null;

  function css(el, obj) { Object.keys(obj).forEach(function (k) { el.style[k] = obj[k]; }); }

  function button(label, onclick, color) {
    var b = document.createElement('button');
    b.textContent = label;
    css(b, {
      margin: '0 4px 4px 0', padding: '3px 8px', fontSize: '12px', lineHeight: '16px',
      color: '#fff', background: color || '#3a3f4b', border: '0', borderRadius: '4px', cursor: 'pointer'
    });
    b.onclick = onclick;
    return b;
  }

  function ensureBar() {
    if (bar && document.body) return bar;
    if (!document.body) return null;
    bar = document.createElement('div');
    bar.id = 'dbg-bridge-bar';
    css(bar, {
      position: 'fixed', right: '10px', bottom: '64px', width: '286px', zIndex: '2147483000',
      background: 'rgba(20,22,28,.92)', color: '#e9ecf2', font: '12px/1.5 -apple-system,system-ui,sans-serif',
      border: '1px solid rgba(255,255,255,.18)', borderRadius: '8px', padding: '8px 10px',
      boxShadow: '0 6px 20px rgba(0,0,0,.35)'
    });
    document.body.appendChild(bar);
    return bar;
  }

  function row(label, value) {
    return '<div style="display:flex;gap:6px"><span style="opacity:.62;min-width:52px">' + label +
      '</span><span style="flex:1;word-break:break-all">' + (value || '-') + '</span></div>';
  }

  function render() {
    var el = ensureBar();
    if (!el) return;
    var s = state;
    if (!s) {
      el.innerHTML = '<div style="margin-bottom:6px;font-weight:600">本地 CLI</div>' +
        '<div style="opacity:.7">没连上 127.0.0.1:8765</div>';
      el.appendChild(button('填 token', askToken));
      return;
    }
    var tone = { done: '#2ea043', running: '#1f6feb', approval: '#bf8700', stalled: '#d1242f', error: '#d1242f' }[s.status] || '#6e7681';
    el.innerHTML =
      '<div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">' +
        '<span style="width:8px;height:8px;border-radius:50%;background:' + tone + '"></span>' +
        '<span style="font-weight:600">' + (s.phase || s.status || '-') + '</span>' +
        '<span style="margin-left:auto;opacity:.55">' + (s.version || '') + '</span>' +
      '</div>' +
      row('会话', String(s.conversationId || '').slice(-8)) +
      row('run', String(s.runId || '').slice(-8)) +
      row('工作区', s.workspace) +
      row('模式', (s.mode || '') + ' / ' + (s.permission || '')) +
      row('探针', '已采 ' + captured + ' 条 · ' + mode());
    if (s.stalled) {
      var warn = document.createElement('div');
      warn.style.cssText = 'margin:6px 0;padding:5px 6px;border-radius:4px;background:rgba(209,36,47,.16);color:#ffb4b4';
      warn.textContent = '云端还在跑、本地已退出：页面会一直转圈。点「续等」。';
      el.appendChild(warn);
    }
    var actions = document.createElement('div');
    actions.style.cssText = 'margin-top:6px';
    if (s.approval) {
      actions.appendChild(button('允许', function () { act('/api/web/approve', { allow: true, scope: 'once' }); }, '#2ea043'));
      actions.appendChild(button('本轮全允许', function () { act('/api/web/approve', { allow: true, scope: 'turn' }); }, '#1f6feb'));
      actions.appendChild(button('拒绝', function () { act('/api/web/approve', { allow: false }); }, '#d1242f'));
    }
    if (s.canResume) actions.appendChild(button('续等', function () { act('/api/web/resume'); }, '#bf8700'));
    if (s.canStop) actions.appendChild(button('停止', function () { act('/api/web/stop'); }, '#d1242f'));
    actions.appendChild(button('本地发', askSend));
    actions.appendChild(button(mode() === 'probe' ? '开接管' : '回探针', toggleMode));
    actions.appendChild(button('填 token', askToken));
    el.appendChild(actions);
  }

  function act(path, body) {
    rpc(path, { method: 'POST', body: body || {} }).then(poll).catch(function (e) {
      alert('调用失败：' + e.message + '\n（先在浮动条里填 token）');
    });
  }

  function askToken() {
    var v = prompt('本机 GUI 启动时终端打印的「桥接」token：', token());
    if (v) { set(K.token, v.trim()); poll(); }
  }

  function askSend() {
    var text = prompt('用本地 CLI 发一条（会写进当前云端会话）：');
    if (!text) return;
    var cid = (state && state.conversationId) || cidFromLocation();
    act('/api/web/send', { text: text, conversationId: cid || '' });
  }

  function toggleMode() {
    var next = mode() === 'probe' ? 'bridge' : 'probe';
    set(K.mode, next);
    render();
  }

  function cidFromLocation() {
    var m = String(location.href).match(/(\d{12,24})/);
    return m ? m[1] : '';
  }

  function poll() {
    rpc('/api/web/state').then(function (res) {
      state = res.data || null;
    }).catch(function () {
      state = null;
    }).finally(render);
  }

  function boot() {
    if (!document.body) { setTimeout(boot, 300); return; }
    poll();
    if (!timer) timer = setInterval(poll, 1500);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
