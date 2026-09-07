"""因子元数据四轴（usage/asset_domain）校验与行业因子登记测试。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest import mock

from quant_etf_api.domain.industry.constants import (
    DEFAULT_DIFFUSION_LOOKBACK,
    DEFAULT_LOOKBACK_MOM,
    DEFAULT_LOOKBACK_RATIO,
    DEFAULT_SMOOTH_WINDOW,
)
from quant_etf_api.domain.industry.factor_defs import get_industry_factor_specs
from quant_etf_api.factors.base import (
    ASSET_DOMAIN_INDUSTRY,
    USAGE_FILTER,
    USAGE_ROTATION_INPUT,
    USAGE_TIMING,
    VALUE_SHAPE_PANEL,
)
from quant_etf_api.factors.builtins.breadth import BreadthMA20Computer
from quant_etf_api.factors.registry import get_default_factor_registry
from quant_etf_api.engine.config import (
    PortfolioConfig,
    RankConfig,
    RotationConfig,
    ScoreConfig,
    StrategyConfig,
)
from quant_etf_api.services.strategy_config_service import StrategyConfigService
from quant_etf_api.services.strategy_decision_service import StrategyDecisionService


def _meta_row(factor_id: str, *, usage: list[str], asset_domain: str = "index") -> SimpleNamespace:
    """构造带四轴元数据的因子定义行替身。"""
    return SimpleNamespace(
        factor_id=factor_id,
        asset_domain=asset_domain,
        usage=usage,
    )


def _make_service(rows: list[SimpleNamespace]) -> StrategyConfigService:
    """构造返回指定因子定义行的策略配置服务。"""
    db = mock.MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = rows
    return StrategyConfigService(db=db)


def test_industry_factor_specs_registered() -> None:
    """4 个行业因子元数据齐全且默认参数与复刻口径一致。"""
    specs = {s.factor_id: s for s in get_industry_factor_specs()}
    assert set(specs) == {
        "rrg_rs_ratio",
        "rrg_rs_momentum",
        "rrg_quadrant",
        "diffusion_count_ratio",
    }
    for spec in specs.values():
        assert spec.asset_domain == ASSET_DOMAIN_INDUSTRY
        assert spec.value_shape == VALUE_SHAPE_PANEL
        assert spec.usage == [USAGE_ROTATION_INPUT]
        params = spec.default_params
        assert params["lookback_ratio"] == DEFAULT_LOOKBACK_RATIO == 220
        assert params["lookback_mom"] == DEFAULT_LOOKBACK_MOM == 60
        assert params["smooth_window"] == DEFAULT_SMOOTH_WINDOW == 20
        assert params["diffusion_lookback"] == DEFAULT_DIFFUSION_LOOKBACK == 220


def test_breadth_factor_usage_restricted() -> None:
    """市场宽度因子 usage 不含 score，配置校验拒绝放入评分。"""
    breadth = BreadthMA20Computer().spec
    assert breadth.usage == [USAGE_TIMING, USAGE_FILTER]
    rows = [
        _meta_row("breadth_ma20_pct", usage=[USAGE_TIMING, USAGE_FILTER]),
        _meta_row("return_20d", usage=["score", "filter", "rank", "timing"]),
    ]
    svc = _make_service(rows)
    config = {
        "score": {"factors": {"breadth_ma20_pct": 1.0}},
        "portfolio": {"method": "equal_weight"},
    }
    result = svc.validate_config(config)
    assert not result.valid
    assert any("不适用于评分位置" in e for e in result.errors)


def test_industry_factor_cannot_be_used_in_index_score() -> None:
    """行业域因子引用进指数策略 score 时按资产域错误快速失败。"""
    rows = [
        _meta_row(
            "rrg_rs_ratio",
            usage=[USAGE_ROTATION_INPUT],
            asset_domain=ASSET_DOMAIN_INDUSTRY,
        )
    ]
    svc = _make_service(rows)
    config = {
        "score": {"factors": {"rrg_rs_ratio": 1.0}},
        "portfolio": {"method": "equal_weight"},
    }
    result = svc.validate_config(config)
    assert not result.valid
    assert any("资产域为 industry" in e for e in result.errors)


def test_rotation_strategy_valid_config() -> None:
    """合法行业轮动策略（显式 industry 域 + rotation 模块）校验通过。"""
    db = mock.MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
    svc = StrategyConfigService(db=db)
    config = {
        "schema_version": "1",
        "asset_domain": "industry",
        "index_codes": ["801010", "801120"],
        "score": {"factors": {}},
        "portfolio": {"method": "equal_weight", "default_exposure": 1.0},
        "rebalance": {"frequency": "monthly"},
        "rotation": {
            "signal": "diffusion_rrg",
            "top_n": 6,
            "keep_quadrants": [1, 2],
            "benchmark_exclude": ["801230"],
        },
    }
    with mock.patch(
        "quant_etf_api.infra.db.repositories.industry.IndustryUniverseRepository.find_active_codes",
        return_value=["801010", "801120", "801230"],
    ):
        result = svc.validate_config(config)
    assert result.valid, result.errors


def test_rotation_strategy_rejects_mixed_modules_and_missing_codes() -> None:
    """rotation 与通用评分/择时混用或缺少行业范围时快速失败。"""
    svc = _make_service([])
    config = {
        "asset_domain": "industry",
        "index_codes": ["801010"],
        "score": {"factors": {"return_20d": 1.0}},
        "timing": {"factors": {"pe_percentile": 1.0}},
        "portfolio": {"method": "equal_weight"},
        "rotation": {"signal": "diffusion_rrg", "top_n": 3},
    }
    result = svc.validate_config(config)
    assert not result.valid
    assert any("不允许配置评分因子" in e for e in result.errors)
    assert any("不允许配置择时模块" in e for e in result.errors)

    config_no_codes = {
        "asset_domain": "industry",
        "score": {"factors": {}},
        "portfolio": {"method": "equal_weight"},
        "rotation": {"signal": "diffusion_rrg", "top_n": 3},
    }
    result = svc.validate_config(config_no_codes)
    assert not result.valid
    assert any("必须通过 index_codes" in e for e in result.errors)


def test_industry_asset_domain_requires_rotation() -> None:
    """asset_domain=industry 但没有 rotation 的通用策略被拒绝。"""
    svc = _make_service([])
    config = {
        "asset_domain": "industry",
        "score": {"factors": {"return_20d": 1.0}},
        "portfolio": {"method": "equal_weight"},
    }
    result = svc.validate_config(config)
    assert not result.valid
    assert any("仅支持 rotation" in e for e in result.errors)


def test_default_registry_index_factors_have_explicit_usage() -> None:
    """默认注册表因子四轴有值：指数域/asset 形态/至少可评分或过滤。"""
    for spec in get_default_factor_registry().specs():
        assert spec.asset_domain == "index"
        assert spec.value_shape in {"asset", "market"}
        assert spec.usage


def test_run_and_persist_rejects_industry_rotation() -> None:
    """行业轮动策略禁止定时持久化运行（run_and_persist 快速失败）。"""
    config = StrategyConfig(
        strategy_id="rotation_persist",
        display_name="轮动",
        asset_domain="industry",
        index_codes=["801010"],
        score=ScoreConfig(factors={}),
        rank=RankConfig(),
        portfolio=PortfolioConfig(method="equal_weight"),
        rotation=RotationConfig(),
    )
    svc = StrategyDecisionService(db=object())  # type: ignore[arg-type]
    try:
        svc.run_and_persist(config, date(2024, 1, 31), "run-1")
    except ValueError as exc:
        assert "暂不支持持久化运行" in str(exc)
    else:
        raise AssertionError("run_and_persist 应拒绝行业轮动策略")
