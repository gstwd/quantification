"""策略配置快照与哈希单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from quant_etf_api.schemas.strategy import StrategyConfigCreate, StrategyValidationResult
from quant_etf_api.services.strategy_config_service import (
    StrategyConfigService,
    compute_config_hash,
)

_SAMPLE_CONFIG: dict = {
    "score": {"factors": {"return_20d": 1.0}, "scoring_mode": "rank"},
    "portfolio": {"method": "winner_take_all", "default_exposure": 1.0},
}


class TestComputeConfigHash:
    """配置哈希稳定性。"""

    def test_hash_stable_for_different_key_order(self) -> None:
        """键序不同的等价配置哈希一致。"""
        a = {
            "score": {"factors": {"return_20d": 1.0}},
            "portfolio": {"method": "equal_weight"},
        }
        b = {
            "portfolio": {"method": "equal_weight"},
            "score": {"factors": {"return_20d": 1.0}},
        }
        assert compute_config_hash(a) == compute_config_hash(b)

    def test_hash_differs_for_different_config(self) -> None:
        """不同配置哈希不同。"""
        a = {"score": {"factors": {"return_20d": 1.0}}}
        b = {"score": {"factors": {"return_20d": 2.0}}}
        assert compute_config_hash(a) != compute_config_hash(b)


class TestParseSnapshot:
    """回测快照重建策略配置。"""

    def test_round_trip_parses_metadata_and_modules(self) -> None:
        """快照重建后元数据与引擎模块完整可用。"""
        snapshot = {
            "strategy_id": "s1",
            "display_name": "测试策略",
            "version": "1.2.0",
            "frequency": "daily",
            "config_json": dict(_SAMPLE_CONFIG),
        }
        parsed = StrategyConfigService.parse_snapshot(snapshot)
        assert parsed is not None
        assert parsed.strategy_id == "s1"
        assert parsed.display_name == "测试策略"
        assert parsed.version == "1.2.0"
        assert parsed.portfolio is not None
        assert parsed.portfolio.method == "winner_take_all"

    def test_config_json_overrides_metadata(self) -> None:
        """config_json 内字段覆盖快照元数据（与实时解析口径一致）。"""
        snapshot = {
            "strategy_id": "s1",
            "display_name": "旧名",
            "version": "1.0.0",
            "frequency": "daily",
            "config_json": {
                **dict(_SAMPLE_CONFIG),
                "display_name": "新名",
                "version": "2.0.0",
            },
        }
        parsed = StrategyConfigService.parse_snapshot(snapshot)
        assert parsed is not None
        assert parsed.display_name == "新名"
        assert parsed.version == "2.0.0"

    def test_invalid_snapshot_returns_none(self) -> None:
        """损坏快照返回 None。"""
        assert StrategyConfigService.parse_snapshot({"config_json": {"score": "bad"}}) is None


class TestCreateConfigStatus:
    """草稿状态创建。"""

    def test_create_draft_keeps_status(self) -> None:
        """status=draft 时创建为草稿行且归一化 schema_version。"""
        svc = StrategyConfigService(db=MagicMock())
        svc._repo = MagicMock()
        svc._repo.find_by_id.return_value = None
        svc.validate_config = MagicMock(
            return_value=StrategyValidationResult(valid=True, errors=[])
        )
        svc.create_config(
            StrategyConfigCreate(
                strategy_id="cand_1",
                display_name="候选",
                config_json=dict(_SAMPLE_CONFIG),
                status="draft",
            )
        )
        model = svc._repo.upsert.call_args.args[0]
        assert model.status == "draft"
        assert model.config_json["schema_version"] == "1"
