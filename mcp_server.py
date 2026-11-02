#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PowerTokens 图片 + 视频生成 —— MCP server（FastMCP）。

把 pt_video.py 的能力暴露为 MCP 工具，供任何 MCP 客户端调用
（Claude Desktop / Claude Code / Cursor / Trae / VS Code Copilot / Windsurf / Cline / Zed …）。

设计：本 server 不重复实现逻辑，而是 subprocess 调用同目录的 pt_video.py，
保证 CLI / SKILL / MCP 三处共用同一份引擎（单一真相源）。引擎直连官方 REST：
  - 视频：POST /v1/videos + 轮询 GET /v1/videos/{id}
  - 图片：POST /v1/images/generations（同步）
无需 Node / powertokens-cli。

工具：
  check_key        —— PowerTokens API Key 是否就绪
  list_models      —— 列出模型 + 定价 + 支持参数（JSON，可按类型 video/image/seedance）
  estimate_cost    —— 仅估算某次生成的消耗（美元）
  generate_video   —— 生成视频（异步：提交后自动轮询直到拿到链接；默认 auto）
  generate_image   —— 生成图片（同步：提交后直接返回 data[].url；默认 auto）
  generate_seedance —— 生成 Seedance 视频（全系 1.0/1.5/2.0/2.5；独立类型：需签协议 + asset:// 素材）

运行：
  python mcp_server.py            # stdio（最常用，agent 客户端自动拉起）
"""

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    sys.stderr.write(
        "[error] 未安装 mcp。请运行：pip install -e \".[mcp]\" 或 pip install \"mcp[cli]>=1.2\"\n"
    )
    raise

SCRIPT_DIR = Path(__file__).resolve().parent
ENGINE = SCRIPT_DIR / "pt_video.py"
PYTHON = sys.executable


def _run(args, timeout=None):
    """调用引擎并返回 (returncode, combined_output)。"""
    cmd = [PYTHON, str(ENGINE)] + args
    env = dict(os.environ)
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=timeout)
    return proc.returncode, proc.stdout


def _first_json(out, marker='"ok"'):
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{") and marker in line:
            return line
    return None


mcp = FastMCP("powertokens-kit")


@mcp.tool()
def check_key() -> str:
    """检查 PowerTokens API Key 是否已就绪。返回 JSON：key_configured / default_model / onboard_url。"""
    rc, out = _run(["check"])
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return line
    return json.dumps({"key_configured": rc == 0, "raw": out.strip()[:500]}, ensure_ascii=False)


@mcp.tool()
def list_models(kind: str = "all") -> str:
    """列出模型及其定价与支持的参数（JSON 数组）。kind=all|video|image|seedance。无需 Key 也能看到公开目录。"""
    rc, out = _run(["models", "--json", "-t", kind])
    return out.strip() or "[]"


@mcp.tool()
def estimate_cost(model: str, duration_s: int = 5, resolution: str = "720p") -> str:
    """估算一次生成的消耗（美元）。model 为模型 ID；duration_s 时长秒（视频）；resolution 如 720p/1080p（视频）。图片单价待补充会返回未知。"""
    rc, out = _run(["estimate", "-m", model, "-d", str(duration_s), "-r", resolution])
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            return line
    return json.dumps({"model": model, "error": out.strip()[:300]}, ensure_ascii=False)


@mcp.tool()
def generate_video(prompt: str, model: str = "auto", duration_s: int = 5,
                   resolution: str = "720p", output: str = "", timeout_s: int = 600) -> str:
    """生成视频（异步：提交后自动每 10s 轮询，直到拿到视频链接）。prompt 必填；model 可写具体模型 ID 或 auto（默认，按账号自动选）；duration_s 时长秒（2-30）；resolution 如 720p/1080p；output 输出路径（可选）；timeout_s 轮询总上限秒（默认 600，真实出片约 3 分钟）。返回 JSON：ok / task_id / url / output / params。"""
    args = ["generate", "--json", "-p", prompt, "-m", model,
            "-d", str(duration_s), "-r", resolution, "--timeout", str(timeout_s)]
    if output:
        args += ["-o", output]
    try:
        rc, out = _run(args, timeout=timeout_s + 120)
    except subprocess.TimeoutExpired:
        return json.dumps({"ok": False, "error": f"生成超时（>{timeout_s}s）；可换 fast 版、缩短时长或调大 timeout_s"},
                          ensure_ascii=False)
    js = _first_json(out)
    if js:
        return js
    return json.dumps({"ok": rc == 0, "returncode": rc, "tail": out.strip()[-800:]}, ensure_ascii=False)


@mcp.tool()
def generate_image(prompt: str, model: str = "auto", aspect_ratio: str = "1:1",
                   n: int = 1, output: str = "") -> str:
    """生成图片（同步：提交 POST /v1/images/generations 后直接返回 data[].url，不轮询）。prompt 必填；model 可写具体图片模型 ID 或 auto（默认按账号图片模型自动选，如 seedream-5-0-260128）；aspect_ratio 宽高比如 1:1/16:9/4:3/9:16（默认 1:1）；n 张数 1-9；output 输出路径（可选）。返回 JSON：ok / kind / urls / outputs / params。"""
    args = ["generate", "--json", "--type", "image", "-p", prompt, "-m", model,
            "--aspect-ratio", aspect_ratio, "--n", str(n)]
    if output:
        args += ["-o", output]
    try:
        rc, out = _run(args, timeout=180)
    except subprocess.TimeoutExpired:
        return json.dumps({"ok": False, "error": "图片生成超时（>180s）"}, ensure_ascii=False)
    js = _first_json(out)
    if js:
        return js
    return json.dumps({"ok": rc == 0, "returncode": rc, "tail": out.strip()[-800:]}, ensure_ascii=False)


@mcp.tool()
def generate_seedance(prompt: str, model: str = "auto", duration_s: int = 5,
                      resolution: str = "720p", first_frame: str = "",
                      output: str = "", timeout_s: int = 600) -> str:
    """生成 Seedance 视频（全系：1.0/1.5/2.0/2.5 及 Mini/Fast 变体，模型 ID 含 seedance 即归此类型；需先签《Asset Library User Agreement》）。prompt 必填；model 可写 seedance 模型 ID 或 auto（默认 seedance-1-5-pro-251215）；duration_s 时长秒（2-30）；resolution 720p/1080p；first_frame 资产库素材 asset://<ASSET_ID>（可选——文生视频可留空，仅图生视频/参考图需要，用 upload 子命令免费拿 id）；output 输出路径（可选）。返回 JSON：ok / kind / task_id / url / output。未签协议会提示跳转 PT 开通（python pt_video.py seedance enable 标记已签）。"""
    args = ["generate", "--json", "--type", "seedance", "-p", prompt, "-m", model,
            "-d", str(duration_s), "-r", resolution, "--timeout", str(timeout_s)]
    if first_frame:
        args += ["--first-frame", first_frame]
    if output:
        args += ["-o", output]
    try:
        rc, out = _run(args, timeout=timeout_s + 120)
    except subprocess.TimeoutExpired:
        return json.dumps({"ok": False, "error": f"生成超时（>{timeout_s}s）"}, ensure_ascii=False)
    js = _first_json(out)
    if js:
        return js
    return json.dumps({"ok": rc == 0, "returncode": rc, "tail": out.strip()[-800:]}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
