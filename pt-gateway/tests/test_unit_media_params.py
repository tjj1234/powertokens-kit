#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unit：T09 —— 友好参数按 catalog param_names 映射为 PT 真实字段名（0.8.12）。

钉死的行为（全程离线，0 花费）：
  1. 恒等映射模型（wan3.0-video: duration_s->seconds/resolution->size/aspect_ratio->ratio）
     body 与历史写死的字段名逐字节一致（R1 默认模型不变）；
  2. 非恒等映射模型（kling-v3: duration_s->duration/aspect_ratio->aspect_ratio）
     字段被正确重命名，避免服务端忽略；无映射的 resolution 回落 size 且不改值；
  3. 无 param_names 模型（seedance-1-5-pro）沿用历史字段名，不猜、不丢参数（R4）；
  4. unsupported 只提示不改值：声明了 param_names 但缺键 = no_param_mapping，
     整模型无来源 = capability_unknown；
  5. 非生成参数（model/prompt/media 等）原样透传，不参与映射；
  6. 图片分支 aspect_ratio 恒等映射，body 不变。
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import pt_video  # noqa: E402


class TestResolveMediaFields(unittest.TestCase):
    def setUp(self):
        pt_video._catalog_models_cache = None

    def tearDown(self):
        pt_video._catalog_models_cache = None

    @staticmethod
    def _tmp_catalog(models):
        d = tempfile.TemporaryDirectory()
        p = os.path.join(d.name, "catalog.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"models": models}, f)
        return d, p

    def test_identity_mapping_keeps_body_unchanged(self):
        """wan3.0-video 恒等映射：输出键仍 seconds/size/ratio，值不变。"""
        models = {"wan3.0-video": {"capabilities": {"media": {
            "param_names": {"duration_s": "seconds", "resolution": "size", "aspect_ratio": "ratio"}}}}}
        d, p = self._tmp_catalog(models)
        try:
            mapped, unsupported = pt_video.resolve_media_fields("wan3.0-video", {
                "duration_s": "5", "resolution": "720P", "aspect_ratio": "adaptive"}, path=p)
            self.assertEqual({"seconds": "5", "size": "720P", "ratio": "adaptive"}, mapped)
            self.assertEqual([], unsupported)
        finally:
            d.cleanup()

    def test_nonidentity_mapping_renames_fields(self):
        """kling-v3: duration_s->duration, aspect_ratio->aspect_ratio；resolution 缺映射回落 size。"""
        models = {"kling-v3": {"capabilities": {"media": {
            "param_names": {"duration_s": "duration", "aspect_ratio": "aspect_ratio"}}}}}
        d, p = self._tmp_catalog(models)
        try:
            mapped, unsupported = pt_video.resolve_media_fields("kling-v3", {
                "duration_s": "5", "resolution": "720P", "aspect_ratio": "16:9"}, path=p)
            self.assertEqual("5", mapped.get("duration"))
            self.assertEqual("16:9", mapped.get("aspect_ratio"))
            self.assertEqual("720P", mapped.get("size"))          # 缺映射回落历史键
            self.assertNotIn("seconds", mapped)
            self.assertNotIn("ratio", mapped)
            self.assertEqual(["resolution"], [u["field"] for u in unsupported])
            self.assertEqual("no_param_mapping", unsupported[0]["reason"])
        finally:
            d.cleanup()

    def test_no_param_names_keeps_legacy_fields(self):
        """无 param_names：沿用历史字段名，不猜、不丢参数，标 capability_unknown。"""
        models = {"some-new-model": {"capabilities": {"media": {}}}}
        d, p = self._tmp_catalog(models)
        try:
            mapped, unsupported = pt_video.resolve_media_fields("some-new-model", {
                "duration_s": "4", "resolution": "1080P", "aspect_ratio": "1:1"}, path=p)
            self.assertEqual({"seconds": "4", "size": "1080P", "ratio": "1:1"}, mapped)
            self.assertEqual(3, len(unsupported))
            self.assertTrue(all(u["reason"] == "capability_unknown" for u in unsupported))
            # 值一个没丢
            self.assertEqual(["4", "1080P", "1:1"], [u["value"] for u in unsupported])
        finally:
            d.cleanup()

    def test_unknown_model_keeps_legacy_fields(self):
        """模型不在 catalog：同样沿用历史字段名（北极星副本 / 未登记模型场景）。"""
        d, p = self._tmp_catalog({})
        try:
            mapped, unsupported = pt_video.resolve_media_fields("not-registered", {
                "duration_s": "5"}, path=p)
            self.assertEqual({"seconds": "5"}, mapped)
            self.assertEqual("capability_unknown", unsupported[0]["reason"])
        finally:
            d.cleanup()

    def test_non_generation_keys_pass_through(self):
        """非生成参数原样透传，不参与映射、不进 unsupported。"""
        models = {"wan3.0-video": {"capabilities": {"media": {
            "param_names": {"resolution": "size"}}}}}
        d, p = self._tmp_catalog(models)
        try:
            mapped, unsupported = pt_video.resolve_media_fields("wan3.0-video", {
                "model": "wan3.0-video", "prompt": "hi", "seed": 42}, path=p)
            self.assertEqual({"model": "wan3.0-video", "prompt": "hi", "seed": 42}, mapped)
            self.assertEqual([], unsupported)
        finally:
            d.cleanup()

    def test_size_alias_maps_to_resolution(self):
        """友好键 size（viduq2 风格）优先用 catalog 映射到 resolution。"""
        models = {"viduq2": {"capabilities": {"media": {
            "param_names": {"size": "resolution", "aspect_ratio": "aspect_ratio"}}}}}
        d, p = self._tmp_catalog(models)
        try:
            mapped, unsupported = pt_video.resolve_media_fields("viduq2", {
                "size": "1080p", "aspect_ratio": "1:1"}, path=p)
            self.assertEqual({"resolution": "1080p", "aspect_ratio": "1:1"}, mapped)
            self.assertEqual([], unsupported)
        finally:
            d.cleanup()

    def test_real_catalog_wan3_identity(self):
        """真实 catalog：wan3.0-video 映射恒等（默认模型 body 不变）。"""
        here = os.path.dirname(os.path.abspath(__file__))
        real = os.path.join(here, "..", "catalog.json")
        mapped, unsupported = pt_video.resolve_media_fields("wan3.0-video", {
            "duration_s": "5", "resolution": "720P", "aspect_ratio": "adaptive"}, path=real)
        self.assertEqual({"seconds": "5", "size": "720P", "ratio": "adaptive"}, mapped)
        self.assertEqual([], unsupported)

    def test_real_catalog_kling_nonidentity(self):
        """真实 catalog：kling-v3 映射 duration/aspect_ratio，不产生 seconds/ratio。"""
        here = os.path.dirname(os.path.abspath(__file__))
        real = os.path.join(here, "..", "catalog.json")
        mapped, _ = pt_video.resolve_media_fields("kling-v3", {
            "duration_s": "5", "resolution": "720P", "aspect_ratio": "16:9"}, path=real)
        self.assertEqual("5", mapped.get("duration"))
        self.assertEqual("16:9", mapped.get("aspect_ratio"))
        self.assertNotIn("seconds", mapped)
        self.assertNotIn("ratio", mapped)


if __name__ == "__main__":
    unittest.main()
