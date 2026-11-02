#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pt-gateway 测试入口（零外部依赖；也兼容 pytest）。

用法：
    python tests/run_tests.py                     # 离线层（unit + contract），0 花费、秒级
    python tests/run_tests.py --only gateway       # 只跑名字含 gateway 的模块
    python tests/run_tests.py --live               # 授权真机层（会花钱，受 --max-usd 保护）
    python tests/run_tests.py --live --tier standard --max-usd 1.0

退出码：0 = 全绿；1 = 有 FAIL/ERROR（可直接接 CI）。

报告：
    tests/reports/<时间戳>.md / .json，同时更新 latest.md / latest.json。
    写盘前做密钥扫描——发现完整 key 立即报错而不是写进仓库。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from harness import (  # noqa: E402
    mask_key, live_key, estimate, TIERS, DEFAULT_MAX_USD, TESTS_DIR,
)

REPORTS = HERE / "reports"
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"POWERTOKENS_API_KEY\s*=\s*[A-Za-z0-9]{16,}"),
]


class RecordingResult(unittest.TextTestResult):
    """在 TextTestResult 基础上记录每条用例的状态与耗时（报告用）。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.records = []
        self._t0 = {}

    def startTest(self, test):
        self._t0[test] = time.time()
        super().startTest(test)

    def _dur(self, test):
        return round(time.time() - self._t0.get(test, time.time()), 3)

    def _add(self, test, status, msg=""):
        self.records.append({
            "id": test.id(),
            "module": test.id().split(".")[0],
            "name": test.id().split(".")[-1],
            "status": status,
            "duration_s": self._dur(test),
            "message": (msg or "").strip()[-1500:],
        })

    def addSuccess(self, test):
        super().addSuccess(test)
        self._add(test, "PASS")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._add(test, "FAIL", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._add(test, "ERROR", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._add(test, "SKIP", str(reason))

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._add(test, "XFAIL", "")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._add(test, "XPASS", "")


def build_suite(patterns, only=None):
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for pat in patterns:
        found = loader.discover(start_dir=str(TESTS_DIR), pattern=pat,
                                top_level_dir=str(TESTS_DIR))
        for group in found:
            for case in _iter_tests(group):
                cid = case.id()
                if only and only.lower() not in cid.lower():
                    continue
                suite.addTest(case)
    return suite


def _iter_tests(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_tests(item)
        else:
            yield item


def summarize(records):
    from collections import Counter, defaultdict
    per_module = defaultdict(Counter)
    for r in records:
        per_module[r["module"]][r["status"]] += 1
        per_module[r["module"]]["total"] += 1
    overall = Counter(r["status"] for r in records)
    return per_module, overall


def render_markdown(meta, per_module, overall, records):
    L = []
    L.append("# pt-gateway 测试报告\n")
    L.append(f"- 时间：{meta['started_at']}（用时 {meta['duration_s']}s）")
    L.append(f"- 层级：**{meta['suites']}**；Python {meta['python']} / {meta['platform']}")
    L.append(f"- 总计：**{meta['total']}** 条 —— PASS {overall.get('PASS', 0)} / "
             f"FAIL {overall.get('FAIL', 0)} / ERROR {overall.get('ERROR', 0)} / "
             f"SKIP {overall.get('SKIP', 0)}")
    L.append(f"- 结论：**{'全绿' if meta['ok'] else '有失败，需要修复'}**（退出码 {meta['exit_code']}）")
    if meta.get("live"):
        L.append(f"- 真机档位：{meta['tier']}，预算上限 ${meta['max_usd']:.2f}，"
                 f"预估 ${meta['estimated_usd']:.4f}，key {meta['key_masked']}")
    else:
        L.append("- 真机层：未运行（离线，0 花费）")
    L.append("")
    L.append("## 按模块\n")
    L.append("| 模块 | 用例 | PASS | FAIL | ERROR | SKIP | 耗时(s) |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for mod in sorted(per_module):
        c = per_module[mod]
        dur = round(sum(r["duration_s"] for r in records if r["module"] == mod), 3)
        L.append(f"| `{mod}` | {c['total']} | {c.get('PASS', 0)} | {c.get('FAIL', 0)} | "
                 f"{c.get('ERROR', 0)} | {c.get('SKIP', 0)} | {dur} |")
    L.append("")
    bad = [r for r in records if r["status"] in ("FAIL", "ERROR")]
    if bad:
        L.append("## 失败明细\n")
        for r in bad:
            L.append(f"### {r['status']} · {r['id']}\n")
            L.append("```")
            L.append(r["message"])
            L.append("```")
            L.append("")
    else:
        L.append("## 失败明细\n\n无。\n")
    L.append("## 分层说明\n")
    L.append("- `test_unit_*`：纯逻辑，离线、0 花费、确定性（假时钟 + 打桩）。")
    L.append("- `test_contract_*`：接口契约，离线（真起回环 HTTP 服务 + 网关打桩 / AST 解析 MCP）。")
    L.append("- `test_live`：真机，花钱；需 `--live` 显式授权，且受预算守卫保护。")
    return "\n".join(L) + "\n"


def scan_secrets(text):
    hits = []
    key = live_key()
    if key and len(key) >= 8 and key in text:
        hits.append("出现完整 API Key")
    for pat in SECRET_PATTERNS:
        if pat.search(text or ""):
            hits.append(f"命中密钥正则 {pat.pattern}")
    return hits


def main():
    ap = argparse.ArgumentParser(description="pt-gateway 测试入口")
    ap.add_argument("--live", action="store_true",
                    help="授权跑真机层（会消耗 PT 额度；受 --max-usd 保护）")
    ap.add_argument("--tier", default=os.environ.get("PT_E2E_TIER", "minimal"),
                    choices=list(TIERS), help="真机档位（默认 minimal）")
    ap.add_argument("--max-usd", type=float,
                    default=float(os.environ.get("PT_E2E_MAX_USD", DEFAULT_MAX_USD)),
                    help=f"真机预算硬上限 USD（默认 {DEFAULT_MAX_USD}）")
    ap.add_argument("--only", default="", help="只跑 id 含该关键字的用例")
    ap.add_argument("--report-dir", default=str(REPORTS), help="报告输出目录")
    ap.add_argument("-q", "--quiet", action="store_true", help="精简输出")
    args = ap.parse_args()

    patterns = ["test_unit_*.py", "test_contract_*.py"]
    suites = "unit + contract（离线）"
    if args.live:
        os.environ["PT_LIVE"] = "1"
        os.environ["PT_E2E_TIER"] = args.tier
        os.environ["PT_E2E_MAX_USD"] = str(args.max_usd)
        patterns.append("test_live.py")
        suites += " + live（真机）"
        if not live_key():
            sys.stderr.write("ERROR: --live 需要 POWERTOKENS_API_KEY（环境变量）\n")
            return 2

    suite = build_suite(patterns, only=args.only or None)
    total = suite.countTestCases()
    if total == 0:
        sys.stderr.write("ERROR: 没匹配到任何用例\n")
        return 2

    sys.stderr.write(f"[run] {suites} | 共 {total} 条用例\n")
    t0 = time.time()
    runner = unittest.TextTestRunner(verbosity=1 if args.quiet else 2,
                                     resultclass=RecordingResult)
    result = runner.run(suite)
    duration = round(time.time() - t0, 3)

    per_module, overall = summarize(result.records)
    ok = not result.failures and not result.errors
    exit_code = 0 if ok else 1

    meta = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_s": duration,
        "suites": suites,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "total": total,
        "ok": ok,
        "exit_code": exit_code,
        "live": bool(args.live),
        "tier": args.tier,
        "max_usd": args.max_usd,
        "estimated_usd": estimate(args.tier)["total"],
        "key_masked": mask_key(live_key()) if args.live else "(未运行真机)",
        "only": args.only,
    }

    md = render_markdown(meta, per_module, overall, result.records)
    payload = {"meta": meta, "overall": dict(overall),
               "per_module": {k: dict(v) for k, v in per_module.items()},
               "records": result.records}

    hits = scan_secrets(md) + scan_secrets(json.dumps(payload, ensure_ascii=False))
    if hits:
        sys.stderr.write("ERROR: 报告含疑似密钥，已阻止落盘：" + "; ".join(hits) + "\n")
        return 3

    out_dir = Path(args.report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    md_path = out_dir / f"pt-gateway-tests-{stamp}.md"
    json_path = out_dir / f"pt-gateway-tests-{stamp}.json"
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "latest.md").write_text(md, encoding="utf-8")
    (out_dir / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                         encoding="utf-8")

    sys.stderr.write(
        f"\n[run] 结果：PASS {overall.get('PASS', 0)} / FAIL {overall.get('FAIL', 0)} / "
        f"ERROR {overall.get('ERROR', 0)} / SKIP {overall.get('SKIP', 0)}  "
        f"（{duration}s）\n[run] 报告：{md_path}\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
