# 发布到注册表（ClawHub / mcp.so / Smithery）

本文件是给维护者看的「提交清单」。仓库根目录**本身就是一个 Agent Skill 文件夹**（含 `SKILL.md`），
同时也是 MCP server 的源码。两种注册表复用同一份内容。

---

## A. 提交到 ClawHub（Agent Skills 注册表）

ClawHub 是 `agentskills.io` 开放标准的社区注册表，收录可被 40+ agent 直接读取的 `SKILL.md` skill。

前提检查（提交前确认）：

- [ ] 仓库根有 `SKILL.md`，frontmatter 含 `name`（与目录名一致：小写、连字符）和 `description`（≤1024 字，说明「做什么 + 何时用」）。
- [ ] `description` 写的是工具无关的触发语（如「generate a video with PowerTokens」），**不含任何单一 agent 专有字眼**。
- [ ] 有 `license` 字段（本项目 MIT）。
- [ ] `SKILL.md` 正文 ≤ 500 行（超出请拆到 `references/`）。
- [ ] `pt_video.py` 可直接 `python pt_video.py check` 运行（不依赖本仓库外的路径）。

提交流程（以 ClawHub 网页/CLI 为准，步骤如下述）：

1. 在 GitHub 上发布一个 **Release**（建议带 tag，如 `v0.1.0`），保证 `SKILL.md` 在仓库根。
2. 打开 ClawHub 提交页（或在 Agent Skills 兼容客户端里执行发布命令），填入：
   - Skill 仓库 URL：`https://github.com/tjj1234/powertokens-kit`
   - 入口路径：`/`（根目录即 skill 文件夹）
   - 分类：`media / video-generation`
3. 提交后等待审核/索引；上线后用户可在客户端执行类似：
   ```bash
   # 例：Claude Code 安装社区 skill
   claude skills install powertokens-kit
   ```
   或直接在支持的市场里一键添加。

> 注：`name` 必须为 `powertokens-kit` 且与目录同名；若 registry 已存在同名，请在 `metadata` 里用 `author` 区分，或改名（同时改目录名与 frontmatter `name`）。

---

## B. 提交到 mcp.so（MCP 注册表）

mcp.so 是 MCP server 的社区目录。本项目 `mcp_server.py` 即 server 入口。

前提检查：

- [ ] `pip install -e ".[mcp]"` 可成功，`python mcp_server.py` 能以 stdio 启动。
- [ ] `README.md` 的「接入方式 B」里给了可直接复制的 `mcpServers` JSON 片段（已含 `command` / `args` / `env`）。

提交流程：

1. 打开 <https://mcp.so> → Add MCP Server。
2. 填写：
   - **Name**：`powertokens-kit`
   - **Repository / Command**：
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
   - **Category**：`Media / Video`
   - **Description**：Generate videos from PowerTokens models (Seedance/Kling/Wan/…) via CLI + MCP.
3. 提交后用户可在支持 mcp.so 的客户端里一键 `npx`/安装。

（Smithery 流程类似，按 Smithery 的 `smithery.yaml` 或网页表单提交 server 元数据即可；server 以 stdio 暴露，无需额外适配。）

---

## C. 发布清单（发版前逐项勾选）

- [ ] `pt_video.py` 语法编译通过（`python -m py_compile pt_video.py`）
- [ ] `python pt_video.py models` 在无 Key 时能输出公开目录（定价+参数）
- [ ] `python pt_video.py check` 在缺 Key 时给出引导并 exit 2
- [ ] `mcp_server.py` 可在有 `mcp` 依赖时启动
- [ ] `README.md` 的「各 agent 目录 / 各客户端配置入口」与当前版本一致
- [ ] `pyproject.toml` 的 `project.urls.Repository` 换成真实仓库地址
- [ ] `SKILL.md` / `README.md` 里的 `your-org` 占位替换成真实 org
- [ ] 打 Git tag 并推送：`git tag v0.1.0 && git push --tags`
