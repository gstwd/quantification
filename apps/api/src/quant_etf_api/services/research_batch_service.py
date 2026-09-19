"""研究批量评估：同一批变体的"秒级"离线评估路径（D-1）。

为什么需要这个服务：一次探索性诊断（消融 + 池扰动 + 参数邻域 + 简化）动辄
几十个变体 × 若干窗口，走平台回测意味着几百条落库回测（队列排期 + 逐日结果
与逐指数结果落库），实测数小时；本轮只能在工作目录里复刻引擎才把成本压到
秒级，而"复刻引擎"本身有新的一份口径风险。

本服务提供官方路径：**复用与平台完全相同的执行路径**
（``BacktestService._run_backtest_loop(persist=False)``），只关掉结果落库，
并按窗口共享行情快照与因子预计算。因此：

- 指标口径与平台回测一致（同一条代码路径，不存在"两套结果"问题）；
- 输出中固定带口径指纹（执行模型 / 数据质量 / 基准 / 日历来源 / 换手口径 / 成本），
  避免不同口径的数字被相互比较；
- **它不是验收凭证**：不落库即不可审计，验收仍必须用 ``backtest run``。

变体既可以直接给完整 ``config``，也可以给 ``patch``（点号 / 下标路径 → 取值），
后者让探索文件保持"只写改了哪几个参数"的可读形态。
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.config.settings import get_settings
from quant_etf_api.domain.common.enums import DEFAULT_EXECUTION_MODEL, ExecutionModel
from quant_etf_api.domain.portfolio.turnover import TURNOVER_MODEL_DELTA_W
from quant_etf_api.domain.research.periods import PeriodBoundaries
from quant_etf_api.engine.config import StrategyConfig
from quant_etf_api.infra.db.models.core import BacktestRunModel
from quant_etf_api.services.backtest_service import BacktestRunCaches, BacktestService
from quant_etf_api.services.robustness_service import _set_leaf
from quant_etf_api.services.strategy_config_service import StrategyConfigService

logger = logging.getLogger(__name__)

# 变体标签与配置二选一；两个都给时以 patch 在 config 之后生效的顺序应用
_VARIANT_KEYS = ("label", "config", "patch", "kind")


def _fmt_number(value: Any, digits: int = 3) -> str:
    """格式化摘要表里的数值，缺失显示 '-'。

    Args:
        value: 待格式化的数值（可能为 None）。
        digits: 小数位数。

    Returns:
        格式化字符串。
    """
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


@dataclass(frozen=True)
class ResearchVariant:
    """研究批量评估中的一个变体。

    Attributes:
        label: 变体标签（用于结果与差异表）。
        config: 完整策略配置 JSON。
        kind: 变体类型（baseline/knob/ablation/patch/manual），仅用于展示。
    """

    label: str
    config: dict[str, Any]
    kind: str = "manual"


@dataclass
class ResearchBatchResult:
    """研究批量评估的结果集合。

    Attributes:
        windows: 评估窗口列表，元素为 {label, start, end}。
        variants: 每个变体的逐窗口指标、聚合指标与口径指纹。
        baseline_label: 作为对照的变体标签。
        cost_bps: 净口径成本（基点）。
    """

    windows: list[dict[str, str]] = field(default_factory=list)
    variants: list[dict[str, Any]] = field(default_factory=list)
    baseline_label: str | None = None
    cost_bps: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """转换为可直接序列化的字典（CLI JSON 输出）。"""
        return {
            "windows": self.windows,
            "baseline_label": self.baseline_label,
            "cost_bps": self.cost_bps,
            "variants": self.variants,
        }

    def to_summary_table(self) -> str:
        """输出"变体 × 指标"排名表（F-9）。

        几十个变体的完整 JSON 动辄几万 token，且没有排序视图，人和 agent
        都难以直接回答"到底哪个变体更好"；这里只保留决策需要的列。

        Returns:
            对齐后的纯文本表格（含窗口口径、成本口径与逐窗口 Δ）。
        """
        lines = [
            "windows: "
            + " | ".join(
                f"{w.get('label')} {w.get('start')}~{w.get('end')}" for w in self.windows
            ),
            f"cost_bps={self.cost_bps}（aggregate 为窗口拼接口径，"
            "不可与整段回测直接比较）",
        ]
        header = (
            f"{'variant':<26}{'grossAnn':>9}{'grossShp':>9}{'netAnn':>8}{'netShp':>8}"
            f"{'turn':>7}{'maxDD':>8}{'dNetAnn':>9}{'dNetShp':>9}{'worse':>7}  perWindow(dNetShp)"
        )
        lines.append(header)
        lines.append("-" * len(header))
        for variant in self.variants:
            aggregate = variant.get("aggregate") or {}
            metrics = aggregate.get("metrics") or {}
            net = aggregate.get("net_metrics") or {}
            delta = variant.get("vs_baseline") or {}
            per_window = " ".join(
                f"{item.get('label')}:{_fmt_number(item.get('delta_net_sharpe'))}"
                for item in (delta.get("per_window") or [])
            )
            lines.append(
                f"{str(variant.get('label')):<26}"
                f"{_fmt_number(metrics.get('annualized_return_pct'), 2):>9}"
                f"{_fmt_number(metrics.get('sharpe_ratio')):>9}"
                f"{_fmt_number(net.get('net_annualized_return_pct'), 2):>8}"
                f"{_fmt_number(net.get('net_sharpe_ratio')):>8}"
                f"{_fmt_number(net.get('annualized_turnover'), 2):>7}"
                f"{_fmt_number(metrics.get('max_drawdown_pct'), 2):>8}"
                f"{_fmt_number(delta.get('delta_net_annualized_return_pct'), 2):>9}"
                f"{_fmt_number(delta.get('delta_net_sharpe_ratio')):>9}"
                f"{_fmt_number(delta.get('worse_window_ratio'), 2):>7}  {per_window}"
            )
        return "\n".join(lines)


class ResearchBatchService:
    """批量变体的离线评估服务（不落库，D-1）。"""

    def __init__(self, db: Session) -> None:
        """初始化服务。

        Args:
            db: SQLAlchemy 同步 Session（只读行情与策略配置）。
        """
        self._db = db
        self._config_svc = StrategyConfigService(db)
        self._backtest_svc = BacktestService(db)

    # ── 变体解析 ──────────────────────────────────────────────────────────

    def baseline_config(self, strategy_id: str) -> dict[str, Any]:
        """返回基线策略的配置 JSON（patch 变体的应用对象）。

        Args:
            strategy_id: 基线策略 ID。

        Returns:
            基线配置 JSON。

        Raises:
            ValueError: 策略不存在时抛出。
        """
        detail = self._config_svc.get_config(strategy_id)
        if detail is None:
            raise ValueError(f"基线策略 {strategy_id} 不存在")
        return _deep_copy_config(detail.config_json)

    @staticmethod
    def parse_variants(baseline_config: dict[str, Any], payload: Any) -> list[ResearchVariant]:
        """解析变体文件内容为变体列表。

        支持两种写法：

        - ``{"variants": [...]}`` 或直接给列表；
        - 每个变体给完整 ``config``，或给 ``patch``（路径 → 取值，路径支持
          ``filters.rules[2].value`` 形式的列表下标），或两者都给（patch 后应用）。

        Args:
            baseline_config: 基线策略配置（patch 的应用对象）。
            payload: 解析后的 JSON（字典或列表）。

        Returns:
            变体列表。

        Raises:
            ValueError: 结构非法、缺 label、既无 config 也无 patch，
                或 patch 路径不存在时抛出。
        """
        if isinstance(payload, dict):
            raw_variants = payload.get("variants")
        else:
            raw_variants = payload
        if not isinstance(raw_variants, list) or not raw_variants:
            raise ValueError("变体文件必须是非空列表，或含非空 variants 列表的对象")

        variants: list[ResearchVariant] = []
        seen: set[str] = set()
        for index, item in enumerate(raw_variants):
            if not isinstance(item, dict):
                raise ValueError(f"第 {index + 1} 个变体不是对象")
            unknown = [key for key in item if key not in _VARIANT_KEYS]
            if unknown:
                raise ValueError(
                    f"第 {index + 1} 个变体包含未知字段：{', '.join(unknown)}；"
                    f"可用字段：{', '.join(_VARIANT_KEYS)}"
                )
            label = item.get("label")
            if not label or not isinstance(label, str):
                raise ValueError(f"第 {index + 1} 个变体缺少 label")
            if label in seen:
                raise ValueError(f"变体标签重复：{label}")
            seen.add(label)

            config = item.get("config")
            patch = item.get("patch")
            if config is None and not patch:
                raise ValueError(f"变体 {label} 既没有 config 也没有 patch")
            resolved = _deep_copy_config(
                config if isinstance(config, dict) else baseline_config
            )
            if patch:
                if not isinstance(patch, dict):
                    raise ValueError(f"变体 {label} 的 patch 必须是对象")
                for path, value in patch.items():
                    try:
                        _set_leaf(resolved, path, value)
                    except (KeyError, TypeError, IndexError) as exc:
                        raise ValueError(
                            f"变体 {label} 的 patch 路径 {path} 无法写入：{exc}"
                        ) from exc
            variants.append(
                ResearchVariant(
                    label=str(label),
                    config=resolved,
                    kind=str(item.get("kind") or ("patch" if patch else "manual")),
                )
            )
        return variants

    # ── 执行 ──────────────────────────────────────────────────────────────

    def run(
        self,
        strategy_id: str,
        variants: list[ResearchVariant],
        *,
        boundaries: PeriodBoundaries | None = None,
        windows: int = 5,
        cost_bps: float | None = None,
        cost_ladder: list[float] | None = None,
        include_baseline: bool = True,
        execution_model: ExecutionModel = DEFAULT_EXECUTION_MODEL,
    ) -> ResearchBatchResult:
        """在研究期窗口上评估全部变体（不落库）。

        Args:
            strategy_id: 基线策略 ID（提供默认配置与标的范围）。
            variants: 变体列表。
            boundaries: 研究期/验证期边界（决定默认区间）。
            windows: 研究期内切分的窗口数（默认 5 段，每段 2 年）。
            cost_bps: 净口径成本覆盖（基点），None 时取系统默认值。
            cost_ladder: 多档成本并列的档位覆盖。
            include_baseline: 是否把基线配置作为第一个对照变体一起评估。
            execution_model: 回测执行模型（探索与正式验收必须同口径）。

        Returns:
            ResearchBatchResult。

        Raises:
            ValueError: 基线策略不存在、窗口无法切分或变体配置无法解析时抛出。
        """
        boundaries = boundaries or period_boundaries()
        baseline_detail = self._config_svc.get_config(strategy_id)
        if baseline_detail is None:
            raise ValueError(f"基线策略 {strategy_id} 不存在")
        baseline_config = _deep_copy_config(baseline_detail.config_json)

        evaluated: list[ResearchVariant] = []
        if include_baseline:
            evaluated.append(ResearchVariant(label="baseline", config=baseline_config, kind="baseline"))
        evaluated.extend(variants)

        windows_list = self._build_windows(boundaries, windows)
        effective_cost = (
            float(cost_bps) if cost_bps is not None else float(get_settings().default_cost_bps)
        )
        ladder = (
            [float(item) for item in cost_ladder]
            if cost_ladder is not None
            else [float(item) for item in get_settings().stability_cost_ladder]
        )

        caches = BacktestRunCaches()
        results: list[dict[str, Any]] = []
        baseline_aggregate: dict[str, Any] | None = None
        for variant in evaluated:
            config = self._parse_config(strategy_id, baseline_detail, variant)
            per_window: list[dict[str, Any]] = []
            all_daily_rows: list[Any] = []
            params: dict[str, Any] = {}
            for window in windows_list:
                row = self._build_row(
                    strategy_id=strategy_id,
                    config=config,
                    window=window,
                    cost_bps=effective_cost,
                    execution_model=execution_model,
                )
                params = dict(row.params or {})
                outcome = self._backtest_svc._run_backtest_loop(  # noqa: SLF001
                    f"research:{strategy_id}:{variant.label}:{window['label']}",
                    row,
                    config,
                    persist=False,
                    caches=caches,
                )
                daily_rows = outcome["daily_rows"]
                all_daily_rows.extend(daily_rows)
                stability = self._backtest_svc._compute_stability(  # noqa: SLF001
                    row, daily_rows, cost_bps=effective_cost, cost_ladder=ladder
                )
                per_window.append(
                    {
                        "label": window["label"],
                        "start": window["start"],
                        "end": window["end"],
                        "metrics": _metrics_digest(outcome["metrics"]),
                        "net_metrics": _net_digest(stability),
                    }
                )
            aggregate_row = self._aggregate_row(strategy_id, all_daily_rows, params)
            aggregate_stability = self._backtest_svc._compute_stability(  # noqa: SLF001
                aggregate_row, all_daily_rows, cost_bps=effective_cost, cost_ladder=ladder
            )
            aggregate = {
                "metrics": _metrics_digest(
                    self._backtest_svc._compute_summary_metrics(  # noqa: SLF001
                        _accumulator_from_rows(all_daily_rows),
                        _benchmark_series(all_daily_rows),
                    )
                ),
                "net_metrics": _net_digest(aggregate_stability),
                "trading_days": len(all_daily_rows),
            }
            entry: dict[str, Any] = {
                "label": variant.label,
                "kind": variant.kind,
                "strategy_id": strategy_id,
                "windows": per_window,
                "aggregate": aggregate,
                "caliber": _caliber_digest(params, effective_cost, windows_list),
            }
            if variant.kind == "baseline":
                baseline_aggregate = aggregate
                entry["vs_baseline"] = _delta_block(aggregate, aggregate, per_window, per_window)
            elif baseline_aggregate is not None:
                baseline_windows = next(
                    (item["windows"] for item in results if item["kind"] == "baseline"), []
                )
                entry["vs_baseline"] = _delta_block(
                    baseline_aggregate, aggregate, baseline_windows, per_window
                )
            results.append(entry)

        return ResearchBatchResult(
            windows=windows_list,
            variants=results,
            baseline_label="baseline" if include_baseline else None,
            cost_bps=effective_cost,
        )

    # ── 内部辅助 ──────────────────────────────────────────────────────────

    def _parse_config(
        self, strategy_id: str, baseline_detail: Any, variant: ResearchVariant
    ) -> StrategyConfig:
        """把变体配置解析为 StrategyConfig（解析失败直接报错，不静默跳过）。

        Args:
            strategy_id: 基线策略 ID（补入元数据字段）。
            baseline_detail: 基线策略详情（提供默认元数据）。
            variant: 变体。

        Returns:
            StrategyConfig。

        Raises:
            ValueError: 配置解析失败时抛出（含变体标签，便于定位）。
        """
        full_config = {
            "strategy_id": strategy_id,
            "display_name": baseline_detail.display_name,
            "version": baseline_detail.version,
            "description": baseline_detail.description or "",
            "frequency": baseline_detail.frequency,
            **_deep_copy_config(variant.config),
        }
        try:
            return StrategyConfig(**full_config)
        except Exception as exc:
            raise ValueError(f"变体 {variant.label} 的配置无法解析：{exc}") from exc

    def _build_windows(
        self, boundaries: PeriodBoundaries, windows: int
    ) -> list[dict[str, str]]:
        """把研究期切分为等长窗口。

        Args:
            boundaries: 研究期/验证期边界。
            windows: 窗口数（至少 1）。

        Returns:
            窗口列表，元素为 {label, start, end}（ISO 日期字符串）。
        """
        from quant_etf_api.domain.research.walk_forward import compute_folds

        trade_dates = self._backtest_svc._index_bar_repo.find_all_trading_dates(  # noqa: SLF001
            boundaries.research_start, boundaries.research_end
        )
        if not trade_dates:
            raise ValueError("研究期内无行情数据，无法切分评估窗口")
        folds = compute_folds(trade_dates, max(1, windows))
        return [
            {"label": f"W{i}", "start": fs.isoformat(), "end": fe.isoformat()}
            for i, (fs, fe) in enumerate(folds)
        ]

    def _build_row(
        self,
        *,
        strategy_id: str,
        config: StrategyConfig,
        window: dict[str, str],
        cost_bps: float,
        execution_model: ExecutionModel = DEFAULT_EXECUTION_MODEL,
    ) -> BacktestRunModel:
        """构造未加入 session 的临时回测行（与 ``create_backtest`` 的同名字段一致）。

        Args:
            strategy_id: 策略 ID。
            config: 策略配置（用于确定标的范围）。
            window: 评估窗口。
            cost_bps: 净口径成本（基点）。
            execution_model: 回测执行模型（探索与验收必须同口径，否则不可比较）。

        Returns:
            临时 ``BacktestRunModel``（不落库，也不进入 session）。
        """
        universe_filter = (
            {"mode": "subset", "index_codes": list(config.index_codes)}
            if config.index_codes
            else {"mode": "all"}
        )
        return BacktestRunModel(
            backtest_id=f"research:{strategy_id}:{window['label']}",
            strategy_id=strategy_id,
            start_date=date.fromisoformat(window["start"]),
            end_date=date.fromisoformat(window["end"]),
            universe_filter=universe_filter,
            params={
                "_execution_model": execution_model,
                "_data_quality_mode": "warn",
                "_cost_bps": cost_bps,
                "_enable_benchmark": True,
                "_benchmark_index_code": "000300",
                "_turnover_model": TURNOVER_MODEL_DELTA_W,
            },
            status="pending",
        )

    def _aggregate_row(
        self, strategy_id: str, daily_rows: list[Any], params: dict[str, Any]
    ) -> BacktestRunModel:
        """构造聚合口径的临时行（窗口拼接后的净口径与稳健性指标）。

        Args:
            strategy_id: 策略 ID。
            daily_rows: 全部窗口的逐日结果。
            params: 口径参数（取自单个窗口，各窗口一致）。

        Returns:
            临时 ``BacktestRunModel``。
        """
        dates = [row.trade_date for row in daily_rows]
        return BacktestRunModel(
            backtest_id=f"research:{strategy_id}:aggregate",
            strategy_id=strategy_id,
            start_date=min(dates) if dates else date.today(),
            end_date=max(dates) if dates else date.today(),
            universe_filter={"mode": "all"},
            params=dict(params),
            status="pending",
        )


def _deep_copy_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """深拷贝配置字典（避免变体派生就地改写污染基线）。"""
    return deepcopy(dict(config or {}))


def period_boundaries() -> PeriodBoundaries:
    """从系统配置构造研究期/验证期边界（与回测、稳健性批次口径一致）。

    Returns:
        PeriodBoundaries。
    """
    settings = get_settings()
    return PeriodBoundaries(
        research_start=date.fromisoformat(settings.research_period_start),
        research_end=date.fromisoformat(settings.research_period_end),
        validation_start=date.fromisoformat(settings.validation_period_start),
    )


def _benchmark_series(daily_rows: list[Any]) -> list[float]:
    """抽取逐日基准收益率序列（缺失日按 0 处理，与平台口径一致）。

    Args:
        daily_rows: 逐日结果对象列表。

    Returns:
        基准日收益率列表；全部缺失时返回空列表（表示不参与基准对比）。
    """
    values = [getattr(row, "benchmark_return", None) for row in daily_rows]
    if all(value is None for value in values):
        return []
    return [float(value) if value is not None else 0.0 for value in values]


def _metrics_digest(metrics: dict[str, Any]) -> dict[str, Any]:
    """抽取毛口径关键指标（避免把全部指标塞进研究输出）。

    年化换手属于净口径块（`_net_digest`），这里不重复输出，避免出现
    "毛口径下的换手为 null"这种看起来像缺失的假象。
    """
    keys = (
        "cumulative_return_pct",
        "annualized_return_pct",
        "sharpe_ratio",
        "sortino_ratio",
        "max_drawdown_pct",
        "win_rate_pct",
    )
    return {key: metrics.get(key) for key in keys}


def _net_digest(stability: Any) -> dict[str, Any]:
    """抽取净口径与稳健性关键指标。"""
    return {
        "cost_bps": stability.cost_bps,
        "net_cumulative_return_pct": stability.net_cumulative_return_pct,
        "net_annualized_return_pct": stability.net_annualized_return_pct,
        "net_sharpe_ratio": stability.net_sharpe_ratio,
        "net_excess_return_pct": stability.net_excess_return_pct,
        "annualized_turnover": stability.annualized_turnover,
        "cost_drag_pct_per_year": stability.cost_drag_pct_per_year,
        "max_drawdown_pct": stability.max_drawdown_pct,
        "year_return_share_max": stability.year_return_share_max,
        "ex_best_year_sharpe_ratio": stability.ex_best_year_sharpe_ratio,
        "segment_sharpe_positive_ratio": stability.segment_sharpe_positive_ratio,
        "cost_ladder": [
            {
                "cost_bps": entry.cost_bps,
                "net_annualized_return_pct": entry.net_annualized_return_pct,
                "net_sharpe_ratio": entry.net_sharpe_ratio,
            }
            for entry in stability.cost_ladder
        ],
    }


def _caliber_digest(
    params: dict[str, Any], cost_bps: float, windows_list: list[dict[str, str]]
) -> dict[str, Any]:
    """输出口径指纹：不同口径的指标不可相互比较。"""
    return {
        "execution_model": params.get("_execution_model"),
        "data_quality_mode": params.get("_data_quality_mode"),
        "benchmark_index_code": (
            params.get("_benchmark_index_code") if params.get("_enable_benchmark", True) else None
        ),
        "calendar_source": params.get("_calendar_source"),
        "turnover_model": params.get("_turnover_model"),
        "cost_bps": cost_bps,
        "start_date": windows_list[0]["start"] if windows_list else None,
        "end_date": windows_list[-1]["end"] if windows_list else None,
        "persisted": False,
        # 拼接口径标记（F-10）：aggregate 是把各窗口的逐日结果首尾相接后重算的，
        # 与"整段一次性回测"不等价（窗口起点会重置仓位、每窗各自预热），
        # 报告里禁止拿它与 backtest show 的整段数字直接比较
        "aggregate_mode": "window_stitched",
    }


def _accumulator_from_rows(daily_rows: list[Any]) -> Any:
    """用逐日结果重建账户累积器（供毛口径汇总指标使用）。

    Args:
        daily_rows: 逐日结果对象列表（含 portfolio_return 与 positions）。

    Returns:
        BacktestDayAccumulator。
    """
    from quant_etf_api.domain.portfolio.accounting import BacktestDayAccumulator

    accumulator = BacktestDayAccumulator()
    for row in daily_rows:
        positions = getattr(row, "executed_positions", None) or getattr(row, "positions", None)
        accumulator.apply_day(row.portfolio_return, bool(positions))
    return accumulator


def _delta_block(
    baseline_aggregate: dict[str, Any],
    variant_aggregate: dict[str, Any],
    baseline_windows: list[dict[str, Any]],
    variant_windows: list[dict[str, Any]],
) -> dict[str, Any]:
    """计算变体相对基线的净口径差异与逐窗口方向。

    Args:
        baseline_aggregate: 基线聚合指标。
        variant_aggregate: 变体聚合指标。
        baseline_windows: 基线逐窗口指标。
        variant_windows: 变体逐窗口指标。

    Returns:
        差异块：净年化 / 净夏普差值、逐窗口净夏普差与"劣化窗口占比"。
    """
    base_net = baseline_aggregate.get("net_metrics") or {}
    var_net = variant_aggregate.get("net_metrics") or {}
    per_window: list[dict[str, Any]] = []
    worse = 0
    for base_item, var_item in zip(baseline_windows, variant_windows, strict=False):
        base_sharpe = (base_item.get("net_metrics") or {}).get("net_sharpe_ratio")
        var_sharpe = (var_item.get("net_metrics") or {}).get("net_sharpe_ratio")
        delta = _safe_delta(var_sharpe, base_sharpe)
        if delta is not None and delta < 0:
            worse += 1
        per_window.append(
            {
                "label": var_item.get("label"),
                "delta_net_sharpe": delta,
            }
        )
    total = len(per_window)
    return {
        "delta_net_annualized_return_pct": _safe_delta(
            var_net.get("net_annualized_return_pct"), base_net.get("net_annualized_return_pct")
        ),
        "delta_net_sharpe_ratio": _safe_delta(
            var_net.get("net_sharpe_ratio"), base_net.get("net_sharpe_ratio")
        ),
        "delta_net_excess_return_pct": _safe_delta(
            var_net.get("net_excess_return_pct"), base_net.get("net_excess_return_pct")
        ),
        "delta_max_drawdown_pct": _safe_delta(
            var_net.get("max_drawdown_pct"), base_net.get("max_drawdown_pct")
        ),
        "per_window": per_window,
        "worse_window_ratio": round(worse / total, 4) if total else None,
    }


def _safe_delta(value: Any, base: Any) -> float | None:
    """安全求差（任一为空返回 None），保留 4 位小数。"""
    if value is None or base is None:
        return None
    try:
        return round(float(value) - float(base), 4)
    except (TypeError, ValueError):
        return None
