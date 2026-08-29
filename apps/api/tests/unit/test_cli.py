"""CLI 新命令处理函数单元测试。"""

from __future__ import annotations

import json
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import quant_etf_api.cli as cli
from quant_etf_api.schemas.backtest import BacktestDetail
from quant_etf_api.schemas.strategy import StrategySummary


class TestStrategyCli:
    """strategy 命令组。"""

    def test_list_emits_json(self, monkeypatch, capsys) -> None:
        """strategy list 默认输出 JSON 列表。"""
        fake = MagicMock()
        fake.list_strategies.return_value = [
            StrategySummary(
                strategy_id="s1",
                display_name="策略A",
                version="1.0.0",
                frequency="daily",
                description="",
                status="active",
            )
        ]
        monkeypatch.setattr(cli, "StrategyService", lambda db: fake)
        monkeypatch.setattr(cli, "SessionLocal", lambda: MagicMock())

        cli._run_strategy(SimpleNamespace(subcommand="list", no_json=False))

        out = json.loads(capsys.readouterr().out)
        assert out[0]["strategy_id"] == "s1"
        assert out[0]["display_name"] == "策略A"


class TestBacktestCli:
    """backtest 命令组。"""

    def test_status_emits_json(self, monkeypatch, capsys) -> None:
        """backtest status 默认输出 JSON 详情。"""
        fake = MagicMock()
        fake.get_backtest.return_value = BacktestDetail(
            backtest_id="bt1",
            strategy_id="s1",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            status="success",
            created_at=datetime(2024, 1, 1, 8, 0, 0),
            universe_filter={"mode": "all"},
        )
        monkeypatch.setattr(cli, "BacktestService", lambda db: fake)
        monkeypatch.setattr(cli, "SessionLocal", lambda: MagicMock())

        cli._run_backtest(
            SimpleNamespace(
                subcommand="status",
                backtest_id="bt1",
                wait=False,
                no_json=False,
            )
        )

        out = json.loads(capsys.readouterr().out)
        assert out["backtest_id"] == "bt1"
        assert out["status"] == "success"
