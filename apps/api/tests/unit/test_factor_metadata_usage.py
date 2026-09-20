"""因子模板元数据与别名声明校验测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from quant_etf_api.factors.base import USAGE_FILTER, USAGE_TIMING
from quant_etf_api.factors.catalog import build_default_registry
from quant_etf_api.factors.templates import FactorParameterError
from quant_etf_api.services.strategy_config_service import StrategyConfigService


def _meta_row(factor_id: str, *, usage: list[str]) -> SimpleNamespace:
    """构造带元数据的因子定义行替身。"""
    return SimpleNamespace(
        factor_id=factor_id,
        usage=usage,
    )


def _make_service(rows: list[SimpleNamespace]) -> StrategyConfigService:
    """构造返回指定因子定义行的策略配置服务。"""
    db = mock.MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = rows
    return StrategyConfigService(db=db)


def test_default_registry_has_no_industry_factors() -> None:
    """正式模板目录不再登记行业域因子，全部挂载在指数资产上。"""
    templates = {template.template_id: template for template in build_default_registry().all()}
    assert "rrg_rs_ratio" not in templates
    assert "rrg_rs_momentum" not in templates
    assert "rrg_quadrant" not in templates
    assert "diffusion_count_ratio" not in templates
    for template in templates.values():
        assert template.value_shape in {"asset", "market"}
        assert template.usage


def test_breadth_factor_usage_restricted() -> None:
    """市场宽度模板 usage 不含 score，配置校验拒绝放入评分。"""
    breadth = build_default_registry().get("breadth_ma20_pct")
    assert breadth is not None
    assert list(breadth.usage) == [USAGE_TIMING, USAGE_FILTER]
    rows = [
        _meta_row("breadth_ma20_pct", usage=[USAGE_TIMING, USAGE_FILTER]),
        _meta_row("return", usage=["score", "filter", "rank", "timing"]),
    ]
    svc = _make_service(rows)
    config = {
        "score": {"factors": {"breadth_ma20_pct": 1.0}},
        "portfolio": {"method": "equal_weight"},
    }
    result = svc.validate_config(config)
    assert not result.valid
    assert any("不适用于评分位置" in e for e in result.errors)


def test_alias_unknown_template_rejected() -> None:
    """别名指向未注册模板时快速失败。"""
    rows = [_meta_row("return", usage=["score", "filter", "rank", "timing"])]
    svc = _make_service(rows)
    config = {
        "score": {"factors": {"trend_fast": 1.0}},
        "portfolio": {"method": "equal_weight"},
        "factor_aliases": {"trend_fast": {"template_id": "no_such_template"}},
    }
    result = svc.validate_config(config)
    assert not result.valid
    assert any("指向未知模板" in e for e in result.errors)


def test_alias_params_rejected_for_zero_param_template() -> None:
    """零参数模板不接受任何参数覆盖。"""
    rows = [_meta_row("close_price", usage=["score", "filter", "rank", "timing"])]
    svc = _make_service(rows)
    config = {
        "score": {"factors": {"last_close": 1.0}},
        "portfolio": {"method": "equal_weight"},
        "factor_aliases": {"last_close": {"template_id": "close_price", "params": {"period": 5}}},
    }
    result = svc.validate_config(config)
    assert not result.valid
    assert any("参数不合法" in e for e in result.errors)


def test_alias_params_out_of_range_rejected() -> None:
    """参数超出模板声明范围时快速失败。"""
    rows = [_meta_row("sma", usage=["score", "filter", "rank", "timing"])]
    svc = _make_service(rows)
    config = {
        "score": {"factors": {"trend_slow": 1.0}},
        "portfolio": {"method": "equal_weight"},
        "factor_aliases": {"trend_slow": {"template_id": "sma", "params": {"period": 999}}},
    }
    result = svc.validate_config(config)
    assert not result.valid
    assert any("不能大于" in e for e in result.errors)


def test_alias_custom_params_accepted() -> None:
    """合法自定义参数通过校验，同一模板可多次以不同参数出现。"""
    rows = [_meta_row("sma", usage=["score", "filter", "rank", "timing"])]
    svc = _make_service(rows)
    config = {
        "score": {"factors": {"trend_fast": 0.35, "trend_slow": 0.25}},
        "portfolio": {"method": "equal_weight"},
        "factor_aliases": {
            "trend_fast": {"template_id": "sma", "params": {"period": 10}},
            "trend_slow": {"template_id": "sma", "params": {"period": 30}},
        },
    }
    result = svc.validate_config(config)
    assert result.valid, result.errors


def test_alias_declared_but_unused_warns() -> None:
    """已声明但未被引用的别名给出非阻塞提示。"""
    rows = [_meta_row("close_price", usage=["score", "filter", "rank", "timing"])]
    svc = _make_service(rows)
    config = {
        "score": {"factors": {"close_price": 1.0}},
        "portfolio": {"method": "equal_weight"},
        "factor_aliases": {"unused_alias": {"template_id": "close_price"}},
    }
    result = svc.validate_config(config)
    assert result.valid, result.errors
    assert any("已声明但未被任何模块引用" in w for w in result.warnings)


def test_template_parameter_spec_coercion() -> None:
    """ParameterSpec 规范化：整数校验与范围校验。"""
    registry = build_default_registry()
    template = registry.get("sma")
    assert template is not None
    assert template.resolve_params({"period": 10})["period"] == 10
    with pytest.raises(FactorParameterError):
        template.resolve_params({"period": 1.5})
    assert template.resolve_params({"period": 250})["period"] == 250
    with pytest.raises(FactorParameterError):
        template.resolve_params({"period": 251})
    with pytest.raises(FactorParameterError):
        template.resolve_params({"unknown": 1})
