# PT Gateway —— 待修正 / 待决策清单（FIXLIST）

> 记录 pt-gateway 开发讨论中暴露的「说错 / 待修正 / 架构决策」事项。
> 最近一轮大改：**2026-10-30（第二轮）** —— `/v1/models` 语义澄清、取消接口删除、爬虫删除、chat 打通。
> 标记：`[已定]` = 已拍板并实施；`[待办]` = 还需动作；`[已废弃]` = 方案作废（保留以溯源）。

---

## 1. 平台事实（2026-10-30 与用户 / 官方确认，是其余决策的前提）

| # | 事实 | 影响 |
|---|---|---|
| F1 | `GET /v1/models` 返回 PT **全量**模型清单，**所有 key 相同** | 它不是「本 key 权限视图」→ 不能用于权限拦截；下架判定可直接用它（不再出现即下架） |
| F2 | PT **不提供**异步任务取消接口 | 网关不实现 cancel；HTTP 无 `/cancel`，MCP 不暴露取消工具 |
| F3 | 提交 / 查询统一：`POST /v1/videos` + `GET /v1/videos/{id}` | 只需归一化请求体构造与结果字段位置，即可跨模型 |
| F4 | audio 走 `POST /v1/audio/speech`（同步，OpenAI 兼容）；chat 走 `POST /v1/chat/completions`（同步） | 这两类不进 `/v1/videos` |
| F5 | TTS 的 `voice` **必填**（缺则报 `voice is required`）；OpenAI 标准音色 `alloy/echo/fable/onyx/nova/shimmer` 实测全部可用 | 音色名用 OpenAI 的，不是 MiniMax 原生 voice_id |

> F1 修正了更早的误判：曾以为「不同 key 的 `/v1/models` 列表不同」——
> 由此设计的双层架构、公开层爬虫、fail-closed 门禁全部作废（见 §2.1 / §2.2 / §3）。

---

## 2. 决策项

### 2.1 模型清单与权限判定 [已定]

- **真相源**：`GET /v1/models`（全量，所有 key 相同）。`ModelRegistry.sync()` 据此 diff：
  出现 → `in_models=true`、`enabled=true`、新出现打 `is_new`；
  不再出现 → `delisted=true`、`enabled=false`（即视为下架）。
- **权限**：**fail-open，不做客户端拦截**（用户拍板）。本 key 对某模型无权限时，
  **由 PT 服务端报错**，网关原样透传。
- **已废弃并删除**：双层（公开层 + key 层）架构、`key_permitted` 字段、
  `_key_permitted_models()` / `_enforce_key_permission()` 门禁、`auto_select(permitted=...)` 权限过滤。

### 2.2 catalog 能力自动补（enrich）[已废弃]

- 原方案依赖公开层爬虫产出的 `catalog.public.json`。爬虫已按用户决定**彻底删除**，enrich 失去数据源。
- `enrich_catalog()` / `enrich_with_llm()` / CLI `enrich` / `public` 已一并删除。
- 能力档案仍由人工维护的 `catalog.json` 提供（`/v1/models` 只给模型 ID，不给分辨率 / 时长 / 模式 / 单价）。

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
| `catalog_crawler.py` | 已删除（F1 后无需再爬官网取全量清单） |
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
