"""研究台账的纯业务规则：假设状态流转、试验次数统计与研究批量评估摘要。

台账把"提出假设 → 检验 → 结论"固化下来，并为多重检验提供试验次数 N：

- 假设登记时必须写定判定标准（事先登记），状态从 ``open`` 开始；
  第一次挂上试验后转为 ``testing``；关闭时给出终态与原因。
- 已关闭的假设不再接收新试验：看过结论后再追加试验等于事后调参，
  应登记一个子假设（``parent_id``）重新检验。唯一例外是 ``blocked_by_schema``，
  schema 补齐后可以重新打开。
- N 只会被低估、不会被高估：研究批量评估每次运行都计入，不论是否挂假设。

不读取数据库、不依赖服务层。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# 假设类别（对应策略管线的模块或研究对象）
HYPOTHESIS_CATEGORIES: tuple[str, ...] = (
    "timing",
    "score",
    "filter",
    "rank",
    "portfolio",
    "risk",
    "rebalance",
    "factor",
    "universe",
    "other",
)

STATUS_OPEN = "open"
STATUS_TESTING = "testing"
STATUS_BLOCKED = "blocked_by_schema"

# 进行中状态：可以继续挂试验
ACTIVE_STATUSES: tuple[str, ...] = (STATUS_OPEN, STATUS_TESTING)
# 关闭终态：必须给出结论原因
CLOSED_STATUSES: tuple[str, ...] = ("accepted", "rejected", "inconclusive", STATUS_BLOCKED)
ALL_STATUSES: tuple[str, ...] = ACTIVE_STATUSES + CLOSED_STATUSES

# 试验来源
TRIAL_KINDS: tuple[str, ...] = (
    "research_batch",
    "manual",
    "optimization",
    "robustness",
    # 派生因子的诊断记录（research factor-ic / buckets）：不计入 N，只作为派生因子
    # 进入研究批量评估 / 优化候选的前置门槛
    "diagnostic",
)
# 计入多重检验 N 的来源；optimization / robustness 行只是关联，N 由各自的表统计
COUNTED_TRIAL_KINDS: tuple[str, ...] = ("research_batch", "manual")
# 研究批量评估中不算独立试验的变体类型：基线是对照，timing_off 是择时归因派生的对照
_NON_TRIAL_VARIANT_KINDS: frozenset[str] = frozenset({"baseline", "timing_off"})


class LedgerRuleError(ValueError):
    """台账业务规则校验失败。"""


def validate_category(category: str) -> str:
    """校验假设类别。

    Args:
        category: 类别字符串。

    Returns:
        原样返回合法类别。

    Raises:
        LedgerRuleError: 类别不在 ``HYPOTHESIS_CATEGORIES`` 中。
    """
    if category not in HYPOTHESIS_CATEGORIES:
        raise LedgerRuleError(
            f"未知的假设类别 {category!r}，可选：{', '.join(HYPOTHESIS_CATEGORIES)}"
        )
    return category


def build_pre_registration(
    criteria: str, execution_models: Sequence[str] | None = None
) -> dict[str, Any]:
    """构造事先登记的判定标准。

    Args:
        criteria: 判定标准文本（主要指标与通过阈值），不能为空。
        execution_models: 必须通过的执行模型列表，缺省为空列表（不限定）。

    Returns:
        {"criteria": str, "execution_models": list[str]}。

    Raises:
        LedgerRuleError: 判定标准为空。
    """
    text = (criteria or "").strip()
    if not text:
        raise LedgerRuleError("登记假设必须写明判定标准（--criteria），检验前写定、检验后不改")
    return {"criteria": text, "execution_models": list(execution_models or [])}


def ensure_accepts_trials(hypothesis_id: str, status: str) -> None:
    """校验假设当前状态是否还能挂新试验。

    Args:
        hypothesis_id: 假设 ID（用于错误信息）。
        status: 假设当前状态。

    Raises:
        LedgerRuleError: 假设已关闭。
    """
    if status not in ACTIVE_STATUSES:
        raise LedgerRuleError(
            f"假设 {hypothesis_id} 已关闭（{status}），不能再挂试验；"
            "看过结论后追加试验属于事后调参，请登记子假设（--parent）重新检验"
        )


def status_after_trial(status: str) -> str:
    """挂上一次试验后的假设状态（open → testing，其余不变）。

    Args:
        status: 当前状态。

    Returns:
        新状态。
    """
    return STATUS_TESTING if status == STATUS_OPEN else status


def validate_close(status: str, verdict: str, conclusion: str) -> str:
    """校验关闭假设的请求。

    Args:
        status: 当前状态。
        verdict: 目标终态。
        conclusion: 结论与原因。

    Returns:
        去除首尾空白后的结论文本。

    Raises:
        LedgerRuleError: 终态非法、假设已关闭或结论为空。
    """
    if verdict not in CLOSED_STATUSES:
        raise LedgerRuleError(f"未知的结论 {verdict!r}，可选：{', '.join(CLOSED_STATUSES)}")
    if status not in ACTIVE_STATUSES:
        raise LedgerRuleError(f"假设已关闭（{status}），不能重复关闭")
    text = (conclusion or "").strip()
    if not text:
        raise LedgerRuleError("关闭假设必须写明结论原因（--reason）")
    return text


def validate_reopen(status: str) -> None:
    """校验重新打开假设的请求：只有受 schema 限制而搁置的假设可以重开。

    Args:
        status: 当前状态。

    Raises:
        LedgerRuleError: 假设不是 ``blocked_by_schema``。
    """
    if status != STATUS_BLOCKED:
        raise LedgerRuleError(
            f"只有 {STATUS_BLOCKED} 状态的假设可以重新打开（当前 {status}）；其他结论请登记子假设"
        )


def summarize_trial_breakdown(
    *,
    robustness: int,
    optimization: int,
    ledger: Mapping[tuple[str, bool], int],
) -> dict[str, int]:
    """合并各来源的试验次数，得到多重检验的 N 拆分。

    Args:
        robustness: 稳健性批次变体总数。
        optimization: 已评估优化会话数。
        ledger: 台账中计入 N 的 {(kind, is_estimated): 次数}。

    Returns:
        {"robustness", "optimization", "research_batch", "manual", "estimated"}；
        前四项相加即 N，``estimated`` 是 research_batch + manual 中补录估算的部分
        （已包含在前两项里，仅供复核）。
    """
    research_batch = sum(v for (kind, _), v in ledger.items() if kind == "research_batch")
    manual = sum(v for (kind, _), v in ledger.items() if kind == "manual")
    estimated = sum(v for (_, is_estimated), v in ledger.items() if is_estimated)
    return {
        "robustness": int(robustness),
        "optimization": int(optimization),
        "research_batch": int(research_batch),
        "manual": int(manual),
        "estimated": int(estimated),
    }


def raw_trial_sum(breakdown: Mapping[str, int]) -> int:
    """N 拆分的原始和（不做下限处理，``estimated`` 不重复计入）。

    Args:
        breakdown: ``summarize_trial_breakdown`` 的输出。

    Returns:
        试验次数原始和。
    """
    keys = ("robustness", "optimization", "research_batch", "manual")
    return sum(int(breakdown.get(key, 0)) for key in keys)


def exceeds_budget(used: int, planned: int, budget: int) -> bool:
    """判断本次检验是否会让研究线超出试验预算。

    Args:
        used: 研究线已用试验次数。
        planned: 本次预计新增次数。
        budget: 研究线预算。

    Returns:
        已用 + 本次 > 预算时为 True。
    """
    return used + planned > budget


def total_trials(breakdown: Mapping[str, int]) -> int:
    """由拆分得到试验次数 N（至少为 1）。

    Args:
        breakdown: ``summarize_trial_breakdown`` 的输出（``estimated`` 不重复计入）。

    Returns:
        N。
    """
    return max(1, raw_trial_sum(breakdown))


def summarize_research_batch(variants: Sequence[Mapping[str, Any]]) -> tuple[int, dict[str, Any]]:
    """从研究批量评估结果中提取试验次数与关键指标摘要。

    Args:
        variants: ``ResearchBatchResult.variants``。

    Returns:
        (试验次数, 摘要)。试验次数为非基线、非择时对照变体数；摘要按变体给出
        净年化、净夏普、最大回撤与相对基线的 Δ净夏普 / 劣化窗口占比。
    """
    count = 0
    rows: list[dict[str, Any]] = []
    for variant in variants:
        kind = str(variant.get("kind") or "")
        if kind not in _NON_TRIAL_VARIANT_KINDS:
            count += 1
        aggregate = variant.get("aggregate") or {}
        metrics = aggregate.get("metrics") or {}
        net = aggregate.get("net_metrics") or {}
        delta = variant.get("vs_baseline") or {}
        rows.append(
            {
                "label": variant.get("label"),
                "kind": kind,
                "net_annualized_return_pct": net.get("net_annualized_return_pct"),
                "net_sharpe_ratio": net.get("net_sharpe_ratio"),
                "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                "delta_net_sharpe_ratio": delta.get("delta_net_sharpe_ratio"),
                "worse_window_ratio": delta.get("worse_window_ratio"),
            }
        )
    return count, {"variants": rows}
