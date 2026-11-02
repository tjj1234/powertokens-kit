# powertokens-kit

<p align="center">
  <b>用一份零依赖的 Python 引擎，跨 Claude Code / Cursor / Trae / VS Code / Windsurf / Cline / Zed 等 40+ agent 调用 PowerTokens 的图片、视频、Seedance 模型。</b>
</p>

<p align="center">
  <a href="https://github.com/tjj1234/powertokens-kit">仓库</a> ·
  <a href="#3-快速开始cli">快速开始</a> ·
  <a href="#13-接入方式-aagent-skills">Agent Skills 接入</a> ·
  <a href="#14-接入方式-bmcp-server">MCP 接入</a> ·
  <a href="#10-seedance-专题独立类型">Seedance 专题</a> ·
  <a href="README.md">English</a>
</p>

> 📘 本文档为中文。 [English](README.md)
>
> **📦 本仓库包含两部分能力**
>
> - **本页（`pt_video.py` + `mcp_server.py`）**：image / video / seedance 生成引擎与 MCP 接入。
> - **`pt-gateway/`（生产级统一网关）**：在引擎之上增加 **audio（TTS）** 与 **chat（多模态 / 联网搜索 / 思考模式能力标注）**，HTTP + MCP 双接口、能力目录、三层测试。
>
> 👉 **chat / 音频 / 网关相关文档不在本页**，见 [`pt-gateway/README.md`](pt-gateway/README.md)（chat 模型能力标注在 **§9.1**，38 个模型逐一标注 vision / audio_in / video_in / web_search / thinking）。

---

## 目录

1. [这是什么](#1-这是什么)
2. [为什么做这个](#2-为什么做这个)
3. [快速开始（CLI）](#3-快速开始cli)
4. [能力总览：image / video / seedance 三类型](#4-能力总览image--video--seedance-三类型)
5. [支持的模型与定价](#5-支持的模型与定价)
6. [前置要求](#6-前置要求)
7. [安装](#7-安装)
8. [配置 API Key](#8-配置-api-key)
9. [命令参考（全参数）](#9-命令参考全参数)
10. [Seedance 专题（独立类型）](#10-seedance-专题独立类型)
11. [跨账户资产共享与安全注意](#11-跨账户资产共享与安全注意)
12. [输出与下载](#12-输出与下载)
13. [接入方式 A：Agent Skills](#13-接入方式-aagent-skills)
14. [接入方式 B：MCP server](#14-接入方式-bmcp-server)
15. [定价与消耗估算](#15-定价与消耗估算)
16. [API 直连参考](#16-api-直连参考)
17. [常见问题 / 排错](#17-常见问题--排错)
18. [已知限制与路线图](#18-已知限制与路线图)
19. [贡献](#19-贡献)
20. [License](#20-license)
21. [Changelog](#21-changelog)

---

## 1. 这是什么

`powertokens-kit` 是一个**跨 agent 的 PowerTokens 工具箱**：把 [PowerTokens](https://powertokens.ai/) 的 **image / video / Seedance / audio / chat** 模型能力，封装成「一份引擎 + 两种接入壳」，并附带一个生产级统一网关：

- **引擎 `pt_video.py`**：纯 Python 标准库，**零外部依赖**、**直连官方 REST API**、**不需要 Node、也不需要 powertokens-cli**。
- **壳 A · Agent Skills（`SKILL.md`）**：符合开放标准，直接被 40+ 支持该标准的 agent 读取（Claude Code、Cursor、GitHub Copilot、Codex、Trae、Zed、Kiro、Junie、Roo Code …）。
- **壳 B · MCP server（`mcp_server.py`）**：被任何 MCP 客户端当作「工具」调用（Claude Desktop、Cursor、Trae、VS Code、Windsurf、Cline、Zed …）。
- **网关 `pt-gateway/`**：HTTP + MCP 统一入口，在引擎之上补齐 **audio（TTS）** 与 **chat**（含多模态 / 联网搜索 / 思考模式能力标注），自带能力目录与三层测试。文档见 [`pt-gateway/README.md`](pt-gateway/README.md)。

> 三种壳共用**同一个引擎**，所有生成逻辑只维护一份（单一真相源）。

**官方 REST 直连，没有中间层：**

| 类型 | 提交端点 | 机制 |
|---|---|---|
| 视频（`video` / `seedance`） | `POST /v1/videos` | 异步：提交后每 10 秒轮询 `GET /v1/videos/{task_id}`，直到 `succeeded/completed` 拿到链接 |
| 图片（`image`） | `POST /v1/images/generations` | 同步：提交后直接返回 `data[].url`，**不轮询**，可一次返回多张 |

默认模型为 `auto`（按你账号真实可用的模型自动挑选，视频/图片/seedance 分开），也可显式指定任意模型，并可查询每个模型的定价与支持的参数。

---

## 2. 为什么做这个

- **跨 agent 通用**：不想每次换 agent（Claude / Cursor / Trae …）都重写一遍调用代码。本仓库同时输出 **Agent Skills 标准** 与 **MCP 标准** 两份接入，覆盖市面上绝大多数 agent 客户端。
- **开源、零依赖**：只依赖 Python 标准库，任何人 `git clone` 后 `python pt_video.py` 即可跑，无需装 Node、无需装 powertokens 的 CLI。
- **官方 API 直连**：不依赖任何第三方中转/网关，调用行为可预测、可审计，出问题直接对照官方端点排查。
- **踩坑已沉淀**：Seedance 协议签署、asset:// 素材格式、文生视频的 media-text 提交方式、跨账户资产共享等坑，都已实测并写入 `docs/pitfall-archive.md` 与本文档。

---

## 3. 快速开始（CLI）

```bash
git clone https://github.com/tjj1234/powertokens-kit.git
cd powertokens-kit
export POWERTOKENS_API_KEY="sk-pt-xxxx"   # 推荐用环境变量（见 §8）

python pt_video.py check                 # Key 体检
python pt_video.py models -t all --json  # 列出模型 + 定价 + 参数

# 视频（默认 auto，按账号可用模型自动选）
python pt_video.py generate -p "一只猫在蒙古草原跳舞" -d 5 -r 720p
python pt_video.py generate -m kling-v2-1-master -p "一只猫跳舞" -d 5 -r 1080p

# 图片（加 --type image）
python pt_video.py generate -p "赛博城市夜景" --type image --aspect-ratio 16:9
python pt_video.py generate -m seedream-5-0-260128 -p "赛博城市" --type image --aspect-ratio 16:9
python pt_video.py estimate -m seedance-1-5-pro-251215 -d 5 -r 720p   # 仅估算消耗（不扣费）

# Seedance 全系（1.0/1.5/2.0/2.5 均可，独立类型：--type seedance，需先签协议，见 §10）
python pt_video.py models -t seedance --json
python pt_video.py generate --type seedance -m seedance-1-5-pro-251215 -p "一只猫在草地上走" -d 5 -r 720p --no-download   # 文生视频：纯 -p 即可
python pt_video.py upload -f ./first_frame.png                          # 上传素材拿 asset://<id>（免费）
python pt_video.py generate --type seedance -m seedance-1-5-pro-251215 -p "镜头推进" --first-frame asset://<id> --no-download   # 图生视频
python pt_video.py seedance enable    # 签好协议后标记，不再弹开通引导

# 只读账户 / 计费查询（不产生消耗，不扣费）
python pt_video.py balance            # 余额查询（按 Key 粒度）
python pt_video.py usage --start-date 2026-01-01 --end-date 2026-01-31   # 账单查询（按模型类型汇总）
python pt_video.py logs               # 消费记录查询
python pt_video.py agreement          # 资产库协议签署状态查询
```

**工作原理：**
- 视频 `generate` 提交后进入**异步轮询**——每 10 秒查一次状态，直到成功拿到视频链接（默认自动下载到 `out/<task_id>.mp4` 并打印链接）。真实出片通常 1–3 分钟。
- 图片 `generate --type image` 提交后**同步**返回 `data[].url`（通常几秒），默认下载到 `out/<model>_<n>.png`。

---

## 4. 能力总览：image / video / seedance 三类型

本项目把生成能力分成**三种并列类型**（这是经过实测后刻意的设计，见 §10）：

| 类型 | 子命令 `--type` | 说明 | 协议前置 | 素材格式 | 生成机制 |
|---|---|---|---|---|---|
| `image` | `--type image` | 文生图 | 无 | — | 同步（`/v1/images/generations`） |
| `video` | `--type video`（默认） | 普通视频模型（Kling / Wan / Hailuo / Vidu / HappyHorse） | 无 | `http(s)` 或 `asset://` | 异步轮询（`/v1/videos`） |
| `seedance` | `--type seedance` | Seedance 全系（ByteDance：1.0/1.5/2.0/2.5，含 Mini/Fast） | **需签《Asset Library User Agreement》** | **仅 `asset://`**（图生）；文生无需素材 | 异步轮询（`/v1/videos`） |

> **关键设计点**：Seedance 被拆成独立类型，而**不是**塞进普通 `video`。原因：它有三大独有前置（签协议 / 素材走资产库 `asset://` / 文生靠 `media-text` 而非顶层 `prompt`），与普通视频模型系统性不同。普通 `video` 的 `auto` 候选已剔除 Seedance，`--type seedance` 才显式走它。

---

## 5. 支持的模型与定价

> 价格为 **PT 预扣单价（美元，$）**，基于 `5s / 720p` 探测；时长与分辨率按系数换算（见 §15）。图片单价 PT 未公开 API，表中标 `n/a`，以仪表盘为准。
> 实际可用模型以 `python pt_video.py models -t all --json` 在你账号下查到的为准（不同账号额度/白名单不同）。

### 视频模型（`--type video`，auto 候选）

| 模型 ID | 5s/720p 单价 | 时长上限 | 分辨率 | 主要输入 |
|---|---|---|---|---|
| `wan3.0-video` | $0.20 | 10s | 720p/1080p | text, image |
| `wan3.0-video-prime` | $0.70 | 10s | 720p/1080p | text, image |
| `wan2.7-t2v` | $0.475 | 10s | 720p/1080p | text, image |
| `kling-v3` | $0.42 | 10s | 720p/1080p | text, image, audio |
| `kling-video-o1` | $0.42 | 10s | 720p/1080p | text, image |
| `kling-v2-1-master` | $1.40 | 10s | 720p/1080p | text, image, audio（运镜） |

### Seedance 模型（`--type seedance`，实测 7 个全部 available）

| 模型 ID | 5s/720p 单价 | 时长上限 | 分辨率 | 主要输入 |
|---|---|---|---|---|
| `seedance-1-5-pro-251215` | $0.247 | 10s | 720p/1080p | text, image(first-frame), start-end, video+audio ref |
| `dreamina-seedance-2-0-fast-260128` | $0.605 | 10s | 720p/1080p | 同上 |
| `dreamina-seedance-2-0-260128` | $0.756 | 10s | 720p/1080p | 同上 |
| `dreamina-seedance-2-0-mini-260615` | n/a | 10s | 720p/1080p | 同上 |
| `dreamina-seedance-2-5-260628` | n/a | 10s | 720p/1080p | 同上 |
| `seedance-1-0-pro-fast-251015` | n/a | 10s | 720p/1080p | 同上 |
| `seedance-1-0-pro-250528` | n/a | 10s | 720p/1080p | 同上 |

### 图片模型（`--type image`，auto 候选）

| 模型 ID | 单价 | 宽高比 | 输入 |
|---|---|---|---|
| `seedream-5-0-260128`（auto 默认） | n/a | 1:1/16:9/4:3/3:2/2:3/3:4/9:16/21:9 | text |
| `qwen-image` / `image-01` / `kling-image` / `wan` 等 | n/a | 同上 | text |

---

## 6. 前置要求

- **Python 3.9+**（仅标准库；只有 MCP server 需要可选的 `mcp` 包）。
- **PowerTokens API Key**：打开 <https://powertokens.ai/zh-Hans/dashboard> → 注册/登录 → 充值 → 创建 Key（形如 `sk-pt-xxxx`）。
- **（仅 Seedance）**：使用 `--type seedance` 前，需在仪表盘手动签署《Asset Library User Agreement》（<https://powertokens.ai/assets-library?tab=1>）。**该协议无 API 端点可程序化签署**，只能网页手签。

---

## 7. 安装

### 方式一：直接用（推荐，零安装）

```bash
git clone https://github.com/tjj1234/powertokens-kit.git
cd powertokens-kit
python pt_video.py check
```

引擎只用标准库，`python pt_video.py <子命令>` 即可，无需 `pip install`。

### 方式二：作为 MCP server 安装（可选）

仅当你要用「接入方式 B（MCP）」时才需要装 `mcp` 依赖：

```bash
pip install -e ".[mcp]"      # 或 pip install "mcp[cli]>=1.2"
python mcp_server.py         # stdio；agent 客户端会自动拉起
```

---

## 8. 配置 API Key

三种配置方式，**优先级从高到低**：

1. **环境变量 `POWERTOKENS_API_KEY`**（推荐，任何 agent 通用）
   ```bash
   export POWERTOKENS_API_KEY="sk-pt-xxxx"
   ```
2. **本地配置文件**（权限 0600，推荐在共享机器上用）
   ```bash
   python pt_video.py config --set-key "sk-pt-xxxx"
   # 保存在 ~/.config/powertokens-kit/config.json（用户级，不在仓库内）
   # 旧路径 ~/.config/powertokens-video/config.json 仍会自动回落读取（更名兼容）
   ```
3. **仓库级配置**（可选）：`PT_VIDEO_CONFIG` 环境变量可覆盖配置文件路径。

> ⚠️ Key 属于敏感凭据：**不要**写进任何会被提交的文件。`.gitignore` 已忽略 `config.json` 与 `.env`。

---

## 9. 命令参考（全参数）

所有命令形式：`python pt_video.py <子命令> [参数]`。

| 子命令 | 作用 | 备注 |
|---|---|---|
| `check` | Key 体检 | exit 0=就绪，2=缺 Key |
| `config --set-key <k>` | 保存 Key 到 config.json | — |
| `config --set-model <id\|auto>` | 保存默认模型 | 默认 `auto` |
| `config --show` | 查看配置（Key 脱敏） | — |
| `auto-model [--type image\|video\|seedance]` | 打印 auto 首选模型（不生成） | — |
| `models [-t video\|image\|seedance\|all] [--json]` | 列出模型 + 定价 + 支持参数 | 无 Key 也能看公开目录 |
| `estimate -m <id> -d <s> -r <p>` | 仅估算消耗（美元） | 不扣费 |
| `generate -m <id\|auto> -p ... [--type ...]` | 生成（视频轮询 / 图片同步） | 核心命令 |
| `upload -f <图片路径> [--group-name <组名>]` | 上传素材到资产库，拿 `asset://<id>` | **免费** |
| `seedance enable\|disable\|status` | 管理 Seedance 协议本地标记 | 见 §10 |
| `balance [-k <key>]` | **余额查询（按 Key 粒度）** | `GET /v1/billing/balance`；只读 |
| `usage [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD] [-k <key>]` | **账单查询（按模型类型汇总）** | `GET /v1/billing/usage`；只读 |
| `logs [-k <key>]` | **消费记录查询** | `GET /v1/logs/query`；只读 |
| `agreement [-k <key>]` | **资产库协议签署状态查询** | `GET /v1/asset/agreement/status`；只读 |

> `balance` / `usage` / `logs` / `agreement` 四个命令均为**只读**——不会产生生成、不会扣费。鉴权沿用「裸 key」（`Authorization: <key>`，不带 Bearer）打到 `powertokens.ai/api` 域（仪表盘 / 资产后端），与资产上传同域同鉴权。见 §16。

### `generate` 完整参数

通用：
- `-p/--prompt`（必填，文生/图生提示词）
- `-m/--model`（默认 `auto`；也可写具体模型 ID）
- `--type`（默认按模型 ID 推断；显式 `video` / `image` / `seedance`）
- `-o/--output`（输出路径；不传则落到 `out/`）
- `--no-download`（只拿链接，不下载文件）
- `--timeout`（轮询总上限秒，默认 600）

视频（`video` / `seedance`）：
- `-d/--duration`（2–30s，默认 5）
- `-r/--resolution`（`480P` / `720P` / `1080P`，默认 720P）
- `--ratio`（如 `16:9` / `9:16` / `adaptive`）
- `--seed`（整数，可复现）
- `--first-frame` / `--last-frame`（首帧/尾帧，`asset://` 或 `http(s)`）
- `--ref-image` / `--ref-video` / `--ref-audio`（参考素材，可多次）
- `--no-audio`（关闭生成音频）
- `--no-prompt-extend`（关闭提示词扩展）
- `--watermark`（开启水印）

图片（`image`）：
- `--aspect-ratio`（`1:1` / `16:9` / `4:3` / `3:2` / `2:3` / `3:4` / `9:16` / `21:9`，默认 `1:1`）
- `--n`（1–9 张，默认 1）
- `--response-format`（`url` / `base64`，默认 `url`）

### `upload` 参数

- `-f/--file`（图片路径，必填；资产库要求宽 300–6000px）
- `--group-name`（资产分组名，可选；不可与已存在组名重复）

### 只读账户 / 计费命令

- `balance [-k <key>]` — 打印 `GET /v1/billing/balance` 原始 JSON（balance、currency `credits`、data_as_of、缓存标志、key_id）。
- `usage [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD] [-k <key>]` — 打印 `GET /v1/billing/usage` 原始 JSON，可选按日期范围过滤（按模型 `total_cost` 明细 + 合计）。
- `logs [-k <key>]` — 打印 `GET /v1/logs/query` 原始 JSON（consume_id、request_id、task_id、endpoint、model、status、quota、usage、billing 等）。
- `agreement [-k <key>]` — 打印 `GET /v1/asset/agreement/status` 原始 JSON（account_id、agreement_accepted、asset_library_enabled）。

若省略 `-k/--key`，命令读取环境变量 `POWERTOKENS_API_KEY`（与生成同一套优先级）。401 时退出码 2，其他 API 错误退出码 1。

---

## 10. Seedance 专题（独立类型）

### 10.1 为什么是独立类型

Seedance 全系模型（ByteDance，1.0 / 1.5 / 2.0 / 2.5 及 Mini/Fast 变体）有三个与其他视频模型**系统性不同**的前置：
1. 必须签《Asset Library User Agreement》（仪表盘手签，无 API）；
2. 图生视频的素材必须走资产库，以 `asset://<ASSET_ID>` 传入；
3. 文生视频的 prompt 必须放进 `media:[{type:text, text:...}]`，服务端**不认顶层 `prompt` 字段**。

因此把它提升为独立类型 `--type seedance`，而不是在 `video` 里打补丁。普通 `video` 的 `auto` 候选已剔除它。

### 10.2 协议签署流程（本地标记 + 报错回退）

PT **没有**「查询协议是否已签」的 API 端点，所以 skill 用**本地标记 + 报错回退**近似判断：

- 未标记时，生成前会打印 4 步「跳转 PT 开通」引导（**不阻断**提交）；
- 你签好后跑 `python pt_video.py seedance enable` 标记已签，之后不再弹引导；
- 若提交仍被服务端判「未签」（关键词命中），skill 自动 `reset` 标记并重新弹引导；
- 首次成功生成后自动记为已签。

`seedance status` 现在还会 **best-effort 真查服务端签署状态**：若设置了 `POWERTOKENS_API_KEY`，会调用 `GET /v1/asset/agreement/status` 并额外返回 `agreement_accepted_server` / `asset_library_enabled_server`；若未设置 Key 则跳过服务端校验。

```bash
python pt_video.py seedance status    # 查看当前标记（+ best-effort 服务端校验）
python pt_video.py seedance enable    # 签好后标记
python pt_video.py seedance disable   # 取消标记
```

### 10.3 文生视频 vs 图生视频（实测澄清）

- **文生视频只需 `-p`，无需任何 media**：提交时引擎自动把 prompt 放进 `media:[{type:text}]`。
- **图生视频**（首帧/参考图）才需要素材，且素材必须经资产库上传后以 `asset://<ASSET_ID>` 格式传入。

> ⚠️ **纠正旧认知**：网络/早期文档有「BytePlus 系文生视频必须带 media」的说法，是**误判**——当初用假 `asset://` 占位符才触发 `BytePlus video models only accept media`；换成真实 Key 实测，纯 prompt 文生视频可正常走到扣费校验（余额不足时返回 `pre-consume quota failed`）。

### 10.4 素材上传闭环（`upload`，免费）

图生视频必须用 `asset://` 素材。两步拿到：

```bash
# 1) 上传本地图（资产库要求宽 300–6000px），免费，返回 asset://<id>
python pt_video.py upload -f ./first_frame.png
# 2) 用 asset id 做首帧
python pt_video.py generate --type seedance -m seedance-1-5-pro-251215 -p "镜头缓慢推进" --first-frame asset://<id>
```

> Seedance 类型下，`--first-frame` 等媒体**只收 `asset://`**；传公网 `http(s)` 会在前置校验被拒绝（避免绕过协议）。

---

## 11. 跨账户资产共享与安全注意

**实测结论（2026-10-27，真实双账户）**：PowerTokens 的 `asset://` 资产 id 是**跨账户可解析、读路径不做账户隔离**的共享引用。

- 用账户 A 上传的 `asset://xxx`，可以直接在**账户 B 的 Key** 下作为首帧生成视频，生成任务完整跑完、消耗记在账户 B。
- **安全含义**：不要把涉密 / PII 内容放进 asset 却指望「账户隔离」——任何知道 asset id 的人都能在自有 Key 下引用它做生成（只要过预扣费）。**asset id 应视作「不可猜测但可共享」的 token**。
- **产品机会**：这也意味着「上传一次素材、多人/多账户复用生成」是平台原生支持的能力，可在协作类产品里直接利用。

---

## 12. 输出与下载

- **视频**：轮询成功后默认下载到 `out/<task_id>.mp4` 并打印链接；`--no-download` 只返回链接。
- **图片**：同步返回后默认下载到 `out/<model>_<n>.png`（多张自动加 `_1`/`_2` 序号）；`--no-download` 只返回 `data[].url`。
- **图片 URL 有效期**：`data[].url` 是临时签名地址（约 24 小时），请及时下载或转存。
- 所有生成产物落在仓库内 `out/` 目录（已加入 `.gitignore`，不会被提交）。

---

## 13. 接入方式 A：Agent Skills

本仓库根目录**本身就是一个 Agent Skill 文件夹**（含 `SKILL.md`），复制到 agent 的 skills 目录即可被读取。

| Agent | skills 目录 |
|---|---|
| Claude Code | `~/.claude/skills/powertokens-kit/` |
| Cursor | `<project>/.cursor/skills/powertokens-kit/` 或用户级 skills 目录 |
| GitHub Copilot | 项目内 `.github/skills/` 或按 Copilot skills 文档 |
| Codex (OpenAI) | `~/.codex/skills/powertokens-kit/` |
| Trae / Zed / Kiro / Junie / Roo Code | 对应 skills 目录（参考各工具文档） |

```bash
# 例：Claude Code
mkdir -p ~/.claude/skills
cp -r . ~/.claude/skills/powertokens-kit
```

目录结构（本仓库根即 skill 文件夹）：

```
powertokens-kit/
├── SKILL.md          # Agent Skills 清单（name + description + 指令）
├── pt_video.py       # 引擎（直连官方 REST，零依赖）
├── mcp_server.py     # MCP server（可选）
├── LICENSE
├── README.md         # 英文版（默认）
├── README.zh-CN.md   # 本文件（中文版）
├── pyproject.toml
└── docs/
    ├── publish.md          # 注册表提交清单（英文）
    ├── publish.zh-CN.md    # 中文版
    └── pitfall-archive.md  # 踩坑档案
```

`SKILL.md` 的 `description` 使用工具无关的触发语（如 “generate a video with PowerTokens”），不含任何单一 agent 专有字眼，因此可被 40+ agent 通用。

---

## 14. 接入方式 B：MCP server

仅 MCP server 需要 `mcp` 依赖（见 §7 方式二）。运行：

```bash
python mcp_server.py           # stdio；agent 客户端自动拉起
```

在各 MCP 客户端的配置里加上（把路径换成你的绝对路径，并带上 Key 环境变量）：

```json
{
  "mcpServers": {
    "powertokens-kit": {
      "command": "python",
      "args": ["/abs/path/to/powertokens-kit/mcp_server.py"],
      "env": { "POWERTOKENS_API_KEY": "sk-pt-xxxx" }
    }
  }
}
```

各客户端配置入口：

| 客户端 | 入口 |
|---|---|
| Claude Desktop | 设置 → 开发者 → Edit Config（claude_desktop_config.json） |
| Claude Code | `claude mcp add powertokens-kit -- python /abs/path/mcp_server.py` |
| Cursor | Settings → MCP → Add |
| Trae | 设置 → MCP → 添加 |
| VS Code (Copilot) | 命令面板 → MCP: Add Server |
| Windsurf / Cline / Zed | 对应 MCP 配置面板 / JSON |

### MCP 暴露的工具

| 工具 | 说明 |
|---|---|
| `check_key` | 检查 API Key 是否就绪 |
| `list_models(kind)` | 列出模型 + 定价 + 参数；`kind=all\|video\|image\|seedance` |
| `estimate_cost(model, duration_s, resolution)` | 估算一次生成消耗（美元） |
| `generate_video(prompt, model, duration_s, resolution, output, timeout_s)` | 生成视频（异步自动轮询） |
| `generate_image(prompt, model, aspect_ratio, n, output)` | 生成图片（同步） |
| `generate_seedance(prompt, model, duration_s, resolution, first_frame, output, timeout_s)` | 生成 Seedance 视频（独立类型） |
| `query_balance()` | **余额查询（按 Key）** — `GET /v1/billing/balance` |
| `query_usage(start_date, end_date)` | **账单查询（按模型类型汇总）** — `GET /v1/billing/usage` |
| `query_logs()` | **消费记录查询** — `GET /v1/logs/query` |
| `query_agreement()` | **资产库协议签署状态查询** — `GET /v1/asset/agreement/status` |

> 注：`upload`（资产上传）目前仅 CLI 提供，MCP 工具未暴露；Seedance 图生视频所需的 `asset://` 可先通过 CLI `upload` 拿到，再传给 `generate_seedance` 的 `first_frame`。四个 `query_*` 工具均为只读，不会产生生成、不会扣费。

---

## 15. 定价与消耗估算

- 视频基于 PT **预扣单价**估算；系数：时长线性（5s→10s = ×2），1080p = 720p ×2。
  例：`seedance-1-5-pro-251215` 5s/720p = $0.247 → 10s/720p = $0.494 → 5s/1080p = $0.494。
- 图片按张计费，PT 未开放单价 API，表中标 `n/a`，以仪表盘为准。
- `estimate` 子命令只估算、**不扣费**，也不进入生成流程。
- PT 额度按**美元**计；生成前服务端会做 `pre-consume quota failed` 预扣校验——**校验不通过不会扣费**。

---

## 16. API 直连参考

生成类端点 Base：`https://api.powertokens.ai`，鉴权：`Authorization: Bearer <POWERTOKENS_API_KEY>`。

| 用途 | 方法 + 路径 | 要点 |
|---|---|---|
| 提交视频 | `POST /v1/videos` | body：`model` / `prompt`（顶层，普通视频用；Seedance 用 `media:[{type:text}]`）/ `size`(720P) / `ratio` / `seconds`(**字符串**) / `generate_audio` / `media[]` |
| 轮询视频 | `GET /v1/videos/{task_id}` | 状态 `succeeded`/`completed` 后取 `metadata.url` |
| 文生图 | `POST /v1/images/generations` | OpenAI 风格，同步返回 `data[].url`（**带 `/generations` 后缀**） |
| 列出可用模型 | `GET /v1/models` | 返回当前 Key 可调用的模型列表（用于 auto 候选 / 预校验） |
| 上传资产 | `POST powertokens.ai/api/.../upload` | 异步：返回 `task_id` → 轮询 `get-asset-id` 拿 `asset_id` → 引用 `asset://<id>` |

**账户 / 计费 / 资产库端点 —— 不同域、不同鉴权**：§9 的四个只读命令（`balance`、`usage`、`logs`、`agreement`）打到**独立的 `https://powertokens.ai/api` 域**（仪表盘 / 资产后端），**不是** `api.powertokens.ai`，且使用**裸 key 鉴权**（`Authorization: <key>`，不带 Bearer）——与资产上传同域同鉴权。

| 用途 | 方法 + 路径（域 `powertokens.ai/api`） | 要点 |
|---|---|---|
| 余额查询（按 Key） | `GET /v1/billing/balance` | 返回 balance / currency `credits` / 缓存标志 / key_id |
| 账单查询（按模型类型） | `GET /v1/billing/usage` | 可选 `start_date`/`end_date`（YYYY-MM-DD）；按模型 `total_cost` 明细 + 合计 |
| 消费记录查询 | `GET /v1/logs/query` | consume_id / request_id / task_id / endpoint / model / status / quota / usage / billing |
| 资产库协议签署状态 | `GET /v1/asset/agreement/status` | account_id / agreement_accepted / asset_library_enabled |

> ⚠️ **未真机验证**：沙箱无可用 Key。若这些端点返回 401/403，该域可能要的是网页登录令牌而非 API Key，请用真实 Key 实测确认。

常见错误：
- `cannot unmarshal number into ... seconds of type string`：服务端 `seconds` 是 `enum<string>`，必须传字符串（引擎已处理）。
- `pre-consume quota failed`：余额不足，预扣校验未通过，**未扣费**。
- `BytePlus video models only accept media`：多为传了无效/假 `asset://` 导致（详见 §10.3）。
- `404` 于 `/v1/images`：正确路径是带 `/generations` 后缀的 `/v1/images/generations`。

---

## 17. 常见问题 / 排错

### 17.1 PT HTTP 状态码（401 / 402 / 403）

PT 对各状态码有明确语义。CLI / MCP / 网关会把原始状态码与 PT 报文透传到 stderr；CLI 将鉴权失败映射为退出码 2，其余错误为退出码 1。

| 状态码 | 含义 | 典型触发 |
|--------|------|----------|
| `401 Unauthorized` | API Key 失效 / 过期 / 缺失 | Key 错误或未传 |
| `402 Payment Required` | 余额不足 / 额度耗尽 | 生成时点数用尽 |
| `403 Forbidden` | 无权限 / 模型下架 | Key 无权限，或模型已下架 |

注意：
- 只读的账户/计费命令（`balance` / `usage` / `logs` / `agreement`，及网关 `/account/*` 路由）不会返回 `402`，只做查询。
- 若 `https://powertokens.ai/api` 返回 `401` / `403`，可能该域要的是**网页登录令牌**而非 API Key，请用真实 Key 实测确认（沙箱未验证）。

**Q：视频生成一直停在某个状态？**
A：视频是异步生成，真实出片 1–3 分钟。脚本每 10s 轮询，默认总上限 600s；超时可加 `--timeout`。若长时间 `unknown`，多半是服务端排队，可稍后拿 `task_id` 用 `GET /v1/videos/{id}` 复查（后续版本会补 `status` 子命令）。

**Q：图片 URL 打不开？**
A：`data[].url` 是临时签名地址（约 24 小时）。请及时用 `--no-download` 之外的默认行为下载，或自行 `curl` 保存。

**Q：Seedance 提示要签协议？**
A：去 <https://powertokens.ai/assets-library?tab=1> 手签，然后 `python pt_video.py seedance enable`。

**Q：为什么我的 `auto` 没选到 Seedance？**
A：Seedance 是独立类型，普通 `video` 的 auto 不含它；要显式 `--type seedance`。

**Q：跨账户能用同一个 asset id 吗？**
A：可以（见 §11）——asset id 跨账户可解析，但注意安全含义。

**Q：MCP server 启动报缺 `mcp`？**
A：`pip install -e ".[mcp]"` 或 `pip install "mcp[cli]>=1.2"`。

**Q：`balance`/`usage`/`logs`/`agreement` 返回 401/403？**
A：这些命令打到 `powertokens.ai/api` 域、用裸 key 鉴权（见 §16）。先确认通过 `-k` 或 `POWERTOKENS_API_KEY` 传了正确的 Key；若仍 401/403，该域可能要的是网页登录令牌而非 API Key。

---

## 18. 已知限制与路线图

- **轮询熔断**：429 退避 / 5xx 熔断为已知待办（见 `docs/pitfall-archive.md` A.02/A.03）。
- **`status` 子命令**：按 `task_id` 单独复查状态未实现。
- **图片单价**：PT 未开放单价 API，`estimate` 对图片返回 `n/a`。
- **Seedance 协议无 API 签署**：只能仪表盘手签 + 本地标记近似。
- **结果视频直连下载**：部分 result URL 在本机直连 R2 可能返回 403（桶热链/访问策略），生成任务 server-side 完成不受影响；下载链路策略待进一步确认。
- **账户 / 计费端点未真机验证**：见 §16 说明。

---

## 19. 贡献

欢迎 Issue / PR。改动请保持「引擎单一真相源」原则：CLI / SKILL / MCP 三处共用 `pt_video.py`，不要在 MCP 或 SKILL 里重复实现生成逻辑。新增踩坑请同步更新 `docs/pitfall-archive.md`。

---

## 20. License

[MIT](LICENSE)

---

## 21. Changelog

- **0.5.0** — 新增**只读账户 / 计费命令**：CLI `balance` / `usage` / `logs` / `agreement` 与配套 MCP 工具（`query_balance` / `query_usage` / `query_logs` / `query_agreement`）；`seedance status` 现在 best-effort 真查服务端签署状态；补充账户 / 计费 / 资产库端点所在的 `powertokens.ai/api` 裸 key 域说明。
- **0.4.1** — 纠正「Seedance 文生视频必须带 media」误判（实测纯 prompt 可行）；补充跨账户资产共享发现（A.14）；`upload` 资产上传闭环；文档对齐。
- **0.4.0** — Seedance 拆为独立类型（`image`/`video`/`seedance` 并列）；协议「本地标记 + 报错回退」开通流程；`force_asset` 媒体校验；MCP `generate_seedance` 工具。
- **0.3.0** — 补齐图片生成（同步 `/v1/images/generations`）；REST 直连替代 CLI。
- **0.2.0** — 异步视频提交 + 轮询；`auto` 模型选择；定价/参数查询。
- **0.1.0** — 初始引擎骨架。
