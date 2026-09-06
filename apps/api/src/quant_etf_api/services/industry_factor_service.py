"""行业因子面板构建与持久化服务（RRG / 扩散数量占比）。"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from quant_etf_api.domain.industry.constants import (
    SW_EXCLUDED_INDUSTRY_CODES,
    normalize_sw_code,
)
from quant_etf_api.domain.industry.factor_algo import (
    classify_quadrant,
    compute_rrg,
    diffusion_count_ratio,
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
# 扩散需要 lookback + smooth（默认 240 个交易日），折算自然日缓冲约 400 天
_DIFFUSION_WARMUP_NATURAL_DAYS = 400


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
    ) -> dict[str, Any]:
        """构建行业因子面板。

        自动向前多取 warm-up 行情/成分数据，返回的面板仅含 [start, end] 行。

        Returns:
            dict：rs_ratio/rs_momentum/quadrant/diffusion 为 DataFrame，
            industry_close 为行业收盘价宽表，trading_dates 为交易日列表。
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
        trading_dates = [d.date() for d in close.index if start <= d.date() <= end]

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

        diffusion = pd.DataFrame(index=close.index, columns=close.columns, dtype=float)
        if need_diffusion:
            try:
                diffusion = self._build_diffusion_panel(
                    start=start,
                    end=end,
                    codes=codes,
                    lookback=diffusion_lookback,
                    smooth_window=smooth_window,
                )
            except ValueError as e:
                logger.warning("扩散指标计算失败: %s", e)

        def _slice(panel: pd.DataFrame) -> pd.DataFrame:
            if panel.empty:
                return panel
            return panel.loc[(panel.index >= start) & (panel.index <= end)]

        return {
            "rs_ratio": _slice(rs_ratio),
            "rs_momentum": _slice(rs_momentum),
            "quadrant": _slice(quadrant),
            "diffusion": _slice(diffusion),
            "industry_close": _slice(close),
            "trading_dates": trading_dates,
            "industry_codes": codes,
        }

    def _build_diffusion_panel(
        self,
        *,
        start: date,
        end: date,
        codes: list[str],
        lookback: int,
        smooth_window: int,
    ) -> pd.DataFrame:
        """构建数量占比扩散面板（宽表，列=行业代码）。"""
        close_start = start - timedelta(days=_DIFFUSION_WARMUP_NATURAL_DAYS)
        close_rows = self._close_repo.find_range(close_start, end)
        if not close_rows:
            raise ValueError("个股收盘无数据，请先执行 industry backfill-stock-close")
        close_panel = _stock_close_panel(close_rows)
        events = self._membership_repo.find_events_until(end)
        if not events:
            raise ValueError("行业成分无数据，请先执行 industry backfill-membership")
        membership, full_industry_codes = _membership_panel(
            events, close_panel.index, close_panel.columns
        )
        diffusion = diffusion_count_ratio(
            close_panel,
            membership,
            lookback=lookback,
            smooth_window=smooth_window,
        )
        if diffusion.empty:
            return pd.DataFrame(index=close_panel.index, columns=codes, dtype=float)
        # 算法层按 membership 值（行业序号）输出列标签，此处映射回行业代码
        diffusion = diffusion.rename(
            columns={float(i): code for i, code in enumerate(full_industry_codes)}
        )
        return diffusion.reindex(columns=codes)

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
    ) -> dict[str, int]:
        """计算区间内全部行业因子并持久化。

        Returns:
            {industry_count, date_count, factor_row_count} 统计。
        """
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
                if d not in panel.index:
                    continue
                row = panel.loc[d]
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
    ) -> pd.DataFrame:
        """从 industry_factor_value 读取因子宽表（date × industry_code）。"""
        codes = self.resolve_industry_codes(industry_codes)
        rows = self._factor_repo.find_values(factor_id, start, end, codes)
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
