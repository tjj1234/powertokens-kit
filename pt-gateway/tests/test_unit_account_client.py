#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unit：账户客户端（2026-11-13 真机实测口径）与 account_query 兼容层。

离线、0 花费：全部用假 opener，不发任何网络请求，也不使用真实凭证。
"""
import io
import json
import os
import sys
import unittest
from pathlib import Path

GW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(GW_DIR))
import account_client  # noqa: E402
from account_client import AccountClient, normalize_result, to_numeric_key_id  # noqa: E402

OK_BODY = {"code": 200, "data": {"quota": 80}, "msg": "success"}


class FakeResponse(io.BytesIO):
    def __init__(self, status, payload):
        super().__init__(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


class FakeOpener:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = OK_BODY if payload is None else payload
        self.calls = []

    def open(self, req, timeout=None):
        self.calls.append(req)
        if self.status >= 400:
            import urllib.error
            raise urllib.error.HTTPError(req.full_url, self.status, "err", {}, None)
        return FakeResponse(self.status, self.payload)


def client(opener, **kw):
    return AccountClient(token="pt_live_TEST_ONLY", key_id=123456, opener=opener, **kw)


class TestNormalize(unittest.TestCase):
    def test_code_is_authoritative_over_http(self):
        r = normalize_result({"code": 500, "msg": "token not found"}, 200)
        self.assertFalse(r["ok"], "HTTP 200 + code=500 不能当成功")
        self.assertEqual("business_error", r["error"]["category"])
        self.assertEqual(200, r["error"]["http_status"])

    def test_success_shape(self):
        r = normalize_result(OK_BODY, 200)
        self.assertTrue(r["ok"])
        self.assertEqual({"quota": 80}, r["data"])
        self.assertFalse(r["is_cached"])
        self.assertTrue(r["data_as_of"])

    def test_http_error_categories(self):
        for status, expect in [(401, "auth_invalid"), (403, "forbidden"),
                               (404, "not_found"), (429, "rate_limited"),
                               (500, "upstream_error"), (400, "bad_request")]:
            r = normalize_result({"code": 500, "msg": "x"}, status)
            self.assertEqual(expect, r["error"]["category"], status)

    def test_numeric_key_id_rejects_empty_and_masked(self):
        self.assertEqual(123456, to_numeric_key_id(123456))
        self.assertEqual(123456, to_numeric_key_id("123456"))
        for bad in (None, "", "   ", "sk-****abcd", "123456abc"):
            self.assertIsNone(to_numeric_key_id(bad), repr(bad))


class TestClient(unittest.TestCase):
    def test_missing_token_fails_locally_without_request(self):
        op = FakeOpener()
        c = AccountClient(key_id=123456, opener=op)
        r = c.get_balance()
        self.assertFalse(r["ok"])
        self.assertEqual("credentials_required", r["error"]["category"])
        self.assertEqual(0, len(op.calls))

    def test_bare_token_and_numeric_key_id(self):
        op = FakeOpener()
        r = client(op).get_balance()
        self.assertTrue(r["ok"])
        req = op.calls[0]
        self.assertEqual("pt_live_TEST_ONLY", req.headers["Authorization"])
        self.assertNotIn("Bearer", req.headers["Authorization"])
        self.assertIn("key_id=123456", req.full_url)
        self.assertIn("/v1/billing/balance", req.full_url)
        self.assertTrue(req.full_url.startswith(account_client.ACCOUNT_BASE_DEFAULT))

    def test_key_id_required_for_balance(self):
        op = FakeOpener()
        c = AccountClient(token="pt_live_TEST_ONLY", key_id="sk-****abcd", opener=op)
        r = c.get_balance()
        self.assertEqual("invalid_key_id", r["error"]["category"])
        self.assertEqual(0, len(op.calls))

    def test_usage_requires_key_id_and_dates(self):
        op = FakeOpener()
        c = client(op)
        self.assertEqual("missing_date_range",
                         c.list_usage_records("2026-11-01", None)["error"]["category"])
        self.assertEqual(0, len(op.calls))
        self.assertTrue(c.list_usage_records("2026-11-01", "2026-11-13")["ok"])
        url = op.calls[0].full_url
        self.assertIn("start_date=2026-11-01", url)
        self.assertIn("end_date=2026-11-13", url)

    def test_usage_record_requires_one_id_not_pagination(self):
        op = FakeOpener()
        c = client(op)
        self.assertEqual("missing_record_id", c.get_usage_record()["error"]["category"])
        self.assertEqual(0, len(op.calls))
        self.assertTrue(c.get_usage_record(request_id="req-123")["ok"])
        self.assertIn("request_id=req-123", op.calls[0].full_url)
        self.assertNotIn("limit=", op.calls[0].full_url)

    def test_estimate_blocks_chat_and_sends_full_envelope(self):
        op = FakeOpener()
        c = client(op, catalog={"models": {"glm-4.7": {"kind": "chat"},
                                           "image-01": {"kind": "image"}}})
        self.assertEqual("unsupported", c.estimate_cost("glm-4.7", "chat", {})["error"]["category"])
        self.assertEqual(0, len(op.calls))
        self.assertTrue(c.estimate_cost("image-01", "image-generation", {"n": 1})["ok"])
        body = json.loads(op.calls[0].data.decode("utf-8"))
        self.assertEqual({"model", "endpoint", "key_id", "request"}, set(body))
        self.assertEqual(123456, body["key_id"])
        self.assertEqual("POST", op.calls[0].get_method())

    def test_key_info_post_with_v1_and_numeric_body(self):
        op = FakeOpener()
        client(op).get_key_status()
        req = op.calls[0]
        self.assertEqual("POST", req.get_method())
        self.assertIn("/v1/key/info", req.full_url)
        self.assertEqual(123456, json.loads(req.data.decode("utf-8"))["key_id"])

    def test_key_models_uses_generation_bearer(self):
        op = FakeOpener()
        client(op).get_key_models("sk-model-key")
        req = op.calls[0]
        self.assertTrue(req.full_url.startswith(account_client.GENERATION_BASE_DEFAULT))
        self.assertEqual("Bearer sk-model-key", req.headers["Authorization"])

    def test_from_env_and_camel_aliases(self):
        c = AccountClient.from_env({"PT_ACCESS_TOKEN": "pt_live_ENV", "PT_KEY_ID": "123456"},
                                   opener=FakeOpener())
        self.assertEqual("pt_live_ENV", c.token)
        self.assertEqual(123456, c.numeric_key_id)
        self.assertTrue(c.getBalance()["ok"])

    def test_no_token_leaks_in_results(self):
        c = client(FakeOpener(payload={"code": 500, "msg": "token not found"}))
        r = c.get_balance()
        self.assertNotIn("pt_live_TEST_ONLY", json.dumps(r, ensure_ascii=False))


class TestCompatLayer(unittest.TestCase):
    def test_account_query_delegates_and_returns_raw(self):
        from harness import pt_gateway
        fake = type("FakeClient", (), {
            "token": None,
            "get_balance": lambda self: {"ok": True, "raw": OK_BODY},
            "list_usage_records": lambda self, s, e: {"ok": True, "raw": OK_BODY},
            "get_usage_record": lambda self, **kw: {"ok": True, "raw": OK_BODY},
            "get_agreement_status": lambda self: {"ok": True, "raw": OK_BODY},
        })()
        saved = account_client.AccountClient.from_env
        account_client.AccountClient.from_env = classmethod(lambda cls, **kw: fake)
        try:
            self.assertEqual(OK_BODY, pt_gateway.account_query("balance"))
            self.assertEqual(OK_BODY, pt_gateway.account_query("agreement"))
            self.assertEqual(OK_BODY, pt_gateway.account_query("logs", record_id="r1"))
        finally:
            account_client.AccountClient.from_env = saved

    def test_account_query_raises_on_business_error(self):
        from harness import pt_gateway
        bad = {"ok": False, "error": {"category": "credentials_required",
                                      "http_status": None, "message": "need token"}}
        fake = type("FakeClient", (), {
            "token": None, "get_balance": lambda self: bad,
        })()
        saved = account_client.AccountClient.from_env
        account_client.AccountClient.from_env = classmethod(lambda cls, **kw: fake)
        try:
            with self.assertRaises(pt_gateway.ApiError):
                pt_gateway.account_query("balance")
        finally:
            account_client.AccountClient.from_env = saved


if __name__ == "__main__":
    unittest.main(verbosity=2)
