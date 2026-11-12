# PT API 2.0.0 官方答复核销报告

> 📌 **2026-11-12 追加**：此后又收到**第二批 PT 答复**（用户转述 12 条 + 两份正式文档
> `C:/Users/admin/Documents/API接口文档.md`〔用户指定最新〕与
> `API接口文档(支付&Key信息&费用预估).md`〔旧规划稿〕）。
> 第二批把 **Q3 / Q5 / Q6 / Q7 打到解决或大类解决**，Q2 彻底作废，Q1 基本解决，
> 并**纠正了本文 §0.1 的 Q4 口径**（最新文档点名 `GET /v1/logs/query`）。
> 逐条结果以 `docs/PT_待确认问题清单_20261112.md` 文末「第二批官方答复核销」为准；
> **本文 §1–§7 保留为第一批（2.0.0 规划稿）核销的历史记录，不改写**。
>

> 日期：2026-11-12 · 输入：PT 官方《API 接口文档》两份（均标注 版本 2.0.0 / 服务地址 `https://powertokens.ai/api`）
> 对象：`docs/PT_待确认问题清单_20261112.md` 的 Q1–Q8，以及 `docs/catalog_v2_决策锁定_20261112.md` 的 14 条决策
> 方法：官方文档逐条比对 + 仓库代码行级核对。**官方文档未写的，一律标「未回答」，不推断、不替 PT 圆。**
> 本文只固化事实与差异；**未改任何代码 / catalog**。
>
> ⚠️ **2026-11-12 后续修订（用户指定）**：此后又收到最新「默认模块」接口清单，用户明确
> 「就只要这些信息」且「**没有 `client_operation_id` 字段了**」。该清单与本文所核销的
> 2.0.0 规划稿**不是同一版接口**（`/key/info` 无 `/v1`、`/v1/logs/list` 为 POST + `page` 分页、
> `data` 为字符串化 JSON、字段出现驼峰 `redirectUrl` / `orderNo`）。
> **实现基线以「默认模块」为准**，见 `docs/PT_账户接口_默认模块依据_20261112.md`；
> 本文 §3 / §4 的破坏性变更清单降级为**历史差异记录**（结论不变，14 条决策未撤销），
> 但不再作为账户客户端的实现依据。**Q2 已作废**。

---

## 0. 结论先行

### 0.1 八问核销状态

| 问题 | 状态 | 一句话结论 |
|---|---|---|
| Q1 key_id / 列出我的 Key | 🟡 部分 | 确认 `key_id` 是**数字主键**，且响应里的 `key_id` 是**脱敏字符串、类型不同**；但「列出我的 Key」「用 API Key 反查数字 key_id」**仍无接口**。鉴权猜测被推翻：要的是 `pt_live_` 个人访问令牌。 |
| Q2 client_operation_id | 🟡 部分 | 字段确实存在、由「我方传入」、可用于反查消费记录；但**传入方式、幂等语义、格式约束全部未写**。 |
| Q3 模型响应里的 request_id | 🔴 未回答 | 两份文档**都是账户域**，没有任何生成接口的响应示例。 |
| Q4 /v1/logs/list | 🟡 部分 | 端点有专章，但正文写 **GET**、附录写 **POST**；路径自称「**拟定，最终以服务端实现为准**」；`limit` 默认值/上限明写「**待 PT 确认后补充**」。 |
| Q5 6 类错误精确报文 | 🔴 未回答 | 只给了**账户域**公共状态码表（无 402），与 A.04 的**生成域** 401/402/403 不是一套；精确报文一条未给。 |
| Q6 1000 vs 3 | ✅ **解决** | **1000 = 1 USD 折算的 credits(quota) 数**（官方字段说明 + calculation 明证）；`3` 是消费记录里 `per_1k_tokens` 计费项的 `quota_per_unit`，**不是换算率**。字段名被复用，语义不同。 |
| Q7 官方费用预估接口 | ✅ **解决** | `POST /v1/billing/estimate` **存在**，但**只覆盖视频/图像模型**，且官方承认「预估值可能与实际扣费存在偏差」。 |
| Q8 失败/超时扣费、幂等 | 🔴 基本未回答 | 消费记录有 `status`(success/failed) 可过滤，但失败是否扣 quota、退费、幂等、重复提交均未写。 |

### 0.2 三个最重要的发现

1. **`currency.credits_per_usd` 可以解锁填 `1000` 了**（`_source: official`）。这是 A2 决策里唯一挂起待确认的数值。但须注明「以 `/v1/billing/estimate` 返回的 `quota_per_unit` 为实时准」，别当死常量。
2. **Q7 从「疑似没有」翻成「有」**，`use_estimate_api` 有确切端点了。同时 A2 里「准确费用以该接口为准」这句**与官方文档冲突**——官方明说可能偏差，应改口径为「事前预估看 estimate，最终准确值看消费记录 `quota` / `final_quota`」。
3. **⚠️ 比 catalog 更紧急：现有账户代码在 2.0.0 下会挂。** 官方变更记录白纸黑字：「2.0.0 / 2026-11-05 全部接口的认证方式由登录令牌改为个人访问令牌（Access Token）——**破坏性：调用方需替换请求头凭证**」。而 `pt_video.py:119` 传的是**裸 API Key**，`/v1/billing/usage` 现在还**必填 `key_id`**。详见 §4。

---

## 1. 输入文档清单

| # | 分组目录 | 端点（方法 + 路径） | 认证 |
|---|---|---|---|
| A | 接口分组 `interface` | `GET /v1/asset/agreement/status` | 个人访问令牌 |
| A | | `GET /v1/logs/query` | 个人访问令牌 |
| A | | `GET /v1/billing/balance` | 个人访问令牌 |
| A | | `GET /v1/billing/usage` | 个人访问令牌 |
| B | 创建支付 / Key 信息 / 费用预估 / 批量消费记录 | `POST /v1/payment/create` | **登录令牌** `{{token}}` |
| B | | `GET /v1/key/info` | 个人访问令牌 |
| B | | `POST /v1/billing/estimate` | 个人访问令牌 |
| B | | `GET /v1/logs/list`（附录写 POST） | 个人访问令牌 |

**令牌规格**：`pt_live_` 前缀 + 32 位随机串；在「个人设置 → Access Token」生成；**与账号绑定、非团队级、一个账号同时只有一枚、权限等价账号、明文只显示一次、移除即失效**。
**公共响应**：`{ code, data, msg }`，且明示「HTTP 状态码与响应体 `code` 字段并行使用，**业务判断请以响应体 `code` 为准**」。

> 注意：同一个域里存在**两种令牌**（创建支付用登录令牌，其余用个人访问令牌），加上生成域 `api.powertokens.ai` 的 `Bearer <API Key>`，合计**三套凭证**。这是账户客户端模块设计时必须分清的第一件事。

---

## 2. 逐条核销

### Q1 有没有「用 API Key 查 key_id」或「列出我的 Key」的接口？ 🟡 部分

**官方新增事实**

| 项 | 结论 | 依据 |
|---|---|---|
| host | 仍是 `https://powertokens.ai/api` ✅ | 两份文档「服务地址」 |
| 鉴权头 | **不是裸 key、不是网页登录令牌，而是个人访问令牌 `pt_live_…`** | A 文档「通用说明 · 认证方式」+ 变更记录 2.0.0 第 1 行 |
| `key_id` 类型 | 请求参数是 **integer 数字主键**（示例 `1024`） | `GET /v1/key/info`、`GET /v1/billing/balance?key_id=`、`GET /v1/billing/usage?key_id=` |
| 响应里的 `key_id` | **脱敏字符串**，与请求参数类型不同 | A 文档 balance「字段区分：请求参数 key_id 是数字 ID，响应字段 key_id 是脱敏字符串」；B 文档 key/info 同样注明「响应字段 key_id 是 Key 的业务标识 `key_8f2c1d4e`」 |
| 查单个 Key | `GET /v1/key/info?key_id=1024` 可拿完整配置 | B 文档 §2 |
| 列出所有 Key / 反查 | **文档无任何接口** | 两份文档附录「接口一览」共 8 个端点，无 keys/list 之类 |

**对清单原猜测的修正**：Q1 第 4 条原本问「鉴权头是否仍为裸 key，还是改为个人访问令牌 + key_id 双参数」——现在有确定答案：**个人访问令牌，单个 `Authorization` 头；`key_id` 是业务参数不是鉴权参数**。清单里「账户客户端要求 key_id 必填 → 必须知道是调用方提供还是接口换取」这个死锁**依然存在**：数字 `key_id` 从哪来，8 个端点里没有产出它的接口。

**新增可用能力（支撑 B3 测试 Key 方案）**：`/v1/key/info` 返回 `status`（`active` / `disabled` / `expired` / `exhausted`）、`unlimited_credit`、`credit_limit`、`credit_remaining`、`expires_at`、`model_restriction_mode`（`all` / `selected`）、`models[]`。这正好覆盖拍板里「额度很小 / 停用 / 过期 / 只允许某个模型」四种测试 Key，B3 的「真实报错标 verified」方案**有了官方可配置依据**。

**仍未回答**：① 有没有「列出我的 Key」或「由 API Key 反查数字 key_id」的接口；② 数字 `key_id` 的获取途径；③ 个人访问令牌能否解析出 key_id。

---

### Q2 `client_operation_id` 怎么传？ 🟡 部分

**官方新增事实**：`GET /v1/logs/list` 的 Query 参数里有 `client_operation_id`，说明是「按**我方传入**的逻辑操作 ID 过滤」，示例 `op_20261029_0001`。→ 字段存在、命名正确、由调用方生成、可在消费记录里反查。

**仍未回答（且是清单的核心）**：
1. 调用模型时**怎么传进去**——哪个接口、HTTP header 还是请求体字段，文档只字未提；
2. 是否幂等键、重复提交是否只扣一次、幂等窗口；
3. 适用范围（仅异步媒体，还是 chat/image/audio 也支持）；
4. 与 `request_id` 的关系（文档把两者列为**并列的两个独立过滤参数**，说明不是同一个值）；
5. 格式/长度约束（只有一个示例值）。

→ `charging` / `submission` 里的 `idempotency_key_supported`、`idempotency_header`、`duplicate_returns_same_task` **仍只能填 `null`**。

---

### Q3 调用模型的响应里怎么拿 `request_id`？ 🔴 未回答

两份文档的 8 个端点全部在账户/计费域，**没有一条生成接口（`/v1/chat/completions`、`/v1/videos`、`/v1/images/generations`、`/v1/audio/speech`）的响应示例**。

**唯一增量**：`GET /v1/logs/list` 支持按 `request_id` 过滤，消费记录里 `request_id` 样例 `202610291027502520079680zCmvsK5`，与 `task_id` 并列。→ 证明「消费记录侧」字段稳定，但「模型调用响应侧」仍缺。

B4 决策「catalog 里只保留 request_id 位置这一条」**仍无法落地**，需要生成域响应样例或真机抓包（header + body + 流式首 chunk）。

---

### Q4 `/v1/logs/list` 是否已上线？GET 还是 POST？`limit` 默认值和上限？ 🟡 部分

**已回答的部分**

| 项 | 官方口径 |
|---|---|
| 端点关系 | **两个不同端点，不是别名**：`GET /v1/logs/query`（A 文档 §2，**无任何请求参数**，返回**单条** `data` 对象）；`GET /v1/logs/list`（B 文档 §4，批量 + 分页）。仓库现用的 `query` 在 2.0.0 里仍然存在。 |
| 过滤参数 | `start_time` / `end_time`（UTC，**左闭右开** `[start,end)`）、`key_id`（此端点用**脱敏字符串**）、`request_id`、`task_id`、`client_operation_id`、`model_id`、`status`、`cursor`、`limit`；条件之间 **AND**；全不传按默认时间范围返回最近记录 |
| 分页 | **cursor 游标**：`items[]` + `next_cursor` + `has_more` + `data_as_of`；`has_more=false` 或 `next_cursor=null` 停；过滤条件变了要丢弃旧 cursor 重新从首页 |
| 记录字段 | 「批量接口不做任何裁剪」，与 `logs/query` 单条完全一致：`consume_id` / `request_id` / `task_id` / `endpoint` / `provider_name` / **`model_id`** / **`model_name`** / `status` / `consume_time` / `request_type` / `quota` / `original_quota` / `usage` / `billing` |

> 顺带修正：清单里写的消费记录字段是 `model`，官方实际是 **`model_id` + `model_name`**。写解析代码时别抄错。

**未回答 / 自相矛盾的部分（见 §5）**：GET vs POST、路径「拟定」、`limit` 默认与上限、数据延迟、保留天数。

**另一个端点级变化**：`GET /v1/billing/usage` 的 `key_id` 在 2.0.0 变成**必填**（变更记录标「破坏性：原调用方需补传 key_id」，不传返回 400）。这与 Q4 无关，但直接打挂现有实现（见 §4）。

---

### Q5 调用模型时「余额不足」「Key 失效」分别返回什么错误？ 🔴 未回答

**必须区分两个域，否则 `errors` 字典会写错**：

| 域 | 文档给出的状态码 | 说明 |
|---|---|---|
| **账户域** `powertokens.ai/api` | A 文档：`200 / 401 / 403 / 429 / 500`；B 文档：`200 / 400 / 401 / 403 / 404 / 409 / 422 / 429 / 500` | **没有 402**。401=未认证/令牌无效或已被移除；403=无权限访问该资源；404=资源不存在（套餐已下架、模型未接入等） |
| **生成域** `api.powertokens.ai` | A.04 已固化：`401` Key 失效 / `402` 余额不足 / `403` 无权限或模型下架 | 见 `README.zh-CN.md:493-505` |

→ 结论：**A.04 的 402 是生成域专属**，账户域状态码表不能混进 `errors` 字典的 `insufficient_balance` 条目。清单 Q5 要的「6 种场景精确 HTTP 状态 + 错误码字符串 + message 原文」**一条都没给**。

**增量**：`/v1/key/info` 的 `status` 枚举为测试 Key 提供了可核验的状态定义（见 Q1），但那是配置查询，不是调用报错报文。

---

### Q6 `credits` 与 USD 的换算率 1000 还是 3？ ✅ 解决

**官方证据（B 文档 `POST /v1/billing/estimate` 响应字段表）**

| 字段 | 官方说明 | 示例 |
|---|---|---|
| `quota_per_unit` | 「每单位额度，**即 1 USD 折算的 credits 数量**」 | `1000` |
| `calculation` | 「`5 seconds × 0.13 USD/second × 1000 quota/USD = 650 quota`」 | 明写 `quota/USD` |
| `estimated_quota` | 预估扣减额度（credits） | `650` |

→ **`1 USD = 1000 credits(quota)` 是官方口径**，`_source: official`。

**那 `3` 是什么？** 它出现在**消费记录** `billing.quota_per_unit`：
- `per_image` 计费项示例：`billing_unit: "per_image"`, `quota_per_unit: 1000`, 但 `items[].unit_quota` 是 `30 credits/images`；
- `per_1k_tokens` 计费项示例：`billing_unit: "per_1k_tokens"`, `unit_size: 1000`, `quota_per_unit: 3`，`formula` 按 `2 credits/1k tokens` + `4 credits/1k tokens` 计算。

→ `3` 是某个计费模式下的 `billing.quota_per_unit`，**与 USD 换算率无关**。同一字段名在 `estimate` 与 `logs` 两处语义不同（见 §5 矛盾 #4），但已足以排除「3 是换算率」的误解。

**对 catalog 的动作建议**：`currency.credits_per_usd` 从 `null` 改为 **`1000`**（`_source: official`，`_checked_at: 2026-11-12`），并在字段说明里写「以 `/v1/billing/estimate` 返回的 `quota_per_unit` 为实时准；按 `key_id` 专属折扣可能不同」。另外 `pricing.billing_unit` 的枚举现在有官方取值可参考：`per_second` / `per_image` / `per_call` / `per_token`（estimate）/ `per_1k_tokens`（消费记录）。

---

### Q7 有没有官方「费用预估」接口？ ✅ 解决（但推翻了两处旧结论）

**端点**：`POST https://powertokens.ai/api/v1/billing/estimate`，**个人访问令牌**（不是生成域 Bearer）。
**请求体**：`model`（必填，需与 `request.model` 一致）、`endpoint`（必填，决定按哪套规则计费）、`request`（必填，**与目标模型正式调用完全一致的请求体**，用于提取 `size`/`duration`/`n`/`quality` 等计费维度）、`key_id`（可选 integer，`0` = 按账号默认策略）。
**响应**：`estimated_quota` / `sale_quota` / `cost_quota` / `breakdown.sale|cost.base_raw_quota` / `quota_per_unit` / `calculation` / `billing_unit` / `matched_rule_id` / `model` / `endpoint`。

**两处必须修正的旧结论**

1. 清单 Q7「已核实」段写「现有 estimate 是本地单价表推算，PT 未开放图片单价 API」——针对**仓库现状**这句没错，但**平台侧现在有官方预估接口了**，不能据此填 `use_estimate_api: false`。正确填法：**视频/图像类模型 = `true`；文本 chat 类 = `false`（文档标题就是「视频/图像模型费用预估」）**。
2. 决策 A2 写「准确费用以该接口为准」——**与官方冲突**。文档原文：「字段缺失时服务端按该模型的默认值兜底，**此时预估值可能与实际扣费存在偏差**」。建议改口径为：**事前预估用 `estimate`，最终准确扣费以消费记录 `quota` / `billing.final_quota` 为准**。

**仍未回答**：预估接口本身是否计费、是否有限频、预估与实扣不一致时以谁为准（文档只说「可能存在偏差」）。

---

### Q8 媒体生成失败 / 超时是否扣费？有无幂等保障？ 🔴 基本未回答

- 消费记录有 `status`（`success` / `failed`），`/v1/logs/list` 可按 `status` 过滤 → 失败会留记录，但**没说 failed 记录里的 `quota` 是 0 还是照扣**；
- `billing.formula` 展示了完整扣费计算过程，但没有「退费」「超时」「结果未知」的任何分支；
- 无自动退费、无到账时长、无取消接口、无幂等说明、无「失败后按 `task_id` 安全复查」说明；
- `client_operation_id` 存在（见 Q2）是唯一的弱线索，但语义未定义，不足以支撑 `charged_on_failure` / `duplicate_returns_same_task` 填值。

---

## 3. 端点对照：官方 2.0.0 vs 仓库现有实现

| 官方端点 | 官方方法 / 认证 / 必填 | 仓库现状 | 差异 |
|---|---|---|---|
| `/v1/asset/agreement/status` | GET / 个人访问令牌 / 无 | ✅ 已实现 `ACCOUNT_AGREEMENT`，裸 key | ⚠️ 鉴权错 |
| `/v1/logs/query` | GET / 个人访问令牌 / **无任何参数** | ✅ 已实现 `ACCOUNT_LOGS`，裸 key，不带参数 | ⚠️ 鉴权错（参数部分恰好一致） |
| `/v1/billing/balance` | GET / 个人访问令牌 / `key_id` **可选** integer | ✅ 已实现，裸 key，**不传 key_id** | ⚠️ 鉴权错；注释语义反了（见 #4） |
| `/v1/billing/usage` | GET / 个人访问令牌 / **`key_id` 必填** integer | ✅ 已实现，裸 key，只传 `start_date`/`end_date` | 🔴 **必 400** |
| `/v1/key/info` | GET / 个人访问令牌 / `key_id` 必填 | ❌ 未实现 | 新增 |
| `/v1/billing/estimate` | POST / 个人访问令牌 / model+endpoint+request | ❌ 未实现（现有 `estimate` 是**本地单价推算**，同名不同物） | ⚠️ 命名会撞车 |
| `/v1/logs/list` | GET(?) / 个人访问令牌 / 游标分页 | ❌ 未实现 | 新增（且未定稿） |
| `/v1/payment/create` | POST / **登录令牌** | ❌ 未实现 | 新增（且属写操作，超出只读范围） |

---

## 4. ⚠️ 破坏性变更：现有账户代码在 2.0.0 下会怎样

> 依据：A 文档「5. 变更记录」与 B 文档「5. 变更记录」均标注 2.0.0 / 2026-11-05，其中 3 条明确写「**破坏性**」。

| # | 问题 | 代码证据 | 后果 |
|---|---|---|---|
| 1 | **鉴权凭证全错** | `pt_video.py:119` `"Authorization": key`（裸 API Key）；`pt_video.py:104`、`:114` 注释；`pt_gateway.py:733`、`:738`；`README.md:472` / `README.zh-CN.md:472` 全篇「裸 key 鉴权」 | 2.0.0 要 `pt_live_` 个人访问令牌 → **4 个只读命令 + `account_query` + 4 个 MCP 工具全部 401**。仓库里「若 401/403 可能要网页登录令牌」的猜测现在有答案：**既不是裸 API Key，也不是登录令牌（登录令牌只用于创建支付），是个人访问令牌**。 |
| 2 | **`usage` 不传必填 key_id** | `pt_video.py:1196-1202` 只塞 `start_date`/`end_date`；`pt_gateway.py:750-756` 同 | 官方「不传 `key_id` 返回 400」→ **账单命令必失败** |
| 3 | **`key_id` 类型/来源不清** | 仓库把 `-k/--key` 当 API Key 用（`pt_video.py:1178`） | 2.0.0 的 `key_id` 是 integer 数字主键，和 API Key、脱敏字符串三者互不等价；且**无接口产出这个数字** |
| 4 | **balance 语义写反** | `pt_video.py:109` 注释「GET 余额（**按 Key 粒度**）」、`:1176` docstring、README 表格「余额查询（**按 Key**）」 | 官方：**不传 `key_id` = 账号级**；传了才是 Key 级（取 `min(Key 剩余, 账号余额)`）。现有实现不传参数，实际拿到的是账号级余额，但文档一直标成 Key 级 |
| 5 | **响应体 `code` 未校验** | `pt_video.py:126` 直接 `json.loads(raw)` 返回；仅资产上传 `:808` 检查了 `code != 200` | 官方明示「业务判断请以**响应体 `code`** 为准」。若返回 HTTP 200 + `code: 401/403/429`，现有代码会当成成功把数据交出去 |
| 6 | **`logs` 拿到的是单条** | `pt_video.py:1210-1217` 命令名「消费记录查询」 | 官方 `logs/query` 无参数、返回**单条 data 对象**；批量要 `logs/list`（但该端点还是「拟定」）。命令语义与用户预期不符 |
| 7 | **MCP / server 路由全部继承上述错误** | `mcp_server.py:156-194`、`mcp.py:125-133`、`server.py` 的 `/account/*` 四路由 | 一处鉴权修，四处受益；但 `key_id` 参数需新增 |

> **重要边界**：拍板 B4 已决定「账户相关不进 catalog，改为独立『账户客户端』模块（Node.js 版 + Python `account_query` 对齐，个人访问令牌 + `key_id` 必填，不存凭证，命名不写死 PT）」。上表 7 条**全部落在那个模块的范围里**，而该模块按 B5「本轮不做」。所以本轮**不建议动代码**；但需求文档第 5 节的接口清单，现在可以直接以这两份官方文档为准来写了。

---

## 5. 官方文档自身的矛盾与未定稿点（建议回问 PT）

| # | 矛盾 | 出现位置 | 需要的答复 |
|---|---|---|---|
| 1 | `/v1/logs/list` 方法：正文 §4 写 **GET**、附录「接口一览」第 4 行写 **POST** | B 文档 | 到底哪个？ |
| 2 | `/v1/logs/list` 路径自称「**按现有 /v1/logs/\* 命名风格拟定，最终以服务端实现为准**」 | B 文档 §4 路径下注 | **该端点是否已上线？** 还是设计稿？ |
| 3 | `limit`：「默认值与最大值**由 PT 给出**」「具体默认值与上限**待 PT 确认后补充**」「超出上限按 400 处理」 | B 文档 §4 | 默认值、上限数值 |
| 4 | `quota_per_unit` 语义漂移：`estimate` 里 = 1 USD 折算 credits（`1000`）；消费记录 `per_1k_tokens` 里 = `3`，且 `per_image` 例子里 `quota_per_unit: 1000` 与 `items[].unit_quota: 30` 对不上 | A §2 / B §3、§4 | 字段名是否应区分？`3` 的准确定义？ |
| 5 | 公共状态码表两份文档不一致：A 只有 `200/401/403/429/500`；B 多 `400/404/409/422` | 两份「公共状态码」 | 是分组差异，还是 A 文档漏写？ |
| 6 | 认证措辞：A 写「**所有接口**均需个人访问令牌」，B 写「创建支付用**登录令牌**，其余用个人访问令牌」 | 两份「认证方式」 | 确认三种凭证的适用边界 |
| 7 | 数据时效 / 保留天数 / 消费记录延迟 | 两份均未提 | Q4 第 5 条仍需答复 |
| 8 | `client_operation_id` 的传入方式 | B §4 只作为**查询过滤条件**出现 | Q2 核心，仍需答复 |
| 9 | 变更记录日期：A 的 1.0.0 首版写 `2026-10-30`，B 的 1.0.0 首版写 `2026-11-04` | 两份「5. 变更记录」 | 两个分组各自首版日期不同，还是笔误？ |

---

## 6. 对 catalog v2 决策的影响

| 决策 | 原状态 | 本次变化 | 可否落地 |
|---|---|---|---|
| A2 `currency.credits_per_usd` | `null` 待 PT 确认 | ✅ **可填 `1000`**（official），注明以 estimate 的 `quota_per_unit` 为实时准 | ✅ 解锁 |
| A2 `use_estimate_api` | 待 Q7 | ✅ 端点确认；**media=true / chat=false** | ✅ 解锁（但属 P2，本轮不填） |
| A2「准确费用以预估接口为准」 | 已锁定 | ⚠️ **需改口径**：官方承认可能偏差 → 「事前预估 estimate，最终准确值消费记录 `quota`/`final_quota`」 | ⚠️ 需你确认后修订决策 |
| A2 `pricing.billing_unit` 枚举 | 待定 | 官方取值：`per_second`/`per_image`/`per_call`/`per_token`/`per_1k_tokens` | ✅ 有参考 |
| 需求 4 `errors` 字典 | 待 Q5 | 🔴 精确报文仍缺；**新增约束：账户域码表（无 402）不得混入生成域 401/402/403** | ❌ 仍阻塞 |
| B3 测试 Key 方案 | 已锁定 | ✅ `/v1/key/info` 的 `status` + `model_restriction_mode` 提供官方可配置依据 | ✅ 可行性 +1（脚本属 P3） |
| B4 账户客户端模块 | 后置 | ✅ **接口清单、认证、参数、响应结构现有官方全文依据**；但暴露 7 条现有实现差异 | ⏸ 仍后置 |
| B4 catalog 唯一的 `request_id` 字段 | 待 Q3 | 🔴 两份文档都是账户域，**仍未回答** | ❌ 仍阻塞 |
| Q2 / `charging` 幂等字段 | 待 Q2 | 🟡 仅确认字段存在 | ❌ 仍填 `null` |

---

## 7. 阻塞状态：未变

这两份文档**没有解除** `docs/catalog_v2_决策锁定_20261112.md` §4 的开工阻塞：

1. **重写后的模板全文**（`auto_policy.purposes`、`pricing` / `charging` 拆分、`pricing.rates[].when`、`capability_gaps` 第 5 节）——未提供；
2. **需求文档第 5 节全文**（账户客户端模块接口清单）——未提供（现在可用本文档 §1/§3 作为事实底稿，但字段形状仍不能猜）；
3. **`--max-usd` 单次上限数值**（B3 里写的是 `___ 美元`）——未填。

按拍板规则「不知道就填 null，不要猜」，**`catalog.json` / `catalog.schema.json` / 测试代码一行未动**。

---

## 8. 建议的下一步（等你拍板）

| 优先级 | 动作 | 依赖 |
|---|---|---|
| P0 | 把 §5 的 9 条矛盾整理成**给 PT 的第二轮追问**（尤其 #1/#2/#3：`logs/list` 到底上没上线、GET/POST、limit） | 无 |
| P0 | 补齐 3 项开工输入 → 按锁定稿 §5 一次性做 5 个批次 | 模板全文 / 需求文档第 5 节 / `--max-usd` 数值 |
| P1 | 确认 A2「准确费用以预估接口为准」是否按 §6 建议改口径 | 你一句话 |
| P2 | 账户客户端模块立项时，以本报告 §3/§4 作为「现有实现迁移清单」（7 条差异） | B5 解禁 |
| P2 | 生成本域 `request_id` 位置：用现有 Key 真机抓一次 `/v1/chat/completions` 响应（header + body），解决 Q3 | `--max-usd` 数值 + 一次极低成本调用 |
