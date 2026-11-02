# Publishing to Registries (ClawHub / mcp.so / Smithery)

> 📘 This document is in English. [中文文档](publish.zh-CN.md)

This file is a **submission checklist** for maintainers. The repo root **is itself an Agent Skill folder** (it contains `SKILL.md`),
and is also the source of the MCP server. The two registry types reuse the same content.

---

## A. Submit to ClawHub (Agent Skills registry)

ClawHub is the community registry for the `agentskills.io` open standard, indexing `SKILL.md` skills that can be read directly by 40+ agents.

Pre-submission checks:

- [ ] The repo root has `SKILL.md`, whose frontmatter contains `name` (matching the directory name: lowercase, hyphenated) and `description` (≤1024 chars, stating "what it does + when to use it").
- [ ] The `description` uses tool-agnostic trigger phrases (e.g. "generate a video with PowerTokens"), **with no single-agent-specific wording**.
- [ ] There is a `license` field (this project: MIT).
- [ ] The `SKILL.md` body is ≤ 500 lines (if exceeded, split into `references/`).
- [ ] `pt_video.py` runs directly via `python pt_video.py check` (does not depend on paths outside this repo).

Submission flow (follow ClawHub's web/CLI; steps below):

1. Publish a **Release** on GitHub (a tag is recommended, e.g. `v0.1.0`), ensuring `SKILL.md` is at the repo root.
2. Open the ClawHub submission page (or run the publish command in an Agent-Skills-compatible client), and fill in:
   - Skill repo URL: `https://github.com/tjj1234/powertokens-kit`
   - Entry path: `/` (the root directory is the skill folder)
   - Category: `media / video-generation`
3. After submission, wait for review/indexing; once live, users can run something like this in their client:
   ```bash
   # e.g. install a community skill in Claude Code
   claude skills install powertokens-kit
   ```
   or add it with one click in a supported marketplace.

> Note: `name` must be `powertokens-kit` and match the directory. If the registry already has that name, distinguish it via the `author` field in `metadata`, or rename (changing both the directory name and the frontmatter `name`).

---

## B. Submit to mcp.so (MCP registry)

mcp.so is the community directory for MCP servers. This project's `mcp_server.py` is the server entry point.

Pre-submission checks:

- [ ] `pip install -e ".[mcp]"` succeeds, and `python mcp_server.py` starts over stdio.
- [ ] The "Integration B" section of `README.md` gives a copy-pasteable `mcpServers` JSON snippet (with `command` / `args` / `env`).

Submission flow:

1. Open <https://mcp.so> → Add MCP Server.
2. Fill in:
   - **Name**: `powertokens-kit`
   - **Repository / Command**:
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
   - **Category**: `Media / Video`
   - **Description**: Generate videos from PowerTokens models (Seedance/Kling/Wan/…) via CLI + MCP.
3. After submission, users can one-click `npx`/install in clients that support mcp.so.

(Smithery's flow is similar — submit the server metadata via Smithery's `smithery.yaml` or web form; the server is exposed over stdio, no extra adaptation needed.)

---

## C. Release checklist (tick off item by item before tagging)

- API error semantics (401 / 402 / 403) are documented in README §17.1 — keep this in sync if PT changes the contract.

- [ ] `pt_video.py` compiles (`python -m py_compile pt_video.py`)
- [ ] `python pt_video.py models` outputs the public catalog (pricing + params) without a Key
- [ ] `python pt_video.py check` gives guidance and exits 2 when the Key is missing
- [ ] `mcp_server.py` starts when the `mcp` dependency is present
- [ ] The "per-agent directories / per-client config entry" section of `README.md` matches the current version
- [ ] `pyproject.toml`'s `project.urls.Repository` is set to the real repo URL
- [ ] The `your-org` placeholders in `SKILL.md` / `README.md` are replaced with the real org
- [ ] Tag and push Git: `git tag v0.1.0 && git push --tags`
