"""申万行业域因子元数据（RRG / 扩散，供因子中心统一登记）。

行业因子与指数因子的区别：
- asset_domain=industry：值作用于申万一级行业资产域（industry_universe）；
- value_shape=panel：值依赖行业集合/基准剔除与参数，按面板组织；
- usage=[rotation_input]：本轮只作为轮动模块（RotationConfig）的输入，
  不开放指数通用 score/filter/rank 消费。

这些定义只描述元数据；实际计算仍由 domain/industry 下的纯算法与
IndustryFactorService 完成，不注册进指数 FactorRegistry。
"""

from __future__ import annotations

from quant_etf_api.domain.industry.constants import canonical_industry_params
from quant_etf_api.factors.base import (
    ASSET_DOMAIN_INDUSTRY,
    USAGE_ROTATION_INPUT,
    VALUE_SHAPE_PANEL,
    FactorSpec,
)


def _default_params() -> dict:
    """返回行业因子默认参数字典（研报复刻口径）。"""
    return canonical_industry_params()


# 四个行业因子统一元数据基础（默认参数相同，因计算各自使用相关子集）。
_INDUSTRY_SPEC_BASE = {
    "asset_domain": ASSET_DOMAIN_INDUSTRY,
    "value_shape": VALUE_SHAPE_PANEL,
    "usage": [USAGE_ROTATION_INPUT],
    "default_params": _default_params(),
}


INDUSTRY_FACTOR_SPECS: list[FactorSpec] = [
    FactorSpec(
        factor_id="rrg_rs_ratio",
        name="RRG RS-Ratio 相对强度",
        category="relative_strength",
        version="1.0.0",
        description=(
            "RS-Ratio = 行业收盘相对行业等权基准的比率再按平滑窗口取 MA，"
            "衡量行业相对强度（研报 JdK 口径，中枢 100）。行业域面板因子。"
        ),
        required_data=["industry_bars"],
        lookback_days=520,
        **_INDUSTRY_SPEC_BASE,
    ),
    FactorSpec(
        factor_id="rrg_rs_momentum",
        name="RRG RS-Momentum 相对强度动量",
        category="relative_strength",
        version="1.0.0",
        description=(
            "RS-Momentum = RS-Ratio 的变化率按平滑窗口取 MA，"
            "与 RS-Ratio 共同构成 RRG 四象限。行业域面板因子。"
        ),
        required_data=["industry_bars"],
        lookback_days=600,
        **_INDUSTRY_SPEC_BASE,
    ),
    FactorSpec(
        factor_id="rrg_quadrant",
        name="RRG 象限",
        category="relative_strength",
        version="1.0.0",
        description=(
            "由 RS-Ratio 与 RS-Momentum 相对 100 中枢判定象限："
            "1=领先、2=改善、3=滞后、4=疲软。文本枚举型行业面板因子。"
        ),
        required_data=["industry_bars"],
        lookback_days=600,
        **_INDUSTRY_SPEC_BASE,
    ),
    FactorSpec(
        factor_id="diffusion_count_ratio",
        name="行业扩散指标（数量占比）",
        category="breadth",
        version="1.0.0",
        description=(
            "行业内上涨（close_t > close_{t-lookback}）个股数量占当日有效样本"
            "比例并按平滑窗口取 MA。依赖成分事件与个股收盘，行业域面板因子。"
        ),
        required_data=["industry_membership_event", "stock_daily_close"],
        lookback_days=420,
        **_INDUSTRY_SPEC_BASE,
    ),
]


def get_industry_factor_specs() -> list[FactorSpec]:
    """返回行业因子元数据列表（每次新建，避免默认参数字典被共享修改）。"""
    return [FactorSpec(**vars(spec)) for spec in INDUSTRY_FACTOR_SPECS]
