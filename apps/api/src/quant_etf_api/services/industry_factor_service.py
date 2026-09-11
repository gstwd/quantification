"""行业因子面板构建与持久化服务（RRG / 扩散数量占比）。

除计算面板外，本服务还提供研究调试台需要的“数据问题可见性”能力：
- RRG 按行业报告输入窗口内的日线缺口、ffill 兜底、warm-up 不足；
- 扩散按行业分批加载成员股收盘并返回有效样本/覆盖度统计，避免全市场
  收盘一次物化导致的 OOM（P02）。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from quant_etf_api.domain.industry.constants import (
    SW_EXCLUDED_INDUSTRY_CODES,
    canonical_industry_params,
    industry_params_hash,
    normalize_sw_code,
)
from quant_etf_api.domain.industry.diffusion_panel import (
    build_membership_chains,
    industry_diffusion_series,
    member_stock_codes,
)
from quant_etf_api.domain.industry.factor_algo import (
    classify_quadrant,
    compute_rrg,
    equal_weight_benchmark,
)
from quant_etf_api.infra.db.base import utcnow
from quant_etf_api.infra.db.repositories.industry import (
    IndustryDailyBarRepository,
    IndustryFactorValueRepository,
    IndustryMembershipEventRepository,
    IndustryUniverseRepository,
    StockDailyCloseRepository,
)

logger = logging.getLogger(__name__)

# RS-Momentum 默认 warm-up 318 个交易日，折算自然日安全缓冲约 520 天
_RRG_WARMUP_NATURAL_DAYS = 520

# 行业因子 ID → IndustryRotationInput 字段映射
_FACTOR_ID_TO_ROTATION_FIELD: dict[str, str] = {
    "rrg_rs_ratio": "rs_ratio",
    "rrg_rs_momentum": "rs_momentum",
    "rrg_quadrant": "quadrant",
    "diffusion_count_ratio": "diffusion",
}


def _diffusion_buffer_natural_days(lookback: int, smooth_window: int) -> int:
    """按扩散参数估算需要的自然日回看缓冲。

    扩散需要在输出起始日之前回看 lookback + smooth_window - 1 个交易日，
    A 股约 244 个交易日/年，按 1.5 系数（≈366 自然日/244 交易日）估算，
    另加 40 天安全垫。默认参数 220/20 得到约 400 天，与原固定缓冲一致。

    Args:
        lookback: 上涨判定回看交易日数。
        smooth_window: MA 平滑窗口。

    Returns:
        自然日缓冲天数。
    """
    return int((lookback + smooth_window) * 1.5) + 40


class IndustryFactorService:
    """构建行业因子宽表面板（RRG 与扩散），并可持久化到 industry_factor_value。"""

    def __init__(self, db: Session) -> None:
        """初始化行业因子服务。

        Args:
            db: SQLAlchemy 同步 Session。
        """
        self._db = db
        self._bar_repo = IndustryDailyBarRepository(db)
        self._universe_repo = IndustryUniverseRepository(db)
        self._membership_repo = IndustryMembershipEventRepository(db)
        self._close_repo = StockDailyCloseRepository(db)
        self._factor_repo = IndustryFactorValueRepository(db)

    def resolve_industry_codes(self, codes: list[str] | None = None) -> list[str]:
        """解析行业代码列表，为空时返回全部启用行业（升序）。"""
        if codes:
            return sorted({normalize_sw_code(code) for code in codes})
        return self._universe_repo.find_active_codes()

    def industry_names(self) -> dict[str, str]:
        """返回启用行业的 代码 → 中文名称 映射。"""
        return {row.industry_code: row.name_cn for row in self._universe_repo.find_all_active()}

    def build_panels(
        self,
        *,
        start: date,
        end: date,
        industry_codes: list[str] | None = None,
        lookback_ratio: int = 220,
        lookback_mom: int = 60,
        smooth_window: int = 20,
        diffusion_lookback: int = 220,
        benchmark_exclude: list[str] | None = None,
        need_rrg: bool = True,
        need_diffusion: bool = True,
        collect_coverage: bool = False,
    ) -> dict[str, Any]:
        """构建行业因子面板。

        自动向前多取 warm-up 行情/成分数据，返回的面板仅含 [start, end] 行。
        collect_coverage=True 时额外返回数据覆盖/问题元信息，供调试页展示，
        因子计算本身不做任何静默 ffill 隐藏（缺口全部进入元信息）。

        Returns:
            dict：rs_ratio/rs_momentum/quadrant/diffusion 为 DataFrame，
            industry_close 为行业收盘价宽表，trading_dates 为交易日列表；
            collect_coverage 开启时追加 data_issues / rrg_coverage /
            diffusion_coverage / diffusion_daily（有效样本与成员数宽表）。
        """
        codes = self.resolve_industry_codes(industry_codes)
        excluded = {
            normalize_sw_code(code)
            for code in (benchmark_exclude or list(SW_EXCLUDED_INDUSTRY_CODES))
        }
        bar_start = start - timedelta(days=_RRG_WARMUP_NATURAL_DAYS)
        bars = self._bar_repo.find_range(bar_start, end, codes)
        if not bars:
            raise ValueError("行业日线无数据，请先执行 industry backfill-bars")
        close = _bars_to_close_panel(bars, codes)
        market_dates = [d.date() for d in close.index]
        trading_dates = [d for d in market_dates if start <= d <= end]

        issues: dict[str, list[dict[str, Any]]] = {"rrg": [], "diffusion": []}
        notes: dict[str, list[str]] = {"rrg": [], "diffusion": []}
        rrg_coverage: list[dict[str, Any]] = []
        diffusion_extra: dict[str, Any] = {}

        rs_ratio = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
        rs_momentum = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
        quadrant = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
        if need_rrg:
            benchmark_close = close.drop(columns=list(excluded & set(close.columns)))
            benchmark = equal_weight_benchmark(benchmark_close)
            try:
                ratio_panel, momentum_panel = compute_rrg(
                    close,
                    benchmark,
                    lookback_ratio=lookback_ratio,
                    lookback_mom=lookback_mom,
                    smooth_window=smooth_window,
                )
                rs_ratio = ratio_panel
                rs_momentum = momentum_panel
                quadrant = classify_quadrant(ratio_panel, momentum_panel)
            except ValueError as e:
                logger.warning("RRG warm-up 数据不足: %s", e)
                rs_ratio = rs_ratio.iloc[:0]
                rs_momentum = rs_momentum.iloc[:0]
                quadrant = quadrant.iloc[:0]
                if collect_coverage:
                    issues["rrg"].append(
                        {
                            "level": "error",
                            "code": "RRG_INSUFFICIENT_DATA",
                            "message": f"RRG 无法计算：{e}",
                            "industry_codes": [],
                            "count": None,
                            "sample_dates": [],
                        }
                    )
            if collect_coverage:
                notes["rrg"] = _rrg_rules(
                    lookback_ratio=lookback_ratio,
                    lookback_mom=lookback_mom,
                    smooth_window=smooth_window,
                )
                rrg_cov = self._rrg_coverage(
                    start=start,
                    end=end,
                    codes=codes,
                    market_dates=market_dates,
                    bars=bars,
                    ratio=rs_ratio,
                    momentum=rs_momentum,
                )
                rrg_coverage = rrg_cov["items"]
                issues["rrg"].extend(rrg_cov["issues"])

        diffusion = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
        if need_diffusion:
            if collect_coverage:
                notes["diffusion"] = _diffusion_rules(
                    lookback=diffusion_lookback,
                    smooth_window=smooth_window,
                )
            try:
                diffusion, diffusion_extra = self._build_diffusion_panel_with_stats(
                    start=start,
                    end=end,
                    codes=codes,
                    lookback=diffusion_lookback,
                    smooth_window=smooth_window,
                    market_dates=market_dates,
                    collect_daily=collect_coverage,
                )
                if collect_coverage:
                    issues["diffusion"].extend(diffusion_extra.get("issues", []))
            except ValueError as e:
                logger.warning("扩散指标计算失败: %s", e)
                diffusion = diffusion.iloc[:0]
                if collect_coverage:
                    issues["diffusion"].append(
                        {
                            "level": "error",
                            "code": "DIFFUSION_INSUFFICIENT_DATA",
                            "message": f"扩散无法计算：{e}",
                            "industry_codes": [],
                            "count": None,
                            "sample_dates": [],
                        }
                    )

        def _slice(panel: pd.DataFrame) -> pd.DataFrame:
            if panel.empty:
                return panel
            return panel.loc[
                (panel.index >= pd.Timestamp(start))
                & (panel.index <= pd.Timestamp(end))
            ]

        result: dict[str, Any] = {
            "rs_ratio": _slice(rs_ratio),
            "rs_momentum": _slice(rs_momentum),
            "quadrant": _slice(quadrant),
            "diffusion": _slice(diffusion),
            "industry_close": _slice(close),
            "trading_dates": trading_dates,
            "market_dates": market_dates,
            "industry_codes": codes,
        }
        if collect_coverage:
            result["data_issues"] = issues
            result["notes"] = notes
            result["rrg_coverage"] = rrg_coverage
            result["diffusion_coverage"] = diffusion_extra.get("items", [])
            result["diffusion_daily"] = diffusion_extra.get(
                "daily", {"valid": None, "member": None}
            )
        return result

    def _rrg_coverage(
        self,
        *,
        start: date,
        end: date,
        codes: list[str],
        market_dates: list[date],
        bars: list[Any],
        ratio: pd.DataFrame,
        momentum: pd.DataFrame,
    ) -> dict[str, Any]:
        """统计各行业在 RRG 输入窗口的数据覆盖与有效区间。

        Returns:
            {"items": [覆盖项], "issues": [数据问题]}。
        """
        actual_by_code: dict[str, set[date]] = {}
        for row in bars:
            if row.close_price is None:
                continue
            actual_by_code.setdefault(row.industry_code, set()).add(row.trade_date)
        items: list[dict[str, Any]] = []
        issues: list[dict[str, Any]] = []
        output_dates = [d for d in market_dates if start <= d <= end]
        latest_any = max((dates for dates in actual_by_code.values() if dates), default=set())
        latest_any = max(latest_any) if latest_any else None
        earliest_any = min(
            (min(dates) for dates in actual_by_code.values() if dates), default=None
        )

        if latest_any is not None and end > latest_any:
            issues.append(
                {
                    "level": "warn",
                    "code": "TRAILING_DATA",
                    "message": (
                        f"请求截止日 {end.isoformat()} 晚于库内最新交易日 "
                        f"{latest_any.isoformat()}；可先执行行业日线补全/日频摄取后再查询。"
                    ),
                    "industry_codes": [],
                    "count": None,
                    "sample_dates": [],
                }
            )
        if earliest_any is not None and start < earliest_any:
            issues.append(
                {
                    "level": "info",
                    "code": "START_BEFORE_DATA",
                    "message": (
                        f"请求起始日 {start.isoformat()} 早于库内最早交易日 "
                        f"{earliest_any.isoformat()}；前置部分会按缺数据处理。"
                    ),
                    "industry_codes": [],
                    "count": None,
                    "sample_dates": [],
                }
            )

        for code in codes:
            actual = sorted(actual_by_code.get(code, set()))
            if not actual:
                items.append(
                    {
                        "industry_code": code,
                        "data_start_date": None,
                        "data_end_date": None,
                        "expected_input_days": 0,
                        "present_input_days": 0,
                        "missing_input_days": 0,
                        "output_days": len(output_dates),
                        "leading_nan_days": len(output_dates),
                        "valid_count": 0,
                        "valid_from": None,
                        "valid_until": None,
                    }
                )
                issues.append(
                    {
                        "level": "error",
                        "code": "NO_INDUSTRY_BARS",
                        "message": f"{code} 在输入窗口内无行业日线。",
                        "industry_codes": [code],
                        "count": 0,
                        "sample_dates": [],
                    }
                )
                continue
            actual_set = set(actual)
            data_start = actual[0]
            data_end = actual[-1]
            expected_dates = [d for d in market_dates if data_start <= d <= end]
            present_dates = [d for d in expected_dates if d in actual_set]
            missing_dates = [d for d in expected_dates if d not in actual_set]

            valid_mask = pd.Series(False, index=ratio.index)
            if not ratio.empty and code in ratio.columns and code in momentum.columns:
                valid_mask = ratio[code].notna() & momentum[code].notna()
            in_output = valid_mask.loc[
                (valid_mask.index >= pd.Timestamp(start))
                & (valid_mask.index <= pd.Timestamp(end))
            ]
            values = in_output.to_numpy()
            valid_count = int(values.sum())
            leading_nan = (
                int(np.argmax(values)) if valid_count else len(output_dates)
            )
            valid_dates = [
                d.date() for d, flag in in_output.items() if bool(flag)
            ]
            items.append(
                {
                    "industry_code": code,
                    "data_start_date": data_start,
                    "data_end_date": data_end,
                    "expected_input_days": len(expected_dates),
                    "present_input_days": len(present_dates),
                    "missing_input_days": len(missing_dates),
                    "output_days": len(output_dates),
                    "leading_nan_days": leading_nan,
                    "valid_count": valid_count,
                    "valid_from": valid_dates[0] if valid_dates else None,
                    "valid_until": valid_dates[-1] if valid_dates else None,
                }
            )
            if missing_dates:
                issues.append(
                    {
                        "level": "warn",
                        "code": "BAR_GAP_FFILL",
                        "message": (
                            f"{code} 在输入窗口内相对市场日历缺 {len(missing_dates)} 个"
                            "交易日，RRG 计算按 ffill 兜底，请核对数据完整性。"
                        ),
                        "industry_codes": [code],
                        "count": len(missing_dates),
                        "sample_dates": [d.isoformat() for d in missing_dates[:5]],
                    }
                )
            if leading_nan > 0 or valid_count == 0:
                issues.append(
                    {
                        "level": "warn",
                        "code": "RRG_WARMUP_LEADING",
                        "message": (
                            f"{code} 在输出窗口前 {leading_nan} 个交易日无有效 RRG"
                            "（warm-up 不足或窗口早于数据起点），请结合覆盖表判断。"
                        ),
                        "industry_codes": [code],
                        "count": leading_nan,
                        "sample_dates": [d.isoformat() for d in output_dates[:3]],
                    }
                )
        return {"items": items, "issues": issues}

    def _build_diffusion_panel_with_stats(
        self,
        *,
        start: date,
        end: date,
        codes: list[str],
        lookback: int,
        smooth_window: int,
        market_dates: list[date],
        collect_daily: bool,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """按行业分批构建扩散面板与覆盖统计（内存有界，P02）。

        Returns:
            (date × industry_code 扩散宽表, 元信息 dict)。
        """
        if not market_dates:
            raise ValueError("行业日线无可用交易日，无法构建扩散面板")
        buffer_days = _diffusion_buffer_natural_days(lookback, smooth_window)
        index_start = start - timedelta(days=buffer_days)
        index_dates = [d for d in market_dates if index_start <= d <= end]
        if not index_dates:
            raise ValueError("扩散窗口内无市场交易日，请检查行业日线覆盖")
        events = self._membership_repo.find_events_until(end)
        if not events:
            raise ValueError("行业成分无数据，请先执行 industry backfill-membership")
        chains = build_membership_chains(events)

        series_map: dict[str, pd.Series] = {}
        stats_map: dict[str, pd.DataFrame] = {}
        row_ranges: dict[str, tuple[date | None, date | None]] = {}
        no_members: list[str] = []
        no_rows: list[str] = []
        for code in codes:
            members = member_stock_codes(chains, code)
            if not members:
                no_members.append(code)
                continue
            rows = self._close_repo.find_close_rows(index_dates[0], end, members)
            series, stats = industry_diffusion_series(
                rows,
                index_dates,
                chains,
                code,
                lookback=lookback,
                smooth_window=smooth_window,
                members=members,
            )
            series_map[code] = series
            stats_map[code] = stats
            dates = [row[0] for row in rows]
            row_ranges[code] = (min(dates), max(dates)) if dates else (None, None)
            if not dates:
                no_rows.append(code)

        if not series_map:
            raise ValueError("所选行业均无成分事件，扩散面板为空")
        frame = pd.DataFrame(series_map, index=pd.DatetimeIndex(index_dates))
        frame = frame.reindex(columns=codes)
        output_dates = [d for d in index_dates if start <= d <= end]

        issues: list[dict[str, Any]] = []
        for code in no_members:
            issues.append(
                {
                    "level": "error",
                    "code": "NO_MEMBERSHIP",
                    "message": f"{code} 在成分事件流中无成员股，扩散恒为 NaN。",
                    "industry_codes": [code],
                    "count": 0,
                    "sample_dates": [],
                }
            )
        for code in no_rows:
            issues.append(
                {
                    "level": "error",
                    "code": "NO_STOCK_CLOSE",
                    "message": f"{code} 成员股在扩散窗口内无任何收盘数据。",
                    "industry_codes": [code],
                    "count": 0,
                    "sample_dates": [],
                }
            )

        daily: dict[str, pd.DataFrame | None] = {"valid": None, "member": None}
        if collect_daily and stats_map:
            index = pd.DatetimeIndex(index_dates)
            daily["member"] = pd.DataFrame(
                {
                    code: stats_map[code]["member_count"].reindex(index)
                    for code in codes
                    if code in stats_map
                }
            ).reindex(columns=codes)
            daily["valid"] = pd.DataFrame(
                {
                    code: stats_map[code]["valid_count"].reindex(index)
                    for code in codes
                    if code in stats_map
                }
            ).reindex(columns=codes)

        items: list[dict[str, Any]] = []
        for code in codes:
            if code not in stats_map:
                continue
            stats = stats_map[code]
            column = frame[code]
            output_mask = column.loc[
                (column.index >= pd.Timestamp(start))
                & (column.index <= pd.Timestamp(end))
            ]
            valid_bool = output_mask.notna()
            values = valid_bool.to_numpy()
            valid_count = int(values.sum())
            leading_nan = int(np.argmax(values)) if valid_count else len(values)
            valid_dates = [
                d.date() for d in output_mask.index if bool(valid_bool.loc[d])
            ]
            member_count_total = int(
                stats["member_count"].loc[
                    (stats.index >= pd.Timestamp(start))
                    & (stats.index <= pd.Timestamp(end))
                ].max()
            )
            output_stats = stats.loc[
                (stats.index >= pd.Timestamp(start))
                & (stats.index <= pd.Timestamp(end))
            ]
            has_member = output_stats["member_count"] > 0
            no_sample = int(
                ((output_stats["member_count"] > 0) & (output_stats["valid_count"] == 0)).sum()
            )
            avg_sample = (
                float(output_stats.loc[has_member, "valid_count"].mean())
                if has_member.any()
                else None
            )
            data_start, data_end = row_ranges.get(code, (None, None))
            items.append(
                {
                    "industry_code": code,
                    "member_count": member_count_total,
                    "data_start_date": data_start,
                    "data_end_date": data_end,
                    "output_days": len(output_dates),
                    "no_sample_days": no_sample,
                    "valid_count": valid_count,
                    "valid_from": valid_dates[0] if valid_dates else None,
                    "valid_until": valid_dates[-1] if valid_dates else None,
                    "avg_sample_count": avg_sample,
                }
            )
            if leading_nan > 0 or valid_count == 0:
                issues.append(
                    {
                        "level": "warn",
                        "code": "DIFFUSION_WARMUP_LEADING",
                        "message": (
                            f"{code} 输出窗口前 {leading_nan} 个交易日无有效扩散"
                            "（回看或平滑 warm-up 不足/数据起点较晚）。"
                        ),
                        "industry_codes": [code],
                        "count": leading_nan,
                        "sample_dates": [d.isoformat() for d in output_dates[:3]],
                    }
                )
            if no_sample > 0:
                issues.append(
                    {
                        "level": "warn",
                        "code": "NO_SAMPLE_DAYS",
                        "message": (
                            f"{code} 输出窗口内有 {no_sample} 个交易日成员股无有效样本"
                            "（停牌/未上市/缺口），当日原始扩散为 NaN。"
                        ),
                        "industry_codes": [code],
                        "count": no_sample,
                        "sample_dates": [],
                    }
                )
        extra: dict[str, Any] = {"items": items, "issues": issues, "daily": daily}
        return frame, extra

    def compute_and_store(
        self,
        *,
        start: date,
        end: date,
        industry_codes: list[str] | None = None,
        lookback_ratio: int = 220,
        lookback_mom: int = 60,
        smooth_window: int = 20,
        diffusion_lookback: int = 220,
        benchmark_exclude: list[str] | None = None,
    ) -> dict[str, int]:
        """计算区间内全部行业因子并持久化。

        行携带规范化参数与参数指纹（params_hash），不同参数组合分行存储，
        互不覆盖（P12）。

        Args:
            start: 计算区间起始日。
            end: 计算区间结束日。
            industry_codes: 行业代码列表，None 表示全部启用行业。
            lookback_ratio: RS-Ratio 比率回看天数。
            lookback_mom: RS-Momentum 比率回看天数。
            smooth_window: MA 平滑窗口。
            diffusion_lookback: 扩散上涨判定回看天数。
            benchmark_exclude: RRG 行业等权基准剔除行业代码列表。

        Returns:
            {industry_count, date_count, factor_row_count} 统计。
        """
        params = canonical_industry_params(
            lookback_ratio=lookback_ratio,
            lookback_mom=lookback_mom,
            smooth_window=smooth_window,
            diffusion_lookback=diffusion_lookback,
            benchmark_exclude=benchmark_exclude,
        )
        params_hash = industry_params_hash(
            lookback_ratio=lookback_ratio,
            lookback_mom=lookback_mom,
            smooth_window=smooth_window,
            diffusion_lookback=diffusion_lookback,
            benchmark_exclude=benchmark_exclude,
        )
        panels = self.build_panels(
            start=start,
            end=end,
            industry_codes=industry_codes,
            lookback_ratio=lookback_ratio,
            lookback_mom=lookback_mom,
            smooth_window=smooth_window,
            diffusion_lookback=diffusion_lookback,
        )
        date_values = [d for d in panels["trading_dates"] if start <= d <= end]
        rows: list[dict[str, Any]] = []
        total_rows = 0
        chunk_size = 20_000
        for factor_id, panel_name in (
            ("rrg_rs_ratio", "rs_ratio"),
            ("rrg_rs_momentum", "rs_momentum"),
            ("rrg_quadrant", "quadrant"),
            ("diffusion_count_ratio", "diffusion"),
        ):
            panel = panels[panel_name]
            if panel.empty:
                continue
            for d in date_values:
                # 行业面板使用 DatetimeIndex，而交易日轴为 date；直接比较会导致
                # 所有日期都不命中，使任务表面成功但实际零行落库。
                panel_date = pd.Timestamp(d)
                if panel_date not in panel.index:
                    continue
                row = panel.loc[panel_date]
                for code in panels["industry_codes"]:
                    if code not in panel.columns:
                        continue
                    value = row.get(code)
                    rows.append(
                        {
                            "trade_date": d,
                            "industry_code": code,
                            "factor_id": factor_id,
                            "factor_value_numeric": (None if pd.isna(value) else float(value)),
                            "factor_payload": None,
                            "params_hash": params_hash,
                            "params": params,
                            "updated_at": utcnow(),
                        }
                    )
                    if len(rows) >= chunk_size:
                        self._factor_repo.bulk_upsert(rows)
                        total_rows += len(rows)
                        rows = []
        if rows:
            self._factor_repo.bulk_upsert(rows)
            total_rows += len(rows)
        self._db.commit()
        return {
            "industry_count": len(panels["industry_codes"]),
            "date_count": len(date_values),
            "factor_row_count": total_rows,
        }

    def load_factor_rows(
        self,
        *,
        factor_id: str,
        start: date,
        end: date,
        industry_codes: list[str] | None = None,
        params_hash: str | None = None,
    ) -> pd.DataFrame:
        """从 industry_factor_value 读取因子宽表（date × industry_code）。

        Args:
            factor_id: 行业因子 ID。
            start: 起始日期。
            end: 结束日期。
            industry_codes: 行业代码列表。
            params_hash: 参数指纹；None 表示不按参数过滤。
        """
        codes = self.resolve_industry_codes(industry_codes)
        rows = self._factor_repo.find_values(factor_id, start, end, codes, params_hash)
        data: dict[tuple[date, str], float | None] = {}
        for row in rows:
            data[(row.trade_date, row.industry_code)] = row.factor_value_numeric
        if not data:
            return pd.DataFrame(columns=codes)
        frame = pd.DataFrame(
            [
                {
                    "trade_date": d,
                    **{code: data.get((d, code)) for code in codes},
                }
                for d in sorted({d for d, _ in data})
            ]
        ).set_index("trade_date")
        return frame.reindex(columns=codes)

def _rrg_rules(
    *,
    lookback_ratio: int,
    lookback_mom: int,
    smooth_window: int,
) -> list[str]:
    """按本次参数生成 RRG 口径说明（供调试页展示）。"""
    ratio_axis = (
        f"RS-Ratio = MA({smooth_window})(100 × RS_t / RS_{{t-{lookback_ratio}}})；"
        f"RS-Momentum = MA({smooth_window})(100 × RSR_t / RSR_{{t-{lookback_mom}}})。"
    )
    return [
        "RS = 行业收盘 / 基准 × 100；基准 = 所选行业中剔除“综合”后的等权组合净值。",
        ratio_axis,
        "行业日线相对市场日历缺交易日时会 ffill 兜底，缺口条数进入下方问题与覆盖表。",
    ]


def _diffusion_rules(*, lookback: int, smooth_window: int) -> list[str]:
    """按本次参数生成扩散口径说明（含缺数据处理规则）。"""
    return [
        f"上涨判定：close_t > close_{{t-{lookback}}}，两日收盘任一缺失则该股票当日不计样本。",
        "有效样本：当日属于该行业且判涨所需两日收盘均存在的股票；"
        "某日无有效样本时原始扩散为 NaN。",
        f"平滑：原始扩散做 {smooth_window} 日 MA；{smooth_window} 日窗口内含任意 NaN"
        "时平滑值也为 NaN。",
        "个股收盘为未复权口径（P14 待定），除权除息会产生人工涨跌，仅限研究观察。",
    ]


def _bars_to_close_panel(rows: list[Any], codes: list[str]) -> pd.DataFrame:
    """将行业日线 ORM 行转成 date × industry_code 收盘价宽表。"""
    data: dict[date, dict[str, float]] = {}
    for row in rows:
        if row.close_price is None:
            continue
        data.setdefault(row.trade_date, {})[row.industry_code] = float(row.close_price)
    frame = pd.DataFrame.from_dict(data, orient="index")
    frame.index = pd.DatetimeIndex(frame.index)
    frame = frame.sort_index()
    return frame.reindex(columns=[c for c in codes if c in frame.columns])


def _stock_close_panel(rows: list[Any]) -> pd.DataFrame:
    """将个股收盘 ORM 行转成 date × stock_code 收盘价宽表。"""
    data: dict[date, dict[str, float]] = {}
    for row in rows:
        if row.close is None:
            continue
        data.setdefault(row.trade_date, {})[row.stock_code] = float(row.close)
    frame = pd.DataFrame.from_dict(data, orient="index")
    frame.index = pd.DatetimeIndex(frame.index)
    return frame.sort_index()


def _membership_panel(
    events: list[Any],
    dates: pd.DatetimeIndex,
    stock_columns: pd.Index,
) -> tuple[pd.DataFrame, list[str]]:
    """把成分事件流扩展为 date × stock 的行业序号宽表（数值标签）。

    对每只股票取 start_date <= 当日的最新事件作为当日归属；无归属记 NaN。
    """
    stocks = sorted(stock_columns.astype(str).tolist())
    stock_index = {code: i for i, code in enumerate(stocks)}
    industry_codes = sorted({event.industry_code for event in events})
    code_index = {code: i for i, code in enumerate(industry_codes)}
    event_rows = sorted(events, key=lambda e: e.start_date)
    cur = np.full(len(stocks), np.nan, dtype=np.float32)
    pointer = 0
    matrix_rows: list[np.ndarray] = []
    for d in dates:
        while pointer < len(event_rows) and event_rows[pointer].start_date <= d.date():
            event = event_rows[pointer]
            si = stock_index.get(event.stock_code)
            if si is not None:
                cur[si] = float(code_index[event.industry_code])
            pointer += 1
        matrix_rows.append(cur.copy())
    if not matrix_rows:
        return pd.DataFrame(index=dates, columns=stocks, dtype=float), industry_codes
    matrix = np.vstack(matrix_rows)
    return pd.DataFrame(matrix, index=dates, columns=stocks), industry_codes
