#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PT Gateway 统一媒体 MCP server（FastMCP 薄壳）。

与 HTTP 服务（server.py）的关系：两者都是 pt_gateway.py 的「壳」，import 同一份核心，
共用同一份 registry.json / catalog.json——HTTP 和 MCP 看到的模型目录永远一致。

设计原则（与用户敲定的方向一致）：
  - 只暴露 **3 个统一工具**，agent 选择面小、不易选错：
      list_capabilities  —— 查能力目录（模型/分辨率/时长/模式/单价/同步或异步），代替翻文档
      media_generate     —— 统一媒体生成入口（image/audio/chat 同步直返；video/seedance 异步拿 task_id）
      task_status        —— 查任务状态（wait=true 时轮询到终态）
  - 本壳不实现任何逻辑，全部转发给 pt_gateway.TaskGateway（单一真相源）。
  - 不提供取消：PT 无异步任务取消接口（2026-10-30 与官方确认），故 MCP 也不暴露取消工具。
  - 权限：不做客户端拦截。/v1/models 是 PT 全量清单；若本 key 对某模型无权限，
    由 PT 服务端报错，网关原样透传（fail-open）。

运行：
  python mcp.py                   # stdio（agent 客户端自动拉起）
依赖：pip install "mcp[cli]>=1.2"
"""

import json
import sys
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    sys.stderr.write("[error] 未安装 mcp。请运行：pip install \"mcp[cli]>=1.2\"\n")
    raise

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pt_gateway import TaskGateway, ApiError  # noqa: E402

GW = TaskGateway()
mcp = FastMCP("pt-gateway")


def _wrap(fn):
    try:
        return json.dumps(fn(), ensure_ascii=False)
    except ApiError as e:
        return json.dumps({"ok": False, "error": str(e.message), "status": e.status},
                          ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"ok": False, "error": repr(e)}, ensure_ascii=False)


@mcp.tool()
def list_capabilities(kind: str = "") -> str:
    """查询 PowerTokens 全部媒体模型的能力目录（代替翻文档）。

    返回 kinds（image/video/seedance/audio 各自的同步或异步执行方式、端点、前置要求）
    和 models（每个模型：kind、价格、时长上限、分辨率、输入模式、注册表 enabled/delisted 状态）。
    kind 可选过滤：image / video / seedance / audio；留空返回全部。
    生成前先调本工具即可知道该传什么参数，无需再查任何文档。
    """
    return _wrap(lambda: GW.list_capabilities(kind or None))


def _j(s):
    """把 JSON 字符串参数解析为对象；空串返回 None。"""
    return json.loads(s) if s else None


@mcp.tool()
def media_generate(kind: str = "", model: str = "auto", prompt: str = "",
                   aspect_ratio: str = "", n: int = 0,
                   duration_s: int = 5, resolution: str = "720p", ratio: str = "16:9",
                   first_frame: str = "", ref_image: str = "", ref_video: str = "",
                   wait: bool = False, timeout_s: int = 600,
                   no_audio: bool = False, no_prompt_extend: bool = False,
                   watermark: bool = False, seed: int = 0,
                   voice: str = "", params_json: str = "",
                   messages_json: str = "", system: str = "",
                   temperature: float = -1, max_tokens: int = 0) -> str:
    """统一媒体生成入口：声明意图（kind + prompt），网关自动路由，agent 无需关心各家差异。

    五种 kind：
    - image    同步返回 {urls:[...]}；aspect_ratio（如 16:9）、n（张数）
    - video    异步返回 {task_id}，再用 task_status 查；duration_s 时长秒、resolution
               720p/1080p、ratio 画面比例；素材 first_frame/ref_image/ref_video 支持 http(s) 或 asset://
               可选 no_audio（无声）、no_prompt_extend（不扩展提示词）、watermark（水印）、seed（固定种子）
    - seedance 与 video 同端点，网关已归一化请求体；图生视频素材**必须** asset://
               （用 PT 资产库上传拿 id）；需先在 PT 仪表盘签《Asset Library User Agreement》
    - audio    同步返回 {url}；voice 指定音色，params_json 传额外厂商参数
    - chat     同步返回 {text}；messages_json 传多轮对话 [{"role":"user","content":"..."}]，
               或只用 prompt 作单轮；system 角色设定、temperature（<0 表示不传）、max_tokens
    model 填 "auto"（默认，自动选）或具体模型 ID（先 list_capabilities 看候选）。
    wait=true 时阻塞轮询到终态（仅异步 kind 生效），timeout_s 为上限秒数。
    权限：不做客户端拦截；本 key 无权限时由 PT 服务端报错并透传。
    """
    return _wrap(lambda: GW.media_generate(
        kind=kind or None, model=model or "auto", prompt=prompt,
        aspect_ratio=aspect_ratio or None, n=n or None,
        duration=duration_s, resolution=resolution, ratio=ratio,
        first_frame=first_frame or None, ref_image=ref_image or None,
        ref_video=ref_video or None, wait=wait, timeout=timeout_s,
        no_audio=no_audio, no_prompt_extend=no_prompt_extend,
        watermark=watermark, seed=seed or None,
        voice=voice or None, params=_j(params_json),
        messages=_j(messages_json), system=system or None,
        temperature=None if temperature < 0 else temperature,
        max_tokens=max_tokens or None))


@mcp.tool()
def task_status(task_id: str, wait: bool = False, timeout_s: int = 600) -> str:
    """查询异步任务状态（video/seedance 生成任务）。

    返回归一化结构 {task_id, state, progress, url, raw}；state 为
    QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED/UNKNOWN 之一。
    wait=true 时轮询到终态再返回（最多 timeout_s 秒），成功时 url 即结果视频地址。
    注意：PT 不提供异步任务取消接口（2026-10-30 与官方确认），本 MCP 也不暴露取消工具；
    任务一旦提交只能等待其自行结束。
    """
    if wait:
        return _wrap(lambda: GW.wait(task_id, timeout=timeout_s))
    return _wrap(lambda: GW.get_status(task_id))


@mcp.tool()
def account_query(action: str, start_date: str = "", end_date: str = "") -> str:
    """账户 / 计费只读查询（与生成接口不同域，用裸 API key 鉴权）。action=balance|usage|logs|agreement。

    usage 可带 start_date/end_date(YYYY-MM-DD) 限定统计周期。返回 PT 原始 JSON（含 code/data）：
    balance 给当前 Key 余额（credits）；usage 给按模型类型汇总的消耗；logs 给消费记录明细；
    agreement 给资产库协议签署状态。只读，不扣费。
    注意：若返回 401/403，可能该域要的是网页登录令牌而非 API Key（与生成接口的 Bearer 不同）。
    """
    return _wrap(lambda: account_query(action, start_date=start_date or None, end_date=end_date or None))


if __name__ == "__main__":
    mcp.run()
