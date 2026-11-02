#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unit：轮询健壮性（§2.5）——429 退避 / 5xx 熔断 / 4xx 透传 / 超时。

这一块是"线上能不能扛住"的核心，但用真机测不出来（要服务端配合造错），
所以必须用假时钟 + 脚本化响应把它测透：
  - 假时钟保证：不真 sleep（否则一个退避用例要跑几十秒）、结果完全确定
  - 脚本化响应保证：能精确复现 429/5xx/抖动恢复/永久排队 等真机难造的分支
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import pt_gateway, GatewayTestBase, ApiError  # noqa: E402

TID = "task-abc"
POLL_PATH = "/v1/videos/" + TID


def task(status, **extra):
    d = {"id": TID, "status": status}
    d.update(extra)
    return d


def err(status, msg="boom", headers=None):
    """构造带响应头的 ApiError —— 与真实引擎路径一致（引擎现在会透出 headers）。"""
    return ApiError(status, msg, headers=headers)


class TestPollHappyPath(GatewayTestBase):
    def test_success_on_first_poll(self):
        api = self.install_api([task("succeeded", metadata={"url": "http://x/1.mp4"})])
        out = pt_gateway.poll_task("KEY", TID, timeout=600)
        self.assertEqual("succeeded", out["status"])
        self.assertEqual(1, len(api.calls))
        self.assertEqual([], self.clock.sleeps, "首次即终态不应 sleep")

    def test_queued_then_running_then_success(self):
        api = self.install_api([task("queued"), task("running"),
                                task("succeeded", metadata={"url": "http://x/1.mp4"})])
        out = pt_gateway.poll_task("KEY", TID, timeout=600)
        self.assertEqual("succeeded", out["status"])
        self.assertEqual(3, len(api.calls))
        # 退避序列：min(backoff, max(1, POLL_INTERVAL))，backoff 翻倍
        self.assertEqual([1.0, 2.0], self.clock.sleeps)

    def test_uses_correct_poll_path(self):
        api = self.install_api([task("succeeded")])
        pt_gateway.poll_task("KEY", TID, timeout=600)
        self.assertEqual("GET", api.calls[0]["method"])
        self.assertEqual(POLL_PATH, api.calls[0]["path"])

    def test_failed_is_terminal(self):
        self.install_api([task("failed")])
        out = pt_gateway.poll_task("KEY", TID, timeout=600)
        self.assertEqual("failed", out["status"])

    def test_cancelled_is_terminal(self):
        """即使我们不做取消，服务端仍可能返回 cancelled——必须当终态退出，不能死等。"""
        self.install_api([task("cancelled")])
        out = pt_gateway.poll_task("KEY", TID, timeout=600)
        self.assertEqual("cancelled", out["status"])


class TestPollRateLimit(GatewayTestBase):
    def test_429_honors_retry_after(self):
        """尊重服务端给的 Retry-After，而不是自顾自退避。"""
        api = self.install_api([err(429, "rate limited", {"Retry-After": "3"}),
                                task("succeeded")])
        out = pt_gateway.poll_task("KEY", TID, timeout=600)
        self.assertEqual("succeeded", out["status"])
        self.assertEqual([3.0], self.clock.sleeps)
        self.assertEqual(2, len(api.calls))

    def test_429_without_header_uses_exponential_backoff(self):
        api = self.install_api([err(429), err(429), task("succeeded")])
        pt_gateway.poll_task("KEY", TID, timeout=600)
        self.assertEqual([1.0, 2.0], self.clock.sleeps)

    def test_429_backoff_is_capped_at_60s(self):
        """退避上限 60s：否则连续 429 会等到天荒地老，任务早被判超时。"""
        script = [err(429)] * 10 + [task("succeeded")]
        self.install_api(script)
        pt_gateway.poll_task("KEY", TID, timeout=6000)
        self.assertTrue(all(s <= 60.0 for s in self.clock.sleeps),
                        f"退避超过 60s 上限：{self.clock.sleeps}")

    def test_retry_after_lowercase_header(self):
        self.install_api([err(429, "r", {"retry-after": "2.5"}), task("succeeded")])
        pt_gateway.poll_task("KEY", TID, timeout=600)
        self.assertEqual([2.5], self.clock.sleeps)

    def test_retry_after_beyond_deadline_fails_fast(self):
        """服务端要求等 999s、总超时只有 10s：不能真睡 999s 把调用方拖死，
        应立即给出明确的限流错误（不是静默超时，也不是硬等）。"""
        self.install_api([err(429, "rate limited", {"Retry-After": "999"})],
                         default=task("running"))
        with self.assertRaises(ApiError) as ctx:
            pt_gateway.poll_task("KEY", TID, timeout=10)
        self.assertEqual(429, ctx.exception.status)
        self.assertIn("限流", str(ctx.exception.message))
        self.assertEqual([], self.clock.sleeps, "明知等不到（Retry-After > 剩余超时）就不该睡")


class TestPollServerErrors(GatewayTestBase):
    def test_5xx_breaks_after_five(self):
        """连续 5 次 5xx 熔断：不无限重试（无限重试 = 卡死 + 白烧时间）。"""
        api = self.install_api([err(500)] * 8)
        with self.assertRaises(ApiError) as ctx:
            pt_gateway.poll_task("KEY", TID, timeout=600)
        self.assertIn("熔断", str(ctx.exception.message))
        self.assertEqual(5, len(api.calls), "第 5 次 5xx 就该熔断")
        self.assertEqual([1.0, 2.0, 4.0, 8.0], self.clock.sleeps)

    def test_5xx_recovers_without_breaking(self):
        """抖动（2 次 5xx 后恢复）不能被误熔断。"""
        api = self.install_api([err(503), err(502), task("succeeded")])
        out = pt_gateway.poll_task("KEY", TID, timeout=600)
        self.assertEqual("succeeded", out["status"])
        self.assertEqual(3, len(api.calls))

    def test_consecutive_counter_resets_on_success(self):
        """成功一次就把 5xx 计数清零——否则累计几次抖动后一次真故障就误熔断。"""
        api = self.install_api([err(500), err(500), task("running"),
                                err(500), err(500), task("succeeded")])
        out = pt_gateway.poll_task("KEY", TID, timeout=600)
        self.assertEqual("succeeded", out["status"])
        self.assertEqual(6, len(api.calls))

    def test_4xx_propagates_immediately_without_retry(self):
        """4xx 是权限/参数问题，重试没有意义，必须立刻透传。"""
        api = self.install_api([err(404, "task not found")])
        with self.assertRaises(ApiError) as ctx:
            pt_gateway.poll_task("KEY", TID, timeout=600)
        self.assertEqual(404, ctx.exception.status)
        self.assertEqual(1, len(api.calls))
        self.assertEqual([], self.clock.sleeps, "4xx 不应退避重试")

    def test_403_propagates(self):
        """权限错误透传给上层（fail-open 语义：由 PT 服务端说了算）。"""
        api = self.install_api([err(403, "no permission")])
        with self.assertRaises(ApiError) as ctx:
            pt_gateway.poll_task("KEY", TID, timeout=600)
        self.assertEqual(403, ctx.exception.status)


class TestPollTimeout(GatewayTestBase):
    def test_timeout_raises_clear_error(self):
        self.install_api(default=task("running"))
        with self.assertRaises(ApiError) as ctx:
            pt_gateway.poll_task("KEY", TID, timeout=1)
        self.assertIn("轮询超时", str(ctx.exception.message))
        self.assertIn(TID, str(ctx.exception.message))

    def test_timeout_is_bounded_by_deadline(self):
        """超时后不再打 API：避免"已经超时了还在继续花钱"。"""
        api = self.install_api(default=task("running"))
        with self.assertRaises(ApiError):
            pt_gateway.poll_task("KEY", TID, timeout=1)
        self.assertEqual(1, len(api.calls))


class TestRetryAfterParsing(unittest.TestCase):
    """_retry_after 的解析逻辑（纯函数，不需要假时钟）。"""

    def test_parses_numeric(self):
        e = ApiError(429, "r"); e.raw = {"headers": {"Retry-After": "7"}}
        self.assertEqual(7.0, pt_gateway._retry_after(e))

    def test_parses_float(self):
        e = ApiError(429, "r"); e.raw = {"headers": {"Retry-After": "1.5"}}
        self.assertEqual(1.5, pt_gateway._retry_after(e))

    def test_non_numeric_degrades_to_none(self):
        e = ApiError(429, "r"); e.raw = {"headers": {"Retry-After": "Wed, 21 Oct 2015"}}
        self.assertIsNone(pt_gateway._retry_after(e), "HTTP-date 形式暂不支持，应退化为指数退避")

    def test_missing_raw_degrades_to_none(self):
        self.assertIsNone(pt_gateway._retry_after(ApiError(429, "r")))

    def test_error_without_headers_degrades_gracefully(self):
        """N4 已修：引擎能带出响应头；万一服务端没给 Retry-After，也要优雅退化，
        返回 None 而不是抛异常（让 poll_task 走指数退避）。"""
        self.assertIsNone(pt_gateway._retry_after(ApiError(429, "no retry-after header")))

    def test_headers_attribute_takes_priority(self):
        """新路径：ApiError.headers 直接可用（不依赖 raw 兼容层）。"""
        e = ApiError(429, "r", headers={"Retry-After": "9"})
        self.assertEqual(9.0, pt_gateway._retry_after(e))

    def test_case_insensitive_header_name(self):
        """HTTP 头名大小写不敏感：服务端发 RETRY-AFTER 也得认。"""
        e = ApiError(429, "r", headers={"RETRY-AFTER": "4"})
        self.assertEqual(4.0, pt_gateway._retry_after(e))


class TestEngineExposesHeaders(unittest.TestCase):
    """N4 回归：真引擎 pt_video._api_call 遇到 429 时，必须把响应头带进 ApiError。

    修复前 ApiError 只有 (status, message)，上层 _retry_after 永远拿不到 Retry-After，
    真机限流时只能盲目指数退避。这个用例直接在 urllib 层造一个带头的 HTTPError，
    验证从「HTTP 响应 → ApiError → _retry_after」整条链路是通的。
    """

    def test_api_call_attaches_headers_and_retry_after_is_parseable(self):
        import io
        import urllib.error
        from email.message import Message
        import pt_video

        msg = Message()
        msg["Retry-After"] = "3"
        msg["X-Request-Id"] = "req-abc"
        http_err = urllib.error.HTTPError(
            "https://api.powertokens.ai/v1/videos/x", 429, "Too Many Requests",
            msg, io.BytesIO(b'{"error":"rate limited"}'),
        )

        orig = pt_video.urllib.request.urlopen

        def _raise(*a, **kw):
            raise http_err

        pt_video.urllib.request.urlopen = _raise
        try:
            with self.assertRaises(ApiError) as ctx:
                pt_video._api_call("GET", "/v1/videos/x", "KEY")
        finally:
            pt_video.urllib.request.urlopen = orig

        e = ctx.exception
        self.assertEqual(429, e.status)
        self.assertEqual("3", e.headers.get("Retry-After"))
        self.assertEqual("req-abc", e.headers.get("X-Request-Id"))
        self.assertEqual(3.0, pt_gateway._retry_after(e),
                         "引擎已带出 Retry-After，上层必须能解析（N4）")

    def test_api_call_without_retry_after_still_raises_cleanly(self):
        """没有 Retry-After 的正常错误：headers 为空、_retry_after 为 None，链路不炸。"""
        import io
        import urllib.error
        from email.message import Message
        import pt_video

        http_err = urllib.error.HTTPError(
            "https://api.powertokens.ai/v1/videos/x", 500, "Server Error",
            Message(), io.BytesIO(b'{"error":"boom"}'),
        )
        orig = pt_video.urllib.request.urlopen
        pt_video.urllib.request.urlopen = lambda *a, **kw: (_ for _ in ()).throw(http_err)
        try:
            with self.assertRaises(ApiError) as ctx:
                pt_video._api_call("GET", "/v1/videos/x", "KEY")
        finally:
            pt_video.urllib.request.urlopen = orig

        self.assertEqual(500, ctx.exception.status)
        self.assertIsNone(pt_gateway._retry_after(ctx.exception))


if __name__ == "__main__":
    unittest.main()
