# Remake 功能：PT 能力缺口 × 可接入第三方工具调研

> 背景：上一轮确认「仅用 PT 模型无法完成整体 remake」（PT 只覆盖 7 步里的视频生成第 ⑤ 步）。本报告调研 PT 缺失的 ①下载 ②ASR ③VLM分析 ④LLM改写 ⑥合成 ⑦发布 分别能用哪些**第三方工具接入**，并给出一个以 PT 为生成核心、可拼装的参考栈。
> 本报告仅做研究，未改动任何代码。数据截至 2026-11-02。

---

## 1. 结论先行

PT 缺的 6 步**都有成熟第三方工具可接入**，且多数是「REST API 调一下」的级别。所以「独立做一个 remake 功能」是可行的，但正确说法是：

> **PT 做第 ⑤ 步视频生成，其余 6 步由第三方工具/自研代码补齐，由一个编排层（我们的代码或 OpenMontage）串起来。**

不是「PT 独立完成」，而是「**以 PT 为生成引擎的可拼装系统**」。下面给对照表和推荐栈。

---

## 2. 能力缺口 → 可接入第三方工具对照表

### ① 视频链接下载（TikTok / YouTube / IG / X）
| 工具 | 类型 | 成本 | 集成方式 | 备注 |
|------|------|------|----------|------|
| **Cobalt** | 开源（AGPL-3.0）自托管 | 免费（自建） | REST API（Docker，默认 9000 端口） | 覆盖 YouTube/Twitter/X/TikTok/Reddit/Instagram/SoundCloud/Twitch 等 20+ 平台；官方实例仅限个人，**商用必须自托管**；低资源（1 核/512MB 即可）；无 Key、无账号 |
| yt-dlp | 开源（CLI/库） | 免费 | Python 调用 | 站点覆盖最广，但无 API 层，需自己包 |

**推荐**：Cobalt 自托管（有现成 REST，最省事）。⚠️ 下载行为受各平台 ToS 与所在司法辖区约束，商用需合规评估。

### ② ASR 转录（语音→文字+时间轴）
| 工具 | 类型 | 成本 | 集成方式 | 备注 |
|------|------|------|----------|------|
| **Whisper**（OpenAI，MIT） | 开源自托管 / API | 自托管免费；API ~$0.006/分钟 | Python / REST | 99 种语言；词级时间戳需 WhisperX |
| **WhisperX** | 开源 | 免费 | Python | 词级时间戳 + 说话人分离（OpenMontage 即用此） |
| AssemblyAI | SaaS | ~$0.37/小时，$50 额度免费 | REST | 自带分离/自动分章/PII 脱敏/LeMUR 问答 |
| Deepgram | SaaS | ~$0.26/小时（批），12k 分钟/年免费 | REST / WebSocket | 流式 <300ms、分离；实时场景首选 |

**推荐**：MVP 用 **WhisperX 自托管**（免费）；要省事上生产用 **AssemblyAI**（功能打包好）。

### ③ VLM 视频分析（结构 / 风格 / 关键帧 / 核心信息）
| 工具 | 类型 | 成本 | 集成方式 | 备注 |
|------|------|------|----------|------|
| **Gemini 2.5/3 Flash·Pro** | SaaS | 2.0 Flash ~$0.10/MTok；2.5 Pro $1.50/$5/MTok | REST | **原生视频理解**（非抽帧），1M+ 上下文，支持 YouTube URL（预览），静态 1FPS 或 agentic 模式；~100 token/秒（低清） |
| GPT-4o | SaaS | $2.50/$10/MTok | REST | 图像理解强，但**无原生视频**，需抽帧；单图上限 20K 像素 |
| Claude | SaaS | $3/$15/MTok | REST | 文档优先视觉，无原生视频 |
| LLaVA / Qwen-VL / InternVL | 开源自托管 | 算力成本 | 本地推理 | 隐私/离线场景 |

**推荐**：**Gemini**（原生视频，最适合「看懂一个视频的结构」）；GPT-4o + 抽帧也可，但工程更重。

### ④ LLM 脚本/分镜改写
| 工具 | 类型 | 备注 |
|------|------|------|
| GPT / Claude / Gemini | SaaS | 任意前沿 LLM；**可复用 ③ 的 Gemini**（一个模型同时做分析+改写） |
| Llama / Qwen 等 | 开源自托管 | 离线/私有 |

**推荐**：与 ③ 共用 Gemini，省一个供应商。

### ⑥ 合成 / 渲染（拼接 + 字幕 + 9:16 + 混音）
| 工具 | 类型 | 成本 | 集成方式 | 备注 |
|------|------|------|----------|------|
| **FFmpeg** | 开源 | 免费（本地算力） | CLI | 拼接/字幕烧入/裁剪/混音标准件；MVP 首选 |
| Shotstack | SaaS | 免费 20 分钟 720p；~$39–49/月 | REST JSON 时间线 | 最成熟确定性渲染，~20 秒/分钟，最快 |
| Creatomate | SaaS | ~$41–54/月 | 模板 + REST | 关键帧/3D 特效，适合批量变体 |
| JSON2Video | SaaS | ~$49.95/月，Hobby ~$16.95 | JSON + REST | **TTS+字幕打包进额度**，旁白类社媒省一个供应商 |
| Remotion | 开源 | 免费起步（自有基建） | React 组件 | OpenMontage 用的引擎；灵活但要自己运维 |
| Reap / Revid / BlitzReels / OpusClip / Submagic | SaaS（agent-native，带 CLI/MCP） | 按量 | API/CLI/MCP | 直接管 剪辑+字幕+转录+发布 的短视品流水线 |

**推荐**：MVP 用 **FFmpeg 自托管**（免费）；要管理式 API 用 Shotstack（纯渲染）或 JSON2Video（带 TTS）；想 agent-native 短视品用 Reap/Revid。

### ⑦ 发布
| 工具 | 类型 | 备注 |
|------|------|------|
| TikTok Content Posting API / YouTube Data API（upload）/ Instagram Graph API / X API | 官方 API | 各需应用审核 + OAuth；工程重 |
| Buffer / Hootsuite 类 | SaaS | 定时多发，省去自接各平台 |

**推荐**：MVP **先不做自动发布**（手动下载成片），或接 Buffer 类；自动发布留到后期。

---

## 3. 推荐参考架构（PT 为生成核心）

```
用户粘贴链接
  → ① Cobalt(自托管)        下载视频文件
  → ② WhisperX(自托管)       转录 + 词级时间轴
  → ③ Gemini                 VLM 分析：结构/风格/关键帧/核心信息 → 结构化分镜
  → ④ Gemini(同模型)         LLM 改写：新脚本 + 各镜 文生/图生 提示词
  → ⑤ PT 视频生成            Seedance/Kling/MiniMax/Wan 生成各镜新画面  ← 我们的接口
  → ⑥ FFmpeg(自托管)         拼接 + 字幕 + 裁 9:16 + 混音
  → ⑦ (可选) 社媒 API / Buffer   发布
         ↑ 编排层：我们的代码 或 OpenMontage 串起以上
```

**以 PT 为中心的含义**：第 ⑤ 步（也是成本/价值核心的生成）由 PT 网关承担；其余全部是可替换的标准件。

---

## 4. 集成方式与成本粗估

- **集成形态**：每个第三方几乎都是 REST/SDK 调用；编排层用我们的代码（或 OpenMontage 的 pipeline）按上面顺序链式调用，PT 在第 ⑤ 步以 `pt_video.py` / PT REST 接入。
- **成本结构**：
  - 自托管免费件：Cobalt、WhisperX、FFmpeg（只花服务器算力）。
  - 按量 SaaS：Gemini（视频分析 ~$0.1–0.4/MTok，一个短视频约几百~几千 token）、AssemblyAI（可选）、Shotstack/JSON2Video（约 $0.1–0.5/分钟渲染）。
  - PT 视频生成：按模型/时长计费（最大头，需预算护栏）。
- **预算护栏**：沿用 OpenMontage 思路（单动作阈值 + 总预算上限）框住 PT 与 SaaS 花费。

---

## 5. 风险与注意

1. **PT 仍只做第 ⑤ 步**：本报告只是把其余 6 步的「第三方替身」列清楚，不等于 PT 能独立完成。落地仍需写编排代码。
2. **下载 ToS/合规**：Cobalt 官方实例禁商用，必须自托管；TikTok/YouTube 对自动下载有条款与反爬，生产化要走合规路径（官方 API / 授权抓取）。
3. **数据治理**：视频/转录涉及用户内容与隐私，尤其 ⑦ 发布前需确权；Gemini/AssemblyAI 等云端会看到内容，敏感场景考虑自托管 WhisperX + 本地 VLM。
4. **VLM 分析是「引线」非事实**：Gemini 可能编造时间戳/细节，关键镜头要回跳校验后再生成。
5. **PT 未真机验证**：第 ⑤ 步依赖真实 Key 联调；跨镜风格一致（参考帧 + 图生视频 + 运镜）需实测。

---

## 6. 下一步建议（待确认后再动手，本次不写代码）

1. **定编排层**：用「我们的代码」轻量串（姿势 B），还是「OpenMontage + PT adapter」（姿势 A）？报告已证明两者都能拼这套栈。
2. **定 MVP 边界**：支持哪些平台链接？(TikTok/YouTube/IG?) 要不要 ②ASR？(③分析用 Gemini 还是本地?) 输出比例？(9:16?) 是否含 ⑦发布？
3. **先打通最小闭环**：Cobalt→WhisperX→Gemini→**PT 生成一镜**→FFmpeg 拼一镜，验证链路再扩。

> 本报告为纯研究产出，未修改任何代码。
