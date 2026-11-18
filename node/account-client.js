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
 *
 * 0.8.3（源码复核 B1-B3）：
 *   - B1 严格校验：账户域 2xx 响应必须是合法 JSON 对象且带 PT 的 code 字段，
 *     code 表示成功才算成功；否则返回 invalid_response（HTML / 空串 / 截断 JSON 均失败）。
 *     生成域 /v1/models 是 OpenAI 风格 {data:[...]}，只要求合法 JSON 对象。
 *   - B2 不编造：data_as_of / is_cached 只透传服务端返回的统计时间与缓存状态，
 *     PT 没返回时为 null（此前固定填"现在"与 false，属于编造，已改）。
 *   - B3 调用方可传 AbortSignal（与内部超时合并）；响应读取有大小上限
 *     （默认 1 MiB，maxResponseBytes 可配），超限中止并返回 response_too_large。
 *
 * 0.8.4（边界情况 D1）：
 *   - D1 账户域只有 code 明确是成功值（200/'200'/0/'0'）才算成功；code=null/''/'abc'/
 *     缺失时即使 HTTP 200 也返回 invalid_response，绝不按 HTTP 状态兜底当成成功。
 *     生成域 /v1/models 规则不变（无 code 时按 HTTP 判断）。
 *
 * 0.8.6（消费记录查询「未查到」二级判定，仅 getUsageRecord）：
 *   - PT 2026-11-18 优化：不存在的 ID 由「code=200 + 空壳记录」改为
 *     「HTTP 200 + body code=500 + msg='record not found' + data=null」。两种形态都保留，
 *     互不替代（旧形态在旧服务端 / 历史数据里仍存在）。
 *   - getUsageRecord 在 normalizeResult 之后做二级判定，输出结构化 found/category：
 *       found:true  有效记录（含金额零但标识/状态完整的真实记录）
 *       found:false 本次未查到有效记录 —— record_not_found（明确）或 empty_record（200 空壳）
 *       found:null  未判定（token 无效 / 参数错 / 文案不匹配 / 网络等），保持原分类
 *   - 对账方只读 found/category，不解析 PT 的 msg；不匹配的失败绝不归为「未查到」。
 *   - found:false 只表示本次未查到有效记录，不代表未扣费 / 最终无记录，更不能据此重发
 *     生成请求（重发由 retryDecision 控制，与此处无关）。
 */

const ACCOUNT_BASE_DEFAULT = 'https://powertokens.ai/api';
const GENERATION_BASE_DEFAULT = 'https://api.powertokens.ai';
const MAX_RESPONSE_BYTES_DEFAULT = 1048576; // 1 MiB

/** 服务端统计时间 / 缓存状态候选字段。只原样透出，绝不编造。 */
const SERVER_TIME_KEYS = ['data_as_of', 'as_of', 'updated_at', 'server_time', 'timestamp', 'generated_at'];
const SERVER_CACHE_KEYS = ['is_cached', 'cached'];

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

/** B2：从响应体（顶层与 data 子对象）里找服务端统计时间与缓存状态；找不到为 null。 */
function serverMeta(body) {
  let time = null;
  let cached = null;
  const sources = [];
  if (body && typeof body === 'object' && !Array.isArray(body)) {
    sources.push(body);
    const d = body.data;
    if (d && typeof d === 'object' && !Array.isArray(d)) sources.push(d);
  }
  for (const src of sources) {
    if (time === null) {
      for (const k of SERVER_TIME_KEYS) {
        if (src[k] !== undefined && src[k] !== null) { time = src[k]; break; }
      }
    }
    if (cached === null) {
      for (const k of SERVER_CACHE_KEYS) {
        if (src[k] !== undefined && src[k] !== null) { cached = src[k]; break; }
      }
    }
  }
  return { data_as_of: time, is_cached: cached };
}

/** 统一失败结果（不编造 data_as_of / is_cached）。 */
function failure(category, message, httpStatus = null) {
  return {
    ok: false, code: null, data: null,
    error: { category, http_status: httpStatus, message: String(message).slice(0, 300) },
    http_status: httpStatus, data_as_of: null, is_cached: null,
  };
}

/** 明确成功 code 白名单：账户域唯一可信的成功依据。 */
function isSuccessCode(code) {
  return code === 200 || code === '200' || code === 0 || code === '0';
}

/**
 * 0.8.6：PT 实测的「明确未找到」文案（2026-11-18 两组凭证实测：record not found）。
 * 只在 getUsageRecord 的二级判定里用；仅匹配 msg，绝不用于其他接口。
 * 放宽为大小写不敏感 + 允许首尾空格，但必须是这条短语，避免把 "token not found" 等误判。
 */
const RECORD_NOT_FOUND_MSG_RE = /^record\s+not\s+found$/i;

/** 消费记录里代表「有效记录」的字段（标识 / 模型 / 状态）。全空即 200 空壳占位。 */
const RECORD_IDENTITY_KEYS = ['request_id', 'task_id', 'consume_id', 'id', 'model', 'model_name', 'status'];

function isBlankValue(v) {
  return v === null || v === undefined || v === '' || (typeof v === 'string' && v.trim() === '');
}

/** 是否「200 空壳记录」：data 非对象，或标识/模型/状态全为空。金额零但有标识的记录不算空壳。 */
function isEmptyRecordData(data) {
  if (data === null || data === undefined) return true;
  if (typeof data !== 'object' || Array.isArray(data)) return false;
  const keys = Object.keys(data);
  if (!keys.length) return true;
  return RECORD_IDENTITY_KEYS.every(k => isBlankValue(data[k]));
}

/**
 * 0.8.6 二级判定：只在 getUsageRecord 里调用，把 logs/query 的两种「没查到」和真实记录区分开。
 * 输入是 normalizeResult 的结果。只读结构化字段，对账方无需解析 PT 的 msg。
 * found 三态：true=有效记录 / false=本次未查到有效记录 / null=未判定（错误与没查到无关）。
 */
function interpretUsageRecord(res) {
  if (!res || typeof res !== 'object') return res;

  // 分支 1：明确未找到 —— code=500 + data=null + msg 实测文案（PT 2026-11-18 优化后的形态）
  if (!res.ok && res.code === 500 && res.data === null) {
    const msg = (res.error && res.error.message) || '';
    if (RECORD_NOT_FOUND_MSG_RE.test(String(msg).trim())) {
      return { ...res, found: false, error: { ...res.error, category: 'record_not_found' } };
    }
  }

  // 分支 2：200 空壳记录 —— 旧形态，PT 未改造前的历史响应；标识/模型/状态全空，不能当零扣费
  if (res.ok && isEmptyRecordData(res.data)) {
    return {
      ...res,
      ok: false,
      found: false,
      data: null,
      code: res.code,
      error: {
        category: 'empty_record',
        http_status: res.http_status,
        message: 'code=200 但记录标识/模型/状态全空，判定为无有效记录（不能作为零扣费依据）',
      },
    };
  }

  // 分支 3：有效记录（含金额零但标识/状态完整）
  if (res.ok) return { ...res, found: true };

  // 分支 4：其他失败（无效 token / 参数错 / 文案不匹配 / 网络等）—— 保持原分类，未判定
  return { ...res, found: null };
}

/** code 形态是否合法：非 null / undefined / 空串，且能转成有限数字。畸形 code 无法判定业务结果。 */
function isValidCodeShape(code) {
  if (code === null || code === undefined || code === '') return false;
  return Number.isFinite(Number(code));
}

/**
 * 把 PT 的 {code,data,msg} 统一成一种结果。
 * 0.8.4 D1：账户域（strictCode=true，默认）只有 code 明确是成功值才算成功；
 *   code 为 null / '' / 'abc' / 缺失一律失败并归 invalid_response，绝不退回 HTTP 200。
 *   明确的业务失败码（如 500 / '500'）仍归 business_error（保持既有语义）。
 *   生成域 /v1/models（strictCode=false）规则不变：无 code 时退回 HTTP 判断。
 */
function normalizeResult(raw, httpStatus, extras = {}, options = {}) {
  const body = (raw && typeof raw === 'object') ? raw : {};
  const innerErr = (body.error && typeof body.error === 'object') ? body.error : {};
  const code = body.code !== undefined ? body.code : (innerErr.code !== undefined ? innerErr.code : null);
  const message = body.msg || body.message || innerErr.message || '';
  const numericCode = Number(code);
  const codeOk = isSuccessCode(code);
  const httpOk = httpStatus >= 200 && httpStatus < 300;
  const strictCode = options.strictCode !== false;

  let ok;
  let category;
  if (strictCode) {
    // 账户域：code 是唯一权威，明文成功值才算成功，不用 HTTP 兜底。
    ok = codeOk;
    if (!ok) {
      category = isValidCodeShape(code)
        ? (classifyHttpStatus(httpStatus) || 'business_error')  // 明确的业务失败码
        : 'invalid_response';                                    // null / '' / 'abc' / 缺失
    }
  } else {
    // 生成域：规则不变 —— 有 code 以 code 为准，无 code 退回 HTTP。
    ok = code !== null && code !== undefined ? codeOk : httpOk;
    category = ok ? null : (classifyHttpStatus(httpStatus) || 'business_error');
  }
  const meta = serverMeta(body);
  return {
    ok,
    code,
    data: ok ? (body.data !== undefined ? body.data : body) : null,
    error: ok ? null : {
      category,
      // 账户域 HTTP 200 + code=500 也要能看出来不是成功
      http_status: httpStatus,
      message: String(message || (category === 'invalid_response'
        ? '响应 code 非法或缺失，无法判定业务结果'
        : (numericCode ? `code=${code}` : 'request failed'))),
    },
    http_status: httpStatus,
    // B2：只透传服务端值，没有就 null，不编造
    data_as_of: meta.data_as_of,
    is_cached: meta.is_cached,
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
   * @param {number} [cfg.maxResponseBytes] 响应大小上限（字节），默认 1 MiB（B3）
   * @param {AbortSignal} [cfg.signal] 客户端级取消信号（B3，与内部超时合并）
   * @param {Function} [cfg.fetchImpl] 可注入，便于离线测试
   * @param {object} [cfg.catalog]     可选，用于拦截 chat 模型的 estimate
   */
  constructor(cfg = {}) {
    this.token = cfg.token || null;
    this.keyId = cfg.keyId === undefined || cfg.keyId === null ? null : cfg.keyId;
    this.baseUrl = (cfg.baseUrl || ACCOUNT_BASE_DEFAULT).replace(/\/$/, '');
    this.generationBaseUrl = (cfg.generationBaseUrl || GENERATION_BASE_DEFAULT).replace(/\/$/, '');
    this.timeout = Number(cfg.timeout || 30000);
    this.maxResponseBytes = Number(cfg.maxResponseBytes || MAX_RESPONSE_BYTES_DEFAULT);
    this.signal = cfg.signal || null;
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
    return failure('credentials_required',
      '账户接口需要 pt_live_ 个人访问令牌（不是模型 API Key）；'
      + '构造 AccountClient 时传 token/keyId，或设置 PT_ACCESS_TOKEN / PT_KEY_ID。');
  }

  _keyIdError() {
    return failure('invalid_key_id',
      'key_id 必填且必须是数字；PT 没有「用 API Key 反查 key_id」的接口，需调用方提供。');
  }

  /**
   * B3：读取响应体，带大小上限。超限返回 null（调用方据此报 response_too_large）。
   * 优先用 content-length 预检；有可读流就边读边限；否则读完再查长度。
   */
  async _readBody(resp) {
    const cap = this.maxResponseBytes;
    const headers = resp && resp.headers;
    if (headers && typeof headers.get === 'function') {
      const cl = Number(headers.get('content-length'));
      if (Number.isFinite(cl) && cl > cap) return null;
    }
    if (resp && resp.body && typeof resp.body.getReader === 'function') {
      const reader = resp.body.getReader();
      const chunks = [];
      let total = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        const size = value ? value.length : 0;
        total += size;
        if (total > cap) {
          try { reader.cancel(); } catch (_) { /* 已取消 */ }
          return null;
        }
        chunks.push(value);
      }
      if (!chunks.length) return '';
      return Buffer.concat(chunks).toString('utf-8');
    }
    const text = await resp.text();
    if (typeof text === 'string' && text.length > cap) return null;
    return text;
  }

  async _request({ method = 'GET', path, params = null, body = null, auth = 'account', apiKey = null, signal = null }) {
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

    // B3：外部取消信号与内部超时合并。外部已取消时直接返回，不发请求。
    const external = signal || this.signal || null;
    if (external && external.aborted) {
      return failure('aborted', '调用方在请求前已取消（AbortSignal aborted）。');
    }
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, this.timeout);
    let onAbort = null;
    if (external) {
      onAbort = () => controller.abort();
      external.addEventListener('abort', onAbort, { once: true });
    }
    try {
      const resp = await this.fetchImpl(url, {
        method,
        headers,
        body: body === null ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      const text = await this._readBody(resp);
      if (text === null) {
        return failure('response_too_large',
          `响应超过大小上限 ${this.maxResponseBytes} 字节，已中止读取。`, resp.status);
      }
      // B1：必须是合法 JSON 对象。
      let parsed;
      try {
        parsed = text ? JSON.parse(text) : undefined;
      } catch (_) {
        parsed = undefined;
      }
      if (parsed === undefined || parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        const preview = typeof text === 'string' ? text.slice(0, 120).replace(/\s+/g, ' ') : '(empty)';
        return failure('invalid_response',
          `HTTP ${resp.status} 响应不是合法 JSON 对象（可能是 HTML / 空串 / 截断 JSON）：${preview}`, resp.status);
      }
      // B1：账户域还必须有 PT 的 code 字段，以 code 判成功。
      // D1：这里同时挡住 code=null / '' / 'abc' 等畸形值 —— 它们无法判定业务结果。
      if (!isGeneration && !('code' in parsed)) {
        return failure('invalid_response',
          `HTTP ${resp.status} 响应是 JSON 但缺少 PT code 字段，无法判定业务结果。`, resp.status);
      }
      return normalizeResult(parsed, resp.status, {}, { strictCode: !isGeneration });
    } catch (e) {
      const isAbort = e && e.name === 'AbortError';
      const category = isAbort
        ? (timedOut ? 'timeout' : (external && external.aborted ? 'aborted' : 'timeout'))
        : 'network_error';
      return failure(category,
        // 绝不带出令牌：只保留错误类型与消息，且消息不包含 Authorization 头
        (isAbort ? (category === 'aborted' ? '调用方取消了请求。' : '请求超时。') : String((e && e.message) || 'request failed')));
    } finally {
      clearTimeout(timer);
      if (external && onAbort) {
        try { external.removeEventListener('abort', onAbort); } catch (_) { /* noop */ }
      }
    }
  }

  /** POST /v1/key/info —— 路径必须带 /v1，GET 或不带都是 404。 */
  async getKeyStatus(keyId, opts = {}) {
    const pre = this._precheck(); if (pre) return pre;
    const kid = keyId === undefined ? this.keyId : keyId;
    const n = toNumericKeyId(kid);
    if (n === null) return this._keyIdError();
    return this._request({ method: 'POST', path: '/v1/key/info', body: { key_id: n }, signal: opts.signal });
  }

  /** GET /v1/billing/balance?key_id=<数字> —— key_id 必填。金额保留 credits。 */
  async getBalance(opts = {}) {
    const pre = this._precheck(); if (pre) return pre;
    const n = this._numericKeyId;
    if (n === null) return this._keyIdError();
    return this._request({ path: '/v1/billing/balance', params: { key_id: n }, signal: opts.signal });
  }

  /** GET /v1/billing/usage?key_id=&start_date=&end_date= —— 三者都必填。 */
  async listUsageRecords({ startDate, endDate, keyId, signal } = {}) {
    const pre = this._precheck(); if (pre) return pre;
    const n = keyId === undefined ? this._numericKeyId : toNumericKeyId(keyId);
    if (n === null) return this._keyIdError();
    if (!startDate || !endDate) {
      return failure('missing_date_range', 'start_date 与 end_date 都必填（YYYY-MM-DD）。');
    }
    return this._request({
      path: '/v1/billing/usage',
      params: { key_id: n, start_date: startDate, end_date: endDate },
      signal,
    });
  }

  /**
   * GET /v1/logs/query —— 只能按 ID 精确查单条，不是分页列表。
   * 目前没有「按 Key 或时间列出」的接口（SSOT §12.5 / 需求 §5 待确认项）。
   *
   * 0.8.6：返回结构化的 found / error.category（见文件头与 interpretUsageRecord）：
   *   found:true            有效记录（ok:true；金额零但标识/状态完整仍是有效记录）
   *   found:false, ok:false 本次未查到有效记录 —— category='record_not_found'（PT 优化后的
   *                         500+record not found）或 'empty_record'（200+空壳记录）
   *   found:null,  ok:false 未判定：无效 token / 参数错 / 文案不匹配 / 网络错误，保持原分类
   * 注意：found:false 不代表未扣费或最终无记录，更不可据此重发生成请求；对账只读结构化字段。
   */
  async getUsageRecord({ requestId, taskId, consumeId, signal } = {}) {
    const pre = this._precheck(); if (pre) return pre;
    const params = {};
    if (requestId) params.request_id = requestId;
    if (taskId) params.task_id = taskId;
    if (consumeId) params.consume_id = consumeId;
    if (!Object.keys(params).length) {
      return failure('missing_record_id',
        'request_id / task_id / consume_id 必须带一个；该端点不支持列表查询。');
    }
    const res = await this._request({ path: '/v1/logs/query', params, signal });
    return interpretUsageRecord(res);
  }

  /** POST /v1/billing/estimate —— 不扣费（实测 80→80），但不支持对话模型。 */
  async estimateCost({ model, endpoint, request, keyId, kind, signal } = {}) {
    const pre = this._precheck(); if (pre) return pre;
    const resolvedKind = kind || (this.catalog && this.catalog.models &&
      this.catalog.models[model] && this.catalog.models[model].kind) || null;
    if (resolvedKind === 'chat') {
      return failure('unsupported',
        '费用预估不支持对话模型（PT 实测口径）；对话费用以实际扣费为准。');
    }
    const n = keyId === undefined ? this._numericKeyId : toNumericKeyId(keyId);
    if (n === null) return this._keyIdError();
    return this._request({
      method: 'POST', path: '/v1/billing/estimate',
      body: { model, endpoint, key_id: n, request: request || {} },
      signal,
    });
  }

  /** GET /v1/asset/agreement/status */
  async getAgreementStatus(opts = {}) {
    const pre = this._precheck(); if (pre) return pre;
    return this._request({ path: '/v1/asset/agreement/status', signal: opts.signal });
  }

  /**
   * 生成域：GET /v1/models —— 用模型 API Key 的 Bearer 鉴权。
   * 返回的是「本 Key / 分组可见视图」，不是平台全量清单（F1 已作废）。
   * 响应是 OpenAI 风格 JSON 对象，不要求 PT code 字段（B1 只对账户域严格）。
   */
  async getKeyModels(apiKey, opts = {}) {
    return this._request({
      path: '/v1/models', auth: 'generation', apiKey, signal: opts.signal,
    });
  }
}

module.exports = {
  AccountClient, normalizeResult, classifyHttpStatus, toNumericKeyId, serverMeta,
  interpretUsageRecord, isEmptyRecordData,
  ACCOUNT_BASE_DEFAULT, GENERATION_BASE_DEFAULT, MAX_RESPONSE_BYTES_DEFAULT,
};
