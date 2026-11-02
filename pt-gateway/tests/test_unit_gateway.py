#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unit：TaskGateway 路由与五种 kind 的行为契约（打桩，不联网不花钱）。

这是整个网关的主干测试，重点覆盖三类问题：
  1. 路由对不对（kind -> 端点 -> 请求体 -> 结果字段）
  2. 历史 bug 不复发（image/audio 常量缺失导致 NameError；audio 端点误判）
  3. 架构决策没被改回去（fail-open：不做客户端权限拦截；不提供 cancel）
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (  # noqa: E402
    pt_gateway, GatewayTestBase, ApiError, CATALOG_JSON, assert_schema_keys,
)

IMAGE_OK = {"data": [{"url": "http://cdn/1.png"}]}
AUDIO_OK = {"data": {"audio": "http://cdn/1.mp3", "status": 2}}
CHAT_OK = {"choices": [{"message": {"content": "你好"}}], "usage": {"total_tokens": 12}}
VIDEO_DONE = {"id": "t1", "status": "succeeded", "metadata": {"url": "http://cdn/1.mp4"}}


class GatewayRoutingTestBase(GatewayTestBase):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def isolated_registry(self, models=None):
        """隔离的注册表：避免读到 ~/.config 下可能存在的真实 registry.json。"""
        reg = pt_gateway.ModelRegistry(path=str(self.tmp / "registry.json")).load()
        reg.data["models"] = models or {}
        return reg

    def gw(self, models=None, key="TEST-KEY"):
        return pt_gateway.TaskGateway(key=key, registry=self.isolated_registry(models))

    def alive(self, kind, mid="x-model"):
        return {mid: {"kind": kind, "enabled": True, "delisted": False, "in_models": True}}


class TestKindRouting(GatewayRoutingTestBase):
    def test_unknown_kind_rejected(self):
        api = self.install_api([])
        with self.assertRaises(ApiError) as ctx:
            self.gw().media_generate(kind="hologram", prompt="p")
        self.assertEqual(0, ctx.exception.status)
        self.assertIn("未知 kind", str(ctx.exception.message))
        self.assertEqual([], api.calls, "校验失败不应发请求（省一次调用）")

    def test_kind_inferred_from_model(self):
        """不传 kind 时从模型 ID 反推（agent 常常只知道模型名）。"""
        self.install_api([IMAGE_OK])
        self.install_submit("t1")
        r = self.gw().media_generate(model="seedream-5-0-260128", prompt="p")
        self.assertEqual("image", r["kind"])

    def test_missing_key_is_401_before_any_call(self):
        api = self.install_api([])
        with self.assertRaises(ApiError) as ctx:
            pt_gateway.TaskGateway(key=None).media_generate(kind="chat", prompt="p")
        self.assertEqual(401, ctx.exception.status)
        self.assertEqual([], api.calls)

    def test_empty_kind_and_auto_model_rejected(self):
        with self.assertRaises(ApiError):
            self.gw().media_generate(kind=None, model="auto", prompt="p")


class TestImageSync(GatewayRoutingTestBase):
    def test_uses_image_endpoint_and_returns_urls(self):
        """回归 G8：IMAGE_ENDPOINT 曾只有使用没有定义，带 key 调用直接 NameError。"""
        api = self.install_api([IMAGE_OK])
        r = self.gw().media_generate(kind="image", model="seedream-5-0-260128", prompt="a dog")
        self.assertEqual(["http://cdn/1.png"], r["urls"])
        self.assertEqual("sync", r["execution"])
        self.assertTrue(r["ok"])
        self.assertEqual("POST", api.calls[0]["method"])
        self.assertEqual(pt_gateway.IMAGE_ENDPOINT, api.calls[0]["path"])

    def test_body_shape(self):
        api = self.install_api([IMAGE_OK])
        self.gw().media_generate(kind="image", model="seedream-5-0-260128",
                                 prompt="a dog", aspect_ratio="16:9", n=2)
        b = api.last_body
        self.assertEqual({"model": "seedream-5-0-260128", "prompt": "a dog",
                          "aspect_ratio": "16:9", "n": 2}, b)

    def test_optional_params_omitted_when_absent(self):
        api = self.install_api([IMAGE_OK])
        self.gw().media_generate(kind="image", model="seedream-5-0-260128", prompt="p")
        self.assertNotIn("n", api.last_body)
        self.assertNotIn("aspect_ratio", api.last_body)

    def test_empty_url_list_is_not_ok(self):
        self.install_api([{"data": []}])
        r = self.gw().media_generate(kind="image", model="seedream-5-0-260128", prompt="p")
        self.assertEqual([], r["urls"])


class TestAudioSync(GatewayRoutingTestBase):
    def test_uses_openai_speech_endpoint(self):
        """回归：端点曾误判为 /ali/...（实测 404/500）。"""
        api = self.install_api([AUDIO_OK])
        r = self.gw().media_generate(kind="audio", model="speech-2.8-turbo", prompt="你好")
        self.assertEqual(pt_gateway.AUDIO_ENDPOINT, api.calls[0]["path"])
        self.assertEqual("/v1/audio/speech", api.calls[0]["path"])
        self.assertEqual("http://cdn/1.mp3", r["url"])
        self.assertEqual("sync", r["execution"])

    def test_voice_always_sent(self):
        """voice 必填，缺失会报 voice is required——网关必须有默认值兜底。"""
        api = self.install_api([AUDIO_OK])
        r = self.gw().media_generate(kind="audio", model="speech-2.8-turbo", prompt="你好")
        self.assertEqual(pt_gateway.DEFAULT_VOICE, api.last_body["voice"])
        self.assertEqual("alloy", r["voice"])

    def test_custom_voice(self):
        api = self.install_api([AUDIO_OK])
        self.gw().media_generate(kind="audio", model="speech-2.8-turbo",
                                 prompt="你好", voice="nova")
        self.assertEqual("nova", api.last_body["voice"])

    def test_input_field_is_prompt(self):
        api = self.install_api([AUDIO_OK])
        self.gw().media_generate(kind="audio", model="speech-2.8-turbo", prompt="你好呀")
        self.assertEqual("你好呀", api.last_body["input"])

    def test_params_merged(self):
        api = self.install_api([AUDIO_OK])
        self.gw().media_generate(kind="audio", model="speech-2.8-turbo", prompt="p",
                                 params={"speed": 1.2})
        self.assertEqual(1.2, api.last_body["speed"])

    def test_url_fallback_data_url(self):
        self.install_api([{"data": {"url": "http://cdn/fallback.mp3"}}])
        r = self.gw().media_generate(kind="audio", model="speech-2.8-turbo", prompt="p")
        self.assertEqual("http://cdn/fallback.mp3", r["url"])

    def test_url_fallback_output_url(self):
        self.install_api([{"output": {"url": "http://cdn/out.mp3"}}])
        r = self.gw().media_generate(kind="audio", model="speech-2.8-turbo", prompt="p")
        self.assertEqual("http://cdn/out.mp3", r["url"])

    def test_no_url_is_none_not_crash(self):
        self.install_api([{}])
        r = self.gw().media_generate(kind="audio", model="speech-2.8-turbo", prompt="p")
        self.assertIsNone(r["url"])


class TestChatSync(GatewayRoutingTestBase):
    def test_prompt_becomes_single_user_message(self):
        api = self.install_api([CHAT_OK])
        r = self.gw().media_generate(kind="chat", model="qwen3.8-flash", prompt="你好")
        self.assertEqual(pt_gateway.CHAT_ENDPOINT, api.calls[0]["path"])
        self.assertEqual([{"role": "user", "content": "你好"}], api.last_body["messages"])
        self.assertEqual("你好", r["text"])

    def test_system_prepended(self):
        api = self.install_api([CHAT_OK])
        self.gw().media_generate(kind="chat", model="qwen3.8-flash", prompt="q",
                                 system="你是个助手")
        self.assertEqual([{"role": "system", "content": "你是个助手"},
                          {"role": "user", "content": "q"}], api.last_body["messages"])

    def test_explicit_messages_win_over_prompt(self):
        api = self.install_api([CHAT_OK])
        msgs = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        self.gw().media_generate(kind="chat", model="qwen3.8-flash", prompt="ignored",
                                 messages=msgs)
        self.assertEqual(msgs, api.last_body["messages"])

    def test_sampling_params_only_when_provided(self):
        api = self.install_api([CHAT_OK])
        self.gw().media_generate(kind="chat", model="qwen3.8-flash", prompt="p")
        self.assertNotIn("temperature", api.last_body)
        self.assertNotIn("max_tokens", api.last_body)

        self.gw().media_generate(kind="chat", model="qwen3.8-flash", prompt="p",
                                 temperature=0.2, max_tokens=64)
        self.assertEqual(0.2, api.last_body["temperature"])
        self.assertEqual(64, api.last_body["max_tokens"])

    def test_temperature_zero_is_kept(self):
        """temperature=0 是合法值，不能被 `if temperature` 吃掉。"""
        api = self.install_api([CHAT_OK])
        self.gw().media_generate(kind="chat", model="qwen3.8-flash", prompt="p", temperature=0)
        self.assertEqual(0, api.last_body["temperature"])

    def test_text_fallback_to_output_text(self):
        self.install_api([{"output": {"text": "来自 output"}}])
        r = self.gw().media_generate(kind="chat", model="qwen3.8-flash", prompt="p")
        self.assertEqual("来自 output", r["text"])

    def test_empty_text_is_empty_string_not_crash(self):
        self.install_api([{"choices": []}])
        r = self.gw().media_generate(kind="chat", model="qwen3.8-flash", prompt="p")
        self.assertEqual("", r["text"])


class TestVideoAsync(GatewayRoutingTestBase):
    def test_submit_only_returns_task_id(self):
        self.install_submit("task-123")
        r = self.gw().media_generate(kind="video", model="wan3.0-video", prompt="a dog")
        self.assertEqual("task-123", r["task_id"])
        self.assertEqual("async", r["execution"])
        self.assertEqual("wan3.0-video", r["model"])

    def test_wait_polls_to_terminal(self):
        api = self.install_api([VIDEO_DONE])
        self.install_submit("task-123")
        r = self.gw().media_generate(kind="video", model="wan3.0-video",
                                     prompt="a dog", wait=True)
        self.assertEqual("SUCCEEDED", r["state"])
        self.assertEqual("http://cdn/1.mp4", r["url"])
        self.assertEqual("/v1/videos/task-123", api.calls[0]["path"])

    def test_submit_body_is_built_for_video(self):
        self.install_api([VIDEO_DONE])
        self.install_submit("t1")
        self.gw().media_generate(kind="video", model="wan3.0-video", prompt="a dog",
                                 duration=5, resolution="480p", wait=True)
        b = self.submitted_body
        self.assertEqual("wan3.0-video", b["model"])
        self.assertEqual("a dog", b["prompt"])
        self.assertEqual("480P", b["size"])
        self.assertEqual("5", b["seconds"])

    def test_submit_rejects_non_video_model(self):
        self.install_api([])
        with self.assertRaises(ApiError) as ctx:
            self.gw().submit(model="seedream-5-0-260128", prompt="p")
        self.assertIn("非异步视频类", str(ctx.exception.message))

    def test_get_status_normalizes(self):
        self.install_api([VIDEO_DONE])
        r = self.gw().get_status("t1")
        self.assertEqual("SUCCEEDED", r["state"])
        self.assertEqual("http://cdn/1.mp4", r["url"])

    def test_wait_swallows_errors_into_failed(self):
        """wait() 语义：不抛，把失败归一化为 FAILED（调用方不用写 try/except）。"""
        self.install_api([], default=ApiError(500, "server exploded"))
        r = self.gw().wait("t1", timeout=5)
        self.assertEqual("FAILED", r["state"])
        self.assertIsNone(r["url"])
        self.assertIn("error", r)


class TestSeedanceRouting(GatewayRoutingTestBase):
    def test_asset_url_enforced_end_to_end(self):
        """seedance 图生视频素材必须 asset://（引擎会拒 http）。"""
        self.install_api([])
        with self.assertRaises(ApiError) as ctx:
            self.gw().media_generate(kind="seedance", model="seedance-1-5-pro-251215",
                                     prompt="p", ref_image="http://x/1.png")
        self.assertIn("asset://", str(ctx.exception.message))

    def test_asset_url_accepted(self):
        self.install_api([VIDEO_DONE])
        self.install_submit("t9")
        r = self.gw().media_generate(kind="seedance", model="seedance-1-5-pro-251215",
                                     prompt="p", ref_image="asset://abc", wait=True)
        self.assertEqual("seedance", r["kind"])
        self.assertEqual("SUCCEEDED", r["state"])


class TestFailOpen(GatewayRoutingTestBase):
    """架构决策：/v1/models 是全量清单，不是权限视图 -> 不做客户端拦截。"""

    def test_no_client_side_permission_gate(self):
        """模型不在注册表里也必须放行，由服务端说了算。"""
        api = self.install_api([CHAT_OK])
        r = self.gw(models={}).media_generate(kind="chat", model="some-unlisted-model",
                                              prompt="p")
        self.assertTrue(r["ok"], "不应在客户端拦截未登记模型")

    def test_server_403_is_propagated_not_swallowed(self):
        """服务端报无权限时，必须原样透传（不能假装成功，也不能换成别的错）。"""
        self.install_api([ApiError(403, "no permission for this model")])
        with self.assertRaises(ApiError) as ctx:
            self.gw(models={}).media_generate(kind="chat", model="restricted-model", prompt="p")
        self.assertEqual(403, ctx.exception.status)
        self.assertIn("no permission", str(ctx.exception.message))

    def test_server_404_is_propagated(self):
        self.install_api([ApiError(404, "model not found")])
        with self.assertRaises(ApiError) as ctx:
            self.gw(models={}).media_generate(kind="chat", model="ghost-model", prompt="p")
        self.assertEqual(404, ctx.exception.status)


class TestModelResolution(GatewayRoutingTestBase):
    def test_explicit_model_wins(self):
        gw = self.gw(models=self.alive("chat", "reg-model"))
        self.assertEqual("explicit", gw._resolve_model("chat", "explicit"))

    def test_registry_beats_catalog(self):
        gw = self.gw(models=self.alive("chat", "reg-model"))
        self.assertEqual("reg-model", gw._resolve_model("chat", "auto"))

    def test_falls_back_to_catalog_when_registry_empty(self):
        gw = self.gw(models={})
        picked = gw._resolve_model("video", "auto")
        self.assertEqual("video", pt_gateway.Catalog().entry(picked).get("kind"))

    def test_raises_when_no_candidate_anywhere(self):
        empty_cat = pt_gateway.Catalog(path=str(self.tmp / "no-catalog.json"))
        gw = pt_gateway.TaskGateway(key="K", registry=self.isolated_registry({}),
                                    catalog=empty_cat)
        with self.assertRaises(ApiError) as ctx:
            gw._resolve_model("chat", "auto")
        self.assertIn("无法为 kind", str(ctx.exception.message))


class TestListCapabilities(GatewayRoutingTestBase):
    def test_merges_registry_flags(self):
        gw = self.gw(models={"wan3.0-video": {"kind": "video", "enabled": True,
                                              "delisted": False, "in_models": True},
                             "gone-model": {"kind": "video", "enabled": False,
                                            "delisted": True, "in_models": False}})
        cap = gw.list_capabilities("video")
        assert_schema_keys(self, cap, ["kinds", "models"], "list_capabilities")
        e = cap["models"]["wan3.0-video"]
        self.assertTrue(e["registry_enabled"])
        self.assertFalse(e["delisted"])
        self.assertTrue(e["in_models"])

    def test_untracked_model_flags_are_none(self):
        """没同步过的模型状态应为 None，而不是 False（避免被误判为下架）。"""
        gw = self.gw(models={})
        cap = gw.list_capabilities("chat")
        any_chat = next(iter(cap["models"].values()))
        self.assertIsNone(any_chat["registry_enabled"])
        self.assertIsNone(any_chat["in_models"])

    def test_filter_returns_only_requested_kind(self):
        cap = self.gw().list_capabilities("audio")
        self.assertEqual(["audio"], list(cap["kinds"].keys()))
        for e in cap["models"].values():
            self.assertEqual("audio", e["kind"])


if __name__ == "__main__":
    unittest.main()
