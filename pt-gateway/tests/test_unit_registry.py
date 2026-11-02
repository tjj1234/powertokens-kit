#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unit：ModelRegistry 的上下架 diff / 过期判定 / auto 选模。

注册表是"模型上下架不用改代码"的实现基础：/v1/models 里新出现的自动 enabled，
消失的自动 delisted。逻辑错了的表现是"模型明明还在却选不到"，属于静默故障，
所以 diff 语义必须逐条钉死（含"下架后又回来了"这种恢复路径）。
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import pt_gateway, GatewayTestBase  # noqa: E402


class RegistryTestBase(GatewayTestBase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.reg_path = self.tmp / "registry.json"
        pt_gateway.list_key_models = lambda key, kind="all": self.live_models

    def tearDown(self):
        self._tmp.cleanup()
        super().tearDown()

    def registry(self, live_models=None, **kw):
        self.live_models = list(live_models or [])
        return pt_gateway.ModelRegistry(path=str(self.reg_path), **kw)


class TestSyncDiff(RegistryTestBase):
    def test_first_sync_enables_all_and_marks_new(self):
        reg = self.registry(["wan3.0-video", "seedream-5-0-260128"])
        d = reg.sync("KEY")
        self.assertEqual(sorted(["wan3.0-video", "seedream-5-0-260128"]), sorted(d["added"]))
        self.assertEqual([], d["removed"])
        self.assertEqual(2, d["total_models"])
        e = reg.data["models"]["wan3.0-video"]
        self.assertTrue(e["enabled"])
        self.assertTrue(e["is_new"])
        self.assertTrue(e["in_models"])
        self.assertFalse(e["delisted"])

    def test_second_sync_clears_is_new(self):
        reg = self.registry(["wan3.0-video"])
        reg.sync("KEY")
        d = reg.sync("KEY")
        self.assertEqual([], d["added"])
        self.assertFalse(reg.data["models"]["wan3.0-video"]["is_new"])

    def test_disappeared_model_is_delisted(self):
        reg = self.registry(["wan3.0-video", "kling-video-1"])
        reg.sync("KEY")
        self.live_models = ["wan3.0-video"]          # kling 下架
        d = reg.sync("KEY")
        self.assertEqual(["kling-video-1"], d["removed"])
        e = reg.data["models"]["kling-video-1"]
        self.assertTrue(e["delisted"])
        self.assertFalse(e["enabled"])
        self.assertFalse(e["in_models"])
        self.assertTrue(reg.data["models"]["wan3.0-video"]["enabled"])

    def test_delisted_model_comes_back(self):
        """下架后又上架：必须能自动恢复 enabled，否则模型永不可用。"""
        reg = self.registry(["wan3.0-video"])
        reg.sync("KEY")
        self.live_models = []                        # 全部下架
        reg.sync("KEY")
        self.live_models = ["wan3.0-video"]          # 又回来了
        d = reg.sync("KEY")
        # 注意：条目一直存在（只是被标 delisted），所以不算 added（added 仅指首次出现）
        self.assertEqual([], d["added"])
        e = reg.data["models"]["wan3.0-video"]
        self.assertTrue(e["enabled"])
        self.assertFalse(e["delisted"])
        self.assertTrue(e["in_models"])

    def test_removed_only_reported_once(self):
        reg = self.registry(["wan3.0-video"])
        reg.sync("KEY")
        self.live_models = []
        reg.sync("KEY")
        d = reg.sync("KEY")
        self.assertEqual([], d["removed"], "已经标记 delisted 的不该重复上报")

    def test_last_synced_and_counts(self):
        reg = self.registry(["wan3.0-video", "seedream-5-0-260128"])
        d = reg.sync("KEY")
        self.assertTrue(d["last_synced"])
        self.assertEqual(2, d["total_enabled"])
        self.assertFalse(reg.is_stale())

    def test_kind_is_recorded(self):
        reg = self.registry(["wan3.0-video"])
        reg.sync("KEY")
        self.assertEqual("video", reg.data["models"]["wan3.0-video"]["kind"])

    def test_persists_to_disk(self):
        reg = self.registry(["wan3.0-video"])
        reg.sync("KEY")
        self.assertTrue(self.reg_path.exists())
        reloaded = pt_gateway.ModelRegistry(path=str(self.reg_path)).load()
        self.assertIn("wan3.0-video", reloaded.data["models"])

    def test_empty_live_list_delists_everything(self):
        reg = self.registry(["wan3.0-video"])
        reg.sync("KEY")
        self.live_models = []
        d = reg.sync("KEY")
        self.assertEqual(0, d["total_enabled"])
        self.assertEqual(1, d["total_models"])


class TestStale(RegistryTestBase):
    def _reg_with(self, last_synced):
        reg = self.registry([])
        reg.data["last_synced"] = last_synced
        return reg

    def test_no_last_synced_is_stale(self):
        self.assertTrue(self._reg_with(None).is_stale())

    def test_fresh_is_not_stale(self):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.assertFalse(self._reg_with(now).is_stale())

    def test_two_hours_old_is_stale(self):
        old = (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat(timespec="seconds")
        self.assertTrue(self._reg_with(old).is_stale())

    def test_naive_timestamp_treated_as_utc(self):
        """没有时区的时间戳按 UTC 处理，否则会误判成"很久以前"或"未来"。"""
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
        self.assertFalse(self._reg_with(now).is_stale())

    def test_garbage_timestamp_is_stale(self):
        self.assertTrue(self._reg_with("not-a-date").is_stale())

    def test_custom_max_age(self):
        old = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat(timespec="seconds")
        reg = self._reg_with(old)
        self.assertFalse(reg.is_stale(max_age=3600))
        self.assertTrue(reg.is_stale(max_age=60))


class TestLoadRobustness(RegistryTestBase):
    def test_missing_file_is_empty_not_crash(self):
        reg = pt_gateway.ModelRegistry(path=str(self.tmp / "nope.json")).load()
        self.assertEqual({}, reg.data["models"])

    def test_corrupt_file_falls_back_to_empty(self):
        p = self.tmp / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        reg = pt_gateway.ModelRegistry(path=str(p)).load()
        self.assertEqual({}, reg.data["models"])


class TestAutoSelect(RegistryTestBase):
    def _reg(self, models):
        reg = self.registry([])
        reg.data["models"] = models
        return reg

    def _m(self, kind, enabled=True, delisted=False):
        return {"kind": kind, "enabled": enabled, "delisted": delisted}

    def test_prefers_higher_priority_model(self):
        """video 优先级表里 kling 在 wan 之前。"""
        reg = self._reg({"wan3.0-video": self._m("video"),
                         "kling-v2": self._m("video")})
        self.assertEqual("kling-v2", reg.auto_select("video"))

    def test_seedance_uses_seedance_priority(self):
        reg = self._reg({"dreamina-seedance-2-0-mini-260615": self._m("seedance"),
                         "seedance-1-5-pro-251215": self._m("seedance")})
        self.assertEqual("seedance-1-5-pro-251215", reg.auto_select("seedance"))

    def test_skips_delisted(self):
        reg = self._reg({"kling-v2": self._m("video", enabled=False, delisted=True),
                         "wan3.0-video": self._m("video")})
        self.assertEqual("wan3.0-video", reg.auto_select("video"))

    def test_kind_filter(self):
        reg = self._reg({"wan3.0-video": self._m("video"),
                         "seedream-5-0-260128": self._m("image")})
        self.assertEqual("wan3.0-video", reg.auto_select("video"))
        self.assertIsNone(reg.auto_select("audio"))

    def test_empty_returns_none(self):
        self.assertIsNone(self._reg({}).auto_select("video"))

    def test_enabled_async(self):
        reg = self._reg({"a-video": self._m("video"), "b-video": self._m("video", delisted=True),
                         "c-image": self._m("image")})
        self.assertEqual(["a-video"], reg.enabled_async("video"))
        self.assertEqual(["a-video", "c-image"], sorted(reg.enabled_async()))


class TestAutoSyncBestEffort(RegistryTestBase):
    def test_syncs_when_stale(self):
        reg = self.registry(["wan3.0-video"])
        d = reg.auto_sync_if_stale("KEY")
        self.assertIsNotNone(d)
        self.assertEqual(["wan3.0-video"], d["added"])

    def test_no_sync_when_fresh(self):
        reg = self.registry(["wan3.0-video"])
        reg.sync("KEY")
        self.assertIsNone(reg.auto_sync_if_stale("KEY"))

    def test_failure_is_swallowed_not_raised(self):
        """自动同步是 best-effort：PT 抖动不能让业务调用直接崩。"""
        reg = self.registry(["wan3.0-video"])

        def boom(key, max_age=3600):
            raise pt_gateway.ApiError(500, "models endpoint down")

        reg.sync = boom
        self.assertIsNone(reg.auto_sync_if_stale("KEY"))

    def test_explicit_sync_propagates_errors(self):
        """对比：显式 sync 必须抛错（调用方明确要求同步，静默失败会误导）。"""
        reg = self.registry(["wan3.0-video"])

        def down(key, kind="all"):
            raise pt_gateway.ApiError(500, "models endpoint down")

        pt_gateway.list_key_models = down
        with self.assertRaises(pt_gateway.ApiError):
            reg.sync("KEY")


if __name__ == "__main__":
    unittest.main()
