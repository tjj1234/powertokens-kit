#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""pt-gateway 端到端真机验收脚本（会真实消耗 PT 额度）。

用法（key 只走环境变量或 stdin，不写进代码、不进 git）:
    $env:POWERTOKENS_API_KEY = "sk-xxx"          # PowerShell
    python e2e_test.py --tier minimal            # 极简档  (~$0.2)
    python e2e_test.py --tier standard           # 标准档  (~$0.6)
    python e2e_test.py --dry-run                 # 只跑免费项（/v1/models + 目录自检），0 花费

分档说明见 TIERS。--max-usd 为硬预算守卫：预估累计超过则跳过后续付费项。
报告写入 e2e_report.md（key 已脱敏）。
"""
import argparse
import importlib.util
import json
import os
import sys
import time
import io

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- 单价（USD，取自 powertokens.ai/pricing，2026-10-30）----
PRICE = {
    # 每类只列本次会用到的模型；单位见 unit
    "qwen3.8-flash":             {"unit": "1M_tok", "in": 0.150, "out": 0.470},
    "deepseek-v4-flash-0731":    {"unit": "1M_tok", "in": 0.150, "out": 0.500},
    "seedream-5-0-260128":       {"unit": "per_image", "per": 0.034},
    "seedream-4-5-251128":       {"unit": "per_image", "per": 0.034},
    "kling-image":               {"unit": "per_image", "per": 0.028},
    "speech-2.8-turbo":          {"unit": "per_call_est", "per": 0.010},   # 官网未列 TTS 单价，按短文本保守估
    "qwen3-tts-instruct-flash":  {"unit": "per_call_est", "per": 0.010},
    "dreamina-seedance-2-0-mini-260615": {"unit": "per_sec", "480p": 0.031, "720p": 0.076},
    "wan3.0-video":              {"unit": "per_sec", "480p": 0.050, "720p": 0.100},
    "dreamina-seedance-2-5-260628": {"unit": "per_sec", "480p": 0.105, "720p": 0.232},
}

TIERS = {
    # tier: (chat_model, image_model, audio_model, video_model, video_res, video_dur)
    "minimal":  ("qwen3.8-flash", "seedream-5-0-260128", "speech-2.8-turbo",
                 "dreamina-seedance-2-0-mini-260615", "480p", 5),
    "standard": ("qwen3.8-flash", "seedream-5-0-260128", "speech-2.8-turbo",
                 "wan3.0-video", "720p", 5),
    "premium":  ("qwen3.8-flash", "seedream-5-0-260128", "speech-2.8-turbo",
                 "dreamina-seedance-2-5-260628", "720p", 5),
}

PROMPT_CHAT = "只回复两个字：你好"
PROMPT_IMAGE = "一只戴着红色围巾的柴犬，坐在雪地里，写实风格"
PROMPT_AUDIO = "你好，这是语音合成接口的验收测试。"
PROMPT_VIDEO = "一只柴犬在雪地里奔跑，镜头缓慢跟随，阳光明媚"


def load_gw():
    spec = importlib.util.spec_from_file_location("gw", os.path.join(HERE, "pt_gateway.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def mask(s, keep=4):
    if not s:
        return "(none)"
    s = str(s)
    return s[:keep] + "*" * max(0, len(s) - keep)


def est_cost(tier):
    c, i, a, v, res, dur = TIERS[tier]
    p = PRICE
    chat = (200 * p[c]["in"] + 50 * p[c]["out"]) / 1_000_000
    img = p[i]["per"]
    aud = p[a]["per"]
    if p[v]["unit"] == "per_sec":
        unit_price = p[v].get(res) or p[v].get("720p") or p[v].get("480p")
        vid = unit_price * dur
    else:
        vid = p[v]["per"]
    return {"chat": chat, "image": img, "audio": aud, "video": vid, "total": chat + img + aud + vid}


class Runner:
    def __init__(self, gw, key, tier, max_usd):
        self.gw = gw
        self.key = key
        self.tier = tier
        self.max_usd = max_usd
        self.spent = 0.0
        self.lines = []
        self.oks = {}

    def log(self, s):
        self.lines.append(s)
        sys.stderr.write(s + "\n")

    def guard(self, item_cost):
        if self.spent + item_cost > self.max_usd:
            self.log(f"  [SKIP] 预算守卫：该项约 ${item_cost:.3f}，累计将超 --max-usd=${self.max_usd:.2f}")
            return False
        return True

    # ---------- 步骤 ----------
    def step_models(self):
        gw = self.gw
        t0 = time.time()
        try:
            ids = gw.list_key_models(self.key, "all")
            dt = time.time() - t0
            self.log(f"[0] GET /v1/models -> {len(ids)} 个模型（免费, {dt:.2f}s）")
            cat = gw.Catalog()
            from collections import Counter
            cnt = Counter(gw._kind_of(m) for m in ids)
            self.log(f"    kind 分布: {dict(cnt)}")
            known = [m for m in ids if gw._kind_of(m) in gw.KNOWN_KINDS]
            self.log(f"    网关可路由: {len(known)} / {len(ids)}")
            # 抽查本次要用的模型是否都在
            c, i, a, v, _, _ = TIERS[self.tier]
            for want in (c, i, a, v):
                self.log(f"    {'OK ' if want in ids else 'MISS'} {want}")
            self.oks["models"] = True
            return ids
        except Exception as e:
            self.log(f"[0] GET /v1/models FAILED: {type(e).__name__}: {e}")
            self.oks["models"] = False
            return []

    def step_chat(self, model):
        gw = self.gw
        cost = est_cost(self.tier)["chat"]
        if not self.guard(cost):
            self.oks["chat"] = None
            return
        t0 = time.time()
        try:
            g = gw.TaskGateway(key=self.key)
            r = g.media_generate(kind="chat", model=model, prompt=PROMPT_CHAT,
                                 temperature=0.1, max_tokens=32)
            dt = time.time() - t0
            text = (r.get("text") or "").strip()
            self.spent += cost
            self.log(f"[1] chat  OK  model={r.get('model')} exec={r.get('execution')} "
                     f"{dt:.2f}s  ~${cost:.4f}")
            self.log(f"    输出: {text[:80]!r}")
            usage = (r.get("raw") or {}).get("usage")
            if usage:
                self.log(f"    usage: {usage}")
            self.oks["chat"] = bool(text)
        except Exception as e:
            self.log(f"[1] chat  FAILED {type(e).__name__}: {e}")
            self.oks["chat"] = False

    def step_image(self, model):
        gw = self.gw
        cost = est_cost(self.tier)["image"]
        if not self.guard(cost):
            self.oks["image"] = None
            return
        t0 = time.time()
        try:
            g = gw.TaskGateway(key=self.key)
            r = g.media_generate(kind="image", model=model, prompt=PROMPT_IMAGE, n=1)
            dt = time.time() - t0
            urls = r.get("urls") or []
            self.spent += cost
            self.log(f"[2] image OK  model={r.get('model')} exec={r.get('execution')} "
                     f"{dt:.2f}s  ~${cost:.3f}")
            self.log(f"    urls: {len(urls)}  {urls[:1]}")
            self.oks["image"] = bool(urls)
        except Exception as e:
            self.log(f"[2] image FAILED {type(e).__name__}: {e}")
            self.oks["image"] = False

    def step_audio(self, model):
        gw = self.gw
        cost = est_cost(self.tier)["audio"]
        if not self.guard(cost):
            self.oks["audio"] = None
            return
        t0 = time.time()
        try:
            g = gw.TaskGateway(key=self.key)
            r = g.media_generate(kind="audio", model=model, prompt=PROMPT_AUDIO)
            dt = time.time() - t0
            url = r.get("url")
            self.spent += cost
            self.log(f"[3] audio OK  model={r.get('model')} exec={r.get('execution')} "
                     f"{dt:.2f}s  ~${cost:.3f}")
            self.log(f"    url: {mask(url, 24)}")
            raw = r.get("raw") or {}
            self.log(f"    raw keys: {sorted(raw.keys())[:12]}")
            # 关键：确认响应结构（用于校正 audio_generate 字段）
            self.log(f"    raw snippet: {json.dumps(raw, ensure_ascii=False)[:400]}")
            self.oks["audio"] = bool(url)
        except Exception as e:
            self.log(f"[3] audio FAILED {type(e).__name__}: {e}")
            self.oks["audio"] = False

    def step_video(self, model, res, dur):
        gw = self.gw
        cost = est_cost(self.tier)["video"]
        if not self.guard(cost):
            self.oks["video"] = None
            return
        t0 = time.time()
        try:
            g = gw.TaskGateway(key=self.key)
            r = g.media_generate(kind="video", model=model, prompt=PROMPT_VIDEO,
                                 duration=dur, resolution=res, wait=True, timeout=900)
            dt = time.time() - t0
            self.spent += cost
            state = r.get("state") or r.get("status")
            self.log(f"[4] video OK  model={r.get('model')} exec={r.get('execution')} "
                     f"{dt:.1f}s  ~${cost:.3f}")
            self.log(f"    task_id={r.get('task_id')} state={state}")
            urls = r.get("urls") or ([r.get("url")] if r.get("url") else [])
            self.log(f"    urls: {urls[:1]}")
            self.oks["video"] = state in ("SUCCESS", "SUCCEEDED", "success", "completed") or bool(urls)
        except Exception as e:
            self.log(f"[4] video FAILED {type(e).__name__}: {e}")
            self.oks["video"] = False

    def report(self, out_path):
        c, i, a, v, res, dur = TIERS[self.tier]
        e = est_cost(self.tier)
        md = []
        md.append("# pt-gateway 端到端真机验收报告\n")
        md.append(f"- 档位：**{self.tier}**（chat `{c}` / image `{i}` / audio `{a}` / video `{v}` {res} {dur}s）")
        md.append(f"- key：`{mask(self.key)}`（已脱敏，未写入本文件）")
        md.append(f"- 预估花费：**${e['total']:.3f}**；实际计入：${self.spent:.3f}")
        md.append("")
        md.append("## 结果\n")
        md.append("| 步骤 | 结果 | 说明 |")
        md.append("|---|---|---|")
        names = {"models": "/v1/models", "chat": "chat 文本", "image": "image 图像",
                 "audio": "audio TTS", "video": "video 视频"}
        for k in ("models", "chat", "image", "audio", "video"):
            st = self.oks.get(k)
            mark = {True: "PASS", False: "FAIL", None: "SKIP"}.get(st, "N/A")
            md.append(f"| {names[k]} | **{mark}** | 见下方日志 |")
        md.append("")
        md.append("## 明细日志\n")
        md.append("```")
        md.extend(self.lines)
        md.append("```")
        md.append("")
        md.append("## 单价依据（powertokens.ai/pricing, 2026-10-30）\n")
        md.append("```json")
        md.append(json.dumps({k: PRICE[k] for k in (c, i, a, v)}, indent=2, ensure_ascii=False))
        md.append("```")
        io.open(out_path, "w", encoding="utf-8").write("\n".join(md))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=None, help="不推荐（会留在命令行历史）；优先用环境变量")
    ap.add_argument("--tier", default="minimal", choices=list(TIERS))
    ap.add_argument("--max-usd", type=float, default=2.0)
    ap.add_argument("--dry-run", action="store_true", help="只跑免费项")
    ap.add_argument("--skip", default="", help="逗号分隔的步骤名，如 video,audio")
    args = ap.parse_args()

    key = args.key or os.environ.get("POWERTOKENS_API_KEY")
    if not key:
        sys.stderr.write("ERROR: 未提供 key。设置环境变量 POWERTOKENS_API_KEY，或用 --key。\n")
        return 2

    gw = load_gw()
    r = Runner(gw, key, args.tier, args.max_usd)
    skip = set(s.strip() for s in args.skip.split(",") if s.strip())

    r.log(f"=== pt-gateway E2E | tier={args.tier} | key={mask(key)} | "
          f"预估 ${est_cost(args.tier)['total']:.3f} | max-usd={args.max_usd} ===")

    r.step_models()
    if args.dry_run:
        r.log("[dry-run] 跳过全部付费项。")
    else:
        c, i, a, v, res, dur = TIERS[args.tier]
        if "chat" not in skip:
            r.step_chat(c)
        if "image" not in skip:
            r.step_image(i)
        if "audio" not in skip:
            r.step_audio(a)
        if "video" not in skip:
            r.step_video(v, res, dur)

    r.log(f"=== 完成 | 计入花费约 ${r.spent:.3f} ===")
    out = os.path.join(HERE, "e2e_report.md")
    r.report(out)
    sys.stderr.write("REPORT -> " + out + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
