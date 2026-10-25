"""日频市场指标同步编排：按分组增量 / 回补拉取并幂等写入 market_indicator。

数据源优先级与降级在本服务决定，客户端只负责拉取与归一化：

- ``cgb10y``：AkShare（Tushare yc_cb 需单独权限，不作为来源）；
- ``margin`` / ``futures`` / ``options``：Tushare。

上游失败只影响对应分组（记入 errors），已有数据不会被覆盖；同步最新数据从各分组已入库
的最新交易日次日拉起，回补 / 重建从分组的历史起点（``GROUP_EPOCH``）拉起。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.domain.market_data.indicators import (
    FUTURES_PRODUCTS,
    GROUP_EPOCH,
    MARKET_INDICATORS,
    MarketIndicatorPoint,
    codes_of_group,
)
from quant_etf_api.infra.clients.market_indicator_client import (
    AkShareBondYieldClient,
    TushareMarketIndicatorClient,
    option_trade_dates,
)
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.infra.db.repositories.market_indicator import MarketIndicatorRepository

logger = logging.getLogger(__name__)

# 分组拉取顺序（期权逐日调用最慢，放最后）
GROUP_ORDER: tuple[str, ...] = ("cgb10y", "margin", "futures", "options")


class MarketIndicatorService:
    """日频市场指标同步服务。"""

    def __init__(
        self,
        db: Session,
        tushare_client: TushareMarketIndicatorClient | None = None,
        akshare_client: AkShareBondYieldClient | None = None,
    ) -> None:
        """初始化。

        Args:
            db: 数据库会话。
            tushare_client: Tushare 客户端（测试可注入）。
            akshare_client: AkShare 国债收益率客户端（测试可注入）。
        """
        self._db = db
        self._repo = MarketIndicatorRepository(db)
        self._tushare = tushare_client or TushareMarketIndicatorClient()
        self._akshare = akshare_client or AkShareBondYieldClient()

    def sync(
        self,
        end: date,
        *,
        partition_key: str | None = None,
        from_epoch: bool = False,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """同步一个或全部分组。

        Args:
            end: 截止交易日（含）。
            partition_key: 指标代码；给出时只同步其所属分组。
            from_epoch: True 时从分组历史起点拉起（回补 / 重建）。
            overwrite: True 时覆盖已有值（重建）；False 时只补缺失。

        Returns:
            ``{"records", "errors", "sources", "ranges"}``。

        Raises:
            ValueError: 指标代码未知。
        """
        if partition_key is not None and partition_key not in MARKET_INDICATORS:
            raise ValueError(f"未知的市场指标: {partition_key}")
        groups = (
            [MARKET_INDICATORS[partition_key][2]]
            if partition_key is not None
            else list(GROUP_ORDER)
        )
        records = 0
        errors: list[str] = []
        sources: dict[str, int] = {}
        ranges: list[dict[str, Any]] = []
        for group in groups:
            start = self._start_of(group, from_epoch)
            if start > end:
                continue
            try:
                points = self._fetch(group, start, end)
                written = self._repo.upsert(points, overwrite=overwrite)
                self._db.commit()
            except Exception as exc:  # noqa: BLE001
                # 分组失败不影响其他分组，也不覆盖已有数据
                self._db.rollback()
                logger.warning("市场指标分组同步失败: group=%s", group, exc_info=True)
                errors.append(f"{group}: {type(exc).__name__}: {exc}")
                continue
            records += written
            failed_days = (
                getattr(self._tushare, "failed_option_days", None) if group == "options" else None
            )
            if failed_days:
                errors.append(
                    f"options: {len(failed_days)} 个交易日重试后仍拉取失败（首个 {failed_days[0]}），"
                    "其余交易日已写入，可再次执行回补"
                )
            for point in points:
                sources[point.source] = sources.get(point.source, 0) + 1
            ranges.append({"group": group, "start": start.isoformat(), "end": end.isoformat()})
        return {"records": records, "errors": errors, "sources": sources, "ranges": ranges}

    def _start_of(self, group: str, from_epoch: bool) -> date:
        """分组本次拉取的起点：历史起点，或已入库最新交易日的次日。"""
        epoch = GROUP_EPOCH[group]
        if from_epoch:
            return epoch
        latest = self._repo.latest_dates(codes_of_group(group))
        if not latest:
            return epoch
        # 取分组内最旧的最新日，保证各指标都追平
        return min(latest.values()) + timedelta(days=1)

    def _basis_points(
        self,
        product: str,
        fetched: list[MarketIndicatorPoint],
        start: date,
        end: date,
    ) -> list[MarketIndicatorPoint]:
        """用已入库的现货指数收盘计算基差与年化基差（%）。

        基差 = 期货主力收盘 / 现货收盘 − 1；年化基差 = 基差 × 365 / 主力合约剩余自然日
        （剩余天数为 0 的交割日不出年化值）。现货收盘缺失的交易日不出值。

        Args:
            product: 期货品种。
            fetched: 本次拉取的主力收盘与剩余天数。
            start: 起始日。
            end: 截止日。

        Returns:
            ``fut_<品种>_basis`` / ``fut_<品种>_basis_ann`` 观测点。
        """
        _continuous, spot = FUTURES_PRODUCTS[product]
        spot_close = {
            bar.trade_date: float(bar.close_price)
            for bar in IndexDailyBarRepository(self._db).find_by_code_date_range(spot, start, end)
            if bar.close_price
        }
        closes = {p.trade_date: p.value for p in fetched if p.indicator_code.endswith("_close")}
        dtes = {p.trade_date: p.value for p in fetched if p.indicator_code.endswith("_dte")}
        points: list[MarketIndicatorPoint] = []
        for day, future_close in sorted(closes.items()):
            base = spot_close.get(day)
            if not base:
                continue
            basis = (future_close / base - 1) * 100
            points.append(MarketIndicatorPoint(f"fut_{product}_basis", day, basis, "tushare"))
            dte = dtes.get(day)
            if dte:
                points.append(
                    MarketIndicatorPoint(
                        f"fut_{product}_basis_ann", day, basis * 365 / dte, "tushare"
                    )
                )
        return points

    def _fetch(self, group: str, start: date, end: date) -> list[MarketIndicatorPoint]:
        """按分组调用上游。"""
        if group == "cgb10y":
            return [p for p in self._akshare.fetch_cgb10y(start) if p.trade_date <= end]
        if group == "margin":
            return self._tushare.fetch_margin(start, end)
        if group == "futures":
            points: list[MarketIndicatorPoint] = []
            for product in FUTURES_PRODUCTS:
                fetched = self._tushare.fetch_futures(product, start, end)
                points.extend(fetched)
                points.extend(self._basis_points(product, fetched, start, end))
            return points
        if group == "options":
            calendar = IndexDailyBarRepository(self._db).find_all_trading_dates(start, end)
            return self._tushare.fetch_option_pcr(option_trade_dates(calendar, start, end))
        raise ValueError(f"未知的市场指标分组: {group}")
