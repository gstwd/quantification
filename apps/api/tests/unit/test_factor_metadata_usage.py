"""因子元数据（usage/asset_domain）校验与旧行业域配置退役测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from quant_etf_api.factors.base import USAGE_FILTER, USAGE_TIMING
from quant_etf_api.factors.builtins.breadth import BreadthMA20Computer
from quant_etf_api.factors.registry import get_default_factor_registry
from quant_etf_api.services.strategy_config_service import StrategyConfigService


def _meta_row(factor_id: str, *, usage: list[str], asset_domain: str = "index") -> SimpleNamespace:
    """构造带元数据的因子定义行替身。"""
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


def test_default_registry_has_no_industry_factors() -> None:
    """正式因子注册表不再登记行业域因子，全部挂载在指数资产上。"""
    specs = {spec.factor_id: spec for spec in get_default_factor_registry().specs()}
    assert "rrg_rs_ratio" not in specs
    assert "rrg_rs_momentum" not in specs
    assert "rrg_quadrant" not in specs
    assert "diffusion_count_ratio" not in specs
    for spec in specs.values():
        assert spec.asset_domain == "index"
        assert spec.value_shape in {"asset", "market"}
        assert spec.usage


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


def test_legacy_industry_rotation_config_rejected() -> None:
    """旧行业轮动配置（asset_domain=industry / rotation）被显式拒绝并提示迁移。"""
    svc = _make_service([])
    config = {
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
    result = svc.validate_config(config)
    assert not result.valid
    assert any("已停用的行业轮动配置" in e for e in result.errors)


def test_industry_domain_row_cannot_be_used_in_generic_score() -> None:
    """历史遗留 industry 挂载域因子行不能进入通用评分。"""
    rows = [
        _meta_row(
            "rrg_rs_ratio",
            usage=["score"],
            asset_domain="industry",
        )
    ]
    svc = _make_service(rows)
    config = {
        "score": {"factors": {"rrg_rs_ratio": 1.0}},
        "portfolio": {"method": "equal_weight"},
    }
    result = svc.validate_config(config)
    assert not result.valid
    assert any("值挂载域为 industry" in e for e in result.errors)


def test_factor_params_rejected_for_non_parameterized_factor() -> None:
    """factor_params 只允许作用于有 default_params 的参数化因子。"""
    rows = [
        _meta_row("return_20d", usage=["score", "filter", "rank", "timing"]),
    ]
    svc = _make_service(rows)
    config = {
        "score": {"factors": {"return_20d": 1.0}},
        "portfolio": {"method": "equal_weight"},
        "factor_params": {"return_20d": {"period": 30}},
    }
    result = svc.validate_config(config)
    assert not result.valid
    assert any("非参数化因子" in e for e in result.errors)


def test_factor_params_custom_values_rejected_for_now() -> None:
    """参数化因子本轮仅接受默认参数，自定义参数快速失败。"""
    rows = [
        _meta_row(
            "index_diffusion_ratio",
            usage=["score", "filter", "rank"],
        )
    ]
    svc = _make_service(rows)
    config = {
        "score": {"factors": {"index_diffusion_ratio": 1.0}},
        "portfolio": {"method": "equal_weight"},
        "factor_params": {
            "index_diffusion_ratio": {"diffusion_lookback": 200, "smooth_window": 20}
        },
    }
    result = svc.validate_config(config)
    assert not result.valid
    assert any("自定义参数暂未支持" in e for e in result.errors)
