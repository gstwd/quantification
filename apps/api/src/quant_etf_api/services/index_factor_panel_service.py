"""指数级复合因子数据装配服务。

为 rrg_industry_match_score / index_diffusion_ratio 组装 FactorContext
所需的 panels：
- index_membership：指数成分事件按日期的有效成员（含权重）；
- stock_closes：成员股收盘；
- industry_selection：从原始行业行情按研报信号产生的每日选中行业集合；
- index_industry_exposure：指数成分的申万行业暴露（有权重按权重，否则
  等权），用于与选中行业集合计算重合度。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from quant_etf_api.domain.industry.constants import normalize_sw_code
from quant_etf_api.domain.industry.selection import (
    IndustryRotationEngine,
    IndustryRotationInput,
    IndustrySelectionConfig,
)
from quant_etf_api.factors.builtins.index_panel_factors import _UNMAPPED_INDUSTRY
from quant_etf_api.infra.db.models.core import IndexMemberEventModel
from quant_etf_api.infra.db.models.industry import IndustryMembershipEventModel
from quant_etf_api.infra.db.repositories.index_member import IndexMemberEventRepository
from quant_etf_api.infra.db.repositories.industry import (
    StockDailyCloseRepository,
)

logger = logging.getLogger(__name__)


def _merge_members_by_stock(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按股票去重成分列表：同一股票保留 start_date 最新的事件。

    PIT 月度回填与“当前快照”可能覆盖同一区间（例如月末 PIT 事件会前向沿用
    到最新日），同日冲突时优先当前快照，避免扩散/行业暴露把成员重复计数。

    Args:
        members: 某交易日命中的所有成分事件（含 stock_code/start_date/类型）。

    Returns:
        每只股票至多一条的去重结果（保持输入顺序）。
    """
    best: dict[str, dict[str, Any]] = {}
    for member in members:
        stock_code = member["stock_code"]
        current = best.get(stock_code)
        if current is None:
            best[stock_code] = member
            continue
        if member["start_date"] > current["start_date"]:
            best[stock_code] = member
            continue
        if (
            member["start_date"] == current["start_date"]
            and member.get("snapshot_type") == "current_snapshot"
            and current.get("snapshot_type") != "current_snapshot"
        ):
            best[stock_code] = member
    return list(best.values())


class IndexFactorPanelService:
    """按指数/区间从 DB 组装复合因子数据面板（实时与回测共用）。"""

    def __init__(self, db: Session) -> None:
        """初始化面板装配服务。

        Args:
            db: SQLAlchemy 同步 Session。
        """
        self._db = db

    def build_panels(
        self,
        index_codes: list[str],
        dates: list[date],
        lookback_natural_days: int = 820,
        include_industry_panels: bool = True,
        calculate_industry_selection: bool = False,
    ) -> dict[str, Any]:
        """构建指定区间内复合因子需要的全部数据面板。

        Args:
            index_codes: 指数代码列表。
            dates: 需要产出的交易日（升序）。
            lookback_natural_days: 为成分扩散预留的个股收盘回看窗口。
            include_industry_panels: 是否加载 RRG 匹配度所需的行业选择和行业暴露面板。
                单日扩散调试只需成分与个股收盘，传 False 可避免无关的行业表查询。
            calculate_industry_selection: 保留兼容参数，行业选择始终从原始行业行情即时计算。

        Returns:
            panels 字典（index_membership / stock_closes /
            industry_selection / index_industry_exposure）。
        """
        if not index_codes or not dates:
            return {}
        logger.debug(
            "指数复合因子面板开始装配: indexes=%d dates=%d start=%s end=%s industry=%s",
            len(index_codes),
            len(dates),
            dates[0],
            dates[-1],
            include_industry_panels,
        )
        membership = self._build_membership(index_codes, dates)
        closes = self._build_stock_closes(membership, dates, lookback_natural_days)
        # 行业选择直接从原始行业日线计算，避免依赖独立的行业因子物化表。
        selection = (
            self._build_industry_selection_from_source(dates)
            if include_industry_panels
            else {}
        )
        exposure = self._build_industry_exposure(membership, dates) if include_industry_panels else {}
        exposure_meta = (
            self._build_industry_exposure_meta(membership, dates) if include_industry_panels else {}
        )
        membership_rows = sum(
            len(members) for daily in membership.values() for members in daily.values()
        )
        stock_close_points = sum(len(series) for series in closes.values())
        result = {
            "calculation_dates": dates,
            "index_membership": membership,
            "stock_closes": closes,
            "industry_selection": selection,
            "index_industry_exposure": exposure,
            "index_industry_exposure_meta": exposure_meta,
            # 记录真实加载规模，便于后台任务监控成本，实时链路不应出现该面板。
            "panel_metrics": {
                "calculation_date_count": len(dates),
                "index_count": len(index_codes),
                "membership_row_count": membership_rows,
                "stock_count": len(closes),
                "stock_close_point_count": stock_close_points,
            },
        }
        logger.debug("指数复合因子面板装配完成: metrics=%s", result["panel_metrics"])
        return result

    def _build_membership(
        self,
        index_codes: list[str],
        dates: list[date],
    ) -> dict[str, dict[date, list[dict[str, Any]]]]:
        """按日期展开每只指数的有效成分（事件区间前向沿用）。"""
        repo = IndexMemberEventRepository(self._db)
        result: dict[str, dict[date, list[dict[str, Any]]]] = {}
        for index_code in index_codes:
            events = repo.find_events(index_code)
            # 为 PIT 取样补齐 end_date：下一事件 start_date - 1；当前快照
            # （end_date=None）沿用至最新日期
            by_start: dict[date, list[IndexMemberEventModel]] = {}
            for event in events:
                by_start.setdefault(event.start_date, []).append(event)
            starts = sorted(by_start)
            ends: dict[date, date | None] = {}
            for i, start in enumerate(starts):
                ends[start] = (
                    starts[i + 1] - timedelta(days=1) if i + 1 < len(starts) else None
                )
            effective: dict[date, list[dict[str, Any]]] = {}
            for trade_date in dates:
                members: list[dict[str, Any]] = []
                for start in starts:
                    if start > trade_date:
                        break
                    end = ends.get(start)
                    if end is not None and trade_date > end:
                        continue
                    for event in by_start[start]:
                        members.append(
                            {
                                "stock_code": event.stock_code,
                                "weight": event.weight,
                                "start_date": event.start_date,
                                "snapshot_type": event.snapshot_type,
                            }
                        )
                if members:
                    effective[trade_date] = _merge_members_by_stock(members)
            result[index_code] = effective
        return result

    def _build_stock_closes(
        self,
        membership: dict[str, dict[date, list[dict[str, Any]]]],
        dates: list[date],
        lookback_natural_days: int,
    ) -> dict[str, dict[date, float | None]]:
        """加载成员股在 [区间起点-lookback, 区间终点] 内的收盘序列。"""
        stock_codes = {
            member["stock_code"]
            for daily in membership.values()
            for members in daily.values()
            for member in members
        }
        if not stock_codes:
            return {}
        start = dates[0] - timedelta(days=lookback_natural_days)
        rows = StockDailyCloseRepository(self._db).find_close_rows(start, dates[-1], stock_codes)
        closes: dict[str, dict[date, float | None]] = {}
        for trade_date, stock_code, close in rows:
            closes.setdefault(stock_code, {})[trade_date] = close
        return closes

    def _build_industry_selection_from_source(
        self,
        dates: list[date],
    ) -> dict[date, dict[str, float]]:
        """从原始行业数据即时构建回测所需的 RRG/扩散行业选择。

        直接从原始行业行情构建 RRG 行业选择，实时和回测共用该路径，
        不写数据库，保证结果不受任务调度历史影响。

        Args:
            dates: 回测交易日列表。

        Returns:
            按交易日映射的 ``{行业代码: 等权重}`` 选择结果；无有效信号的日期省略。
        """
        if not dates:
            return {}

        from quant_etf_api.services.industry_factor_service import IndustryFactorService

        panels = IndustryFactorService(self._db).build_panels(
            start=dates[0],
            end=dates[-1],
            need_diffusion=False,
        )
        logger.debug(
            "RRG行业选择面板完成: dates=%d industries=%d",
            len(panels.get("trading_dates", [])),
            len(panels.get("industry_codes", [])),
        )
        ratio = panels["rs_ratio"]
        momentum = panels["rs_momentum"]
        quadrant = panels["quadrant"]
        diffusion = panels["diffusion"]
        engine = IndustryRotationEngine()
        config = IndustrySelectionConfig()
        result: dict[date, dict[str, float]] = {}

        for trade_date in dates:
            timestamp = pd.Timestamp(trade_date)
            if timestamp not in quadrant.index:
                continue
            data = IndustryRotationInput(
                trade_date=trade_date,
                industry_codes=list(panels["industry_codes"]),
                rs_ratio=ratio.loc[timestamp].to_dict() if timestamp in ratio.index else {},
                rs_momentum=momentum.loc[timestamp].to_dict()
                if timestamp in momentum.index
                else {},
                quadrant={
                    code: int(value) if pd.notna(value) else None
                    for code, value in quadrant.loc[timestamp].items()
                },
                diffusion=diffusion.loc[timestamp].to_dict() if timestamp in diffusion.index else {},
            )
            selected, weights = engine.select(config, data)
            if selected:
                result[trade_date] = weights
        return result

    def _build_industry_exposure(
        self,
        membership: dict[str, dict[date, list[dict[str, Any]]]],
        dates: list[date],
    ) -> dict[str, dict[date, dict[str, float]]]:
        """按指数成分（权重可选）聚合申万一级行业暴露。"""
        stock_rows = (
            self._db.query(IndustryMembershipEventModel)
            .filter(IndustryMembershipEventModel.start_date <= dates[-1])
            .all()
        )
        industry_by_stock: dict[str, list[tuple[date, str]]] = {}
        for row in stock_rows:
            industry_by_stock.setdefault(row.stock_code, []).append((row.start_date, row.industry_code))
        for events in industry_by_stock.values():
            events.sort(key=lambda item: item[0])

        def industry_asof(stock_code: str, trade_date: date) -> str | None:
            events = industry_by_stock.get(stock_code) or []
            hit = None
            for start, industry in events:
                if start > trade_date:
                    break
                hit = industry
            return hit

        exposure: dict[str, dict[date, dict[str, float]]] = {}
        for index_code, daily in membership.items():
            by_date: dict[date, dict[str, float]] = {}
            for trade_date, members in daily.items():
                industries: dict[str, float] = {}
                # 仅全部权重可用时按权重聚合，避免权重与等权混合造成量纲错误。
                weighted = all(member.get("weight") is not None for member in members)
                for member in members:
                    industry = industry_asof(member["stock_code"], trade_date)
                    value = member.get("weight") if weighted else None
                    if value is None:
                        value = 1.0 / len(members)
                    if industry is None:
                        industries[_UNMAPPED_INDUSTRY] = (
                            industries.get(_UNMAPPED_INDUSTRY, 0.0) + float(value)
                        )
                        continue
                    industry = normalize_sw_code(industry)
                    industries[industry] = industries.get(industry, 0.0) + float(value)
                if industries:
                    by_date[trade_date] = industries
            exposure[index_code] = by_date
        return exposure

    def _build_industry_exposure_meta(
        self,
        membership: dict[str, dict[date, list[dict[str, Any]]]],
        dates: list[date],
    ) -> dict[str, dict[date, dict[str, int | float | str]]]:
        """统计指数成分行业归属覆盖与权重口径，供匹配因子解释。"""
        stock_rows = (
            self._db.query(IndustryMembershipEventModel)
            .filter(IndustryMembershipEventModel.start_date <= dates[-1])
            .all()
        )
        by_stock: dict[str, list[date]] = {}
        for row in stock_rows:
            by_stock.setdefault(row.stock_code, []).append(row.start_date)
        result: dict[str, dict[date, dict[str, int | float | str]]] = {}
        for index_code, daily in membership.items():
            result[index_code] = {}
            for trade_date, members in daily.items():
                member_count = len(members)
                mapped = sum(
                    any(start <= trade_date for start in by_stock.get(member["stock_code"], []))
                    for member in members
                )
                result[index_code][trade_date] = {
                    "member_count": member_count,
                    "mapped_member_count": mapped,
                    "unmapped_member_count": member_count - mapped,
                    "industry_coverage": round(mapped / member_count, 6) if member_count else 0.0,
                    "weighting_mode": (
                        "index_weight" if all(member.get("weight") is not None for member in members) else "equal"
                    ),
                }
        return result
