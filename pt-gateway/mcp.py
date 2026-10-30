#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PT Gateway 统一媒体 MCP server（FastMCP 薄壳）。

与 HTTP 服务（server.py）的关系：两者都是 pt_gateway.py 的「壳」，import 同一份核心，
共用同一份 registry.json / catalog.json——HTTP 和 MCP 看到的模型目录永远一致。

设计原则（与用户敲定的方向一致）：
  - 只暴露 **3 个统一工具**，agent 选择面小、不易选错：
      list_capabilities  —— 查能力目录（模型/分辨率/时长/模式/单价/同步或异步），代替翻文档
      media_generate     —— 统一媒体生成入口（image 同步直返；video/seedance 异步拿 task_id；audio 诚实 unsupported）
      task_status        —— 查任务状态（wait=true 时轮询到终态）
  - 本壳不实现任何逻辑，全部转发给 pt_gateway.TaskGateway（单一真相源）。

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


@mcp.tool()
def media_generate(kind: str = "", model: str = "auto", prompt: str = "",
                   aspect_ratio: str = "", n: int = 0,
                   duration_s: int = 5, resolution: str = "720p", ratio: str = "16:9",
                   first_frame: str = "", ref_image: str = "", ref_video: str = "",
                   wait: bool = False) -> str:
    """统一媒体生成入口：声明意图（kind + prompt），网关自动路由，agent 无需关心各家差异。

    - image：同步直接返回 {urls:[...]}；可选 aspect_ratio（如 16:9）与 n（张数）
    - video：异步返回 {task_id, execution:"async"}，用 task_status 查询；duration_s 时长秒、
      resolution 720p/1080p、ratio 画面比例；素材（可选）first_frame/ref_image/ref_video 支持 http(s) 或 asset://
    - seedance：与 video 同端点但网关已归一化请求体；图生视频素材**必须** asset://（用 PT 资产库上传拿 id）；
      需先在 PT 仪表盘签《Asset Library User Agreement》；文生视频纯 prompt 即可
    - audio：同步生成（POST /ali/.../multimodal-generation/generation，返回 {execution:"sync", url}）
    model 填 "auto"（默认，自动选）或具体模型 ID（先 list_capabilities 看候选）。
    wait=true 时阻塞轮询到终态（仅异步 kind 生效），直接返回 {state, url}。
    """
    return _wrap(lambda: GW.media_generate(
        kind=kind or None, model=model or "auto", prompt=prompt,
        aspect_ratio=aspect_ratio or None, n=n or None,
        duration=duration_s, resolution=resolution, ratio=ratio,
        first_frame=first_frame or None, ref_image=ref_image or None,
        ref_video=ref_video or None, wait=wait))


@mcp.tool()
def task_status(task_id: str, wait: bool = False, timeout_s: int = 600) -> str:
    """查询异步任务状态（video/seedance 生成任务）。

    返回归一化结构 {task_id, state, progress, url, raw}；state 为
    QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED/UNKNOWN 之一。
    wait=true 时轮询到终态再返回（最多 timeout_s 秒），成功时 url 即结果视频地址。
    注意：Seedance 支持取消（cancel 端点 DELETE /byteplus/api/v3/contents/generations/tasks/{id}，对应 Seedance 任务）；其他模型取消端点待确认。
    """
    if wait:
        return _wrap(lambda: GW.wait(task_id, timeout=timeout_s))
    return _wrap(lambda: GW.get_status(task_id))


if __name__ == "__main__":
    mcp.run()
