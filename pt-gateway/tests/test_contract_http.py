#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""contract：HTTP 服务的接口契约（离线：真起服务 + 网关打桩）。

为什么不用"直接调函数"代替：agent 是通过 HTTP 调进来的，路由写错、状态码映射写错、
CORS 漏了，都只会在 HTTP 这层暴露。所以这里真起一个 127.0.0.1 回环服务，
只把网关换成桩——网络不出本机，0 花费，但把路由/状态码/头部全部钉死。
"""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import pt_gateway, ApiError, GW_DIR  # noqa: E402

sys.path.insert(0, str(GW_DIR))
import server as gw_server  # noqa: E402


class FakeGateway:
    """网关替身：记录调用，可按需抛 ApiError（用来验证状态码映射）。"""

    def __init__(self):
        self.calls = []
        self.raise_err = None

    def _maybe_raise(self):
        if self.raise_err:
            e = self.raise_err
            self.raise_err = None
            raise e

    def media_generate(self, **kw):
        self.calls.append(("media_generate", kw))
        self._maybe_raise()
        return {"ok": True, "kind": kw.get("kind"), "execution": "sync",
                "model": kw.get("model"), "urls": ["http://cdn/1.png"]}

    def submit(self, **kw):
        self.calls.append(("submit", kw))
        self._maybe_raise()
        return {"task_id": "t1", "kind": "video"}

    def get_status(self, task_id):
        self.calls.append(("get_status", {"task_id": task_id}))
        self._maybe_raise()
        return {"task_id": task_id, "state": "QUEUED", "progress": 0, "url": None, "raw": {}}

    def wait(self, task_id, timeout=600):
        self.calls.append(("wait", {"task_id": task_id, "timeout": timeout}))
        self._maybe_raise()
        return {"task_id": task_id, "state": "SUCCEEDED", "progress": 100,
                "url": "http://cdn/1.mp4", "raw": {}}

    def list_capabilities(self, kind=None):
        self.calls.append(("list_capabilities", {"kind": kind}))
        self._maybe_raise()
        return {"kinds": {kind or "all": {}}, "models": {}}


class FakeRegistry:
    def __init__(self):
        self.data = {"last_synced": "2026-10-30T00:00:00+00:00", "models": {}}
        self.synced = 0

    def enabled_async(self, kind=None):
        return ["wan3.0-video", "seedance-1-5-pro-251215"] if kind in (None, "video") else []

    def auto_sync_if_stale(self, key):
        return None

    def sync(self, key):
        self.synced += 1
        return {"added": [], "removed": [], "total_enabled": 2,
                "total_models": 2, "last_synced": "2026-10-30T00:00:00+00:00"}


class HttpContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake_gw = FakeGateway()
        cls.fake_reg = FakeRegistry()
        cls._saved = (gw_server.GW, gw_server.REGISTRY)
        gw_server.GW = cls.fake_gw
        gw_server.REGISTRY = cls.fake_reg
        try:
            cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), gw_server.Handler)
        except OSError as e:  # 沙箱/环境不允许绑定端口
            gw_server.GW, gw_server.REGISTRY = cls._saved
            raise unittest.SkipTest(f"无法绑定回环端口（跳过 HTTP 契约测试）：{e}")
        cls.port = cls.srv.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "srv"):
            cls.srv.shutdown()
            cls.srv.server_close()
            cls.thread.join(timeout=5)
        gw_server.GW, gw_server.REGISTRY = cls._saved

    def setUp(self):
        self.fake_gw.calls.clear()
        self.fake_gw.raise_err = None
        self.fake_reg.synced = 0
        self._saved_resolve = gw_server.resolve_key
        gw_server.resolve_key = lambda: ""

    def tearDown(self):
        gw_server.resolve_key = self._saved_resolve

    # ---- 请求工具 ----
    def req(self, method="GET", path="/", body=None):
        url = self.base + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        r = urllib.request.Request(url, data=data, method=method,
                                   headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, dict(resp.headers), self._parse(raw)
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), self._parse(e.read().decode("utf-8"))

    @staticmethod
    def _parse(raw):
        """204/空响应没有 body，不能强行 json 解析。"""
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return {"_raw": raw}

    # ---- 用例 ----
    def test_health(self):
        code, _, obj = self.req("GET", "/health")
        self.assertEqual(200, code)
        self.assertEqual({"ok": True, "service": "pt-gateway"}, obj)

    def test_capabilities_passes_kind(self):
        code, _, obj = self.req("GET", "/capabilities?kind=video")
        self.assertEqual(200, code)
        self.assertIn("video", obj["kinds"])
        self.assertEqual(("list_capabilities", {"kind": "video"}), self.fake_gw.calls[-1])

    def test_status_ok(self):
        code, _, obj = self.req("GET", "/status?task_id=t1")
        self.assertEqual(200, code)
        self.assertEqual("QUEUED", obj["state"])
        self.assertEqual("t1", obj["task_id"])

    def test_status_missing_task_id_is_400(self):
        code, _, obj = self.req("GET", "/status")
        self.assertEqual(400, code)
        self.assertIn("missing task_id", json.dumps(obj))

    def test_status_upstream_error_is_502(self):
        self.fake_gw.raise_err = ApiError(500, "upstream down")
        code, _, obj = self.req("GET", "/status?task_id=t1")
        self.assertEqual(502, code)
        self.assertEqual(500, obj["status"])

    def test_generate_ok(self):
        code, _, obj = self.req("POST", "/generate",
                                {"kind": "image", "model": "seedream-5-0-260128", "prompt": "a dog"})
        self.assertEqual(200, code)
        self.assertTrue(obj["ok"])
        kw = self.fake_gw.calls[-1][1]
        self.assertEqual("image", kw["kind"])
        self.assertEqual("a dog", kw["prompt"])

    def test_generate_forwards_all_kind_params(self):
        """契约测试的核心：HTTP 层不能吞参数（漏一个就白传）。"""
        self.req("POST", "/generate", {
            "kind": "chat", "model": "qwen3.8-flash", "prompt": "hi",
            "system": "you are", "temperature": 0.3, "max_tokens": 64,
            "voice": "nova", "duration": 10, "resolution": "1080p", "ratio": "9:16",
            "n": 2, "aspect_ratio": "16:9", "wait": True, "timeout": 42,
            "no_audio": True, "watermark": True, "seed": 7,
        })
        kw = self.fake_gw.calls[-1][1]
        self.assertEqual("you are", kw["system"])
        self.assertEqual(0.3, kw["temperature"])
        self.assertEqual(64, kw["max_tokens"])
        self.assertEqual("nova", kw["voice"])
        self.assertEqual(10, kw["duration"])
        self.assertEqual("1080p", kw["resolution"])
        self.assertEqual("9:16", kw["ratio"])
        self.assertEqual(2, kw["n"])
        self.assertEqual("16:9", kw["aspect_ratio"])
        self.assertTrue(kw["wait"])
        self.assertEqual(42, kw["timeout"])
        self.assertTrue(kw["no_audio"])
        self.assertTrue(kw["watermark"])
        self.assertEqual(7, kw["seed"])

    def test_generate_error_mapping_401(self):
        self.fake_gw.raise_err = ApiError(401, "no key")
        code, _, _ = self.req("POST", "/generate", {"kind": "chat", "prompt": "p"})
        self.assertEqual(401, code)

    def test_generate_error_mapping_400_for_validation(self):
        """ApiError(0) = 网关侧参数/校验错误 -> 400（不是 502）。"""
        self.fake_gw.raise_err = ApiError(0, "未知 kind")
        code, _, _ = self.req("POST", "/generate", {"kind": "nope"})
        self.assertEqual(400, code)

    def test_generate_error_mapping_502_for_upstream(self):
        self.fake_gw.raise_err = ApiError(503, "upstream")
        code, _, _ = self.req("POST", "/generate", {"kind": "chat", "prompt": "p"})
        self.assertEqual(502, code)

    def test_submit_ok(self):
        code, _, obj = self.req("POST", "/submit", {"model": "wan3.0-video", "prompt": "p"})
        self.assertEqual(200, code)
        self.assertEqual("t1", obj["task_id"])

    def test_submit_missing_model_is_400(self):
        code, _, obj = self.req("POST", "/submit", {"prompt": "p"})
        self.assertEqual(400, code)
        self.assertIn("missing required field: model", json.dumps(obj))

    def test_wait_ok(self):
        code, _, obj = self.req("POST", "/wait", {"task_id": "t1", "timeout": 30})
        self.assertEqual(200, code)
        self.assertEqual("SUCCEEDED", obj["state"])
        self.assertEqual(("wait", {"task_id": "t1", "timeout": 30}), self.fake_gw.calls[-1])

    def test_wait_missing_task_id_is_400(self):
        code, _, _ = self.req("POST", "/wait", {})
        self.assertEqual(400, code)

    def test_sync_requires_key(self):
        code, _, obj = self.req("POST", "/sync", {})
        self.assertEqual(400, code)
        self.assertIn("缺少 API Key", obj.get("error", ""))
        self.assertEqual(0, self.fake_reg.synced)

    def test_sync_ok_with_key(self):
        gw_server.resolve_key = lambda: "TEST-KEY"
        code, _, obj = self.req("POST", "/sync", {})
        self.assertEqual(200, code)
        self.assertEqual(1, self.fake_reg.synced)
        self.assertEqual(2, obj["total_enabled"])

    def test_models_accepts_async_kind(self):
        code, _, obj = self.req("GET", "/models?kind=video")
        self.assertEqual(200, code)
        self.assertEqual("video", obj["kind"])
        self.assertEqual(2, obj["count"])
        self.assertIn("wan3.0-video", obj["models"])

    def test_models_rejects_sync_kind(self):
        """GET /models 只服务异步模型（历史设计），传 image 要明确报 400 而不是返回空。"""
        code, _, _ = self.req("GET", "/models?kind=image")
        self.assertEqual(400, code)

    def test_unknown_route_404_with_route_list(self):
        code, _, obj = self.req("GET", "/nope")
        self.assertEqual(404, code)
        self.assertIn("routes", obj)
        self.assertIn("POST /generate", obj["routes"])

    def test_cors_on_get(self):
        _, headers, _ = self.req("GET", "/health")
        self.assertEqual("*", headers.get("Access-Control-Allow-Origin") or
                         headers.get("access-control-allow-origin"))

    def test_options_preflight(self):
        code, headers, _ = self.req("OPTIONS", "/generate")
        self.assertEqual(204, code)
        self.assertEqual("*", headers.get("Access-Control-Allow-Origin") or
                         headers.get("access-control-allow-origin"))

    def test_content_type_is_json_utf8(self):
        _, headers, _ = self.req("GET", "/health")
        self.assertIn("application/json", headers.get("Content-Type", ""))


if __name__ == "__main__":
    unittest.main()
