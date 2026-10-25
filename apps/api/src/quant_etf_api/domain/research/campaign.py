"""研究战役（自动研究循环）的纯业务规则：步骤流转、停止条件与命令白名单。

战役把"提出假设 → 检验 → 评审"交给一轮轮独立的 headless agent 会话，平台只做三件事：

1. **决定下一步是谁**：研究员提交评审（``review_requested``）后由评审员接手，
   其余情况进入下一轮研究员；失败的步骤原地重试。
2. **决定什么时候停**：试验增量、研究线预算、墙钟时长、轮数、连续否决数、
   连续失败步骤数任一触顶即停；研究员回报 ``no_idea`` 视为战役完成。
3. **决定 agent 能执行什么**：战役模式下 CLI 只放行研究所需的命令；
   promote、非严格接受、超预算放行、生命周期与验证期操作一律拒绝；
   诊断命令按战役限次，写文件的命令只能写到战役工作目录。

不读取数据库、不依赖服务层。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

ROLE_RESEARCHER = "researcher"
ROLE_REVIEWER = "reviewer"
ROLES: tuple[str, ...] = (ROLE_RESEARCHER, ROLE_REVIEWER)

# 研究员每轮的回报结果
OUTCOME_REVIEW_REQUESTED = "review_requested"
OUTCOME_NO_IDEA = "no_idea"
RESEARCHER_OUTCOMES: tuple[str, ...] = (
    OUTCOME_REVIEW_REQUESTED,  # 已有评估完成的优化会话，请求评审
    "rejected",  # 本轮假设在探索 / 评估阶段即被否决（已关闭）
    "blocked",  # 配置写不出来（假设已关闭为 blocked_by_schema）
    OUTCOME_NO_IDEA,  # 研究员判断范围内没有值得检验的新方向
)
# 评审员结论
VERDICT_RECOMMEND_ACCEPT = "recommend_accept"
VERDICT_NEEDS_MORE = "needs_more"
REVIEWER_VERDICTS: tuple[str, ...] = (VERDICT_RECOMMEND_ACCEPT, "reject", VERDICT_NEEDS_MORE)

STATUS_RUNNING = "running"
STATUS_STOPPED = "stopped"
STATUS_COMPLETED = "completed"

STEP_RUNNING = "running"
STEP_DONE = "done"
STEP_FAILED = "failed"

# 角色手册目录（相对仓库根目录）
SKILL_DIR = ".claude/skills/research-campaign"

# 连续失败多少步即停（agent 反复报错时继续烧钱没有意义）
MAX_CONSECUTIVE_FAILURES = 2

# 战役模式下允许的命令：{命令组: 允许的子命令集合}；None 表示整组放行
_ALLOWED_COMMANDS: dict[str, frozenset[str] | None] = {
    "strategy": frozenset(
        {"list", "show", "validate", "diff", "create", "update", "prune-variants"}
    ),
    "backtest": frozenset(
        {"run", "list", "status", "show", "results", "pool", "batch", "execute", "cancel"}
    ),
    "research": None,
    "robustness": None,
    "optimization": None,
    "queue": frozenset({"stats", "jobs"}),
}
# 整组放行的命令组里仍须拒绝的子命令：(命令组, 子命令)。
# dsr-calibrate 校准的是验收门槛本身，属于平台治理，不是战役内的研究动作
_DENIED_SUBCOMMANDS: frozenset[tuple[str, str]] = frozenset({("research", "dsr-calibrate")})
# research campaign 子命令中 agent 可用的部分：不得自行开新战役、驱动战役或停止战役
_ALLOWED_CAMPAIGN_ACTIONS: frozenset[str] = frozenset(
    {"status", "list", "step-done", "review", "report"}
)
# 会按 --file 写文件的命令：(命令组, 子命令, research campaign 动作)；战役内只能写到工作目录
_FILE_WRITING_COMMANDS: frozenset[tuple[str, str, str | None]] = frozenset(
    {
        ("optimization", "report", None),
        ("research", "campaign", "report"),
    }
)
# 诊断命令（只读、不计入 N）：战役内按次数限额，避免先筛几十个方向再只检验最好的一个
DIAGNOSTIC_COMMANDS: frozenset[tuple[str, str]] = frozenset(
    {
        ("research", "factor-ic"),
        ("research", "buckets"),
        ("research", "signal-lab"),
    }
)
# 战役诊断次数缺省上限
DEFAULT_MAX_DIAGNOSTICS = 30


class CampaignRuleError(ValueError):
    """战役业务规则校验失败。"""


@dataclass(frozen=True)
class StepView:
    """判定下一步所需的步骤视图。

    Attributes:
        round: 轮次。
        role: 角色。
        status: running / done / failed。
        outcome: 回报结果或评审结论。
        optimization_id: 提交评审 / 被评审的优化会话 ID。
        hypothesis_id: 涉及的假设 ID。
    """

    round: int
    role: str
    status: str
    outcome: str | None = None
    optimization_id: str | None = None
    hypothesis_id: str | None = None


@dataclass(frozen=True)
class NextStep:
    """下一步安排。

    Attributes:
        role: 角色。
        round: 轮次。
        optimization_id: 评审员要评审的优化会话 ID。
        hypothesis_id: 相关假设 ID（评审对象，或研究员需继续补证据的假设）。
        retry: 是否为失败步骤的重试。
    """

    role: str
    round: int
    optimization_id: str | None = None
    hypothesis_id: str | None = None
    retry: bool = False


def plan_next_step(steps: Sequence[StepView]) -> NextStep | None:
    """根据已有步骤（按开始时间升序）决定下一步。

    Args:
        steps: 战役的全部步骤。

    Returns:
        下一步安排；最后一步仍在运行时返回 None（需等待或人工收口）。
    """
    if not steps:
        return NextStep(role=ROLE_RESEARCHER, round=1)
    last = steps[-1]
    if last.status == STEP_RUNNING:
        return None
    if last.status == STEP_FAILED:
        return NextStep(
            role=last.role,
            round=last.round,
            optimization_id=last.optimization_id,
            hypothesis_id=last.hypothesis_id,
            retry=True,
        )
    if last.role == ROLE_RESEARCHER and last.outcome == OUTCOME_REVIEW_REQUESTED:
        return NextStep(
            role=ROLE_REVIEWER,
            round=last.round,
            optimization_id=last.optimization_id,
            hypothesis_id=last.hypothesis_id,
        )
    if last.role == ROLE_REVIEWER and last.outcome == VERDICT_NEEDS_MORE:
        # 评审要求补证据：下一轮研究员继续同一假设
        return NextStep(
            role=ROLE_RESEARCHER,
            round=last.round + 1,
            optimization_id=last.optimization_id,
            hypothesis_id=last.hypothesis_id,
        )
    return NextStep(role=ROLE_RESEARCHER, round=last.round + 1)


def count_consecutive_failures(steps: Sequence[StepView]) -> int:
    """统计末尾连续失败的步骤数。

    Args:
        steps: 战役的全部步骤（按开始时间升序）。

    Returns:
        连续失败数。
    """
    count = 0
    for step in reversed(steps):
        if step.status != STEP_FAILED:
            break
        count += 1
    return count


def count_consecutive_rejects(closed_statuses: Sequence[str]) -> int:
    """统计按关闭时间升序排列的假设结论中，末尾连续未被接受的个数。

    Args:
        closed_statuses: 战役内已关闭假设的状态（按 closed_at 升序）。

    Returns:
        连续未接受（rejected / inconclusive / blocked_by_schema）的个数。
    """
    count = 0
    for status in reversed(closed_statuses):
        if status == "accepted":
            break
        count += 1
    return count


def evaluate_stop(
    *,
    trials_used: int,
    max_trials: int,
    lineage_remaining: int,
    elapsed_hours: float,
    max_hours: float,
    rounds_done: int,
    max_rounds: int,
    consecutive_rejects: int,
    max_consecutive_rejects: int,
    consecutive_failures: int,
    next_is_new_round: bool,
) -> str | None:
    """判断战役是否应当停止。

    轮数与连续否决只在"即将开始新一轮研究员"时检查：评审员必须能把已经提交的
    评审做完，否则最后一轮的候选会停在没人评审的状态。

    Args:
        trials_used: 战役开始以来研究线新增的试验次数。
        max_trials: 战役试验增量上限。
        lineage_remaining: 研究线预算剩余次数。
        elapsed_hours: 已用墙钟时长（小时）。
        max_hours: 时长上限。
        rounds_done: 已完成的研究员轮数。
        max_rounds: 轮数上限。
        consecutive_rejects: 连续未接受的假设数。
        max_consecutive_rejects: 连续否决上限。
        consecutive_failures: 末尾连续失败的步骤数。
        next_is_new_round: 下一步是否为新一轮研究员。

    Returns:
        停止原因；不需要停止时为 None。
    """
    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
        return f"连续 {consecutive_failures} 个步骤失败"
    if elapsed_hours >= max_hours:
        return f"已运行 {elapsed_hours:.1f} 小时，达到时长上限 {max_hours:g} 小时"
    if trials_used >= max_trials:
        return f"本战役新增试验 {trials_used} 次，达到上限 {max_trials}"
    if lineage_remaining <= 0:
        return "研究线试验预算已用尽（战役内不允许 --over-budget）"
    if next_is_new_round and rounds_done >= max_rounds:
        return f"已完成 {rounds_done} 轮研究，达到轮数上限 {max_rounds}"
    if next_is_new_round and consecutive_rejects >= max_consecutive_rejects:
        return f"连续 {consecutive_rejects} 个假设未被接受，达到上限 {max_consecutive_rejects}"
    return None


def validate_outcome(role: str, outcome: str) -> str:
    """校验步骤回报结果与角色是否匹配。

    Args:
        role: 角色。
        outcome: 回报结果。

    Returns:
        原样返回合法结果。

    Raises:
        CampaignRuleError: 结果不属于该角色。
    """
    allowed = RESEARCHER_OUTCOMES if role == ROLE_RESEARCHER else REVIEWER_VERDICTS
    if outcome not in allowed:
        raise CampaignRuleError(f"{role} 的回报结果只能是 {', '.join(allowed)}（收到 {outcome!r}）")
    return outcome


def check_command_allowed(
    command: str, subcommand: str | None, flags: Mapping[str, Any]
) -> str | None:
    """检查战役模式下某条 CLI 命令是否放行（只看命令本身与参数，不查库）。

    Args:
        command: 命令组（如 ``optimization``）。
        subcommand: 子命令（如 ``finish``）。
        flags: 解析后的参数（``vars(args)``）。

    Returns:
        拒绝原因；放行时为 None。
    """
    if command not in _ALLOWED_COMMANDS:
        return f"战役模式不允许执行 {command} 命令组"
    allowed = _ALLOWED_COMMANDS[command]
    if allowed is not None and subcommand not in allowed:
        return f"战役模式不允许执行 {command} {subcommand}"
    if (command, subcommand) in _DENIED_SUBCOMMANDS:
        return f"战役模式不允许执行 {command} {subcommand}"
    if command == "research" and subcommand == "campaign":
        action = flags.get("campaign_action")
        if action not in _ALLOWED_CAMPAIGN_ACTIONS:
            return f"战役内的 agent 不能执行 research campaign {action}"
    if flags.get("over_budget"):
        return "战役模式不允许 --over-budget：预算用尽即停止战役"
    if command == "optimization" and subcommand == "finish":
        if flags.get("promote"):
            return "战役模式不允许 --promote：promote 必须由人工执行"
        if flags.get("strict") is False:
            return "战役模式不允许 --no-strict：接受必须通过全部验收项"
    if command == "backtest" and subcommand == "run":
        purpose = flags.get("purpose") or "research"
        if purpose != "research":
            return f"战役模式只允许 purpose=research 的回测（收到 {purpose}）"
    if command == "strategy" and subcommand == "update":
        if flags.get("status") == "active":
            return "战役模式不允许把策略改为 active：启用策略由人工决定"
        if flags.get("lineage"):
            return "战役模式不允许改归研究线：研究线成员关系由人工调整"
    if command == "strategy" and subcommand == "create" and not flags.get("draft"):
        return "战役内新建策略必须加 --draft：启用策略由人工决定"
    return None


def resolve_campaign_mode(
    env_campaign_id: str | None,
    desktop_step: tuple[str, str] | None,
    caller_session_id: str | None,
) -> str | None:
    """判定一次 CLI 调用是否处于战役模式（以及属于哪个战役）。

    两种来源：
    - headless 驱动器给 agent 进程设置的 ``QUANT_CAMPAIGN_ID``；
    - 桌面编排模式下有运行中步骤时，除编排会话自身外的所有调用（子会话、终端）
      都进入该战役的战役模式——子会话无法注入环境变量，也就不能靠"自觉"进入。

    Args:
        env_campaign_id: 环境变量中的战役 ID。
        desktop_step: 桌面模式运行中步骤的 (战役 ID, 编排会话 ID)；没有时为 None。
        caller_session_id: 调用方所在的 Claude Code 会话 ID（``CLAUDE_CODE_SESSION_ID``）。

    Returns:
        战役 ID；不在战役模式时为 None。
    """
    if env_campaign_id:
        return env_campaign_id
    if desktop_step is None:
        return None
    campaign_id, orchestrator = desktop_step
    if caller_session_id and caller_session_id == orchestrator:
        return None
    return campaign_id


def build_agent_prompt(
    card: Mapping[str, Any], *, skill_dir: str, desktop: bool, python_cmd: str = "python"
) -> str:
    """把任务卡组装成 agent 一步的输入提示（headless 与桌面编排模式共用）。

    Args:
        card: ``next_task`` 返回的任务卡。
        skill_dir: 角色手册目录（相对仓库根目录）。
        desktop: 是否为桌面编排模式（子会话权限不受驱动器设置约束，需在提示里写明边界）。
        python_cmd: 运行平台 CLI 的解释器（桌面子会话的 PATH 不含项目虚拟环境时须显式给出）。

    Returns:
        提示文本。
    """
    step = card["next"]
    role = step["role"]
    cid = card["campaign_id"]
    cli = f"{python_cmd} -m quant_etf_api.cli"
    if role == ROLE_RESEARCHER:
        finish = (
            f"{cli} research campaign step-done {cid} "
            '--outcome <review_requested|rejected|blocked|no_idea> --summary "<结论>" '
            "[--hypothesis-id <hid>] [--optimization-id <oid>]"
        )
    else:
        finish = (
            f"{cli} research campaign review {cid} "
            f"--optimization-id {step.get('optimization_id')} "
            '--verdict <recommend_accept|reject|needs_more> --reason "<理由>"，'
            f"处理完会话与假设后再执行 {cli} research campaign step-done {cid} "
            '--summary "<结论>"'
        )
    role_name = {ROLE_RESEARCHER: "研究员", ROLE_REVIEWER: "评审员"}.get(role, role)
    session_kind = "桌面" if desktop else "headless"
    lines = [
        f"你是研究战役 {cid} 的{role_name}（{role}），"
        f"第 {step['round']} 轮{'（上一次失败后的重试）' if step.get('retry') else ''}。"
        f"你在一个全新的 {session_kind} 会话中执行这一步，看不到之前任何会话的内容，"
        "只能依据下面的任务卡与平台数据。",
        "",
        f"开始前完整阅读 {skill_dir}/SKILL.md 与 {skill_dir}/references/{role}.md，"
        "严格按其中流程执行；平台契约以 AGENTS.md 与 CLI --help 为准。",
        "所有平台操作只能用 `python -m quant_etf_api.cli ...`，工作目录就是仓库根目录，不要 cd；"
        f"文件只能写在 {card['workspace']}。",
    ]
    if python_cmd != "python":
        lines.append(
            f"本机 PATH 上的 python 不是项目虚拟环境：手册与 --help 中的 `python -m quant_etf_api.cli` "
            f"一律用 `{cli}` 执行（同一个 CLI、同样的参数）。"
        )
    if desktop:
        lines.append(
            "桌面编排模式的额外边界（平台无法用权限设置强制，必须自觉遵守）：不要修改仓库中的"
            "任何代码、配置或文档；不要运行 git、curl、pip、alembic 或 `python -c` 等平台 CLI "
            f"以外的命令；不要读取 candidates/ 下除 {card['workspace']} 以外的目录；"
            "不要新开会话或子 agent；不要向用户提问（无人值守），遇到阻塞写进摘要后回报。"
            "CLI 已按数据库自动进入战役模式，被拒绝的命令不要绕路。"
        )
    lines += [
        f"本步结束前必须执行：{finish}",
        "",
        "任务卡（JSON）：",
        json.dumps(card, ensure_ascii=False, indent=2, default=str),
        "",
    ]
    return "\n".join(lines)


def file_output_target(
    command: str, subcommand: str | None, flags: Mapping[str, Any]
) -> str | None:
    """返回命令将要写入的文件路径（只认会按 ``--file`` 写文件的命令）。

    Args:
        command: 命令组。
        subcommand: 子命令。
        flags: 解析后的参数。

    Returns:
        目标路径；命令不写文件或未给 ``--file`` 时为 None。
    """
    action = flags.get("campaign_action") if command == "research" else None
    if (command, subcommand, action) not in _FILE_WRITING_COMMANDS:
        return None
    target = flags.get("file")
    return str(target) if target else None


def is_diagnostic_command(command: str, subcommand: str | None) -> bool:
    """是否为计入战役诊断配额的诊断命令。

    Args:
        command: 命令组。
        subcommand: 子命令。

    Returns:
        属于诊断命令时为 True。
    """
    return (command, subcommand or "") in DIAGNOSTIC_COMMANDS


def check_diagnostic_quota(used: int, limit: int) -> str | None:
    """检查战役诊断配额。

    Args:
        used: 已执行的诊断次数。
        limit: 上限。

    Returns:
        超限时的拒绝原因；仍有余量时为 None。
    """
    if used >= limit:
        return (
            f"本战役诊断命令已用 {used}/{limit} 次，不再放行：用已有诊断结论登记假设并检验，"
            "或回报 no_idea"
        )
    return None
