---
name: powertokens-kit
description: >-
  Generate images AND videos from PowerTokens models via the official REST API.
  Images: POST /v1/images/generations (synchronous, returns data[].url, no polling).
  Videos: POST /v1/videos then poll GET /v1/videos/{id} every 10s until the URL is ready.
  Pure Python stdlib, zero external deps. Covers Seedance / Kling / Hailuo / Wan / Vidu / HappyHorse
  (video) and Seedream / Qwen-Image / Image-01 / Kling-Image / Wan-Image (image). Use when the user
  wants to "generate a video or image with PowerTokens / make a video / make an image / use a specific
  model (e.g. kling-v2-1-master or seedream-5-0)". The agent MUST first ensure a PowerTokens API Key is
  configured; if missing, show the key-acquisition guide and stop. Default model is auto (picks from the
  user's available models); the user may name any model. The agent can list every model with its pricing
  and supported parameters via `models`. Three content types: video, image, and Seedance (a distinct type
  for the full ByteDance Seedance line: 1.0/1.5/2.0/2.5, incl. Mini/Fast variants). IMPORTANT: Seedance (all
  versions) requires signing the Asset Library User Agreement in
  the dashboard (no API endpoint) before use and uses `--type seedance`. Text-to-video works with a plain
  prompt (the engine wraps it in media:[{type:text,...}]); image-to-video needs assets uploaded to the Asset
  Library first, passed as asset:// URLs (http(s) is rejected for image-to-video).
  The same repository also ships a unified gateway (`pt-gateway/`) that adds audio (TTS) and chat
  models (with per-model vision / web_search / thinking annotations); this skill covers the
  image / video / Seedance engine itself.
license: MIT
compatibility: >-
  Requires Python 3.9+ only (standard library, no Node.js / requests / powertokens-cli needed).
  Key is read from the POWERTOKENS_API_KEY env var or the local config file. Works in any agent
  that can run a shell command (Claude Code, Cursor, Trae, VS Code Copilot, Windsurf, Cline,
  Zed, Codex, …) and as an MCP server for any MCP client.
metadata:
  author: powertokens-kit contributors
  version: "0.5.0"
  homepage: https://github.com/tjj1234/powertokens-kit
---

# PowerTokens 图片 / 视频 / Seedance Skill（powertokens-kit 的 Skill 入口）

直连官方 REST API 调用 PowerTokens 的全部图片与视频模型。入口脚本 `pt_video.py`（纯 Python 标准库，
零外部依赖）：

- **视频**：提交 `POST /v1/videos` 后自动每 10 秒轮询 `GET /v1/videos/{task_id}`，直到拿到视频链接。
- **图片**：提交 `POST /v1/images/generations`（OpenAI 风格，带 `/generations` 后缀），**同步返回**
  `data[].url`，**不轮询**，可一次返回多张。

含 auto 模型解析（视频/图片分开）、显式模型预校验、定价与参数查询、可选本地下载。

官方文档：
- 视频提交：https://docs.powertokens.ai/zh-Hans/zmodelVideo/ali/wan3.0-video-generation
- 视频查询：https://docs.powertokens.ai/zh-Hans/zmodelVideo/minimax/query-video-status
- 图片（image-01）：https://docs.powertokens.ai/zh-Hans/zmodelImage/minimax/image01-image-to-image
- Seedance 协议：https://docs.powertokens.ai/assets-library?tab=1 （仪表盘手动签 Asset Library User Agreement）

## 🟢 上手引导（首次触发必输出，展示型不阻塞）

当本 skill 首次被触发时，**先输出下面的 onboarding 三段**（不要先反问用户「key 配好了吗」）。
输出后**紧接着正常干活**（唯一例外：用户没配 Key，才停下等 Key）。

```
欢迎使用 PowerTokens 图片+视频生成 ✨ 上手前 3 件事：

1️⃣ 配置 Key（一次即可，本地保存，不上传）
   获取方式：打开 https://powertokens.ai/zh-Hans/dashboard
   → 注册/登录 → 充值 → 创建 Key（形如 sk-pt-xxxx）
   → 设环境变量 POWERTOKENS_API_KEY，或跑：
     python pt_video.py config --set-key "sk-pt-xxxx"

2️⃣ 指定模型：提示词里直接写模型名即可
   - 视频：「用 kling-v2-1-master 生成一只猫」（不写则自动选视频模型 auto）
   - 图片：「用 seedream-5-0 生成赛博城市」+ 加 --type image（不写则自动选图片模型）
   - Seedance：「用 seedance-1-5-pro 生成一段舞蹈」+ 加 --type seedance（**独立类型**，普通视频不含它）
   ⚠️ Seedance（全系 1.0/1.5/2.0/2.5）需先在仪表盘签《Asset Library User Agreement》；文生视频纯 prompt 即可，图生视频素材以 asset:// 传入
   （签好跑 `python pt_video.py seedance enable` 标记，之后不再弹开通引导）

3️⃣ 查看所有模型与定价：跑 python pt_video.py models --json
   （或输入「获取所有模型」）—— 列出每个模型的单价与支持的参数（video / image / seedance 三类）
```

## 触发与执行流程

1. **每次收到生成请求，第一步先跑体检**：
   ```bash
   python pt_video.py check
   ```
   - 退出码 `2` = 缺 Key → 立刻停下，向用户展示上面的 onboarding 第 1 段 + Key 获取链接，**不要继续生成**。
   - 退出码 `0` = 就绪 → 继续。

2. **用户没指定模型时，默认 auto**（不要替用户固定某个模型）：
   ```bash
   # 视频（默认）
   python pt_video.py generate -p "一只猫在蒙古草原跳舞" -d 5 -r 720p
   # 图片（加 --type image）
   python pt_video.py generate -p "赛博城市夜景" --type image --aspect-ratio 16:9
   # Seedance（独立类型：--type seedance；文生视频纯 prompt 即可，图生视频才需 --first-frame asset://xxxx）
   python pt_video.py generate --type seedance -m seedance-1-5-pro-251215 -p "一段舞蹈" -d 5 -r 720p
   # 图生视频：先把本地图上传拿 asset_id（免费）
   python pt_video.py upload -f /path/to/img.png
   python pt_video.py generate --type seedance -m seedance-1-5-pro-251215 -p "猫在跑" --first-frame asset://<asset_id> -d 5 -r 720p
   ```
   auto 会按用户账号真实可用的模型自动挑选并按优先级尝试；若某个模型不可用，自动换下一个。
   **注意**：普通 `video` 的 auto 候选已不含 Seedance；Seedance 只能走 `--type seedance` 显式使用。

3. **用户指定了模型名时，原样透传 `-m`**（脚本会先与 live 列表预校验，不在列表则秒退、不扣费）：
   ```bash
   python pt_video.py generate -m kling-v2-1-master -p "一只猫在蒙古草原跳舞" -d 5 -r 1080p
   python pt_video.py generate -m seedream-5-0-260128 -p "赛博城市" --type image --aspect-ratio 16:9
   ```
   脚本按模型 ID 自动判断走视频还是图片流程（含 `image`/`seedream` 的是图片，其余视频）。

4. **用户想看模型/定价/参数时，跑**：
   ```bash
   python pt_video.py models -t video --json   # 视频模型 + 单价 + 参数
   python pt_video.py models -t image --json   # 图片模型 + 参数（单价待补充，标 n/a）
   python pt_video.py models -t seedance --json  # Seedance 全系模型 + 单价 + 协议提示
   python pt_video.py models -t all --json     # 全部
   python pt_video.py estimate -m seedance-1-5-pro-251215 -d 5 -r 720p   # 估算视频消耗
   ```

5. **生成成功后**，脚本会打印 `[params] 生成参数：…`、`[credits] 本次预估消耗：…` 与
   `[result] 链接：…`，把这几行原样转述给用户（模型 / 时长或比例 / 分辨率 / 预估美元消耗 / 链接）。

## 关键约束

- **禁止反问「是否已配置 Key」**：用 `check` 的退出码判断，缺 Key 就展示引导并停下。
- **auto 是默认**，除非用户在提示词或参数里显式给了模型名。
- **模型 ID 必须精确**（区分大小写、带日期后缀，如 `seedance-1-5-pro-251215`、`seedream-5-0-260128`）。
  用户给的近似名（如 `MiniMax-H3`）会被预校验拒绝并给出相似候选，**不要猜、不要硬凑**。
- **Seedance 全系（1.0/1.5/2.0/2.5）是独立类型**：它**不**属于普通 `video`，普通 `video` 的 auto 候选里已剔除 Seedance。
  使用它必须 `--type seedance`（显式），例如 `python pt_video.py generate --type seedance -m <id> ...`。
- **Seedance 协议开通流程（本地标记 + 报错回退）**：图生视频的素材须经资产库上传后以 `asset://<ASSET_ID>`
  传入，且使用前须在仪表盘签署《Asset Library User Agreement》（https://powertokens.ai/assets-library?tab=1，
  **无 API 可代签**）。skill 用本地标记判断要不要弹开通引导：
  - 未标记时，生成前会打印 4 步「跳转 PT 开通」引导（不阻断提交）；
  - 你签好后跑 `python pt_video.py seedance enable` 标记已签，之后不再弹；
  - 若提交仍被服务端报「未签」（关键词命中），skill 自动重置标记并重新弹引导（报错回退）。
  `python pt_video.py seedance status` 可查看当前标记。
- **Seedance 文生 vs 图生**：**文生视频（text-to-video）只需 prompt，不需要任何 media**——引擎会把 prompt
  包进 `media:[{type:text, text:...}]` 提交（实测纠正：旧假设「BytePlus 强制 media」源于旧 key 假 asset 报错的
  误判，已删除）。**图生视频**（首帧/参考图）才需 assets，且必须是 `asset://`（外部 http(s) 不收）。
- **资产上传**：本地图/视频先 `python pt_video.py upload -f <file>`（免费，不扣 credits）上传到资产库，
  返回 `asset://<asset_id>`；图生视频时用 `--first-frame asset://<asset_id>`。注意资产库要求图片宽 300–6000px。
- 价格基于 PT 预扣单价估算（视频系数：时长线性、1080p = 720p ×2；图片按张计费，单价待补充标 n/a），
  PT 额度按美元计；以 powertokens.ai 仪表盘为准。
- 视频生成是异步的：提交后脚本自动每 10 秒轮询，直到 `succeeded/completed`；轮询总上限默认 600s
  （可用 `--timeout` 调整）。真实出片约 3 分钟，请耐心等待，不要中途杀进程。
- 图片生成是同步的：提交后直接返回链接（通常几秒），无需轮询。

## 参数速查（generate）

| 参数 | 含义 | 默认 |
|---|---|---|
| `-p / --prompt` | 提示词（与媒体二选一/互补） | 必填 |
| `-m / --model` | 模型 ID 或 `auto` | `auto` |
| `--type` | `video` / `image` / `seedance` / `auto`（默认按模型 ID 推断） | `auto` |
| **视频** | | |
| `-d / --duration` | 时长（秒，2-30） | 5 |
| `-r / --resolution` | `480P` / `720P` / `1080P` | 720p |
| `--ratio` | 画面比例 adaptive/16:9/4:3/1:1/3:4/9:16 | adaptive |
| `--timeout` | 轮询总上限秒 | 600 |
| **图片** | | |
| `--aspect-ratio` | 宽高比 1:1/16:9/4:3/3:2/2:3/3:4/9:16/21:9 | 1:1 |
| `--n` | 生成张数（1-9） | 1 |
| `--response-format` | `url` / `base64` | url |
| **共用** | | |
| `--seed` | 随机种子 | — |
| `--first-frame` | 首帧图 URL（图生视频 / `asset://`） | — |
| `--last-frame` | 尾帧图 URL（首尾帧） | — |
| `--ref-image/--ref-video/--ref-audio` | 参考素材 URL（可重复） | — |
| `--no-audio` | 关闭输出音轨 | 开 |
| `--watermark` | 添加 AI 生成水印 | 关 |
| `-o / --output` | 下载保存路径（默认 `out/<id>.mp4` 或 `out/<model>_<n>.png`） | — |
| `--no-download` | 只返回链接，不下载 | — |

媒体输入规则（与官方一致）：`first_frame`/`last_frame` 与 `reference_*` 互斥，不能在同一请求混用；
媒体 URL：在 `video`/`image` 下必须是公网 `http(s)` 或资产库 `asset://`；在 `seedance` 类型下，图生视频的
媒体**只收** `asset://`（http(s) 会被前置拒绝），文生视频不需要媒体。
