"""择时代理指数选择（纯领域规则，实时与回测共用）。

择时因子是市场级口径，按 ``timing.proxy_index_codes`` 的顺序从代理指数上取值。
历史实现"逐因子各自取第一个有值的代理"，同一交易日的综合分可能混用
000300 与 000905 两个口径。本模块收敛为唯一实现：

1. 按代理顺序，选第一个让**全部**择时因子都有值的代理，所有因子统一取它的值；
2. 没有任何代理全覆盖时，退回逐因子取第一个有值代理，并记录每个因子
   实际使用的代理，供明细展示与告警判断。

单代理配置下两种口径结果完全一致，存量回测口径不变。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

# 代理选择模式：consistent=单一代理全覆盖，configured=按因子指定代理且各组全覆盖，
# per_factor=存在未全覆盖的代理组、逐因子回退
ProxyMode = Literal["consistent", "configured", "per_factor"]


@dataclass(frozen=True)
class TimingProxySelection:
    """一次择时代理选择的结果。

    Attributes:
        values: 择时因子引用 → 因子值；所有代理都无值时为 None。
        factor_proxies: 择时因子引用 → 实际取值的代理指数代码；无值时为 None。
        proxy_index_code: 全覆盖的代理指数代码；None 表示没有代理全覆盖，
            已退回逐因子口径（或按因子指定了不同代理）。
        configured_groups: 按因子指定代理（``timing.factor_proxies``）时各代理组都
            全覆盖、但落在不同代理上；此时各因子来源见 ``factor_proxies``。
    """

    values: dict[str, float | None] = field(default_factory=dict)
    factor_proxies: dict[str, str | None] = field(default_factory=dict)
    proxy_index_code: str | None = None
    configured_groups: bool = False

    @property
    def mode(self) -> ProxyMode:
        """代理选择模式：单一代理全覆盖为 consistent，按因子指定代理且各组全覆盖为
        configured，否则为 per_factor。"""
        if self.proxy_index_code is not None:
            return "consistent"
        return "configured" if self.configured_groups else "per_factor"

    @property
    def mixed(self) -> bool:
        """逐因子回退时是否实际混用了多个代理（缺值因子不计入）。"""
        used = {code for code in self.factor_proxies.values() if code is not None}
        return len(used) > 1

    def to_detail(self) -> dict[str, Any]:
        """转换为择时明细中的代理字段。

        Returns:
            含 ``proxy_index_code`` 与 ``proxy_mode`` 的字典。
        """
        return {"proxy_index_code": self.proxy_index_code, "proxy_mode": self.mode}


def select_timing_proxy_values(
    proxy_codes: Sequence[str],
    factor_refs: Sequence[str],
    lookup: Callable[[str, str], float | None],
) -> TimingProxySelection:
    """按代理顺序选出择时因子的取值口径（实时与回测共用的唯一实现）。

    Args:
        proxy_codes: 择时代理指数代码（按优先级）。
        factor_refs: 择时因子引用名。
        lookup: 取值函数 ``(代理指数代码, 因子引用) → 因子值``，无值返回 None。

    Returns:
        代理选择结果；优先使用第一个全覆盖代理，否则逐因子回退。
    """
    refs = list(dict.fromkeys(factor_refs))
    if not refs:
        return TimingProxySelection()

    for code in proxy_codes:
        candidate = {ref: lookup(code, ref) for ref in refs}
        if all(value is not None for value in candidate.values()):
            return TimingProxySelection(
                values=candidate,
                factor_proxies={ref: code for ref in refs},
                proxy_index_code=code,
            )

    # 无代理全覆盖：退回逐因子取第一个有值代理，并记录实际来源
    values: dict[str, float | None] = {}
    factor_proxies: dict[str, str | None] = {}
    for ref in refs:
        values[ref] = None
        factor_proxies[ref] = None
        for code in proxy_codes:
            value = lookup(code, ref)
            if value is not None:
                values[ref] = value
                factor_proxies[ref] = code
                break
    return TimingProxySelection(values=values, factor_proxies=factor_proxies)


def select_grouped_timing_proxy_values(
    groups: Sequence[tuple[Sequence[str], Sequence[str]]],
    lookup: Callable[[str, str], float | None],
) -> TimingProxySelection:
    """按"代理组"选出择时因子取值（支持按因子指定代理）。

    每组为 ``(代理指数代码列表, 该组因子引用)``：未单独指定代理的因子共用
    ``timing.proxy_index_codes`` 组成默认组，``timing.factor_proxies`` 中的因子各按
    自己的代理列表成组。组内沿用 :func:`select_timing_proxy_values` 的一致性口径。

    只有一组时结果与 :func:`select_timing_proxy_values` 完全一致；多组时
    ``proxy_index_code`` 仅在各组都全覆盖、且恰好落在同一代理上时给出，否则为 None
    （明细按各因子的 ``factor_proxies`` 展示实际来源）。

    Args:
        groups: 代理组列表，按配置顺序排列。
        lookup: 取值函数 ``(代理指数代码, 因子引用) → 因子值``，无值返回 None。

    Returns:
        合并后的代理选择结果。
    """
    non_empty = [(proxies, refs) for proxies, refs in groups if refs]
    if not non_empty:
        return TimingProxySelection()
    if len(non_empty) == 1:
        proxies, refs = non_empty[0]
        return select_timing_proxy_values(proxies, refs, lookup)

    values: dict[str, float | None] = {}
    factor_proxies: dict[str, str | None] = {}
    consistent_codes: set[str] = set()
    all_consistent = True
    for proxies, refs in non_empty:
        selection = select_timing_proxy_values(proxies, refs, lookup)
        values.update(selection.values)
        factor_proxies.update(selection.factor_proxies)
        if selection.proxy_index_code is None:
            all_consistent = False
        else:
            consistent_codes.add(selection.proxy_index_code)
    proxy_code = (
        next(iter(consistent_codes)) if all_consistent and len(consistent_codes) == 1 else None
    )
    return TimingProxySelection(
        values=values,
        factor_proxies=factor_proxies,
        proxy_index_code=proxy_code,
        configured_groups=all_consistent and proxy_code is None,
    )
