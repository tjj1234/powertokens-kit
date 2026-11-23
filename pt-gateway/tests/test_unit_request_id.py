#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unit：T08 请求编号逐笔对账——X-Oneapi-Request-Id 必须被捕获并保存。

钉死：
  1. 成功响应带 X-Oneapi-Request-Id → 返回 dict 含 _request_id，日志记一笔；
  2. 响应头大小写不敏感（x-oneapi-request-id 也能识别）；
  3. 错误响应同样记一笔（从 HTTPError.headers 提取）；
  4. 无该头 → 不注入 _request_id，日志仍记 request_id=None（不漏记调用）；
  5. submit_task 把提交阶段的 request id 记录到 LAST_SUBMIT_REQUEST_ID；
  6. get_request_log 返回副本，外部改动不影响内部日志。
全程 mock urlopen，不联网、不花钱。
"""
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import pt_video  # noqa: E402

_KEY = "sk-test-key-T08"


class _Resp(io.BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


class TestRequestId(unittest.TestCase):
    def setUp(self):
        pt_video.clear_request_log()
        pt_video.LAST_SUBMIT_REQUEST_ID = None

    def tearDown(self):
        pt_video.clear_request_log()
        pt_video.LAST_SUBMIT_REQUEST_ID = None

    # 1) 成功响应注入 + 记日志
    def test_success_injects_and_logs(self):
        resp = _Resp(b'{"id":"t1"}', headers={"X-Oneapi-Request-Id": "req-abc"})
        with mock.patch.object(pt_video.urllib.request, "urlopen", return_value=resp):
            out = pt_video._api_call("POST", "/v1/videos", _KEY, body={"model": "m"})
        self.assertEqual("req-abc", out["_request_id"])
        log = pt_video.get_request_log()
        self.assertEqual(1, len(log))
        self.assertEqual(("POST", "/v1/videos", "req-abc"),
                         (log[0]["method"], log[0]["path"], log[0]["request_id"]))

    # 2) 大小写不敏感
    def test_header_case_insensitive(self):
        resp = _Resp(b'{"ok":true}', headers={"x-oneapi-request-id": "req-lower"})
        with mock.patch.object(pt_video.urllib.request, "urlopen", return_value=resp):
            out = pt_video._api_call("GET", "/v1/videos/t1", _KEY)
        self.assertEqual("req-lower", out["_request_id"])

    # 3) 错误响应也记一笔
    def test_error_response_logged(self):
        def boom(req, timeout=None):
            err = pt_video.urllib.error.HTTPError(
                "http://x", 400, "bad", {"X-Oneapi-Request-Id": "req-err"}, None)
            raise err
        with mock.patch.object(pt_video.urllib.request, "urlopen", side_effect=boom):
            with self.assertRaises(pt_video.ApiError):
                pt_video._api_call("POST", "/v1/videos", _KEY, body={})
        log = pt_video.get_request_log()
        self.assertEqual("req-err", log[0]["request_id"])

    # 4) 无该头 → 不注入、仍记 None
    def test_no_header_logs_none(self):
        resp = _Resp(b'{"id":"t2"}', headers={"Content-Type": "application/json"})
        with mock.patch.object(pt_video.urllib.request, "urlopen", return_value=resp):
            out = pt_video._api_call("POST", "/v1/videos", _KEY, body={})
        self.assertNotIn("_request_id", out)
        self.assertIsNone(pt_video.get_request_log()[0]["request_id"])

    # 5) submit_task 记录提交 request id
    def test_submit_records_last_request_id(self):
        def fake_api(method, path, key, body=None, timeout=30):
            return {"id": "task-t08", "_request_id": "req-submit"}
        with mock.patch.object(pt_video, "_api_call", side_effect=fake_api):
            tid, _ = pt_video.submit_task(_KEY, {"model": "m"})
        self.assertEqual("task-t08", tid)
        self.assertEqual("req-submit", pt_video.LAST_SUBMIT_REQUEST_ID)

    # 6) get_request_log 返回副本
    def test_get_log_returns_copy(self):
        resp = _Resp(b'{}', headers={"X-Oneapi-Request-Id": "req-x"})
        with mock.patch.object(pt_video.urllib.request, "urlopen", return_value=resp):
            pt_video._api_call("GET", "/v1/models", _KEY)
        log = pt_video.get_request_log()
        log[0]["request_id"] = "tampered"
        self.assertEqual("req-x", pt_video.get_request_log()[0]["request_id"])


if __name__ == "__main__":
    unittest.main()
