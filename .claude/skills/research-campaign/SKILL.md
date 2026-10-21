---
name: research-campaign
description: >
  研究战役（自动研究循环）的角色手册：由 `research campaign run` 启动的 headless Claude Code
  会话、或桌面编排模式下新开的桌面会话，按本技能以研究员或评审员身份执行一步。也用于用户要求
  "开一个研究战役 / 让 AI 自动研究某个方向 / 用桌面会话跑战役 / 查看战役进度 / 解释战役报告"时
  开启与驱动战役（桌面会话按「桌面编排模式」一节充当编排者）。不用于：单次手工优化（用
  .agents/skills/strategy-optimizer）、配置新策略（用 strategy-builder）。
---

# 研究战役

> **当前契约优先**：先读仓库根目录 `AGENTS.md`；命令参数以 `python -m quant_etf_api.cli <命令> --help` 为准。
> 本系统只输出研究结果，不构成投资建议；promote、上线与验证期使用都由人工决定。

## 战役是什么

平台持有规则，agent 负责判断：

- **平台**：战役状态、限额（试验增量、研究线预算、时长、轮数、连续否决）、停止条件、命令拦截。
- **agent**：每一步由驱动器（headless）或编排会话（桌面编排模式）启动一个**全新的**会话执行。
  你看不到之前会话的内容，只能依据任务卡与平台数据（台账、优化会话、回测、稳健性批次）。

每一轮：**研究员**登记一个假设并检验 → 有评估完成的候选时请求评审 → **评审员**只看证据给结论 →
下一轮研究员读评审结果，换方向或补证据。

## 你在 agent 会话里

任务卡里的 `next.role` 决定你的角色：

- `researcher` → 读 [references/researcher.md](references/researcher.md)
- `reviewer` → 读 [references/reviewer.md](references/reviewer.md)

通用规则：

1. 只用 `python -m quant_etf_api.cli ...`，工作目录就是仓库根目录，**不要 cd**、不要用其他 shell 命令。
2. 文件只能写在任务卡的 `workspace`（`candidates/<战役>/`），文件名带轮次前缀（如 `r02_candidate.json`）。
   只能读 `AGENTS.md`、`README.md`、`docs/`、`.claude/skills/`、`.agents/skills/`、`apps/api/src/` 与本战役目录；
   `candidates/` 下其他目录（旧研究产物）不可读，结论以研究台账为准。
3. 战役模式下 CLI 会拒绝：`--promote`、`--no-strict`、`--over-budget`、`lifecycle`、`consume-validation`、
   非 research 用途的回测、研究线外的策略 / 批次 / 回测、其他战役登记的假设、把策略改为 active、
   改归研究线、修改基线或启用策略。新建策略必须 `strategy create ... --draft --lineage <strategy_id>`。
   被拒绝时不要绕路，把原因写进本步摘要。
   诊断命令（`research factor-ic|buckets|signal-lab`）整个战役共用 `limits.max_diagnostics` 次配额
   （`limits.diagnostics_used` 为已用），用完即拒绝：先想清楚要验证什么再诊断，不要拿诊断批量筛方向。
4. 研究期固定 2016-01-01 ~ 2025-12-31，执行模型统一 `t_plus_1_close`（CLI 缺省即是），不碰 2026 数据。
5. 并行回测进程数用环境变量 `QUANT_ETF_CLI_WORKERS`（命令里写 `--workers 0` 即自动读取；
   需要显式值时取任务卡外驱动器设定的数，不要超过它）。
6. 本步结束前**必须**回报：研究员 `research campaign step-done`；评审员先 `research campaign review`
   再 `research campaign step-done`。不回报的步骤会被判为失败并重试。
7. 每个 research batch 变体、每个优化会话、每个稳健性变体都计入研究线的试验次数 N，
   而 N 越大 Deflated Sharpe 门槛越难过：**少而精**，不要撒网。验收第 8 项检验的是
   「候选 − 基线」逐日超额的 Deflated Sharpe，改进幅度小、差异日少的候选很难通过。

## 人工操作（不在 agent 会话里）

前提：本机 Claude Code CLI 已登录（`claude auth status` 显示 `loggedIn: true`；否则先在终端运行
`claude auth login`）。桌面端的登录态不会自动给 CLI 用；驱动器启动前会检查，未登录时直接报错，不消耗步骤。

```bash
# 开启：种子基线策略必须已存在（可先用 strategy-builder 建）
python -m quant_etf_api.cli research campaign start --strategy <种子策略> \
    --objective "<研究目标>" --index-codes 000852 --max-trials 30 --max-hours 6 \
    --max-rounds 5 --max-rejects 3 [--max-diagnostics 30] [--max-step-usd 5] [--model <模型>]
# 驱动：逐步启动 headless claude，直到停止（可 --max-steps 分段跑；中断后 --recover 续跑）
python -m quant_etf_api.cli research campaign run <cid> --workers 4
# 查看 / 报告 / 停止
python -m quant_etf_api.cli research campaign status <cid>
python -m quant_etf_api.cli research campaign report <cid> --file candidates/<cid>/report.md
python -m quant_etf_api.cli research campaign stop <cid> --reason "<原因>"
```

评审推荐接受的优化会话会以严格模式 `finish --verdict accept`（不 promote），报告的「待人工确认」
列出它们；是否 promote、另建策略或上线由人决定。agent 会话的完整记录在
`candidates/<cid>/steps/`，也可用 `claude --resume <agent_session_id>` 复查。

## 桌面编排模式（不需要 CLI 登录）

由一个 Claude Code **桌面会话**充当编排者：每一步用桌面端的「新会话」执行，用的是 App 自身的登录，
不需要 `claude auth login` 或 API Key。适合有人在旁监督的运行；长时间无人值守仍建议 headless 驱动器。

平台约束：编排者 `begin-step` 时记下自己的 `CLAUDE_CODE_SESSION_ID`；步骤运行期间，**除编排会话外**
的所有 CLI 调用（子会话、终端）都按数据库进入战役模式，子会话不需要也无法自行声明。
子会话不能 `begin-step` / `finalize-step` / `stop`；不在任何 Claude Code 会话里的终端可以
`research campaign stop` 收口卡住的战役。CLI 层之外的边界（不改代码、不跑其他命令）写在给子会话的
提示里，平台无法强制——这是与 headless 模式（`dontAsk` + 权限白名单）的主要差别。

编排者循环（用户已明确要求用桌面会话跑战役时执行）。编排者的 CLI 一律用项目虚拟环境执行
（`apps/api/.venv/Scripts/python -m quant_etf_api.cli ...`）：`begin-step` 按编排者的解释器告诉子会话用哪个
python，PATH 上的系统 python 未必装了项目依赖。

1. 已有战役用其 ID；没有时先 `research campaign start ...`（同上）。
2. `python -m quant_etf_api.cli research campaign begin-step <cid>`：
   - `action=stop` → 战役结束，生成报告（`report --file candidates/<cid>/report.md`）并向用户汇报；
   - `action=wait` → 有未收口的步骤：确认对应子会话已结束后执行第 4 步收口，再回到第 2 步；
   - `action=run` → 第 3 步。
3. 新开桌面会话执行本步：`start_session`，`initiation="user_asked"`、`context="fresh"`、
   `use_worktree=false`（必须在本仓库目录运行：数据库配置、`candidates/` 与战役模式判定都依赖它）、
   `wake_when_done=true`；`prompt` 原样使用返回的 `prompt`；`background` 写明"研究战役 <cid> 第 N 轮
   <角色>，按 .claude/skills/research-campaign 执行一步，结束前必须回报 step-done"。
   等待子会话结束的通知，**不要轮询，也不要替子会话做研究**；子会话来问问题时，回复"按角色手册自行
   判断，阻塞写进摘要后回报"。
4. 子会话结束后收口：`research campaign finalize-step <cid> --agent-session <子会话 local_ ID>`；
   子会话报错、卡在等待输入或被人工停止时加 `--error "<原因>"`。然后回到第 2 步。
5. 每轮结束向用户报一行进度（轮次、角色、结果、试验增量）。

注意：桌面端连续多次自动续跑而用户没有新消息时会暂停，需用户说"继续"；App 关闭或电脑休眠会中断
子会话，下次先 `status` 看运行中步骤，再按第 2 步的 `wait` 分支处理。
