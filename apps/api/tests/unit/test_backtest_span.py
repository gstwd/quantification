"""回测跨度限制已取消（C4）的回归测试。

规则变更：不再要求"总跨度 > 5 年必须分段"——研究期内任意跨度（1 个月到
研究期全段 10 年）都可以单次回测。本用例锁定三点：

1. 长/中/短跨度都能创建成功，不产生任何分段；
2. 判定只依赖用途与研究期末端，与 ``end - start`` 无关；
3. 研究类回测越过研究期末端仍被拒绝（该规则**没有**被放宽）。
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from quant_etf_api.engine.config import (
    PortfolioConfig,
    RankConfig,
    RiskConfig,
    ScoreConfig,
    StrategyConfig,
)
from quant_etf_api.schemas.backtest import BacktestCreateRequest
from quant_etf_api.services.backtest_service import BacktestService


class _FakeConfigService:
    """StrategyConfigService 替身：返回固定可运行配置。"""

    def __init__(self, db: object) -> None:
        """记录 db（未使用）。"""
        self._db = db

    def get_parsed_config(self, strategy_id: str) -> StrategyConfig:
        """返回最小可运行配置。"""
        return StrategyConfig(
            strategy_id=strategy_id,
            display_name=strategy_id,
            index_codes=["000300"],
            score=ScoreConfig(factors={"return_5d": 1.0}),
            rank=RankConfig(top_n=1),
            portfolio=PortfolioConfig(method="equal_weight"),
            risk=RiskConfig(max_asset_weight=1.0),
        )

    def validate_parsed(self, config: StrategyConfig) -> object:
        """校验恒通过。"""
        return type("R", (), {"valid": True, "errors": []})()

    def get_config(self, strategy_id: str) -> None:
        """无快照（快照属于增强能力）。"""
        return None


@pytest.fixture()
def service(monkeypatch: pytest.MonkeyPatch) -> BacktestService:
    """构造可创建回测的服务（DB 为桩）。"""
    monkeypatch.setattr(
        "quant_etf_api.services.backtest_service.StrategyConfigService",
        _FakeConfigService,
    )
    db = MagicMock()
    return BacktestService(db=db)


def _request(start: date, end: date, purpose: str = "research") -> BacktestCreateRequest:
    """构造创建回测请求。"""
    return BacktestCreateRequest(
        strategy_id="t_span",
        start_date=start,
        end_date=end,
        purpose=purpose,  # type: ignore[arg-type]
    )


class TestArbitrarySpanAccepted:
    """任意跨度都能单次创建。"""

    @pytest.mark.parametrize(
        ("start", "end", "label"),
        [
            (date(2016, 1, 1), date(2025, 12, 31), "研究期全段（约 10 年）"),
            (date(2016, 1, 1), date(2019, 1, 1), "3 年"),
            (date(2024, 1, 1), date(2025, 12, 31), "2 年"),
            (date(2025, 11, 1), date(2025, 12, 31), "2 个月"),
            (date(2025, 12, 1), date(2025, 12, 31), "1 个月"),
        ],
    )
    def test_span_accepted(
        self, service: BacktestService, start: date, end: date, label: str
    ) -> None:
        """研究期内任意跨度均可创建，不产生分段。"""
        summary = service.create_backtest(_request(start, end))
        assert summary.status == "pending"
        assert summary.start_date == start
        assert summary.end_date == end, label

    def test_full_research_period_is_single_backtest(self, service: BacktestService) -> None:
        """研究期全段只创建一条回测记录（不拆分）。"""
        service.create_backtest(_request(date(2016, 1, 1), date(2025, 12, 31)))
        assert service._db.add.call_count == 1

    def test_span_length_does_not_affect_validation(self, service: BacktestService) -> None:
        """判定只依赖用途与边界，与跨度无关（超长区间在研究期内同样通过）。"""
        # 20 年跨度但被研究期边界约束：这里显式传研究期内区间，仍应通过
        summary = service.create_backtest(_request(date(2016, 1, 1), date(2025, 12, 31)))
        assert summary.backtest_id


class TestPurposeBoundaryUnchanged:
    """研究/验证期边界规则不变。"""

    def test_research_beyond_research_end_rejected(self, service: BacktestService) -> None:
        """研究类回测越过研究期末端仍被拒绝。"""
        with pytest.raises(ValueError) as exc:
            service.create_backtest(_request(date(2024, 1, 1), date(2026, 6, 30)))
        assert "研究期" in str(exc.value)

    def test_validation_purpose_allows_validation_period(self, service: BacktestService) -> None:
        """验证用途允许使用验证期数据（跨度同样不受限制）。"""
        summary = service.create_backtest(
            _request(date(2026, 1, 1), date(2026, 12, 31), purpose="validation")
        )
        assert summary.purpose == "validation"

    def test_inverted_range_rejected(self, service: BacktestService) -> None:
        """起止倒置仍被拒绝。"""
        with pytest.raises(ValueError):
            service.create_backtest(_request(date(2025, 12, 31), date(2025, 1, 1)))
