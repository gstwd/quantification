"""验收清单"参数邻域"证据匹配单元测试。

防止复用过期证据：scan 批次必须与**本次会话的基线或候选配置**对应，
且执行口径（execution_model）相同，否则 promote 之后旧批次的邻域结论、
或另一执行口径下的邻域结论，仍会让清单通过。
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

from quant_etf_api.infra.db.models.core import RobustnessRunModel, StrategyOptimizationModel
from quant_etf_api.services.optimization_service import OptimizationService

_BASE_HASH = "hash-base"
_CAND_HASH = "hash-cand"


def _session(execution_model: str = "t_plus_1_close") -> StrategyOptimizationModel:
    """构造测试用优化会话行。"""
    return StrategyOptimizationModel(
        optimization_id="opt1",
        strategy_id="base",
        baseline_version="1.0.0",
        baseline_config_hash=_BASE_HASH,
        candidate_strategy_id="base__opt_1234",
        candidate_version="1.0.1",
        candidate_config_hash=_CAND_HASH,
        hypothesis="测试假设",
        execution_model=execution_model,
        status="evaluated",
        start_date=date(2016, 1, 1),
        end_date=date(2025, 12, 31),
    )


def _scan_row(
    baseline_config_hash: str,
    strategy_id: str = "base",
    execution_model: str = "t_plus_1_close",
) -> RobustnessRunModel:
    """构造测试用 scan 批次行。"""
    return RobustnessRunModel(
        robustness_id="rb-1",
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        baseline_config_hash=baseline_config_hash,
        execution_model=execution_model,
        kind="scan",
        status="success",
        start_date=date(2016, 1, 1),
        end_date=date(2025, 12, 31),
        windows=[{"label": "W0", "start": "2016-01-01", "end": "2018-06-30"}],
        variants=[],
        summary={
            "neighborhood": {
                "n_variants": 4,
                "delta_min": -0.05,
                "delta_max": 0.08,
                "worse_ratio": 0.5,
                "reversal": False,
                "is_plateau": True,
                "tolerance": 0.1,
            }
        },
        scan_params={"preset": "standard", "windows": 4, "max_knobs": 30},
        trial_count=4,
    )


def _service(scan_row: RobustnessRunModel | None) -> OptimizationService:
    """构造返回指定 scan 批次的 OptimizationService 替身。"""
    svc = OptimizationService(db=MagicMock())
    svc._db.query.return_value.filter.return_value.order_by.return_value.first.return_value = (
        scan_row
    )
    return svc


class TestNeighborhoodEvidence:
    """证据匹配与清单项内容。"""

    def test_filter_covers_baseline_candidate_and_candidate_strategy(self) -> None:
        """查询条件覆盖"执行口径 + 基线哈希 / 候选哈希 / 候选策略 ID"。"""
        svc = _service(None)
        svc._check_neighborhood(_session())

        args = svc._db.query.return_value.filter.call_args.args
        assert len(args) == 4
        assert "kind" in str(args[0])
        assert "status" in str(args[1])
        assert "execution_model" in str(args[2])
        or_group = args[3]
        assert len(or_group.clauses) == 3
        assert "baseline_config_hash" in str(or_group)
        assert "strategy_id" in str(or_group)

    def test_execution_model_mismatch_fails(self) -> None:
        """另一执行口径下的邻域平台不能为本次会话背书。"""
        svc = _service(None)
        item = svc._check_neighborhood(_session(execution_model="t_plus_1_close"))

        assert item["pass"] is False
        assert "t_plus_1_close" in item["description"]

    def test_baseline_hash_match_passes_with_evidence(self) -> None:
        """匹配会话基线哈希的 scan 通过，并带出可审计证据。"""
        svc = _service(_scan_row(_BASE_HASH))
        item = svc._check_neighborhood(_session())

        assert item["pass"] is True
        assert item["evidence"]["robustness_id"] == "rb-1"
        assert item["evidence"]["matched_config"] == "baseline"
        assert item["evidence"]["preset"] == "standard"
        assert item["evidence"]["windows"] == 4
        assert item["evidence"]["tolerance"] == 0.1
        assert item["evidence"]["execution_model"] == "t_plus_1_close"

    def test_candidate_hash_match_marks_candidate(self) -> None:
        """scan 的基线哈希等于候选配置哈希时，标记为对候选做过扫描。"""
        svc = _service(_scan_row(_CAND_HASH))
        item = svc._check_neighborhood(_session())

        assert item["pass"] is True
        assert item["evidence"]["matched_config"] == "candidate"

    def test_candidate_strategy_scan_marks_candidate(self) -> None:
        """直接扫描候选策略同样视为有效证据。"""
        svc = _service(_scan_row(_BASE_HASH, strategy_id="base__opt_1234"))
        item = svc._check_neighborhood(_session())

        assert item["pass"] is True
        assert item["evidence"]["matched_config"] == "candidate"

    def test_no_matching_scan_fails_with_hint(self) -> None:
        """没有匹配证据时不通过，并提示该对哪个策略跑 scan。"""
        svc = _service(None)
        item = svc._check_neighborhood(_session())

        assert item["pass"] is False
        assert item["evidence"] is None
        assert "robustness scan" in item["description"]
        assert "base" in item["description"]

    def test_reversal_evidence_fails(self) -> None:
        """邻域存在方向反转时判定不通过。"""
        row = _scan_row(_BASE_HASH)
        row.summary = {"neighborhood": {"n_variants": 4, "reversal": True, "is_plateau": False}}
        svc = _service(row)

        assert svc._check_neighborhood(_session())["pass"] is False
