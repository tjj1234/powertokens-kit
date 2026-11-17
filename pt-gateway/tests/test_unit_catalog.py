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
    # A5：catalog v2 统一 9 值（2026-11-13 增 models_api）。product 仅用于顶层 auto_policy；
    # documented / inferred / configured / verified(unmapped) 的语义见 catalog.schema.json 与 README §21.1。
    VALID_SOURCE = ("official", "documented", "verified", "official+verified",
                    "inferred", "configured", "verified(unmapped)", "product",
                    "models_api")
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


class TestCatalogV2Integrity(unittest.TestCase):
    """catalog v2（schema 2）结构与 14 条决策约束的离线守门员。

    依据 docs/catalog_v2_决策锁定_20261112.md：A1 / A3 / A4 / A5 / B1 / B2
    与需求 2 / 3 / 4。只断言结构、枚举与「不得编造实测结论」，不联网、0 花费。
    与 validate_catalog.py 互补：这里是 CI 里跑的那份（run_tests.py 会收集本文件）。
    """
    KNOWN_KINDS = {"image", "video", "seedance", "audio", "chat"}
    MAIN = ("glm-4.7", "deepseek-v4-pro", "qwen3.8-flash", "seed-2-0-pro-260328",
            "MiniMax-M3", "glm-5.2", "mimo-v2.6-pro")
    ERROR_KEYS = {"rate_limited", "upstream_error", "timeout", "auth_invalid",
                  "insufficient_balance", "model_unavailable", "param_unsupported",
                  "content_filtered", "context_too_long"}
    SOURCE_9 = {"official", "documented", "verified", "official+verified",
                "inferred", "configured", "verified(unmapped)", "product", "models_api"}
    V2_SECTIONS = ("currency", "auto_policy", "errors", "capability_gaps")

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(CATALOG_JSON.read_text(encoding="utf-8"))
        cls.models = cls.data["models"]
        cls.schema = json.loads(
            (CATALOG_JSON.parent / "catalog.schema.json").read_text(encoding="utf-8"))

    # --- 需求 2：版本与格式 ----------------------------------------------
    def test_schema_is_2(self):
        self.assertEqual(2, self.data.get("schema"))

    def test_updated_is_present(self):
        self.assertTrue(self.data.get("updated"))

    def test_v2_top_level_sections(self):
        for sec in self.V2_SECTIONS:
            self.assertIsInstance(self.data.get(sec), dict, "缺顶层节 %s" % sec)

    def test_source_enum_is_exactly_nine_values(self):
        enum = set(self.schema["definitions"]["source"]["enum"])
        self.assertEqual(self.SOURCE_9, enum)
        self.assertEqual(self.SOURCE_9, set(TestChatCapabilitiesIntegrity.VALID_SOURCE))

    def test_models_api_contract_is_declared(self):
        """2026-11-13：官方 /v1/models 字段契约落地，防止 F1 口径被改回「全量清单」。"""
        c = self.data.get("models_api_contract")
        self.assertIsInstance(c, dict)
        self.assertEqual("models_api", c.get("_source"))
        self.assertIn("本 Key / 分组可见", c.get("scope", ""))
        self.assertIn("不在列表不等于下架", c.get("scope", ""))
        self.assertIn("output_modalities",
                      c["capability_rules"]["generation_vs_understanding"])
        self.assertEqual(77, c["observed"]["model_count"])
        self.assertTrue(c["observed"]["same_view_across_keys"])
        self.assertEqual(9, len(self.schema["definitions"]["source"]["enum"]))

    def test_kinds_still_five(self):
        """B1：kinds 只保留当前可调用的 5 类，缺口走 capability_gaps。"""
        self.assertEqual(self.KNOWN_KINDS, set(self.data["kinds"]))

    # --- 需求 3：AUTO 两层 -----------------------------------------------
    def test_auto_policy_source_is_product(self):
        self.assertEqual("product", self.data["auto_policy"].get("_source"))

    def test_auto_policy_purposes_min_two_and_exist(self):
        purposes = self.data["auto_policy"].get("purposes", {})
        self.assertTrue(purposes)
        for use, cands in purposes.items():
            self.assertGreaterEqual(len(cands), 2, "%s 候选不足 2 个" % use)
            missing = [c for c in cands if c not in self.models]
            self.assertEqual([], missing, "%s 引用了不存在的模型" % use)

    def test_auto_policy_purposes_span_channels(self):
        for use, cands in self.data["auto_policy"]["purposes"].items():
            channels = {self.models[c]["capabilities"].get("channel") for c in cands}
            self.assertGreaterEqual(len(channels), 2, "%s 未跨渠道" % use)

    def test_product_source_only_in_auto_policy(self):
        """A5：product 仅用于顶层 auto_policy。"""
        stray = []

        def walk(node, path):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k == "_source" and v == "product" and not path.startswith("auto_policy"):
                        stray.append(path)
                    walk(v, path + "/" + str(k))
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, path + "/[%d]" % i)

        for sec in self.V2_SECTIONS:
            walk(self.data[sec], sec)
        for mid in self.MAIN:
            for g in ("limits", "tool_calling", "web_search_detail", "routing"):
                if g in self.models[mid]:
                    walk(self.models[mid][g], "%s/%s" % (mid, g))
        self.assertEqual([], stray)

    # --- 需求 4：errors 9 类 ---------------------------------------------
    def test_errors_exactly_nine_categories(self):
        self.assertEqual(self.ERROR_KEYS, set(self.data["errors"]))

    def test_every_error_has_action_example_and_source(self):
        for k, v in self.data["errors"].items():
            self.assertIn(v.get("agent_action"),
                          ("retry", "switch_model", "stop", "compress_context"), k)
            self.assertGreaterEqual(len(v.get("examples", [])), 1, k)
            self.assertIn(v.get("_source"), self.SOURCE_9, k)
            for ex in v["examples"]:
                self.assertIn(ex.get("_source"), self.SOURCE_9, k)

    def test_generation_domain_status_semantics_present(self):
        """A.04：401/402/403 是生成域语义，账户域状态码表（无 402）不得混入。"""
        ins = self.data["errors"]["insufficient_balance"]
        statuses = [e.get("status") for e in ins["examples"]]
        self.assertIn(402, statuses)
        self.assertIn("402", json.dumps(ins, ensure_ascii=False))

    # --- P0 字段与「不编造」约束 -----------------------------------------
    def test_main_models_have_p0_groups(self):
        for mid in self.MAIN:
            for g in ("limits", "tool_calling", "routing"):
                self.assertIn(g, self.models[mid], "%s 缺 %s" % (mid, g))

    def test_web_search_detail_only_on_conditional(self):
        """A4：只给 conditional 模型；how_to_enable 从 web_search_param 原样复制。"""
        n = 0
        for mid in self.MAIN:
            e = self.models[mid]
            cond = e["capabilities"].get("web_search") == "conditional"
            has = "web_search_detail" in e
            self.assertEqual(cond, has, mid)
            if has:
                n += 1
                self.assertEqual(e["capabilities"].get("web_search_param"),
                                 e["web_search_detail"].get("how_to_enable"), mid)
        self.assertEqual(4, n)

    def test_no_fabricated_verified_results(self):
        """A3：未实测一律 null —— 防止手改目录时把 null 改成 true。"""
        for mid in self.MAIN:
            e = self.models[mid]
            self.assertIsNone(e["tool_calling"].get("verified"), mid)
            if "web_search_detail" in e:
                self.assertIsNone(e["web_search_detail"].get("works"), mid)
                self.assertIsNone(e["web_search_detail"].get("returns_sources"), mid)

    def test_routing_source_is_configured(self):
        for mid in self.MAIN:
            self.assertEqual("configured", self.models[mid]["routing"].get("_source"), mid)

    # --- B1 / 顶层其他 ---------------------------------------------------
    def test_capability_gaps_not_in_kinds(self):
        gaps = self.data["capability_gaps"]
        self.assertTrue(gaps)
        self.assertFalse(set(gaps) & set(self.data["kinds"]))
        for k, v in gaps.items():
            self.assertIs(False, v.get("in_kinds"), k)

    def test_currency_credits_per_usd(self):
        """Q6 官方答案：1 USD = 1000 credits；3 是 quota_per_unit，非换算率。"""
        cur = self.data["currency"]
        self.assertEqual(1000, cur.get("credits_per_usd"))
        self.assertIn(cur.get("_source"), self.SOURCE_9)



class TestCatalogV21Integrity(unittest.TestCase):
    """catalog v2.1（G1 / G2）离线守门员：8 处 errors 数据订正 + 新增顶层节 / Seedance 双接口。

    依据 docs/PT_项目资料总集_20261112.md §6.3 与 §2。0 联网、0 花费。
    """
    SOURCE_9 = {"official", "documented", "verified", "official+verified",
                "inferred", "configured", "verified(unmapped)", "product", "models_api"}

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(CATALOG_JSON.read_text(encoding="utf-8"))
        cls.errs = cls.data["errors"]

    def test_errors_still_exactly_nine(self):
        """retry_policy / charging 必须走顶层，不得挤进 errors 第 10 类。"""
        self.assertEqual(9, len(self.errs))
        self.assertNotIn("retry_policy", self.errs)
        self.assertNotIn("charging", self.errs)

    def test_retry_policy_present_and_well_formed(self):
        rp = self.data.get("retry_policy")
        self.assertIsInstance(rp, dict)
        self.assertIn(rp.get("_source"), self.SOURCE_9)
        self.assertNotEqual("product", rp.get("_source"))
        self.assertEqual("no_single_dedicated_signal", rp.get("signal"))
        self.assertEqual("X-Oneapi-Request-Id", rp.get("request_id_header"))
        self.assertEqual(5, len(rp.get("rules", [])))
        for rule in rp["rules"]:
            self.assertIn("observe", rule)
            self.assertIn("auto_retry", rule)
        # 关键：流中途失败不可自动重试
        mid = [x for x in rp["rules"] if "finish_reason=error" in x["observe"]]
        self.assertEqual(1, len(mid))
        self.assertFalse(mid[0]["auto_retry"])
        self.assertTrue(mid[0]["charged"])

    def test_retry_policy_rate_limit(self):
        rl = self.data["retry_policy"].get("rate_limit", {})
        self.assertEqual("model_rate_limited", rl.get("code"))
        self.assertEqual("always_present", rl.get("retry_after"))
        self.assertEqual(5, rl.get("gateway_default_s"))

    def test_charging_split_by_path(self):
        """C6：异步失败全额退 / 同步流中途按量扣费，不能一律 false。"""
        ch = self.data.get("charging")
        self.assertIsInstance(ch, dict)
        self.assertIn(ch.get("_source"), self.SOURCE_9)
        a, s = ch["async_task"], ch["sync_chat_stream"]
        self.assertIs(False, a.get("charged_on_failure"))
        self.assertIs(False, a.get("charged_on_timeout"))
        self.assertIs(True, s.get("charged_on_midstream_failure"))
        self.assertEqual(200, s.get("http_status"))
        self.assertEqual(["error", "timeout", "abort"], s.get("finish_reason"))
        self.assertEqual("success", s.get("log_status"))

    def test_seedance_interface_variants(self):
        iv = self.data["kinds"]["seedance"].get("interface_variants")
        self.assertIsInstance(iv, dict)
        self.assertEqual("openai_standard", iv.get("active"))
        self.assertIn("/v1/videos", iv["openai_standard"]["submit"])
        self.assertIn("/byteplus/api/v3/", iv["vendor_compatible"]["submit"])
        self.assertIs(True, iv.get("agreement_required_both"))
        # 字段名必须避开「透传」二字
        self.assertNotIn("透传", json.dumps(iv, ensure_ascii=False).split("note")[0])

    def test_seedance_result_persistence_and_c4_conflict(self):
        rpv = self.data["kinds"]["seedance"].get("result_persistence")
        self.assertIsInstance(rpv, dict)
        self.assertEqual("permanent", rpv["openai_standard"]["persistence"])
        self.assertEqual("24h", rpv["vendor_compatible"]["persistence"])
        self.assertIs(True, rpv["vendor_compatible"]["force_download_on_success"])
        self.assertIn("C4", rpv.get("note", ""))

    def test_g1_error_status_corrections(self):
        self.assertEqual([502, 503], self.errs["upstream_error"]["http_status"])
        self.assertEqual([500], self.errs["timeout"]["http_status"])
        self.assertNotIn("Q8 未答", self.errs["timeout"]["notes"])
        self.assertEqual([400], self.errs["param_unsupported"]["http_status"])
        self.assertEqual([400], self.errs["context_too_long"]["http_status"])
        self.assertEqual([403, 503], self.errs["model_unavailable"]["http_status"])
        self.assertIn("作废", self.errs["insufficient_balance"]["examples"][1]["note"])
        self.assertIn("token_invalid", self.errs["auth_invalid"]["notes"])
        self.assertIn("model_rate_limited", self.errs["rate_limited"]["notes"])

    def test_new_nodes_sources_are_valid(self):
        bad = []

        def walk(n, p):
            if isinstance(n, dict):
                for k, v in n.items():
                    if k == "_source" and v not in self.SOURCE_9:
                        bad.append("%s=%r" % (p, v))
                    if k == "_source" and v == "product":
                        bad.append("product@" + p)
                    walk(v, p + "/" + k)
            elif isinstance(n, list):
                for i, v in enumerate(n):
                    walk(v, p + "/[%d]" % i)

        for sec in ("retry_policy", "charging"):
            walk(self.data[sec], sec)
        for sec in ("interface_variants", "result_persistence"):
            walk(self.data["kinds"]["seedance"][sec], "seedance/" + sec)
        self.assertEqual([], bad)

    def test_currency_c3_note(self):
        self.assertEqual(1000, self.data["currency"]["credits_per_usd"])
        self.assertIn("C3", self.data["currency"]["notes"])



class TestLiveFindingsV21(unittest.TestCase):
    """2026-11-12 真机实测结论的离线固化（防止把 P0 证据改回书面口径）。"""

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(CATALOG_JSON.read_text(encoding="utf-8"))

    def test_current_402_sample_is_verified(self):
        ex = self.data["errors"]["insufficient_balance"]["examples"][-1]
        self.assertEqual(402, ex["status"])
        self.assertEqual("verified", ex["_source"])
        self.assertIn("pre_consume_token_quota_failed", ex["note"])

    def test_image_sync_observed_24h(self):
        o = self.data["kinds"]["seedance"]["result_persistence"]["image_sync_observed"]
        self.assertEqual("verified", o["_source"])
        self.assertEqual(86415, o["ttl_seconds"])
        self.assertEqual(24.0, o["ttl_hours"])
        self.assertEqual(["Expires", "OSSAccessKeyId", "Signature"], o["signed_query_keys"])
        self.assertIn("aliyuncs.com", o["url_host"])

    def test_no_secret_in_catalog(self):
        """catalog 不得含任何签名值 / 完整签名 URL / 48 位 Key 形态串。
        用通用形态检查，不在源码里硬编码任何真实密钥片段。"""
        import re
        raw = json.dumps(self.data, ensure_ascii=False)
        self.assertIsNone(re.search(r"(?:OSSAccessKeyId|Signature|Expires)=", raw),
                          "catalog 泄漏了签名查询串实值")
        self.assertIsNone(re.search(r"https?://\S+\?\S*Signature", raw),
                          "catalog 泄漏了完整签名 URL")
        self.assertIsNone(re.search(r"\b[A-Za-z0-9]{48}\b", raw),
                          "catalog 泄漏了 API Key 形态串")

    def test_default_strategy_force_download(self):
        s = self.data["kinds"]["seedance"]["result_persistence"]["default_strategy"]
        self.assertIn("成功即下载", s)
        self.assertIn("不承诺永久", s)

    def test_request_id_success_body_field(self):
        rp = self.data["retry_policy"]
        self.assertEqual("id", rp["request_id_success_body_field"])
        self.assertEqual("verified", rp["request_id_observed"]["_source"])


class TestCatalogV080(unittest.TestCase):
    """0.8.0：补充需求 P0 —— 5 种用途 / limits 子键 / 无官方来源不编数值。"""

    @classmethod
    def setUpClass(cls):
        cls.cat = json.loads(CATALOG_JSON.read_text(encoding="utf-8"))

    def test_purposes_cover_five_uses(self):
        self.assertEqual({"general", "agent", "search", "long_report", "vision"},
                         set(self.cat["auto_policy"]["purposes"]))

    def test_every_purpose_crosses_channels(self):
        for use, cands in self.cat["auto_policy"]["purposes"].items():
            channels = {self.cat["models"][c]["capabilities"]["channel"] for c in cands}
            self.assertGreaterEqual(len(channels), 2, use)

    def test_vision_candidates_actually_declare_vision(self):
        for mid in self.cat["auto_policy"]["purposes"]["vision"]:
            self.assertIs(True,
                          self.cat["models"][mid]["capabilities"]["multimodal"]["vision"], mid)

    def test_limits_filled_from_models_api_top_provider(self):
        """0.8.5：PT 上线 top_provider 官方上下限字段，按实测补值并标 models_api。"""
        probe = self.cat["models_api_contract"]["limits_probe"]
        self.assertEqual(31, probe["limit_like_field_carriers"])
        self.assertTrue(probe["has_max_context_tokens"])
        self.assertTrue(probe["has_max_output_tokens"])
        self.assertEqual("top_provider.context_length / top_provider.max_completion_tokens",
                         probe["limits_field_path"])
        self.assertTrue(probe["top_level_context_length_consistent"])
        # 7 个主力：数值逐一对应 2026-11-17 实测
        expected = {
            "glm-4.7": (204800, 131072),
            "deepseek-v4-pro": (1048576, 393216),
            "qwen3.8-flash": (1000000, 131072),
            "seed-2-0-pro-260328": (262144, 131072),
            "MiniMax-M3": (1048576, 512000),
            "glm-5.2": (1048576, 943718),
            "mimo-v2.6-pro": (1050000, 131072),
        }
        for mid, (ctx, out) in expected.items():
            lim = self.cat["models"][mid]["limits"]
            self.assertEqual(ctx, lim["max_context_tokens"], mid)
            self.assertEqual(out, lim["max_output_tokens"], mid)
            self.assertEqual("models_api", lim["_source"], mid)
            self.assertEqual("2026-11-17", lim["_checked_at"], mid)
        # 全部 models_api 来源的 limits 恰好 30 个，值均为正整数
        api_lims = [(mid, e["limits"]) for mid, e in self.cat["models"].items()
                    if isinstance(e.get("limits"), dict)
                    and e["limits"].get("_source") == "models_api"]
        self.assertEqual(30, len(api_lims))
        for mid, lim in api_lims:
            self.assertIsInstance(lim["max_context_tokens"], int, mid)
            self.assertGreater(lim["max_context_tokens"], 0, mid)
            self.assertIsInstance(lim["max_output_tokens"], int, mid)
            self.assertGreater(lim["max_output_tokens"], 0, mid)

    def test_live_billing_observations(self):
        """0.8.1 真机观测：只登记证据与冲突，不擅自覆盖已锁定单价。"""
        obs = self.cat["live_billing_observations"]
        self.assertEqual("verified", obs["_source"])
        self.assertEqual(1000, obs["credits_per_usd"])
        c0 = obs["cases"][0]
        self.assertEqual("wan3.0-video", c0["model"])
        self.assertEqual(500, c0["charged_quota"])
        self.assertEqual(500, c0["balance_before"] - c0["balance_after"])
        self.assertEqual("per_second", c0["billing"]["billing_unit"])
        self.assertFalse(c0["result_persistence"]["signed"])
        self.assertFalse(c0["result_persistence"]["requires_auth"])
        self.assertIsNone(c0["result_persistence"]["ttl_field_in_task"])
        self.assertEqual("X-Oneapi-Request-Id",
                         c0["request_id_contract"]["request_id_header"])
        self.assertFalse(c0["request_id_contract"]["has_x_request_id_header"])
        self.assertTrue(c0["request_id_contract"]["submit_body_id_equals_task_id"])
        self.assertGreaterEqual(len(obs["conflicts_registered"]), 5)
        # 0.8.2 / U10 裁决：PT 确认以实扣为准，仅 wan3.0-video 5s/720p 档位订正
        w3 = self.cat["models"]["wan3.0-video"]
        self.assertEqual(0.5, w3["price_usd_5s_720p"])
        self.assertEqual("verified", w3["price_usd_5s_720p_source"])
        pp = self.cat["pricing_policy"]
        self.assertEqual("verified", pp["_source"])
        self.assertIn("实际扣费", pp["authority"])
        self.assertIn("偏高", pp["estimate_caveat"])
        self.assertEqual(1, len(pp["verified_models"]))
        self.assertEqual("wan3.0-video", pp["verified_models"][0]["model"])
        self.assertEqual(500, pp["verified_models"][0]["credits_5s_720p"])
        # 其他视频模型未实测 → 单价与来源标注保持原样，不批量改
        self.assertEqual(0.7, self.cat["models"]["wan3.0-video-prime"]["price_usd_5s_720p"])
        self.assertEqual(0.475, self.cat["models"]["wan2.7-t2v"]["price_usd_5s_720p"])
        for mid, e in self.cat["models"].items():
            if mid != "wan3.0-video":
                self.assertNotIn("price_usd_5s_720p_source", e, mid)

    def test_no_verified_results_fabricated(self):
        for e in self.cat["models"].values():
            if "tool_calling" in e:
                self.assertIsNone(e["tool_calling"].get("verified"))
            if "web_search_detail" in e:
                self.assertIsNone(e["web_search_detail"].get("works"))


if __name__ == "__main__":
    unittest.main()
