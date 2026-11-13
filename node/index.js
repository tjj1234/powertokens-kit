'use strict';

/**
 * powertokens-kit core (Node.js, zero dependency)
 *
 * 这个包只做「规则」，不发任何网络请求。所有事实都来自同一份
 * pt-gateway/catalog.json —— Python 工具链读的也是这一份。
 *
 *   loadCatalog(options)                     固定版本 + 格式校验，失败抛错
 *   selectCandidates(purpose, keyModels)     AUTO 选模（实现 auto_policy 的 4 条跳过规则）
 *   classifyError(status, body)              错误归类（errors 字典）
 *   retryDecision(input)                     重试 / 换模型 / 停止（retry_policy）
 *   reasoningParams(model, effort)           思考模式传参（models_api_contract）
 *
 * 参考：docs/PT_项目资料总集_20261112.md §7（Agent 侧约定）、
 *       "powertokens-kit catalog 补充需求.md" §0 P0 / §3 需求 3、4 / §7。
 */

const fs = require('node:fs');
const path = require('node:path');

const DEFAULT_CATALOG_PATH = path.resolve(__dirname, '..', 'pt-gateway', 'catalog.json');
const DEFAULT_SCHEMA = 2;
const REQUIRED_TOP_LEVEL = [
  'schema', 'models', 'kinds', 'auto_policy', 'errors',
  'retry_policy', 'charging', 'capability_gaps', 'currency', 'models_api_contract',
];
const KNOWN_SOURCES = new Set([
  'official', 'documented', 'verified', 'official+verified',
  'verified(unmapped)', 'models_api', 'product',
]);

class CatalogValidationError extends Error {
  constructor(message, reasons) {
    super(message);
    this.name = 'CatalogValidationError';
    this.reasons = reasons || [];
  }
}

// 错误码 / 文案 → category 的判别线索。这些是实现层的匹配关键词，
// 权威分类仍以 catalog.errors 的 http_status + agent_action 为准。
const CODE_HINTS = {
  rate_limited: ['model_rate_limited', 'rate_limit', 'rate limited'],
  auth_invalid: ['token_invalid', 'token_expired', 'token_disabled', 'invalid_api_key'],
  insufficient_balance: ['pre_consume_token_quota_failed', 'quota_not_enough', 'insufficient_quota', 'quota is not enough'],
  timeout: ['do_request_failed', 'fail_to_fetch_task', 'timeout'],
  model_unavailable: ['pricing rule not found', 'model_not_found', 'model_unavailable', 'no_permission', 'permission'],
  param_unsupported: ['unsupported_parameter', 'param_unsupported', 'invalid_parameter'],
  content_filtered: ['content_filter', 'content_policy', 'sensitive'],
  context_too_long: ['context_length_exceeded', 'context_too_long', 'too_long'],
};

let _cached = null;
let _cachedPath = null;

function _parseBody(body) {
  if (body == null) return {};
  if (typeof body === 'string') {
    try { return JSON.parse(body); } catch (_) { return { message: body }; }
  }
  if (typeof body === 'object') return body;
  return { message: String(body) };
}

function _extract(body) {
  const o = _parseBody(body);
  const err = (o && typeof o.error === 'object' && o.error) || {};
  const code = err.code || o.code || o.error_code || null;
  const message = err.message || o.message || (typeof o.error === 'string' ? o.error : null) || '';
  return { code: code ? String(code) : null, message: message ? String(message) : '' };
}

/**
 * 加载并校验 catalog。默认要求 schema === 2；校验失败一律抛错，
 * 调用方应「继续用上一个版本」（需求 §7 约定），不要降级使用坏数据。
 */
function loadCatalog(options = {}) {
  const p = options.path ? path.resolve(options.path) : DEFAULT_CATALOG_PATH;
  const expectedSchema = options.expectedSchema === undefined ? DEFAULT_SCHEMA : options.expectedSchema;
  const reasons = [];
  let raw;
  try {
    raw = fs.readFileSync(p, 'utf8');
  } catch (e) {
    throw new CatalogValidationError(`cannot read catalog: ${p}`, [e.message]);
  }
  let cat;
  try {
    cat = JSON.parse(raw);
  } catch (e) {
    throw new CatalogValidationError(`catalog is not valid JSON: ${p}`, [e.message]);
  }
  if (!cat || typeof cat !== 'object' || Array.isArray(cat)) reasons.push('root must be an object');
  else {
    for (const k of REQUIRED_TOP_LEVEL) if (!(k in cat)) reasons.push(`missing top-level section: ${k}`);
    if (expectedSchema !== null && cat.schema !== expectedSchema) {
      reasons.push(`schema mismatch: expected ${expectedSchema}, got ${String(cat.schema)}`);
    }
    if (!cat.models || typeof cat.models !== 'object' || Object.keys(cat.models).length === 0) {
      reasons.push('models must be a non-empty object');
    }
    if (!cat.kinds || Object.keys(cat.kinds).length !== 5) reasons.push('kinds must have exactly 5 entries');
    if (!cat.auto_policy || !cat.auto_policy.purposes || typeof cat.auto_policy.purposes !== 'object') {
      reasons.push('auto_policy.purposes missing');
    }
    if (!cat.errors || Object.keys(cat.errors).length === 0) reasons.push('errors dictionary missing');
    for (const use of Object.keys((cat.auto_policy && cat.auto_policy.purposes) || {})) {
      const list = cat.auto_policy.purposes[use];
      if (!Array.isArray(list) || list.length < 2) reasons.push(`purposes.${use} needs >= 2 candidates`);
      else for (const id of list) if (!cat.models[id]) reasons.push(`purposes.${use} references unknown model ${id}`);
    }
  }
  if (reasons.length) throw new CatalogValidationError('catalog validation failed', reasons);
  _cached = cat;
  _cachedPath = p;
  return cat;
}

function _catalog(options) {
  if (options && options.catalog) return options.catalog;
  if (_cached) return _cached;
  return loadCatalog(options || {});
}

function catalogVersion(catalog) {
  const c = catalog || (_cached || loadCatalog());
  return { schema: c.schema, updated: c.updated, path: _cachedPath || DEFAULT_CATALOG_PATH };
}

/** 需求 §7：来源可信 / null 一律按未知处理。 */
function isKnownSource(value) { return KNOWN_SOURCES.has(value); }
function isKnownValue(value, source) {
  if (value === null || value === undefined) return false;
  if (source === 'inferred' || source === 'configured') return false;
  return isKnownSource(source);
}

/**
 * AUTO 选模：按 auto_policy.purposes[purpose] 的顺序返回候选，并应用 4 条跳过规则。
 *
 * @param {string} purpose  用途键，例如 general / agent / search / long_report / vision
 * @param {Iterable<string>|null} keyModels  该 Key 的 /v1/models 可见 ID；null/undefined = 不做 Key 过滤
 * @param {object} options  { catalog, detail, registry, purposeKind }
 * @returns {Array|object}  默认返回 [{id, position, channel, kind}]；detail:true 返回 {purpose, candidates, skipped}
 */
function selectCandidates(purpose, keyModels, options = {}) {
  const cat = _catalog(options);
  const purposes = (cat.auto_policy && cat.auto_policy.purposes) || {};
  const list = Array.isArray(purposes[purpose]) ? purposes[purpose] : null;
  const skipped = [];
  const candidates = [];
  if (!list) {
    const empty = { purpose, candidates, skipped, reason: 'unknown_purpose' };
    return options.detail ? empty : candidates;
  }
  let keySet = null;
  if (keyModels !== null && keyModels !== undefined) {
    keySet = keyModels instanceof Set ? keyModels : new Set(keyModels);
  }
  const searchLike = purpose === 'search' || options.purposeKind === 'search';
  const registry = options.registry || null;

  list.forEach((id, position) => {
    const e = cat.models[id];
    if (!e) { skipped.push({ id, reason: 'not_in_catalog' }); return; }
    // 规则 1：已确认下架。0.7.3 起 "not_in_key_view" 不等于 delisted，不能当跳过理由。
    const reg = registry && registry[id];
    if (e.delisted === true || (reg && reg.delisted === true)) {
      skipped.push({ id, reason: 'delisted' }); return;
    }
    // 规则 2：显式 false 才跳过；null（未实测）不跳过。
    if (e.tool_calling && e.tool_calling.verified === false) {
      skipped.push({ id, reason: 'tool_calling_not_verified' }); return;
    }
    // 规则 3：搜索用途且实测搜索不生效 / 不返回来源。
    if (searchLike && e.web_search_detail) {
      const w = e.web_search_detail;
      if (w.works === false || w.returns_sources === false) {
        skipped.push({ id, reason: 'web_search_unusable' }); return;
      }
    }
    // 规则 4：当前 Key 无权使用。
    if (keySet && !keySet.has(id)) {
      skipped.push({ id, reason: 'not_in_key_view' }); return;
    }
    candidates.push({
      id, position,
      kind: e.kind || null,
      channel: (e.capabilities && e.capabilities.channel) || null,
    });
  });
  if (options.detail) return { purpose, candidates, skipped };
  return candidates;
}

function _actionFlags(agentAction) {
  switch (agentAction) {
    case 'retry': return { retryable: true, switch_model: false };
    case 'switch_model': return { retryable: false, switch_model: true };
    case 'compress_context': return { retryable: true, switch_model: true };
    case 'stop': return { retryable: false, switch_model: false };
    default: return { retryable: false, switch_model: false };
  }
}

/**
 * 把 HTTP 状态码 + 响应体归到 catalog.errors 的 9 类之一。
 * 字典里没有的错误按需求 §7：不重试、换模型、上报补充。
 */
function classifyError(status, body, options = {}) {
  const cat = _catalog(options);
  const { code, message } = _extract(body);
  const hay = `${code || ''} ${message}`.toLowerCase();
  const dict = cat.errors || {};
  const byStatus = [];
  for (const [category, def] of Object.entries(dict)) {
    const statuses = def && def.http_status;
    if (Array.isArray(statuses) && statuses.includes(Number(status))) byStatus.push(category);
  }
  let category = null;
  let matchedBy = null;
  // 1) 先看 error.code / 文案线索（PT 口径：判定只看 code，不看 i18n 文案）
  for (const [candidate, hints] of Object.entries(CODE_HINTS)) {
    if (!dict[candidate]) continue;
    if (hints.some((h) => hay.includes(h.toLowerCase()))) { category = candidate; matchedBy = 'code_or_message'; break; }
  }
  // 2) 再看状态码；多义状态码（503）时用 code 线索优先，否则取唯一匹配
  if (!category) {
    if (byStatus.length === 1) { category = byStatus[0]; matchedBy = 'http_status'; }
    else if (byStatus.length > 1) { category = byStatus[0]; matchedBy = 'http_status_ambiguous'; }
  }
  if (!category) {
    return {
      category: 'unmapped', agent_action: 'switch', retryable: false, switch_model: true,
      matched_by: 'none', http_status: Number(status) || null, code, message,
      note: 'errors 字典未收录：按「不重试、换模型」处理，并记录交给产品补充（需求 §7）。',
    };
  }
  const def = dict[category];
  const flags = _actionFlags(def.agent_action);
  return {
    category,
    agent_action: def.agent_action,
    ...flags,
    matched_by: matchedBy,
    http_status: Number(status) || null,
    code, message,
    note: def.notes || '',
  };
}

/**
 * 按 retry_policy 给出重试决策。
 * input 可直接是 {status, body, attempt, finishReason, hasTaskId, retryAfter}，
 * 也可以传 classifyError() 的结果（此时仍建议带上 status / attempt）。
 */
function retryDecision(input = {}, options = {}) {
  const cat = _catalog(options);
  const policy = cat.retry_policy || {};
  const cls = input.category && input.matched_by
    ? input
    : classifyError(input.status, input.body, options);
  const status = Number(input.status || cls.http_status || 0);
  const attempt = Math.max(1, Number(input.attempt || 1));
  const maxRetries = Number(options.maxRetries || 3);
  const finishReason = input.finishReason || null;
  const midstreamBad = ['error', 'timeout', 'abort'].includes(finishReason);

  let accepted = false;
  let settled = false;
  let charged = false;
  let rule = null;
  let autoRetry = false;

  const rules = Array.isArray(policy.rules) ? policy.rules : [];
  if (status >= 200 && status < 300) {
    accepted = true;
    settled = true;
    if (midstreamBad) {
      rule = rules.find((r) => String(r.observe || '').includes('finish_reason=error')) || rules[3];
      charged = true; autoRetry = false;
    } else if (input.hasTaskId) {
      rule = rules.find((r) => String(r.observe || '').includes('task_id')) || rules[4];
      autoRetry = false;
    } else {
      rule = rules.find((r) => String(r.observe || '').includes('200 + JSON')) || rules[1];
      autoRetry = false;
    }
  } else {
    rule = rules.find((r) => String(r.observe || '').includes('非 2xx')) || rules[0];
    accepted = false; settled = false;
    charged = false;
    autoRetry = rule ? rule.auto_retry === true : false;
  }

  let action;
  let reason;
  if (cls.agent_action === 'stop' || cls.category === 'auth_invalid' || cls.category === 'insufficient_balance') {
    action = 'stop'; reason = `category=${cls.category} 不可重试（换 Key / 充值 / 告知用户）`;
  } else if (status >= 200 && status < 300) {
    action = 'none'; reason = midstreamBad
      ? 'HTTP 200 流中途失败已按量扣费，重试会二次扣费；只记录不重放'
      : '已受理并结算，不能重放';
  } else if (!autoRetry || !cls.retryable) {
    action = cls.switch_model ? 'switch' : 'stop';
    reason = cls.switch_model ? '规则不允许自动重试，换模型' : '规则不允许自动重试';
  } else if (attempt > maxRetries) {
    action = 'switch'; reason = `已重试 ${maxRetries} 次仍失败，换模型`;
  } else {
    action = 'retry'; reason = '非 2xx 首包前失败，费用自动全额退回，可安全重试';
  }

  let delayMs = 0;
  if (action === 'retry') {
    if (cls.category === 'rate_limited') {
      const ra = Number(input.retryAfter);
      const def = policy.rate_limit || {};
      delayMs = (Number.isFinite(ra) && ra > 0 ? ra : (def.gateway_default_s || 5)) * 1000;
    } else {
      delayMs = Math.min(8000, 500 * Math.pow(2, attempt - 1));
    }
  }
  return {
    action,
    category: cls.category,
    retryable: action === 'retry',
    switch_model: action === 'switch',
    accepted, settled, charged,
    attempt,
    max_retries: maxRetries,
    delay_ms: delayMs,
    reason,
    rule_observe: rule ? rule.observe : null,
    refund: status >= 200 && status < 300 ? null : (rule ? rule.refund : null),
  };
}

/**
 * 思考模式传参。PT 尚未给出「关闭思考」的取值，所以 effort='none' 时
 * 返回 value:null + needs_confirmation:true，绝不猜一个字符串塞进请求。
 */
function reasoningParams(model, effort, options = {}) {
  const cat = _catalog(options);
  const e = cat.models[model];
  const caps = (e && e.capabilities) || {};
  const view = options.modelsApiView
    ? options.modelsApiView.find((m) => m && m.id === model)
    : null;
  const params = (view && view.supported_parameters) || [];
  const reasoning = (view && view.reasoning) || null;
  const supported = Boolean(
    caps.thinking === true || caps.thinking === 'conditional' ||
    (reasoning && reasoning.supported) || params.includes('reasoning_effort')
  );
  const param = caps.thinking_param || (supported ? 'reasoning_effort' : null);
  const base = {
    model,
    supported,
    param,
    default_enabled: reasoning ? reasoning.default_enabled : (supported ? true : null),
    mandatory: reasoning ? reasoning.mandatory : null,
    // PT 答复：mandatory=false / default_enabled=true 表示不传参也会开思考。
    // 关闭取值「待与 relay 层对齐」，catalog 保持 null，不编。
    disable_value: null,
    needs_confirmation: false,
    note: '不传参时网关也会开思考，成本与延迟按开启估算（SSOT §13）。',
  };
  if (!e) return { ...base, supported: false, param: null, error: 'unknown_model' };
  if (!supported) return { ...base, value: null, note: '该模型未声明思考能力，不要传 reasoning_effort。' };
  if (effort === 'none' || options.disable === true) {
    return {
      ...base, value: null, needs_confirmation: true,
      note: 'PT 尚未给出关闭思考的取值（SSOT §13.8 U6）。不要猜测 "none"/"off"/0，需与 relay 层对齐后再补。',
    };
  }
  return { ...base, value: effort === undefined || effort === null ? null : effort };
}

/** PT 答复的生成/理解判定规则：看 output_modalities，不看 input_modalities。 */
function isGenerationModel(modelsApiEntry) {
  const out = (modelsApiEntry && modelsApiEntry.architecture &&
    modelsApiEntry.architecture.output_modalities) || [];
  return out.some((m) => m === 'image' || m === 'video' || m === 'audio');
}

module.exports = {
  // catalog
  loadCatalog, catalogVersion, CatalogValidationError,
  DEFAULT_CATALOG_PATH, KNOWN_SOURCES, isKnownSource, isKnownValue,
  // rules
  selectCandidates, classifyError, retryDecision, reasoningParams, isGenerationModel,
};
