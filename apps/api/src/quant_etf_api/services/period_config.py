"""研究期 / 验证期边界的配置装配。

``domain.research.periods`` 只承载纯规则、不读取配置；各服务统一从这里取
边界，避免多份读取逻辑在配置调整时出现漂移。
"""

from __future__ import annotations

from datetime import date

from quant_etf_api.config.settings import get_settings
from quant_etf_api.domain.research.periods import PeriodBoundaries


def load_period_boundaries() -> PeriodBoundaries:
    """从系统配置读取研究期/验证期边界。

    Returns:
        PeriodBoundaries 实例（研究期 2016-01-01 ~ 2025-12-31，验证期自 2026-01-01 起）。
    """
    settings = get_settings()
    return PeriodBoundaries(
        research_start=date.fromisoformat(settings.research_period_start),
        research_end=date.fromisoformat(settings.research_period_end),
        validation_start=date.fromisoformat(settings.validation_period_start),
    )
