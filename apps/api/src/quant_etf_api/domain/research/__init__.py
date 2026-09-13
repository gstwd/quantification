"""研究评估领域规则。"""

from __future__ import annotations

from quant_etf_api.domain.research.lifecycle import (
    HealthAssessment,
    assess_health,
    build_baseline_distribution,
    evaluate_against_baseline,
)
from quant_etf_api.domain.research.metrics import (
    AnnualPerformanceRow,
    PerformanceMetrics,
    RollingMetrics,
    compute_annual_breakdown,
    compute_performance_metrics,
    compute_rolling_metrics,
)
from quant_etf_api.domain.research.robustness import (
    BootstrapCiResult,
    DeflatedSharpeResult,
    NeighborhoodSummary,
    PboResult,
    annualized_sharpe,
    block_bootstrap_sharpe_ci,
    compute_pbo,
    deflated_sharpe_ratio,
    summarize_neighborhood,
)
from quant_etf_api.domain.research.stability import (
    StabilityMetrics,
    compute_stability_metrics,
    net_return_series,
)

__all__ = [
    "AnnualPerformanceRow",
    "BootstrapCiResult",
    "DeflatedSharpeResult",
    "HealthAssessment",
    "NeighborhoodSummary",
    "PerformanceMetrics",
    "PboResult",
    "RollingMetrics",
    "StabilityMetrics",
    "annualized_sharpe",
    "assess_health",
    "block_bootstrap_sharpe_ci",
    "compute_annual_breakdown",
    "compute_pbo",
    "compute_performance_metrics",
    "compute_rolling_metrics",
    "compute_stability_metrics",
    "build_baseline_distribution",
    "deflated_sharpe_ratio",
    "evaluate_against_baseline",
    "net_return_series",
    "summarize_neighborhood",
]
