#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PowerTokens 图片 + 视频生成 —— 跨 agent 工具的可移植引擎（直连官方 REST，零外部依赖）。

为什么这样设计：
  PowerTokens 两类生成都是「提交任务」但返回形态不同：
  - 视频：POST /v1/videos 提交拿 task_id → 每 10s GET /v1/videos/{id} 轮询 → succeeded 拿 url
  - 图片：POST /v1/images/generations 同步返回 data[].url（OpenAI 风格，不轮询）
  官方文档：
    - 视频提交：https://docs.powertokens.ai/zh-Hans/zmodelVideo/ali/wan3.0-video-generation
    - 视频查询：https://docs.powertokens.ai/zh-Hans/zmodelVideo/minimax/query-video-status
    - 图片（image-01）：https://docs.powertokens.ai/zh-Hans/zmodelImage/minimax/image01-image-to-image
    - Seedance 协议：https://docs.powertokens.ai/assets-library?tab=1 （仪表盘手动签 Asset Library User Agreement）
  本引擎只用 Python 标准库（urllib），**不依赖 Node / powertokens-cli / requests**，
  所以任何能跑 Python 的 agent（WorkBuddy / Claude / Cursor / Trae / 任意 MCP 客户端）都能直接用。

能力层：
  1) check    —— 秒级检查 Key 是否就绪（不生成分文）
  2) 配置     —— 从环境变量或本地 config.json 注入 Key（"输入 Key 的地方"）
  3) auto     —— generate 不写 -m 时，按账号真实可用模型自动选（视频/图片分开排序）
  4) 预校验   —— 显式 -m 先与 live 列表比对，不在则 1 秒内报错退出，不进生成、不扣费
  5) 查询     —— models 命令输出每个模型的定价 + 支持参数（视频 + 图片）
  6) 生成     —— generate 提交图片(同步)/视频(轮询)，直到拿到链接（可选下载到本地）

设计原则（工具无关）：
  - 不依赖任何特定 agent 格式。Key 优先读环境变量 POWERTOKENS_API_KEY；其次 config.json。
  - config.json 默认在 ~/.config/powertokens-kit/（可用 PT_VIDEO_CONFIG 覆盖），不写进 skill 目录；
    旧路径 ~/.config/powertokens-video/ 仍会自动回落读取（更名兼容，老用户零迁移）。
  - 默认模型 = auto（除非用 config --set-model 固定，或 generate 显式 -m）。generate 按模型类型自动分流视频/图片。

子命令：
  check                         Key 体检（exit 0=就绪 2=缺 Key）
  config --set-key <k>          保存 Key 到 config.json
  config --set-model <id|auto>  保存默认模型（generate 未写 -m 时优先用它；默认 auto）
  config --show                 查看配置（Key 脱敏）
  auto-model [--type image|video|seedance]  打印 auto 首选模型 ID（不生成）
  models [-t video|image|seedance|all] [--json]  列出模型 + 定价 + 支持参数
  generate -m <id|auto> -p ... [--type image|video|seedance]  提交并取回链接
  seedance [enable|disable|status]  Seedance 协议标记管理（本地，全系 1.0/1.5/2.0/2.5 通用）
  estimate -m <id> ...          仅估算消耗（美元；图片单价待补充）
  balance                       查询当前 Key 维度余额（只读，credits）
  usage [--start-date --end-date] 查询按模型类型汇总的账单（只读）
  logs                          查询消费记录明细（只读）
  agreement                     查询资产库协议签署状态（只读）

退出码：0=成功 2=缺 Key 3=显式模型未过预校验 1=其他失败
"""

import argparse
import base64
import difflib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
# 配置与任何 agent 解耦：默认放用户级目录，可用环境变量覆盖。
# 0.5.0 起项目更名为 powertokens-kit；配置目录保持向后兼容：
#   1) PT_VIDEO_CONFIG 显式指定优先
#   2) 新目录 ~/.config/powertokens-kit/config.json
#   3) 回落到旧目录 ~/.config/powertokens-video/config.json（老用户无需迁移）
def _resolve_config_path(env_path=None, home=None):
    """解析配置文件路径（参数可注入，便于单测）。

    优先级：PT_VIDEO_CONFIG > ~/.config/powertokens-kit/config.json
    > ~/.config/powertokens-video/config.json（旧名回落）> 新路径（首次写入）。
    """
    if env_path is None:
        env_path = os.environ.get("PT_VIDEO_CONFIG")
    if env_path:
        return Path(env_path)
    base = Path(home) if home else Path.home()
    new = base / ".config" / "powertokens-kit" / "config.json"
    legacy = base / ".config" / "powertokens-video" / "config.json"
    if new.exists():
        return new
    if legacy.exists():
        return legacy
    return new


CONFIG_PATH = _resolve_config_path()
ONBOARD_URL = "https://powertokens.ai/zh-Hans/dashboard"
PT_BASE = "https://api.powertokens.ai"

# ---------- 端点 ----------
VIDEO_SUBMIT = "/v1/videos"
VIDEO_POLL = "/v1/videos/{id}"
IMAGE_ENDPOINT = "/v1/images/generations"   # 实测确认：OpenAI 风格，带 /generations 后缀，同步返回

# ---------- T01：提交入口按 catalog 的 model→submit 对照选择（0.8.10）----------
# 设计原则（不破坏北极星）：
#   - catalog 只读、可选：读得到就用它的 submit；读不到/字段缺失/值异常一律回落上面的写死常量。
#   - 北极星 / 独立分发的 skill 副本可以不含 pt-gateway/catalog.json —— 此时行为与旧版逐字节一致。
#   - 不猜新入口：kling 等模型的"真实可用入口"需真机确认，catalog 里是什么就用什么，不臆造。
_CATALOG_PATH = SCRIPT_DIR / "pt-gateway" / "catalog.json"
_catalog_models_cache = None


def load_catalog_models(path=None):
    """只读加载 catalog 的 models 映射；任何异常都返回 {}（不抛、不联网、不缓存失败）。

    路径可用参数注入便于单测；默认取 skill 目录旁的 pt-gateway/catalog.json。
    成功结果缓存到模块级变量，避免每次提交都读盘。
    """
    global _catalog_models_cache
    p = Path(path) if path else _CATALOG_PATH
    if path is None and _catalog_models_cache is not None:
        return _catalog_models_cache
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, dict):
            models = {}
    except (OSError, ValueError, TypeError):
        models = {}
    if path is None:
        _catalog_models_cache = models
    return models


def resolve_submit_endpoint(model, kind, path=None, strict=False):
    """按 (model, kind) 从 catalog 选提交入口；无可靠映射则回落写死常量。

    复核 0.9.2 #1：seedance 与 video 同为异步视频类（走 /v1/videos），
    不能再让 seedance 掉进 image 分支（0.9.1 曾把 seedance 误发到 /v1/images/generations）。
    kind: 'video'/'seedance' → 回落 VIDEO_SUBMIT，只接受 catalog 中含 '/videos' 的入口；
          'image' → 回落 IMAGE_ENDPOINT，只接受含 '/images' 的入口。
    未知 kind：strict=True 时报错，不默认当图片（避免错误入口）；strict=False 回落图片（兼容旧行为）。
    跨类型（例如给视频模型配了 /v1/images/...）视为脏数据，回落常量，防止提交到错误入口。
    """
    KIND_FALLBACK = {"video": (VIDEO_SUBMIT, "/videos"),
                     "seedance": (VIDEO_SUBMIT, "/videos"),
                     "image": (IMAGE_ENDPOINT, "/images")}
    if kind not in KIND_FALLBACK:
        if strict:
            raise ApiError(0, f"未知媒体类型 kind={kind!r}，无法解析提交入口（strict 模式拒绝盲目提交）")
        # 兼容旧行为：未知类型此前一律按图片处理
        fallback, needle = IMAGE_ENDPOINT, "/images"
    else:
        fallback, needle = KIND_FALLBACK[kind]
    entry = load_catalog_models(path).get(model) if model else None
    if isinstance(entry, dict):
        submit = entry.get("submit")
        if isinstance(submit, str) and submit.startswith("/") and needle in submit:
            return submit
    return fallback


# 友好参数键 -> capabilities.media.param_names 里的键。
# param_names 的值才是 PT 请求体真正要用的字段名（如 duration_s->seconds/size->resolution）。
# 只处理这三类生成参数；模型/提示词/媒体/开关类字段不参与重命名。
_T09_FRIENDLY_KEYS = ("duration_s", "resolution", "aspect_ratio", "size")
# 无 param_names / 缺某键映射时的默认真实字段名 —— 等于生成链路历史写死的字段名。
# 作用：默认模型（wan3.0-video 等）body 逐字节不变；只有 catalog 明确给了不同映射才重命名。
_T09_DEFAULT_REAL = {
    "duration_s": "seconds", "resolution": "size",
    "aspect_ratio": "ratio", "size": "size",
}


def resolve_media_fields(model, friendly, path=None):
    """按 catalog 的 capabilities.media.param_names 把友好字段名映射成真实字段名（T09）。

    纯规则、不发请求、不猜：
      - friendly: {友好键: 值}，如 {"duration_s":"5","resolution":"720p","aspect_ratio":"16:9"}；
      - 优先用 catalog param_names 的映射；无该模型/无该键映射 → 回落 _T09_DEFAULT_REAL
        （即历史写死的字段名），保证默认模型 body 逐字节不变（R1）；
      - 返回 (mapped, unsupported)：unsupported 收集「catalog 声明了 param_names 但该键无映射」
        的维度（no_param_mapping）或整模型无来源（capability_unknown），供上层提示；
        **无论哪种都不改值、不丢弃参数**，只是告知。
    这与 node 的 mediaRequestParams 同源：字段名取自 catalog param_names，缺来源不猜。
    """
    mapped = {}
    unsupported = []
    pnames = None
    entry = load_catalog_models(path).get(model) if model else None
    if isinstance(entry, dict):
        media = entry.get("capabilities")
        media = media.get("media") if isinstance(media, dict) else None
        if isinstance(media, dict) and isinstance(media.get("param_names"), dict):
            pnames = media["param_names"]
    for key, value in friendly.items():
        if key not in _T09_FRIENDLY_KEYS:
            mapped[key] = value          # 非生成参数原样透传，不参与映射
            continue
        real = (pnames or {}).get(key) or _T09_DEFAULT_REAL.get(key)
        if pnames and key not in pnames:
            unsupported.append({"field": key, "value": value, "reason": "no_param_mapping"})
        elif not pnames:
            unsupported.append({"field": key, "value": value, "reason": "capability_unknown"})
        mapped[real] = value
    return mapped, unsupported


# 复核 T09（2026-11-24）：图片请求统一构造。
# 字段名来源优先级：catalog capabilities.media.param_names > 下表（en.zip 官方字段，documented）> 明确拒绝。
# 下表只列 PT 官方 OpenAPI（en.zip）里确有该字段的模型 slug；没有的一律不猜，进 unsupported。
_IMAGE_OFFICIAL_FIELDS = {
    # slug: {友好键: 官方请求体字段名}（来源 en.zip OpenAPI，_source=documented）
    "seedream-5-0-260128": {"ref_image": "image", "size": "size", "resolution": "size",
                            "aspect_ratio": "aspect_ratio", "response_format": "response_format",
                            "seed": "seed"},
    "seedream-4-5-251128": {"ref_image": "image", "size": "size", "resolution": "size",
                            "response_format": "response_format", "seed": "seed"},
    "seedream-4-0-250828": {"ref_image": "image", "size": "size", "resolution": "size",
                            "response_format": "response_format", "seed": "seed"},
    "dola-seedream-5-0-pro-260628": {"ref_image": "image", "size": "size", "resolution": "size",
                                     "response_format": "response_format", "seed": "seed"},
    "qwen-image-3.0-pro": {"ref_image": "image", "size": "size", "resolution": "size",
                           "n": "n", "seed": "seed"},
    "qwen-image-2.0-pro": {"ref_image": "image", "size": "size", "resolution": "size",
                           "n": "n", "seed": "seed"},
    "wan2.7-image-pro": {"ref_image": "image", "size": "size", "resolution": "size",
                         "n": "n", "seed": "seed"},
    "image-01": {"aspect_ratio": "aspect_ratio", "n": "n", "seed": "seed",
                 "response_format": "response_format"},
}
# 友好键 -> catalog param_names 里的键（T09 已有），图片侧额外支持 ref_image。
_IMAGE_FRIENDLY_TO_PARAM = {
    "ref_image": "ref_image", "size": "size", "resolution": "resolution",
    "aspect_ratio": "aspect_ratio", "n": "n", "seed": "seed", "response_format": "response_format",
}


def build_image_request_body(model, prompt, *, aspect_ratio=None, n=None, response_format=None,
                             seed=None, ref_image=None, size=None, resolution=None, path=None):
    """统一构造 /v1/images/generations 请求体（复核 T09，CLI 与网关共用）。

    设计：
      - model/prompt 必带；n/response_format/seed/size/resolution/ref_image/aspect_ratio 按需带；
      - 字段名先查 catalog capabilities.media.param_names，再查 _IMAGE_OFFICIAL_FIELDS
        （en.zip 官方字段，documented）；两者都没有 → 记入 unsupported 并**不**写进 body
        （明确拒绝，不静默丢弃、也不猜字段名，守 R4）；
      - 返回 (body, unsupported)。unsupported 每项 {field, value, reason}。
      - ref_image 支持 str 或 list：官方字段 `image` 接受单个或数组 URL/Base64。
    """
    body = {"model": model, "prompt": prompt or ""}
    unsupported = []
    provided = {
        "aspect_ratio": aspect_ratio, "n": n, "response_format": response_format,
        "seed": seed, "ref_image": ref_image, "size": size, "resolution": resolution,
    }
    # 字段映射来源：catalog param_names 优先，官方表兜底
    entry = load_catalog_models(path).get(model) if model else None
    pnames = None
    if isinstance(entry, dict):
        caps = entry.get("capabilities")
        media = caps.get("media") if isinstance(caps, dict) else None
        if isinstance(media, dict) and isinstance(media.get("param_names"), dict):
            pnames = media["param_names"]
    official = _IMAGE_OFFICIAL_FIELDS.get(model, {})

    for friendly, value in provided.items():
        if value is None or value == [] or value == "":
            continue
        key = _IMAGE_FRIENDLY_TO_PARAM.get(friendly)
        # catalog param_names 的值即真实字段名；但图片模型历史上更常见官方表，
        # 两者都查：param_names 命中优先，否则官方表。
        real = None
        if pnames and key in pnames:
            real = pnames[key]
        elif friendly in official:
            real = official[friendly]
        elif key and pnames is None and friendly in ("aspect_ratio", "n", "seed", "response_format"):
            # 通用字段在无映射来源时按历史写死字段名透传（旧行为兼容）
            real = friendly
        if not real:
            unsupported.append({"field": friendly, "value": value,
                                "reason": "no_official_field_mapping"})
            continue
        # size 与 resolution 都映射到 size 时，只保留一个（size 优先）
        if real == "size" and "size" in body and friendly == "resolution":
            continue
        body[real] = value
    return body, unsupported


# ---------- 资产库上传（Seedance 图生视频必须先把素材上传拿 asset://）----------
# 注意：资产库 base 是 powertokens.ai/api（带 /api 前缀），不是 api.powertokens.ai
ASSET_BASE = "https://powertokens.ai/api"
ASSET_UPLOAD = ASSET_BASE + "/v1/asset/upload"                 # POST multipart，需鉴权
ASSET_GET_ID = ASSET_BASE + "/v1/asset/jobs/get-asset-id?task_id={}"   # GET，无需鉴权

# ---------- 账户 / 计费（只读查询；与资产库同域 powertokens.ai/api，鉴权用裸 key，与资产上传一致）----------
# 注意：这些接口的 host 是 powertokens.ai/api（仪表盘 / 资产后端域），与生成类接口的 api.powertokens.ai 不是同一套。
# 鉴权沿用资产上传（ASSET_UPLOAD）的「裸 key」写法（Authorization: <key>，不带 Bearer），不要与生成类接口的 Bearer 混淆。
# ⚠️ 未真机验证：沙箱无可用 Key。若返回 401/403，可能该域要的是网页登录令牌而非 API Key，请用真实 Key 实测确认。
ACCOUNT_BASE = ASSET_BASE                                       # https://powertokens.ai/api
ACCOUNT_AGREEMENT = "/v1/asset/agreement/status"                # GET 资产库协议签署状态
ACCOUNT_LOGS = "/v1/logs/query"                                 # GET 消费记录
ACCOUNT_BALANCE = "/v1/billing/balance"                         # GET 余额（按 Key 粒度）
ACCOUNT_USAGE = "/v1/billing/usage"                             # GET 账单（按模型类型汇总）


def _account_call(method, path, key, params=None, timeout=30):
    """账户 / 计费类只读 GET（与资产上传同域同鉴权：裸 key）。返回 PT 原始 dict（含 code/data）。"""
    url = ACCOUNT_BASE + path
    if params:
        url += ("&" if "?" in path else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": key,                                    # 裸 key（与 ASSET_UPLOAD 一致），非 Bearer
        "Accept": "application/json",
        "User-Agent": "pt-media-skill",
    }, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8"))
            if isinstance(detail, dict):
                detail = detail.get("error") or detail.get("message") or json.dumps(detail, ensure_ascii=False)
        except Exception:
            pass
        raise ApiError(e.code, str(detail) or e.reason, headers=getattr(e, "headers", None))
    except urllib.error.URLError as e:
        raise ApiError(0, f"网络错误：{e.reason}", headers=None)


# ---------- 提交 / 轮询时序（按用户要求）----------
SUBMIT_TIMEOUT = 120      # POST /v1/videos 的 HTTP 客户端超时（秒）
POLL_INTERVAL = 10        # 每 10 秒查询一次任务状态
POLL_TIMEOUT = 600        # 整体轮询总上限（秒）；真实出片约 3 分钟，故默认 600。
                          # 若想更短可用 generate --timeout 120；但低于实际出片时长会轮询超时。
IMAGE_HTTP_TIMEOUT = 120  # 图片同步生成的 HTTP 客户端超时（秒）
DOWNLOAD_CHUNK = 65536

# ---------- auto 模型优先级（小写包含匹配，排在前先试）----------
# 注意：Seedance 已拆为独立类型，普通 video auto 不再包含它（见 AUTO_PRIORITY_SEEDANCE）
AUTO_PRIORITY_VIDEO = ["kling", "wan", "hailuo", "vidu", "happyhorse"]
AUTO_PRIORITY_SEEDANCE = ["seedance-1-5-pro-251215", "dreamina-seedance-2-0-fast-260128", "dreamina-seedance-2-0-260128"]
AUTO_PRIORITY_IMAGE = ["seedream", "qwen-image", "image-01", "kling-image", "wan"]
AUTO_FALLBACK_VIDEO = "wan3.0-video"   # 仅在拉不到任何列表时的最后兜底
AUTO_FALLBACK_SEEDANCE = "seedance-1-5-pro-251215"
AUTO_FALLBACK_IMAGE = "seedream-5-0-260128"
AUTO_MAX_TRIES = 4

# ---------- 消耗估算（基于 PT 真实 pre-consume 单价探测；零成本建表）----------
# 系数：时长线性（5s→10s = ×2）、1080p = 720p ×2。单位：美元（$）。
# 视频单价已实测；图片单价待补充（生成会扣费，且当前 key 余额有限，未探测）。
# Seedance 全系单价单列（走资产库协议 + asset:// 素材，与普通视频分开计价）
# T03：wan3.0-video 旧写死价 0.20 已过期，catalog 实测实扣为 0.5（verified，U10 裁决）。
# 本表仅作离线兜底；estimate 优先读 catalog（带 _source）与账户域实时预估。
PRICE_VIDEO_5S_720P = {
    "wan3.0-video": 0.5,
    "wan3.0-video-prime": 0.70,
    "kling-v2-1-master": 1.40,
    "kling-video-o1": 0.42,
    "kling-v3": 0.42,
    "wan2.7-t2v": 0.475,
}
PRICE_SEEDANCE_5S_720P = {
    "seedance-1-5-pro-251215": 0.247,
    "dreamina-seedance-2-0-fast-260128": 0.605,
    "dreamina-seedance-2-0-260128": 0.756,
}
# 图片单价：未知（PT 图片按张计费，结构不同于视频）。填真实单价后 estimate 即生效。
# 占位 ID 取自实测可用列表，值为 None 表示「待补充」；有 Key 时 live 列表会覆盖 available。
IMAGE_PRICE = {
    "seedream-5-0-260128": None,
    "seedream-4-5-251128": None,
    "seedream-4-0-250828": None,
    "dola-seedream-5-0-pro-260628": None,
    "qwen-image-2.0-pro": None,
    "qwen-image-3.0-pro": None,
    "image-01": None,
    "kling-image-o1": None,
    "wan2.7-image-pro": None,
}

# ---------- 各模型族支持参数（静态目录，基于 PT 公开能力；以仪表盘为准）----------
# 普通视频模型（不含 Seedance —— Seedance 拆为独立类型）
MODEL_CAPS = [
    ("kling", dict(vendor="Kling", kind="video", max_dur=10, res="720p,1080p",
                   inputs="text, image, audio", camera="supported")),
    ("wan", dict(vendor="Wan", kind="video", max_dur=10, res="720p,1080p", inputs="text, image", camera="—")),
    ("hailuo", dict(vendor="MiniMax Hailuo", kind="video", max_dur=10, res="720p,1080p", inputs="text, image", camera="—")),
    ("vidu", dict(vendor="Vidu", kind="video", max_dur=10, res="720p,1080p", inputs="text, image, multi-reference", camera="—")),
    ("happyhorse", dict(vendor="HappyHorse", kind="video", max_dur=10, res="720p", inputs="text", camera="—")),
]
# Seedance 全系独立类型：必须签 Asset Library 协议 + 素材走 asset://
SEEDANCE_CAPS = [
    ("seedance", dict(vendor="ByteDance Seedance", kind="seedance", max_dur=10, res="720p,1080p",
                      inputs="text, image(first-frame, asset://), start-end frames, video+audio ref",
                      camera="—", requires_agreement=True)),
]
IMAGE_MODEL_CAPS = [
    ("seedream", dict(vendor="ByteDance Seedream", kind="image", max_dur=0, res="1K/2K/4K",
                      inputs="text, image(ref)", camera="—")),
    ("qwen-image", dict(vendor="Qwen Image", kind="image", max_dur=0, res="1K+",
                        inputs="text, image", camera="—")),
    ("image-01", dict(vendor="MiniMax Image-01", kind="image", max_dur=0, res="512-2048px",
                      inputs="text, image(subject_reference)", camera="—")),
    ("kling-image", dict(vendor="Kling Image", kind="image", max_dur=0, res="—",
                         inputs="text, image", camera="—")),
    ("wan", dict(vendor="Wan Image", kind="image", max_dur=0, res="—",
                 inputs="text, image", camera="—")),
]
DEFAULT_CAPS = dict(vendor="unknown", kind="video", max_dur=10, res="720p,1080p", inputs="text, image", camera="—")

# Seedance 全系需要签署 Asset Library User Agreement（仪表盘手动签，无 API 端点）
SEEDANCE_AGREEMENT_URL = "https://powertokens.ai/assets-library?tab=1"


def resolve_caps(mid, kind="video"):
    l = mid.lower()
    if kind == "seedance":
        src = SEEDANCE_CAPS
    elif kind == "image":
        src = IMAGE_MODEL_CAPS
    else:
        src = MODEL_CAPS
    for sub, caps in src:
        if sub in l:
            return caps
    return DEFAULT_CAPS


# ---------- 模型类型识别 ----------
def is_video_model(mid):
    l = mid.lower()
    if "image" in l:
        return False
    if "seedream" in l or "dola-seedream" in l:   # 图片模型，排除出视频
        return False
    if is_seedance(l):
        return False   # Seedance 走独立类型，不计入普通 video
    if "video" in l or "hailuo" in l or "vidu" in l or "happyhorse" in l:
        return True
    if "kling-v" in l:
        return True
    if "wan" in l and any(k in l for k in ("t2v", "i2v", "r2v", "kf2v")):
        return True
    return False


def is_image_model(mid):
    l = mid.lower()
    if "image" in l:
        return True
    if "seedream" in l or "dola-seedream" in l:
        return True
    return False


def is_seedance(mid):
    """模型 ID 含 seedance 即归入 Seedance 独立类型（全系 1.0/1.5/2.0/2.5，含 Mini/Fast 变体）。"""
    return "seedance" in mid.lower()


def kind_of_model(mid):
    if is_image_model(mid):
        return "image"
    if is_seedance(mid):
        return "seedance"
    if is_video_model(mid):
        return "video"
    return "unknown"


# ---------- 消耗估算 ----------
def catalog_price_usd(model, catalog_models=None, kind="video"):
    """T03：从 catalog 读 5s/720p 基准价，返回 (price_usd, source)；无来源返回 (None, None)。

    只读、纯函数、零联网。价来源带 _source（documented/verified），按 U10 以实扣为准、
    预估只作参考。catalog_models 缺省时读本地 catalog（读不到返回空，不抛）。
    """
    models = catalog_models if catalog_models is not None else load_catalog_models()
    entry = (models or {}).get(model) or {}
    media = ((entry.get("capabilities") or {}).get("media") or {})
    price = media.get("price") or {}
    val = price.get("usd_5s_720p")
    src = price.get("_source")
    if isinstance(val, (int, float)) and val > 0:
        return float(val), src
    return None, None


def estimate_cost(model, duration, resolution, kind="video", catalog_models=None,
                  prefer_catalog=True):
    """返回该次生成的预估消耗（美元），未知模型返回 None。

    T03：默认优先读 catalog 带 _source 的价格（wan3.0-video 现为 0.5 verified），
    读不到回落写死表；prefer_catalog=False 可强制走旧写死表（兼容/单测）。
    函数签名向后兼容：新增参数都在末尾且有默认值。
    """
    if not model:
        return None
    lm = model.lower()
    base = None
    if prefer_catalog and kind in ("video", "seedance"):
        base, _src = catalog_price_usd(model, catalog_models=catalog_models, kind=kind)
    if base is None:
        if kind == "seedance":
            table = PRICE_SEEDANCE_5S_720P
        elif kind == "image":
            table = IMAGE_PRICE
        else:
            table = PRICE_VIDEO_5S_720P
        for key in sorted(table, key=len, reverse=True):
            if key in lm:
                base = table[key]
                break
    if base is None:
        return None
    if kind == "image":
        return round(base, 3)
    factor = max(float(duration or 5), 1) / 5.0
    if "1080" in (resolution or ""):
        factor *= 2.0
    return round(base * factor, 3)


def print_cost(model, duration, resolution, kind="video"):
    c = estimate_cost(model, duration, resolution, kind)
    if c is None:
        sys.stderr.write(
            f"[credits] 本次预估消耗：未知（{model} 不在单价表；"
            f"可在 PRICE_VIDEO_5S_720P / PRICE_SEEDANCE_5S_720P / IMAGE_PRICE 补充，或 PT 开放消耗接口后直读真值）\n"
        )
    else:
        unit = "张" if kind == "image" else f"{duration}s·{resolution}"
        sys.stderr.write(
            f"[credits] 本次预估消耗：约 ${c}（{model} · {unit}，"
            f"按 PT 预扣单价估算；PT 额度按美元计）\n"
        )


def print_generation_summary(model, kind, duration, resolution, prompt, outputs, urls, seed=None, ratio=None):
    parts = [f"模型={model}", f"类型={kind}"]
    if kind == "video":
        parts.append(f"时长={duration}s")
    parts.append(f"分辨率={resolution}")
    if ratio:
        parts.append(f"比例={ratio}")
    if seed is not None:
        parts.append(f"种子={seed}")
    if prompt:
        parts.append(f"提示词={prompt}")
    if outputs:
        parts.append(f"输出={', '.join(str(o) for o in outputs)}")
    sys.stderr.write("[params] 生成参数：" + " · ".join(parts) + "\n")
    print_cost(model, duration, resolution, kind)
    for u in urls:
        sys.stderr.write(f"[result] 链接：{u}\n")


KEY_MISSING_MSG = f"""[需要 API Key] 还没配置 PowerTokens Key。
获取 Key 三步：打开 {ONBOARD_URL} → 注册/登录 → 充值后创建 Key（形如 sk-pt-xxxx）。
任选一种配置：
  1) 环境变量（推荐，任何 agent 通用）：export POWERTOKENS_API_KEY=sk-pt-xxxx
  2) 命令行保存：python pt_video.py config --set-key "sk-pt-xxxx"
  3) 手动编辑 {CONFIG_PATH} 加 "api_key": "sk-pt-xxx"
"""

ONBOARD_GUIDE = f"""[上手引导] 欢迎使用 PowerTokens 图片+视频生成，上手前 3 件事：
1) 配置 Key：打开 {ONBOARD_URL} → 注册/登录 → 充值 → 创建 Key（形如 sk-pt-xxxx）
   → 设环境变量 POWERTOKENS_API_KEY，或跑：python pt_video.py config --set-key "sk-pt-xxxx"
2) 指定模型：直接写模型名（如「用 kling-v2-1-master 生成视频」「用 seedream-5-0 生成图片」），
   不写则自动选（视频默认 auto；图片加 --type image -m auto）
3) 查看所有模型与定价：输入「获取所有模型」，或跑：python pt_video.py models --json
"""


# ---------- 配置 ----------
def load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(data):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except Exception:
        pass


# ---------- Key 注入 ----------
def resolve_key():
    env_key = os.environ.get("POWERTOKENS_API_KEY")
    if env_key:
        return env_key
    return load_config().get("api_key", "")


# ---------- 官方 REST 调用（仅标准库）----------
# T08：请求编号逐笔对账。SSOT §15/C4 确认 X-Oneapi-Request-Id 即 request id，
# logs/query 三键（request_id/task_id/consume_id）可查同一条。每次响应都记一笔，
# 供命令行与网关（复用本模块）按编号对账；仅为旁路记录，不改变正常返回语义。
REQUEST_ID_HEADER = "X-Oneapi-Request-Id"
_REQUEST_LOG = []
LAST_SUBMIT_REQUEST_ID = None   # 最近一次【视频提交】拿到的 request id，写进视频结果对象供对账
LAST_IMAGE_REQUEST_ID = None    # 复核 T08（2026-11-24）：图片同步请求独立编号，避免读到上一次视频提交的编号


def get_request_log():
    """返回已记录的请求编号日志（副本）；每项含 method/path/request_id。"""
    return [dict(x) for x in _REQUEST_LOG]


def clear_request_log():
    """清空请求编号日志（供长驻进程按批次对账）。"""
    _REQUEST_LOG.clear()


def _remember_request_id(resp_headers, method, path):
    """从响应头提取 request id 并记一笔；无该头则记 request_id=None。返回提取值或 None。"""
    rid = None
    try:
        if resp_headers is not None:
            for k, v in resp_headers.items():
                if k.lower() == REQUEST_ID_HEADER.lower():
                    rid = v
                    break
    except Exception:
        rid = None
    _REQUEST_LOG.append({"method": method, "path": path, "request_id": rid})
    return rid


def _headers(key, extra=None):
    h = {"Authorization": f"Bearer {key}", "User-Agent": "pt-media-skill",
         "Content-Type": "application/json", "Accept": "application/json"}
    if extra:
        h.update(extra)
    return h


def _api_call(method, path, key, body=None, timeout=30):
    url = PT_BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(key), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            rid = _remember_request_id(getattr(resp, "headers", None), method, path)
            raw = resp.read().decode("utf-8")
            result = json.loads(raw) if raw else {}
            if rid and isinstance(result, dict):
                result["_request_id"] = rid
            return result
    except urllib.error.HTTPError as e:
        _remember_request_id(getattr(e, "headers", None), method, path)
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8"))
            if isinstance(detail, dict):
                detail = detail.get("error") or detail.get("message") or json.dumps(detail, ensure_ascii=False)
        except Exception:
            pass
        raise ApiError(e.code, str(detail) or e.reason, headers=getattr(e, "headers", None))


class ApiError(Exception):
    """PT API 错误。

    headers 必须透出：429 的 Retry-After 是服务端给出的重试节奏，
    吞掉它就只能盲目指数退避，容易在限流窗口内反复撞击、放大限流。
    """

    def __init__(self, status, message, headers=None):
        self.status = status
        self.message = message
        # 复制成普通 dict（HTTPMessage / dict 都支持 items()），保留原始大小写
        self.headers = dict(headers.items()) if headers is not None else {}
        # 向后兼容：早期上层从 raw["headers"] 读取
        self.raw = {"headers": self.headers}
        super().__init__(f"HTTP {status}: {message}")


class ModelListUnavailable(ApiError):
    """T05：GET /v1/models 查询失败——可用性「未知」，不等于「不可用」。

    查询失败时 Key 下有哪些模型是不知道的，绝不能当成「该模型不可用」展示。
    仅在 strict=True 的调用路径（models 命令）抛出；其他调用点保持 fail-open 旧行为。
    """

    def __init__(self, message):
        super().__init__(0, message)


def list_key_models(key, kind="all", strict=False):
    """拉取该 Key / 分组可见的模型视图（/v1/models 反映 Key 限额与分组过滤）。

    2026-11-13 PT 书面答复确认此口径正确：列表按「Key 模型限额 → 分组启用模型 →
    HasPricingRuleCandidateForModel + IsModelNameListed 双重过滤」生成，是子集视图；
    **不在列表不等于模型下架或不可调用**（FIXLIST F1 旧「全量清单」口径已作废）。
    按 kind 过滤并排序。
    """
    try:
        data = _api_call("GET", "/v1/models", key, timeout=15)
    except ApiError as e:
        msg = f"查询 /v1/models 失败: {e}"
        if strict:
            raise ModelListUnavailable(msg) from e
        sys.stderr.write(f"[warn] {msg}\n")
        return []
    except Exception as e:
        msg = f"查询 /v1/models 异常: {e}"
        if strict:
            raise ModelListUnavailable(msg) from e
        sys.stderr.write(f"[warn] {msg}\n")
        return []
    ids = []
    items = data.get("data", data if isinstance(data, list) else [])
    for item in items:
        mid = item.get("id") if isinstance(item, dict) else str(item)
        if mid:
            ids.append(mid)
    if kind == "video":
        out = [m for m in ids if is_video_model(m)]
    elif kind == "image":
        out = [m for m in ids if is_image_model(m)]
    elif kind == "seedance":
        out = [m for m in ids if is_seedance(m)]
    else:
        out = ids
    out.sort(key=lambda m: (_priority_rank(m, kind), 0 if "fast" in m.lower() else 1))
    return out


# ---------- 模型识别与 auto 解析 ----------
def _priority_rank(mid, kind="video"):
    l = mid.lower()
    if kind == "seedance":
        prio = AUTO_PRIORITY_SEEDANCE
    elif kind == "image":
        prio = AUTO_PRIORITY_IMAGE
    else:
        prio = AUTO_PRIORITY_VIDEO
    for rank, hint in enumerate(prio):
        if hint in l:
            return rank
    return len(prio)


def auto_candidates(key, kind="video", ignore_default=False):
    """auto 模式的候选模型列表（有序）。config 默认模型先过 live 校验，失效则忽略。

    ignore_default=True 表示用户**显式**写了 -m auto，跳过 config 的 default_model，
    走完整多候选自动排序（保留失败自动换模型的降级链路）。
    """
    cfg_default = "" if ignore_default else load_config().get("default_model", "")
    models = list_key_models(key, kind)
    if cfg_default and cfg_default.lower() != "auto":
        same_kind = (
            (kind == "video" and is_video_model(cfg_default)) or
            (kind == "image" and is_image_model(cfg_default)) or
            (kind == "seedance" and is_seedance(cfg_default))
        )
        if same_kind:
            hit = next((v for v in models if v.lower() == cfg_default.lower()), None)
            if hit:
                return [hit]
            if models:
                sys.stderr.write(
                    f"[precheck] 配置的默认模型 {cfg_default} 已不在你的可用列表，忽略它改用 auto 排序\n"
                )
            else:
                return [cfg_default]
    if models:
        return models
    if kind == "seedance":
        return [AUTO_FALLBACK_SEEDANCE]
    if kind == "image":
        return [AUTO_FALLBACK_IMAGE]
    return [AUTO_FALLBACK_VIDEO]


def suggest_similar(model, vids, n=3):
    lm = model.lower()
    close = difflib.get_close_matches(model, vids, n=n, cutoff=0.35)
    seen = {v.lower() for v in close}
    prefix = lm.split("-")[0]
    if len(prefix) >= 3:
        for v in vids:
            if v.lower().startswith(prefix) and v.lower() not in seen:
                close.append(v)
                seen.add(v.lower())
    return close[:n]


def precheck_explicit_model(key, model, kind="video"):
    """显式 -m 的预校验：只放行 live 列表里存在的模型。返回 (ok?, 最终ID 或 (候选, 总数))。"""
    vids = list_key_models(key, kind)
    if not vids:
        sys.stderr.write("[precheck] 拉不到模型列表（网络/接口异常），跳过校验，照常执行\n")
        return (True, model)
    if model in vids:
        return (True, model)
    ci = next((v for v in vids if v.lower() == model.lower()), None)
    if ci:
        sys.stderr.write(f"[precheck] 模型大小写已纠正: {model} -> {ci}\n")
        return (True, ci)
    return (False, (suggest_similar(model, vids), len(vids)))


# ---------- 模型目录（定价 + 参数）----------
def build_catalog(key, kind="all", strict=False):
    """返回模型目录列表：合并静态单价表与 live 可用列表。

    strict=True 时 live 列表查询失败抛 ModelListUnavailable（可用性未知），
    供 models 命令区分「查询失败」与「确实无模型」；默认 False 保持旧行为。
    """
    live = list_key_models(key, kind, strict=strict) if key else []
    live_set = {m.lower() for m in live}
    rows, seen = [], set()

    def add_from_table(table, caps_src):
        for mid, price in table.items():
            k = kind_of_model(mid) or "video"
            if kind != "all" and k != kind:
                continue
            caps = resolve_caps(mid, k)
            avail = "yes" if mid.lower() in live_set else ("n/a" if not key else "no")
            rows.append({
                "model": mid, "kind": k,
                "price_5s_720p": price if k in ("video", "seedance") else None,
                "max_dur_s": caps["max_dur"], "resolution": caps["res"],
                "inputs": caps["inputs"], "camera_control": caps["camera"],
                "available": avail,
            })
            seen.add(mid.lower())

    add_from_table(PRICE_VIDEO_5S_720P, MODEL_CAPS)
    add_from_table(PRICE_SEEDANCE_5S_720P, SEEDANCE_CAPS)
    add_from_table(IMAGE_PRICE, IMAGE_MODEL_CAPS)

    for mid in live:
        if mid.lower() in seen:
            continue
        k = kind_of_model(mid) or "video"
        caps = resolve_caps(mid, k)
        rows.append({
            "model": mid, "kind": k, "price_5s_720p": None,
            "max_dur_s": caps["max_dur"], "resolution": caps["res"],
            "inputs": caps["inputs"], "camera_control": caps["camera"],
            "available": "yes",
        })
        seen.add(mid.lower())
    rows.sort(key=lambda r: (
        0 if r["available"] == "yes" else (1 if r["available"] == "n/a" else 2),
        r["kind"],
        -(r["price_5s_720p"] or 0),
    ))
    return rows


def cmd_models(argv):
    parser = argparse.ArgumentParser(prog="pt_video.py models")
    parser.add_argument("-t", "--type", default="all", choices=["video", "image", "seedance", "all"])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    key = resolve_key()
    try:
        rows = build_catalog(key, args.type, strict=bool(key))
    except ModelListUnavailable as e:
        # T05：查询失败 = 可用性「未知」，绝不能显示成「不可用」还报成功（exit 0）
        sys.stderr.write(f"[error] {e.message}\n")
        sys.stderr.write("[error] 模型可用性未知（查询失败，非'不可用'）；请稍后重试或检查 Key/网络\n")
        if args.json:
            print(json.dumps({"ok": False, "availability": "unknown",
                              "error": str(e.message)}, ensure_ascii=False))
        return 4
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"{'MODEL':<34} {'KIND':<7} {'PRICE(5s/720p)':<15} {'MAX_DUR':<8} {'RES':<13} {'AVAIL':<5} INPUTS")
    print("-" * 120)
    for r in rows:
        price = f"${r['price_5s_720p']}" if r["price_5s_720p"] is not None else "n/a"
        dur = "—" if not r["max_dur_s"] else f"{r['max_dur_s']}s"
        print(f"{r['model']:<34} {r['kind']:<7} {price:<15} {dur:<8} "
              f"{r['resolution']:<13} {r['available']:<5} {r['inputs']}")
    if not key:
        print("\n[提示] 未配置 Key，以上为公开目录（价格基于 PT 预扣单价估算）。"
              "配置 Key 后 AVAIL 列显示你账号当前可用的模型（yes）。")
    print("[说明] 视频价格系数：时长线性（5s→10s = ×2），1080p = 720p ×2。图片按张计费，单价待补充（标 n/a）。"
          "参数以 powertokens.ai 仪表盘为准。")
    return 0


# ---------- 子命令 ----------
def cmd_check():
    key = resolve_key()
    cfg = load_config()
    status = {
        "key_configured": bool(key),
        "key_masked": ("****" + key[-4:]) if key else None,
        "default_model": cfg.get("default_model") or "auto",
        "onboard_url": ONBOARD_URL,
        "engine": "official REST (video: POST /v1/videos + poll; image: POST /v1/images/generations sync)",
    }
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if not key:
        sys.stderr.write(ONBOARD_GUIDE)
        sys.stderr.write(KEY_MISSING_MSG)
        return 2
    return 0


def cmd_config(argv):
    parser = argparse.ArgumentParser(prog="pt_video.py config")
    parser.add_argument("--set-key")
    parser.add_argument("--set-model")
    parser.add_argument("--set-access-token", help="账户域个人访问令牌（pt_live_ 开头，用于 balance/usage/logs）")
    parser.add_argument("--set-key-id", help="账户域 key_id（纯数字，balance/usage 必需）")
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args(argv)
    cfg = load_config()
    if args.set_key:
        cfg["api_key"] = args.set_key
        save_config(cfg)
        print(f"[ok] 已保存 api_key 到 {CONFIG_PATH}（权限 0600）")
    if args.set_access_token:
        cfg["access_token"] = args.set_access_token
        save_config(cfg)
        print(f"[ok] 已保存 access_token 到 {CONFIG_PATH}（权限 0600；仅显示尾4位：****{args.set_access_token[-4:]}）")
    if args.set_key_id:
        raw = str(args.set_key_id).strip()
        if not raw.isdigit():
            sys.stderr.write("[error] key_id 必须是纯数字（PT 没有接口能反查，请到仪表盘查看）\n")
            return 2
        cfg["key_id"] = int(raw)
        save_config(cfg)
        print(f"[ok] 已保存 key_id={cfg['key_id']} 到 {CONFIG_PATH}")
    if args.set_model:
        cfg["default_model"] = args.set_model
        save_config(cfg)
        print(f"[ok] 已保存 default_model={args.set_model}（generate 未写 -m 时优先用它；auto=按账号自动选）")
    if args.show or (not any([args.set_key, args.set_model, args.set_access_token, args.set_key_id])):
        masked = ("****" + cfg["api_key"][-4:]) if cfg.get("api_key") else "(未设置)"
        tok = cfg.get("access_token", "")
        masked_tok = (f"pt_live_****{tok[-4:]}（len={len(tok)}）" if tok.startswith("pt_live_")
                      else ("****" + tok[-4:] if tok else "(未设置)"))
        print(json.dumps({"api_key": masked,
                          "access_token": masked_tok,
                          "key_id": cfg.get("key_id"),
                          "default_model": cfg.get("default_model", "") or "auto",
                          "config_path": str(CONFIG_PATH), "onboard": ONBOARD_URL},
                         ensure_ascii=False, indent=2))
    return 0


def cmd_auto_model(argv):
    parser = argparse.ArgumentParser(prog="pt_video.py auto-model")
    parser.add_argument("--type", default="video", choices=["video", "image", "seedance"])
    args = parser.parse_args(argv)
    key = resolve_key()
    if not key:
        sys.stderr.write(KEY_MISSING_MSG)
        return 2
    cands = auto_candidates(key, kind=args.type)
    print(json.dumps({"kind": args.type, "model": cands[0],
                      "resolved_by": "config 默认模型" if len(cands) == 1 and load_config().get("default_model") else "按 Key 额度自动选择",
                      "candidates": cands[:AUTO_MAX_TRIES]}, ensure_ascii=False))
    return 0


def cmd_estimate(argv):
    parser = argparse.ArgumentParser(prog="pt_video.py estimate")
    parser.add_argument("-m", "--model", required=True)
    parser.add_argument("-d", "--duration", type=int, default=5)
    parser.add_argument("-r", "--resolution", default="720p")
    parser.add_argument("--ratio", default="adaptive", help="视频比例，参与实时估价（默认与 generate 的 adaptive 一致）")
    parser.add_argument("--aspect-ratio", default="1:1", help="图片宽高比，参与实时估价（默认与 generate 的 1:1 一致）")
    parser.add_argument("--size", default=None, help="图片尺寸档位（如 2K/3K），参与实时估价（复核 0.9.3 #2）")
    parser.add_argument("-n", "--n", type=int, default=None, help="图片张数，参与实时估价")
    parser.add_argument("--type", default="auto", choices=["video", "image", "auto"])
    parser.add_argument("--live", action="store_true",
                        help="有 pt_live_ 凭证时走账户域实时预估（不扣费）；失败自动回落 catalog 估算")
    args = parser.parse_args(argv)
    kind = args.type
    if kind == "auto":
        kind = kind_of_model(args.model)
    source = "builtin_table"
    c = estimate_cost(args.model, args.duration, args.resolution, kind)
    # T03：优先用 catalog 带 _source 的价格覆盖（wan3.0-video 0.5 verified）
    cat_price, cat_src = catalog_price_usd(args.model, kind=kind)
    if cat_price is not None and kind in ("video", "seedance"):
        factor = max(float(args.duration or 5), 1) / 5.0
        if "1080" in (args.resolution or ""):
            factor *= 2.0
        c = round(cat_price * factor, 3)
        source = f"catalog:{cat_src or 'unknown'}"
    live_fallback = None
    if args.live:
        # 复核 0.9.3 #2：估价条件与生成同源——图片带 size/aspect_ratio/n，视频带 ratio。
        live = _live_estimate(args.model, kind, args.duration, args.resolution,
                              ratio=args.ratio, n=args.n,
                              size=getattr(args, "size", None),
                              aspect_ratio=getattr(args, "aspect_ratio", None))
        if live is not None:
            c, source = live, "live:estimate"
        else:
            # 复核 0.9.2 #3：实时估价失败时明确告知回落，不能让调用方误以为是实时值
            live_fallback = "实时估价不可用，已回落到目录价/内置表估算"
            sys.stderr.write(f"[estimate] {live_fallback}\n")
    if c is None:
        print(f"[estimate] {args.model}: 单价表无此模型，无法估算（已知模型见 `models`）")
        return 1
    out = {"model": args.model, "kind": kind, "duration_s": args.duration,
           "resolution": args.resolution, "est_cost_usd": c,
           "price_source": source, "note": "预估仅供参考，以实际扣费为准（U10）"}
    if live_fallback:
        out["live_fallback"] = live_fallback
    print(json.dumps(out, ensure_ascii=False))
    return 0


def build_billable_request(model, kind, duration, resolution, ratio=None, n=None,
                           size=None, aspect_ratio=None):
    """构造与真实生成链路同源的「计费相关参数」（复核 0.9.2 #3 / 0.9.3 #2）。

    0.9.3 #2 修正：图片分支不再自己写第二套映射，直接复用 build_image_request_body——
    与 generate_image / 网关完全相同的一条构造器，字段名（size/aspect_ratio/n）天然同源；
    图片请求不再混入 duration/resolution（那是视频字段，复核报告实测为口径错位）。
    视频/seedance 分支继续用 resolve_media_fields，与 generate_video 的 gen_fields 同源。
    PT 估价接口字段名无官方来源（R4）：视频额外双写 duration/resolution 兼容字段不猜。
    """
    is_vid = kind in ("video", "seedance")
    if not is_vid:
        # 与 generate_image 同一构造器；估价不需要 response_format/seed/ref_image。
        body, _unsupported = build_image_request_body(
            model, "", aspect_ratio=aspect_ratio or "1:1", n=n, size=size)
        body.pop("prompt", None)  # 估价只看计费条件，prompt 不参与
        return body
    req_ratio = ratio or "adaptive"  # 与 generate 默认 adaptive 一致（0.9.2 曾写死 16:9）
    mapped, _ = resolve_media_fields(model, {
        "duration_s": str(duration),
        "resolution": str(resolution or "").upper(),
        "aspect_ratio": req_ratio,
    })
    req = {"model": model, **mapped}
    req["duration"] = duration
    req["resolution"] = resolution
    return req


def _live_estimate(model, kind, duration, resolution, ratio=None, n=None,
                   size=None, aspect_ratio=None):
    """T03：账户域实时预估（POST /v1/billing/estimate，不扣费）。缺凭证/缺模块/出错返回 None。

    保护式 import：pt_video.py 可独立分发、不依赖 pt-gateway 目录；独立副本没有
    account_client 时自动跳过，回落 catalog 估算，绝不因此中断命令。
    复核 0.9.2 #3：请求参数改用 build_billable_request，与真实生成链路同源。
    """
    token, key_id = resolve_account_credentials()
    if not token or not key_id or not str(key_id).strip().isdigit():
        return None
    try:
        import sys as _sys
        gw = str(Path(__file__).resolve().parent / "pt-gateway")
        if gw not in _sys.path:
            _sys.path.insert(0, gw)
        from account_client import AccountClient
        # 复核 T03（2026-11-24）：用已解析好的凭证直接构造客户端——config.json 里的
        # token/key_id 也要能用，不能只靠 from_env() 读环境变量（否则配置里存的令牌丢失）。
        client = AccountClient(token=token, key_id=int(key_id))
        # endpoint 与请求体口径与真实生成链路一致（T01 复用 model→submit 解析）。
        endpoint = resolve_submit_endpoint(model, kind) if kind in ("video", "seedance") else resolve_submit_endpoint(model, "image")
        req = build_billable_request(model, kind, duration, resolution, ratio=ratio, n=n,
                                     size=size, aspect_ratio=aspect_ratio)
        resp = client.estimate_cost(model=model, endpoint=endpoint, request=req,
                                    key_id=int(key_id),
                                    kind="video" if kind in ("video", "seedance") else kind)
        if resp.get("ok") and isinstance(resp.get("data"), dict):
            data = resp["data"]
            # PT 实际返回 {estimated_quota, quota_per_unit}（积分口径）；按 credits_per_usd 换算。
            quota = data.get("estimated_quota")
            per_unit = data.get("quota_per_unit") or 1000  # catalog currency.credits_per_usd=1000（official）
            if isinstance(quota, (int, float)) and isinstance(per_unit, (int, float)) and per_unit:
                return round(float(quota) / float(per_unit), 6)
            for k in ("usd", "cost_usd", "total_usd", "estimated_usd", "price"):
                if isinstance(data.get(k), (int, float)):
                    return float(data[k])
    except Exception:
        pass
    return None


# ---------- 生成：媒体构造（视频/图片/seedance 共用，支持 asset://）----------
def build_media(args, force_asset=False):
    media = []
    if args.first_frame:
        media.append({"type": "first_frame", "url": args.first_frame})
    if args.last_frame:
        media.append({"type": "last_frame", "url": args.last_frame})
    for u in args.ref_image or []:
        media.append({"type": "reference_image", "url": u})
    for u in args.ref_video or []:
        media.append({"type": "reference_video", "url": u})
    for u in args.ref_audio or []:
        media.append({"type": "reference_audio", "url": u})
    # 校验：REST 接口 media[].url 必须是公网 http(s) 或资产库 asset://（Seedance 协议素材）
    for m in media:
        u = m["url"]
        if force_asset:
            # Seedance 独立类型：素材只收资产库 asset://，强制契约，避免误传 http 绕过协议
            if not u.startswith("asset://"):
                raise ApiError(0, f"Seedance 素材必须是资产库 asset:// 格式（如 asset://<ASSET_ID>），收到：{u}")
        else:
            if not (u.startswith("http://") or u.startswith("https://") or u.startswith("asset://")):
                raise ApiError(0, f"媒体地址必须是公网 http(s) 或资产库 asset:// 格式，收到：{u}")
    return media


# ---------- 视频生成：提交 + 自动轮询 ----------
def submit_task(key, body, endpoint=None):
    """POST 提交视频任务，返回 task_id（str）。提交 HTTP 超时 = SUBMIT_TIMEOUT。

    endpoint 由调用方按 catalog 的 model→submit 解析后传入；不传则回落 VIDEO_SUBMIT，
    保持旧签名兼容（北极星副本 / 外部脚本按旧方式调用行为不变）。
    """
    resp = _api_call("POST", endpoint or VIDEO_SUBMIT, key, body=body, timeout=SUBMIT_TIMEOUT)
    global LAST_SUBMIT_REQUEST_ID
    LAST_SUBMIT_REQUEST_ID = resp.get("_request_id")
    tid = resp.get("id") or resp.get("task_id")
    if not tid:
        raise ApiError(0, f"提交响应缺少 task_id：{json.dumps(resp, ensure_ascii=False)}")
    return tid, resp


def poll_task(key, task_id, interval=POLL_INTERVAL, timeout=POLL_TIMEOUT):
    """每 interval 秒查询一次，直到 succeeded/completed 或 failed/cancelled/超时。
    返回成功时的任务对象（含 metadata.url）。

    已知短板（见 docs/pitfall-archive.md A.02/A.03）：未做 429 退避 / 5xx 熔断 / 未识别状态熔断，
    遇网关抖动可能静默空转到 POLL_TIMEOUT。后续可加三重熔断。
    """
    deadline = time.time() + timeout
    last_status = None
    while True:
        try:
            task = _api_call("GET", VIDEO_POLL.format(id=task_id), key, timeout=30)
        except ApiError as e:
            sys.stderr.write(f"[poll] 查询失败 {e}（{int(deadline - time.time())}s 内重试）\n")
            if time.time() >= deadline:
                raise ApiError(0, f"轮询超时（>{timeout}s），最后状态={last_status}；稍后可单独 GET /v1/videos/{task_id} 复查")
            time.sleep(interval)
            continue
        status = (task.get("status") or "").lower()
        last_status = status
        progress = task.get("progress")
        if status in ("succeeded", "completed"):
            return task
        if status in ("failed", "cancelled"):
            err = task.get("error") or (task.get("metadata") or {}).get("error") \
                or f"任务结束于异常状态：{status}"
            raise ApiError(0, str(err))
        bar = f"进度 {progress}%" if progress is not None else status
        sys.stderr.write(f"[poll] task {task_id} 状态={status} {bar}（{int(deadline - time.time())}s 内继续）\n")
        if time.time() >= deadline:
            raise ApiError(0, f"轮询超时（>{timeout}s），最后状态={status}；稍后可单独 GET /v1/videos/{task_id} 复查")
        time.sleep(interval)


def result_url(task):
    meta = task.get("metadata") or {}
    return meta.get("url") or meta.get("content_url") or task.get("url")


def _trusted_pt_host(url):
    """T06 安全：仅 PT 官方域名允许携带 API Key。
    产物地址多为 PT 自有 R2 公共桶（pub-*.r2.dev）或上游签名链接（OSS/S3），
    这些地址绝不能收到 Authorization——否则等于把 Key 交给第三方。
    精确匹配 powertokens.ai 及其子域（api./docs. 等），防止 evil-powertokens.ai 绕过。"""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return False
    return host == "powertokens.ai" or host.endswith(".powertokens.ai")


# ---------- T07：产物真实后缀识别（魔数优先，Content-Type 兜底）----------
# 旧行为：generate 默认把任何产物存成 .png，JPEG/WebP/GIF 被错标为 .png。
# 这里只做"识别"，不改 download_binary 的落盘与安全逻辑；显式 -o 指定后缀时仍尊重用户。
_MAGIC_EXTS = (
    (b"\x89png\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"gif87a", ".gif"),
    (b"gif89a", ".gif"),
    (b"bm", ".bmp"),
    (b"ri", ".webp"),   # RIFF....WEBP，仅凭前两字节不够，下面复核 WEBP 标记
)
_CT_EXT_MAP = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
    "image/webp": ".webp", "image/gif": ".gif", "image/bmp": ".bmp",
    "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
    "application/octet-stream": None,   # 无信息量，不据此判断
}


def detect_file_extension(path=None, head=None, content_type=None, default=".png"):
    """按魔数 / Content-Type 推断真实后缀（含前导点）；判断不了返回 default。

    纯函数、零依赖、零联网。优先级：魔数 > Content-Type > default。
    - head: 文件头字节（至少 12 字节即可）；给了 path 则自动读前 16 字节。
    - content_type: HTTP Content-Type，可带 charset 参数（如 'image/jpeg; charset=binary'）。
    """
    data = head
    if data is None and path:
        try:
            with open(path, "rb") as f:
                data = f.read(16)
        except OSError:
            data = None
    data = data or b""
    low = data[:16].lower()
    # RIFF....WEBP 需要第 8-12 字节确认，单独判断避免把其它 RIFF 误判为 webp
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return ".mp4"
    for magic, ext in _MAGIC_EXTS:
        if ext == ".webp":
            continue  # 已在上面精确判断
        if low.startswith(magic):
            return ext
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if ct in _CT_EXT_MAP and _CT_EXT_MAP[ct]:
        return _CT_EXT_MAP[ct]
    return default


def download_binary(url, path, key):
    """下载生成产物。安全约束（T06）：默认裸请求，Key 只发给 PT 官方域名。

    历史 bug：先带 Bearer key 请求产物 URL，失败再裸请求——导致每次下载都先把
    API Key 发给第三方产物地址（R2/OSS）。修正为先裸请求；只有目标是可信 PT
    域名且裸请求明确 401/403 时，才带 key 重试一次。第三方地址永不接触 Key。

    可靠性约束（T02）：先写同目录 ``<path>.part`` 临时文件，校验完整（非空 +
    Content-Length 一致 + 非 HTML/JSON 错误正文）后再原子改名为目标文件；任何失败都
    删除临时文件、绝不留下残缺的目标文件，也不向调用方返回成功。

    复核加固（2026-11-24）：
      - 鉴权重试仅允许 HTTPS（拒绝降级到明文 HTTP 泄露 Key）；
      - Authorization 走 ``add_unredirected_header``，urllib 原生重定向不会把它
        转发到其它来源（跨来源跳转不再带 Key）；
      - 完整性校验识别 JSON 错误正文（如 {"error":"..."}）与非媒体 Content-Type。
    """
    def _save(req):
        tmp = str(path) + ".part"
        total = 0
        expected = None
        ctype = ""
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                headers = getattr(r, "headers", None)
                if headers is not None:
                    try:
                        cl = headers.get("Content-Length")
                        expected = int(cl) if cl is not None else None
                    except (TypeError, ValueError):
                        expected = None
                    try:
                        ctype = (headers.get("Content-Type") or "").lower()
                    except Exception:
                        ctype = ""
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                with open(tmp, "wb") as f:
                    while True:
                        chunk = r.read(DOWNLOAD_CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                        total += len(chunk)
            # 完整性校验：空文件 / 长度不符 / HTML 或 JSON 错误正文一律判失败
            if total == 0:
                raise ApiError(0, f"下载产物为空：{url}")
            if expected is not None and total != expected:
                raise ApiError(0, f"下载不完整：实际 {total} 字节，声明 {expected} 字节：{url}")
            with open(tmp, "rb") as f:
                head = f.read(512)
            probe = head.lstrip().lstrip(b"\xef\xbb\xbf").lower()
            if probe.startswith(b"<") and (b"<html" in probe[:200] or b"<!doctype" in probe[:200]):
                raise ApiError(0, f"下载内容是 HTML 错误页而非产物：{url}")
            if probe[:1] in (b"{", b"["):
                raise ApiError(0, f"下载内容是 JSON 错误正文而非产物：{url}")
            if "application/json" in ctype or "text/html" in ctype:
                raise ApiError(0, f"下载 Content-Type 非媒体（{ctype}）：{url}")
            os.replace(tmp, path)  # 原子落盘：成功前目标文件始终不存在/不变
        except Exception:
            # 任何失败都清掉临时文件，绝不留下残缺文件供上层误用
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise

    bare = urllib.request.Request(url, headers={"User-Agent": "pt-media-skill"})
    try:
        _save(bare)
        return True
    except urllib.error.HTTPError as e:
        # 只有明确 401/403、目标是 PT 官方域名、且是 HTTPS，才允许带 key 重试一次；
        # 第三方地址即便 401/403 也绝不携带 Key；明文 HTTP 一律不重试（防降级泄露）。
        https = (urllib.parse.urlparse(url).scheme or "").lower() == "https"
        if e.code in (401, 403) and https and _trusted_pt_host(url) and key:
            h2 = _headers(key)
            auth = h2.pop("Authorization", None)
            req2 = urllib.request.Request(url, headers=h2)
            if auth:
                # 关键：unredirected header 不随 urllib 重定向复制到其它来源的 URL，
                # 因此官方地址鉴权后跳转到第三方时，Authorization 不会被带过去。
                req2.add_unredirected_header("Authorization", auth)
            _save(req2)
            return True
        raise


# ---------- 资产库上传（Seedance 图生视频必须先把素材上传拿 asset://）----------
def upload_asset(key, path, group_name=None, poll_timeout=120):
    """上传本地文件到 PT 资产库，返回 asset_id（引用为 asset://<id>）。上传免费，不扣 credits。"""
    import uuid
    boundary = "----ptboundary" + uuid.uuid4().hex
    body = b""
    if group_name:
        body += f'--{boundary}\r\n'.encode()
        body += b'Content-Disposition: form-data; name="group_name"\r\n\r\n'
        body += group_name.encode() + b"\r\n"
    fn = os.path.basename(path)
    body += f'--{boundary}\r\n'.encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{fn}"\r\n'.encode()
    body += b'Content-Type: application/octet-stream\r\n\r\n'
    with open(path, "rb") as f:
        body += f.read()
    body += b"\r\n"
    body += f'--{boundary}--\r\n'.encode()
    req = urllib.request.Request(
        ASSET_UPLOAD, data=body,
        headers={"Authorization": key, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ApiError(e.code, f"资产上传失败：{e.read().decode('utf-8', 'replace')}",
                       headers=getattr(e, "headers", None))
    if data.get("code") != 200:
        raise ApiError(data.get("code", 0), f"资产上传失败：{data}")
    task_id = (data.get("data") or {}).get("task_id")
    if not task_id:
        raise ApiError(0, f"上传未返回 task_id：{data}")
    # 轮询 get-asset-id（无需鉴权）
    deadline = time.time() + poll_timeout
    while True:
        try:
            with urllib.request.urlopen(ASSET_GET_ID.format(task_id), timeout=30) as r:
                j = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise ApiError(e.code, f"查询 asset_id 失败：{e.read().decode('utf-8', 'replace')}",
                           headers=getattr(e, "headers", None))
        aid = (j.get("data") or {}).get("asset_id")
        if aid:
            return aid
        if time.time() >= deadline:
            raise ApiError(0, f"轮询 asset_id 超时（>{poll_timeout}s），task_id={task_id}")
        time.sleep(2)


def cmd_upload(argv):
    parser = argparse.ArgumentParser(prog="pt_video.py upload")
    parser.add_argument("-f", "--file", required=True, help="本地文件路径")
    parser.add_argument("--group-name", default=None, help="资产组名（可选，自动创建/复用同名组）")
    args = parser.parse_args(argv)
    key = resolve_key()
    if not key:
        sys.stderr.write(KEY_MISSING_MSG)
        return 2
    if not os.path.isfile(args.file):
        sys.stderr.write(f"[error] 文件不存在：{args.file}\n")
        return 1
    sys.stderr.write(f"[upload] POST {ASSET_UPLOAD}  file={os.path.basename(args.file)}\n")
    aid = upload_asset(key, args.file, group_name=args.group_name)
    print(json.dumps({"ok": True, "asset_id": aid, "asset_ref": f"asset://{aid}"}, ensure_ascii=False))
    sys.stderr.write(f"[done] 资产已上传，引用：asset://{aid}\n")
    return 0


def generate_video(key, model, args, timeout, force_asset=False):
    """提交单个视频模型并轮询；成功返回 (url, output_path)。force_asset=Seedance 时媒体只收 asset://。"""
    media = build_media(args, force_asset=force_asset)
    if not args.prompt and not media:
        raise ApiError(0, "必须提供 -p/--prompt 或至少一种媒体输入（--first-frame/--ref-image...）")
    # T09：友好字段 -> PT 真实字段名（按 catalog param_names）。默认模型映射恒等，
    # body 与历史逐字节一致；kling 等模型字段名不同才被正确重命名，避免参数被服务端忽略。
    gen_fields, unsupported = resolve_media_fields(model, {
        "duration_s": str(args.duration),        # 服务端为 enum<string>，必须传字符串
        "resolution": args.resolution.upper(),
        "aspect_ratio": args.ratio,
    })
    if unsupported:
        # 只提示不改值、不阻断（R4 不猜：无来源沿用历史字段名，让链路继续）。
        names = ", ".join(u["field"] for u in unsupported)
        sys.stderr.write(f"[fields] 模型 {model} 未在 catalog 声明参数映射：{names}（沿用历史字段名）\n")
    if force_asset:
        # Seedance（BytePlus 系）：文本必须放在 media 数组的 text 类型里，不认顶层 prompt；
        # 若用户传了图/视频媒体（首帧/参考），其 url 必须是资产库 asset://（外部 URL 不被接受）。
        text_part = {"type": "text", "text": args.prompt or ""}
        media = [text_part] + media if media else [text_part]
        body = {
            "model": model,
            "media": media,
            **gen_fields,
            "generate_audio": not args.no_audio,
            "prompt_extend": not args.no_prompt_extend,
            "watermark": args.watermark,
        }
    else:
        body = {
            "model": model,
            "prompt": args.prompt or "",
            **gen_fields,
            "generate_audio": not args.no_audio,
            "prompt_extend": not args.no_prompt_extend,
            "watermark": args.watermark,
        }
        if media:
            body["media"] = media
    if args.seed is not None:
        body["seed"] = args.seed

    submit_endpoint = resolve_submit_endpoint(model, "video")
    sys.stderr.write(f"[submit] POST {PT_BASE}{submit_endpoint}  model={model} seconds={args.duration} size={args.resolution.upper()} ratio={args.ratio}\n")
    task_id, _ = submit_task(key, body, endpoint=submit_endpoint)
    sys.stderr.write(f"[submit] 已提交，task_id={task_id}，开始轮询（每 {POLL_INTERVAL}s）...\n")
    task = poll_task(key, task_id, timeout=timeout)
    url = result_url(task)
    output = None
    if url and not args.no_download:
        out_dir = Path(args.output).resolve().parent if args.output else (SCRIPT_DIR / "out")
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = args.output or str(out_dir / f"{task_id}.mp4")
        try:
            download_binary(url, fname, key)
            output = fname
            sys.stderr.write(f"[download] 已保存：{fname}\n")
        except Exception as e:
            sys.stderr.write(f"[warn] 下载失败（链接仍可用）：{e}\n")
    return url, output, task_id


# ---------- 图片生成：同步提交 ----------
def generate_image(key, model, args, timeout):
    """POST /v1/images/generations 同步返回 data[].url。成功返回 (urls, output_paths)。"""
    # 复核 T09（2026-11-24）：统一按官方字段名构造请求体，参考图/尺寸真正送出；
    # 模型无官方字段映射的参数进 unsupported，明确告警而非静默丢弃（R4）。
    body, unsupported = build_image_request_body(
        model, args.prompt,
        aspect_ratio=getattr(args, "aspect_ratio", None),
        n=getattr(args, "n", None),
        response_format=getattr(args, "response_format", None),
        seed=getattr(args, "seed", None),
        ref_image=getattr(args, "ref_image", None) or None,
        size=getattr(args, "size", None) or None,
        # resolution 是视频参数，不进图片请求体；图片尺寸只看显式 --size。
    )
    for u in unsupported:
        sys.stderr.write(f"[warn] 参数 {u['field']}={u['value']!r} 无官方字段映射，未发送"
                         f"（{u['reason']}）：{model}\n")
    # 复核 0.9.2 #6：--strict-params 默认关闭（兼容旧行为）；开启时明确要求
    # （参考图/尺寸等）无法满足就不提交，避免花钱生成不符合要求的结果。
    if unsupported and getattr(args, "strict_params", False):
        names = ", ".join(f"{u['field']}={u['value']!r}" for u in unsupported)
        raise ApiError(0, f"模型 {model} 无法执行你明确指定的参数：{names}；"
                          f"已停止提交（关闭 --strict-params 可忽略这些参数继续）")

    submit_endpoint = resolve_submit_endpoint(model, "image")
    sys.stderr.write(f"[submit] POST {PT_BASE}{submit_endpoint}  model={model} aspect_ratio={args.aspect_ratio}\n")
    resp = _api_call("POST", submit_endpoint, key, body=body, timeout=IMAGE_HTTP_TIMEOUT)
    # 复核 T08（2026-11-24）：图片是同步请求，request_id 随本次响应返回，
    # 存到图片专用变量；_finish_image 不再读共享的 LAST_SUBMIT_REQUEST_ID，
    # 避免同进程里先提交过视频时把旧视频编号写进图片结果。
    global LAST_IMAGE_REQUEST_ID
    LAST_IMAGE_REQUEST_ID = resp.get("_request_id")
    data = resp.get("data") or []
    # 复核 0.9.3 #3：response_format=base64 时服务返回 data[].b64_json 而非 url。
    # 旧代码只取 url → 图被丢、状态矛盾（ok:true 但 generation_ok:false）。
    # 统一为「产物项」：url 项走网络下载；b64 项直接解码落盘（无需网络，不受 no_download 影响），
    # 并把 data URI 作为可交付标识写进 urls，generation_ok/download_ok 判定因此一致。
    deliverables = []   # [{"src": url 或 data-uri, "b64": base64 字符串或 None}]
    for it in data:
        if not isinstance(it, dict):
            continue
        url = it.get("url")
        b64 = it.get("b64_json") or it.get("base64") or it.get("b64")
        if isinstance(b64, str) and b64.strip():
            src = b64 if str(b64).strip().startswith("data:") else f"data:image/png;base64,{b64}"
            deliverables.append({"src": src, "b64": b64})
        elif url:
            deliverables.append({"src": url, "b64": None})
    urls = [d["src"] for d in deliverables]
    if not urls:
        sys.stderr.write(f"[warn] 响应未含 data[].url / data[].b64_json：{json.dumps(resp, ensure_ascii=False)[:300]}\n")
    outputs = []
    failed_urls = []   # 复核 0.9.2 #2：逐个记录下载失败的链接，供部分失败判定与补下载
    if urls:
        out_dir = Path(args.output).resolve().parent if args.output else (SCRIPT_DIR / "out")
        out_dir.mkdir(parents=True, exist_ok=True)
        for i, item in enumerate(deliverables):
            url = item["src"]
            b64 = item["b64"]
            # Base64 项：无需网络下载，no_download 也落盘（内容已在响应里，不落就丢了）
            if b64 is not None:
                if args.output and len(deliverables) > 1:
                    p = Path(args.output)
                    fname = str(p.parent / f"{p.stem}_{i+1}{p.suffix}")
                else:
                    fname = args.output or str(out_dir / f"{model}_{i+1}.png")
                explicit_output = bool(args.output)
                try:
                    payload = str(b64).split(",", 1)[1] if str(b64).strip().startswith("data:") else str(b64)
                    blob = base64.b64decode(payload)
                    Path(fname).write_bytes(blob)
                    real_ext = detect_file_extension(path=fname, default=Path(fname).suffix or ".png")
                    suffix_mismatch = real_ext and Path(fname).suffix.lower() != real_ext.lower()
                    if not explicit_output and suffix_mismatch:
                        renamed = str(Path(fname).with_suffix(real_ext))
                        try:
                            os.replace(fname, renamed)
                            fname = renamed
                        except OSError:
                            pass
                    outputs.append(fname)
                    sys.stderr.write(f"[download] 已保存（base64 解码）：{fname}\n")
                except Exception as e:
                    failed_urls.append(url)
                    sys.stderr.write(f"[warn] 第 {i+1} 张 base64 解码落盘失败：{e}\n")
                continue
            # URL 项：尊重 no_download
            if args.no_download:
                continue
            if args.output and len(deliverables) > 1:
                p = Path(args.output)
                fname = str(p.parent / f"{p.stem}_{i+1}{p.suffix}")
            else:
                fname = args.output or str(out_dir / f"{model}_{i+1}.png")
            explicit_output = bool(args.output)   # T07：显式 -o 时尊重用户后缀，不改
            try:
                download_binary(url, fname, key)
                # T07：默认命名下按魔数校正真实后缀（JPEG 不再存成 .png）。
                # 仅在用户未显式指定 -o 时才改名，避免覆盖用户意图。
                real_ext = detect_file_extension(path=fname, default=Path(fname).suffix or ".png")
                suffix_mismatch = real_ext and Path(fname).suffix.lower() != real_ext.lower()
                if not explicit_output:
                    if suffix_mismatch:
                        renamed = str(Path(fname).with_suffix(real_ext))
                        try:
                            os.replace(fname, renamed)
                            fname = renamed
                        except OSError:
                            pass   # 改名失败不影响已下载产物，保留原名
                elif suffix_mismatch:
                    # 复核补充（2026-11-24）：显式 -o 尊重用户后缀不改名，但要告警实际格式不符。
                    sys.stderr.write(f"[warn] 指定后缀 {Path(fname).suffix} 与产物真实格式 {real_ext} 不符"
                                     f"（已按你的 -o 保留原名）：{fname}\n")
                outputs.append(fname)
                sys.stderr.write(f"[download] 已保存：{fname}\n")
            except Exception as e:
                failed_urls.append(url)
                sys.stderr.write(f"[warn] 下载第 {i+1} 张失败（链接仍可用）：{e}\n")
    return urls, outputs, failed_urls


# ---------- Seedance 协议开通流程（全系通用；本地标记 + 报错回退）----------
# PT 没有查询「协议是否已签」的 API，只能仪表盘手动签。故用本地标记决定是否弹开通引导，
# 并在提交被拒报「未签」时重置标记、重新弹引导（报错回退）。
def is_agreement_error(e):
    """提交返回的错误是否因 Seedance 协议未签导致（用于报错回退重置标记）。"""
    m = (getattr(e, "message", "") or "").lower()
    return any(k in m for k in ("agreement", "asset library", "not signed", "sign the",
                                "user agreement", "协议", "未签", "授权"))


def seedance_enable_flow(key, model):
    """Seedance 协议（全系通用）：检测本地标记，未签则弹『跳转 PT 开通』流程（不阻断提交）。"""
    cfg = load_config()
    if cfg.get("seedance_agreement_signed"):
        return
    sys.stderr.write(
        f"[seedance] ⚠️ 使用 Seedance（全系 1.0/1.5/2.0/2.5）前需签署《Asset Library User Agreement》\n"
        f"  ① 打开并签署协议：{SEEDANCE_AGREEMENT_URL}\n"
        f"  ② 上传素材到资产库，拿到 asset://<ASSET_ID>\n"
        f"  ③ 用 asset:// 传入媒体，例如：--first-frame asset://xxxx\n"
        f"  ④ 签好后告诉本工具：python pt_video.py seedance enable\n"
        f"  （协议只能网页手动签，无 API 可代签；标记后此提示不再出现。若提交仍报未签，会自动重置并再提示）\n"
    )


def mark_seedance_signed():
    cfg = load_config()
    if not cfg.get("seedance_agreement_signed"):
        cfg["seedance_agreement_signed"] = True
        save_config(cfg)
        sys.stderr.write("[seedance] 已记录协议为已签（本地标记）；后续不再弹开通引导\n")


def reset_seedance_signed():
    cfg = load_config()
    if cfg.get("seedance_agreement_signed"):
        cfg["seedance_agreement_signed"] = False
        save_config(cfg)
        sys.stderr.write("[seedance] 检测到协议未签，已重置本地标记；请签好后跑 `seedance enable`\n")


def cmd_generate(argv):
    parser = argparse.ArgumentParser(prog="pt_video.py generate")
    parser.add_argument("-m", "--model", default=None, help="模型 ID；省略或 auto = 按账号自动选")
    parser.add_argument("--type", default="auto", choices=["video", "image", "seedance", "auto"],
                        help="生成类型（默认 auto=按模型 ID 推断；video/image/seedance 三选一；"
                             "seedance 为独立类型，需签协议且素材走 asset://）")
    parser.add_argument("-p", "--prompt", default=None, help="文本提示词")
    # 视频参数
    parser.add_argument("-d", "--duration", type=int, default=5, help="时长秒数 2-30（默认 5）")
    parser.add_argument("-r", "--resolution", default="720p", help="480P/720P/1080P（视频，默认 720p）")
    parser.add_argument("--ratio", default="adaptive", help="视频比例 adaptive/16:9/4:3/1:1/3:4/9:16")
    # 图片参数
    parser.add_argument("--aspect-ratio", default="1:1", help="图片宽高比 1:1/16:9/4:3/...（图片，默认 1:1）")
    parser.add_argument("--size", default=None, help="图片尺寸档位（如 2K/3K/1024*1024，按模型官方枚举；无映射则告警不发送）")
    parser.add_argument("--n", type=int, default=1, help="图片数量 1-9（图片）")
    parser.add_argument("--response-format", default="url", choices=["url", "base64"], help="图片返回形式（图片）")
    # 共用
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--first-frame", default=None, help="首帧图 URL（图生视频/asset://）")
    parser.add_argument("--last-frame", default=None, help="尾帧图 URL（首尾帧）")
    parser.add_argument("--ref-image", action="append", default=[], help="参考图 URL（可重复）")
    parser.add_argument("--ref-video", action="append", default=[], help="参考视频 URL（可重复）")
    parser.add_argument("--ref-audio", action="append", default=[], help="参考音频 URL（可重复）")
    parser.add_argument("--no-audio", action="store_true", help="关闭输出音轨")
    parser.add_argument("--no-prompt-extend", action="store_true", help="关闭 prompt 智能改写")
    parser.add_argument("--watermark", action="store_true", help="添加 AI 生成水印")
    parser.add_argument("-o", "--output", default=None, help="下载保存路径")
    parser.add_argument("--no-download", action="store_true", help="只返回链接，不下载")
    parser.add_argument("--timeout", type=int, default=POLL_TIMEOUT, help=f"视频轮询总上限秒（默认 {POLL_TIMEOUT}）")
    parser.add_argument("--json", action="store_true", help="最终以 JSON 输出结果（便于 MCP 解析）")
    parser.add_argument("--strict-params", action="store_true",
                        help="明确指定的参数（参考图/尺寸等）无官方映射时停止提交，不花钱生成不符要求的结果；默认关闭")
    args = parser.parse_args(argv)

    key = resolve_key()
    if not key:
        sys.stderr.write(KEY_MISSING_MSG)
        return 2

    model_val = args.model
    # 确定类型
    kind = args.type
    if model_val and is_image_model(model_val):
        kind = "image"
    elif model_val and is_seedance(model_val):
        kind = "seedance"
    elif model_val and is_video_model(model_val):
        kind = "video"
    elif kind == "auto":
        kind = "video"   # 未指定模型且无法推断时，默认走视频 auto（兼容原行为）
    # 此后 kind ∈ {video, image, seedance}

    # 解析最终模型列表（auto / 默认模型 / 显式指定+预校验）
    if model_val is None or model_val.lower() == "auto":
        explicit_auto = model_val is not None and model_val.lower() == "auto"
        cands = auto_candidates(key, kind=kind, ignore_default=explicit_auto)[:AUTO_MAX_TRIES]
        if not cands:
            sys.stderr.write(f"[error] 拉不到可用{kind}模型列表，无法 auto；请显式 -m <id> 或检查 Key\n")
            return 1
        sys.stderr.write(f"[auto] {kind} 候选顺序：{cands}\n")
        last_err = None
        for i, cand in enumerate(cands):
            try:
                return _run_one(key, cand, kind, args)
            except ApiError as e:
                low = str(e.message).lower()
                last_err = e
                if i + 1 < len(cands) and ("not supported" in low or "not listed" in low
                                          or "model_not_found" in low or "model is not" in low):
                    sys.stderr.write(f"[auto] {cand} 不可用（{e.message}），换下一个\n")
                    continue
                # Seedance 协议未签：重置标记并弹开通引导后退出
                if kind == "seedance" and is_agreement_error(e):
                    reset_seedance_signed()
                    seedance_enable_flow(key, cand)
                sys.stderr.write(f"[error] 生成失败：{e.message}\n")
                return 1
        sys.stderr.write(f"[error] 全部候选失败，最后错误：{getattr(last_err, 'message', last_err)}\n")
        return 1
    else:
        ok, val = precheck_explicit_model(key, model_val, kind=kind)
        if not ok:
            sugg, total = val
            sys.stderr.write(
                f"[error] 模型 {model_val} 不在你账号的可用列表里（校验自 GET /v1/models，"
                f"未进入生成流程、未扣费）\n"
            )
            if sugg:
                sys.stderr.write("你是不是想用：" + " / ".join(sugg) + "\n")
            sys.stderr.write(f"跑 `models` 查看你账号当前可调用的 {total} 个模型\n")
            return 3
        try:
            return _run_one(key, val, kind, args)
        except ApiError as e:
            if kind == "seedance" and is_agreement_error(e):
                reset_seedance_signed()
                seedance_enable_flow(key, val)
            sys.stderr.write(f"[error] 生成失败：{e.message}\n")
            return 1


def _run_one(key, model, kind, args):
    # 复核 T02（2026-11-24）：生成成功 ≠ 下载成功。用户要求下载（未 --no-download）
    # 却没拿到文件时，返回部分完成状态 + 非零退出码 3（保留 task_id/url 供补下载）。
    want_download = not getattr(args, "no_download", False)
    if kind == "seedance":
        # 未签协议时弹开通引导（不阻断）；Seedance 文生视频把 prompt 放进 media 的 text 类型，
        # 若用户传了图/视频媒体则其 url 必须是资产库 asset://；成功生成后记为已签。
        seedance_enable_flow(key, model)
        url, output, tid = generate_video(key, model, args, args.timeout, force_asset=True)
        mark_seedance_signed()
        dl_ok = bool(output) if want_download else True
        _finish_video(model, url, output, tid, args, kind="seedance", dl_ok=dl_ok)
        return 0 if dl_ok else 3
    if kind == "image":
        urls, outputs, failed_urls = generate_image(key, model, args, IMAGE_HTTP_TIMEOUT)
        # 复核 0.9.2 #2：要求下载时，成功数必须等于应下载数——全失败或部分失败都算 partial。
        # no_download 时 dl_ok=True（未要求下载，不算失败）。
        if want_download:
            dl_ok = bool(urls) and not failed_urls and len(outputs) == len(urls)
        else:
            dl_ok = True
        _finish_image(model, urls, outputs, args, dl_ok=dl_ok, failed_urls=failed_urls)
        return 0 if dl_ok else 3
    else:
        url, output, tid = generate_video(key, model, args, args.timeout)
        dl_ok = bool(output) if want_download else True
        _finish_video(model, url, output, tid, args, kind="video", dl_ok=dl_ok)
        return 0 if dl_ok else 3


def _finish_video(model, url, output, tid, args, kind="video", dl_ok=None):
    """输出视频最终结果。dl_ok=None 时按旧逻辑（有 output 即成功）推断，保持旧签名兼容；
    _run_one 显式传下载状态，用于「要求下载却没拿到文件」时返回部分完成（复核 T02）。"""
    if dl_ok is None:
        dl_ok = bool(output) or getattr(args, "no_download", False)
    status = "ok" if dl_ok else "partial"
    if args.json:
        result = {"ok": bool(dl_ok), "kind": kind, "task_id": tid, "model": model,
                  "url": url, "output": output,
                  "generation_ok": True,          # 生成/任务本身已成功（有 url + task_id）
                  "download_ok": bool(output) if not getattr(args, "no_download", False) else None,
                  "status": status,
                  "request_id": LAST_SUBMIT_REQUEST_ID,
                  "params": {"duration_s": args.duration, "resolution": args.resolution.upper(),
                             "ratio": args.ratio, "seed": args.seed, "prompt": args.prompt}}
        if not dl_ok:
            result["note"] = "生成已完成但产物下载失败；url 仍可用于补下载（见 stderr 警告）"
        print(json.dumps(result, ensure_ascii=False))
    else:
        print_generation_summary(model, "video", args.duration, args.resolution.upper(),
                                 args.prompt, [output] if output else [], [url] if url else [],
                                 seed=args.seed, ratio=args.ratio)
        if not dl_ok:
            print(f"[partial] 生成成功但下载失败，产物链接仍可用：{url}")
        else:
            print(f"[done] task_id={tid}")


def _finish_image(model, urls, outputs, args, dl_ok=None, failed_urls=None):
    """输出图片最终结果。dl_ok=None 时按旧逻辑推断，保持旧签名兼容。
    request_id 取本次图片同步请求的 LAST_IMAGE_REQUEST_ID（复核 T08），不再读视频提交编号。
    复核 0.9.2 #2：failed_urls 列出部分下载失败的链接，供调用方补下载而不重复生成。"""
    if dl_ok is None:
        dl_ok = bool(outputs) or getattr(args, "no_download", False)
    failed_urls = list(failed_urls or [])
    status = "ok" if dl_ok else "partial"
    if args.json:
        result = {"ok": bool(dl_ok), "kind": "image", "model": model,
                  "urls": urls, "outputs": outputs,
                  "generation_ok": bool(urls),
                  "download_ok": dl_ok if not getattr(args, "no_download", False) else None,
                  "status": status,
                  "download_failed_urls": failed_urls,
                  "request_id": LAST_IMAGE_REQUEST_ID,
                  "params": {"aspect_ratio": args.aspect_ratio, "n": args.n,
                             "seed": args.seed, "prompt": args.prompt}}
        if not dl_ok:
            result["note"] = ("生成已完成但部分/全部产物下载失败；"
                              "download_failed_urls 为失败链接，urls 含全部链接，均可用于补下载（见 stderr 警告）")
        print(json.dumps(result, ensure_ascii=False))
    else:
        print_generation_summary(model, "image", None, args.aspect_ratio, args.prompt,
                                 outputs, urls, seed=args.seed, ratio=args.aspect_ratio)
        if not dl_ok:
            if failed_urls:
                print(f"[partial] 生成成功但 {len(failed_urls)}/{len(urls)} 张下载失败，失败链接仍可用：{failed_urls}")
            else:
                print(f"[partial] 生成成功但下载失败，产物链接仍可用：{urls}")
        else:
            print(f"[done] {len(urls)} 张")


def cmd_seedance(argv):
    """Seedance 协议标记管理（全系通用）：enable / disable / status。status 会 best-effort 真查服务端签署状态。"""
    parser = argparse.ArgumentParser(prog="pt_video.py seedance")
    parser.add_argument("action", nargs="?", default="status", choices=["enable", "disable", "status"])
    args = parser.parse_args(argv)
    cfg = load_config()
    if args.action == "enable":
        cfg["seedance_agreement_signed"] = True
        save_config(cfg)
        print("[ok] 已标记 Seedance 协议为已签（本地）。之后使用 Seedance 不再弹开通引导。")
        print("     若实际提交仍报未签，跑：python pt_video.py seedance disable 再重试。")
    elif args.action == "disable":
        cfg["seedance_agreement_signed"] = False
        save_config(cfg)
        print("[ok] 已清除 Seedance 已签标记；下次使用会重新弹开通引导。")
    else:
        signed = bool(cfg.get("seedance_agreement_signed", False))
        out = {"seedance_agreement_signed": signed,
               "agreement_url": SEEDANCE_AGREEMENT_URL,
               "note": "协议只能仪表盘手动签，无 API；签后素材以 asset:// 传入 media"}
        key = os.environ.get("POWERTOKENS_API_KEY")
        if key:
            try:
                sv = _account_call("GET", ACCOUNT_AGREEMENT, key)
                d = sv.get("data", {}) if isinstance(sv, dict) else {}
                out["agreement_accepted_server"] = bool(d.get("agreement_accepted"))
                out["asset_library_enabled_server"] = bool(d.get("asset_library_enabled"))
            except ApiError as e:
                out["server_check_error"] = str(e)
        else:
            out["server_check_skipped"] = "未设置 POWERTOKENS_API_KEY，跳过服务端签署校验"
        print(json.dumps(out, ensure_ascii=False))
    return 0




# ---------- T04：账户域凭证（裸 pt_live_ 令牌 + 数字 key_id）----------
# 依据 SSOT C1 真机裁决：账户域 https://powertokens.ai/api 用裸 pt_live_ 令牌（不带 Bearer），
# balance/usage 还要 key_id（请求侧必须是数字）；与生成域 Bearer <API Key> 是两套凭证，不可混用。
# 旧行为兼容：仍接受 -k/POWERTOKENS_API_KEY 当裸 key 用（老用户零迁移），
# 但推荐用 PT_ACCESS_TOKEN + PT_KEY_ID；缺凭证时给首次配置引导。
ACCOUNT_CREDENTIAL_HINT = (
    "[需要账户凭证] 余额/账单/消费记录属于账户域，需要个人访问令牌（不是模型 API Key）。\n"
    "  1) 打开 " + ONBOARD_URL + " → 登录 → 创建个人访问令牌（形如 pt_live_xxxx）\n"
    "  2) 设置环境变量：export PT_ACCESS_TOKEN=pt_live_xxxx  和  export PT_KEY_ID=12345（纯数字）\n"
    "  3) 或命令行：python pt_video.py config --set-access-token <令牌> --set-key-id <数字>\n"
    "  说明：令牌是账户级、明文只展示一次；key_id 需到仪表盘查看，平台没有接口能反查。\n"
)


def resolve_account_credentials(explicit_token=None):
    """解析账户域凭证，返回 (token, key_id)。纯本地、不联网、不落盘新凭证。

    优先级：
      1) -k 显式传入：pt_live_ 开头当令牌；否则当旧模型 Key（裸，兼容老行为）
      2) 环境变量 PT_ACCESS_TOKEN / PT_KEY_ID
      3) config.json 的 access_token / key_id
      4) 回落 POWERTOKENS_API_KEY（旧行为，裸 key）
    """
    cfg = load_config()
    token = explicit_token
    if token and token.startswith("pt_live_"):
        return token, cfg.get("key_id") or os.environ.get("PT_KEY_ID")
    if token:
        # 旧的 -k 模型 Key：作为裸 key 兼容返回，key_id 从 env/config 尽力补
        return token, cfg.get("key_id") or os.environ.get("PT_KEY_ID")
    token = os.environ.get("PT_ACCESS_TOKEN") or cfg.get("access_token")
    key_id = os.environ.get("PT_KEY_ID") or cfg.get("key_id")
    if token:
        return token, key_id
    legacy = os.environ.get("POWERTOKENS_API_KEY") or cfg.get("api_key")
    return (legacy or ""), key_id


def _account_get(key, action, params=None):
    """统一封装账户 / 计费只读查询；缺凭证抛 ApiError(401)（T04：支持 pt_live_ + key_id）。

    签名保持不变（旧调用方零改动），内部解析凭证：
      - token 走裸 Authorization（C1 裁决，不带 Bearer）
      - balance/usage 需要数字 key_id，自动合并到 query params
      - 旧 -k 模型 Key 仍可用（裸 key 兼容），但缺 key_id 时账户接口可能返回业务错误，
        由上层按响应体 code 判断，这里不臆造 key_id（R4）。
    """
    token, key_id = resolve_account_credentials(key)
    if not token:
        raise ApiError(401, ACCOUNT_CREDENTIAL_HINT)
    merged = dict(params or {})
    needs_key_id = action in (ACCOUNT_BALANCE, ACCOUNT_USAGE)
    if needs_key_id and key_id is not None:
        # key_id 请求侧必须数字；非数字（脱敏串/占位）不猜、不传，交给响应体报错（R4）。
        # 内联校验而非 import account_client：本文件需独立分发，不依赖 pt-gateway 目录存在。
        raw = str(key_id).strip()
        if raw.isdigit():
            merged["key_id"] = int(raw)
    return _account_call("GET", action, token, params=merged or None)


def _account_business_error(data):
    """复核补充（2026-11-24）：检查账户响应正文里的业务状态。

    PT 账户域常见「HTTP 200 + body code=500 + msg=invalid token」，仅看 HTTP 会误报成功。
    返回 (is_error, msg)：code 明确为失败码则为 True；成功码/无 code 则为 False。
    纯函数、零依赖；由各账户命令的 --strict 选项控制是否启用（默认关闭，兼容旧行为 / 红线2）。
    """
    if not isinstance(data, dict):
        return False, None
    # 复核 0.9.2 #5：统一规范化业务码，不再只列举部分字符串码。
    # codex 实测 code:"429"（字符串）在 --strict 下仍报成功——旧 fail_codes 无 "429"，
    # 且 isinstance(str, int) 为假导致 >=400 判定失效。另纳入 success:false 形态。
    msg = str(data.get("msg") or data.get("message") or "")
    if data.get("success") is False:
        return True, msg or "success=false"
    code = data.get("code")
    if code is None:
        return False, None
    # 统一转 int 比较（数字或纯数字字符串）；无法转 int 的按字符串白/黑名单处理。
    try:
        ncode = int(code)
    except (ValueError, TypeError):
        ncode = None
    if ncode is not None:
        if ncode in (0, 200):
            return False, None
        if ncode >= 400:
            return True, msg or str(code)
        return False, None
    # 非数字业务码（如 "rate_limited"）：仅明确失败字符串算失败，未知不猜（R4）
    s = str(code).strip()
    if s in ("200", "0", "ok", "success"):
        return False, None
    fail_words = ("fail", "error", "invalid", "denied", "expired", "rate_limit", "limited", "forbidden")
    if any(w in s.lower() for w in fail_words):
        return True, msg or s
    return False, None


def _account_strict_flag(argv, prog):
    """给账户命令解析 --strict；返回 (args, strict)。不改旧调用（不传则 False）。"""
    parser = argparse.ArgumentParser(prog="pt_video.py " + prog)
    parser.add_argument("-k", "--key", default=None, help="API Key（缺省读 POWERTOKENS_API_KEY）")
    parser.add_argument("--strict", action="store_true",
                        help="校验响应正文业务码（如 HTTP 200 + code=500）；默认关闭以兼容旧行为")
    return parser.parse_args(argv)


def cmd_balance(argv):
    """余额查询（按 Key 粒度）：GET /v1/billing/balance。只读，不产生消耗。"""
    args = _account_strict_flag(argv, "balance")
    try:
        data = _account_get(args.key, ACCOUNT_BALANCE)
        if getattr(args, "strict", False):
            bad, msg = _account_business_error(data)
            if bad:
                sys.stderr.write(f"[balance] 业务失败：{msg}\n")
                return 1
        print(json.dumps({"ok": True, "endpoint": "balance", "raw": data}, ensure_ascii=False))
    except ApiError as e:
        sys.stderr.write("[balance] 失败：" + str(e) + "\n")
        return 2 if e.status == 401 else 1
    return 0


def cmd_usage(argv):
    """账单查询（按模型类型汇总）：GET /v1/billing/usage。支持 --start-date / --end-date。只读。"""
    parser = argparse.ArgumentParser(prog="pt_video.py usage")
    parser.add_argument("-k", "--key", default=None, help="API Key（缺省读 POWERTOKENS_API_KEY）")
    parser.add_argument("--start-date", default=None, help="起始日期 YYYY-MM-DD（可选）")
    parser.add_argument("--end-date", default=None, help="结束日期 YYYY-MM-DD（可选）")
    parser.add_argument("--strict", action="store_true", help="校验响应正文业务码；默认关闭")
    args = parser.parse_args(argv)
    params = {}
    if args.start_date:
        params["start_date"] = args.start_date
    if args.end_date:
        params["end_date"] = args.end_date
    try:
        data = _account_get(args.key, ACCOUNT_USAGE, params=params or None)
        if getattr(args, "strict", False):
            bad, msg = _account_business_error(data)
            if bad:
                sys.stderr.write(f"[usage] 业务失败：{msg}\n")
                return 1
        print(json.dumps({"ok": True, "endpoint": "usage", "raw": data}, ensure_ascii=False))
    except ApiError as e:
        sys.stderr.write("[usage] 失败：" + str(e) + "\n")
        return 2 if e.status == 401 else 1
    return 0


def cmd_logs(argv):
    """消费记录查询：GET /v1/logs/query。只读。"""
    args = _account_strict_flag(argv, "logs")
    try:
        data = _account_get(args.key, ACCOUNT_LOGS)
        if getattr(args, "strict", False):
            bad, msg = _account_business_error(data)
            if bad:
                sys.stderr.write(f"[logs] 业务失败：{msg}\n")
                return 1
        print(json.dumps({"ok": True, "endpoint": "logs", "raw": data}, ensure_ascii=False))
    except ApiError as e:
        sys.stderr.write("[logs] 失败：" + str(e) + "\n")
        return 2 if e.status == 401 else 1
    return 0


def cmd_agreement(argv):
    """资产库协议签署状态查询：GET /v1/asset/agreement/status。只读。"""
    args = _account_strict_flag(argv, "agreement")
    try:
        data = _account_get(args.key, ACCOUNT_AGREEMENT)
        if getattr(args, "strict", False):
            bad, msg = _account_business_error(data)
            if bad:
                sys.stderr.write(f"[agreement] 业务失败：{msg}\n")
                return 1
        print(json.dumps({"ok": True, "endpoint": "agreement", "raw": data}, ensure_ascii=False))
    except ApiError as e:
        sys.stderr.write("[agreement] 失败：" + str(e) + "\n")
        return 2 if e.status == 401 else 1
    return 0

def main():
    if len(sys.argv) > 1:
        sub = sys.argv[1]
        if sub == "config":
            return cmd_config(sys.argv[2:])
        if sub == "check":
            return cmd_check()
        if sub == "auto-model":
            return cmd_auto_model(sys.argv[2:])
        if sub == "models":
            return cmd_models(sys.argv[2:])
        if sub == "estimate":
            return cmd_estimate(sys.argv[2:])
        if sub == "seedance":
            return cmd_seedance(sys.argv[2:])
        if sub == "balance":
            return cmd_balance(sys.argv[2:])
        if sub == "usage":
            return cmd_usage(sys.argv[2:])
        if sub == "logs":
            return cmd_logs(sys.argv[2:])
        if sub == "agreement":
            return cmd_agreement(sys.argv[2:])
        if sub == "upload":
            return cmd_upload(sys.argv[2:])
        if sub == "generate":
            return cmd_generate(sys.argv[2:])
    # 无子命令：给用法
    sys.stderr.write("用法：pt_video.py <check|config|auto-model|models|estimate|seedance|upload|balance|usage|logs|agreement|generate> [参数]\n")
    sys.stderr.write("例：pt_video.py generate -m wan3.0-video -p \"一只猫在跳舞\" -d 5 -r 720p\n")
    sys.stderr.write("例：pt_video.py generate -m seedream-5-0-260128 -p \"赛博城市\" --type image --aspect-ratio 16:9\n")
    sys.stderr.write("例：pt_video.py generate --type seedance -m auto -p \"\" --first-frame asset://xxxx\n")
    sys.stderr.write("例：pt_video.py seedance enable   # 签好协议后标记，不再弹开通引导\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
