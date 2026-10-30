# PT Gateway —— PT 统一媒体能力层（核心库 + HTTP 服务 + MCP 薄壳）

把 PowerTokens 的**全部媒体生成能力**（image / video / seedance / audio，含每个模型的能力档案）
收敛成一套统一接口：agent 调 `list_capabilities` 就能拿到全部模型能力，**不用再翻文档查**；
调 `media_generate` 一个入口即可生成，网关按 kind 自动路由（image 同步直返、video/seedance
异步拿 task_id、audio 诚实 unsupported）。

> 关键前提纠偏：PT 底层是 OneAPI 透明路由，**所有视频/Seedance 模型本来就共用**
> `POST /v1/videos` + `GET /v1/videos/{id}`，并不存在「各家独立接口」。本服务归一化的是
> **请求体构造差异**与**返回字段位置差异**，并补上**能力目录**与**模型自动发现/上下架同步**。

---

## 1. 架构

```
调用方 (任意 agent / 网页 / 脚本)
        │  POST /submit │ GET /status │ POST /wait │ POST /sync │ GET /models │ POST /cancel
        ▼
┌──────────────────────── pt-gateway (stdlib http.server, 零依赖) ────────────────────────┐
│  TaskGateway                                                                             │
│   ├─ submit(model,...)  → build_submit_body(kind 适配) → POST /v1/videos → {task_id}      │
│   ├─ get_status(tid)    → GET /v1/videos/{id} → normalize_status()                       │
│   ├─ wait(tid,timeout)  → 复用引擎 poll_task 轮询到终态 → 归一化                          │
│   └─ cancel(tid)        → Seedance 真实 DELETE /byteplus/...；其他诚实 {supported:false}                     │
│  ModelRegistry (registry.json)                                                           │
│   └─ sync(key): GET /v1/models → diff → 新上架 enable(is_new) / 下架 delisted            │
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

### GET /capabilities?kind=<image|video|seedance|audio>
能力目录（agent 查这个，代替翻文档）：`kinds`（各类型执行方式/端点/前置）+ `models`
（每模型：kind、价格、时长上限、分辨率、输入模式），并叠加注册表 `registry_enabled` / `delisted` 实时状态。
```json
{
  "kinds": {
    "video": { "execution": "async", "submit": "/v1/videos", "poll": "/v1/videos/{task_id}" },
    "audio": { "execution": "sync", "submit": "/ali/api/v1/services/aigc/multimodal-generation/generation",
               "poll": null, "vendor": "Ali (DashScope compatible)",
               "capabilities_observed": ["语音合成(TTS, 短/长文本)", "音色设置"] }
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
- `audio` → `{"ok":true,"kind":"audio","execution":"sync","url":"..."}`（同步生成，POST /ali/.../multimodal-generation/generation）

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

### POST /cancel
```json
{ "task_id": "abc123", "supported": true, "kind": "seedance", "state": "CANCELLED" }
```

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
- **新上架**：出现在 live `/v1/models` 但不在注册表 → 新增 `enabled=true`，打 `is_new=true`。
- **下架**：曾在注册表但不再出现在 live → `enabled=false`，`delisted=true`（保留历史，不物理删除）。
- **惰性自动重拉**：`auto_sync_if_stale()` 在注册表为空或超过 `max_age`（默认 3600s，
  对齐 PT 的 1 小时缓存）时，于 `/models`、`/submit` 等调用前 best-effort 重拉；失败不抛，回退到
  现有/内置列表。也可显式 `POST /sync` 强制刷新。

> 这样「自动获取最新上架的异步模型 + 禁用下架的」就由注册表 diff 兜底，调用方无感。

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

- **cancel 已实现 Seedance 真实取消**：Seedance 走 `DELETE /byteplus/api/v3/contents/generations/tasks/{id}`
  （queued→cancelled），`/cancel` 真实调用并返回 `{supported:true, state:CANCELLED}`；其他模型（video 等）
  vendor 取消端点待确认，`/cancel` 诚实返回 `{supported:false}` 并说明已知端点。任务不再"无法中途取消"（限 Seedance）。
- 轮询仍沿用引擎的 10s 间隔 + 600s 上限（无 429 退避/5xx 熔断，见 `pt_video.py` 注释 A.02/A.03）。

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
音频类（TTS）端点已确认：`POST /ali/api/v1/services/aigc/multimodal-generation/generation`（Ali 兼容，同步返回 url），
`kinds.audio` 已填真实端点，`media_generate(kind=audio)` 现在同步生成并返回 `{execution:"sync", url}`；
请求体采用 Ali 标准结构（model + input.prompt[+voice]），响应 url 多位置兜底抽取，具体字段以 PT 文档实测为准。

CLI 快查：
```bash
python pt_gateway.py capabilities            # 打印全部能力目录
python pt_gateway.py capabilities seedance   # 只看某类
```

---

## 公开层爬虫（catalog_crawler.py）

无 Key 爬 PT 官网，产出「公开层全局模型真相」`catalog.public.json`，消除多 key 误判（详见 FIXLIST.md §2.1）。

数据源（已实测）：
- `https://docs.powertokens.ai/sitemap.xml`（396 个 `<loc>`，含 350 个模型页，无子 sitemap）
- 模型页 `https://docs.powertokens.ai/{lang}/zmodel{Type}/{vendor}/{rest}`
- OpenAPI 3.0.1 片段 `https://docs.powertokens.ai/api-reference/{lang}/zmodel{Type}/{vendor}/{rest}.json`
  （模型页 path 前插 `/api-reference`、后缀加 `.json` 即得；含 title / paths / `model` enum / 参数 schema / 响应 status enum）

产出 `catalog.public.json` 每模型字段：
`kind` / `type` / `vendor` / `title` / `execution`(同步·异步) / `vendor_endpoint`(真实 vendor 路径)
/ `submit`(按 kind 派生的统一端点) / `params_observed` / `status_enum` / `first_seen` / `last_seen`。
其中 kind / execution / 端点 是爬虫可证字段；`price` / `resolution` / `modes` 等 PT 不给的字段由人工档案 `catalog.json` 维护。

用法：
```bash
python catalog_crawler.py                 # 完整爬 → 写 catalog.public.json
python catalog_crawler.py --dry-run       # 爬但不写文件（打印摘要）
python catalog_crawler.py --limit 30      # 只处理前 30 个模型页（调试）
python catalog_crawler.py --merge         # 爬 + 合并进 catalog.json（默认关闭，待 FIXLIST §2.2 讨论）
```

说明：爬虫默认只写 `catalog.public.json`，**绝不碰 `registry.json`**；下架判定（本次爬未出现的模型 → `delisted`）
从 key 层移到公开层。`--merge` 仅把爬虫可证字段并入 `catalog.json.models`，人工档案字段不被覆盖；
合并策略与多 key 分文件方案为待讨论项（FIXLIST §2.1 / §2.2）。
