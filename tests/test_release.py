#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub Release 检查的端到端测试：本地起一个假 GitHub API，不联网。"""

import http.server
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from test_api import Harness, RESULTS, api, check, free_port  # noqa: E402


class FakeRelease(http.server.BaseHTTPRequestHandler):
    hits = 0
    payload: dict = {}
    status = 200

    def do_GET(self):  # noqa: N802
        type(self).hits += 1
        body = json.dumps(type(self).payload).encode("utf-8")
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve(port: int) -> http.server.ThreadingHTTPServer:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), FakeRelease)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


RELEASE_OK = {
    "tag_name": "v99.9.9",
    "name": "测试版 99.9.9",
    "published_at": "2026-11-26T08:00:00Z",
    "html_url": "https://github.com/olddriverdoge/doubao-cli-gui/releases/tag/v99.9.9",
    "body": "## 更新说明\n- 新增检查更新\n- 修了登录面板",
    "assets": [
        {"name": "doubao-cli-gui-99.9.9.apk", "size": 22000000,
         "browser_download_url": "https://example.com/a.apk"},
    ],
}


def scenario_release() -> None:
    print("\n[1] 有新版本 / 已是最新 / 500 / 缓存 / 强制刷新")
    fake_port = free_port()
    server = serve(fake_port)
    url = f"http://127.0.0.1:{fake_port}/latest"
    FakeRelease.hits = 0
    FakeRelease.status = 200
    FakeRelease.payload = dict(RELEASE_OK)
    try:
        with Harness({"DOUBAO_GUI_RELEASE_API": url, "DOUBAO_GUI_RELEASE_TTL": "30"}) as harness:
            port = harness.port
            state = api(port, "GET", "/api/state", timeout=10)
            current = (state.get("data") or {}).get("version")
            check("取到本地版本号", bool(current), str(current))

            first = api(port, "GET", "/api/release", timeout=20)
            check("接口返回 ok", first.get("ok") is True, str(first.get("error")))
            check("current 与本地一致", first.get("current") == current, str(first.get("current")))
            check("latest 是 tag", first.get("latest") == "v99.9.9", str(first.get("latest")))
            check("识别到有更新", first.get("hasUpdate") is True)
            check("带 Release 链接", str(first.get("url", "")).startswith("https://github.com/"))
            check("带更新说明", "检查更新" in str(first.get("notes", "")))
            check("带附件下载链接", len(first.get("assets") or []) == 1
                  and (first["assets"][0].get("url", "").endswith("a.apk")))
            check("首次未命中缓存", first.get("cached") is False)
            check("只打了一次 GitHub", FakeRelease.hits == 1, str(FakeRelease.hits))

            FakeRelease.payload = dict(RELEASE_OK, tag_name="v88.8.8")
            second = api(port, "GET", "/api/release", timeout=20)
            check("缓存期内不再请求", FakeRelease.hits == 1, str(FakeRelease.hits))
            check("缓存命中标记", second.get("cached") is True)
            check("缓存返回旧值", second.get("latest") == "v99.9.9", str(second.get("latest")))

            forced = api(port, "GET", "/api/release?force=1", timeout=20)
            check("force 绕过缓存", FakeRelease.hits == 2, str(FakeRelease.hits))
            check("force 拿到新 tag", forced.get("latest") == "v88.8.8", str(forced.get("latest")))
            check("force 标记未缓存", forced.get("cached") is False)

            FakeRelease.payload = dict(RELEASE_OK, tag_name="v0.0.1")
            same = api(port, "GET", "/api/release?force=1", timeout=20)
            check("低版本不算更新", same.get("hasUpdate") is False, str(same.get("latest")))

            FakeRelease.status = 404
            FakeRelease.payload = {"message": "Not Found"}
            empty = api(port, "GET", "/api/release?force=1", timeout=20)
            check("仓库没 Release 时 ok=True", empty.get("ok") is True)
            check("空仓库标记 empty", empty.get("empty") is True)
            check("空仓库不算更新", empty.get("hasUpdate") is False)
            check("空仓库给提示", "Release" in str(empty.get("hint")), str(empty.get("hint")))
            check("空仓库仍带链接", str(empty.get("url", "")).endswith("/releases"))

            FakeRelease.status = 500
            FakeRelease.payload = {"message": "boom"}
            broken = api(port, "GET", "/api/release?force=1", timeout=20)
            check("GitHub 报错时 ok=False", broken.get("ok") is False)
            check("错误里有 HTTP 状态", "500" in str(broken.get("error")), str(broken.get("error")))
            check("失败也带本地版本", broken.get("current") == current)
            check("失败带兜底链接", str(broken.get("url", "")).startswith("https://github.com/"))
    finally:
        server.shutdown()


def scenario_offline() -> None:
    print("\n[2] GitHub 连不上时给出可读错误")
    dead = free_port()
    with Harness({"DOUBAO_GUI_RELEASE_API": f"http://127.0.0.1:{dead}/latest",
                  "DOUBAO_GUI_RELEASE_TTL": "0"}) as harness:
        port = harness.port
        result = api(port, "GET", "/api/release", timeout=20)
        check("离线时 ok=False", result.get("ok") is False)
        check("离线错误可读", "取不到 Release" in str(result.get("error")), str(result.get("error")))
        check("离线仍能读页面", api(port, "GET", "/api/state", timeout=10).get("ok") is True)


if __name__ == "__main__":
    scenario_release()
    scenario_offline()
    total = len(RESULTS)
    failed = [row for row in RESULTS if not row[1]]
    print(f"\n结果：{total - len(failed)}/{total} 通过")
    for label, _, detail in failed:
        print("  ✗", label, detail)
    sys.exit(1 if failed else 0)
