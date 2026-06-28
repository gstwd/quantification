"""市场日志服务，编排日志 CRUD、自动填充、交易日历查询等业务逻辑。"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from uuid import uuid4 as _uuid4

from sqlalchemy.orm import Session

from quant_etf_api.config.settings import get_settings
from quant_etf_api.infra.db.models.core import (
    BenchmarkIndexModel,
    IndexDailyBarModel,
    IndexFactorValueModel,
    JournalEntryModel,
    JournalEntryTagModel,
    JournalTagModel,
    TradingCalendarModel,
)
from quant_etf_api.infra.db.repositories.journal_repository import JournalRepository
from quant_etf_api.schemas.journal import (
    AIAnalysisResponse,
    CalendarDay,
    CalendarResponse,
    IndexSnapshotRow,
    JournalEntryCreate,
    JournalEntryDetail,
    JournalEntrySummary,
    JournalEntryUpdate,
    JournalMarketData,
    ObservationRow,
    ObservationsBatchUpdate,
    SetTagsRequest,
    TagCreate,
    TagSummary,
    TagUpdate,
)

_logger = logging.getLogger(__name__)

# 自动填充所需的因子 ID 列表（strategy_id IS NULL）
_SNAPSHOT_FACTOR_IDS = [
    "volume_ratio_20d",
    "return_5d",
    "return_20d",
    "return_60d",
    "return_120d",
    "ma_20d",
    "ma_60d",
    "ma_120d",
    "volatility_20d",
    "max_drawdown_60d",
]


class JournalService:
    """市场日志服务，封装日志模块的全部业务逻辑。

    组合 JournalRepository 完成数据访问，负责自动填充快照、交易日历查询、
    字数统计等编排逻辑。事务由调用方（router）通过 db.commit() 统一管理。
    """

    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = JournalRepository(db)

    # =========================================================================
    # 日历
    # =========================================================================

    def get_calendar(self, year: int, month: int | None = None) -> CalendarResponse:
        """获取指定年/月的日历视图数据。

        Args:
            year: 年份。
            month: 月份（1-12），不传则返回全年。

        Returns:
            CalendarResponse 包含该时间范围内每天的日历信息。
        """
        # 确定日期范围
        if month is not None:
            start_date = date(year, month, 1)
            if month == 12:
                end_date = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                end_date = date(year, month + 1, 1) - timedelta(days=1)
        else:
            start_date = date(year, 1, 1)
            end_date = date(year, 12, 31)

        # 获取交易日列表
        trading_days = self._get_trading_days_set(start_date, end_date)

        # 获取该日期范围内的所有日志
        entries = (
            self._db.query(JournalEntryModel)
            .filter(
                JournalEntryModel.trade_date >= start_date,
                JournalEntryModel.trade_date <= end_date,
            )
            .order_by(JournalEntryModel.trade_date)
            .all()
        )
        entry_map = {e.trade_date: e for e in entries}

        # 收集有日志日期的标签
        entry_ids = [e.id for e in entries]
        tag_map: dict[str, list] = {}
        if entry_ids:
            tag_rows = (
                self._db.query(JournalEntryTagModel.entry_id, JournalTagModel)
                .join(JournalTagModel, JournalEntryTagModel.tag_id == JournalTagModel.id)
                .filter(JournalEntryTagModel.entry_id.in_(entry_ids))
                .all()
            )
            for entry_id, tag in tag_rows:
                tag_map.setdefault(entry_id, []).append(
                    TagSummary(
                        id=tag.id,
                        name=tag.name,
                        color=tag.color,
                        description=tag.description,
                        is_system=tag.is_system,
                        usage_count=tag.usage_count,
                    )
                )

        # 构建每天的日历数据
        days: list[CalendarDay] = []
        current = start_date
        while current <= end_date:
            is_trading = current in trading_days
            entry = entry_map.get(current)
            has_entry = entry is not None

            days.append(
                CalendarDay(
                    date=current,
                    is_trading_day=is_trading,
                    has_entry=has_entry,
                    entry_id=entry.id if entry else None,
                    market_phase=entry.market_phase if entry else None,
                    market_temperature=entry.market_temperature if entry else None,
                    tags=tag_map.get(entry.id, []) if entry else [],
                    one_line_summary=entry.one_line_summary if entry else None,
                )
            )
            current += timedelta(days=1)

        return CalendarResponse(year=year, month=month, days=days)

    def _get_trading_days_set(self, start: date, end: date) -> set[date]:
        """获取日期范围内的交易日集合。

        优先从 trading_calendar 表读取，若表为空则降级使用 TradingCalendar 类。

        Args:
            start: 起始日期。
            end: 结束日期。

        Returns:
            交易日日期集合。
        """
        rows = (
            self._db.query(TradingCalendarModel.trade_date)
            .filter(
                TradingCalendarModel.trade_date >= start,
                TradingCalendarModel.trade_date <= end,
                TradingCalendarModel.is_trading_day.is_(True),
            )
            .all()
        )
        if rows:
            return {r.trade_date for r in rows}

        # 降级：使用 TradingCalendar 类（首次调用从 AkShare 拉取）
        try:
            from quant_etf_api.infra.trading_calendar import TradingCalendar

            cal = TradingCalendar()
            result: set[date] = set()
            current = start
            while current <= end:
                if cal.is_trading_day(current):
                    result.add(current)
                current += timedelta(days=1)
            return result
        except Exception:
            _logger.warning("交易日历不可用，降级为周末判断")
            result: set[date] = set()
            current = start
            while current <= end:
                if current.weekday() < 5:
                    result.add(current)
                current += timedelta(days=1)
            return result

    # =========================================================================
    # 日志 CRUD
    # =========================================================================

    def create_entry(self, req: JournalEntryCreate) -> JournalEntryDetail:
        """创建一条新的日志记录，自动填充指数快照和空观察分区。

        Args:
            req: 创建请求（仅含 trade_date）。

        Returns:
            创建后的完整日志详情。

        Raises:
            ValueError: 当日为非交易日或已存在日志。
        """
        # 校验交易日
        trading_days = self._get_trading_days_set(req.trade_date, req.trade_date)
        if req.trade_date not in trading_days:
            raise ValueError(f"{req.trade_date} 不是交易日，无法创建日志")

        # 检查重复
        existing = self._repo.find_entry_by_date(req.trade_date)
        if existing is not None:
            raise ValueError(f"日期 {req.trade_date} 已存在日志记录")

        # 创建日志主记录
        entry = self._repo.create_entry(req.trade_date)

        # 自动填充指数快照
        snapshots = self._build_index_snapshots(req.trade_date)
        if snapshots:
            self._repo.bulk_upsert_snapshots(entry.id, snapshots)

        # 预创建 10 个空观察分区
        self._repo.create_empty_observation_sections(entry.id)

        self._db.commit()
        return self.get_entry(entry.id)

    def get_entry(self, entry_id: str) -> JournalEntryDetail | None:
        """按 ID 获取日志完整详情。

        Args:
            entry_id: 日志 ID。

        Returns:
            日志详情，未找到返回 None。
        """
        entry = self._repo.find_entry_by_id(entry_id)
        if entry is None:
            return None
        return self._assemble_detail(entry)

    def get_entry_by_date(self, trade_date: date) -> JournalEntryDetail | None:
        """按交易日期获取日志详情。

        Args:
            trade_date: 交易日期。

        Returns:
            日志详情，未找到返回 None。
        """
        entry = self._repo.find_entry_by_date(trade_date)
        if entry is None:
            return None
        return self._assemble_detail(entry)

    def list_entries(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        tag_id: str | None = None,
        phase: str | None = None,
        is_complete: bool | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[JournalEntrySummary], int]:
        """分页查询日志列表。

        Args:
            date_from: 起始日期（含）。
            date_to: 结束日期（含）。
            tag_id: 按标签过滤。
            phase: 按市场阶段过滤。
            is_complete: 按完成状态过滤。
            offset: 分页偏移。
            limit: 每页条数。

        Returns:
            (日志摘要列表, 总数) 元组。
        """
        entries, total = self._repo.list_entries(
            date_from=date_from,
            date_to=date_to,
            tag_id=tag_id,
            phase=phase,
            is_complete=is_complete,
            offset=offset,
            limit=limit,
        )
        summaries = [self._assemble_summary(e) for e in entries]
        return summaries, total

    def update_entry(self, entry_id: str, data: JournalEntryUpdate) -> JournalEntryDetail | None:
        """更新日志内容。

        Args:
            entry_id: 日志 ID。
            data: 更新数据（仅更新传入的字段）。

        Returns:
            更新后的日志详情，未找到返回 None。
        """
        entry = self._repo.find_entry_by_id(entry_id)
        if entry is None:
            return None

        # 更新 journal_entry 标量字段
        scalar_fields = {
            "market_temperature",
            "profit_effect",
            "risk_preference",
            "trading_difficulty",
            "market_consistency",
            "market_phase",
            "one_line_summary",
            "is_complete",
        }
        update_kwargs = {}
        for field in scalar_fields:
            value = getattr(data, field, None)
            if value is not None:
                update_kwargs[field] = value
        if update_kwargs:
            self._repo.update_entry(entry_id, **update_kwargs)

        # 更新市场数据
        if data.market_data is not None:
            md = data.market_data
            md_dict = {
                k: v
                for k, v in {
                    "market_up_stocks": md.market_up_stocks,
                    "market_down_stocks": md.market_down_stocks,
                    "market_flat_stocks": md.market_flat_stocks,
                    "limit_up_stocks": md.limit_up_stocks,
                    "limit_down_stocks": md.limit_down_stocks,
                    "total_turnover_yi": md.total_turnover_yi,
                    "turnover_vs_prev_pct": md.turnover_vs_prev_pct,
                    "north_bound_net_yi": md.north_bound_net_yi,
                    "margin_balance_change_yi": md.margin_balance_change_yi,
                    "size_style": md.size_style,
                    "growth_style": md.growth_style,
                    "sector_leading": md.sector_leading,
                    "top_sectors": md.top_sectors,
                    "bottom_sectors": md.bottom_sectors,
                    "data_source": md.data_source,
                    "notes": md.notes,
                }.items()
                if v is not None
            }
            if md_dict:
                self._repo.upsert_market_data(entry_id, md_dict)

        # 重新计算字数
        word_count = self._repo.count_observation_words(entry_id)
        self._repo.update_entry(entry_id, word_count=word_count)

        self._db.commit()
        return self.get_entry(entry_id)

    def delete_entry(self, entry_id: str) -> bool:
        """删除日志（CASCADE 自动清理关联数据）。

        Args:
            entry_id: 日志 ID。

        Returns:
            是否成功删除。
        """
        result = self._repo.delete_entry(entry_id)
        if result:
            self._db.commit()
        return result

    # =========================================================================
    # 快照
    # =========================================================================

    def refresh_snapshots(self, entry_id: str) -> list[IndexSnapshotRow]:
        """刷新指定日志的指数快照数据。

        Args:
            entry_id: 日志 ID。

        Returns:
            更新后的快照行列表。

        Raises:
            ValueError: 日志不存在。
        """
        entry = self._repo.find_entry_by_id(entry_id)
        if entry is None:
            raise ValueError(f"日志 {entry_id} 不存在")

        snapshots = self._build_index_snapshots(entry.trade_date)
        if snapshots:
            self._repo.bulk_upsert_snapshots(entry_id, snapshots)

        self._db.commit()
        rows = self._repo.find_snapshots_by_entry(entry_id)
        return [self._snapshot_model_to_schema(r) for r in rows]

    # =========================================================================
    # 观察分区
    # =========================================================================

    def save_observations(
        self, entry_id: str, data: ObservationsBatchUpdate
    ) -> list[ObservationRow]:
        """批量保存观察分区内容。

        Args:
            entry_id: 日志 ID。
            data: 批量更新请求。

        Returns:
            更新后的观察分区列表。
        """
        obs_dicts = [
            {"section_key": o.section_key, "content": o.content} for o in data.observations
        ]
        self._repo.bulk_upsert_observations(entry_id, obs_dicts)

        # 更新字数
        word_count = self._repo.count_observation_words(entry_id)
        self._repo.update_entry(entry_id, word_count=word_count)

        self._db.commit()
        rows = self._repo.find_observations_by_entry(entry_id)
        return [self._obs_model_to_schema(r) for r in rows]

    # =========================================================================
    # 标签
    # =========================================================================

    def list_tags(self) -> list[TagSummary]:
        """获取所有标签列表（按使用次数降序）。"""
        tags = self._repo.find_all_tags()
        return [self._tag_model_to_schema(t) for t in tags]

    def create_tag(self, data: TagCreate) -> TagSummary:
        """创建新标签。

        Args:
            data: 标签创建请求。

        Returns:
            新创建的标签。

        Raises:
            ValueError: 标签名已存在。
        """
        tag = self._repo.create_tag(
            name=data.name,
            color=data.color,
            description=data.description,
        )
        try:
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise ValueError(f"标签 '{data.name}' 已存在")

        return self._tag_model_to_schema(tag)

    def update_tag(self, tag_id: str, data: TagUpdate) -> TagSummary | None:
        """更新标签信息。

        Args:
            tag_id: 标签 ID。
            data: 更新数据。

        Returns:
            更新后的标签，未找到返回 None。
        """
        update_kwargs = {}
        for field in ("name", "color", "description"):
            value = getattr(data, field, None)
            if value is not None:
                update_kwargs[field] = value
        if not update_kwargs:
            return None
        tag = self._repo.update_tag(tag_id, **update_kwargs)
        if tag is None:
            return None
        self._db.commit()
        return self._tag_model_to_schema(tag)

    def delete_tag(self, tag_id: str) -> bool:
        """删除标签（仅非系统标签可删除）。

        Args:
            tag_id: 标签 ID。

        Returns:
            是否成功删除。

        Raises:
            ValueError: 标签为系统预设标签，不可删除。
        """
        tag = self._repo.find_tag_by_id(tag_id)
        if tag is None:
            return False
        if tag.is_system:
            raise ValueError(f"预设标签 '{tag.name}' 不可删除")
        result = self._repo.delete_tag(tag_id)
        if result:
            self._db.commit()
        return result

    def set_entry_tags(self, entry_id: str, data: SetTagsRequest) -> list[TagSummary]:
        """设置日志的标签（全量替换）。

        Args:
            entry_id: 日志 ID。
            data: 标签 ID 列表。

        Returns:
            设置后的标签列表。

        Raises:
            ValueError: 日志不存在或标签数超过 10 个。
        """
        if len(data.tag_ids) > 10:
            raise ValueError("标签数量不能超过 10 个")

        entry = self._repo.find_entry_by_id(entry_id)
        if entry is None:
            raise ValueError(f"日志 {entry_id} 不存在")

        self._repo.set_entry_tags(entry_id, data.tag_ids)
        self._db.commit()

        tags = self._repo.find_tags_for_entry(entry_id)
        return [self._tag_model_to_schema(t) for t in tags]

    # =========================================================================
    # AI 分析（Phase 1 占位）
    # =========================================================================

    def trigger_ai_analysis(self, entry_id: str) -> dict:
        """触发 AI 分析（Phase 1 占位，LLM 集成在 Phase 2 实现）。

        Args:
            entry_id: 日志 ID。

        Returns:
            状态信息。

        Raises:
            ValueError: AI 功能未配置。
        """
        settings = get_settings()
        if not settings.llm_api_key:
            raise ValueError("AI 分析功能未启用，请在 .env 中配置 QUANT_ETF_LLM_API_KEY")
        raise ValueError("AI 分析功能将在后续版本中开放")

    def get_ai_analysis(self, entry_id: str) -> AIAnalysisResponse | None:
        """获取 AI 分析结果。

        Args:
            entry_id: 日志 ID。

        Returns:
            AI 分析结果，不存在返回 None。
        """
        analysis = self._repo.find_ai_analysis_by_entry(entry_id)
        if analysis is None:
            return None
        return AIAnalysisResponse(
            id=analysis.id,
            model=analysis.model,
            status=analysis.status,
            market_summary=analysis.market_summary,
            phase_judgment=analysis.phase_judgment,
            style_judgment=analysis.style_judgment,
            core_narrative=analysis.core_narrative,
            risk_alert=analysis.risk_alert,
            focus_direction=analysis.focus_direction,
            error_message=analysis.error_message,
            tokens_used=analysis.tokens_used,
            created_at=analysis.created_at,
        )

    # =========================================================================
    # 内部方法：自动填充
    # =========================================================================

    def _build_index_snapshots(self, trade_date: date) -> list[dict]:
        """为指定交易日构建所有活跃指数的行情与技术指标快照。

        从 index_daily_bar 获取收盘价和涨跌幅，
        从 index_factor_value 获取动量、波动率、均线、量比、回撤等因子值。

        Args:
            trade_date: 目标交易日。

        Returns:
            快照字典列表，可直接传给 repository 写入。
        """
        # 获取活跃指数列表
        indices = (
            self._db.query(BenchmarkIndexModel)
            .filter(BenchmarkIndexModel.is_active.is_(True))
            .order_by(BenchmarkIndexModel.index_code)
            .all()
        )

        index_codes = [idx.index_code for idx in indices]
        if not index_codes:
            return []

        # 批量查询日线数据
        bars = (
            self._db.query(IndexDailyBarModel)
            .filter(
                IndexDailyBarModel.trade_date == trade_date,
                IndexDailyBarModel.index_code.in_(index_codes),
            )
            .all()
        )
        bar_map = {b.index_code: b for b in bars}

        # 批量查询因子值（strategy_id IS NULL，即内置因子）
        factor_rows = (
            self._db.query(IndexFactorValueModel)
            .filter(
                IndexFactorValueModel.trade_date == trade_date,
                IndexFactorValueModel.index_code.in_(index_codes),
                IndexFactorValueModel.factor_id.in_(_SNAPSHOT_FACTOR_IDS),
                IndexFactorValueModel.strategy_id.is_(None),
            )
            .all()
        )
        # 组织为 {(index_code, factor_id): numeric}
        factor_map: dict[tuple[str, str], float] = {}
        for fr in factor_rows:
            if fr.factor_value_numeric is not None:
                factor_map[(fr.index_code, fr.factor_id)] = float(fr.factor_value_numeric)

        snapshots = []
        for idx in indices:
            bar = bar_map.get(idx.index_code)
            if bar is None:
                continue  # 无日线数据，跳过

            close_price = float(bar.close_price) if bar.close_price else None
            change_pct_val = float(bar.change_pct) if bar.change_pct is not None else None

            # 计算 MA 偏离率
            ma_20d_dev = self._calc_ma_deviation(
                close_price, factor_map.get((idx.index_code, "ma_20d"))
            )
            ma_60d_dev = self._calc_ma_deviation(
                close_price, factor_map.get((idx.index_code, "ma_60d"))
            )
            ma_120d_dev = self._calc_ma_deviation(
                close_price, factor_map.get((idx.index_code, "ma_120d"))
            )

            snapshots.append(
                {
                    "index_code": idx.index_code,
                    "index_name": idx.name_cn,
                    "index_category": "broad",
                    "sort_order": 0,
                    "close_price": close_price,
                    "change_pct": change_pct_val,
                    "volume_ratio_20d": factor_map.get((idx.index_code, "volume_ratio_20d")),
                    "return_5d": factor_map.get((idx.index_code, "return_5d")),
                    "return_20d": factor_map.get((idx.index_code, "return_20d")),
                    "return_60d": factor_map.get((idx.index_code, "return_60d")),
                    "return_120d": factor_map.get((idx.index_code, "return_120d")),
                    "ma_20d_deviation": ma_20d_dev,
                    "ma_60d_deviation": ma_60d_dev,
                    "ma_120d_deviation": ma_120d_dev,
                    "volatility_20d": factor_map.get((idx.index_code, "volatility_20d")),
                    "max_drawdown_60d": factor_map.get((idx.index_code, "max_drawdown_60d")),
                }
            )

        return snapshots

    @staticmethod
    def _calc_ma_deviation(close: float | None, ma_value: float | None) -> float | None:
        """计算收盘价偏离 MA 的百分比。

        Args:
            close: 收盘价。
            ma_value: 均线值。

        Returns:
            (close - ma) / ma * 100，任一为 None 返回 None。
        """
        if close is None or ma_value is None or ma_value == 0:
            return None
        return round((close - ma_value) / ma_value * 100, 4)

    # =========================================================================
    # 内部方法：Schema 组装
    # =========================================================================

    def _assemble_detail(self, entry) -> JournalEntryDetail:
        """将 ORM 模型组装为完整的 JournalEntryDetail schema。"""
        # 快照
        snapshot_rows = self._repo.find_snapshots_by_entry(entry.id)
        # 市场数据
        market_data = self._repo.find_market_data_by_entry(entry.id)
        # 观察分区
        obs_rows = self._repo.find_observations_by_entry(entry.id)
        # 标签
        tags = self._repo.find_tags_for_entry(entry.id)
        # AI 分析
        ai = self._repo.find_ai_analysis_by_entry(entry.id)

        return JournalEntryDetail(
            id=entry.id,
            trade_date=entry.trade_date,
            market_temperature=entry.market_temperature,
            profit_effect=entry.profit_effect,
            risk_preference=entry.risk_preference,
            trading_difficulty=entry.trading_difficulty,
            market_consistency=entry.market_consistency,
            market_phase=entry.market_phase,
            one_line_summary=entry.one_line_summary,
            is_complete=entry.is_complete,
            word_count=entry.word_count,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            index_snapshots=[self._snapshot_model_to_schema(s) for s in snapshot_rows],
            market_data=(
                JournalMarketData(
                    market_up_stocks=market_data.market_up_stocks,
                    market_down_stocks=market_data.market_down_stocks,
                    market_flat_stocks=market_data.market_flat_stocks,
                    limit_up_stocks=market_data.limit_up_stocks,
                    limit_down_stocks=market_data.limit_down_stocks,
                    total_turnover_yi=market_data.total_turnover_yi,
                    turnover_vs_prev_pct=market_data.turnover_vs_prev_pct,
                    north_bound_net_yi=market_data.north_bound_net_yi,
                    margin_balance_change_yi=market_data.margin_balance_change_yi,
                    size_style=market_data.size_style,
                    growth_style=market_data.growth_style,
                    sector_leading=market_data.sector_leading,
                    top_sectors=market_data.top_sectors,
                    bottom_sectors=market_data.bottom_sectors,
                    data_source=market_data.data_source,
                    notes=market_data.notes,
                )
                if market_data is not None
                else None
            ),
            observations=[self._obs_model_to_schema(o) for o in obs_rows],
            tags=[self._tag_model_to_schema(t) for t in tags],
            ai_analysis=(
                AIAnalysisResponse(
                    id=ai.id,
                    model=ai.model,
                    status=ai.status,
                    market_summary=ai.market_summary,
                    phase_judgment=ai.phase_judgment,
                    style_judgment=ai.style_judgment,
                    core_narrative=ai.core_narrative,
                    risk_alert=ai.risk_alert,
                    focus_direction=ai.focus_direction,
                    error_message=ai.error_message,
                    tokens_used=ai.tokens_used,
                    created_at=ai.created_at,
                )
                if ai is not None
                else None
            ),
        )

    def _assemble_summary(self, entry) -> JournalEntrySummary:
        """将 ORM 模型组装为 JournalEntrySummary schema。"""
        tags = self._repo.find_tags_for_entry(entry.id)
        return JournalEntrySummary(
            id=entry.id,
            trade_date=entry.trade_date,
            market_temperature=entry.market_temperature,
            profit_effect=entry.profit_effect,
            risk_preference=entry.risk_preference,
            trading_difficulty=entry.trading_difficulty,
            market_consistency=entry.market_consistency,
            market_phase=entry.market_phase,
            one_line_summary=entry.one_line_summary,
            is_complete=entry.is_complete,
            word_count=entry.word_count,
            tags=[self._tag_model_to_schema(t) for t in tags],
            created_at=entry.created_at,
            updated_at=entry.updated_at,
        )

    @staticmethod
    def _snapshot_model_to_schema(m) -> IndexSnapshotRow:
        return IndexSnapshotRow(
            id=m.id,
            index_code=m.index_code,
            index_name=m.index_name,
            index_category=m.index_category,
            sort_order=m.sort_order,
            close_price=m.close_price,
            change_pct=m.change_pct,
            volume_ratio_20d=m.volume_ratio_20d,
            return_5d=m.return_5d,
            return_20d=m.return_20d,
            return_60d=m.return_60d,
            return_120d=m.return_120d,
            ma_20d_deviation=m.ma_20d_deviation,
            ma_60d_deviation=m.ma_60d_deviation,
            ma_120d_deviation=m.ma_120d_deviation,
            volatility_20d=m.volatility_20d,
            max_drawdown_60d=m.max_drawdown_60d,
        )

    @staticmethod
    def _obs_model_to_schema(m) -> ObservationRow:
        return ObservationRow(
            id=m.id,
            section_key=m.section_key,
            section_label=m.section_label,
            content=m.content,
            sort_order=m.sort_order,
        )

    @staticmethod
    def _tag_model_to_schema(m) -> TagSummary:
        return TagSummary(
            id=m.id,
            name=m.name,
            color=m.color,
            description=m.description,
            is_system=m.is_system,
            usage_count=m.usage_count,
        )
