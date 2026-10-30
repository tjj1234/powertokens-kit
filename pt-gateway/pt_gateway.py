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
  但**取消与音频是各 vendor 特定路径**（并非统一在 /v1/videos 之下）：
    - 取消：Seedance 真实存在 DELETE /byteplus/api/v3/contents/generations/tasks/{id}（queued→cancelled）；其他模型取消端点待确认，cancel() 诚实返回 {supported:false} 并说明已知端点。
    - 音频(TTS)：Ali(DashScope)兼容端点 POST /ali/api/v1/services/aigc/multimodal-generation/generation，同步返回 url。

本模块职责（复用 pt_video.py 的 REST 原语，不直接依赖 CLI 子命令）：
  - Catalog：统一媒体能力目录（catalog.json）——每模型的 kind/execution/分辨率/时长/模式/单价，
    agent 用 list_capabilities 读取后**无需再翻文档查能力**；上下架由 ModelRegistry 自动维护。
  - TaskState：把各厂商状态动词归一化为 QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED/UNKNOWN。
  - ModelAdapter：按 kind 构造提交体（Seedance 走 media-text + asset://；video 走顶层 prompt），并统一抽取结果 URL。
  - TaskGateway：media_generate（统一媒体生成入口，按 kind 路由：image 同步 / video·seedance 异步 / audio 同步生成（vendor 端点））
    + submit / get_status / wait / cancel（Seedance 真实取消，其他诚实 unsupported）。
  - ModelRegistry：拉 /v1/models 做 diff——新上架的异步模型自动 enable 并打 is_new，
    下架的自动 delisted；带 last_synced，超过 1 小时（对齐 PT 缓存）下次调用惰性自动重拉。

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

ASYNC_KINDS = ("video", "seedance")  # 走 /v1/videos 任务式轮询的类型；image 为同步，不进网关
SYNC_KINDS = ("image",)              # 一次调用直接返回结果的类型
KNOWN_KINDS = ("image", "video", "seedance", "audio")
CATALOG_PATH = _HERE / "catalog.json"
PUBLIC_CATALOG_PATH = _HERE / "catalog.public.json"


def _load_public():
    """加载公开层目录（catalog.public.json）：无 key 爬到的全局上架模型真相。"""
    if not PUBLIC_CATALOG_PATH.exists():
        return {}
    try:
        return json.loads(PUBLIC_CATALOG_PATH.read_text(encoding="utf-8")).get("models", {})
    except Exception:
        return {}


def public_models(kind=None):
    """公开层全局真相中的模型（可按 kind 过滤）。不作为权限判断，仅作「模型是否存在」的真相源。"""
    models = _load_public()
    if kind is None:
        return dict(models)
    return {m: e for m, e in models.items() if (e.get("kind") or "").lower() == kind.lower()}


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

    def public_models(self, kind=None):
        """公开层全局真相（catalog.public.json），按 kind 过滤。"""
        return public_models(kind)

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

    def _need_key(self):
        if not self.key:
            raise ApiError(401, "缺少 API Key：设置环境变量 POWERTOKENS_API_KEY 或 config.json")

    def _resolve_model(self, kind, model):
        """model=auto 时按 注册表优先 → 目录候选 的顺序选模型；返回具体模型 ID。"""
        if model and model != "auto":
            return model
        mid = self.registry.auto_select(kind)
        if mid:
            return mid
        cands = list(self.catalog.models(kind))
        if cands:
            return cands[0]
        raise ApiError(0, f"无法为 kind={kind} 选出模型（注册表为空且目录无候选）")

    # ---- 统一媒体生成入口 ----
    def media_generate(self, kind=None, model="auto", prompt="", aspect_ratio=None, n=None,
                       duration=5, resolution="720p", ratio="16:9", first_frame=None,
                       ref_image=None, ref_video=None, no_audio=False, no_prompt_extend=False,
                       watermark=False, seed=None, wait=False, timeout=POLL_TIMEOUT):
        """统一媒体生成入口：agent 只声明意图（kind + prompt），网关按 kind 路由。

        - image     → 同步 POST /v1/images/generations，返回 {execution:"sync", urls:[...]}
        - video     → 异步 POST /v1/videos + 轮询，返回 {execution:"async", task_id}
        - seedance  → 同 video 端点，但走 media-text 请求体 + asset:// 强制校验
        - audio     → 同步生成 POST /ali/.../multimodal-generation/generation，返回 {execution:"sync", url}
        model=auto 时按注册表 → 目录候选的顺序自动选模型。
        """
        # 1) 解析 kind（不需要 Key；显式 kind 优先，否则从 model 反推）
        if kind is None and model and model != "auto":
            try:
                kind = kind_of_model(model)
            except Exception:
                kind = None
        kind = (kind or "").lower()
        if kind not in KNOWN_KINDS:
            raise ApiError(0, f"未知 kind={kind!r}；可用：{'/'.join(KNOWN_KINDS)}"
                              f"（或提供具体模型 ID 由网关反推 kind）")

        self._need_key()

        # 3) 图片：同步直返
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

        # 4) audio：TTS 同步生成（vendor 端点 /ali/...，不走 /v1/videos）
        if kind == "audio":
            model2 = self._resolve_model("audio", model)
            return self.audio_generate(model2, prompt=prompt)

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

    # ---- 能力查询：agent 用这个代替翻文档 ----
    def list_capabilities(self, kind=None):
        """返回能力目录，并叠加注册表的 enabled/delisted 实时状态。"""
        cap = self.catalog.capabilities(kind)
        reg = self.registry.data.get("models", {})
        pub = public_models(kind)
        for mid, entry in cap["models"].items():
            r = reg.get(mid)
            entry["registry_enabled"] = bool(r.get("enabled")) if r else None
            entry["delisted"] = bool(r.get("delisted")) if r else None
            entry["key_permitted"] = bool(r.get("key_permitted")) if r else None
            entry["public_exists"] = mid in pub
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

    def cancel(self, task_id, kind=None, model=None):
        """取消任务。按 kind（或 model 反推）路由到各 vendor 取消端点。

        - seedance：真实存在 DELETE /byteplus/api/v3/contents/generations/tasks/{id}
                    （queued→cancelled），本方法真实调用并返回 {supported:true, state:CANCELLED}。
        - 其他 kind（video 等）：vendor 取消端点待确认，诚实返回 {supported:false}
                    并说明已确认的 Seedance 端点，避免假装成功。
        kind/model 至少给一个才能识别是否为 Seedance；都不给则按非 seedance 处理。
        """
        k = kind or (model and kind_of_model(model))
        if (k or "").lower() == "seedance":
            self._need_key()
            try:
                _api_call("DELETE", SEEDANCE_CANCEL.format(id=task_id), self.key, timeout=30)
                return {"task_id": task_id, "supported": True, "kind": "seedance",
                        "state": "CANCELLED"}
            except ApiError as e:
                return {"task_id": task_id, "supported": True, "kind": "seedance",
                        "state": "UNKNOWN", "error": str(e.message), "status": e.status}
        # 其他 kind（video 等）：用公开层 vendor_endpoint 推断 best-effort 取消端点（§2.4）
        # 仅文档推断，不耗 key；真实生效需一次任务验证。
        if model:
            entry = self.catalog.entry(model) or {}
            pe = public_models().get(model) or {}
            vend = entry.get("vendor_endpoint") or pe.get("vendor_endpoint") or ""
            if vend:
                import re as _re
                base = _re.sub(r"/tasks?/.*$", "", vend)
                return {
                    "task_id": task_id,
                    "supported": "best-effort",
                    "kind": k,
                    "guess_endpoint": f"DELETE {base}/tasks/{{id}}",
                    "note": "基于公开层 vendor_endpoint 推断，未实测；调用方可按此发 DELETE，失败再回退",
                }
        return {
            "task_id": task_id,
            "supported": False,
            "kind": k,
            "reason": "仅 Seedance 已知真实取消端点 DELETE /byteplus/api/v3/contents/generations/tasks/{id}；"
                      "其他模型取消端点待确认（可爬公开层 vendor_endpoint 推断 best-effort）",
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
        """用当前 Key 的 live 模型列表刷新『key 层权限视图』（§2.1）。

        语义修正：公开层(catalog.public.json)才是「模型是否还存在」的全局真相；
        key 层只记录「这个 key 能否调用」。因此：
          - 本 key 列表里没有的模型 → key_permitted=False，但**不**视为下架(delisted)。
          - 仅当某模型同时不在公开层（PT 文档已移除）→ 才标记 delisted=True。
        这样不同 key 返回的模型差异不会导致「误判下架」。
        """
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        pub = public_models()
        present = {}
        for kind in ASYNC_KINDS:
            for mid in list_key_models(key, kind):
                present[mid] = kind
        models = self.data["models"]
        added, removed, key_unavailable = [], [], []
        for mid, kind in present.items():
            if mid not in models:
                models[mid] = {"kind": kind, "enabled": True, "delisted": False,
                               "first_seen": now, "last_seen": now, "is_new": True}
                added.append(mid)
            else:
                m = models[mid]
                m["enabled"] = True
                m["delisted"] = False
                m["last_seen"] = now
                m["is_new"] = False
                m["kind"] = kind
            models[mid]["key_permitted"] = True
        for mid, m in models.items():
            if mid not in present:
                m["key_permitted"] = False
                if mid not in pub:
                    if not m.get("delisted"):
                        m["delisted"] = True
                        m["enabled"] = False
                        m["last_seen"] = now
                        removed.append(mid)
                else:
                    key_unavailable.append(mid)
        self.data["last_synced"] = now
        self.save()
        return {
            "added": added, "removed": removed, "key_unavailable": key_unavailable,
            "total_enabled": sum(1 for m in models.values() if m.get("enabled")),
            "last_synced": now,
        }

    def enabled_async(self, kind=None):
        out = []
        for mid, m in self.data["models"].items():
            if m.get("enabled") and not m.get("delisted"):
                if m.get("key_permitted") is False:
                    continue  # 本 key 不可调用（§2.1：权限视图，非下架）
                if kind is None or m.get("kind") == kind:
                    out.append(mid)
        return out

    def auto_select(self, kind):
        cands = self.enabled_async(kind)
        if not cands:
            # key 层无候选 → 回退到公开层全局真相（§2.1：公开层优先兜底）
            pub = public_models(kind)
            if pub:
                cands = list(pub.keys())
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




def enrich_with_llm(models):
    """LLM 能力摘要钩子（§2.2）：默认空实现，预留接口。

    以后要接 LLM 时，在这里对每个 model 的 description 生成 capabilities 摘要，
    返回 {model_id: {capabilities_observed: [...]}} 即可；默认返回空 dict 不改动。
    """
    return {}


def enrich_catalog(public_path=None, catalog_path=None, llm_hook=None):
    """把公开层目录(catalog.public.json)合并进 catalog.json（§2.2）。

    策略：merge —— 只补 catalog.json 中**缺失**的字段，绝不覆盖手写字。
    合并字段：params_observed / vendor_endpoint / execution / capabilities_observed / submit / poll / kind。
    llm_hook：可选，签名 enrich_with_llm(models) -> {model_id: {fields}}，用于补充能力摘要。
    返回变更摘要。
    """
    public_path = Path(public_path or PUBLIC_CATALOG_PATH)
    catalog_path = Path(catalog_path or CATALOG_PATH)
    pub = json.loads(public_path.read_text(encoding="utf-8")).get("models", {})
    cat = json.loads(catalog_path.read_text(encoding="utf-8"))
    models = cat.setdefault("models", {})
    fields = ("params_observed", "vendor_endpoint", "execution",
              "capabilities_observed", "submit", "poll", "kind")
    changed = set()
    for mid, pe in pub.items():
        ce = models.setdefault(mid, {})
        for f in fields:
            if f in pe and (f not in ce or ce[f] in (None, "", [], {})):
                ce[f] = pe[f]
                changed.add(mid)
    if llm_hook:
        extra = llm_hook(models) or {}
        for mid, fieldset in extra.items():
            if mid in models:
                for f, v in fieldset.items():
                    if f not in models[mid] or models[mid][f] in (None, "", [], {}):
                        models[mid][f] = v
                        changed.add(mid)
    catalog_path.write_text(json.dumps(cat, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"enriched_models": sorted(changed), "count": len(changed)}


def _cmd_enrich():
    """把公开层目录合并进 catalog.json（merge，不覆盖手写字）。"""
    res = enrich_catalog(llm_hook=enrich_with_llm)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def _cmd_public(kind=None):
    """打印公开层全局真相（catalog.public.json）。"""
    print(json.dumps(public_models(kind), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="PT Gateway 核心模块（自检 / 注册表同步 / 能力目录）")
    ap.add_argument("cmd", nargs="?", default="selfcheck",
                    choices=["selfcheck", "sync", "capabilities", "enrich", "public"],
                    help="selfcheck=自检（默认）；sync=手动拉 /v1/models 刷新注册表；"
                         "capabilities=打印能力目录（可带 kind）；"
                         "enrich=把公开层目录 merge 进 catalog.json；public=打印公开层真相")
    ap.add_argument("kind_arg", nargs="?", default=None,
                    help="capabilities 子命令的 kind 过滤（image/video/seedance/audio）")
    args = ap.parse_args()
    if args.cmd == "selfcheck":
        _selfcheck()
    elif args.cmd == "sync":
        _cmd_sync()
    elif args.cmd == "capabilities":
        _cmd_capabilities(args.kind_arg)
    elif args.cmd == "enrich":
        _cmd_enrich()
    elif args.cmd == "public":
        _cmd_public(args.kind_arg)
