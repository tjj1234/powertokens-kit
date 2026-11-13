# Changelog

本文件记录 `powertokens-kit` 可交付版本的变更。catalog 的字段级决策依据见
`docs/catalog_v2_决策锁定_20261112.md`；与 PT 官方 2.0.0 文档的核销见
`docs/PT_API_2.0.0_官方答复核销_20261112.md`。

---

## 0.7.3 — 2026-11-13 · `/v1/models` 上线：F1 口径作废与字段契约落地

**范围**：PT 丰富 `GET /v1/models` 后，两批书面答复（6 问 + 6 答）固化进 SSOT §13；
双 Key 影子对账（免费只读、0 花费）证实「列表是 Key/分组可见视图」，据此修正 `ModelRegistry.sync()` 的下架语义，
并把官方字段契约与观测写入 `catalog.json`。**本轮动了代码与 catalog 数据**（前两轮是纯文档）。

- **F1 作废**：`FIXLIST.md` F1「`/v1/models` 全量、所有 key 相同」不再成立。真实响应按
  「Key 模型限额 → 分组启用模型 → `HasPricingRuleCandidateForModel` + `IsModelNameListed` 双重过滤」生成。
  证据：双 Key 各 77 个且完全一致；77 ⊂ 89 文档样例；catalog 15 个不在本视图，
  其中 `kling-v2-1-master` 同日 `estimate` 仍可计价 1400 credits。
  **不恢复已删的双层架构 / fail-closed**（服务端返回单个已过滤列表）。
- **代码**：`sync()` 新增 `not_in_key_view` 状态与 `confirmed_delisted` 参数，不再自动 `delisted`；
  新增 `mark_delisted(ids, reason)` 作为唯一显式下架入口；`list_capabilities()` 透出新字段。
  旧逻辑会把「本 Key 暂时看不到的可用模型」永久误踢（含 10 个 chat），属静默故障。
- **数据**：`catalog.json` 新增顶层 `models_api_contract`（`_source=models_api`，含字段口径、六能力判定规则、
  影子对账观测），`note` 订正、`updated` → `2026-11-13`；**86 个模型的 `capabilities` 一条未改**（不批量回填）。
- **A5 决策变更**：`_source` 8 → **9 值**（新增 `models_api`）。三道守门员同步：
  `catalog.schema.json`（枚举 + 新节点声明）、`validate_catalog.py`（+3 断言）、`tests/test_unit_catalog.py`（+1 用例）。
- **测试**：`tests/test_unit_registry.py` 旧「消失即下架」断言改为新语义，新增 5 条回归；
  `test_unit_gateway.py` +2 字段断言。
- **文档**：`FIXLIST.md` 新增 §9（两批答复 / 证据链 / 落地 / 未决）；双语 README 共 20 处口径订正；
  `mcp.py` / `pt_video.py` / `pt_gateway.py` 注释；SSOT 新增 §13；看板升级 v2.2；详细版 v1.2。
- **验收**：`validate_catalog.py` **137 项全 PASS**（133 → 137）、`run_tests.py` **265/265 PASS**（260 → 265），
  两个退出码 0；全程 0 花费、凭证未入库、输出脱敏。
- **未决**：`dola-seedream-5-0-pro-260628` 输出模态冲突、5 个 vision 归属、20 个 `web_search=conditional`、
  `reasoning_effort` 关闭取值、`thinking_param` 收敛、C4 视频侧 / R1 异步（需 ≥1000 credits）。见 SSOT §13.8。

---

## 0.7.2 — 2026-11-13 · 账户域真机实测回写（纯文档）

**范围**：用户提供 `pt_live_` + 数字 `key_id` 后，零成本跑完账户域只读端点与不计费 estimate；
只动 `docs/` 与 `CHANGELOG.md`，**未改任何 `.py` / `catalog.json` / 测试 / CI / README**。
凭证未入库、未进日志、输出脱敏；账号余额 80 credits，**全程 0 花费**。

- **C1 裁决**：账户域 `Authorization: pt_live_<token>` **裸值**（不带 `Bearer`）；
  D4 §7 的 Bearer 在账户域不成立。现有 `_account_call` 裸值写法保留，B4/B5 只需换凭证源。
- **T6 验证**：`POST /v1/billing/estimate` 整包 `{model, endpoint, key_id, request}` 被接受；
  连跑两次 `balance` 均为 80 credits，**确认不计费**。
- **T7 / T8**：balance / usage 需数字 `key_id`（usage 还需 `start_date` / `end_date`）；
  agreement 返回协议状态与资产库开关。
- **R3 补完**：`POST /v1/key/info` 返回数字 `key_id` + `key_masked`；`/key/info` 为 404；
  balance / usage 响应仍把脱敏串塞在 `key_id` 字段（D4 §5 的「将统一」尚未上线）。
- **新增 6 条偏差登记 N1–N6**：logs 按 ID 精查（非分页列表）、estimate 不支持 chat、
  balance 的 `key_id` 必填、`/key/info` 路径要带 `/v1`、usage 日期必填、D4 §7 Bearer 不成立。
- **仍未闭合**：C4 视频侧 / R1 异步 —— `wan3.0-video` 5 秒试算 1000 credits、
  `kling-v2-1-master` 1400 credits，余额仅 80 credits，如实保留未裁决。
- **文档**：SSOT 新增 §12（实测全章）并更新 §3 C1 / §10.2.1 / §11.4；详细对照版升 v1.1；
  看板升 **v2.1** 并同步 KPI、冲突表与未决项。
- **验收**：`validate_catalog.py` **133 项全 PASS**；`run_tests.py` **260/260 PASS**（纯文档改动后复跑）。
- **发布**：已提交并推送 `origin/main`；commit 号以 `git log -1 --oneline` 为准。

---

## 0.7.1 — 2026-11-13 · PT 资料补漏与看板 v2.0（纯文档）

**范围**：只动 `docs/` 与 `CHANGELOG.md`；**未改任何 `.py` / `catalog.json` / 测试 / CI / README 内容**，生成链路功能代码仍是 0 行改动；未引入新事实。

- **新增** `docs/PT_问题_答复_影响功能_全量对照_20261112.md`：Q1–Q8 / R1–R11 / S1–S3 / D4 10+2 逐条「原始提问 → PT 答复 → 影响到的功能与文件 → 最终处置 → 对应文档」；§3 功能域总映射、§4 PT 原表固化、§8 文档全量索引与速查。
- **升级** `docs/PT_项目资料总集_看板_20261112.html` → **v2.0 完整版**（10 节 + 锚点导航 + 影响功能筛选/搜索）：六批时间线、22 项编号对账、F1–F12 功能域映射、D4 逐条答复、9 处冲突含实测裁决、T0–T8、catalog v2.1 落地、文档索引、未决项。v1.0（开工前决策摘要）保留于 git 历史。
- **回填** `docs/PT_项目资料总集_20261112.md`：`§1.4` 台账 21→25 份、新增 `§1.4b` 测试报告资产、`§10.3` 补 `4ce26f4` / push 方式 / 远程校验 / 30 文件 / 排除项、新增 `§11` 补漏章；`§1.5 / §5 / §2.7 / §4.2` 加开工基线与真机交叉注记（历史原文不改写，只加导航）。
- **修正** 本文件 `0.6.0` / `0.7.0` 标题串行与 0.6.0 正文缺标题；补 0.7.0 最终验收口径。
- **验收**：`validate_catalog.py` **133 项全 PASS**；`run_tests.py` **260/260 PASS**（2026-11-12 18:52 复跑，退出码均 0）。
- **发布**：已提交并推送 `origin/main`；commit 号以 `git log -1 --oneline` 为准。

---

## 0.7.0 — 2026-11-12 · `catalog.json` v2.1（PT 12/12 答复落地）

**范围**：G1–G4 + G5 真机测试 + G6 发布。依据 `docs/PT_项目资料总集_20261112.md` §6.3。
**不做**：生成链路功能代码（认证/端点/请求构造全部不变，N1）、真接供应商兼容接口（N2）、
账户客户端 Bearer `pt_live_` 改造（N3，B4/B5 后置）。

- **G1 · `errors` 8 处数据订正**（`errors` 仍恰好 9 类，未新增类）：
  - `upstream_error`：`http_status` `[502, 503]`，OpenAI error 格式 + `message` 追加 request_id + 自动 Refund；
  - `timeout`：`[500]` + `do_request_failed`，**删掉 notes 里「Q8 未答」过时表述**；
  - `rate_limited`：`model_rate_limited`、`Retry-After` 一定有（网关默认 5s）、预扣费前限流不产生费用；
  - `insufficient_balance`：`examples[1]` 历史 403 余额报文标注「按新版作废，统一 402」（C9）；
  - `auth_invalid`：补 `token_invalid` / `expired` / `disabled`；
  - `param_unsupported` / `context_too_long`：`[400]` + 上游原 code/type + `context_length_exceeded` 报文；
  - `model_unavailable`：`[403, 503]` + D4 §1 四个 403 细分 code + 503 同 code「无可用渠道」，
    第五批对比表第 8 行作废（C5 解锁）。
- **G2 · 新增字段**：
  - 顶层 `retry_policy` — 4+1 条受理/重试规则 + `X-Oneapi-Request-Id` 仅作报障定位；
  - 顶层 `charging` — **按链路分叉**（C6）：`async_task.charged_on_failure=false` 全额退，
    `sync_chat_stream.charged_on_midstream_failure=true` 按量结算且 `log_status=success`；
  - `kinds.seedance.interface_variants` / `result_persistence` — 双端点表 + 标准永久 / 兼容接口 24h +
    C4 冲突 caveat；字段名避开「透传」二字；
  - `currency.notes` 补 C3（设计层汇率可配 vs 对外固定 1000）。
- **G3 · 三道守门员同步**：`catalog.schema.json`（+2 顶层节 / +4 definitions）、
  `validate_catalog.py`（+55 行 v2.1 校验，含真机结论防回退）、
  `test_unit_catalog.py`（+14 个断言：v2.1 结构 9 + 真机结论 5）。
- **只加字段 + 列明的数据订正**：原子写前逐条 deep-equal，86 个模型、`note`、
  4 类 kinds、`auto_policy`、`capability_gaps`、`errors.content_filtered` 全部未动；
  `schema` 保持 2（v2 内订正，不升版本）。
- **验收（G1–G4 阶段）**：`validate_catalog.py` 128 项全 PASS；`run_tests.py` 255/255 PASS（246 → 255）。
- **最终验收（含 G5 真机结论回写）**：`validate_catalog.py` **133 项全 PASS**；
  `run_tests.py` **260/260 PASS**（连跑两次稳定）；`latest.md` 记录 2026-11-12 18:52 全绿（本轮资料补漏后复跑）。
- **G5 真机测试**：见 `docs/PT_项目资料总集_20261112.md` §10.2 与
  `docs/PT_发布与测试报告_20261112.html`；预算硬上限 0.5 USD，`T2/T3/T5` 分别
  裁决 C1（当前凭证无法裁决）、补 R1（`body.id`）、澄清 C4（图像链路 24h OSS 签名 URL）。
  **三条真机硬结论** R1 / C4 / C9 已按 P0 规则回写 catalog；C1 与 C4 视频侧如实保留待实测。
- **口径更正（C2）**：0.6.0 记录的「其他接口按 access key + key id」已被 D4 §7 判为
  **渠道（上游）配置字段、文档笔误**；对外接口统一 `Authorization: Bearer pt_live_<token>`，
  **不要做 AK/SK 双头**（账户客户端改造按 B4/B5 后置）。
- **G6 发布**：`main` HEAD `4ce26f4` 已 push `github.com/tjj1234/powertokens-kit`，
  远程 `main` == 本地 HEAD、工作区 CLEAN、30 个文件、未打 tag。PT 官方附件与任何
  Key / 令牌 / 签名值一律不入库。

---



## 0.6.0 — 2026-11-12 · `catalog.json` v2（P0）

**范围**：按 14 条拍板决策，只做 catalog 的 P0 字段 + 需求 2 / 3 / 4。
**不做**：DSH 插件化、账户客户端模块、P2 的 `pricing` / `charging`、需求 5/6/7/10/11/12/13。

- `schema` 1 → 2，`updated` → `2026-11-12`。**只加字段，不改已有字段**：原子写入前对原有值做逐条深比对，
  顶层 `note` / `kinds` / `multimodal_source` 与非主力模型条目完全未动。
- 新增 4 个顶层节：
  - `currency` — `credits_per_usd = 1000`（`_source: official`，PT API 2.0.0 `/v1/billing/estimate`）；
    `3` 是 `per_1k_tokens` 的 `quota_per_unit`，非换算率。
  - `auto_policy.purposes` — AUTO 第一层，按用途的**有序候选列表**；本轮落 `agent` / `search`
    两种用途，各 3 个跨渠道候选，`_source: product`。附 A1 的 4 条跳过规则。
  - `errors` — 生成域 9 类错误码字典，含 A.04 官方 401/402/403 语义与 3 条真机报文
    （`token quota is not enough`、`pre-consume quota failed`、`model_not_found ... not listed (pending)`）；
    **账户域状态码表（含 500、无 402）未混入**。
  - `capability_gaps` — 5 类当前不可调用能力（ASR / TTS / OCR / Embeddings·Rerank / 实时语音），
    每项 `in_kinds: false`；`kinds` 仍只有 `image` / `video` / `seedance` / `audio` / `chat` 五类（B1）。
- 7 个主力模型（`glm-4.7` / `deepseek-v4-pro` / `qwen3.8-flash` / `seed-2-0-pro-260328` /
  `MiniMax-M3` / `glm-5.2` / `mimo-v2.6-pro`，覆盖 6 渠道）追加 `limits` / `tool_calling` / `routing`；
  其中 4 个 `web_search = "conditional"` 的模型另加 `web_search_detail`，
  `how_to_enable` 从 `capabilities.web_search_param` 原样复制（A4）。
- **未实测的值一律 `null`**（A3）：`tool_calling.verified`、`works`、`returns_sources` 全部为 `null`，
  来源标 `inferred` / `configured`，无任何假 `true`。`limits` 的子字段形状因模板全文未落地，
  本轮只落结构占位与来源标注，未擅自补 `max_context_tokens` 等子键。
- `_source` 统一 8 值（A5）：`official` / `documented` / `verified` / `official+verified` /
  `inferred` / `configured` / `verified(unmapped)` / `product`；其中 `product` **仅用于 `auto_policy`**，
  `routing` 用 `configured`。`verified(unmapped)` 当作已知处理（README 双语已补说明）。
- 需求 2 基础设施：
  - `pt-gateway/catalog.schema.json` — 给 Agent / 使用方的**格式说明**（不参与运行时强校验）。
  - `pt-gateway/validate_catalog.py` — **手写零依赖校验器**，退出码 0/1。
  - `pt-gateway/tests/test_unit_catalog.py` — `VALID_SOURCE` 统一 8 值 + v2 结构断言。
  - `.github/workflows/ci.yml` — 最小 CI，**只跑** `python pt-gateway/tests/run_tests.py`；
    不跑 live、不做 pre-commit。
  - `pt-gateway/tests/test_contract_mcp.py` — 顺带校正 0.5.0 遗留的「只暴露 3 个工具」断言为 4 个
    （含 `account_query`）。**与本轮 catalog 无关**，仅为让离线 CI 全绿；未改任何 MCP 功能代码。
- 账户接口最新口径固化于 `docs/PT_账户接口_默认模块依据_20261112.md`：
  生成域仍用 API Key；其他接口按 access key + key id；**`client_operation_id` 已移除**；
  `data` 为字符串化 JSON 需二次解析。**账户客户端模块本轮不建**（B4/B5 后置）。

**待核对 / 未落地（不阻塞本轮 P0）**

| 项 | 状态 |
|---|---|
| `limits` 子字段形状、`auto_policy.purposes` 用途键全集与元素形态、`capability_gaps` 精确格式 | 需模板全文核对后一次补齐（决策锁定稿 §4） |
| P2 `pricing` / `charging` | 本轮不填 |
| `--max-usd` 真机单次预算数值 / 真机实测 `works` 等 | 未给，P0 按 A3 填 `null` |
| 生成响应里 `request_id` 的精确位置（Q3） | PT 未答 |
| A2「准确费用以 estimate 为准」措辞修订 | 待用户一句话确认，未写入 |

---

## 0.5.0

新增**只读账户 / 计费命令**：CLI `balance` / `usage` / `logs` / `agreement` 与配套 MCP 工具
（`query_balance` / `query_usage` / `query_logs` / `query_agreement`）；`seedance status` 现在
best-effort 真查服务端签署状态；补充账户 / 计费 / 资产库端点所在的 `powertokens.ai/api` 裸 key 域说明。
仓库双语化（英文为默认 README）。
