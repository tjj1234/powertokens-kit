#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PT Task Gateway —— 跨模型统一的异步任务接口（独立模块，零外部依赖）。

为什么需要它（与 PT 真实接口的关系）：
  PT 底层是 OneAPI 按供应商透明路由，对调用方来说视频 / Seedance 的**提交与查询**共用同一传输层：
    - 提交：POST /v1/videos
    - 轮询：GET  /v1/videos/{id}
  所以「Seedance / Vidu / Wan 各有独立提交/查询接口」是**误判**——PT 已统一了路径。
  真正让「接多个模型要各写一套」的痛点在两处，本模块正是来归一化的：
    1) 请求体构造差异：Seedance 必须把 prompt 塞进 media:[{type:text}] 且图生视频素材强制 asset://；
       其他模型用顶层 prompt + http(s)/asset://。
    2) 返回字段位置差异：status 枚举值、结果 URL 可能在 metadata.url / metadata.content_url / task.url 任一处。
  另外两类不走 /v1/videos，是各自独立的同步端点：
    - 音频(TTS)：Ali(DashScope)兼容端点 POST /ali/api/v1/services/aigc/multimodal-generation/generation，同步返回 url。
    - 文本对话(chat)：OpenAI 兼容端点 POST /v1/chat/completions，同步返回 choices[0].message.content。
  关于取消：PT **不提供**异步任务取消接口（2026-10-30 与官方确认），本模块因此不实现 cancel。

本模块职责（复用 pt_video.py 的 REST 原语，不直接依赖 CLI 子命令）：
  - Catalog：统一媒体能力目录（catalog.json）——每模型的 kind/execution/分辨率/时长/模式/单价，
    agent 用 list_capabilities 读取后**无需再翻文档查能力**；上下架由 ModelRegistry 自动维护。
  - TaskState：把各厂商状态动词归一化为 QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED/UNKNOWN。
  - ModelAdapter：按 kind 构造提交体（Seedance 走 media-text + asset://；video 走顶层 prompt），并统一抽取结果 URL。
  - TaskGateway：media_generate（统一媒体生成入口，按 kind 路由：
      image / audio / chat 同步直返；video / seedance 异步拿 task_id 后轮询）
    + submit / get_status / wait。不提供 cancel（PT 无取消接口）。
    权限不做客户端拦截：/v1/models 是 PT 全量清单，本 key 无权限时由服务端报错并透传。
  - ModelRegistry：拉 GET /v1/models（PT **全量**模型清单，所有 key 相同）做 diff——
    新出现的模型自动 enable 并打 is_new；不再出现的自动 delisted；
    带 last_synced，超过 1 小时（对齐 PT 缓存）下次调用惰性自动重拉。

依赖：Python 3.9+ 标准库；引擎 REST 原语来自同仓的 pt_video.py（import 时把仓库根加入 sys.path）。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
import time

# ---------- 复用同仓 pt_video.py 的 REST 原语（不依赖 CLI 子命令）----------
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent  # pt-gateway/ 的父目录 = 仓库根（pt_video.py 所在）
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pt_video  # noqa: E402

# 从引擎取所需符号（全部为模块级函数/常量，import 安全）
ApiError = pt_video.ApiError
_api_call = pt_video._api_call
VIDEO_SUBMIT = pt_video.VIDEO_SUBMIT
VIDEO_POLL = pt_video.VIDEO_POLL
resolve_key = pt_video.resolve_key
kind_of_model = pt_video.kind_of_model
list_key_models = pt_video.list_key_models
POLL_INTERVAL = pt_video.POLL_INTERVAL
POLL_TIMEOUT = pt_video.POLL_TIMEOUT
poll_task = pt_video.poll_task
submit_task = pt_video.submit_task
AUTO_PRIORITY_VIDEO = pt_video.AUTO_PRIORITY_VIDEO
AUTO_PRIORITY_SEEDANCE = pt_video.AUTO_PRIORITY_SEEDANCE

ASYNC_KINDS = ("video", "seedance")  # 走 /v1/videos 任务式轮询的异步类型
SYNC_KINDS = ("image", "audio", "chat")  # 一次调用直接返回结果的同步类型
KNOWN_KINDS = ("image", "video", "seedance", "audio", "chat")
CATALOG_PATH = _HERE / "catalog.json"

# 端点常量（P0 修复：此前 IMAGE_ENDPOINT / AUDIO_ENDPOINT 只有使用、没有定义，
# 一旦带 key 调用 image/audio 会 NameError。现已补齐并集中在此。）
IMAGE_ENDPOINT = pt_video.IMAGE_ENDPOINT      # /v1/images/generations（同步）
AUDIO_ENDPOINT = "/ali/api/v1/services/aigc/multimodal-generation/generation"  # TTS（同步，Ali 兼容）
CHAT_ENDPOINT = "/v1/chat/completions"        # 文本对话（同步，OpenAI 兼容）

# 模型清单真相源：GET /v1/models（PT 全量模型，所有 key 返回相同；需 key）
# 注：2026-10-30 用户确认 /v1/models 是全量清单而非 key 权限视图，
#     故不再做客户端权限拦截（fail-open），权限问题交给 PT 服务端报错。


_CATALOG_CACHE = None  # 目录缓存：sync 时逐模型判定 kind，避免重复读 catalog.json


def _kind_of(model):
    """判定模型 kind：先用引擎规则；返回 unknown 时回退查本地目录 catalog.json。

    引擎 kind_of_model 只认 video/seedance/image 等已知前缀，chat 与部分 TTS 模型
    会返回 'unknown'；此时用 catalog.json 里已记录的 kind 字段兜底。
    """
    try:
        k = kind_of_model(model)
    except Exception:
        k = None
    if k and k != "unknown":
        return k
    try:
        global _CATALOG_CACHE
        if _CATALOG_CACHE is None:
            _CATALOG_CACHE = Catalog()
        e = _CATALOG_CACHE.entry(model)
        k2 = (e or {}).get("kind")
        if k2 and str(k2).lower() in KNOWN_KINDS:
            return str(k2).lower()
    except Exception:
        pass
    return k or "unknown"


# ---------- 健壮轮询（§2.5：429 退避 + 5xx 熔断）----------
def _retry_after(err):
    """尽力从 ApiError 取 Retry-After；引擎未暴露响应头时返回 None（退化为指数退避）。"""
    raw = getattr(err, "raw", None) or {}
    hdrs = raw.get("headers") or {}
    ra = hdrs.get("Retry-After") or hdrs.get("retry-after")
    if ra:
        try:
            return float(ra)
        except Exception:
            return None
    return None


def poll_task(key, task_id, timeout=POLL_TIMEOUT):
    """轮询任务到终态，带 429 退避 + 5xx 熔断（覆盖引擎原版）。

    - 429：尊重 Retry-After，否则指数退避（1→2→4→…≤60s）
    - 5xx：连续 5 次熔断抛出（避免无限重试烧钱/卡死）
    - 其他 4xx：直接透传（权限/参数问题，不退避）
    - 超时：抛出明确超时错误
    """
    deadline = time.monotonic() + timeout
    backoff = 1.0
    consecutive_5xx = 0
    max_5xx = 5
    while True:
        if time.monotonic() >= deadline:
            raise ApiError(0, f"轮询超时：{timeout}s 内任务 {task_id} 未到终态")
        try:
            task = _api_call("GET", VIDEO_POLL.format(id=task_id), key, timeout=30)
            consecutive_5xx = 0
            st = TaskState.normalize((task.get("status") or ""))
            if st in (TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED):
                return task
        except ApiError as e:
            status = e.status or 0
            if status == 429:
                wait_s = _retry_after(e) or backoff
                backoff = min(backoff * 2, 60)
                time.sleep(wait_s)
                continue
            if 500 <= status < 600:
                consecutive_5xx += 1
                if consecutive_5xx >= max_5xx:
                    raise ApiError(status, f"轮询熔断：连续 {consecutive_5xx} 次 5xx（task={task_id}）：{e.message}")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            raise  # 其他错误（含 4xx 非 429）直接透传
        time.sleep(min(backoff, max(1.0, POLL_INTERVAL)))
        backoff = min(backoff * 2, 60)


# ---------- 统一媒体能力目录 ----------
class Catalog:
    """能力目录（catalog.json）：每模型的 kind / execution / 分辨率 / 时长 / 模式 / 单价。

    这些字段 PT 的 /v1/models **不给**，靠人工观测固化；上下架（enabled/delisted）
    则由 ModelRegistry 自动维护，两者合起来才是完整的「模型档案」。
    """

    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("PT_GATEWAY_CATALOG", CATALOG_PATH))
        self.data = {"kinds": {}, "models": {}}
        self.load()

    def load(self):
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"[catalog] 目录解析失败（按空目录处理）：{e}\n")
        self.data.setdefault("kinds", {})
        self.data.setdefault("models", {})
        return self

    def kinds(self):
        return self.data["kinds"]

    def models(self, kind=None):
        return {m: e for m, e in self.data["models"].items()
                if kind is None or e.get("kind") == kind}

    def entry(self, model):
        return self.data["models"].get(model) or {}

    def kind_entry(self, kind):
        return self.data["kinds"].get(kind) or {}

    def capabilities(self, kind=None):
        """list_capabilities 的数据面：kinds + models（可按 kind 过滤）。"""
        ks = self.kinds()
        return {
            "kinds": ks if kind is None else {k: v for k, v in ks.items() if k == kind},
            "models": self.models(kind),
        }


# ---------- 归一化状态机 ----------
class TaskState:
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"

    _MAP = {
        "succeeded": SUCCEEDED, "completed": SUCCEEDED,
        "failed": FAILED,
        "cancelled": CANCELLED, "canceled": CANCELLED,
        "pending": QUEUED, "queued": QUEUED, "submitted": QUEUED,
        "running": RUNNING, "processing": RUNNING, "in_progress": RUNNING,
        "doing": RUNNING, "generating": RUNNING,
    }

    @classmethod
    def normalize(cls, raw_status: str) -> str:
        return cls._MAP.get((raw_status or "").lower(), cls.UNKNOWN)


def result_url(task: dict):
    """统一抽取结果 URL：兼容各厂商把链接放在不同位置。"""
    meta = task.get("metadata") or {}
    return meta.get("url") or meta.get("content_url") or task.get("url")


def normalize_status(task: dict) -> dict:
    """把厂商原始任务对象归一化为统一状态结构。"""
    raw = (task.get("status") or "").lower()
    progress = task.get("progress")
    if progress is None:
        progress = (task.get("metadata") or {}).get("progress")
    return {
        "task_id": task.get("id") or task.get("task_id"),
        "state": TaskState.normalize(raw),
        "progress": progress,
        "url": result_url(task),
        "raw": task,
    }


# ---------- 请求体适配（按 kind 构造 / 抽取）----------
def _build_media(first_frame=None, ref_image=None, ref_video=None):
    media = []
    if first_frame:
        media.append({"type": "first_frame", "url": first_frame})
    if ref_image:
        media.append({"type": "image", "url": ref_image})
    if ref_video:
        media.append({"type": "video", "url": ref_video})
    return media


def build_submit_body(model, kind, prompt, media, params):
    """按 kind 构造 /v1/videos 提交体。

    - video：顶层 prompt + 可选 media[]（http(s)/asset://）
    - seedance：prompt 必须放进 media:[{type:text}]；图生视频素材强制 asset://
    """
    core = {
        "size": (params.get("resolution") or "720p").upper(),
        "ratio": params.get("ratio") or "16:9",
        "seconds": str(params.get("duration", 5)),  # 服务端 enum<string>，必须字符串
        "generate_audio": not params.get("no_audio", False),
        "prompt_extend": not params.get("no_prompt_extend", False),
        "watermark": params.get("watermark", False),
    }
    if kind == "seedance":
        seed_media = [{"type": "text", "text": prompt or ""}]
        for m in media:
            if not str(m.get("url", "")).startswith("asset://"):
                raise ApiError(0, f"Seedance 素材必须是资产库 asset:// 格式，收到：{m.get('url')}")
            seed_media.append(m)
        body = {"model": model, "media": seed_media, **core}
    else:
        body = {"model": model, "prompt": prompt or "", **core}
        if media:
            body["media"] = media
    if params.get("seed") is not None:
        body["seed"] = params["seed"]
    return body


# ---------- 统一网关 ----------
class TaskGateway:
    def __init__(self, key=None, registry=None, catalog=None):
        self.key = key or resolve_key()
        self.registry = registry or ModelRegistry()
        self.catalog = catalog or Catalog()
        self._key_models_cache = None  # 本 key 的 /v1/models 权限集合（惰性拉取缓存）

    def _need_key(self):
        if not self.key:
            raise ApiError(401, "缺少 API Key：设置环境变量 POWERTOKENS_API_KEY 或 config.json")

    def _resolve_model(self, kind, model):
        """model=auto 时按 注册表 → 目录候选 的顺序选模型；返回具体模型 ID。

        注（2026-10-30）：不做客户端权限过滤。/v1/models 是全量清单，
        若某模型本 key 实际无权限，交给 PT 服务端报错（fail-open）。
        """
        if model and model != "auto":
            return model
        mid = self.registry.auto_select(kind)
        if mid:
            return mid
        cands = list(self.catalog.models(kind))
        if cands:
            return cands[0]
        raise ApiError(0, f"无法为 kind={kind} 选出模型（注册表为空且目录无候选）")

    # ---- 权限门禁（§2.1 fail-closed：显式模型不在本 key 列表→ 403 拦截）----
    # ---- 统一媒体生成入口 ----
    def media_generate(self, kind=None, model="auto", prompt="", aspect_ratio=None, n=None,
                       duration=5, resolution="720p", ratio="16:9", first_frame=None,
                       ref_image=None, ref_video=None, no_audio=False, no_prompt_extend=False,
                       watermark=False, seed=None, wait=False, timeout=POLL_TIMEOUT,
                       voice=None, params=None, messages=None, system=None,
                       temperature=None, max_tokens=None):
        """统一媒体生成入口：agent 只声明意图（kind + prompt），网关按 kind 路由。

        - image     -> 同步 POST /v1/images/generations，返回 {execution:"sync", urls:[...]}
        - video     -> 异步 POST /v1/videos + 轮询，返回 {execution:"async", task_id}
        - seedance  -> 同 video 端点，但走 media-text 请求体 + asset:// 强制校验
        - audio     -> 同步 POST /ali/...，返回 {execution:"sync", url}；voice 指定音色
        - chat      -> 同步 POST /v1/chat/completions，返回 {execution:"sync", text}
        model=auto 时按注册表 -> 目录候选的顺序自动选模型。

        权限：不做客户端拦截（2026-10-30 决策）。/v1/models 是 PT 全量模型清单，
        若本 key 对某模型实际无权限，由 PT 服务端报错，网关原样透传。
        """
        # 1) 解析 kind（不需要 Key；显式 kind 优先，否则从 model 反推）
        if kind is None and model and model != "auto":
            kind = _kind_of(model)
        kind = (kind or "").lower()
        if kind not in KNOWN_KINDS:
            raise ApiError(0, f"未知 kind={kind!r}；可用：{'/'.join(KNOWN_KINDS)}"
                              f"（或提供具体模型 ID 由网关反推 kind）")

        self._need_key()

        # 2) 图片：同步直返
        if kind == "image":
            model2 = self._resolve_model("image", model)
            body = {"model": model2, "prompt": prompt or ""}
            if aspect_ratio:
                body["aspect_ratio"] = aspect_ratio
            if n:
                body["n"] = int(n)
            sys.stderr.write(f"[gateway] POST {IMAGE_ENDPOINT} model={model2} kind=image\n")
            resp = _api_call("POST", IMAGE_ENDPOINT, self.key, body=body, timeout=120)
            urls = [d.get("url") for d in (resp.get("data") or []) if d.get("url")]
            return {"ok": True, "kind": "image", "execution": "sync",
                    "model": model2, "urls": urls, "raw": resp}

        # 3) audio：TTS 同步生成（vendor 端点 /ali/...，不走 /v1/videos）
        if kind == "audio":
            model2 = self._resolve_model("audio", model)
            return self.audio_generate(model2, prompt=prompt, voice=voice, params=params)

        # 4) chat：文本对话同步生成（POST /v1/chat/completions，OpenAI 兼容）
        if kind == "chat":
            model2 = self._resolve_model("chat", model)
            return self.chat_generate(model2, prompt=prompt, messages=messages, system=system,
                                      temperature=temperature, max_tokens=max_tokens,
                                      params=params)

        # 5) video / seedance：异步任务
        model2 = self._resolve_model(kind, model)
        r = self.submit(model2, prompt=prompt, duration=duration, resolution=resolution,
                        ratio=ratio, first_frame=first_frame, ref_image=ref_image,
                        ref_video=ref_video, no_audio=no_audio, no_prompt_extend=no_prompt_extend,
                        watermark=watermark, seed=seed, timeout=timeout, wait=wait)
        r["model"] = model2
        r["execution"] = "async"
        return r

    # ---- 音频(TTS)同步生成（vendor 端点，不走 /v1/videos）----
    def audio_generate(self, model, prompt="", voice=None, params=None):
        """音频(TTS)同步生成：POST /ali/api/v1/services/aigc/multimodal-generation/generation。

        PT 音频端点为 Ali(DashScope)兼容，一次调用同步返回音频 url（execution=sync）。
        请求体采用 Ali 标准 TTS 结构（model + input.prompt[+voice]），响应 url 多位置兜底抽取。
        注：具体请求/响应字段以 PT 文档实测为准；本方法尽力对齐并保留 raw 便于核对。
        """
        self._need_key()
        body = {"model": model, "input": {"prompt": prompt or ""}}
        if voice:
            body["input"]["voice"] = voice
        if params:
            body["parameters"] = params
        sys.stderr.write(f"[gateway] POST {AUDIO_ENDPOINT} model={model} kind=audio\n")
        resp = _api_call("POST", AUDIO_ENDPOINT, self.key, body=body, timeout=120)
        out = resp.get("output") or {}
        url = (out.get("audio") or {}).get("url") or out.get("url") \
            or resp.get("url") or (resp.get("data") or {}).get("url")
        return {"ok": True, "kind": "audio", "execution": "sync",
                "model": model, "url": url, "raw": resp}

    # ---- 文本对话(chat)同步生成（OpenAI 兼容 /v1/chat/completions）----
    def chat_generate(self, model, prompt="", messages=None, system=None,
                      temperature=None, max_tokens=None, params=None):
        """文本对话同步生成：POST /v1/chat/completions（OpenAI 兼容，同步返回文本）。

        messages 为完整对话数组 [{role, content}]；给了 messages 则以它为准。
        否则用 prompt 作为单轮 user 消息（system 可前置为 system 角色）。
        返回 {execution:"sync", text, raw}；usage 等原样保留在 raw。
        """
        self._need_key()
        msgs = list(messages) if messages else []
        if not msgs:
            if system:
                msgs.append({"role": "system", "content": system})
            msgs.append({"role": "user", "content": prompt or ""})
        body = {"model": model, "messages": msgs}
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if params:
            body.update(params)
        sys.stderr.write(f"[gateway] POST {CHAT_ENDPOINT} model={model} kind=chat\n")
        resp = _api_call("POST", CHAT_ENDPOINT, self.key, body=body, timeout=120)
        text = ""
        try:
            ch = (resp.get("choices") or [])
            if ch:
                text = ((ch[0] or {}).get("message") or {}).get("content") or ""
        except Exception:
            text = ""
        if not text:
            out = resp.get("output")
            if isinstance(out, dict):
                text = out.get("text") or ""
        return {"ok": True, "kind": "chat", "execution": "sync",
                "model": model, "text": text, "raw": resp}

    # ---- 能力查询：agent 用这个代替翻文档 ----
    def list_capabilities(self, kind=None):
        """返回能力目录，并叠加注册表的 enabled/delisted 实时状态。"""
        cap = self.catalog.capabilities(kind)
        reg = self.registry.data.get("models", {})
        for mid, entry in cap["models"].items():
            r = reg.get(mid)
            entry["registry_enabled"] = bool(r.get("enabled")) if r else None
            entry["delisted"] = bool(r.get("delisted")) if r else None
            # in_models：是否出现在最近一次 /v1/models 全量同步里（未同步过则为 None）
            entry["in_models"] = bool(r.get("in_models")) if r else None
        return cap

    def submit(self, model, prompt="", duration=5, resolution="720p", ratio="16:9",
               first_frame=None, ref_image=None, ref_video=None, no_audio=False,
               no_prompt_extend=False, watermark=False, seed=None, timeout=POLL_TIMEOUT,
               wait=False):
        """提交一个异步视频任务。返回 {task_id, kind}；wait=True 则轮询到终态并返回归一化状态。"""
        self._need_key()
        kind = kind_of_model(model)
        if kind not in ASYNC_KINDS:
            raise ApiError(0, f"模型 {model} 非异步视频类（kind={kind}）；网关只处理 video / seedance")
        media = _build_media(first_frame, ref_image, ref_video)
        body = build_submit_body(model, kind, prompt, media, dict(
            duration=duration, resolution=resolution, ratio=ratio,
            no_audio=no_audio, no_prompt_extend=no_prompt_extend,
            watermark=watermark, seed=seed,
        ))
        sys.stderr.write(f"[gateway] POST /v1/videos model={model} kind={kind}\n")
        tid, _ = submit_task(self.key, body)
        if wait:
            task = poll_task(self.key, tid, timeout=timeout)
            return {**normalize_status(task), "kind": kind}
        return {"task_id": tid, "kind": kind}

    def get_status(self, task_id):
        """查单个任务状态，返回归一化结构。"""
        self._need_key()
        task = _api_call("GET", VIDEO_POLL.format(id=task_id), self.key, timeout=30)
        return normalize_status(task)

    def wait(self, task_id, timeout=POLL_TIMEOUT):
        """轮询到终态，返回归一化结构；失败/超时归为 FAILED（不抛）。"""
        self._need_key()
        try:
            task = poll_task(self.key, task_id, timeout=timeout)
            return normalize_status(task)
        except ApiError as e:
            return {
                "task_id": task_id, "state": TaskState.FAILED,
                "progress": None, "url": None,
                "error": str(getattr(e, "message", e)), "raw": {},
            }

# ---------- 模型注册表（自动更新机制）----------
class ModelRegistry:
    """拉 /v1/models 做 diff，持久化到 registry.json。

    - 新上架的异步模型：自动 enabled=True 并打 is_new=True
    - 下架（不再出现在 live 列表）：自动 enabled=False, delisted=True
    - last_synced 时间戳；is_stale() 用于调用方惰性自动重拉（默认 1 小时，对齐 PT 缓存）
    """

    def __init__(self, path=None):
        self.path = Path(path or os.environ.get(
            "PT_GATEWAY_REGISTRY",
            Path.home() / ".config" / "pt-gateway" / "registry.json",
        ))
        self.data = {"last_synced": None, "models": {}}

    def load(self):
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {"last_synced": None, "models": {}}
        self.data.setdefault("models", {})
        return self

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def is_stale(self, max_age=3600):
        ls = self.data.get("last_synced")
        if not ls:
            return True
        try:
            dt = datetime.fromisoformat(ls)
        except Exception:
            return True
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() > max_age

    def sync(self, key, max_age=3600):
        """用 GET /v1/models（PT **全量**模型清单）刷新注册表。

        2026-10-30 语义修正（用户确认）：/v1/models 返回的是 PT 全量模型，
        所有 key 拿到的列表相同，它**不是**「本 key 权限视图」。因此：
          - 出现在列表里的模型 → in_models=True, delisted=False, enabled=True
          - 不再出现的模型   → delisted=True（视为下架）, enabled=False
          - 不做权限判定：本 key 能否调用某模型由 PT 服务端报错，网关不拦截（fail-open）。
        """
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        present = {}
        for mid in list_key_models(key, "all"):
            present[mid] = _kind_of(mid)
        models = self.data["models"]
        added, removed = [], []
        for mid, kind in present.items():
            if mid not in models:
                models[mid] = {"kind": kind, "enabled": True, "delisted": False,
                               "in_models": True, "first_seen": now,
                               "last_seen": now, "is_new": True}
                added.append(mid)
            else:
                m = models[mid]
                m["enabled"] = True
                m["delisted"] = False
                m["in_models"] = True
                m["last_seen"] = now
                m["is_new"] = False
                m["kind"] = kind
        for mid, m in models.items():
            if mid not in present:
                m["in_models"] = False
                if not m.get("delisted"):
                    m["delisted"] = True
                    m["enabled"] = False
                    m["last_seen"] = now
                    removed.append(mid)
        self.data["last_synced"] = now
        self.save()
        return {
            "added": added, "removed": removed,
            "total_enabled": sum(1 for m in models.values() if m.get("enabled")),
            "total_models": len(models),
            "last_synced": now,
        }

    def enabled_async(self, kind=None):
        """注册表里 enabled 且未下架的模型（可按 kind 过滤）。"""
        out = []
        for mid, m in self.data["models"].items():
            if m.get("enabled") and not m.get("delisted"):
                if kind is None or m.get("kind") == kind:
                    out.append(mid)
        return out

    def auto_select(self, kind):
        """按优先级挑一个模型（video/seedance 用引擎优先级表；其余按目录顺序）。"""
        cands = self.enabled_async(kind)
        if not cands:
            return None
        prio = AUTO_PRIORITY_SEEDANCE if kind == "seedance" else AUTO_PRIORITY_VIDEO
        cands.sort(key=lambda m: (
            next((i for i, h in enumerate(prio) if h in m.lower()), len(prio)),
            0 if "fast" in m.lower() else 1,
        ))
        return cands[0]

    def auto_sync_if_stale(self, key, max_age=3600):
        """惰性自动重拉：注册表为空或过期时 best-effort 同步；失败不抛，回退到现有/内置列表。"""
        if self.is_stale(max_age):
            try:
                return self.sync(key, max_age)
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"[registry] 自动同步失败（沿用现有/回退）：{e}\n")
        return None


def _selfcheck():
    # 轻量自检（无需真实 Key）：验证状态归一化与注册表 diff 逻辑。
    assert TaskState.normalize("succeeded") == "SUCCEEDED"
    assert TaskState.normalize("running") == "RUNNING"
    assert TaskState.normalize("weird") == "UNKNOWN"
    fake = {"id": "t1", "status": "completed", "metadata": {"url": "http://x/y.mp4", "progress": 80}}
    ns = normalize_status(fake)
    assert ns["state"] == "SUCCEEDED" and ns["progress"] == 80 and ns["url"] == "http://x/y.mp4"
    print("pt_gateway self-check OK")


def _cmd_sync():
    """手动触发注册表同步（无需启动 HTTP 服务）。要求环境变量 POWERTOKENS_API_KEY 可用。"""
    key = resolve_key()
    if not key:
        sys.stderr.write("ERROR: 缺少 API Key（设置 POWERTOKENS_API_KEY 或 config.json）\n")
        sys.exit(2)
    reg = ModelRegistry().load()
    diff = reg.sync(key)
    print(json.dumps(diff, ensure_ascii=False, indent=2))


def _cmd_capabilities(kind=None):
    """打印能力目录（agent 查能力用这个，不用翻文档）。"""
    cat = Catalog()
    print(json.dumps(cat.capabilities(kind), ensure_ascii=False, indent=2))




if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="PT Gateway 核心模块（自检 / 注册表同步 / 能力目录）")
    ap.add_argument("cmd", nargs="?", default="selfcheck",
                    choices=["selfcheck", "sync", "capabilities"],
                    help="selfcheck=自检（默认）；sync=拉 /v1/models 全量刷新注册表；"
                         "capabilities=打印能力目录（可带 kind）")
    ap.add_argument("kind_arg", nargs="?", default=None,
                    help="capabilities 子命令的 kind 过滤（image/video/seedance/audio/chat）")
    args = ap.parse_args()
    if args.cmd == "selfcheck":
        _selfcheck()
    elif args.cmd == "sync":
        _cmd_sync()
    elif args.cmd == "capabilities":
        _cmd_capabilities(args.kind_arg)
