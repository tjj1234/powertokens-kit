#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PT Gateway HTTP 服务（stdlib http.server，零外部依赖）。

把 pt_gateway.TaskGateway / ModelRegistry 暴露为统一 REST 接口，调用方不必关心
「Seedance 要用 media-text + asset://」「各家结果 URL 位置不同」等差异，也不用各写一套轮询。

端点：
  GET  /health                       -> {"ok":true,"service":"pt-gateway"}
  POST /submit                       -> {model, prompt, duration?, resolution?, ratio?, first_frame?, ref_image?, ref_video?, no_audio?, no_prompt_extend?, watermark?, seed?, wait?}
                                     返回 {task_id, kind} 或（wait=true）归一化终态
  GET  /status?task_id=<id>          -> 归一化状态 {task_id,state,progress,url,raw}
  POST /wait                         -> {task_id, timeout?} 轮询到终态
  POST /sync                         -> 触发 ModelRegistry.sync(key)，返回 {added,removed,total_enabled,last_synced}
  GET  /models?kind=video|seedance   -> 注册表里 enabled 的异步模型列表（自动惰性同步）

Key：服务端与 pt_video 一致——环境变量 POWERTOKENS_API_KEY 或 config.json（~/.config/powertokens-video/config.json）。
端口：环境变量 PT_GATEWAY_PORT（默认 8787）。

启动：
  python server.py
  # 或指定端口
  PT_GATEWAY_PORT=9000 python server.py
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 复用同目录的网关核心
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pt_gateway import TaskGateway, ModelRegistry, resolve_key, ApiError, ASYNC_KINDS  # noqa: E402

PORT = int(os.environ.get("PT_GATEWAY_PORT", "8787"))
REGISTRY = ModelRegistry().load()
GW = TaskGateway(registry=REGISTRY)


def _autosync_best_effort():
    key = resolve_key()
    if key:
        REGISTRY.auto_sync_if_stale(key)


class Handler(BaseHTTPRequestHandler):
    server_version = "pt-gateway/0.1.0"

    # ---- 工具 ----
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        # 允许浏览器/任意客户端跨域调用（与 PT 私有 API 的 CORS 策略一致）
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def log_message(self, fmt, *args):  # 静默默认访问日志，改走 stderr 精简
        sys.stderr.write("[gateway] " + (fmt % args) + "\n")

    # ---- 路由 ----
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/health"):
            return self._send(200, {"ok": True, "service": "pt-gateway"})
        if self.path.startswith("/capabilities"):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            kind = (qs.get("kind") or [None])[0]
            _autosync_best_effort()
            return self._send(200, GW.list_capabilities(kind))
        if self.path.startswith("/status"):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            tid = (qs.get("task_id") or [None])[0]
            if not tid:
                return self._send(400, {"error": "missing task_id"})
            try:
                return self._send(200, GW.get_status(tid))
            except ApiError as e:
                return self._send(502, {"error": str(e.message), "status": e.status})
        if self.path.startswith("/models"):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            kind = (qs.get("kind") or [None])[0]
            if kind and kind not in ASYNC_KINDS:
                return self._send(400, {"error": f"kind 必须是 {ASYNC_KINDS} 之一"})
            _autosync_best_effort()
            models = REGISTRY.enabled_async(kind)
            return self._send(200, {
                "kind": kind, "count": len(models), "models": models,
                "last_synced": REGISTRY.data.get("last_synced"),
            })
        return self._send(404, {"error": "not found", "routes": [
            "GET /health", "GET /capabilities?kind=", "POST /generate",
            "POST /submit", "GET /status?task_id=",
            "POST /wait", "POST /sync", "GET /models?kind=",
        ]})

    def do_POST(self):
        if self.path.startswith("/generate"):
            body = self._read_json()
            try:
                out = GW.media_generate(
                    kind=body.get("kind"),
                    model=body.get("model", "auto"),
                    prompt=body.get("prompt", ""),
                    aspect_ratio=body.get("aspect_ratio"),
                    n=body.get("n"),
                    duration=int(body.get("duration", 5)),
                    resolution=body.get("resolution", "720p"),
                    ratio=body.get("ratio", "16:9"),
                    first_frame=body.get("first_frame"),
                    ref_image=body.get("ref_image"),
                    ref_video=body.get("ref_video"),
                    no_audio=bool(body.get("no_audio", False)),
                    no_prompt_extend=bool(body.get("no_prompt_extend", False)),
                    watermark=bool(body.get("watermark", False)),
                    seed=body.get("seed"),
                    wait=bool(body.get("wait", False)),
                    timeout=int(body.get("timeout", 600)),
                    voice=body.get("voice"),
                    params=body.get("params"),
                    messages=body.get("messages"),
                    system=body.get("system"),
                    temperature=body.get("temperature"),
                    max_tokens=body.get("max_tokens"),
                )
                return self._send(200, out)
            except ApiError as e:
                code = 401 if e.status == 401 else (400 if e.status == 0 else 502)
                return self._send(code, {"error": str(e.message), "status": e.status})
        if self.path.startswith("/submit"):
            body = self._read_json()
            try:
                out = GW.submit(
                    model=body["model"], prompt=body.get("prompt", ""),
                    duration=int(body.get("duration", 5)),
                    resolution=body.get("resolution", "720p"),
                    ratio=body.get("ratio", "16:9"),
                    first_frame=body.get("first_frame"),
                    ref_image=body.get("ref_image"),
                    ref_video=body.get("ref_video"),
                    no_audio=bool(body.get("no_audio", False)),
                    no_prompt_extend=bool(body.get("no_prompt_extend", False)),
                    watermark=bool(body.get("watermark", False)),
                    seed=body.get("seed"),
                    wait=bool(body.get("wait", False)),
                    timeout=int(body.get("timeout", 600)),
                )
                return self._send(200, out)
            except KeyError:
                return self._send(400, {"error": "missing required field: model"})
            except ApiError as e:
                code = 401 if e.status == 401 else (400 if e.status == 0 else 502)
                return self._send(code, {"error": str(e.message), "status": e.status})
        if self.path.startswith("/wait"):
            body = self._read_json()
            tid = body.get("task_id")
            if not tid:
                return self._send(400, {"error": "missing task_id"})
            try:
                return self._send(200, GW.wait(tid, timeout=int(body.get("timeout", 600))))
            except ApiError as e:
                return self._send(502, {"error": str(e.message), "status": e.status})
        if self.path.startswith("/sync"):
            key = resolve_key()
            if not key:
                return self._send(400, {"error": "缺少 API Key：设置 POWERTOKENS_API_KEY 或 config.json"})
            try:
                return self._send(200, REGISTRY.sync(key))
            except ApiError as e:
                return self._send(502, {"error": str(e.message), "status": e.status})
        return self._send(404, {"error": "not found"})


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    sys.stderr.write(f"[gateway] listening on http://0.0.0.0:{PORT}  (PT_GATEWAY_PORT 可改)\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
