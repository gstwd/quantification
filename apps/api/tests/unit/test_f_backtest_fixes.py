"""F 类修复的回测服务单元测试（F-6 / F-7 / F-8 / F-12）。

- F-6：预热期按"各指数自身首根 K 线"起算，单只后上市指数不再把整段回测标成预热；
  MISSING_FACTOR 警告给出按指数排序的缺失明细；
- F-7：日历判定为非交易日的行情日期被剔除，日历不可用时保持原行为；
- F-8：缺开盘价被剔除的资产在警告里单列（不再只有一条 info 汇总）；
- F-12：列表路径可批量现算净成本口径指标。
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from quant_etf_api.domain.common.trading_calendar import TradingCalendarUnavailableError
from quant_etf_api.services.backtest_service import BacktestService, _execution_price_warning


def _make_service() -> BacktestService:
    """构建测试用 BacktestService（Mock 会话）。"""
    return BacktestService(db=MagicMock())


class TestWarmupPerAsset:
    """F-6：预热期口径。"""

    def test_late_listed_index_does_not_inflate_warmup(self) -> None:
        """后上市指数只按自己的首根 K 线起算，不把前面整段算成预热。"""
        svc = _make_service()
        dates = [date(2024, 6, 1) + timedelta(days=i) for i in range(6)]
        codes = ["000300", "000688"]
        precomputed: dict[date, dict[tuple[str, str], float | None]] = {}
        for index, trade_date in enumerate(dates):
            precomputed[trade_date] = {
                # 000300 首日即有值
                ("000300", "return_60d"): 1.0,
                # 000688 从第 3 个交易日才开始有行情，且前两天回望不足
                ("000688", "return_60d"): (None if index < 5 else 2.0) if index >= 3 else None,
            }
        first_bar = {"000300": dates[0], "000688": dates[3]}

        warmup = svc._estimate_warmup_trading_days(
            precomputed, dates, codes, ["return_60d"], first_bar
        )

        # 只有 000688 在"自己的前 2 天"缺值（index 3、4 → 局部缺口 2），
        # 而不是旧的"整段前 5 天都算预热"
        assert warmup == 2

    def test_all_index_warmup_uses_max_local_gap(self) -> None:
        """多个指数的预热期取各自局部缺口的最大值。"""
        svc = _make_service()
        dates = [date(2024, 6, 1) + timedelta(days=i) for i in range(4)]
        precomputed = {
            dates[0]: {("A", "f"): None, ("B", "f"): None},
            dates[1]: {("A", "f"): 1.0, ("B", "f"): None},
            dates[2]: {("A", "f"): 1.0, ("B", "f"): None},
            dates[3]: {("A", "f"): 1.0, ("B", "f"): 2.0},
        }

        warmup = svc._estimate_warmup_trading_days(
            precomputed, dates, ["A", "B"], ["f"], {"A": dates[0], "B": dates[0]}
        )

        assert warmup == 3


class TestMissingFactorWarningDetail:
    """F-6：缺失因子警告给出按指数排序的明细。"""

    def test_lists_index_by_missing_days(self) -> None:
        """明细应按缺失天数排序，让人一眼看出是哪只指数在缺。"""
        svc = _make_service()
        dates = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]
        precomputed = {
            dates[0]: {("000300", "f"): 1.0, ("000688", "f"): None, ("000905", "f"): 1.0},
            dates[1]: {("000300", "f"): 1.0, ("000688", "f"): None, ("000905", "f"): 1.0},
            dates[2]: {("000300", "f"): None, ("000688", "f"): 1.0, ("000905", "f"): 1.0},
        }

        warnings = svc._collect_missing_factor_warnings(
            precomputed, dates, ["000300", "000688", "000905"], ["f"]
        )

        assert len(warnings) == 1
        message = warnings[0].message
        assert "3 个交易日" in message
        # 000688 缺 2 天排在最前，000300 缺 1 天在后
        assert message.index("000688 2 天") < message.index("000300 1 天")


class _StubCalendar:
    """替身交易日历：只认给定的交易日集合。"""

    def __init__(self, days: set[date]) -> None:
        """初始化替身日历。

        Args:
            days: 交易日集合。
        """
        self._days = days

    def is_trading_day(self, value: date) -> bool:
        """判断是否为交易日。"""
        return value in self._days


class TestNonTradingDateFilter:
    """F-7：剔除行情里的非交易日。"""

    def test_drops_holiday_bar_date(self) -> None:
        """日历判定非交易日的日期（如 2018-06-18 端午节）应被剔除。"""
        svc = _make_service()
        days = [date(2018, 6, 15), date(2018, 6, 18), date(2018, 6, 19)]
        calendar = _StubCalendar({date(2018, 6, 15), date(2018, 6, 19)})
        with patch(
            "quant_etf_api.services.backtest_service.resolve_trading_calendar",
            return_value=(calendar, "upstream"),
        ):
            filtered, dropped = svc._filter_non_trading_dates(
                date(2018, 6, 1), date(2018, 6, 30), days
            )

        assert filtered == [date(2018, 6, 15), date(2018, 6, 19)]
        assert dropped == [date(2018, 6, 18)]

    def test_calendar_unavailable_keeps_original(self) -> None:
        """日历不可用时保持原有交易日列表（不静默改变口径）。"""
        svc = _make_service()
        days = [date(2018, 6, 15), date(2018, 6, 18)]
        with patch(
            "quant_etf_api.services.backtest_service.resolve_trading_calendar",
            side_effect=TradingCalendarUnavailableError("无法解析日历"),
        ):
            filtered, dropped = svc._filter_non_trading_dates(
                date(2018, 6, 1), date(2018, 6, 30), days
            )

        assert filtered == days
        assert dropped == []


class TestIngestNonTradingGuard:
    """F-7：摄取侧也要拦住非交易日行情。"""

    def test_ingest_drops_holiday_bars(self) -> None:
        """非交易日行情在入库前被剔除并计数。"""
        from quant_etf_api.services.ingest_service import IngestService

        svc = object.__new__(IngestService)
        svc._db = MagicMock()
        calendar = _StubCalendar({date(2018, 6, 15), date(2018, 6, 19)})
        bars = [
            SimpleNamespace(trade_date=date(2018, 6, 15)),
            SimpleNamespace(trade_date=date(2018, 6, 18)),
            SimpleNamespace(trade_date=date(2018, 6, 19)),
        ]
        with patch(
            "quant_etf_api.services.ingest_service.resolve_trading_calendar",
            return_value=(calendar, "upstream"),
        ):
            kept, dropped = svc._drop_non_trading_bars(bars)

        assert [b.trade_date for b in kept] == [date(2018, 6, 15), date(2018, 6, 19)]
        assert [b.trade_date for b in dropped] == [date(2018, 6, 18)]

    def test_ingest_keeps_bars_when_calendar_missing(self) -> None:
        """日历不可用时不阻断摄取（保持原行为）。"""
        from quant_etf_api.services.ingest_service import IngestService

        svc = object.__new__(IngestService)
        svc._db = MagicMock()
        bars = [SimpleNamespace(trade_date=date(2018, 6, 18))]
        with patch(
            "quant_etf_api.services.ingest_service.resolve_trading_calendar",
            side_effect=TradingCalendarUnavailableError("无日历"),
        ):
            kept, dropped = svc._drop_non_trading_bars(bars)

        assert kept == bars
        assert dropped == []


class TestExecutionPriceWarning:
    """F-8：缺开盘价导致的剔除必须显式可见。"""

    def test_lists_excluded_assets_by_days(self) -> None:
        """按剔除天数排序列出资产与原因。"""
        warning = _execution_price_warning(
            {
                "931994": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
                "H11059": [date(2024, 1, 2)],
                "000905": [date(2024, 1, 2)],
            },
            {
                "931994": {"MISSING_OPEN"},
                "H11059": {"MISSING_OPEN"},
                "000905": {"MISSING_CLOSE"},
            },
        )

        assert warning is not None
        assert warning.code == "EXECUTION_PRICE_MISSING"
        assert "2 个指数" in warning.message
        assert "931994 3 天" in warning.message
        # 只缺收盘价的指数不算执行价缺失
        assert "000905" not in warning.message

    def test_returns_none_without_missing_open(self) -> None:
        """没有缺开盘价的剔除时不产生警告。"""
        assert _execution_price_warning({"000905": [date(2024, 1, 2)]}, {"000905": {"MISSING_CLOSE"}}) is None


class _FakeBacktestRepo:
    """替身回测仓库：提供批量 (收益, 换手) 序列。"""

    def __init__(
        self, pairs: dict[str, list[tuple[float | None, float | None, float | None]]]
    ) -> None:
        """初始化替身仓库。

        Args:
            pairs: backtest_id → 逐日 (收益, 换手)。
        """
        self.pairs = pairs

    def find_return_turnover_pairs(
        self, backtest_ids: list[str]
    ) -> dict[str, list[tuple[float | None, float | None, float | None]]]:
        """按 ID 返回预置序列。"""
        return {key: value for key, value in self.pairs.items() if key in backtest_ids}


class TestListNetMetrics:
    """F-12：列表摘要带净成本口径。"""

    def test_net_metrics_follow_turnover_cost(self) -> None:
        """净夏普低于毛夏普，且成本越高折损越大。"""
        svc = _make_service()
        row = MagicMock()
        row.backtest_id = "bt-1"
        row.status = "success"
        row.metrics = {"sharpe_ratio": 1.0}
        # 10 天、每天 0.1% 收益、每天 0.05 单边换手
        svc._backtest_repo = _FakeBacktestRepo(
            {"bt-1": [(0.1, 0.05, 0.05) for _ in range(10)]}
        )

        result = svc._net_metrics_by_backtest([row], cost_bps=10.0)

        item: dict[str, Any] = result["bt-1"]
        assert item["cost_bps"] == 10.0
        assert item["net_annualized_return_pct"] < 100.0
        # 换手 0.05/天 × 252 = 年化 12.6 倍；10bp 成本 ≈ 1.26pp/年
        assert item["annualized_turnover"] == pytest.approx(12.6, rel=1e-6)
        assert item["cost_drag_pct_per_year"] == pytest.approx(1.26, rel=1e-6)

    def test_skips_rows_without_daily_results(self) -> None:
        """没有逐日结果的回测不进结果字典。"""
        svc = _make_service()
        row = MagicMock()
        row.backtest_id = "bt-empty"
        row.status = "success"
        row.metrics = {"sharpe_ratio": 1.0}
        svc._backtest_repo = _FakeBacktestRepo({})

        assert svc._net_metrics_by_backtest([row], cost_bps=10.0) == {}
