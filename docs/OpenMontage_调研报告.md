# OpenMontage 调研报告

> 研究对象：`https://github.com/calesthio/OpenMontage`
> 调研目的：① 梳理 OpenMontage 能实现的功能；② 判断能否「替换成咱们现在的 PowerTokens 视频接口」、能否「用咱们接口的模型完成」。
> 结论预览：**视频生成这一层可以换，但整体系统不能替**——两者是不同层（OpenMontage = 生产系统 + 编排层；PowerTokens = 模型网关 / 生成后端）。详见第 5、6 节。
> 本报告仅做研究，未改动任何代码。

---

## 1. 项目概况

| 项 | 值 |
|----|----|
| 定位 | 全球首个开源、**智能体驱动（agentic）** 的视频生产系统 |
| Stars / Forks | 74,701 / 9,825 |
| 许可证 | **GNU AGPLv3**（注意：修改后网络分发须开源衍生代码，见第 7 节） |
| 主语言 | Python（~5.3MB）为主，配 TypeScript / JavaScript（Remotion / HyperFrames） |
| 创建 / 最近推送 | 2026-03-29 / 2026-10-28 |
| 规模 | 12 条生产流水线、100+ 工具、700+ 智能体技能 / 生产知识文件、60+ 云/本地供应商 |
| 架构标签 | `agent` · `agentic-ai` · `video-generation` · `text-to-video` · `remotion` · `ffmpeg` · `stable-diffusion` |

**一句话**：OpenMontage 不是一个「视频生成工具」，而是一个**把 AI 编程助手（Claude Code / Cursor / Copilot / Codex）变成完整视频工作室的操作系统**——自然语言进，成片出，中间的研究、脚本、素材、剪辑、合成全部由 Agent 编排。

---

## 2. 它能实现什么功能（能力拆解）

### 2.1 端到端流水线（从创意到成片）
`research → proposal → script → scene_plan → assets → edit → compose`
（研究 → 提案 → 脚本 → 场景规划 → 素材 → 编辑 → 合成）

### 2.2 12 条生产流水线（pipeline_defs）
解释器、动画、数字人发言人、电影感、短视频工厂、纪录片蒙太奇、混合、本地化与配音、播客重用、屏幕演示、Talking Head 等。

### 2.3 100+ 生产工具（分 7 类）
`video` / `audio` / `graphics` / `enhancement` / `analysis` / `avatar` / `subtitle`

### 2.4 关键能力（不是单纯「图转动画」）
- **参考视频驱动创作**：粘贴 YouTube / Short / Reel / TikTok / 本地视频 → Agent 分析转录、节奏、场景、关键帧、风格 → 产出 2–3 个差异化方案 + 成本估算。
- **真实素材纪录片**：从 Archive.org / NASA / Wikimedia / Pexels 检索真实运动素材，语义排序、剪辑成片（**无需付费视频模型也能出片**）。
- **实时网络研究**：脚本前跑 15–25+ 次跨平台搜索（YouTube / Reddit / 新闻 / 学术）确保内容有据。
- **质量门禁**：合成前验证 + 渲染后自检（ffprobe + 抽帧 + 音频分析），成片仅在自检通过时输出。
- **预算治理**：执行前估算、单动作审批阈值（默认 $0.50）、总预算上限（默认 $10）。
- **无供应商锁定**：评分选择器跨 7 维（任务契合 30% / 质量 20% / 控制 15% / 可靠 15% / 成本 10% / 延迟 5% / 连续性 5%）自动选优。

### 2.5 合成 / 渲染引擎（**本地，非模型 API**）
- **Remotion**（React/Node 程序化视频）：数据驱动解释器、文字卡、图表、TikTok 词级字幕、TalkingHead。
- **HyperFrames**（HTML/CSS/GSAP）：动能排版、产品宣传、SVG 角色动画。
- **FFmpeg**：编码、字幕烧入、音频复用、调色、视频拼接、精确裁剪、混音。
- **Blender**：3D 世界 / 物理动画（示例用）。

> ⚠️ 这点很重要：**「生成」和「合成/渲染」是两层**。视频生成只是 100+ 工具里的一类；Remotion / HyperFrames / FFmpeg 才是把素材拼成成片的引擎，它们是本地渲染，不是云端模型调用。

---

## 3. 它用到的模型 / 供应商（与我们的重叠）

### 3.1 视频生成（20+ 供应商）—— 重点看重叠
| OpenMontage 的供应商 | 是否与我们接口重叠 | 说明 |
|----------------------|------------------|------|
| **Seedance 2.0 / 2.5**（Volcengine Ark `seedance_ark`） | ✅ **重叠** | 我们 PT 网关有 Seedance 2.5/2.0/Fast/Mini |
| **Kling**（fal.ai `kling_fal` / 官方 `kling_official`） | ✅ **重叠** | 我们 PT 网关有 Kling |
| **MiniMax / H3（Hailuo）** | ✅ **重叠** | 我们 PT 网关有 MiniMax Hailuo |
| **WAN 2.1 / 2.2**（本地 GPU，免费） | ✅ **重叠（云端替代）** | 我们 PT 网关有 Wan 3.0（云端，非本地免费） |
| Runway Gen-3/4、Google Veo 3.1、Higgsfield、HeyGen、Grok Imagine、Gemini Omni | ❌ 不重叠 | 我们 PT 网关暂无这些 |
| Vidu、HappyHorse | ✅ 我们也有 | 我们 PT 网关覆盖 |

### 3.2 其它模态（我们接口的部分覆盖）
| 类别 | OpenMontage 供应商 | 我们 PT 接口覆盖情况 |
|------|-------------------|---------------------|
| 图像生成 | FLUX、Imagen 4、GPT Image、Seedream、Nano Banana、Recraft、SD（本地） | ⚠️ 部分（PT 有图像能力，但非全部） |
| TTS | ElevenLabs、Google、OpenAI、Azure、DashScope、Piper（本地免费） | ⚠️ 部分（PT 有 TTS 能力） |
| 音乐 | Suno、ElevenLabs Music/SFX | ❌ 基本无 |
| 增强 | Real-ESRGAN 超分、rembg 去背、CodeFormer/GFPGAN 人脸修复 | ❌ 无 |
| 分析 | WhisperX 转录、CLIP/BLIP-2 视觉分析 | ❌ 无 |
| 数字人 / 唇形 | SadTalker、MuseTalk、Wav2Lip、Kling Avatar | ❌ 无 |
| 素材库 | Pexels、Pixabay、Unsplash、Archive.org、NASA、Wikimedia | ❌ 无（这是开放档案，非模型） |

---

## 4. 我们的接口（PowerTokens 视频网关）能做什么

- **定位**：**模型网关 / 生成后端**，统一封装多家视频生成模型，提供 REST API + CLI（`pt_video.py`）+ MCP。
- **视频模型清单**：Seedance（2.5/2.0/Fast/Mini）、Kling、MiniMax Hailuo、Wan 3.0、Vidu、HappyHorse 等。
- **生成能力**：文生视频、图生视频（首帧）、首尾帧、多模态参考（参考视频+音频）、Kling 运镜控制。
- **附加**：图像 / 音频 / TTS 等能力（按 PT 官方 REST API）。
- **它不做的**：编排、脚本、素材检索、剪辑合成、渲染、质量门禁、预算治理——这些都不是「模型调用」。

---

## 5. 核心判断：能否替换？能否用我们的模型完成？

### 5.1 整体系统 —— **不能替换** ❌
OpenMontage 的核心价值是 **agentic 编排 + 100+ 工具 + 60+ 供应商适配 + 质量门禁 + 预算治理**；我们的 PT 接口只是其中的「生成后端」之一。拿 PT 接口去「替换 OpenMontage」等于拿一个数据库去替换一个 ERP 系统——层级不对。

### 5.2 视频生成这一层 —— **可以换成我们的模型** ✅
理由充分：
1. **模型完全重叠**：OpenMontage 的 Seedance / Kling / MiniMax(Hailuo) / Wan 与 PT 网关**同名同模型**，是 like-for-like 替换。
2. **架构可插拔**：OpenMontage 是 provider-pluggable——每个供应商有独立 adapter + 评分选择器。新增一个 `PowerTokens` provider 即可，不动其它逻辑。
3. **接口形态匹配**：PT 网关已暴露 REST / CLI（`pt_video.py` 的文生视频、图生视频、首尾帧、多模态参考），直接对接 OpenMontage 的 video 工具契约（提交 → 轮询 → 拿 URL）即可。

> 即：**「换成咱们接口的模型完成」这句话，对「视频生成步骤」成立；对「整个视频生产系统」不成立。**

### 5.3 不能由我们接口单独覆盖的部分
编排/技能知识库、TTS（绝大部分）、图像（部分）、音乐（Suno）、增强（超分/去背/人脸）、分析（转录/VLM）、数字人/唇形、素材库（Pexels 等）、本地渲染（Remotion/HyperFrames/FFmpeg/Blender）。这些要么不是模型调用，要么 PT 没有对应能力。

---

## 6. 两条可行路线

### 路线 A：集成式（**推荐**）
给 OpenMontage 写一个 **PowerTokens provider adapter**，把它的 `seedance_ark` / `kling_*` / `minimax` / `wan` 等视频生成适配器**替换为调用我们的 PT 网关**。
- 收益：复用 OpenMontage 全部编排 / 编辑 / TTS / 素材 / 质量门禁能力，视频生成统一走我们网关（可能更便宜 / 更可控 / 单一账单）。
- 成本：写一个 adapter（调用 PT REST/CLI），约 1 个工具文件的工程量；**不碰 OpenMontage 其它代码**。
- 风险：AGPLv3——若把改后的 OpenMontage 对外提供网络服务，须开源你的衍生版（仅内部使用影响较小，但需法务确认）。

### 路线 B：自建轻量版
参考 OpenMontage 的 agentic 工作流思路，用我们自己的 Agent + PT 接口自建一个「视频生产工作室」。
- 收益：完全自主、无 AGPL 传染性、与 PT 计费/网关深度绑定。
- 成本：**大**——需补齐 TTS、图像、音乐、素材检索、剪辑合成（FFmpeg）、质量门禁、预算治理，等于重造 OpenMontage 的大部分非生成能力。
- 结论：除非有强定制化诉求，否则不如路线 A 划算。

---

## 7. 风险与注意事项

1. **AGPLv3 传染性**（最关键）：OpenMontage 是 AGPLv3。若你**修改**它并把修改版以网络服务形式提供给他人，须开源整个衍生代码。仅「加一个独立 adapter 调用 PT」且内部使用，风险低；但若分发 / SaaS 化，务必让法务过目。我们自研接口（PT 网关）本身不受此约束。
2. **模型覆盖差**：Runway / Veo / Higgsfield / HeyGen 等我们无。若用户要这些风格，需保留原 provider，PT 只接管 Seedance/Kling/MiniMax/Wan 部分。
3. **本地免费模型缺失**：OpenMontage 的 WAN/Hunyuan/CogVideo/LTX **本地 GPU 免费**路径，PT 是云端付费。替换后「零成本出片」能力会削弱（除非保留本地路径）。
4. **计费差异**：PT 按模型计费；OpenMontage 的预算治理（$0.50/动作、$10 总预算）可保留，正好用来框住 PT 调用花费。
5. **我们接口当前未真机验证**：PT 账户/账单端点此前在沙箱无 Key 未实测；视频生成本身依赖真实 Key，adapter 写好需拿真 Key 联调。

---

## 8. 结论与下一步建议

**结论**：
- OpenMontage 是个「视频生产操作系统」，不是单纯生成器。
- 它的**视频生成层**（Seedance/Kling/MiniMax/Wan）可以干净地换成我们的 PowerTokens 模型，因为其架构 provider-pluggable、模型同名重叠。
- 但**整个系统无法被我们的接口替换**——编排、剪辑合成、TTS、素材、质量门禁、预算治理都不在 PT 接口职责内。

**建议下一步（待你确认后再动手）**：
1. 若走路线 A：我可以在 OpenMontage 仓库里**新增一个 `powertokens` provider adapter**（不改其它代码），对接我们的 `pt_video.py` / PT REST，替换视频生成供应商。需要先确认 AGPL 使用边界。
2. 若只想评估：我可以进一步 `Clone` OpenMontage，精读它的 `tools/video/*` 和 provider adapter 接口契约，给出「adapter 对接清单」（入参/出参/轮询协议），方便后续落地。
3. 若走路线 B：需要先和你敲定「最小可用功能集」，避免重造全部非生成能力。

> 本报告为纯研究产出，未修改任何代码。下一步请确认走哪条路线。
