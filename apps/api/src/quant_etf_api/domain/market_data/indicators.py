"""日频市场指标的代码表（纯常量，数据管理、客户端、因子模板共用）。

指标代码 → (中文名, 单位, 分组)。分组决定由哪个上游接口拉取：

- ``cgb10y``：中国 10 年期国债收益率（AkShare，Tushare yc_cb 需要单独权限）；
- ``margin``：沪深两市融资融券汇总（Tushare margin，金额换算为亿元）；
- ``futures``：中金所股指期货主力连续合约收盘与对应主力合约剩余自然日（Tushare
  fut_daily 连续代码 + fut_mapping + fut_basic）；
- ``options``：中金所股指期权逐日认沽 / 认购成交量与持仓量之比（Tushare opt_daily + opt_basic）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: 股指期货品种 → (Tushare 连续代码, 对应现货指数)
FUTURES_PRODUCTS: dict[str, tuple[str, str]] = {
    "if": ("IF.CFX", "000300"),
    "ih": ("IH.CFX", "000016"),
    "ic": ("IC.CFX", "000905"),
    "im": ("IM.CFX", "000852"),
}

#: 股指期权品种 → Tushare opt_basic.opt_code（标的指数）
OPTION_PRODUCTS: dict[str, str] = {
    "io": "OP000300.SH",
    "mo": "OP000852.SH",
    "ho": "OP000016.SH",
}

#: 指标代码 → (中文名, 单位, 分组)
MARKET_INDICATORS: dict[str, tuple[str, str, str]] = {
    "cgb10y": ("中国10年期国债收益率", "%", "cgb10y"),
    "margin_rzye": ("沪深融资余额", "亿元", "margin"),
    "margin_rzmre": ("沪深融资买入额", "亿元", "margin"),
    "margin_rqye": ("沪深融券余额", "亿元", "margin"),
    **{
        f"fut_{product}_close": (f"{product.upper()} 主力连续收盘", "点", "futures")
        for product in FUTURES_PRODUCTS
    },
    **{
        f"fut_{product}_dte": (f"{product.upper()} 主力合约剩余自然日", "天", "futures")
        for product in FUTURES_PRODUCTS
    },
    **{
        f"fut_{product}_basis": (f"{product.upper()} 基差（期货/现货 − 1）", "%", "futures")
        for product in FUTURES_PRODUCTS
    },
    **{
        f"fut_{product}_basis_ann": (f"{product.upper()} 年化基差", "%", "futures")
        for product in FUTURES_PRODUCTS
    },
    **{
        f"opt_{product}_pcr_vol": (f"{product.upper()} 期权认沽认购成交量比", "倍", "options")
        for product in OPTION_PRODUCTS
    },
    **{
        f"opt_{product}_pcr_oi": (f"{product.upper()} 期权认沽认购持仓量比", "倍", "options")
        for product in OPTION_PRODUCTS
    },
}

#: 分组 → 历史起点（回补 / 重建时从这里拉起；早于上市的部分上游返回空）
GROUP_EPOCH: dict[str, date] = {
    "cgb10y": date(2013, 1, 1),
    "margin": date(2013, 1, 1),
    "futures": date(2015, 4, 16),
    "options": date(2019, 12, 23),
}


@dataclass(frozen=True)
class MarketIndicatorPoint:
    """一条日频市场指标观测。

    Attributes:
        indicator_code: 指标代码（见 ``MARKET_INDICATORS``）。
        trade_date: 交易日。
        value: 数值。
        source: 数据来源。
    """

    indicator_code: str
    trade_date: date
    value: float
    source: str


def codes_of_group(group: str) -> list[str]:
    """返回某个分组下的全部指标代码。

    Args:
        group: 分组名。

    Returns:
        指标代码列表（按定义顺序）。
    """
    return [code for code, (_name, _unit, g) in MARKET_INDICATORS.items() if g == group]


#: 宏观指标代码（macro_indicator 表，按发布周期入库）：CPI / PMI / LPR / 货币供应 / 社融存量
MACRO_INDICATOR_CODES: tuple[str, ...] = (
    "cpi",
    "pmi",
    "lpr1y",
    "lpr5y",
    "m1_yoy",
    "m2_yoy",
    "sf_stock",
)
