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


class TestReview092(unittest.TestCase):
    """复核 0.9.2：codex 第二轮报告的 4 条失败场景 + 3 项新行为回归。全程 mock、0 费用。"""

    def test_seedance_gateway_uses_video_endpoint(self):
        """#1：seedance 必须走 /v1/videos，不能再退化成图片入口。"""
        paths = []
        def fake_api(method, path, key, body=None, timeout=None):
            paths.append(path)
            return {"id": "task-1"}
        with mock.patch.object(pt_video, "_api_call", side_effect=fake_api), \
             redirect_stderr(io.StringIO()):
            pt_gateway.TaskGateway(key=_KEY).submit("seedance-1-5-pro-251215", prompt="x")
        self.assertEqual(["/v1/videos"], paths)

    def test_resolve_endpoint_seedance_and_unknown(self):
        self.assertEqual("/v1/videos",
                         pt_video.resolve_submit_endpoint("seedance-1-5-pro-251215", "seedance"))
        # 未知 kind 在 strict 下报错，非 strict 回落图片（兼容）
        with self.assertRaises(pt_video.ApiError):
            pt_video.resolve_submit_endpoint("m", "bogus", strict=True)
        self.assertEqual(pt_video.IMAGE_ENDPOINT,
                         pt_video.resolve_submit_endpoint("m", "bogus"))

    def test_partial_image_download_reports_partial(self):
        """#2：两张图只下载一张 -> exit 3 + ok:false + status:partial + 失败链接列出。"""
        def multi_api(*a, **kw):
            return {"data": [{"url": "https://result.invalid/1.jpg"},
                             {"url": "https://result.invalid/2.jpg"}]}
        def some_download(url, path, key):
            if "/2.jpg" in url:
                raise TimeoutError("second download failed")
            with open(path, "wb") as f:
                f.write(b"\xff\xd8\xffmock-jpeg")
            return True
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pt_video, "_api_call", side_effect=multi_api), \
             mock.patch.object(pt_video, "download_binary", side_effect=some_download), \
             redirect_stdout(out), redirect_stderr(err):
            code = pt_video._run_one(_KEY, "image-01", "image", self._img_args())
        result = json.loads(out.getvalue())
        self.assertEqual(3, code)
        self.assertFalse(result["ok"])
        self.assertEqual("partial", result["status"])
        self.assertFalse(result["download_ok"])
        self.assertIn("https://result.invalid/2.jpg", result["download_failed_urls"])

    def test_all_image_downloads_ok_stays_ok(self):
        def multi_api(*a, **kw):
            return {"data": [{"url": "https://result.invalid/1.jpg"},
                             {"url": "https://result.invalid/2.jpg"}]}
        def ok_download(url, path, key):
            with open(path, "wb") as f:
                f.write(b"\xff\xd8\xffmock-jpeg")
            return True
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pt_video, "_api_call", side_effect=multi_api), \
             mock.patch.object(pt_video, "download_binary", side_effect=ok_download), \
             redirect_stdout(out), redirect_stderr(err):
            code = pt_video._run_one(_KEY, "image-01", "image", self._img_args())
        result = json.loads(out.getvalue())
        self.assertEqual(0, code)
        self.assertTrue(result["ok"])
        self.assertEqual([], result["download_failed_urls"])

    def test_billable_request_matches_generation_fields(self):
        """#3：估价 request 与真实生成 body 使用同源字段（seconds/size/ratio）。"""
        req = pt_video.build_billable_request("wan3.0-video", "video", 5, "720p", ratio="16:9")
        self.assertEqual("5", req["seconds"])
        self.assertEqual("720P", req["size"])
        self.assertEqual("16:9", req["ratio"])
        self.assertEqual(5, req["duration"])  # 兼容字段仍在

    def test_account_string_code_429_rejected(self):
        """#5：字符串业务码 '429' 在 strict 下必须判失败。"""
        bad, _ = pt_video._account_business_error(
            {"code": "429", "data": None, "msg": "rate limit"})
        self.assertTrue(bad)
        bad, _ = pt_video._account_business_error({"success": False, "message": "denied"})
        self.assertTrue(bad)

    def test_strict_params_blocks_submission(self):
        """#6：--strict-params 下未映射参数不提交、不花钱。"""
        def fake_api(method, path, key, body=None, timeout=None):
            self.fail("不应发起提交请求")
        args = self._img_args()
        args.strict_params = True
        with mock.patch.object(pt_video, "_api_call", side_effect=fake_api), \
             redirect_stderr(io.StringIO()):
            with self.assertRaises(pt_video.ApiError):
                pt_video.generate_image(_KEY, "some-unknown-model", args, 30)

    @staticmethod
    def _img_args():
        return SimpleNamespace(
            prompt="offline", duration=5, resolution="720p", ratio="16:9",
            aspect_ratio="1:1", n=2, response_format="url", seed=None,
            first_frame=None, last_frame=None, ref_image=[], ref_video=[], ref_audio=[],
            size="3K", no_audio=True, no_prompt_extend=False, watermark=False,
            output=None, no_download=False, timeout=1, json=True,
        )


class TestReview093(unittest.TestCase):
    """扩大复核报告 2026-11-25：估价对齐 / Base64 / seed 透传。全程离线 mock。"""

    def test_estimate_image_reuses_generation_builder(self):
        """#2：图片估价请求与生成构造器逐字段同源，无 duration/resolution 视频字段。"""
        req = pt_video.build_billable_request("seedream-5-0-260128", "image", 5, "720p",
                                              n=2, size="3K", aspect_ratio="1:1")
        gen_body, _ = pt_video.build_image_request_body(
            "seedream-5-0-260128", "x", n=2, size="3K", aspect_ratio="1:1")
        gen_billable = {k: v for k, v in gen_body.items()
                        if k not in ("prompt", "response_format")}
        self.assertEqual(req, gen_billable)
        self.assertNotIn("duration", req)
        self.assertNotIn("resolution", req)
        self.assertNotIn("seconds", req)

    def test_estimate_video_default_ratio_adaptive(self):
        """#2：视频默认比例与 generate 一致（adaptive），字段映射同源。"""
        req = pt_video.build_billable_request("wan3.0-video", "video", 5, "720p")
        gen_fields, _ = pt_video.resolve_media_fields("wan3.0-video", {
            "duration_s": "5", "resolution": "720P", "aspect_ratio": "adaptive"})
        for k, v in gen_fields.items():
            self.assertEqual(req.get(k), v)

    def test_estimate_accepts_size_flag(self):
        """#2：estimate 接受 --size 3K（此前 argparse 直接 exit 2）。"""
        buf = io.StringIO()
        code = None
        with redirect_stdout(buf), redirect_stderr(buf):
            try:
                pt_video.cmd_estimate(["-m", "seedream-5-0-260128", "--size", "3K"])
            except SystemExit as e:
                code = e.code
        self.assertNotEqual(2, code)

    def test_base64_image_decoded_and_saved(self):
        """#3：b64_json 解码落盘，urls 含 data URI，generation_ok 与 download_ok 一致。"""
        import base64 as _b64
        import tempfile
        png = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
               "/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
        resp = {"data": [{"b64_json": png}], "_request_id": "b64-test"}
        with tempfile.TemporaryDirectory() as td:
            args = SimpleNamespace(
                prompt="offline", duration=5, resolution="720p", ratio="16:9",
                aspect_ratio="1:1", n=1, response_format="base64", seed=None,
                first_frame=None, last_frame=None, ref_image=[], ref_video=[], ref_audio=[],
                size=None, no_audio=True, no_prompt_extend=False, watermark=False,
                output=str(__import__("pathlib").Path(td) / "r.png"),
                no_download=False, timeout=1, json=True,
            )
            with mock.patch.object(pt_video, "_api_call", return_value=resp), \
                 redirect_stderr(io.StringIO()):
                urls, outputs, failed = pt_video.generate_image(_KEY, "image-01", args, 30)
            self.assertEqual(1, len(urls))
            self.assertTrue(urls[0].startswith("data:image/"))
            self.assertEqual(1, len(outputs))
            self.assertTrue(os.path.isfile(outputs[0]))
            self.assertEqual([], failed)

    def test_gateway_image_seed_reaches_body(self):
        """#4：网关图片分支把 seed 传给统一构造器，真实出现在 body。"""
        captured = {}

        def fake_api(method, path, key, body=None, timeout=None):
            captured["body"] = body
            return {"data": [{"url": "https://result.invalid/i.jpg"}]}

        gw = pt_gateway.TaskGateway(_KEY)
        with mock.patch.object(pt_gateway, "_api_call", side_effect=fake_api), \
             redirect_stderr(io.StringIO()):
            gw.media_generate(kind="image", model="image-01", prompt="offline", seed=123)
        self.assertEqual(123, captured["body"].get("seed"))


if __name__ == "__main__":
    unittest.main()
