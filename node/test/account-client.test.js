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

test('normalizeResult: 业务判断以响应体 code 为准；不编造 data_as_of/is_cached', () => {
  const sneaky = normalizeResult({ code: 500, msg: 'token not found' }, 200);
  assert.equal(sneaky.ok, false, 'HTTP 200 + code=500 不能当成功');
  assert.equal(sneaky.error.category, 'business_error');
  assert.equal(sneaky.error.http_status, 200);
  const good = normalizeResult(OK, 200);
  assert.equal(good.ok, true);
  assert.deepEqual(good.data, { quota: 80 });
  // B2：PT 没返回服务端时间与缓存状态时为 null，不编造
  assert.equal(good.is_cached, null);
  assert.equal(good.data_as_of, null);
});

test('B2: 响应里带服务端统计时间 / 缓存状态时原样透出（顶层或 data 内）', () => {
  const top = normalizeResult({ code: 200, data: { quota: 80 }, server_time: '2026-11-16T04:00:00Z' }, 200);
  assert.equal(top.data_as_of, '2026-11-16T04:00:00Z');
  const inData = normalizeResult({ code: 200, data: { quota: 80, updated_at: '2026-11-15T00:00:00Z' } }, 200);
  assert.equal(inData.data_as_of, '2026-11-15T00:00:00Z');
  assert.equal(inData.is_cached, null);
  const cached = normalizeResult({ code: 200, data: { cached: true } }, 200);
  assert.equal(cached.is_cached, true);
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

// ---- B1：严格响应校验（HTML / 空串 / 截断 JSON / 缺 code 字段） ----

test('B1: HTTP 200 的 HTML / 空串 / 截断 JSON 都返回 invalid_response，不当成功', async () => {
  const bodies = [
    '<html><body>gateway error</body></html>',
    '',
    '{"code":200,"data":{"quota":',
    'not json at all',
  ];
  for (const b of bodies) {
    const c = new AccountClient({ token: 'pt_live_TEST_ONLY', keyId: 123456, fetchImpl: fakeFetch(200, b) });
    const r = await c.getBalance();
    assert.equal(r.ok, false, `body=${JSON.stringify(b.slice(0, 30))} 不能当成功`);
    assert.equal(r.error.category, 'invalid_response');
    assert.equal(r.http_status, 200);
  }
});

test('B1: 200 响应是 JSON 但缺少 PT code 字段 → invalid_response；生成域 /v1/models 不要求 code', async () => {
  const acct = new AccountClient({ token: 'pt_live_TEST_ONLY', keyId: 123456, fetchImpl: fakeFetch(200, { data: { some: 'field' } }) });
  const r1 = await acct.getBalance();
  assert.equal(r1.ok, false);
  assert.equal(r1.error.category, 'invalid_response');
  // 生成域：OpenAI 风格 {object, data:[...]} 无 code，合法 JSON 对象即可
  const gen = new AccountClient({ fetchImpl: fakeFetch(200, { object: 'list', data: [{ id: 'wan3.0-video' }] }) });
  const r2 = await gen.getKeyModels('sk-model-key');
  assert.equal(r2.ok, true);
  assert.deepEqual(r2.data, [{ id: 'wan3.0-video' }]);
});

// ---- D1：code 必须明确是成功值，畸形 code 不按 HTTP 兜底 ----

test('D1: normalizeResult 账户域 code=null/空串/abc/缺失 + HTTP 200 → ok:false invalid_response', () => {
  // 复现输入：code:null + 200 原来会退回 HTTP 判成 ok:true，必须修成 invalid_response
  const cases = [
    { name: 'code=null', raw: { code: null, data: {} } },
    { name: "code=''", raw: { code: '', data: {} } },
    { name: "code='abc'", raw: { code: 'abc', data: {} } },
    { name: 'code 缺失', raw: { data: {} } },
  ];
  for (const { name, raw } of cases) {
    const r = normalizeResult(raw, 200);
    assert.equal(r.ok, false, `${name} 即使 HTTP 200 也不能当成功`);
    assert.equal(r.error.category, 'invalid_response', `${name} 应归 invalid_response`);
    assert.equal(r.error.http_status, 200);
    assert.equal(r.data, null);
  }
  // 明确的成功值仍要通过（0.8.3 既有语义不变）
  for (const c of [200, '200', 0, '0']) {
    const r = normalizeResult({ code: c, data: { quota: 80 } }, 200);
    assert.equal(r.ok, true, `code=${JSON.stringify(c)} 是成功值`);
    assert.deepEqual(r.data, { quota: 80 });
  }
});

test('D1: 明确的业务失败码（如 500/字符串500）仍归 business_error，不误伤', () => {
  for (const c of [500, '500', 402]) {
    const r = normalizeResult({ code: c, msg: 'token not found' }, 200);
    assert.equal(r.ok, false, `code=${c} 是失败`);
    assert.equal(r.error.category, 'business_error', `code=${c} 是明确业务码，不归 invalid_response`);
  }
});

test('D1: _request 集成 —— getBalance 收到 HTTP 200 + code:null/字符串abc/缺失 都返回 invalid_response', async () => {
  for (const payload of [{ code: null, data: {} }, { code: 'abc', data: {} }, { data: {} }]) {
    const c = new AccountClient({ token: 'pt_live_TEST_ONLY', keyId: 123456, fetchImpl: fakeFetch(200, payload) });
    const r = await c.getBalance();
    assert.equal(r.ok, false, `payload=${JSON.stringify(payload)} 不能当成功`);
    assert.equal(r.error.category, 'invalid_response');
  }
});

test('D1: 生成域 /v1/models 规则不变 —— 无 code 的 OpenAI 风格响应仍按 HTTP 判成功', async () => {
  const gen = new AccountClient({ fetchImpl: fakeFetch(200, { object: 'list', data: [{ id: 'm' }] }) });
  const r = await gen.getKeyModels('sk-model-key');
  assert.equal(r.ok, true, '生成域不要求 code，规则与 0.8.3 一致');
  assert.deepEqual(r.data, [{ id: 'm' }]);
});

// ---- B3：AbortSignal 与响应大小上限 ----

test('B3: 调用方预先 abort 的信号直接返回 aborted，不发请求', async () => {
  let called = 0;
  const c = new AccountClient({
    token: 'pt_live_TEST_ONLY', keyId: 123456,
    fetchImpl: async () => { called += 1; return fakeFetch(200, OK)(); },
  });
  const ac = new AbortController();
  ac.abort();
  const r = await c.getBalance({ signal: ac.signal });
  assert.equal(r.ok, false);
  assert.equal(r.error.category, 'aborted');
  assert.equal(called, 0);
});

test('B3: 请求进行中调用方 abort → 立刻结束并返回 aborted（不是 timeout）', async () => {
  const ac = new AbortController();
  const c = new AccountClient({
    token: 'pt_live_TEST_ONLY', keyId: 123456, timeout: 100000,
    fetchImpl: (url, init) => new Promise((resolve, reject) => {
      init.signal.addEventListener('abort', () => {
        const e = new Error('The operation was aborted');
        e.name = 'AbortError';
        reject(e);
      });
    }),
  });
  const p = c.getBalance({ signal: ac.signal });
  setTimeout(() => ac.abort(), 20);
  const r = await p;
  assert.equal(r.ok, false);
  assert.equal(r.error.category, 'aborted');
});

test('B3: 超过大小上限的响应（text 路径 / content-length 预检 / 流式路径）返回 response_too_large', async () => {
  // text 路径：fakeFetch 没有 body.getReader，走 resp.text()
  const big = 'x'.repeat(2048);
  const c1 = new AccountClient({
    token: 'pt_live_TEST_ONLY', keyId: 123456, maxResponseBytes: 1024,
    fetchImpl: fakeFetch(200, { code: 200, data: big }),
  });
  const r1 = await c1.getBalance();
  assert.equal(r1.ok, false);
  assert.equal(r1.error.category, 'response_too_large');

  // content-length 预检：头里声明超限，直接不读
  const c2 = new AccountClient({
    token: 'pt_live_TEST_ONLY', keyId: 123456, maxResponseBytes: 1024,
    fetchImpl: async () => ({
      status: 200,
      headers: { get: (k) => (k === 'content-length' ? '99999999' : null) },
      text: async () => { throw new Error('should not be called'); },
    }),
  });
  const r2 = await c2.getBalance();
  assert.equal(r2.error.category, 'response_too_large');

  // 流式路径：分块读，累计超限即取消
  const chunk = Buffer.alloc(700);
  const c3 = new AccountClient({
    token: 'pt_live_TEST_ONLY', keyId: 123456, maxResponseBytes: 1024,
    fetchImpl: async () => ({
      status: 200,
      body: { getReader: () => { let i = 0; return { read: async () => (i++ < 3 ? { done: false, value: chunk } : { done: true }), cancel: async () => {} }; } },
    }),
  });
  const r3 = await c3.getBalance();
  assert.equal(r3.error.category, 'response_too_large');
});

test('B3: 流式响应在上限内正常读取并按 B1 校验', async () => {
  const payload = Buffer.from(JSON.stringify(OK));
  const c = new AccountClient({
    token: 'pt_live_TEST_ONLY', keyId: 123456,
    fetchImpl: async () => ({
      status: 200,
      body: { getReader: () => { let i = 0; const chunks = [payload]; return { read: async () => (i < chunks.length ? { done: false, value: chunks[i++] } : { done: true }), cancel: async () => {} }; } },
    }),
  });
  const r = await c.getBalance();
  assert.equal(r.ok, true);
  assert.deepEqual(r.data, { quota: 80 });
});

// ==========================================================================
// 0.8.6：getUsageRecord 二级判定 —— 两种「没查到」+ 通用兜底 + 有效记录
// 全部用模拟响应，零 PT 真实调用。
// ==========================================================================

function usageClient(payload, status = 200) {
  return new AccountClient({
    token: 'pt_live_TEST_ONLY', keyId: 123456,
    fetchImpl: fakeFetch(status, payload),
  });
}

test('0.8.6-① 明确未找到：500 + data:null + msg=record not found → ok:false found:false category=record_not_found', async () => {
  const r = await usageClient({ code: 500, data: null, msg: 'record not found' }).getUsageRecord({ consumeId: 'CONS_MISSING' });
  assert.equal(r.ok, false);
  assert.equal(r.found, false);
  assert.equal(r.error.category, 'record_not_found');
  // 实测就是 HTTP 200 + body code=500；http_status 仍要如实透出
  assert.equal(r.http_status, 200);
  assert.equal(r.code, 500);
  // 大小写 / 首尾空格容错，仍归同一类
  const r2 = await usageClient({ code: 500, data: null, msg: '  Record Not Found  ' }).getUsageRecord({ requestId: 'REQ_MISSING' });
  assert.equal(r2.found, false);
  assert.equal(r2.error.category, 'record_not_found');
});

test('0.8.6-② 无效 token：500 + token not found → 保持 business_error，found:null（不归为未查到）', async () => {
  const r = await usageClient({ code: 500, data: null, msg: 'token not found' }).getUsageRecord({ consumeId: 'CONS_X' });
  assert.equal(r.ok, false);
  assert.equal(r.found, null);
  assert.equal(r.error.category, 'business_error');
  assert.equal(r.error.message, 'token not found');
});

test('0.8.6-③ 参数错：500 + Parameter error → 保持 business_error，found:null', async () => {
  const r = await usageClient({ code: 500, data: null, msg: 'Parameter error' }).getUsageRecord({ consumeId: 'CONS_X' });
  assert.equal(r.ok, false);
  assert.equal(r.found, null);
  assert.equal(r.error.category, 'business_error');
});

test('0.8.6-④ 文案不匹配：500 + 近似但不同的 msg → 不归为未查到，保持 business_error found:null', async () => {
  for (const msg of ['record is not found here', 'no record found', 'record not found!', '记录不存在']) {
    const r = await usageClient({ code: 500, data: null, msg }).getUsageRecord({ consumeId: 'CONS_X' });
    assert.equal(r.found, null, msg);
    assert.equal(r.error.category, 'business_error', msg);
  }
});

test('0.8.6-⑤ 200 空记录：code=200 但标识/模型/状态全空 → ok:false found:false category=empty_record，不当零扣费', async () => {
  const empty = { request_id: null, task_id: '', consume_id: null, model: null, model_name: '', status: null, quota: 0 };
  const r = await usageClient({ code: 200, data: empty, msg: 'success' }).getUsageRecord({ requestId: 'REQ_OLD' });
  assert.equal(r.ok, false);
  assert.equal(r.found, false);
  assert.equal(r.error.category, 'empty_record');
  assert.equal(r.data, null, '空壳不能带出当有效数据');
  assert.equal(r.code, 200, '原始 code 如实保留');
  // data:null 占位也归空壳
  const r2 = await usageClient({ code: 200, data: null, msg: 'success' }).getUsageRecord({ requestId: 'REQ_OLD2' });
  assert.equal(r2.found, false);
  assert.equal(r2.error.category, 'empty_record');
  // 与「明确未找到」用不同 category，契约中可区分
  assert.notEqual(r.error.category, 'record_not_found');
});

test('0.8.6-⑥ 有效记录：标识/状态完整，金额零 → ok:true found:true，不当空壳', async () => {
  const record = { request_id: 'req-1', task_id: 'task-1', consume_id: 'COS_1', model: 'glm-4.7', status: 'success', quota: 0 };
  const r = await usageClient({ code: 200, data: record, msg: 'success' }).getUsageRecord({ consumeId: 'COS_1' });
  assert.equal(r.ok, true);
  assert.equal(r.found, true);
  assert.equal(r.error, null);
  assert.deepEqual(r.data, record);
  assert.equal(r.data.quota, 0, '金额零但有效，不能当零扣费依据被吞掉');
});

test('0.8.6-⑦ 判定只作用于 getUsageRecord，其他接口返回不带 found 字段', async () => {
  const r = await usageClient({ code: 500, data: null, msg: 'record not found' }).getBalance();
  assert.equal(r.ok, false);
  assert.equal(r.error.category, 'business_error');
  assert.equal('found' in r, false, 'normalizeResult 语义不变，D1 不误伤');
});
