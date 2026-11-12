# Remake 功能「全免费 · 开源 · 自托管」替代方案调研报告

> 调研日期：2026-11-03 ｜ 目的：回答「给视频链接 → remake 出新视频」能否**完全免费**完成
> 结论先行：**可以。** 7 步流水线每一步都有开源/免费工具，零 API 账单。唯一“成本”是你自己的硬件（GPU/CPU），
> 或白嫖 Colab 免费额度 / 按小时租卡。

---

## 1. 与上一轮结论的关系（先说清楚）

上一轮我们判定：**PT 只覆盖 7 步里的「第 5 步视频生成」**，其余 6 步要第三方工具。
现在用户问“能不能全免费”——答案分两种：

| 诉求 | 能否做到 | 说明 |
|------|----------|------|
| 用 PT（付费）做生成 + 其他免费 | 部分免费 | PT 生成要烧 credits，不算“全免费” |
| **全部开源自托管，连生成也不要 PT** | ✅ **能** | 生成改用本地开源视频模型（Wan 等），与 PT 卖的模型同源 |

**关键澄清**：PT 网关里能调用的 `Wan` 模型，本身就是 **Apache 2.0 开源**，阿里免费放出权重。
所以“全免费”= 跳过 PT 网关，自己本地/租卡跑 Wan/CogVideoX/LTX 等开源模型。
PT 在这个方案里**可以被完全替代**（或仅作为可选的云端兜底，不强制）。

---

## 2. 全免费栈对照表（7 步全开源）

| 步 | 能力 | 免费/开源工具 | 协议 | 硬件需求 |
|----|------|--------------|------|----------|
| ① 链接下载 | 抓 YT/TikTok/抖音/B站/IG/X 视频 | **yt-dlp**（命令行，1000+ 站点） | Unlicense（完全免费） | 任意机器，Python |
| ② ASR 转录 | 语音→文字（词级时间戳+说话人分离） | **faster-whisper** / **whisper.cpp** / **WhisperX** | MIT / MIT / BSD | CPU 即可；GPU 加速更快 |
| ③ VLM 分析 | 抽结构/风格/关键帧 | **Qwen2-VL** / **LLaVA** / **InternVL2**（Ollama 本地跑） | Apache 2.0 | 7B 约 8–16GB 显存 |
| ④ LLM 改写 | 出脚本/分镜 | **Ollama + Qwen2.5 / Llama 3.1 / Qwen2.5-Coder** | Apache 2.0 | 7B 约 4.7GB 显存 |
| **⑤ 视频生成** | **生成新画面** | **Wan 2.1/2.2 / CogVideoX / LTX-Video / HunyuanVideo / Mochi 1**（本地或 ComfyUI） | Apache 2.0 / MIT 为主 | 8–24GB 显存（见 §4） |
| ⑥ 合成渲染 | 拼接+字幕+9:16 | **FFmpeg** | LGPL/GPL | 任意机器 |
| ⑦ 发布 | 推社媒 | 各平台官方 API（建议 MVP 先不做） | 免费但要过审 | — |

**结论：7 步零 API 账单，全部可本地跑。**

---

## 3. 各步靠谱工具详述

### ① 下载 — yt-dlp ✅
- 168k★、Unlicense、完全免费，支持 **YouTube / Bilibili / TikTok / 抖音 / Instagram / X / Vimeo** 等 1000+ 站点。
- 纯命令行，可嵌入脚本；支持字幕下载、音频提取、断点续传。
- ⚠️ YouTube 需 `--cookies-from-browser chrome` 绕过 403；站点改接口 yt-dlp 会快速跟进。
- 合规提醒：自动下载受各平台 ToS 约束，**生产化需走合规路径**（自建抓取 or 取得授权）。

### ② 转录 — faster-whisper / whisper.cpp / WhisperX ✅
- **faster-whisper**（MIT）：GPU 下比原版快 4–6 倍，支持 INT8 量化（distil 版仅 ~1GB 显存）、词级时间戳、说话人分离。
- **whisper.cpp**（MIT/Apache）：纯 C++，树莓派/手机都能跑，Apple Silicon Metal 加速。
- **WhisperX**（BSD）：在 faster-whisper 基础上加说话人标签，适合多人口播视频。
- 全部离线、零 API 费用；中文识别好（large-v3-turbo 首选）。

### ③ 视频理解 — Qwen2-VL / LLaVA / InternVL2（Ollama）✅
- **Qwen2-VL**（Apache 2.0）：支持**原生视频理解**，中文 OCR/图表强，7B 版 INT4 仅需 8GB 显存，消费级显卡可跑。
- **InternVL2**（Apache 2.0）：26B 版 MMMU 达 GPT-4V 85%，适合长视频/长文档。
- 本地做法：对参考视频**抽关键帧** → base64 喂给 `localhost:11434` 的 Ollama API → 让它输出结构/风格/分镜描述。
- ⚠️ 小尺寸 VLM 会**编造时间戳**，关键镜头要回跳校验；画质分析能力弱于云端 Gemini。

### ④ 脚本改写 — Ollama + Qwen2.5 ✅
- **Qwen2.5**（多数尺寸 Apache 2.0）：0.5B–72B 可选，7B 仅 4.7GB，支持 128K 上下文、结构化 JSON 输出、中文强。
- 把 ②③ 的产物（转录稿 + 结构分析）喂给它，让它**改写成你的版本**脚本/分镜。
- 也可加 **Qwen2.5-Coder** 做编排脚本生成。完全本地、隐私、零账单。

### ⑤ 视频生成 — 本地开源模型（**替代 PT 的核心一步**）✅
见 §4 专节。

### ⑥ 合成 — FFmpeg ✅
- 免费、跨平台，把生成的各镜头拼成 9:16、加字幕、转码。已是行业标配。

### ⑦ 发布 — 先不做
- 各社媒官方 API 免费但要审核/授权；**MVP 建议砍掉**，先产出可下载视频即可。

---

## 4. 生成那一步：开源视频模型选型（决定“免费”是否成立）

> 这是全免费方案的关键。PT 网关卖的 `Wan` 就是下面这些开源权重之一的托管版。

| 模型 | 参数量 | 最低显存 | 画质 | 协议 | 备注 |
|------|--------|----------|------|------|------|
| **Wan 2.1 (T2V 1.3B)** | 1.3B | **8GB** | Good | Apache 2.0 ✅商用 | 消费级甜点，PT 同源 |
| Wan 2.2 (14B) | 14B | 16GB (FP8) | Excellent | Apache 2.0 ✅ | 画质天花板级 |
| CogVideoX-5B | 5B | 6GB (FP8) | Very Good | Apache 2.0 ✅ | 中文友好 |
| LTX-Video 2.x | 22B | 12GB (量化) | Excellent | Apache 2.0 ✅ | 最快、带原生音频 |
| HunyuanVideo 13B | 13B | 24GB (量化) | Excellent | **MIT** ✅ | 腾讯，运动质量好 |
| Mochi 1 | 10B | 24GB | Very Good | Apache 2.0 ✅ | 可 LoRA 练角色 |
| Open-Sora 2.0 | 11B | 16GB | Very Good | Apache 2.0 ✅ | 含训练流水线 |
| SkyReels V1 | — | — | Good | Apache 2.0 ✅ | 数字人向 |

**部署方式（任选）：**
- **ComfyUI / Pinokio**：节点流或一键安装，社区工作流照抄即出片（最省心）。
- **Docker 本地推理**：`docker run --gpus all ...`，REST 暴露给编排层。
- **无 GPU / 显卡不够**：Google Colab 免费 T4 额度试水；或 RunPod / Vast.ai 按小时租 4090（约 $0.4–0.8/小时，**试验期近乎免费**）。

**商业授权注意**：Wan / LTX / Mochi / Open-Sora / SkyReels / HunyuanVideo(MIT) 可商用；
**CogVideoX-5B 与 HunyuanVideo 原版有自定义/条件条款**，商用前务必查官方 license 卡。

---

## 5. 真实成本与坑（避免“免费”幻觉）

1. **硬件是隐性成本**：本地生成要 GPU。8GB 显存只能跑 Wan 2.1 1.3B（画质“Good”非电影级）；
   想要电影感需 16–24GB（RTX 4090 甜点卡）或租卡。CPU-only 基本不可行（几分钟/秒级太慢）。
2. **速度**：本地 5 秒片段约 1–5 分钟；云端 API 约 30–60 秒。免费换的是时间。
3. **VLM 分析质量**：本地小 VLM 对视频结构/风格的理解弱于云端 Gemini，可能漏镜、编时间戳——需校验回路。
4. **ToS / 法律**：yt-dlp 自动下载 YT/TikTok 受平台条款约束，生产化要合规抓取或授权；remake 还要留意原片版权。
5. **模型 license**：上文已标，商用前逐一核对 license 卡，别默认“HuggingFace 上就能免费商用”。
6. **稳定性**：开源模型靠社区维护，版本/量化兼容偶有坑；ComfyUI 工作流需锁版本。

---

## 6. 最小可行落地建议（纯免费路线）

**推荐栈（以本地开源为中心）：**
```
链接 → yt-dlp 下载 → faster-whisper 转录 → Qwen2-VL(Ollama) 分析结构
     → Qwen2.5(Ollama) 改写分镜 → 本地 Wan2.1/2.2(ComfyUI) 生成各镜
     → FFmpeg 拼接+字幕+9:16 → 产出视频
```
- MVP 先支持 YouTube / Bilibili / TikTok 链接；输出 9:16；**砍掉发布与换脸换声**。
- 编排用轻量 Python 串（无需 OpenMontage，除非想要它那套工具库）。
- 验证闭环：先打通 `yt-dlp → WhisperX → Qwen2-VL → 本地 Wan 生成 1 镜 → FFmpeg 拼 1 镜`。

**与 PT 的关系（给你决策用）：**
- 走全免费：PT **可不用**（生成被本地 Wan 替代）。
- 若想“平时免费、忙时扩产能”：保留 PT 作为**云端兜底生成**（第 5 步双路：本地优先、PT 兜底），其余 6 步仍免费。这是我们 `powertokens-video` 项目本就擅长的位置。

---

## 7. 下一步（都不碰代码，你拍板）

1. **纯免费自托管**：我可细化 MVP 技术选型 + 环境清单（ComfyUI 工作流、Ollama 模型列表、依赖版本）。
2. **PT 兜底混合**：保留 PT 生成作为云端备线，我先画“本地优先 / PT 兜底”的路由设计。
3. **先验证闭环**：列一份“最小可跑 demo 的操作步骤清单”（不含代码），你照着本地试。

⚠️ 提醒：上面所有“免费”指**软件/模型零授权与零 API 账单**；跑起来仍需你自备算力（或白嫖 Colab/租卡）。
若你机器没有独显，建议先走 Colab 免费档验证可行性，再决定是否添置 GPU。
