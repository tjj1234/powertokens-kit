# @powertokens/kit-core（Node.js 核心包）

零依赖、不发请求的规则层。开发态与 Python 工具链**读同一份** `../pt-gateway/catalog.json`；
`npm pack` 后脱离仓库时会回退到包内 `catalog.snapshot.json`（固定版本快照，详见文末「打包」）。
所以 Agent 侧不需要另存一份模型能力表。模型调用请求仍由 DSH 自己的供应商层发出。

```bash
cd node && npm test          # node --test，62 条离线用例，0 花费
```

---

## 快速上手与能力清单（中文 · 先看这里）

> 本节是 [完整使用说明](../docs/PT_Kit_使用与接口能力清单_20261118.md) 的上手浓缩，让 agent 读到这里就知道「怎么用、能干吗」。下方英文部分是逐版本的技术细节。

### 它是什么 / 能干什么

**它不替你发模型请求，也不生成图片 / 视频。** 它是「**决策大脑 + 记账本**」，只做两件事：

- **规则层**（`index.js`）：零依赖、不联网，只读 `catalog.json`，告诉 Agent「选哪个模型 / 报错怎么办 / 要不要重试 / 思考参数怎么传」。
- **账户只读客户端**（`account-client.js` / `account_client.py`）：连 PT 查余额 / Key 状态 / 消费记录 / 可见模型 / **费用预估**——全部只读或计费探针，不发生成请求。

模型调用由 DSH / 你自己的供应商层去做；kit 不是「发动机」。

### 怎么装 / 怎么引入

```js
// 开发态（在仓库里）：规则层与账户客户端都从 ./node 引入
const core = require('./node');
const { AccountClient } = require('./node/account-client');
```

```bash
cd node && npm pack     # 生成 dist/powertokens-kit-core-0.8.12.tgz，自包含
```

tgz 内含 `index.js` / `account-client.js` / `catalog.snapshot.json` / `catalog.schema.snapshot.json` / `README.md` / `package.json`。把解包后的 `package/` 整个放进 DSH 的 `vendor/` 即可，`loadCatalog()` 找不到仓库 catalog 时自动回退读包内快照。零运行时依赖，Node ≥ 18。

### 凭证（两套域，不可混用）

| 域 | 地址 | 鉴权 |
|---|---|---|
| 账户域 | `https://powertokens.ai/api` | 裸 `pt_live_...`（**不带 Bearer**）+ 数字 `key_id` |
| 生成域 | `https://api.powertokens.ai/v1` | `Bearer <模型 API Key>` |

- 凭证由调用方每次传入，客户端不读配置 / 不落盘 / 不写日志；`AccountClient.fromEnv()` 读 `PT_ACCESS_TOKEN` / `PT_KEY_ID`。
- `key_id` 必须是数字；PT 没有「用 API Key 反查 key_id」的接口。
- 账户域业务成功只看响应体 `code`——无效凭证返回 **HTTP 200 + `code=500`**，只看状态码会误判成功。

### 规则层 5 条 API（`index.js`）

| API | 作用 | 关键返回 |
|---|---|---|
| `loadCatalog({path, expectedSchema})` | 固定版本 + 格式校验，失败抛 `CatalogValidationError`（调用方应继续用上一版本） | catalog 对象 |
| `selectCandidates(purpose, keyModels, opts)` | AUTO 按用途有序返回候选（4 条跳过规则）；PINNED 用户指定模型只判定可用性、绝不自动换 | AUTO 数组 / PINNED 判定对象 |
| `classifyError(status, body, opts)` | HTTP 状态 + 响应体归入 `errors` 9 类；未收录按「不重试、换模型、上报」 | `{category, agent_action, retryable, switch_model, ...}` |
| `retryDecision(input, opts)` | 重试 / 换模型 / 停止决策，支持 pinned / 退避 / 次数上限 | `{action, accepted, settled, charged, reason_code, ...}` |
| `reasoningParams(model, effort, opts)` | 思考模式传参；`effort='none'` 返回 `needs_confirmation:true`，不猜关闭取值 | `{supported, param, value, needs_confirmation, ...}` |

辅助导出：`catalogVersion()` / `isKnownValue()` / `isKnownSource()` / `isGenerationModel()` / `CatalogValidationError`。

**媒体选模（0.8.8 新增，向后兼容）**：`auto_policy.purposes` 新增 4 个媒体用途
`text2image` / `image2image` / `text2video` / `image2video`（候选为 image / video / seedance 模型）。
- `selectCandidates(purpose, keyModels, {catalog, requirements})` 可选传 `requirements`（如
  `{duration:4, resolution:'720p', ratio:'16:9'}`）：不满足的候选进 `excluded`（带 `reason`
  与 `available_values`），能力未知标 `capability_unknown`。**不传 `requirements` 时行为与
  0.8.7 逐字节一致**，`detail` 也不会多出键。
- `mediaRequestParams(modelId, friendlyParams, {catalog})` 按条目 `capabilities.media.param_names`
  把用户友好参数映射到真实请求字段；不支持的参数进 `unsupported`，不静默丢。
- 媒体能力权威字段是条目的 `capabilities.media`（`durations/resolutions/aspect_ratios/sizes/
  duration_rule/reference_image/pixel_map/price/param_names/family`）；顶层旧字段保留兼容，
  差异由 `supersedes_top_level` 标注。价格带 `_source`（`verified` 附日期），像素映射由 kit
  按档位统一定义、标「待实测校准」，精确像素以官网原厂参数为准。

**读取规则（0.8.9 官方约定，接入方按此实现）**：
> 以 `capabilities.media` 为权威；新字段里存在的项，以新字段为准。新字段里缺失、且
> `supersedes_top_level.fields` 未声明被替代的项，**沿用该模型顶层旧字段值**；只有被
> `supersedes_top_level` 明确声明替代的项，才不使用旧值。
- 作用：seedream 等「顶层有比例、新字段尚未枚举」的模型不会被判成无能力；而被 `supersedes`
  标注替代的 9 个视频模型（顶层分辨率/时长是 0.8.5 历史值）不会误回退到旧值。
- `adaptive` 自 0.8.9 起从 `aspect_ratios.value` 移出，改用 `aspect_ratios.adaptive_supported: true`
  标记；选模与 `mediaRequestParams` 会把该标记还原成可匹配的 `adaptive` 值，语义不变。
- **`sizes` 语义自 0.8.12 收紧**：`capabilities.media.sizes.value` 只放模型保证的**精确像素**
  （如 `2560x1440`），无官方枚举则 `null` + `_source`；分辨率**档位**（如 `1080p/2k/4k`，小写）
  移到 `capabilities.media.size_tiers.value`。读旧档位请改用 `size_tiers`，勿再从 `sizes` 取档位；
  像素换算见 `pixel_map`（kit_derived 待实测，不当作支持）。传 `size` 需求时，选模与
  `mediaRequestParams` 在 `sizes.value` 非数组时自动回落 `size_tiers.value`，旧调用不失效。
- **计价结构化自 0.8.12**：`capabilities.media.price.price_rows` 是 playground 价档结构化数组
  （每行含 resolution/mode/ability/billing_unit/sale_input_price/sale_input_credit 等，带
  `_source=documented`）；`price_rows_count` 保留为条数兼容。预估仅参考、以实扣为准（U10）。
- 无官方来源的能力值一律 `null` + `_source`（北极星只把 `documented` / `verified` 当成支持）。

**重试分支优先级**：发送前视觉拦截 → 受理未知 → 2xx 已受理（不重放）→ 上下文超长 → 其余。

- 2xx（含流中途报错、含带 `context_length_exceeded`）一律 `action:'none'`，绝不 compact / retry / switch，避免重复扣费。
- 无有效 HTTP 状态（超时 / 断网）→ `stop` + `acceptance_unknown`，绝不重发（PT 可能已受理）。
- 非 2xx 上下文超长（400/422）→ `compact`（先压缩再发，不原样重试）。
- `maxRetries:0` 合法（不重试），只有参数未传才用默认 3。
- 搜索候选只有 `works===true && returns_sources===true` 才进 `candidates`，`null` 进 `pending_verification`。

```js
const { loadCatalog, selectCandidates, classifyError, retryDecision } = require('./node');
const cat = loadCatalog({ expectedSchema: 2 });
const visible = await yourModelsCall();          // 该 Key 的 /v1/models 可见 ID
const cands = selectCandidates('general', visible, { catalog: cat, detail: true });
const err = classifyError(429, { error: { code: 'model_rate_limited' } });
const d = retryDecision({ status: 429, attempt: 1, retryAfter: 5 });
```

### 账户只读客户端 7 个接口（JS / Python 一一对应）

| 能力 | Node 方法 | Python 方法 | 端点 |
|---|---|---|---|
| Key 状态 | `getKeyStatus(keyId)` | `get_key_status()` | `POST /v1/key/info`（body 传整数 `key_id`，GET 404） |
| 余额 | `getBalance()` | `get_balance()` | `GET /v1/billing/balance?key_id=` |
| 消费记录列表 | `listUsageRecords({startDate,endDate,keyId})` | `list_usage_records()` | `GET /v1/billing/usage`（日汇总有延迟） |
| 单条消费记录 | `getUsageRecord({requestId,taskId,consumeId})` | `get_usage_record()` | `GET /v1/logs/query`（**按 ID 精查单条，不是列表接口**） |
| **费用预估** | `estimateCost({model,endpoint,request,keyId,kind})` | `estimate_cost()` | `POST /v1/billing/estimate`（见下节） |
| 资产库协议状态 | `getAgreementStatus()` | `get_agreement_status()` | `GET /v1/asset/agreement/status` |
| 可见模型 | `getKeyModels(apiKey)` | `get_key_models()` | `GET /v1/models`（生成域，`Bearer`） |

`getUsageRecord` 的「未查到」三态（仅此接口）：有效记录 `found:true`；明确未找到 `found:false, category:'record_not_found'`；200 空壳 `found:false, category:'empty_record'`；其他错误 `found:null`。对账只读结构化 `found` / `category`，**不解析 PT 的 msg**；`found:false` 不代表未扣费、**不可据此重发**。

```js
const { AccountClient } = require('./node/account-client');
const acct = AccountClient.fromEnv();        // 读 PT_ACCESS_TOKEN / PT_KEY_ID
const bal = await acct.getBalance();         // { ok, code, data, error, data_as_of, is_cached }
if (!bal.ok) console.error(bal.error.category);
```

### 是否包含预估接口？—— 有，且是官方接口

`estimateCost()` / `estimate_cost()` 走官方 `POST /v1/billing/estimate`。四个关键边界：

1. **不扣费**：真机实测余额 `80 → 80 credits`。
2. **整包请求体**：`{ model, endpoint, key_id, request:{...} }`，`key_id` 必填且为数字。
3. **不支持对话模型（chat）**：chat 在本地被拦截、不发请求，返回 `error.category:'unsupported'`；chat 费用**以实际扣费为准**，不自建单价表。预估主要面向图 / 视频类。
4. **金额保留 credits**，不换算 USD；**预估值只作参考**——U10 实测 `wan3.0-video` 5s/720p 预估 1000 vs 实扣 500 credits（`pricing_policy`：预估可能偏高）。

> 真正扣费的单价真相源是消费记录 `logs/query`；`GET /v1/billing/usage` 当日不实时计入消费。

### Python 工具链（校验 / 复核 / 测试）

| 工具 | 命令 | 作用 |
|---|---|---|
| catalog 校验 | `python pt-gateway/validate_catalog.py` | schema / 字段 / 来源枚举守门，改 catalog 后必跑 |
| 复核脚本 | `python pt-gateway/recheck.py` | 离线对账（0 花费）；`--live-view <models.json>` 对账 `top_provider`；`--registry` 生成待复核清单 |
| 真机复核 | `recheck.py --live --max-usd <N> --usd-per-1k-tokens <N>` | 真实调用，**会产生费用**；`--max-usd` 硬上限须 > 0，未显式给出则不跑 |
| 测试 | `python pt-gateway/tests/run_tests.py` + `cd node && npm test` | Python 全量 / Node **83/83**（0.8.12），0 花费 |

### 能力边界（明确不做什么）

- 不发模型生成请求、不做媒体生成（视频 / 图片由供应商层负责）。
- 不做客户端权限拦截（fail-open）；某 Key 能否调用某模型以该 Key 的 `/v1/models` 与服务端报错为准。
- 字段为 `null` 或来源为 `inferred` / `configured` 一律按未知处理。
- 不保存凭证；令牌只在内存 / 本机临时文件，用完即删。

### 一页速查

```
我想……
├─ 选模型 / 判断能不能用        → selectCandidates()（AUTO / pinned）
├─ 归类一个报错                 → classifyError()
├─ 决定要不要重试 / 换模型      → retryDecision()
├─ 决定思考参数怎么传           → reasoningParams()
├─ 查余额 / Key 状态 / 协议     → getBalance / getKeyStatus / getAgreementStatus
├─ 查消费记录（单条 / 列表）    → getUsageRecord / listUsageRecords
├─ 查这把 Key 能看到哪些模型    → getKeyModels(apiKey)   [Bearer]
├─ 预估一次调用多少钱           → estimateCost()         [不支持 chat，不扣费]
└─ 改完 catalog 想自证          → validate_catalog.py + run_tests.py + npm test
```

---

**（以下为英文技术细节：逐版本演进、打包说明与边界）**

## index.js —— 五条规则 API

| API | 读 catalog 的哪部分 | 作用 |
|---|---|---|
| `loadCatalog({path, expectedSchema})` | 全文 | 固定版本 + 格式校验；失败抛 `CatalogValidationError`，调用方应继续用上一版本 |
| `selectCandidates(purpose, keyModels, opts)` | `auto_policy.purposes` / `skip_rules` | 按用途有序返回候选，实现 4 条跳过规则，支持传 `registry` |
| `classifyError(status, body, opts)` | `errors` 9 类 | 按 `error.code` + HTTP 状态归类；未收录的错误按「不重试、换模型」并上报 |
| `retryDecision(input, opts)` | `retry_policy.rules` | 区分「首包前失败可重试」与「HTTP 200 已结算不可重放」，带退避与次数上限 |
| `reasoningParams(model, effort, opts)` | `models_api_contract.capability_rules.thinking` | 思考模式传参；`effort='none'` 返回 `needs_confirmation=true`，不猜关闭取值 |

```js
const { loadCatalog, selectCandidates, classifyError, retryDecision } = require('./node');

const cat = loadCatalog({ expectedSchema: 2 });
const visible = await yourModelsCall();          // 该 Key 的 /v1/models 可见 ID
const candidates = selectCandidates('general', visible, { catalog: cat, detail: true });
const err = classifyError(429, { error: { code: 'model_rate_limited' } });
const decision = retryDecision({ status: 429, body: {}, attempt: 1, retryAfter: 5 });
```

**4 条跳过规则**：已 `delisted` / `tool_calling.verified === false`（`null` 不跳过）/
搜索用途且 `works` 或 `returns_sources` 为 `false` / 不在本 Key 可见列表。
`not_in_key_view` **不等于**下架（0.7.3 起 `ModelRegistry.sync()` 已不再自动 `delisted`）。

### 用户指定模型（pinned，0.8.2）

核心能力：「用户指定了模型」时**只判定、绝不自动换模型**：

```js
// 1) 指定模型可用性：返回 { pinned, available, reasons, checks, candidate }
const verdict = selectCandidates('general', visible, {
  catalog: cat, pinnedModel: 'gpt-5.2', hasImage: true,
});
// reasons[].code: not_in_catalog / not_in_key_view / vision_unsupported /
//                 tool_calling_not_verified / web_search_unusable / delisted

// 2) 指定模型出错时的决策：input.pinned = true
const d = retryDecision({ status: 429, attempt: 1, pinned: true, model: 'gpt-5.2' });
// d.action: retry（同模型，耗尽后 stop 而非 switch）
const e = retryDecision({ status: 403, pinned: true, model: 'gpt-5.2' });
// e.action: user_choice（不自动换，交用户决定）
```

| 场景 | AUTO 未指定 | `pinned: true` 指定 |
|---|---|---|
| 429 / 5xx / 超时 | 重试，3 次后换模型 | 只重试同一模型，耗尽后 stop，不返回「换模型」 |
| 模型不可用 / 无权限 / 参数不支持 | 直接换下一个 | `user_choice`，不自动换 |
| 带图片但模型不能看图 | 选能看图的模型 | 发送前 `unsupported`，由用户决定 |
| 工具调用实测为 `null` | 照常使用 | 照常使用，`untested: ['tool_calling']` 标出「未实测」 |
| 401 失效 / 402 余额不足 | stop | stop |

`retryDecision` 额外输出：`pinned` / `pinned_model` / `reason_code` /
`needs_user_choice` / `before_send` / `untested`。

**用途兜底**：`purpose` 不在 `auto_policy.purposes` 时，不再直接 `unknown_purpose`，
改按模型的 `routing.recommended` 标签匹配，`detail:true` 时返回 `matched_by: 'routing.recommended'`；
仍无匹配才 `unknown_purpose`。标签无序、不宣称优先级。

### 0.8.3（源码复核修复）

- **`maxRetries: 0` 是合法值**（不重试）：只在参数未传时才用默认 3；传 0 时可重试错误
  第一次就返回 `switch` / `stop`，绝不返回 `retry`。
- **受理状态未知**：`status` 为 0 / `undefined` / `ETIMEDOUT`（客户端超时 / 断网）时返回
  `action:'stop'` + `reason_code:'acceptance_unknown'` + `accepted:'unknown'`，**不返回
  retry / switch** —— PT 可能已受理并扣费，重发会二次扣费；先用 `logs/query` 确认。
  发送前的视觉拦截优先于此分支（请求还没发，不存在受理未知）。
- **上下文超长 → `compact`**：`400 + context_length_exceeded` 返回 `action:'compact'`
  （先压缩上下文或缩减输入再发），禁止原样重试。
- **搜索候选分组（A4）**：搜索用途只有 `works===true` 且 `returns_sources===true` 才进
  `candidates`；值为 `null`（未实测）的进 `pending_verification` 分组。当前 3 个搜索
  候选全 null：`candidates` 空、`pending_verification` 3 个。
- **`catalogVersion()`**：新增 `kit_version`（包版本），与 catalog `updated` 一起透出，
  方便与 CHANGELOG 对账。

`account-client.js` 同步加严（B1-B3）：

- **B1**：账户域 2xx 必须是**合法 JSON 对象且带 PT `code` 字段**才算成功；HTML / 空串 /
  截断 JSON / 缺 `code` 一律返回 `error.category:'invalid_response'`。生成域 `/v1/models`
  是 OpenAI 风格 `{data:[...]}`，只要求合法 JSON 对象。
- **B2**：`data_as_of` / `is_cached` 只**原样透传**响应里的服务端统计时间与缓存状态；
  PT 没返回时为 `null`，不编造。
- **B3**：所有方法接受 `{signal}`（`AbortSignal`，与内部超时合并，取消返回
  `category:'aborted'`）；响应大小上限 `maxResponseBytes`（默认 1 MiB 可配），超限返回
  `response_too_large`。

### 0.8.4（第二轮：2 项边界情况）

- **D1 `code:null` 不再当成功**：账户域只有 `code` 明确是成功值（`200`/`'200'`/`0`/`'0'`）
  才算成功；`code` 为 `null` / `''` / `'abc'` / 缺失时即使 HTTP 200 也返回
  `ok:false` + `error.category:'invalid_response'`。明确的失败码（如 `500`）仍归
  `business_error`，生成域 `/v1/models` 规则不变。
- **D2 2xx 已受理优先于上下文超长**：`retryDecision` 分支顺序固定为 发送前拦截 → 受理未知 →
  **2xx 已受理（不重放）** → 上下文超长 → 其余。HTTP 200 + `context_length_exceeded`
  现在返回 `action:'none'`（加不加 `pinned` 都一样），绝不 compact/retry/switch，避免重复扣费；
  非 2xx（400/422）+ 上下文超长仍 `compact`。

### 0.8.5（/v1/models 上线上下文/输出上限字段 → limits 实测补值）

- PT 在 `GET /v1/models` 每个模型的 `top_provider` 下新增两个整数 token 字段：
  `context_length` / `max_completion_tokens`。实测 77 模型中 31 个携带，顶层
  `context_length` 与 `top_provider.context_length` 逐模型一致。
- 按映射 `top_provider.context_length → limits.max_context_tokens`、
  `top_provider.max_completion_tokens → limits.max_output_tokens`，给 catalog 中命中的
  **30 个模型**补上正整数上限，`_source='models_api'`、`_checked_at='2026-11-17'`。
  无该官方字段的模型 `limits` 仍为 `null`（视频/图像等本就不吃上下文窗口）。
- 本版只补数据，**不改** `retryDecision` / `selectCandidates` 的路由行为；
  "按上下文窗口事前预估 compact"仍属可选增强，未纳入。
- `recheck.py --live-view <models.json>` 现在会对账 `top_provider` 上下限字段
  （一致→confirmed，catalog null→suggestion，不一致→conflict，不自动写回正文）。


### 0.8.12（T09 尺寸传参 + P2-7 sizes 语义/计价结构化 + 10 default 小写）

- **动 catalog 字段语义，单独发版 + 发版说明（R2）**：发新 core tgz，北极星按新字段读取规则平滑跟进、换一次锁。旧签名 `selectCandidates(purpose, keyModels)` 输出逐字节一致（差分 OLD 7994B == NEW 7994B，含代码差分 + 数据差分）。
- **P2-7 sizes 语义**：`viduq2` 的 `sizes.value=['1080p','2K','4K']` → `sizes.value=null`（无官方精确像素）+ 新 `size_tiers.value=['1080p','2k','4k']`。`REQ_FIELD_MAP.size.get` / `mediaRequestParams` 在 `sizes.value` 非数组时回落 `size_tiers.value`，传 `size` 需求的三档均映射为 `resolution` 且不判 unsupported。
- **P2-7 计价结构化**：35 个媒体价格模型新增 `price.price_rows`（playground 价档数组，共 150 行，带 `_source=documented`），`price_rows_count` 保留兼容；以实扣为准（U10）。
- **10 default 小写**：`resolutions.default` 的 `1080P/720P/768P` 统一小写（10 模型）。
- **T09（Python 生成链路）**：`pt_video.py` 新增 `resolve_media_fields()` 按 catalog `param_names` 映射字段名；默认模型恒等映射 body 不变，kling 等被正确重命名。详见根目录 CHANGELOG。
- **验收**：Node 83/83、Python 327/327、validate_catalog 全过、差分 OLD==NEW + P2-7 sizes 专项 PASS。

### 0.8.9（媒体数据补齐：北极星审核 P1 + P2-4/5/6，读取规则文档化）

- **纯增量、对 0.8.8 零破坏**：旧签名 `selectCandidates(purpose, keyModels)` 输出逐字节一致
  （差分 OLD==NEW）。只改 catalog 媒体字段 + node `aspect_ratio` 取值逻辑（把 `adaptive_supported`
  标记还原成可匹配值，语义不变）；不碰生成链路、账户客户端、5 个 chat 用途的候选顺序。
- **P1 补欠账**：4 个 seedream 补 `aspect_ratios`（seedream-5-0 沿用顶层 8 比例 documented，
  其余 3 个顶层/en.zip 均无枚举→留 null）；9 个图片模型补 `pixel_map`（image-01 有官方精确像素
  documented，wan2.7-image-pro 为 kit 自定档位换算待校准，其余 null）；19 个 `reference_image`
  补全字段（wan2.7-image-pro 有 20MB/≤9 张 documented，其余 null）。
- **P2 新口径**：24 个模型分辨率字符串统一小写（`_normMediaVal` 本就大小写不敏感，行为不变）；
  8 个模型的 `adaptive` 从 `aspect_ratios.value` 移到 `adaptive_supported:true`；8 个 `duration_rule`
  补 `duration_step_s`（官网无步长依据→留 null）；删 `wan3.0-video` 的 `confirmed_card`。
- **读取规则文档化**：README + catalog 顶层 `note` 写明「新字段优先；缺失且未被 `supersedes_top_level`
  替代则沿用顶层旧字段」的 fallback 约定。
- **R4 不猜**：共 86 处无官方来源的值留 `null` + `_source`，北极星只把 `documented`/`verified`
  当支持。
- **验收**：Node **83/83**（新增 adaptive 单列用例）、Python 全量、validate_catalog 全过、差分 OLD==NEW。

### 0.8.8（媒体能力补全：4 用途 + capabilities.media + 可选选模分支）

- **纯增量、对 0.8.7 零破坏**：只新增 4 个媒体用途（`text2image/image2image/text2video/
  image2video`）、条目 `capabilities.media` 权威字段、M6 别名与一个可选函数
  `mediaRequestParams()`。`selectCandidates(purpose, keyModels)` 旧调用（不传 `requirements`）
  输出逐字节一致，差分测试 OLD/NEW 均 4410 字节相同；北极星锁定 tgz + catalog SHA 的方案不受影响。
- **数据来源**：从 PT 官方 en.zip OpenAPI + playground 全量抽取媒体参数；404 下架的 17 个
  模型只从媒体候选排除、不删主条目；在售 75 个模型。价格预估按高档、实扣为准（仅
  `wan3.0-video` 5s/720p 有 `verified` 实扣 0.5 USD），chat 无预估接口。
- **北极星复核六条已逐条落实**：字段名 / 完整枚举、`capabilities.media` 权威 + 新旧差异
  标注、价格 `_source`、像素映射（kit 自定档位映射 + 待实测校准）、参考图要求、seedance
  不改 `kind` 只标 `family:'video'`。
- **不碰**：生成链路 `pt_video.py`、账户客户端、现有调用路径与现有测试。T06 下载安全补丁
  已作为 `v0.8.7-security.1` 单独先行发布，与本版解耦。
- **验收**：Node **73/73**（新增 11 例媒体测试）、Python **292/292**、validate_catalog 全过。

### 0.8.7（请求 ID 官方确认，纯文档）

- PT 官方确认：`X-Oneapi-Request-Id` 响应头与消费记录 `request_id` **同值**（可查
  `logs/query?request_id=<header 值>`），且**全场景必返回**（中间件在路由前执行，
  与流式 / 错误无关）。证据边界从实测升为官方实现层保证，非逐模型实测。
- **代码 / catalog 不改**：`getUsageRecord()` 本就透传 `requestId` → `params.request_id`；
  `retry_policy.request_id_header_note`「不是受理 / 重试信号」核心更成立，仅说明补强。
  注意 `X-Oneapi-Request-Id`==`request_id`，别混 chat 同步 `X-Request-Id`（== body `id`）
  与视频异步 body `id`（== `task_id`）。

### 0.8.6（getUsageRecord「未查到」二级判定）

- PT 2026-11-18 优化：不存在的 ID 由「code=200 + 空壳」改为「HTTP 200 + body
  `code=500` + `msg="record not found"` + `data:null`」（真机实测：两组凭证、
  consume_id/request_id 三条查询一致）。两种形态并存、互不替代。
- `getUsageRecord` 在 `normalizeResult` 之后做二级判定（**仅此接口**，其他方法返回不变）：
  - 有效记录 → `ok:true, found:true`（金额零但标识/状态完整仍是有效记录）
  - 500 + `data:null` + msg=`record not found` → `ok:false, found:false,
    error.category:'record_not_found'`
  - code=200 + 标识/模型/状态全空 → `ok:false, found:false,
    error.category:'empty_record'`（与前者不同分类）
  - 其他错误（token 无效/参数错/文案不匹配/网络）→ 原分类不变，`found:null`（未判定）
- **契约**：对账只读 `found` / `error.category`，不解析 PT 的 msg；`found:false`
  仅表示本次未查到，**不代表未扣费 / 最终无记录，不可据此重发**（重发由
  `retryDecision` 独立控制）。`interpretUsageRecord` / `isEmptyRecordData` 可单独引入测试。

### 打包（npm pack / vendor 离线使用）

```bash
cd node && npm pack              # 生成 dist/powertokens-kit-core-0.8.12.tgz
sha256sum dist/powertokens-kit-core-0.8.12.tgz
```

tgz 含 `index.js` / `account-client.js` / `catalog.snapshot.json` /
`catalog.schema.snapshot.json` / `README.md` / `package.json`，**自包含**。
把解包后的 `package/` 整个放进 DSH 的 `vendor/` 目录即可，无需 `pt-gateway/` 目录：
`loadCatalog()` 检测不到仓库内 catalog 时自动读包内快照。校验器会守门快照与主 catalog 内容一致。
空目录 `npm install <tgz>` 后 `require('@powertokens/kit-core').loadCatalog()` 同样可读可校验（已实测）。

## account-client.js —— 账户只读客户端

命名不写死 PT，方便以后接其他供应商：`getBalance` / `getKeyStatus` /
`listUsageRecords` / `getUsageRecord` / `estimateCost` / `getAgreementStatus` / `getKeyModels`。

```js
const { AccountClient } = require('./node/account-client');

// 凭证由使用方每次传入；客户端不读配置文件、不落盘、不写日志
const acct = new AccountClient({ token: process.env.PT_ACCESS_TOKEN, keyId: 123456 });
const bal = await acct.getBalance();      // { ok, code, data, error, data_as_of, is_cached }
```

实测口径（SSOT §12 / §13）：

- 账户域 `https://powertokens.ai/api`：`Authorization: <pt_live_...>` **裸值，不带 Bearer**；
  生成域 `https://api.powertokens.ai/v1/models` 用 `Bearer <API Key>`，两域凭证不混用。
- 业务成功以响应体 `code` 为准 —— 无效凭证会返回 **HTTP 200 + `code=500`**。
- `key_id` 请求侧必须是数字；余额/账单响应里的 `key_id` 目前仍可能是脱敏串。
- `getUsageRecord()` 必须带 `request_id` / `task_id` / `consume_id` 之一，**不是列表接口**。
  响应头 `X-Oneapi-Request-Id` 与消费记录 `request_id` 同值、全场景必返回（PT 官方确认），
  可直接取其值作为查询参数；但它是定位用 ID，**不是受理 / 重试信号**。
- `estimateCost()` 不扣费，但**不支持对话模型**（会在本地拦截，不发请求）。
- 金额保留 `credits`，不换算 USD。

环境变量便捷构造：`AccountClient.fromEnv()` 读 `PT_ACCESS_TOKEN` / `PT_KEY_ID`。
**不要**把令牌写进仓库、日志或提交信息。

## 边界

- 本包不负责发模型生成请求，也不做媒体生成。
- 不做客户端权限拦截（fail-open）；Key 能否调用某模型以该 Key 的 `/v1/models` 与服务端报错为准。
- 字段为 `null` 或来源为 `inferred` / `configured` 一律按未知处理（`isKnownValue()`）。
