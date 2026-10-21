"""过滤模块：根据规则过滤不满足条件的资产。

支持 gt / lt / gte / lte / eq / neq / between 操作符，
AND / OR 逻辑组合。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol

from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import FilterConfig, FilterRule
from quant_etf_api.engine.pipeline_detail import AssetFilterDetail, FilterRuleResult

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
            assets: 资产得分，key=index_code。
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
    if value is None or threshold is None:
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


# 单条规则的判定回调：(规则序号, 规则, 因子值, 参照值, 是否通过, 是否缺失, 子组路径)
RuleObserver = Callable[[int, FilterRule, float | None, Any, bool, bool, str], None]


def evaluate_rule_group(
    group: FilterConfig,
    lookup: Callable[[str], float | None],
    *,
    short_circuit: bool = False,
    observer: RuleObserver | None = None,
    path: str = "",
) -> bool:
    """按 ``logic`` 求值规则组（规则在前、子规则组在后，子组可嵌套）。

    规则口径与过滤规则一致：比较原值；因子值或参照值缺失时按 ``missing_strategy``
    处理（pass=视为满足，fail/exclude=视为不满足）。没有任何规则的（子）组视为不满足。

    Args:
        group: 规则组。
        lookup: 因子引用 → 因子值（资产级或市场级）。
        short_circuit: True 时 AND 遇到不满足、OR 遇到满足立即返回（过滤引擎逐资产求值用）。
        observer: 每条规则求值后的回调（用于收集明细）。
        path: 当前子组路径（顶层为空串）。

    Returns:
        是否满足。
    """
    if group.is_empty:
        return False
    use_and = group.logic == "AND"
    flags: list[bool] = []
    for index, rule in enumerate(group.rules):
        value = lookup(rule.factor)
        compare_value = lookup(rule.compare_to) if rule.compare_to is not None else None
        target = compare_value if rule.compare_to is not None else rule.value
        missing = value is None or target is None
        passed = rule.missing_strategy == "pass" if missing else _check_rule(value, rule.op, target)
        if observer is not None:
            observer(index, rule, value, compare_value, passed, missing, path)
        flags.append(passed)
        if short_circuit and (passed != use_and):
            return passed
    for index, child in enumerate(group.groups):
        child_path = f"{path}.{index}" if path else str(index)
        passed = evaluate_rule_group(
            child, lookup, short_circuit=short_circuit, observer=observer, path=child_path
        )
        flags.append(passed)
        if short_circuit and (passed != use_and):
            return passed
    return all(flags) if use_and else any(flags)


class DefaultFilterEngine:
    """默认过滤引擎。"""

    def filter(
        self,
        config: FilterConfig,
        assets: dict[str, float],
        context: EngineContext,
        debug: list[AssetFilterDetail] | None = None,
    ) -> dict[str, float]:
        """过滤不满足条件的资产。

        Args:
            config: 过滤配置。
            assets: 资产得分，key=index_code。
            context: 引擎上下文。
            debug: 可选的调试收集列表，传入时记录每资产的过滤判定明细。

        Returns:
            过滤后的资产得分。
        """
        if config.is_empty:
            return assets

        result: dict[str, float] = {}
        use_and = config.logic == "AND"

        for code, score in assets.items():
            rule_results: list[FilterRuleResult] = []
            passed = self._evaluate_asset(code, config, context, use_and, rule_results)
            if passed:
                result[code] = score
            logger.debug(
                "[pipeline] 过滤明细: %s passed=%s rules=%s",
                code,
                passed,
                [(r.factor, r.op, r.passed, r.missing) for r in rule_results],
            )

            if debug is not None:
                name_cn = (
                    context.asset_metadata.get(code, {}).get("name_cn", code)
                    if hasattr(context, "asset_metadata")
                    else code
                )
                # 构建失败原因描述
                fail_reason = ""
                if not passed:
                    failed_rules = [r for r in rule_results if not r.passed]
                    if failed_rules:
                        parts = []
                        for r in failed_rules:
                            if r.missing:
                                parts.append(f"{r.factor} 因子缺失({r.missing_strategy})")
                            elif isinstance(r.threshold, str):
                                parts.append(
                                    f"{r.factor}({r.factor_value}) {r.op} {r.threshold}"
                                    f"({r.compare_value})"
                                )
                            else:
                                parts.append(f"{r.factor}({r.factor_value}) {r.op} {r.threshold}")
                        fail_reason = "；".join(parts)
                debug.append(
                    AssetFilterDetail(
                        index_code=code,
                        name_cn=name_cn,
                        passed=passed,
                        rule_results=rule_results,
                        fail_reason=fail_reason,
                    )
                )

        return result

    def _evaluate_asset(
        self,
        code: str,
        config: FilterConfig,
        context: EngineContext,
        use_and: bool,
        rule_results: list[FilterRuleResult] | None = None,
    ) -> bool:
        """评估单个资产是否通过过滤规则组（含嵌套子组）。

        支持两种比较模式：
        - 因子 vs 固定阈值：使用 rule.value。
        - 因子 vs 因子（跨因子比较）：使用 rule.compare_to 引用另一个因子的值。
        缺失判定：因子值或参照值为 None 时按 missing_strategy 处理。pass=规则视为通过，
        fail/exclude=规则不满足（exclude 在语义上标记"因子缺失导致的排除"，便于调试定位）。
        """

        def _observe(
            index: int,
            rule: FilterRule,
            value: float | None,
            compare_value: Any,
            passed: bool,
            missing: bool,
            path: str,
        ) -> None:
            if rule_results is None:
                return
            rule_results.append(
                FilterRuleResult(
                    rule_index=index,
                    factor=rule.factor,
                    op=rule.op,
                    threshold=rule.compare_to if rule.compare_to is not None else rule.value,
                    factor_value=value,
                    compare_value=compare_value,
                    passed=passed,
                    missing=missing,
                    missing_strategy=rule.missing_strategy,
                    group_path=path,
                )
            )

        return evaluate_rule_group(
            config,
            lambda ref: context.asset_factors.get((code, ref)),
            short_circuit=True,
            observer=_observe,
        )
