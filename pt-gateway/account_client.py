"""账户 / 计费只读客户端（Python 版，与 node/account-client.js 功能一致）。

2026-11-13 真机实测口径（SSOT §12 / §13）：
  - 账户域 https://powertokens.ai/api：Authorization 用裸 pt_live_ 令牌，**不带 Bearer**
  - 生成域 https://api.powertokens.ai：Bearer <API Key>，两套凭证不可混用
  - 业务成功以响应体 code 为准 —— 无效凭证会返回 HTTP 200 + code=500
  - key_id 请求侧必须是数字；响应里的 key_id 目前仍可能是脱敏串
  - balance / usage 必须带 key_id，usage 还要 start_date + end_date
  - logs/query 必须带 request_id / task_id / consume_id 之一，返回单条而非列表
  - estimate 不扣费（实测 80→80），但不支持对话模型

设计约定（需求 §5）：
  - 不保存任何凭证：每次由使用方传入，或由 from_env() 读 PT_ACCESS_TOKEN / PT_KEY_ID
  - 统一输出 {ok, code, data, error, http_status, data_as_of, is_cached, raw}
  - 金额保留 credits，不换算 USD；命名不写死 PT，附 camelCase 别名对齐 Node 版
  - 不写日志里的令牌；异常信息只带错误类型与消息
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ACCOUNT_BASE_DEFAULT = "https://powertokens.ai/api"
GENERATION_BASE_DEFAULT = "https://api.powertokens.ai"


def classify_http_status(status):
    """401/403/404/429 与 5xx 分别给出明确错误类别。"""
    if status == 401:
        return "auth_invalid"
    if status == 403:
        return "forbidden"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limited"
    if status >= 500:
        return "upstream_error"
    if status >= 400:
        return "bad_request"
    return None


def to_numeric_key_id(value):
    """key_id 请求侧必须是数字。注意 Number(null)==0 的坑，显式挡掉空值与脱敏串。"""
    if value is None:
        return None
    s = str(value).strip()
    if not re.fullmatch(r"\d+", s):
        return None
    return int(s)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _result(ok, code, data, error, http_status, raw=None):
    return {
        "ok": ok,
        "code": code,
        "data": data,
        "error": error,
        "http_status": http_status,
        "data_as_of": _now(),
        "is_cached": False,
        "raw": raw,
    }


def _failure(category, message, http_status=None):
    return _result(False, None, None,
                   {"category": category, "http_status": http_status, "message": message},
                   http_status)


def normalize_result(raw, http_status):
    """把 PT 的 {code,data,msg} 统一成一种结果。HTTP 与 code 冲突时以 code 为准。"""
    body = raw if isinstance(raw, dict) else {}
    inner = body.get("error") if isinstance(body.get("error"), dict) else {}
    code = body.get("code", inner.get("code"))
    message = body.get("msg") or body.get("message") or inner.get("message") or ""
    code_ok = code in (200, "200", 0, "0")
    http_ok = 200 <= (http_status or 0) < 300
    ok = code_ok if code is not None else http_ok
    if ok:
        return _result(True, code, body.get("data", body), None, http_status, body)
    category = classify_http_status(http_status or 0) or "business_error"
    return _result(False, code, None,
                   {"category": category, "http_status": http_status,
                    "message": str(message) or ("code=%s" % code)},
                   http_status, body)


class AccountClient:
    """账户只读客户端。不发模型生成请求，不做客户端权限拦截。"""

    def __init__(self, token=None, key_id=None, base_url=None,
                 generation_base_url=None, timeout=30, opener=None, catalog=None):
        self.token = token
        self.key_id = key_id
        self.base_url = (base_url or ACCOUNT_BASE_DEFAULT).rstrip("/")
        self.generation_base_url = (generation_base_url or GENERATION_BASE_DEFAULT).rstrip("/")
        self.timeout = timeout
        self.opener = opener or urllib.request.build_opener()
        self.catalog = catalog

    @classmethod
    def from_env(cls, env=None, **kwargs):
        env = env if env is not None else os.environ
        return cls(
            token=kwargs.pop("token", None) or env.get("PT_ACCESS_TOKEN"),
            key_id=kwargs.pop("key_id", None) or env.get("PT_KEY_ID"),
            **kwargs,
        )

    @property
    def numeric_key_id(self):
        return to_numeric_key_id(self.key_id)

    def _precheck(self):
        if not self.token:
            return _failure(
                "credentials_required",
                "账户接口需要 pt_live_ 个人访问令牌（不是模型 API Key）；"
                "传 token/key_id 或设置 PT_ACCESS_TOKEN / PT_KEY_ID。",
            )
        return None

    @staticmethod
    def _key_id_error():
        return _failure(
            "invalid_key_id",
            "key_id 必填且必须是数字；PT 没有「用 API Key 反查 key_id」的接口，需调用方提供。",
        )

    def _request(self, method="GET", path="", params=None, body=None,
                 auth="account", api_key=None):
        pre = self._precheck() if auth == "account" else None
        if pre:
            return pre
        is_generation = auth == "generation"
        base = self.generation_base_url if is_generation else self.base_url
        url = base + path
        if params:
            clean = {k: v for k, v in params.items() if v not in (None, "")}
            if clean:
                url += ("&" if "?" in url else "?") + urllib.parse.urlencode(clean)
        headers = {"Accept": "application/json", "User-Agent": "powertokens-kit/account-client"}
        if is_generation:
            headers["Authorization"] = "Bearer " + (api_key or "")
        else:
            headers["Authorization"] = self.token          # 裸值，绝不加 Bearer
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                status = resp.status
                text = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                text = e.read().decode("utf-8", errors="replace")
            except Exception:
                text = ""
        except Exception as e:                                # noqa: BLE001 - 统一成结构化结果
            return _failure("network_error", "%s: %s" % (type(e).__name__, str(e)[:300]))
        try:
            parsed = json.loads(text) if text else {}
        except ValueError:
            parsed = {"message": text}
        return normalize_result(parsed, status)

    # ---- Key 信息 ----
    def get_key_status(self, key_id=None):
        pre = self._precheck()
        if pre:
            return pre
        n = to_numeric_key_id(self.key_id if key_id is None else key_id)
        if n is None:
            return self._key_id_error()
        return self._request("POST", "/v1/key/info", body={"key_id": n})

    # ---- 余额 ----
    def get_balance(self):
        pre = self._precheck()
        if pre:
            return pre
        n = self.numeric_key_id
        if n is None:
            return self._key_id_error()
        return self._request("GET", "/v1/billing/balance", params={"key_id": n})

    # ---- 按模型汇总的账单 ----
    def list_usage_records(self, start_date=None, end_date=None, key_id=None):
        pre = self._precheck()
        if pre:
            return pre
        n = self.numeric_key_id if key_id is None else to_numeric_key_id(key_id)
        if n is None:
            return self._key_id_error()
        if not start_date or not end_date:
            return _failure("missing_date_range", "start_date 与 end_date 都必填（YYYY-MM-DD）。")
        return self._request("GET", "/v1/billing/usage",
                             params={"key_id": n, "start_date": start_date, "end_date": end_date})

    # ---- 消费记录：只能按 ID 精确查单条 ----
    def get_usage_record(self, request_id=None, task_id=None, consume_id=None):
        pre = self._precheck()
        if pre:
            return pre
        params = {}
        if request_id:
            params["request_id"] = request_id
        if task_id:
            params["task_id"] = task_id
        if consume_id:
            params["consume_id"] = consume_id
        if not params:
            return _failure("missing_record_id",
                            "request_id / task_id / consume_id 必须带一个；该端点不支持列表查询。")
        return self._request("GET", "/v1/logs/query", params=params)

    # ---- 费用预估：不扣费，但不支持对话模型 ----
    def estimate_cost(self, model=None, endpoint=None, request=None, key_id=None, kind=None):
        pre = self._precheck()
        if pre:
            return pre
        resolved_kind = kind
        if resolved_kind is None and self.catalog:
            entry = (self.catalog.get("models") or {}).get(model) or {}
            resolved_kind = entry.get("kind")
        if resolved_kind == "chat":
            return _failure("unsupported",
                            "费用预估不支持对话模型（PT 实测口径）；对话费用以实际扣费为准。")
        n = self.numeric_key_id if key_id is None else to_numeric_key_id(key_id)
        if n is None:
            return self._key_id_error()
        return self._request("POST", "/v1/billing/estimate",
                             body={"model": model, "endpoint": endpoint,
                                   "key_id": n, "request": request or {}})

    # ---- 资产库协议状态 ----
    def get_agreement_status(self):
        return self._request("GET", "/v1/asset/agreement/status")

    # ---- 生成域：按 Key/分组可见模型视图 ----
    def get_key_models(self, api_key=None):
        return self._request("GET", "/v1/models", auth="generation", api_key=api_key)

    # camelCase 别名：与 node/account-client.js 同名，方便 DSH 侧对照迁移
    getBalance = get_balance
    getKeyStatus = get_key_status
    listUsageRecords = list_usage_records
    getUsageRecord = get_usage_record
    estimateCost = estimate_cost
    getAgreementStatus = get_agreement_status
    getKeyModels = get_key_models
