#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手写零依赖校验器：catalog.json v2 的数据完整性守门员。

用法：
    python validate_catalog.py                 # 默认校验同目录 catalog.json
    python validate_catalog.py path/to.json   # 指定文件

设计约束（docs/catalog_v2_决策锁定_20261112.md）：
  - 零外部依赖，只用标准库；退出码 0 = OK / 1 = FAIL
  - A3：null = 不知道，[] = 确认没有，false = 确认不支持
  - A5：_source 九值（2026-11-13 增 models_api）；product 仅用于 auto_policy
  - A4：web_search_detail 只在 conditional 模型上；未实测值不得为 true
  - B1：kinds 恒为 5 类；capability_gaps 不得混入 kinds
  - 「只加字段」：原有 kinds/chat/capability_schema 里的 _source 是一段说明文字，
    不属于枚举，校验器不扫描原有子树，也不要求修改它。
"""
import json
import sys
from pathlib import Path

VALID_SOURCE = {
    "official", "documented", "verified", "official+verified",
    "inferred", "configured", "verified(unmapped)", "product", "models_api",
}
KNOWN_KINDS = {"image", "video", "seedance", "audio", "chat"}
MAIN = ["glm-4.7", "deepseek-v4-pro", "qwen3.8-flash", "seed-2-0-pro-260328",
        "MiniMax-M3", "glm-5.2", "mimo-v2.6-pro"]
ERROR_KEYS = ["rate_limited", "upstream_error", "timeout", "auth_invalid",
              "insufficient_balance", "model_unavailable", "param_unsupported",
              "content_filtered", "context_too_long"]
V2_SECTIONS = ["currency", "auto_policy", "errors", "capability_gaps"]


class Report:
    def __init__(self):
        self.fails = []

    def check(self, cond, label, detail=""):
        if cond:
            print("  PASS  " + label)
        else:
            print("  FAIL  " + label + ("  -- " + detail if detail else ""))
            self.fails.append(label)


def scan_sources(node, r, path, bad):
    """递归收集新增节点里的 _source 值（不触碰原有 kinds 子树）。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "_source":
                if v not in VALID_SOURCE:
                    bad.append("%s = %r" % (path, v))
            scan_sources(v, r, path + "/" + str(k), bad)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            scan_sources(v, r, path + "/[%d]" % i, bad)


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "catalog.json"
    r = Report()
    print("validate_catalog.py | %s" % path)

    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print("  FAIL  JSON 可解析 -- %s" % e)
        return 1
    r.check(isinstance(d, dict), "顶层是 object")

    # --- 版本与顶层节 ----------------------------------------------------
    r.check(d.get("schema") == 2, "schema == 2", "实际 %r" % d.get("schema"))
    r.check(bool(d.get("updated")), "updated 非空", "实际 %r" % d.get("updated"))
    for sec in V2_SECTIONS:
        r.check(isinstance(d.get(sec), dict), "顶层节存在: %s" % sec)

    # --- kinds / models（原有数据不得被破坏）-----------------------------
    kinds = d.get("kinds", {})
    r.check(set(kinds) == KNOWN_KINDS, "kinds 恰好 5 类（B1）", "实际 %r" % sorted(kinds))
    models = d.get("models", {})
    r.check(len(models) > 50, "models 数量 > 50", "实际 %d" % len(models))
    bad_kind = [m for m, e in models.items() if e.get("kind") not in KNOWN_KINDS]
    r.check(not bad_kind, "无 kind 越界条目", repr(bad_kind[:5]))
    missing_exec = [m for m, e in models.items() if not e.get("execution")]
    r.check(not missing_exec, "每个模型都有 execution", repr(missing_exec[:5]))
    missing_submit = [m for m, e in models.items() if not e.get("submit")]
    r.check(not missing_submit, "每个模型都有 submit", repr(missing_submit[:5]))

    # --- _source 枚举 ----------------------------------------------------
    bad = []
    for sec in V2_SECTIONS:
        if isinstance(d.get(sec), dict):
            scan_sources(d[sec], r, sec, bad)
    for mid in MAIN:
        e = models.get(mid, {})
        for g in ("limits", "tool_calling", "web_search_detail", "routing"):
            if g in e:
                scan_sources(e[g], r, "%s/%s" % (mid, g), bad)
        caps = e.get("capabilities")
        if isinstance(caps, dict) and "_source" in caps:
            if caps["_source"] not in VALID_SOURCE:
                bad.append("%s/capabilities = %r" % (mid, caps["_source"]))
    r.check(not bad, "新增节点 _source 全在 9 值枚举内（A5）", "; ".join(bad[:5]))
    r.check(d.get("auto_policy", {}).get("_source") == "product",
            "auto_policy._source == product")
    mac = d.get("models_api_contract", {})
    r.check(isinstance(mac, dict) and mac.get("_source") == "models_api",
            "models_api_contract._source == models_api")
    r.check("不在列表不等于下架" in mac.get("scope", ""),
            "models_api_contract.scope 声明「不在列表不等于下架」")
    r.check(mac.get("observed", {}).get("model_count") == 77,
            "models_api_contract.observed 记录 77 个可见模型")
    # product 只能出现在 auto_policy
    stray = []
    def find_product(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "_source" and v == "product" and not path.startswith("auto_policy"):
                    stray.append(path)
                find_product(v, path + "/" + str(k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                find_product(v, path + "/[%d]" % i)
    for sec in V2_SECTIONS:
        if isinstance(d.get(sec), dict):
            find_product(d[sec], sec)
    for mid in MAIN:
        for g in ("limits", "tool_calling", "web_search_detail", "routing"):
            if g in models.get(mid, {}):
                find_product(models[mid][g], "%s/%s" % (mid, g))
    r.check(not stray, "product 仅用于 auto_policy（A5）", repr(stray[:5]))
    r.check(all(models.get(m, {}).get("routing", {}).get("_source") == "configured"
                for m in MAIN), "routing._source == configured（A5）")

    # --- 需求 3：AUTO 两层 -----------------------------------------------
    ap = d.get("auto_policy", {})
    purposes = ap.get("purposes", {})
    r.check(isinstance(purposes, dict) and len(purposes) >= 1, "auto_policy.purposes 非空")
    for use, cands in (purposes.items() if isinstance(purposes, dict) else []):
        r.check(isinstance(cands, list) and len(cands) >= 2,
                "purposes.%s 候选 >= 2" % use, "实际 %r" % cands)
        missing = [c for c in (cands or []) if c not in models]
        r.check(not missing, "purposes.%s 候选都在 models 中" % use, repr(missing))
        channels = {models.get(c, {}).get("capabilities", {}).get("channel")
                    for c in (cands or []) if c in models}
        r.check(len(channels) >= 2, "purposes.%s 跨 >= 2 渠道" % use, repr(channels))
    r.check(isinstance(ap.get("skip_rules"), list) and len(ap.get("skip_rules", [])) >= 1,
            "auto_policy.skip_rules 存在")

    # --- 需求 4：errors 9 类 ---------------------------------------------
    errs = d.get("errors", {})
    r.check(set(errs) == set(ERROR_KEYS), "errors 恰好 9 类",
            "缺 %r / 多 %r" % (sorted(set(ERROR_KEYS) - set(errs)), sorted(set(errs) - set(ERROR_KEYS))))
    for k, v in (errs.items() if isinstance(errs, dict) else []):
        r.check(v.get("agent_action") in ("retry", "switch_model", "stop", "compress_context"),
                "errors.%s.agent_action 合法" % k, repr(v.get("agent_action")))
        ex = v.get("examples")
        r.check(isinstance(ex, list) and len(ex) >= 1, "errors.%s 至少 1 条样例" % k)
        r.check(v.get("_source") in VALID_SOURCE, "errors.%s._source 合法" % k,
                repr(v.get("_source")))
        r.check(all(isinstance(e, dict) and e.get("_source") in VALID_SOURCE
                    for e in (ex or [])), "errors.%s 每条样例都有合法 _source" % k)

    # --- B1：capability_gaps ---------------------------------------------
    gaps = d.get("capability_gaps", {})
    r.check(len(gaps) >= 1, "capability_gaps 非空")
    r.check(all(v.get("in_kinds") is False for v in gaps.values() if isinstance(v, dict)),
            "capability_gaps 每项 in_kinds === false")
    r.check(not (set(gaps) & set(kinds)), "capability_gaps 键未混入 kinds")

    # --- P0 字段 + 不编造实测结论 ----------------------------------------
    conditional = []
    for mid in MAIN:
        e = models.get(mid, {})
        for g in ("limits", "tool_calling", "routing"):
            r.check(g in e, "%s 有 %s" % (mid, g))
        r.check(e.get("tool_calling", {}).get("verified") is None,
                "%s.tool_calling.verified 未实测为 null" % mid,
                repr(e.get("tool_calling", {}).get("verified")))
        is_cond = e.get("capabilities", {}).get("web_search") == "conditional"
        if is_cond:
            conditional.append(mid)
            w = e.get("web_search_detail", {})
            r.check("web_search_detail" in e, "%s(conditional) 有 web_search_detail" % mid)
            r.check(w.get("how_to_enable") == e.get("capabilities", {}).get("web_search_param"),
                    "%s.how_to_enable 与 web_search_param 一致" % mid)
            r.check(w.get("works") is None and w.get("returns_sources") is None,
                    "%s 未实测 works/returns_sources 为 null" % mid)
        else:
            r.check("web_search_detail" not in e,
                    "%s 非 conditional，按 A4 不该有 web_search_detail" % mid)
    r.check(len(conditional) == 4, "conditional 主力模型共 4 个", repr(conditional))

    # --- 0.8.0：AUTO 用途覆盖 / vision 候选 / limits 子键 -------------------
    required_uses = {"general", "agent", "search", "long_report", "vision"}
    r.check(required_uses.issubset(set(purposes)),
            "auto_policy.purposes 覆盖 5 种用途",
            repr(sorted(required_uses - set(purposes))))
    vision_cands = purposes.get("vision", [])
    r.check(bool(vision_cands) and all(
        models.get(c, {}).get("capabilities", {}).get("multimodal", {}).get("vision") is True
        for c in vision_cands), "purposes.vision 候选都声明 vision=true", repr(vision_cands))
    for mid in MAIN:
        lim = models.get(mid, {}).get("limits", {})
        r.check("max_context_tokens" in lim and "max_output_tokens" in lim,
                "%s.limits 有 max_context_tokens / max_output_tokens 子键" % mid)
        r.check(isinstance(lim.get("max_context_tokens"), int) and lim["max_context_tokens"] > 0
                and isinstance(lim.get("max_output_tokens"), int) and lim["max_output_tokens"] > 0,
                "%s.limits 两个上限均为 /v1/models 实测正整数" % mid)
        r.check(lim.get("_source") == "models_api", "%s.limits._source == models_api" % mid)
    api_lims = [(mid, e["limits"]) for mid, e in models.items()
                if isinstance(e, dict) and isinstance(e.get("limits"), dict)
                and e["limits"].get("_source") == "models_api"]
    r.check(len(api_lims) == 30, "恰好 30 个模型 limits 来自 models_api 官方字段（2026-11-17）")
    r.check(all(isinstance(l["max_context_tokens"], int) and l["max_context_tokens"] > 0
                and isinstance(l["max_output_tokens"], int) and l["max_output_tokens"] > 0
                for _, l in api_lims), "所有 models_api limits 值均为正整数")
    lp = d.get("models_api_contract", {}).get("limits_probe", {})
    r.check(lp.get("_source") == "models_api", "limits_probe._source == models_api")
    r.check(lp.get("limit_like_field_carriers") == 31 and lp.get("model_count") == 77,
            "limits_probe 记录 77 模型 / 31 个上下限字段 carrier（2026-11-17）")
    r.check(lp.get("has_max_context_tokens") is True and lp.get("has_max_output_tokens") is True,
            "limits_probe.has_max_context_tokens / has_max_output_tokens 均为 true")
    r.check(lp.get("top_level_context_length_consistent") is True,
            "limits_probe 顶层 context_length 与 top_provider 逐模型一致")

    # --- v2.1：G1 errors 订正 + G2 新增节（依据资料总集 §6.3）--------------
    rp = d.get("retry_policy", {})
    r.check(isinstance(rp, dict) and rp.get("_source") in VALID_SOURCE,
            "retry_policy 存在且 _source 合法")
    r.check(isinstance(rp.get("rules"), list) and len(rp.get("rules", [])) == 5,
            "retry_policy 恰好 4+1 条规则")
    r.check(all(isinstance(x, dict) and "auto_retry" in x for x in rp.get("rules", [])),
            "retry_policy 每条规则含 auto_retry")
    r.check(rp.get("request_id_header") == "X-Oneapi-Request-Id",
            "retry_policy.request_id_header == X-Oneapi-Request-Id")
    ch = d.get("charging", {})
    r.check(isinstance(ch, dict) and ch.get("_source") in VALID_SOURCE,
            "charging 存在且 _source 合法")
    r.check(ch.get("async_task", {}).get("charged_on_failure") is False,
            "charging.async_task.charged_on_failure === false（异步全额退）")
    r.check(ch.get("sync_chat_stream", {}).get("charged_on_midstream_failure") is True,
            "charging.sync_chat_stream 流中途按量扣费（C6 裁决）")
    sd = kinds.get("seedance", {})
    iv = sd.get("interface_variants", {})
    rpv = sd.get("result_persistence", {})
    r.check(iv.get("active") == "openai_standard",
            "seedance.interface_variants.active == openai_standard")
    r.check(isinstance(iv.get("openai_standard"), dict)
            and isinstance(iv.get("vendor_compatible"), dict),
            "seedance 双接口定义齐全")
    r.check(isinstance(rpv.get("openai_standard"), dict)
            and isinstance(rpv.get("vendor_compatible"), dict),
            "seedance.result_persistence 双链路齐全")
    r.check("C3" in d.get("currency", {}).get("notes", ""),
            "currency.notes 含 C3 汇率说明")
    # G1 八处订正不得回退
    r.check(d["errors"]["upstream_error"]["http_status"] == [502, 503],
            "upstream_error 透传 502/503")
    r.check(d["errors"]["timeout"]["http_status"] == [500]
            and "Q8 未答" not in d["errors"]["timeout"]["notes"],
            "timeout 固定 500 且删除过时 Q8 表述")
    r.check(d["errors"]["param_unsupported"]["http_status"] == [400],
            "param_unsupported 400")
    r.check(d["errors"]["context_too_long"]["http_status"] == [400],
            "context_too_long 400")
    r.check(d["errors"]["model_unavailable"]["http_status"] == [403, 503],
            "model_unavailable 403 + 503")
    # 新增节点 _source 扫描（收窄到新增节点，不碰原有说明文字型 _source）
    bad21 = []
    for name in ("retry_policy", "charging"):
        scan_sources(d.get(name, {}), r, name, bad21)
    for name in ("interface_variants", "result_persistence"):
        scan_sources(sd.get(name, {}), r, "kinds.seedance/" + name, bad21)
    r.check(not bad21, "新增节点 _source 全在 9 值枚举内（v2.1）", "; ".join(bad21[:5]))

    # --- 真机实测结论（2026-11-12，P0 优先于书面材料）-----------------------
    rpv_obs = sd.get("result_persistence", {}).get("image_sync_observed", {})
    r.check(rpv_obs.get("_source") == "verified"
            and rpv_obs.get("ttl_seconds") == 86415,
            "图像同步接口 24h 签名 URL 真机结论已落地（C4）")
    r.check(rpv_obs.get("url_kind", "").startswith("上游对象存储签名链接"),
            "image_sync_observed.url_kind 标注上游签名链接")
    r.check("成功即下载" in sd.get("result_persistence", {}).get("default_strategy", ""),
            "result_persistence.default_strategy 要求成功即下载")
    r.check(d.get("retry_policy", {}).get("request_id_success_body_field") == "id",
            "R1 实测：成功 body request_id 字段名 == id")
    last_ex = d["errors"]["insufficient_balance"]["examples"][-1]
    r.check(last_ex.get("status") == 402
            and last_ex.get("_source") == "verified"
            and "pre_consume_token_quota_failed" in last_ex.get("note", ""),
            "现行 402 真机样例已落地（C9 验证）")

    # --- 0.8.1 真机计费观测（C4 视频侧 / R1 异步定案） --------------------
    obs = d.get("live_billing_observations")
    r.check(isinstance(obs, dict) and obs.get("_source") == "verified",
            "live_billing_observations 存在且来源 verified")
    r.check(isinstance(obs, dict) and obs.get("credits_per_usd") == 1000,
            "观测节汇率 credits_per_usd=1000")
    cases = obs.get("cases") if isinstance(obs, dict) else None
    r.check(isinstance(cases, list) and len(cases) >= 1,
            "观测节至少 1 个真机 case")
    c0 = cases[0] if isinstance(cases, list) and cases else {}
    r.check(c0.get("model") == "wan3.0-video" and c0.get("charged_quota") == 500,
            "wan3.0-video 5s 真机实扣 500 credits")
    r.check(c0.get("balance_before") == 49450 and c0.get("balance_after") == 48950,
            "余额前后 49450 -> 48950 自洽")
    r.check((c0.get("billing") or {}).get("billing_unit") == "per_second"
            and (c0.get("billing") or {}).get("unit_quota") == 100,
            "计费口径 per_second / 100 credits 每秒")
    rp = c0.get("result_persistence") or {}
    r.check(rp.get("signed") is False and rp.get("requires_auth") is False
            and rp.get("ttl_field_in_task") is None,
            "C4 视频侧：无签名 / 无需鉴权 / 任务对象无 TTL 字段")
    rq = c0.get("request_id_contract") or {}
    r.check(rq.get("request_id_header") == "X-Oneapi-Request-Id"
            and rq.get("has_x_request_id_header") is False
            and rq.get("submit_body_id_equals_task_id") is True,
            "R1 异步：request_id 在 X-Oneapi-Request-Id，body.id 是 task_id")
    r.check(isinstance(obs, dict) and len(obs.get("conflicts_registered") or []) >= 5,
            "单价/产物/usage 冲突已登记，不静默选边")

    # --- 0.8.2：U10 单价裁决（PT 2026-11-16 确认以实际扣费为准）-----------
    pp = d.get("pricing_policy")
    r.check(isinstance(pp, dict) and pp.get("_source") == "verified",
            "pricing_policy 存在且来源 verified")
    r.check(isinstance(pp, dict) and "实际扣费" in pp.get("authority", "")
            and "estimate" in pp.get("authority", ""),
            "价格真相源：实际扣费 > estimate 预估 > 探测值")
    r.check(isinstance(pp, dict) and "偏高" in pp.get("estimate_caveat", "")
            and "参考" in pp.get("estimate_caveat", ""),
            "estimate 预估可能偏高、只作参考已注明")
    verified_models = pp.get("verified_models") if isinstance(pp, dict) else None
    r.check(isinstance(verified_models, list) and len(verified_models) == 1,
            "仅 1 个模型档位经实扣验证，未批量订正", "实际 %r" % (verified_models,))
    vm = verified_models[0] if isinstance(verified_models, list) and verified_models else {}
    r.check(vm.get("model") == "wan3.0-video" and vm.get("price_usd_5s_720p") == 0.5
            and vm.get("credits_5s_720p") == 500 and vm.get("source") == "verified",
            "wan3.0-video 5s/720p 实扣 500 credits / 0.5 USD 标 verified",
            repr(vm))
    w3 = models.get("wan3.0-video", {})
    r.check(w3.get("price_usd_5s_720p") == 0.5
            and w3.get("price_usd_5s_720p_source") == "verified",
            "wan3.0-video 单价已订正为 0.5 且带 source 标注")
    # 其他视频模型不得被批量改写（保留原有探测值 / null）
    other_prices = {k: v.get("price_usd_5s_720p") for k, v in models.items()
                    if v.get("kind") == "video" and k != "wan3.0-video"}
    r.check(other_prices.get("wan3.0-video-prime") == 0.7
            and other_prices.get("wan2.7-t2v") == 0.475,
            "其他视频模型单价未批量订正", repr({k: other_prices.get(k) for k in ("wan3.0-video-prime", "wan2.7-t2v")}))
    r.check(all(v.get("price_usd_5s_720p_source") is None
                for k, v in models.items() if k != "wan3.0-video"),
            "只有 wan3.0-video 带 verified 价格来源标注")
    r.check("实际扣费" in d.get("charging", {}).get("estimate_vs_actual", "")
            and "参考" in d.get("charging", {}).get("estimate_vs_actual", ""),
            "charging.estimate_vs_actual 以实扣为准并注明预估只作参考")

    # --- 0.8.2：Node 包内 catalog 快照与主 catalog 一致性（打包自包含）----
    snap_path = Path(__file__).resolve().parent.parent / "node" / "catalog.snapshot.json"
    main_path = Path(__file__).resolve().parent / "catalog.json"
    if snap_path.exists():
        try:
            snap = json.loads(snap_path.read_text(encoding="utf-8"))
            main = json.loads(main_path.read_text(encoding="utf-8"))
            r.check(snap == main, "node/catalog.snapshot.json 与主 catalog 内容一致",
                    "快照滞后，npm pack 前需重新生成")
        except Exception as e:
            r.check(False, "node/catalog.snapshot.json 可解析", str(e))
    else:
        r.check(False, "node/catalog.snapshot.json 存在（npm pack 自包含）")

    # --- 汇总 -------------------------------------------------------------
    print("")
    if r.fails:
        print("FAIL: %d 项不通过" % len(r.fails))
        for f in r.fails:
            print("  - " + f)
        return 1
    print("OK: catalog v2 全部校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
