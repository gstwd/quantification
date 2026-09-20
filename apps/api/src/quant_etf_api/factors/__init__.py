"""因子层公开接口。"""

from __future__ import annotations

from quant_etf_api.factors.base import FactorComputer, FactorContext, FactorSpec, FactorValue
from quant_etf_api.factors.catalog import (
    FactorInstance,
    FactorResolutionError,
    FactorTemplateRegistry,
    build_default_templates,
    get_factor_template_registry,
)
from quant_etf_api.factors.templates import (
    FactorParameterError,
    FactorTemplate,
    ParameterSpec,
)

__all__ = [
    "FactorComputer",
    "FactorContext",
    "FactorInstance",
    "FactorParameterError",
    "FactorResolutionError",
    "FactorSpec",
    "FactorTemplate",
    "FactorTemplateRegistry",
    "FactorValue",
    "ParameterSpec",
    "build_default_templates",
    "get_factor_template_registry",
]
