"""组合策略的持仓级合成（纯领域逻辑）。

成员策略彼此独立决策（成员的选股、择时、风控都不依赖组合层），因此可以先把每个成员
在内存里完整跑一遍，再逐日合成，结果与"同一循环里并排执行"完全等价。

记账口径（``COMPOSITE_MODEL``）：

- 组合收益 r_c = Σ W_m × r_m（现金收益为 0），W_m 为窗口起点的成员权重；
- 成员权重随成员净值漂移：W_m' = W_m (1 + r_m) / (1 + r_c)，在再平衡日的窗口起点调回目标；
- 组合持仓 = Σ W_m × 成员持仓；换手按合成后的持仓计算：成交前持仓取各成员成交前持仓
  按上一窗口终点权重合成，成交后持仓按本窗口起点权重合成，因此成员之间方向相反的
  交易被轧差，再平衡调回目标权重带来的交易也计入换手。

开盘执行模型下成员的隔夜段使组合权重在开盘前略有漂移，换手按收盘权重近似；
收盘执行模型（研究缺省）下口径精确。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

#: 组合策略记账口径指纹
COMPOSITE_MODEL = "position_blend_v1"


@dataclass(frozen=True)
class MemberDay:
    """成员在某个决策日的执行结果。

    Attributes:
        portfolio_return: 收益窗口 [T, T+1] 的成员收益（%）。
        pre_trade: 成交前一刻的成员持仓。
        post_trade: 窗口起点承担收益的成员持仓（成交后）。
    """

    portfolio_return: float
    pre_trade: Mapping[str, float]
    post_trade: Mapping[str, float]


@dataclass(frozen=True)
class CompositeDay:
    """组合在某个决策日的合成结果。

    Attributes:
        trade_date: 决策日。
        portfolio_return: 组合收益（%）。
        positions: 窗口起点的合成持仓。
        turnover: 轧差后的单边换手。
        member_weights: 窗口起点的成员权重。
    """

    trade_date: date
    portfolio_return: float
    positions: dict[str, float]
    turnover: float
    member_weights: dict[str, float]


def _combine(
    weights: Mapping[str, float], books: Mapping[str, Mapping[str, float]]
) -> dict[str, float]:
    """按成员权重合成持仓。"""
    combined: dict[str, float] = {}
    for member, weight in weights.items():
        for code, value in books.get(member, {}).items():
            combined[code] = combined.get(code, 0.0) + weight * value
    return {code: value for code, value in combined.items() if value > 1e-12}


def blend_members(
    dates: Sequence[date],
    member_days: Mapping[str, Mapping[date, MemberDay]],
    target_weights: Mapping[str, float],
    rebalance_dates: set[date] | None = None,
) -> list[CompositeDay]:
    """逐日合成成员结果。

    Args:
        dates: 升序决策日（各成员共同的决策日）。
        member_days: 成员 ID → {决策日: MemberDay}。
        target_weights: 成员目标权重（其余为现金）。
        rebalance_dates: 成员权重调回目标的决策日；None 表示每日（恒定比例）。

    Returns:
        与 dates 等长的合成结果。
    """
    results: list[CompositeDay] = []
    end_weights: dict[str, float] | None = None
    for day in dates:
        previous = end_weights if end_weights is not None else dict(target_weights)
        rebalance = end_weights is None or rebalance_dates is None or day in rebalance_dates
        start = dict(target_weights) if rebalance else dict(previous)
        days = {member: member_days[member][day] for member in target_weights}
        pre = _combine(previous, {m: d.pre_trade for m, d in days.items()})
        post = _combine(start, {m: d.post_trade for m, d in days.items()})
        turnover = sum(
            abs(post.get(code, 0.0) - pre.get(code, 0.0)) for code in set(pre) | set(post)
        ) / 2
        composite_return = sum(start[m] * days[m].portfolio_return for m in start)
        growth = 1 + composite_return / 100
        end_weights = {
            m: (start[m] * (1 + days[m].portfolio_return / 100) / growth if growth > 0 else start[m])
            for m in start
        }
        results.append(
            CompositeDay(
                trade_date=day,
                portfolio_return=round(composite_return, 4),
                positions=post,
                turnover=turnover,
                member_weights=start,
            )
        )
    return results
