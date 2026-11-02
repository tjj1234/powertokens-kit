#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""contract：MCP 工具面（离线）。

两条腿走路，因为本机不一定装了 mcp 包：
  1) AST 静态解析：不 import 就能校验"暴露了哪几个工具、参数名有没有漂移"——
     这条必须永远能跑，它是 CI 里唯一稳定的守门员。
  2) 桩掉 fastmcp 后真 import：校验行为（参数接线、JSON 出参、错误包装）。
     装了 mcp 包或者能桩住就跑，桩不住则跳过（不因此判失败）。
"""
import ast
import importlib.util
import json
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import pt_gateway, ApiError, GW_DIR  # noqa: E402

MCP_PY = GW_DIR / "mcp.py"
SERVER_PY = GW_DIR / "server.py"

# 网关参数 -> MCP 参数名（改名是可以的，但必须有映射，否则参数传不进来）
ALIAS = {"duration": "duration_s", "timeout": "timeout_s",
         "params": "params_json", "messages": "messages_json"}


def tool_functions():
    """AST 解析 mcp.py 里被 @mcp.tool() 装饰的函数。"""
    tree = ast.parse(MCP_PY.read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            name = None
            if isinstance(dec, ast.Call):
                name = ast.unparse(dec.func)
            elif isinstance(dec, (ast.Attribute, ast.Name)):
                name = ast.unparse(dec)
            if name and name.endswith("tool"):
                out[node.name] = [a.arg for a in node.args.args]
    return out


def load_mcp_module():
    """桩掉 fastmcp 后加载 mcp.py（失败返回 None，调用方 skip）。"""
    try:
        pkg = types.ModuleType("mcp")
        srv = types.ModuleType("mcp.server")
        fm = types.ModuleType("mcp.server.fastmcp")

        class FastMCP:
            def __init__(self, *a, **kw):
                self.tools = []

            def tool(self, *a, **kw):
                def deco(fn):
                    self.tools.append(fn.__name__)
                    return fn
                return deco

            def run(self, *a, **kw):
                return None

        fm.FastMCP = FastMCP
        srv.fastmcp = fm
        pkg.server = srv
        sys.modules["mcp"] = pkg
        sys.modules["mcp.server"] = srv
        sys.modules["mcp.server.fastmcp"] = fm

        spec = importlib.util.spec_from_file_location("pt_gateway_mcp_shell", str(MCP_PY))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


class FakeGateway:
    def __init__(self):
        self.calls = []
        self.err = None

    def list_capabilities(self, kind=None):
        self.calls.append(("list_capabilities", {"kind": kind}))
        return {"kinds": {"image": {}}, "models": {}}

    def media_generate(self, **kw):
        self.calls.append(("media_generate", kw))
        if self.err:
            raise self.err
        return {"ok": True, "kind": kw.get("kind"), "urls": ["http://cdn/1.png"]}

    def get_status(self, task_id):
        self.calls.append(("get_status", {"task_id": task_id}))
        return {"task_id": task_id, "state": "QUEUED"}

    def wait(self, task_id, timeout=600):
        self.calls.append(("wait", {"task_id": task_id, "timeout": timeout}))
        return {"task_id": task_id, "state": "SUCCEEDED"}


class TestMcpToolSurface(unittest.TestCase):
    """静态契约：工具数量、名字、参数（不用装 mcp 包也能跑）。"""

    def test_exactly_three_tools(self):
        """设计原则：只暴露 3 个统一工具，agent 选择面小、不易选错。"""
        self.assertEqual({"list_capabilities", "media_generate", "task_status"},
                         set(tool_functions().keys()))

    def test_no_cancel_tool(self):
        """回归：PT 无取消接口，MCP 不得暴露取消工具。"""
        for name in tool_functions():
            self.assertNotIn("cancel", name.lower(), f"出现了取消类工具：{name}")

    def test_media_generate_exposes_all_params(self):
        """漂移检测：网关有的参数，MCP 必须也有（或改名后仍在 ALIAS 里）。"""
        import inspect
        gw_params = [p for p in inspect.signature(
            pt_gateway.TaskGateway.media_generate).parameters if p != "self"]
        mcp_params = tool_functions()["media_generate"]
        missing = [p for p in gw_params if p not in mcp_params and ALIAS.get(p) not in mcp_params]
        self.assertEqual([], missing, f"MCP 未暴露网关参数：{missing}")

    def test_renamed_params_are_wired_not_just_declared(self):
        """声明了改名参数还不够——必须真的接到了网关上（否则是哑参数）。"""
        src = MCP_PY.read_text(encoding="utf-8")
        for wire in ("duration=duration_s", "timeout=timeout_s",
                     "params=_j(params_json)", "messages=_j(messages_json)"):
            self.assertIn(wire, src, f"MCP 参数未接线：{wire}")

    def test_task_status_has_wait_and_timeout(self):
        params = tool_functions()["task_status"]
        for p in ("task_id", "wait", "timeout_s"):
            self.assertIn(p, params)

    def test_list_capabilities_takes_optional_kind(self):
        self.assertIn("kind", tool_functions()["list_capabilities"])


class TestMcpNoCancelAnywhere(unittest.TestCase):
    """全仓扫描：取消相关的面必须彻底移除（避免文档/代码自相矛盾）。"""

    def test_server_has_no_cancel_route(self):
        self.assertNotIn("/cancel", SERVER_PY.read_text(encoding="utf-8"))

    def test_gateway_has_no_cancel_definition(self):
        src = (GW_DIR / "pt_gateway.py").read_text(encoding="utf-8")
        self.assertNotIn("def cancel", src)
        self.assertNotIn("SEEDANCE_CANCEL", src)

    def test_mcp_has_no_cancel_definition(self):
        src = MCP_PY.read_text(encoding="utf-8")
        self.assertNotIn("def cancel", src)


class TestMcpBehavior(unittest.TestCase):
    """行为契约：桩掉 fastmcp 后真跑一遍工具函数。"""

    @classmethod
    def setUpClass(cls):
        cls.mod = load_mcp_module()
        if cls.mod is None:
            raise unittest.SkipTest("无法桩载 mcp.py（跳过行为契约测试）")
        cls.fake = FakeGateway()
        cls.mod.GW = cls.fake

    def setUp(self):
        self.fake.calls.clear()
        self.fake.err = None

    def test_list_capabilities_returns_json_string(self):
        out = self.mod.list_capabilities("image")
        obj = json.loads(out)
        self.assertIn("kinds", obj)
        self.assertEqual(("list_capabilities", {"kind": "image"}), self.fake.calls[-1])

    def test_media_generate_plumbs_params(self):
        json.loads(self.mod.media_generate(kind="chat", model="qwen3.8-flash",
                                           prompt="hi", temperature=0.5, max_tokens=32))
        kw = self.fake.calls[-1][1]
        self.assertEqual("chat", kw["kind"])
        self.assertEqual(0.5, kw["temperature"])
        self.assertEqual(32, kw["max_tokens"])

    def test_temperature_negative_means_unset(self):
        self.mod.media_generate(kind="chat", prompt="hi", temperature=-1)
        self.assertIsNone(self.fake.calls[-1][1]["temperature"])

    def test_zero_like_values_are_dropped(self):
        """MCP 用 0 / 空串表示"没传"，网关应收到 None（否则会发出 n=0 这种怪请求）。"""
        self.mod.media_generate(kind="image", prompt="p", n=0, seed=0)
        kw = self.fake.calls[-1][1]
        self.assertIsNone(kw["n"])
        self.assertIsNone(kw["seed"])

    def test_messages_json_parsed(self):
        self.mod.media_generate(kind="chat", messages_json='[{"role":"user","content":"hi"}]')
        self.assertEqual([{"role": "user", "content": "hi"}],
                         self.fake.calls[-1][1]["messages"])

    def test_params_json_parsed(self):
        self.mod.media_generate(kind="audio", prompt="p", params_json='{"speed":1.2}')
        self.assertEqual({"speed": 1.2}, self.fake.calls[-1][1]["params"])

    def test_apierror_is_wrapped_as_ok_false(self):
        """MCP 层不抛异常——agent 拿到的是结构化错误，不是崩溃。"""
        self.fake.err = ApiError(403, "no permission")
        obj = json.loads(self.mod.media_generate(kind="chat", prompt="p"))
        self.assertFalse(obj["ok"])
        self.assertEqual(403, obj["status"])
        self.assertIn("no permission", obj["error"])

    def test_unexpected_error_is_also_wrapped(self):
        self.fake.err = RuntimeError("boom")
        obj = json.loads(self.mod.media_generate(kind="chat", prompt="p"))
        self.assertFalse(obj["ok"])
        self.assertIn("boom", obj["error"])

    def test_task_status_without_wait(self):
        obj = json.loads(self.mod.task_status("t1", wait=False))
        self.assertEqual("QUEUED", obj["state"])
        self.assertEqual(("get_status", {"task_id": "t1"}), self.fake.calls[-1])

    def test_task_status_with_wait_passes_timeout(self):
        json.loads(self.mod.task_status("t1", wait=True, timeout_s=120))
        self.assertEqual(("wait", {"task_id": "t1", "timeout": 120}), self.fake.calls[-1])


if __name__ == "__main__":
    unittest.main()
