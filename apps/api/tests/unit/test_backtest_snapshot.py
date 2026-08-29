"""回测配置快照行为单元测试。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from quant_etf_api.engine.config import ScoreConfig, StrategyConfig
from quant_etf_api.schemas.backtest import BacktestCreateRequest
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.strategy_config_service import compute_config_hash


def _make_service() -> BacktestService:
    """构建测试用 BacktestService（Mock 会话）。"""
    return BacktestService(db=MagicMock())


def _make_config() -> StrategyConfig:
    """构建测试策略配置。"""
    return StrategyConfig(
        strategy_id="s1",
        display_name="测试策略",
        score=ScoreConfig(factors={"return_20d": 1.0}),
    )


class TestCreateBacktestSnapshot:
    """创建回测时写入配置快照。"""

    def test_snapshot_hash_and_optimization_id_written(self, monkeypatch) -> None:
        """create_backtest 写入 config_snapshot、config_hash 与 optimization_id。"""
        svc = _make_service()
        config_json = {"score": {"factors": {"return_20d": 1.0}}}
        fake_config_svc = MagicMock()
        parsed = MagicMock(portfolio=MagicMock(), index_codes=[])
        fake_config_svc.get_parsed_config.return_value = parsed
        fake_config_svc.validate_parsed.return_value = SimpleNamespace(
            valid=True, errors=[]
        )
        fake_config_svc.get_config.return_value = SimpleNamespace(
            strategy_id="s1",
            display_name="策略A",
            version="1.0.0",
            frequency="daily",
            config_json=config_json,
        )
        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.StrategyConfigService",
            lambda db: fake_config_svc,
        )

        svc.create_backtest(
            BacktestCreateRequest(
                strategy_id="s1",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 12, 31),
            ),
            optimization_id="opt_1",
        )

        row = svc._db.add.call_args.args[0]
        assert row.config_snapshot["config_json"] == config_json
        assert row.config_snapshot["strategy_id"] == "s1"
        assert row.config_hash == compute_config_hash(config_json)
        assert row.optimization_id == "opt_1"


class TestRunBacktestSnapshot:
    """执行回测时配置解析优先级。"""

    def test_uses_snapshot_when_present(self, monkeypatch) -> None:
        """存在快照时用快照重建配置，不读取实时配置。"""
        svc = _make_service()
        snapshot_config = _make_config()
        row = SimpleNamespace(
            backtest_id="bt1",
            strategy_id="s1",
            config_snapshot={"config_json": {"score": {"factors": {"return_20d": 1.0}}}},
            data_cutoff_date=None,
        )
        svc._backtest_repo.find_by_id = MagicMock(return_value=row)
        svc._backtest_repo.mark_running = MagicMock()
        fake_config_svc = MagicMock()
        fake_config_svc.parse_snapshot.return_value = snapshot_config
        fake_config_svc.get_parsed_config.side_effect = AssertionError("不应读取实时配置")
        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.StrategyConfigService",
            lambda db: fake_config_svc,
        )
        svc._index_bar_repo.get_latest_trade_date = MagicMock(return_value=date(2025, 1, 10))
        svc._run_backtest_loop = MagicMock()

        svc.run_backtest("bt1")

        fake_config_svc.get_parsed_config.assert_not_called()
        svc._run_backtest_loop.assert_called_once_with("bt1", row, snapshot_config)
        assert row.data_cutoff_date == date(2025, 1, 10)

    def test_falls_back_to_live_config_without_snapshot(self, monkeypatch) -> None:
        """无快照时回退到实时配置。"""
        svc = _make_service()
        live_config = _make_config()
        row = SimpleNamespace(
            backtest_id="bt1",
            strategy_id="s1",
            config_snapshot=None,
            data_cutoff_date=None,
        )
        svc._backtest_repo.find_by_id = MagicMock(return_value=row)
        svc._backtest_repo.mark_running = MagicMock()
        fake_config_svc = MagicMock()
        fake_config_svc.parse_snapshot.return_value = None
        fake_config_svc.get_parsed_config.return_value = live_config
        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.StrategyConfigService",
            lambda db: fake_config_svc,
        )
        svc._index_bar_repo.get_latest_trade_date = MagicMock(return_value=None)
        svc._run_backtest_loop = MagicMock()

        svc.run_backtest("bt1")

        svc._run_backtest_loop.assert_called_once_with("bt1", row, live_config)
