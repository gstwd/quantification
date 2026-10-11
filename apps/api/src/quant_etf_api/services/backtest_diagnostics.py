"""回测诊断：结构化告警、预热期估算与有效候选池时间线。

从回测服务中拆出的纯计算辅助：输入为主循环收集的逐日记录与预加载数据，输出
``BacktestWarning`` 与可落库的候选池时间线，不读写数据库，便于单独测试。
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from quant_etf_api.config.settings import get_settings
from quant_etf_api.domain.common.numeric import price_invalid
from quant_etf_api.schemas.backtest import BacktestWarning

# 因行情价格字段缺失被剔除的原因 → 展示用标签（F-8）
_PRICE_EXCLUSION_LABELS: dict[str, str] = {
    "MISSING_OPEN": "缺开盘价",
    "MISSING_HIGH_LOW": "缺最高/最低价",
    "MISSING_NEXT_HIGH_LOW": "缺最高/最低价",
}


@dataclass
class RunDiagnostics:
    """主循环收集的诊断原始记录，供收尾时统一生成告警。

    Attributes:
        calendar_source: 调仓日历来源（upstream/database/not_required）。
        non_trading_dates: 被剔除的非交易日行情日期（ISO 字符串）。
        delisted_codes: 标的池中已停用（退市/停发）的指数代码。
        warmup_trading_days: 因子预热交易日数。
        candidate_pool: 有效候选池时间线。
        timing_degraded_dates: 择时因子全部缺失、按中性运行的交易日。
        exclusion_dates: 指数 → 被剔除的交易日。
        exclusion_reasons: 指数 → 剔除原因集合。
        data_quality_mode: 数据缺口提示口径（strict/warn）。
    """

    calendar_source: str | None = None
    non_trading_dates: list[str] = field(default_factory=list)
    delisted_codes: list[str] = field(default_factory=list)
    warmup_trading_days: int = 0
    candidate_pool: dict[str, Any] = field(default_factory=dict)
    timing_degraded_dates: list[date] = field(default_factory=list)
    exclusion_dates: dict[str, list[date]] = field(default_factory=dict)
    exclusion_reasons: dict[str, set[str]] = field(default_factory=dict)
    data_quality_mode: str = "warn"


def execution_price_warning(
    exclusion_dates: dict[str, list[date]],
    exclusion_reasons: dict[str, set[str]],
) -> BacktestWarning | None:
    """构造"缺开盘价或最高/最低价被剔除"的警告（F-8）。

    执行模型是 T+1 开盘价时，缺开盘价的指数当期无法建仓；策略使用 ATR、
    唐奇安、RSRS 等需要最高/最低价的因子时，缺最高/最低价的指数同样会被
    排除在候选池之外。部分指数上游长期只提供收盘价，被剔除的天数可达数年，
    因此这里列出全部受影响指数及其天数与原因，不再只列前 5 只。

    Args:
        exclusion_dates: 指数 → 被剔除的交易日列表。
        exclusion_reasons: 指数 → 剔除原因集合。

    Returns:
        结构化警告；没有因价格字段缺失被剔除的指数时返回 None。
    """
    affected: dict[str, tuple[int, list[str]]] = {}
    for code, dates in exclusion_dates.items():
        labels = sorted(
            {
                label
                for reason, label in _PRICE_EXCLUSION_LABELS.items()
                if reason in exclusion_reasons.get(code, set())
            }
        )
        if labels:
            affected[code] = (len(dates), labels)
    if not affected:
        return None
    ranked = sorted(affected.items(), key=lambda item: (-item[1][0], item[0]))
    detail = "、".join(f"{code} {days} 天（{'/'.join(labels)}）" for code, (days, labels) in ranked)
    return BacktestWarning(
        level="warning",
        code="EXECUTION_PRICE_MISSING",
        message=(
            f"{len(affected)} 个指数因行情价格字段缺失被排除在候选池之外"
            f"（合计 {sum(days for days, _ in affected.values())} 个交易日）：{detail}；"
            "缺开盘价时 T+1 开盘执行无法建仓，缺最高/最低价时相关因子无法计算，"
            "实际可交易资产域小于配置资产池"
        ),
    )


def first_bar_by_code(all_bars: dict[tuple[str, date], Any]) -> dict[str, date]:
    """汇总每个指数在回测区间内的首根 K 线日期（F-6 预热期口径用）。

    Args:
        all_bars: 行情数据，键为 (index_code, trade_date)。

    Returns:
        指数代码 → 首根 K 线日期。
    """
    first: dict[str, date] = {}
    for code, trade_date in all_bars:
        current = first.get(code)
        if current is None or trade_date < current:
            first[code] = trade_date
    return first


def build_candidate_pool_timeline(
    trading_dates: list[date],
    pool_sizes: list[int],
    base_size: int,
    exclusion_dates: dict[str, list[date]],
    exclusion_reasons: dict[str, set[str]],
    exclusion_limit: int | None = None,
) -> dict[str, Any]:
    """构建逐日有效候选池时间线（C6）。

    - 逐日规模做**游程编码**（连续相同规模合并为一段），避免把 2400 个交易日
      逐条落库；
    - 剔除明细按"指数—日期区间"聚合，按交易日数降序截断到
      ``candidate_pool_exclusion_limit``。

    Args:
        trading_dates: 决策交易日列表（升序，与 pool_sizes 等长）。
        pool_sizes: 与 ``trading_dates`` 等长的逐日有效候选池规模。
        base_size: 回测标的池的理论规模。
        exclusion_dates: 指数 → 被剔除交易日列表。
        exclusion_reasons: 指数 → 剔除原因集合。
        exclusion_limit: 剔除明细截断条数，None 时取系统配置。

    Returns:
        可直接写入 ``backtest_run.candidate_pool`` 的字典。
    """
    total_days = len(trading_dates)
    if not pool_sizes or total_days == 0:
        return {
            "base_size": base_size,
            "min_size": 0,
            "max_size": 0,
            "median_size": 0,
            "trading_days": 0,
            "pool_coverage_ratio": 0.0,
            "segments": [],
            "exclusions": [],
            "truncated_exclusions": False,
        }

    # 游程编码：把连续相同规模合并为一段
    # 日期统一转 ISO 字符串——candidate_pool 列是 JSONB，psycopg 的 json
    # 编码器不认 date 对象（读取时由 pydantic 还原为 date）
    segments: list[dict[str, Any]] = []
    start_index = 0
    for index in range(1, len(pool_sizes) + 1):
        if index < len(pool_sizes) and pool_sizes[index] == pool_sizes[start_index]:
            continue
        segments.append(
            {
                "start_date": trading_dates[start_index].isoformat(),
                "end_date": trading_dates[index - 1].isoformat(),
                "trading_days": index - start_index,
                "size": pool_sizes[start_index],
            }
        )
        start_index = index

    sorted_sizes = sorted(pool_sizes)
    median_size = sorted_sizes[len(sorted_sizes) // 2]
    total_slots = base_size * total_days
    coverage = (sum(pool_sizes) / total_slots) if total_slots > 0 else 0.0

    limit = (
        exclusion_limit
        if exclusion_limit is not None
        else get_settings().candidate_pool_exclusion_limit
    )
    exclusions = sorted(
        (
            {
                "index_code": code,
                "first_date": dates[0].isoformat(),
                "last_date": dates[-1].isoformat(),
                "trading_days": len(dates),
                "reasons": sorted(exclusion_reasons.get(code, set())),
            }
            for code, dates in exclusion_dates.items()
            if dates
        ),
        key=lambda item: item["trading_days"],
        reverse=True,
    )
    return {
        "base_size": base_size,
        "min_size": min(pool_sizes),
        "max_size": max(pool_sizes),
        "median_size": median_size,
        "trading_days": total_days,
        "pool_coverage_ratio": round(coverage, 4),
        "segments": segments,
        "exclusions": exclusions[:limit],
        "truncated_exclusions": len(exclusions) > limit,
    }


def collect_compute_failure_warnings(failed_refs: set[str]) -> list[BacktestWarning]:
    """为计算抛异常的因子生成 COMPUTE_FAILED 警告（与"数据不足"区分）。

    Args:
        failed_refs: 计算失败的因子引用集合。

    Returns:
        每个失败引用一条警告，按引用名排序。
    """
    return [
        BacktestWarning(
            level="warning",
            code="COMPUTE_FAILED",
            message=(
                f"因子 {ref} 现算抛出异常（计算失败，而非数据不足），"
                "该因子在回测区间内按缺失处理，请检查服务日志"
            ),
        )
        for ref in sorted(failed_refs)
    ]


def collect_missing_factor_warnings(
    precomputed: dict[date, dict[tuple[str, str], float | None]],
    trading_dates: list[date],
    index_codes: list[str],
    factor_ids: list[str],
) -> list[BacktestWarning]:
    """按因子聚合回测区间内的缺失情况，生成 MISSING_FACTOR 警告。

    计数口径（F-6）：``缺失天数`` 仍是"任一指数缺值即算一天"，但**必须
    同时给出按指数排序的明细**——历史实现只列字母序前 5 个指数，导致
    "只有一只后上市指数缺数据"被读成"整个资产池数据损坏"。

    Args:
        precomputed: 预计算因子值，date → (index_code, factor_id) → 数值。
        trading_dates: 回测交易日列表。
        index_codes: 参与因子计算的指数代码列表（含择时代理指数）。
        factor_ids: 策略实际引用的因子 ID 列表。

    Returns:
        结构化警告列表，最多 5 条（按因子），避免刷屏。
    """
    warnings: list[BacktestWarning] = []
    for factor_id in factor_ids:
        missing_days = 0
        missing_days_by_code: dict[str, int] = {}
        first_missing: date | None = None
        last_missing: date | None = None
        for trade_date in trading_dates:
            day_values = precomputed.get(trade_date, {})
            missing_codes = [
                code for code in index_codes if day_values.get((code, factor_id)) is None
            ]
            if missing_codes:
                missing_days += 1
                for code in missing_codes:
                    missing_days_by_code[code] = missing_days_by_code.get(code, 0) + 1
                if first_missing is None:
                    first_missing = trade_date
                last_missing = trade_date
        if missing_days > 0:
            ranked = sorted(missing_days_by_code.items(), key=lambda item: (-item[1], item[0]))
            detail = "、".join(f"{code} {days} 天" for code, days in ranked[:5])
            if len(ranked) > 5:
                detail += f" 等 {len(ranked)} 个指数"
            warnings.append(
                BacktestWarning(
                    level="warning",
                    code="MISSING_FACTOR",
                    message=(
                        f"因子 {factor_id} 在 {missing_days} 个交易日存在缺失"
                        f"（{first_missing}~{last_missing}）；"
                        f"按缺失天数排序：{detail}"
                    ),
                )
            )
    return warnings[:5]


def collect_data_gap_warnings(
    trading_dates: list[date],
    index_codes: list[str],
    all_bars: dict,
    execution_model: str = "t_plus_1_open",
    data_quality_mode: str = "warn",
) -> list[BacktestWarning]:
    """按指数统计回测区间内缺行情数据的交易日，生成 DATA_GAP 警告。

    兼容模式下提示实际可能影响收益的行情缺口；严格模式下，已由
    DATA_EXCLUDED 覆盖的开盘价缺失不重复提示，避免把“已排除”误解为
    “仍参与收益计算”。

    Args:
        trading_dates: 回测交易日列表。
        index_codes: 回测标的指数代码列表。
        all_bars: 预加载行情（原始行情，非前向填充），key=(index_code, trade_date)。
        execution_model: 执行模型，决定是否检查开盘价。
        data_quality_mode: 数据质量模式，严格模式不重复报告已排除的开盘价缺失。

    Returns:
        结构化警告列表，最多 5 条（按指数）。
    """
    warnings: list[BacktestWarning] = []
    for code in index_codes:
        missing_days: list[date] = []
        open_missing_days: list[date] = []
        for d in trading_dates:
            bar = all_bars.get((code, d))
            if bar is None or price_invalid(getattr(bar, "close_price", None)):
                # 整日缺行情或收盘价不可用：该日收益按前值平盘结算，缺口后补回
                missing_days.append(d)
                continue
            if (
                execution_model == "t_plus_1_open"
                and data_quality_mode != "strict"
                and price_invalid(getattr(bar, "open_price", None))
            ):
                # bar 存在但开盘价缺失：T+1 开盘执行模型的隔夜/日内段受影响
                open_missing_days.append(d)
        parts: list[str] = []
        if missing_days:
            parts.append(
                f"缺行情数据 {len(missing_days)} 个交易日（{missing_days[0]}~{missing_days[-1]}）"
            )
        if open_missing_days:
            parts.append(
                f"开盘价缺失 {len(open_missing_days)} 个交易日"
                f"（{open_missing_days[0]}~{open_missing_days[-1]}，影响 T+1 开盘执行日）"
            )
        if parts:
            warnings.append(
                BacktestWarning(
                    level="warning",
                    code="DATA_GAP",
                    message=(
                        f"指数 {code} 回测区间内{'、'.join(parts)}，相关日期的因子与收益按缺失处理"
                    ),
                    index_code=code,
                )
            )
    return warnings[:5]


def estimate_warmup_trading_days(
    precomputed: dict[date, dict[tuple[str, str], float | None]],
    trading_dates: list[date],
    index_codes: list[str],
    factor_ids: list[str],
    first_bars: dict[str, date] | None = None,
) -> int:
    """估算回测前段因子数据不足的预热交易日数。

    口径（F-6）：对每个 (因子, 指数) 组合，从**该指数自己的首根 K 线**
    开始数"该因子还没有有效值"的天数，取最大值作为预热期——这才是
    真正影响策略的可交易起点。

    历史实现要求"全部指数都有有效值"才算预热结束，单只后上市指数
    （如 000688 行情自 2020 年才有）会把整个回测的前 1038 个交易日
    （约 4.3 年）判成预热期，读起来像"前四年结果无效"，实际只是
    一只指数缺数据。指数上市前的空缺属于数据缺口（DATA_GAP），
    不在这里计数。

    Args:
        precomputed: 预计算因子值，date → (index_code, factor_id) → 数值。
        trading_dates: 回测交易日列表。
        index_codes: 参与因子计算的指数代码列表（含择时代理指数）。
        factor_ids: 策略实际引用的因子 ID 列表。
        first_bars: 指数 → 回测区间内首根 K 线日期；提供后每个指数
            只从自己的首根 K 线起算（None 时等价于从区间首日起算）。

    Returns:
        前 N 个交易日中因子数据不足的天数；无预热期时返回 0。
    """
    if not factor_ids or not trading_dates:
        return 0
    warmup = 0
    for factor_id in factor_ids:
        for code in index_codes:
            start_index = 0
            if first_bars is not None:
                first_bar = first_bars.get(code)
                if first_bar is None:
                    # 该指数在窗口内完全没有行情，整体缺失由 DATA_GAP 报告
                    continue
                start_index = bisect_left(trading_dates, first_bar)
            for i in range(start_index, len(trading_dates)):
                if precomputed.get(trading_dates[i], {}).get((code, factor_id)) is not None:
                    # 只计"该指数自己序列开头"的缺口，指数上市前的空缺不算
                    warmup = max(warmup, i - start_index)
                    break
            # 该指数在窗口内始终没有该因子值：结构性缺失，
            # 由 MISSING_FACTOR / 数据质量检查负责，不算预热
    return warmup


def collect_run_warnings(
    diagnostics: RunDiagnostics,
    factor_warnings: list[BacktestWarning] | None = None,
) -> list[BacktestWarning]:
    """把主循环收集的口径与候选池记录转换为结构化告警。

    覆盖日历来源、非交易日行情、退市标的、预热期、候选池缩水、因子缺失、择时
    降级与数据缺口剔除；行情缺口与基准缺失依赖额外数据，由调用方追加。

    Args:
        diagnostics: 主循环收集的诊断记录。
        factor_warnings: 因子缺失/计算失败告警，插在候选池提示之后。

    Returns:
        结构化告警列表（顺序与历史实现一致）。
    """
    warnings: list[BacktestWarning] = []
    # 口径提示（C1）：使用本地日历表快照而非上游数据源时显式告知，
    # 便于判断"同一配置两条回测的调仓日历是否来自同一来源"
    if diagnostics.calendar_source == "database":
        warnings.append(
            BacktestWarning(
                level="info",
                code="CALENDAR_SOURCE_DATABASE",
                message=(
                    "交易日历来自本地 trading_calendar 表快照"
                    "（上游 Tushare/AkShare 当前不可用，快照可能滞后于官方日历）"
                ),
            )
        )
    # 非交易日行情（F-7）：行情表里存在日历判定为非交易日的日期时
    # 显式告知，避免"回测多出一个假调仓日"被当成正常波动
    non_trading = diagnostics.non_trading_dates
    if non_trading:
        warnings.append(
            BacktestWarning(
                level="warning",
                code="NON_TRADING_BAR",
                message=(
                    f"行情数据包含 {len(non_trading)} 个非交易日"
                    f"（{', '.join(non_trading[:5])}"
                    f"{' 等' if len(non_trading) > 5 else ''}），"
                    "已从回测交易日中剔除（上游把行情打在假期日期上）"
                ),
            )
        )
    # point-in-time 标的池提示：纳入"已停用但区间内仍存续"的指数，
    # 让研究者知道资产域包含退市/停发指数（而非只统计活到今天的资产）
    delisted = diagnostics.delisted_codes
    if delisted:
        warnings.append(
            BacktestWarning(
                level="info",
                code="UNIVERSE_INCLUDES_DELISTED",
                message=(
                    f"标的池包含 {len(delisted)} 个已停用（退市/停发）指数："
                    f"{'、'.join(delisted[:5])}"
                    f"{' 等' if len(delisted) > 5 else ''}；"
                    "回测按 point-in-time 口径纳入区间内仍存续的指数，"
                    "其停止发布后的日期由逐日候选池自动剔除（避免幸存者偏差）"
                ),
            )
        )
    if diagnostics.warmup_trading_days > 0:
        warnings.append(
            BacktestWarning(
                level="warning",
                code="WARMUP",
                message=(
                    f"回测前 {diagnostics.warmup_trading_days} 个交易日长周期因子数据不足"
                    "（按各指数自身首根 K 线起算的预热期），前段信号与绩效参考价值有限"
                ),
            )
        )
    # 有效候选池时间线（C6）：缩水到理论池以下时补一条信息级提示
    pool = diagnostics.candidate_pool
    if pool and pool["min_size"] < pool["base_size"]:
        shrunk = next(
            (seg for seg in pool["segments"] if seg["size"] == pool["min_size"]),
            None,
        )
        warnings.append(
            BacktestWarning(
                level="info",
                code="CANDIDATE_POOL_SHRINK",
                message=(
                    f"有效候选池 {pool['base_size']} → 最小 {pool['min_size']}"
                    + (
                        f"（{shrunk['start_date']}~{shrunk['end_date']}）"
                        if shrunk is not None
                        else ""
                    )
                    + "，逐日时间线见 stability.candidate_pool"
                ),
            )
        )
    warnings.extend(factor_warnings or [])
    degraded = diagnostics.timing_degraded_dates
    if degraded:
        warnings.append(
            BacktestWarning(
                level="warning",
                code="TIMING_DEGRADED",
                message=(
                    f"择时因子在 {len(degraded)} 个交易日全部缺失"
                    f"（{degraded[0]}~{degraded[-1]}），"
                    "这些交易日按中性 regime 决定总仓位"
                ),
            )
        )
    warnings.extend(_exclusion_warnings(diagnostics))
    return warnings


def _exclusion_warnings(diagnostics: RunDiagnostics) -> list[BacktestWarning]:
    """生成数据缺口剔除相关告警（价格字段缺失 + DATA_EXCLUDED）。

    Args:
        diagnostics: 主循环收集的诊断记录。

    Returns:
        剔除相关的结构化告警。
    """
    exclusion_dates = diagnostics.exclusion_dates
    if not exclusion_dates:
        return []
    exclusion_reasons = diagnostics.exclusion_reasons
    warnings: list[BacktestWarning] = []
    # 价格字段缺失导致的剔除单列（F-8）：t_plus_1_open 下缺开盘价、
    # 使用高低价因子时缺最高/最低价的指数会被静默排除在候选池之外，
    # 实际资产域可能远小于配置的资产池；这里显式列出全部受影响资产、
    # 被排除的天数与原因
    execution_warning = execution_price_warning(exclusion_dates, exclusion_reasons)
    if execution_warning is not None:
        warnings.append(execution_warning)
    if diagnostics.data_quality_mode == "strict":
        for code, dates in list(exclusion_dates.items())[:5]:
            warnings.append(
                BacktestWarning(
                    level="warning",
                    code="DATA_EXCLUDED",
                    message=(
                        f"数据缺口剔除指数 {code} {len(dates)} 个交易日，"
                        f"范围 {dates[0]}~{dates[-1]}；"
                        f"原因：{','.join(sorted(exclusion_reasons.get(code, set())))}"
                    ),
                    index_code=code,
                )
            )
    else:
        # 宽松口径：决策与严格口径一致，仅把逐指数明细压缩为一条汇总提示
        excluded_days_total = sum(len(dates) for dates in exclusion_dates.values())
        warnings.append(
            BacktestWarning(
                level="warning",
                code="DATA_EXCLUDED",
                message=(
                    f"数据缺口剔除 {len(exclusion_dates)} 个指数、"
                    f"合计 {excluded_days_total} 个交易日（候选池口径与严格模式一致，"
                    "切换严格模式可查看逐指数明细）"
                ),
            )
        )
    return warnings
