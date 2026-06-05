"""过滤模块：根据规则过滤不满足条件的资产。

支持 gt / lt / gte / lte / eq / neq / between 操作符，
AND / OR 逻辑组合。
"""

from __future__ import annotations

import logging
from typing import Protocol

from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import FilterConfig

logger = logging.getLogger(__name__)

# ── Protocol ──────────────────────────────────────────────────────────────


class FilterEngine(Protocol):
    """过滤引擎协议。"""

    def filter(
        self,
        config: FilterConfig,
        assets: dict[str, float],
        context: EngineContext,
    ) -> dict[str, float]:
        """过滤不满足条件的资产。

        Args:
            config: 过滤配置。
            assets: 资产得分，key=etf_code。
            context: 引擎上下文。

        Returns:
            过滤后的资产得分。
        """
        ...


# ── 默认实现 ──────────────────────────────────────────────────────────────


_OPERATORS = {
    "gt": lambda v, t: v > t,
    "lt": lambda v, t: v < t,
    "gte": lambda v, t: v >= t,
    "lte": lambda v, t: v <= t,
    "eq": lambda v, t: v == t,
    "neq": lambda v, t: v != t,
}


def _check_rule(value: float | None, op: str, threshold: float | list[float]) -> bool:
    """检查单条规则是否满足。

    Args:
        value: 因子值，None 表示无数据。
        op: 操作符。
        threshold: 阈值。

    Returns:
        是否满足规则，无数据时返回 False。
    """
    if value is None:
        return False
    if op == "between":
        if isinstance(threshold, list) and len(threshold) == 2:
            return threshold[0] <= value <= threshold[1]
        return False
    cmp = _OPERATORS.get(op)
    if cmp is None:
        logger.warning("未知的过滤操作符: %s", op)
        return False
    return cmp(value, threshold)


class DefaultFilterEngine:
    """默认过滤引擎。"""

    def filter(
        self,
        config: FilterConfig,
        assets: dict[str, float],
        context: EngineContext,
    ) -> dict[str, float]:
        """过滤不满足条件的资产。"""
        if not config.rules:
            return assets

        result: dict[str, float] = {}
        use_and = config.logic == "AND"

        for code, score in assets.items():
            passed = self._evaluate_asset(code, config, context, use_and)
            if passed:
                result[code] = score

        return result

    def _evaluate_asset(
        self, code: str, config: FilterConfig, context: EngineContext, use_and: bool
    ) -> bool:
        """评估单个资产是否通过所有过滤规则。"""
        for rule in config.rules:
            factor_value = context.asset_factors.get((code, rule.factor))
            rule_passed = _check_rule(factor_value, rule.op, rule.value)

            if use_and and not rule_passed:
                return False
            if not use_and and rule_passed:
                return True

        return use_and
