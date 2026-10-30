# PT Gateway —— PT 统一媒体能力层（核心库 + HTTP 服务 + MCP 薄壳）

把 PowerTokens 的**全部生成能力**（image / video / seedance / audio / chat，含每个模型的能力档案）
收敛成一套统一接口：agent 调 `list_capabilities` 就能拿到全部模型能力，**不用再翻文档查**；
调 `media_generate` 一个入口即可生成，网关按 kind 自动路由（image / audio / chat 同步直返，
video / seedance 异步拿 task_id 后轮询）。

> 两个已确认的平台事实（2026-10-30）：
> 1. `GET /v1/models` 返回 PT **全量**模型清单（所有 key 相同），不是「本 key 权限视图」；
>    因此网关不做权限拦截，本 key 无权限时由 PT 服务端报错并透传。
> 2. PT **不提供**异步任务取消接口，故本网关不实现取消（HTTP 无 `/cancel`，MCP 不暴露取消工具）。

> 关键前提纠偏：PT 底层是 OneAPI 透明路由，**所有视频/Seedance 模型本来就共用**
> `POST /v1/videos` + `GET /v1/videos/{id}`，并不存在「各家独立接口」。本服务归一化的是
> **请求体构造差异**与**返回字段位置差异**，并补上**能力目录**与**模型自动发现/上下架同步**。

---

## 1. 架构

```
调用方 (任意 agent / 网页 / 脚本)
        │  POST /generate │ POST /submit │ GET /status │ POST /wait │ POST /sync │ GET /models │ GET /capabilities
        ▼
┌──────────────────────── pt-gateway (stdlib http.server, 零依赖) ────────────────────────┐
│  TaskGateway                                                                                    │
│   ├─ submit(model,...)  → build_submit_body(kind 适配) → POST /v1/videos → {task_id}       │
│   ├─ get_status(tid)    → GET /v1/videos/{id} → normalize_status()                          │
│   ├─ wait(tid,timeout)  → 复用引擎 poll_task 轮询到终态 → 归一化                            │
│   └─ chat/audio        → 同步直返（POST /v1/chat/completions、POST /v1/audio/speech）        │
│  ModelRegistry (registry.json)                                                                  │
│   └─ sync(key): GET /v1/models（PT 全量清单）→ diff 出上下架；不做权限拦截                   │
└───────────────────────────────┬──────────────────────────────────────────────────────────┘
                                 │  Bearer <POWERTOKENS_API_KEY>
                                 ▼
                   api.powertokens.ai  (/v1/videos, /v1/models)
```

`pt_gateway.py` 复用同仓 `pt_video.py` 的 REST 原语（`_api_call` / 端点常量 / 模型识别），
**不依赖 CLI 子命令**——它是独立模块，只是借用了已验证的传输层实现。

---

## 2. 接口契约

服务地址：`http://<host>:8787`（端口取 `PT_GATEWAY_PORT`，默认 8787）。Key 走
`POWERTOKENS_API_KEY` 环境变量或 `~/.config/powertokens-video/config.json`（与 CLI 共用）。

### GET /health
```json
{ "ok": true, "service": "pt-gateway" }
```

### GET /capabilities?kind=<image|video|seedance|audio|chat>
能力目录（agent 查这个，代替翻文档）：`kinds`（各类型执行方式/端点/前置）+ `models`
（每模型：kind、价格、时长上限、分辨率、输入模式），并叠加注册表 `registry_enabled` / `delisted` 实时状态。
```json
{
  "kinds": {
    "video": { "execution": "async", "submit": "/v1/videos", "poll": "/v1/videos/{task_id}" },
    "audio": { "execution": "sync", "submit": "/v1/audio/speech",
               "poll": null, "vendor": "OpenAI-compatible (MiniMax TTS)",
               "capabilities_observed": ["语音合成(TTS)", "音色选择(voice 必填)"] }
  },
  "models": {
    "seedance-1-5-pro-251215": { "kind": "seedance", "price_usd_5s_720p": 0.247,
                                 "max_duration_s": 10, "resolutions": ["720p","1080p"],
                                 "modes": ["text","image(first-frame)","start-end","video+audio-ref"],
                                 "registry_enabled": true, "delisted": false }
  }
}
```

### POST /generate（统一媒体生成入口）
请求体：
```json
{
  "kind": "video",          // 可选；model 具体给 ID 时可省略（自动反推）
  "model": "auto",          // auto=按 注册表优先→目录候选 自动选；或具体模型 ID
  "prompt": "一只猫在草地上走",
  "aspect_ratio": "16:9",   // 仅 image
  "n": 1,                   // 仅 image
  "duration": 5, "resolution": "720p", "ratio": "16:9",   // 仅 video/seedance
  "first_frame": "asset://<id>", "ref_image": "", "ref_video": "",  // 可选
  "wait": false, "timeout": 600
}
```
响应按 kind 分流：
- `image` → `{"ok":true,"kind":"image","execution":"sync","model":"...","urls":["..."]}`（同步直返）
- `video` / `seedance` → `{"task_id":"...","kind":"...","execution":"async","model":"..."}`（用 /status 查）
- `audio` → `{"ok":true,"kind":"audio","execution":"sync","voice":"alloy","url":"..."}`（同步生成，POST `/v1/audio/speech`；**`voice` 必填**）
- `chat` → `{"ok":true,"kind":"chat","execution":"sync","text":"..."}`（同步生成，POST /v1/chat/completions）

### POST /submit
请求体：
```json
{
  "model": "seedance-1-5-pro-251215",   // 必填；video/seedance 任一模型 ID
  "prompt": "一只猫在草地上走",           // 文生视频必填；图生视频也可留空
  "duration": 5,                          // 秒，2-30
  "resolution": "720p",                   // 480P/720P/1080P
  "ratio": "16:9",
  "first_frame": "asset://<id>",          // 可选，图生视频（Seedance 必须 asset://）
  "ref_image": "https://...",             // 可选
  "ref_video": "https://...",             // 可选
  "no_audio": false,
  "no_prompt_extend": false,
  "watermark": false,
  "seed": 42,                             // 可选
  "wait": false,                          // true 则轮询到终态再返回
  "timeout": 600                          // wait=true 时的轮询上限（秒）
}
```
响应（`wait=false`）：
```json
{ "task_id": "abc123", "kind": "seedance" }
```
响应（`wait=true`）：归一化状态（见 §3）。

### GET /status?task_id=<id>
```json
{ "task_id": "abc123", "state": "RUNNING", "progress": 30, "url": null, "raw": { ...厂商原文... } }
```

### POST /wait
```json
{ "task_id": "abc123", "timeout": 600 }
```
→ 归一化终态（成功带 `url`，失败带 `error`）。

### POST /sync
用当前 Key 拉 `GET /v1/models` 刷新注册表。
```json
{ "added": ["new-model-id"], "removed": ["delisted-model-id"], "total_enabled": 9, "last_synced": "2026-10-29T..." }
```

### GET /models?kind=video|seedance
返回注册表里 enabled 的异步模型列表（调用时惰性自动同步）。
```json
{ "kind": "seedance", "count": 3, "models": ["..."], "last_synced": "..." }
```

### 取消（不提供）

PT 无异步任务取消接口（2026-10-30 与官方确认），本网关因此**不实现取消**：
HTTP 无 `/cancel` 端点，MCP 也不暴露取消工具。任务提交后只能等待其自行结束。

---

## 3. 归一化状态机

`normalize_status()` 把各厂商动词映射为统一枚举：

| 厂商原始 status | 归一化 |
|---|---|
| `succeeded` / `completed` | `SUCCEEDED` |
| `pending` / `queued` / `submitted` | `QUEUED` |
| `running` / `processing` / `in_progress` / `generating` | `RUNNING` |
| `failed` | `FAILED` |
| `cancelled` / `canceled` | `CANCELLED` |
| 其他 | `UNKNOWN` |

结果 URL 兼容 `metadata.url` / `metadata.content_url` / `task.url` 多处位置，统一放到 `url` 字段。

---

## 4. 模型注册表与自动更新机制

文件：`~/.config/pt-gateway/registry.json`（可用 `PT_GATEWAY_REGISTRY` 覆盖；已 gitignore）。

schema：
```json
{
  "last_synced": "2026-10-29T15:00:00+00:00",
  "models": {
    "seedance-1-5-pro-251215": {
      "kind": "seedance", "enabled": true, "delisted": false,
      "first_seen": "2026-10-20T...", "last_seen": "2026-10-29T...", "is_new": false
    }
  }
}
```

同步策略（`ModelRegistry.sync`）：

- **模型清单真相源**：`GET /v1/models` 返回 PT **全量**模型（所有 key 相同，2026-10-30 确认）。
  - 出现在列表 → `in_models=true`、`enabled=true`、`delisted=false`；新出现的打 `is_new=true`。
  - 不再出现 → `delisted=true`、`enabled=false`（即视为下架）。
- **不做权限拦截（fail-open）**：网关不在客户端判断「本 key 能否调某模型」。
  若本 key 对某模型无权限，由 **PT 服务端报错**，网关原样透传。
  （早期版本做过 fails-closed 门禁，前提是把 `/v1/models` 误当权限视图；已撤销。）
- **惰性自动重拉**：`auto_sync_if_stale()` 在注册表为空或超过 `max_age`（默认 3600s，
  对齐 PT 的 1 小时缓存）时，于 `/models`、`/generate` 等调用前 best-effort 重拉；失败不抛，
  回退到现有/内置列表。也可显式 `POST /sync` 强制刷新。

---

## 5. 启动

```bash
cd pt-gateway
python server.py                 # HTTP 服务，默认 8787
python mcp.py                    # MCP 薄壳（stdio；需 pip install "mcp[cli]>=1.2"）
PT_GATEWAY_PORT=9000 python server.py
```

MCP 客户端配置示例（把统一工具挂进 Claude/CodeBuddy/Trae）：
```json
{ "pt-gateway": { "command": "python", "args": ["<仓库路径>/pt-gateway/mcp.py"] } }
```
3 个统一工具：`list_capabilities`（查能力，代替翻文档）/ `media_generate`（统一生成入口）/
`task_status`（查状态，wait=true 可等到终态）。MCP 与 HTTP 共用同一份 registry.json /
catalog.json，看到的模型目录永远一致。

调用示例（curl）：
```bash
curl http://localhost:8787/health
curl -X POST http://localhost:8787/submit -H 'Content-Type: application/json' \
  -d '{"model":"seedance-1-5-pro-251215","prompt":"一只猫在草地上走","wait":false}'
curl "http://localhost:8787/status?task_id=abc123"
curl -X POST http://localhost:8787/sync
```

作为 Python 库直接调用：
```python
from pt_gateway import TaskGateway, ModelRegistry
gw = TaskGateway()                      # Key 自动从环境变量/config 取
tid = gw.submit("wan3.0-video", prompt="a cat", wait=False)["task_id"]
print(gw.wait(tid))
```

---

## 6. 与现有 CLI 的关系

`pt_video.py`（CLI + MCP）继续负责**直接生成**（含图片同步生成、Seedance 协议引导、下载等）。
`pt-gateway` 是**独立部署的任务中枢**：把「提交/查状态/轮询/模型目录」抽成服务，适合给多个 agent
或前端统一调用，避免每个接入方重复实现轮询与字段归一化。

## 7. 已知缺口（本轮）

- **不提供取消**：PT 无异步任务取消接口（已与官方确认），故本网关不实现 cancel——HTTP 无 `/cancel`、
  MCP 不暴露取消工具。异步任务提交后只能等待其自行结束。
- **轮询健壮性**：网关侧已覆盖 `poll_task`（429 指数退避 + 5xx 连续 5 次熔断 + 4xx 透传 + 超时报错）；
  引擎 `pt_video.py` 仍是旧实现（10s 间隔 + 600s 上限），勿混谈。

---

## 8. 手动触发开关 + 定时同步（自动更新不产生额外负担）

注册表的「自动发现新上架 / 自动下线已下架」由 §4 的 diff 兜底。**但它只在有人调用时才惰性重拉**；
若服务长期空闲，注册表不会自己变新鲜。因此提供两种显式触发方式：

### 8.1 手动触发开关（两种方式，任选）

1. **终端直跑（不用起服务）** —— `pt_gateway.py` 自带 `sync` 子命令：
   ```bash
   python pt_gateway.py sync        # 拉 /v1/models 刷新 registry.json，打印 {added,removed,total_enabled,last_synced}
   python pt_gateway.py selfcheck   # 状态归一化自检（默认，无需 Key）
   ```
   这条命令和 HTTP 服务**写入同一个** `~/.config/pt-gateway/registry.json`，所以手动刷新后服务立即可见。

2. **服务内热刷新** —— 对运行中的网关发：
   ```bash
   curl -X POST http://localhost:8787/sync
   ```

### 8.2 定时同步（推荐 12:00 + 19:00，零资源负担）

同步动作本身只是一次 `GET /v1/models`（返回几 KB JSON），服务空闲时 CPU≈0，
**每日两次完全无感**。已用 WorkBuddy 自动化建好每日 12:00 / 19:00 的定时任务，本质是定时执行：

```
C:/Users/admin/.workbuddy/binaries/python/versions/3.13.12/python.exe ^
  c:/Users/admin/WorkBuddy/2026-09-28-11-17-14/powertokens-video/pt-gateway/pt_gateway.py sync
```

- 若想改成**每小时**一次（同样免费），把自动化 rrule 改成 `FREQ=HOURLY;INTERVAL=1` 即可。
- 前提：定时任务执行时需 `POWERTOKENS_API_KEY` 在环境变量中可用（与网关取 Key 方式一致）。
  若自动化环境未带该变量，定时刷新会跳过——此时用 8.1 的手动开关在带 Key 的终端里跑即可。

> 设计取舍：PT 不提供 webhook，故采用「pull 模式」——惰性重拉（调用时 ≤1h 过期即刷）+
> 每日两次主动定时 + 手动开关三者互补，覆盖「调用中实时新鲜」与「空闲时也不陈旧」。

---

## 9. 统一媒体能力目录（catalog.json）——agent「不用查」的关键

`catalog.json`（随仓库分发，可用 `PT_GATEWAY_CATALOG` 覆盖路径）是**人工观测固化的能力档案**：
PT 的 `GET /v1/models` 只返回模型 ID，**不给**分辨率/时长/模式/单价这些字段——本目录把它们
结构化存下来，`list_capabilities` 一次给全，agent 从此不再翻文档。

**分工（和 ModelRegistry 的关系）**：

| | 谁维护 | 管什么 |
|---|---|---|
| ModelRegistry（registry.json） | **自动**：sync() 拉 /v1/models 做 diff | 上下架（enabled/delisted）、is_new |
| Catalog（catalog.json） | **人工**：实测/截图观测后补一条 | 能力字段（execution/分辨率/时长/模式/单价/前置） |

两者在 `list_capabilities` 里**合并输出**：目录给「能干什么」，注册表给「现在还上不上架」。
目录没收录的新上架模型，注册表也会自动 enable（能力字段显示缺失，诚实不编造）。

schema：顶层 `kinds`（每类型的 execution/端点/前置要求/能力观测）+ `models`（每模型档案）。
音频类（TTS）端点：**`POST /v1/audio/speech`（OpenAI 兼容，2026-10-30 真机实测）**——
此前假设的 `/ali/...` 是错的（实测 404 / 500 `pricing rule not found`）。
请求体 `{model, input, voice}`；**`voice` 必填**（缺则报 `voice is required`），
可用 OpenAI 标准音色 `alloy / echo / fable / onyx / nova / shimmer`（实测全部 200）。
响应 `{data:{audio:<mp3 url>, status:2}}`，url 在 **`data.audio`**；附 `extra_info`（时长/采样率/体积/码率）。
文本类（chat）端点：`POST /v1/chat/completions`（OpenAI 兼容，同步返回 `choices[0].message.content`），
`kinds.chat` 已填真实端点，`media_generate(kind=chat)` 同步返回 `{execution:"sync", text}`；
可用 `messages` 传多轮对话，或只用 `prompt` 作单轮 user 消息（`system` 前置）。

CLI 快查：
```bash
python pt_gateway.py capabilities            # 打印全部能力目录
python pt_gateway.py capabilities seedance   # 只看某类
```

---
