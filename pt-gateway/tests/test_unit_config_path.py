#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unit：配置路径解析（更名 powertokens-video → powertokens-kit 的兼容策略）。

改名最怕的是老用户读不到自己的 Key。这里把优先级钉死：
PT_VIDEO_CONFIG > 新路径 > 旧路径回落 > 新路径（首次写入）。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import pt_video  # noqa: E402


class TestConfigPathResolution(unittest.TestCase):
    def test_env_override_wins(self):
        p = pt_video._resolve_config_path(env_path="/tmp/custom.json", home="/nonexistent")
        self.assertEqual(Path("/tmp/custom.json"), p)

    def test_prefers_new_when_both_exist(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            new = base / ".config" / "powertokens-kit" / "config.json"
            legacy = base / ".config" / "powertokens-video" / "config.json"
            for f in (new, legacy):
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text("{}", encoding="utf-8")
            self.assertEqual(new, pt_video._resolve_config_path(env_path="", home=base))

    def test_falls_back_to_legacy_when_only_legacy_exists(self):
        """老用户只迁移代码、不动配置：必须还能读到旧目录的 key。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            legacy = base / ".config" / "powertokens-video" / "config.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("{}", encoding="utf-8")
            self.assertEqual(legacy, pt_video._resolve_config_path(env_path="", home=base))

    def test_defaults_to_new_when_nothing_exists(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            p = pt_video._resolve_config_path(env_path="", home=base)
            self.assertEqual(base / ".config" / "powertokens-kit" / "config.json", p)


if __name__ == "__main__":
    unittest.main()
