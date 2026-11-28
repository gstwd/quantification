"""每日量化研究简报的事实快照生成与不可变发布服务。"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from datetime import date, datetime, time, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from quant_etf_api.domain.research.daily_report import (
    SentimentObservation,
    calculate_sentiment,
    classify_anomaly,
    sentiment_state,
)
from quant_etf_api.infra.db.models.daily_report import MarketDailyReportModel
from quant_etf_api.infra.db.repositories.daily_report import DailyReportRepository
from quant_etf_api.infra.time import CHINA_TZ, now_cn, utcnow_aware
from quant_etf_api.schemas.daily_report import (
    DailyReportContent,
    DailyReportDetail,
    DailyReportFacts,
    DailyReportListResponse,
    DailyReportSnapshot,
    DailyReportSummary,
)
from quant_etf_api.services.data_management_service import DataManagementService

logger = logging.getLogger(__name__)

_CORE_INDEX_CODES = ("000300", "000905", "000852", "000688")
_THEME_LEADER_CODES = ("000688", "931160", "930713", "399673", "930601")
_THEME_LAG_CODES = ("H30269", "000015", "399998", "000932", "399965")
_ANOMALY_THEME_CODES = set(_THEME_LEADER_CODES + _THEME_LAG_CODES)
_BROAD_INDEX_NAMES = {"沪深300", "中证500", "中证1000指数", "上证50"}


class DailyReportBlockedError(ValueError):
    """日报事实不满足发布前置条件。"""


class DailyReportService:
    """编排日报事实快照、校验、发布和只读查询。"""

    def __init__(self, db: Session) -> None:
        """初始化服务。

        Args:
            db: SQLAlchemy 会话。
        """
        self._db = db
        self._repo = DailyReportRepository(db)

    def resolve_report_date(self, requested: date | None = None) -> date:
        """解析要生成日报的最近已完成交易日。

        Args:
            requested: 显式指定的交易日；为空时自动选择最近已完成交易日。

        Returns:
            可生成日报的交易日。

        Raises:
            DailyReportBlockedError: 日期非交易日、未来日期或没有可用行情时抛出。
        """
        today = now_cn()
        upper = requested or today.date()
        if upper > today.date():
            raise DailyReportBlockedError("不能为未来日期生成日报")
        latest = self._repo.latest_market_date(upper)
        if latest is None:
            raise DailyReportBlockedError("数据库中没有可用个股行情")
        if latest == today.date() and now_cn().time() < time(15, 30):
            prior = self._repo.trading_dates(latest, 2)
            if len(prior) < 2:
                raise DailyReportBlockedError("当日尚未收盘且没有上一交易日行情")
            latest = prior[-2]
        if requested is not None:
            if latest != requested:
                raise DailyReportBlockedError(f"{requested} 没有完整个股行情")
            if not self._repo.is_trading_date(requested):
                raise DailyReportBlockedError(f"{requested} 不是交易日")
        return latest

    def build_snapshot(self, requested: date | None = None) -> DailyReportSnapshot:
        """构建日报事实快照和供 skill 编辑的草稿骨架。

        Args:
            requested: 可选交易日。

        Returns:
            包含事实、哈希与草稿的只读快照。

        Raises:
            DailyReportBlockedError: 核心行情或交易日历不足时抛出。
        """
        report_date = self.resolve_report_date(requested)
        next_trade_date = self._repo.next_trading_date(report_date)
        if next_trade_date is None:
            raise DailyReportBlockedError("交易日历中缺少下一交易日")

        history_dates = self._repo.trading_dates(report_date, 272)
        if len(history_dates) < 21:
            raise DailyReportBlockedError("历史交易日不足 21 天，无法生成市场快照")
        timeline_dates = history_dates[-6:]
        stock_stats = self._repo.stock_market_daily(history_dates[0], report_date)
        if not stock_stats or stock_stats[-1]["trade_date"] != report_date:
            raise DailyReportBlockedError(f"{report_date} 缺少个股行情")
        limit_map = self._repo.limit_daily(history_dates[0], report_date)
        pcr_map = self._repo.pcr_daily(history_dates[0], report_date)
        index_rows = self._repo.index_daily(
            list(dict.fromkeys(_CORE_INDEX_CODES + _THEME_LEADER_CODES + _THEME_LAG_CODES)),
            timeline_dates[0],
            report_date,
        )
        index_by_code: dict[str, dict[date, dict[str, Any]]] = defaultdict(dict)
        for row in index_rows:
            index_by_code[str(row["index_code"])][row["trade_date"]] = row
        missing_indices = [
            code
            for code in _CORE_INDEX_CODES
            if report_date not in index_by_code.get(code, {})
        ]
        if missing_indices:
            raise DailyReportBlockedError(
                f"核心指数缺少 {report_date} 行情: {', '.join(missing_indices)}"
            )

        warnings = self._readiness_warnings(report_date)
        readiness_status = "partial" if warnings else "ready"
        observations = self._build_sentiment_observations(stock_stats, limit_map, pcr_map)
        sentiment_value = calculate_sentiment(observations, report_date)
        state, state_label = sentiment_state(sentiment_value.score)
        target_stats = stock_stats[-1]
        limit_up, limit_down = limit_map.get(report_date, (0, 0))
        market_snapshot = {
            "trade_date": report_date.isoformat(),
            "indices": [
                self._index_snapshot(code, index_by_code[code], timeline_dates)
                for code in _CORE_INDEX_CODES
            ],
            "turnover_billion": round(float(target_stats["turnover_billion"] or 0), 2),
            "turnover_vs_20d_pct": self._activity_for_date(
                stock_stats, report_date, "turnover_billion"
            ),
            "main_net_inflow_billion": self._main_net(report_date),
            "breadth": {
                "total": int(target_stats["total"]),
                "advances": int(target_stats["advances"]),
                "unchanged": int(target_stats["unchanged"]),
                "declines": int(target_stats["declines"]),
                "advance_ratio_pct": round(
                    int(target_stats["advances"]) / int(target_stats["total"]) * 100, 2
                ),
                "median_change_pct": round(float(target_stats["median_change_pct"] or 0), 3),
                "limit_up": limit_up,
                "limit_down": limit_down,
            },
        }
        timeline = [
            self._timeline_point(
                point,
                index_by_code,
                limit_map,
                pcr_map,
                observations,
            )
            for point in stock_stats
            if point["trade_date"] in timeline_dates
        ]
        market_signals = self._market_signals(report_date)
        if len(market_signals) < 2:
            warnings.append(f"{report_date} 市场指标不足，资金与期权信号不完整")
        structure = self._structure(report_date, index_by_code)
        if structure.get("warning"):
            warnings.append(str(structure["warning"]))
        if warnings:
            readiness_status = "partial"
        stock_anomalies = self._stock_anomalies(report_date, timeline_dates, index_by_code)
        index_anomalies = self._index_anomalies(report_date, index_by_code)
        macro_items = self._macro_items()
        facts_payload = {
            "report_date": report_date.isoformat(),
            "next_trade_date": next_trade_date.isoformat(),
            "data_cutoff": f"{report_date.isoformat()} 收盘",
            "readiness_status": readiness_status,
            "market_snapshot": market_snapshot,
            "timeline": timeline,
            "sentiment": {
                "score": sentiment_value.score,
                "state": state,
                "state_label": state_label,
                "summary": self._sentiment_summary(sentiment_value),
                "components": self._sentiment_components(sentiment_value),
            },
            "market_signals": market_signals,
            "stock_anomalies": stock_anomalies,
            "index_anomalies": index_anomalies,
            "structure_basis": structure["basis"],
            "industry_leaders": structure["leaders"],
            "industry_laggards": structure["laggards"],
            "style_pairs": self._style_pairs(report_date, index_by_code),
            "macro_items": macro_items,
            "warnings": list(dict.fromkeys(warnings)),
        }
        facts = DailyReportFacts.model_validate(facts_payload)
        facts_json = facts.model_dump(mode="json")
        fact_hash = _sha256_json(facts_json)
        draft = {
            **facts_json,
            "schema_version": "daily_report_v1",
            "title": "待填写标题",
            "subtitle": "待填写副标题",
            "generated_at": utcnow_aware().isoformat(),
            "executive_summary": "根据事实快照与可核验新闻填写当日结论。",
            "market_regime": state_label,
            "news_items": [],
            "claim_checks": [],
            "interpretation": [],
            "risks": [],
            "watch_items": [],
            "calendar_items": [],
        }
        return DailyReportSnapshot(
            fact_snapshot_hash=fact_hash,
            facts=facts,
            draft=draft,
        )

    def validate_content(self, content: DailyReportContent) -> DailyReportFacts:
        """重新生成事实并确认内容中的事实块未被改写。

        Args:
            content: 待发布日报。

        Returns:
            与内容一致的事实块。

        Raises:
            DailyReportBlockedError: 事实过期、不可发布或直接冲突时抛出。
        """
        expected = self.build_snapshot(content.report_date).facts
        content_payload = content.model_dump(mode="json")
        provided = DailyReportFacts.model_validate(
            {key: content_payload[key] for key in DailyReportFacts.model_fields}
        )
        if expected.model_dump(mode="json") != provided.model_dump(mode="json"):
            raise DailyReportBlockedError(
                "日报事实块与当前数据库快照不一致，请重新运行 daily-report snapshot"
            )
        return expected

    def publish(self, content: DailyReportContent) -> DailyReportDetail:
        """直接发布校验通过的日报，并保留同日历史修订。

        Args:
            content: 完整日报内容。

        Returns:
            已发布日报详情。

        Raises:
            DailyReportBlockedError: 内容不可发布时抛出。
        """
        facts = self.validate_content(content)
        facts_json = facts.model_dump(mode="json")
        content_json = content.model_dump(mode="json")
        content_hash = _sha256_json(content_json)
        existing = self._repo.latest_published(content.report_date)
        if existing is not None and existing.content_hash == content_hash:
            return self._to_detail(existing)

        revision = self._repo.next_revision(content.report_date)
        self._repo.supersede_published(content.report_date)
        row = MarketDailyReportModel(
            report_id=str(uuid4()),
            report_date=content.report_date,
            revision_no=revision,
            status="published",
            title=content.title,
            market_regime=content.market_regime,
            readiness_status=content.readiness_status,
            generator_version="market-daily-report/v1",
            payload_version=content.schema_version,
            generated_at=content.generated_at,
            data_cutoff_at=_close_cutoff_utc(content.report_date),
            content_json=content_json,
            fact_snapshot_json=facts_json,
            fact_snapshot_hash=_sha256_json(facts_json),
            content_hash=content_hash,
        )
        self._repo.add(row)
        try:
            self._repo.flush()
            self._db.commit()
        except Exception:
            self._repo.rollback()
            logger.exception("发布日报失败: report_date=%s", content.report_date)
            raise
        return self._to_detail(row)

    def list_reports(self, limit: int = 30) -> DailyReportListResponse:
        """返回按日期倒序的已发布日报摘要。"""
        return DailyReportListResponse(
            items=[self._to_summary(row) for row in self._repo.list_reports(limit)]
        )

    def get_report(
        self, report_date: date, revision_no: int | None = None
    ) -> DailyReportDetail | None:
        """读取指定交易日的最新发布版本或历史修订。"""
        row = self._repo.latest_published(report_date, revision_no)
        return self._to_detail(row) if row is not None else None

    def list_reports_cli(self, limit: int = 30) -> list[dict[str, Any]]:
        """返回 CLI 可直接序列化的摘要列表。"""
        return [item.model_dump(mode="json") for item in self.list_reports(limit).items]

    def get_report_cli(
        self, report_date: date, revision_no: int | None = None
    ) -> dict[str, Any] | None:
        """返回 CLI 可直接序列化的日报详情。"""
        report = self.get_report(report_date, revision_no)
        return report.model_dump(mode="json") if report is not None else None

    def _readiness_warnings(self, report_date: date) -> list[str]:
        """依据统一健康快照和核心日期覆盖生成降级提示。"""
        overview = DataManagementService(self._db).overview()
        relevant = {
            "trading_calendar",
            "index_daily_bar",
            "stock_daily_close",
            "stock_daily_basic",
            "stock_moneyflow",
            "market_indicator",
            "industry_daily_bar",
            "macro_indicator",
        }
        warnings: list[str] = []
        by_key = {item.dataset_key: item for item in overview.datasets}
        for key in relevant:
            item = by_key.get(key)
            if item is None:
                warnings.append(f"数据健康快照缺少 {key}")
                continue
            if item.health_status == "error":
                warnings.append(f"{item.display_name}健康状态为 error")
            if key in {"index_daily_bar", "stock_daily_close"} and (
                item.latest_date is None or item.latest_date < report_date
            ):
                raise DailyReportBlockedError(f"{item.display_name}未覆盖 {report_date}")
        macro = by_key.get("macro_indicator")
        if macro and macro.health_status not in {"healthy", "warning"}:
            warnings.append("宏观数据不是最近发布状态，报告中仅作背景")
        return list(dict.fromkeys(warnings))

    def _build_sentiment_observations(
        self,
        stats: list[dict[str, Any]],
        limit_map: dict[date, tuple[int, int]],
        pcr_map: dict[date, float],
    ) -> list[SentimentObservation]:
        """把日频原始指标转换成情绪分位口径。"""
        amount_values = [float(row["turnover_billion"] or 0) for row in stats]
        observations: list[SentimentObservation] = []
        for index, row in enumerate(stats):
            day = row["trade_date"]
            if day not in pcr_map or index < 20:
                continue
            prior = amount_values[max(0, index - 20) : index]
            prior_avg = sum(prior) / len(prior) if prior else 0.0
            activity = (amount_values[index] / prior_avg - 1) * 100 if prior_avg else 0.0
            total = int(row["total"] or 0)
            breadth = int(row["advances"] or 0) / total * 100 if total else 0.0
            limit_up, limit_down = limit_map.get(day, (0, 0))
            observations.append(
                SentimentObservation(
                    trade_date=day,
                    breadth_pct=breadth,
                    median_change_pct=float(row["median_change_pct"] or 0),
                    limit_spread_pct=(limit_up - limit_down) / total * 100 if total else 0.0,
                    activity_pct=activity,
                    put_call_ratio=pcr_map[day],
                )
            )
        return observations

    def _activity_for_date(
        self, stats: list[dict[str, Any]], target: date, field: str
    ) -> float:
        """计算目标日相对此前 20 日均值的变化。"""
        index = next((i for i, row in enumerate(stats) if row["trade_date"] == target), -1)
        if index < 20:
            return 0.0
        prior = [float(row[field] or 0) for row in stats[index - 20 : index]]
        avg = sum(prior) / len(prior)
        return round((float(stats[index][field] or 0) / avg - 1) * 100, 2) if avg else 0.0

    def _main_net(self, report_date: date) -> float:
        """读取目标日主力净流入。"""
        rows = self._repo.moneyflow_daily(report_date, report_date)
        return round(float(rows[0]["main_net_billion"]), 2) if rows else 0.0

    def _index_snapshot(
        self,
        code: str,
        rows: dict[date, dict[str, Any]],
        dates: list[date],
    ) -> dict[str, Any]:
        """构造一个指数快照与五日走势。"""
        current = rows[dates[-1]]
        names = self._repo.index_names([code])
        return {
            "code": code,
            "name": names.get(code, code),
            "close": round(float(current["close_price"]), 2),
            "change_pct": round(float(current["change_pct"]), 2),
            "trend": [
                round(float(rows[day]["change_pct"]), 2)
                for day in dates
                if day in rows and rows[day].get("change_pct") is not None
            ],
        }

    def _timeline_point(
        self,
        row: dict[str, Any],
        index_by_code: dict[str, dict[date, dict[str, Any]]],
        limit_map: dict[date, tuple[int, int]],
        pcr_map: dict[date, float],
        observations: list[SentimentObservation],
    ) -> dict[str, Any]:
        """构造连续市场序列中的单日点。"""
        day = row["trade_date"]
        total = int(row["total"] or 0)
        source = next((item for item in observations if item.trade_date == day), None)
        sentiment = (
            calculate_sentiment(observations, day).score if source is not None else 0.0
        )
        return {
            "trade_date": day.isoformat(),
            "label": day.strftime("%m/%d"),
            "advance_ratio_pct": round(int(row["advances"] or 0) / total * 100, 2)
            if total
            else 0.0,
            "turnover_billion": round(float(row["turnover_billion"] or 0), 2),
            "csi300_pct": round(
                float(index_by_code["000300"][day]["change_pct"]), 2
            ),
            "csi1000_pct": round(
                float(index_by_code["000852"][day]["change_pct"]), 2
            ),
            "sentiment_score": sentiment,
        }

    def _market_signals(self, report_date: date) -> list[dict[str, Any]]:
        """根据目标日可用数据构造资金与情绪指标。"""
        signals: list[dict[str, Any]] = []
        flows = self._repo.moneyflow_daily(report_date, report_date)
        if flows:
            value = float(flows[0]["main_net_billion"])
            signals.append(
                {
                    "key": "main-flow",
                    "label": "主力净流入",
                    "value": f"{value:+.2f} 亿元",
                    "delta": "按大单与特大单净额统计",
                    "note": "反映当日主动大额资金方向。",
                    "tone": "positive" if value >= 0 else "negative",
                    "impact": "bullish" if value >= 0 else "bearish",
                    "impact_strength": 2,
                    "affected_sectors": ["A股整体", "高弹性成长"],
                }
            )
        indicators = self._repo.market_indicators_on(report_date)
        pcr = indicators.get("opt_io_pcr_vol")
        if pcr:
            signals.append(
                {
                    "key": "io-pcr",
                    "label": "300 期权 PCR",
                    "value": f"{float(pcr['value']):.3f}",
                    "delta": "看跌/看涨成交量比",
                    "note": "PCR 越低，看涨交易相对越活跃。",
                    "tone": "positive",
                    "impact": "bullish",
                    "impact_strength": 2,
                    "affected_sectors": ["指数期权", "大盘成长"],
                }
            )
        basis = indicators.get("fut_if_basis")
        if basis:
            value = float(basis["value"])
            signals.append(
                {
                    "key": "if-basis",
                    "label": "IF 基差",
                    "value": f"{value:+.2f} 点",
                    "delta": "股指期货相对现货",
                    "note": "升水通常表示期货风险偏好更强。",
                    "tone": "positive" if value >= 0 else "negative",
                    "impact": "bullish" if value >= 0 else "bearish",
                    "impact_strength": 2,
                    "affected_sectors": ["权重蓝筹", "股指期货"],
                }
            )
        margin = indicators.get("margin_rzye")
        if margin:
            value = float(margin["value"])
            signals.append(
                {
                    "key": "margin",
                    "label": "融资余额",
                    "value": f"{value:,.2f} 亿元",
                    "delta": "交易所两融合计",
                    "note": "反映杠杆资金存量。",
                    "tone": "neutral",
                    "impact": "neutral",
                    "impact_strength": 1,
                    "affected_sectors": ["杠杆资金敏感型"],
                }
            )
        return signals[:4]

    def _stock_anomalies(
        self,
        report_date: date,
        dates: list[date],
        index_by_code: dict[str, dict[date, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """筛选个股单日、多日及相对行业异动。"""
        rows = self._repo.stock_rows(dates[0], report_date)
        by_stock: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_stock[str(row["stock_code"])].append(row)
        basics = self._repo.stock_basic_on(report_date)
        names = self._repo.stock_names()
        industries = self._repo.latest_industry_by_stock(report_date)
        theme_map: dict[str, list[str]] = defaultdict(list)
        for item in self._repo.latest_index_memberships(report_date):
            if item["index_code"] in _ANOMALY_THEME_CODES and item["name"] not in _BROAD_INDEX_NAMES:
                theme_map[item["stock_code"]].append(str(item["name"]))
        industry_returns = self._target_industry_returns(report_date)
        theme_names = self._repo.index_names(list(_ANOMALY_THEME_CODES))
        theme_returns = {
            theme_names.get(code, code): float(index_by_code[code][report_date]["change_pct"])
            for code in _ANOMALY_THEME_CODES
            if report_date in index_by_code.get(code, {})
        }
        candidates: list[dict[str, Any]] = []
        for code, bars in by_stock.items():
            if code not in basics or names.get(code, "").startswith(("ST", "*ST")):
                continue
            ordered = sorted(bars, key=lambda item: item["trade_date"])
            current = next(
                (item for item in ordered if item["trade_date"] == report_date), None
            )
            if current is None or len(ordered) < 6:
                continue
            base = ordered[-6]
            amount = float(current["amount"] or 0)
            if amount < 50_000:
                continue
            pct_1d = float(current["pct_chg"] or 0)
            current_adj = float(current["close"] or 0) * float(current.get("adj_factor") or 1)
            base_adj = float(base["close"] or 0) * float(base.get("adj_factor") or 1)
            if base_adj <= 0:
                continue
            pct_5d = (current_adj / base_adj - 1) * 100
            industry_code, industry_name = industries.get(code, (None, None))
            themes = sorted(set(theme_map.get(code, [])))[:4]
            reference_name = industry_name or "沪深300"
            reference_pct = float(index_by_code["000300"][report_date]["change_pct"])
            if industry_code and industry_returns.get(industry_code) is not None:
                reference_pct = industry_returns[industry_code]
            elif themes:
                matched_theme = next(
                    (theme for theme in themes if theme in theme_returns), None
                )
                if matched_theme is not None:
                    reference_name = matched_theme
                    reference_pct = theme_returns[matched_theme]
            elif report_date in index_by_code.get("000852", {}):
                reference_name = "中证1000"
                reference_pct = float(index_by_code["000852"][report_date]["change_pct"])
            relative = pct_1d - reference_pct
            label = classify_anomaly(pct_1d, pct_5d, relative)
            candidates.append(
                {
                    "code": code,
                    "name": names.get(code, code),
                    "asset_type": "stock",
                    "industry": industry_name,
                    "themes": themes,
                    "reference_name": reference_name,
                    "reference_change_pct": round(reference_pct, 2),
                    "relative_pct": round(relative, 2),
                    "change_1d_pct": round(pct_1d, 2),
                    "change_5d_pct": round(pct_5d, 2),
                    "volume_ratio": round(float(basics[code]["volume_ratio"] or 0), 2),
                    "turnover_rate_pct": round(
                        float(basics[code]["turnover_rate"] or 0), 2
                    ),
                    "anomaly_label": label,
                    "analysis": (
                        f"{label}：单日 {pct_1d:+.2f}%，五日 {pct_5d:+.2f}%，"
                        f"相对{reference_name} {relative:+.2f}pp。"
                        f"所属行业为{industry_name or '未映射'}，"
                        f"主题为{'、'.join(themes) if themes else '未覆盖'}。"
                    ),
                }
            )
        candidates.sort(
            key=lambda item: max(
                abs(item["change_1d_pct"]),
                abs(item["change_5d_pct"]) / 2,
            )
            + abs(item["relative_pct"]) / 2,
            reverse=True,
        )
        return candidates[:6]

    def _target_industry_returns(self, report_date: date) -> dict[str, float]:
        """读取目标日申万行业涨跌幅。"""
        rows = self._repo.industry_daily(report_date, report_date)
        return {
            str(row["industry_code"]): float(row["change_pct"] or 0)
            for row in rows
        }

    def _industry_moves(
        self, report_date: date, reverse: bool
    ) -> list[dict[str, Any]]:
        """读取并排序目标日行业表现。"""
        rows = self._repo.industry_daily(report_date, report_date)
        rows.sort(key=lambda item: float(item["change_pct"] or 0), reverse=reverse)
        selected = rows[:5]
        start = self._repo.trading_dates(report_date, 6)[0]
        history = self._repo.industry_daily(start, report_date)
        history_map: dict[str, dict[date, float]] = defaultdict(dict)
        for row in history:
            history_map[str(row["industry_code"])][row["trade_date"]] = float(
                row["close_price"] or 0
            )
        result = []
        for row in selected:
            closes = history_map[str(row["industry_code"])]
            dates = sorted(closes)
            five_day = (
                (closes[dates[-1]] / closes[dates[0]] - 1) * 100 if len(dates) >= 6 else 0.0
            )
            result.append(
                {
                    "code": str(row["industry_code"]),
                    "name": str(row["name"]),
                    "change_pct": round(float(row["change_pct"] or 0), 2),
                    "five_day_change_pct": round(five_day, 2),
                    "basis": "industry",
                }
            )
        return result

    def _theme_moves(
        self,
        report_date: date,
        codes: tuple[str, ...],
        index_by_code: dict[str, dict[date, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """读取固定主题指数作为行业数据滞后时的替代口径。"""
        names = self._repo.index_names(list(codes))
        result = []
        for code in codes:
            rows = index_by_code.get(code, {})
            if report_date not in rows or len(rows) < 6:
                continue
            dates = sorted(rows)[-6:]
            current = rows[report_date]
            five_day = (
                (float(current["close_price"]) / float(rows[dates[0]]["close_price"]) - 1)
                * 100
            )
            result.append(
                {
                    "code": code,
                    "name": names.get(code, code),
                    "change_pct": round(float(current["change_pct"]), 2),
                    "five_day_change_pct": round(five_day, 2),
                    "basis": "theme_index",
                }
            )
        return result

    def _structure(
        self,
        report_date: date,
        index_by_code: dict[str, dict[date, dict[str, Any]]],
    ) -> dict[str, Any]:
        """根据行业是否覆盖目标日决定行业或主题结构口径。"""
        rows = self._repo.industry_daily(report_date, report_date)
        if rows:
            return {
                "basis": f"申万一级行业 {report_date} 收盘",
                "leaders": self._industry_moves(report_date, True),
                "laggards": self._industry_moves(report_date, False),
                "warning": None,
            }
        return {
            "basis": "当日主题指数；申万行业日线尚未覆盖报告日",
            "leaders": self._theme_moves(report_date, _THEME_LEADER_CODES, index_by_code),
            "laggards": self._theme_moves(report_date, _THEME_LAG_CODES, index_by_code),
            "warning": "申万行业日线未覆盖报告日，行业结构改用同日主题指数",
        }

    def _style_pairs(
        self,
        report_date: date,
        index_by_code: dict[str, dict[date, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """计算规模、成长和科技相对红利的同日收益差。"""

        def change(code: str) -> float:
            return round(float(index_by_code[code][report_date]["change_pct"]), 2)

        pairs = [
            ("size", "大盘", "小盘", "000300", "000852"),
            ("growth", "红利", "创业板50", "000015", "399673"),
            ("technology", "红利", "科创50", "000015", "000688"),
        ]
        result = []
        for key, left, right, left_code, right_code in pairs:
            left_value = change(left_code)
            right_value = change(right_code)
            result.append(
                {
                    "key": key,
                    "left_label": left,
                    "right_label": right,
                    "left_return_pct": left_value,
                    "right_return_pct": right_value,
                    "spread_pct": round(abs(right_value - left_value), 2),
                    "leader": "right" if right_value >= left_value else "left",
                }
            )
        return result

    def _index_anomalies(
        self,
        report_date: date,
        index_by_code: dict[str, dict[date, dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """生成最多三个指数异动。"""
        names = self._repo.index_names(["000688", "931160", "H30269"])
        reference = float(index_by_code["000300"][report_date]["change_pct"])
        candidates = []
        for code in ("000688", "931160", "H30269"):
            rows = index_by_code.get(code, {})
            if report_date not in rows or len(rows) < 6:
                continue
            dates = sorted(rows)[-6:]
            current = rows[report_date]
            day = float(current["change_pct"])
            five = (float(current["close_price"]) / float(rows[dates[0]]["close_price"]) - 1) * 100
            relative = day - reference
            label = classify_anomaly(day, five, relative)
            candidates.append(
                {
                    "code": code,
                    "name": names.get(code, code),
                    "asset_type": "index",
                    "industry": None,
                    "themes": [],
                    "reference_name": "沪深300",
                    "reference_change_pct": round(reference, 2),
                    "relative_pct": round(relative, 2),
                    "change_1d_pct": round(day, 2),
                    "change_5d_pct": round(five, 2),
                    "volume_ratio": None,
                    "turnover_rate_pct": None,
                    "anomaly_label": label,
                    "analysis": (
                        f"{label}：单日 {day:+.2f}%，五日 {five:+.2f}%，"
                        f"相对沪深300 {relative:+.2f}pp。"
                    ),
                }
            )
        candidates.sort(key=lambda item: abs(item["change_1d_pct"]), reverse=True)
        return candidates[:3]

    def _macro_items(self) -> list[dict[str, Any]]:
        """生成最近发布期的四个宏观背景指标。"""
        definitions = {
            "pmi": ("制造业 PMI", "neutral"),
            "cpi": ("CPI 同比", "neutral"),
            "m1_yoy": ("M1 同比", "positive"),
            "m2_yoy": ("M2 同比", "neutral"),
        }
        rows = self._repo.macro_rows()
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            if row.get("indicator_code") in definitions:
                grouped[str(row["indicator_code"])].append(row)
        result = []
        for code, (label, tone) in definitions.items():
            values = sorted(
                grouped.get(code, []),
                key=lambda item: item.get("period_date")
                or date.fromisoformat(f"{item['period']}-01"),
            )
            if not values:
                continue
            latest = values[-1]
            previous = values[-2] if len(values) > 1 else latest
            delta = float(latest["value"]) - float(previous["value"])
            result.append(
                {
                    "key": code,
                    "label": label,
                    "value": f"{float(latest['value']):.1f}{latest.get('unit') or ''}",
                    "previous_value": f"{float(previous['value']):.1f}{previous.get('unit') or ''}",
                    "delta": f"{delta:+.1f}",
                    "period": str(latest.get("period") or ""),
                    "tone": tone,
                    "impact": "bullish" if delta > 0 else "bearish" if delta < 0 else "neutral",
                    "impact_strength": 1,
                    "affected_sectors": ["内需顺周期", "金融"],
                }
            )
        return result

    def _sentiment_summary(self, result: Any) -> str:
        """生成情绪温度的事实性摘要。"""
        _, label = sentiment_state(result.score)
        return (
            f"情绪温度 {result.score:.1f}，处于{label}区间。"
            f"上涨广度分位 {result.breadth_percentile:.1f}，"
            f"中位涨跌分位 {result.median_percentile:.1f}，"
            f"成交活跃度分位 {result.activity_percentile:.1f}。"
        )

    def _sentiment_components(self, result: Any) -> list[dict[str, Any]]:
        """生成五项情绪分项。"""
        return [
            {
                "key": "breadth",
                "label": "上涨家数占比",
                "display_value": f"{result.breadth_percentile:.1f} 分位",
                "percentile": result.breadth_percentile,
                "weight_pct": 30,
                "note": "衡量上涨个股覆盖范围。",
            },
            {
                "key": "median",
                "label": "涨跌中位数",
                "display_value": f"{result.median_percentile:.1f} 分位",
                "percentile": result.median_percentile,
                "weight_pct": 25,
                "note": "衡量典型个股收益。",
            },
            {
                "key": "limit",
                "label": "涨停扩散度",
                "display_value": f"{result.limit_percentile:.1f} 分位",
                "percentile": result.limit_percentile,
                "weight_pct": 20,
                "note": "衡量局部赚钱效应。",
            },
            {
                "key": "activity",
                "label": "成交活跃度",
                "display_value": f"{result.activity_percentile:.1f} 分位",
                "percentile": result.activity_percentile,
                "weight_pct": 15,
                "note": "衡量成交相对近期均值。",
            },
            {
                "key": "pcr",
                "label": "300 期权 PCR",
                "display_value": f"{result.pcr_inverse_percentile:.1f} 分位",
                "percentile": result.pcr_inverse_percentile,
                "weight_pct": 10,
                "note": "PCR 越低对应越高风险偏好。",
            },
        ]

    def _to_summary(self, row: MarketDailyReportModel) -> DailyReportSummary:
        """ORM 行转列表摘要。"""
        return DailyReportSummary(
            report_id=row.report_id,
            report_date=row.report_date,
            revision_no=row.revision_no,
            status=row.status,
            title=row.title,
            market_regime=row.market_regime,
            readiness_status=row.readiness_status,
            published_at=row.published_at,
        )

    def _to_detail(self, row: MarketDailyReportModel) -> DailyReportDetail:
        """ORM 行转详情并重新通过 Pydantic 校验。"""
        content = DailyReportContent.model_validate(row.content_json)
        return DailyReportDetail(
            **self._to_summary(row).model_dump(),
            content=content,
        )


def _sha256_json(payload: dict[str, Any]) -> str:
    """计算稳定 JSON 哈希。"""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _close_cutoff_utc(report_date: date) -> datetime:
    """将 A 股收盘业务时点转换为 UTC。"""
    local = datetime.combine(report_date, time(15, 0), tzinfo=CHINA_TZ)
    return local.astimezone(timezone.utc)
