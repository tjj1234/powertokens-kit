'use strict';
// 0.8.8 媒体能力测试（新增文件，不碰 core.test.js 现有用例）
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  loadCatalog, selectCandidates, mediaRequestParams,
} = require('../index.js');

const cat = loadCatalog();

// ---------- M1：四个媒体用途 ----------
test('M1: 四个媒体用途存在且候选都在 models 中', () => {
  const purposes = cat.auto_policy.purposes;
  for (const p of ['text2image', 'image2image', 'text2video', 'image2video']) {
    assert.ok(Array.isArray(purposes[p]) && purposes[p].length >= 2, `${p} 应有 >=2 候选`);
    for (const id of purposes[p]) assert.ok(cat.models[id], `${p} 引用了不存在的 ${id}`);
  }
});

// ---------- M2：requirements 可选筛选 ----------
test('M2: 不传 requirements 时 detail 返回不含 requirements/excluded（逐键向后兼容）', () => {
  const r = selectCandidates('text2video', null, { catalog: cat, detail: true });
  assert.ok(Array.isArray(r.candidates));
  assert.equal('requirements' in r, false, '不应出现 requirements 键');
  assert.equal('excluded' in r, false, '不应出现 excluded 键');
  assert.equal('match_basis' in (r.candidates[0] || {}), false);
});

test('M2: 4 秒是合法时长（在完整枚举里），应命中候选', () => {
  const r = selectCandidates('text2video', null, {
    catalog: cat, detail: true, requirements: { duration_s: 4 },
  });
  assert.ok(r.candidates.length >= 1, 'wan3.0-video seconds 含 4，应被选中');
  assert.ok(r.candidates.some((c) => c.match_basis === 'capabilities.media'));
});

test('M2: 超范围时长进 excluded，reason=unsupported 且附 available_values', () => {
  const r = selectCandidates('text2video', null, {
    catalog: cat, detail: true, requirements: { duration_s: 999 },
  });
  assert.equal(r.requirements.duration_s, 999);
  assert.ok(r.excluded.length >= 1);
  const ex = r.excluded.find((x) => x.reason === 'requirements_unmatched');
  assert.ok(ex, '应有 requirements_unmatched 排除项');
  const du = ex.unmatched.find((u) => u.field === 'duration_s');
  assert.equal(du.reason, 'unsupported');
  assert.ok(Array.isArray(du.available) && du.available.includes(4), '可用值里应有 4');
});

test('M2: 能力未知的模型按 capability_unknown 排除，available=null', () => {
  // 构造一个无 capabilities.media 的探针模型
  const probe = JSON.parse(JSON.stringify(cat));
  probe.auto_policy = { purposes: { text2video: ['probe-no-caps', 'wan3.0-video'] } };
  probe.models['probe-no-caps'] = { kind: 'video', execution: 'async', submit: '/v1/videos' };
  const r = selectCandidates('text2video', null, {
    catalog: probe, detail: true, requirements: { duration_s: 4 },
  });
  const ex = r.excluded.find((x) => x.id === 'probe-no-caps');
  assert.ok(ex, '无能力模型应被排除');
  const du = ex.unmatched.find((u) => u.field === 'duration_s');
  assert.equal(du.reason, 'capability_unknown');
  assert.equal(du.available, null);
});

// ---------- 复核 §1：完整枚举，4/6 秒不被误判 ----------
test('复核§1: wan3.0-video durations 是完整枚举且含 -1/4/6', () => {
  const d = cat.models['wan3.0-video'].capabilities.media.durations;
  assert.deepEqual(d.value, [-1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
    16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30]);
  assert.equal(d.auto_value, -1);
  for (const s of [4, 6]) {
    const r = selectCandidates('text2video', null, {
      catalog: cat, requirements: { duration_s: s },
    });
    assert.ok(r.some((c) => c.id === 'wan3.0-video'), `${s} 秒应命中 wan3.0-video`);
  }
});

// ---------- 复核 §2：新旧字段权威 + 差异标注 ----------
test('复核§2: 所有新旧字段矛盾的模型都有 supersedes_top_level 标注', () => {
  const norm = (x) => String(x).toLowerCase().replace(/p$/, '').trim();
  for (const [id, e] of Object.entries(cat.models)) {
    const cm = e.capabilities && e.capabilities.media;
    if (!cm) continue;
    const newRes = (cm.resolutions || cm.sizes || {}).value || [];
    const topRes = e.resolutions || [];
    if (topRes.length && newRes.length) {
      const a = new Set(newRes.map(norm)), b = new Set(topRes.map(norm));
      if (a.size !== b.size || [...a].some((x) => !b.has(x))) {
        assert.ok(cm.supersedes_top_level, `${id} 分辨率新旧不同但缺差异标注`);
        assert.ok(cm.supersedes_top_level.fields.includes('resolutions'));
      }
    }
    const du = (cm.durations || {}).value || [];
    const mx = Math.max(...du.filter((v) => Number.isInteger(v) && v > 0), 0);
    if (e.max_duration_s && mx && e.max_duration_s !== mx) {
      assert.ok(cm.supersedes_top_level, `${id} 时长新旧不同但缺差异标注`);
      assert.ok(cm.supersedes_top_level.fields.includes('max_duration_s'));
    }
  }
});

// ---------- 复核 §3：价格 _source 完整性 ----------
test('复核§3: 所有媒体价格带 _source；verified 附日期；仅 verified 有确认卡', () => {
  for (const [id, e] of Object.entries(cat.models)) {
    const p = e.capabilities && e.capabilities.media && e.capabilities.media.price;
    if (!p) continue;
    assert.ok(['verified', 'documented'].includes(p._source), `${id} price 缺合法 _source`);
    if (p._source === 'verified') {
      assert.ok(p.verified_at, `${id} verified 应有日期`);
      assert.ok(p.confirmed_card && /实际扣费/.test(p.confirmed_card));
    } else {
      assert.equal(p.confirmed_card, undefined, `${id} documented 不应有 verified 确认卡`);
    }
  }
});

// ---------- 复核 §6：seedance 按视频类 ----------
test('复核§6: seedance 模型不改 kind，但标 family=video', () => {
  for (const id of ['seedance-1-0-pro-fast-251015', 'seedance-1-0-pro-250528']) {
    assert.equal(cat.models[id].kind, 'seedance');
    assert.equal(cat.models[id].capabilities.media.family, 'video');
  }
});

// ---------- M5：参数映射新函数 ----------
test('M5: mediaRequestParams 把友好名映射到真实字段名', () => {
  const r = mediaRequestParams('wan3.0-video', { duration_s: 4, resolution: '720P' }, { catalog: cat });
  assert.equal(r.params.seconds, 4, 'duration_s 应映射到 seconds');
  assert.equal(r.params.size || r.params.resolution, '720P');
  assert.equal(r.unsupported.length, 0);
});

test('M5: 不支持的参数进 unsupported，不静默丢', () => {
  const r = mediaRequestParams('wan3.0-video', {
    duration_s: 999, unknown_field: 'x',
  }, { catalog: cat });
  assert.equal(r.params.seconds, undefined, '超范围值不应进 params');
  assert.ok(r.unsupported.some((u) => u.field === 'duration_s' && u.reason === 'unsupported'));
  assert.ok(r.unsupported.some((u) => u.field === 'unknown_field' && u.reason === 'no_param_mapping'));
});
