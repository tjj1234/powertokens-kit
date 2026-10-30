#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PT 公开层模型目录爬虫（无 Key，零外部依赖）。

数据来源（已实测，2026-10-30）：
  - sitemap : https://docs.powertokens.ai/sitemap.xml
              396 个 <loc>，其中 350 个是模型页（zmodelVideo/Image/Audio/Chat），无子 sitemap。
  - 模型页  : https://docs.powertokens.ai/{lang}/zmodel{Type}/{vendor}/{rest}
              lang ∈ {en, zh-Hans}；Type ∈ {Video, Image, Audio, Chat}。
  - OpenAPI : https://docs.powertokens.ai/api-reference/{lang}/zmodel{Type}/{vendor}/{rest}.json
              把模型页 path 前插 /api-reference、后缀加 .json 即得（无 key 可取），
              返回标准 OpenAPI 3.0.1 片段，含：
                - info.title / info.description   （模型名称与说明，en/zh 双语）
                - paths                            （真实 vendor 端点 path + method）
                - components.schemas[*].model.enum（模型 ID 枚举，一页可含多个）
                - 请求体 schema / 响应 status enum（判同步/异步）

产出（公开层全局真相，对应 FIXLIST §2.1 / §2.2）：
  catalog.public.json —— 全量模型的结构化档案（无 key 即知的全集）。
  字段：kind / type / vendor / title / execution(同步·异步) / vendor_endpoint(真实路径) /
        submit(按 kind 派生的统一端点) / params_observed / status_enum / first_seen / last_seen。
  其中 kind/execution/端点 是爬虫可证的「全局真相」；price/resolution/modes 等 PT 不给的
  字段由人工档案(catalog.json)维护，二者在 --merge 时合并，互不覆盖（见下）。

下架判定（§2.1 核心）：
  本次 crawl 未出现的模型 → delisted=True（判定从 key 层移到公开层，消除多 key 误判）。
  爬虫只写 catalog.public.json，绝不碰 registry.json；registry 退化「本 key 权限视图」，
  其 delisted 由公开层真相驱动（合并逻辑在后续讨论 §2.1 时再定，本脚本只产出真相）。

合并策略（§2.2，默认不执行，需显式 --merge）：
  把 kind/execution/vendor/endpoint/params 等爬虫可证字段并入 catalog.json.models，
  **保留**人工档案（price_usd_5s_720p / resolutions / modes / aspect_ratios）不被覆盖；
  爬虫未证实的字段绝不编造。新增模型打 source:"crawler" 便于区分。

用法：
  python catalog_crawler.py                 # 完整爬 PT 官网 → 写 catalog.public.json
  python catalog_crawler.py --dry-run       # 爬但不写 catalog.public.json（仅打印摘要）
  python catalog_crawler.py --limit 30      # 只处理前 30 个模型页（调试）
  python catalog_crawler.py --lang en       # 仅用 en 标题（默认 en 优先，缺则 zh-Hans）
  python catalog_crawler.py --merge         # 爬 + 合并进 catalog.json（默认关闭，待 §2.2 讨论）
  python catalog_crawler.py --delay 0.1 --retries 3 --timeout 30
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler, urlopen

_HERE = Path(__file__).resolve().parent
SITEMAP_URL = "https://docs.powertokens.ai/sitemap.xml"
PUBLIC_CATALOG = _HERE / "catalog.public.json"
CATALOG_JSON = _HERE / "catalog.json"
UA = "Mozilla/5.0 (compatible; PTGatewayCrawler/1.0)"
MODEL_RE = re.compile(r"/zmodel(Video|Image|Audio|Chat)/")
PAGE_RE = re.compile(r"https?://docs\.powertokens\.ai/(?P<lang>en|zh-Hans)/zmodel(?P<type>Video|Image|Audio|Chat)/(?P<vendor>[^/]+)/(?P<rest>.+?)/?$")
TASK_POLL_RE = re.compile(r"/tasks/\{[^}]+\}$", re.IGNORECASE)

# kind 派生：type + vendor / 标题 判定 seedance
SEEDANCE_VENDOR = {"seedance"}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_opener():
    """沙箱内走代理（HTTP_PROXY/HTTPS_PROXY），无代理则直连。"""
    ph = {}
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        v = os.environ.get(k)
        if v:
            ph["https"] = v
            ph["http"] = v
            break
    return build_opener(ProxyHandler(ph)) if ph else build_opener()


OPENER = make_opener()
CTX = ssl.create_default_context()


def http_get(url, timeout=30, retries=3, delay=0.2):
    """带重试/超时/代理/gzip 的 GET，返回解码后的文本；失败抛 URLError/HTTPError。"""
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers={"User-Agent": UA,
                                        "Accept": "application/json,text/xml,*/*"})
            with OPENER.open(req, timeout=timeout) as r:
                raw = r.read()
            if raw[:2] == b"\x1f\x8b":  # gzip
                raw = gzip.decompress(raw)
            return raw.decode("utf-8-sig", "replace")
        except (HTTPError, URLError, ssl.SSLError, OSError) as e:
            last = e
            if isinstance(e, HTTPError) and e.code in (404, 403):
                raise  # 404/403 不重试，直接判定缺失
            if attempt < retries:
                time.sleep(delay * attempt)
    raise last


def fetch_sitemap():
    txt = http_get(SITEMAP_URL, timeout=30)
    return re.findall(r"<loc>(.*?)</loc>", txt)


def parse_page(loc):
    m = PAGE_RE.search(loc)
    if not m:
        return None
    return m.groupdict()


def openapi_url_for(loc):
    """模型页 → OpenAPI JSON URL：path 前插 /api-reference、后缀 .json。"""
    path = loc.split("docs.powertokens.ai", 1)[1]
    return "https://docs.powertokens.ai/api-reference" + path + ".json"


def collect_model_ids(schemas):
    """遍历所有 schema，收集 properties.model.enum（含 default 兜底）。"""
    ids = []
    for sc in schemas.values():
        props = (sc or {}).get("properties") or {}
        mp = props.get("model")
        if isinstance(mp, dict):
            en = mp.get("enum")
            if isinstance(en, list) and en:
                ids.extend(en)
            elif mp.get("default") and isinstance(mp["default"], str):
                ids.append(mp["default"])
    # 去重保序
    seen, out = set(), []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def collect_params(schemas):
    """从请求体 schema 抽取可观察参数名 + required（兼容 input/parameters 为 $ref 或内联对象）。"""
    names, required = [], []
    for sc in schemas.values():
        props = (sc or {}).get("properties") or {}
        if "model" in props and ("input" in props or "parameters" in props):
            # 这是请求体 schema
            for pname in props:
                if pname in ("model",):
                    continue
                names.append(pname)
            required.extend((sc or {}).get("required") or [])
            # 递归 input/parameters 的子字段（可能是 $ref 或内联对象）
            for sub in ("input", "parameters"):
                node = props.get(sub)
                if not isinstance(node, dict):
                    continue
                ref = node.get("$ref")
                if ref:
                    sname = ref.split("/")[-1]
                    sub_props = (schemas.get(sname) or {}).get("properties") or {}
                else:
                    sub_props = node.get("properties") or {}
                for sp in sub_props:
                    if sp not in names:
                        names.append(sp)
    return names, required


def collect_status_enum(schemas):
    for sc in schemas.values():
        props = (sc or {}).get("properties") or {}
        ts = props.get("task_status")
        if isinstance(ts, dict) and isinstance(ts.get("enum"), list):
            return ts["enum"]
    return None


def detect_execution(raw_paths, kind_default):
    """存在 /tasks/{id} GET 轮询路径或 task_status 枚举 → 异步；否则按 kind 默认。"""
    for p, methods in (raw_paths or {}).items():
        if TASK_POLL_RE.search(p) and "get" in (methods or {}):
            return "async"
    # 退而求其次：POST 响应描述含 task_id / 查询状态
    for methods in (raw_paths or {}).values():
        for meth in (methods or {}).values():
            desc = (meth or {}).get("description", "") or ""
            if "task_id" in desc.lower() and ("query" in desc.lower() or "status" in desc.lower()):
                return "async"
    return kind_default


def derive_kind(type_, vendor, title):
    t = (type_ or "").lower()
    if t == "image":
        return "image"
    if t == "audio":
        return "audio"
    if t == "chat":
        return "chat"
    if t == "video":
        if (vendor or "").lower() in SEEDANCE_VENDOR or "seedance" in (title or "").lower():
            return "seedance"
        return "video"
    return "unknown"


def unified_submit(kind):
    if kind in ("video", "seedance"):
        return "/v1/videos"
    if kind == "image":
        return "/v1/images/generations"
    if kind == "audio":
        return "/ali/api/v1/services/aigc/multimodal-generation/generation"
    return None


def parse_openapi(text, type_, vendor):
    """解析单页 OpenAPI → 该页模型列表的字典。"""
    try:
        data = json.loads(text)
    except Exception:
        return None
    info = data.get("info", {})
    title = info.get("title")
    raw_paths = data.get("paths") or {}
    paths = {p: list((m or {}).keys()) for p, m in raw_paths.items()}
    schemas = (data.get("components") or {}).get("schemas") or {}
    model_ids = collect_model_ids(schemas)
    if not model_ids:
        return None
    params, required = collect_params(schemas)
    status_enum = collect_status_enum(schemas)
    # 取 vendor 端点（POST 优先，异步取提交 path）
    vendor_endpoint = None
    vendor_method = None
    for p, methods in (data.get("paths") or {}).items():
        if "post" in methods:
            vendor_endpoint, vendor_method = p, "post"
            break
    if vendor_endpoint is None and paths:
        p = next(iter(paths))
        vendor_endpoint, vendor_method = p, (paths[p][0] if paths[p] else None)
    out = []
    kind_default = "async" if type_ in ("Video",) else "sync"
    for mid in model_ids:
        kind = derive_kind(type_, vendor, title)
        execution = detect_execution(raw_paths, kind_default)
        out.append({
            "model_id": mid,
            "kind": kind,
            "type": type_,
            "vendor": vendor,
            "title": title,
            "execution": execution,
            "vendor_endpoint": vendor_endpoint,
            "vendor_method": vendor_method,
            "submit": unified_submit(kind),
            "poll": "/v1/videos/{task_id}" if execution == "async" else None,
            "params_observed": params,
            "required_observed": required,
            "status_enum": status_enum,
        })
    return out


def dedupe_pages(locs):
    """按 (type, vendor, rest) 去重（忽略 lang），每组保留 en 优先。"""
    groups = {}
    for loc in locs:
        pg = parse_page(loc)
        if not pg:
            continue
        key = (pg["type"], pg["vendor"], pg["rest"])
        grp = groups.setdefault(key, [])
        grp.append(pg)
    # 每组选主 lang：en 优先，否则 zh-Hans
    chosen = []
    for key, grp in groups.items():
        primary = next((g for g in grp if g["lang"] == "en"), grp[0])
        chosen.append(primary)
    return chosen


def crawl(limit=None, lang_filter=None, dry_run=False, delay=0.05,
          retries=3, timeout=30):
    print(f"[crawler] 抓取 sitemap: {SITEMAP_URL}")
    locs = fetch_sitemap()
    print(f"[crawler] sitemap 共 {len(locs)} 个 <loc>")
    pages = dedupe_pages(locs)
    print(f"[crawler] 去重后模型页 {len(pages)} 个（en 优先）")
    if lang_filter:
        pages = [p for p in pages if p["lang"] == lang_filter]
        print(f"[crawler] 过滤 lang={lang_filter} 后 {len(pages)} 个")
    if limit:
        pages = pages[:limit]
        print(f"[crawler] --limit {limit}：处理前 {len(pages)} 个")

    models = {}
    stats = {"Video": 0, "Image": 0, "Audio": 0, "Chat": 0}
    fail = 0
    ts = now_iso()
    for idx, pg in enumerate(pages, 1):
        loc = (f"https://docs.powertokens.ai/{pg['lang']}/zmodel{pg['type']}"
               f"/{pg['vendor']}/{pg['rest']}")
        api_url = openapi_url_for(loc)
        try:
            txt = http_get(api_url, timeout=timeout, retries=retries, delay=delay)
        except HTTPError as e:
            # 主 lang 404 时尝试另一 lang
            other = "zh-Hans" if pg["lang"] == "en" else "en"
            alt_loc = (f"https://docs.powertokens.ai/{other}/zmodel{pg['type']}"
                       f"/{pg['vendor']}/{pg['rest']}")
            try:
                txt = http_get(openapi_url_for(alt_loc), timeout=timeout,
                               retries=retries, delay=delay)
            except Exception as e2:
                print(f"  [{idx}/{len(pages)}] FAIL {loc} ({e.code if isinstance(e, HTTPError) else e})")
                fail += 1
                time.sleep(delay)
                continue
        except Exception as e:
            print(f"  [{idx}/{len(pages)}] FAIL {loc} ({e})")
            fail += 1
            time.sleep(delay)
            continue

        parsed = parse_openapi(txt, pg["type"], pg["vendor"])
        if not parsed:
            print(f"  [{idx}/{len(pages)}] SKIP {loc}（无 model enum）")
            time.sleep(delay)
            continue
        for entry in parsed:
            mid = entry["model_id"]
            if mid in models:
                # 同模型多页：保留 title 更完整的（en 优先已隐含）
                if not models[mid].get("title") and entry.get("title"):
                    models[mid]["title"] = entry["title"]
                continue
            entry["first_seen"] = ts
            entry["last_seen"] = ts
            models[mid] = entry
            stats[pg["type"]] = stats.get(pg["type"], 0) + 1
        if idx % 25 == 0:
            print(f"  [{idx}/{len(pages)}] 已解析 {len(models)} 个模型 …")
        time.sleep(delay)

    total = len(models)
    print(f"[crawler] 完成：解析 {total} 个模型（失败 {fail}）")
    print(f"[crawler] 按 type 分布：Video={stats['Video']} Image={stats['Image']} "
          f"Audio={stats['Audio']} Chat={stats['Chat']}")

    public = {
        "schema": 1,
        "source": "https://docs.powertokens.ai (sitemap.xml + api-reference OpenAPI 3.0.1 片段)",
        "crawled_at": ts,
        "note": ("公开层全局模型真相（无 key 爬 PT 官网）。kind/execution/端点为爬虫可证字段；"
                 "price/resolution/modes 等由人工档案 catalog.json 维护，--merge 时合并且不覆盖。"),
        "stats": {"total": total, **stats},
        "models": models,
    }
    if not dry_run:
        PUBLIC_CATALOG.write_text(json.dumps(public, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"[crawler] 已写 {PUBLIC_CATALOG.name}（{total} 个模型）")
    else:
        print("[crawler] --dry-run：未写文件")
    return public


def merge_into_catalog(public):
    """把爬虫可证字段并入 catalog.json.models，保留人工档案（price/resolution/modes）。"""
    if not CATALOG_JSON.exists():
        print("[merge] 找不到 catalog.json，跳过合并")
        return
    cat = json.loads(CATALOG_JSON.read_text(encoding="utf-8"))
    models = cat.setdefault("models", {})
    added, updated = [], []
    for mid, e in public["models"].items():
        cur = models.get(mid, {})
        changed = False
        for f in ("kind", "type", "vendor", "title", "execution",
                  "vendor_endpoint", "vendor_method", "submit", "poll",
                  "params_observed", "required_observed", "status_enum"):
            if e.get(f) is not None and cur.get(f) != e.get(f):
                cur[f] = e[f]
                changed = True
        if "model_id" not in cur:
            cur["model_id"] = mid
        if mid not in models:
            cur["source"] = "crawler"
            cur["first_seen"] = e.get("first_seen")
            cur["last_seen"] = e.get("last_seen")
            added.append(mid)
            models[mid] = cur
        elif changed:
            updated.append(mid)
    # 下架判定：本次 crawl 未出现 → delisted
    present = set(public["models"].keys())
    delisted = [m for m in models if m not in present and not models[m].get("delisted")]
    for m in delisted:
        models[m]["delisted"] = True
        models[m]["last_seen_public"] = public["crawled_at"]
    cat["updated"] = public["crawled_at"]
    CATALOG_JSON.write_text(json.dumps(cat, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[merge] 新增 {len(added)}，更新 {len(updated)}，下架标记 {len(delisted)} → {CATALOG_JSON.name}")


def main():
    ap = argparse.ArgumentParser(description="PT 公开层模型目录爬虫（无 Key）")
    ap.add_argument("--dry-run", action="store_true", help="爬但不写 catalog.public.json")
    ap.add_argument("--limit", type=int, default=None, help="只处理前 N 个模型页（调试）")
    ap.add_argument("--lang", default=None, choices=["en", "zh-Hans"], help="仅用指定 lang 标题")
    ap.add_argument("--merge", action="store_true", help="爬 + 合并进 catalog.json（默认关闭）")
    ap.add_argument("--delay", type=float, default=0.05, help="请求间隔秒（默认 0.05）")
    ap.add_argument("--retries", type=int, default=3, help="失败重试次数（默认 3）")
    ap.add_argument("--timeout", type=int, default=30, help="单请求超时秒（默认 30）")
    args = ap.parse_args()

    public = crawl(limit=args.limit, lang_filter=args.lang, dry_run=args.dry_run,
                   delay=args.delay, retries=args.retries, timeout=args.timeout)
    if args.merge and not args.dry_run:
        merge_into_catalog(public)


if __name__ == "__main__":
    main()
