"""绩效指标兼容模块。

绩效计算已下沉到 :mod:`quant_etf_api.domain.research.metrics`；本模块只
保留历史导入路径转发，服务层新代码应直接依赖 domain。
"""

from quant_etf_api.domain.research import metrics as _metrics

AnnualPerformanceRow = _metrics.AnnualPerformanceRow
PerformanceMetrics = _metrics.PerformanceMetrics
RollingMetrics = _metrics.RollingMetrics
_calc_alpha_beta = _metrics._calc_alpha_beta
_calc_annualized_return = _metrics._calc_annualized_return
_calc_information_ratio = _metrics._calc_information_ratio
_calc_max_consecutive_loss_days = _metrics._calc_max_consecutive_loss_days
_calc_max_drawdown_details = _metrics._calc_max_drawdown_details
_calc_profit_loss_ratio = _metrics._calc_profit_loss_ratio
_calc_sharpe_ratio = _metrics._calc_sharpe_ratio
_calc_sortino_ratio = _metrics._calc_sortino_ratio
_calc_total_return = _metrics._calc_total_return
_calc_var_cvar = _metrics._calc_var_cvar
_calc_win_rate = _metrics._calc_win_rate
_daily_risk_free_pct = _metrics._daily_risk_free_pct
compute_annual_breakdown = _metrics.compute_annual_breakdown
compute_performance_metrics = _metrics.compute_performance_metrics
compute_rolling_metrics = _metrics.compute_rolling_metrics

__all__ = [
    "AnnualPerformanceRow",
    "PerformanceMetrics",
    "RollingMetrics",
    "compute_annual_breakdown",
    "compute_performance_metrics",
    "compute_rolling_metrics",
]
