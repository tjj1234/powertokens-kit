# media.io Viral Studio「Remake」功能调研报告

> 研究对象：`https://www.media.io/ai/zh/viral-studio`
> 调研目的：用户想实现一个**「给视频链接 → remake 成一个新视频」**的窄功能（对标 media.io Viral Studio）。本报告拆解它需要哪些能力，并判断**咱们的 PowerTokens 视频接口/模型能在其中承担哪一段**。
> 本报告仅做研究，未改动任何代码。

---

## 1. media.io Viral Studio 是什么

- **一句话**：AI 把病毒视频「克隆/翻拍」成你的版本。粘贴一个 TikTok / Instagram / YouTube 链接 → AI 分析原片的**创意套路（结构、风格、关键信息）** → 保留赢的结构、换上新文案 / 新画面 / 你的角度 → 产出个性化新视频/图像。
- **卖点**：5× 更快、可从一个想法批量生成多版本、零剪辑经验、日更涨粉工作流。
- **它明确不做身份克隆**：页面只说「new copy / new visuals / your own angle」「upload your personal assets to the cloned template」——是**结构/风格 remix**，不是换脸/换声（这点对我们的接口友好，因为 PT 不做换脸）。

---

## 2. 它的工作流（官方 3 步）

| 步骤 | 动作 | 隐含的技术能力 |
|------|------|----------------|
| **Step 1 Explore** | 从趋势库选主题，**或粘贴社媒链接**，点 "Clone it" | 视频链接抓取 + 解析；趋势库（TikTok/YouTube/IG/X） |
| **Step 2 Create Your Version** | 上传个人素材、填需求，点 "Generate" | Agent 内容规划 + 创意改编（LLM） |
| **Step 3 Generate & Publish** | AI Agent 把病毒创意转成社媒可用视频，可继续对话微调 | **AI 视频生成** + 合成/渲染（9:16 等）+ 发布 |

### 隐含的底层技术栈（从文案反推）
1. **链接摄取 + 下载**：从 TikTok/YouTube/IG/X 拉视频（含反爬/ToS 处理）。
2. **视频分析**：ASR 转录 + 视觉分析，提取**结构（镜头/节奏）、风格、关键帧、核心信息**。
3. **创意改写（LLM）**：基于结构重写脚本 / 分镜，注入用户角度。
4. **AI 视频生成（核心生成步）**：用新脚本/参考生成新画面。
5. **合成/渲染**：拼接、加字幕、转 9:16 社媒格式。
6. （可选）发布到社媒。

> 注意：第 1/2/3/5/6 步**都不是「视频生成模型」**，而是一个 **Agent 编排流水线**。视频生成只是其中第 4 步。

---

## 3. 能力拆解 × PT 接口能否覆盖

| 能力模块 | PT 视频接口能否覆盖 | 说明 |
|----------|-------------------|------|
| ① 链接抓取/下载 | ❌ 不能 | 反爬+ToS，需自建或用第三方抓取服务 |
| ② ASR 转录 | ❌ 不能 | PT 无 ASR；需 Whisper/云 ASR |
| ③ 视觉分析（结构/风格/关键帧） | ❌ 不能 | 需 VLM（GPT-4V/Claude/Gemini 等） |
| ④ 文案/分镜改写（LLM） | ❌ 不能直接 | 需通用 LLM；PT 是视频网关不是 LLM |
| **⑤ AI 视频生成（新画面）** | ✅ **能，且是主力** | PT 的 Seedance / Kling / MiniMax Hailuo / Wan 正是这步的引擎 |
| ⑥ 合成/渲染（拼接+字幕+9:16） | ❌ 不能 | 需 FFmpeg/Remotion 类 |
| ⑦ 发布 | ❌ 不能 | 社媒 API |

**结论**：media.io 这个「remake 功能」= 一个 7 步 Agent 流水线；**咱们的 PT 接口只覆盖第 ⑤ 步（视频生成）**，是核心生成引擎，但单独无法构成这个功能。

---

## 4. 我们的 PT 接口在「remake」里具体能顶什么

第 ⑤ 步「生成新画面」正是 PT 的强项，且 media.io 要的两种生成 PT 都支持：

- **文生视频（text-to-video）**：用改写后的分镜脚本，调 Seedance / Kling / MiniMax 生成新镜头。
- **图生视频（image-to-video）+ 多模态参考 + 运镜**：把参考视频抽出的**关键帧作为风格/构图参考**，用 Kling 的「参考视频+音频」「运镜控制」或 Seedance 的「图生视频/首尾帧/多模态参考」重生成——这正好对应「保留原片结构、换上新画面」的 remake 语义。

> 即：PT 能让你**从「结构/风格描述 + 参考帧」生成一段新视频**，这正是 remake 的「生成」内核。但它**不会**替你做下载、转录、分析、改写、合成。

---

## 5. 要真正做出这个功能，还缺什么（非 PT 部分）

按最小可用，需要在 PT 外围补齐：

1. **链接 → 视频**：下载 TikTok/YouTube/IG 视频（自建或第三方；注意各平台 ToS 与反爬）。
2. **转录**：Whisper / 云 ASR 拿到文案与时间轴。
3. **分析 Agent**：用 VLM（GPT-4V/Claude/Gemini）从视频+转录提取「镜头结构、节奏、风格、核心信息」→ 输出结构化分镜。
4. **改写 Agent（LLM）**：基于分镜 + 用户角度，生成新脚本 / 每镜的文生/图生提示词。
5. **调用 PT 视频生成**：对每镜调 Seedance/Kling 等生成新画面（**本步用 PT**）。
6. **合成（FFmpeg/Remotion）**：把多镜拼接、加字幕、裁成 9:16。
7. （可选）发布。

其中 2/3/4/6 是「工程 + 其它模型」，PT 只负责 5。

---

## 6. 一个最小可行方案（MVP）草图

**思路**：PT 做生成引擎，其余用「轻量自建 + 现有服务」补齐，不做成 OpenMontage 那种全栈。

```
用户粘贴链接
   → [抓取+下载] 第三方/自建
   → [ASR] Whisper
   → [分析 Agent] VLM 抽结构/风格/分镜
   → [改写 Agent] LLM 出新脚本+各镜提示词
   → [PT 视频生成] Seedance/Kling 生成各镜  ← 咱们的接口在这
   → [合成] FFmpeg 拼 + 字幕 + 9:16
   → 输出 remake 视频
```

**两种落地姿势**：
- **姿势 A（站在 OpenMontage 肩上）**：OpenMontage 本就有「reference video driven creation」（粘贴 YouTube/Short 链接 → Agent 分析 → 出生产计划），与 media.io 这条流水线高度同构。给它加一个 **PT provider adapter**（上一轮报告路线 A），就能把视频生成换成我们的模型，再包一层简单 UI 即近似 media.io 体验。**最省事。**
- **姿势 B（从零自建）**：自己串起「抓取→ASR→VLM→LLM→PT生成→FFmpeg」。灵活但工程量大，且要自己处理下载 ToS / 反爬。

---

## 7. 风险与注意

1. **PT 只覆盖生成步**：别指望「接上 PT 就能 remake」——缺的 6 步（尤其下载、ASR、VLM 分析、LLM 改写、FFmpeg 合成）才是主要工程量。
2. **链接下载的 ToS/反爬**：TikTok/YouTube 对自动下载有条款与反爬，生产化需合规方案（官方 API / 授权抓取），否则易封。
3. **身份/版权**：remake 应保留「结构/风格」而非原片素材，避免直接搬运；media.io 自身也强调「your own angle / new visuals」，我们照此边界更安全。
4. **PT 生成一致性**：remake 要「保留原结构」，靠「参考帧 + 图生视频 + 运镜」实现，跨镜角色/风格一致需实测（Kling 的 reference / Soul ID 类能力可关注）。
5. **PT 未真机验证**：视频生成本身依赖真实 Key；MVP 第 5 步需拿真 Key 联调。

---

## 8. 结论

- media.io Viral Studio 的「remake」是一个 **Agent 编排流水线（链接→分析→改写→生成→合成）**，不是单个模型能力。
- **咱们的 PT 视频接口能且只能源覆盖其中的「AI 视频生成」一步**（Seedance/Kling/MiniMax/Wan），而且是适配的引擎——尤其图生视频 + 多模态参考 + 运镜正好对应「保留结构、换新画面」。
- 要做成这个功能，还需在 PT 外围补齐**抓取、ASR、VLM 分析、LLM 改写、FFmpeg 合成**；或直接复用 **OpenMontage 的 reference-driven 流水线 + PT adapter**（最省力）。

**建议下一步（待确认后再动手，本次不写代码）**：
1. 若走**姿势 A**：我可 clone OpenMontage，精读其 `reference video` 相关 pipeline + `tools/video/*` adapter 契约，产出「PT adapter 对接清单」。
2. 若走**姿势 B**：先和你敲定 MVP 的边界（支持哪些平台链接？是否要 ASR？输出什么比例？），再给技术选型（抓取/ASR/VLM/LLM 各自用什么）。
3. 纯评估到此已够用。

> 本报告为纯研究产出，未修改任何代码。
