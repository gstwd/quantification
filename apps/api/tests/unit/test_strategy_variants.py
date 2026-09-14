"""变体策略清理（D-4）与验证期消费留痕（D-5）单元测试。

- D-4：稳健性批次派生的 ``<基线>__rbXXXX_*`` 草稿策略带 ``source_batch_id``
  元数据，可按批次预演/清理；仍被回测引用时必须显式 ``force``。
- D-5：验证期（2026-01-01 起）数据一旦被消费，就记在策略元数据上——
  回测级留痕只能证明"跑过验证期回测"，无法说明该策略的验证期是否已被消费。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from quant_etf_api.engine.config import (
    PortfolioConfig,
    RankConfig,
    RiskConfig,
    ScoreConfig,
    StrategyConfig,
)
from quant_etf_api.infra.db.models.core import StrategyConfigModel
from quant_etf_api.schemas.backtest import BacktestCreateRequest
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.strategy_config_service import StrategyConfigService

BATCH_ID = "7fead4e55d0246b096a2561ace5ca328"


def _variant_row(strategy_id: str) -> StrategyConfigModel:
    """构造变体草稿策略行。"""
    return StrategyConfigModel(
        strategy_id=strategy_id,
        display_name=f"{strategy_id}（稳健性变体）",
        version="1.6.0",
        description=f"稳健性验证批次 {BATCH_ID} 的派生变体",
        frequency="weekly",
        config_json={"index_codes": ["000300"]},
        status="draft",
        is_variant=True,
        source_batch_id=BATCH_ID,
    )


class _FakeVariantRepo:
    """内存版策略仓库（只需 find_variants / delete_by_id）。"""

    def __init__(self, rows: list[StrategyConfigModel]) -> None:
        """记录初始行。"""
        self.rows = {row.strategy_id: row for row in rows}
        self.deleted: list[str] = []

    def find_variants(self, batch_id: str) -> list[StrategyConfigModel]:
        """返回该批次的变体行。"""
        return [
            row
            for row in self.rows.values()
            if row.source_batch_id == batch_id and row.is_variant
        ]

    def delete_by_id(self, strategy_id: str) -> bool:
        """删除策略行。"""
        if strategy_id not in self.rows:
            return False
        del self.rows[strategy_id]
        self.deleted.append(strategy_id)
        return True

    def mark_validation_consumed(
        self, strategy_id: str, note: str | None, consumed_at: Any
    ) -> bool:
        """记录验证期消费（仅首次写入时间）。"""
        row = self.rows.get(strategy_id)
        if row is None:
            return False
        if row.validation_consumed_at is None:
            row.validation_consumed_at = consumed_at
            row.validation_consumed_note = note
        elif note:
            row.validation_consumed_note = f"{row.validation_consumed_note}；{note}"
        return True


def _service(rows: list[StrategyConfigModel], backtests: dict[str, list[str]]) -> tuple[Any, Any]:
    """构造注入了内存仓库的策略配置服务。"""
    svc = StrategyConfigService(MagicMock())
    repo = _FakeVariantRepo(rows)
    svc._repo = repo
    svc._backtest_ids_by_strategy = lambda strategy_ids: {  # type: ignore[method-assign]
        sid: backtests.get(sid, []) for sid in strategy_ids
    }
    return svc, repo


class TestPruneVariants:
    """D-4：按批次清理变体草稿策略。"""

    def test_dry_run_reports_without_deleting(self) -> None:
        """默认预演：只报告将删除的策略，不写库。"""
        rows = [_variant_row("base__rb7fea_topn_2"), _variant_row("base__rb7fea_topn_4")]
        svc, repo = _service(rows, {})

        result = svc.prune_variants(BATCH_ID)

        assert result["dry_run"] is True
        assert result["matched"] == 2
        assert len(result["deleted"]) == 2
        assert repo.deleted == []

    def test_apply_deletes_unreferenced_variants(self) -> None:
        """无回测引用时直接删除（--apply）。"""
        rows = [_variant_row("base__rb7fea_topn_2")]
        svc, repo = _service(rows, {})

        result = svc.prune_variants(BATCH_ID, dry_run=False)

        assert repo.deleted == ["base__rb7fea_topn_2"]
        assert result["deleted"][0]["strategy_id"] == "base__rb7fea_topn_2"

    def test_referenced_variant_is_skipped_without_force(self) -> None:
        """仍被回测引用时跳过并说明原因（避免留下指向不存在策略的回测）。"""
        rows = [_variant_row("base__rb7fea_topn_2")]
        svc, repo = _service(rows, {"base__rb7fea_topn_2": ["bt-1"]})

        result = svc.prune_variants(BATCH_ID, dry_run=False)

        assert repo.deleted == []
        assert result["skipped"][0]["strategy_id"] == "base__rb7fea_topn_2"
        assert "回测" in result["skipped"][0]["reason"]

    def test_force_deletes_referenced_backtests_then_variant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--force 时先删变体的回测，再删变体策略。"""
        deleted_backtests: list[str] = []

        class _FakeBacktestService:
            """记录被删除的回测 ID。"""

            def __init__(self, db: object) -> None:
                """记录 db（未使用）。"""
                self._db = db

            def delete_backtest(self, backtest_id: str, *, force: bool = False) -> None:
                """记录删除调用。"""
                deleted_backtests.append(backtest_id)

        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.BacktestService", _FakeBacktestService
        )
        rows = [_variant_row("base__rb7fea_topn_2")]
        svc, repo = _service(rows, {"base__rb7fea_topn_2": ["bt-1", "bt-2"]})

        result = svc.prune_variants(BATCH_ID, dry_run=False, force=True)

        assert deleted_backtests == ["bt-1", "bt-2"]
        assert repo.deleted == ["base__rb7fea_topn_2"]
        assert result["deleted"][0]["backtests"] == 2

    def test_unknown_batch_returns_empty_with_hint(self) -> None:
        """批次没有标记变体时给出明确提示（而不是静默成功）。"""
        svc, _ = _service([], {})

        result = svc.prune_variants("deadbeef" * 4)

        assert result["matched"] == 0
        assert "message" in result


class TestValidationConsumed:
    """D-5：验证期消费留痕。"""

    def test_first_mark_records_time_then_appends_note(self) -> None:
        """首次标记写入时间；再次标记只追加说明，不覆盖首次时间。"""
        row = _variant_row("base_v1")
        row.is_variant = False
        row.source_batch_id = None
        svc, repo = _service([row], {})

        assert svc.mark_validation_consumed("base_v1", "人工查看 2026 段") is True
        first_at = repo.rows["base_v1"].validation_consumed_at
        assert first_at is not None

        assert svc.mark_validation_consumed("base_v1", "又跑了一条 monitor 回测") is True
        assert repo.rows["base_v1"].validation_consumed_at == first_at
        assert "又跑了一条 monitor 回测" in repo.rows["base_v1"].validation_consumed_note

    def test_unknown_strategy_returns_false(self) -> None:
        """策略不存在时返回 False（CLI 据此报错退出）。"""
        svc, _ = _service([], {})

        assert svc.mark_validation_consumed("nope") is False

    def test_validation_backtest_marks_consumption(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """创建验证期回测即自动留痕（不需要人工再补一步）。"""
        recorded: list[tuple[str, str | None]] = []

        class _FakeConfigService:
            """记录 mark_validation_consumed 调用。"""

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
                return SimpleNamespace(valid=True, errors=[])

            def get_config(self, strategy_id: str) -> None:
                """无快照。"""
                return None

            def mark_validation_consumed(self, strategy_id: str, note: str | None = None) -> bool:
                """记录留痕调用。"""
                recorded.append((strategy_id, note))
                return True

        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.StrategyConfigService",
            _FakeConfigService,
        )
        svc = BacktestService(db=MagicMock())

        svc.create_backtest(
            BacktestCreateRequest(
                strategy_id="base_v1",
                start_date=date(2026, 1, 1),
                end_date=date(2026, 6, 30),
                purpose="validation",
                purpose_reason="上线验收",
            )
        )

        assert len(recorded) == 1
        assert recorded[0][0] == "base_v1"
        assert "validation" in (recorded[0][1] or "")

    def test_research_backtest_does_not_mark_consumption(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """研究类回测（不越过研究期末端）不产生验证期留痕。"""
        recorded: list[str] = []

        class _FakeConfigService:
            """记录 mark_validation_consumed 调用。"""

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
                return SimpleNamespace(valid=True, errors=[])

            def get_config(self, strategy_id: str) -> None:
                """无快照。"""
                return None

            def mark_validation_consumed(self, strategy_id: str, note: str | None = None) -> bool:
                """记录留痕调用。"""
                recorded.append(strategy_id)
                return True

        monkeypatch.setattr(
            "quant_etf_api.services.backtest_service.StrategyConfigService",
            _FakeConfigService,
        )
        svc = BacktestService(db=MagicMock())

        svc.create_backtest(
            BacktestCreateRequest(
                strategy_id="base_v1",
                start_date=date(2016, 1, 1),
                end_date=date(2025, 12, 31),
            )
        )

        assert recorded == []


class TestModelColumns:
    """迁移 0049 新增列的 ORM 侧一致性（防模型与迁移漂移）。"""

    def test_strategy_config_has_research_metadata_columns(self) -> None:
        """strategy_config 必须含 D-4/D-5 的四个元数据列。"""
        columns = set(StrategyConfigModel.__table__.columns.keys())
        assert {"is_variant", "source_batch_id"} <= columns
        assert {"validation_consumed_at", "validation_consumed_note"} <= columns
