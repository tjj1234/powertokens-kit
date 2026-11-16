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

test('loadCatalog: 包内 catalog.snapshot.json 存在且与主 catalog 一致（npm pack 自包含）', () => {
  const snap = path.join(__dirname, '..', 'catalog.snapshot.json');
  assert.ok(fs.existsSync(snap), '包内快照应存在');
  const snapCat = JSON.parse(fs.readFileSync(snap, 'utf8'));
  assert.equal(snapCat.schema, cat.schema);
  assert.equal(snapCat.updated, cat.updated);
  assert.deepEqual(Object.keys(snapCat.models).sort(), Object.keys(cat.models).sort());
  assert.equal(snapCat.models['wan3.0-video'].price_usd_5s_720p, 0.5);
});

test('C1: 包内 catalog.schema.snapshot.json 存在且与 pt-gateway 版本一致', () => {
  const snap = path.join(__dirname, '..', 'catalog.schema.snapshot.json');
  assert.ok(fs.existsSync(snap), 'schema 快照应存在');
  const snapSchema = JSON.parse(fs.readFileSync(snap, 'utf8'));
  const main = path.join(__dirname, '..', 'pt-gateway', 'catalog.schema.json');
  if (fs.existsSync(main)) {
    const mainSchema = JSON.parse(fs.readFileSync(main, 'utf8'));
    assert.deepEqual(snapSchema, mainSchema, 'schema 快照应与 pt-gateway/catalog.schema.json 完全一致');
  }
  assert.equal(snapSchema.properties && typeof snapSchema.properties, 'object');
});

test('C2: catalogVersion 透出 kit 包版本，与 package.json / CHANGELOG 可对账', () => {
  const v = catalogVersion(cat);
  const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf8'));
  assert.equal(v.kit_version, pkg.version, 'kit_version 应等于 package.json 的 version');
  assert.ok(/^\d+\.\d+\.\d+$/.test(String(v.kit_version)));
  assert.ok(v.updated, 'catalog updated 应透出');
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

test('selectCandidates: 按 auto_policy 顺序返回，且候选跨 >= 2 渠道（search 除外，见 A4）', () => {
  for (const use of Object.keys(cat.auto_policy.purposes)) {
    const list = selectCandidates(use, null, { catalog: cat });
    if (use === 'search') {
      // A4（0.8.3）：3 个搜索候选的 works/returns_sources 全是 null（未实测），
      // candidates 必须为空、进 pending_verification，绝不把未实测模型当可用。
      assert.equal(list.length, 0, 'search 全 null 时候选必须为空');
      continue;
    }
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

// ---------------------------------------------------------------------------
// 0.8.2：pinned（用户指定模型）+ routing.recommended 兜底
// ---------------------------------------------------------------------------

test('selectCandidates.pinnedModel: 可用时只判定该模型，不返回候选列表', () => {
  const mid = cat.auto_policy.purposes.agent[0];
  const v = selectCandidates('agent', new Set([mid]), { catalog: cat, pinnedModel: mid });
  assert.equal(v.pinned, mid);
  assert.equal(v.available, true);
  assert.deepEqual(v.reasons, []);
  assert.equal(v.candidate.id, mid);
  assert.ok(/不自动换模型/.test(v.note));
});

test('selectCandidates.pinnedModel: 不在 Key 视图 / 工具调用实测 false / 带图不能看 → 给出原因', () => {
  const mid = 'glm-4.7';
  const notVisible = selectCandidates('agent', new Set(['deepseek-v4-pro']),
    { catalog: cat, pinnedModel: mid });
  assert.equal(notVisible.available, false);
  assert.deepEqual(notVisible.reasons.map((x) => x.code), ['not_in_key_view']);

  const clone = JSON.parse(JSON.stringify(cat));
  clone.models[mid].tool_calling = { verified: false, _source: 'verified' };
  const toolBad = selectCandidates('agent', null, { catalog: clone, pinnedModel: mid });
  assert.equal(toolBad.available, false);
  assert.ok(toolBad.reasons.some((x) => x.code === 'tool_calling_not_verified'));
  assert.equal(toolBad.checks.tool_calling, false);

  // 带图片但该模型未声明看图能力（用 clone 显式置 false 以避免巧合）
  clone.models[mid].capabilities.vision = false;
  if (clone.models[mid].capabilities.multimodal) clone.models[mid].capabilities.multimodal.vision = false;
  const img = selectCandidates('vision', null, { catalog: clone, pinnedModel: mid, hasImage: true });
  assert.equal(img.available, false);
  assert.ok(img.reasons.some((x) => x.code === 'vision_unsupported'));
  assert.equal(img.checks.vision, false);
});

test('selectCandidates.pinnedModel: 未指定模型的目录项 → not_in_catalog', () => {
  const v = selectCandidates('agent', null, { catalog: cat, pinnedModel: 'no-such-model' });
  assert.equal(v.available, false);
  assert.deepEqual(v.reasons.map((x) => x.code), ['not_in_catalog']);
});

test('selectCandidates: auto_policy 未覆盖的用途按 routing.recommended 兜底', () => {
  // clone 一个 catalog，给若干模型加自定义标签（不污染真实 catalog）
  const clone = JSON.parse(JSON.stringify(cat));
  const ids = ['qwen3.8-flash', 'glm-4.7'];
  for (const id of ids) clone.models[id].routing = { recommended: ['north_star_ab_test'] };
  const v = selectCandidates('north_star_ab_test', null, { catalog: clone, detail: true });
  assert.equal(v.matched_by, 'routing.recommended');
  assert.equal(v.reason, undefined);
  assert.deepEqual(v.candidates.map((x) => x.id), ids);
  // 真实 catalog 里这个用途既不在 auto_policy 也无标签 → 仍返回 unknown_purpose
  const none = selectCandidates('north_star_ab_test', null, { catalog: cat, detail: true });
  assert.equal(none.reason, 'unknown_purpose');
  assert.equal(none.matched_by, null);
});

test('retryDecision.pinned: 429/5xx/超时只重试同一模型，耗尽后停止不换模型', () => {
  const mk = (status, code, attempt) => ({ status, body: { error: { code } }, attempt, pinned: true, model: 'glm-4.7' });
  for (const [status, code] of [[429, 'model_rate_limited'], [502, 'upstream_5xx'], [500, 'do_request_failed']]) {
    const r = retryDecision(mk(status, code, 1), { catalog: cat, maxRetries: 3 });
    assert.equal(r.action, 'retry', `status=${status} 应重试`);
    assert.equal(r.reason_code, 'pinned_retry_same_model');
    assert.equal(r.switch_model, false);
    const exhausted = retryDecision(mk(status, code, 4), { catalog: cat, maxRetries: 3 });
    assert.equal(exhausted.action, 'stop');
    assert.equal(exhausted.reason_code, 'pinned_retry_exhausted');
    assert.equal(exhausted.switch_model, false);
  }
});

test('retryDecision.pinned: 不可用/无权限/参数不支持 → user_choice，不自动换', () => {
  const cases = [
    [403, 'no_permission'],
    [400, 'unsupported_parameter'],
    [418, 'brand_new_error'], // unmapped
  ];
  for (const [status, code] of cases) {
    const r = retryDecision({ status, body: { error: { code } }, pinned: true, model: 'glm-4.7' }, { catalog: cat });
    assert.equal(r.action, 'user_choice', `status=${status} code=${code}`);
    assert.equal(r.reason_code, 'pinned_user_choice');
    assert.equal(r.needs_user_choice, true);
    assert.equal(r.switch_model, false);
  }
  // AUTO 对照：同样输入走 switch
  const auto = retryDecision({ status: 403, body: { error: { code: 'no_permission' } } }, { catalog: cat });
  assert.equal(auto.action, 'switch');
});

test('retryDecision.pinned: 带图不能看图 → 发送前 unsupported；AUTO 自动换', () => {
  const input = { status: 0, pinned: true, model: 'glm-4.7', inputModalities: ['text', 'image'] };
  const r = retryDecision(input, { catalog: cat });
  assert.equal(r.action, 'unsupported');
  assert.equal(r.reason_code, 'vision_unsupported');
  assert.equal(r.before_send, true);
  assert.equal(r.needs_user_choice, true);
  const auto = retryDecision({ status: 0, model: 'glm-4.7', inputModalities: ['text', 'image'] }, { catalog: cat });
  assert.equal(auto.action, 'switch');
  assert.equal(auto.reason_code, 'vision_unsupported_auto_switch');
});

test('retryDecision.pinned: 工具调用实测 null → 照常用并标 untested；401/402 仍停止', () => {
  const clone = JSON.parse(JSON.stringify(cat));
  clone.models['glm-4.7'].tool_calling = { verified: null, _source: 'verified' };
  const r = retryDecision({ status: 429, body: { error: { code: 'model_rate_limited' } },
    pinned: true, model: 'glm-4.7' }, { catalog: clone });
  assert.equal(r.action, 'retry');
  assert.deepEqual(r.untested, ['tool_calling']);
  assert.equal(retryDecision({ status: 401, body: { error: { code: 'token_invalid' } },
    pinned: true, model: 'glm-4.7' }, { catalog: cat }).action, 'stop');
  assert.equal(retryDecision({ status: 402, body: { error: { code: 'pre_consume_token_quota_failed' } },
    pinned: true, model: 'glm-4.7' }, { catalog: cat }).action, 'stop');
});

// ---- 北极星 Codex 复核（0.8.3）A1-A4 ----

test('A1: maxRetries=0 是合法值不被默认 3 覆盖；可重试错误第一次就返回 switch', () => {
  for (const input of [
    { status: 503 },
    { status: 429, body: { message: 'rate limited' } },
    { status: 500, body: { error: { message: 'upstream' } } },
  ]) {
    const r = retryDecision(input, { catalog: cat, maxRetries: 0 });
    assert.equal(r.max_retries, 0, 'maxRetries:0 必须原样保留');
    assert.notEqual(r.action, 'retry', 'maxRetries:0 时绝不返回 retry');
    assert.equal(r.action, 'switch', `maxRetries:0 应直接换模型：${JSON.stringify(input)}`);
  }
  // 不传时仍是默认 3
  assert.equal(retryDecision({ status: 503 }, { catalog: cat }).max_retries, 3);
  // 非法值回退 3
  assert.equal(retryDecision({ status: 503 }, { catalog: cat, maxRetries: 'abc' }).max_retries, 3);
});

test('A2: 受理状态未知（status 0 / undefined / ETIMEDOUT）→ stop + acceptance_unknown，绝不 retry/switch', () => {
  for (const input of [
    { status: 0 },
    {},
    { status: undefined },
    { error: { code: 'ETIMEDOUT' } },
    { error: 'socket hang up' },
    { status: null },
  ]) {
    const r = retryDecision(input, { catalog: cat });
    assert.equal(r.action, 'stop', `无状态码应 stop：${JSON.stringify(input)}`);
    assert.equal(r.reason_code, 'acceptance_unknown');
    assert.equal(r.accepted, 'unknown');
    assert.equal(r.retryable, false);
    assert.equal(r.switch_model, false, '受理未知绝不返回换模型重发');
  }
  // pinned 同样先走受理未知（不重试也不换）
  const p = retryDecision({ status: 0, pinned: true, model: 'glm-4.7' }, { catalog: cat });
  assert.equal(p.action, 'stop');
  assert.equal(p.reason_code, 'acceptance_unknown');
});

test('A3: 400 context_length_exceeded → compact，不原样重试', () => {
  const body = { error: { code: 'context_length_exceeded', message: 'too long' } };
  const r = retryDecision({ status: 400, body }, { catalog: cat });
  assert.equal(r.category, 'context_too_long');
  assert.equal(r.action, 'compact');
  assert.equal(r.retryable, false, '上下文超长禁止原样重试');
  assert.equal(r.switch_model, false);
  assert.equal(r.reason_code, 'context_too_long');
  // pinned 也一样：compact 优先于 user_choice
  const p = retryDecision({ status: 400, body, pinned: true, model: 'glm-4.7' }, { catalog: cat });
  assert.equal(p.action, 'compact');
});

// ---- D2：2xx 已受理优先于上下文超长；2xx 一律不重放 ----

test('D2: HTTP 200 + context_length_exceeded（含 pinned）→ none，绝不 compact/retry/switch', () => {
  const body = { error: { code: 'context_length_exceeded', message: 'midstream too long' } };
  // 复现 1：无 pinned
  const r = retryDecision({ status: 200, body }, { catalog: cat });
  assert.equal(r.action, 'none', '2xx 已受理优先，不因 body 的上下文超长而 compact');
  assert.equal(r.retryable, false);
  assert.equal(r.switch_model, false);
  assert.equal(r.settled, true);
  assert.equal(r.reason_code, null, 'none 不挂 context_too_long reason_code');
  // 复现 2：加 pinned + model 仍一样
  const p = retryDecision({ status: 200, body, pinned: true, model: 'glm-4.7' }, { catalog: cat });
  assert.equal(p.action, 'none', '2xx + pinned + 上下文超长也必须是 none');
  assert.equal(p.retryable, false);
  assert.equal(p.switch_model, false);
  assert.equal(p.pinned, true);
});

test('D2: 非 2xx 的上下文超长仍 compact（400 未受影响）', () => {
  const body = { error: { code: 'context_length_exceeded' } };
  for (const pinned of [false, true]) {
    const r = retryDecision({ status: 400, body, pinned, model: pinned ? 'glm-4.7' : undefined }, { catalog: cat });
    assert.equal(r.action, 'compact', `400 + 上下文超长（pinned=${pinned}）仍 compact`);
    assert.equal(r.category, 'context_too_long');
    assert.equal(r.reason_code, 'context_too_long');
  }
});

test('D2: 组合矩阵 —— 2xx / 受理未知 / 上下文超长 / pinned 两两组合都符合优先级', () => {
  const tooLong = { error: { code: 'context_length_exceeded' } };
  const p = { pinned: true, model: 'glm-4.7' };
  // 优先级：发送前拦截 > 受理未知 > 2xx > 上下文超长 > 其余
  const cases = [
    // 2xx × 上下文超长 × pinned：永远是 none
    { name: '200 + 超长', input: { status: 200, body: tooLong }, action: 'none', rc: null },
    { name: '200 + 超长 + pinned', input: { status: 200, body: tooLong, ...p }, action: 'none', rc: null },
    { name: '201 + 超长', input: { status: 201, body: tooLong }, action: 'none', rc: null },
    { name: '200 普通 + pinned', input: { status: 200, body: { id: 'x' }, ...p }, action: 'none', rc: null },
    // 受理未知 × 上下文超长 × pinned：受理未知优先 → stop
    { name: '无状态 + 超长', input: { status: 0, body: tooLong }, action: 'stop', rc: 'acceptance_unknown' },
    { name: '无状态 + 超长 + pinned', input: { status: 0, body: tooLong, ...p }, action: 'stop', rc: 'acceptance_unknown' },
    // 2xx × 受理未知：无有效状态码就是受理未知，即便 body 像成功
    { name: '无状态 + 成功体', input: { status: 0, body: { id: 'x' } }, action: 'stop', rc: 'acceptance_unknown' },
    // 非 2xx × 上下文超长 × pinned：compact
    { name: '400 + 超长', input: { status: 400, body: tooLong }, action: 'compact', rc: 'context_too_long' },
    { name: '400 + 超长 + pinned', input: { status: 400, body: tooLong, ...p }, action: 'compact', rc: 'context_too_long' },
    { name: '422 + 超长', input: { status: 422, body: tooLong }, action: 'compact', rc: 'context_too_long' },
  ];
  for (const c of cases) {
    const r = retryDecision(c.input, { catalog: cat });
    assert.equal(r.action, c.action, `${c.name}: action`);
    assert.equal(r.reason_code, c.rc, `${c.name}: reason_code`);
    if (c.action === 'none') {
      assert.equal(r.retryable, false, `${c.name}: none 不重放`);
      assert.equal(r.switch_model, false, `${c.name}: none 不换模型`);
    }
    if (c.rc === 'acceptance_unknown') {
      assert.equal(r.accepted, 'unknown', `${c.name}: 受理未知`);
    }
  }
});

test('A4: 搜索用途只有 works===true 且 returns_sources===true 才是候选；null 进 pending_verification', () => {
  const d = selectCandidates('search', null, { catalog: cat, detail: true });
  // 当前 3 个搜索候选的 works / returns_sources 全是 null → candidates 为空、pending 3 个
  assert.equal(d.candidates.length, 0, '全 null 时搜索候选必须为空，不能把未实测模型当可用');
  assert.equal(d.pending_verification.length, 3);
  for (const p of d.pending_verification) {
    assert.equal(p.reason, 'pending_verification');
    assert.equal(p.works, null);
    assert.equal(p.returns_sources, null);
  }
  // 非 detail 模式返回空数组（向后兼容）
  assert.deepEqual(selectCandidates('search', null, { catalog: cat }), []);
  // 用注入 catalog 验证 works/returns_sources 都为 true 才进 candidates
  const fake = JSON.parse(JSON.stringify(cat));
  for (const id of fake.auto_policy.purposes.search) {
    fake.models[id].web_search_detail.works = true;
    fake.models[id].web_search_detail.returns_sources = true;
  }
  const ok = selectCandidates('search', null, { catalog: fake, detail: true });
  assert.equal(ok.candidates.length, 3);
  assert.equal(ok.pending_verification.length, 0);
  // 一个 false 一个 true → 既不在候选也不进 pending（被 skip 规则判死）
  fake.models[fake.auto_policy.purposes.search[0]].web_search_detail.works = false;
  const mixed = selectCandidates('search', null, { catalog: fake, detail: true });
  assert.equal(mixed.candidates.length, 2);
  assert.equal(mixed.pending_verification.length, 0);
  assert.ok(mixed.skipped.some((s) => s.reason === 'web_search_unusable'));
});
