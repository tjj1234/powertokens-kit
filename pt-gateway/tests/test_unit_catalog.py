#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unit：Catalog（能力目录）——加载、过滤、健壮性，以及作为"交付物"的数据完整性。

catalog.json 是给 agent 看的"模型档案"。它不是代码，但和代码一样会因手改而腐化：
本文件的 TestCatalogDataIntegrity 就是它的 CI 守门员——
缺字段、kind 越界、端点写错（比如 audio 仍写着已废弃的 /ali/...）都会立刻红。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (  # noqa: E402
    pt_gateway, GatewayTestBase, KNOWN_KINDS, ASYNC_KINDS, SYNC_KINDS, CATALOG_JSON,
    TIERS,
)

EXPECTED_EXECUTION = {k: ("async" if k in ASYNC_KINDS else "sync") for k in KNOWN_KINDS}


class TestCatalogLoad(GatewayTestBase):
    def test_missing_file_yields_empty_catalog(self):
        c = pt_gateway.Catalog(path=str(Path(self.tmpdir()) / "nope.json"))
        self.assertEqual({}, c.models())

    def test_corrupt_json_does_not_crash(self):
        """目录坏了要能降级成空目录（能力查询失败不该让整个网关起不来）。"""
        d = self.tmpdir()
        p = Path(d) / "bad.json"
        p.write_text("{oops", encoding="utf-8")
        c = pt_gateway.Catalog(path=str(p))
        self.assertEqual({}, c.models())
        self.assertEqual({}, c.kinds())

    def test_env_override(self):
        d = self.tmpdir()
        p = Path(d) / "c.json"
        p.write_text(json.dumps({"kinds": {}, "models": {"m1": {"kind": "chat"}}}),
                     encoding="utf-8")
        old = os.environ.get("PT_GATEWAY_CATALOG")
        os.environ["PT_GATEWAY_CATALOG"] = str(p)
        try:
            c = pt_gateway.Catalog()
            self.assertIn("m1", c.models())
        finally:
            if old is None:
                os.environ.pop("PT_GATEWAY_CATALOG", None)
            else:
                os.environ["PT_GATEWAY_CATALOG"] = old

    def tmpdir(self):
        if not hasattr(self, "_tmpd"):
            self._tmpd = tempfile.TemporaryDirectory()
            self.addCleanup(self._tmpd.cleanup)
        return self._tmpd.name


class TestCatalogApi(unittest.TestCase):
    """用真实 catalog.json 验证查询 API（只读，不联网）。"""

    @classmethod
    def setUpClass(cls):
        cls.cat = pt_gateway.Catalog()

    def test_has_all_kinds(self):
        self.assertEqual(set(KNOWN_KINDS), set(self.cat.kinds().keys()))

    def test_filter_by_kind(self):
        vids = self.cat.models("video")
        self.assertTrue(vids)
        for mid, e in vids.items():
            self.assertEqual("video", e["kind"], mid)

    def test_entry_and_kind_entry(self):
        any_model = next(iter(self.cat.models()))
        self.assertTrue(self.cat.entry(any_model))
        self.assertEqual({}, self.cat.entry("__not_exist__"))
        self.assertEqual({}, self.cat.kind_entry("__not_exist__"))

    def test_capabilities_shape(self):
        cap = self.cat.capabilities()
        self.assertEqual({"kinds", "models"}, set(cap.keys()))
        self.assertEqual(set(KNOWN_KINDS), set(cap["kinds"].keys()))

    def test_capabilities_filter_narrows_both(self):
        cap = self.cat.capabilities("audio")
        self.assertEqual(["audio"], list(cap["kinds"].keys()))
        for e in cap["models"].values():
            self.assertEqual("audio", e["kind"])


class TestCatalogDataIntegrity(unittest.TestCase):
    """catalog.json 数据完整性（交付物守卫）。

    这些断言防的是"手改目录改坏了但没人发现"：
    agent 读到缺 execution 的条目会不知道该同步还是该轮询，
    读到错误的 submit 端点会直接拿去发请求。
    """

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(CATALOG_JSON.read_text(encoding="utf-8"))
        cls.models = cls.data["models"]

    def test_not_empty(self):
        self.assertGreater(len(self.models), 50, "目录条目异常少，可能误清空")

    def test_every_model_has_known_kind(self):
        bad = {m: e.get("kind") for m, e in self.models.items()
               if e.get("kind") not in KNOWN_KINDS}
        self.assertEqual({}, bad, f"存在 kind 越界的条目：{bad}")

    def test_every_model_has_execution(self):
        missing = [m for m, e in self.models.items() if not e.get("execution")]
        self.assertEqual([], missing, f"缺 execution（agent 无法判断同步/异步）：{missing}")

    def test_execution_matches_kind(self):
        bad = []
        for m, e in self.models.items():
            want = EXPECTED_EXECUTION.get(e.get("kind"))
            if want and e.get("execution") != want:
                bad.append((m, e.get("kind"), e.get("execution"), want))
        self.assertEqual([], bad, f"execution 与 kind 不符：{bad}")

    def test_every_model_has_submit_endpoint(self):
        missing = [m for m, e in self.models.items() if not e.get("submit")]
        self.assertEqual([], missing, f"缺 submit 端点：{missing}")

    def test_async_models_have_poll_endpoint(self):
        missing = [m for m, e in self.models.items()
                   if e.get("kind") in ASYNC_KINDS and not e.get("poll")]
        self.assertEqual([], missing, f"异步模型缺 poll 端点：{missing}")

    def test_sync_models_have_no_poll(self):
        bad = [m for m, e in self.models.items()
               if e.get("kind") in SYNC_KINDS and e.get("poll")]
        self.assertEqual([], bad, f"同步模型不该有 poll：{bad}")

    def test_audio_endpoint_is_not_the_deprecated_ali_path(self):
        """回归：/ali/api/v1/services/aigc/... 实测 404/500，早已废弃；
        目录中不得再出现该端点，否则 agent 会照着发请求然后一脸懵。"""
        bad = [m for m, e in self.models.items()
               if e.get("kind") == "audio" and "/ali/" in (e.get("submit") or "")]
        self.assertEqual([], bad, f"audio 仍指向已废弃端点：{bad}")

    def test_audio_submit_is_openai_compatible(self):
        for m, e in self.models.items():
            if e.get("kind") == "audio":
                self.assertEqual("/v1/audio/speech", e.get("submit"), m)

    def test_chat_submit_is_chat_completions(self):
        for m, e in self.models.items():
            if e.get("kind") == "chat":
                self.assertEqual("/v1/chat/completions", e.get("submit"), m)

    def test_video_and_seedance_submit_is_videos(self):
        for m, e in self.models.items():
            if e.get("kind") in ("video", "seedance"):
                self.assertEqual("/v1/videos", e.get("submit"), m)

    def test_note_points_to_current_audio_endpoint(self):
        """note 必须指向当前端点；若提到旧端点，只能是"已废弃"的上下文，不能是可调用的推荐。"""
        note = json.dumps(self.data.get("note") or [], ensure_ascii=False)
        self.assertIn("/v1/audio/speech", note, "顶层 note 未声明当前音频端点")
        deprecated = "/ali/api/v1/services/aigc"
        if deprecated in note:
            i = note.index(deprecated)
            ctx = note[i:i + 80]
            self.assertIn("废弃", ctx,
                          f"note 中提到旧端点但没有标注已废弃，会误导 agent：{ctx}")

    def test_schema_and_updated_present(self):
        self.assertIn("schema", self.data)
        self.assertTrue(self.data.get("updated"))


class TestKindOfFallback(GatewayTestBase):
    """_kind_of：引擎认不出时回退查目录（chat / 部分 TTS 靠这条）。"""

    def test_engine_known_kind_wins(self):
        self.assertEqual("video", pt_gateway._kind_of("wan3.0-video"))

    def test_seedance_recognized(self):
        self.assertEqual("seedance", pt_gateway._kind_of("dreamina-seedance-2-0-mini-260615"))

    def test_image_recognized(self):
        self.assertEqual("image", pt_gateway._kind_of("seedream-5-0-260128"))

    def test_fallback_to_catalog_for_chat(self):
        """chat 类模型引擎返回 unknown，必须靠目录兜底，否则 kind 判定失败。"""
        k = pt_gateway._kind_of("qwen3.8-flash")
        self.assertEqual("chat", k, "chat 模型应通过 catalog 兜底识别")

    def test_fallback_to_catalog_for_tts(self):
        k = pt_gateway._kind_of("speech-2.8-turbo")
        self.assertEqual("audio", k)

    def test_unknown_model_returns_unknown_not_crash(self):
        k = pt_gateway._kind_of("definitely-not-a-model-xyz")
        self.assertIn(k, ["unknown", None] + list(KNOWN_KINDS))


class TestTierModelReferences(unittest.TestCase):
    """真机档位（harness.TIERS）引用的模型必须在 catalog.json 中存在。

    这条是离线守门员：一旦有人把档位里的模型 ID 写错、或引用了一个
    已经被 PT 下线的模型（但目录还没清），unit 层立刻红，
    而不是等真机烧钱跑到一半才发现 403。
    """

    @classmethod
    def setUpClass(cls):
        cls.models = json.loads(CATALOG_JSON.read_text(encoding="utf-8"))["models"]

    def test_every_tier_model_exists_in_catalog(self):
        missing = []
        for tier, cfg in TIERS.items():
            for role in ("chat", "image", "audio", "video"):
                m = cfg[role]
                if m not in self.models:
                    missing.append(f"{tier}.{role}={m}")
        self.assertEqual([], missing, "档位引用了 catalog 中不存在的模型：" + ", ".join(missing))

    def test_tier_models_have_consistent_kind(self):
        """档位里 model->kind 的绑定要和 catalog 一致。

        video 角色允许 kind 为 video 或 seedance（两者都是异步出片，
        档位挑最便宜的 seedance 当视频步骤是故意的，不算错配）。
        """
        video_roles = {"video", "seedance"}
        bad = []
        for tier, cfg in TIERS.items():
            for role in ("chat", "image", "audio", "video"):
                m = cfg[role]
                e = self.models.get(m)
                if not e:
                    continue
                kind = e.get("kind")
                if role == "video":
                    if kind not in video_roles:
                        bad.append(f"{tier}.{role}={m} 实际kind={kind}")
                elif kind != role:
                    bad.append(f"{tier}.{role}={m} 实际kind={kind}")
        self.assertEqual([], bad, "档位模型 kind 与目录不符：" + ", ".join(bad))


class TestChatCapabilitiesIntegrity(unittest.TestCase):
    """chat 模型能力字段（capabilities）结构完整性守卫。

    用户要求：每个 chat 模型标清楚多模态 / 联网搜索 / 思考模式等。
    数据来源分层：
      - web_search / thinking / multimodal 均来自 PT 官方《模型能力汇总》渠道表与 §4 逐模型清单
        （official）；其中 multimodal 曾用真机探测交叉校验，但探针对 ali-qwen3 / BytePlus-seed /
        zhipu-glm-5.3-flash / deepseek-flash-v3-2 给出过假阴性（只发 base64 PNG data URI，
        部分渠道不接受该格式），最终以官方文档为准。
    这个测试保证——字段存在、维度齐全、取值合法、参数提示齐全；改坏即红。
    """

    REQUIRED_DIMS = ("multimodal", "channel", "web_search", "web_search_param",
                     "thinking", "thinking_param", "tools", "streaming")
    # 取值域：已支持 / 不支持 / 需显式传参且依赖上游白名单 / 未知
    CAP_VALUES = (True, False, "conditional", None)
    VALID_CHANNELS = ("ali", "byteplus", "zhipu", "deepseek", "minimax", "mimo")
    VALID_SOURCE = ("configured", "verified", "official", "official+verified",
                    "verified(unmapped)")
    # 官方 §4 未列入清单、因此不推断（vision 允许保持 None）的模型白名单
    MM_UNKNOWN_ALLOWED = {"qwen3-coder-plus"}

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(CATALOG_JSON.read_text(encoding="utf-8"))
        cls.chat = {m: e for m, e in cls.data["models"].items()
                    if e.get("kind") == "chat"}

    def test_every_chat_model_has_capabilities(self):
        missing = [m for m, e in self.chat.items() if not isinstance(e.get("capabilities"), dict)]
        self.assertEqual([], missing, "缺 capabilities 的 chat 模型：" + ", ".join(missing))

    def test_capabilities_has_all_dimensions(self):
        bad = []
        for m, e in self.chat.items():
            caps = e["capabilities"]
            for dim in self.REQUIRED_DIMS:
                if dim not in caps:
                    bad.append(f"{m}.{dim}")
        self.assertEqual([], bad, "capabilities 缺维度：" + ", ".join(bad))

    def test_channel_is_known(self):
        bad = [m for m, e in self.chat.items()
               if e["capabilities"].get("channel") not in self.VALID_CHANNELS]
        self.assertEqual([], bad, "chat 模型未匹配到官方渠道（能力未知）：" + ", ".join(bad))

    def test_capabilities_source_known(self):
        bad = [m for m, e in self.chat.items()
               if e["capabilities"].get("_source") not in self.VALID_SOURCE]
        self.assertEqual([], bad, "capabilities._source 非法：" + ", ".join(bad))

    def test_cap_values_within_domain(self):
        bad = []
        for m, e in self.chat.items():
            caps = e["capabilities"]
            if caps["web_search"] not in self.CAP_VALUES:
                bad.append(f"{m}.web_search={caps['web_search']}")
            if caps["thinking"] not in self.CAP_VALUES:
                bad.append(f"{m}.thinking={caps['thinking']}")
        self.assertEqual([], bad, "web_search/thinking 取值越界：" + ", ".join(bad))

    def test_param_hint_consistency(self):
        """支持/条件支持时必须有实际参数提示；明确不支持时参数必须为 None。"""
        bad = []
        for m, e in self.chat.items():
            caps = e["capabilities"]
            for dim, param in (("web_search", "web_search_param"),
                              ("thinking", "thinking_param")):
                val = caps[dim]
                hint = caps[param]
                if val in (True, "conditional") and not hint:
                    bad.append(f"{m}.{dim} 支持但缺参数提示")
                if val is False and hint is not None:
                    bad.append(f"{m}.{dim} 不支持却带了参数提示")
        self.assertEqual([], bad, "参数提示与能力声明不一致：" + ", ".join(bad))

    def test_multimodal_is_well_formed(self):
        for m, e in self.chat.items():
            mm = e["capabilities"]["multimodal"]
            self.assertIsInstance(mm, dict, m)
            for k in ("vision", "audio_in", "video_in"):
                self.assertIn(k, mm, f"{m}.multimodal 缺字段 {k}")
                if k == "vision" and mm[k] is None:
                    # 允许为 None 的唯一例外：官方 §4 未列入逐模型清单、明确"不推断"的模型
                    self.assertIn(m, self.MM_UNKNOWN_ALLOWED,
                                  f"{m}.vision=None 但不在官方未列清单白名单，必须明确标 true/false")
                else:
                    self.assertIsInstance(mm[k], bool, f"{m}.multimodal.{k} 必须为 bool")

    def test_multimodal_source_is_declared(self):
        """目录顶层应声明多模态数据的权威来源，避免 agent 误以为是网关实测保证。"""
        self.assertIn("multimodal_source", self.data, "缺失 multimodal_source 声明")
        self.assertIn("模型能力汇总", self.data["multimodal_source"])

    def test_capabilities_surfaced_by_list_capabilities(self):
        """接口（list_capabilities / capabilities 命令）必须透传 capabilities，否则等于没标。"""
        cap = pt_gateway.Catalog().capabilities("chat")
        for mid, e in cap["models"].items():
            self.assertIn("capabilities", e, f"{mid} 经接口未透传 capabilities")
            self.assertIn("web_search", e["capabilities"])
            self.assertIn("thinking", e["capabilities"])
            self.assertIn("channel", e["capabilities"])


if __name__ == "__main__":
    unittest.main()
