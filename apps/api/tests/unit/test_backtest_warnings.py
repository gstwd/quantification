"""回测结构化警告（B6 配套）单元测试。

覆盖：
- 因子缺失按因子聚合生成 MISSING_FACTOR 警告
- 指数断档生成 DATA_GAP 警告
- 预热期 / 部分结果警告构造
- get_backtest 返回 BacktestDetail.warnings（序列化与解析）
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock

from quant_etf_api.infra.db.models.core import BacktestRunModel
from quant_etf_api.schemas.backtest import BacktestWarning
from quant_etf_api.services.backtest_service import BacktestService


def _make_service() -> BacktestService:
    """构建测试用 BacktestService（Mock 会话）。"""
    return BacktestService(db=MagicMock())


class TestCollectMissingFactorWarnings:
    """MISSING_FACTOR 聚合逻辑。"""

    def test_aggregates_missing_days_and_codes(self) -> None:
        """应按因子汇总缺失天数与涉及指数，生成一条警告。"""
        svc = _make_service()
        dates = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]
        precomputed = {
            dates[0]: {("000300", "pe_percentile"): 50.0, ("000905", "pe_percentile"): None},
            dates[1]: {("000300", "pe_percentile"): 52.0, ("000905", "pe_percentile"): None},
            dates[2]: {("000300", "pe_percentile"): 55.0, ("000905", "pe_percentile"): 60.0},
        }

        warnings = svc._collect_missing_factor_warnings(
            precomputed, dates, ["000300", "000905"], ["pe_percentile"]
        )

        assert len(warnings) == 1
        w = warnings[0]
        assert w.code == "MISSING_FACTOR"
        assert w.level == "warning"
        assert "pe_percentile" in w.message
        assert "2 个交易日" in w.message
        assert "000905" in w.message

    def test_no_missing_returns_empty(self) -> None:
        """因子全区间完整时不产生警告。"""
        svc = _make_service()
        dates = [date(2025, 1, 2)]
        precomputed = {dates[0]: {("000300", "ma_5d"): 1.0}}

        warnings = svc._collect_missing_factor_warnings(precomputed, dates, ["000300"], ["ma_5d"])

        assert warnings == []


class TestCollectDataGapWarnings:
    """DATA_GAP 聚合逻辑。"""

    def test_missing_bars_generate_warning(self) -> None:
        """指数在回测区间内缺行情时应生成 DATA_GAP 警告。"""
        svc = _make_service()
        dates = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]
        all_bars = {
            ("000300", dates[0]): object(),
            ("000300", dates[1]): object(),
            ("000300", dates[2]): object(),
            # 000905 缺 1 月 3 日行情
            ("000905", dates[0]): object(),
            ("000905", dates[2]): object(),
        }

        warnings = svc._collect_data_gap_warnings(dates, ["000300", "000905"], all_bars)

        assert len(warnings) == 1
        w = warnings[0]
        assert w.code == "DATA_GAP"
        assert w.index_code == "000905"
        assert "1 个交易日" in w.message


class TestWarningsPersistence:
    """warnings 持久化与读取。"""

    def test_row_to_detail_parses_warnings(self) -> None:
        """BacktestDetail 应解析回测行的结构化警告。"""
        svc = _make_service()
        warning = BacktestWarning(
            level="warning",
            code="WARMUP",
            message="回测前 10 个交易日长周期因子数据不足（预热期）",
        )
        row = BacktestRunModel(
            backtest_id="bt-1",
            strategy_id="s1",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 2, 1),
            universe_filter={"mode": "all"},
            status="success",
            created_at=datetime(2025, 1, 1),
            started_at=datetime(2025, 1, 1, 1, 0),
            finished_at=datetime(2025, 1, 1, 2, 0),
        )
        row.warnings = [warning.model_dump()]

        detail = svc._row_to_detail(row)

        assert len(detail.warnings) == 1
        assert detail.warnings[0].code == "WARMUP"
        assert detail.warnings[0].level == "warning"

    def test_row_to_detail_tolerates_corrupt_warnings(self) -> None:
        """warnings 数据损坏时降级为空列表，不影响详情返回。"""
        svc = _make_service()
        row = BacktestRunModel(
            backtest_id="bt-2",
            strategy_id="s1",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 2, 1),
            universe_filter={"mode": "all"},
            status="success",
            created_at=datetime(2025, 1, 1),
            started_at=datetime(2025, 1, 1, 1, 0),
            finished_at=datetime(2025, 1, 1, 2, 0),
        )
        row.warnings = [{"level": "unknown_level"}]

        detail = svc._row_to_detail(row)

        assert detail.warnings == []

    def test_repo_mark_success_persists_warnings(self) -> None:
        """mark_success 应将 warnings 写入回测行。"""
        from quant_etf_api.infra.db.repositories.backtest import BacktestRepository

        db = MagicMock()
        db.is_active = True
        repo = BacktestRepository(db)
        run = BacktestRunModel(
            backtest_id="bt-3",
            strategy_id="s1",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 2, 1),
            universe_filter={"mode": "all"},
            created_at=None,
        )
        repo.find_by_id = MagicMock(return_value=run)

        repo.mark_success("bt-3", {"cumulative_return_pct": 1.0}, warnings=[{"code": "WARMUP"}])

        assert run.warnings == [{"code": "WARMUP"}]
        assert run.status == "success"

    def test_failed_warning_shape_matches_schema(self) -> None:
        """失败场景的 PARTIAL_RESULT 警告可被 schema 反序列化。"""
        warning = BacktestWarning(
            level="error",
            code="PARTIAL_RESULT",
            message="回测中途失败，已保存部分结果至 2025-01-10",
        )
        parsed = BacktestWarning(**warning.model_dump())
        assert parsed.level == "error"
        assert parsed.code == "PARTIAL_RESULT"
