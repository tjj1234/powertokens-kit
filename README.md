# powertokens-kit

<p align="center">
  <b>One zero-dependency Python engine to drive PowerTokens image / video / Seedance models across 40+ agents (Claude Code, Cursor, Trae, VS Code, Windsurf, Cline, Zed, …).</b>
</p>

<p align="center">
  <a href="https://github.com/tjj1234/powertokens-kit">Repository</a> ·
  <a href="#3-quick-start-cli">Quick Start</a> ·
  <a href="#13-integration-a-agent-skills">Agent Skills</a> ·
  <a href="#14-integration-b-mcp-server">MCP</a> ·
  <a href="#10-seedance-deep-dive-standalone-type">Seedance</a> ·
  <a href="README.zh-CN.md">中文文档</a>
</p>

> 📘 This document is in English. [中文文档](README.zh-CN.md)
>
> **📦 This repository contains two capabilities**
>
> - **This page (`pt_video.py` + `mcp_server.py`)**: the image / video / Seedance generation engine and MCP integration.
> - **`pt-gateway/` (production-grade unified gateway)**: builds on the engine to add **audio (TTS)** and **chat** (multimodal / web-search / thinking-mode capability labeling), with both HTTP and MCP interfaces, a capability catalog, and a three-tier test suite.
>
> 👉 **chat / audio / gateway docs are NOT on this page** — see [`pt-gateway/README.md`](pt-gateway/README.md) (chat model capability labeling in **§9.1**, 38 models labeled one-by-one for vision / audio_in / video_in / web_search / thinking).

---

## Table of Contents

1. [What is this](#1-what-is-this)
2. [Why build this](#2-why-build-this)
3. [Quick Start (CLI)](#3-quick-start-cli)
4. [Capability Overview: image / video / seedance](#4-capability-overview-image--video--seedance)
5. [Supported Models & Pricing](#5-supported-models--pricing)
6. [Prerequisites](#6-prerequisites)
7. [Installation](#7-installation)
8. [Configure API Key](#8-configure-api-key)
9. [Command Reference (full parameters)](#9-command-reference-full-parameters)
10. [Seedance Deep Dive (standalone type)](#10-seedance-deep-dive-standalone-type)
11. [Cross-account asset sharing & security notes](#11-cross-account-asset-sharing--security-notes)
12. [Output & Download](#12-output--download)
13. [Integration A: Agent Skills](#13-integration-a-agent-skills)
14. [Integration B: MCP server](#14-integration-b-mcp-server)
15. [Pricing & cost estimation](#15-pricing--cost-estimation)
16. [Direct API reference](#16-direct-api-reference)
17. [FAQ / Troubleshooting](#17-faq--troubleshooting)
18. [Known limitations & roadmap](#18-known-limitations--roadmap)
19. [Contributing](#19-contributing)
20. [License](#20-license)
21. [Changelog](#21-changelog)

---

## 1. What is this

`powertokens-kit` is a **cross-agent PowerTokens toolkit**: it wraps [PowerTokens](https://powertokens.ai/)'s **image / video / Seedance / audio / chat** model capabilities into "one engine + two integration shells", plus a production-grade unified gateway:

- **Engine `pt_video.py`**: pure Python standard library, **zero external dependencies**, **talks directly to the official REST API**, **no Node, no `powertokens-cli`**.
- **Shell A · Agent Skills (`SKILL.md`)**: conforms to the open standard and is read directly by 40+ supporting agents (Claude Code, Cursor, GitHub Copilot, Codex, Trae, Zed, Kiro, Junie, Roo Code …).
- **Shell B · MCP server (`mcp_server.py`)**: called as a "tool" by any MCP client (Claude Desktop, Cursor, Trae, VS Code, Windsurf, Cline, Zed …).
- **Gateway `pt-gateway/`**: a unified HTTP + MCP entry point that adds **audio (TTS)** and **chat** (with multimodal / web-search / thinking-mode capability labeling) on top of the engine, plus a capability catalog and three-tier tests. Docs: [`pt-gateway/README.md`](pt-gateway/README.md).

> All three shells share **the same engine**; all generation logic is maintained in one place (single source of truth).

**Official REST, no middle layer:**

| Type | Submit endpoint | Mechanism |
|---|---|---|
| Video (`video` / `seedance`) | `POST /v1/videos` | Async: poll `GET /v1/videos/{task_id}` every 10s until `succeeded/completed` to get the URL |
| Image (`image`) | `POST /v1/images/generations` | Sync: returns `data[].url` directly, **no polling**, can return multiple images at once |

The default model is `auto` (auto-picks from the models actually available on your account, separate pools for video/image/seedance); you can also specify any model explicitly and query each model's pricing and supported parameters.

---

## 2. Why build this

- **Cross-agent, universal**: avoid re-writing the calling code every time you switch agents (Claude / Cursor / Trae …). This repo ships **both** the Agent Skills standard and the MCP standard, covering the vast majority of agent clients on the market.
- **Open source, zero dependencies**: only the Python standard library. Anyone can `git clone` and run `python pt_video.py` — no Node, no powertokens CLI to install.
- **Official API direct**: no third-party relay/gateway; behavior is predictable and auditable, and problems can be debugged directly against the official endpoints.
- **Pitfalls documented**: Seedance agreement signing, the `asset://` asset format, the media-text submission style for text-to-video, cross-account asset sharing, etc. — all tested in practice and written into `docs/pitfall-archive.md` and this document.

---

## 3. Quick Start (CLI)

```bash
git clone https://github.com/tjj1234/powertokens-kit.git
cd powertokens-kit
export POWERTOKENS_API_KEY="sk-pt-xxxx"   # recommended via env var (see §8)

python pt_video.py check                 # Key health check
python pt_video.py models -t all --json  # list models + pricing + params

# Video (default auto, picks from account-available models)
python pt_video.py generate -p "a cat dancing on the Mongolian grassland" -d 5 -r 720p
python pt_video.py generate -m kling-v2-1-master -p "a cat dancing" -d 5 -r 1080p

# Image (add --type image)
python pt_video.py generate -p "cyberpunk city night" --type image --aspect-ratio 16:9
python pt_video.py generate -m seedream-5-0-260128 -p "cyberpunk city" --type image --aspect-ratio 16:9
python pt_video.py estimate -m seedance-1-5-pro-251215 -d 5 -r 720p   # estimate cost only (no charge)

# Seedance family (1.0/1.5/2.0/2.5 all work; standalone type: --type seedance; sign agreement first, see §10)
python pt_video.py models -t seedance --json
python pt_video.py generate --type seedance -m seedance-1-5-pro-251215 -p "a cat walking on grass" -d 5 -r 720p --no-download   # text-to-video: just -p
python pt_video.py upload -f ./first_frame.png                          # upload asset, get asset://<id> (free)
python pt_video.py generate --type seedance -m seedance-1-5-pro-251215 -p "camera push-in" --first-frame asset://<id> --no-download   # image-to-video
python pt_video.py seedance enable    # after signing, mark it so the onboarding prompt stops

# Read-only account / billing queries (no generation, no credit deduction)
python pt_video.py balance            # account balance (by Key)
python pt_video.py usage --start-date 2026-01-01 --end-date 2026-01-31   # billing summary by model type
python pt_video.py logs               # consumption records
python pt_video.py agreement          # asset-library agreement status
```

**How it works:**
- Video `generate` enters **async polling** after submission — checks status every 10s until success, then downloads to `out/<task_id>.mp4` by default and prints the link. Real render time is usually 1–3 minutes.
- Image `generate --type image` returns `data[].url` **synchronously** (usually a few seconds) and downloads to `out/<model>_<n>.png` by default.

---

## 4. Capability Overview: image / video / seedance

This project splits generation into **three parallel types** (a deliberate design, tested in practice — see §10):

| Type | `--type` subcommand | Description | Agreement prerequisite | Asset format | Generation mechanism |
|---|---|---|---|---|---|
| `image` | `--type image` | text-to-image | none | — | sync (`/v1/images/generations`) |
| `video` | `--type video` (default) | ordinary video models (Kling / Wan / Hailuo / Vidu / HappyHorse) | none | `http(s)` or `asset://` | async polling (`/v1/videos`) |
| `seedance` | `--type seedance` | Seedance family (ByteDance: 1.0/1.5/2.0/2.5, incl. Mini/Fast) | **must sign the "Asset Library User Agreement"** | **`asset://` only** (image-to-video); text-to-video needs no asset | async polling (`/v1/videos`) |

> **Key design point**: Seedance is split into a standalone type rather than crammed into ordinary `video`. Reason: it has three unique prerequisites (sign agreement / assets go through the asset library `asset://` / text-to-video uses `media-text` instead of the top-level `prompt`), which are systematically different from ordinary video models. The `auto` candidate for ordinary `video` already excludes Seedance; `--type seedance` explicitly routes to it.

---

## 5. Supported Models & Pricing

> Prices are **PT pre-deduction unit prices (USD, $)**, probed at `5s / 720p`; duration and resolution are converted by coefficients (see §15). Image unit prices are not public via PT API, marked `n/a` in the table — refer to the dashboard.
> Actually available models depend on `python pt_video.py models -t all --json` queried under your account (different accounts have different quotas/whitelists).

### Video models (`--type video`, auto candidates)

| Model ID | 5s/720p price | Max duration | Resolution | Main inputs |
|---|---|---|---|---|
| `wan3.0-video` | $0.20 | 10s | 720p/1080p | text, image |
| `wan3.0-video-prime` | $0.70 | 10s | 720p/1080p | text, image |
| `wan2.7-t2v` | $0.475 | 10s | 720p/1080p | text, image |
| `kling-v3` | $0.42 | 10s | 720p/1080p | text, image, audio |
| `kling-video-o1` | $0.42 | 10s | 720p/1080p | text, image |
| `kling-v2-1-master` | $1.40 | 10s | 720p/1080p | text, image, audio (camera motion) |

### Seedance models (`--type seedance`, 7 tested, all available)

| Model ID | 5s/720p price | Max duration | Resolution | Main inputs |
|---|---|---|---|---|
| `seedance-1-5-pro-251215` | $0.247 | 10s | 720p/1080p | text, image(first-frame), start-end, video+audio ref |
| `dreamina-seedance-2-0-fast-260128` | $0.605 | 10s | 720p/1080p | same as above |
| `dreamina-seedance-2-0-260128` | $0.756 | 10s | 720p/1080p | same as above |
| `dreamina-seedance-2-0-mini-260615` | n/a | 10s | 720p/1080p | same as above |
| `dreamina-seedance-2-5-260628` | n/a | 10s | 720p/1080p | same as above |
| `seedance-1-0-pro-fast-251015` | n/a | 10s | 720p/1080p | same as above |
| `seedance-1-0-pro-250528` | n/a | 10s | 720p/1080p | same as above |

### Image models (`--type image`, auto candidates)

| Model ID | Price | Aspect ratio | Input |
|---|---|---|---|
| `seedream-5-0-260128` (auto default) | n/a | 1:1/16:9/4:3/3:2/2:3/3:4/9:16/21:9 | text |
| `qwen-image` / `image-01` / `kling-image` / `wan` etc. | n/a | same as above | text |

---

## 6. Prerequisites

- **Python 3.9+** (standard library only; only the MCP server needs the optional `mcp` package).
- **PowerTokens API Key**: open <https://powertokens.ai/zh-Hans/dashboard> → register/log in → top up → create Key (looks like `sk-pt-xxxx`).
- **(Seedance only)**: before using `--type seedance`, you must manually sign the "Asset Library User Agreement" on the dashboard (<https://powertokens.ai/assets-library?tab=1>). **There is no API endpoint to sign it programmatically** — it must be done in the web UI.

---

## 7. Installation

### Method 1: Use directly (recommended, zero install)

```bash
git clone https://github.com/tjj1234/powertokens-kit.git
cd powertokens-kit
python pt_video.py check
```

The engine uses only the standard library; `python pt_video.py <subcommand>` works with no `pip install`.

### Method 2: Install as an MCP server (optional)

Only needed if you want "Integration B (MCP)" — install the `mcp` dependency:

```bash
pip install -e ".[mcp]"      # or pip install "mcp[cli]>=1.2"
python mcp_server.py         # stdio; the agent client launches it automatically
```

---

## 8. Configure API Key

Three configuration methods, **highest to lowest priority**:

1. **Environment variable `POWERTOKENS_API_KEY`** (recommended, works across any agent)
   ```bash
   export POWERTOKENS_API_KEY="sk-pt-xxxx"
   ```
2. **Local config file** (permissions 0600, recommended on shared machines)
   ```bash
   python pt_video.py config --set-key "sk-pt-xxxx"
   # saved to ~/.config/powertokens-kit/config.json (user-level, not in the repo)
   # old path ~/.config/powertokens-video/config.json is still auto-fallback-read (rename compatibility)
   ```
3. **Repo-level config** (optional): the `PT_VIDEO_CONFIG` env var can override the config file path.

> ⚠️ Keys are sensitive credentials: **do not** write them into any file that will be committed. `.gitignore` already ignores `config.json` and `.env`.

---

## 9. Command Reference (full parameters)

All commands: `python pt_video.py <subcommand> [args]`.

| Subcommand | Purpose | Notes |
|---|---|---|
| `check` | Key health check | exit 0=ready, 2=missing Key |
| `config --set-key <k>` | Save Key to config.json | — |
| `config --set-model <id\|auto>` | Save default model | default `auto` |
| `config --show` | View config (Key masked) | — |
| `auto-model [--type image\|video\|seedance]` | Print the auto-preferred model (no generation) | — |
| `models [-t video\|image\|seedance\|all] [--json]` | List models + pricing + supported params | public catalog visible without Key |
| `estimate -m <id> -d <s> -r <p>` | Estimate cost only (USD) | no charge |
| `generate -m <id\|auto> -p ... [--type ...]` | Generate (video polling / image sync) | core command |
| `upload -f <image path> [--group-name <group>]` | Upload asset to library, get `asset://<id>` | **free** |
| `seedance enable\|disable\|status` | Manage Seedance agreement local flag | see §10 |
| `balance [-k <key>]` | **Query account balance (by Key)** | `GET /v1/billing/balance`; read-only |
| `usage [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD] [-k <key>]` | **Query billing summary by model type** | `GET /v1/billing/usage`; read-only |
| `logs [-k <key>]` | **Query consumption records** | `GET /v1/logs/query`; read-only |
| `agreement [-k <key>]` | **Query asset-library agreement status** | `GET /v1/asset/agreement/status`; read-only |

> All four `balance` / `usage` / `logs` / `agreement` commands are **read-only** — they never generate or deduct credits. Auth uses the bare Key (`Authorization: <key>`, no `Bearer`) against the `powertokens.ai/api` host (the dashboard/asset backend), the same domain and auth style as asset upload. See §16.

### `generate` full parameters

General:
- `-p/--prompt` (required, text-to/image-to prompt)
- `-m/--model` (default `auto`; or a specific model ID)
- `--type` (inferred from model ID by default; explicit `video` / `image` / `seedance`)
- `-o/--output` (output path; defaults to `out/`)
- `--no-download` (return link only, don't download)
- `--timeout` (total polling timeout in seconds, default 600)

Video (`video` / `seedance`):
- `-d/--duration` (2–30s, default 5)
- `-r/--resolution` (`480P` / `720P` / `1080P`, default 720P)
- `--ratio` (e.g. `16:9` / `9:16` / `adaptive`)
- `--seed` (integer, reproducible)
- `--first-frame` / `--last-frame` (first/last frame, `asset://` or `http(s)`)
- `--ref-image` / `--ref-video` / `--ref-audio` (reference assets, repeatable)
- `--no-audio` (disable generated audio)
- `--no-prompt-extend` (disable prompt extension)
- `--watermark` (enable watermark)

Image (`image`):
- `--aspect-ratio` (`1:1` / `16:9` / `4:3` / `3:2` / `2:3` / `3:4` / `9:16` / `21:9`, default `1:1`)
- `--n` (1–9 images, default 1)
- `--response-format` (`url` / `base64`, default `url`)

### `upload` parameters

- `-f/--file` (image path, required; asset library requires width 300–6000px)
- `--group-name` (asset group name, optional; must not duplicate an existing group name)

### Read-only account / billing commands

- `balance [-k <key>]` — prints the raw `GET /v1/billing/balance` JSON (balance, currency `credits`, data-as-of, cached flag, key_id).
- `usage [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD] [-k <key>]` — prints the raw `GET /v1/billing/usage` JSON, optionally filtered by date range (per-model `total_cost` items + grand total).
- `logs [-k <key>]` — prints the raw `GET /v1/logs/query` JSON (consume_id, request_id, task_id, endpoint, model, status, quota, usage, billing, …).
- `agreement [-k <key>]` — prints the raw `GET /v1/asset/agreement/status` JSON (account_id, agreement_accepted, asset_library_enabled).

If `-k/--key` is omitted, the command reads `POWERTOKENS_API_KEY` from the environment (same precedence as generation). On a 401 it exits 2; any other API error exits 1.

---

## 10. Seedance Deep Dive (standalone type)

### 10.1 Why a standalone type

The entire Seedance family (ByteDance, 1.0 / 1.5 / 2.0 / 2.5 and Mini/Fast variants) has three prerequisites that are **systematically different** from other video models:
1. Must sign the "Asset Library User Agreement" (manual in the dashboard, no API);
2. Image-to-video assets must go through the asset library, passed as `asset://<ASSET_ID>`;
3. Text-to-video prompt must be placed in `media:[{type:text, text:...}]` — the server **does not recognize a top-level `prompt` field**.

So it is promoted to a standalone type `--type seedance` rather than patched inside `video`. The `auto` candidate for ordinary `video` already excludes it.

### 10.2 Agreement signing flow (local flag + error fallback)

PT has **no** API endpoint to "query whether the agreement is signed", so the skill approximates with a **local flag + error fallback**:

- When unflagged, before generation it prints a 4-step "jump to PT onboarding" guide (**does not block** submission);
- After you sign, run `python pt_video.py seedance enable` to flag as signed, and the guide stops showing;
- If submission is still rejected by the server as "unsigned" (keyword hit), the skill auto-`reset`s the flag and shows the guide again;
- On first successful generation it auto-marks as signed.

`seedance status` now also does a **best-effort server-side check**: if `POWERTOKENS_API_KEY` is set, it calls `GET /v1/asset/agreement/status` and reports `agreement_accepted_server` / `asset_library_enabled_server` alongside the local flag. If the key is not set, it skips the server check.

```bash
python pt_video.py seedance status    # view current flag (+ best-effort server check)
python pt_video.py seedance enable    # flag after signing
python pt_video.py seedance disable   # clear the flag
```

### 10.3 Text-to-video vs image-to-video (clarified by testing)

- **Text-to-video needs only `-p`, no media at all**: on submission the engine auto-places the prompt into `media:[{type:text}]`.
- **Image-to-video** (first frame / reference image) needs assets, and the asset must be uploaded via the asset library and passed as `asset://<ASSET_ID>`.

> ⚠️ **Correction of an old belief**: some network/early docs claimed "BytePlus-family text-to-video must carry media" — that was a **misjudgment**. It was triggered by using a fake `asset://` placeholder (`BytePlus video models only accept media`); tested with a real Key, plain-prompt text-to-video proceeds normally to the charge check (returns `pre-consume quota failed` when balance is insufficient).

### 10.4 Asset upload loop (`upload`, free)

Image-to-video must use `asset://` assets. Get them in two steps:

```bash
# 1) upload a local image (asset library requires width 300–6000px), free, returns asset://<id>
python pt_video.py upload -f ./first_frame.png
# 2) use the asset id as first frame
python pt_video.py generate --type seedance -m seedance-1-5-pro-251215 -p "slow camera push-in" --first-frame asset://<id>
```

> Under the Seedance type, media like `--first-frame` **only accepts `asset://`**; passing a public `http(s)` URL is rejected at pre-validation (to avoid bypassing the agreement).

---

## 11. Cross-account asset sharing & security notes

**Tested conclusion (2026-10-27, real dual accounts)**: PowerTokens `asset://` asset ids are **cross-account resolvable shared references** — the read path does no account isolation.

- An `asset://xxx` uploaded under account A can be used directly as a first frame under **account B's Key** to generate video; the task runs to completion and the cost is charged to account B.
- **Security implication**: do not put confidential / PII content into an asset and expect "account isolation" — anyone who knows the asset id can reference it under their own Key to generate (as long as pre-deduction passes). **Treat the asset id as an "unguessable but shareable" token.**
- **Product opportunity**: this also means "upload once, reuse across people/multiple accounts" is a natively supported platform capability, usable directly in collaborative products.

---

## 12. Output & Download

- **Video**: after polling succeeds, defaults to downloading to `out/<task_id>.mp4` and printing the link; `--no-download` returns the link only.
- **Image**: after sync returns, defaults to downloading to `out/<model>_<n>.png` (multiple images get `_1`/`_2` suffixes automatically); `--no-download` returns `data[].url` only.
- **Image URL validity**: `data[].url` is a temporary signed address (~24 hours) — download or re-host promptly.
- All generation artifacts land in the repo's `out/` directory (already in `.gitignore`, won't be committed).

---

## 13. Integration A: Agent Skills

The repo root **is itself an Agent Skill folder** (contains `SKILL.md`) — copy it into the agent's skills directory to be read.

| Agent | skills directory |
|---|---|
| Claude Code | `~/.claude/skills/powertokens-kit/` |
| Cursor | `<project>/.cursor/skills/powertokens-kit/` or user-level skills directory |
| GitHub Copilot | in-project `.github/skills/` or per Copilot skills docs |
| Codex (OpenAI) | `~/.codex/skills/powertokens-kit/` |
| Trae / Zed / Kiro / Junie / Roo Code | respective skills directory (see each tool's docs) |

```bash
# Example: Claude Code
mkdir -p ~/.claude/skills
cp -r . ~/.claude/skills/powertokens-kit
```

Directory structure (the repo root is the skill folder):

```
powertokens-kit/
├── SKILL.md          # Agent Skills manifest (name + description + instructions)
├── pt_video.py       # engine (direct official REST, zero deps)
├── mcp_server.py     # MCP server (optional)
├── LICENSE
├── README.md
├── README.zh-CN.md   # Chinese version of this document
├── pyproject.toml
└── docs/
    ├── publish.md          # registry submission checklist
    ├── publish.zh-CN.md    # Chinese version
    └── pitfall-archive.md  # pitfall archive
```

`SKILL.md`'s `description` uses tool-agnostic trigger phrases (e.g. "generate a video with PowerTokens") with no single-agent-specific wording, so it is universal across 40+ agents.

---

## 14. Integration B: MCP server

Only the MCP server needs the `mcp` dependency (see §7 Method 2). Run:

```bash
python mcp_server.py           # stdio; the agent client launches it automatically
```

Add this to each MCP client's config (replace the path with your absolute path, and include the Key env var):

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

Client config entry points:

| Client | Entry |
|---|---|
| Claude Desktop | Settings → Developer → Edit Config (claude_desktop_config.json) |
| Claude Code | `claude mcp add powertokens-kit -- python /abs/path/mcp_server.py` |
| Cursor | Settings → MCP → Add |
| Trae | Settings → MCP → Add |
| VS Code (Copilot) | Command Palette → MCP: Add Server |
| Windsurf / Cline / Zed | respective MCP config panel / JSON |

### MCP tools exposed

| Tool | Description |
|---|---|
| `check_key` | check whether the API Key is ready |
| `list_models(kind)` | list models + pricing + params; `kind=all\|video\|image\|seedance` |
| `estimate_cost(model, duration_s, resolution)` | estimate one generation's cost (USD) |
| `generate_video(prompt, model, duration_s, resolution, output, timeout_s)` | generate video (async auto-poll) |
| `generate_image(prompt, model, aspect_ratio, n, output)` | generate image (sync) |
| `generate_seedance(prompt, model, duration_s, resolution, first_frame, output, timeout_s)` | generate Seedance video (standalone type) |
| `query_balance()` | **read account balance (by Key)** — `GET /v1/billing/balance` |
| `query_usage(start_date, end_date)` | **read billing summary by model type** — `GET /v1/billing/usage` |
| `query_logs()` | **read consumption records** — `GET /v1/logs/query` |
| `query_agreement()` | **read asset-library agreement status** — `GET /v1/asset/agreement/status` |

> Note: `upload` (asset upload) is currently CLI-only and not exposed as an MCP tool; the `asset://` needed for Seedance image-to-video can be obtained first via CLI `upload`, then passed to `generate_seedance`'s `first_frame`. The four `query_*` tools are read-only and never generate or deduct credits.

---

## 15. Pricing & cost estimation

- Video is estimated from PT's **pre-deduction unit price**; coefficients: duration is linear (5s→10s = ×2), 1080p = 720p ×2.
  Example: `seedance-1-5-pro-251215` 5s/720p = $0.247 → 10s/720p = $0.494 → 5s/1080p = $0.494.
- Images are billed per image; PT has not opened a unit-price API, marked `n/a` in the table — refer to the dashboard.
- The `estimate` subcommand only estimates, **does not charge**, and does not enter the generation flow.
- PT quota is counted in **USD**; before generation the server does a `pre-consume quota failed` pre-deduction check — **no charge if the check fails**.

---

## 16. Direct API reference

Generation endpoints base: `https://api.powertokens.ai`, auth: `Authorization: Bearer <POWERTOKENS_API_KEY>`.

| Purpose | Method + path | Key points |
|---|---|---|
| Submit video | `POST /v1/videos` | body: `model` / `prompt` (top-level, for ordinary video; Seedance uses `media:[{type:text}]`) / `size`(720P) / `ratio` / `seconds`(**string**) / `generate_audio` / `media[]` |
| Poll video | `GET /v1/videos/{task_id}` | after status `succeeded`/`completed`, take `metadata.url` |
| Text-to-image | `POST /v1/images/generations` | OpenAI-style, sync returns `data[].url` (**with `/generations` suffix**) |
| List available models | `GET /v1/models` | returns the model list callable by the current Key (for auto candidate / pre-check) |
| Upload asset | `POST powertokens.ai/api/.../upload` | async: returns `task_id` → poll `get-asset-id` for `asset_id` → reference `asset://<id>` |

**Account / billing / asset-library endpoints — different host & auth**: the four read-only commands in §9 (`balance`, `usage`, `logs`, `agreement`) hit a **separate host `https://powertokens.ai/api`** (the dashboard / asset backend), **not** `api.powertokens.ai`, and use **bare-key auth** (`Authorization: <key>`, no `Bearer`) — the same domain and auth style as asset upload.

| Purpose | Method + path (host `powertokens.ai/api`) | Key points |
|---|---|---|
| Account balance (by Key) | `GET /v1/billing/balance` | returns balance / currency `credits` / cached flag / key_id |
| Billing summary by model | `GET /v1/billing/usage` | optional `start_date` / `end_date` (YYYY-MM-DD); per-model `total_cost` + grand total |
| Consumption records | `GET /v1/logs/query` | consume_id / request_id / task_id / endpoint / model / status / quota / usage / billing |
| Asset-library agreement status | `GET /v1/asset/agreement/status` | account_id / agreement_accepted / asset_library_enabled |

> ⚠️ **Not verified on real hardware**: the sandbox has no usable Key. If these endpoints return 401/403, the `powertokens.ai/api` host may require a web-login token rather than the API Key — verify with a real Key.

Common errors:
- `cannot unmarshal number into ... seconds of type string`: the server's `seconds` is `enum<string>` and must be passed as a string (the engine handles this).
- `pre-consume quota failed`: insufficient balance, pre-deduction check failed, **no charge**.
- `BytePlus video models only accept media`: mostly caused by an invalid/fake `asset://` (see §10.3).
- `404` on `/v1/images`: the correct path is `/v1/images/generations` with the `/generations` suffix.

---

## 17. FAQ / Troubleshooting

### 17.1 PT HTTP status codes (401 / 402 / 403)

PT assigns specific semantics to these status codes. Our CLI / MCP / gateway surface the original status and PT's message on stderr; the CLI maps auth failures to exit code 2 and everything else to exit code 1.

| Status | Meaning | Typical trigger |
|--------|---------|-----------------|
| `401 Unauthorized` | API Key invalid / expired / missing | Bad or absent key |
| `402 Payment Required` | Insufficient balance / quota | Generation when credits are exhausted |
| `403 Forbidden` | No permission / model delisted | Key lacks access, or the model was removed |

Notes:
- The read-only account / billing commands (`balance` / `usage` / `logs` / `agreement`, and the gateway `/account/*` routes) never return `402` — they only query.
- A `401` / `403` from the `https://powertokens.ai/api` host may mean the host expects a **web-login token** rather than the API Key — verify with a real Key (not verified in the sandbox).

**Q: Video generation stuck at some status?**
A: Video is generated asynchronously; real render time is 1–3 minutes. The script polls every 10s with a default total cap of 600s; raise `--timeout` if needed. If it stays `unknown` for a long time, it's likely server-side queuing — you can later re-check with `GET /v1/videos/{id}` using the `task_id` (a `status` subcommand will be added in a later version).

**Q: Image URL won't open?**
A: `data[].url` is a temporary signed address (~24 hours). Download promptly (the default behavior, or use `curl` manually).

**Q: Seedance says I need to sign the agreement?**
A: Go to <https://powertokens.ai/assets-library?tab=1> to sign manually, then `python pt_video.py seedance enable`.

**Q: Why didn't my `auto` pick Seedance?**
A: Seedance is a standalone type; the `auto` for ordinary `video` does not include it — use explicit `--type seedance`.

**Q: Can I use the same asset id across accounts?**
A: Yes (see §11) — asset ids are cross-account resolvable, but mind the security implication.

**Q: MCP server fails to start, missing `mcp`?**
A: `pip install -e ".[mcp]"` or `pip install "mcp[cli]>=1.2"`.

**Q: The `balance`/`usage`/`logs`/`agreement` commands return 401/403?**
A: These hit the `powertokens.ai/api` host with bare-key auth (see §16). Confirm you're passing the right Key via `-k` or `POWERTOKENS_API_KEY`; if 401/403 persists, the host may need a web-login token instead of the API Key.

---

## 18. Known limitations & roadmap

- **Polling circuit breaker**: 429 backoff / 5xx circuit breaking is a known to-do (see `docs/pitfall-archive.md` A.02/A.03).
- **`status` subcommand**: re-checking status by `task_id` alone is not implemented.
- **Image unit price**: PT has not opened a unit-price API, `estimate` returns `n/a` for images.
- **Seedance agreement has no API signing**: only manual dashboard signing + local flag approximation.
- **Result video direct download**: some result URLs may return 403 on direct R2 access from this machine (bucket hotlink/access policy); the server-side generation task is unaffected; the download-link policy needs further confirmation.
- **Account/billing endpoints not verified on real hardware**: see §16 caveat.

---

## 19. Contributing

Issues / PRs welcome. Keep the "engine single source of truth" principle: CLI / SKILL / MCP all share `pt_video.py`; do not re-implement generation logic in MCP or SKILL. New pitfalls should be synced to `docs/pitfall-archive.md`.

---

## 20. License

[MIT](LICENSE)

---

## 21. Changelog

- **0.5.0** — Add **read-only account/billing commands**: CLI `balance` / `usage` / `logs` / `agreement` and matching MCP tools (`query_balance` / `query_usage` / `query_logs` / `query_agreement`); `seedance status` now best-effort checks server-side agreement; document the `powertokens.ai/api` bare-key domain for account/billing/asset endpoints.
- **0.4.1** — Correct the misjudgment that "Seedance text-to-video must carry media" (tested: plain prompt works); add the cross-account asset-sharing finding (A.14); `upload` asset-upload loop; docs aligned.
- **0.4.0** — Split Seedance into a standalone type (`image`/`video`/`seedance` parallel); "local flag + error fallback" onboarding flow; `force_asset` media validation; MCP `generate_seedance` tool.
- **0.3.0** — Add image generation (sync `/v1/images/generations`); REST direct replaces CLI.
- **0.2.0** — Async video submit + polling; `auto` model selection; pricing/param query.
- **0.1.0** — Initial engine skeleton.
