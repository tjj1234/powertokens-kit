'use strict';

/**
 * powertokens-kit core (Node.js, zero dependency)
 *
 * 这个包只做「规则」，不发任何网络请求。所有事实都来自同一份
 * pt-gateway/catalog.json —— Python 工具链读的也是这一份。
 *
 *   loadCatalog(options)                     固定版本 + 格式校验，失败抛错
 *   selectCandidates(purpose, keyModels, opts)  AUTO 选模 / PINNED 指定模型可用性判定
 *   classifyError(status, body)              错误归类（errors 字典）
 *   retryDecision(input)                     重试 / 换模型 / 停止（retry_policy，支持 pinned）
 *   reasoningParams(model, effort)           思考模式传参（models_api_contract）
 *
 * 0.8.2 起：selectCandidates 支持 options.pinnedModel（只判定、不自动换），
 * 用途不在 auto_policy 时按 routing.recommended 兜底；retryDecision 支持
 * input.pinned，按「用户指定模型」规则处理（同模型重试、不自动换）。
 * 0.8.2 起 npm pack 自包含：包内 catalog.snapshot.json 是 pt-gateway/catalog.json
 * 的只读快照；开发态仍读仓库同一份，放进 vendor 后自动回退包内快照。
 * 0.8.4 起 retryDecision 分支顺序固定为：发送前拦截 → 受理未知 → 2xx 已受理
 * （不重放）→ 上下文超长 → 其余（D2）；2xx 一律不返回 compact/retry/switch。
 *
 * 参考：docs/PT_项目资料总集_20261112.md §7（Agent 侧约定）、
 *       "powertokens-kit catalog 补充需求.md" §0 P0 / §3 需求 3、4 / §7。
 */

const fs = require('node:fs');
const path = require('node:path');

const REPO_CATALOG_PATH = path.resolve(__dirname, '..', 'pt-gateway', 'catalog.json');
const PACKAGED_CATALOG_PATH = path.resolve(__dirname, 'catalog.snapshot.json');
/** 开发态读仓库同一份 catalog；放进 vendor 脱离仓库时回退到包内快照。 */
function _defaultCatalogPath() {
  try {
    fs.accessSync(REPO_CATALOG_PATH, fs.constants.R_OK);
    return REPO_CATALOG_PATH;
  } catch (_) {
    return PACKAGED_CATALOG_PATH;
  }
}
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
  const p = options.path ? path.resolve(options.path) : _defaultCatalogPath();
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
  // C2：把 kit 包版本与 catalog updated 一起透出，三处（package.json / CHANGELOG /
  // catalogVersion）可对账。require 自身包内 package.json，零依赖、不发请求。
  let kitVersion = null;
  try {
    kitVersion = require('./package.json').version || null;
  } catch (_) { kitVersion = null; }
  return {
    schema: c.schema,
    updated: c.updated,
    kit_version: kitVersion,
    path: _cachedPath || _defaultCatalogPath(),
  };
}

/** 需求 §7：来源可信 / null 一律按未知处理。 */
function isKnownSource(value) { return KNOWN_SOURCES.has(value); }
function isKnownValue(value, source) {
  if (value === null || value === undefined) return false;
  if (source === 'inferred' || source === 'configured') return false;
  return isKnownSource(source);
}

/**
 * 单条候选的跳过理由。AUTO 与 PINNED 共用同一套判定，避免两种模式口径漂移。
 * 顺序固定：delisted → tool_calling.verified===false → 搜索实测不可用 → 不在 Key 视图。
 * 注意：null（未实测）一律不跳过；"not_in_key_view" 不等于 delisted。
 */
function _skipReason(id, e, ctx) {
  const { keySet, searchLike, registry } = ctx;
  const reg = registry && registry[id];
  if (e.delisted === true || (reg && reg.delisted === true)) return 'delisted';
  if (e.tool_calling && e.tool_calling.verified === false) return 'tool_calling_not_verified';
  if (searchLike && e.web_search_detail) {
    const w = e.web_search_detail;
    if (w.works === false || w.returns_sources === false) return 'web_search_unusable';
  }
  if (keySet && !keySet.has(id)) return 'not_in_key_view';
  return null;
}

/**
 * 请求是否带图片：options.hasImage===true 或 inputModalities 含 'image'。
 */
function _needsVision(options) {
  return options.hasImage === true ||
    (Array.isArray(options.inputModalities) && options.inputModalities.includes('image'));
}

/** 模型是否声明看图能力：兼容 capabilities.vision 与 capabilities.multimodal.vision 两种写法。 */
function _modelCanSeeImages(e) {
  const caps = (e && e.capabilities) || {};
  return caps.vision === true || Boolean(caps.multimodal && caps.multimodal.vision === true);
}

// ---------------------------------------------------------------- 0.8.8 媒体能力筛选
// 需求 M2：按参数筛选候选。规则（用方复核 §1）：列表里没有就是不支持，绝不乐观放行；
// 能力字段缺失报 capability_unknown 并排除，同时附实际可用值。只读 capabilities.media，
// 顶层同名键（0.8.5 历史值）不参与判断——capabilities.media 是唯一权威。
const REQ_FIELD_MAP = {
  duration_s:   { get: (m) => (m.durations || {}).value, rule: (m) => m.duration_rule },
  resolution:   { get: (m) => (m.resolutions || {}).value },
  // 0.8.12 P2-7：sizes 新语义只放精确像素（无官方枚举则 null）；分辨率档位迁入 size_tiers。
  // 平滑过渡：sizes.value 非数组时回落到 size_tiers.value，避免带 size 需求的调用因语义迁移失效；
  // 不传 requirements 时本函数不触发，旧签名默认行为逐字节不变。
  size:         { get: (m) => {
    const s = m.sizes || {};
    if (Array.isArray(s.value)) return s.value;
    const t = m.size_tiers || {};
    return Array.isArray(t.value) ? t.value : s.value;
  } },
  // 0.8.9 P2-5：adaptive 从 aspect_ratios.value 单列为 adaptive_supported 标记。
  // 取值时若该标记为真，把 'adaptive' 还原进可匹配列表——使升级前后对 adaptive 需求语义一致，
  // 且不改变 old 签名（不传 requirements 时本函数不触发）。
  aspect_ratio: { get: (m) => {
    const ar = m.aspect_ratios || {};
    const vals = Array.isArray(ar.value) ? ar.value.slice() : ar.value;
    if (ar.adaptive_supported === true && Array.isArray(vals)) vals.push('adaptive');
    return vals;
  } },
  mode:         { get: (m) => (m.modes || {}).value },
};

function _normMediaVal(v) {
  return String(v).trim().toLowerCase();
}

/** 把一个需求值对到模型能力上。返回 null=匹配，或 {field,value,available,reason}。 */
function _matchOneRequirement(media, field, value) {
  const spec = REQ_FIELD_MAP[field];
  if (!spec) return { field, value, available: null, reason: 'capability_unknown' }; // 未知维度一律排除
  let available = spec.get ? spec.get(media) : null;
  // duration_s 特殊：有 duration_rule(min/max) 时用区间判断，无枚举时按区间
  if (field === 'duration_s' && !available && spec.rule) {
    const rule = spec.rule(media);
    if (rule && (rule.min_duration_s !== undefined || rule.max_duration_s !== undefined)) {
      const n = Number(value);
      const min = rule.min_duration_s === -1 ? 0 : rule.min_duration_s;
      const max = rule.max_duration_s;
      const inRange = Number.isFinite(n) &&
        (min === undefined || n >= min) && (max === undefined || n <= max);
      return inRange ? null
        : { field, value, available: { min_duration_s: rule.min_duration_s, max_duration_s: rule.max_duration_s }, reason: 'unsupported' };
    }
  }
  if (!Array.isArray(available)) {
    return { field, value, available: null, reason: 'capability_unknown' };
  }
  const hit = available.some((x) => _normMediaVal(x) === _normMediaVal(value));
  return hit ? null : { field, value, available, reason: 'unsupported' };
}

/** 模型的媒体能力是否满足全部 requirements。返回 {ok, unmatched:[...]}。 */
function _matchMediaRequirements(entry, requirements) {
  const media = (entry && entry.capabilities && entry.capabilities.media) || null;
  const unmatched = [];
  for (const [field, value] of Object.entries(requirements || {})) {
    if (value === null || value === undefined) continue;
    if (!media) {
      unmatched.push({ field, value, available: null, reason: 'capability_unknown' });
      continue;
    }
    const miss = _matchOneRequirement(media, field, value);
    if (miss) unmatched.push(miss);
  }
  return { ok: unmatched.length === 0, unmatched };
}

/**
 * 选模。两种互斥模式：
 *
 * AUTO（默认）：按 auto_policy.purposes[purpose] 顺序返回候选，应用 4 条跳过规则；
 *   用途不在 auto_policy 时，按模型 routing.recommended 标签兜底（0.8.2）。
 *   @returns {Array} 默认 [{id, position, channel, kind}]；
 *            detail:true → {purpose, candidates, skipped, matched_by}
 *
 * PINNED（options.pinnedModel 指定）：只判定这一个模型是否可用，**不自动换模型**。
 *   返回 {pinned, available, reasons:[{code,message}], candidate, checks}。
 *
 * @param {string} purpose  用途键，例如 general / agent / search / long_report / vision
 * @param {Iterable<string>|null} keyModels  该 Key 的 /v1/models 可见 ID；null/undefined = 不做 Key 过滤
 * @param {object} options  { catalog, detail, registry, purposeKind, pinnedModel, hasImage, inputModalities }
 */
function selectCandidates(purpose, keyModels, options = {}) {
  const cat = _catalog(options);
  const purposes = (cat.auto_policy && cat.auto_policy.purposes) || {};
  let keySet = null;
  if (keyModels !== null && keyModels !== undefined) {
    keySet = keyModels instanceof Set ? keyModels : new Set(keyModels);
  }
  const ctx = {
    keySet,
    searchLike: purpose === 'search' || options.purposeKind === 'search',
    registry: options.registry || null,
    needVision: _needsVision(options),
  };

  // ---- PINNED：用户指定模型，只返回可用性判定，绝不自动换 ----
  if (Object.prototype.hasOwnProperty.call(options, 'pinnedModel') &&
      options.pinnedModel !== null && options.pinnedModel !== undefined) {
    const id = String(options.pinnedModel);
    const e = cat.models[id];
    const reasons = [];
    const checks = { in_catalog: Boolean(e), vision: null, key_view: null, tool_calling: null };
    if (!e) {
      reasons.push({ code: 'not_in_catalog', message: `模型 ${id} 不在 catalog 中` });
    } else {
      const skip = _skipReason(id, e, ctx);
      checks.tool_calling = !(e.tool_calling && e.tool_calling.verified === false);
      checks.key_view = !keySet || keySet.has(id);
      if (skip) reasons.push({ code: skip, message: _pinnedReasonMessage(skip, id) });
      if (ctx.needVision) {
        const vision = _modelCanSeeImages(e);
        checks.vision = vision;
        if (!vision) reasons.push({
          code: 'vision_unsupported',
          message: `请求带图片，但模型 ${id} 未声明看图能力；按 pinned 规则由用户决定，不自动换模型`,
        });
      }
    }
    const available = reasons.length === 0;
    return {
      pinned: id,
      available,
      reasons,
      checks,
      candidate: available ? {
        id, position: 0,
        kind: e.kind || null,
        channel: (e.capabilities && e.capabilities.channel) || null,
      } : null,
      note: available
        ? '用户指定模型可用；pinned 模式下不自动换模型。'
        : '用户指定模型不可用；pinned 模式下不自动换模型，交给用户选择。',
    };
  }

  // ---- AUTO：用途候选列表 ----
  let list = Array.isArray(purposes[purpose]) ? purposes[purpose] : null;
  let matchedBy = 'auto_policy.purposes';
  if (!list) {
    // 0.8.2 兜底：auto_policy 未覆盖的用途，按模型 routing.recommended 标签匹配。
    // 标签是无序的：按 catalog 中的稳定顺序返回，不宣称任何优先级。
    const viaRouting = Object.keys(cat.models).filter((mid) => {
      const r = cat.models[mid] && cat.models[mid].routing && cat.models[mid].routing.recommended;
      return Array.isArray(r) && r.includes(purpose);
    });
    if (viaRouting.length) {
      list = viaRouting;
      matchedBy = 'routing.recommended';
    }
  }
  const skipped = [];
  const candidates = [];
  // 0.8.8 M2：可选媒体能力筛选。只在显式传 requirements 时启用；不传时以下逻辑全不触发。
  const hasReq = Boolean(options.requirements && typeof options.requirements === 'object'
    && Object.keys(options.requirements).length);
  const excluded = [];
  // A4：搜索用途只有 works===true 且 returns_sources===true 才是可投入使用候选；
  // 值为 null（未实测）的进 pending_verification，绝不进 candidates。
  const pendingVerification = [];
  if (!list) {
    const empty = {
      purpose, candidates, skipped, pending_verification: pendingVerification,
      reason: 'unknown_purpose', matched_by: null,
    };
    return options.detail ? empty : candidates;
  }
  list.forEach((id, position) => {
    const e = cat.models[id];
    if (!e) { skipped.push({ id, reason: 'not_in_catalog' }); return; }
    const skip = _skipReason(id, e, ctx);
    if (skip) { skipped.push({ id, reason: skip }); return; }
    if (ctx.searchLike) {
      const w = e.web_search_detail;
      const works = w ? w.works : undefined;
      const sources = w ? w.returns_sources : undefined;
      if (works === true && sources === true) {
        candidates.push({
          id, position,
          kind: e.kind || null,
          channel: (e.capabilities && e.capabilities.channel) || null,
        });
      } else {
        // null / 缺字段：未实测，不投入使用也不判死，等真机验证。
        pendingVerification.push({
          id, position,
          works: works === undefined ? null : works,
          returns_sources: sources === undefined ? null : sources,
          reason: 'pending_verification',
        });
      }
      return;
    }
    // 0.8.8 M2：媒体能力筛选。列表里没有就是不支持；能力未知一律排除并附可用值。
    if (hasReq) {
      const match = _matchMediaRequirements(e, options.requirements);
      if (!match.ok) {
        excluded.push({
          id, position, reason: 'requirements_unmatched',
          unmatched: match.unmatched,
        });
        return;
      }
    }
    candidates.push({
      id, position,
      kind: e.kind || null,
      channel: (e.capabilities && e.capabilities.channel) || null,
      ...(hasReq ? { match_basis: 'capabilities.media' } : {}),
    });
  });
  if (options.detail) {
    return {
      purpose, candidates, skipped, pending_verification: pendingVerification, matched_by: matchedBy,
      ...(hasReq ? { requirements: options.requirements, excluded } : {}),
    };
  }
  return candidates;
}

function _pinnedReasonMessage(code, id) {
  switch (code) {
    case 'delisted': return `模型 ${id} 已确认下架`;
    case 'tool_calling_not_verified': return `模型 ${id} 的 tool_calling.verified===false（已实测不支持工具调用）`;
    case 'web_search_unusable': return `模型 ${id} 的搜索实测 works/returns_sources 为 false`;
    case 'not_in_key_view': return `模型 ${id} 不在这把 Key 的可见列表里（/v1/models 视图）`;
    default: return `模型 ${id} 不可用：${code}`;
  }
}

function _actionFlags(agentAction) {
  switch (agentAction) {
    case 'retry': return { retryable: true, switch_model: false };
    case 'switch_model': return { retryable: false, switch_model: true };
    // A3：上下文超长不能原样重试 —— 必须先压缩上下文 / 缩减输入再发。
    case 'compress_context': return { retryable: false, switch_model: false, compact: true };
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

const TRANSIENT_CATEGORIES = new Set(['rate_limited', 'upstream_error', 'timeout']);
const PINNED_STOP_CATEGORIES = new Set(['auth_invalid', 'insufficient_balance', 'content_filtered']);

/**
 * 按 retry_policy 给出重试决策。
 * input 可直接是 {status, body, attempt, finishReason, hasTaskId, retryAfter}，
 * 也可以传 classifyError() 的结果（此时仍建议带上 status / attempt）。
 *
 * 0.8.2 起支持 input.pinned===true（用户指定了模型）：
 *   - 429/5xx/超时：只重试同一模型，耗尽后停止，绝不返回「换模型」；
 *   - 不可用/无权限/参数不支持：返回 user_choice，交给用户选择，不自动换；
 *   - 带图片但模型不能看图：发送前返回 unsupported，交给用户决定；
 *   - tool_calling 实测为 null：照常使用，结果里 untested=['tool_calling']；
 *   - 401/402：停止（与 AUTO 一致）。
 *
 * 0.8.4 D2：分支优先级固定为 发送前拦截 → 受理未知 → 2xx 已受理（不重放）
 *   → 上下文超长 → 其余。2xx 一律 action='none'（含流中途报错、含 body 带
 *   context_length_exceeded），pinned 与否都一致，绝不 compact/retry/switch；
 *   只有非 2xx 的上下文超长才 compact（避免宿主压缩后重发重复扣费）。
 */
function retryDecision(input = {}, options = {}) {
  const cat = _catalog(options);
  const policy = cat.retry_policy || {};
  const cls = input.category && input.matched_by
    ? input
    : classifyError(input.status, input.body, options);
  const rawStatus = input.status !== undefined && input.status !== null && input.status !== ''
    ? input.status : cls.http_status;
  const hasNumericStatus = rawStatus !== undefined && rawStatus !== null && rawStatus !== '' &&
    Number.isFinite(Number(rawStatus)) && Number(rawStatus) !== 0;
  const status = hasNumericStatus ? Number(rawStatus) : 0;
  const attempt = Math.max(1, Number(input.attempt || 1));
  // A1：maxRetries=0 是合法值（不重试），只有 undefined 才用默认 3。
  const rawMax = options.maxRetries;
  const maxRetries = rawMax === undefined ? 3
    : (Number.isFinite(Number(rawMax)) ? Number(rawMax) : 3);
  const finishReason = input.finishReason || null;
  const midstreamBad = ['error', 'timeout', 'abort'].includes(finishReason);
  const pinned = input.pinned === true;
  const pinnedModel = input.model || input.pinnedModel ||
    (typeof input.pinned === 'string' ? input.pinned : null);
  const entry = pinnedModel && cat.models[pinnedModel] ? cat.models[pinnedModel] : null;
  const hasImage = input.hasImage === true ||
    (Array.isArray(input.inputModalities) && input.inputModalities.includes('image'));
  const visionUnsupported = Boolean(hasImage && entry && !_modelCanSeeImages(entry));
  const toolUntested = input.toolCallingVerified === null ||
    (input.toolCallingVerified === undefined && Boolean(entry && entry.tool_calling &&
      entry.tool_calling.verified === null));

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
  let reasonCode = null;
  let needsUserChoice = false;
  let beforeSend = false;

  // 发送前拦截（优先于一切响应态分支）：带图片但模型不能看图。
  // AUTO 选能看图的；PINNED 交给用户。此时请求还没发出，不存在"受理未知"问题。
  if (visionUnsupported) {
    beforeSend = true;
    if (pinned) {
      action = 'unsupported'; reasonCode = 'vision_unsupported';
      needsUserChoice = true;
      reason = `请求带图片，但指定模型 ${pinnedModel} 不能看图；发送前返回不支持，由用户决定`;
    } else {
      action = 'switch'; reasonCode = 'vision_unsupported_auto_switch';
      reason = '请求带图片，当前模型不能看图；自动改为选能看图的模型';
    }
  } else if (!hasNumericStatus) {
    // A2：受理状态未知（无有效 HTTP 状态码：客户端超时 / 断网 / 连接被重置）。
    // 没收到响应 ≠ PT 返回非 2xx；PT 可能已受理并扣费，重发会二次扣费。
    // 绝不是 retry / switch。
    accepted = 'unknown'; settled = false; charged = false;
    action = 'stop'; reasonCode = 'acceptance_unknown';
    needsUserChoice = true;
    reason = '未收到有效 HTTP 状态码（客户端超时 / 断网 / 连接重置）：PT 可能已受理并扣费，'
      + '不重发、不换模型；需用户用 request_id / task_id 查 logs/query 确认受理状态';
  } else if (status >= 200 && status < 300) {
    // D2：2xx 已受理（含流中途报错）一律不重放，优先于上下文超长与一切重放分支。
    // 否则 HTTP 200 + context_length_exceeded 会返回 compact，宿主压缩后再发可能重复扣费。
    action = 'none'; reason = midstreamBad
      ? 'HTTP 200 流中途失败已按量扣费，重试会二次扣费；只记录不重放'
      : '已受理并结算，不能重放';
  } else if (cls.agent_action === 'compress_context' || cls.compact === true) {
    // A3：上下文超长 —— 仅非 2xx（通常 400）先压缩上下文或缩减输入再发，禁止原样重试。
    action = 'compact'; reasonCode = 'context_too_long';
    reason = '上下文超长（400 context_length_exceeded）：先压缩上下文或缩减输入再发，不原样重试；'
      + '各模型上下文上限见 limits（未实测为 null，不猜）';
  } else if (pinned && PINNED_STOP_CATEGORIES.has(cls.category)) {
    action = 'stop'; reasonCode = 'pinned_stop';
    reason = `category=${cls.category} 不可重试（换 Key / 充值 / 告知用户）`;
  } else if (pinned && TRANSIENT_CATEGORIES.has(cls.category)) {
    // 429/5xx/超时：只重试同一个模型；耗尽后停止，绝不返回换模型。
    if (attempt > maxRetries) {
      action = 'stop'; reasonCode = 'pinned_retry_exhausted';
      needsUserChoice = true;
      reason = `指定模型已重试 ${maxRetries} 次仍失败；pinned 不自动换模型，请用户选择`;
    } else {
      action = 'retry'; reasonCode = 'pinned_retry_same_model';
      reason = '429/5xx/超时首包前失败可安全重试；pinned 只重试同一个模型，不返回换模型';
    }
  } else if (pinned) {
    // 模型不可用 / 无权限 / 参数不支持 / unmapped → user_choice，不自动换。
    action = 'user_choice'; reasonCode = 'pinned_user_choice';
    needsUserChoice = true;
    reason = `category=${cls.category} 在 pinned 模式下不自动换模型，需要用户选择`;
  } else if (cls.agent_action === 'stop' || cls.category === 'auth_invalid' || cls.category === 'insufficient_balance') {
    action = 'stop'; reason = `category=${cls.category} 不可重试（换 Key / 充值 / 告知用户）`;
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
    switch_model: pinned ? false : action === 'switch',
    accepted, settled, charged,
    attempt,
    max_retries: maxRetries,
    delay_ms: delayMs,
    reason,
    reason_code: reasonCode,
    pinned,
    pinned_model: pinnedModel || null,
    needs_user_choice: needsUserChoice,
    before_send: beforeSend,
    untested: pinned && toolUntested ? ['tool_calling'] : [],
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

/**
 * 0.8.8 M5：把调用方的「友好参数」映射成 PT 请求该用的真实字段名。
 * 纯规则、不发请求、不改默认值；不支持的参数进 unsupported 报错，绝不静默丢弃。
 *
 * @param {string} modelId  catalog 中的模型 id
 * @param {object} req      友好参数，如 { duration_s: 4, resolution: '720P', aspect_ratio: '16:9' }
 * @param {object} options  { catalog }
 * @returns { object } { model, params:{真实字段名:值}, unsupported:[{field,value,reason,available}] }
 */
function mediaRequestParams(modelId, req, options = {}) {
  const cat = _catalog(options);
  const entry = cat.models[modelId];
  if (!entry) {
    return { model: modelId, params: {}, unsupported: Object.entries(req || {}).map(
      ([field, value]) => ({ field, value, reason: 'not_in_catalog', available: null })) };
  }
  const media = (entry.capabilities && entry.capabilities.media) || null;
  const pnames = (media && media.param_names) || {};
  const params = {};
  const unsupported = [];
  for (const [field, value] of Object.entries(req || {})) {
    if (value === null || value === undefined) continue;
    const real = pnames[field];
    if (!real) {
      unsupported.push({ field, value, reason: 'no_param_mapping', available: null });
      continue;
    }
    // 值校验：用与 M2 相同的媒体能力判定，避免传默认值掩盖不支持
    if (media) {
      const miss = _matchOneRequirement(media, field, value);
      if (miss) { unsupported.push(miss); continue; }
    }
    params[real] = value;
  }
  return { model: modelId, params, unsupported };
}

module.exports = {
  // catalog
  loadCatalog, catalogVersion, CatalogValidationError,
  REPO_CATALOG_PATH, PACKAGED_CATALOG_PATH, KNOWN_SOURCES, isKnownSource, isKnownValue,
  // rules
  selectCandidates, classifyError, retryDecision, reasoningParams, isGenerationModel,
  mediaRequestParams,
};
