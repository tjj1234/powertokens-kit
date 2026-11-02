# PT Gateway — Unified PT Media Capability Layer (core library + HTTP service + thin MCP shell)

> 📘 This document is in English. [中文文档](README.zh-CN.md)

Consolidates **all** of PowerTokens' generation capabilities (image / video / seedance / audio / chat, including a capability profile for every model) into one unified interface: an agent calls `list_capabilities` to get every model's abilities **without consulting docs**; it calls `media_generate` as a single entry point and the gateway routes by kind automatically (image / audio / chat return synchronously, video / seedance return a task_id and then poll).

> Two confirmed platform facts (2026-10-30):
> 1. `GET /v1/models` returns PT's **full** model list (identical for every key), not a "this-key permission view";
>    so the gateway does no permission gating — when this key lacks permission, PT's server errors and is passed through transparently.
> 2. PT **provides no** async task-cancel endpoint, so this gateway implements no cancel (no `/cancel` over HTTP, no cancel tool exposed over MCP).

> Key premise correction: PT's substrate is OneAPI transparent routing — **all** video / Seedance models already share
> `POST /v1/videos` + `GET /v1/videos/{id}`; there is no "separate interface per vendor". What this service normalizes is
> the **request-body construction differences** and the **result-field location differences**, plus a **capability catalog** and **automatic model discovery / on/off-board sync**.

---

## Table of Contents

1. [Architecture](#1-architecture)
2. [Interface Contract](#2-interface-contract)
3. [Normalized State Machine](#3-normalized-state-machine)
4. [Model Registry & Auto-Update](#4-model-registry--auto-update)
5. [Startup](#5-startup)
6. [Relationship to the Existing CLI](#6-relationship-to-the-existing-cli)
7. [Known Gaps](#7-known-gaps)
8. [Manual Trigger + Scheduled Sync](#8-manual-trigger--scheduled-sync)
9. [Unified Media Capability Catalog (catalog.json)](#9-unified-media-capability-catalog-catalogjson)
10. [Testing (three tiers: unit / contract / live)](#10-testing-three-tiers-unit--contract--live)
11. [Account / Billing Read-Only Query](#11-account--billing-read-only-query)

---

## 1. Architecture

```
Caller (any agent / web page / script)
        │  POST /generate │ POST /submit │ GET /status │ POST /wait │ POST /sync │ GET /models │ GET /capabilities
        ▼
┌──────────────────────── pt-gateway (stdlib http.server, zero deps) ────────────────────────┐
│  TaskGateway                                                                                    │
│   ├─ submit(model,...)  → build_submit_body(kind adapt) → POST /v1/videos → {task_id}       │
│   ├─ get_status(tid)    → GET /v1/videos/{id} → normalize_status()                          │
│   ├─ wait(tid,timeout)  → reuse engine poll_task to poll to terminal → normalize             │
│   └─ chat/audio        → sync direct return (POST /v1/chat/completions, POST /v1/audio/speech)│
│  ModelRegistry (registry.json)                                                                  │
│   └─ sync(key): GET /v1/models (PT full list) → diff on/off-board; no permission gating     │
└───────────────────────────────┬──────────────────────────────────────────────────────────┘
                                 │  Bearer <POWERTOKENS_API_KEY>
                                 ▼
                   api.powertokens.ai  (/v1/videos, /v1/models)
```

`pt_gateway.py` reuses the same-repo `pt_video.py`'s REST primitives (`_api_call` / endpoint constants / model recognition),
**without depending on CLI subcommands** — it is a standalone module that merely borrows the validated transport layer.

---

## 2. Interface Contract

Service address: `http://<host>:8787` (port from `PT_GATEWAY_PORT`, default 8787). The Key comes from the
`POWERTOKENS_API_KEY` environment variable or `~/.config/powertokens-kit/config.json` (shared with the CLI;
the old path `~/.config/powertokens-video/config.json` is auto-fallback-compatible).

### GET /health
```json
{ "ok": true, "service": "pt-gateway" }
```

### GET /capabilities?kind=<image|video|seedance|audio|chat>
Capability catalog (agents query this instead of reading docs): `kinds` (per-type execution method / endpoint / prerequisites) + `models`
(per model: kind, price, max duration, resolution, input modes), overlaid with the registry's live `registry_enabled` / `delisted` status.
```json
{
  "kinds": {
    "video": { "execution": "async", "submit": "/v1/videos", "poll": "/v1/videos/{task_id}" },
    "audio": { "execution": "sync", "submit": "/v1/audio/speech",
               "poll": null, "vendor": "OpenAI-compatible (MiniMax TTS)",
               "capabilities_observed": ["TTS (voice synthesis)", "voice selection (voice required)"] }
  },
  "models": {
    "seedance-1-5-pro-251215": { "kind": "seedance", "price_usd_5s_720p": 0.247,
                                 "max_duration_s": 10, "resolutions": ["720p","1080p"],
                                 "modes": ["text","image(first-frame)","start-end","video+audio-ref"],
                                 "registry_enabled": true, "delisted": false }
  }
}
```

### POST /generate (unified media generation entry point)
Request body:
```json
{
  "kind": "video",          // optional; can be omitted when a specific model ID is given (auto-inferred)
  "model": "auto",          // auto = auto-select by registry priority → catalog candidate; or a specific model ID
  "prompt": "a cat walking on the grass",
  "aspect_ratio": "16:9",   // image only
  "n": 1,                   // image only
  "duration": 5, "resolution": "720p", "ratio": "16:9",   // video/seedance only
  "first_frame": "asset://<id>", "ref_image": "", "ref_video": "",  // optional
  "wait": false, "timeout": 600
}
```
Response branches by kind:
- `image` → `{"ok":true,"kind":"image","execution":"sync","model":"...","urls":["..."]}` (sync direct return)
- `video` / `seedance` → `{"task_id":"...","kind":"...","execution":"async","model":"..."}` (query via /status)
- `audio` → `{"ok":true,"kind":"audio","execution":"sync","voice":"alloy","url":"..."}` (sync generation, POST `/v1/audio/speech`; **`voice` required**)
- `chat` → `{"ok":true,"kind":"chat","execution":"sync","text":"..."}` (sync generation, POST /v1/chat/completions)

### POST /submit
Request body:
```json
{
  "model": "seedance-1-5-pro-251215",   // required; any video/seedance model ID
  "prompt": "a cat walking on the grass", // required for text-to-video; can be left empty for image-to-video
  "duration": 5,                          // seconds, 2-30
  "resolution": "720p",                   // 480P/720P/1080P
  "ratio": "16:9",
  "first_frame": "asset://<id>",          // optional, image-to-video (Seedance requires asset://)
  "ref_image": "https://...",             // optional
  "ref_video": "https://...",             // optional
  "no_audio": false,
  "no_prompt_extend": false,
  "watermark": false,
  "seed": 42,                             // optional
  "wait": false,                          // true → poll to terminal then return
  "timeout": 600                          // poll cap (seconds) when wait=true
}
```
Response (`wait=false`):
```json
{ "task_id": "abc123", "kind": "seedance" }
```
Response (`wait=true`): normalized status (see §3).

### GET /status?task_id=<id>
```json
{ "task_id": "abc123", "state": "RUNNING", "progress": 30, "url": null, "raw": { ...vendor original... } }
```

### POST /wait
```json
{ "task_id": "abc123", "timeout": 600 }
```
→ normalized terminal state (success carries `url`, failure carries `error`).

### POST /sync
Refresh the registry by pulling `GET /v1/models` with the current Key.
```json
{ "added": ["new-model-id"], "removed": ["delisted-model-id"], "total_enabled": 9, "last_synced": "2026-10-29T..." }
```

### GET /models?kind=video|seedance
Returns the enabled async models in the registry (lazy auto-sync on call).
```json
{ "kind": "seedance", "count": 3, "models": ["..."], "last_synced": "..." }
```

### Cancel (not provided)

PT has no async task-cancel endpoint (confirmed with the official team on 2026-10-30), so this gateway therefore **implements no cancel**:
there is no `/cancel` endpoint over HTTP, and MCP exposes no cancel tool. Once a task is submitted it can only be left to finish on its own.

### Account / billing read-only query (different host & auth — see §11)

Four read-only endpoints are also exposed over HTTP:

| Route | Method + path | Notes |
|---|---|---|
| Account balance (by Key) | `GET /account/balance` | returns balance / currency `credits` / key_id; wraps `account_query("balance")` |
| Billing summary by model | `GET /account/usage` | reads optional `start_date` / `end_date` query params (YYYY-MM-DD); wraps `account_query("usage", ...)` |
| Consumption records | `GET /account/logs` | wraps `account_query("logs")` |
| Asset-library agreement status | `GET /account/agreement` | wraps `account_query("agreement")` |

On `ApiError` these routes return **502** with `{"error":...,"status":...}`. These endpoints hit the **separate** `https://powertokens.ai/api` host (the dashboard / asset backend), **not** `api.powertokens.ai`, and use **bare-key auth** (`Authorization: <key>`, no `Bearer`) — the same domain and auth style as asset upload. They are **read-only**: they never generate and never deduct credits. See §11 for the full API reference and the verification caveat.

---

## 3. Normalized State Machine

`normalize_status()` maps each vendor's verb into a unified enum:

| Vendor raw status | Normalized |
|---|---|
| `succeeded` / `completed` | `SUCCEEDED` |
| `pending` / `queued` / `submitted` | `QUEUED` |
| `running` / `processing` / `in_progress` / `generating` | `RUNNING` |
| `failed` | `FAILED` |
| `cancelled` / `canceled` | `CANCELLED` |
| other | `UNKNOWN` |

The result URL is reconciled across `metadata.url` / `metadata.content_url` / `task.url`, unified into the `url` field.

---

## 4. Model Registry & Auto-Update

File: `~/.config/pt-gateway/registry.json` (override with `PT_GATEWAY_REGISTRY`; already gitignored).

schema:
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

Sync strategy (`ModelRegistry.sync`):

- **Model-list source of truth**: `GET /v1/models` returns PT's **full** model list (identical for every key, confirmed 2026-10-30).
  - Present in the list → `in_models=true`, `enabled=true`, `delisted=false`; newly appearing ones get `is_new=true`.
  - No longer present → `delisted=true`, `enabled=false` (treated as off-board).
- **No permission gating (fail-open)**: the gateway does not decide "can this key call model X" on the client side.
  If this key lacks permission for a model, **PT's server errors** and the gateway passes it through verbatim.
  (An earlier version did fail-closed gating based on the mistaken premise that `/v1/models` is a permission view; that was rolled back.)
- **Lazy auto-repull**: `auto_sync_if_stale()` best-effort re-pulls before `/models`, `/generate`, etc. calls when the registry is empty or older than `max_age` (default 3600s, aligned with PT's 1-hour cache); on failure it does not raise, falling back to the existing / built-in list. You can also explicitly `POST /sync` to force a refresh.

---

## 5. Startup

```bash
cd pt-gateway
python server.py                 # HTTP service, default 8787
python mcp.py                    # thin MCP shell (stdio; requires pip install "mcp[cli]>=1.2")
PT_GATEWAY_PORT=9000 python server.py
```

MCP client config example (mount the unified tools into Claude/CodeBuddy/Trae):
```json
{ "pt-gateway": { "command": "python", "args": ["<repo path>/pt-gateway/mcp.py"] } }
```
Three unified tools: `list_capabilities` (query capabilities instead of reading docs) / `media_generate` (unified generation entry point) /
`task_status` (query status, wait=true waits to terminal). MCP and HTTP share the same registry.json /
catalog.json, so the model catalog they see is always consistent.

Call examples (curl):
```bash
curl http://localhost:8787/health
curl -X POST http://localhost:8787/submit -H 'Content-Type: application/json' \
  -d '{"model":"seedance-1-5-pro-251215","prompt":"a cat walking on the grass","wait":false}'
curl "http://localhost:8787/status?task_id=abc123"
curl -X POST http://localhost:8787/sync
```

Use as a Python library directly:
```python
from pt_gateway import TaskGateway, ModelRegistry
gw = TaskGateway()                      # Key auto-read from env/config
tid = gw.submit("wan3.0-video", prompt="a cat", wait=False)["task_id"]
print(gw.wait(tid))
```

**MCP tools exposed:**

| Tool | Description |
|---|---|
| `list_capabilities(kind)` | query the capability catalog (instead of reading docs) |
| `media_generate(...)` | unified media generation entry point (image/audio/chat sync; video/seedance async) |
| `task_status(task_id, wait, timeout_s)` | query async task status (wait=true polls to terminal) |
| `account_query(action, start_date, end_date)` | **read-only** account / billing query — `action=balance\|usage\|logs\|agreement`; `usage` accepts optional `start_date`/`end_date` (YYYY-MM-DD). Wraps `pt_gateway.account_query`. Read-only, never generates or deducts credits. |

> Note: `account_query` is exposed as a fourth MCP tool. It hits the `powertokens.ai/api` host with bare-key auth (see §11). The three generation-related tools (`list_capabilities` / `media_generate` / `task_status`) are unchanged.

---

## 6. Relationship to the Existing CLI

`pt_video.py` (CLI + MCP) continues to handle **direct generation** (including image sync generation, Seedance agreement guidance, downloads, etc.).
`pt-gateway` is an **independently deployable task hub**: it extracts "submit / query status / poll / model catalog" into a service, suitable for multiple agents
or a front-end to call uniformly, avoiding every integrator re-implementing polling and field normalization.

## 7. Known Gaps (this round)

- **No cancel**: PT has no async task-cancel endpoint (confirmed with the official team), so this gateway implements no cancel — no `/cancel` over HTTP,
  no cancel tool exposed over MCP. Once an async task is submitted it can only be left to finish on its own.
- **Poll robustness**: the gateway side already covers `poll_task` (429 → prefer server `Retry-After`, fall back to exponential backoff when missing, fast-fail when `Retry-After` exceeds remaining timeout + 5 consecutive 5xx circuit-break + 4xx pass-through + timeout error);
  the engine `pt_video.py`'s `ApiError` now surfaces response headers for upper-layer parsing, but its own `poll_task` is still the old implementation (10s interval + 600s cap) — do not conflate the two.

---

## 8. Manual Trigger Switch + Scheduled Sync (auto-update at no extra cost)

The registry's "auto-discover newly on-boarded / auto-off-board delisted" is backstopped by the §4 diff. **But it only lazy-repulls when someone calls**;
if the service sits idle for a long time, the registry will not freshen on its own. Hence two explicit triggers are provided:

### 8.1 Manual trigger switch (two ways, pick one)

1. **Run straight from the terminal (no service needed)** — `pt_gateway.py` ships a `sync` subcommand:
   ```bash
   python pt_gateway.py sync        # pull /v1/models to refresh registry.json, print {added,removed,total_enabled,last_synced}
   python pt_gateway.py selfcheck   # state-normalization self-check (default, no Key needed)
   ```
   This command writes the **same** `~/.config/pt-gateway/registry.json` as the HTTP service, so a manual refresh is immediately visible to the service.

2. **In-service hot refresh** — send to the running gateway:
   ```bash
   curl -X POST http://localhost:8787/sync
   ```

### 8.2 Scheduled sync (recommended 12:00 + 19:00, zero-resource cost)

The sync action itself is just one `GET /v1/models` (returns a few KB of JSON); CPU is ≈0 when the service is idle,
**twice daily is completely unnoticed**. A WorkBuddy automation is already set up for the daily 12:00 / 19:00 jobs, essentially running on schedule:

```
# run at the repo root (swap python for your interpreter)
python pt-gateway/pt_gateway.py sync
```

- If you want **hourly** instead (also free), change the automation rrule to `FREQ=HOURLY;INTERVAL=1`.
- Prerequisite: the scheduled task needs `POWERTOKENS_API_KEY` available in the environment when it runs (same Key-source as the gateway).
  If the automation environment lacks that variable, the scheduled refresh is skipped — in that case use the 8.1 manual switch in a terminal that has the Key.

> Design trade-off: PT provides no webhook, so a "pull model" is used — lazy repull (≤1h expiry on call → refresh) +
> twice-daily active scheduling + manual switch complement each other, covering both "fresh during calls" and "not stale when idle".

---

## 9. Unified Media Capability Catalog (catalog.json) — the key to "agents don't look things up"

`catalog.json` (shipped with the repo, path overridable with `PT_GATEWAY_CATALOG`) is a **human-observed, fixed capability profile**:
PT's `GET /v1/models` returns only model IDs, **not** resolution/duration/mode/price fields — this catalog structures them,
and `list_capabilities` returns everything at once, so agents no longer read docs.

**Division of labor (relationship to ModelRegistry)**:

| | Maintained by | Manages what |
|---|---|---|
| ModelRegistry (registry.json) | **automatic**: sync() pulls /v1/models and diffs | on/off-board (enabled/delisted), is_new |
| Catalog (catalog.json) | **human**: add an entry after measurement/screenshot observation | capability fields (execution/resolution/duration/mode/price/prerequisites) |

The two are **merged** in `list_capabilities`: the catalog says "what it can do", the registry says "whether it's still on-board now".
New on-boarded models not yet in the catalog are still auto-enabled by the registry (capability fields show as missing — honest, not fabricated).

schema: top-level `kinds` (per-type execution/endpoint/prerequisites/capability observations) + `models` (per-model profile).
Audio (TTS) endpoint: **`POST /v1/audio/speech` (OpenAI-compatible, real-hardware tested 2026-10-30)** —
the previously assumed `/ali/...` was wrong (tested 404 / 500 `pricing rule not found`).
Request body `{model, input, voice}`; **`voice` required** (missing → `voice is required`),
usable OpenAI standard voices `alloy / echo / fable / onyx / nova / shimmer` (all 200 in testing).
Response `{data:{audio:<mp3 url>, status:2}}`, url at **`data.audio`**; with `extra_info` (duration/sample-rate/size/bitrate).
Text (chat) endpoint: `POST /v1/chat/completions` (OpenAI-compatible, sync returns `choices[0].message.content`),
`kinds.chat` is filled with the real endpoint, `media_generate(kind=chat)` sync returns `{execution:"sync", text}`;
you can pass multi-turn dialog with `messages`, or use only `prompt` as a single-turn user message (`system` prepended).

### 9.1 chat model capability labeling (multimodal / web search / thinking mode)

**Background**: when an agent picks a chat model it first needs to know "can it see images / can it web-search / is it a thinking model",
otherwise it can only guess blindly and waste tokens. Now every `kind=chat` entry carries structured `capabilities`:

```json
"capabilities": {
  "multimodal": {"vision": true, "audio_in": false, "video_in": false}, // vision/audio/video input: official §4 per-model list (official)
  "channel": "deepseek",            // which channel adapter this model goes through PT (ali/byteplus/zhipu/deepseek/minimax/mimo)
  "web_search": false,              // web search: official channel table (official)
  "web_search_param": null,         // the actual param to pass when enabling (null=unsupported)
  "thinking": true,                 // thinking/reasoning mode: official channel table (official)
  "thinking_param": "thinking:{\"type\":\"enabled\"|\"disabled\"}", // actual param when enabling
  "tools": true,                    // whether function calling is supported
  "streaming": true,                // whether SSE streaming is supported
  "_source": "official+verified"    // official=official docs / verified=real-hardware probe / both merged
}
```

**Source layering (key)**: PT's `GET /v1/models` **returns no capability fields**, so these never come from the API automatically.
- `web_search` / `thinking`: **authoritative official data**, from PT's "Model Capability Summary — websearch & thinking"
  (based on the gateway channel-adapter code + each channel's OpenAPI docs, as of 2026-11-02). Value meanings:
  - `true`: the channel has an explicit param entry (e.g. DeepSeek `thinking`, MiniMax `reasoning_split`)
  - `false`: neither the gateway nor the upstream has this capability (e.g. DeepSeek / MiniMax / mimo's web_search)
  - `"conditional"`: **the gateway does not inject it; the client must pass the param explicitly, and actual availability depends on the upstream whitelist**
    (e.g. ali's `enable_search`, BytePlus / Zhipu's `tools:[{type:web_search}]`, Zhipu / mimo's `thinking`)
  - `null`: no channel matched / unknown
- `multimodal` (incl. `vision` / `audio_in` / `video_in`): **authoritative official data**, from PT's "Model Capability Summary" (2026-11-02)
  **§4 per-model multimodal list** (based on the gateway channel-adapter code + each channel's OpenAPI docs).
  ⚠️ Probe experience: early `probe_chat_capabilities` real-hardware probing of `vision` sent only a `data:image/png;base64`
  single-image request, producing **false negatives** for channels like **ali-qwen3 / BytePlus-seed / zhipu-glm-5.3-flash / deepseek-flash-v3-2**
  (these channels don't accept that image format and return non-vision errors). So the official §4 list is authoritative,
  and the probe is only used as "double-confirmation when consistent with docs" (`official+verified`), not as a standalone conclusion.
  Also: the official docs explicitly state `seed-*` / `dola-seed-*` support **image/video** input, so these models get `video_in=true`.

**Official channel capability matrix (directly decides web_search / thinking values)**:

| Channel | Web Search | Thinking mode |
|---|---|---|
| Alibaba BaiLian (ali) | 🟡 `enable_search:true` (only some qwen models) | 🟡 `enable_thinking:true` (qwen3 series / qwq) |
| BytePlus | 🟡 gateway does not inject; manually `tools:[{type:web_search}]` | 🟢 `thinking` + `reasoning_effort` |
| Zhipu (zhipu_4v) | 🟡 manual `tools:[{type:web_search}]` (glm-4.7/5 series) | 🟡 `thinking` (passed through verbatim, some models documented) |
| DeepSeek | 🔴 unsupported | 🟢 `thinking:{enabled\|disabled}` |
| MiniMax | 🔴 unsupported | 🟢 `reasoning_split`(OpenAI) / `thinking`(Claude) |
| mimo | 🔴 unsupported | 🟡 pass-through `thinking` (await upstream test) |

(🟢=channel has explicit entry / 🟡=needs manual param and depends on upstream whitelist / 🔴=neither)

**Official per-model multimodal list (§4, decides vision / video_in values; covers 37 of 38 chat models)**:

| Channel | Multimodal conclusion |
|---|---|
| Alibaba BaiLian (ali) | `qwen3-max` / `qwen3.5~3.8` series / `deepseek-v3.2` / `deepseek-v4-pro-0813` ✅ image |
| BytePlus | `seed-*` / `dola-seed-*` ✅ image/video (`video_in=true`); `deepseek-v3-2-251201` / `deepseek-v4-1-flash-260910` / `gpt-oss-120b` ✅ image |
| Zhipu (zhipu_4v) | `glm-5-turbo/5/5.1/5.2/5.3` / `glm-4.7/4.7-flash/4.5-air` ❌ text only; only `glm-5.3-flash` ✅ image |
| DeepSeek | `deepseek-v4-pro` / `deepseek-v4-flash` / `deepseek-flash` ✅ image |
| MiniMax | `M2.5/M2.7/M3/M3.1-Flash` full series ✅ image |
| mimo | `mimo-v2.6-pro` / `mimo-v2.6-flash` ✅ image (`mimo-v2.5` series not in the list, not inferred) |

> Note: `qwen3-coder-plus` is not in the official per-model list, so by the "don't infer" principle `vision` stays `null` (await upstream test).
> The gateway passes multimodal **through verbatim, no whitelist**; actual availability depends on the upstream.

Reproduce / cross-check `vision` (the probe sends only one very short request, fast-fails on error, costs a few cents; note it may produce the false negatives above):
```bash
python pt_gateway.py probe-cap <model>            # single-model vision probe (auxiliary check, not authoritative)
python pt_gateway.py probe-cap --all --persist    # full probe (results deferred to official §4; docs override on conflict)
```

CLI quick lookup:
```bash
python pt_gateway.py capabilities            # print the full capability catalog
python pt_gateway.py capabilities seedance   # view only one type
python pt_gateway.py capabilities chat       # view all chat models and their capabilities (incl. web_search/thinking/param)
```

---

## 10. Testing (three tiers: unit / contract / live)

One test entry point only, `tests/run_tests.py` (zero external deps, Python 3.9+ stdlib; also pytest-compatible):

```bash
python tests/run_tests.py                    # offline tier: unit + contract, 0 cost, seconds
python tests/run_tests.py --only polling     # run only cases whose id contains the keyword
python tests/run_tests.py --live             # authorized run of the live tier (costs money, budget-guard protected)
python tests/run_tests.py --live --tier standard --max-usd 1.0
```

| Tier | File | Network | Cost | Duration | Covers |
|---|---|---|---|---|---|
| unit | `test_unit_*.py` | forbidden (offline guard fails immediately) | 0 | <1s | state normalization, request-body construction, poll backoff/circuit-break, registry diff, catalog data integrity, kind routing |
| contract | `test_contract_*.py` | 127.0.0.1 loopback only | 0 | <1s | HTTP routing/status-code mapping/CORS, MCP tool surface & param wiring |
| live | `test_live.py` | real PT API | per tier | minutes | all five kinds end-to-end + error paths + leak self-check |

**Three hard rules** (uniformly enforced by `tests/harness.py`, not by conscience):

1. **Offline tier must not touch the network**: `_api_call` / `submit_task` are stubbed guards; a missed stub fails red immediately instead of silently hitting the real API.
   Time is also taken over by `FakeClock`, so polling cases (429 backoff, 5xx circuit-break, timeout) don't really sleep and are fully deterministic.
2. **live must be explicitly authorized**: `PT_LIVE=1` **and** a key is obtainable, otherwise the whole tier is skipped; before paid steps run, the
   `Budget` budget guard must also pass — cumulative over `--max-usd` immediately SKIPs (rather not run than overspend).
3. **Reports must not carry secrets**: before writing to disk it scans for full keys and the `sk-xxxx` regex; a hit errors and refuses to write;
   `test_live.py` also has a case specifically validating that log masking works.

Report artifacts: `tests/reports/latest.md` (+ timestamped version and `.json`), with per-module stats, failure detail, and live cost.
Exit code 0/1, directly CI-friendly.

> Note: `e2e_report.md` is a historical record left by an early one-off smoke script, now superseded by this test suite;
> new reports should always look at `tests/reports/`.

---

## 11. Account / Billing Read-Only Query

The gateway already implements **read-only account / billing / asset-library query** capabilities, exposed three ways: a Python function, HTTP routes, and an MCP tool. All of them hit a **separate host** `https://powertokens.ai/api` (the dashboard / asset backend) — **not** `api.powertokens.ai` — and use **bare-key auth** (`Authorization: <key>`, no `Bearer`). This is the **same domain and auth style as asset upload** (`ASSET_UPLOAD` in the engine). They are strictly **READ-ONLY**: they never generate and never deduct credits.

### 11.1 Python function

`account_query(action, key=None, start_date=None, end_date=None)` in `pt_gateway.py`:

- `action` ∈ `balance` | `usage` | `logs` | `agreement`.
- The key resolves from the `key` argument or the `POWERTOKENS_API_KEY` environment variable; if no key is available it raises `ApiError 401`.
- Returns PT's raw JSON (with `code` / `data`).

### 11.2 HTTP routes (all GET, in `server.py`)

| Route | Backing call | Notes |
|---|---|---|
| `GET /account/balance` | `account_query("balance")` | returns balance / currency `credits` / key_id |
| `GET /account/usage` | `account_query("usage", start_date, end_date)` | reads `start_date` / `end_date` query params (YYYY-MM-DD) |
| `GET /account/logs` | `account_query("logs")` | consumption records |
| `GET /account/agreement` | `account_query("agreement")` | asset-library agreement status |

On `ApiError` they return **502** with `{"error":...,"status":...}`.

### 11.3 MCP tool

`account_query(action: str, start_date: str = "", end_date: str = "")` in `mcp.py`, wrapping the core function above. `usage` accepts optional `start_date` / `end_date`.

### 11.4 API reference (endpoints on host `powertokens.ai/api`)

### 11.5 PT HTTP status-code contract (401 / 402 / 403)

All PT endpoints (generation and the `powertokens.ai/api` account / billing host) use these codes with a fixed meaning. The gateway propagates `ApiError(status, message)` unchanged; HTTP routes return `502` only when an `ApiError` is raised locally.

| Status | Meaning | Typical trigger |
|--------|---------|-----------------|
| `401 Unauthorized` | API Key invalid / expired / missing | Bad or absent key |
| `402 Payment Required` | Insufficient balance / quota | Generation when credits are exhausted |
| `403 Forbidden` | No permission / model delisted | Key lacks access, or the model was removed |

- The `/account/*` routes are read-only and will not return `402`.
- A `401` / `403` from `powertokens.ai/api` may indicate the host wants a web-login token, not the API Key.

| Purpose | Method + path (host `powertokens.ai/api`) | Key points |
|---|---|---|
| Account balance (by Key) | `GET /v1/billing/balance` | returns balance / currency `credits` / cached flag / key_id |
| Billing summary by model | `GET /v1/billing/usage` | optional `start_date` / `end_date` (YYYY-MM-DD); per-model `total_cost` + grand total |
| Consumption records | `GET /v1/logs/query` | consume_id / request_id / task_id / endpoint / model / status / quota / usage / billing |
| Asset-library agreement status | `GET /v1/asset/agreement/status` | account_id / agreement_accepted / asset_library_enabled |

> ⚠️ **Not verified on real hardware in the sandbox (no usable Key).** If these endpoints return 401/403, the `powertokens.ai/api` host may require a **web-login token** rather than the API Key — verify with a real Key.
