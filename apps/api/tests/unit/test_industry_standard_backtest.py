"""行业轮动标准回测主循环冒烟测试（BacktestService 行业域分支）。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest import mock

import pandas as pd

from quant_etf_api.engine.config import (
    PortfolioConfig,
    RankConfig,
    RebalanceConfig,
    RotationConfig,
    ScoreConfig,
    StrategyConfig,
)
from quant_etf_api.services.backtest_service import BacktestService


def _config() -> StrategyConfig:
    """构造行业轮动策略配置（monthly + 行业域）。"""
    return StrategyConfig(
        strategy_id="industry_bt_test",
        display_name="行业回测",
        asset_domain="industry",
        index_codes=["801010", "801120"],
        score=ScoreConfig(factors={}),
        rank=RankConfig(),
        portfolio=PortfolioConfig(method="equal_weight", default_exposure=1.0),
        rebalance=RebalanceConfig(frequency="monthly"),
        rotation=RotationConfig(
            signal="diffusion_rrg",
            top_n=1,
            keep_quadrants=[1, 2],
            benchmark_exclude=["801230"],
        ),
    )


def _panels(dates: list[date]) -> dict:
    """构造含扩散/象限/RS 面板的行业因子面板。"""
    index = pd.DatetimeIndex(dates)
    codes = ["801010", "801120"]
    return {
        "diffusion": pd.DataFrame(
            [[1.0, 0.5], [1.0, 0.5], [1.0, 0.5]], index=index, columns=codes
        ),
        "quadrant": pd.DataFrame(
            [[1.0, 4.0], [1.0, 4.0], [1.0, 4.0]], index=index, columns=codes
        ),
        "rs_ratio": pd.DataFrame(index=index, columns=codes, dtype=float),
        "rs_momentum": pd.DataFrame(index=index, columns=codes, dtype=float),
        "industry_codes": codes,
    }


def test_industry_standard_backtest_loop_writes_rows() -> None:
    """行业轮动回测写入统一 daily/index 结果并成功完成。"""
    dates = [date(2024, 1, 31), date(2024, 2, 1), date(2024, 2, 29)]
    codes = ["801010", "801120"]
    bars = []
    for i, d in enumerate(dates):
        for code in codes:
            bars.append(
                SimpleNamespace(
                    industry_code=code,
                    trade_date=d,
                    open_price=100.0 + i,
                    close_price=101.0 + i,
                )
            )

    row = SimpleNamespace(
        backtest_id="bt-industry",
        strategy_id="industry_bt_test",
        start_date=dates[0],
        end_date=dates[-1],
        params={},
    )
    repo = mock.MagicMock()
    db = mock.MagicMock()
    svc = BacktestService(db=db, backtest_repo=repo)

    with (
        mock.patch(
            "quant_etf_api.infra.db.repositories.industry.IndustryDailyBarRepository.find_trading_dates",
            return_value=dates,
        ),
        mock.patch(
            "quant_etf_api.infra.db.repositories.industry.IndustryDailyBarRepository.find_range",
            return_value=bars,
        ),
        mock.patch(
            "quant_etf_api.infra.db.repositories.industry.IndustryUniverseRepository.find_by_codes",
            return_value=[
                SimpleNamespace(industry_code=c, name_cn=f"行业{c}") for c in codes
            ],
        ),
        mock.patch(
            "quant_etf_api.services.industry_factor_service.IndustryFactorService.build_panels",
            return_value=_panels(dates),
        ),
    ):
        svc._run_industry_backtest_loop("bt-industry", row, _config())

    assert repo.add_daily_result.call_count == len(dates)
    assert repo.add_index_result.call_count == len(dates) * len(codes)
    assert repo.mark_success.call_count == 1
    # 日结果按决策日归因写入持仓（含 801 行业代码）
    last_daily = repo.add_daily_result.call_args_list[-1].args[0]
    assert last_daily.backtest_id == "bt-industry"
    assert last_daily.positions
    captured_codes = {
        call.args[0].index_code for call in repo.add_index_result.call_args_list
    }
    assert captured_codes == set(codes)
