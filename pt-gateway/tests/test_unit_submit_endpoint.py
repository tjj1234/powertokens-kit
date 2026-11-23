#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unit：T01 —— 提交入口按 catalog 的 model→submit 对照选择（0.8.10）。

钉死的行为（全程离线，0 花费）：
  1. 视频模型从 catalog 读到 /v1/videos → 用它；
  2. 图片模型从 catalog 读到 /v1/images/... → 用它；
  3. catalog 读不到（坏路径/坏 JSON/无 models）→ 回落写死常量，不抛错；
  4. 未登记模型 / submit 字段缺失 / 跨类型脏数据 → 回落写死常量（R4 不猜）；
  5. 北极星副本场景：catalog 缺失时 submit_task 旧签名调用仍走 VIDEO_SUBMIT。
"""
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import pt_video  # noqa: E402


class TestResolveSubmitEndpoint(unittest.TestCase):
    def setUp(self):
        # 清掉模块级缓存，保证用例互不污染
        pt_video._catalog_models_cache = None

    def tearDown(self):
        pt_video._catalog_models_cache = None

    @staticmethod
    def _cat(*triples):
        """构造最小 catalog：((model, kind, submit), ...)。"""
        return {"models": {m: {"kind": k, "submit": s} for m, k, s in triples}}

    def _tmp_catalog(self, obj):
        d = tempfile.TemporaryDirectory()
        p = os.path.join(d.name, "catalog.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        self.addCleanup(d.cleanup)
        return p

    # ---- 1) 正常读 catalog ----
    def test_video_model_uses_catalog_submit(self):
        p = self._tmp_catalog(self._cat(("kling-v3", "video", "/v1/videos")))
        self.assertEqual("/v1/videos",
                         pt_video.resolve_submit_endpoint("kling-v3", "video", path=p))
        # 即使 catalog 写了一个同类型的新入口，也应跟随（不写死代码）
        p2 = self._tmp_catalog(self._cat(("kling-v3", "video", "/v1/videos/kling")))
        self.assertEqual("/v1/videos/kling",
                         pt_video.resolve_submit_endpoint("kling-v3", "video", path=p2))

    def test_image_model_uses_catalog_submit(self):
        p = self._tmp_catalog(self._cat(("kling-image", "image", "/v1/images/generations")))
        self.assertEqual("/v1/images/generations",
                         pt_video.resolve_submit_endpoint("kling-image", "image", path=p))

    # ---- 2) 读不到 catalog → 兜底，不抛 ----
    def test_missing_catalog_falls_back(self):
        self.assertEqual(pt_video.VIDEO_SUBMIT,
                         pt_video.resolve_submit_endpoint("kling-v3", "video", path="/no/such/catalog.json"))
        self.assertEqual(pt_video.IMAGE_ENDPOINT,
                         pt_video.resolve_submit_endpoint("kling-image", "image", path="/no/such/catalog.json"))

    def test_malformed_catalog_falls_back(self):
        d = tempfile.TemporaryDirectory()
        p = os.path.join(d.name, "catalog.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{not-json")
        self.addCleanup(d.cleanup)
        self.assertEqual(pt_video.VIDEO_SUBMIT,
                         pt_video.resolve_submit_endpoint("kling-v3", "video", path=p))

    def test_catalog_without_models_key_falls_back(self):
        p = self._tmp_catalog({"note": ["no models here"]})
        self.assertEqual(pt_video.VIDEO_SUBMIT,
                         pt_video.resolve_submit_endpoint("kling-v3", "video", path=p))

    # ---- 3) 无来源 / 脏数据 → 兜底（R4 不猜）----
    def test_unknown_model_falls_back(self):
        p = self._tmp_catalog(self._cat(("kling-v3", "video", "/v1/videos")))
        self.assertEqual(pt_video.VIDEO_SUBMIT,
                         pt_video.resolve_submit_endpoint("not-registered", "video", path=p))

    def test_missing_submit_field_falls_back(self):
        p = self._tmp_catalog({"models": {"kling-v3": {"kind": "video"}}})
        self.assertEqual(pt_video.VIDEO_SUBMIT,
                         pt_video.resolve_submit_endpoint("kling-v3", "video", path=p))

    def test_cross_type_submit_falls_back(self):
        # 视频模型却配了 image 入口 → 视为脏数据，回落 VIDEO_SUBMIT
        p = self._tmp_catalog(self._cat(("kling-v3", "video", "/v1/images/generations")))
        self.assertEqual(pt_video.VIDEO_SUBMIT,
                         pt_video.resolve_submit_endpoint("kling-v3", "video", path=p))
        # 反向同理
        p2 = self._tmp_catalog(self._cat(("kling-image", "image", "/v1/videos")))
        self.assertEqual(pt_video.IMAGE_ENDPOINT,
                         pt_video.resolve_submit_endpoint("kling-image", "image", path=p2))

    def test_malformed_submit_value_falls_back(self):
        p = self._tmp_catalog(self._cat(("kling-v3", "video", "videos-without-slash")))
        self.assertEqual(pt_video.VIDEO_SUBMIT,
                         pt_video.resolve_submit_endpoint("kling-v3", "video", path=p))

    # ---- 4) 兼容性：submit_task 旧签名仍走 VIDEO_SUBMIT ----
    def test_submit_task_old_signature_still_works(self):
        calls = []

        def fake_api(method, path, key, body=None, timeout=30):
            calls.append((method, path))
            return {"id": "task-t01"}

        with mock.patch.object(pt_video, "_api_call", side_effect=fake_api):
            tid, _ = pt_video.submit_task("sk-test", {"model": "kling-v3"})
        self.assertEqual("task-t01", tid)
        self.assertEqual(("POST", pt_video.VIDEO_SUBMIT), calls[0],
                         "不传 endpoint 时必须回落 VIDEO_SUBMIT（北极星副本旧调用方式）")

    def test_submit_task_uses_resolved_endpoint(self):
        calls = []

        def fake_api(method, path, key, body=None, timeout=30):
            calls.append((method, path))
            return {"id": "task-t01"}

        with mock.patch.object(pt_video, "_api_call", side_effect=fake_api):
            pt_video.submit_task("sk-test", {"model": "kling-v3"},
                                 endpoint="/v1/videos/kling")
        self.assertEqual(("POST", "/v1/videos/kling"), calls[0])

    # ---- 5) 真实 catalog：北极星在用的模型入口确实是 /v1/videos ----
    def test_real_catalog_in_use_models(self):
        here = os.path.dirname(os.path.abspath(__file__))
        real = os.path.join(here, "..", "catalog.json")
        for model in ("wan2.7-t2v", "wan2.7-i2v", "kling-v3"):
            self.assertEqual("/v1/videos",
                             pt_video.resolve_submit_endpoint(model, "video", path=real))


if __name__ == "__main__":
    unittest.main()
