"""策略对比回测服务：创建两条子回测、等待两者终态后汇总对比指标。

从 ``BacktestService`` 拆出：对比只编排两条普通回测（创建复用
``BacktestService.create_backtest``，执行由回测队列完成），本身不跑回测主循环。
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from quant_etf_api.domain.common.numeric import safe_metric_diff as _safe_metric_diff
from quant_etf_api.infra.db.models.core import BacktestComparisonModel
from quant_etf_api.infra.db.repositories.backtest import BacktestRepository
from quant_etf_api.infra.time import utcnow_aware
from quant_etf_api.schemas.backtest import (
    BacktestComparisonCreateRequest,
    BacktestComparisonDetail,
    BacktestComparisonSummary,
    BacktestCreateRequest,
    BacktestDailyResult,
    ComparisonDailyPoint,
    ComparisonDailyResponse,
    ComparisonMetrics,
)
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.strategy_config_service import StrategyConfigService

logger = logging.getLogger(__name__)


class BacktestComparisonService:
    """策略对比回测服务。"""

    def __init__(
        self,
        db: Session,
        backtest_service: BacktestService | None = None,
        backtest_repo: BacktestRepository | None = None,
    ) -> None:
        """初始化对比回测服务。

        Args:
            db: SQLAlchemy 同步 Session。
            backtest_service: 子回测创建与详情转换所用的回测服务，未提供时自动创建。
            backtest_repo: 回测仓库，未提供时自动创建。
        """
        self._db = db
        self._backtests = backtest_service or BacktestService(db)
        self._backtest_repo = backtest_repo or BacktestRepository(db)

    def create_comparison(self, req: BacktestComparisonCreateRequest) -> BacktestComparisonSummary:
        """创建策略对比回测，生成两个子回测和一个对比记录。

        两个子回测共享同一时间区间，但标的范围各自独立。
        若策略自身已配置 index_codes，则强制使用策略配置（不可修改）；
        否则使用请求中对应策略的 a_index_codes / b_index_codes。

        Args:
            req: 对比回测创建请求。

        Returns:
            BacktestComparisonSummary 对比摘要。

        Raises:
            ValueError: 策略不存在或两策略相同。
        """
        comparison_id = str(uuid4())
        now = utcnow_aware()

        # 校验两个策略不同
        if req.strategy_a_id == req.strategy_b_id:
            raise ValueError("请选择两个不同的策略进行对比")

        # 校验两个策略都存在且配置了 portfolio
        config_svc = StrategyConfigService(self._db)
        for sid in [req.strategy_a_id, req.strategy_b_id]:
            cfg = config_svc.get_parsed_config(sid)
            if cfg is None:
                raise ValueError(f"策略 {sid} 配置不存在")
        # 创建子回测 A（create_backtest 内部会处理策略自身的 index_codes 限定）
        a_mode = "subset" if req.a_index_codes else "all"
        bt_a_summary = self._backtests.create_backtest(
            BacktestCreateRequest(
                strategy_id=req.strategy_a_id,
                start_date=req.start_date,
                end_date=req.end_date,
                universe_mode=a_mode,  # type: ignore[arg-type]
                index_codes=req.a_index_codes,
                enable_benchmark=req.enable_benchmark,
                benchmark_index_code=req.benchmark_index_code,
                execution_model=req.execution_model,
                data_quality_mode=req.data_quality_mode,
            )
        )

        # 创建子回测 B
        b_mode = "subset" if req.b_index_codes else "all"
        bt_b_summary = self._backtests.create_backtest(
            BacktestCreateRequest(
                strategy_id=req.strategy_b_id,
                start_date=req.start_date,
                end_date=req.end_date,
                universe_mode=b_mode,  # type: ignore[arg-type]
                index_codes=req.b_index_codes,
                enable_benchmark=req.enable_benchmark,
                benchmark_index_code=req.benchmark_index_code,
                execution_model=req.execution_model,
                data_quality_mode=req.data_quality_mode,
            )
        )

        # 创建对比记录
        comp_row = BacktestComparisonModel(
            comparison_id=comparison_id,
            name=req.name,
            strategy_a_id=req.strategy_a_id,
            strategy_b_id=req.strategy_b_id,
            backtest_a_id=bt_a_summary.backtest_id,
            backtest_b_id=bt_b_summary.backtest_id,
            start_date=req.start_date,
            end_date=req.end_date,
            status="pending",
            params={
                "_enable_benchmark": req.enable_benchmark,
                "_benchmark_index_code": req.benchmark_index_code,
                "_execution_model": req.execution_model,
                "_data_quality_mode": req.data_quality_mode,
                "a_index_codes": req.a_index_codes,
                "b_index_codes": req.b_index_codes,
            },
            created_at=now,
        )
        self._db.add(comp_row)
        self._db.commit()

        return self._comp_row_to_summary(comp_row)

    def list_comparisons(
        self, offset: int = 0, limit: int = 50
    ) -> tuple[list[BacktestComparisonSummary], int]:
        """分页返回对比回测列表，按创建时间倒序。"""
        try:
            rows, total = self._backtest_repo.find_all_comparisons(offset=offset, limit=limit)
            items = [self._comp_row_to_summary(r) for r in rows]
            return items, total
        except Exception:
            logger.warning("list_comparisons DB query failed", exc_info=True)
            return [], 0

    def get_comparison(self, comparison_id: str) -> BacktestComparisonDetail | None:
        """返回对比回测详情，含两个子回测的完整信息。"""
        try:
            comp = self._backtest_repo.find_comparison_by_id(comparison_id)
            if comp is None:
                return None

            bt_a = self._backtest_repo.find_by_id(comp.backtest_a_id)
            bt_b = self._backtest_repo.find_by_id(comp.backtest_b_id)

            return BacktestComparisonDetail(
                **self._comp_row_to_summary(comp).model_dump(),
                backtest_a=self._backtests.row_to_detail(bt_a) if bt_a else None,
                backtest_b=self._backtests.row_to_detail(bt_b) if bt_b else None,
                params=comp.params,
            )
        except Exception:
            logger.warning("get_comparison DB query failed", exc_info=True)
            return None

    def get_comparison_daily(self, comparison_id: str) -> ComparisonDailyResponse:
        """返回两个策略的每日组合绩效摘要（仅图表渲染所需字段），用于叠加图表渲染。"""
        try:
            comp = self._backtest_repo.find_comparison_by_id(comparison_id)
            if comp is None:
                return ComparisonDailyResponse(a_daily=[], b_daily=[])

            def _to_point(daily: BacktestDailyResult) -> ComparisonDailyPoint:
                return ComparisonDailyPoint(
                    trade_date=daily.trade_date,
                    portfolio_return=daily.portfolio_return,
                    cumulative_return=daily.cumulative_return,
                    drawdown=daily.drawdown,
                )

            return ComparisonDailyResponse(
                a_daily=[
                    _to_point(d) for d in self._backtests.get_daily_results(comp.backtest_a_id)
                ],
                b_daily=[
                    _to_point(d) for d in self._backtests.get_daily_results(comp.backtest_b_id)
                ],
            )
        except Exception:
            logger.warning("get_comparison_daily DB query failed", exc_info=True)
            return ComparisonDailyResponse(a_daily=[], b_daily=[])

    def launch_comparison(self, comparison_id: str) -> tuple[str, str] | None:
        """标记对比回测为运行中，返回两个子回测 ID。

        由 comparison 任务处理器调用：对比任务不再等待子回测完成，
        而是由子回测完成后的 finalize_comparison_if_ready 触发汇总，
        避免嵌套线程池与父任务空等占用 worker。

        Args:
            comparison_id: 对比回测 ID。

        Returns:
            (backtest_a_id, backtest_b_id)；对比记录不存在时返回 None。
        """
        comp = self._backtest_repo.find_comparison_by_id(comparison_id)
        if comp is None:
            logger.error("launch_comparison: comparison_id %s not found", comparison_id)
            return None

        comp.status = "running"
        comp.started_at = utcnow_aware()
        self._db.commit()
        return comp.backtest_a_id, comp.backtest_b_id

    def finalize_comparison_if_ready(self, comparison_id: str) -> None:
        """子回测完成后尝试汇总对比结果，两个子回测均终态时执行。

        单个子回测完成即调用本方法；若另一个子回测仍在执行则直接返回，
        待其完成后再次触发。汇总逻辑与旧的 run_comparison 保持一致。

        Args:
            comparison_id: 对比回测 ID。
        """
        comp = self._backtest_repo.find_comparison_by_id(comparison_id)
        if comp is None:
            return
        bt_a = self._backtest_repo.find_by_id(comp.backtest_a_id)
        bt_b = self._backtest_repo.find_by_id(comp.backtest_b_id)
        if bt_a is None or bt_b is None:
            return

        statuses = [bt_a.status, bt_b.status]
        if any(s not in ("success", "failed") for s in statuses):
            # 仍有子回测未完成，等待另一个子回测完成时再次触发
            return

        errors: dict[str, str] = {}
        if bt_a.status == "failed":
            errors["a"] = bt_a.error_message or "策略A回测失败"
        if bt_b.status == "failed":
            errors["b"] = bt_b.error_message or "策略B回测失败"

        if len(errors) == 2:
            self._backtest_repo.mark_comparison_failed(
                comparison_id,
                f"策略A: {errors['a']}; 策略B: {errors['b']}",
            )
        elif len(errors) == 1:
            self._backtest_repo.mark_comparison_partial(comparison_id, str(errors))
        else:
            self._compute_and_save_comparison_metrics(comparison_id)

    def _compute_and_save_comparison_metrics(self, comparison_id: str) -> None:
        """从两个子回测的 metrics JSON 汇总对比指标。

        读取两个 backtest_run 的 metrics 字段，
        逐指标提取 A/B 值并计算差值，写入 comparison_metrics。
        """
        comp = self._backtest_repo.find_comparison_by_id(comparison_id)
        if comp is None:
            return

        bt_a = self._backtest_repo.find_by_id(comp.backtest_a_id)
        bt_b = self._backtest_repo.find_by_id(comp.backtest_b_id)
        if bt_a is None or bt_b is None:
            return

        ma = bt_a.metrics or {}
        mb = bt_b.metrics or {}

        def _get(key: str, metrics: dict, default: Any = 0.0) -> Any:
            return metrics.get(key, default)

        # 读取各项指标并计算差值。
        # 指标值经 _sanitize_metric_value 可能为 None，复制项统一回退 0.0
        # 保证 ComparisonMetrics 必需 float 字段可解析；差值用 _safe_metric_diff 容错。
        a_cum = _get("cumulative_return_pct", ma) or 0.0
        b_cum = _get("cumulative_return_pct", mb) or 0.0
        a_ann = _get("annualized_return_pct", ma) or 0.0
        b_ann = _get("annualized_return_pct", mb) or 0.0
        a_dd = _get("max_drawdown_pct", ma) or 0.0
        b_dd = _get("max_drawdown_pct", mb) or 0.0
        a_sharpe = _get("sharpe_ratio", ma) or 0.0
        b_sharpe = _get("sharpe_ratio", mb) or 0.0
        a_sortino = _get("sortino_ratio", ma) or 0.0
        b_sortino = _get("sortino_ratio", mb) or 0.0
        a_calmar = _get("calmar_ratio", ma) or 0.0
        b_calmar = _get("calmar_ratio", mb) or 0.0
        a_win = _get("win_rate_pct", ma) or 0.0
        b_win = _get("win_rate_pct", mb) or 0.0
        a_sig = _get("signal_accuracy_pct", ma) or 0.0
        b_sig = _get("signal_accuracy_pct", mb) or 0.0
        a_days = _get("total_trading_days", ma, 0)
        b_days = _get("total_trading_days", mb, 0)
        a_active = _get("active_days", ma, 0)
        b_active = _get("active_days", mb, 0)

        comparison_metrics = {
            "a_cumulative_return_pct": a_cum,
            "b_cumulative_return_pct": b_cum,
            "a_annualized_return_pct": a_ann,
            "b_annualized_return_pct": b_ann,
            "a_max_drawdown_pct": a_dd,
            "b_max_drawdown_pct": b_dd,
            "a_sharpe_ratio": a_sharpe,
            "b_sharpe_ratio": b_sharpe,
            "a_sortino_ratio": a_sortino,
            "b_sortino_ratio": b_sortino,
            "a_calmar_ratio": a_calmar,
            "b_calmar_ratio": b_calmar,
            "a_win_rate_pct": a_win,
            "b_win_rate_pct": b_win,
            "a_signal_accuracy_pct": a_sig,
            "b_signal_accuracy_pct": b_sig,
            "a_total_trading_days": a_days,
            "b_total_trading_days": b_days,
            "a_active_days": a_active,
            "b_active_days": b_active,
            # 差值
            "cumulative_return_diff_pct": _safe_metric_diff(a_cum, b_cum),
            "annualized_return_diff_pct": _safe_metric_diff(a_ann, b_ann),
            "max_drawdown_diff_pct": _safe_metric_diff(a_dd, b_dd),
            "sharpe_diff": _safe_metric_diff(a_sharpe, b_sharpe, 4),
            "sortino_diff": _safe_metric_diff(a_sortino, b_sortino, 4),
            "calmar_diff": _safe_metric_diff(a_calmar, b_calmar, 4),
            "win_rate_diff_pct": _safe_metric_diff(a_win, b_win),
            "signal_accuracy_diff_pct": _safe_metric_diff(a_sig, b_sig),
            # 基准对比
            "a_benchmark_return_pct": _get("benchmark_return_pct", ma),
            "b_benchmark_return_pct": _get("benchmark_return_pct", mb),
            "a_excess_return_pct": _get("excess_return_pct", ma),
            "b_excess_return_pct": _get("excess_return_pct", mb),
            "a_alpha": _get("alpha", ma),
            "b_alpha": _get("alpha", mb),
            "a_beta": _get("beta", ma),
            "b_beta": _get("beta", mb),
            "a_information_ratio": _get("information_ratio", ma),
            "b_information_ratio": _get("information_ratio", mb),
        }

        self._backtest_repo.mark_comparison_success(comparison_id, comparison_metrics)

    def _comp_row_to_summary(self, row: BacktestComparisonModel) -> BacktestComparisonSummary:
        """将 ORM 行转换为 BacktestComparisonSummary。"""
        comp_metrics = None
        if row.comparison_metrics:
            try:
                comp_metrics = ComparisonMetrics(**row.comparison_metrics)
            except Exception:
                pass
        return BacktestComparisonSummary(
            comparison_id=row.comparison_id,
            name=row.name,
            strategy_a_id=row.strategy_a_id,
            strategy_b_id=row.strategy_b_id,
            backtest_a_id=row.backtest_a_id,
            backtest_b_id=row.backtest_b_id,
            start_date=row.start_date,
            end_date=row.end_date,
            status=row.status,
            comparison_metrics=comp_metrics,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            error_message=row.error_message,
            progress=row.progress or 0,
        )
