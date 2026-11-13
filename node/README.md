# @powertokens/kit-core（Node.js 核心包）

零依赖、不发请求的规则层。与 Python 工具链**读同一份** `../pt-gateway/catalog.json`，
所以 Agent 侧不需要另存一份模型能力表。模型调用请求仍由 DSH 自己的供应商层发出。

```bash
cd node && npm test          # node --test，26 条离线用例，0 花费
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
