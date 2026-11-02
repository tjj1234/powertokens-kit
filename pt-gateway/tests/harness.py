#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pt-gateway 测试公共基建（零外部依赖，Python 3.9+ 标准库即可跑）。

为什么单独抽一层基建（而不是每个测试文件各写一份）：
  线上生产环境对测试有 5 条硬要求，这些要求必须由基建统一强制，不能靠每个写测试的人自觉：

  1. 分层：unit(纯逻辑) / contract(接口契约，离线) / live(真机花钱)。
     默认只跑前两层，live 必须显式授权。
  2. 离线层严禁真联网、真 sleep：NetworkGuard 让任何 _api_call 直接抛错，
     FakeClock 接管 time.sleep / time.monotonic，保证测试是确定性的、秒级结束。
  3. live 层双门禁：必须 PT_LIVE=1 **且** 拿得到 key，缺一即整层 skip（不会误烧钱）。
  4. 预算守卫：Budget 按单价预扣，累计超 --max-usd 立即停跑后续付费项。
  5. 报告脱敏：mask_key() 统一脱敏，assert_no_secret() 在写报告前兜底校验——
     万一报告里出现完整 key，直接判失败而不是把密钥写进仓库。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
GW_DIR = TESTS_DIR.parent          # <repo>/pt-gateway
REPO_ROOT = GW_DIR.parent          # <repo>（pt_video.py 所在）

for _p in (str(GW_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pt_gateway  # noqa: E402
import pt_video  # noqa: E402

ApiError = pt_gateway.ApiError
KNOWN_KINDS = pt_gateway.KNOWN_KINDS
ASYNC_KINDS = pt_gateway.ASYNC_KINDS
SYNC_KINDS = pt_gateway.SYNC_KINDS

# catalog.json 与被测模块同目录
CATALOG_JSON = GW_DIR / "catalog.json"


# ---------------------------------------------------------------- 脱敏 / 防泄漏
def mask_key(k, keep=4):
    """脱敏：只留前 keep 位。key 一律不准进日志和报告。"""
    if not k:
        return "(none)"
    k = str(k)
    return k[:keep] + "*" * max(0, len(k) - keep)


def assert_no_secret(text, *secrets):
    """报告落盘前的兜底校验：任何一处出现完整 secret 即判失败。

    这是"线上标准"里最容易被忽略、代价又最大的一条——
    测试报告是要进 git / 进 CI 产物的，泄漏 key 等于泄漏钱包。
    """
    for s in secrets:
        if s and len(str(s)) >= 8 and str(s) in (text or ""):
            raise AssertionError("报告/日志中出现了未脱敏的密钥，已阻止落盘（len=%d）" % len(str(s)))


# ---------------------------------------------------------------- 离线守卫
class NetworkGuard:
    """替代 pt_gateway._api_call / submit_task：离线测试里碰网即失败。

    目的不是"防止外部调用"，而是**让测试失败得响亮**——
    某个 unit 测试如果不小心漏打桩，会立刻红，而不是静默打一次真实 API（那要花钱）。
    """

    def __init__(self, name="network"):
        self.name = name
        self.calls = []

    def __call__(self, *a, **kw):
        self.calls.append((a, kw))
        raise AssertionError(
            f"[离线守卫] {self.name} 被真实调用（unit/contract 测试禁止联网与真实请求）：args={a[:2]}")


class FakeClock:
    """假时钟：接管 time.monotonic / time.sleep，让轮询类测试不真等。

    - monotonic() 返回虚拟时刻，sleep(s) 推进虚拟时刻并记录
    - 这样 429 退避 / 5xx 熔断 / 超时分支都能在毫秒内跑完且完全确定
    """

    def __init__(self, start=0.0):
        self.now = start
        self.sleeps = []

    def monotonic(self):
        return self.now

    def time(self):
        return self.now

    def sleep(self, s):
        s = float(s)
        self.sleeps.append(s)
        self.now += s

    def advance(self, s):
        self.now += float(s)

    def total_slept(self):
        return sum(self.sleeps)


class StubAPI:
    """脚本化的 _api_call 替身：按脚本顺序返回 dict 或抛异常，并记录全部调用。"""

    def __init__(self, script=None, default=None):
        self.script = list(script or [])
        self.default = default or {"id": "t1", "status": "succeeded"}
        self.calls = []

    def __call__(self, method, path, key, body=None, timeout=30):
        self.calls.append({"method": method, "path": path, "key": mask_key(key),
                           "body": body, "timeout": timeout})
        if self.script:
            item = self.script.pop(0)
        else:
            item = self.default
        if isinstance(item, BaseException):
            raise item
        return item

    @property
    def paths(self):
        return [c["path"] for c in self.calls]

    @property
    def last_body(self):
        return self.calls[-1]["body"] if self.calls else None


class GatewayTestBase(unittest.TestCase):
    """unit / contract 测试的基类：自动装离线守卫 + 假时钟 + 隔离环境变量。

    子类要用打桩就覆盖 self.api / self.clock 后调用 self.install()。
    """

    def setUp(self):
        self._saved = {
            "_api_call": pt_gateway._api_call,
            "submit_task": pt_gateway.submit_task,
            "list_key_models": pt_gateway.list_key_models,
            "resolve_key": pt_gateway.resolve_key,
            "time": pt_gateway.time,
        }
        # 清掉可能存在的真实 key，避免测试意外拿到真 key 走真实调用
        self._saved_env = os.environ.get("POWERTOKENS_API_KEY")
        os.environ.pop("POWERTOKENS_API_KEY", None)
        self.api = NetworkGuard("_api_call")
        self.clock = FakeClock()
        pt_gateway._api_call = self.api
        pt_gateway.submit_task = NetworkGuard("submit_task")
        pt_gateway.list_key_models = NetworkGuard("list_key_models")
        pt_gateway.resolve_key = lambda: ""
        pt_gateway.time = self.clock

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(pt_gateway, k, v)
        if self._saved_env is not None:
            os.environ["POWERTOKENS_API_KEY"] = self._saved_env

    # ---- 便捷打桩 ----
    def install_api(self, script=None, default=None):
        """把 _api_call 换成脚本化替身（本测试确实要模拟 HTTP 时用）。"""
        self.api = StubAPI(script, default)
        pt_gateway._api_call = self.api
        return self.api

    def install_submit(self, task_id="task-001", resp=None):
        """打桩 submit_task 并记录提交体（视频请求体断言靠它）。"""
        self.submit_calls = []

        def _submit(key, body):
            self.submit_calls.append({"key": mask_key(key), "body": body})
            return task_id, resp or {"id": task_id}

        pt_gateway.submit_task = _submit
        return task_id

    @property
    def submitted_body(self):
        return self.submit_calls[-1]["body"] if getattr(self, "submit_calls", None) else None


# ---------------------------------------------------------------- 预算与单价
# 单价（USD）取自 powertokens.ai/pricing，2026-10-30；仅覆盖真机档会用到的模型。
PRICE = {
    "qwen3.8-flash":                  {"unit": "1M_tok", "in": 0.150, "out": 0.470},
    "deepseek-v4-1-flash-260910":     {"unit": "1M_tok", "in": 0.150, "out": 0.500},
    "seedream-5-0-260128":            {"unit": "per_image", "per": 0.034},
    "seedream-4-5-251128":            {"unit": "per_image", "per": 0.034},
    "kling-image":                    {"unit": "per_image", "per": 0.028},
    "speech-2.8-turbo":               {"unit": "per_call_est", "per": 0.010},
    "qwen3-tts-instruct-flash":       {"unit": "per_call_est", "per": 0.010},
    "dreamina-seedance-2-0-mini-260615": {"unit": "per_sec", "480p": 0.031, "720p": 0.076},
    "wan3.0-video":                   {"unit": "per_sec", "480p": 0.050, "720p": 0.100},
    "dreamina-seedance-2-5-260628":   {"unit": "per_sec", "480p": 0.105, "720p": 0.232},
}

# 真机档位：不同档位只改"用哪些模型"，测试步骤完全相同。
TIERS = {
    "minimal": dict(chat="qwen3.8-flash", image="seedream-5-0-260128",
                    audio="speech-2.8-turbo", video="dreamina-seedance-2-0-mini-260615",
                    res="480p", dur=5, videos=1),
    "standard": dict(chat="qwen3.8-flash", image="seedream-5-0-260128",
                     audio="speech-2.8-turbo", video="wan3.0-video",
                     res="720p", dur=5, videos=1),
    "full": dict(chat="deepseek-v4-1-flash-260910", image="seedream-5-0-260128",
                 audio="speech-2.8-turbo", video="wan3.0-video",
                 res="720p", dur=5, videos=2),
}

DEFAULT_MAX_USD = 2.0


def price_of(model):
    return PRICE.get(model) or {"unit": "unknown", "per": 0.0}


def estimate(tier="minimal"):
    """预估各步骤花费（USD）。models / 状态查询 / 错误路径为 0。"""
    t = TIERS[tier]
    p_chat = price_of(t["chat"])
    chat = (300 * p_chat.get("in", 0) + 64 * p_chat.get("out", 0)) / 1_000_000
    p_img = price_of(t["image"])
    p_aud = price_of(t["audio"])
    p_vid = price_of(t["video"])
    if p_vid.get("unit") == "per_sec":
        unit_p = p_vid.get(t["res"]) or p_vid.get("720p") or p_vid.get("480p") or 0.0
        vid = unit_p * t["dur"] * t["videos"]
    else:
        vid = p_vid.get("per", 0.0) * t["videos"]
    return {
        "models": 0.0, "chat": round(chat, 6), "image": p_img.get("per", 0.0),
        "audio": p_aud.get("per", 0.0), "video": round(vid, 4),
        "total": round(chat + p_img.get("per", 0.0) + p_aud.get("per", 0.0) + vid, 4),
    }


class Budget:
    """预算守卫：按步预扣，累计超上限即拒绝后续付费项（宁可 SKIP 也不超支）。"""

    def __init__(self, max_usd=DEFAULT_MAX_USD):
        self.max_usd = float(max_usd)
        self.spent = 0.0
        self.items = []

    def can_afford(self, usd):
        return (self.spent + float(usd)) <= self.max_usd + 1e-9

    def charge(self, name, usd):
        usd = float(usd)
        self.spent += usd
        self.items.append({"step": name, "usd": round(usd, 6)})
        return usd

    def as_dict(self):
        return {"max_usd": round(self.max_usd, 4), "spent": round(self.spent, 6),
                "items": self.items}


# ---------------------------------------------------------------- live 门禁
def live_enabled():
    """真机层是否放行：(bool, reason)。

    双门禁，缺一不可——PT_LIVE=1 表示"人已授权"，key 存在表示"能真跑"。
    只有授权没有 key 时不能静默跳过（那会让人误以为跑过了），所以返回明确原因。
    """
    flag = os.environ.get("PT_LIVE", "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return False, "未授权：需设置 PT_LIVE=1（真机测试会消耗 PT 额度）"
    key = os.environ.get("POWERTOKENS_API_KEY", "").strip()
    if not key:
        return False, "已授权但拿不到 key：需设置 POWERTOKENS_API_KEY"
    return True, ""


def live_key():
    return os.environ.get("POWERTOKENS_API_KEY", "").strip()


LIVE_SKIP_REASON = "live 真机测试未授权（设置 PT_LIVE=1 + POWERTOKENS_API_KEY 后运行 run_tests.py --live）"


def skip_if_not_live():
    """装饰器：未授权时整类 skip。"""
    return unittest.skipUnless(live_enabled()[0], LIVE_SKIP_REASON)


# ---------------------------------------------------------------- 断言小工具
def assert_schema_keys(tc, obj, required, where=""):
    """断言返回结构包含必需字段（契约测试用：字段名变了要立刻红）。"""
    missing = [k for k in required if k not in obj]
    tc.assertEqual([], missing, f"{where} 缺少字段 {missing}；实际 keys={sorted(obj.keys())}")


__all__ = [
    "pt_gateway", "pt_video", "ApiError", "KNOWN_KINDS", "ASYNC_KINDS", "SYNC_KINDS",
    "CATALOG_JSON", "GW_DIR", "REPO_ROOT", "TESTS_DIR",
    "mask_key", "assert_no_secret", "NetworkGuard", "FakeClock", "StubAPI",
    "GatewayTestBase", "PRICE", "TIERS", "estimate", "Budget", "price_of",
    "live_enabled", "live_key", "skip_if_not_live", "LIVE_SKIP_REASON",
    "assert_schema_keys", "DEFAULT_MAX_USD",
]
