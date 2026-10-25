"""Deflated Sharpe 门槛校准服务：从真实研究记录抽取超额序列并做零假设模拟。

数据来源（均为已落库的成功回测，口径与验收第 8 项一致：系统默认成本下的逐日净收益）：

- 已评估优化会话：全区间基线回测与候选回测的「候选 − 基线」超额；
- 成功的稳健性批次：每个变体按窗口顺序拼接后的「变体 − 基线」超额，同一批次的
  变体构成一组相关候选，用来模拟"从相关候选里挑赢家"。

另附一组合成稀疏序列作为对照，区分"公式本身的偏差"与"真实数据结构带来的偏差"。
本服务只读，不写台账、不改门槛；门槛是否调整由人根据报告决定。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date
from statistics import median
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.config.settings import get_settings
from quant_etf_api.domain.research.dsr_calibration import (
    DEFAULT_INJECTED_SHARPES,
    DEFAULT_THRESHOLDS,
    null_pass_rates,
    power_curve,
    series_profile,
    synthetic_sparse_excess,
)
from quant_etf_api.domain.research.ledger import total_trials
from quant_etf_api.domain.research.robustness import deflated_sharpe_ratio
from quant_etf_api.infra.db.models.core import RobustnessRunModel, StrategyOptimizationModel
from quant_etf_api.infra.db.repositories.backtest import BacktestRepository
from quant_etf_api.infra.db.repositories.research_ledger import ResearchLedgerRepository
from quant_etf_api.services.optimization_service import load_daily_net_series
from quant_etf_api.services.research_ledger_service import ResearchLedgerService

logger = logging.getLogger(__name__)

# 稳健性批次中不作为候选参与挑选的变体类型（与台账 N 的口径一致）
_NON_CANDIDATE_VARIANT_KINDS: frozenset[str] = frozenset({"baseline", "timing_off"})
# 超额序列至少需要的共同交易日数，过短的序列不参与校准
_MIN_COMMON_DAYS = 60


class DsrCalibrationService:
    """Deflated Sharpe 门槛校准（只读）。"""

    def __init__(self, db: Session) -> None:
        """初始化服务。

        Args:
            db: 数据库会话。
        """
        self._db = db
        self._backtest_repo = BacktestRepository(db)
        self._ledger_repo = ResearchLedgerRepository(db)
        self._ledger = ResearchLedgerService(db)
        self._n_cache: dict[str, int] = {}

    def calibrate(
        self,
        strategy_id: str | None = None,
        *,
        n_sims: int = 1000,
        power_sims: int = 500,
        block: int = 20,
        seed: int = 20261024,
        thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
        injected_sharpes: Sequence[float] = DEFAULT_INJECTED_SHARPES,
    ) -> dict[str, Any]:
        """对真实研究记录与合成对照做零假设模拟，输出各门槛的假阳性率与功效。

        Args:
            strategy_id: 只校准该策略所在研究线；None 表示全部研究记录。
            n_sims: 零假设模拟次数。
            power_sims: 功效曲线每个注入值的模拟次数。
            block: 循环块长度（交易日）。
            seed: 随机种子。
            thresholds: 待检验的门槛档位。
            injected_sharpes: 功效曲线注入的年化超额夏普。

        Returns:
            报告字典：current_threshold、sessions、robustness、synthetic、summary。
        """
        members = self._ledger.lineage_scope(strategy_id)["members"] if strategy_id else None
        sessions = [
            item
            for item in (
                self._calibrate_session(
                    row, n_sims, power_sims, block, seed, thresholds, injected_sharpes
                )
                for row in self._ledger_repo.find_evaluated_optimizations(members)
            )
            if item is not None
        ]
        runs = [
            item
            for item in (
                self._calibrate_run(row, n_sims, block, seed, thresholds)
                for row in self._ledger_repo.find_successful_robustness_runs(members)
            )
            if item is not None
        ]
        synthetic = self._synthetic(n_sims, block, seed, thresholds)
        return {
            "current_threshold": get_settings().acceptance_min_deflated_sharpe,
            "scope": {"strategy_id": strategy_id, "lineage_members": members},
            "settings": {
                "n_sims": n_sims,
                "power_sims": power_sims,
                "block": block,
                "seed": seed,
                "cost_bps": get_settings().default_cost_bps,
                "thresholds": list(thresholds),
                "injected_sharpes": list(injected_sharpes),
            },
            "sessions": sessions,
            "robustness": runs,
            "synthetic": synthetic,
            "summary": _summarize(sessions, runs, thresholds, injected_sharpes),
        }

    # ------------------------------------------------------------------
    # 数据来源
    # ------------------------------------------------------------------

    def _lineage_n(self, strategy_id: str) -> int:
        """研究线累计试验数 N（同一研究线只统计一次）。"""
        if strategy_id not in self._n_cache:
            n = total_trials(self._ledger.trial_breakdown(strategy_id))
            for member in self._ledger.lineage_scope(strategy_id)["members"]:
                self._n_cache[member] = n
            self._n_cache[strategy_id] = n
        return self._n_cache[strategy_id]

    def _net(self, backtest_id: str | None) -> dict[date, float]:
        """系统默认成本下的逐日净收益（%）。"""
        return load_daily_net_series(
            self._backtest_repo, backtest_id, get_settings().default_cost_bps
        )

    def _calibrate_session(
        self,
        row: StrategyOptimizationModel,
        n_sims: int,
        power_sims: int,
        block: int,
        seed: int,
        thresholds: Sequence[float],
        injected_sharpes: Sequence[float],
    ) -> dict[str, Any] | None:
        """校准一个优化会话的「候选 − 基线」超额序列。

        Returns:
            会话校准结果；全区间回测缺失或共同交易日过少时返回 None。
        """
        base = self._net(row.baseline_backtest_id)
        cand = self._net(row.candidate_backtest_id)
        common = sorted(set(base) & set(cand))
        if len(common) < _MIN_COMMON_DAYS:
            return None
        excess = [(cand[d] - base[d]) / 100 for d in common]
        n_trials = self._lineage_n(row.strategy_id)
        observed = deflated_sharpe_ratio(excess, n_trials)
        null = null_pass_rates(
            [excess], n_trials, thresholds, n_sims=n_sims, block=block, seed=seed
        )
        null_n1 = null_pass_rates([excess], 1, thresholds, n_sims=n_sims, block=block, seed=seed)
        power = power_curve(
            excess,
            n_trials,
            thresholds,
            injected_sharpes,
            n_sims=power_sims,
            block=block,
            seed=seed,
        )
        return {
            "optimization_id": row.optimization_id,
            "strategy_id": row.strategy_id,
            "status": row.status,
            "profile": series_profile(excess).to_payload(),
            "n_trials": n_trials,
            "observed_dsr": round(observed.deflated_sharpe, 4),
            "null": null.to_payload(),
            "null_n1": null_n1.to_payload(),
            "power": [point.to_payload() for point in power],
        }

    def _calibrate_run(
        self,
        row: RobustnessRunModel,
        n_sims: int,
        block: int,
        seed: int,
        thresholds: Sequence[float],
    ) -> dict[str, Any] | None:
        """校准一个稳健性批次：变体 − 基线的相关超额序列组。

        Returns:
            批次校准结果；基线缺失、候选变体不足 2 个或共同交易日过少时返回 None。
        """
        labels = [w.get("label") for w in (row.windows or [])]
        variants = row.variants or []
        baseline = next((v for v in variants if v.get("kind") == "baseline"), None)
        if baseline is None:
            return None
        base_series = self._joined(baseline, labels)
        candidates: list[tuple[str, dict[date, float]]] = []
        for variant in variants:
            if variant.get("kind") in _NON_CANDIDATE_VARIANT_KINDS:
                continue
            series = self._joined(variant, labels)
            if series:
                candidates.append((str(variant.get("label")), series))
        if len(candidates) < 2 or not base_series:
            return None
        common = set(base_series)
        for _, series in candidates:
            common &= set(series)
        days = sorted(common)
        if len(days) < _MIN_COMMON_DAYS:
            return None
        matrix = [[(series[d] - base_series[d]) / 100 for d in days] for _, series in candidates]
        k = len(matrix)
        observed = [deflated_sharpe_ratio(series, k) for series in matrix]
        best = max(range(k), key=lambda i: observed[i].sharpe_period)
        null = null_pass_rates(matrix, k, thresholds, n_sims=n_sims, block=block, seed=seed)
        lineage_n = self._lineage_n(row.strategy_id)
        null_lineage = null_pass_rates(
            matrix, max(k, lineage_n), thresholds, n_sims=n_sims, block=block, seed=seed
        )
        return {
            "robustness_id": row.robustness_id,
            "strategy_id": row.strategy_id,
            "kind": row.kind,
            "n_candidates": k,
            "common_days": len(days),
            "best_variant": candidates[best][0],
            "best_profile": series_profile(matrix[best]).to_payload(),
            "observed_best_dsr": round(observed[best].deflated_sharpe, 4),
            "null": null.to_payload(),
            "null_lineage_n": null_lineage.to_payload(),
        }

    def _joined(self, variant: dict[str, Any], labels: list[Any]) -> dict[date, float]:
        """按窗口顺序拼接变体各窗口回测的逐日净收益。"""
        joined: dict[date, float] = {}
        backtest_ids = variant.get("backtest_ids") or {}
        for label in labels:
            joined.update(self._net(backtest_ids.get(label)))
        return joined

    # ------------------------------------------------------------------
    # 合成对照
    # ------------------------------------------------------------------

    def _synthetic(
        self, n_sims: int, block: int, seed: int, thresholds: Sequence[float]
    ) -> list[dict[str, Any]]:
        """合成稀疏厚尾序列的零假设通过率（公式本身在稀疏结构下的偏差）。"""
        scenarios: list[dict[str, Any]] = []
        single = synthetic_sparse_excess(seed=seed)
        for n_trials in (1, 50):
            result = null_pass_rates(
                single, n_trials, thresholds, n_sims=n_sims, block=block, seed=seed
            )
            scenarios.append(
                {
                    "scenario": f"单条稀疏序列，N={n_trials}",
                    "profile": series_profile(single[0]).to_payload(),
                    "null": result.to_payload(),
                }
            )
        for correlation in (0.0, 0.7):
            group = synthetic_sparse_excess(n_series=20, correlation=correlation, seed=seed)
            result = null_pass_rates(
                group, len(group), thresholds, n_sims=n_sims, block=block, seed=seed
            )
            scenarios.append(
                {
                    "scenario": f"20 条相关系数 {correlation:g} 的稀疏序列挑最优，N=20",
                    "profile": series_profile(group[0]).to_payload(),
                    "null": result.to_payload(),
                }
            )
        return scenarios


def _summarize(
    sessions: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    thresholds: Sequence[float],
    injected_sharpes: Sequence[float],
) -> dict[str, Any]:
    """汇总真实来源在各门槛下的假阳性率与功效中位数。"""

    def _stats(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, float | None]]:
        out: dict[str, dict[str, float | None]] = {}
        for threshold in thresholds:
            label = f"{threshold:g}"
            rates = [row[key]["pass_rates"][label] for row in rows]
            out[label] = {
                "median": round(median(rates), 4) if rates else None,
                "max": round(max(rates), 4) if rates else None,
            }
        return out

    power: dict[str, dict[str, float | None]] = {}
    for threshold in thresholds:
        label = f"{threshold:g}"
        per_target: dict[str, float | None] = {}
        for target in injected_sharpes:
            values = [
                point["pass_rates"][label]
                for row in sessions
                for point in row["power"]
                if point["injected_sharpe"] == target
            ]
            per_target[f"{target:g}"] = round(median(values), 4) if values else None
        power[label] = per_target
    return {
        "n_sessions": len(sessions),
        "n_robustness_runs": len(runs),
        "session_fpr_lineage_n": _stats(sessions, "null"),
        "session_fpr_n1": _stats(sessions, "null_n1"),
        "robustness_fpr_batch_n": _stats(runs, "null"),
        "robustness_fpr_lineage_n": _stats(runs, "null_lineage_n"),
        "session_power_median": power,
    }


def render_markdown(report: dict[str, Any]) -> str:
    """把校准报告渲染为 markdown。

    Args:
        report: ``DsrCalibrationService.calibrate`` 的返回值。

    Returns:
        markdown 文本。
    """
    thresholds = [f"{t:g}" for t in report["settings"]["thresholds"]]
    summary = report["summary"]
    lines = [
        "# Deflated Sharpe 门槛校准报告",
        "",
        f"- 当前门槛：{report['current_threshold']:g}",
        f"- 范围：{report['scope']['strategy_id'] or '全部研究记录'}",
        f"- 模拟：零假设 {report['settings']['n_sims']} 次、功效每点 "
        f"{report['settings']['power_sims']} 次、块长 {report['settings']['block']}、"
        f"种子 {report['settings']['seed']}、成本 {report['settings']['cost_bps']:g}bp",
        f"- 来源：优化会话 {summary['n_sessions']} 个、稳健性批次 "
        f"{summary['n_robustness_runs']} 个",
        "",
        "假阳性率 = 真实改进为零时仍通过门槛的比例；名义上门槛 t 对应 1 − t。",
        "",
        "## 汇总：假阳性率（中位数 / 最大值）",
        "",
        "| 来源 | " + " | ".join(thresholds) + " |",
        "|---|" + "---|" * len(thresholds),
    ]
    rows = [
        ("优化会话（N=研究线）", "session_fpr_lineage_n"),
        ("优化会话（N=1，仅检验公式）", "session_fpr_n1"),
        ("稳健性批次挑最优（N=批次变体数）", "robustness_fpr_batch_n"),
        ("稳健性批次挑最优（N=研究线）", "robustness_fpr_lineage_n"),
    ]
    for title, key in rows:
        cells = [_pct_pair(summary[key][t]["median"], summary[key][t]["max"]) for t in thresholds]
        lines.append(f"| {title} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## 汇总：功效中位数（优化会话，N=研究线）",
        "",
        "| 注入年化超额夏普 | " + " | ".join(thresholds) + " |",
        "|---|" + "---|" * len(thresholds),
    ]
    for target in report["settings"]["injected_sharpes"]:
        label = f"{target:g}"
        cells = [_pct(summary["session_power_median"][t][label]) for t in thresholds]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## 合成对照",
        "",
        "| 场景 | " + " | ".join(thresholds) + " |",
        "|---|" + "---|" * len(thresholds),
    ]
    for item in report["synthetic"]:
        cells = [_pct(item["null"]["pass_rates"][t]) for t in thresholds]
        lines.append(f"| {item['scenario']} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## 优化会话明细",
        "",
        "| 会话 | 状态 | 差异日/总日 | 峰度 | 超额夏普 | N | 实测 DSR | 0.95 假阳性 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in report["sessions"]:
        profile = row["profile"]
        lines.append(
            f"| {row['optimization_id']} | {row['status']} | "
            f"{profile['active_days']}/{profile['n_observations']} | {profile['kurtosis']:.1f} | "
            f"{profile['sharpe_annualized']:.2f} | {row['n_trials']} | {row['observed_dsr']:.3f} | "
            f"{_pct(row['null']['pass_rates'].get('0.95'))} |"
        )
    lines += [
        "",
        "## 稳健性批次明细",
        "",
        "| 批次 | 类型 | 候选数 | 最优变体 | 最优 DSR | 0.95 假阳性（N=批次） |",
        "|---|---|---|---|---|---|",
    ]
    for row in report["robustness"]:
        lines.append(
            f"| {row['robustness_id']} | {row['kind']} | {row['n_candidates']} | "
            f"{row['best_variant']} | {row['observed_best_dsr']:.3f} | "
            f"{_pct(row['null']['pass_rates'].get('0.95'))} |"
        )
    lines.append("")
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    """百分比展示（缺失为 -）。"""
    return "-" if value is None else f"{value * 100:.1f}%"


def _pct_pair(mid: float | None, top: float | None) -> str:
    """「中位数 / 最大值」百分比展示。"""
    return f"{_pct(mid)} / {_pct(top)}"
