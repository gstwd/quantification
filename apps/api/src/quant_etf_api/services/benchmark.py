"""基准收益计算兼容模块。

实现已下沉到 :mod:`quant_etf_api.domain.portfolio.benchmark`；保留本模块的
转发是为了兼容历史导入路径，服务层和新代码应直接依赖 domain。
"""

from quant_etf_api.domain.portfolio.benchmark import (
    compute_buy_hold_benchmark,
    compute_equal_weight_benchmark,
)

__all__ = ["compute_buy_hold_benchmark", "compute_equal_weight_benchmark"]
