#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unit：状态归一化 / 结果抽取 / 模块级常量不变量。

这些是所有 kind 的公共底座——一旦这里的映射写错，上层表现是
"任务永远在跑"或"明明成功了却拿不到 URL"，排查成本极高，所以逐条钉死。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (  # noqa: E402
    pt_gateway, GatewayTestBase, KNOWN_KINDS, ASYNC_KINDS, SYNC_KINDS,
)


class TestTaskState(unittest.TestCase):
    """各厂商状态动词 -> 统一状态机。"""

    def test_success_aliases(self):
        TS = pt_gateway.TaskState
        for raw in ("succeeded", "completed", "SUCCEEDED", "Completed"):
            self.assertEqual(TS.SUCCEEDED, TS.normalize(raw), raw)

    def test_failed(self):
        self.assertEqual(pt_gateway.TaskState.FAILED,
                         pt_gateway.TaskState.normalize("failed"))

    def test_cancelled_both_spellings(self):
        TS = pt_gateway.TaskState
        self.assertEqual(TS.CANCELLED, TS.normalize("cancelled"))
        self.assertEqual(TS.CANCELLED, TS.normalize("canceled"))

    def test_queued_aliases(self):
        TS = pt_gateway.TaskState
        for raw in ("pending", "queued", "submitted"):
            self.assertEqual(TS.QUEUED, TS.normalize(raw), raw)

    def test_running_aliases(self):
        TS = pt_gateway.TaskState
        for raw in ("running", "processing", "in_progress", "doing", "generating"):
            self.assertEqual(TS.RUNNING, TS.normalize(raw), raw)

    def test_unknown_is_not_silently_terminal(self):
        """未知状态必须落到 UNKNOWN，而不能被当成终态提前结束轮询。"""
        TS = pt_gateway.TaskState
        self.assertEqual(TS.UNKNOWN, TS.normalize("weird"))
        self.assertEqual(TS.UNKNOWN, TS.normalize(""))
        self.assertEqual(TS.UNKNOWN, TS.normalize(None))

    def test_only_terminal_states_are_three(self):
        """终态集合：SUCCEEDED / FAILED / CANCELLED。poll 依赖这个集合退出循环。"""
        TS = pt_gateway.TaskState
        terminal = {TS.SUCCEEDED, TS.FAILED, TS.CANCELLED}
        for s in (TS.QUEUED, TS.RUNNING, TS.UNKNOWN):
            self.assertNotIn(s, terminal)


class TestResultUrl(unittest.TestCase):
    """结果 URL 抽取：厂商把链接放在不同字段（历史踩坑点）。"""

    def test_metadata_url(self):
        self.assertEqual("http://a/1.mp4",
                         pt_gateway.result_url({"metadata": {"url": "http://a/1.mp4"}}))

    def test_metadata_content_url_fallback(self):
        self.assertEqual("http://a/2.mp4",
                         pt_gateway.result_url({"metadata": {"content_url": "http://a/2.mp4"}}))

    def test_top_level_url_fallback(self):
        self.assertEqual("http://a/3.mp4", pt_gateway.result_url({"url": "http://a/3.mp4"}))

    def test_metadata_url_wins_over_top_level(self):
        t = {"url": "http://top", "metadata": {"url": "http://meta"}}
        self.assertEqual("http://meta", pt_gateway.result_url(t))

    def test_missing_returns_none_not_exception(self):
        self.assertIsNone(pt_gateway.result_url({}))
        self.assertIsNone(pt_gateway.result_url({"metadata": None}))


class TestNormalizeStatus(unittest.TestCase):
    def test_full_shape(self):
        ns = pt_gateway.normalize_status(
            {"id": "t1", "status": "completed",
             "metadata": {"url": "http://x/y.mp4", "progress": 80}})
        self.assertEqual("t1", ns["task_id"])
        self.assertEqual("SUCCEEDED", ns["state"])
        self.assertEqual(80, ns["progress"])
        self.assertEqual("http://x/y.mp4", ns["url"])

    def test_task_id_alias(self):
        """有的厂商用 task_id 而不是 id。"""
        self.assertEqual("t2", pt_gateway.normalize_status({"task_id": "t2", "status": "queued"})["task_id"])

    def test_progress_top_level_preferred(self):
        ns = pt_gateway.normalize_status({"status": "running", "progress": 42,
                                          "metadata": {"progress": 7}})
        self.assertEqual(42, ns["progress"])

    def test_progress_from_metadata(self):
        ns = pt_gateway.normalize_status({"status": "running", "metadata": {"progress": 7}})
        self.assertEqual(7, ns["progress"])

    def test_raw_is_preserved_for_debugging(self):
        raw = {"id": "t3", "status": "failed", "error": {"message": "boom"}}
        self.assertIs(raw, pt_gateway.normalize_status(raw)["raw"])


class TestKindInvariants(unittest.TestCase):
    """模块级常量自洽：新增 kind 时必须同步三类常量，否则路由会漏。"""

    def test_known_equals_async_plus_sync(self):
        self.assertEqual(set(KNOWN_KINDS), set(ASYNC_KINDS) | set(SYNC_KINDS))

    def test_async_sync_no_overlap(self):
        self.assertEqual(set(), set(ASYNC_KINDS) & set(SYNC_KINDS))

    def test_expected_kinds(self):
        self.assertEqual({"image", "video", "seedance", "audio", "chat"}, set(KNOWN_KINDS))

    def test_sync_kinds_are_exactly_the_cheap_ones(self):
        """同步 = 一次调用出结果；异步 = 要轮询。分错会导致同步接口被拿去轮询。"""
        self.assertEqual({"image", "audio", "chat"}, set(SYNC_KINDS))
        self.assertEqual({"video", "seedance"}, set(ASYNC_KINDS))


class TestEndpointConstants(unittest.TestCase):
    """端点常量：历史 bug 回归（G8：常量只有使用没有定义，带 key 调用即 NameError）。"""

    def test_all_endpoints_defined_and_absolute(self):
        for name in ("IMAGE_ENDPOINT", "AUDIO_ENDPOINT", "CHAT_ENDPOINT",
                     "VIDEO_SUBMIT", "VIDEO_POLL"):
            v = getattr(pt_gateway, name, None)
            self.assertIsInstance(v, str, f"{name} 未定义或不是字符串")
            self.assertTrue(v.startswith("/"), f"{name}={v} 应为绝对路径")

    def test_audio_endpoint_is_openai_compatible(self):
        """回归：曾误判为 /ali/api/v1/services/aigc/...（实测 404/500）。真端点是 /v1/audio/speech。"""
        self.assertEqual("/v1/audio/speech", pt_gateway.AUDIO_ENDPOINT)
        self.assertNotIn("/ali/", pt_gateway.AUDIO_ENDPOINT)

    def test_image_endpoint(self):
        self.assertEqual("/v1/images/generations", pt_gateway.IMAGE_ENDPOINT)

    def test_chat_endpoint(self):
        self.assertEqual("/v1/chat/completions", pt_gateway.CHAT_ENDPOINT)

    def test_default_voice_present(self):
        """TTS 的 voice 必填（缺失会报 voice is required），必须有默认值兜底。"""
        self.assertTrue(pt_gateway.DEFAULT_VOICE)


class TestNoCancelSurface(unittest.TestCase):
    """回归：PT 无异步取消接口（2026-10-30 与官方确认），网关不得再提供取消。"""

    def test_gateway_has_no_cancel(self):
        self.assertFalse(hasattr(pt_gateway.TaskGateway, "cancel"),
                         "TaskGateway 不应再有 cancel()")

    def test_module_has_no_cancel_constant(self):
        for name in ("SEEDANCE_CANCEL", "CANCEL_ENDPOINT"):
            self.assertFalse(hasattr(pt_gateway, name), f"{name} 应已删除")


if __name__ == "__main__":
    unittest.main()
