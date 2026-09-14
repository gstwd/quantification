"""回测悬挂引用（C5）单元测试。

覆盖：
- JSONB 引用（稳健性变体窗口、优化折窗口）的悬挂检测与清理；
- ``robustness collect`` 不再把"已删除的回测"当成"仍在排队"（否则批次永久 running）；
- 受控删除：运行中拒绝、存在 JSONB 引用需 force；
- ``prune_dangling_references`` 默认预演不写库。
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock

import pytest

from quant_etf_api.infra.db.models.core import (
    BacktestRunModel,
    RobustnessRunModel,
    StrategyOptimizationModel,
)
from quant_etf_api.services.backtest_reference_service import BacktestReferenceService
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.robustness_service import RobustnessService


class _FakeQuery:
    """按模型返回预置行的查询桩。"""

    def __init__(self, rows: list[Any], filters: int = 0) -> None:
        self._rows = rows
        self._filters = filters

    def all(self) -> list[Any]:
        """返回预置行。"""
        return list(self._rows)

    def filter(self, *args: Any, **kwargs: Any) -> _FakeQuery:
        """记录过滤（本桩忽略条件，由调用方保证语义）。"""
        return _FakeQuery(self._rows, self._filters + 1)

    def one_or_none(self) -> Any:
        """返回第一行或 None。"""
        return self._rows[0] if self._rows else None


class _FakeDb:
    """按模型分发预置行的 Session 桩。"""

    def __init__(
        self,
        *,
        backtest_ids: list[str] | None = None,
        robustness: list[Any] | None = None,
        optimizations: list[Any] | None = None,
    ) -> None:
        self._backtest_ids = backtest_ids or []
        self._robustness = robustness or []
        self._optimizations = optimizations or []

    def query(self, model: Any, *args: Any) -> _FakeQuery:
        """按模型类型返回对应行。"""
        if model is RobustnessRunModel:
            return _FakeQuery(self._robustness)
        if model is StrategyOptimizationModel:
            return _FakeQuery(self._optimizations)
        # 其余查询一律视为"存在的回测 ID"集合查询
        return _FakeQuery([(bid,) for bid in self._backtest_ids])

    def commit(self) -> None:
        """空实现（桩）。"""


def _robustness_row(robustness_id: str, mapping: dict[str, str | None]) -> RobustnessRunModel:
    """构造稳健性批次行。"""
    row = RobustnessRunModel(
        robustness_id=robustness_id,
        strategy_id="t_ref",
        strategy_version="v1",
        baseline_config_hash="hash",
        kind="ablate",
        status="running",
        start_date=date(2016, 1, 1),
        end_date=date(2025, 12, 31),
        windows=[{"label": "w1", "start": "2020-01-01", "end": "2021-12-31"}],
        trial_count=1,
    )
    row.variants = [
        {
            "label": "ablate_x",
            "kind": "ablation",
            "backtest_ids": mapping,
        }
    ]
    return row


def _optimization_row(
    optimization_id: str, folds: list[dict[str, Any]]
) -> StrategyOptimizationModel:
    """构造优化会话行。"""
    row = StrategyOptimizationModel(
        optimization_id=optimization_id,
        strategy_id="t_ref",
        baseline_version="v1",
        baseline_config_hash="hash",
        candidate_strategy_id="t_ref__cand",
        candidate_version="v1",
        candidate_config_hash="hash2",
        hypothesis="h",
        status="evaluated",
        start_date=date(2020, 1, 1),
        end_date=date(2025, 12, 31),
    )
    row.baseline_backtest_id = "bt-keep"
    row.candidate_backtest_id = "bt-gone"
    row.fold_backtests = folds
    return row


class TestDanglingDetection:
    """悬挂引用检测。"""

    def test_detects_robustness_and_optimization_holes(self) -> None:
        """同时检出稳健性变体与优化会话（含折窗口）的悬挂引用。"""
        opt_row = _optimization_row(
            "opt-1",
            [
                {
                    "fold": 1,
                    "start": "2020-01-01",
                    "end": "2021-12-31",
                    "baseline_backtest_id": "bt-keep",
                    "candidate_backtest_id": "bt-gone",
                }
            ],
        )
        # 会话级两个直连引用都存在，悬挂只来自折窗口
        opt_row.candidate_backtest_id = "bt-keep"
        db = _FakeDb(
            backtest_ids=["bt-keep"],
            robustness=[_robustness_row("rb-1", {"w1": "bt-keep", "w2": "bt-gone"})],
            optimizations=[opt_row],
        )
        report = BacktestReferenceService(db).find_dangling_references()
        holders = {item["holder"] for item in report["items"]}
        assert "robustness_run.variants" in holders
        assert "strategy_optimization.fold_backtests" in holders
        assert report["total"] == 2

    def test_detects_session_level_missing_ids(self) -> None:
        """会话级 baseline/candidate 引用缺失时同样被检出。"""
        db = _FakeDb(
            backtest_ids=["bt-keep"],
            optimizations=[_optimization_row("opt-1", [])],
        )
        report = BacktestReferenceService(db).find_dangling_references()
        assert report["total"] == 1
        assert report["items"][0]["holder"] == "strategy_optimization"
        assert report["items"][0]["missing_backtest_ids"] == ["bt-gone"]

    def test_no_dangling_when_all_present(self) -> None:
        """全部引用都存在时报告为空。"""
        db = _FakeDb(
            backtest_ids=["bt-keep"],
            robustness=[_robustness_row("rb-1", {"w1": "bt-keep"})],
        )
        report = BacktestReferenceService(db).find_dangling_references()
        assert report == {"total": 0, "items": []}

    def test_find_referencing_holders(self) -> None:
        """按回测 ID 反查持有者。"""
        db = _FakeDb(
            backtest_ids=["bt-keep"],
            robustness=[_robustness_row("rb-1", {"w1": "bt-keep"})],
            optimizations=[_optimization_row("opt-1", [])],
        )
        holders = BacktestReferenceService(db).find_referencing_holders("bt-keep")
        assert {item["holder"] for item in holders} == {
            "robustness_run.variants",
            "strategy_optimization",
        }

    def test_missing_ids_for_preserves_order_and_dedupes(self) -> None:
        """批量缺失检查保序去重。"""
        db = _FakeDb(backtest_ids=["bt-a"])
        missing = BacktestReferenceService(db).missing_ids_for(
            ["bt-gone", "bt-a", "bt-gone", "bt-b"]
        )
        assert missing == ["bt-gone", "bt-b"]


class TestRobustnessCollectMissingSemantics:
    """批次汇总对"已删除回测"的处理。"""

    def _service(self, row: RobustnessRunModel, existing: dict[str, str]) -> RobustnessService:
        """构造稳健性服务（仓储为桩）。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.one_or_none.return_value = row
        svc = RobustnessService(db)

        def _find(backtest_id: str) -> Any:
            """已删除的回测返回 None，其余返回对应状态的行。"""
            if backtest_id not in existing:
                return None
            return type("Row", (), {"status": existing[backtest_id]})()

        svc._backtest_repo = MagicMock()
        svc._backtest_repo.find_by_id.side_effect = _find
        svc._summarize = MagicMock(return_value={"neighborhood": {}})
        return svc

    def test_missing_backtest_does_not_block_forever(self) -> None:
        """回测已删除时批次落 failed，而不是永远停在 running。"""
        row = _robustness_row("rb-1", {"w1": "bt-ok", "w2": "bt-gone"})
        svc = self._service(row, {"bt-ok": "success"})
        result = svc.collect("rb-1", allow_partial=False)
        assert result["status"] == "failed"
        assert result["missing_backtests"] == 1
        assert row.status == "failed"

    def test_allow_partial_records_missing_windows(self) -> None:
        """部分汇总时 coverage 显式记录缺失窗口。"""
        row = _robustness_row("rb-1", {"w1": "bt-ok", "w2": "bt-gone"})
        svc = self._service(row, {"bt-ok": "success"})
        result = svc.collect("rb-1", allow_partial=True)
        assert result["status"] == "partial"
        coverage = result["summary"]["coverage"]
        assert coverage["missing_windows"]
        assert coverage["is_partial"] is True

    def test_pending_still_waits(self) -> None:
        """仍在排队的回测依旧让批次保持 running。"""
        row = _robustness_row("rb-1", {"w1": "bt-pending"})
        svc = self._service(row, {"bt-pending": "pending"})
        result = svc.collect("rb-1", allow_partial=False)
        assert result["status"] == "running"


class TestDeleteAndPrune:
    """受控删除与 JSONB 清理。"""

    def _service(self, db: Any, row: BacktestRunModel | None) -> BacktestService:
        """构造回测服务（仓储为桩）。"""
        svc = BacktestService(db=db)
        svc._backtest_repo = MagicMock()
        svc._backtest_repo.find_by_id.return_value = row
        return svc

    def test_delete_rejects_running(self) -> None:
        """运行中的回测必须先取消再删除。"""
        row = _backtest_row("bt-1", status="running")
        svc = self._service(_FakeDb(backtest_ids=["bt-1"]), row)
        with pytest.raises(ValueError, match="请先取消"):
            svc.delete_backtest("bt-1")

    def test_delete_rejects_when_referenced_without_force(self) -> None:
        """存在 JSONB 引用且未 force 时拒绝。"""
        row = _backtest_row("bt-1", status="success")
        db = _FakeDb(
            backtest_ids=["bt-1"],
            robustness=[_robustness_row("rb-1", {"w1": "bt-1"})],
        )
        svc = self._service(db, row)
        with pytest.raises(ValueError, match="仍被引用"):
            svc.delete_backtest("bt-1")

    def test_delete_with_force_reports_holders(self) -> None:
        """force 删除成功并回显持有者。"""
        row = _backtest_row("bt-1", status="success")
        db = _FakeDb(
            backtest_ids=["bt-1"],
            robustness=[_robustness_row("rb-1", {"w1": "bt-1"})],
        )
        svc = self._service(db, row)
        result = svc.delete_backtest("bt-1", force=True)
        assert result.deleted is True
        assert result.holders
        svc._backtest_repo.delete.assert_called_once_with("bt-1")

    def test_prune_dry_run_does_not_write(self) -> None:
        """预演只统计，不改写 variants。"""
        row = _robustness_row("rb-1", {"w1": "bt-keep", "w2": "bt-gone"})
        db = _FakeDb(backtest_ids=["bt-keep"], robustness=[row])
        svc = self._service(db, None)
        result = svc.prune_dangling_references(dry_run=True)
        assert result.dry_run is True
        assert result.removed_windows == 1
        assert row.variants[0]["backtest_ids"] == {"w1": "bt-keep", "w2": "bt-gone"}
        assert "deleted_windows" not in row.variants[0]

    def test_prune_apply_removes_keys_and_marks_windows(self) -> None:
        """实际清理移除悬挂键并记录 deleted_windows。"""
        row = _robustness_row("rb-1", {"w1": "bt-keep", "w2": "bt-gone"})
        db = _FakeDb(backtest_ids=["bt-keep"], robustness=[row])
        svc = self._service(db, None)
        result = svc.prune_dangling_references(dry_run=False)
        assert result.removed_windows == 1
        assert row.variants[0]["backtest_ids"] == {"w1": "bt-keep"}
        assert row.variants[0]["deleted_windows"] == ["w2"]

    def test_prune_marks_jsonb_dirty(self) -> None:
        """清理 JSONB 时必须显式标记脏，否则 SQLAlchemy 不会写回（回归防护）。"""
        import quant_etf_api.services.backtest_service as module

        row = _robustness_row("rb-1", {"w1": "bt-gone"})
        opt = _optimization_row(
            "opt-1",
            [
                {
                    "fold": 1,
                    "start": "2020-01-01",
                    "end": "2021-12-31",
                    "baseline_backtest_id": "bt-gone",
                    "candidate_backtest_id": None,
                }
            ],
        )
        opt.baseline_backtest_id = None
        opt.candidate_backtest_id = None
        db = _FakeDb(backtest_ids=["bt-keep"], robustness=[row], optimizations=[opt])
        svc = self._service(db, None)
        flag = MagicMock()
        original = module.flag_modified
        module.flag_modified = flag
        try:
            svc.prune_dangling_references(dry_run=False)
        finally:
            module.flag_modified = original
        flagged = {call.args[1] for call in flag.call_args_list}
        assert flagged == {"variants", "fold_backtests"}

    def test_prune_nulls_optimization_folds(self) -> None:
        """优化折窗口中指向已删除回测的引用被置空。"""
        row = _optimization_row(
            "opt-1",
            [
                {
                    "fold": 1,
                    "start": "2020-01-01",
                    "end": "2021-12-31",
                    "baseline_backtest_id": "bt-keep",
                    "candidate_backtest_id": "bt-gone",
                }
            ],
        )
        row.baseline_backtest_id = "bt-keep"
        row.candidate_backtest_id = "bt-keep"
        db = _FakeDb(backtest_ids=["bt-keep"], optimizations=[row])
        svc = self._service(db, None)
        result = svc.prune_dangling_references(dry_run=False)
        assert result.nulled_folds == 1
        assert row.fold_backtests[0]["candidate_backtest_id"] is None
        assert row.fold_backtests[0]["baseline_backtest_id"] == "bt-keep"


def _backtest_row(backtest_id: str, *, status: str) -> BacktestRunModel:
    """构造回测 ORM 行。"""
    return BacktestRunModel(
        backtest_id=backtest_id,
        strategy_id="t_ref",
        start_date=date(2020, 1, 1),
        end_date=date(2021, 12, 31),
        universe_filter={"mode": "all"},
        status=status,
    )
