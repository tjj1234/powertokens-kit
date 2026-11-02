# 踩坑档案 · PowerTokens 视频 Skill 开发全记录

> 📘 本文档为中文。 [English](pitfall-archive.md)

> **对齐版本：powertokens-video `0.3.0`（图文通用 · 直连官方 REST，零 CLI / 零 Node 依赖）**
> 周期：2026-09-28 ~ 10-26（REST 直连 + 图片路由 + Seedance 协议 → 0.4.0 拆为独立类型）
> 引擎：`pt_video.py` · 配置：`~/.config/powertokens-video/config.json`（可用 `PT_VIDEO_CONFIG` 覆盖）
> 真实出片：**视频 1 次**（wan3.0-video · 480p · 5s · 2.1MB）+ **图片 1 次**（image-01 · 1:1 · 245KB）
> 单测：**0 项**（0.2.0 时点）→ **228 项离线套件**（2026-11-02，`pt-gateway/tests/`，unit + contract 两层，live 层需显式 `--live`）
> 接口：视频 `POST /v1/videos` 提交 + 轮询；图片 `POST /v1/images/generations`（同步，不轮询）
>
> ⚠️ 本档案是**对照 0.2.0 真实代码与实测结果校正过的版本**。与旧版（powertokens-wan 档案）的差异见文末「校正说明」。
> **2026-10-30 追加**：新增 pt-gateway 子模块（跨模型统一异步任务网关）踩坑 —— **A.15–A.19**（PT 认知）、**C.24–C.27**（本机工具）、**F.39**（协作流程）；并更正 B 层「轮询熔断未实现」为**已在 pt-gateway 落地**（引擎 `pt_video.py` 仍为旧实现，勿混谈）。
> **2026-11-02 追加**：新增 **A.20–A.23**（`Retry-After` 跨模块链路断裂 / 模型下架的 403 形态 / chat 的 web_search·thinking 三重条件 / 多模态探针整族假阴性）、**E.40**（PT 预签名产物 URL 触发 GitHub Push Protection）、**H.4**（协作流程）；并**更正** B 层：此前宣称「429 尊重 `Retry-After` 已落地」，实际因引擎丢响应头而恒失效，2026-11-02 才真正修好（见 A.20）。
> 📌 项目已于 2026-11-02 更名 **powertokens-kit**（`0.5.0`），覆盖 image / video / seedance / audio / chat；配置路径 `~/.config/powertokens-kit/config.json` 优先、自动回落旧的 `powertokens-video`。文中历史段落保留原版本名，是对当时状态的准确记录。

---

## ⚡ 七条最值钱的通用教训

1. **第三方网关的文档枚举不可信，必须实测真实响应。** 成功态 `succeeded`/`completed` 写法不一；无效 Key 的报错形态需拿假 Key 打一次确认。
2. **轮询逻辑必须对「查询失败」和「状态读不出」显式处理。** 0.2.0 当前**尚未做熔断**（见 B 层已知短板），长视频轮询遇网关抖动可能静默空转。
3. **HTTP 状态码语义会被重载。** 403 同时表示「余额不足 / 无权使用模型 / Key 失效」三种不同问题，顺序写错会把用户引向死路。
4. **限流（429）与服务器错误（5xx）不宜共用阈值。** 429 是暂时的、退避后大概率恢复；5xx 才该快速失败。
5. **改同一文件禁止并行 Edit，改用原子批量替换脚本。** 并发写同文件会静默丢写（返回 success 但磁盘没改）。
6. **视频/图片端点形态与同步异步差异不能想当然。** 图片是 `POST /v1/images/generations` 同步返回 `data[].url`，视频是 `POST /v1/videos` 异步轮询 task_id——盲猜 `/v1/images` 会吃 404（见 A.12）。第三方模型的「协议/白名单/素材格式」前置条件往往只能仪表盘手动处理，skill 应「检测 + 引导」而非「代签」（见 A.13）。
7. **「报错」不等于「不支持」，「文档写已实现」也不等于链路真通。** 同一能力在不同渠道 / 不同传参格式下常是一种通一种不通，单点探测的否定结论最不可靠（A.23）；跨模块时上层写对了、下层没给数据，功能照样是死的（A.20）。两者的解药一样：**让测试穿透真实调用栈**，别只测纯函数、别只看文档声明。

---

## A. PT 网关 / API 认知类（文档不可信）

这一层共同特征：**官方文档与网关真实行为不一致，或状态码语义被重载**。只能靠实测取证。

### A.01 成功态枚举写法不一：`succeeded` / `completed` 都可能出现
- **现象**：长视频生成时日志一直停在「排队中」，但 PT 后台任务早就完成了，客户端卡着不动。
- **根因**：官方提交页示例写 `succeeded`，查询页示例写 `completed`——不同文档页 / 不同模型写法不一，**没有统一契约**。
- **实测（0.2.0）**：我们这轮 REST 实测（wan3.0-video 480p）查询返回的状态链是 `queued → in_progress(30%) → succeeded`，最终在 `metadata.url` 拿到视频——**返回的是 `succeeded`，不是 `completed`**。旧档案称「网关实际返回 completed」与我们实测相反，特此更正。
- **解法（已实现）**：多路兜底——① 提取到 `metadata.url` 即判成功（最强信号，无视 status 写法）② `status` 命中成功词表（`succeeded/completed/finished/done/ready`）③ `progress≥100`。`pt_video.py` 的 `poll_task` 已实现 ①②（`succeeded`/`completed` 都认）。
- **教训**：判定成功别只信单一字段。「拿到产物地址」是比「状态字段等于某个词」强得多的信号。

**解决说明（2026-11-02，PT 官方）：** PT 已将成功态统一为 `completed`。我们的轮询**同时接受** `succeeded` 与 `completed`（`pt_video.py` 与 `pt_gateway.TaskState.normalize`），既向前兼容，也能容忍尚未迁移的端点。无需改代码。

### A.02 查询返回非 200 时旧逻辑会空转 —— ⚠️ 0.2.0 已知短板（未做熔断）
- **现象 / 根因**：若 `GET /v1/videos/{id}` 反复返回 429/500/502/503，响应体就没有 `status` → 状态变量变空 → 既不匹配成功、也不匹配失败 → 继续 `sleep` 轮询，一路空转到 `POLL_TIMEOUT`（当前 600s）。日志看起来「一切正常，只是在排队」。
- **当前状态**：0.2.0 的 `poll_task` 仅做「超时抛错 + 提示稍后单独 `GET` 复查」，**没有** 429 退避、5xx 计数熔断、未识别状态计数熔断。这是已知短板，长视频轮询遇网关抖动可能静默空转。
- **解法（路线图，尚未实现）**：加三重熔断——连续 8 次 5xx 失败退出；`status` 读不出 / 未识别值计数 40 次熔断并打印原始响应；429 单独计数 + 指数退避（间隔翻倍，上限 60s）。
- **教训**：「静默继续」是轮询逻辑最危险的默认分支。任何「读不出状态」都必须显式计数 + 熔断。

### A.03 429 与 5xx 不宜共用严阈值（同 A.02 路线图）
- **现象（推演）**：修 A.02 时若把 429 和 5xx 一起计入同一严阈值，会在任务正常生成途中误杀（长视频轮询几十次极易撞限流）。
- **解法（路线图）**：429 单独计数、阈值放宽到 30 次 + 指数退避；5xx 用严阈值快速失败；查询成功时两个计数器都重置。
- **教训**：可恢复错误与不可恢复错误的熔断策略必须分开。

### A.04 403 语义重载：余额不足 / 无权 / Key 失效 三选一（真实，我们实测遇到）
- **现象**：余额不足时报 403，信息形如 `token quota is not enough ... need quota: ＄0.200000`（我们这轮 720p 提交即触发）。
- **根因**：同一 403 承载三种语义：余额不足 / 无权使用模型 / Key 失效。顺序写错会把「没开模型权限」误报成「Key 已失效」。
- **解法（当前实现）**：0.2.0 **未做细分类**（`classify_rest` 未实现），统一走 `[error] 生成失败：{message}` 把网关原文**透传**给用户 / agent——至少不误导方向。建议后续按「先具体特征（`token_invalid` / `quota` / `not permitted`）后状态码兜底」重排。
- **教训**：错误分类的价值不在分类本身，而在把用户引向正确修复路径；当前实现靠用户 / agent 读原文。

**解决说明（2026-11-02，PT 官方）：** 该重载已收敛为清晰的三档，客户端可直接分支判断：
- `401 Unauthorized` → API Key 失效 / 过期 / 缺失
- `402 Payment Required` → 余额不足 / 额度耗尽（仅生成时）
- `403 Forbidden` → 无权限 / 模型下架

我们的代码本就原样透传这些状态码（不做重映射），无需改代码。只读的账户/计费命令不会触发 `402`。

### A.05 无效 Key 的报错形态 —— ⚠️ 待实测（0.2.0 未验证）
- **说明**：我们这轮只用了**真实 Key**，没拿假 Key 打 REST 端点，无法证实「HTTP 500 + `token_invalid`」这一传闻。鉴权用 `Authorization: Bearer <token>`，实测可用。
- **建议**：接新网关第一件事——拿一个假 Key 打一次 `POST /v1/videos`，记录真实返回（状态码 + body），再补进分类器。旧档案把此条写成「已确认事实」，此处降为「待实测」。

### A.06 `GET /v1/models` 返回的是「该令牌被授权的模型」，不是全局目录
- **现象**：同事的 Key 不限制模型时正常出片；一旦在后台开了模型白名单（哪怕已勾 wan3），生成就报「key 没有权限」。
- **根因**：`/v1/models` 反映该 Key 的可用模型（白名单**精确字符串匹配**）。后台勾选项显示名（如「Wan 3.0」）与真实 ID（`wan3.0-video`）易不一致。
- **实测（0.2.0）**：我们实测新 Key 返回 **32 个视频模型**（口径：仅 video 类型；若含全部类型会更多，**需注明口径**）。旧档案写「85 个」未注明口径，此处更正为实测值。
- **解法（已实现）**：提交前 `precheck`——显式 `-m` 先与 `/v1/models` 比对，不在则 1 秒内 `exit 3`、不进生成、不扣费，并给相近候选 + 大小写纠正（`pt_video.py` 的 `precheck_explicit_model` 已实现）。注意：当前实现是「模型不存在则报错退出」，**不是**「跳过该 Key 换下一个」。

### A.07 「PT 有 900s 硬上限」是误判 —— 异步生成服务端无上限（真实，用户纠正）
- **现象**：同事截图里跑到 908s 失败，曾被推断「PT 侧有 900s 任务上限」。
- **根因**：908s 是**客户端轮询放弃的时刻**，不是 PT 的限制。用户明确纠正：生成是异步的，没有 900s 上限。
- **解法（已实现）**：`pt_video.py` 提交 HTTP 超时 **120s**（按用户要求）、轮询间隔 **10s**（按用户要求）、整体轮询总上限 **600s**（仅为防无限轮询，**非 PT 限制**；真实出片约 3 分钟，120s 总上限会失败，故默认 600s，可 `--timeout` 调）。文案统一为「本地轮询守护超时，服务端无硬上限」。
- **教训**：看到超时数字，先分清是「谁超时了」。客户端守护 / 网关 / 上游模型三层各有各的超时。

### A.08 「无可用渠道」多为瞬时故障，不是用户 Key 问题
- **现象**：生成报「无可用渠道 / 未指定模型 / 该令牌无权使用模型」，看起来像用户配置问题。
- **根因**：两道关——① 令牌层（Key 是否绑模型白名单）→ 报「无权使用模型」；② 渠道路由层（网关能否找到「支持该模型 + 有余额 + 未禁用」的上游）→ 报「无可用渠道」。绝大多数是 ②，属瞬时故障。
- **解法（建议）**：先 `models` 确认模型在可用列表，再用完全相同的命令复现。话术要明确告诉用户「不是你的 Key 问题」，别让用户去改配置白折腾。

### A.09 无余额查询接口，消耗只能估算（真实，`estimate` 已实现）
- **现象**：想报「本次花了多少钱」，成功路径不返回消耗金额。
- **根因**：PT 按美元钱包计费、视频提交即预扣，但 `/v1/dashboard/billing/*` 全 404、无可编程查余额的公开接口。
- **解法（已实现）**：用**真实探测的预扣单价表**估算并明确标注「估算」。单价表零成本建成——余额不足时提交必 403 但**不扣费**，而 403 会返回该模型的 `required pre-consume` 真实预扣额。系数：时长线性、1080p = 720p ×2。未知模型打印「未知」，绝不瞎编数字（`pt_video.py` 的 `estimate_cost` 已实现）。
- **教训**：拿不到的真值宁可标「估算 / 未知」，也不要编。

### A.10 `media` 只收 http(s) URL；`@xxx` 占位符不识别
- **现象**：① 用户想直接传本地截图 / 图片生成视频；② prompt 里写 `@male_lead` 想锁角色，出来却是通用形象。
- **根因**：① REST 接口不接收本地文件上传，`media[].url` 必须是公网可访问地址；② wan3.0 不识别 `@xxx` 占位符语法。
- **解法（已实现）**：本地路径直接报错并说明原因（前置防护，避免提交后才失败白扣时间）；锁角色走 `--ref-image` / `--first-frame` 等 `media` 数组类型。注意 `reference_*` 与 `first_frame`/`last_frame` 互斥，不能混用。
- **教训**：参数校验要前置到提交之前；模型不认的语法要提前告知替代方案。

### A.11【新增 · 致命】请求体枚举字段必须传字符串（我们这轮真踩，旧档案遗漏）
- **现象**：第一次提交直接报错：`json: cannot unmarshal number into Go struct field .seconds of type string`。
- **根因**：`seconds` 是 `enum<string>`，`size`（480P/720P/1080P）、`ratio`（adaptive/16:9/...）、`model` 同为**字符串枚举**。传整数 / 小写会被服务端拒。
- **解法（已实现）**：`pt_video.py` 提交时 `"seconds": str(args.duration)`、`"size": args.resolution.upper()`、`ratio` / `model` 均为字符串，再 `json.dumps`。所有枚举字段统一转 `str`。
- **教训**：REST 提交前，先用 `models --json` 或官方文档确认每个字段的类型——**枚举字段宁可显式转字符串**，这是最易踩、最可复现的坑。

---

**状态（2026-11-02）：** 已核对合规——Seedance 提交体发送 `"seconds": str(args.duration)`，且所有枚举字段（`model`、`size`、`ratio`）均为字符串；线上不会发出 int。`seconds` 同时接受 int / string，故字符串写法两边安全。无需改代码。

### A.12【新增 · 0.3.0】图片端点不是 `/v1/images`，是 `/v1/images/generations`（同步，不轮询）
- **现象**：按视频端点类比猜 `POST /v1/images`（无 `/generations`），返回 `404 Invalid URL (POST /v1/images)`；文档页写的是 `POST /v1/images`，但实际网关不认（文档与实际不一致，同 A 层老问题）。
- **根因**：PT 图片接口是 OpenAI 风格，带 `/generations` 后缀：`POST /v1/images/generations`。且图片是**同步返回**——响应体 `data[].url`（数组，可多张），直接拿到链接，**不需要轮询**（和视频的异步 task_id 轮询完全不同）。
- **实测（0.3.0）**：`POST /v1/images/generations` 带 `{"model":"image-01","prompt":"x"}` 返回 HTTP 200 + `{"data":[{"url":"http://...aliyuncs.com/...jpeg?Expires=...&Signature=..."}],"metadata":{"success_count":1,...}}`——已真生成并下载到 `out/test_image.png`（245KB）。
- **解法（已实现）**：`generate_image` 走 `IMAGE_ENDPOINT="/v1/images/generations"`，同步取 `data[].url` 逐张下载（扩展名 .png），不轮询。图片字段用 `aspect_ratio`(枚举串)/`n`(1-9)/`seed`，**没有 `seconds`**（图片无时长）。
- **教训**：视频/图片端点形态、同步异步差异巨大，绝不能共用一套提交+轮询逻辑；先实测端点再写代码（我们盲猜 `/v1/images` 吃了 404）。

### A.13【新增 · 0.3.0 → 0.4.0/0.4.1】Seedance 拆为独立类型：普通 video 不含它 + 资产库 asset:// + 本地标记开通流程
- **现象（0.3.0 误判，已纠正）**：早期用 `dreamina-seedance-2-0-260128` 文生视频，传**假** `asset://` 占位符，
  报 `BytePlus video models only accept media`，于是误以为「Seedance 文生视频必须带 media、纯 prompt 不行」。
  **这是误判**：报错根因是 asset id 是假的，不是缺 media。
- **根因（经真实 key 实测澄清，2026-10-26）**：① Seedance 全系（1.0/1.5/2.0/2.5，非仅 2.0）需先签
  《Asset Library User Agreement》，但**只能仪表盘手动签**（`https://powertokens.ai/assets-library?tab=1`），
  **无 API 端点可程序化签署**；
  ② 图生视频的素材须经资产库上传拿 `asset://<ASSET_ID>` 传入（非普通 http URL）；③ **文生视频只需 prompt，
  不需要任何 media**——提交时由 skill 把 prompt 放进 `media:[{type:text, text:...}]`（服务端不认顶层 `prompt`）；
  ④ 它有三个独有前置门槛（签协议 / 素材走资产库 / 文生靠 media-text 而非顶层 prompt），与普通视频模型系统性
  不同——折进 `video` 类型并不合理，故提升为独立类型。
- **解法（0.4.0/0.4.1 重构，已实现）**：把 Seedance 拆为**第三种独立类型** `seedance`（与 `image`/`video` 并列）：
  - `is_video_model` 不再识别 seedance；`kind_of_model` 增加 `seedance` 分支；`--type` 增加 `seedance`；
  - 普通 `video` 的 `AUTO_PRIORITY_VIDEO` 剔除 seedance，`auto` 不会误选它；Seedance 走自己的 `AUTO_PRIORITY_SEEDANCE`；
  - `generate_video(force_asset=True)`：文生视频把 prompt 放进 `media:[{type:text}]`；只在用户**传了图/视频媒体**时
    才要求 `asset://`（`build_media(force_asset=True)` 对 http(s) 前置拒绝），纯 prompt 文生视频不触发该校验；
  - 协议开通流程「**本地标记 + 报错回退**」：`seedance_enable_flow` 未标记时弹 4 步「跳转 PT 开通」引导（不阻断）；
    `python pt_video.py seedance enable|disable|status` 管理本地标记；`is_agreement_error` 命中服务端「未签」关键词时
    `reset_seedance_signed` 并重新弹引导；首次成功生成后 `mark_seedance_signed`；
  - **资产上传闭环**：`python pt_video.py upload -f 图.png`（免费）走 `POST powertokens.ai/api/.../upload` 拿
    `task_id` → 轮询 `get-asset-id` 返回 `asset_id`，引用为 `asset://<id>`，供图生视频 `--first-frame` 使用。
- **实测（0.4.1，真实 key `...3eu2` 已签协议、80 credits）**：
  - `models -t seedance`：返回 7 个 seedance 模型均 `available=yes`；最便宜 `seedance-1-5-pro-251215`；
  - **文生视频** `generate --type seedance -m seedance-1-5-pro-251215 -p "a cat walking"`：服务端接受 media-text 提交，
    走到 `pre-consume quota failed`（余额 $0.08 < 预扣 $0.247），**证明链路通、不报协议错也不报 media 错**；
  - **upload**：`upload -f 图.png` 免费拿到真 asset_id `asset-20261026180014-bc47m`；
  - **图生视频** `generate --type seedance -m seedance-1-5-pro-251215 --first-frame asset://真id`：服务端接受真 asset，
    同样走到 `pre-consume quota failed`（**无 asset 错误**，证明真 asset 被接受）；
  - 全程**未扣 credits**（失败都在预扣费校验前）。
- **结论**：Seedance 端到端链路 100% 打通（协议✓、文生 media-text✓、图生真 asset✓、上传闭环✓），仅差充值。
- **教训**：① 第三方模型的「协议/白名单/素材格式」前置条件往往只能仪表盘手动处理，skill 应「检测 + 引导」而非「代签」；
  ② 当某模型约束与普通同类系统性不同时，应提升为独立类型而非打补丁；③ **不要把一次报错直接当成模型约束**——
  当初的 `only accept media` 是假 asset 触发的，纯 prompt 文生视频其实可行，必须换真 key 实测才能下结论。

### A.14【新增 · 0.4.1】PT 资产 id（asset://）跨账户可用、无读路径隔离
- **现象/问题**：用户给第二个 key（账户 B，末 4 位 `8EWG`，非账户 A），问「账户 A 上传的 asset id 能否在账户 B 的 key 下生成」。
- **实测（真实双账户）**：用账户 A 上传的真 asset `asset-20261026180014-bc47m` 作首帧，拿账户 B 的 key 提交
  `generate --type seedance -m seedance-1-5-pro-251215 --first-frame asset://asset-20261026180014-bc47m`：
  服务端接受跨账户 asset，**生成任务完整跑完**（in_progress 50% → 返回 result 链接 `task_aayPol2v2RbrlHSmNFImRzqTXDFud3KZ.mp4`），
  消耗记在账户 B 上。即 **asset:// id 是全局可解析、跨账户可读的共享引用**。
- **安全含义**：① PT 资产库**不做账户级读隔离**——任何知道 asset id 的人都能在自有 key 下引用它做生成（只要过 pre-consume）；
  ② 因此不要把涉密 / PII 内容放进 asset 指望账户隔离，**asset id 应视作「不可猜测但可共享」的 token**；
  ③ 这也意味着「共享素材库 / 团队协作上传一次多人生成」是平台原生支持的能力，可在产品里利用。
- **附带观察（下载链路）**：该 result 视频 URL 在本沙箱直连 + 经代理均返回 `403 Forbidden`（R2 桶对直连 GET 拒，热链/访问策略所致），
  与跨账户无关、非引擎 bug（引擎 `poll_task` 第 679 行完整取 PT 返回的 `metadata.url`，seedance 给的是裸链接无签名 query）。
  生成任务 server-side 完成即为铁证；结果文件能否经裸 URL 公网下载需另查（可能与桶的 Referer/签名策略有关）。
- **处置**：本发现不影响 skill 代码（asset 引用行为符合文档预期）；仅作为安全/产品注意事项沉淀。

### A.20【新增 · 致命】`Retry-After` 「已实现」却永远拿不到：跨模块错误对象丢响应头
- **现象**：本档案 B 层写着「429 尊重 `Retry-After`，已在 pt-gateway 落地」，但真机限流时**每次**都按 1→2→4s 盲目退避，从没读过服务端给的值。
- **根因**：缺陷在**跨模块契约**上，不在任何单个模块里——引擎 `pt_video._api_call` 捕获 `HTTPError` 后只抛 `ApiError(code, message)`，把 `e.headers` 丢了；上层 `pt_gateway._retry_after()` 读 `err.raw["headers"]` 恒为空 → 永远返回 `None`。两层单看都「对」，拼起来是死的。
- **为什么测试也没发现**：离线用例只测 `_retry_after` 这个**纯函数**（手工喂一个带 headers 的假对象），从不经过引擎的 `HTTPError` 转换——所以「200+ 条全绿」证明不了链路通。
- **修法（2026-11-02 已实现）**：`ApiError(status, message, headers=None)` 透出响应头；`_api_call` / `_upload_asset` / asset 轮询**三处** `HTTPError` 转换全部传 `headers=getattr(e, "headers", None)`；`_retry_after` 优先读 `err.headers` 且头名**大小写不敏感**（服务端可能发 `retry-after`），`raw["headers"]` 保留兼容；新增一条测试**在 urllib 层造带头的真 `HTTPError`**，验证「HTTP 响应 → ApiError → `_retry_after`」整条链。
- **修好后引出的新风险（必须一起处理）**：以前拿不到 `Retry-After`，不存在睡过头；现在能拿到了，就会出现「服务端要求等 999s、但总超时只有 10s」。已加 **deadline 保护**：`Retry-After` 超过剩余超时 → 立即抛 `429 限流：服务端要求 999s 后重试，超过剩余超时 10s`，**不硬睡**（有用例断言此时 `sleeps == []`）。
- **教训**：① 错误对象是跨模块契约，**透出响应头和透出 body 一样重要**，否则上层的「智能退避」永远只能退化；② 纯函数单测会漏掉「上游根本没给数据」这类缺陷，至少要有一条**穿透真实调用栈**的链路测试；③ 文档写「已实现」不算数，能力声明要能指到一条**会让它变红的测试**。

### A.21【新增】模型下架的 403 形态：`model_not_found ... not listed (pending)`，而它还在 `/v1/models` 里
- **现象**：真机 full 档跑 `deepseek-v4-flash-0731`，403：`model_not_found ... not listed (pending), please contact admin to enable it`。
- **认知要点**：这句话同时承载两种可能——「你的 key 没开这个模型」和「平台已下架 / 待上架」。`not listed (pending)` + `contact admin` 是**平台侧话术**，不是在说 key 权限。更要紧的是它**仍然出现在 `/v1/models` 列表**里（H.2 的延伸）——所以「列表里有」≠「能用」，「调用 403」≠「key 无权限」。
- **处置**：从 `catalog.json` **删除**该条目，而不是标 deprecated。依据是真机已证明平台级不可用、非临时维护；留着会让 CLI `list` / MCP `list_capabilities` 把它当活模型展示，用户调用必 403——**静默陷阱比直接删更危险**。`deprecated` 只适合「还在但将移除」。
- **连带风险**：测试档位（`harness.TIERS`）里硬编码的模型 ID 同样会腐化。已加离线守卫 `TestTierModelReferences`：tier 引用的模型必须存在于 catalog，**否则离线就红**，不必等到真机 403 才发现。
- **教训**：① 模型清单天然会腐化，**任何硬编码模型 ID 的地方都要有离线守卫**；② 「删」比「标 deprecated」安全的前提是**已被真机证明不可用**，否则会误删一个只是在维护的模型。

### A.22【新增】chat 的 web_search / thinking 是「渠道级 + 客户端传参 + 上游白名单」三重条件
- **现象**：给 38 个 chat 模型标「能不能联网 / 能不能思考」，发现**没有统一答案**，连同一模型在不同渠道也不一样。
- **真相（PT 官方《模型能力汇总》+ 真机交叉）**：① 网关**不注入**这些参数，必须客户端显式传；② 参数名**按渠道不同**——ali 是 `enable_search:true` / `enable_thinking:true`，byteplus 与 zhipu 的联网搜索走 `tools` 里的工具声明，zhipu / mimo 的思考另有参数；③ 即使传了，**是否真生效由上游白名单决定**（同渠道不是所有模型都支持）。
- **处置**：能力字段用**三值**而非布尔——`true` / `false` / `"conditional"`，并单独记 `web_search_param` / `thinking_param` 与 `_source`（`configured` / `verified` / `official` / `official+verified`）；`"conditional"` 的 notes 明确写「网关不注入、需客户端传参、依赖上游白名单」。
- **教训**：① 能力要分三层看——**网关支持**（透传）/ **协议支持**（参数存在）/ **上游支持**（真生效），揉成一个布尔就是撒谎；② 不确定时用三值枚举 + notes，比硬写 `true/false` 既诚实又可用；③ 官方文档按渠道分表，本身就在告诉你：**能力是渠道属性，不是模型属性**。

### A.23【新增 · 致命】多模态探针整族假阴性：单格式 base64 `data:` URI 会被部分渠道拒
- **现象**：真机探针（发一张 `data:image/png;base64,...` 单图问「这是什么」)判 vision，结果 ali-qwen3 / BytePlus-seed / 智谱 / DeepSeek 等渠道**整族报错** → 判 `vision=false`，**21 个实际支持视觉的模型被误判**。
- **根因**：不同渠道对图片输入的**承载格式**兼容性不同（`data:` URI / 公网 URL / 必须先上传）。探针只用一种格式，被拒就当成「模型不支持视觉」，其实只是「这种传法这个渠道不认」。
- **校正**：拿官方《模型能力汇总》§4 逐模型清单比对，21 个 `false` 全部应为 `true`；`seed-*` / `dola-seed-*` 还额外支持 `video_in`。官方清单没列到的（如 `qwen3-coder-plus`）保持 `vision: null`，不猜。
- **处置**：① catalog 顶层加 `multimodal_source`，写明数据来源与「网关原样透传、实际可用性以上游为准」的边界；② 每条能力带 `_source`，让人能分清是探测来的还是文档来的；③ 探针降级为「辅助交叉校验」工具，docstring 明确写**不可单独作结论、有假阴性风险**。
- **教训**：① **单点探针的否定结论最不可靠**——「报错」可能只是「这种传法不行」，不等于「这个能力没有」；② 能力标注必须带**来源与置信度**，别把探测值和文档结论揉成一个布尔；③ 不知道就写 `null`，别用 `false` 冒充「已确认没有」。

---

## B. 异步 / 轮询逻辑设计类（通用模式 + 0.2.0 已知短板）

抽离出 PT 场景也成立的通用教训——任何「提交任务 + 轮询取结果」的异步对接都会遇到。

| # | 坑 | 正确做法 | 0.2.0 状态 |
|---|---|---|---|
| 11 | 成功判定只信 `status` 字段 | 多路兜底：拿到产物 URL > status 词表 > progress≥100；递归提取字段，兼容 `data/result/task` 包裹与别名键 | ✅ 已实现（URL + 词表） |
| 12 | 「读不出状态」被当成「还没好」 | 三种情况显式计数熔断：查询非 200 / status 缺失 / status 未识别；熔断时打印原始响应 | ⚠️ 仅超时抛错，**未做熔断**（已知短板，见 A.02） |
| 13 | 可恢复 / 不可恢复错误同阈值 | 429 单独计数 + 退避；5xx 严阈值；查询成功两计数器都重置 | ⚠️ 未实现（路线图，见 A.03） |
| 14 | 任务卡住就重新生成 | 先跑只读诊断复查真实状态（当前 0.2.0 **无 `status` 子命令**，需手动 `GET /v1/videos/{id}`），若已完成直接取 URL 下载，别重新提交 | ⚠️ 手动替代，子命令待补 |

---

## C. 本机环境 / 工具类（Windows + WorkBuddy 沙箱，已验证）

这一层最磨人——工具本身时好时坏，且失败方式很安静（报 success 但没生效）。核心策略：**结果写文件再 Read**。

- **C.15 同文件并行 Edit 静默丢写（致命）**：一次发 4 条 Edit 改同文件，只有 2 条落盘、另 2 条静默失败，工具返回仍是 success。解法：改用 Python 原子批量替换脚本（先备份 → 逐条断言命中 1 次 → 全中才写回 → 任一失败回滚）。**跨不同文件时并行才安全**。
- **C.16 Bash PATH 损坏**：`dirname: command not found` / `head: command not found` / exit 127。解法：命令开头显式补全 `PATH`；Python/Node 用绝对路径；`tail/grep/mv` 常不可用 → 输出重定向文件再 Read、移动用 `os.rename`、匹配用 Grep 工具。
- **C.17 PowerShell 偶尔不回显 stdout**：exit 0 但零输出。解法：结果一律 `Out-File` 落盘再 Read。
- **C.18 PowerShell 中文 GBK 乱码**：`-Encoding utf8` 也乱。解法：设 `$env:PYTHONIOENCODING="utf-8"`；诊断输出把关键结论放 ASCII 字段（exit code / JSON key / 数字）。
- **C.19 当前模型不支持读图**：用户贴截图报错 `does not support reading images`。铁律：**绝不凭想象描述截图内容**，必须明说「我看不到」，改用代码 / HTTP 实测定位，或请用户贴纯文本。
- **C.20 `/tmp` 在 Windows 不可见**：日志写工作区绝对路径。
- **C.21 假 API key 字面量被安全过滤截断**：`sk-test-...` 实际发出去只剩 3 字符，误判「Key 太短」。解法：程序化构造测试假凭据，避开真实格式特征。
- **C.22 curl 对 localhost 走代理**：探测本地服务加 `--noproxy '*'`。
- **C.23 长命令前台被超时 SIGTERM 杀**：视频生成 3~8 分钟，前台极易被杀。解法：生成任务一律 `run_in_background: true`，完成后回看结果再答复。注意 `dangerouslyDisableSandbox` 对后台任务不生效。

---

## D. git 沙箱类（跨项目通用，已验证）

WorkBuddy 沙箱里 **git 的 loose ref 写入不落盘**是一切怪象总根源。核心策略：**绕开一切会动 ref 的 git 命令**。

- **D.24 push 凭据失败**：`could not read Username: terminal prompts disabled`。解法：命令行临时改用 `gh` 凭据助手，不写持久配置。
- **D.25 loose ref 写入不落盘**：`rev-parse HEAD` 报 unknown revision。解法：`git write-tree` + `commit-tree -p <parent>` 生成 commit 对象，再用 `fs.writeFileSync('.git/refs/heads/<br>', sha+'\n')` 手写 ref（建父目录后再写）。
- **D.26 `reset --hard` 破坏更隐蔽**：先删 ref 再重写，重写不落盘 → 分支 ref 消失。解法：用 Node 重写 ref + HEAD，再 `git read-tree` 修 index，别再用 `reset --hard`。
- **D.27 远程跟踪 ref `origin/*` 也会丢**：用本地分支 ref 或 commit SHA 合并（`merge --no-ff <sha>`）。
- **D.28 checkout/merge 偶发漏写工作树**：用 `git checkout -f -- .` 从 index 重建（不写 ref）。
- **D.29 拿陈旧本地 ref 做历史判定 → 假结论**：❌ 禁止 `git log origin/xxx | grep`；✅ 用不可变 SHA + `git merge-base --is-ancestor <sha> <实时 tip>`（tip 来自 `git ls-remote`）。
- **D.30 `ls-tree` 不加 `-r` 列目录回显为空**：加 `-r` 递归列出；交叉验证组合 `ls-tree -r` + `diff --name-only` + `cat-file -t`。
- **D.31 与其他 agent 共用工作目录**：用「零接触提交」（临时 index + commit-tree + SHA refspec 推送）。
- **D.32 校验文件一致性直接比字节数 → 误判**：先归一化行尾（CRLF→LF）再比。
- **D.33【致命 · 丢数据】`git rm` 会连带清空整个父目录**：执行 `git rm docs/pitfall-archive.html` 后，**整个 `docs/` 从工作区消失**——同目录的 `pitfall-archive.md` / `publish.md` 一并被删，`git status` 把它们显示成 ` D`。当时 md 有 28282 字符未暂存编辑，而 HEAD 里只有 22462 字符的旧版，编辑随工作区一起蒸发。
  - **判据**：删完立刻 `git status --porcelain`。若出现**你没指定的**文件被标 ` D`，就是踩了这个坑，而不是你真的删了它。
  - **恢复**：别用 `checkout` / `reset`（同样会漏写工作树，见 D.28）。用 `git cat-file -p HEAD:<path>` 取原始字节 → 用脚本写回磁盘（先建父目录），再逐条重放未提交的修改，每条 `assert 锚点 count == 1`。
  - **预防**：任何动 worktree 的 git 命令（`rm` / `filter-branch` / `checkout` / `reset`）执行前，先把未提交改动 `git add` 进 index **或复制到仓库外**；提交后再校验目标文件仍在。

---

## E. 分发 / 安全类

skill 要给不会代码的同事用，「安全」和「零门槛」是两条硬约束。

- **E.33 config 文件不能进分发包（Key 泄漏）**：Key 存 `~/.config/powertokens-video/config.json`，若打包进 zip 等于把 Key 明文送人。解法：config 与 skill 包分离，zip 只含 `SKILL.md` / `pt_video.py` / `mcp_server.py` / `README.md` 四件；每次打包后必做泄漏扫描（`grep` Key 片段，命中数必须为 0）；config 权限 0600。
- **E.34 同事不会代码 → 不让用户跑任何命令**：确立「用户全程只用说话，命令一律由 agent 代跑」——对话里识别 `sk-pt-` 开头字符串 → agent 自己 `config --set-key` → `check` 回显池状态 → 直接干活。SKILL.md 明确禁止让用户跑命令 / 编辑文件 / 设环境变量。
- **E.35 「同事没装 Python」是伪问题**：WorkBuddy 客户端自带托管 Python/Node 运行时。后续彻底去 CLI 改纯 REST 后，连 Node 依赖也去掉了——只要 Python 3.9+。少一层依赖少一类故障。

- **E.40 PT 产物 URL 自带预签名凭证，写进报告/文档会触发 GitHub Push Protection**：push 被 GitHub 拦下，报 **VolcEngine Access Key ID**，命中 `pt-gateway/e2e_report.md:32`——那是 seedream 生成图片的下载地址，query 里带 `X-Tos-Credential=***`（火山 TOS 的 Access Key ID）+ 签名 + `X-Tos-Expires=86400`。PT 返回的产物 URL（TOS / R2）常是**预签名 URL**，凭证与签名写在 query 里，形态足以触发 secret 扫描；它们 24h 就过期，但扫描器不认「已过期」，且**一旦进了历史提交就会永久拦推**（扫的是整条待推分支，不是本次改动）。处置：① 测试报告/临时产物一律不进仓库——`tests/reports/` **整目录** gitignore（一开始只忽略 `*.md`/`*.json`，一个 `.txt` 就漏网）；② 已被历史带上的用 `git filter-branch --index-filter "git rm --cached <path>" --prune-empty -- --all` 从全部 commit 移除再推（远端从未推成功过时零风险）；③ 文档贴产物 URL 示例时 query 截断成 `?X-Tos-Credential=***`。教训：**secret 扫描不只认 API key 形态，也认云存储签名 URL**；测试产物默认 gitignore，别等被拦。

---

## F. 协作 / 流程类（用户明确要求）

这些不是技术 bug，是协作边界——踩了会直接消耗信任。

- **F.36 代码改动先讨论后动手**：方案聊清楚再改；但明确的 bug 修复倾向直接动手。本项目多次遵守：用户问「先回复我，同意后再修改」→ 先给方案评估，等确认才动手。
- **F.37 运行中的服务不碰**：dsh 之类进程的重启 / 杀进程 / 改配置一律先征得明确确认，优先只读诊断、非侵入式动作。
- **F.38 范围要守住 + 证据先行**：说单站点就只在单站点内诊断；排障走四段式（每步配证据 → 推结论 → 当前状态 / 目标 / 对比 / 建议），不做脱离证据的泛化判断。A.07（900s 误判）、D.29（陈旧 ref 假结论）都是违反证据先行的代价。

---

## G. pt-gateway 子模块（2026-10-30 新增）

> pt-gateway = 跨模型统一异步任务网关（`pt-gateway/pt_gateway.py` + `server.py` + `mcp.py` + `catalog_crawler.py`）。
> 这一轮的核心产出：纠正三处端点硬编码、无 key 爬 PT docs 建「公开层全局真相」、把权限判断做成 fail-closed 门禁。

### A.15【新增】「PT 端点全统一 /v1/videos」只对提交/查询成立，cancel 与 audio 是 vendor 特定路径
- **现象（两次反向误判）**：① 早期以为「Seedance / Vidu / Wan 各有独立提交/查询接口」→ 实测**是误判**，PT 已统一：提交 `POST /v1/videos`、轮询 `GET /v1/videos/{id}`；② 纠正后又被写成「全部统一在 /v1/videos 之下」→ **也是误判**。
- **真实形态**：统一是**分层**的——
  - 提交 / 查询：统一 `POST /v1/videos` + `GET /v1/videos/{id}`；
  - 取消：仅 Seedance 经 ByteDance/BytePlus 提供 `DELETE /byteplus/api/v3/contents/generations/tasks/{id}`（queued→cancelled，2026-10-30 实测）；
  - 音频(TTS)：`POST /ali/api/v1/services/aigc/multimodal-generation/generation`（Ali/DashScope 兼容，**同步**返回 url），**不走** `/v1/videos`。
- **教训**：PT 统一的是「提交/查询」传输层，**没统一**「取消/音频」。写网关要按能力逐个确认端点，别用一句「全统一」概括，也别一句「各有各的」概括。

### A.16【新增 · 致命】PT 无异步任务取消接口（用户已与 PT 官方确认，2026-10-30）
- **现象**：想给「取消进行中的视频任务」做通用实现，按 OpenAPI 文档推断 wan / kling / vidu 各有 `DELETE /.../tasks/{id}`。
- **真相**：用户已**与 PT 官方确认——PT 没有异步任务的取消接口**。文档里的 `/tasks/{id}` 只有 **GET 查询**，DELETE 并不存在。唯一例外是 Seedance 的 BytePlus vendor 端点（A.15）。
- **处置**：`cancel()` 对非 seedance 一律**诚实**返回 `{supported:false}` + 理由说明——不做 best-effort 猜端点、不假装成功。其余模型无法在服务端取消进行中的任务，**只能等其自行结束**。
- **教训**：① 文档「存在 `/tasks/{id}`」≠「存在 DELETE 取消」；② 拿不准就诚实降级，比 best-effort 发一个不存在的 DELETE 更可靠；③ **发现端点（读公开文档）不耗 key**，**验证取消生效（提交+取消）才耗额度**——既然官方已确认没有，就别花额度去试。

### A.17【新增】`/v1/models` 只是「本 key 权限视图」，不能当全局目录 / 下架依据
- 承接 **A.06**：不同 key 返回的模型列表不同（白名单 / 额度）。所以「本 key 列表里没有某模型」**不等于**「该模型已下架」。旧 `ModelRegistry` 就这么误判过——把「别的 key 有、本 key 无」写成 `delisted=true`，多 key 场景下会误杀合法调用。
- **正确分工（两层）**：
  - **下架判定看公开层**（无 key 爬 PT docs，见 A.18）；
  - **`/v1/models` 只记 `key_permitted` 权限视图**；仅当模型**同时**不在 live 列表、也不在公开层，才判 `delisted=true`。
- **权限门禁（用户拍板 fail-closed）**：显式指定的模型若不在本 key 的 `/v1/models` 列表 → 本地直接 `ApiError(403)`「当前 key 没有模型 X 的调用权限」，**不发起真实生成请求**——省掉一次必 403 的调用与额度（比「让服务端报错」的 fail-open 更省）。
- **两个边界（都要处理，否则出 bug）**：
  - `model=auto` 不做门禁校验，但**选模型时必须在本 key 权限集合内挑**——否则 auto 会选中无权限模型再被门禁拦（实测曾选中无权限的 `wan2.2-kf2v-flash`，改为 key-aware 后正确选中 `wan3.0-video`）；
  - 权限查询（`/v1/models`）**本身失败** → **降级放行**，不误杀合法调用，交给服务端最终裁判。

### A.18【新增 · 低成本方案】PT 官网 docs 可无 key 爬取，拿到「全局模型真相」
- **必要性**：A.17 要求下架判定看公开层，但 PT 没有无 key 的模型目录 API —— 于是爬官网 docs。
- **实测数据源（2026-10-30 验证可用）**：
  - `https://docs.powertokens.ai/sitemap.xml` **直出 396 个 `<loc>`**，其中 **350 个模型页**（Video 180 / Image 66 / Audio 24 / Chat 80；无子 sitemap）；en + zh-Hans 双语去重后 **181 个唯一页**；
  - 模型页 URL 规律：`docs.powertokens.ai/{lang}/zmodel{Type}/{vendor}/{slug}`；
  - 每页配套 **OpenAPI 3.0.1 片段**，URL = 模型页 path 前插 `/api-reference` + 后缀 `.json`
    （例：`/api-reference/en/zmodelVideo/ali/wan3.0-video-generation.json`）。⚠️ `/en/...json` 与 `/openapi/...json` 均 **404**，只有 `/api-reference/{lang}/...json` 这一条路。
  - 模型 ID 在 `components.schemas[*].properties.model.enum`（一页可含多个）；**同步/异步**看有没有 `GET /tasks/{id}` 轮询路径。
  - ⚠️ **Chat 类页面 JSON 带 UTF-8 BOM** → 必须 `utf-8-sig` 解码，否则 `json.loads` 直接抛错。
- **产出**：`catalog.public.json` —— 82 个模型（video 20 / seedance 7 / image 9 / audio 8 / chat 38；异步 28 / 同步 54）。10 个 404 全是 zh-Hans 的 minimax 镜像页（其 en 版已抓到，**不丢模型**）；任务查询/取消等「帮助页」无 model enum，应正确跳过。
- **教训**：没有目录 API 时，**官网 sitemap + 文档页内嵌 OpenAPI** 是最低成本的「全局真相」来源；但要处理 BOM、双语去重、辅助页跳过三类噪声。

### A.19【新增 · 待实测】音频 TTS 请求体字段仍未固化（同步接口别靠「兼容协议猜」）
- **现状**：端点已确认（Ali 兼容 `POST /ali/.../multimodal-generation/generation`），但从 OpenAPI 抽到的参数是 `input/text`、`voice`、`language_type`，而当前 `audio_generate()` 发的是 `input.prompt`。
- **未决**：到底是 `text` 还是 `prompt`？响应 url 落在 `output.audio.url` / `output.url` / 顶层 `url` 的哪一处？**必须拿真 key 跑一次才能固化**（当前代码是多位置兜底抽 url + 保留 `raw` 便于核对）。
- **教训**：同步接口的字段名不能用「它兼容某某协议」来推断 —— 兼容实现常在字段上有出入，一次真实调用就能固化，成本远低于反复猜。

### B 层补充：A.02 / A.03 的轮询熔断**已在 pt-gateway 落地**（2026-10-30 更正）
- `pt-gateway/pt_gateway.py` 重写了 `poll_task`（覆盖引擎原版）：429 尊重 `Retry-After`、否则指数退避（≤60s）；5xx **连续 5 次熔断**抛出；其他 4xx 直接透传；超时明确报错。即 A.02 / A.03 的「路线图」在此子模块已实现。
- **2026-11-02 更正**：上面「429 尊重 `Retry-After`」在 2026-11-02 之前**实际是失效的**——引擎 `ApiError` 不透出响应头，`_retry_after()` 恒返回 `None`，所谓「尊重」从来没发生过。已修，见 **A.20**。典型形态：上层写对了、下层没给数据、文档却宣布已落地。
- ⚠️ **别混谈**：这是 **pt-gateway** 的实现；引擎 `pt_video.py`（0.2.0）的 `poll_task` 仍是「仅超时抛错、无熔断」（见 A.02 表格第 12 行）。在文档/对话里必须指明是哪个模块。

### C.24【新增】Bash 工具会**整体失效**，不是 PATH 损坏
- **现象**：某轮开始所有 Bash 命令都报 `Parameter "command" expected string, but received undefined`——连 `echo hi` 都被拒（区别于 C.16 的 PATH 损坏，那条还能跑绝对路径）。
- **解法**：**立刻改用 PowerShell 工具**，不要反复重试 Bash。PowerShell 查 git / 跑 Python 均正常（本轮全部 git 操作与脚本执行都靠它完成）。

### C.25【新增】PowerShell `>` 重定向写的是 **UTF-16**，Read 会报 binary
- **现象**：`python x.py > out.txt 2>&1` 产出的文件，用 Read 读报 `Cannot display content of binary file`。
- **解法**：二选一 —— ① `cmd | Out-File -FilePath out.txt -Encoding utf8`；② 更稳：让 Python 脚本**自己** `open(..., 'w', encoding='utf-8')` 写结果文件，再 Read。（本轮统一用 ②，脚本顺带把锚点命中数/错误一起写进去。）

### C.26【新增 · 致命】补丁脚本用 `\uXXXX` 转义中文 → 笔误 → **静默不落盘**
- **现象**：给 `pt_gateway.py` 打补丁，`assert s.count(old)==1` 其实失败了，但 PowerShell 吞掉报错（C.17），表现为「脚本跑了、代码没改」，反复两轮才发现。
- **根因**：锚点里的中文用 `\uXXXX` 手写极易笔误——把「兜」U+514C 写成了「兆」U+5146，一个字符错导致整段锚点不匹配。
- **解法**：① 锚点**只用 ASCII**（如 `    def auto_select(self, kind):`），中文一律用**字面量**写在替换串里；② 脚本**无论如何**都把各锚点命中数与异常写到 OUT 文件，跑完 Read 核对 `count=1`；③ 命中数不对就**不落盘**（避免半改状态）。
- **定位**：这是 C.15（并行 Edit 静默丢写）的进阶版——同样的「报成功但没生效」，诱因从并发改成了锚点笔误。

### C.27【新增】Edit 工具遇到**花括号**会假成功
- **现象**：`old_string` / `new_string` 含 `{...}`（如 `{supported:false}`、`{id}`、`{kind}`）时，Edit 可能返回 "Successfully edited" 但**内容根本没落盘**；用 Grep/Read 复核才发现。
- **解法**：含花括号（或长中文易漏改）的改动，一律改用 Python 脚本：`s.count(old)==1` 断言 + `s.replace` 精确写回，写完用 Read 复核。本轮所有含 `{...}` 的改动都走这条。

### F.39【新增】讨论中产生的修正项必须**落到文档**，别留在对话里
- **用户明确要求（2026-10-30）**：① 把之前**说错**的地方纠正（代码 + catalog + server/mcp + README 一起对齐）；② 把**要修改的地方记录成一个文档**；③ 先写爬虫，**写完再回来逐条讨论**文档里待修正的项。
- **落地**：建 `pt-gateway/FIXLIST.md`，分三段——「已纠正（含三处硬性错误的证据/结论）」「待讨论（逐项标 `[已定]`/`[待讨论]`）」「实施记录（用户拍板后逐条落地情况）」。
- **教训**：① 讨论里冒出的修正项散落在对话中会**丢失、无法回溯**，必须落文件；② 「纠正 → 记录 → 实现 → 回来讨论」要**分阶段**，每段有交付物；③ 已定/已实施项要在文档里**标状态**，否则下次会重复讨论同一件事。

## 校正说明（本版 vs 旧版 powertokens-wan 档案）

| # | 旧版问题 | 本版校正 |
|---|---|---|
| 1 | **漏坑**：完全没提 `seconds` / `size` / `ratio` / `model` 须传字符串 | 新增 **A.11**，记录我们这轮真实报的 `cannot unmarshal number into ... .seconds of type string` |
| 2 | A.01 称「网关实际返回 `completed`」 | 我们实测返回 `succeeded`，更正为「写法不一，多路兜底」 |
| 3 | 项目名 `powertokens-wan`、版本 0.1.2、26 单测、4 出片、`pt_wan.py`、`~/.config/powertokens-wan/` | 对齐 **powertokens-video 0.2.0**、0 单测、1 出片、`pt_video.py`、`~/.config/powertokens-video/` |
| 4 | A.05 把「无效 Key 返 500+token_invalid」当已确认事实；A.06「85 个模型」未注口径 | A.05 降为**待实测**；A.06 改为我们实测 **32 个视频模型**并注明口径 |
| 5 | 把 `classify_rest` / `rest_poll` 三重熔断 / `fetch_model_ids` / `ensure_model_allowed` / `status` 子命令写成「已落地」 | 0.2.0 实际只有简单轮询 + `precheck_explicit_model`；上述高级能力改为**已知短板 / 路线图**，不谎称已实现 |
| 6 | 旧版仅覆盖视频（0.2.0） | 0.3.0 扩展为**图文通用**：图片端点 `POST /v1/images/generations` 同步（A.12）、Seedance Asset Library 协议 + BytePlus media 前置（A.13）；真实出片新增图片 1 次（image-01） |
| 7 | 未覆盖 pt-gateway 子模块；B 层称「轮询熔断未实现」 | 2026-10-30 新增 **A.15–A.19**（端点分层的两次反向误判 / PT 无取消接口 / `/v1/models` 只是权限视图 + fail-closed 门禁 / 无 key 爬 docs 建全局真相 / 音频字段待实测）、**C.24–C.27**、**F.39**；并更正 B 层：熔断**已在 pt-gateway 落地**，引擎 `pt_video.py` 仍为旧实现 |
| 8 | 未覆盖 chat（38 个模型）的能力标注与探测；把 429 `Retry-After` 写成「已落地」 | 2026-11-02 新增 **A.20–A.23**（Retry-After 整链断裂并修复 / 下架模型的 403 形态 / chat 能力三重条件 / 多模态探针假阴性）、**E.40**、**H.4**；并更正 B 层「已落地」实为失效 |

---

## H. 真机验收实测（2026-10-30，真 key 实跑）

### H.1 音频端点的第三次误判：真实是 OpenAI 兼容 `/v1/audio/speech`
现象：`audio_generate` 按「Ali 兼容」假设请求 `/ali/api/v1/services/aigc/multimodal-generation/generation`，
`speech-2.8-turbo` 返回 500 `pricing rule not found`；换 `qwen3-tts-instruct-flash` 返回 404 `Model not exist`。
证据：`POST /v1/audio/speech` 带非法 voice 时返回 `400 voice is required`（而非 404 Invalid URL）→ 端点真实存在。
结论（实测）：
- 端点 `POST /v1/audio/speech`；请求体 `{model, input, voice}`；**`voice` 必填**。
- 音色用 OpenAI 标准名 `alloy/echo/fable/onyx/nova/shimmer`，实测 6 个全部 200。
- 响应 `{data:{audio:<mp3 url>, status:2}}`，url 在 `data.audio`（不是 `output.audio.url`）。
教训：端点形态要靠**错误码区分**判定——404 `Invalid URL` = 路径不存在；4xx/5xx 业务错 = 路径存在但参数/计价有问题。

### H.2 `/v1/models` 里列出的模型 ≠ 都能用
实测：8 个 audio 模型中，`qwen3-tts-instruct-flash` 404 不存在，`audio1.0` / `speech-2.6-hd` 报「倍率或价格未配置」。
结论：模型清单只是「目录」，可用性还取决于平台侧是否配了计价规则。网关不做客户端拦截是对的——
让服务端报错即可，别在客户端维护一份必然过期的「可用清单」。

### H.3 真机验收的成本量级（供后续回归参考）
一次全链路（模型清单 + chat + 图像 + TTS + 视频 720p 5s）约 **$0.54**，其中视频占 92%。
视频生成耗时约 145s；图像约 22s；TTS 约 3s；chat 约 1s。
回归测试省钱法：视频换成 Seedance Mini 480p（$0.031/s）可把整轮压到约 $0.20。

### H.4 分层真机验收的成本实测（2026-11-02，为「要不要再跑一遍」提供判据）

| 档位 | 覆盖内容 | 实测花费 |
|---|---|---|
| minimal | 每类能力各打一发，验证链路通 | **$0.199** |
| standard | 主要模型族 + 错误路径 | **$0.544** |
| full | 全模型遍历（38 chat + 全 image/audio/video） | **$1.044** |
| 累计 | 含一次单独验证 | **≈ $1.79** |

- **full 首跑就撞上已下架模型**（A.21）——印证「模型清单会腐化」，并直接催生了离线守卫。
- **省钱判据**：① 只改**数据**（删条目、改能力标注）→ 跑离线即可，0 成本、1 秒；② 改**引擎 / 错误路径** → 造假 429 比真机更确定，因为真机根本造不出 429；③ 只有发版前或新增模型时才值得跑真机全量。
- 视频仍占成本 90%+（同 H.3），缩短视频时长 / 分辨率比砍用例更划算。

*踩坑档案 · 校正于 2026-10-26 · 2026-10-30 追加 pt-gateway 段落 · 2026-11-02 追加 chat 能力 / 429 链路 / 预签名 URL · 阿伴 🦝*
*诊断工具留存：`pt_video.py`（引擎）· `mcp_server.py`（MCP 壳）· `README.md`（安装）· `docs/publish.md`（发布）*
