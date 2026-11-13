#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unit：ModelRegistry 的可见性 diff / 过期判定 / auto 选模。

2026-11-13 语义修正（PT 答复 + 双 Key 实测，见 SSOT §13）：
/v1/models 是「本 Key / 分组可见视图」，不是平台全量清单。
「不在列表」可能是真下架，也可能只是本 key 限额 / 分组未启用 / 无定价规则；
旧逻辑一律 delisted 会把可用模型永久误踢（静默故障）。
现在：不在列表只标 not_in_key_view=True 并保持 enabled（fail-open），
只有 sync(confirmed_delisted=[...]) 或 mark_delisted() 才真正停用。
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
        self.assertEqual([], d["not_in_key_view"])
        self.assertEqual(2, d["total_models"])
        e = reg.data["models"]["wan3.0-video"]
        self.assertTrue(e["enabled"])
        self.assertTrue(e["is_new"])
        self.assertTrue(e["in_models"])
        self.assertFalse(e["delisted"])
        self.assertFalse(e["not_in_key_view"])

    def test_second_sync_clears_is_new(self):
        reg = self.registry(["wan3.0-video"])
        reg.sync("KEY")
        d = reg.sync("KEY")
        self.assertEqual([], d["added"])
        self.assertFalse(reg.data["models"]["wan3.0-video"]["is_new"])

    def test_disappeared_model_is_not_delisted(self):
        """核心回归：不在可见视图 ≠ 下架，不能永久误踢可用模型。"""
        reg = self.registry(["wan3.0-video", "kling-video-1"])
        reg.sync("KEY")
        self.live_models = ["wan3.0-video"]          # kling 本 key 本次不可见
        d = reg.sync("KEY")
        self.assertEqual([], d["removed"])
        self.assertEqual(["kling-video-1"], d["not_in_key_view"])
        self.assertEqual(1, d["total_not_in_key_view"])
        e = reg.data["models"]["kling-video-1"]
        self.assertFalse(e["delisted"], "不该自动判下架")
        self.assertTrue(e["enabled"], "fail-open：仍保留为候选")
        self.assertFalse(e["in_models"])
        self.assertTrue(e["not_in_key_view"])
        self.assertTrue(reg.data["models"]["wan3.0-video"]["enabled"])

    def test_confirmed_delisted_marks_delisted(self):
        """只有显式确认（跨 Key 复核 / PT 公告）才真正停用。"""
        reg = self.registry(["wan3.0-video", "kling-video-1"])
        reg.sync("KEY")
        self.live_models = ["wan3.0-video"]
        d = reg.sync("KEY", confirmed_delisted=["kling-video-1"])
        self.assertEqual(["kling-video-1"], d["removed"])
        e = reg.data["models"]["kling-video-1"]
        self.assertTrue(e["delisted"])
        self.assertFalse(e["enabled"])
        self.assertTrue(e["not_in_key_view"])

    def test_mark_delisted_persists(self):
        reg = self.registry(["wan3.0-video", "kling-video-1"])
        reg.sync("KEY")
        self.assertEqual(["kling-video-1"],
                         reg.mark_delisted(["kling-video-1"], reason="PT 公告下架"))
        self.assertEqual([], reg.mark_delisted(["not-tracked"]))
        reloaded = pt_gateway.ModelRegistry(path=str(self.reg_path)).load()
        e = reloaded.data["models"]["kling-video-1"]
        self.assertTrue(e["delisted"])
        self.assertFalse(e["enabled"])
        self.assertEqual("PT 公告下架", e["delisted_reason"])

    def test_not_in_key_view_model_comes_back(self):
        """重新出现在视图里：必须清掉标记并保持可用。"""
        reg = self.registry(["wan3.0-video"])
        reg.sync("KEY")
        self.live_models = []                        # 本 key 暂时看不到
        reg.sync("KEY")
        self.assertTrue(reg.data["models"]["wan3.0-video"]["not_in_key_view"])
        self.live_models = ["wan3.0-video"]          # 又可见了
        d = reg.sync("KEY")
        self.assertEqual([], d["added"])             # 条目一直在，不算新增
        self.assertEqual([], d["not_in_key_view"])
        e = reg.data["models"]["wan3.0-video"]
        self.assertTrue(e["enabled"])
        self.assertFalse(e["delisted"])
        self.assertFalse(e["not_in_key_view"])
        self.assertTrue(e["in_models"])

    def test_explicitly_delisted_model_recovers_when_visible(self):
        """显式下架过的模型若重新出现，视为恢复（与旧行为一致）。"""
        reg = self.registry(["wan3.0-video"])
        reg.sync("KEY")
        self.live_models = []
        reg.sync("KEY", confirmed_delisted=["wan3.0-video"])
        self.assertTrue(reg.data["models"]["wan3.0-video"]["delisted"])
        self.live_models = ["wan3.0-video"]
        reg.sync("KEY")
        e = reg.data["models"]["wan3.0-video"]
        self.assertTrue(e["enabled"])
        self.assertFalse(e["delisted"])

    def test_removed_empty_without_confirmation(self):
        reg = self.registry(["wan3.0-video"])
        reg.sync("KEY")
        self.live_models = []
        reg.sync("KEY")
        d = reg.sync("KEY")
        self.assertEqual([], d["removed"], "没有显式确认就不该产生 removed")

    def test_not_in_key_view_is_an_observation_not_an_event(self):
        """not_in_key_view 是当前状态观测，每次 sync 都如实上报。"""
        reg = self.registry(["wan3.0-video"])
        reg.sync("KEY")
        self.live_models = []
        reg.sync("KEY")
        d = reg.sync("KEY")
        self.assertEqual(["wan3.0-video"], d["not_in_key_view"])

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

    def test_empty_live_list_keeps_enabled(self):
        """空列表通常是拉取失败 / 分组配置变化，不应把全部模型停用。"""
        reg = self.registry(["wan3.0-video"])
        reg.sync("KEY")
        self.live_models = []
        d = reg.sync("KEY")
        self.assertEqual(1, d["total_enabled"])
        self.assertEqual(1, d["total_models"])
        self.assertEqual(1, d["total_not_in_key_view"])
        self.assertEqual([], d["removed"])


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
