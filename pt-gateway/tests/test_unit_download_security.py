#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unit：T06 下载安全——API Key 绝不能泄露给第三方产物地址。

背景：产物 URL 多为 PT 自有 R2 公共桶（pub-*.r2.dev）或上游签名链接（OSS/S3），
旧逻辑「先带 Bearer key 请求、失败再裸请求」导致每次下载都先把 Key 发给第三方。
本测试钉死修正后的行为：
  1. 第三方地址：只发裸请求（无 Authorization），即便 401/403 也不带 key 重试；
  2. PT 官方域名：裸请求优先；仅当 401/403 才带 key 重试一次；
  3. 仿冒域名（evil-powertokens.ai）：按第三方处理，永不接触 key。
全程 mock urlopen，不联网、不花钱。
"""
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import pt_video  # noqa: E402

_TEST_KEY = "sk-pt-T06-SECRET-KEY-MUST-NOT-LEAK"


class _Resp(io.BytesIO):
    """最小 urlopen 响应：支持 context manager + read。"""
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _http_error(code):
    return pt_video.urllib.error.HTTPError(
        "http://x", code, "denied", {}, None
    )


class TestDownloadSecurity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "out.bin")

    def tearDown(self):
        self.tmp.cleanup()

    def _assert_no_key_in_headers(self, calls):
        for (req, *_) in calls:
            headers = {k.lower(): v for k, v in (req.header_items() if hasattr(req, "header_items") else [])}
            self.assertNotIn("authorization", headers, "第三方请求不得带 Authorization")
            for v in headers.values():
                self.assertNotIn(_TEST_KEY, str(v))

    # ---- 1) 第三方产物地址：只裸请求，成功即止 ----
    def test_third_party_url_sends_no_key(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append((req, timeout))
            return _Resp(b"video-bytes")

        with mock.patch.object(pt_video.urllib.request, "urlopen", side_effect=fake_urlopen):
            self.assertTrue(pt_video.download_binary(
                "https://pub-abc123.r2.dev/task-results/x.mp4", self.path, _TEST_KEY))
        self.assertEqual(1, len(calls))
        self._assert_no_key_in_headers(calls)
        with open(self.path, "rb") as f:
            self.assertEqual(b"video-bytes", f.read())

    # ---- 2) 第三方地址即便 401/403 也绝不带 key 重试 ----
    def test_third_party_401_never_retries_with_key(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append((req, timeout))
            raise _http_error(401)

        with mock.patch.object(pt_video.urllib.request, "urlopen", side_effect=fake_urlopen):
            with self.assertRaises(pt_video.urllib.error.HTTPError):
                pt_video.download_binary(
                    "https://xxx.oss-us-east-1.aliyuncs.com/a.png?Expires=1", self.path, _TEST_KEY)
        self.assertEqual(1, len(calls))  # 没有第二次带 key 的请求
        self._assert_no_key_in_headers(calls)

    # ---- 3) PT 官方域名：裸请求 401 → 带 key 重试一次 ----
    def test_pt_domain_401_retries_with_key(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            headers = {k.lower(): v for k, v in req.header_items()}
            calls.append(headers)
            if len(calls) == 1:
                self.assertNotIn("authorization", headers)
                raise _http_error(401)
            return _Resp(b"pt-bytes")

        with mock.patch.object(pt_video.urllib.request, "urlopen", side_effect=fake_urlopen):
            self.assertTrue(pt_video.download_binary(
                "https://api.powertokens.ai/v1/videos/t1/content", self.path, _TEST_KEY))
        self.assertEqual(2, len(calls))
        self.assertIn("authorization", calls[1])
        self.assertIn(_TEST_KEY, calls[1]["authorization"])

    # ---- 4) 仿冒域名按第三方处理 ----
    def test_lookalike_domain_never_gets_key(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append((req, timeout))
            raise _http_error(403)

        with mock.patch.object(pt_video.urllib.request, "urlopen", side_effect=fake_urlopen):
            with self.assertRaises(pt_video.urllib.error.HTTPError):
                pt_video.download_binary(
                    "https://evil-powertokens.ai/content.mp4", self.path, _TEST_KEY)
        self.assertEqual(1, len(calls))
        self._assert_no_key_in_headers(calls)

    # ---- 5) host 判定白名单 ----
    def test_trusted_host_predicate(self):
        self.assertTrue(pt_video._trusted_pt_host("https://powertokens.ai/a"))
        self.assertTrue(pt_video._trusted_pt_host("https://api.powertokens.ai/x"))
        self.assertTrue(pt_video._trusted_pt_host("https://API.POWERTOKENS.AI./x"))
        self.assertFalse(pt_video._trusted_pt_host("https://pub-x.r2.dev/x"))
        self.assertFalse(pt_video._trusted_pt_host("https://evil-powertokens.ai/x"))
        self.assertFalse(pt_video._trusted_pt_host("not a url"))


class TestDownloadSecurityHardening(unittest.TestCase):
    """2026-11-24 复核加固：跨来源跳转 / HTTP 降级 / JSON 错误正文。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "out.mp4")

    def tearDown(self):
        self.tmp.cleanup()

    # ---- 6) 鉴权后跨来源跳转：跳转请求不得带 Authorization ----
    def test_auth_redirect_to_third_party_drops_key(self):
        seen = []

        def fake_urlopen(req, timeout=None):
            auth = req.get_header("Authorization")
            seen.append((req.full_url, bool(auth)))
            if not auth:
                raise _http_error(401)
            moved = pt_video.urllib.request.HTTPRedirectHandler().redirect_request(
                req, None, 302, "Found", {}, "https://thirdparty.invalid/result.mp4")
            seen.append((moved.full_url, bool(moved.get_header("Authorization"))))
            return _Resp(b"video")

        with mock.patch.object(pt_video.urllib.request, "urlopen", side_effect=fake_urlopen):
            pt_video.download_binary("https://api.powertokens.ai/result.mp4", self.path, _TEST_KEY)
        # seen[0]: 裸请求（无 key）；seen[1]: 带 key 重试的官方地址；最后一次：第三方跳转
        self.assertFalse(seen[0][1], "裸请求不应带 key")
        third = [s for s in seen if "thirdparty" in s[0]]
        self.assertTrue(third, "应观察到跨来源跳转请求")
        self.assertFalse(third[-1][1], "跨来源跳转不得携带 Authorization")

    # ---- 7) 官方 HTTP 地址 401：拒绝降级明文重试 ----
    def test_http_auth_never_retries(self):
        seen = []

        def fake_urlopen(req, timeout=None):
            seen.append(bool(req.get_header("Authorization")))
            if not req.get_header("Authorization"):
                raise _http_error(401)
            return _Resp(b"video")

        with mock.patch.object(pt_video.urllib.request, "urlopen", side_effect=fake_urlopen):
            with self.assertRaises(pt_video.urllib.error.HTTPError):
                pt_video.download_binary("http://api.powertokens.ai/result.mp4", self.path, _TEST_KEY)
        self.assertEqual([False], seen, "HTTP 地址不得带 key 重试")

    # ---- 8) JSON 错误正文：不得存成媒体产物 ----
    def test_json_error_body_rejected(self):
        payload = b'{"error":"download refused"}'
        resp = _Resp(payload)
        resp.headers = {"Content-Type": "application/json", "Content-Length": str(len(payload))}
        with mock.patch.object(pt_video.urllib.request, "urlopen", return_value=resp):
            with self.assertRaises(pt_video.ApiError):
                pt_video.download_binary("https://result.invalid/file.mp4", self.path, _TEST_KEY)
        self.assertFalse(os.path.exists(self.path), "JSON 错误正文不得落盘为产物")


if __name__ == "__main__":
    unittest.main()
