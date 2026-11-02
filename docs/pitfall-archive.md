# Pitfall Archive · Full Record of PowerTokens Video Skill Development

> 📘 This document is in English. [中文文档](pitfall-archive.zh-CN.md)

> **Aligned version: powertokens-video `0.3.0` (text+image universal · direct official REST, zero CLI / zero Node deps)**
> Period: 2026-09-28 ~ 10-26 (REST direct connection + image routing + Seedance agreement → 0.4.0 split into standalone type)
> Engine: `pt_video.py` · Config: `~/.config/powertokens-video/config.json` (overridable via `PT_VIDEO_CONFIG`)
> Real renders: **1 video** (wan3.0-video · 480p · 5s · 2.1MB) + **1 image** (image-01 · 1:1 · 245KB)
> Unit tests: **0** (at 0.2.0) → **228 offline suites** (2026-11-02, `pt-gateway/tests/`, unit + contract tiers; live tier needs explicit `--live`)
> Interfaces: video `POST /v1/videos` submit + poll; image `POST /v1/images/generations` (sync, no poll)
>
> ⚠️ This archive is a **corrected version checked against 0.2.0 real code and measured results**. See the "Correction Notes" at the end for differences from the old version (powertokens-wan archive).
> **2026-10-30 addendum**: new pt-gateway submodule (cross-model unified async task gateway) pitfalls — **A.15–A.19** (PT cognition), **C.24–C.27** (local tools), **F.39** (collaboration flow); and corrected the B-tier "poll circuit-break not implemented" to **already landed in pt-gateway** (engine `pt_video.py` still old impl, don't conflate).
> **2026-11-02 addendum**: new **A.20–A.23** (Retry-After cross-module link break / 403 form of model delisting / chat web_search·thinking triple condition / multimodal probe family false negatives), **E.40** (PT pre-signed product URL triggers GitHub Push Protection), **H.4** (tiered live acceptance cost measured). And **corrected** the B-tier claim "429 honoring `Retry-After` already landed" — in fact it was always broken because the engine dropped response headers; fixed for real only on 2026-11-02 (see A.20).
> 📌 Project renamed **powertokens-kit** on 2026-11-02 (`0.5.0`), covering image / video / seedance / audio / chat; config path `~/.config/powertokens-kit/config.json` takes priority, auto-falling-back to the old `powertokens-video`. Historical paragraphs keep the original version names, as accurate records of the state at the time.

---

## ⚡ Seven most valuable general lessons

1. **Third-party gateway docs' enums cannot be trusted; you must measure the real response.** Success states `succeeded`/`completed` are written inconsistently; the error form for an invalid Key must be confirmed by hitting it once with a fake Key.
2. **Polling logic must explicitly handle "query failed" and "status unreadable".** 0.2.0 currently **has no circuit-break** (see B-tier known shortfall); long-video polling may silently spin when the gateway jitters.
3. **HTTP status-code semantics get overloaded.** 403 simultaneously means three different problems — "insufficient balance / no model permission / Key invalid" — and getting the order wrong leads the user into a dead end.
4. **Rate-limiting (429) and server errors (5xx) should not share a threshold.** 429 is temporary and likely recovers after backoff; 5xx is what should fail fast.
5. **Editing the same file in parallel is forbidden; use an atomic batch-replace script instead.** Concurrent writes to the same file silently drop writes (returns success but disk unchanged).
6. **Video/image endpoint shapes and sync/async differences cannot be assumed.** Image is `POST /v1/images/generations` sync returning `data[].url`; video is `POST /v1/videos` async polling task_id — guessing `/v1/images` blindly eats a 404 (see A.12). Third-party models' "agreement/whitelist/asset-format" prerequisites often can only be handled manually in the dashboard; the skill should "detect + guide" rather than "sign on behalf" (see A.13).
7. **"Error" ≠ "unsupported", and "docs say implemented" ≠ "link actually works".** The same capability often works one way and not another across channels / param formats; a single-point probe's negative conclusion is the least reliable (A.23); across modules, even if the upper layer is right and the lower layer didn't supply data, the feature is still dead (A.20). The cure for both: **let tests pierce the real call stack**, don't only test pure functions, don't only read doc claims.

---

## A. PT Gateway / API cognition class (docs untrustworthy)

This layer's shared trait: **official docs and the gateway's real behavior disagree, or status-code semantics are overloaded**. Only real measurement yields evidence.

### A.01 Success-state enum written inconsistently: `succeeded` / `completed` both possible
- **Symptom**: during long video generation the log stays at "queued", but the PT backend task finished long ago and the client is stuck.
- **Root cause**: the official submit page example writes `succeeded`, the query page example writes `completed` — different doc pages / different models write it differently, **no unified contract**.
- **Measured (0.2.0)**: our round of REST measurement (wan3.0-video 480p) returned the status chain `queued → in_progress(30%) → succeeded`, finally getting the video at `metadata.url` — **it returned `succeeded`, not `completed`**. The old archive claimed "gateway actually returns completed", opposite to our measurement; hereby corrected.
- **Fix (implemented)**: multi-path fallback — ① extract `metadata.url` → judge success (strongest signal, ignore status wording) ② `status` hits success word list (`succeeded/completed/finished/done/ready`) ③ `progress≥100`. `pt_video.py`'s `poll_task` has implemented ①② (both `succeeded`/`completed` recognized).
- **Lesson**: don't judge success by a single field alone. "Got the product URL" is a far stronger signal than "status field equals some word".

**Resolution (2026-11-02, PT official):** PT unified the success state to `completed`. Our polling already accepts **both** `succeeded` and `completed` (`pt_video.py` and `pt_gateway.TaskState.normalize`), so it is forward-compatible and also tolerates endpoints not yet migrated. No code change required.

### A.02 When query returns non-200 the old logic spins — ⚠️ 0.2.0 known shortfall (no circuit-break)
- **Symptom / root cause**: if `GET /v1/videos/{id}` repeatedly returns 429/500/502/503, the response body has no `status` → the status variable goes empty → matches neither success nor failure → keeps `sleep` polling, spinning all the way to `POLL_TIMEOUT` (currently 600s). The log looks "all normal, just queued".
- **Current state**: 0.2.0's `poll_task` only does "timeout throws + suggests later manual `GET` recheck", **no** 429 backoff, 5xx-count circuit-break, or unrecognized-status-count circuit-break. This is a known shortfall; long-video polling may silently spin when the gateway jitters.
- **Fix (roadmap, not yet implemented)**: add triple circuit-break — exit after 8 consecutive 5xx failures; count `status` unreadable / unrecognized 40 times → circuit-break and print raw response; 429 counted separately + exponential backoff (interval doubles, cap 60s).
- **Lesson**: "silently continue" is the most dangerous default branch of polling logic. Any "status unreadable" must be explicitly counted + circuit-broken.

### A.03 429 and 5xx should not share a strict threshold (same as A.02 roadmap)
- **Symptom (reasoning)**: when fixing A.02, if 429 and 5xx are counted into the same strict threshold, you'll wrongly kill tasks mid normal generation (long-video polling dozens of times easily hits rate limits).
- **Fix (roadmap)**: count 429 separately, relax threshold to 30 + exponential backoff; use a strict threshold for 5xx to fail fast; reset both counters on successful query.
- **Lesson**: circuit-break strategy for recoverable vs unrecoverable errors must be separated.

### A.04 403 semantics overloaded: insufficient balance / no permission / Key invalid, pick one (real, we hit it)
- **Symptom**: on insufficient balance it reports 403, with text like `token quota is not enough ... need quota: ＄0.200000` (our 720p submit triggered this round).
- **Root cause**: the same 403 carries three semantics: insufficient balance / no model permission / Key invalid. Wrong ordering misreports "model permission not opened" as "Key invalid".
- **Fix (current impl)**: 0.2.0 **does no fine classification** (`classify_rest` not implemented), uniformly goes `[error] generation failed: {message}` passing the gateway's raw text **through** to the user / agent — at least it doesn't mislead direction. Suggest later reordering by "specific feature first (`token_invalid` / `quota` / `not permitted`) then status-code fallback".
- **Lesson**: the value of error classification isn't the classification itself, but pointing the user to the correct fix path; the current impl relies on the user / agent reading the raw text.

**Resolution (2026-11-02, PT official):** the overload is now resolved into a clean three-way split that clients can branch on:
- `401 Unauthorized` → API Key invalid / expired / missing
- `402 Payment Required` → insufficient balance / quota (generation only)
- `403 Forbidden` → no permission / model delisted

Our code already propagates these statuses verbatim (no remapping), so no code change was required. The read-only account / billing commands never trigger `402`.

### A.05 Error form for invalid Key — ⚠️ awaiting measurement (0.2.0 unverified)
- **Note**: this round we only used a **real Key**, never hit REST endpoints with a fake Key, so we can't confirm the rumor "HTTP 500 + `token_invalid`". Auth uses `Authorization: Bearer <token>`, measured working.
- **Suggestion**: the first thing when connecting a new gateway — hit `POST /v1/videos` once with a fake Key, record the real return (status code + body), then add to the classifier. The old archive wrote this as "confirmed fact"; here downgraded to "awaiting measurement".

### A.06 `GET /v1/models` returns "models this token is authorized for", not the global catalog
- **Symptom**: a colleague's Key with no model restriction renders fine; once a model whitelist is opened in the backend (even with wan3 checked), generation reports "key has no permission".
- **Root cause**: `/v1/models` reflects this Key's available models (whitelist is **exact string match**). The backend checkbox display name (e.g. "Wan 3.0") easily disagrees with the real ID (`wan3.0-video`).
- **Measured (0.2.0)**: our measurement of a new Key returned **32 video models** (caliber: video type only; would be more if all types included, **caliber must be noted**). The old archive wrote "85" without noting caliber; hereby corrected to the measured value.
- **Fix (implemented)**: pre-submit `precheck` — explicit `-m` first compared against `/v1/models`; if absent, `exit 3` within 1s, no generation, no charge, with similar candidates + case correction (`pt_video.py`'s `precheck_explicit_model` already implemented). Note: the current impl is "model absent → error exit", **not** "skip this Key and try the next".

### A.07 "PT has a 900s hard cap" is a misjudgment — async generation has no server-side cap (real, user-corrected)
- **Symptom**: a colleague's screenshot ran to 908s failure, inferred "PT has a 900s task cap".
- **Root cause**: 908s is **the moment the client polling gave up**, not a PT limit. User explicitly corrected: generation is async, no 900s cap.
- **Fix (implemented)**: `pt_video.py` submit HTTP timeout **120s** (per user), poll interval **10s** (per user), overall poll cap **600s** (only to prevent infinite polling, **not a PT limit**; real renders ~3 min, a 120s cap would fail, so default 600s, adjustable via `--timeout`). Copy unified as "local polling guard timeout, server-side no hard cap".
- **Lesson**: seeing a timeout number, first tell whose timeout it is. Client guard / gateway / upstream model each have their own timeouts.

### A.08 "No available channel" is mostly transient failure, not the user's Key problem
- **Symptom**: generation reports "no available channel / model not specified / this token not permitted to use the model", looking like a user config problem.
- **Root cause**: two gates — ① token layer (does Key bind a model whitelist) → reports "no model permission"; ② channel-routing layer (can the gateway find an upstream "supporting this model + has balance + not disabled") → reports "no available channel". The vast majority is ②, a transient failure.
- **Fix (suggestion)**: first `models` to confirm the model is in the available list, then reproduce with the exact same command. Message must clearly tell the user "this is not your Key problem", don't let the user fiddle with config for nothing.

### A.09 No balance-query interface; consumption can only be estimated (real, `estimate` implemented)
- **Symptom**: wanting to report "how much this run cost", the success path doesn't return the consumed amount.
- **Root cause**: PT bills by USD wallet, video submit pre-deducts, but `/v1/dashboard/billing/*` all 404, no public programmable balance-check interface.
- **Fix (implemented)**: estimate using a **real-measured pre-deduction unit-price table** and clearly label "estimate". The table is built at zero cost — on insufficient balance the submit always 403s but **doesn't charge**, and the 403 returns that model's `required pre-consume` real pre-deduction amount. Coefficients: duration linear, 1080p = 720p ×2. Unknown models print "unknown", never fabricate numbers (`pt_video.py`'s `estimate_cost` already implemented).
- **Lesson**: when you can't get the true value, prefer labeling "estimate / unknown" over making it up.

### A.10 `media` only accepts http(s) URLs; `@xxx` placeholder not recognized
- **Symptom**: ① user wants to pass a local screenshot / image directly to generate video; ② writes `@male_lead` in the prompt to lock a character, but gets a generic figure.
- **Root cause**: ① REST interface doesn't accept local file upload, `media[].url` must be a publicly reachable address; ② wan3.0 doesn't recognize the `@xxx` placeholder syntax.
- **Fix (implemented)**: local paths error out directly with the reason (front-guard, avoid failing after submission and wasting time); lock character via `--ref-image` / `--first-frame` etc. `media` array types. Note `reference_*` and `first_frame`/`last_frame` are mutually exclusive, can't mix.
- **Lesson**: param validation must be front-loaded before submission; model-unrecognized syntax must be told of alternatives in advance.

### A.11【New · Fatal】Request-body enum fields must be passed as strings (we actually hit this round, old archive missed it)
- **Symptom**: first submit errors directly: `json: cannot unmarshal number into Go struct field .seconds of type string`.
- **Root cause**: `seconds` is `enum<string>`, and `size` (480P/720P/1080P), `ratio` (adaptive/16:9/...), `model` are also **string enums**. Passing integers / lowercase gets rejected by the server.
- **Fix (implemented)**: on `pt_video.py` submit `"seconds": str(args.duration)`, `"size": args.resolution.upper()`, `ratio` / `model` are all strings, then `json.dumps`. All enum fields uniformly converted to `str`.
- **Lesson**: before REST submit, first use `models --json` or official docs to confirm each field's type — **for enum fields, prefer explicitly converting to string**; this is the most easily hit, most reproducible pitfall.

**Status (2026-11-02):** verified compliant — the Seedance submit body sends `"seconds": str(args.duration)` and all enum fields (`model`, `size`, `ratio`) as strings; no int is sent on the wire. Seedance's `seconds` accepts both int and string, so the string form is safe on both sides. No code change required.

### A.12【New · 0.3.0】Image endpoint is not `/v1/images`, it's `/v1/images/generations` (sync, no poll)
- **Symptom**: guessing by analogy to the video endpoint `POST /v1/images` (no `/generations`) returns `404 Invalid URL (POST /v1/images)`; the doc page writes `POST /v1/images`, but the real gateway doesn't recognize it (docs vs reality disagree, same old A-tier problem).
- **Root cause**: PT's image interface is OpenAI-style, with the `/generations` suffix: `POST /v1/images/generations`. And image is **sync return** — response body `data[].url` (array, can be multiple), get the link directly, **no polling needed** (completely different from video's async task_id polling).
- **Measured (0.3.0)**: `POST /v1/images/generations` with `{"model":"image-01","prompt":"x"}` returned HTTP 200 + `{"data":[{"url":"http://...aliyuncs.com/...jpeg?Expires=...&Signature=..."}],"metadata":{"success_count":1,...}}` — actually generated and downloaded to `out/test_image.png` (245KB).
- **Fix (implemented)**: `generate_image` goes to `IMAGE_ENDPOINT="/v1/images/generations"`, sync takes `data[].url` and downloads each (extension .png), no polling. Image fields use `aspect_ratio`(enum string)/`n`(1-9)/`seed`, **no `seconds`** (image has no duration).
- **Lesson**: video/image endpoint shapes and sync/async differences are huge, absolutely can't share one submit+polling logic; measure the endpoint before writing code (we guessed `/v1/images` and ate a 404).

### A.13【New · 0.3.0 → 0.4.0/0.4.1】Seedance split into standalone type: ordinary video doesn't include it + asset-library asset:// + local-flag onboarding flow
- **Symptom (0.3.0 misjudgment, corrected)**: early use of `dreamina-seedance-2-0-260128` text-to-video, passing a **fake** `asset://` placeholder,
  reported `BytePlus video models only accept media`, so wrongly thought "Seedance text-to-video must carry media, plain prompt won't work".
  **This is a misjudgment**: the root cause of the error is the asset id is fake, not missing media.
- **Root cause (clarified by real-key measurement, 2026-10-26)**: ① the whole Seedance family (1.0/1.5/2.0/2.5, not just 2.0) must first sign
  the "Asset Library User Agreement", but **only manually in the dashboard** (`https://powertokens.ai/assets-library?tab=1`),
  **no API endpoint to sign programmatically**;
  ② image-to-video assets must go through the asset library upload to get `asset://<ASSET_ID>` (not ordinary http URL); ③ **text-to-video only needs a prompt,
  no media at all** — on submit the skill puts the prompt into `media:[{type:text, text:...}]` (server doesn't recognize top-level `prompt`);
  ④ it has three unique prerequisite gates (sign agreement / assets via asset library / text-to-video via media-text not top-level prompt), systematically
  different from ordinary video models — folding it into the `video` type isn't reasonable, so it's promoted to a standalone type.
- **Fix (0.4.0/0.4.1 refactor, implemented)**: split Seedance into a **third standalone type** `seedance` (alongside `image`/`video`):
  - `is_video_model` no longer recognizes seedance; `kind_of_model` adds a `seedance` branch; `--type` adds `seedance`;
  - ordinary `video`'s `AUTO_PRIORITY_VIDEO` excludes seedance, `auto` won't mis-select it; Seedance uses its own `AUTO_PRIORITY_SEEDANCE`;
  - `generate_video(force_asset=True)`: text-to-video puts prompt into `media:[{type:text}]`; only when the user **passes image/video media**
    does it require `asset://` (`build_media(force_asset=True)` pre-rejects http(s)), plain-prompt text-to-video doesn't trigger this check;
  - agreement onboarding flow "**local flag + error fallback**": `seedance_enable_flow` when unflagged pops a 4-step "jump to PT onboarding" guide (non-blocking);
    `python pt_video.py seedance enable|disable|status` manages the local flag; `is_agreement_error` on hitting the server's "unsigned" keyword
    `reset_seedance_signed` and re-pops the guide; after first successful generation `mark_seedance_signed`;
  - **asset upload loop**: `python pt_video.py upload -f image.png` (free) goes `POST powertokens.ai/api/.../upload` to get
    `task_id` → poll `get-asset-id` returns `asset_id`, referenced as `asset://<id>`, for image-to-video `--first-frame`.
- **Measured (0.4.1, real key `...3eu2` with agreement signed, 80 credits)**:
  - `models -t seedance`: returns 7 seedance models all `available=yes`; cheapest `seedance-1-5-pro-251215`;
  - **text-to-video** `generate --type seedance -m seedance-1-5-pro-251215 -p "a cat walking"`: server accepts media-text submit,
    reaches `pre-consume quota failed` (balance $0.08 < pre-deduct $0.247), **proving the link works, no agreement error and no media error**;
  - **upload**: `upload -f image.png` got a real asset_id `asset-20261026180014-bc47m` for free;
  - **image-to-video** `generate --type seedance -m seedance-1-5-pro-251215 --first-frame asset://real-id`: server accepts the real asset,
    also reaches `pre-consume quota failed` (**no asset error**, proving the real asset is accepted);
  - throughout **no credits deducted** (failures all before the pre-deduction check).
- **Conclusion**: Seedance end-to-end link 100% through (agreement✓, text-to-video media-text✓, image-to-video real asset✓, upload loop✓), only short of top-up.
- **Lesson**: ① third-party models' "agreement/whitelist/asset-format" prerequisites often can only be handled manually in the dashboard; the skill should "detect + guide" rather than "sign on behalf";
  ② when a model's constraints are systematically different from ordinary peers, promote it to a standalone type rather than patch; ③ **don't take one error as the model constraint** —
  the original `only accept media` was triggered by a fake asset; plain-prompt text-to-video is actually feasible, you must measure with a real key to conclude.

### A.14【New · 0.4.1】PT asset id (asset://) works cross-account, no read-path isolation
- **Symptom/problem**: user gave a second key (account B, last 4 `8EWG`, not account A), asking "can the asset id uploaded under account A be generated under account B's key".
- **Measured (real dual account)**: using account A's uploaded real asset `asset-20261026180014-bc47m` as first frame, submit with account B's key
  `generate --type seedance -m seedance-1-5-pro-251215 --first-frame asset://asset-20261026180014-bc47m`:
  server accepts the cross-account asset, **the generation task fully completes** (in_progress 50% → returns result link `task_aayPol2v2RbrlHSmNFImRzqTXDFud3KZ.mp4`),
  consumption charged to account B. I.e. **asset:// id is a globally resolvable, cross-account readable shared reference**.
- **Security implication**: ① PT's asset library **does no account-level read isolation** — anyone who knows the asset id can reference it under their own key to generate (as long as pre-consume passes);
  ② so don't put confidential / PII content into an asset expecting "account isolation", **the asset id should be treated as an "unguessable but shareable" token**;
  ③ this also means "shared asset library / team collaboration upload once, generate by many" is a natively supported platform capability, usable in products.
- **Ancillary observation (download link)**: that result video URL returned `403 Forbidden` both direct from this sandbox and via proxy (R2 bucket rejects direct GET, hotlink/access policy),
  unrelated to cross-account, not an engine bug (engine `poll_task` line 679 fully takes PT's returned `metadata.url`, seedance gives a bare link with no signed query).
  The generation task completing server-side is ironclad proof; whether the result file can be downloaded publicly via the bare URL needs separate checking (may relate to the bucket's Referer/signature policy).
- **Handling**: this finding doesn't affect skill code (asset reference behavior matches doc expectations); only recorded as a security/product caveat.

### A.20【New · Fatal】`Retry-After` "implemented" but never obtainable: cross-module error object drops response headers
- **Symptom**: the B-tier of this archive says "429 honors `Retry-After`, already landed in pt-gateway", but on real rate-limiting it **every time** blindly backsoff 1→2→4s, never reading the server's given value.
- **Root cause**: the defect is in the **cross-module contract**, not in any single module — engine `pt_video._api_call` catches `HTTPError` then only throws `ApiError(code, message)`, dropping `e.headers`; upper `pt_gateway._retry_after()` reads `err.raw["headers"]` always empty → always returns `None`. Each layer alone looks "right", together they're dead.
- **Why tests didn't catch it**: offline cases only test `_retry_after` as a **pure function** (hand-feeding a fake object with headers), never going through the engine's `HTTPError` conversion — so "200+ all green" can't prove the link works.
- **Fix (implemented 2026-11-02)**: `ApiError(status, message, headers=None)` surfaces response headers; `_api_call` / `_upload_asset` / asset-poll **three places**' `HTTPError` conversions all pass `headers=getattr(e, "headers", None)`; `_retry_after` prefers reading `err.headers` and is **case-insensitive** on header names (server may send `retry-after`), `raw["headers"]` kept for compatibility; added a test that **fabricates a real `HTTPError` with headers at the urllib layer**, verifying the whole chain "HTTP response → ApiError → `_retry_after`".
- **New risk after the fix (must handle together)**: before we couldn't get `Retry-After`, no oversleep; now we can, so "server asks to wait 999s but total timeout is only 10s" appears. Added **deadline protection**: `Retry-After` exceeds remaining timeout → immediately throw `429 rate-limited: server asks 999s retry, exceeds remaining timeout 10s`, **no hard sleep** (a test asserts `sleeps == []` then).
- **Lesson**: ① the error object is a cross-module contract, **surfacing response headers matters as much as surfacing body**, otherwise the upper layer's "smart backoff" can only degrade forever; ② pure-function unit tests miss defects like "upstream never gave data" — at least one **pierces-the-real-call-stack** link test; ③ docs saying "implemented" doesn't count, a capability claim must point to a **test that would turn red**.

### A.21【New】403 form of model delisting: `model_not_found ... not listed (pending)`, while it's still in `/v1/models`
- **Symptom**: real-key full tier runs `deepseek-v4-flash-0731`, 403: `model_not_found ... not listed (pending), please contact admin to enable it`.
- **Cognitive point**: this sentence simultaneously carries two possibilities — "your key didn't open this model" and "platform delisted / pending on-board". `not listed (pending)` + `contact admin` is **platform-side wording**, not about key permission. More importantly it **still appears in the `/v1/models` list** (extension of H.2) — so "in the list" ≠ "usable", "call 403" ≠ "key no permission".
- **Handling**: from `catalog.json` **delete** this entry, not mark deprecated. Basis: real-key has proven platform-level unusable, not temporary maintenance; leaving it makes CLI `list` / MCP `list_capabilities` show it as a live model, user call guaranteed 403 — **silent trap is more dangerous than direct deletion**. `deprecated` only fits "still there but will be removed".
- **Collateral risk**: hardcoded model IDs in test tier (`harness.TIERS`) also rot. Added offline guard `TestTierModelReferences`: tier-referenced models must exist in catalog, **else red offline**, no need to wait for real-key 403.
- **Lesson**: ① model lists naturally rot, **any place with hardcoded model IDs needs an offline guard**; ② "delete" being safer than "mark deprecated" requires **real-key proven unusable**, otherwise you'd wrongly delete a model just in maintenance.

### A.22【New】chat's web_search / thinking is a "channel-level + client-passed-param + upstream-whitelist" triple condition
- **Symptom**: labeling 38 chat models "can web-search / can think", found **no unified answer**, even the same model differs across channels.
- **Truth (PT official "Model Capability Summary" + real-key cross-check)**: ① the gateway **doesn't inject** these params, the client must pass explicitly; ② param names **differ by channel** — ali is `enable_search:true` / `enable_thinking:true`, byteplus and zhipu's web search goes through `tools` tool declaration, zhipu / mimo's thinking has another param; ③ even if passed, **whether it actually takes effect is decided by the upstream whitelist** (not all models in a channel support it).
- **Handling**: capability fields use **three values** not boolean — `true` / `false` / `"conditional"`, and separately record `web_search_param` / `thinking_param` and `_source` (`configured` / `verified` / `official` / `official+verified`); `"conditional"` notes explicitly "gateway doesn't inject, client must pass param, depends on upstream whitelist".
- **Lesson**: ① capabilities must be seen in three layers — **gateway support** (pass-through) / **protocol support** (param exists) / **upstream support** (actually takes effect); mashing into one boolean is lying; ② when unsure use three-value enum + notes, more honest and usable than hard `true/false`; ③ official docs table by channel, itself telling you: **capability is a channel attribute, not a model attribute**.

### A.23【New · Fatal】Multimodal probe whole-family false negatives: single-format base64 `data:` URI rejected by some channels
- **Symptom**: real-key probe (send one `data:image/png;base64,...` single image asking "what is this") judges vision, result ali-qwen3 / BytePlus-seed / Zhipu / DeepSeek etc. channels **whole-family error** → judged `vision=false`, **21 actually vision-supporting models misjudged**.
- **Root cause**: different channels' compatibility with image input **carrier format** differs (`data:` URI / public URL / must upload first). The probe uses only one format, rejected → treated as "model doesn't support vision", but actually just "this channel doesn't accept this transmission method".
- **Correction**: compare against official "Model Capability Summary" §4 per-model list, all 21 `false` should be `true`; `seed-*` / `dola-seed-*` additionally support `video_in`. Official list doesn't cover (e.g. `qwen3-coder-plus`) keeps `vision: null`, no guess.
- **Handling**: ① catalog top-level adds `multimodal_source`, stating data source and the boundary "gateway passes through verbatim, actual availability subject to upstream"; ② each capability carries `_source`, letting people distinguish probe-derived from doc-derived; ③ probe downgraded to "auxiliary cross-check" tool, docstring clearly states **can't be used alone as conclusion, has false-negative risk**.
- **Lesson**: ① **a single-point probe's negative conclusion is least reliable** — "error" may just be "this transmission doesn't work", not "this capability doesn't exist"; ② capability labeling must carry **source and confidence**, don't mash probe values and doc conclusions into one boolean; ③ don't know → write `null`, don't use `false` to fake "confirmed absent".

---

## B. Async / polling logic design class (generic patterns + 0.2.0 known shortfall)

Extract the generic lessons that hold even outside the PT scenario — any "submit task + poll for result" async integration hits these.

| # | Pitfall | Correct approach | 0.2.0 status |
|---|---|---|---|
| 11 | Judge success only by `status` field | Multi-path fallback: get product URL > status word list > progress≥100; recursively extract fields, compatible with `data/result/task` wrapping and alias keys | ✅ implemented (URL + word list) |
| 12 | "Status unreadable" treated as "not ready yet" | Three cases explicitly counted + circuit-broken: query non-200 / status missing / status unrecognized; on circuit-break print raw response | ⚠️ only timeout throws, **no circuit-break** (known shortfall, see A.02) |
| 13 | Recoverable / unrecoverable error same threshold | 429 counted separately + backoff; 5xx strict threshold; reset both counters on successful query | ⚠️ not implemented (roadmap, see A.03) |
| 14 | Task stuck → regenerate | First run a read-only diagnostic to recheck real status (current 0.2.0 **no `status` subcommand**, need manual `GET /v1/videos/{id}`), if completed just take URL to download, don't resubmit | ⚠️ manual workaround, subcommand pending |

---

## C. Local environment / tooling class (Windows + WorkBuddy sandbox, verified)

This layer is the most grinding — the tools themselves are flaky, and they fail quietly (report success but didn't take effect). Core strategy: **write results to a file then Read**.

- **C.15 Same-file parallel Edit silently drops writes (fatal)**: send 4 Edits to change the same file at once, only 2 land, the other 2 silently fail, the tool still returns success. Fix: switch to a Python atomic batch-replace script (first backup → assert each hit exactly 1 time → write back only if all hit → roll back on any failure). **Parallel is only safe across different files**.
- **C.16 Bash PATH corrupted**: `dirname: command not found` / `head: command not found` / exit 127. Fix: explicitly prepend `PATH` at the start of commands; use absolute paths for Python/Node; `tail/grep/mv` often unusable → redirect output to a file then Read, use `os.rename` to move, use Grep tool to match.
- **C.17 PowerShell occasionally doesn't echo stdout**: exit 0 but zero output. Fix: always `Out-File` results to disk then Read.
- **C.18 PowerShell Chinese GBK garbling**: `-Encoding utf8` still garbles. Fix: set `$env:PYTHONIOENCODING="utf-8"`; put key conclusions in ASCII fields in diagnostic output (exit code / JSON key / number).
- **C.19 Current model doesn't support reading images**: user pastes screenshot, error `does not support reading images`. Hard rule: **never describe screenshot content from imagination**, must say "I can't see it", switch to code / HTTP real measurement to locate, or ask the user for plain text.
- **C.20 `/tmp` invisible on Windows**: logs write to workspace absolute path.
- **C.21 Fake API key literal truncated by security filter**: `sk-test-...` actually sent only 3 chars, misjudged "Key too short". Fix: programmatically construct test fake credentials, avoid real format features.
- **C.22 curl goes through proxy for localhost**: add `--noproxy '*'` when probing local services.
- **C.23 Long command foreground killed by timeout SIGTERM**: video generation 3~8 min, foreground easily killed. Fix: generation tasks always `run_in_background: true`, review results after completion then reply. Note `dangerouslyDisableSandbox` doesn't apply to background tasks.

---

## D. git sandbox class (cross-project generic, verified)

In the WorkBuddy sandbox, **git's loose ref writes don't land on disk** is the root of all weird phenomena. Core strategy: **avoid all git commands that touch refs**.

- **D.24 push credential failure**: `could not read Username: terminal prompts disabled`. Fix: temporarily switch to `gh` credential helper on the command line, don't write persistent config.
- **D.25 loose ref write doesn't land**: `rev-parse HEAD` reports unknown revision. Fix: `git write-tree` + `commit-tree -p <parent>` to generate commit object, then use `fs.writeFileSync('.git/refs/heads/<br>', sha+'\n')` to write the ref by hand (create parent dir first then write).
- **D.26 `reset --hard` breaks more stealthily**: deletes ref first then rewrites, rewrite doesn't land → branch ref disappears. Fix: use Node to rewrite ref + HEAD, then `git read-tree` to fix index, don't use `reset --hard` again.
- **D.27 Remote-tracking ref `origin/*` also lost**: merge using local branch ref or commit SHA (`merge --no-ff <sha>`).
- **D.28 checkout/merge occasionally misses writing worktree**: use `git checkout -f -- .` to rebuild from index (no ref write).
- **D.29 Using stale local ref for history judgment → false conclusion**: ❌ forbid `git log origin/xxx | grep`; ✅ use immutable SHA + `git merge-base --is-ancestor <sha> <real-time tip>` (tip from `git ls-remote`).
- **D.30 `ls-tree` without `-r` lists directory echo as empty**: add `-r` to recurse; cross-validate combo `ls-tree -r` + `diff --name-only` + `cat-file -t`.
- **D.31 Sharing working directory with other agents**: use "zero-touch commit" (temp index + commit-tree + SHA refspec push).
- **D.32 Comparing file consistency by byte count directly → misjudgment**: first normalize line endings (CRLF→LF) then compare.
- **D.33【Fatal · data loss】`git rm` clears the entire parent directory**: after running `git rm docs/pitfall-archive.html`, **the entire `docs/` vanished from the worktree** — `pitfall-archive.md` / `publish.md` in the same directory were also deleted, `git status` showed them as ` D`. At the time md had 28282 unstaged edited chars, while HEAD only had the 22462-char old version; the edits evaporated with the worktree.
  - **Criterion**: immediately `git status --porcelain` after deleting. If files **you didn't specify** show ` D`, you hit this pit, not that you really deleted it.
  - **Recovery**: don't use `checkout` / `reset` (same worktree-miss issue, see D.28). Use `git cat-file -p HEAD:<path>` to get the original bytes → write back to disk with a script (create parent dir first), then replay uncommitted changes one by one, each `assert anchor count == 1`.
  - **Prevention**: before any git command that touches the worktree (`rm` / `filter-branch` / `checkout` / `reset`), first `git add` uncommitted changes into the index **or copy outside the repo**; after committing, re-verify the target file still exists.

---

## E. Distribution / security class

The skill is for colleagues who don't code; "security" and "zero-threshold" are two hard constraints.

- **E.33 config file must not enter the distribution package (Key leak)**: Key stored in `~/.config/powertokens-video/config.json`; if zipped into the package it's handing the Key in plaintext. Fix: separate config from skill package, zip only contains `SKILL.md` / `pt_video.py` / `mcp_server.py` / `README.md` four items; after each packaging must do a leak scan (`grep` Key fragments, hit count must be 0); config permission 0600.
- **E.34 Colleague doesn't code → don't make the user run any command**: establish "user only talks throughout, agent runs all commands" — recognize `sk-pt-` prefix strings in conversation → agent itself `config --set-key` → `check` echoes pool status → directly works. SKILL.md explicitly forbids making the user run commands / edit files / set env vars.
- **E.35 "Colleague didn't install Python" is a pseudo-problem**: the WorkBuddy client ships a managed Python/Node runtime. After fully removing CLI for pure REST, even Node deps are gone — just Python 3.9+. One less dependency, one less failure class.

- **E.40 PT product URL carries pre-signed credentials; writing it into reports/docs triggers GitHub Push Protection**: push blocked by GitHub, reporting **VolcEngine Access Key ID**, hitting `pt-gateway/e2e_report.md:32` — that's the seedream generated image's download address, query carries `X-Tos-Credential=***` (Volcano TOS Access Key ID) + signature + `X-Tos-Expires=86400`. PT-returned product URLs (TOS / R2) are often **pre-signed URLs**, credentials and signature written in the query, enough to trigger secret scanning; they expire in 24h, but the scanner doesn't recognize "expired", and **once in history commits it permanently blocks push** (it scans the entire to-push branch, not just this change). Handling: ① test reports/temp products never enter the repo — `tests/reports/` **entire directory** gitignore (initially only ignored `*.md`/`*.json`, one `.txt` slipped through); ② already historically carried ones use `git filter-branch --index-filter "git rm --cached <path>" --prune-empty -- --all` to remove from all commits then push (zero risk if remote never pushed successfully); ③ when docs paste product URL examples truncate query to `?X-Tos-Credential=***`. Lesson: **secret scanning recognizes not only API-key shapes but also cloud-storage signed URLs**; test products gitignored by default, don't wait to be blocked.

---

## F. Collaboration / process class (user explicitly required)

These aren't technical bugs, they're collaboration boundaries — stepping on them directly consumes trust.

- **F.36 Discuss code changes before acting**: clarify the plan then change; but clear bug fixes lean toward acting directly. This project complied multiple times: user asks "reply to me first, modify after agreement" → give plan assessment first, wait for confirmation before acting.
- **F.37 Don't touch running services**: restarting / killing / reconfiguring processes like dsh must first get explicit confirmation, prefer read-only diagnostics, non-invasive actions.
- **F.38 Hold scope + evidence-first**: say single-site diagnosis stays within single site; troubleshooting goes four-stage (each step with evidence → draw conclusion → current state / goal / comparison / suggestion), no evidence-free generalization. A.07 (900s misjudgment), D.29 (stale ref false conclusion) are the cost of violating evidence-first.

---

## G. pt-gateway submodule (added 2026-10-30)

> pt-gateway = cross-model unified async task gateway (`pt-gateway/pt_gateway.py` + `server.py` + `mcp.py` + `catalog_crawler.py`).
> This round's core output: correct three hardcoded endpoints, crawl PT docs without a key to build a "public-layer global truth", and make permission judgment a fail-closed gate.

### A.15【New】"PT endpoints all unified /v1/videos" only holds for submit/query; cancel and audio are vendor-specific paths
- **Symptom (two reverse misjudgments)**: ① early thought "Seedance / Vidu / Wan each have independent submit/query interfaces" → measured **is a misjudgment**, PT already unified: submit `POST /v1/videos`, poll `GET /v1/videos/{id}`; ② after correcting, written as "all unified under /v1/videos" → **also a misjudgment**.
- **Real shape**: unification is **layered** —
  - submit / query: unified `POST /v1/videos` + `GET /v1/videos/{id}`;
  - cancel: only Seedance via ByteDance/BytePlus provides `DELETE /byteplus/api/v3/contents/generations/tasks/{id}` (queued→cancelled, measured 2026-10-30);
  - audio (TTS): `POST /ali/api/v1/services/aigc/multimodal-generation/generation` (Ali/DashScope compatible, **sync** returns url), **doesn't go** `/v1/videos`.
- **Lesson**: PT unifies the "submit/query" transport layer, **not** "cancel/audio". Writing the gateway requires confirming endpoints per capability, don't summarize with one "all unified", nor one "each its own".

### A.16【New · Fatal】PT has no async task-cancel interface (user confirmed with PT official, 2026-10-30)
- **Symptom**: wanting a generic implementation for "cancel in-progress video tasks", inferred from OpenAPI docs that wan / kling / vidu each have `DELETE /.../tasks/{id}`.
- **Truth**: user has **confirmed with PT official — PT has no async task-cancel interface**. In the docs `/tasks/{id}` only has **GET query**, DELETE doesn't exist. The only exception is Seedance's BytePlus vendor endpoint (A.15).
- **Handling**: `cancel()` for non-seedance honestly returns `{supported:false}` + reason — no best-effort guessing endpoints, no faking success. Other models can't be cancelled server-side mid-task, **can only wait for them to finish on their own**.
- **Lesson**: ① doc "exists `/tasks/{id}`" ≠ "exists DELETE cancel"; ② when unsure, honestly degrade, more reliable than best-effort sending a non-existent DELETE; ③ **discovering endpoints (read public docs) costs no key**, **verifying cancel works (submit+cancel) costs quota** — since official confirmed none, don't spend quota to try.

### A.17【New】`/v1/models` is just "this key's permission view", can't be global catalog / delist basis
- Continues **A.06**: different keys return different model lists (whitelist / quota). So "a model not in this key's list" **≠** "that model is delisted". Old `ModelRegistry` misjudged this way — wrote "other key has it, this key doesn't" as `delisted=true`, multi-key scenarios would wrongly kill legitimate calls.
- **Correct division (two layers)**:
  - **Delist judgment looks at the public layer** (crawl PT docs without a key, see A.18);
  - **`/v1/models` only records `key_permitted` permission view**; only when a model is **simultaneously** absent from the live list and absent from the public layer is it judged `delisted=true`.
- **Permission gate (user decided fail-closed)**: an explicitly specified model not in this key's `/v1/models` list → locally directly `ApiError(403)` "current key has no call permission for model X", **no real generation request sent** — saves a guaranteed-403 call and quota (more economical than the fail-open "let the server error").
- **Two boundaries (both must be handled, else bugs)**:
  - `model=auto` does no gate check, but **model selection must pick within this key's permission set** — otherwise auto selects a no-permission model then gets gated (measured once selected no-permission `wan2.2-kf2v-flash`, after key-aware correctly selected `wan3.0-video`);
  - permission query (`/v1/models`) **itself fails** → **degrade to allow**, don't wrongly kill legitimate calls, leave final judgment to the server.

### A.18【New · low-cost plan】PT official docs crawlable without a key, get "global model truth"
- **Necessity**: A.17 requires delist judgment to look at the public layer, but PT has no key-less model-catalog API — so crawl the official docs.
- **Measured data source (2026-10-30 verified usable)**:
  - `https://docs.powertokens.ai/sitemap.xml` **directly outputs 396 `<loc>`**, of which **350 model pages** (Video 180 / Image 66 / Audio 24 / Chat 80; no sub-sitemap); en + zh-Hans bilingual dedup → **181 unique pages**;
  - model page URL pattern: `docs.powertokens.ai/{lang}/zmodel{Type}/{vendor}/{slug}`;
  - each page ships an **OpenAPI 3.0.1 fragment**, URL = model page path with `/api-reference` inserted before + suffix `.json`
    (e.g. `/api-reference/en/zmodelVideo/ali/wan3.0-video-generation.json`). ⚠️ both `/en/...json` and `/openapi/...json` are **404**, only `/api-reference/{lang}/...json` works.
  - model ID is in `components.schemas[*].properties.model.enum` (one page may contain many); **sync/async** depends on whether there's a `GET /tasks/{id}` poll path.
  - ⚠️ **Chat page JSON carries UTF-8 BOM** → must `utf-8-sig` decode, else `json.loads` throws directly.
- **Output**: `catalog.public.json` — 82 models (video 20 / seedance 7 / image 9 / audio 8 / chat 38; async 28 / sync 54). 10 404s all zh-Hans minimax mirror pages (their en version already captured, **no model lost**); task query/cancel etc. "help pages" have no model enum, should be correctly skipped.
- **Lesson**: when there's no catalog API, **official sitemap + doc-page-embedded OpenAPI** is the lowest-cost "global truth" source; but handle BOM, bilingual dedup, and helper-page skipping — three kinds of noise.

### A.19【New · awaiting measurement】audio TTS request-body fields still not fixed (sync interface don't rely on "compatible protocol guess")
- **Current**: endpoint confirmed (Ali-compatible `POST /ali/.../multimodal-generation/generation`), but params drawn from OpenAPI are `input/text`, `voice`, `language_type`, while current `audio_generate()` sends `input.prompt`.
- **Undecided**: is it `text` or `prompt`? Which of `output.audio.url` / `output.url` / top-level `url` does the response url land in? **Must run once with a real key to fix** (current code is multi-location fallback url extraction + keeps `raw` for checking).
- **Lesson**: sync interface field names can't be inferred from "it's compatible with some protocol" — compatible implementations often differ on fields; one real call fixes it, far cheaper than repeated guessing.

### B-tier supplement: A.02 / A.03 polling circuit-break **already landed in pt-gateway** (2026-10-30 correction)
- `pt-gateway/pt_gateway.py` rewrote `poll_task` (overriding the engine original): 429 honors `Retry-After`, else exponential backoff (≤60s); 5xx **consecutive 5 times circuit-break** throws; other 4xx pass through; timeout clearly errors. I.e. A.02 / A.03's "roadmap" is implemented in this submodule.
- **2026-11-02 correction**: the above "429 honors `Retry-After`" was **actually broken before 2026-11-02** — engine `ApiError` didn't surface response headers, `_retry_after()` always returned `None`, the so-called "honoring" never happened. Fixed, see **A.20**. Typical shape: upper layer right, lower layer didn't give data, docs declared landed.
- ⚠️ **Don't conflate**: this is **pt-gateway**'s implementation; engine `pt_video.py` (0.2.0)'s `poll_task` is still "only timeout throws, no circuit-break" (see A.02 table row 12). Docs/conversation must specify which module.

### C.24【New】Bash tool **fails entirely**, not PATH corruption
- **Symptom**: one round all Bash commands report `Parameter "command" expected string, but received undefined` — even `echo hi` rejected (distinct from C.16's PATH corruption, which can still run absolute paths).
- **Fix**: **immediately switch to the PowerShell tool**, don't retry Bash repeatedly. PowerShell for git / running Python both work (this round all git ops and script execution done via it).

### C.25【New】PowerShell `>` redirect writes **UTF-16**, Read reports binary
- **Symptom**: `python x.py > out.txt 2>&1` produced file, Read reports `Cannot display content of binary file`.
- **Fix**: either — ① `cmd | Out-File -FilePath out.txt -Encoding utf8`; ② more stable: let the Python script **itself** `open(..., 'w', encoding='utf-8')` write the result file, then Read. (This round uniformly uses ②, script also writes anchor hit counts/errors into it.)

### C.26【New · Fatal】Patch script uses `\uXXXX` to escape Chinese → typo → **silently doesn't land**
- **Symptom**: patching `pt_gateway.py`, `assert s.count(old)==1` actually failed, but PowerShell swallowed the error (C.17), showing "script ran, code unchanged", took two rounds to find.
- **Root cause**: Chinese in anchors hand-written as `\uXXXX` easily typo — wrote "兜" U+514C as "兆" U+5146, one char wrong makes the whole anchor not match.
- **Fix**: ① anchors **only use ASCII** (e.g. `    def auto_select(self, kind):`), Chinese always written as **literals** in the replacement string; ② script **in any case** writes each anchor's hit count and exceptions to an OUT file, after running Read to verify `count=1`; ③ if hit count wrong, **don't land** (avoid half-changed state).
- **Locating**: this is an advanced version of C.15 (parallel Edit silent drop) — same "reports success but didn't take effect", inducement from concurrency changed to anchor typo.

### C.27【New】Edit tool hits **curly braces** can fake-success
- **Symptom**: `old_string` / `new_string` contains `{...}` (e.g. `{supported:false}`, `{id}`, `{kind}`), Edit may return "Successfully edited" but **content didn't land**; found only after Grep/Read recheck.
- **Fix**: changes containing curly braces (or long Chinese easily missed) all switch to Python script: `s.count(old)==1` assert + `s.replace` precise write-back, after writing use Read to recheck. This round all `{...}` changes went this route.

### F.39【New】Corrections arising in discussion must **land in docs**, not stay in conversation
- **User explicitly required (2026-10-30)**: ① correct what was **said wrong** before (code + catalog + server/mcp + README aligned together); ② record **what needs changing** as a doc; ③ write the crawler first, **come back and discuss item by item** the to-correct items in the doc after writing.
- **Landing**: create `pt-gateway/FIXLIST.md`, in three sections — "Corrected (with evidence/conclusion of three hard errors)" / "To discuss (each marked `[已定]`/`[待讨论]`)" / "Implementation record (item-by-item landing after user decides)".
- **Lesson**: ① correction items popping up in discussion scattered in conversation will **be lost, can't be traced back**, must land in a file; ② "correct → record → implement → come back to discuss" must be **phased**, each phase has a deliverable; ③ decided/implemented items must be **marked with status** in the doc, else next time the same thing gets discussed again.

## Correction Notes (this version vs old powertokens-wan archive)

| # | Old-version problem | This-version correction |
|---|---|---|
| 1 | **Missing pitfall**: completely omitted that `seconds` / `size` / `ratio` / `model` must be passed as strings | Added **A.11**, recording the real `cannot unmarshal number into ... .seconds of type string` we hit this round |
| 2 | A.01 said "gateway actually returns `completed`" | Our measurement returns `succeeded`, corrected to "written inconsistently, multi-path fallback" |
| 3 | Project name `powertokens-wan`, version 0.1.2, 26 unit tests, 4 renders, `pt_wan.py`, `~/.config/powertokens-wan/` | Aligned **powertokens-video 0.2.0**, 0 unit tests, 1 render, `pt_video.py`, `~/.config/powertokens-video/` |
| 4 | A.05 treated "invalid Key returns 500+token_invalid" as confirmed fact; A.06 "85 models" without caliber note | A.05 downgraded to **awaiting measurement**; A.06 changed to our measured **32 video models** with caliber noted |
| 5 | Wrote `classify_rest` / `rest_poll` triple circuit-break / `fetch_model_ids` / `ensure_model_allowed` / `status` subcommand as "landed" | 0.2.0 actually only has simple polling + `precheck_explicit_model`; the above advanced capabilities changed to **known shortfall / roadmap**, not falsely claimed implemented |
| 6 | Old version only covered video (0.2.0) | 0.3.0 expanded to **text+image universal**: image endpoint `POST /v1/images/generations` sync (A.12), Seedance Asset Library agreement + BytePlus media prerequisite (A.13); real render added 1 image (image-01) |
| 7 | Didn't cover pt-gateway submodule; B-tier said "poll circuit-break not implemented" | 2026-10-30 added **A.15–A.19** (two reverse misjudgments of endpoint layering / PT no cancel interface / `/v1/models` just permission view + fail-closed gate / crawl docs without key for global truth / audio fields awaiting measurement), **C.24–C.27**, **F.39**; and corrected B-tier: circuit-break **already landed in pt-gateway**, engine `pt_video.py` still old impl |
| 8 | Didn't cover chat (38 models) capability labeling and probing; wrote 429 `Retry-After` as "landed" | 2026-11-02 added **A.20–A.23** (Retry-After whole-chain break and fix / delisted model 403 form / chat capability triple condition / multimodal probe false negatives), **E.40**, **H.4**; and corrected B-tier "landed" was actually broken |

---

## H. Live acceptance measurement (2026-10-30, real-key real run)

### H.1 Audio endpoint's third misjudgment: real is OpenAI-compatible `/v1/audio/speech`
Symptom: `audio_generate` assumed "Ali-compatible" and requested `/ali/api/v1/services/aigc/multimodal-generation/generation`,
`speech-2.8-turbo` returned 500 `pricing rule not found`; switching to `qwen3-tts-instruct-flash` returned 404 `Model not exist`.
Evidence: `POST /v1/audio/speech` with illegal voice returns `400 voice is required` (not 404 Invalid URL) → endpoint really exists.
Conclusion (measured):
- Endpoint `POST /v1/audio/speech`; request body `{model, input, voice}`; **`voice` required**.
- Voices use OpenAI standard names `alloy/echo/fable/onyx/nova/shimmer`, all 6 measured 200.
- Response `{data:{audio:<mp3 url>, status:2}}`, url at `data.audio` (not `output.audio.url`).
Lesson: endpoint shape must be judged by **error code** — 404 `Invalid URL` = path doesn't exist; 4xx/5xx business error = path exists but param/pricing problem.

### H.2 Models listed in `/v1/models` ≠ all usable
Measured: of 8 audio models, `qwen3-tts-instruct-flash` 404 doesn't exist, `audio1.0` / `speech-2.6-hd` report "multiplier or price not configured".
Conclusion: model list is just a "catalog", usability also depends on whether the platform configured pricing rules. Gateway not doing client-side gating is right —
let the server error, don't maintain an inevitably-stale "usable list" on the client.

### H.3 Cost magnitude of live acceptance (reference for later regression)
One full chain (model list + chat + image + TTS + video 720p 5s) about **$0.54**, video占 92%.
Video generation takes ~145s; image ~22s; TTS ~3s; chat ~1s.
Regression money-saving: switch video to Seedance Mini 480p ($0.031/s) can compress the whole round to ~$0.20.

### H.4 Tiered live acceptance cost measurement (2026-11-02, provides a basis for "whether to run another round")

| Tier | Coverage | Measured cost |
|---|---|---|
| minimal | one hit per capability type, verify link works | **$0.199** |
| standard | main model families + error paths | **$0.544** |
| full | full model traversal (38 chat + all image/audio/video) | **$1.044** |
| cumulative | including one separate verification | **≈ $1.79** |

- **full first run hit an already-delisted model** (A.21) — confirms "model list rots", directly spawned the offline guard.
- **Money-saving basis**: ① only change **data** (delete entry, change capability label) → run offline, 0 cost, 1 second; ② change **engine / error path** → faking 429 is more deterministic than real-key, because real-key can't produce 429 at all; ③ only before release or adding new models is it worth running real-key full.
- Video still占 cost 90%+ (same as H.3), shortening video duration / resolution is cheaper than cutting cases.

*Pitfall archive · corrected 2026-10-26 · 2026-10-30 added pt-gateway section · 2026-11-02 added chat capability / 429 link / pre-signed URL · 阿伴 🦝*
*Diagnostic tools retained: `pt_video.py` (engine) · `mcp_server.py` (MCP shell) · `README.md` (install) · `docs/publish.md` (publish)*
