# -*- coding: utf-8 -*-
"""APK 内的网关。

职责只有三件：
1. 把打包进 APK 的前端页面发出去（WebView 指到 127.0.0.1:8790）；
2. 把 /api/* 原样反代给 Termux 里跑的 doubao-cli-gui 服务（默认 8765），
   SSE 也一起转发，所以前端代码和 Termux 版完全同一套；
3. Termux 服务没起的时候，用 RUN_COMMAND 把它拉起来，并给前端一个
   “未启动”的快照，而不是白屏。
"""

import asyncio
import json
import os
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

WEB_DIR = Path(__file__).resolve().parent / "web"
DEFAULT_UPSTREAM = "http://127.0.0.1:8765"
HOP_HEADERS = {
    "host", "content-length", "transfer-encoding", "connection", "keep-alive",
    "proxy-authenticate", "proxy-authorization", "te", "trailer", "upgrade",
}
PERMISSIONS = {"ask_risky": "风险操作时确认", "ask_always": "始终确认", "always_allow": "允许所有操作"}


def upstream_base() -> str:
    return (os.environ.get("DOUBAO_GUI_UPSTREAM") or DEFAULT_UPSTREAM).rstrip("/")


def offline_snapshot(reason: str) -> dict:
    return {
        "status": "error",
        "phase": "Termux 服务未启动",
        "conversationId": "",
        "runId": "",
        "startedAt": 0,
        "approval": None,
        "messages": [],
        "config": {
            "workspace": "",
            "permission": "ask_risky",
            "permissionLabel": PERMISSIONS["ask_risky"],
            "mode": "work",
            "profile": "",
            "bin": "doubao",
        },
        "permissions": PERMISSIONS,
        "offline": True,
        "reason": reason,
    }


def sse(payload: dict) -> bytes:
    return ("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8")


async def probe_state(timeout: float = 2.0):
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(upstream_base() + "/api/state")
            if response.status_code != 200:
                return None
            return response.json().get("data")
    except Exception:
        return None


def termux_state():
    try:
        from java import jclass

        python_class = jclass("com.chaquo.python.Python")
        context = python_class.getPlatform().getApplication()
        return str(jclass("com.olddriverdoge.doubaocli.TermuxBridge").describe(context))
    except Exception:
        return None


def boot_termux() -> str:
    try:
        from java import jclass

        python_class = jclass("com.chaquo.python.Python")
        context = python_class.getPlatform().getApplication()
        return str(jclass("com.olddriverdoge.doubaocli.TermuxBridge").boot(context))
    except Exception as error:
        return "no-java:" + type(error).__name__


# ------------------------------------------------------------------ 路由


async def api_proxy(request):
    target = upstream_base() + request.url.path
    if request.url.query:
        target += "?" + request.url.query
    headers = {key: value for key, value in request.headers.items() if key.lower() not in HOP_HEADERS}
    body = await request.body()
    client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=3.0))
    try:
        upstream_request = client.build_request(request.method, target, headers=headers, content=body)
        upstream_response = await client.send(upstream_request, stream=True)
    except Exception as error:
        await client.aclose()
        if request.url.path.endswith("/events"):
            return _offline_events(str(error))
        return JSONResponse(
            {"ok": False, "offline": True, "error": "Termux 服务未启动：" + type(error).__name__},
            status_code=503,
        )

    response_headers = {
        key: value for key, value in upstream_response.headers.items()
        if key.lower() not in HOP_HEADERS and key.lower() != "content-type"
    }

    async def body_stream():
        try:
            async for chunk in upstream_response.aiter_raw():
                yield chunk
        finally:
            await upstream_response.aclose()
            await client.aclose()

    return StreamingResponse(
        body_stream(),
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
        background=None,
    )


def _offline_events(reason: str) -> StreamingResponse:
    async def generator():
        yield sse({"type": "snapshot", "data": offline_snapshot(reason)})
        # 每 3 秒探一次；Termux 服务一起来就把真快照推过去，然后断开让
        # EventSource 重连到真正的反代通道。
        for _ in range(100):
            await asyncio.sleep(3)
            state = await probe_state()
            if state is not None:
                yield sse({"type": "snapshot", "data": state})
                return
            yield b": ping\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def app_available(request):
    return JSONResponse({"ok": True, "upstream": upstream_base(), "termux": termux_state()})


async def app_status(request):
    state = await probe_state()
    return JSONResponse({
        "ok": state is not None,
        "upstream": upstream_base(),
        "termux": termux_state(),
        "state": state,
    })


async def app_boot(request):
    detail = boot_termux()
    return JSONResponse({"ok": detail == "sent", "detail": detail})


def open_termux_app() -> str:
    try:
        from java import jclass

        python_class = jclass("com.chaquo.python.Python")
        context = python_class.getPlatform().getApplication()
        return str(jclass("com.olddriverdoge.doubaocli.TermuxBridge").openApp(context))
    except Exception as error:
        return "no-java:" + type(error).__name__


async def app_open_termux(request):
    detail = open_termux_app()
    return JSONResponse({"ok": detail.startswith("opened"), "detail": detail})


routes = [
    Route("/app/available", app_available),
    Route("/app/status", app_status),
    Route("/app/boot", app_boot, methods=["POST", "GET"]),
    Route("/app/open-termux", app_open_termux, methods=["POST", "GET"]),
    Route("/api/{path:path}", api_proxy, methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]),
]

if WEB_DIR.is_dir():
    routes.append(Mount("/", app=StaticFiles(directory=str(WEB_DIR), html=True), name="web"))

app = Starlette(routes=routes)


def serve(port: int = 8790) -> None:
    import uvicorn

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=int(port),
        log_level="warning",
        access_log=False,
        loop="asyncio",
        http="h11",
    )
    uvicorn.Server(config).run()
