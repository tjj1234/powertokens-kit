#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unit：复核 2026-11-24 修复——T02 下载误报成功 / T08 图片 request_id 串号。

全程 mock 生成分发与下载，使用虚拟 key，不联网、不花钱。
"""
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import pt_video  # noqa: E402


def _args(**changes):
    d = dict(prompt="offline review", duration=5, resolution="720p", ratio="16:9",
             aspect_ratio="16:9", n=1, response_format="url", seed=None,
             first_frame=None, last_frame=None, ref_image=[], ref_video=[], ref_audio=[],
             no_audio=True, no_prompt_extend=False, watermark=False, output=None,
             no_download=False, timeout=1, json=True)
    d.update(changes)
    return SimpleNamespace(**d)


def _fake_api(method, path, key, body=None, timeout=None):
    if method == "POST" and "images" in path:
        return {"data": [{"url": "https://result.invalid/image.jpg"}], "_request_id": "image-request-current"}
    if method == "POST":
        return {"id": "task-review", "_request_id": "video-request-current"}
    return {"status": "completed", "metadata": {"url": "https://result.invalid/video.mp4"}}


class TestDownloadFinishStatus(unittest.TestCase):
    """复核 T02：要求下载却没拿到文件 → 部分完成 + 非零退出码。"""

    def test_video_download_failure_is_partial(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pt_video, "_api_call", side_effect=_fake_api), \
             mock.patch.object(pt_video, "download_binary", side_effect=TimeoutError("fail")), \
             redirect_stdout(out), redirect_stderr(err):
            code = pt_video._run_one("DUMMY", "wan3.0-video", "video", _args())
        value = json.loads(out.getvalue())
        self.assertEqual(3, code)
        self.assertFalse(value["ok"])
        self.assertEqual("partial", value["status"])
        self.assertTrue(value["generation_ok"])
        self.assertFalse(value["download_ok"])
        self.assertEqual("task-review", value["task_id"])
        self.assertEqual("https://result.invalid/video.mp4", value["url"])
        self.assertIn("补下载", value["note"])

    def test_image_download_failure_is_partial(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pt_video, "_api_call", side_effect=_fake_api), \
             mock.patch.object(pt_video, "download_binary", side_effect=TimeoutError("fail")), \
             redirect_stdout(out), redirect_stderr(err):
            code = pt_video._run_one("DUMMY", "image-01", "image", _args())
        value = json.loads(out.getvalue())
        self.assertEqual(3, code)
        self.assertFalse(value["ok"])
        self.assertEqual("partial", value["status"])
        self.assertEqual([], value["outputs"])

    def test_no_download_mode_still_ok(self):
        """--no-download 只返回链接，不算失败（旧行为兼容）。"""
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pt_video, "_api_call", side_effect=_fake_api), \
             redirect_stdout(out), redirect_stderr(err):
            code = pt_video._run_one("DUMMY", "wan3.0-video", "video", _args(no_download=True))
        value = json.loads(out.getvalue())
        self.assertEqual(0, code)
        self.assertTrue(value["ok"])
        self.assertIsNone(value["download_ok"])

    def test_success_still_ok(self):
        """下载成功时保持 ok=true（旧输出语义不变）。"""
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pt_video, "_api_call", side_effect=_fake_api), \
             mock.patch.object(pt_video, "download_binary", return_value=True), \
             redirect_stdout(out), redirect_stderr(err):
            code = pt_video._run_one("DUMMY", "wan3.0-video", "video", _args(output="/tmp/x.mp4"))
        value = json.loads(out.getvalue())
        self.assertEqual(0, code)
        self.assertTrue(value["ok"])
        self.assertEqual("ok", value["status"])


class TestImageRequestId(unittest.TestCase):
    """复核 T08：图片 request_id 取本次同步响应，不吃上一次视频提交的编号。"""

    def test_fresh_process(self):
        pt_video.LAST_SUBMIT_REQUEST_ID = None
        pt_video.LAST_IMAGE_REQUEST_ID = None
        out = io.StringIO()
        with mock.patch.object(pt_video, "_api_call", side_effect=_fake_api), \
             redirect_stdout(out), redirect_stderr(io.StringIO()):
            pt_video._run_one("DUMMY", "image-01", "image", _args(no_download=True))
        self.assertEqual("image-request-current", json.loads(out.getvalue())["request_id"])

    def test_after_video_submit(self):
        pt_video.LAST_SUBMIT_REQUEST_ID = "previous-video-request"
        pt_video.LAST_IMAGE_REQUEST_ID = None
        out = io.StringIO()
        with mock.patch.object(pt_video, "_api_call", side_effect=_fake_api), \
             redirect_stdout(out), redirect_stderr(io.StringIO()):
            pt_video._run_one("DUMMY", "image-01", "image", _args(no_download=True))
        self.assertEqual("image-request-current", json.loads(out.getvalue())["request_id"])


if __name__ == "__main__":
    unittest.main()
