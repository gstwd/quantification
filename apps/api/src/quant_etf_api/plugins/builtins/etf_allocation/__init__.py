"""ETF 资产配置策略插件。

综合择时、资产轮动、仓位管理的完整投资决策管线。
"""

from quant_etf_api.plugins.builtins.etf_allocation.plugin import EtfAllocationPlugin

__all__ = ["EtfAllocationPlugin"]
