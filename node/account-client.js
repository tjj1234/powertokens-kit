'use strict';

/**
 * 账户 / 计费客户端（Node.js，零依赖）。
 *
 * 与 node/index.js 的分工：那边只读 catalog 出规则、不发请求；这里只管账户域
 * 只读接口，也不发模型生成请求。两侧都不保存凭证。
 *
 * 2026-11-13 真机实测口径（SSOT §12 / §13）：
 *   - 账户域 https://powertokens.ai/api，Authorization 用裸 pt_live_ 令牌，不带 Bearer
 *   - 生成域 https://api.powertokens.ai 用 Bearer <API Key>，两套凭证不可混用
 *   - balance / usage 的 key_id 必填且必须是数字；响应里的 key_id 目前可能是脱敏串
 *   - logs/query 必须带 request_id / task_id / consume_id 三者之一，返回单条而非列表
 *   - estimate 不扣费，但不支持对话模型
 *   - 业务判断以响应体 code 为准（无效凭证会返回 HTTP 200 + code=500）
 */

const ACCOUNT_BASE_DEFAULT = 'https://powertokens.ai/api';
const GENERATION_BASE_DEFAULT = 'https://api.powertokens.ai';

function classifyHttpStatus(status) {
  if (status === 401) return 'auth_invalid';
  if (status === 403) return 'forbidden';
  if (status === 404) return 'not_found';
  if (status === 429) return 'rate_limited';
  if (status >= 500) return 'upstream_error';
  if (status >= 400) return 'bad_request';
  return null;
}

/** key_id 请求侧必须是数字。注意 Number(null)===0，必须显式挡掉空值与脱敏串。 */
function toNumericKeyId(value) {
  if (value === null || value === undefined) return null;
  const s = String(value).trim();
  if (!/^\d+$/.test(s)) return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

/** 把 PT 的 {code,data,msg} 统一成一种结果。HTTP 与 code 冲突时以 code 为准。 */
function normalizeResult(raw, httpStatus, extras = {}) {
  const body = (raw && typeof raw === 'object') ? raw : {};
  const innerErr = (body.error && typeof body.error === 'object') ? body.error : {};
  const code = body.code !== undefined ? body.code : (innerErr.code !== undefined ? innerErr.code : null);
  const message = body.msg || body.message || innerErr.message || '';
  const numericCode = Number(code);
  const codeOk = code === 200 || code === '200' || (code === 0) || (code === '0');
  const httpOk = httpStatus >= 200 && httpStatus < 300;
  // code 存在时它是权威；code 缺失才退回 HTTP 判断。
  const ok = code !== null && code !== undefined ? codeOk : httpOk;
  const category = ok ? null : (classifyHttpStatus(httpStatus) || 'business_error');
  return {
    ok,
    code,
    data: ok ? (body.data !== undefined ? body.data : body) : null,
    error: ok ? null : {
      category,
      // 账户域 HTTP 200 + code=500 也要能看出来不是成功
      http_status: httpStatus,
      message: String(message || (numericCode ? `code=${code}` : 'request failed')),
    },
    http_status: httpStatus,
    data_as_of: new Date().toISOString(),
    is_cached: false,
    ...extras,
  };
}

class AccountClient {
  /**
   * @param {object} cfg
   * @param {string} cfg.token          个人访问令牌 pt_live_...（裸值使用，不带 Bearer）
   * @param {number|string} cfg.keyId  数字 key_id（请求侧必须数字）
   * @param {string} [cfg.baseUrl]
   * @param {string} [cfg.generationBaseUrl]
   * @param {number} [cfg.timeout]     ms
   * @param {Function} [cfg.fetchImpl] 可注入，便于离线测试
   * @param {object} [cfg.catalog]     可选，用于拦截 chat 模型的 estimate
   */
  constructor(cfg = {}) {
    this.token = cfg.token || null;
    this.keyId = cfg.keyId === undefined || cfg.keyId === null ? null : cfg.keyId;
    this.baseUrl = (cfg.baseUrl || ACCOUNT_BASE_DEFAULT).replace(/\/$/, '');
    this.generationBaseUrl = (cfg.generationBaseUrl || GENERATION_BASE_DEFAULT).replace(/\/$/, '');
    this.timeout = Number(cfg.timeout || 30000);
    this.fetchImpl = cfg.fetchImpl || globalThis.fetch;
    this.catalog = cfg.catalog || null;
  }

  /** 便捷构造：从环境变量取凭证，不读配置文件、不写日志。 */
  static fromEnv(env = process.env, cfg = {}) {
    return new AccountClient({
      ...cfg,
      token: cfg.token || env.PT_ACCESS_TOKEN || null,
      keyId: cfg.keyId === undefined ? (env.PT_KEY_ID || null) : cfg.keyId,
    });
  }

  get _hasCredentials() { return Boolean(this.token); }
  get _numericKeyId() { return toNumericKeyId(this.keyId); }

  _precheck() {
    return this._hasCredentials ? null : this._credentialError();
  }

  _credentialError() {
    return {
      ok: false, code: null, data: null,
      error: {
        category: 'credentials_required', http_status: null,
        message: '账户接口需要 pt_live_ 个人访问令牌（不是模型 API Key）；'
          + '构造 AccountClient 时传 token/keyId，或设置 PT_ACCESS_TOKEN / PT_KEY_ID。',
      },
      http_status: null, data_as_of: new Date().toISOString(), is_cached: false,
    };
  }

  _keyIdError() {
    return {
      ok: false, code: null, data: null,
      error: {
        category: 'invalid_key_id', http_status: null,
        message: 'key_id 必填且必须是数字；PT 没有「用 API Key 反查 key_id」的接口，需调用方提供。',
      },
      http_status: null, data_as_of: new Date().toISOString(), is_cached: false,
    };
  }

  async _request({ method = 'GET', path, params = null, body = null, auth = 'account', apiKey = null }) {
    if (auth === 'account' && !this._hasCredentials) return this._credentialError();
    const isGeneration = auth === 'generation';
    const base = isGeneration ? this.generationBaseUrl : this.baseUrl;
    let url = base + path;
    if (params && Object.keys(params).length) {
      const q = new URLSearchParams();
      for (const [k, v] of Object.entries(params)) {
        if (v !== undefined && v !== null && v !== '') q.set(k, String(v));
      }
      url += (url.includes('?') ? '&' : '?') + q.toString();
    }
    const headers = { Accept: 'application/json' };
    if (isGeneration) headers.Authorization = 'Bearer ' + (apiKey || '');
    else headers.Authorization = this.token;      // 裸值，绝不加 Bearer
    if (body !== null) headers['Content-Type'] = 'application/json';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);
    try {
      const resp = await this.fetchImpl(url, {
        method,
        headers,
        body: body === null ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      const text = await resp.text();
      let parsed = {};
      try { parsed = text ? JSON.parse(text) : {}; } catch (_) { parsed = { message: text }; }
      return normalizeResult(parsed, resp.status);
    } catch (e) {
      return {
        ok: false, code: null, data: null,
        error: {
          category: e && e.name === 'AbortError' ? 'timeout' : 'network_error',
          http_status: null,
          // 绝不带出令牌：只保留错误类型与消息，且消息不包含 Authorization 头
          message: String((e && e.message) || 'request failed').slice(0, 300),
        },
        http_status: null, data_as_of: new Date().toISOString(), is_cached: false,
      };
    } finally {
      clearTimeout(timer);
    }
  }

  /** POST /v1/key/info —— 路径必须带 /v1，GET 或不带都是 404。 */
  async getKeyStatus(keyId) {
    const pre = this._precheck(); if (pre) return pre;
    const kid = keyId === undefined ? this.keyId : keyId;
    const n = toNumericKeyId(kid);
    if (n === null) return this._keyIdError();
    return this._request({ method: 'POST', path: '/v1/key/info', body: { key_id: n } });
  }

  /** GET /v1/billing/balance?key_id=<数字> —— key_id 必填。金额保留 credits。 */
  async getBalance() {
    const pre = this._precheck(); if (pre) return pre;
    const n = this._numericKeyId;
    if (n === null) return this._keyIdError();
    return this._request({ path: '/v1/billing/balance', params: { key_id: n } });
  }

  /** GET /v1/billing/usage?key_id=&start_date=&end_date= —— 三者都必填。 */
  async listUsageRecords({ startDate, endDate, keyId } = {}) {
    const pre = this._precheck(); if (pre) return pre;
    const n = keyId === undefined ? this._numericKeyId : toNumericKeyId(keyId);
    if (n === null) return this._keyIdError();
    if (!startDate || !endDate) {
      return {
        ok: false, code: null, data: null,
        error: { category: 'missing_date_range', http_status: null,
          message: 'start_date 与 end_date 都必填（YYYY-MM-DD）。' },
        http_status: null, data_as_of: new Date().toISOString(), is_cached: false,
      };
    }
    return this._request({
      path: '/v1/billing/usage',
      params: { key_id: n, start_date: startDate, end_date: endDate },
    });
  }

  /**
   * GET /v1/logs/query —— 只能按 ID 精确查单条，不是分页列表。
   * 目前没有「按 Key 或时间列出」的接口（SSOT §12.5 / 需求 §5 待确认项）。
   */
  async getUsageRecord({ requestId, taskId, consumeId } = {}) {
    const pre = this._precheck(); if (pre) return pre;
    const params = {};
    if (requestId) params.request_id = requestId;
    if (taskId) params.task_id = taskId;
    if (consumeId) params.consume_id = consumeId;
    if (!Object.keys(params).length) {
      return {
        ok: false, code: null, data: null,
        error: { category: 'missing_record_id', http_status: null,
          message: 'request_id / task_id / consume_id 必须带一个；该端点不支持列表查询。' },
        http_status: null, data_as_of: new Date().toISOString(), is_cached: false,
      };
    }
    return this._request({ path: '/v1/logs/query', params });
  }

  /** POST /v1/billing/estimate —— 不扣费（实测 80→80），但不支持对话模型。 */
  async estimateCost({ model, endpoint, request, keyId, kind } = {}) {
    const pre = this._precheck(); if (pre) return pre;
    const resolvedKind = kind || (this.catalog && this.catalog.models &&
      this.catalog.models[model] && this.catalog.models[model].kind) || null;
    if (resolvedKind === 'chat') {
      return {
        ok: false, code: null, data: null,
        error: { category: 'unsupported', http_status: null,
          message: '费用预估不支持对话模型（PT 实测口径）；对话费用以实际扣费为准。' },
        http_status: null, data_as_of: new Date().toISOString(), is_cached: false,
      };
    }
    const n = keyId === undefined ? this._numericKeyId : toNumericKeyId(keyId);
    if (n === null) return this._keyIdError();
    return this._request({
      method: 'POST', path: '/v1/billing/estimate',
      body: { model, endpoint, key_id: n, request: request || {} },
    });
  }

  /** GET /v1/asset/agreement/status */
  async getAgreementStatus() {
    const pre = this._precheck(); if (pre) return pre;
    return this._request({ path: '/v1/asset/agreement/status' });
  }

  /**
   * 生成域：GET /v1/models —— 用模型 API Key 的 Bearer 鉴权。
   * 返回的是「本 Key / 分组可见视图」，不是平台全量清单（F1 已作废）。
   */
  async getKeyModels(apiKey) {
    return this._request({
      path: '/v1/models', auth: 'generation', apiKey,
    });
  }
}

module.exports = {
  AccountClient, normalizeResult, classifyHttpStatus, toNumericKeyId,
  ACCOUNT_BASE_DEFAULT, GENERATION_BASE_DEFAULT,
};
