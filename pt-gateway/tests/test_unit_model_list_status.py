#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unit：T05 模型列表查询失败必须标「未知」，不得当成「不可用」还报成功。

钉死：
  1. list_key_models(strict=True) 查询失败 → 抛 ModelListUnavailable（可用性未知）；
  2. list_key_models(默认 strict=False) 查询失败 → 仍 warn + 返回 []（向后兼容 fail-open）；
  3. cmd_models 在查询失败时退出码 4（非 0），JSON 输出 availability=unknown；
  4. cmd_models 未配置 Key 时走公开目录，不查询、退出 0（不受影响）。
全程 mock，不联网、不花钱。
"""
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import pt_video  # noqa: E402

_KEY = "sk-test-key-T05"


def _raise_api(*a, **kw):
    raise pt_video.ApiError(500, "gateway down")


class TestModelListStatus(unittest.TestCase):
    # 1) strict=True 抛 ModelListUnavailable
    def test_strict_raises_unavailable(self):
        with mock.patch.object(pt_video, "_api_call", side_effect=_raise_api):
            with self.assertRaises(pt_video.ModelListUnavailable):
                pt_video.list_key_models(_KEY, "video", strict=True)

    # 2) 默认 strict=False 保持旧行为
    def test_default_still_fail_open(self):
        err = io.StringIO()
        with mock.patch.object(pt_video, "_api_call", side_effect=_raise_api), \
             redirect_stderr(err):
            out = pt_video.list_key_models(_KEY, "video")
        self.assertEqual([], out)
        self.assertIn("[warn]", err.getvalue())

    # 3) cmd_models 查询失败：退出 4 + JSON availability=unknown
    def test_cmd_models_marks_unknown(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pt_video, "resolve_key", return_value=_KEY), \
             mock.patch.object(pt_video, "_api_call", side_effect=_raise_api), \
             redirect_stdout(out), redirect_stderr(err):
            rc = pt_video.cmd_models(["--json"])
        self.assertEqual(4, rc, "查询失败必须非 0 退出")
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual("unknown", payload["availability"])

    # 4) 未配置 Key：公开目录，不查询，退出 0
    def test_cmd_models_no_key_public_catalog(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pt_video, "resolve_key", return_value=""), \
             mock.patch.object(pt_video, "_api_call", side_effect=AssertionError("不应联网")), \
             redirect_stdout(out), redirect_stderr(err):
            rc = pt_video.cmd_models(["--json"])
        self.assertEqual(0, rc)
        rows = json.loads(out.getvalue())
        self.assertTrue(len(rows) > 0)


if __name__ == "__main__":
    unittest.main()
