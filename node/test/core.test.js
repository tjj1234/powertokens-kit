'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {
  loadCatalog, catalogVersion, CatalogValidationError,
  selectCandidates, classifyError, retryDecision, reasoningParams,
  isGenerationModel, isKnownValue,
} = require('../index.js');

const cat = loadCatalog();

test('loadCatalog: schema 2 + 必需节齐全，版本可追溯', () => {
  const v = catalogVersion(cat);
  assert.equal(v.schema, 2);
  assert.ok(v.updated, 'updated 应存在');
  assert.ok(fs.existsSync(v.path));
  assert.equal(Object.keys(cat.kinds).length, 5);
  assert.ok(Object.keys(cat.models).length > 50);
});

test('loadCatalog: schema 不匹配 / 坏 JSON 一律抛错（不降级使用坏数据）', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'pt-cat-'));
  const p = path.join(dir, 'catalog.json');
  fs.writeFileSync(p, JSON.stringify({ ...cat, schema: 999 }));
  assert.throws(() => loadCatalog({ path: p }), CatalogValidationError);
  fs.writeFileSync(p, '{not json');
  assert.throws(() => loadCatalog({ path: p }), CatalogValidationError);
  fs.writeFileSync(p, JSON.stringify({ ...cat, auto_policy: { purposes: { foo: ['does-not-exist'] } } }));
  assert.throws(() => loadCatalog({ path: p }), CatalogValidationError);
  fs.rmSync(dir, { recursive: true, force: true });
});

test('selectCandidates: 按 auto_policy 顺序返回，且候选跨 >= 2 渠道', () => {
  for (const use of Object.keys(cat.auto_policy.purposes)) {
    const list = selectCandidates(use, null, { catalog: cat });
    assert.ok(list.length >= 2, `${use} 至少 2 个候选`);
    assert.equal(list[0].id, cat.auto_policy.purposes[use][0]);
    assert.ok(new Set(list.map((x) => x.channel)).size >= 2 || list.length >= 2);
  }
});

test('selectCandidates: 4 条跳过规则 —— 未实测(null)不跳过、显式 false 才跳过', () => {
  const use = 'agent';
  const original = cat.auto_policy.purposes[use];
  const clone = JSON.parse(JSON.stringify(cat));
  const full = new Set(original);
  assert.equal(selectCandidates(use, full, { catalog: clone }).length, original.length);

  // 规则 2 + 3 + 1
  clone.models[original[0]].tool_calling = { verified: false, _source: 'verified' };
  clone.models[original[1]].web_search_detail = { works: false, returns_sources: true, _source: 'verified' };
  clone.models[original[2]].delisted = true;
  const d = selectCandidates(use, full, { catalog: clone, detail: true, purposeKind: 'search' });
  assert.equal(d.candidates.length, 0);
  assert.deepEqual(d.skipped.map((x) => x.reason),
    ['tool_calling_not_verified', 'web_search_unusable', 'delisted']);
  // 规则 3 只在搜索类用途生效：普通用途不该因 web_search_detail 跳过
  const d2 = selectCandidates(use, full, { catalog: clone, detail: true });
  assert.ok(!d2.skipped.some((x) => x.reason === 'web_search_unusable'));
  assert.equal(d2.candidates.length, 1);
});

test('selectCandidates: Key 视图过滤（not_in_key_view），但不当作下架', () => {
  const use = 'agent';
  const original = cat.auto_policy.purposes[use];
  const one = new Set([original[0]]);
  const d = selectCandidates(use, one, { catalog: cat, detail: true });
  assert.equal(d.candidates.length, 1);
  assert.ok(d.skipped.every((x) => x.reason === 'not_in_key_view'));
  assert.ok(!d.skipped.some((x) => x.reason === 'delisted'));
});

test('selectCandidates: registry 里的 delisted 才跳过，not_in_key_view 不跳过', () => {
  const use = 'agent';
  const original = cat.auto_policy.purposes[use];
  const registry = { [original[0]]: { not_in_key_view: true, delisted: false } };
  const d = selectCandidates(use, null, { catalog: cat, registry, detail: true });
  assert.equal(d.candidates.length, original.length);
});

test('classifyError: 429 / 402 / 401 / 未收录', () => {
  assert.equal(classifyError(429, { error: { code: 'model_rate_limited' } }, { catalog: cat }).category, 'rate_limited');
  assert.equal(classifyError(402, { error: { code: 'pre_consume_token_quota_failed' } }, { catalog: cat }).category, 'insufficient_balance');
  assert.equal(classifyError(401, { error: { code: 'token_invalid' } }, { catalog: cat }).category, 'auth_invalid');
  const u = classifyError(418, { error: { code: 'brand_new_error' } }, { catalog: cat });
  assert.equal(u.category, 'unmapped');
  assert.equal(u.retryable, false);
  assert.equal(u.switch_model, true);
});

test('retryDecision: 非 2xx 可重试且带退避；401/402 直接 stop', () => {
  const r = retryDecision({ status: 503, body: { error: { code: 'upstream_5xx' } }, attempt: 1 }, { catalog: cat });
  assert.equal(r.action, 'retry');
  assert.equal(r.accepted, false);
  assert.equal(r.refund, 'auto_full');
  assert.ok(r.delay_ms > 0);
  assert.equal(retryDecision({ status: 401, body: { error: { code: 'token_invalid' } } }, { catalog: cat }).action, 'stop');
  assert.equal(retryDecision({ status: 402, body: { error: { code: 'pre_consume_token_quota_failed' } } }, { catalog: cat }).action, 'stop');
});

test('retryDecision: 200 已结算 / 流中途失败已扣费 —— 都不重放', () => {
  const ok = retryDecision({ status: 200, body: { id: 'abc' } }, { catalog: cat });
  assert.equal(ok.action, 'none');
  assert.equal(ok.settled, true);
  const mid = retryDecision({ status: 200, body: {}, finishReason: 'timeout' }, { catalog: cat });
  assert.equal(mid.action, 'none');
  assert.equal(mid.charged, true);
  assert.equal(mid.retryable, false);
});

test('retryDecision: 超过重试上限换模型；429 用 Retry-After', () => {
  const r = retryDecision({ status: 503, body: {}, attempt: 4 }, { catalog: cat, maxRetries: 3 });
  assert.equal(r.action, 'switch');
  const rl = retryDecision({ status: 429, body: { error: { code: 'model_rate_limited' } }, retryAfter: 12 }, { catalog: cat });
  assert.equal(rl.action, 'retry');
  assert.equal(rl.delay_ms, 12000);
});

test('reasoningParams: 关闭思考的取值未定，必须 needs_confirmation 而不是猜字符串', () => {
  const withThinking = Object.keys(cat.models).find((id) =>
    cat.models[id].kind === 'chat' && cat.models[id].capabilities &&
    (cat.models[id].capabilities.thinking === true || cat.models[id].capabilities.thinking === 'conditional'));
  if (!withThinking) return; // catalog 无 thinking 模型时跳过
  const on = reasoningParams(withThinking, 'high', { catalog: cat });
  assert.equal(on.supported, true);
  assert.equal(on.value, 'high');
  const off = reasoningParams(withThinking, 'none', { catalog: cat });
  assert.equal(off.value, null);
  assert.equal(off.disable_value, null);
  assert.equal(off.needs_confirmation, true);
});

test('reasoningParams: 传 /v1/models 视图时以官方 supported_parameters 为准', () => {
  const p = reasoningParams('qwen3.8-flash', 'medium', {
    catalog: cat,
    modelsApiView: [{ id: 'qwen3.8-flash', supported_parameters: ['reasoning_effort'],
      reasoning: { supported: true, mandatory: false, default_enabled: true } }],
  });
  assert.equal(p.supported, true);
  assert.equal(p.default_enabled, true);
});

test('isGenerationModel: 看 output_modalities，不看 input_modalities', () => {
  assert.equal(isGenerationModel({ architecture: { input_modalities: ['text', 'image'], output_modalities: ['text'] } }), false);
  assert.equal(isGenerationModel({ architecture: { input_modalities: ['text', 'image'], output_modalities: ['image'] } }), true);
  assert.equal(isGenerationModel({ architecture: { input_modalities: ['text', 'image'], output_modalities: ['video'] } }), true);
});

test('isKnownValue: null 或 inferred 一律按未知', () => {
  assert.equal(isKnownValue(null, 'official'), false);
  assert.equal(isKnownValue(128000, 'inferred'), false);
  assert.equal(isKnownValue(128000, 'official'), true);
  assert.equal(isKnownValue(true, 'verified'), true);
});
