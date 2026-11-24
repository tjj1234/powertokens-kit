#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unit：复核 2026-11-24 其余项——T09 图片参数 / T03 实时估价 / T01 网关映射 / 账户正文校验。

全程 mock 传输层，使用虚拟 key/token，不联网、不花钱。
"""
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import pt_video  # noqa: E402
import pt_gateway  # noqa: E402

_KEY = "sk-test-seq7-review"


class TestImageRequestBody(unittest.TestCase):
    """复核 T09：CLI 与网关共用统一构造，参考图/尺寸按官方字段名送出。"""

    def test_seedream_ref_image_and_size_sent(self):
        body, unsupported = pt_video.build_image_request_body(
            "seedream-5-0-260128", "review",
            ref_image=["https://ref.invalid/a.jpg"], size="3K", aspect_ratio="16:9")
        self.assertEqual("https://ref.invalid/a.jpg", body["image"][0])
        self.assertEqual("3K", body["size"])
        self.assertEqual([], unsupported)

    def test_unsupported_param_not_silently_dropped(self):
        """无官方字段映射的图片模型：参数进 unsupported，不静默丢弃、不猜字段名。"""
        body, unsupported = pt_video.build_image_request_body(
            "some-unknown-model", "review", size="3K")
        self.assertNotIn("size", body)
        self.assertTrue(any(u["field"] == "size" for u in unsupported))

    def test_default_body_unchanged(self):
        """不传新参数时 body 与旧版一致（R1）。"""
        body, _ = pt_video.build_image_request_body("image-01", "review", n=1,
                                                    aspect_ratio="1:1", seed=None)
        self.assertEqual({"model": "image-01", "prompt": "review",
                          "aspect_ratio": "1:1", "n": 1}, body)

    def test_gateway_uses_same_builder(self):
        captured = {}

        def fake_api(method, path, key, body=None, timeout=None):
            captured["body"] = body
            return {"data": [{"url": "https://result.invalid/i.jpg"}]}

        with mock.patch.object(pt_gateway, "_api_call", side_effect=fake_api), \
             redirect_stderr(io.StringIO()):
            pt_gateway.TaskGateway(key=_KEY).media_generate(
                kind="image", model="seedream-5-0-260128", prompt="review",
                ref_image="https://ref.invalid/a.jpg", params={"size": "3K"})
        self.assertEqual("https://ref.invalid/a.jpg", captured["body"]["image"])
        self.assertEqual("3K", captured["body"]["size"])


class TestLiveEstimate(unittest.TestCase):
    """复核 T03：config 凭证可用 + estimated_quota 解析。"""

    def test_config_token_and_quota_parsed(self):
        captured = {}

        class FakeAccount:
            def __init__(self, token=None, key_id=None, **kw):
                captured["token"] = token

            def estimate_cost(self, **kw):
                captured["call"] = kw
                return {"ok": True, "data": {"estimated_quota": 123, "quota_per_unit": 1000}}

        import types
        mod = types.ModuleType("account_client")
        mod.AccountClient = FakeAccount
        with mock.patch.object(pt_video, "resolve_account_credentials",
                               return_value=("DUMMY_TOKEN", "123")), \
             mock.patch.dict(sys.modules, {"account_client": mod}):
            value = pt_video._live_estimate("wan3.0-video", "video", 5, "720p")
        self.assertEqual(0.123, value)
        self.assertEqual("DUMMY_TOKEN", captured["token"])
        self.assertEqual("/v1/videos", captured["call"]["endpoint"])


class TestGatewaySubmitRoute(unittest.TestCase):
    """复核 T01：网关按 catalog submit 映射提交；strict 拒绝未知模型。"""

    def test_gateway_uses_catalog_endpoint(self):
        paths = []

        def fake_api(method, path, key, body=None, timeout=None):
            paths.append(path)
            return {"id": "task-1", "_request_id": "req-1"}

        with mock.patch.object(pt_video, "load_catalog_models",
                               return_value={"wan3.0-video": {"submit": "/custom/v1/videos",
                                                              "kind": "video"}}), \
             mock.patch.object(pt_video, "_api_call", side_effect=fake_api), \
             redirect_stderr(io.StringIO()):
            pt_gateway.TaskGateway(key=_KEY).submit("wan3.0-video", prompt="x")
        self.assertEqual(["/custom/v1/videos"], paths)

    def test_strict_rejects_unknown_model(self):
        with mock.patch.object(pt_video, "load_catalog_models", return_value={}), \
             redirect_stderr(io.StringIO()):
            with self.assertRaises(pt_gateway.ApiError):
                pt_gateway.TaskGateway(key=_KEY).submit("mystery", prompt="x", strict=True)


class TestAccountBusinessStatus(unittest.TestCase):
    """复核补充：--strict 检查正文业务码；默认关闭（红线2 兼容）。"""

    def test_business_error_detected(self):
        bad, msg = pt_video._account_business_error(
            {"code": 500, "data": None, "msg": "invalid token"})
        self.assertTrue(bad)
        self.assertEqual("invalid token", msg)

    def test_success_code_ok(self):
        for code in (200, "200", 0, "0", None):
            bad, _ = pt_video._account_business_error({"code": code})
            self.assertFalse(bad)

    def test_default_balance_still_ok(self):
        """默认（不传 --strict）时 HTTP 200 + code=500 仍 ok:true（兼容旧行为）。"""
        out = io.StringIO()
        with mock.patch.object(pt_video, "_account_get",
                               return_value={"code": 500, "data": None, "msg": "invalid token"}), \
             redirect_stdout(out):
            code = pt_video.cmd_balance([])
        self.assertEqual(0, code)
        self.assertTrue(json.loads(out.getvalue())["ok"])

    def test_strict_balance_fails(self):
        out = io.StringIO()
        with mock.patch.object(pt_video, "_account_get",
                               return_value={"code": 500, "data": None, "msg": "invalid token"}), \
             redirect_stdout(out):
            code = pt_video.cmd_balance(["--strict"])
        self.assertEqual(1, code)


if __name__ == "__main__":
    unittest.main()
