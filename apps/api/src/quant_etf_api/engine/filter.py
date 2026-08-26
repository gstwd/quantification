"""过滤模块：根据规则过滤不满足条件的资产。

支持 gt / lt / gte / lte / eq / neq / between 操作符，
AND / OR 逻辑组合。
"""

from __future__ import annotations

import logging
from typing import Protocol

from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import FilterConfig
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
            assets: 资产得分，key=etf_code。
            context: 引擎上下文。
            debug: 可选的调试收集列表，传入时记录每资产的过滤判定明细。

        Returns:
            过滤后的资产得分。
        """
        if not config.rules:
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
                        etf_code=code,
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
        """评估单个资产是否通过所有过滤规则。

        支持两种比较模式：
        - 因子 vs 固定阈值：使用 rule.value。
        - 因子 vs 因子（跨因子比较）：使用 rule.compare_to 引用另一个因子的值。
        """
        for i, rule in enumerate(config.rules):
            factor_value = context.asset_factors.get((code, rule.factor))
            compare_value = None

            if rule.compare_to is not None:
                # 跨因子比较模式
                compare_value = context.asset_factors.get((code, rule.compare_to))
                threshold = rule.compare_to
                missing = factor_value is None or compare_value is None
            else:
                threshold = rule.value
                missing = factor_value is None or threshold is None

            # 缺失判定：因子值或参照值为 None 时按 missing_strategy 处理。
            # pass=规则视为通过，fail/exclude=规则不满足（exclude 在语义上
            # 标记"因子缺失导致的排除"，与普通规则不满足区分，便于调试定位）。
            if missing:
                rule_passed = rule.missing_strategy == "pass"
            else:
                compare_target = compare_value if rule.compare_to is not None else rule.value
                rule_passed = _check_rule(factor_value, rule.op, compare_target)

            if rule_results is not None:
                rule_results.append(
                    FilterRuleResult(
                        rule_index=i,
                        factor=rule.factor,
                        op=rule.op,
                        threshold=threshold,
                        factor_value=factor_value,
                        compare_value=compare_value,
                        passed=rule_passed,
                        missing=missing,
                        missing_strategy=rule.missing_strategy,
                    )
                )

            if use_and and not rule_passed:
                return False
            if not use_and and rule_passed:
                return True

        return use_and
