# @powertokens/kit-core（Node.js 核心包）

零依赖、不发请求的规则层。开发态与 Python 工具链**读同一份** `../pt-gateway/catalog.json`；
`npm pack` 后脱离仓库时会回退到包内 `catalog.snapshot.json`（固定版本快照，详见文末「打包」）。
所以 Agent 侧不需要另存一份模型能力表。模型调用请求仍由 DSH 自己的供应商层发出。

```bash
cd node && npm test          # node --test，48 条离线用例，0 花费
```

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

北极星交付 1 需要「用户指定了模型」时**只判定、绝不自动换模型**：

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

### 0.8.3（北极星 Codex 复核修复）

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

### 0.8.4（北极星 Codex 复核第二轮：2 项边界情况）

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
cd node && npm pack              # 生成 dist/powertokens-kit-core-0.8.6.tgz
sha256sum dist/powertokens-kit-core-0.8.6.tgz
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
- `estimateCost()` 不扣费，但**不支持对话模型**（会在本地拦截，不发请求）。
- 金额保留 `credits`，不换算 USD。

环境变量便捷构造：`AccountClient.fromEnv()` 读 `PT_ACCESS_TOKEN` / `PT_KEY_ID`。
**不要**把令牌写进仓库、日志或提交信息。

## 边界

- 本包不负责发模型生成请求，也不做媒体生成。
- 不做客户端权限拦截（fail-open）；Key 能否调用某模型以该 Key 的 `/v1/models` 与服务端报错为准。
- 字段为 `null` 或来源为 `inferred` / `configured` 一律按未知处理（`isKnownValue()`）。
