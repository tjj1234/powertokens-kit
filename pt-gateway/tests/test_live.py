#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""live：真机验收（会真实消耗 PT 额度，默认整层跳过）。

三层门禁，缺一不可（防误烧钱）：
  1. PT_LIVE=1                    —— 人已授权
  2. POWERTOKENS_API_KEY 可用      —— 能真跑
  3. 每步执行前过 Budget 预算守卫   —— 累计超 --max-usd 立即 SKIP 后续付费项

编号即执行顺序（unittest 按方法名字典序跑）：
  01/02 免费项（清单、注册表同步）先跑，能提前发现 key 无效等问题，避免白花钱。
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import (  # noqa: E402
    pt_gateway, ApiError, Budget, estimate, TIERS, mask_key, live_key,
    skip_if_not_live, DEFAULT_MAX_USD,
)

TIER = os.environ.get("PT_E2E_TIER", "minimal")
MAX_USD = float(os.environ.get("PT_E2E_MAX_USD", DEFAULT_MAX_USD))

PROMPT_CHAT = "只回复两个字：你好"
PROMPT_IMAGE = "一只戴着红色围巾的柴犬，坐在雪地里，写实风格"
PROMPT_AUDIO = "你好，这是语音合成接口的验收测试。"
PROMPT_VIDEO = "一只柴犬在雪地里奔跑，镜头缓慢跟随，阳光明媚"


@skip_if_not_live()
class TestLive(unittest.TestCase):
    """真机验收：五种 kind 各跑一遍 + 错误路径 + 防泄漏自检。"""

    key = None
    gw = None
    budget = None
    log = None
    last_task_id = None
    last_video_url = None

    @classmethod
    def setUpClass(cls):
        cls.key = live_key()
        cls.gw = pt_gateway.TaskGateway(key=cls.key)
        cls.budget = Budget(max_usd=MAX_USD)
        cls.log = []
        cls.tier = TIERS[TIER]
        cls.est = estimate(TIER)
        cls.note(f"live 开始 | tier={TIER} | key={mask_key(cls.key)} | "
                 f"预估 ${cls.est['total']:.4f} | 预算上限 ${MAX_USD:.2f}")

    @classmethod
    def tearDownClass(cls):
        cls.note(f"live 结束 | 计入花费约 ${cls.budget.spent:.4f} "
                 f"（明细 {[(i['step'], i['usd']) for i in cls.budget.items]}）")

    @classmethod
    def note(cls, s):
        cls.log.append(s)
        sys.stderr.write("[live] " + s + "\n")

    # ---- 预算守卫 ----
    def _afford(self, step):
        """付费项执行前调用：超预算就 skip（skip 不是 fail——没跑不等于错）。"""
        cost = self.est.get(step, 0.0)
        if not self.budget.can_afford(cost):
            self.skipTest(f"预算守卫：{step} 约 ${cost:.4f}，累计将超上限 ${self.budget.max_usd:.2f}")
        return cost

    # ---------------- 免费项 ----------------
    def test_01_models_list_is_reachable(self):
        t0 = time.time()
        ids = pt_gateway.list_key_models(self.key, "all")
        dt = time.time() - t0
        self.note(f"/v1/models -> {len(ids)} 个模型（{dt:.2f}s，免费）")
        self.assertGreater(len(ids), 0, "模型清单为空：key 无效或接口变更")
        from collections import Counter
        dist = dict(Counter(pt_gateway._kind_of(m) for m in ids))
        self.note(f"kind 分布: {dist}")
        # 本档要用的模型必须在清单里（否则后续付费项是白花钱）
        for want in (self.tier["chat"], self.tier["image"],
                     self.tier["audio"], self.tier["video"]):
            self.assertIn(want, ids, f"档位依赖的模型不在清单中：{want}")

    def test_02_registry_sync(self):
        with tempfile.TemporaryDirectory() as d:
            reg = pt_gateway.ModelRegistry(path=str(Path(d) / "registry.json"))
            t0 = time.time()
            diff = reg.sync(self.key)
            dt = time.time() - t0
        self.note(f"registry.sync -> total={diff['total_models']} "
                  f"enabled={diff['total_enabled']}（{dt:.2f}s，免费）")
        self.assertGreater(diff["total_models"], 0)
        self.assertGreater(diff["total_enabled"], 0)
        self.assertTrue(diff["last_synced"])

    # ---------------- 付费项 ----------------
    def test_03_chat(self):
        cost = self._afford("chat")
        t0 = time.time()
        r = self.gw.media_generate(kind="chat", model=self.tier["chat"],
                                   prompt=PROMPT_CHAT, temperature=0.1, max_tokens=32)
        dt = time.time() - t0
        self.budget.charge("chat", cost)
        text = (r.get("text") or "").strip()
        self.note(f"chat OK model={r.get('model')} {dt:.2f}s ~${cost:.6f} 输出={text[:40]!r}")
        usage = (r.get("raw") or {}).get("usage")
        if usage:
            self.note(f"chat usage: {usage}")
        self.assertTrue(text, "chat 返回空文本")
        self.assertEqual("sync", r.get("execution"))

    def test_04_image(self):
        cost = self._afford("image")
        t0 = time.time()
        r = self.gw.media_generate(kind="image", model=self.tier["image"],
                                   prompt=PROMPT_IMAGE, n=1)
        dt = time.time() - t0
        self.budget.charge("image", cost)
        urls = r.get("urls") or []
        self.note(f"image OK model={r.get('model')} {dt:.2f}s ~${cost:.4f} urls={len(urls)}")
        self.assertTrue(urls, "image 未返回任何 URL")
        self.assertEqual("sync", r.get("execution"))
        self.assertTrue(urls[0].startswith("http"), f"URL 形态异常：{urls[0][:40]}")

    def test_05_audio(self):
        cost = self._afford("audio")
        t0 = time.time()
        r = self.gw.media_generate(kind="audio", model=self.tier["audio"],
                                   prompt=PROMPT_AUDIO)
        dt = time.time() - t0
        self.budget.charge("audio", cost)
        url = r.get("url")
        self.note(f"audio OK model={r.get('model')} voice={r.get('voice')} "
                  f"{dt:.2f}s ~${cost:.4f} url={'有' if url else '无'}")
        raw = r.get("raw") or {}
        self.note(f"audio raw keys: {sorted(raw.keys())[:12]}")
        self.assertTrue(url, "audio 未返回 URL（端点或字段又变了？看 raw keys）")
        self.assertEqual("sync", r.get("execution"))

    def test_06_video_submit_and_wait(self):
        cost = self._afford("video")
        per = cost / max(1, self.tier["videos"])
        for i in range(self.tier["videos"]):
            t0 = time.time()
            r = self.gw.media_generate(kind="video", model=self.tier["video"],
                                       prompt=PROMPT_VIDEO, duration=self.tier["dur"],
                                       resolution=self.tier["res"], wait=True, timeout=900)
            dt = time.time() - t0
            self.budget.charge("video", per)
            self.note(f"video[{i}] OK model={r.get('model')} {dt:.1f}s ~${per:.4f} "
                      f"state={r.get('state')}")
            self.assertEqual("SUCCEEDED", r.get("state"), f"视频未成功：{r}")
            self.assertTrue(r.get("url"), "成功但没有结果 URL")
            TestLive.last_task_id = r.get("task_id")
            TestLive.last_video_url = r.get("url")

    def test_07_status_query_of_finished_task(self):
        """复用 06 的 task_id 复查（免费）：验证轮询之外的单查路径也通。"""
        tid = self.last_task_id
        if not tid:
            self.skipTest("没有可用的 task_id（前一步视频未跑）")
        st = self.gw.get_status(tid)
        self.note(f"get_status({tid}) -> state={st.get('state')}")
        self.assertIn(st.get("state"), ("SUCCEEDED", "RUNNING", "QUEUED"))
        self.assertEqual(tid, st.get("task_id"))

    def test_08_error_path_is_propagated(self):
        """错误路径（免费）：服务端拒绝时必须透传，不能假装成功。

        这条同时验证 fail-open 语义——网关不做客户端拦截，由 PT 服务端说了算。
        """
        bad_model = "definitely-not-a-real-model-zzz"
        with self.assertRaises(ApiError) as ctx:
            self.gw.media_generate(kind="chat", model=bad_model, prompt="p", max_tokens=8)
        self.note(f"错误路径 OK：status={ctx.exception.status} msg={str(ctx.exception.message)[:80]}")
        self.assertNotEqual(0, ctx.exception.status, "应拿到服务端 HTTP 状态码")

    def test_09_unknown_kind_rejected_before_spending(self):
        """参数校验必须在发请求前完成（不花钱也不打 API）。"""
        with self.assertRaises(ApiError) as ctx:
            self.gw.media_generate(kind="hologram", prompt="p")
        self.assertIn("未知 kind", str(ctx.exception.message))

    def test_90_no_secret_in_logs(self):
        """防泄漏自检：日志里绝不能出现完整 key。"""
        blob = "\n".join(self.log)
        self.assertNotIn(self.key, blob, "日志中出现了完整 key")
        self.assertTrue(mask_key(self.key).endswith("*"), "脱敏函数失效")
        self.assertNotIn(self.key, mask_key(self.key))


if __name__ == "__main__":
    unittest.main()
