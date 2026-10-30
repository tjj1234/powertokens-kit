# PT Gateway —— 待修正 / 待讨论清单（FIXLIST）

> 本文档记录 pt-gateway 开发讨论中暴露的「说错 / 待修正 / 待架构决策」事项。
> 标记：`[已纠正]` = 本次已改代码+文档；`[待讨论]` = 等公开层爬虫写完后回来逐条定方案。
> 关联：task #29（registry 多 key 误判 + catalog 能力自动补）。

---

## 1. 已纠正的硬性错误（本次已改）[已纠正]

| # | 原错误结论 | 真相 | 改动位置 |
|---|---|---|---|
| 1 | `cancel` 占位 `{supported:false}`，文档称「PT 无取消端点」 | Seedance 真实存在 `DELETE /byteplus/api/v3/contents/generations/tasks/{id}`（queued→cancelled） | `pt_gateway.cancel()` 实现真实取消（其他 kind 诚实 unsupported）；`server.py` / `mcp.py` / `README.md` 注释同步改 |
| 2 | `catalog.json` 标 `audio.status=pending-endpoint`，`media_generate` 对 audio 返回 unsupported | 音频端点已确认：`POST /ali/api/v1/services/aigc/multimodal-generation/generation`（Ali 兼容，同步返回 url） | `catalog.json` kinds.audio 填真实端点；`pt_gateway.audio_generate()` + `media_generate` 路由；`mcp.py` / `README.md` 注释 |
| 3 | 文档称「所有视频/Seedance 模型共用同一传输层」，暗示 audio 也共用 | submit/query 才共用 `/v1/videos`；**cancel/audio 是各 vendor 特定路径** | `pt_gateway.py` 模块 docstring、`README.md` §0 前提纠偏、架构图 cancel 行 |

---

## 2. 待回来讨论 / 架构决策 [待讨论]

### 2.1 registry 单文件多 key 误判（task #29 核心 bug）
- **现状**：`ModelRegistry` 把 synced 模型写死 `~/.config/pt-gateway/registry.json` 单文件
  （`pt_gateway.py` 的 `ModelRegistry.__init__` 默认路径）。不同 key 的 `GET /v1/models`
  列表不同 → 多 key 跑 `sync` 会把「别的 key 有、本 key 无」的模型误判下架
  （`enabled=False, delisted=True`），污染全局注册表。这是比 catalog 残留更严重的真坑。
- **已定方向（用户 2026-10-29 拍板）**：
  - **公开层（无 key）** 每天 2 次爬 PT 官网，掌握「全局模型真相」（含上架/下架）；
  - **key 层** 只返回「当前 key 能调的模型」权限视图；
  - **下架判定从 key 层移到公开层**，多 key 误判根因自然消除。原「按 key 分桶」方案作废。
- **待讨论**：
  1. 公开层爬虫（见 §3）产出的 catalog 如何与 registry 合并？registry 是否还需保留
     （退化为「本 key 权限视图缓存」）？
  2. 多 key 场景 registry 路径是否按 key 哈希分文件，避免交叉污染？

### 2.2 catalog 能力自动补全（缺口一）
- **现状**：`list_capabilities` 只扫 `catalog.json` 人工档案。新上架模型不在 catalog →
  agent 按能力选会漏（违背「不用查」初衷）。
- **已定方向**：公开层爬虫每天 2 次爬 PT docs，新模型 ID 去对应文档页爬结构化能力
  （同步/异步/必要参数），自动补齐 `catalog.models`。enrich 规则 + LLM 维护口子待定
  （用户原话：「留个口子，当用户填 key 时调一个模型去维护」）。
- **待讨论**：
  1. enrich 用纯规则（OpenAPI schema 解析）还是规则 + LLM？
  2. 自动补的字段如何标注置信度（避免编造未证实参数）？

### 2.3 音频请求体 / 响应字段待实测
- 端点已确认（Ali 兼容），但**请求体确切字段**（`model + input.prompt[+voice]`？
  DashScope TTS 标准结构）与**响应 url 位置**（`output.audio.url` / `output.url` / ...）
  以 PT 文档实测为准。当前 `audio_generate()` 多位置兜底抽 url，并保留 `raw` 便于核对。
- **待讨论**：拿到一个可用 TTS 模型 ID + 实测一次，固化确切 schema。

### 2.4 其他模型取消端点 [已定]
- 仅 Seedance 经 ByteDance/BytePlus 提供真实取消端点 `DELETE /byteplus/api/v3/contents/generations/tasks/{id}`。
- **已确认（用户 2026-10-30）**：PT 无统一的异步任务取消接口，其他模型（wan/kling/vidu/hailuo 等）
  **不存在**服务端取消能力。故上轮加的 best-effort 推断已回退——`cancel()` 对非 seedance 一律
  诚实返回 `{supported:false, reason:...}`（不假装成功、不误导调用方发无效 DELETE）。
- 结论：进行中的非 Seedance 异步任务无法取消，只能等其自行结束。

### 2.5 轮询健壮性（引擎老问题，非阻塞）
- `poll_task` 无 429 退避 / 5xx 熔断 / 未识别状态熔断（`pt_video.py` 注释 A.02/A.03）。
  网关沿用该实现。
- **待讨论**：是否在网关层加三重熔断（与爬虫无关，可单独排期，不阻塞本次）。

---

## 3. 公开层爬虫（本次新增 `catalog_crawler.py`）

无 key 爬 `docs.powertokens.ai/sitemap.xml`
→ 过滤 `zmodelVideo` / `zmodelImage` / `zmodelAudio` / `zmodelChat` 模型页
→ 抓各页配套的 OpenAPI 3.0.1 JSON 片段（无 key 可取）
→ 解析 model enum / 端点 path / 请求参数 schema / 响应 status enum（判同步/异步）
→ 合并结构化数据进 `catalog.json`（**公开层全局真相**）。

这是 §2.1 / §2.2 的根基：公开层提供全局模型真相，消除多 key 误判，并自动补能力。


**已交付（2026-10-30）**：`catalog_crawler.py` 已写好并全量跑通 —— 无 key 爬 sitemap(396 loc / 350 模型页)
→ 去重 181 个唯一模型页 → 抓各页 OpenAPI 3.0.1 片段 → 解析出 **82 个模型**
（Video 20 / Seedance 7 / Image 9 / Audio 8 / Chat 38；同步 54 / 异步 28），写入 `catalog.public.json`。
跳过的是任务查询/取消/下载等「帮助页」（无 model enum），404 的是 zh-Hans minimax 镜像页（en 版已抓取，模型不丢）。
爬虫默认只写公开层真相，绝不碰 registry.json；`--merge` 可把可证字段并入 catalog.json（默认关闭，待 §2.2 讨论）。

---

## 4. 状态汇总

- [已纠正] §1 三处硬性错误（cancel 端点 / audio 端点 / 统一端点措辞）—— 代码 + catalog + server/mcp + README 已全部对齐。
- [待讨论] §2.1–§2.5 —— 等 `catalog_crawler.py` 写完后，回来逐条讨论定方案。
- [已交付] §3 公开层爬虫 —— `catalog_crawler.py` 已写好并全量跑通，产出 `catalog.public.json`（82 模型，含 kind/execution/vendor_endpoint/params；下架判定从 key 层移到公开层）。默认不合并 catalog.json（待 §2.2）。

---

## 5. 实施记录（用户逐条拍板后已落地，2026-10-30）

- [已实施] **§2.1 registry 双层**：公开层(catalog.public.json)为全局真相 + key 层权限视图。
  `ModelRegistry.sync()` 不再因「本 key 列表无某模型」误判下架，改标 `key_permitted=False`
  （仅当模型同时不在公开层才 `delisted=True`）；`auto_select()` 无 key 候选时回退公开层；
  显式模型输入 **fail-open** 放行到 PT 服务端（用户拍板：允许提交、让服务端报错）。
- [已实施] **§2.2 catalog 能力自动补**：新增 `enrich_catalog()`（merge 策略——只补缺失字段、
  绝不覆盖手写字）+ CLI `enrich` / `public`；预留 `enrich_with_llm()` 钩子（默认空实现，
  不接 LLM，以后可接）；公开层 enrich 频率由外部调度（建议一天两次，无需 key）。
  已实跑一次：merge 进 82 个模型的 vendor_endpoint / execution / params 等字段。
- [部分] **§2.3 音频实测**：当前环境无 API Key（~/.config/powertokens-video/config.json
  仅含 seedance_agreement_signed），真实 TTS 调用待用户设置 `POWERTOKENS_API_KEY` 后跑一次，
  校正 `audio_generate()` 的 input 字段（prompt vs text）与响应 url 抽取位置。
- [已修正] **§2.4 其他模型取消端点**：用户确认 PT 无异步任务取消接口，故回退上轮的 best-effort 推断——
  `cancel()` 对非 seedance 一律诚实 `{supported:false}`（理由说明仅 Seedance 有 vendor 特定取消端点）；
  仅 seedance 保留真实 `DELETE /byteplus/api/v3/contents/generations/tasks/{id}`（需 key）。
- [已实施] **§2.5 轮询健壮性**：`poll_task` 覆盖为带 429 退避（指数退避 ≤60s，尊重 Retry-After）
  + 5xx 连续 5 次熔断抛出 + 其他 4xx 直接透传 + 超时明确报错。纯逻辑、已 selfcheck 通过，
  真实任务链路需 key 验证。
