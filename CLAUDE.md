# Claude Code 入口

本仓库的项目级 AI 协作规范统一维护在 [AGENTS.md](AGENTS.md)。开始任何代码、配置、迁移或文档改动前，必须完整阅读该文件，并遵守其中引用的 [编码规范](docs/CODING_STANDARDS.md)。

Claude 专属约定：

- 不要复制或覆盖 `AGENTS.md` 中的规则；需要新增跨助手规则时只修改 `AGENTS.md`。
- 策略构建任务使用 `.claude/skills/strategy-builder/`；与 Codex 同名技能的共同边界以 `AGENTS.md`、当前 schema 和 CLI 为准。
- 策略优化任务优先读取 `.agents/skills/strategy-optimizer/` 的流程与约束；运行命令前以当前 CLI `--help` 为准。
- `.claude/settings.json` 只管理 Claude 插件启用状态，不存放业务架构或安全规则。
