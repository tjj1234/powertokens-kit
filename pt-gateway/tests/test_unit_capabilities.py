#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unit：chat 能力真机探测 probe_chat_capabilities（全部打桩，0 联网）。

PT 的 /v1/models 不暴露能力字段，所以"真机探测"是把 configured 升级为 verified 的唯一手段。
本文件验证探测逻辑的三个分支都能正确判定 vision，且失败路径（报错不含 image 关键字）
会被保守记为 null 而不是误判。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import pt_gateway  # noqa: E402


class TestProbeChatCapabilities(unittest.TestCase):

    def _gw(self):
        gw = pt_gateway.TaskGateway("dummy-key")
        gw.key = "dummy-key"  # 跳过 _need_key；_api_call 会被打桩
        return gw

    def _stub(self, side_effect):
        old = pt_gateway._api_call
        pt_gateway._api_call = side_effect
        self.addCleanup(lambda: setattr(pt_gateway, "_api_call", old))

    def test_vision_true_when_200(self):
        def ok(*a, **k):
            return {"choices": [{"message": {"content": "一只猫"}}]}
        self._stub(ok)
        r = self._gw().probe_chat_capabilities("some-chat-model")
        self.assertEqual("verified", r["capabilities"]["_source"])
        self.assertTrue(r["capabilities"]["multimodal"]["vision"])

    def test_vision_false_when_image_error(self):
        def err(*a, **k):
            raise pt_gateway.ApiError(400, "messages with image_url are not supported by this model")
        self._stub(err)
        r = self._gw().probe_chat_capabilities("plain-llm")
        self.assertFalse(r["capabilities"]["multimodal"]["vision"])

    def test_vision_null_when_unrelated_error(self):
        def err(*a, **k):
            raise pt_gateway.ApiError(500, "internal server error")
        self._stub(err)
        r = self._gw().probe_chat_capabilities("flaky-model")
        self.assertIsNone(r["capabilities"]["multimodal"]["vision"])

    def test_persist_writes_verified_back(self):
        def ok(*a, **k):
            return {"choices": [{"message": {"content": "x"}}]}
        self._stub(ok)
        gw = self._gw()
        # 用一个临时 catalog，避免污染真实目录
        import json
        import tempfile
        from pathlib import Path
        d = tempfile.mkdtemp()
        p = Path(d) / "c.json"
        data = {
            "kinds": {},
            "models": {
                "m1": {
                    "kind": "chat",
                    "capabilities": {
                        "multimodal": {"vision": False, "audio_in": False},
                        "web_search": None,
                        "thinking": False,
                        "tools": True,
                        "streaming": True,
                        "_source": "configured",
                    },
                }
            },
        }
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        gw.catalog = pt_gateway.Catalog(path=str(p))
        gw.probe_chat_capabilities("m1", persist=True)
        back = json.loads(p.read_text(encoding="utf-8"))["models"]["m1"]["capabilities"]
        self.assertEqual("verified", back["_source"])
        self.assertTrue(back["multimodal"]["vision"])


if __name__ == "__main__":
    unittest.main()
