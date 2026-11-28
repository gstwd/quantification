"""每日量化研究简报的内容契约、纯计算与发布逻辑测试。"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from quant_etf_api.domain.research.campaign import check_command_allowed
from quant_etf_api.domain.research.daily_report import (
    SentimentObservation,
    calculate_sentiment,
    classify_anomaly,
    sentiment_state,
)
from quant_etf_api.infra.db.models.daily_report import MarketDailyReportModel
from quant_etf_api.main import app
from quant_etf_api.schemas.daily_report import DailyReportContent, DailyReportFacts
from quant_etf_api.services.daily_report_service import DailyReportService


def _content_payload() -> dict[str, Any]:
    """构造一份最小可发布的完整日报。"""
    return {
        "schema_version": "daily_report_v1",
        "report_date": "2026-11-27",
        "next_trade_date": "2026-11-30",
        "data_cutoff": "2026-11-27 收盘",
        "readiness_status": "partial",
        "title": "放量扩散",
        "subtitle": "测试副标题",
        "generated_at": "2026-11-28T10:00:00+00:00",
        "executive_summary": "测试摘要",
        "market_regime": "放量扩散",
        "market_snapshot": {
            "trade_date": "2026-11-27",
            "indices": [
                {
                    "code": "000300",
                    "name": "沪深300",
                    "close": 4000.0,
                    "change_pct": 1.0,
                    "trend": [0.1, 0.2, 0.3, 0.4, 1.0],
                }
            ],
            "turnover_billion": 20000.0,
            "turnover_vs_20d_pct": 20.0,
            "main_net_inflow_billion": 100.0,
            "breadth": {
                "total": 5000,
                "advances": 4000,
                "unchanged": 100,
                "declines": 900,
                "advance_ratio_pct": 80.0,
                "median_change_pct": 1.2,
                "limit_up": 100,
                "limit_down": 2,
            },
        },
        "timeline": [
            {
                "trade_date": "2026-11-27",
                "label": "11/27",
                "advance_ratio_pct": 80.0,
                "turnover_billion": 20000.0,
                "csi300_pct": 1.0,
                "csi1000_pct": 2.0,
                "sentiment_score": 88.0,
            }
        ],
        "sentiment": {
            "score": 88.0,
            "state": "boiling",
            "state_label": "沸点",
            "summary": "测试情绪",
            "components": [
                {
                    "key": "breadth",
                    "label": "上涨家数占比",
                    "display_value": "80%",
                    "percentile": 90.0,
                    "weight_pct": 100,
                    "note": "测试",
                }
            ],
        },
        "market_signals": [],
        "stock_anomalies": [],
        "index_anomalies": [],
        "watch_items": [
            {
                "key": "breadth",
                "label": "广度",
                "current_state": "80%",
                "confirm_condition": "高于 55%",
                "invalidate_condition": "低于 45%",
                "affected_sectors": ["A股整体"],
            }
        ],
        "calendar_items": [],
        "structure_basis": "申万一级行业",
        "industry_leaders": [],
        "industry_laggards": [],
        "style_pairs": [],
        "macro_items": [],
        "news_items": [
            {
                "id": "n1",
                "category": "政策",
                "title": "测试新闻",
                "publisher": "官方",
                "source_tier": "official",
                "published_at": "2026-11-27",
                "url": "https://example.com/news",
                "verification": "verified",
                "verification_note": "已核验",
                "excerpt": "测试",
                "impact": "bullish",
                "impact_strength": 2,
                "impact_horizon": "immediate",
                "affected_sectors": ["A股整体"],
            }
        ],
        "claim_checks": [],
        "interpretation": [],
        "risks": [],
        "warnings": [],
    }


class TestDailyReportSchema:
    """验证日报内容契约。"""

    def test_valid_content(self) -> None:
        """完整必填字段可通过校验。"""
        content = DailyReportContent.model_validate(_content_payload())
        assert content.report_date == date(2026, 11, 27)
        assert content.news_items[0].impact_horizon == "immediate"

    def test_rejects_conflicted_claim(self) -> None:
        """直接冲突的结论不得发布。"""
        payload = _content_payload()
        payload["claim_checks"] = [
            {
                "claim": "冲突",
                "status": "conflicted",
                "data_evidence": "数据",
                "news_evidence": "新闻",
                "assessment": "冲突",
            }
        ]
        with pytest.raises(ValidationError, match="conflicted"):
            DailyReportContent.model_validate(payload)

    def test_rejects_invalid_news_url(self) -> None:
        """新闻必须包含有效 http/https 地址。"""
        payload = _content_payload()
        payload["news_items"][0]["url"] = "not-a-url"
        with pytest.raises(ValidationError, match="http/https"):
            DailyReportContent.model_validate(payload)

    def test_readonly_routes_registered(self) -> None:
        """日报只暴露列表与详情 GET 路由。"""
        paths = app.openapi()["paths"]
        assert set(paths["/api/daily-reports"]) == {"get"}
        assert set(paths["/api/daily-reports/{report_date}"]) == {"get"}


class TestDailyReportCalculations:
    """验证情绪与异动纯计算。"""

    def test_sentiment_score_and_state(self) -> None:
        """五因子分位能稳定得到沸点。"""
        observations = [
            SentimentObservation(date(2026, 11, 20 + i), i, i - 2, i / 10, i, 1.0 - i / 10)
            for i in range(1, 11)
        ]
        result = calculate_sentiment(observations, date(2026, 11, 30))
        assert result.score > 85
        assert sentiment_state(result.score) == ("boiling", "沸点")

    @pytest.mark.parametrize(
        ("one_day", "five_day", "relative", "expected"),
        [
            (20, 32, 10, "五日加速"),
            (20, -6, 10, "反弹未修复"),
            (-9, -22, -5, "弱势延续"),
            (2, 22, 1, "多日趋势"),
        ],
    )
    def test_anomaly_labels(
        self, one_day: float, five_day: float, relative: float, expected: str
    ) -> None:
        """异动标签按规则分类。"""
        assert classify_anomaly(one_day, five_day, relative) == expected

    def test_campaign_mode_rejects_daily_report(self) -> None:
        """研究战役不能生成或发布日报。"""
        assert check_command_allowed("daily-report", "publish", {}) is not None


class TestDailyReportPublish:
    """验证直接发布与幂等语义。"""

    def test_same_content_is_idempotent(self) -> None:
        """相同内容直接返回现有版本。"""
        content = DailyReportContent.model_validate(_content_payload())
        db = MagicMock()
        service = DailyReportService(db)
        service.validate_content = MagicMock(return_value=DailyReportFacts.model_validate(
            {key: content.model_dump(mode="json")[key] for key in DailyReportFacts.model_fields}
        ))
        existing = MarketDailyReportModel(
            report_id="r1",
            report_date=content.report_date,
            revision_no=1,
            status="published",
            title=content.title,
            market_regime=content.market_regime,
            readiness_status=content.readiness_status,
            generator_version="test",
            payload_version=content.schema_version,
            generated_at=content.generated_at,
            data_cutoff_at=content.generated_at,
            published_at=content.generated_at,
            content_json=content.model_dump(mode="json"),
            fact_snapshot_json={},
            fact_snapshot_hash="f",
            content_hash=service._to_detail(
                MarketDailyReportModel(
                    report_id="tmp",
                    report_date=content.report_date,
                    revision_no=1,
                    status="published",
                    title=content.title,
                    market_regime=content.market_regime,
                    readiness_status=content.readiness_status,
                    generator_version="test",
                    payload_version=content.schema_version,
                    generated_at=content.generated_at,
                    data_cutoff_at=content.generated_at,
                    published_at=content.generated_at,
                    content_json=content.model_dump(mode="json"),
                    fact_snapshot_json={},
                    fact_snapshot_hash="f",
                    content_hash="x",
                )
            ).content.model_dump_json(),
        )
        from quant_etf_api.services.daily_report_service import _sha256_json

        existing.content_hash = _sha256_json(content.model_dump(mode="json"))
        service._repo = MagicMock()
        service._repo.latest_published.return_value = existing
        result = service.publish(content)
        assert result.report_id == "r1"
        service._repo.add.assert_not_called()
