#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键复核脚本（需求 12）：catalog 人工标注 vs `/v1/models` 官方字段 vs 真机能力。

默认**离线、0 花费**：读本地快照做影子对账，只输出报告，不改 catalog 正文。

    python recheck.py                                   # 离线对账（需要 --live-view 才比对字段）
    python recheck.py --live-view /tmp/models.json      # 用本地 /v1/models 快照对账
    python recheck.py --live --max-usd 0.5 --models glm-4.7,qwen3.8-flash

优先级（需求 12 / 决策锁定 A3）：人工 confirmed 的 verified > official/documented
> 自动探测结果 > inferred。探测**不覆盖** verified / official / documented 的值；
默认连 `_checked_at` 也不动，只有显式 `--write-checked-at` 才更新该字段。

安全边界：
  - live 模式必须显式给 `--max-usd > 0`，累计估算花费到上限立刻停止；
  - 报告不含 Key / 令牌 / 账号 ID；请求结果只留结构化摘要；
  - 估算花费仅供参考，准确费用以实际扣费为准（PT estimate 不支持 chat）。
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
CATALOG = HERE / "catalog.json"
REPORT_DIR = HERE / "probe-reports"
GENERATION_BASE = "https://api.powertokens.ai"
CHAT_ENDPOINT = "/v1/chat/completions"
MAIN_MODELS = ["glm-4.7", "deepseek-v4-pro", "qwen3.8-flash", "seed-2-0-pro-260328",
               "MiniMax-M3", "glm-5.2", "mimo-v2.6-pro"]
TRUSTED_SOURCES = {"official", "documented", "verified", "official+verified",
                   "verified(unmapped)", "models_api"}


def _load(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _blank_report(mode, max_usd):
    return {
        "generated_at": _now(), "mode": mode, "max_usd": max_usd,
        "cost_usd_estimated": 0.0, "requests_made": 0,
        "confirmed": [], "conflicts": [], "suggestions": [],
        "not_in_key_view": [], "new_models_to_review": [], "live_results": {},
        "notes": ["报告不含任何凭证；估算花费仅供参考，准确费用以实际扣费为准。",
                  "探测结果不写回 catalog 正文；verified / official / documented 的值必须人工确认。"],
    }


def offline_reconcile(cat, live_rows, registry, report, selected):
    """把官方 /v1/models 字段与 catalog 人工标注逐条对账，只出结论不改数据。"""
    by_id = {m.get("id"): m for m in (live_rows or [])}
    models = cat.get("models", {})
    report["live_view_model_count"] = len(by_id)
    for mid, entry in models.items():
        if entry.get("kind") != "chat":
            continue
        if selected and mid not in selected:
            continue
        caps = entry.get("capabilities") or {}
        live = by_id.get(mid)
        if not live:
            report["not_in_key_view"].append(mid)
            continue
        params = live.get("supported_parameters") or []
        reasoning = live.get("reasoning") or {}
        arch = live.get("architecture") or {}
        outs = arch.get("output_modalities") or []
        ins = arch.get("input_modalities") or []

        # 思考模式：官方 reasoning_effort 是能力声明，不等于真机 verified
        if "reasoning_effort" in params:
            declared = caps.get("thinking") in (True, "conditional")
            (report["confirmed"] if declared else report["conflicts"]).append({
                "model": mid, "field": "capabilities.thinking",
                "catalog": caps.get("thinking"),
                "models_api": {"reasoning_effort": True, "reasoning": reasoning},
                "note": "官方声明支持；思考开关是否真生效仍需 live 实测" if declared
                        else "官方声明支持 reasoning_effort，但 catalog 未标注 thinking，建议人工确认后补",
            })
        # 联网搜索：supported_parameters 与 catalog 的 web_search / how_to_enable 对账
        if "web_search_options" in params:
            wsd = entry.get("web_search_detail") or {}
            report["conflicts"].append({
                "model": mid, "field": "web_search_detail.how_to_enable",
                "catalog": wsd.get("how_to_enable"),
                "models_api": "supported_parameters 含 web_search_options",
                "note": "两种写法不一致且都未经真机验证；works / returns_sources 仍 null，"
                        "按 A1 目前不能作为搜索候选。需 live 实测后人工裁决（SSOT §14）。",
            })
        # 图像理解：output 仅 text + input 含 image 才算「理解类」
        if "image" in ins and set(outs) == {"text"}:
            vision = (caps.get("multimodal") or {}).get("vision")
            if vision is not True:
                report["conflicts"].append({
                    "model": mid, "field": "capabilities.multimodal.vision",
                    "catalog": vision, "models_api": "input 含 image 且 output 仅 text",
                    "note": "按 PT 判定规则应属理解类；vision 归属待人工裁决（SSOT §13.8 U2）。",
                })
        # limits：/v1/models top_provider 官方上下限字段对账（2026-11-17 PT 上线）
        # 映射：top_provider.context_length      -> limits.max_context_tokens
        #       top_provider.max_completion_tokens -> limits.max_output_tokens
        tp = live.get("top_provider") or {}
        api_ctx, api_out = tp.get("context_length"), tp.get("max_completion_tokens")
        lim = entry.get("limits")
        if isinstance(api_ctx, int) and isinstance(api_out, int):
            cur_ctx = lim.get("max_context_tokens") if lim else None
            cur_out = lim.get("max_output_tokens") if lim else None
            item = {
                "model": mid, "field": "limits.max_context_tokens / max_output_tokens",
                "catalog": {"max_context_tokens": cur_ctx, "max_output_tokens": cur_out,
                            "_source": lim.get("_source") if lim else None},
                "models_api": {"context_length": api_ctx, "max_completion_tokens": api_out},
            }
            if cur_ctx == api_ctx and cur_out == api_out:
                item["note"] = "与 /v1/models top_provider 官方字段一致。"
                report["confirmed"].append(item)
            elif cur_ctx is None or cur_out is None:
                item["note"] = "catalog 有 null，建议按 top_provider 官方字段补值"
                item["note"] += "（_source=models_api）；禁止从模型名推断。"
                report["suggestions"].append(item)
            else:
                item["note"] = "与 /v1/models top_provider 官方字段不一致，需人工裁决；"
                item["note"] += "探测不覆盖 verified / official / documented 的值。"
                report["conflicts"].append(item)

    # 新上架模型（registry is_new=true）自动生成待复核清单
    for mid, flags in (registry or {}).items():
        if isinstance(flags, dict) and flags.get("is_new"):
            report["new_models_to_review"].append(mid)


# ---------------------------------------------------------------- live 模式
_1PX_PNG = base64.b64encode(base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)).decode("ascii")


def _post_chat(key, payload, timeout=45):
    req = urllib.request.Request(
        GENERATION_BASE + CHAT_ENDPOINT,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"message": raw[:300]}
    except Exception as e:                                    # noqa: BLE001
        return 0, {"message": "%s: %s" % (type(e).__name__, str(e)[:200])}


def live_probe(cat, key, selected, max_usd, usd_per_1k, report):
    """按能力逐项实测。每次调用前检查预算；结果只写报告，不写 catalog。"""
    models = cat.get("models", {})
    tests = [
        ("tool_calling", lambda mid, lv: {
            "model": mid, "max_tokens": 24, "messages": [{"role": "user", "content": "say ok"}],
            "tools": [{"type": "function", "function": {"name": "ping", "description": "ping",
                       "parameters": {"type": "object", "properties": {}}}}],
        }),
        ("plain_chat", lambda mid, lv: {
            "model": mid, "max_tokens": 16, "messages": [{"role": "user", "content": "say ok"}],
        }),
    ]
    for mid in selected:
        if report["cost_usd_estimated"] >= max_usd:
            report["notes"].append("达到 --max-usd 上限，提前停止。")
            break
        entry = models.get(mid)
        if not entry or entry.get("kind") != "chat":
            continue
        out = {}
        for name, build in tests:
            if report["cost_usd_estimated"] >= max_usd:
                break
            status, body = _post_chat(key, build(mid, None))
            usage = (body or {}).get("usage") or {}
            tokens = usage.get("total_tokens") or 0
            report["cost_usd_estimated"] += tokens / 1000.0 * usd_per_1k
            report["requests_made"] += 1
            msg = ((body or {}).get("choices") or [{}])[0].get("message") or {}
            out[name] = {
                "http": status,
                "ok": 200 <= status < 300,
                "has_tool_calls": bool(msg.get("tool_calls")),
                "total_tokens": tokens,
                "error_code": ((body or {}).get("error") or {}).get("code"),
            }
        # 看图：两种格式各测一次（README 提醒 base64 单格式会假阴性）
        if (entry.get("capabilities") or {}).get("multimodal", {}).get("vision") is True:
            variants = []
            for label, content in [
                ("data_url_object", [
                    {"type": "text", "text": "what color is this?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + _1PX_PNG}},
                ]),
                ("data_url_string", "data:image/png;base64," + _1PX_PNG),
            ]:
                if report["cost_usd_estimated"] >= max_usd:
                    break
                status, body = _post_chat(key, {
                    "model": mid, "max_tokens": 16,
                    "messages": [{"role": "user", "content": content}],
                })
                usage = (body or {}).get("usage") or {}
                report["cost_usd_estimated"] += (usage.get("total_tokens") or 0) / 1000.0 * usd_per_1k
                report["requests_made"] += 1
                variants.append({"variant": label, "http": status,
                                 "ok": 200 <= status < 300,
                                 "error_code": ((body or {}).get("error") or {}).get("code")})
            out["vision_two_formats"] = variants
        report["live_results"][mid] = out
        report["suggestions"].append({
            "model": mid, "field": "tool_calling.verified / web_search_detail.works",
            "note": "本次 live 结果仅为探测建议值；需人工确认后手动写入 catalog 并把 _source 升为 verified。",
        })


def _api_key_from_local():
    """live 模式取 Key：优先环境变量，其次本机 skill config（不写日志、不落盘）。"""
    env = os.environ.get("POWERTOKENS_API_KEY")
    if env:
        return env
    cfg = pathlib.Path.home() / ".workbuddy" / "skills" / "powertokens-video" / "config.json"
    if cfg.exists():
        try:
            return json.loads(cfg.read_text(encoding="utf-8")).get("api_key")
        except (ValueError, OSError):
            return None
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="catalog 一键复核（默认离线 0 花费）")
    ap.add_argument("--catalog", default=str(CATALOG))
    ap.add_argument("--live-view", help="本地 /v1/models 响应 JSON（离线对账用）")
    ap.add_argument("--registry", help="本地 registry.json（生成新上架待复核清单）")
    ap.add_argument("--models", help="逗号分隔的模型 ID，默认 7 个主力模型")
    ap.add_argument("--out-dir", default=str(REPORT_DIR))
    ap.add_argument("--write-checked-at", action="store_true",
                    help="只把受影响字段的 _checked_at 更新为今天（不碰任何值）")
    ap.add_argument("--live", action="store_true", help="真实调用 chat/completions（会产生费用）")
    ap.add_argument("--max-usd", type=float, default=0.0, help="live 模式的花费硬上限，必须 > 0")
    ap.add_argument("--usd-per-1k-tokens", type=float, default=0.001,
                    help="live 估算单价，仅用于预算保护，不代表真实扣费")
    args = ap.parse_args(argv)

    if args.live and args.max_usd <= 0:
        print("[recheck] --live 必须显式给 --max-usd > 0（例如 0.5）；拒绝执行。", file=sys.stderr)
        return 2

    cat = _load(args.catalog)
    selected = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else MAIN_MODELS
    report = _blank_report("live" if args.live else "offline", args.max_usd)
    live_rows = []
    if args.live_view:
        raw = _load(args.live_view)
        live_rows = raw.get("data", raw) if isinstance(raw, dict) else raw
    registry = None
    if args.registry and pathlib.Path(args.registry).exists():
        registry = _load(args.registry).get("models")

    offline_reconcile(cat, live_rows, registry, report, set(selected) if not args.live else set(selected))

    if args.live:
        key = _api_key_from_local()
        if not key:
            print("[recheck] 没有可用生成域 API Key；设置 POWERTOKENS_API_KEY。", file=sys.stderr)
            return 2
        live_probe(cat, key, selected, args.max_usd, args.usd_per_1k_tokens, report)

    report["summary"] = {
        "confirmed": len(report["confirmed"]),
        "conflicts": len(report["conflicts"]),
        "suggestions": len(report["suggestions"]),
        "not_in_key_view": len(report["not_in_key_view"]),
        "new_models_to_review": len(report["new_models_to_review"]),
        "cost_usd_estimated": round(report["cost_usd_estimated"], 6),
        "requests_made": report["requests_made"],
    }
    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / ("%s.json" % dt.date.today().isoformat())
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.write_checked_at:
        today = dt.date.today().isoformat()
        changed = 0
        for mid, e in cat.get("models", {}).items():
            for field in ("limits", "tool_calling", "web_search_detail"):
                node = e.get(field)
                if isinstance(node, dict) and node.get("_source") not in TRUSTED_SOURCES:
                    node["_checked_at"] = today
                    changed += 1
        CATALOG.write_text(json.dumps(cat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["notes"].append("--write-checked-at 已更新 %d 处 _checked_at，未改动任何值。" % changed)

    print("[recheck] 报告：%s" % out)
    print("[recheck] %s" % json.dumps(report["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
