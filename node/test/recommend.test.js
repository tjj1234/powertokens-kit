'use strict';
// 0.9.0 C01 按需求推荐测试（新增文件，不碰 core.test.js / media.test.js 现有用例）
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  loadCatalog, selectCandidates, recommendModels, parseRecommendIntent,
} = require('../index.js');

const cat = loadCatalog();

// ---------- 自然语言解析（纯规则）----------
test('C01 parse: 用途关键词识别', () => {
  assert.equal(parseRecommendIntent('来个视频').purpose, 'text2video');
  assert.equal(parseRecommendIntent('生成一张海报').purpose, 'text2image');
  assert.equal(parseRecommendIntent('long report 写报告').purpose, 'long_report');
  assert.equal(parseRecommendIntent('随便聊聊').purpose, 'general');   // 兜底
});

test('C01 parse: 单一策略识别', () => {
  assert.equal(parseRecommendIntent('便宜的视频').strategy, 'cost');
  assert.equal(parseRecommendIntent('cheap video').strategy, 'cost');
  assert.equal(parseRecommendIntent('效果最好的图').strategy, 'quality');
  assert.equal(parseRecommendIntent('realistic image').strategy, 'quality');
});

test('C01 parse: 混合信号 -> balanced 且保留 detected_strategies', () => {
  const p = parseRecommendIntent('真实好看又便宜的产品视频');
  assert.equal(p.strategy, 'balanced');
  assert.equal(p.strategy_from, 'intent_mixed');
  assert.ok(p.detected_strategies.includes('quality'));
  assert.ok(p.detected_strategies.includes('cost'));
});

test('C01 parse: opts 显式指定优先于关键词', () => {
  const p = parseRecommendIntent('便宜的视频', { purpose: 'text2image', strategy: 'quality' });
  assert.equal(p.purpose, 'text2image');
  assert.equal(p.strategy, 'quality');
  assert.equal(p.purpose_from, 'option');
});

// ---------- 三档输出结构 ----------
test('C01: 三档齐全，recommended 按 strategy 指向', () => {
  const r = recommendModels('便宜的视频', null, { catalog: cat });
  assert.deepEqual(Object.keys(r.tiers).sort(), ['balanced', 'cost', 'quality']);
  assert.equal(r.strategy, 'cost');
  assert.equal(r.recommended, r.tiers.cost);
  for (const t of Object.values(r.tiers)) {
    assert.ok(Array.isArray(t.models) && t.models.length >= 1);
    assert.ok(typeof t.basis === 'string' && t.basis.length > 0);
  }
});

test('C01: 每档模型带可审计 signals（能力覆盖度/价格来源）', () => {
  const r = recommendModels('视频', null, { catalog: cat });
  const m = r.tiers.quality.models[0];
  assert.ok('capability_count' in m.signals);
  assert.ok(Array.isArray(m.signals.capability_breadth));
  assert.ok('price' in m.signals);
  if (m.signals.price) {
    assert.ok(['documented', 'verified'].includes(m.signals.price.source));
  }
  assert.equal(typeof m.catalog_position, 'number');
  assert.equal(m.rank, 1);
});

// ---------- 复核 0.9.2 #4：创作交付物优先于描述词 ----------
test('C01 review 092: 创作动作不被泛词「视觉/图片内容」误判为看图', () => {
  assert.equal(parseRecommendIntent('根据图片内容生成一段视频').purpose, 'text2video');
  assert.equal(parseRecommendIntent('给我生成一张视觉冲击力强的海报').purpose, 'text2image');
  // 真正的看图/理解仍归 vision
  assert.equal(parseRecommendIntent('请看图，告诉我图片中写了什么').purpose, 'vision');
  assert.equal(parseRecommendIntent('视觉理解：分析这张图片中的账单').purpose, 'vision');
  const r = recommendModels('根据图片内容生成一段视频', null, { catalog: cat });
  assert.equal(r.purpose, 'text2video');
});

// ---------- 复核 0.9.3 #1：历轮正反例合并成一张表，防止「修 A 坏 B」 ----------
// 每行 [输入, 期望 purpose, 首次出现的轮次]；改判定逻辑必须整表重跑。
const INTENT_REGRESSION_TABLE = [
  // 0.9.0 既有
  ['来个视频', 'text2video', '0.9.0'],
  ['生成一张海报', 'text2image', '0.9.0'],
  ['long report 写报告', 'long_report', '0.9.0'],
  ['随便聊聊', 'general', '0.9.0'],
  // 0.9.1 看图/识字（generation 不得劫持理解）
  ['请看图，告诉我图片中写了什么', 'vision', '0.9.1'],
  ['视觉理解：分析这张图片中的账单', 'vision', '0.9.1'],
  ['portrait photo of a woman', 'text2image', '0.9.1'],
  // 0.9.2 创作不被泛词误伤
  ['根据图片内容生成一段视频', 'text2video', '0.9.2'],
  ['给我生成一张视觉冲击力强的海报', 'text2image', '0.9.2'],
  // 0.9.3 扩大复核新暴露的三条回归
  ['分析图片中的画面', 'vision', '0.9.3'],
  ['请看图，告诉我海报上写了什么', 'vision', '0.9.3'],
  ['generate an image of a cat', 'text2image', '0.9.3'],
  // 0.9.3 补的中英文常用说法
  ['分析这张图片', 'vision', '0.9.3'],
  ['识别图片里的文字', 'vision', '0.9.3'],
  ['describe the image', 'vision', '0.9.3'],
  ['generate a video about cats', 'text2video', '0.9.3'],
  ['generate an image of a sunset', 'text2image', '0.9.3'],
  ['做一段视频', 'text2video', '0.9.3'],
];
test('C01 review 093: 历轮意图正反例全表（动作词优先，名词中性）', () => {
  for (const [q, want, round] of INTENT_REGRESSION_TABLE) {
    assert.equal(parseRecommendIntent(q).purpose, want, `[${round}] ${q}`);
  }
});

// ---------- 复核 0.9.3 #5：pinned 模式不再返回空候选 ----------
test('C01 review 093: pinnedModel 可用时三档只含该模型并保留 pinned 元信息', () => {
  for (const km of [null, ['image-01']]) {
    const r = recommendModels('低成本生成一张图片', km, { catalog: cat, pinnedModel: 'image-01' });
    assert.equal(r.pinned.pinned, 'image-01');
    assert.equal(r.pinned.available, true);
    for (const t of Object.values(r.tiers)) {
      assert.equal(t.models.length, 1);
      assert.equal(t.models[0].id, 'image-01');
    }
    assert.equal(r.recommended.models[0].id, 'image-01');
  }
});

test('C01 review 093: pinnedModel 不可用时明确给出原因，不伪装成无候选', () => {
  const r = recommendModels('低成本生成一张图片', null, { catalog: cat, pinnedModel: '__nope__' });
  assert.equal(r.pinned.available, false);
  assert.ok(r.pinned.reasons.length >= 1);
  for (const t of Object.values(r.tiers)) assert.equal(t.models.length, 0);
  assert.match(r.note, /不可用/);
});

// ---------- R4：不猜、只用 documented/verified ----------
test('C01 cost: 合规价格升序，无价格者排末尾', () => {
  const r = recommendModels('视频', null, { catalog: cat });
  const models = r.tiers.cost.models;
  const priced = models.filter((m) => m.signals.price);
  const unpriced = models.filter((m) => !m.signals.price);
  // 有价格的在前
  if (priced.length && unpriced.length) {
    assert.ok(models.indexOf(priced[priced.length - 1]) < models.indexOf(unpriced[0]));
  }
  // 有价格部分单调不减
  for (let i = 1; i < priced.length; i++) {
    assert.ok(priced[i].signals.price.usd >= priced[i - 1].signals.price.usd,
      '成本档应按价格升序');
  }
});

test('C01 quality: 能力覆盖度降序，同分保持原顺序（稳定）', () => {
  const r = recommendModels('视频', null, { catalog: cat });
  const models = r.tiers.quality.models;
  for (let i = 1; i < models.length; i++) {
    assert.ok(models[i].signals.capability_count <= models[i - 1].signals.capability_count,
      '效果档应按能力覆盖度降序');
  }
  // 同分组内 catalog_position 递增（稳定排序，不制造虚假区分）
  for (let i = 1; i < models.length; i++) {
    if (models[i].signals.capability_count === models[i - 1].signals.capability_count) {
      assert.ok(models[i].catalog_position > models[i - 1].catalog_position);
    }
  }
});

test('C01 quality: 不编造质量分（signals 里无主观 score 字段）', () => {
  const r = recommendModels('视频', null, { catalog: cat });
  const signals = r.tiers.quality.models[0].signals;
  assert.equal('score' in signals, false);
  assert.equal('quality_score' in signals, false);
});

// ---------- R1/R3：不改 selectCandidates 默认行为 ----------
test('C01 R1/R3: 调 recommendModels 不改变 selectCandidates 输出', () => {
  const before = JSON.stringify(selectCandidates('general', null, { catalog: cat }));
  recommendModels('真实好看又便宜的产品视频', null, { catalog: cat });
  recommendModels('视频', null, { catalog: cat, strategy: 'quality' });
  const after = JSON.stringify(selectCandidates('general', null, { catalog: cat }));
  assert.equal(before, after, 'selectCandidates 默认输出必须逐字节不变');
});

test('C01 R3: 不传 intent 时走默认 balanced/general，不报错', () => {
  const r = recommendModels('', null, { catalog: cat });
  assert.equal(r.purpose, 'general');
  assert.equal(r.strategy, 'balanced');
  assert.ok(r.tiers.balanced.models.length >= 1);
});

// ---------- 继承 selectCandidates 的筛选规则 ----------
test('C01: 继承 requirements 筛选（不满足的候选不进任何档）', () => {
  const r = recommendModels('视频', null, {
    catalog: cat, requirements: { duration_s: 999 },
  });
  for (const t of Object.values(r.tiers)) {
    assert.equal(t.models.length, 0, '不可能时长应过滤掉全部候选');
  }
});

test('C01: 继承 keyModels 过滤', () => {
  const r = recommendModels('视频', ['__nonexistent__'], { catalog: cat });
  for (const t of Object.values(r.tiers)) {
    assert.equal(t.models.length, 0);
  }
});

// ---------- 复核 2026-11-24：看图意图 + 图片成本排序 ----------
test('C01 review: 看图/识字意图优先于生成图片', () => {
  assert.equal(parseRecommendIntent('请看图，告诉我图片中写了什么').purpose, 'vision');
  assert.equal(parseRecommendIntent('视觉理解：分析这张图片中的账单').purpose, 'vision');
  // 生成类不被误伤
  assert.equal(parseRecommendIntent('生成一张海报').purpose, 'text2image');
  assert.equal(parseRecommendIntent('portrait photo of a woman').purpose, 'text2image');
  const r = recommendModels('请看图，告诉我图片中写了什么', null, { catalog: cat });
  assert.equal(r.purpose, 'vision');
});

test('C01 review: 图片成本排序读 documented price_rows（便宜的在前）', () => {
  const r = recommendModels('低成本生成一张图片', null, { catalog: cat });
  const priced = r.tiers.cost.models.filter((m) => m.signals.price);
  assert.ok(priced.length >= 2, '图片候选应有可比的 documented 价格');
  // image-01 每张 $0.004 < seedream $0.034 < qwen $0.04
  const ids = r.tiers.cost.models.map((m) => m.id);
  const iImage01 = ids.indexOf('image-01');
  const iSeedream = ids.indexOf('seedream-5-0-260128');
  assert.ok(iImage01 >= 0 && iSeedream >= 0 && iImage01 < iSeedream,
    '每张 4 积分的 image-01 应排在 34 积分的 seedream 之前');
  for (const m of priced) {
    assert.ok(m.signals.price.source === 'documented' || m.signals.price.source === 'verified');
    assert.ok(String(m.signals.price.basis).startsWith('price_rows'));
  }
});
