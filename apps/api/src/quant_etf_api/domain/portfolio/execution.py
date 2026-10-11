"""回测逐日执行模拟（纯领域逻辑）。

把"引擎给出的目标持仓"转换为"实际持仓、当日收益与换手"，与回测服务的数据读取、
落库与告警编排分离，便于逐种执行模型单独测试。

记账口径（``ACCOUNTING_MODEL_DRIFT``）：

1. **持仓随行情漂移**：两次调仓之间各资产权重按 ``w_i × (1 + r_i) / (1 + R_p)``
   更新，而不是每天被"免费"调回目标权重（历史口径 ``constant_mix_v0`` 等价于
   每日再平衡，既改变收益，也让调仓日换手按未漂移的目标比较而被低估）；
2. **调仓日两段复利**：T+1 开盘执行时旧仓位吃隔夜段、新仓位吃日内段，两段按
   ``(1 + R_隔夜)(1 + R_日内) - 1`` 衔接，换手按"隔夜漂移后的开盘持仓 → 新目标"计算；
3. **收益行情前向填充**：持仓资产某日缺收盘价时，该段收益记 0，缺口之后的首个
   有效收盘价相对缺口前最后一个有效收盘价结算，缺口期间的涨跌不再永久丢失
   （见 :func:`forward_fill_return_bars`）；
4. **无终端行**：最后一个交易日没有下一交易日，不产生收益窗口，回测逐日序列
   只覆盖有实现收益的决策日，不再人为加入一天 0 收益样本。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from quant_etf_api.domain.common.numeric import price_invalid
from quant_etf_api.domain.portfolio.returns import (
    count_missing_allocation_assets,
    count_missing_rebalance_assets,
    get_index_rebalance_legs,
    get_index_return,
)
from quant_etf_api.domain.portfolio.turnover import compute_turnover

#: 历史记账口径：非调仓日按固定权重计收益（等价于每日再平衡），
#: 调仓日两段收益直接相加，缺行情段收益永久丢失，且末日记一行 0 收益。
ACCOUNTING_MODEL_LEGACY = "constant_mix_v0"

#: 当前记账口径：持仓漂移 + 调仓日两段复利 + 收益行情前向填充 + 无终端行。
ACCOUNTING_MODEL_DRIFT = "drift_v1"

EXECUTION_MODEL_OPEN = "t_plus_1_open"
EXECUTION_MODEL_CLOSE = "t_plus_1_close"

# 漂移后权重低于该值视为已清零，避免浮点残渣占据仓位键位
_WEIGHT_EPSILON = 1e-12


@dataclass(frozen=True)
class FilledBar:
    """收益结算用的合成行情（仅含开盘/收盘价）。

    Attributes:
        open_price: 开盘价；原始开盘价缺失时取收盘价（隔夜段吃满全天、日内段为 0）。
        close_price: 收盘价；原始收盘价缺失时取缺口前最后一个有效收盘价。
    """

    open_price: float
    close_price: float


@dataclass(frozen=True)
class DayExecution:
    """单个决策日的执行结果。

    Attributes:
        portfolio_return: 收益窗口 [收盘 T, 收盘 T+1] 的组合收益率（%，4 位小数）。
        positions: 收益窗口起点的实际持仓（调仓日为成交后的新仓位）。
        executed_positions: 当日实际承担收益的持仓（与 positions 一致，保留字段语义）。
        turnover: 当日成交的单边换手率（Σ|Δw| / 2，按漂移后的实际持仓比较）。
        missing_bar_count: 受原始行情缺失影响的持仓资产数（数据缺口统计口径）。
    """

    portfolio_return: float
    positions: dict[str, float]
    executed_positions: dict[str, float]
    turnover: float
    missing_bar_count: int


def forward_fill_return_bars(
    all_bars: dict[tuple[str, date], Any],
    index_codes: list[str],
    trading_dates: list[date],
) -> dict[tuple[str, date], Any]:
    """构造收益结算专用的前向填充行情。

    只用于组合/基准/指数收益结算，**不得**用于候选池、因子计算或数据缺口统计
    （那些必须看原始行情）。规则：

    - 原始行情开盘价、收盘价都有效：原样保留；
    - 收盘价有效、开盘价缺失：合成 ``open = close``（旧仓位隔夜段吃满全天）；
    - 收盘价缺失：若此前（含回望窗口）存在有效收盘价，合成为前值平盘 bar，
      使缺口日收益为 0、缺口后首日相对缺口前收盘价结算；从未有过有效收盘价时
      不填充（指数尚未发布）。

    Args:
        all_bars: 原始行情，key=(index_code, trade_date)。
        index_codes: 需要结算收益的指数代码（标的池 + 基准）。
        trading_dates: 回测交易日（升序）。

    Returns:
        新的行情映射（不修改入参）；对象需具备 open_price / close_price 属性。
    """
    if not trading_dates:
        return dict(all_bars)
    wanted = set(index_codes)
    first_day = trading_dates[0]
    # 回望窗口内最后一个有效收盘价：作为首个交易日缺行情时的前值
    seed: dict[str, tuple[date, float]] = {}
    for (code, day), bar in all_bars.items():
        if code not in wanted or day >= first_day:
            continue
        close = getattr(bar, "close_price", None)
        if price_invalid(close):
            continue
        current = seed.get(code)
        if current is None or day > current[0]:
            seed[code] = (day, float(close))

    filled: dict[tuple[str, date], Any] = dict(all_bars)
    last_close: dict[str, float] = {code: close for code, (_, close) in seed.items()}
    for day in trading_dates:
        for code in index_codes:
            bar = all_bars.get((code, day))
            close = getattr(bar, "close_price", None) if bar is not None else None
            if not price_invalid(close):
                last_close[code] = float(close)
                if price_invalid(getattr(bar, "open_price", None)):
                    filled[(code, day)] = FilledBar(
                        open_price=float(close), close_price=float(close)
                    )
                continue
            previous = last_close.get(code)
            if previous:
                filled[(code, day)] = FilledBar(open_price=previous, close_price=previous)
    return filled


def drift_weights(
    weights: dict[str, float],
    asset_returns_pct: dict[str, float | None],
    portfolio_return_pct: float,
) -> dict[str, float]:
    """把收益窗口起点的权重推进到窗口终点（持仓随行情漂移）。

    ``w_i' = w_i × (1 + r_i) / (1 + R_p)``；资产收益缺失按 0 处理（与组合收益口径
    一致），现金权重隐含为 ``1 - Σw'``。

    Args:
        weights: 窗口起点权重。
        asset_returns_pct: 各资产窗口收益率（%），None 表示缺失。
        portfolio_return_pct: 窗口组合收益率（%）。

    Returns:
        窗口终点权重（不取整，接近 0 的权重被剔除）。
    """
    growth = 1 + portfolio_return_pct / 100
    if growth <= 0:
        # 组合净值归零属于极端情形，保持权重不变避免除零放大
        return dict(weights)
    drifted: dict[str, float] = {}
    for code in sorted(weights):
        ret = asset_returns_pct.get(code)
        value = weights[code] * (1 + (ret or 0.0) / 100) / growth
        if value > _WEIGHT_EPSILON:
            drifted[code] = value
    return drifted


def _hold_window(
    weights: dict[str, float],
    trade_date: date,
    next_date: date,
    return_bars: dict[tuple[str, date], Any],
) -> tuple[float, dict[str, float]]:
    """按收盘到收盘持有一个收益窗口。

    Args:
        weights: 窗口起点权重。
        trade_date: 窗口起始交易日。
        next_date: 窗口结束交易日。
        return_bars: 收益结算用行情（前向填充后）。

    Returns:
        (组合收益率 %, 窗口终点漂移权重)。
    """
    asset_returns = {
        code: get_index_return(code, trade_date, next_date, return_bars) for code in weights
    }
    total = 0.0
    for code in sorted(weights):
        ret = asset_returns[code]
        if ret is not None:
            total += weights[code] * ret
    return total, drift_weights(weights, asset_returns, total)


class BacktestExecutor:
    """回测逐日执行器：维护实际持仓，按执行模型结算收益与换手。

    使用方式：每个决策日先用 :attr:`composition_base` 作为"调仓前实际持仓"合成
    当日目标（选股腿/风险腿），再调用 :meth:`step` 结算该日收益窗口。

    Args:
        execution_model: ``t_plus_1_open``（默认）或 ``t_plus_1_close``。
    """

    def __init__(self, execution_model: str = EXECUTION_MODEL_OPEN) -> None:
        """初始化执行器。

        Args:
            execution_model: 执行模型。

        Raises:
            ValueError: 执行模型未知时抛出。
        """
        if execution_model not in (EXECUTION_MODEL_OPEN, EXECUTION_MODEL_CLOSE):
            raise ValueError(f"未知的执行模型: {execution_model}")
        self._model = execution_model
        # 当前实际持仓：已随行情漂移到最近一个收盘
        self._holdings: dict[str, float] = {}
        # T+1 收盘模式下尚未成交的上一决策日指令
        self._pending: dict[str, float] | None = None

    @property
    def holdings(self) -> dict[str, float]:
        """当前实际持仓（漂移到最近收盘后的权重）。"""
        return dict(self._holdings)

    @property
    def composition_base(self) -> dict[str, float]:
        """合成当日目标时应视为"调仓前实际持仓"的权重。

        T+1 收盘模式先成交昨日指令、再合成今日指令：仅风险腿到期时必须沿用今日
        收盘成交后的成分，避免换回旧成分或把已清空的仓位重新建立。

        Returns:
            调仓前实际持仓。
        """
        if self._model == EXECUTION_MODEL_CLOSE and self._pending is not None:
            return dict(self._pending)
        return dict(self._holdings)

    def step(
        self,
        *,
        trade_date: date,
        next_date: date,
        target_positions: dict[str, float],
        should_rebalance: bool,
        return_bars: dict[tuple[str, date], Any],
        quality_bars: dict[tuple[str, date], Any],
    ) -> DayExecution:
        """结算一个决策日的收益窗口 [收盘 T, 收盘 T+1]。

        Args:
            trade_date: 决策日 T。
            next_date: 下一交易日 T+1。
            target_positions: 当日合成后的目标持仓（仅 should_rebalance 时生效）。
            should_rebalance: 当日是否有调仓腿到期。
            return_bars: 收益结算用行情（前向填充后）。
            quality_bars: 原始行情，仅用于统计数据缺口资产数。

        Returns:
            当日执行结果。
        """
        if self._model == EXECUTION_MODEL_CLOSE:
            return self._step_close(
                trade_date, next_date, target_positions, should_rebalance, return_bars, quality_bars
            )
        return self._step_open(
            trade_date, next_date, target_positions, should_rebalance, return_bars, quality_bars
        )

    def _step_open(
        self,
        trade_date: date,
        next_date: date,
        target_positions: dict[str, float],
        should_rebalance: bool,
        return_bars: dict[tuple[str, date], Any],
        quality_bars: dict[tuple[str, date], Any],
    ) -> DayExecution:
        """T+1 开盘执行：调仓日旧仓位吃隔夜段、新仓位吃日内段，两段复利。

        Args:
            trade_date: 决策日 T。
            next_date: 下一交易日 T+1。
            target_positions: 当日目标持仓。
            should_rebalance: 当日是否调仓。
            return_bars: 收益结算用行情。
            quality_bars: 原始行情（缺口统计）。

        Returns:
            当日执行结果。
        """
        previous = self._holdings
        if not should_rebalance:
            portfolio_return, end_weights = _hold_window(
                previous, trade_date, next_date, return_bars
            )
            self._holdings = end_weights
            return DayExecution(
                portfolio_return=round(portfolio_return, 4),
                positions=dict(previous),
                executed_positions=dict(previous),
                turnover=0.0,
                missing_bar_count=count_missing_allocation_assets(
                    previous, trade_date, next_date, quality_bars
                ),
            )

        new_positions = dict(target_positions)
        overnight: dict[str, float | None] = {}
        intraday: dict[str, float | None] = {}
        for code in sorted(set(previous) | set(new_positions)):
            overnight[code], intraday[code] = get_index_rebalance_legs(
                code, trade_date, next_date, return_bars
            )
        overnight_return = sum(
            previous[code] * (overnight[code] or 0.0) for code in sorted(previous)
        )
        # 换手按"隔夜漂移后的开盘持仓 → 新目标"计算：成交发生在 T+1 开盘
        weights_at_open = drift_weights(previous, overnight, overnight_return)
        turnover = compute_turnover(weights_at_open, new_positions)
        intraday_return = sum(
            new_positions[code] * (intraday[code] or 0.0) for code in sorted(new_positions)
        )
        portfolio_return = ((1 + overnight_return / 100) * (1 + intraday_return / 100) - 1) * 100
        self._holdings = drift_weights(new_positions, intraday, intraday_return)
        return DayExecution(
            portfolio_return=round(portfolio_return, 4),
            positions=new_positions,
            executed_positions=dict(new_positions),
            turnover=turnover,
            missing_bar_count=count_missing_rebalance_assets(
                previous, new_positions, trade_date, next_date, quality_bars
            ),
        )

    def _step_close(
        self,
        trade_date: date,
        next_date: date,
        target_positions: dict[str, float],
        should_rebalance: bool,
        return_bars: dict[tuple[str, date], Any],
        quality_bars: dict[tuple[str, date], Any],
    ) -> DayExecution:
        """T+1 收盘执行：先在今日收盘成交昨日指令，再持有至次日收盘。

        Args:
            trade_date: 决策日 T（同时是上一决策日指令的成交日）。
            next_date: 下一交易日 T+1。
            target_positions: 当日目标持仓（次日收盘成交）。
            should_rebalance: 当日是否调仓。
            return_bars: 收益结算用行情。
            quality_bars: 原始行情（缺口统计）。

        Returns:
            当日执行结果。
        """
        if self._pending is not None:
            # 成交腿 = 漂移到今日收盘的实际持仓 → 昨日指令
            turnover = compute_turnover(self._holdings, self._pending)
            executed = dict(self._pending)
        else:
            turnover = 0.0
            executed = dict(self._holdings)
        portfolio_return, end_weights = _hold_window(executed, trade_date, next_date, return_bars)
        self._holdings = end_weights
        self._pending = dict(target_positions) if should_rebalance else None
        return DayExecution(
            portfolio_return=round(portfolio_return, 4),
            positions=executed,
            executed_positions=dict(executed),
            turnover=turnover,
            missing_bar_count=count_missing_allocation_assets(
                executed, trade_date, next_date, quality_bars
            ),
        )
