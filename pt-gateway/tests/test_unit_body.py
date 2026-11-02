#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unit：请求体构造（video / seedance 两条路径的差异点）。

这里是被测模块存在的唯一理由——"接多个模型要各写一套"，差异全在请求体：
  - video：顶层 prompt + 可选 media[]
  - seedance：prompt 必须塞进 media[{type:text}]，且素材强制 asset://
写错的表现是服务端 400，但排查时看不出是网关构造错还是参数错，所以逐字段钉死。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import pt_gateway, ApiError  # noqa: E402

VIDEO_MODEL = "wan3.0-video"
SEED_MODEL = "dreamina-seedance-2-0-mini-260615"


class TestBuildMedia(unittest.TestCase):
    def test_empty(self):
        self.assertEqual([], pt_gateway._build_media())

    def test_order_and_types(self):
        m = pt_gateway._build_media(first_frame="a", ref_image="b", ref_video="c")
        self.assertEqual([
            {"type": "first_frame", "url": "a"},
            {"type": "image", "url": "b"},
            {"type": "video", "url": "c"},
        ], m)

    def test_partial(self):
        self.assertEqual([{"type": "video", "url": "v"}],
                         pt_gateway._build_media(ref_video="v"))


class TestVideoBody(unittest.TestCase):
    def _body(self, **params):
        p = dict(duration=5, resolution="720p", ratio="16:9")
        p.update(params)
        return pt_gateway.build_submit_body(VIDEO_MODEL, "video", "a dog", [], p)

    def test_core_fields(self):
        b = self._body()
        self.assertEqual(VIDEO_MODEL, b["model"])
        self.assertEqual("a dog", b["prompt"])
        self.assertEqual("720P", b["size"])        # 服务端要求大写
        self.assertEqual("16:9", b["ratio"])
        self.assertEqual("5", b["seconds"])        # 服务端 enum<string>，必须字符串
        self.assertIsInstance(b["seconds"], str)

    def test_defaults_when_params_empty(self):
        b = pt_gateway.build_submit_body(VIDEO_MODEL, "video", "p", [], {})
        self.assertEqual("720P", b["size"])
        self.assertEqual("16:9", b["ratio"])
        self.assertEqual("5", b["seconds"])
        self.assertFalse(b["generate_audio"] is False)   # 默认带音频
        self.assertFalse(b["prompt_extend"] is False)    # 默认扩展提示词
        self.assertFalse(b["watermark"])                 # 默认无水印

    def test_no_media_key_when_no_media(self):
        """没有素材时不能发空 media 数组——部分厂商会拒绝空数组。"""
        self.assertNotIn("media", self._body())

    def test_media_attached_when_provided(self):
        media = pt_gateway._build_media(ref_image="http://x/1.png")
        b = pt_gateway.build_submit_body(VIDEO_MODEL, "video", "p", media, {})
        self.assertEqual([{"type": "image", "url": "http://x/1.png"}], b["media"])

    def test_video_accepts_http_media(self):
        """video 素材支持 http(s)://（与 seedance 不同）。"""
        media = pt_gateway._build_media(first_frame="https://x/f.png")
        b = pt_gateway.build_submit_body(VIDEO_MODEL, "video", "p", media, {})
        self.assertEqual("https://x/f.png", b["media"][0]["url"])

    def test_opt_out_flags(self):
        b = self._body(no_audio=True, no_prompt_extend=True, watermark=True)
        self.assertFalse(b["generate_audio"])
        self.assertFalse(b["prompt_extend"])
        self.assertTrue(b["watermark"])

    def test_seed_omitted_when_none(self):
        self.assertNotIn("seed", self._body(seed=None))

    def test_seed_zero_is_kept(self):
        """seed=0 是合法值，不能用 `if seed` 判空把它吃掉。"""
        self.assertEqual(0, self._body(seed=0)["seed"])

    def test_seed_value(self):
        self.assertEqual(42, self._body(seed=42)["seed"])

    def test_resolution_and_ratio_passthrough(self):
        b = self._body(resolution="1080p", ratio="9:16", duration=10)
        self.assertEqual("1080P", b["size"])
        self.assertEqual("9:16", b["ratio"])
        self.assertEqual("10", b["seconds"])


class TestSeedanceBody(unittest.TestCase):
    def test_prompt_inside_media_text(self):
        b = pt_gateway.build_submit_body(SEED_MODEL, "seedance", "a dog", [], {})
        self.assertNotIn("prompt", b, "seedance 的 prompt 不能在顶层")
        self.assertEqual({"type": "text", "text": "a dog"}, b["media"][0])

    def test_asset_media_appended_after_text(self):
        media = pt_gateway._build_media(first_frame="asset://abc")
        b = pt_gateway.build_submit_body(SEED_MODEL, "seedance", "p", media, {})
        self.assertEqual("text", b["media"][0]["type"])
        self.assertEqual("first_frame", b["media"][1]["type"])
        self.assertEqual("asset://abc", b["media"][1]["url"])

    def test_http_media_rejected(self):
        """回归：seedance 图生视频素材必须是资产库 asset://，http 会被引擎拒。"""
        media = pt_gateway._build_media(ref_image="http://x/1.png")
        with self.assertRaises(ApiError) as ctx:
            pt_gateway.build_submit_body(SEED_MODEL, "seedance", "p", media, {})
        self.assertIn("asset://", str(ctx.exception.message))

    def test_https_media_also_rejected(self):
        media = pt_gateway._build_media(ref_image="https://x/1.png")
        with self.assertRaises(ApiError):
            pt_gateway.build_submit_body(SEED_MODEL, "seedance", "p", media, {})

    def test_error_message_shows_offending_url(self):
        media = pt_gateway._build_media(ref_image="http://bad/1.png")
        with self.assertRaises(ApiError) as ctx:
            pt_gateway.build_submit_body(SEED_MODEL, "seedance", "p", media, {})
        self.assertIn("http://bad/1.png", str(ctx.exception.message))

    def test_core_fields_same_as_video(self):
        b = pt_gateway.build_submit_body(SEED_MODEL, "seedance", "p", [],
                                         dict(duration=5, resolution="480p", ratio="16:9"))
        self.assertEqual("480P", b["size"])
        self.assertEqual("5", b["seconds"])


if __name__ == "__main__":
    unittest.main()
