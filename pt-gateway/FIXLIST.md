# PT Gateway —— 待修正 / 待决策清单（FIXLIST）

> 记录 pt-gateway 开发讨论中暴露的「说错 / 待修正 / 架构决策」事项。
> 最近一轮大改：**2026-11-13（第三轮）** —— **F1 口径作废**（`/v1/models` 是 Key/分组可见视图）、
> `sync()` 下架语义修正、`_source` 扩 9 值、`/v1/models` 结构化字段上线。见 **§9**。
> 上一轮：2026-10-30（第二轮）—— `/v1/models` 语义澄清、取消接口删除、爬虫删除、chat 打通。
> 标记：`[已定]` = 已拍板并实施；`[待办]` = 还需动作；`[已废弃]` = 方案作废（保留以溯源）。

---

## 1. 平台事实（2026-10-30 与用户 / 官方确认，是其余决策的前提）

| # | 事实 | 影响 |
|---|---|---|
| F1 | ~~`GET /v1/models` 返回 PT **全量**模型清单，**所有 key 相同**~~ → **已作废 2026-11-13**：真实响应按「Key 模型限额 → 分组启用模型 → `HasPricingRuleCandidateForModel` + `IsModelNameListed` 双重过滤」生成，是**本 Key / 分组可见视图** | 「不在列表」≠ 下架 → `sync()` 改为只标 `not_in_key_view`，保持 `enabled`；确认下架须显式 `mark_delisted()`。**详见 §9** |
| F2 | PT **不提供**异步任务取消接口 | 网关不实现 cancel；HTTP 无 `/cancel`，MCP 不暴露取消工具 |
| F3 | 提交 / 查询统一：`POST /v1/videos` + `GET /v1/videos/{id}` | 只需归一化请求体构造与结果字段位置，即可跨模型 |
| F4 | audio 走 `POST /v1/audio/speech`（同步，OpenAI 兼容）；chat 走 `POST /v1/chat/completions`（同步） | 这两类不进 `/v1/videos` |
| F5 | TTS 的 `voice` **必填**（缺则报 `voice is required`）；OpenAI 标准音色 `alloy/echo/fable/onyx/nova/shimmer` 实测全部可用 | 音色名用 OpenAI 的，不是 MiniMax 原生 voice_id |

> **历史备注（保留溯源，2026-11-13 重新标注）**：更早曾以为「不同 key 的 `/v1/models` 列表不同」，
> 由此设计了双层架构 / 公开层爬虫 / fail-closed 门禁；2026-10-30 依据「全量清单」把它们全部作废。
> **2026-11-13 PT 答复证明「按 Key / 分组过滤」这一半其实是对的**，但结论不变：服务端返回的是
> **单个已过滤列表**，不需要客户端双层架构，也不恢复 fail-closed。真正要改的是「不在列表即下架」。

---

## 2. 决策项

### 2.1 模型清单与权限判定 [已定]

- **真相源**：`GET /v1/models`（**本 Key / 分组可见视图**，非平台全量清单）。`sync()` 据此 diff：
  出现 → `in_models=true`、`enabled=true`、`not_in_key_view=false`、新出现打 `is_new`；
  不再出现 → `in_models=false`、`not_in_key_view=true`，**保持 enabled、不自动 delisted**。
- **确认下架**：`mark_delisted(ids, reason)` 或 `sync(confirmed_delisted=[...])`（需跨 Key 复核 / PT 公告）。
- **权限**：**fail-open，不做客户端拦截**（用户拍板，2026-11-13 复核不变）。本 key 对某模型无权限时，
  **由 PT 服务端报错**，网关原样透传。
- **已废弃并删除**：双层（公开层 + key 层）架构、`key_permitted` 字段、
  `_key_permitted_models()` / `_enforce_key_permission()` 门禁、`auto_select(permitted=...)` 权限过滤。

### 2.2 catalog 能力自动补（enrich）[已废弃]

- 原方案依赖公开层爬虫产出的 `catalog.public.json`。爬虫已按用户决定**彻底删除**，enrich 失去数据源。
- `enrich_catalog()` / `enrich_with_llm()` / CLI `enrich` / `public` 已一并删除。
- 能力档案仍由人工维护的 `catalog.json` 提供。
  **⚠️ 括号里那句是 2026-10-30 的时点说明，2026-11-13 起已过时**：新接口会返回
  `architecture` / `supported_parameters` / `reasoning` / `pricing` 等结构化字段（见 §9）；
  但仍**不给**可作计费依据的单价、以及分辨率 / 时长 / 模式，所以人工能力档案继续保留。

### 2.3 音频请求体 / 响应字段 [已解决：2026-10-30 真机实测]

实测（`speech-2.8-turbo`，真 key 实跑）：
- **端点是 `POST /v1/audio/speech`（OpenAI 兼容）——此前假设的 `/ali/...` 是错的**（该路径 404 / 500）。
- 请求体 `{model, input, voice}`（**不是** `{model, input:{prompt}}`）；`voice` 必填。
- 音色：OpenAI 标准名 `alloy / echo / fable / onyx / nova / shimmer` 实测全部 200；非法值报 `voice id not exist`。
- 响应：`{data:{audio:<mp3 url>, status:2}}`，url 在 **`data.audio`**；附 `extra_info`。
- 平台侧异常（非网关问题）：`qwen3-tts-instruct-flash` → 404 `Model not exist`；
  `audio1.0` / `speech-2.6-hd` → 500「倍率或价格未配置」。

### 2.4 取消接口 [已定：不做]

- PT 无异步任务取消接口（已与官方确认）。
- 已删除：`pt_gateway.cancel()`、`SEEDANCE_CANCEL` 常量、`server.py` 的 `/cancel` 路由、
  MCP 与 README 中的取消说明。
- 注：Seedance 的 `DELETE /byteplus/...` 是 ByteDance vendor 端点（实测可将任务置为 CANCELLED），
  但按用户决定**不在网关实现**。

### 2.5 轮询健壮性 [已实施]

- 网关侧 `poll_task` 已覆盖为：429 指数退避 + 5xx 连续 5 次熔断 + 4xx 透传 + 超时明确报错。
- 引擎 `pt_video.py` 仍是旧实现（10s 间隔 + 600s 上限），勿混谈。

### 2.6 chat（文本）支持 [已实施]

- 用户拍板：目录里的 38 个 chat 模型要能真的用。
- 实现：`KNOWN_KINDS` 加 `chat`；新增 `chat_generate()`（OpenAI 兼容，同步返回 `text`）；
  `media_generate` 支持 `messages` / `system` / `temperature` / `max_tokens`；
  `catalog.json` 补 `kinds.chat` 节点。
- `_kind_of()`：引擎 `kind_of_model()` 对 chat / TTS 返回 `unknown`，
  回退查 `catalog.json` 的 `kind` 字段兜底（带目录缓存，避免逐模型重读文件）。

---

## 3. 已废弃的交付物

| 文件 / 概念 | 结局 |
|---|---|
| `catalog_crawler.py` | 已删除（无需再爬官网取清单；2026-11-13 复核：服务端返回单个已过滤列表，仍不需要客户端双层） |
| `catalog.public.json` | 已删除 |
| 公开层 / 双层架构 | 已废弃，改为单层 `/v1/models` |
| fail-closed 权限门禁 | 已撤销，改 fail-open |

---

## 4. 本轮实施记录（2026-10-30 第二轮）

- [已修] **P0 致命 bug**：`IMAGE_ENDPOINT` / `AUDIO_ENDPOINT` / `SEEDANCE_CANCEL` 在
  `pt_gateway.py` 中**只有使用、没有定义** —— 带 key 调用 image / audio 会直接 `NameError`。
  （此前一直被「缺 key → `_need_key()` 先抛 401」掩盖，从未暴露。）现已补齐并集中定义。
- [已修] **MCP 参数缺失**：`media_generate` 补齐 `no_audio` / `no_prompt_extend` / `seed` /
  `watermark` / `timeout_s` / `voice` / `params_json` / `messages_json` / `system` /
  `temperature` / `max_tokens`。此前 MCP 比 HTTP 弱，agent 做不了「无水印」「固定种子」「选音色」。
- [已修] **MCP docstring 过时**：「audio 诚实 unsupported」「取消端点待确认」均与实际不符，已改。
- [已修] **目录误导**：`catalog.json.models` 含 38 个 chat，但 `kinds` 无 chat 节点，已补。
- [已修] **SYNC_KINDS 不一致**：原为 `("image",)`，缺 audio；现为 `("image", "audio", "chat")`。
- [已验证] 四类端点路由（mock 网络实测）：chat → `/v1/chat/completions`、
  audio → `/v1/audio/speech`、image → `/v1/images/generations`、video → `/v1/videos`；
  只给 model 不给 kind 时 `_kind_of()` 能正确反推；未知 kind 报错清晰。

---

## 5. 待办

1. ~~§2.3 音频实测~~ [已完成 2026-10-30]：端点 / 请求体 / 响应字段已固化（见 §2.3）。
2. ~~全链路真机验证~~ [已完成 2026-10-30]：5 项全 PASS，见 §6。
3. 其余无待办；模型或价格变动时同步 `catalog.json`。

---

## 6. 真机验收（2026-10-30，真 key 实跑）

`GET /v1/models` → 83 个模型（image 9 / video 24 / seedance 7 / audio 8 / chat 35），网关可路由 83/83。

| 步骤 | 模型 | 结果 | 耗时 | 花费 |
|---|---|---|---|---|
| 模型清单 | `GET /v1/models` | PASS 83 个 | 0.37s | 免费 |
| chat | `qwen3.8-flash` | PASS 输出「你好」87 tokens | 1.3s | $0.0001 |
| image | `seedream-5-0-260128` | PASS 出图 1 张 | 22.7s | $0.034 |
| audio | `speech-2.8-turbo` | PASS（修正端点后） | 2.8s | $0.010 |
| video | `wan3.0-video` 720p 5s | PASS `SUCCEEDED` 出片 | 144.9s | $0.500 |

**合计约 $0.544**（人民币约 4 元）。

本轮唯一代码修正：音频端点 `/ali/...` → `/v1/audio/speech`，请求体改 OpenAI 风格 `{model, input, voice}`，响应 url 取 `data.audio`。

---

## 7. 测试体系补建（2026-10-30 第三轮）

补齐「生产级」测试：`tests/`（unit / contract / live 三层，入口 `tests/run_tests.py`）。
旧的 `e2e_test.py`（一次性冒烟脚本，5 步、无断言体系、无法在 CI 跑）已删除。

### 7.1 写测试时新发现的缺陷

| # | 级别 | 问题 | 处置 |
|---|---|---|---|
| N1 | 数据 | `catalog.json` 有 **5 个条目缺 `execution` / `submit` / `poll` / `vendor_endpoint`**（`kling-v3`、`kling-video-o1`、`kling-v2-1-master`、`qwen-image`、`kling-image`）——agent 读到会不知道该同步还是该轮询 | 已按 kind 推导补齐 |
| N2 | 数据 | **8 个 audio 条目 `submit` 仍写着已废弃的 `/ali/api/v1/services/aigc/...`**（实测 404/500）——目录在误导调用方 | 已改为 `/v1/audio/speech`，上游厂商提示挪到 `upstream_endpoint` |
| N3 | 数据 | **38 个 chat 条目 `submit` 为 `null`** | 已填 `/v1/chat/completions` |
| N4 | 代码 | `poll_task` 的 429 `Retry-After` **永远拿不到**：引擎 `ApiError` 不带响应头，`_retry_after()` 恒返回 `None` → 退化成指数退避（不致命，但退避不够聪明） | **[已修 2026-11-02]** `pt_video.ApiError` 新增 `headers` 参数并在 `_api_call` / `_upload_asset` / asset 轮询三处 HTTPError 转换时透出 `e.headers`；`pt_gateway._retry_after` 优先读 `err.headers`（大小写不敏感），`raw["headers"]` 保留兼容；并新增 deadline 保护（`Retry-After` 超过剩余超时则快速失败，不硬等）。新增 5 条回归用例（含 urllib 层造假 429 的端到端链路验证），离线 224/224 全绿 |
| N5 | 数据+配置 | **`deepseek-v4-flash-0731` 已下线但残留**：① 真机档位 `harness.TIERS["full"].chat` 引用它，导致 `full` 档首次真机 `test_03_chat` 报 403 `model_not_found (not listed, pending)`；② **`catalog.json` 也仍收录它（kind=chat）**，而 PT 当前 `/v1/models`（83 个）已无此 ID。属于「模型已下线但目录没收」 | **[已处置配置 / 待决策数据]** ① 档位已改用当前在册的 `deepseek-v4-1-flash-260910`（catalog 与 live 清单都有），并加 `TestTierModelReferences` 离线守卫（以后再引用失效/拼错模型，unit 层立刻红，不必烧真机）；② catalog 里的 `deepseek-v4-flash-0731` 条目**建议删除或标记 deprecated**（fail-open 下网关仍会路由后被服务端 403 拒绝，不崩溃，但会误导 agent 认为该模型可用）。是否清目录待用户拍板 |
| N6 | 数据+接口 | **chat 模型缺能力标注**：agent 选 chat 模型时无法知道它「多模态 / 联网搜索 / 思考模式」等，只能瞎试浪费 token。且 PT `GET /v1/models` **不返回任何能力字段**，纯靠 API 拿不到 | **[已处置 + 校正 2026-11-02]** ① 每个 `kind=chat` 条目加结构化 `capabilities`；② 来源分层：`official`=PT 官方《模型能力汇总-websearch与思考模式》(2026-11-02，依据网关渠道适配器+各渠道 OpenAPI 文档)、`verified`=真机探测 vision、`official+verified`=二者合并；③ **官方数据补齐 `web_search` / `thinking`**：按 6 渠道映射每个 chat 模型，取值 `true/false/conditional/null` + 实际参数提示；④ 新增 `probe_chat_capabilities`（CLI `probe-cap [--all --persist]`）；⑤ **2026-11-02 续：官方新增 §4 逐模型多模态清单**，据此把 `multimodal` 升级为权威数据（新增 `video_in`，`seed-*`/`dola-seed-*`=`true`），并把早期探针的**假阴性**纠正——探针只发 `data:image/png;base64` 单图，对 **ali-qwen3 / BytePlus-seed / zhipu-glm-5.3-flash / deepseek-flash-v3-2** 误判 `vision=false`，官方 §4 明确这些均 ✅ 图，已用文档覆盖（共 21 个模型 `vision` 由 false/null → true，`_source` 置 `official`）；仅 `qwen3-coder-plus` 因未列入清单保持 `null`；⑥ `TestChatCapabilitiesIntegrity`（结构/渠道/取值/参数/`video_in`/来源）+ `TestProbeChatCapabilities` 守着。详见 README §9.1 |

> N1–N3、N6 由 `tests/test_unit_catalog.py::TestCatalogDataIntegrity` / `TestChatCapabilitiesIntegrity` 守着，改坏即红。

### 7.2 测试的三条硬规则

1. 离线层禁网禁真 sleep：`_api_call` / `submit_task` 打成守卫（漏打桩立刻红），`time` 由 `FakeClock` 接管。
2. live 双门禁 + 预算守卫：`PT_LIVE=1` 且拿到 key，`Budget` 累计超 `--max-usd` 即 SKIP。
3. 报告禁密钥：写盘前扫完整 key + `sk-` 正则，命中即失败不落盘。

### 7.3 待办更新

- [x] 补建测试体系（unit 163 条 / contract 41 条 + live 10 条，全绿）
- [x] live 层跑 `standard` / `full` 档（2026-11-02 补完，见 §8.1）
- [x] **N5-配置**：`harness.TIERS["full"].chat` 改用 `deepseek-v4-1-flash-260910` + 加离线守卫 `TestTierModelReferences`
- [x] **N6**：chat 模型加 `capabilities` 标注 + 官方渠道表补齐 `web_search`/`thinking`（含参数提示）+ `probe_chat_capabilities` 真机探测 vision + 全量写回（见 README §9.1）
- [x] **N6-多模态校正**：官方 §4 逐模型清单取代探针假阴性，`video_in` 维度补齐，21 个模型 vision 由 false/null → true（见 N6 行）；仅 `qwen3-coder-plus` 未列入清单保持 `null`
- [ ] **N6-后续**：`conditional` 类 `web_search`/`thinking` 建议在真机实测时抽样验证「客户端显式传参后上游确实生效」（ali `enable_search` / byteplus·zhipu `tools:[{type:web_search}]` / zhipu·mimo `thinking`）；`qwen3-coder-plus` 的 vision 待上游实测
- [x] **N4**：让引擎 `ApiError` 携带响应头，使 429 `Retry-After` 真正生效（2026-11-02 已修 + 回归用例）
- [x] **N5-数据**：`catalog.json` 中 `deepseek-v4-flash-0731` 已下线条目**已删除**（非 deprecated 标记）：真机证明平台级 `403 model_not_found`，且原条目无 deprecated 标记，留着会误导用户以为可用。同步移除 `tests/harness.py` 价格表死条目、`README.md` §9 多模态表里该模型示例。

---

## 8. 真机验收（2026-10-30 第三轮：新测试体系首次真机）

命令：`python tests/run_tests.py --live --tier minimal --max-usd 2.0`
结果：**214 条全绿（PASS 214 / FAIL 0 / ERROR 0）**，用时 104s，**计入花费约 $0.199**。

| 步骤 | 模型 | 结果 | 耗时 | 花费 |
|---|---|---|---|---|
| `/v1/models` | — | PASS 83 个（image 9 / video 24 / seedance 7 / audio 8 / chat 35） | 0.89s | 免费 |
| registry.sync | — | PASS total=83 enabled=83 | 0.35s | 免费 |
| chat | `qwen3.8-flash` | PASS 输出「你好」（91 tokens） | 2.85s | $0.000075 |
| image | `seedream-5-0-260128` | PASS 出图 1 张 | 19.2s | $0.034 |
| audio | `speech-2.8-turbo` | PASS（voice=alloy） | 1.91s | $0.010 |
| video | `dreamina-seedance-2-0-mini-260615` 480p 5s | PASS `SUCCEEDED` 出片 | 77.3s | $0.155 |
| 单查状态 | 复用上一步 task_id | PASS `SUCCEEDED` | — | 免费 |
| 错误路径 | 不存在的模型 | PASS 服务端 403 `model_not_found` 原样透传 | — | 免费 |
| 参数校验 | kind=hologram | PASS 发请求前即拒绝（0 花费 0 调用） | — | 免费 |
| 防泄漏自检 | — | PASS 日志无完整 key | — | 免费 |

报告：`tests/reports/latest.md`（+ 时间戳 `.md` / `.json`）。

## 8.1 真机验收（2026-11-02 第四轮：补齐 standard / full 档）

用户授权「完成所有测试」。三档全跑，发现并修复 N5（失效模型）。

| 档位 | 命令 | 结果 | 用时 | 实付 |
|---|---|---|---:|---:|
| minimal | `--tier minimal`（早前） | 214/214 全绿 | 104s | $0.199 |
| standard | `--tier standard --max-usd 1.0` | **214/214 全绿**（含 `wan3.0-video` 720p 主模型） | 220s | $0.544 |
| full | `--tier full --max-usd 1.5` | 首跑 212/214（2 项因 N5 失效模型失败）；修复配置后视频两段 + chat 均验证通过 | 394s | $1.044 |

> `full` 首跑的 2 个失败**不是网关 bug，是测试配置引用了已下线模型** `deepseek-v4-flash-0731`：
> `test_01` 断言该模型不在 live 清单、`test_03` chat 调用被服务端 403 `model_not_found`。
> 视频两段（wan3.0 720p ×2）本就 `SUCCEEDED`。修复后改用 `deepseek-v4-1-flash-260910`，
> 单独真机验证 chat 输出「你好」（$0.000077），全档实质绿。

**累计真机花费（本轮所有档位）≈ $1.79**（minimal $0.199 + standard $0.544 + full $1.044）。

关键结论：五种 kind 全链路 + 错误路径 + 防泄漏，在 minimal / standard / full 三档下均通过；
`wan3.0-video`（主推模型，720p）在 standard / full 档验证可正常出片。

---

## 9. 2026-11-13 第三轮：`/v1/models` 接口丰富与 F1 口径作废

**触发**：PT 上线 `GET /v1/models` 结构化字段，并就我方 6 条提问给出两批书面答复
（完整固化见 `docs/PT_项目资料总集_20261112.md` §13）。同日用真 Key 做双 Key 影子对账。

### 9.1 PT 书面答复（6 + 6 条，结论）

| # | PT 答复 | 项目结论 |
|---|---|---|
| 1 | `supported_parameters` 会加 | 上线实测 **56/77** 有，含 `reasoning_effort`(25) / `web_search_options`(6) / `reasoning`(25)；`omitempty` 需兜底 |
| 2 | `input_modalities` 对生成类是**素材输入**；判生成看 `output_modalities` + `supported_endpoint_types`，不另加字段 | 判定规则可用；实测 27 个 image 输入 = 理解类 16 + 生成类 11，**11/11 命中** |
| 3 | `pricing.web_search:"0"` 是免费；图片/视频有意省略 pricing；其余字段只是匹配 OpenRouter 模型 ID | 6 个 qwen3.x 搜索免费确认；**撤回「用 pricing 对账单价」设想** |
| 4 | 89 条样例是文档片段；真实响应按 Key / 分组过滤（限额 → 分组启用 → 双重过滤） | **F1 作废**；样例 89 ⊃ 实测 77，catalog 86 中 15 个不在本视图 |
| 5 | 只增不改、扩展字段 `omitempty`、`success`/`object:"list"` 非本次新增、无分页 | 老客户端零影响；`pt_video.list_key_models()` 只取 `data[].id`，不受影响 |
| 6 | `reasoning` 是硬编码能力声明；开关收敛到 `reasoning_effort`；`default_enabled=true` 表示不传参也开思考 | 实测 25/25 全为 `(mandatory=false, default_enabled=true)`；关闭取值待与 relay 层对齐，catalog 不写死 |

### 9.2 F1 作废的证据链（2026-11-13 真机，全程 0 花费）

| 证据 | 数据 |
|---|---|
| 双 Key 同账号/分组 | 两枚 Key 各拉一次，均 HTTP 200、**77/77 完全一致**（`only_a=0, only_b=0`） |
| 视图是子集 | 77 个全部落在 89 条文档样例内（`only_live=0`），样例另有 **12 个本视图不可见** |
| 「不在列表」≠ 下架 | catalog 86 个里 **15 个不在本视图**；其中 `kling-v2-1-master` 同日 `estimate` 仍可计价 1400 credits |
| 误踢面 | 若按旧逻辑跑 `sync()`，这 15 个（含 10 个 chat）会被 `delisted=true, enabled=false` 永久移除 |
| 新模型 | live 有 6 个 catalog 未收录：`kling-image-o1` / `kling-v2-5-turbo` / `kling-v3-omni` / `mimo-v2.6-pro-ultraspeed` / `qwen3-tts-flash` / `wan2.7-image` |

> SSOT §2.2 早在 2026-11-12 就记过「列表按分组可用 + 命中定价规则 + status=2 三重过滤」，
> 与本次 PT 答复一致 —— **F1 是 2026-10-30 的误判，本次纠正回来**。
> **不恢复已删的双层架构**：服务端返回的是单个已过滤列表，客户端不需要双层，也不恢复 fail-closed。

### 9.3 本轮代码改动

| 文件 | 改动 |
|---|---|
| `pt_gateway.py` | `ModelRegistry.sync()` 新增 `not_in_key_view` 状态与 `confirmed_delisted` 参数；不再自动 `delisted`；新增 `mark_delisted()`；`list_capabilities()` 透出新字段；全量注释订正 |
| `catalog.json` | `note` F1 口径订正；`updated` → `2026-11-13`；新增顶层 `models_api_contract`（`_source=models_api`，字段契约 + 能力判定规则 + 影子对账观测） |
| `catalog.schema.json` / `validate_catalog.py` / `tests/test_unit_catalog.py` | `_source` **8 → 9 值**（新增 `models_api`），三道守门员同步，新增契约断言 |
| `tests/test_unit_registry.py` | 旧「消失即下架」断言改为「只标 `not_in_key_view`、保持 enabled」；新增显式下架 / 恢复 / 空列表等 5 条回归 |
| `mcp.py` / `pt_video.py` / 双语 README / 测试注释 | 口径订正 |

**保持不变**：fail-open 不做客户端拦截；取消接口仍不实现；86 个模型条目的 `capabilities` **一条未改**
（影子对账只出报告，官方字段待逐条裁决后再讨论批量回填）。

### 9.4 未决 / 后置

1. `dola-seedream-5-0-pro-260628`：真实接口给 `output_modalities:["text"]` + `endpoint:["openai"]`，
   按 PT 规则会被判成「理解类」，但 catalog 是 `kind=image`（名字带 seedream 显然是图像生成）。
   **上线后数据依然如此，不是样例笔误**，需回问 PT。
2. **vision 冲突**：catalog 28 个 `vision=true` vs live 理解类 16 个，重合 14。排除「本视图不可见」的 9 个后，
   真正待裁决 5 个：`MiniMax-M2.5` / `M2.5-highspeed` / `M2.7` / `M2.7-highspeed` / `qwen3.7-max`。
3. **web_search**：官方只给 6 个 qwen3.x 有 `web_search_options`，catalog 有 26 个 `conditional`；
   不能因「官方没写」就判 false，需真机抽样。
4. `reasoning_effort` 关闭取值 PT 自己说「待与 relay 层对齐」，catalog 不写死。
5. `thinking_param` 7 种写法尚未收敛（PT 第 6 条）。
6. 视频侧 C4 / R1 异步仍缺 ≥1000 credits 额度；80 credits 不够（见 SSOT §12.6）。

### 9.5 验收

`validate_catalog.py` **137 项全 PASS**；`tests/run_tests.py` **265/265 PASS**（离线，0 花费）。
详见 `CHANGELOG.md` 0.7.3 与 SSOT §13。
