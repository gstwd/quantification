"""有效候选池时间线（C6）单元测试。

覆盖：
- 逐日规模游程编码（连续相同规模合并、边界段）；
- 剔除区间聚合、按天数降序与截断标记；
- 池覆盖率计算；
- 主循环落库与 ``CANDIDATE_POOL_SHRINK`` 信息级告警。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from quant_etf_api.engine.base import EngineContext, EngineResult
from quant_etf_api.engine.config import (
    PortfolioConfig,
    RankConfig,
    RiskConfig,
    ScoreConfig,
    StrategyConfig,
)
from quant_etf_api.infra.db.models.core import BacktestRunModel
from quant_etf_api.services.backtest_service import BacktestService

DATES = [date(2025, 1, 2) + timedelta(days=i) for i in range(6)]


def _service() -> BacktestService:
    """构造只依赖内存桩的服务。"""
    return BacktestService(db=MagicMock())


class TestTimelineEncoding:
    """游程编码与统计。"""

    def test_run_length_encoding_merges_equal_sizes(self) -> None:
        """连续相同规模合并为一段，规模变化处切分。"""
        pool = _service()._build_candidate_pool_timeline(
            DATES, [20, 20, 21, 21, 21, 19], 21, {}, {}
        )
        assert [seg["size"] for seg in pool["segments"]] == [20, 21, 19]
        assert pool["segments"][0] == {
            "start_date": DATES[0].isoformat(),
            "end_date": DATES[1].isoformat(),
            "trading_days": 2,
            "size": 20,
        }
        assert pool["segments"][2]["trading_days"] == 1
        assert pool["base_size"] == 21
        assert pool["min_size"] == 19
        assert pool["max_size"] == 21
        assert pool["trading_days"] == 6

    def test_timeline_is_json_serializable(self) -> None:
        """时间线必须可直接 JSON 序列化（JSONB 列不接受 date 对象，回归防护）。"""
        exclusion_dates = {"000905": [DATES[0], DATES[1]]}
        reasons = {"000905": {"MISSING_OPEN"}}
        pool = _service()._build_candidate_pool_timeline(
            DATES[:4], [18, 19, 19, 20], 21, exclusion_dates, reasons
        )
        encoded = json.dumps(pool)
        decoded = json.loads(encoded)
        assert decoded["segments"][0]["start_date"] == DATES[0].isoformat()
        assert decoded["exclusions"][0]["first_date"] == DATES[0].isoformat()

    def test_coverage_ratio(self) -> None:
        """池覆盖率 = 逐日规模之和 /（理论规模 × 交易日数）。"""
        pool = _service()._build_candidate_pool_timeline(DATES[:4], [10, 10, 20, 20], 20, {}, {})
        # (10+10+20+20) / (20*4) = 0.75
        assert pool["pool_coverage_ratio"] == 0.75

    def test_exclusions_sorted_and_truncated(self, monkeypatch) -> None:
        """剔除明细按交易日数降序并按配置上限截断。"""
        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.get_settings",
            lambda: SimpleNamespace(candidate_pool_exclusion_limit=2),
        )
        exclusion_dates = {
            "000300": [DATES[0]],
            "000905": [DATES[0], DATES[1], DATES[2]],
            "399001": [DATES[0], DATES[1]],
        }
        reasons = {"000905": {"MISSING_OPEN", "MISSING_FACTOR"}}
        pool = _service()._build_candidate_pool_timeline(
            DATES[:4], [18, 19, 19, 20], 21, exclusion_dates, reasons
        )
        assert [item["index_code"] for item in pool["exclusions"]] == ["000905", "399001"]
        assert pool["truncated_exclusions"] is True
        assert pool["exclusions"][0]["reasons"] == ["MISSING_FACTOR", "MISSING_OPEN"]
        assert pool["exclusions"][0]["first_date"] == DATES[0].isoformat()
        assert pool["exclusions"][0]["last_date"] == DATES[2].isoformat()

    def test_empty_pool_sizes(self) -> None:
        """无逐日规模时返回零值结构而不是抛错。"""
        pool = _service()._build_candidate_pool_timeline([], [], 21, {}, {})
        assert pool["trading_days"] == 0
        assert pool["segments"] == []
        assert pool["pool_coverage_ratio"] == 0.0


def _stub_loop_service() -> tuple[BacktestService, Any]:
    """构造候选池逐日缩水的内存回测服务。"""
    svc = _service()
    svc._ensure_market_scope_bars = MagicMock()
    svc._get_lookback_days = MagicMock(return_value=90)
    svc._write_index_results = MagicMock(return_value=(0, 0))
    bars = {
        ("000300", d): SimpleNamespace(
            close_price=100.0, open_price=100.0, high_price=100.0, low_price=100.0
        )
        for d in DATES
    }
    # 000905 在 DATES[1]/DATES[2] 缺开盘价 → T+1 开盘执行下，这两天的"次日"
    # 不可交易，导致 DATES[0]/DATES[1] 的候选池缩水到 1
    for idx, d in enumerate(DATES):
        bars[("000905", d)] = SimpleNamespace(
            close_price=100.0,
            open_price=None if idx in (1, 2) else 100.0,
            high_price=100.0,
            low_price=100.0,
        )
    universe = [
        {"index_code": "000300", "name_cn": "000300", "category": "broad_index"},
        {"index_code": "000905", "name_cn": "000905", "category": "broad_index"},
    ]
    svc._prepare_backtest_data = MagicMock(
        return_value=(universe, ["000300", "000905"], list(DATES), bars, {}, {})
    )
    svc._factor_provider = MagicMock()
    svc._factor_provider.precompute_backtest_factors.return_value = {
        d: {("000300", "return_5d"): 1.0, ("000905", "return_5d"): 2.0} for d in DATES
    }
    seen: list[list[str]] = []

    def _build_context(config: StrategyConfig, trade_date: date, **kwargs: Any) -> EngineContext:
        """记录每日候选池。"""
        codes = list(kwargs.get("index_codes") or [])
        seen.append(codes)
        return EngineContext(
            trade_date=trade_date,
            universe=[{"index_code": c, "name_cn": c, "category": "broad_index"} for c in codes],
            asset_factors={},
        )

    svc._context_builder = MagicMock()
    svc._context_builder.build.side_effect = _build_context
    svc._engine = MagicMock()
    svc._engine.run.side_effect = lambda config, context, include_details=False: EngineResult(
        trade_date=context.trade_date,
        strategy_id=config.strategy_id,
        timing=None,
        scores={item["index_code"]: 1.0 for item in context.universe},
        rankings=[],
        positions={context.universe[-1]["index_code"]: 1.0} if context.universe else {},
        total_exposure=1.0 if context.universe else 0.0,
        cash_ratio=0.0,
        strategy_results=[],
    )
    svc._backtest_repo = MagicMock()
    row = BacktestRunModel(
        backtest_id="bt-pool",
        strategy_id="t_pool",
        start_date=DATES[0],
        end_date=DATES[-1],
        universe_filter={"mode": "subset", "index_codes": ["000300", "000905"]},
        params={"_execution_model": "t_plus_1_open", "_data_quality_mode": "warn"},
        status="running",
    )
    return svc, {"row": row, "seen": seen}


def _config() -> StrategyConfig:
    """最小可运行配置。"""
    return StrategyConfig(
        strategy_id="t_pool",
        display_name="t_pool",
        index_codes=["000300", "000905"],
        score=ScoreConfig(factors={"return_5d": 1.0}),
        rank=RankConfig(top_n=1),
        portfolio=PortfolioConfig(method="equal_weight"),
        risk=RiskConfig(max_asset_weight=1.0),
    )


class TestLoopIntegration:
    """主循环落库与告警。"""

    def test_candidate_pool_persisted_with_shrink_warning(self) -> None:
        """候选池时间线随 mark_success 落库，缩水时产出信息级提示。"""
        svc, ctx = _stub_loop_service()
        svc._run_backtest_loop("bt-pool", ctx["row"], _config())
        kwargs = svc._backtest_repo.mark_success.call_args.kwargs
        pool = kwargs["candidate_pool"]
        assert pool["base_size"] == 2
        assert pool["min_size"] == 1
        assert pool["segments"][0]["size"] == 1
        codes = [w["code"] for w in kwargs["warnings"]]
        assert "CANDIDATE_POOL_SHRINK" in codes

    def test_no_shrink_warning_when_full_pool(self) -> None:
        """候选池未缩水时不产出缩水提示。"""
        svc, ctx = _stub_loop_service()
        # 让两个指数全程可交易（恢复 000905 的开盘价）
        bars = svc._prepare_backtest_data.return_value[3]
        for d in DATES:
            bars[("000905", d)] = SimpleNamespace(
                close_price=100.0, open_price=100.0, high_price=100.0, low_price=100.0
            )
        svc._run_backtest_loop("bt-pool", ctx["row"], _config())
        kwargs = svc._backtest_repo.mark_success.call_args.kwargs
        assert kwargs["candidate_pool"]["min_size"] == 2
        codes = [w["code"] for w in kwargs["warnings"]]
        assert "CANDIDATE_POOL_SHRINK" not in codes
