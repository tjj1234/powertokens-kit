#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unit：T02 下载完整性——残缺文件绝不落盘、成功才原子改名。

背景：旧 download_binary 直接 open(path,"wb") 边读边写，超时/中断即留残缺
目标文件，上层还报告成功。T02 改为写 <path>.part → 校验 → os.replace 原子落盘。
本测试钉死：
  1. 正常下载成功：目标文件存在、内容正确、无 .part 残留；
  2. 读一半抛异常（模拟超时）：目标文件不落盘、.part 被清理、异常上抛；
  3. 空响应：判失败、不落盘、无残留；
  4. Content-Length 与实际不符：判失败、不落盘、无残留；
  5. HTML 错误页冒充产物：判失败、不落盘、无残留。
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

_KEY = "sk-test-key-integrity"


class _Resp(io.BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


class TestDownloadIntegrity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "out.bin")

    def tearDown(self):
        self.tmp.cleanup()

    def _no_leftovers(self):
        self.assertFalse(os.path.exists(self.path), "失败时目标文件不应落盘")
        self.assertFalse(os.path.exists(self.path + ".part"), "失败时 .part 应被清理")

    # 1) 正常成功 + 原子落盘
    def test_success_writes_target_no_part(self):
        with mock.patch.object(pt_video.urllib.request, "urlopen",
                               return_value=_Resp(b"complete-video")):
            self.assertTrue(pt_video.download_binary("https://x.dev/a.mp4", self.path, _KEY))
        with open(self.path, "rb") as f:
            self.assertEqual(b"complete-video", f.read())
        self.assertFalse(os.path.exists(self.path + ".part"))

    # 2) 读一半中断：目标不落盘、.part 清理、异常上抛
    def test_partial_read_leaves_no_residual_file(self):
        class HalfResp(_Resp):
            def read(self, n=-1):
                data = super().read(n)
                return data[:4] if data else b""  # 只吐一小块，第二次为空→看似结束

        # 用 side_effect 在读的过程中抛错，模拟 socket 中断
        def boom(req, timeout=None):
            class Broken(_Resp):
                def read(self, n=-1):
                    raise OSError("connection reset mid-download")
            return Broken(b"")

        with mock.patch.object(pt_video.urllib.request, "urlopen", side_effect=boom):
            with self.assertRaises(OSError):
                pt_video.download_binary("https://x.dev/a.mp4", self.path, _KEY)
        self._no_leftovers()

    # 3) 空响应
    def test_empty_response_rejected(self):
        with mock.patch.object(pt_video.urllib.request, "urlopen",
                               return_value=_Resp(b"")):
            with self.assertRaises(pt_video.ApiError):
                pt_video.download_binary("https://x.dev/a.mp4", self.path, _KEY)
        self._no_leftovers()

    # 4) Content-Length 不符
    def test_length_mismatch_rejected(self):
        resp = _Resp(b"short", headers={"Content-Length": "999"})
        with mock.patch.object(pt_video.urllib.request, "urlopen", return_value=resp):
            with self.assertRaises(pt_video.ApiError):
                pt_video.download_binary("https://x.dev/a.mp4", self.path, _KEY)
        self._no_leftovers()

    # 5) HTML 错误页冒充产物
    def test_html_error_page_rejected(self):
        page = b"<!DOCTYPE html><html><body>404 Not Found</body></html>"
        with mock.patch.object(pt_video.urllib.request, "urlopen",
                               return_value=_Resp(page)):
            with self.assertRaises(pt_video.ApiError):
                pt_video.download_binary("https://x.dev/a.mp4", self.path, _KEY)
        self._no_leftovers()


if __name__ == "__main__":
    unittest.main()
