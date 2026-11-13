'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  AccountClient, normalizeResult, classifyHttpStatus,
  ACCOUNT_BASE_DEFAULT, GENERATION_BASE_DEFAULT,
} = require('../account-client.js');

function fakeFetch(status, payload, capture) {
  return async (url, init) => {
    if (capture) capture.push({ url, init });
    return {
      status,
      text: async () => (typeof payload === 'string' ? payload : JSON.stringify(payload)),
    };
  };
}

const OK = { code: 200, data: { quota: 80 }, msg: 'success' };

test('normalizeResult: 业务判断以响应体 code 为准', () => {
  const sneaky = normalizeResult({ code: 500, msg: 'token not found' }, 200);
  assert.equal(sneaky.ok, false, 'HTTP 200 + code=500 不能当成功');
  assert.equal(sneaky.error.category, 'business_error');
  assert.equal(sneaky.error.http_status, 200);
  const good = normalizeResult(OK, 200);
  assert.equal(good.ok, true);
  assert.deepEqual(good.data, { quota: 80 });
  assert.equal(good.is_cached, false);
  assert.ok(good.data_as_of);
});

test('classifyHttpStatus: 401/403/404/429/5xx 各自归类', () => {
  assert.equal(classifyHttpStatus(401), 'auth_invalid');
  assert.equal(classifyHttpStatus(403), 'forbidden');
  assert.equal(classifyHttpStatus(404), 'not_found');
  assert.equal(classifyHttpStatus(429), 'rate_limited');
  assert.equal(classifyHttpStatus(500), 'upstream_error');
  assert.equal(classifyHttpStatus(400), 'bad_request');
  assert.equal(classifyHttpStatus(200), null);
});

test('不保存凭证：缺省 token 直接返回 credentials_required，且不发起请求', async () => {
  let called = 0;
  const c = new AccountClient({ fetchImpl: async () => { called += 1; return fakeFetch(200, OK)(); } });
  const r = await c.getBalance();
  assert.equal(r.ok, false);
  assert.equal(r.error.category, 'credentials_required');
  assert.equal(called, 0);
  assert.ok(r.error.message.includes('pt_live_'));
});

test('getBalance: 裸 pt_live_ 令牌（不带 Bearer）+ 数字 key_id', async () => {
  const calls = [];
  const c = new AccountClient({ token: 'pt_live_TEST_ONLY', keyId: 123456, fetchImpl: fakeFetch(200, OK, calls) });
  const r = await c.getBalance();
  assert.equal(r.ok, true);
  assert.equal(calls[0].init.headers.Authorization, 'pt_live_TEST_ONLY');
  assert.ok(!calls[0].init.headers.Authorization.startsWith('Bearer '));
  assert.ok(calls[0].url.startsWith(ACCOUNT_BASE_DEFAULT));
  assert.ok(calls[0].url.includes('key_id=123456'));
  assert.ok(calls[0].url.includes('/v1/billing/balance'));
});

test('getBalance: key_id 缺失 / 脱敏串一律本地拒绝', async () => {
  for (const bad of [null, undefined, 'sk-****abcd']) {
    const c = new AccountClient({ token: 'pt_live_TEST_ONLY', keyId: bad, fetchImpl: fakeFetch(200, OK) });
    const r = await c.getBalance();
    assert.equal(r.ok, false);
    assert.equal(r.error.category, 'invalid_key_id');
  }
});

test('listUsageRecords: key_id 与起止日期都必填', async () => {
  const calls = [];
  const c = new AccountClient({ token: 'pt_live_TEST_ONLY', keyId: 123456, fetchImpl: fakeFetch(200, OK, calls) });
  assert.equal((await c.listUsageRecords({ startDate: '2026-11-01' })).error.category, 'missing_date_range');
  assert.equal(calls.length, 0);
  const r = await c.listUsageRecords({ startDate: '2026-11-01', endDate: '2026-11-13' });
  assert.equal(r.ok, true);
  assert.ok(calls[0].url.includes('start_date=2026-11-01'));
  assert.ok(calls[0].url.includes('end_date=2026-11-13'));
});

test('getUsageRecord: 必须带 request_id / task_id / consume_id 之一（不是列表接口）', async () => {
  const calls = [];
  const c = new AccountClient({ token: 'pt_live_TEST_ONLY', keyId: 123456, fetchImpl: fakeFetch(200, OK, calls) });
  const bad = await c.getUsageRecord({ limit: 10, page: 1 });
  assert.equal(bad.error.category, 'missing_record_id');
  assert.equal(calls.length, 0);
  await c.getUsageRecord({ requestId: 'req-123' });
  assert.ok(calls[0].url.includes('request_id=req-123'));
  assert.ok(!calls[0].url.includes('limit='));
});

test('estimateCost: 对话模型本地拦截；其余发整包 {model,endpoint,key_id,request}', async () => {
  const calls = [];
  const c = new AccountClient({
    token: 'pt_live_TEST_ONLY', keyId: 123456, fetchImpl: fakeFetch(200, OK, calls),
    catalog: { models: { 'glm-4.7': { kind: 'chat' }, 'image-01': { kind: 'image' } } },
  });
  const chat = await c.estimateCost({ model: 'glm-4.7', endpoint: 'chat', request: {} });
  assert.equal(chat.error.category, 'unsupported');
  assert.equal(calls.length, 0);
  const img = await c.estimateCost({ model: 'image-01', endpoint: 'image-generation', request: { n: 1 } });
  assert.equal(img.ok, true);
  const body = JSON.parse(calls[0].init.body);
  assert.deepEqual(Object.keys(body).sort(), ['endpoint', 'key_id', 'model', 'request']);
  assert.equal(body.key_id, 123456);
});

test('getKeyStatus: POST /v1/key/info，路径带 /v1 且 body 是数字', async () => {
  const calls = [];
  const c = new AccountClient({ token: 'pt_live_TEST_ONLY', keyId: 123456, fetchImpl: fakeFetch(200, OK, calls) });
  await c.getKeyStatus();
  assert.equal(calls[0].init.method, 'POST');
  assert.ok(calls[0].url.includes('/v1/key/info'));
  assert.equal(JSON.parse(calls[0].init.body).key_id, 123456);
});

test('getKeyModels: 生成域用 Bearer 模型 Key，两域凭证不混用', async () => {
  const calls = [];
  const c = new AccountClient({ token: 'pt_live_TEST_ONLY', keyId: 123456, fetchImpl: fakeFetch(200, OK, calls) });
  await c.getKeyModels('sk-model-key');
  assert.ok(calls[0].url.startsWith(GENERATION_BASE_DEFAULT));
  assert.equal(calls[0].init.headers.Authorization, 'Bearer sk-model-key');
});

test('网络异常 / 超时归类，且错误信息不带出令牌', async () => {
  const c = new AccountClient({
    token: 'pt_live_SECRET_SHOULD_NOT_LEAK', keyId: 123456,
    fetchImpl: async () => { const e = new Error('boom'); e.name = 'AbortError'; throw e; },
  });
  const r = await c.getBalance();
  assert.equal(r.ok, false);
  assert.equal(r.error.category, 'timeout');
  assert.ok(!JSON.stringify(r).includes('pt_live_SECRET_SHOULD_NOT_LEAK'));
});

test('fromEnv 用环境变量注入，不读配置文件', () => {
  const c = AccountClient.fromEnv({ PT_ACCESS_TOKEN: 'pt_live_ENV', PT_KEY_ID: '123456' },
    { fetchImpl: fakeFetch(200, OK) });
  assert.equal(c.token, 'pt_live_ENV');
  assert.equal(c._numericKeyId, 123456);
});
