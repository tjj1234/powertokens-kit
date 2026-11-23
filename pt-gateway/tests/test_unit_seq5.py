#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unit：顺序5 = T04 账户凭证 + T03 实时报价/目录价 + T07 产物后缀。

全程离线、零花费、不碰真凭证。钉死：
  T04: resolve_account_credentials 优先级、_account_get 数字 key_id 合并、旧 -k 兼容；
  T03: catalog 价格优先（wan3.0-video 0.5 verified）、prefer_catalog=False 回落写死表、
       cmd_estimate 输出 price_source、无来源不猜；
  T07: detect_file_extension 魔数优先/Content-Type 兜底/默认；显式 -o 不改后缀。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import pt_video  # noqa: E402

_TEST_TOKEN = "pt_live_" + "a" * 24   # 占位，非真凭证（真实形态仅用于优先级判定）


# ---------------- T04 ----------------
class TestT04AccountCredentials(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("PT_ACCESS_TOKEN", "PT_KEY_ID", "POWERTOKENS_API_KEY")}
        for k in self._saved:
            os.environ.pop(k, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / "config.json"

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_explicit_pt_live_token_wins(self):
        os.environ["PT_ACCESS_TOKEN"] = "pt_live_" + "b" * 24
        os.environ["PT_KEY_ID"] = "999"
        token, kid = pt_video.resolve_account_credentials(explicit_token=_TEST_TOKEN)
        self.assertEqual(_TEST_TOKEN, token)
        self.assertEqual("999", kid)   # key_id 仍从 env/config 尽力补

    def test_env_pt_live_credentials(self):
        os.environ["PT_ACCESS_TOKEN"] = _TEST_TOKEN
        os.environ["PT_KEY_ID"] = "12345"
        token, kid = pt_video.resolve_account_credentials()
        self.assertEqual(_TEST_TOKEN, token)
        self.assertEqual("12345", kid)

    def test_legacy_explicit_key_compat(self):
        # 旧的 -k sk-pt-xxx 仍当裸 key 返回，不臆造 key_id
        token, kid = pt_video.resolve_account_credentials(explicit_token="sk-pt-legacy")
        self.assertEqual("sk-pt-legacy", token)
        self.assertIsNone(kid)

    def test_legacy_env_fallback(self):
        os.environ["POWERTOKENS_API_KEY"] = "sk-pt-env"
        token, kid = pt_video.resolve_account_credentials()
        self.assertEqual("sk-pt-env", token)
        self.assertIsNone(kid)

    def test_account_get_merges_numeric_key_id(self):
        called = {}
        def fake_call(method, action, token, params=None, timeout=30):
            called.update(method=method, action=action, token=token, params=params)
            return {"code": 200, "data": {}}
        os.environ["PT_ACCESS_TOKEN"] = _TEST_TOKEN
        os.environ["PT_KEY_ID"] = "12345"
        with mock.patch.object(pt_video, "_account_call", fake_call):
            pt_video._account_get(None, pt_video.ACCOUNT_BALANCE)
        self.assertEqual(_TEST_TOKEN, called["token"])
        self.assertEqual(12345, called["params"]["key_id"])   # 数字，非字符串

    def test_account_get_skips_non_numeric_key_id(self):
        called = {}
        def fake_call(method, action, token, params=None, timeout=30):
            called.update(params=params)
            return {}
        os.environ["PT_ACCESS_TOKEN"] = _TEST_TOKEN
        os.environ["PT_KEY_ID"] = "****45"   # 脱敏串，非数字 -> 不猜不传（R4）
        with mock.patch.object(pt_video, "_account_call", fake_call):
            pt_video._account_get(None, pt_video.ACCOUNT_USAGE)
        self.assertIsNone(called["params"])   # key_id 未合并

    def test_account_get_missing_credential_raises_401(self):
        for k in ("PT_ACCESS_TOKEN", "PT_KEY_ID", "POWERTOKENS_API_KEY"):
            os.environ.pop(k, None)
        with mock.patch.object(pt_video, "load_config", return_value={}):
            with self.assertRaises(pt_video.ApiError) as ctx:
                pt_video._account_get(None, pt_video.ACCOUNT_BALANCE)
        self.assertEqual(401, ctx.exception.status)
        self.assertIn("pt_live_", str(ctx.exception))


# ---------------- T03 ----------------
class TestT03Pricing(unittest.TestCase):
    def test_wan30_catalog_price_is_verified_0_5(self):
        models = pt_video.load_catalog_models()
        price, src = pt_video.catalog_price_usd("wan3.0-video", catalog_models=models)
        self.assertEqual(0.5, price)
        self.assertEqual("verified", src)

    def test_buildin_table_updated_to_0_5(self):
        # 写死旧价 0.20 已更正；prefer_catalog=False 也拿到新价
        self.assertEqual(0.5, pt_video.PRICE_VIDEO_5S_720P["wan3.0-video"])
        c = pt_video.estimate_cost("wan3.0-video", 5, "720p", prefer_catalog=False)
        self.assertEqual(0.5, c)

    def test_estimate_prefers_catalog(self):
        models = {
            "demo-model": {"capabilities": {"media": {
                "price": {"usd_5s_720p": 0.7, "_source": "documented"}}}}
        }
        c = pt_video.estimate_cost("demo-model", 5, "720p",
                                   catalog_models=models, prefer_catalog=True)
        self.assertEqual(0.7, c)

    def test_estimate_legacy_path_unchanged(self):
        # prefer_catalog=False + 不在写死表 -> None（旧行为）
        self.assertIsNone(pt_video.estimate_cost("unknown-xyz", 5, "720p", prefer_catalog=False))

    def test_estimate_duration_and_resolution_factor(self):
        c5 = pt_video.estimate_cost("wan3.0-video", 5, "720p", prefer_catalog=False)
        c10 = pt_video.estimate_cost("wan3.0-video", 10, "720p", prefer_catalog=False)
        c1080 = pt_video.estimate_cost("wan3.0-video", 5, "1080p", prefer_catalog=False)
        self.assertEqual(0.5, c5)
        self.assertEqual(1.0, c10)     # 时长线性
        self.assertEqual(1.0, c1080)   # 1080p = 2x

    def test_cmd_estimate_output_has_source(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = pt_video.cmd_estimate(["-m", "wan3.0-video", "-d", "5", "-r", "720p"])
        self.assertEqual(0, rc)
        data = __import__("json").loads(buf.getvalue())
        self.assertEqual("catalog:verified", data["price_source"])
        self.assertEqual(0.5, data["est_cost_usd"])


# ---------------- T07 ----------------
class TestT07FileExtension(unittest.TestCase):
    def test_png_magic(self):
        self.assertEqual(".png", pt_video.detect_file_extension(
            head=b"\x89PNG\r\n\x1a\n" + b"\x00" * 8))

    def test_jpeg_magic(self):
        self.assertEqual(".jpg", pt_video.detect_file_extension(head=b"\xff\xd8\xff\xe0" + b"\x00" * 8))

    def test_webp_magic(self):
        self.assertEqual(".webp", pt_video.detect_file_extension(head=b"RIFF\x00\x00\x00\x00WEBP"))

    def test_mp4_ftyp(self):
        self.assertEqual(".mp4", pt_video.detect_file_extension(head=b"\x00\x00\x00\x18ftypisom"))

    def test_content_type_fallback(self):
        self.assertEqual(".jpg", pt_video.detect_file_extension(
            head=b"", content_type="image/jpeg; charset=binary"))

    def test_default_when_unknown(self):
        self.assertEqual(".png", pt_video.detect_file_extension(head=b"????", default=".png"))

    def test_reads_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.bin"
            p.write_bytes(b"\xff\xd8\xff\xe0" + b"data")
            self.assertEqual(".jpg", pt_video.detect_file_extension(path=str(p)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
