# Changelog

本文件记录 `powertokens-kit` 可交付版本的变更。catalog 的字段级决策依据见
`docs/catalog_v2_决策锁定_20261112.md`；与 PT 官方 2.0.0 文档的核销见
`docs/PT_API_2.0.0_官方答复核销_20261112.md`。

---

## 0.9.0 — 2026-11-23 · C01 按需求推荐（新增可选）+ P3 price_rows 小写（顺序 6）

**来源**：统一排期计划顺序 6（C01），用户 11-23 批准开工并明确"改选模逻辑须做成新增可选、旧调用输出不变"。**本版动 node core（新增函数）+ catalog（P3 大小写）→ 发新 core tgz，北极星换一次锁。**

### 交付四项（供北极星审核换锁）

1. **新旧差分 OLD==NEW**：
   - A) 代码差分：`OLD index.js`(0.8.12) vs `NEW index.js`(0.9.0)，同一 catalog，旧签名 `selectCandidates(purpose, keyModels)` 36 用例（9 用途 × 无参/detail/keyModel 过滤 + pinned + requirements + hasImage/purposeKind）输出逐字节一致，mismatches=0。
   - B) 数据差分：同一 NEW index.js，OLD catalog(0.8.12) vs NEW catalog(0.9.0，P3 小写) 22 用例输出逐字节一致，mismatches=0。
   - **R1/R3 红线**：`selectCandidates` 函数体、签名、默认路径一个字节未改；`recommendModels` 是独立新函数，不传不生效。
2. **SHA-256**：tgz `0532cbdb7b95aab91ad51708898690c18d830edef8d7419f58c77891a5baba82`（64745B）；包内 catalog `fcfce6f0f5ac31a188fa12239ef47ecbb46e233618ff2d33d4f3baa9cc7f2687`（227707B、LF、0 CRLF）。6 文件自包含，干净安装实测通过。
3. **改动清单**：
   - **新增** `recommendModels()` / `parseRecommendIntent()`（node/index.js 导出，函数体新增于 selectCandidates 之后）；新增 `node/test/recommend.test.js` 13 例。
   - **catalog（P3，搭车）**：25 个模型共 137 处 `price_rows.value[].resolution` 由大写改小写（`1080P×43/720P×38/540P×17/480P×9/2K×8/4K×8/768P×7/1K×3/1.5K×2/512P×2`，按 OLD 统计）。**只改大小写、不改数值、不改字段语义**；严格验证 OLD 仅转小写后与 NEW 逐字节一致，大写归零。
   - 无来源字段一律不打分/不猜（R4）：catalog 无任何 documented/verified 的质量分字段，效果档用可审计的能力覆盖度代理，不编造质量分。
4. **C01 三档语义**（新旧对照）：
   - `recommendModels(intent, keyModels, opts)` → `{intent, purpose, strategy, tiers:{quality,balanced,cost}, recommended}`。
   - **成本档**：按 `price.usd_5s_720p` 的 documented/verified 价升序；无合规价格排末尾、不参与打分。
   - **效果档**：按有官方来源的能力维度数量（durations/resolutions/aspect_ratios/frame_control/reference_image 等）降序；同分保持 catalog 顺序；`signals` 随附依据，**不编造质量分**（catalog 无 documented/verified 的 score/rank）。
   - **均衡档**：保持 catalog 候选顺序，不主观重排。
   - 自然语言纯规则解析；混合信号（如「真实好看又便宜」命中 quality+cost）判 balanced，三档都返回交调用方对比。内部复用 `selectCandidates(detail:true)`，继承跳过 / A4 / requirements / keyModels 过滤。

### 守门与验收

- **R1 差分 OLD==NEW**（代码 36 + 数据 22 用例）、**R2** 5 个 chat 用途候选顺序默认不动、**R3** 新增可选不传不生效、**R4** 无来源不打分不猜——四条全守。
- 测试：Node **96/96**（83+13）、Python **347/347**、validate_catalog 全过、干净安装自包含 PASS。未调用 PT、0 花费。
- **P3 说明**：这是你 0.8.12 审核提的 price_rows 大小写遗留，本次因顺序 6 动 catalog 顺手搭车完成，不额外换锁。

---

## 0.8.13 — 2026-11-23 · T04 账户凭证 + T03 实时报价 + T07 产物后缀（顺序 5，说明性发版无新 core 包）

**来源**：统一排期计划顺序 5（T04 + T03 + T07），用户 11-23 批准开工。**本版只改 `pt_video.py` + 两版 README + 新测试，不动 catalog / node core → 不发新 tgz，北极星无需换锁；core tgz 与 catalog SHA 沿用 0.8.12。**

### 改动

1. **T04 账户域凭证**（修"账户命令仍用模型 Key"）：
   - 新增 `resolve_account_credentials()`，优先级：`-k`（`pt_live_` 开头当令牌）→ `PT_ACCESS_TOKEN`/`PT_KEY_ID` → config.json 的 `access_token`/`key_id` → 旧 `POWERTOKENS_API_KEY`（裸 key 兜底，不猜 key_id）。
   - `_account_get()` 保持旧签名；内部把数字 `key_id` 合并进 `balance`/`usage` 的 query（非数字脱敏串不传，R4）。令牌走裸 `Authorization`（SSOT C1 裁决，不带 Bearer）。缺凭证时打印首次配置引导并以 401 退出。
   - `config` 新增 `--set-access-token` / `--set-key-id`（`--show` 令牌只显示尾4位）；key_id 非纯数字直接拒绝。
2. **T03 实时报价**（修"写死旧价 0.20"）：
   - `PRICE_VIDEO_5S_720P["wan3.0-video"]` 由 `0.20` 更正为 `0.5`（catalog verified 实扣，U10 裁决）。
   - 新增 `catalog_price_usd()`（只读、带 `_source`）；`estimate_cost()` 末尾新增可选参数 `catalog_models` / `prefer_catalog`（默认优先 catalog，旧调用可 `prefer_catalog=False` 回落写死表）。
   - `estimate` 子命令输出 `price_source` + "以实扣为准"提示；新增 `--live`（有 `pt_live_` 凭证+数字 key_id 时走账户域 `POST /v1/billing/estimate`，不扣费，失败静默回落 catalog）。
3. **T07 产物后缀**（修"JPEG 被存成 .png"）：
   - 新增纯函数 `detect_file_extension()`（魔数优先 → Content-Type 兜底 → default；识别 png/jpg/webp/gif/bmp/mp4）。
   - `generate_image` 默认命名下载后按魔数校正后缀；**显式 `-o` 仍尊重用户后缀**（不改旧行为）。
4. **文档**：两版 README 的 §9 命令表、§16 账户域鉴权、FAQ 同步 T04/T03 口径。

### 守门与验收

- **旧行为兼容**：T04 旧 `-k`/`POWERTOKENS_API_KEY` 裸 key 兜底；T03 `prefer_catalog=False` 走写死表、函数签名向后兼容；T07 显式 `-o` 不改后缀。北极星不用 `pt_video.py`，零影响。
- **测试**：新增 `test_unit_seq5.py` 20 例（T04 7 + T03 6 + T07 7）；Python 全量 **347/347**（327→347）、0 花费、未调用 PT。
- **SHA**：core tgz `30a6f3e963900aa94098359a8647df22df4c8fb333009382af8ee8d557667f1f`、包内 catalog `030301a2aa19c8de63f5daa1c78fe0915fb892e9d703af9fdaef533a5cef6924`，与 0.8.12 完全相同（无新资产）。

---

## 0.8.12 — 2026-11-20 · T09 尺寸传参 + P2-7 sizes 语义/计价结构化 + 10 default 小写（顺序 4）

**来源**：统一排期计划顺序 4（T09 + P2-7），用户 11-20 批准开工；动 catalog 字段语义，按 R2 单独发版 + 发版说明（照 0.8.9 格式）。**本版动 catalog → 发新 core tgz，北极星按新字段读取规则平滑跟进、换一次锁。**

### 交付四项（供北极星审核换锁）

1. **新旧差分**：OLD 7994B == NEW 7994B 逐字节一致。
   - A) 代码差分：`OLD index.js` vs `NEW index.js`，同一 NEW catalog，旧签名 `selectCandidates(purpose, keyModels)`（5 chat 用途 + 4 媒体用途 × 无参/detail + 指定模型 + keyModel 过滤）输出逐字节一致。
   - B) 数据差分：同一 NEW index.js，OLD catalog(0.8.9) vs NEW catalog(0.8.12)，旧签名输出逐字节一致——证明 sizes/price_rows/default 数据改动不改变旧签名行为。
   - **P2-7 sizes 专项**：`mediaRequestParams('viduq2', {size})` 在 `sizes.value=null` 时回落 `size_tiers.value`，`1080p`/`2K`/`4k` 三档均正确映射为 `resolution` 且不判 unsupported（大小写不敏感）。
2. **SHA-256**：tgz `30a6f3e963900aa94098359a8647df22df4c8fb333009382af8ee8d557667f1f`（60494B）；包内 catalog `030301a2aa19c8de63f5daa1c78fe0915fb892e9d703af9fdaef533a5cef6924`（227707B、LF、0 CRLF）。
3. **改动清单**：见下方表格；无权威来源的值一律 `null` + `_source`（R4），不猜。
4. **sizes 新旧语义**：见下方专节；`node/README.md` 与 catalog 顶层 `note` 已写读取规则。

### 数据改动（两份 catalog 同步：pt-gateway + node 快照）

| 批次 | 内容 | 数量 |
|---|---|---|
| P2-7 sizes 语义 | `viduq2` 原 `sizes.value=['1080p','2K','4K']`（档位）迁出：`sizes.value=null`（无官方精确像素），档位小写落 `size_tiers.value=['1080p','2k','4k']` | 1 模型 |
| P2-7 计价结构化 | 35 个媒体价格模型新增 `price.price_rows`（playground 价档结构化数组，共 150 行；每行含 resolution/mode/ability/billing_unit/sale_input_price/sale_input_credit 等有值字段，`_source=documented`）；`price_rows_count` 保留兼容 | 35 模型 / 150 行 |
| 10 default 小写 | `resolutions.default` 大写档位统一小写：`1080P→1080p`(7)、`720P→720p`(2)、`768P→768p`(1) | 10 模型 |

- **保留 null + _source 的项（R4 不猜）**：`viduq2.sizes` 的 `value/default/_source` 全为 null（原厂仅给档位名+比例、无精确像素枚举）；`price_rows` 每行只保留有值字段，全 0/空维度（has_audio/is_peak_shifting_mode 等无值）不落键，表示「该档未声明此维度」不作能力承诺；其余模型既有 `reference_image.*`/`pixel_map.basis`/`usd_5s_720p` 等 null 项保持不变（本版不填、不猜）。
- **计价口径**：`price_rows` 为 documented 基准价，预估仅参考、以实扣为准（U10）；未做付费实测，不新增 verified 价格。

### sizes 新旧语义（读旧档位请改用 size_tiers）

- **旧语义（≤0.8.11）**：`capabilities.media.sizes.value` 放分辨率档位名（如 `['1080p','2K','4K']`），与实际「精确像素」字段语义混淆。
- **新语义（0.8.12）**：`sizes` 只放模型保证的**精确像素**（如 `2560x1440`）；无官方精确像素枚举则 `value=null` + `_source`。分辨率**档位**移到新字段 `size_tiers.value`（如 `1080p/2k/4k`，统一小写）。像素换算见 `pixel_map`（kit_derived 待实测，不当作支持）。
- **平滑过渡**：node 侧 `REQ_FIELD_MAP.size.get` 在 `sizes.value` 非数组时回落 `size_tiers.value`，传 `size` 需求的旧调用不失效；`mediaRequestParams` 同口径。`selectCandidates` 不传 `requirements` 时完全不触发，默认行为逐字节不变。

### 代码与验收

- **T09 尺寸传参**：`pt_video.py` 新增 `resolve_media_fields(model, friendly, path)`，按 catalog `capabilities.media.param_names` 把友好字段名映射为 PT 真实字段名（`duration_s→seconds`、`resolution→size`、`aspect_ratio→ratio` 等）。默认模型（wan3.0-video，映射恒等）提交 body 逐字节不变；kling 等映射不同的模型字段被正确重命名，避免参数被服务端忽略；无 param_names 模型沿用历史字段名、不猜不丢（R4）。视频/图片提交链路接入，参考图仍由 `build_media` 传出（M3 已覆盖）。
- **测试**：新增 `test_unit_media_params.py`（8 例）；Python 全套 **327/327**、Node **83/83**、validate_catalog 全过（含两 catalog 一致）、差分 OLD==NEW + P2-7 sizes 专项 PASS。未调用 PT、0 花费。

---

## 0.8.11 — 2026-11-23 · 下载可靠性 + 错误可见 + 请求追踪（顺序 3，不发新 core 包）

**只改 Python 引擎 + 测试 + .gitattributes，不动 catalog 数据与 node core。** core tgz SHA `89966e85…` / catalog SHA `e0397450…` 与 0.8.9/0.8.10 完全一致；北极星无需换锁。

- **T02 下载残缺文件**：`download_binary()` 改为写 `<path>.part` 临时文件 → 校验完整（非空 + Content-Length 一致 + 非 HTML 错误页）→ `os.replace` 原子落盘；任何失败删除临时文件、绝不留下残缺目标文件、不返回成功。T06 安全逻辑（第三方地址永不接触 API Key）完整保留。
- **T05 查询失败标「未知」**：`list_key_models()` 新增 `strict` 参数（默认 False 保持 fail-open 兼容）；`models` 命令用 strict 模式，查询失败抛 `ModelListUnavailable`、`availability=unknown`、退出码 4（非 0），明确区分「查询失败（未知）」与「确实不可用」。
- **T08 请求编号追踪**：`_api_call()` 从响应头 `X-Oneapi-Request-Id` 提取 request id（大小写不敏感）→ 附加 `_request_id` 到返回对象 + 累积到模块级 `_REQUEST_LOG`（`get_request_log()` 可取副本）；提交号写入 `LAST_SUBMIT_REQUEST_ID`，结果 JSON 增 `request_id` 字段。网关复用同一 `_api_call`，自动覆盖。仅旁路记录，不改变正常返回语义。
- **`.gitattributes`**：新增 `*.py text eol=lf`，消除 Windows `core.autocrlf` 导致的「同一文件两个 SHA」（本地 CRLF vs 线上 LF）。工作区 `pt_video.py` 已归一化为 LF，从此工作区/线上/git blob 三者统一。
- **测试**：新增 15 例（T02 5 + T05 4 + T08 6）；Python 全套 **319/319**、Node **83/83**、validate_catalog 全过；T06 下载安全 5/5 回归通过。0 花费、未调用 PT。
- **pt_video.py 新 SHA256（LF 版，kit 侧记录）**：`1b0ceebb899414b1b07b7fc6d5053fa5d92d5ad33598cdbc96c3aa0c1f7c98e5`。按用户确认，北极星使用其自有仓库的改造版、不替换 kit 文件，此 SHA 仅 kit 侧留档，北极星无需据此同步。

---

## 0.8.10 — 2026-11-23 · T01 提交入口按 catalog 选择（不发新 core 包）

**只改 Python 引擎 + 文档，不动 catalog 数据与 node core。** core tgz / catalog 两个 SHA 与 0.8.9 完全一致。

- **T01（同事实测）**：`pt_video.py` 提交视频 / 图片前按 `model` 从 catalog 读 `submit` 选择入口（新增 `resolve_submit_endpoint()` / `load_catalog_models()`），不再一律写死。读不到 catalog / 模型未登记 / 入口跨类型 → 回落 `VIDEO_SUBMIT` / `IMAGE_ENDPOINT`（不抛错）。`submit_task()` 新增可选 `endpoint` 参数，不传则旧签名行为不变。
- **为什么这样改**：catalog 早已有 `submit`（86）+ `vendor_endpoint`（86）对照，同事「没有模型↔接口对照」的前提不成立。真问题是引擎没去读字段；kling 等模型真实可用入口需真机确认，按 R4 不猜、不动 catalog 数据。
- **北极星零影响**：北极星副本可不含 `catalog.json`，此时行为与旧版逐字节一致；A5 按本版 `pt_video.py` 新 SHA256 同步副本。
- **不发新 core 包**：本版不改 `node/` 任何入包文件、不改 `catalog.json`。core tgz SHA256 仍为 `89966e857fd85a5e915ec70ae55b6e88976fadbdf3014b93734f9d3adeb022d0`，包内 catalog SHA256 仍为 `e039745078115456d82624f812ac81e90b998dab0e1c39e314c406a2e9952ca8`（均与 0.8.9 相同）。`node/README.md` 若有改动先提交，打包并入顺序 4 那次 catalog 变更。
- **文档**：`submit` / `vendor_endpoint` 语义写入根目录 `README.md` §16.1 与 `README.zh-CN.md` §16.1（均不在 core tgz 内）。
- **测试**：新增 `test_unit_submit_endpoint.py`（12 例：正常读取 / 缺 catalog 兜底 / 坏 JSON / 未登记模型 / 跨类型脏数据 / 旧签名兼容 / 真实 catalog）。Python 全量与 T06 下载安全回归全过。
- 未调用 PT、0 花费；生成链路其它部分、账户客户端、node core、catalog 数据 0 改动。

## 0.8.9 — 2026-11-23 · 媒体数据补齐（北极星审核 P1 + P2-4/5/6，读取规则文档化）

**来源**：北极星 11-23 对 0.8.8 的审核反馈 + 第三节答复（全部采纳），按第五节顺序 1 实施。
**性质**：纯增量、对 0.8.8 零破坏——旧签名 `selectCandidates(purpose, keyModels)` 输出逐字节一致。

### 交付四项（供北极星审核换锁）

1. **新旧差分**：OLD 6804B == NEW 6804B 逐字节一致（5 chat 用途 × 无参/detail + 指定模型 + keyModel 过滤 + 4 媒体用途无参/detail）。**P2-5 专项**：adaptive 语义在 0.8.8 catalog 与 0.8.9 catalog 上结果完全相同（selectCandidates 命中 + mediaRequestParams 接受均一致）。
2. **SHA-256**：tgz `89966e857fd85a5e915ec70ae55b6e88976fadbdf3014b93734f9d3adeb022d0`（57009B）；包内 catalog `e039745078115456d82624f812ac81e90b998dab0e1c39e314c406a2e9952ca8`（170402B、LF、0 CRLF）。
3. **改动清单**：见下方表格与 GitHub Release v0.8.9 正文（逐模型逐字段）；86 处无来源值留 `null` + `_source`。
4. **读取规则**：「新字段优先；缺失且未被 `supersedes_top_level` 替代则沿用顶层旧字段」已写入 `node/README.md` 与 catalog 顶层 `note`。

### 数据改动（两份 catalog 同步：pt-gateway + node 快照）

| 批次 | 内容 | 数量 |
|---|---|---|
| P1 seedream 比例 | `seedream-5-0` 补顶层 8 比例（documented）；其余 3 个 seedream 顶层/en.zip 无枚举→null | 4 模型 |
| P1 pixel_map | `image-01` 官方精确像素（documented）；`wan2.7-image-pro` kit 自定档位换算（kit_derived、待校准）；其余 7 图片模型 null | 9 模型 |
| P1 reference_image | 补全 formats/input/max_reference_images/max_file_size_mb；`wan2.7-image-pro` 有 20MB/≤9 张（documented），其余 null | 19 模型 |
| P2-4 分辨率小写 | `resolutions.value` 统一小写（16 个含大写 P/K 的模型）；`_normMediaVal` 大小写不敏感，行为不变 | 14 模型 |
| P2-5 adaptive 单列 | `adaptive` 从 `aspect_ratios.value` 移出→`adaptive_supported:true`；node 取值逻辑还原成可匹配值，语义不变 | 8 模型 |
| P2-6 duration_step_s | 补字段；官网无步长依据→null | 8 模型 |
| 删 confirmed_card | `wan3.0-video` 价格确认卡移除（北极星不读） | 1 |

### 验收

Node **83/83**（新增 10 例 0.8.9 测试）、Python **292/292**、validate_catalog 全过（含两 catalog 一致）、差分 OLD==NEW、P2-5 专项 PASS。未调用 PT、0 费用、生成链路与账户客户端 0 改动。

---

## 0.8.8 — 2026-11-20 · 媒体能力补全（4 用途 + capabilities.media，纯增量零破坏）

**来源**：《kit 媒体能力需求 M1–M6》+ Codex/T01–T09 报告 + 北极星复核六条（已逐条核实接受）。
从 PT 官方 en.zip OpenAPI + playground 全量抽取媒体参数，404 下架模型从媒体候选剔除。
版本增量兼容，**对 0.8.7 零破坏，可无感升级**。

- **纯新增，不改调用路径**：`auto_policy.purposes` 加 4 个媒体用途
  `text2image`/`image2image`/`text2video`/`image2video`（各 3 候选，image/video/seedance 模型）；
  条目加权威字段 `capabilities.media`（`durations/resolutions/aspect_ratios/sizes/duration_rule/
  reference_image/pixel_map/price/param_names/family`）+ M6 别名；顶层旧字段保留兼容，
  差异由 `supersedes_top_level` 标注。新增可选导出 `mediaRequestParams(modelId, friendlyParams,
  {catalog})` 按 `param_names` 映射用户友好参数，不支持项进 `unsupported` 不静默丢。
- **`selectCandidates` 可选 `requirements`**：传时长 / 分辨率 / 比例时，不满足的候选进
  `excluded`（`reason` + `available_values`），能力未知标 `capability_unknown`。**不传
  `requirements` 时输出与 0.8.7 逐字节一致**——差分测试 OLD 4410B == NEW 4410B，北极星锁定的
  旧签名调用不受影响；`detail` 仅在传 requirements 时才多返回键。
- **数据口径**：预估按高档、实扣为准（价格带 `_source`，仅 `wan3.0-video` 5s/720p 有
  `verified` 实扣 0.5 USD）；chat 无预估接口。像素映射由 kit 按档位统一定义并标
  「待实测校准」，精确像素以官网原厂为准。404 下架 17 个模型只排除媒体候选、不删主条目；
  seedance 不改 `kind`、只标 `family:'video'`。
- **北极星复核六条逐条落实**：① 字段名 / 完整枚举（含 -1/4/6 秒）② `capabilities.media`
  权威 + 新旧差异标注 ③ 价格 `_source`（`verified` 附日期）④ 像素映射 kit 自定 +
  待实测 ⑤ 参考图要求 ⑥ seedance `family` 标注。
- **零影响**：生成链路 `pt_video.py`、账户客户端、现有测试与现有调用路径 0 改动。T06 下载
  安全补丁已作为 `v0.8.7-security.1` 单独先行发布，与本版解耦。
- **验收**：Node `--test` **73/73**（新增 11 例媒体测试）、Python **292/292**、
  `validate_catalog.py` 全通过；`npm pack` 自包含 6 文件、`expectedSchema:2` 校验通过。
- 产物 `dist/powertokens-kit-core-0.8.8.tgz`，tgz SHA-256
  `121cc4e46b49ebd48ceed84da4b71d5b0fa7a87f0533d9382c7688b2dd7c218b`；包内 catalog SHA-256
  `698ffc4980d06728a20bcd5c1820d59fa27c0db80547d3415ad901262c0e9b82`（LF、0 CRLF）。
  附 GitHub Release v0.8.8。

## 0.8.7-security.1 — 2026-11-20 · 下载链路 API Key 泄露修复（T06，安全补丁）

**优先级**：安全补丁，独立于 0.8.8 能力补全，先行发布。只改 `pt_video.py`，不碰 catalog / node / npm 包，故无关的在用方无需换锁。

- **问题**：`download_binary()` 下载生成产物时，先用 `Authorization: Bearer <API Key>` 请求产物 URL，失败后才裸请求。产物地址多为 PT 自有 R2 公共桶（`pub-*.r2.dev`）或上游签名链接（OSS/S3），这些第三方地址**不需要鉴权**，旧顺序等于每次下载都先把 API Key 发给第三方。
- **修复**：改为**默认裸请求**（只带 User-Agent）；仅当目标 host 是 PT 官方域名（`powertokens.ai` 及子域，精确后缀匹配防仿冒）且裸请求明确返回 401/403 时，才带 Key 重试一次。第三方地址（含 `evil-powertokens.ai` 这类仿冒域名）**永不接触** API Key。
- **兼容性**：PT 自有地址 `/v1/videos/{task_id}/content`（catalog `kinds.seedance.result_persistence` 记载需 token 鉴权）在 401 时仍会自动带 Key 重试，功能不回退；公开产物桶走裸请求，行为不变。
- **验证**：新增 `pt-gateway/tests/test_unit_download_security.py` 5 个离线用例（第三方不带 key / 第三方 401 不带 key 重试 / PT 域名 401 带 key 重试 / 仿冒域名不带 key / host 白名单判定）；全套 Python 292/292 通过。
- **对账**：本补丁的载体是根目录 `pt_video.py`（不进 npm tgz）。其 SHA-256 与版本号随 Release `v0.8.7-security.1` 给出，供使用 Python 适配器的调用方同步文件副本。

## 0.8.7 — 2026-11-18 · 请求 ID 官方确认，三项闭环（纯文档）

**来源**：用户转发 PT 官方答复。闭合「接口确认」三项的最后一项（③ 请求 ID）。

- PT 确认：`X-Oneapi-Request-Id` 与消费记录 `request_id` **代码中同值**，可作为
  `logs/query?request_id=` 查询参数；响应头**一定返回**（中间件在路由前执行，
  与是否流式 / 错误响应无关）。
- 证据边界：从「仅流式 chat + 本次模型实测」升为「官方实现层保证、全场景适用」，
  **不写成逐模型实测**。与 §15.3 / §15.x 实测互补。
- **代码 / catalog 模型数据 / `pt_video.py` 0 改动**：`getUsageRecord` 本就透传
  `requestId` → `params.request_id`，kit 不读响应头；catalog `request_id_header_note`
  「不是受理 / 重试信号」核心更成立，仅作说明补强、非冲突。
- 验收：Node 62/62、Python 287/287、校验器全过（与 0.8.6 一致）。
- 产物 `dist/powertokens-kit-core-0.8.7.tgz`，Release v0.8.7：
  tgz SHA-256 `69097f28a045e587ccf94d318f897d6fcd2e6ec687f90a0e5f45dc097a67e82f`；包内 catalog SHA-256 `ada6498964adb639048496046129c1ac878dc2783981105e9d98bb531adc2892`（LF，与 0.8.6 相同）。

## 0.8.6 — 2026-11-18 · 消费记录查询「未查到」二级判定

**来源**：用户转发 PT 答复 + 两组账户凭证真机实测（只读 0 花费）。

- **① Key 信息接口**：PT 确认 `POST /v1/key/info` + body 整数 `key_id`，与 kit 现状一致，代码不改。
- **② 空消费记录**：PT 已优化为「内部 newAPI 判存在，false→code=500 / true→200」。
  2026-11-18 真机实测确认新形态：HTTP 200 + `{code:500, data:null, msg:"record not found"}`
  （两组凭证、consume_id/request_id 三条查询逐字段一致；令牌脱敏、用完即删、不落盘）。
- **`getUsageRecord` 二级判定**（仅此接口，`normalizeResult` 不变）：输出结构化 `found` /
  `error.category` —— 有效记录 `found:true`；明确未找到 `found:false` +
  `record_not_found`；200 空壳 `found:false` + `empty_record`（与前者不同分类）；
  其他错误 `found:null` 保持原分类，绝不归为「未查到」。两种「没查到」形态并存、
  互不替代（旧 200+空壳仍在 SSOT §12.5 N1 / §15.3）。
- **契约边界**：对账只读结构化字段、不解析 PT 的 msg；`found:false` 仅表示本次未查到，
  不代表未扣费 / 最终无记录，更不可据此重发生成请求；金额零但标识状态完整的记录是
  有效记录（`found:true`）。
- **③ 请求 ID**：PT 暂未回复，保留既有证据边界，另行处理。
- **不改**：生成链路 `pt_video.py`、catalog 模型数据、路由行为、单价。

验收：Node `--test` **62/62**（新增 7 例：明确未找到/无效 token/参数错/文案不匹配/
200 空壳/金额零有效记录/其他接口不带 found）；Python 287/287；validate_catalog 全过。
产物 `dist/powertokens-kit-core-0.8.6.tgz`，tgz SHA-256 `7d617f0f308db97aa718f2cf2cc09e9c23b6c4f1dc6b5bdfca64bd7336baa25b`；包内 catalog.json
SHA-256 `ada6498964adb639048496046129c1ac878dc2783981105e9d98bb531adc2892`（LF、0 CRLF，与主 catalog 逐字节一致）。附 GitHub Release v0.8.6。

## 0.8.5 — 2026-11-17 · /v1/models 上线上下文/输出上限 → limits 实测补值

**来源**：PT 2026-11-17 发版，`GET https://api.powertokens.ai/v1/models` 在每个模型的
`top_provider` 下新增 `context_length` / `max_completion_tokens` 两个整数 token 字段。
0 花费只读实测（`实测 > 书面材料`），生成链路 `pt_video.py` 0 行改动。

- **实测**：77 模型（本 Key 视图）中 31 个携带 `top_provider`；顶层 `context_length`
  与 `top_provider.context_length` 逐模型一致（31/31）。用户贴出的样例
  `{1048576, 393216}` 经实测确认为 `deepseek-flash`。
- **补值**：按 `top_provider.context_length → limits.max_context_tokens`、
  `top_provider.max_completion_tokens → limits.max_output_tokens` 映射，给 catalog 中
  命中的 **30 个模型**补正整数上限（7 主力替换原 null + 23 个新增 limits），
  `_source='models_api'`、`_checked_at='2026-11-17'`。31 个 carrier 中唯一未命中的
  `mimo-v2.6-pro-ultraspeed` 不新增模型；其余 46 个模型无该字段，保持无 limits。
- **`_source` 裁决**：用 schema 九值枚举中的 `models_api`（定义即"来自 GET /v1/models
  官方结构化字段"），比笼统的 verified/official 更精确、可被 recheck 自动对账；
  §14.5 B2 当时的"升为 official/documented/verified"为该场景出现前泛称，本轮以
  `models_api` 为准，冲突登记于 SSOT §19.2。
- **代码同步**：`validate_catalog.py` 删除"值必须 null"断言、改校验 30 个正整数；
  `recheck.py --live-view` 从"不能补值"改为 `top_provider` 逐字段对账（一致→confirmed /
  catalog null→suggestion / 不一致→conflict，不写回正文）；`catalog.schema.json`
  描述、Python 单测、node/README 同步。
- **不改**：`retryDecision` / `selectCandidates` 路由行为、chat 的
  `works` / `returns_sources`、单价；"按上下文窗口事前预估 compact"仍属可选增强。
- **验收**：Node 55/55 · Python 287/287 · validate_catalog 全过 · recheck 实测快照
  limits confirmed 7 / conflicts 0 / suggestions 0（只遍历 chat 类模型）· 0 花费。

产物 `dist/powertokens-kit-core-0.8.5.tgz`（43,755 B），SHA-256
`e9683b9337e6365bb4964f78079fd52aa2e5adfeeb5fc8e32a3f60edbdb27386`；包内 catalog.json（catalog.snapshot.json）SHA-256
`ada6498964adb639048496046129c1ac878dc2783981105e9d98bb531adc2892`（LF、0 个 CRLF，与主 catalog 逐字节一致）。附 GitHub Release v0.8.5。

---

## 0.8.4 — 2026-11-16 · 2 项边界情况修复（D1 / D2）+ LF 行尾固定

**背景**：对 0.8.3 `c35ee5b` 的源码复核，2 项问题已在 c35ee5b 实际运行复现。
修复前账户链路与自动重试不正式接入；其余准备工作照常。

- **D1 `code:null` 不再被当成功**（`node/account-client.js`）：账户域只有 `code` 明确是
  成功值（`200` / `'200'` / `0` / `'0'`）才算成功；`code` 为 `null` / `''` / `'abc'` / 缺失
  一律返回 `ok:false` + `error.category:'invalid_response'`，即使 HTTP 200 也不按状态兜底
  （根因：`normalizeResult` 原来在 `code===null` 时退回 HTTP 判断）。明确的业务失败码
  （如 `500` / `'500'`）仍归 `business_error`，不误伤；生成域 `/v1/models` 规则不变
  （无 `code` 时按 HTTP 判断）。
- **D2 上下文超长不再抢在"已受理"之前**（`node/index.js`）：`retryDecision` 分支顺序固定为
  发送前拦截 → 受理未知 → **2xx 已受理（不重放）** → 上下文超长 → 其余。HTTP 200 + 
  `context_length_exceeded`（如流中途报错）现在返回 `action:'none'`，加不加 `pinned` 都一样，
  绝不 `compact`/`retry`/`switch`，避免宿主压缩后重发重复扣费；非 2xx（400/422）+ 上下文超长
  仍返回 `compact`。
- **`.gitattributes` 固定 `*.json` 为 LF**：消除 Windows `core.autocrlf=true` 导致 tgz 内
  catalog 为 CRLF、git blob 为 LF 的哈希分歧，三者（工作区 / 仓库 / tgz）统一 LF。

**验收**：Node `--test` **55/55**（新增 D1 四项 + D2 三项含两两组合矩阵，全部模拟响应零真实
调用）；`validate_catalog.py` 全通过；Python `run_tests.py` **287/287**。生成链路 `pt_video.py`
与 catalog 模型数据 0 改动（上下文/输出上限字段等 PT 更新 `/v1/models` 后另发一版）。
产物 `dist/powertokens-kit-core-0.8.4.tgz`（42,985 B），SHA-256
`e00615533df1e11d0940e7f37dc660001580a26a981253b17e73e17aad41c975`；包内 catalog.json
SHA-256 `83dba22af13b71c3c1731b98551ddb08ea6acb2db832b5c945d5b3f68f51afe6`（LF，与主 catalog
逐字节一致）。附 GitHub Release v0.8.4（含 tgz 与包内 catalog SHA-256）。

---

## 0.8.3 — 2026-11-16 · 源码复核 9 项修复（A1-A4 / B1-B3 / C1-C2 补强）

**背景**：对 0.8.1 `node/` 的源码复核，4 个问题已在 905e7a3 实际运行复现。

**A. 重试与选模（`node/index.js`）**：

- **A1 `maxRetries:0` 不再被默认值 3 覆盖**：仅在参数未传（`undefined`）时才用默认 3；
  `0` 是合法值（不重试），可重试错误第一次就返回 `switch` / `stop`，不返回 `retry`。
- **A2 受理状态未知独立分支**：无有效 HTTP 状态码（`status:0` / `undefined` / `ETIMEDOUT` /
  断网）时返回 `action:'stop'` + `reason_code:'acceptance_unknown'` + `accepted:'unknown'`，
  **绝不返回 retry / switch** —— 客户端没收到响应 ≠ PT 返回非 2xx，PT 可能已受理并扣费。
  发送前的视觉拦截（带图不能看图）优先于此分支（请求尚未发出，不存在受理未知）。
- **A3 上下文超长 → `compact`**：`400 + context_length_exceeded` 归类 `context_too_long`，
  返回 `action:'compact'`（先压缩上下文或缩减输入再发），`_actionFlags` 不再把它标成可重试。
- **A4 搜索候选分组**：搜索用途只有 `works===true 且 returns_sources===true` 才进
  `candidates`；值为 `null`（未实测）的进新分组 `pending_verification`（不投入使用也不判死）。
  当前 3 个搜索候选全 null：`candidates` 为空、`pending_verification` 3 个。
- **A5 / A6（重复项，0.8.2 已完成，本轮只核验未重做）**：`pinnedModel` 判定与 5 行规则表、
  `routing.recommended` 用途兜底。

**B. 账户客户端（`node/account-client.js`）**：

- **B1 严格响应校验**：账户域 2xx 响应必须是**合法 JSON 对象且带 PT `code` 字段**、
  code 表示成功才算成功，否则返回 `error.category:'invalid_response'`（HTML / 空串 /
  截断 JSON 全部失败，不再退回 `{message:text}` 当成功）。生成域 `/v1/models` 是
  OpenAI 风格 `{data:[...]}`，只要求合法 JSON 对象。
- **B2 不编造统计口径**：`data_as_of` / `is_cached` 只**原样透传**响应里服务端返回的
  统计时间与缓存状态（顶层或 `data` 内的 `data_as_of`/`as_of`/`updated_at`/`server_time`/
  `timestamp`/`generated_at` 与 `is_cached`/`cached`）；PT 没返回时为 `null`
  （此前固定填「现在」与 `false`，属于编造，已改）。
- **B3 AbortSignal + 响应大小上限**：调用方可传 `AbortSignal`（各方法 `{signal}` 或
  构造器 `cfg.signal`，与内部超时合并，外部取消返回 `category:'aborted'` 区别于
  `timeout`）；响应读取设上限 `maxResponseBytes`（默认 1 MiB 可配），content-length
  预检 + 流式边读边限 + text 长度兜底，超限中止并返回 `response_too_large`。

**C. 打包（`node/package.json`）**：

- **C1 补强**：`catalog.schema.snapshot.json` 一并入包（`./catalog-schema` 导出），
  tgz 在空目录单独安装（不克隆仓库）`loadCatalog()` 可正常读取并通过校验（已实测）。
- **C2 补强**：`catalogVersion()` 新增 `kit_version`（读包内 `package.json`），与
  `updated` 一起透出；包版本 0.8.3 / CHANGELOG 0.8.3 / `catalogVersion().kit_version`
  三处可对账。

**验收**：Node `--test` **48/48**（新增 A1/A2/A3/A4/B1/B2/B3/C1/C2 用例，全部模拟响应
零真实调用）；`validate_catalog.py` 全通过；Python `run_tests.py` **287/287**；
空目录 `npm install <tgz>` 干净安装实跑通过。生成链路 `pt_video.py` 0 行改动；
catalog 模型数据未动（U10 已在 0.8.2 订正）。
产物 `dist/powertokens-kit-core-0.8.3.tgz`，已附 GitHub Release（含 tgz 与包内
catalog.json 的 SHA-256）。

---

## 0.8.2 — 2026-11-16 · pinned 指定模型 / 用途兜底 / U10 订正 / npm pack 自包含

**P0 核心能力**：

- **`selectCandidates` 支持 `options.pinnedModel`**：用户指定模型时只判定可用性、绝不自动换模型。
  返回 `{ pinned, available, reasons, checks, candidate }`；不可用原因 code 覆盖
  `not_in_catalog` / `not_in_key_view`（不在这把 Key 的可见列表）/ `vision_unsupported`（带图片但不能看图）/
  `tool_calling_not_verified`（`verified === false`）/ `web_search_unusable` / `delisted`。
  `null`（未实测）一律不当成不可用。
- **`retryDecision` 支持 `input.pinned: true`**，按 5 行规则表实现：
  ① 429/5xx/超时只重试同一模型，耗尽后 `stop`、**不返回换模型**；
  ② 不可用/无权限/参数不支持 → `user_choice`，不自动换；
  ③ 带图片但不能看图 → 发送前 `unsupported`（`before_send:true`），交用户决定；
  ④ 工具调用实测 `null` → 照常用，`untested:['tool_calling']` 标「未实测」；
  ⑤ 401/402 → `stop`。新增输出 `reason_code` / `needs_user_choice` / `pinned_model`。
- **用途兜底**：`purpose` 不在 `auto_policy.purposes` 时，不再直接 `unknown_purpose`，
  改按 `routing.recommended` 标签匹配（`matched_by:'routing.recommended'`），仍无匹配才返回未知。
- **npm pack 自包含**：新增 `node/catalog.snapshot.json`（固定版本快照），包脱离仓库放进 `vendor/`
  后自动回退读包内 catalog；`package.json` 加 `files` 白名单与 `./catalog` 导出；
  `validate_catalog.py` / Node 单测各加一条快照一致性守门。
  产物 `dist/powertokens-kit-core-0.8.2.tgz`（33171 B），SHA-256
  `81e678ba48c7083584f59e30686db4446cd4c667900f8725af9eda552d53d89d`，已附 GitHub Release。

**U10 单价订正（PT 2026-11-16 确认「以实际扣费为准」）**：

- `wan3.0-video` 5s/720p `price_usd_5s_720p` 由 `0.20` 订正为 **`0.5` USD**，
  新增 `price_usd_5s_720p_source: "verified"`（logs.billing `per_second × 100 credits/s × 5s = 500 credits`）。
  **其他视频模型未实测，单价一律未动**。
- 新增顶层 `pricing_policy`（真相源：实扣 > estimate > 探测值；仅 1 档 verified）；
  `charging.estimate_vs_actual` 改为「预估可能偏高、只作参考，勿据此定价或对账」——
  0.8.1 实测 estimate `1000 credits` vs 实扣 `500 credits`。
- 冲突登记保留审计轨迹并标注 U10 已裁决；`catalog.schema.json` / `validate_catalog.py`
  / Python 单测同步（校验器 +13 项含快照一致性）。`max_context_tokens` / `max_output_tokens` 仍为 `null`（等 PT 答复）。

**验收**：catalog 校验全通过；Python 287/287；Node 35/35；tgz 解包到临时 `vendor/` 目录
模拟脱离仓库实跑，pinned / 用途兜底 / 重试规则 / 单价全部正确，0 花费。
`pt_video.py` 生成链路 **0 行改动**。

---

## 0.8.1 — 2026-11-16 · 付费真机实测：C4 视频侧 / R1 异步定案

**背景**：用户批准单次 **1.5 USD** 上限，用账户 B（未签资产库协议）跑一次 5 秒文生视频；
实际花费 **500 credits（＄0.5）**，提交 1 次、无重试。事实全文见 `docs/PT_项目资料总集_20261112.md` §15。

- **C4 视频侧定案**：产物落在 PT 自有 Cloudflare R2 公共桶（`pub-<hash>.r2.dev`，
  `/task-results/YYYY/MM/DD/<task_id>.mp4`），**无签名、无 TTL 字段、公开可读**；
  带不带 PT token 都返回 `206 video/mp4`；`cache-control: public, max-age=31536000, immutable`。
  推翻 D4 的「`/v1/videos/{id}/content` + 需带 token 鉴权」与 D5 的「字节 TOS 域名」；
  「14 天保留期」仍未裁决（需跨周复测，登记 U9）。`provider_name=Ali` 但产物已由 PT 转存。
- **R1 异步闭环**：提交响应 `id` 与 `task_id` 同值（是任务 ID）；`request_id` **只**在
  `X-Oneapi-Request-Id` 响应头，body 没有 `X-Request-Id`。`logs/query` 三键
  （`task_id` / `request_id` / `consume_id`）都能查到同一条 500 credits 记录。
  ⚠️ 与 chat 同步链路的 body `id`（= request_id）**同名不同义**，日志查询别用错键。
- **单价三方冲突登记（不静默选边）**：catalog 记 `0.20 USD`、上轮 estimate 试算 `1000 credits`、
  **真机实扣 `500 credits`（＄0.5）**；`logs.billing` 给出 `per_second × 100 credits/秒 × 5s`。
  86 个模型单价字段一条未改，是否订正待拍板（U10）。
- **新偏差**：`GET /v1/billing/usage` 当日 `total_cost=0`，未实时计入该笔消费 →
  `logs/query` 才是实时真相源；未签协议账户可正常跑普通文生视频（协议只约束 Seedance / `asset://`）。
- `catalog.json` 新增 `live_billing_observations`（观测 + 5 条冲突，`updated=2026-11-16`），
  `catalog.schema.json` / `validate_catalog.py`（+9 项）/ `test_unit_catalog.py`（+1 用例）同步；
  `pt_video.py` 生成链路 **0 行改动**。
- 文档：SSOT §15、看板 v2.4（新增 §13）、详细对照版 v1.4、本条。
- **验收**：`validate_catalog.py` **169 项 PASS**、`run_tests.py` **287/287 PASS**、
  `node --test` **26/26 PASS**，退出码全 0；凭证与临时文件已清理，无明文入库。

---

## 0.8.0 — 2026-11-13 · Node 核心包 / 账户客户端 / catalog 补充需求 P0

**范围**：按《powertokens-kit catalog 补充需求》§0 的 P0 清单落地，逐条固化进 SSOT §14。

- **新增 Node 核心包 `node/`**（零依赖、不发请求，与 Python 读同一份 `catalog.json`）：
  `loadCatalog` / `selectCandidates`（`auto_policy` 4 条跳过规则）/ `classifyError` /
  `retryDecision`（`retry_policy`）/ `reasoningParams`；关闭思考的取值未定，返回
  `needs_confirmation=true` 而不猜字符串。`node --test` **26/26 PASS**。
- **账户客户端按实测结论重写**：新增 `pt-gateway/account_client.py` 与 `node/account-client.js`，
  裸 `pt_live_`（不带 Bearer）、数字 `key_id`、业务判断以响应体 `code` 为准、
  `logs/query` 只按 ID 精查、`estimate` 拦截 chat、credits 原样、不保存凭证；
  `account_query` 改为兼容委托层，`pt_video.py` 生成链路 0 行改动。
  顺手修掉 `mcp.py` 中 `account_query` 自调用导致无限递归的既有 bug。
- **catalog 数据**：`auto_policy.purposes` 补 `general` / `long_report` / `vision`（共 5 种，
  每用途 ≥2 候选且跨渠道，vision 候选均校验 `vision===true`）；7 主力模型 `limits` 补
  `max_context_tokens` / `max_output_tokens` 子键；新增 `models_api_contract.limits_probe`。
  **86 个模型能力档案与价格一条未改，schema 保持 2。**
- **一键复核 `pt-gateway/recheck.py`**：默认离线 0 花费影子对账，报告写
  `probe-reports/日期.json`，不覆盖 `verified` / `official` / `documented`；
  `--live` 必须显式给 `--max-usd > 0`。本轮只跑离线（0 花费）。
- **CI**：新增 `validate_catalog.py` 与 `node --test` 两步，仍不跑 live、不上传凭证。
- **文档**：SSOT §14、看板 v2.3、详细版 v1.3、`node/README.md`、双语 README 补链。

**验收**：`validate_catalog.py` **160 项全 PASS**、`run_tests.py` **286/286 PASS**、
`node --test` **26/26 PASS**，退出码全 0。唯一线上调用是一次免费只读
`GET /v1/models`（77 模型，0 花费）。

**未完成（如实登记，见 SSOT §14.5）**：① 配套字段模板全文缺失；② `limits` 数值无官方来源
（`/v1/models` 77 个模型 0 个上下限字段 carrier），只能保持 `null`；③ 7 主力模型
`tool_calling.verified` / `works` / `returns_sources` 需真机调用，本轮按安全边界未跑 live。

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
