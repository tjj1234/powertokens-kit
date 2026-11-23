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
test('复核§3: 所有媒体价格带 _source；verified 附日期；无 confirmed_card（0.8.9 移除）', () => {
  for (const [id, e] of Object.entries(cat.models)) {
    const p = e.capabilities && e.capabilities.media && e.capabilities.media.price;
    if (!p) continue;
    assert.ok(['verified', 'documented'].includes(p._source), `${id} price 缺合法 _source`);
    if (p._source === 'verified') {
      assert.ok(p.verified_at, `${id} verified 应有日期`);
    }
    // 0.8.9：confirmed_card 已全部移除（北极星不读，文案由接入方自定）
    assert.equal(p.confirmed_card, undefined, `${id} 不应再有 confirmed_card`);
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

// ================= 0.8.9 专项 =================
// ---------- P2-5：adaptive 单列，且语义不变（差分关键） ----------
test('0.8.9 P2-5: adaptive 从 aspect_ratios.value 移出，改用 adaptive_supported 标记', () => {
  const n = ['wan3.0-video', 'dreamina-seedance-2-0-260128', 'MiniMax-H3'];
  for (const id of n) {
    const ar = cat.models[id].capabilities.media.aspect_ratios;
    assert.ok(ar, `${id} 应有 aspect_ratios`);
    assert.equal((ar.value || []).includes('adaptive'), false, `${id} value 不应再含 adaptive`);
    assert.equal(ar.adaptive_supported, true, `${id} 应标 adaptive_supported=true`);
  }
});

test('0.8.9 P2-5: adaptive 需求仍可匹配（标记被还原成可匹配值），语义不变', () => {
  // wan3.0-video 的 aspect_ratio 取值会把 adaptive_supported 还原成 'adaptive'
  const r = selectCandidates('text2video', ['wan3.0-video'], {
    catalog: cat, detail: true, requirements: { aspect_ratio: 'adaptive' },
  });
  assert.ok(r.candidates.some((c) => c.id === 'wan3.0-video'),
    'adaptive 需求应仍能命中 wan3.0-video（升级前后语义一致）');
});

test('0.8.9 P2-5: mediaRequestParams 传 adaptive 不被判 unsupported', () => {
  const r = mediaRequestParams('wan3.0-video', { aspect_ratio: 'adaptive' }, { catalog: cat });
  assert.ok(!r.unsupported.some((u) => u.field === 'aspect_ratio'),
    'adaptive 应被接受，不应进 unsupported');
});

// ---------- P2-4：分辨率全小写 ----------
test('0.8.9 P2-4: capabilities.media.resolutions.value 全部小写', () => {
  for (const [id, e] of Object.entries(cat.models)) {
    const rs = e.capabilities && e.capabilities.media && e.capabilities.media.resolutions;
    if (!rs || !Array.isArray(rs.value)) continue;
    for (const v of rs.value) {
      assert.equal(v, String(v).toLowerCase(), `${id} 分辨率 ${v} 未小写`);
    }
  }
});

test('0.8.9 P2-4: 传大写分辨率仍可匹配（_normMediaVal 大小写不敏感，行为不变）', () => {
  const r = selectCandidates('text2video', ['wan3.0-video'], {
    catalog: cat, detail: true, requirements: { resolution: '720P' },
  });
  assert.ok(r.candidates.some((c) => c.id === 'wan3.0-video'), '大写 720P 仍应命中');
});

// ---------- P1：seedream 比例 / pixel_map / reference_image ----------
test('0.8.9 P1: seedream-5-0 比例沿用顶层 8 个，其余 3 个留 null + source', () => {
  const s5 = cat.models['seedream-5-0-260128'].capabilities.media.aspect_ratios;
  assert.deepEqual(s5.value, ['1:1', '16:9', '4:3', '3:2', '2:3', '3:4', '9:16', '21:9']);
  assert.equal(s5._source, 'documented');
  for (const id of ['seedream-4-0-250828', 'seedream-4-5-251128', 'dola-seedream-5-0-pro-260628']) {
    const ar = cat.models[id].capabilities.media.aspect_ratios;
    assert.equal(ar.value, null, `${id} 无来源应留 null`);
  }
});

test('0.8.9 P1: 图片模型 pixel_map 有 _source；image-01 有精确像素 documented', () => {
  const im = cat.models['image-01'].capabilities.media.pixel_map;
  assert.equal(im._source, 'documented');
  assert.equal(im.basis['1K']['16:9'], '1280x720');
  const w = cat.models['wan2.7-image-pro'].capabilities.media.pixel_map;
  assert.equal(w._source, 'kit_derived', 'kit 自定档位换算应标 kit_derived');
  const sd = cat.models['seedream-4-5-251128'].capabilities.media.pixel_map;
  assert.equal(sd.basis, null, '无来源留 null');
});

test('0.8.9 P1: reference_image 补全，无来源字段留 null；wan2.7-image-pro 有 20MB', () => {
  const w = cat.models['wan2.7-image-pro'].capabilities.media.reference_image;
  assert.equal(w.max_file_size_mb, 20);
  assert.equal(w.max_reference_images, 9);
  assert.equal(w._source, 'documented');
  const sd = cat.models['seedream-5-0-260128'].capabilities.media.reference_image;
  assert.equal(sd.max_file_size_mb, null, '无来源留 null');
  assert.ok('input' in sd && 'formats' in sd, '空壳应补全字段名');
});

// ---------- P2-6 / 删 confirmed_card / P0 读取规则 ----------
test('0.8.9 P2-6: duration_rule 补 duration_step_s，无来源留 null', () => {
  let n = 0;
  for (const [id, e] of Object.entries(cat.models)) {
    const rule = e.capabilities && e.capabilities.media && e.capabilities.media.duration_rule;
    if (!rule) continue;
    n++;
    assert.ok('duration_step_s' in rule, `${id} 应补 duration_step_s`);
    assert.equal(rule.duration_step_s, null, `${id} 无步长依据应留 null`);
  }
  assert.ok(n >= 8, '应有 8 个 duration_rule');
});

test('0.8.9: catalog 顶层 note 含 fallback 读取规则', () => {
  assert.ok(cat.note.some((x) => String(x).includes('读取规则') && String(x).includes('沿用')),
    '顶层 note 应写明 fallback 读取规则');
});

