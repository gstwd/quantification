"""优化验收的纯统计判据：分年度一致性与差异事件数。

两项判据都只比较"候选 vs 基线"的全区间逐日净收益（同一执行口径、同一成本）：

- 分年度一致性（验收第 9 项）：逐自然年计算两侧夏普，剔除候选领先最多的一年后，
  候选年均夏普仍不低于基线，且候选胜出年数不少于一半（``(n + 1) // 2``）。逐折一致性（第 7 项）每折约 2.5 年，
  粒度太粗，单一年份的偶然收益仍可能撑起整体结论。
- 差异事件数（验收第 10 项）：候选与基线逐日收益出现差异的交易日聚合成独立区段，
  要求区段数与差异天数都达到下限。改善若只来自少数几个事件（例如只拦下 5 次下跌），
  在统计上不可区分于运气。

收益序列按 {交易日: 日收益（%）} 传入；不读取数据库、不依赖服务层。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from quant_etf_api.domain.research.robustness import annualized_sharpe

# 单个自然年的最少交易日数：不足的年份（如区间首尾的残年）不参与分年度比较
MIN_DAYS_PER_YEAR = 60
# 分年度比较至少需要的完整年份数
MIN_YEARS = 3
# 逐日收益差异阈值（%）：两侧日收益相差超过 1bp 记为"有差异"，过滤浮点与漂移噪声
DIFF_EPS_PCT = 0.01
# 差异区段合并间隔：两个差异日之间相隔不超过该交易日数视为同一事件
MAX_GAP_DAYS = 2


def yearly_sharpe_comparison(
    baseline: Mapping[date, float], candidate: Mapping[date, float]
) -> dict[str, Any]:
    """逐自然年比较候选与基线的夏普，并给出剔除最好年份后的判定。

    Args:
        baseline: 基线 {交易日: 日净收益（%）}。
        candidate: 候选 {交易日: 日净收益（%）}。

    Returns:
        {"years": [{year, days, baseline_sharpe, candidate_sharpe, delta}],
        "best_year", "ex_best_baseline_mean", "ex_best_candidate_mean",
        "wins", "total_years", "pass", "reason"}；年份不足时 pass=False 并给出 reason。
    """
    common = sorted(set(baseline) & set(candidate))
    by_year: dict[int, list[date]] = {}
    for day in common:
        by_year.setdefault(day.year, []).append(day)
    years: list[dict[str, Any]] = []
    for year in sorted(by_year):
        days = by_year[year]
        if len(days) < MIN_DAYS_PER_YEAR:
            continue
        base_sharpe = annualized_sharpe([baseline[d] / 100 for d in days])
        cand_sharpe = annualized_sharpe([candidate[d] / 100 for d in days])
        years.append(
            {
                "year": year,
                "days": len(days),
                "baseline_sharpe": base_sharpe,
                "candidate_sharpe": cand_sharpe,
                "delta": cand_sharpe - base_sharpe,
            }
        )
    result: dict[str, Any] = {
        "years": years,
        "total_years": len(years),
        "best_year": None,
        "ex_best_baseline_mean": None,
        "ex_best_candidate_mean": None,
        "wins": sum(1 for item in years if item["delta"] > 0),
        "pass": False,
        "reason": None,
    }
    if len(years) < MIN_YEARS:
        result["reason"] = f"可比较的完整年份不足 {MIN_YEARS} 个（{len(years)}）"
        return result
    best = max(years, key=lambda item: item["delta"])
    rest = [item for item in years if item is not best]
    base_mean = sum(item["baseline_sharpe"] for item in rest) / len(rest)
    cand_mean = sum(item["candidate_sharpe"] for item in rest) / len(rest)
    result["best_year"] = best["year"]
    result["ex_best_baseline_mean"] = base_mean
    result["ex_best_candidate_mean"] = cand_mean
    majority = (len(years) + 1) // 2
    result["pass"] = cand_mean >= base_mean and result["wins"] >= majority
    if not result["pass"]:
        result["reason"] = (
            f"剔除最好年份 {best['year']} 后候选年均夏普 {cand_mean:.3f} vs 基线 {base_mean:.3f}，"
            f"胜出 {result['wins']}/{len(years)} 年（需 ≥ {majority}）"
        )
    return result


def difference_episodes(
    baseline: Mapping[date, float],
    candidate: Mapping[date, float],
    *,
    eps_pct: float = DIFF_EPS_PCT,
    max_gap: int = MAX_GAP_DAYS,
) -> dict[str, int]:
    """统计候选与基线逐日收益出现差异的交易日数与独立区段数。

    Args:
        baseline: 基线 {交易日: 日净收益（%）}。
        candidate: 候选 {交易日: 日净收益（%）}。
        eps_pct: 差异阈值（%），|候选 − 基线| 超过该值记为差异日。
        max_gap: 相邻差异日之间最多相隔的交易日数，仍算同一区段。

    Returns:
        {"common_days", "diff_days", "episodes"}。
    """
    common = sorted(set(baseline) & set(candidate))
    diff_positions = [
        i for i, day in enumerate(common) if abs(candidate[day] - baseline[day]) > eps_pct
    ]
    episodes = 0
    previous: int | None = None
    for position in diff_positions:
        if previous is None or position - previous > max_gap + 1:
            episodes += 1
        previous = position
    return {"common_days": len(common), "diff_days": len(diff_positions), "episodes": episodes}
