"""策略生命周期服务：上线冻结、健康刷新与诊断留痕。

只覆盖"人工标记上线之后"的阶段：研究、回测与优化仍由既有体系承担。
上线时冻结配置快照并生成研究期分布作为参照系；之后每次刷新把上线后的
表现填回该分布，输出分位、期望差与诊断结论，并追加一条健康快照。

设计约束（与两篇方法论文档一致）：
- 状态只由人工变更，健康等级只由系统计算，系统不自动调参、不自动改状态；
- 阈值全部取自该策略自身的研究期分布，不使用全局固定数字；
- 上线后回测标记为 ``purpose=monitor``，使用验证期数据必须留痕。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.config.settings import get_settings
from quant_etf_api.domain.research.lifecycle import (
    SCORE_IC_DECAY_MIN_HALF_N,
    assess_health,
    build_baseline_distribution,
    evaluate_against_baseline,
    has_ic_decay_evidence,
    ic_decay_evidence,
    ic_evidence_shortfall,
)
from quant_etf_api.domain.research.periods import (
    PURPOSE_MONITOR,
    PURPOSE_RESEARCH,
    PeriodBoundaries,
)
from quant_etf_api.domain.research.stability import compute_stability_metrics
from quant_etf_api.factors.evaluation import analyze_backtest_score_ic, analyze_ic
from quant_etf_api.infra.db.base import utcnow
from quant_etf_api.infra.db.models.core import (
    BacktestRunModel,
    StrategyHealthSnapshotModel,
    StrategyLifecycleModel,
)
from quant_etf_api.infra.time import today_cn
from quant_etf_api.schemas.backtest import BacktestCreateRequest
from quant_etf_api.schemas.lifecycle import (
    HealthSnapshotSchema,
    LifecycleDetail,
    LifecycleOnlineRequest,
    LifecycleRefreshRequest,
    LifecycleStatusRequest,
    LifecycleSummary,
)
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.strategy_config_service import (
    StrategyConfigService,
    compute_config_hash,
)

logger = logging.getLogger(__name__)

# 详情页默认返回的最近快照条数
DEFAULT_SNAPSHOT_LIMIT = 24


class StrategyLifecycleService:
    """策略生命周期服务，负责上线冻结、健康刷新与状态记录。

    所有写操作都发生在人工触发（API 调用或页面按钮）时；本服务不做定时任务，
    也不依据诊断结论自动修改策略配置或生命周期状态。
    """

    def __init__(self, db: Session) -> None:
        """初始化服务。

        Args:
            db: SQLAlchemy 同步 Session。
        """
        self._db = db
        self._backtest_svc = BacktestService(db)
        self._config_svc = StrategyConfigService(db)

    # ── 查询 ──────────────────────────────────────────────────────────────

    def list_lifecycles(self) -> list[LifecycleSummary]:
        """返回全部上线策略的生命周期摘要（按上线日期倒序）。

        Returns:
            生命周期摘要列表。
        """
        try:
            rows = (
                self._db.query(StrategyLifecycleModel)
                .order_by(StrategyLifecycleModel.live_at.desc())
                .all()
            )
        except Exception:
            logger.warning("list_lifecycles DB query failed", exc_info=True)
            return []
        return [self._to_summary(row) for row in rows]

    def get_lifecycle(
        self, strategy_id: str, snapshot_limit: int = DEFAULT_SNAPSHOT_LIMIT
    ) -> LifecycleDetail | None:
        """返回单策略的生命周期详情（含最近若干次健康快照）。

        Args:
            strategy_id: 策略 ID。
            snapshot_limit: 返回的最近快照条数，默认 24。

        Returns:
            生命周期详情，未上线时返回 None。
        """
        row = self._find(strategy_id)
        if row is None:
            return None
        snapshots = (
            self._db.query(StrategyHealthSnapshotModel)
            .filter(StrategyHealthSnapshotModel.strategy_id == strategy_id)
            .order_by(StrategyHealthSnapshotModel.computed_at.desc())
            .limit(snapshot_limit)
            .all()
        )
        summary = self._to_summary(row)
        return LifecycleDetail(
            **summary.model_dump(),
            frozen_config_hash=row.frozen_config_hash,
            frozen_config_snapshot=row.frozen_config_snapshot,
            research_backtest_id=row.research_backtest_id,
            validation_backtest_id=row.validation_backtest_id,
            baseline_distribution=row.baseline_distribution,
            note=row.note,
            created_at=row.created_at,
            updated_at=row.updated_at,
            snapshots=[self._to_snapshot(item) for item in snapshots],
        )

    # ── 人工操作 ──────────────────────────────────────────────────────────

    def online(self, strategy_id: str, req: LifecycleOnlineRequest) -> LifecycleDetail:
        """标记策略上线：冻结配置快照，并生成研究期分布作为监控参照系。

        优先复用已存在的研究期回测（同策略、同配置哈希、覆盖完整研究期且成功），
        避免每次上线重复跑十年回测；无可用回测时才创建并同步执行。

        Args:
            strategy_id: 策略 ID。
            req: 上线请求（上线日期、备注、成本）。

        Returns:
            上线后的生命周期详情。

        Raises:
            ValueError: 策略不存在、上线日期早于验证期起点，或研究期回测失败时抛出。
        """
        detail = self._config_svc.get_config(strategy_id)
        if detail is None:
            raise ValueError(f"策略 {strategy_id} 不存在")

        boundaries = _period_boundaries()
        live_at = req.live_at or today_cn()
        if live_at < boundaries.validation_start:
            raise ValueError(
                f"上线日期 {live_at.isoformat()} 早于验证期起点 "
                f"{boundaries.validation_start.isoformat()}；"
                "研究期区间只能用于研究，不能作为实盘监控起点。"
            )

        config_hash = compute_config_hash(detail.config_json)
        research_backtest_id = self._find_reusable_research_backtest(
            strategy_id, config_hash, boundaries
        )
        if research_backtest_id is None:
            created = self._backtest_svc.create_backtest(
                BacktestCreateRequest(
                    strategy_id=strategy_id,
                    start_date=boundaries.research_start,
                    end_date=boundaries.research_end,
                    purpose=PURPOSE_RESEARCH,
                    purpose_reason=f"lifecycle online: {strategy_id}",
                )
            )
            research_backtest_id = created.backtest_id
            self._backtest_svc.run_backtest(research_backtest_id)
            run_detail = self._backtest_svc.get_backtest(research_backtest_id)
            if run_detail is None or run_detail.status != "success":
                raise ValueError(
                    f"研究期基线回测未成功（backtest_id={research_backtest_id}），"
                    "请先排查数据与配置后重新上线"
                )

        distribution = self._build_distribution(research_backtest_id)
        row = self._find(strategy_id)
        if row is None:
            row = StrategyLifecycleModel(
                strategy_id=strategy_id,
                lifecycle_status="LIVE",
                live_at=live_at,
                frozen_config_hash=config_hash,
                frozen_config_snapshot={
                    "strategy_id": detail.strategy_id,
                    "display_name": detail.display_name,
                    "version": detail.version,
                    "frequency": detail.frequency,
                    "config_json": detail.config_json,
                },
                research_backtest_id=research_backtest_id,
                baseline_distribution=distribution,
                note=req.note,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            self._db.add(row)
        else:
            # 重新上线：覆盖冻结点与参照分布，旧快照保留作历史观察
            row.lifecycle_status = "LIVE"
            row.live_at = live_at
            row.retired_at = None
            row.frozen_config_hash = config_hash
            row.frozen_config_snapshot = {
                "strategy_id": detail.strategy_id,
                "display_name": detail.display_name,
                "version": detail.version,
                "frequency": detail.frequency,
                "config_json": detail.config_json,
            }
            row.research_backtest_id = research_backtest_id
            row.baseline_distribution = distribution
            row.note = req.note or row.note
            row.updated_at = utcnow()
        self._db.commit()
        result = self.get_lifecycle(strategy_id)
        if result is None:
            raise ValueError(f"策略 {strategy_id} 上线记录写入失败")
        return result

    def update_status(
        self, strategy_id: str, req: LifecycleStatusRequest
    ) -> LifecycleDetail:
        """人工变更生命周期状态（运行中 / 暂停观察 / 退役）。

        Args:
            strategy_id: 策略 ID。
            req: 目标状态与说明。

        Returns:
            更新后的生命周期详情。

        Raises:
            ValueError: 策略未上线时抛出。
        """
        row = self._find(strategy_id)
        if row is None:
            raise ValueError(f"策略 {strategy_id} 尚未标记上线，无法变更生命周期状态")
        if req.status == "LIVE":
            current = self._config_svc.get_config(strategy_id)
            if current is None:
                raise ValueError(f"策略 {strategy_id} 不存在，无法恢复上线")
            current_hash = compute_config_hash(current.config_json)
            if current_hash != row.frozen_config_hash:
                raise ValueError(
                    f"策略 {strategy_id} 的当前配置已不同于上线冻结版本，"
                    "不能直接恢复 LIVE；请重新执行上线以重建研究期基线。"
                )
        row.lifecycle_status = req.status
        if req.status == "RETIRED":
            row.retired_at = today_cn()
        else:
            row.retired_at = None
        if req.note:
            row.note = req.note
        row.updated_at = utcnow()
        self._db.commit()
        result = self.get_lifecycle(strategy_id)
        if result is None:
            raise ValueError(f"策略 {strategy_id} 状态更新后详情不可用")
        return result

    def refresh(
        self, strategy_id: str, req: LifecycleRefreshRequest
    ) -> HealthSnapshotSchema:
        """人工触发一次体检：跑上线后回测并与研究期分布比对，追加一条快照。

        同步执行（不使用后台任务），因此调用方需容忍一次验证期回测的耗时。

        Args:
            strategy_id: 策略 ID。
            req: 刷新请求（可选成本覆盖）。

        Returns:
            新生成的健康快照。

        Raises:
            ValueError: 策略未上线、已退役，或监控回测未成功时抛出。
        """
        row = self._find(strategy_id)
        if row is None:
            raise ValueError(f"策略 {strategy_id} 尚未标记上线，无法刷新健康快照")
        if row.lifecycle_status == "RETIRED":
            raise ValueError(f"策略 {strategy_id} 已退役，不再接受健康刷新")
        if not row.frozen_config_snapshot:
            raise ValueError(
                f"策略 {strategy_id} 缺少上线冻结配置，无法生成可审计监控；请重新执行上线。"
            )

        live_start = row.live_at
        live_end = today_cn()
        if live_end < live_start:
            raise ValueError(
                f"上线日期 {live_start.isoformat()} 晚于当前日期 {live_end.isoformat()}，"
                "暂无可用监控区间"
            )
        cost_bps = req.cost_bps if req.cost_bps is not None else get_settings().default_cost_bps
        created = self._backtest_svc.create_backtest(
            BacktestCreateRequest(
                strategy_id=strategy_id,
                start_date=live_start,
                end_date=live_end,
                purpose=PURPOSE_MONITOR,
                purpose_reason=f"lifecycle refresh: {strategy_id}",
                cost_bps=cost_bps,
            ),
            config_snapshot_override=row.frozen_config_snapshot,
        )
        self._backtest_svc.run_backtest(created.backtest_id)
        run_detail = self._backtest_svc.get_backtest(created.backtest_id)
        if run_detail is None or run_detail.status != "success":
            raise ValueError(
                f"监控区间回测未成功（backtest_id={created.backtest_id}），"
                "请先排查数据与配置后重试"
            )

        daily_rows = self._backtest_svc.get_daily_results(created.backtest_id)
        returns = [r.portfolio_return for r in daily_rows]
        benchmark = [getattr(r, "benchmark_return", None) for r in daily_rows]
        trade_dates = [r.trade_date for r in daily_rows]
        stability = compute_stability_metrics(
            returns,
            trade_dates,
            benchmark_returns=benchmark,
            turnovers=[getattr(r, "turnover", None) for r in daily_rows],
            exposures=[getattr(r, "total_exposure", None) for r in daily_rows],
            positions=[getattr(r, "positions", None) for r in daily_rows],
            cost_bps=float(cost_bps),
        )
        evaluation = evaluate_against_baseline(
            returns, benchmark, row.baseline_distribution or {}
        )
        window_percentiles = {
            label: entry.get("excess_return_percentile_pct")
            for label, entry in (evaluation.get("windows") or {}).items()
        }
        drawdown_percentile = (evaluation.get("drawdown") or {}).get("percentile_pct")
        signal_diagnostics = self._compute_signal_diagnostics(
            strategy_id,
            created.backtest_id,
            live_start,
            live_end,
            live_end,
            row.frozen_config_snapshot,
        )
        assessment = assess_health(
            drawdown_percentile,
            window_percentiles,
            trailing_alpha_percentiles=self._trailing_alpha_percentiles(strategy_id, live_end),
            composite_ic_decay=signal_diagnostics["signals"]["composite_rank_ic"].get(
                "ic_decay"
            ),
        )

        metrics = {
            "period": {
                "start": live_start.isoformat(),
                "end": live_end.isoformat(),
                "trading_days": len(daily_rows),
            },
            "cost_bps": cost_bps,
            "gross": {
                "cumulative_return_pct": round(_compound(returns), 4),
                "annualized_return_pct": round(_annualized_return(returns), 4),
                "max_drawdown_pct": stability.max_drawdown_pct,
                "annualized_turnover": stability.annualized_turnover,
            },
            "net": {
                "cumulative_return_pct": stability.net_cumulative_return_pct,
                "annualized_return_pct": stability.net_annualized_return_pct,
                "sharpe_ratio": stability.net_sharpe_ratio,
                "excess_return_pct": stability.net_excess_return_pct,
                "cost_drag_pct_per_year": stability.cost_drag_pct_per_year,
            },
            "concentration": {
                "year_return_share_max": stability.year_return_share_max,
                "position_concentration": stability.position_concentration,
                "average_exposure": stability.average_exposure,
            },
            "evaluation": evaluation,
            "evaluation_window_periods": {
                label: {
                    "start": trade_dates[-int(entry["days"])].isoformat()
                    if entry.get("available") and len(trade_dates) >= int(entry["days"])
                    else None,
                    "end": live_end.isoformat() if entry.get("available") else None,
                }
                for label, entry in (evaluation.get("windows") or {}).items()
            },
            "signals": signal_diagnostics["signals"],
            "factors": signal_diagnostics["factors"],
            "validation_backtest_id": created.backtest_id,
        }
        snapshot = StrategyHealthSnapshotModel(
            strategy_id=strategy_id,
            as_of_date=live_end,
            live_start=live_start,
            live_end=live_end,
            metrics=metrics,
            health_level=assessment.health_level,
            diagnosis=assessment.diagnosis,
            recommended_action=assessment.recommended_action,
            reasons=assessment.reasons,
            trigger="manual",
            computed_at=utcnow(),
        )
        self._db.add(snapshot)
        row.validation_backtest_id = created.backtest_id
        row.latest_health_level = assessment.health_level
        row.latest_diagnosis = assessment.diagnosis
        row.latest_recommended_action = assessment.recommended_action
        row.last_refreshed_at = snapshot.computed_at
        row.updated_at = utcnow()
        self._db.commit()
        self._db.refresh(snapshot)
        return self._to_snapshot(snapshot)

    # ── 内部辅助 ──────────────────────────────────────────────────────────

    def _find(self, strategy_id: str) -> StrategyLifecycleModel | None:
        """读取生命周期行，不存在时返回 None。"""
        try:
            return (
                self._db.query(StrategyLifecycleModel)
                .filter(StrategyLifecycleModel.strategy_id == strategy_id)
                .one_or_none()
            )
        except Exception:
            logger.warning("查询生命周期记录失败：%s", strategy_id, exc_info=True)
            return None

    def _find_reusable_research_backtest(
        self, strategy_id: str, config_hash: str, boundaries: PeriodBoundaries
    ) -> str | None:
        """查找可复用的研究期基线回测，避免重复跑十年回测。

        Args:
            strategy_id: 策略 ID。
            config_hash: 当前配置哈希。
            boundaries: 研究期/验证期边界。

        Returns:
            可复用的回测 ID，无匹配时返回 None。
        """
        try:
            row = (
                self._db.query(BacktestRunModel)
                .filter(
                    BacktestRunModel.strategy_id == strategy_id,
                    BacktestRunModel.purpose == PURPOSE_RESEARCH,
                    BacktestRunModel.status == "success",
                    BacktestRunModel.config_hash == config_hash,
                    BacktestRunModel.start_date <= boundaries.research_start,
                    BacktestRunModel.end_date >= boundaries.research_end,
                )
                .order_by(BacktestRunModel.created_at.desc())
                .first()
            )
        except Exception:
            logger.warning("查找可复用研究期回测失败", exc_info=True)
            return None
        return row.backtest_id if row is not None else None

    def _build_distribution(self, backtest_id: str) -> dict[str, Any]:
        """从研究期回测的逐日结果构建分布快照。

        Args:
            backtest_id: 研究期回测 ID。

        Returns:
            可 JSON 序列化的研究期分布（各窗口超额/夏普/回撤分位表）。
        """
        daily_rows = self._backtest_svc.get_daily_results(backtest_id)
        return build_baseline_distribution(
            [r.portfolio_return for r in daily_rows],
            [getattr(r, "benchmark_return", None) for r in daily_rows],
            [r.trade_date for r in daily_rows],
        )

    def _trailing_alpha_percentiles(
        self, strategy_id: str, as_of_date: date
    ) -> list[float | None]:
        """取最近几次快照的 3M 超额分位，用于判断"连续多次低于阈值"。

        Args:
            strategy_id: 策略 ID。

        Returns:
            3M 超额分位列表（最近在前，最多 5 条）。
        """
        try:
            rows = (
                self._db.query(StrategyHealthSnapshotModel)
                .filter(StrategyHealthSnapshotModel.strategy_id == strategy_id)
                .order_by(StrategyHealthSnapshotModel.computed_at.desc())
                .limit(24)
                .all()
            )
        except Exception:
            logger.warning("读取历史快照失败", exc_info=True)
            return []
        result: list[float | None] = []
        selected_dates: list[date] = [as_of_date]
        for row in rows:
            if any(
                len(
                    self._backtest_svc._index_bar_repo.find_all_trading_dates(  # noqa: SLF001
                        row.as_of_date, selected
                    )
                ) <= 21
                for selected in selected_dates
            ):
                continue
            windows = ((row.metrics or {}).get("evaluation") or {}).get("windows") or {}
            result.append((windows.get("3m") or {}).get("excess_return_percentile_pct"))
            selected_dates.append(row.as_of_date)
            if len(result) == 5:
                break
        return result

    def _compute_signal_diagnostics(
        self,
        strategy_id: str,
        backtest_id: str,
        start: date,
        end: date,
        as_of: date,
        config_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """计算组合分数 IC 与评分因子的解释性 IC 诊断。

        组合分数 IC 是健康判定的唯一 IC 证据。评分因子的单独 IC 仅用于解释
        组合信号变化，过滤与择时因子不是横截面 Alpha 预测器，不纳入此处。

        组合 IC 只覆盖实际选股调仓日中**真正参与评分**的资产（``scored=True``）。
        因此周/月策略不从每日分数任意抽样；是否取消前瞻窗口重叠折算则由实际交易
        日历验证。单因子诊断仍按前瞻跨度处理重叠窗口（两者的日期集不同，不可直接
        比较）。

        检测门槛用策略级口径 ``SCORE_IC_DECAY_MIN_HALF_N``（低于单因子），因为
        调仓日观测数天然只有日频的 1/5~1/21；同时把"还差几个调仓日、约需几个月"
        一并算出，避免非日频策略长期只看到一句静默的"未确认衰减"。

        Args:
            strategy_id: 策略 ID。
            backtest_id: 监控回测 ID（须由迁移 0053/0054 之后写入，才有
                scored 与 selection_rebalanced 标记）。
            start: 监控区间起始日。
            end: 监控区间截止日。
            as_of: 计算时点（用于记录，不参与计算）。
            config_snapshot: 冻结的配置快照，None 时读取当前配置。

        Returns:
            含 ``signals.composite_rank_ic`` 和 ``factors.per_factor`` 的字典；
            ``composite_rank_ic`` 额外带 ``decay_evidence_sufficient`` /
            ``decay_min_required_n`` / ``decay_shortfall_n`` /
            ``decay_shortfall_months``，供前端区分"未确认衰减"与"没有证据"，
            并给出还需要积累多久。
        """
        config = (
            self._config_svc.parse_snapshot(config_snapshot)
            if config_snapshot is not None
            else self._config_svc.get_parsed_config(strategy_id)
        )
        factor_ids = list(config.score.factors) if config is not None else []
        forward_days = _selection_forward_days(config)
        composite = analyze_backtest_score_ic(
            self._db, backtest_id, start, end, forward_days=forward_days
        )
        # 只有实际交易日历验证前瞻窗口互不重叠时，才不再折算半段样本数。
        # 月度策略在节假日、月初/月末切换时也可能出现重叠窗口。
        composite_windows_non_overlapping = bool(
            composite["summary"].get("selection_windows_non_overlapping", False)
        )
        composite_decay = ic_decay_evidence(
            [item["ic"] for item in composite["series"]],
            overlap_step=1 if composite_windows_non_overlapping else forward_days,
            min_half_n=SCORE_IC_DECAY_MIN_HALF_N,
        )
        shortfall = ic_evidence_shortfall(
            int(composite_decay["effective_n"]),
            min_half_n=SCORE_IC_DECAY_MIN_HALF_N,
            forward_days=forward_days,
        )
        result: dict[str, Any] = {
            "signals": {
                "composite_rank_ic": {
                    **composite["summary"],
                    "ic_decay": composite_decay,
                    "decay_evidence_sufficient": has_ic_decay_evidence(composite_decay),
                    "decay_min_required_n": shortfall["required_n"],
                    "decay_shortfall_n": shortfall["shortfall_n"],
                    "decay_shortfall_months": shortfall["shortfall_months"],
                    "forward_days": forward_days,
                    "selection_dates_only": True,
                    "source_backtest_id": backtest_id,
                    "as_of": as_of.isoformat(),
                }
            },
            "factors": {"score_factor_ids": factor_ids, "per_factor": {}},
        }
        for factor_id in factor_ids:
            try:
                analysis = analyze_ic(
                    self._db, factor_id, start, end, forward_days=forward_days
                )
            except Exception:
                logger.warning("计算因子 %s 的 IC 失败", factor_id, exc_info=True)
                continue
            summary = analysis["summary"]
            decay = ic_decay_evidence(
                [item["ic"] for item in analysis["series"]], overlap_step=forward_days
            )
            result["factors"]["per_factor"][factor_id] = {
                "role": "score_diagnostic",
                "count": summary["count"],
                "ic_mean": summary["ic_mean"],
                "ic_ir": summary["ic_ir"],
                "t_stat": summary["t_stat"],
                "ic_positive_ratio": summary["ic_positive_ratio"],
                "cross_section_n_avg": summary["cross_section_n_avg"],
                "excluded_low_n_days": summary["excluded_low_n_days"],
                "insufficient_reason": summary["insufficient_reason"],
                "ic_decay": decay,
            }
        return result

    def _to_summary(self, row: StrategyLifecycleModel) -> LifecycleSummary:
        """把生命周期 ORM 行转换为摘要模型。"""
        display_name: str | None = None
        try:
            config = self._config_svc.get_config(row.strategy_id)
            display_name = config.display_name if config is not None else None
        except Exception:
            display_name = None
        return LifecycleSummary(
            strategy_id=row.strategy_id,
            display_name=display_name,
            lifecycle_status=row.lifecycle_status,
            live_at=row.live_at,
            retired_at=row.retired_at,
            live_days=(today_cn() - row.live_at).days,
            health_level=row.latest_health_level,
            diagnosis=row.latest_diagnosis,
            recommended_action=row.latest_recommended_action,
            last_refreshed_at=row.last_refreshed_at,
        )

    @staticmethod
    def _to_snapshot(row: StrategyHealthSnapshotModel) -> HealthSnapshotSchema:
        """把健康快照 ORM 行转换为响应模型。"""
        return HealthSnapshotSchema(
            id=row.id,
            as_of_date=row.as_of_date,
            live_start=row.live_start,
            live_end=row.live_end,
            health_level=row.health_level,
            diagnosis=row.diagnosis,
            recommended_action=row.recommended_action,
            reasons=list(row.reasons or []),
            trigger=row.trigger,
            computed_at=row.computed_at,
            metrics=row.metrics,
        )


def _period_boundaries() -> PeriodBoundaries:
    """从系统配置读取研究期/验证期边界。"""
    settings = get_settings()
    return PeriodBoundaries(
        research_start=date.fromisoformat(settings.research_period_start),
        research_end=date.fromisoformat(settings.research_period_end),
        validation_start=date.fromisoformat(settings.validation_period_start),
    )


def _selection_forward_days(config: Any) -> int:
    """把冻结策略的选股调仓频率映射为组合 IC 前瞻交易日数。"""
    selection = getattr(getattr(config, "rebalance", None), "selection", None)
    frequency = getattr(selection, "frequency", "daily")
    return {"daily": 1, "weekly": 5, "biweekly": 10, "monthly": 21}.get(frequency, 1)


def _compound(daily_returns: list[float]) -> float:
    """计算收益率序列的累计收益（百分点）。"""
    cumulative = 1.0
    for r in daily_returns:
        cumulative *= 1 + r / 100
    return (cumulative - 1) * 100


def _annualized_return(
    daily_returns: list[float], trading_days: int = 252
) -> float:
    """把日收益序列折算为年化收益率（百分点），空序列返回 0。"""
    if not daily_returns:
        return 0.0
    total = _compound(daily_returns) / 100
    years = len(daily_returns) / trading_days
    if years <= 0 or total <= -1:
        return 0.0
    return ((1 + total) ** (1 / years) - 1) * 100
