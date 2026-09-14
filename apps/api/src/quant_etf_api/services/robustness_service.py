"""稳健性验证服务：候选集级别的过拟合风险检验。

单次回测只能描述"自己长什么样"；PBO、Deflated Sharpe 这类指标必须知道
候选集合与试验次数。本服务由基线策略派生一组变体（单旋钮扰动 / 删因子 /
子池扰动），逐窗口批量回测后汇总邻域稳定度、边际贡献与统计显著性，
并把结果落库为可审计的批次记录（同时充当试验次数台账）。

批量回测默认走后台任务队列（``async_mode=True``）以复用 worker 并行能力，
再由 ``collect`` 汇总；与既有优化会话的处理方式一致。
"""

from __future__ import annotations

import logging
import random
import re
from copy import deepcopy
from hashlib import md5
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.orm import Session

from quant_etf_api.config.settings import get_settings
from quant_etf_api.domain.research.periods import PURPOSE_RESEARCH, PeriodBoundaries
from quant_etf_api.domain.research.robustness import (
    block_bootstrap_sharpe_ci,
    compute_pbo,
    deflated_sharpe_ratio,
    summarize_neighborhood,
)
from quant_etf_api.domain.research.stability import net_return_series
from quant_etf_api.domain.research.walk_forward import compute_folds
from quant_etf_api.infra.db.base import utcnow
from quant_etf_api.infra.db.models.core import (
    BacktestIndexResultModel,
    BacktestRunModel,
    IndexDailyBarModel,
    RobustnessRunModel,
)
from quant_etf_api.infra.db.repositories.backtest import BacktestRepository
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.schemas.backtest import BacktestCreateRequest
from quant_etf_api.schemas.robustness import (
    RobustnessDetail,
    RobustnessListResponse,
    RobustnessSummary,
    RobustnessVariantSchema,
)
from quant_etf_api.schemas.strategy import StrategyConfigCreate
from quant_etf_api.services.backtest_reference_service import BacktestReferenceService
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.strategy_config_service import (
    StrategyConfigService,
    compute_config_hash,
)

logger = logging.getLogger(__name__)

# 不参与单旋钮扰动的配置键：版本号改动不产生行为差异
_KNOB_SKIP_KEYS = {"schema_version"}
# 默认子池抽样次数
DEFAULT_POOL_SAMPLES = 8
# 默认单旋钮扰动上限，防止配置过大时生成过多回测
DEFAULT_MAX_KNOBS = 30
# 子池扰动保留比例
POOL_KEEP_RATIO = 0.8

# 旋钮扰动优先级（D-2）：截断时按"业务上最可疑的拟合参数优先"排序，
# 而不是按路径字母序——字母序会把 timing.thresholds.* 排到最后截掉，
# 而择时阈值恰恰是最容易被调到"刚好"的参数。
_KNOB_PRIORITY: tuple[tuple[str, int], ...] = (
    ("timing.thresholds", 0),
    ("timing.proxy_index_codes", 1),
    ("timing.", 2),
    ("filters.rules", 3),
    ("filters.", 4),
    ("score.factors", 5),
    ("score.", 6),
    ("risk.", 7),
    ("portfolio.", 8),
    ("rank.", 9),
    ("rebalance.", 10),
)

# 扫描预设（D-2）：quick 用于"轻量体检"（默认写进优化验收清单），
# standard 与既有默认值一致
SCAN_PRESETS: dict[str, dict[str, int]] = {
    "quick": {"windows": 2, "max_knobs": 8},
    "standard": {"windows": 4, "max_knobs": DEFAULT_MAX_KNOBS},
}


def _knob_rank(path: str) -> int:
    """返回配置路径的扰动优先级（数值越小越先扫）。

    Args:
        path: 配置路径（如 ``timing.thresholds.offensive_score``）。

    Returns:
        优先级序号；未登记在优先级表中的路径排到最后。
    """
    for prefix, rank in _KNOB_PRIORITY:
        if path.startswith(prefix):
            return rank
    return len(_KNOB_PRIORITY)


def resolve_scan_options(
    preset: str | None,
    windows: int | None,
    max_knobs: int | None,
) -> tuple[int, int, dict[str, Any]]:
    """解析扫描预设与显式参数，返回最终窗口数、旋钮上限与口径记录（D-2）。

    显式参数优先于预设；都未提供时等价于 ``standard``。

    Args:
        preset: 预设名（quick/standard），None 表示不指定。
        windows: 显式窗口数，None 表示未指定。
        max_knobs: 显式旋钮上限，None 表示未指定。

    Returns:
        (窗口数, 旋钮上限, 口径记录字典)。

    Raises:
        ValueError: 预设名不支持时抛出。
    """
    if preset is not None and preset not in SCAN_PRESETS:
        raise ValueError(
            f"不支持的扫描预设：{preset}，可选值为 {', '.join(sorted(SCAN_PRESETS))}"
        )
    base = SCAN_PRESETS.get(preset or "standard", SCAN_PRESETS["standard"])
    resolved_windows = windows if windows is not None else base["windows"]
    resolved_knobs = max_knobs if max_knobs is not None else base["max_knobs"]
    if preset is not None:
        preset_label = preset
    elif windows is not None or max_knobs is not None:
        preset_label = "custom"
    else:
        preset_label = "standard"
    return resolved_windows, resolved_knobs, {
        "preset": preset_label,
        "windows": resolved_windows,
        "max_knobs": resolved_knobs,
    }


class RobustnessService:
    """稳健性验证服务，负责变体派生、批量回测与结果汇总。"""

    def __init__(self, db: Session) -> None:
        """初始化服务。

        Args:
            db: SQLAlchemy 同步 Session。
        """
        self._db = db
        self._config_svc = StrategyConfigService(db)
        self._backtest_svc = BacktestService(db)
        self._backtest_repo = BacktestRepository(db)
        self._index_bar_repo = IndexDailyBarRepository(db)

    # ── 查询 ──────────────────────────────────────────────────────────────

    def list_runs(
        self, limit: int = 100, strategy_id: str | None = None
    ) -> RobustnessListResponse:
        """返回最近的稳健性验证批次摘要（按创建时间倒序）。

        Args:
            limit: 返回条数上限，默认 100。
            strategy_id: 可选，只返回该基线策略的批次（策略详情页"稳健性"页签用）。

        Returns:
            RobustnessListResponse。
        """
        try:
            query = self._db.query(RobustnessRunModel)
            if strategy_id:
                query = query.filter(RobustnessRunModel.strategy_id == strategy_id)
            rows = query.order_by(RobustnessRunModel.created_at.desc()).limit(limit).all()
        except Exception:
            logger.warning("list_runs DB query failed", exc_info=True)
            return RobustnessListResponse()
        items = [self._to_summary(row) for row in rows]
        return RobustnessListResponse(items=items, total=len(items))

    def get_run(self, robustness_id: str) -> RobustnessDetail | None:
        """返回稳健性验证批次详情。

        Args:
            robustness_id: 批次 ID。

        Returns:
            批次详情，不存在时返回 None。
        """
        row = self._find(robustness_id)
        if row is None:
            return None
        summary = self._to_summary(row)
        referenced = [
            bid
            for variant in (row.variants or [])
            for bid in (variant.get("backtest_ids") or {}).values()
            if bid
        ]
        missing_ids = BacktestReferenceService(self._db).missing_ids_for(referenced)
        return RobustnessDetail(
            **summary.model_dump(),
            baseline_config_hash=row.baseline_config_hash,
            windows=list(row.windows or []),
            variants=[RobustnessVariantSchema(**item) for item in (row.variants or [])],
            summary=row.summary,
            statistics=row.statistics,
            missing_backtest_ids=missing_ids,
            scan_params=dict(getattr(row, "scan_params", None) or {}),
        )

    # ── 批次创建 ──────────────────────────────────────────────────────────

    def create(
        self,
        strategy_id: str,
        kind: str,
        windows: int | None = None,
        pool_samples: int = DEFAULT_POOL_SAMPLES,
        max_knobs: int | None = None,
        async_mode: bool = True,
        priority: int = 0,
        knobs: list[str] | None = None,
        preset: str | None = None,
    ) -> dict[str, Any]:
        """创建一次稳健性验证批次：派生变体并批量提交回测。

        全部变体都在研究期（2016-01-01 ~ 2025-12-31）内评估，因此自动受
        "研究类回测不得越过研究期末端"的硬约束保护，不会消耗验证期数据。

        Args:
            strategy_id: 基线策略 ID。
            kind: 验证类型，scan=单旋钮邻域扰动，ablate=因子消融，pool=子池扰动。
            windows: 研究期内切分的验证窗口数量；None 时取预设值（默认 4）。
            pool_samples: 子池扰动的随机抽样次数（kind=pool 时生效）。
            max_knobs: 单旋钮扰动数量上限（kind=scan 时生效）；None 时取预设值。
            async_mode: True 时仅入队，由服务端 worker 执行。
            priority: 入队优先级（越大越先执行），仅 async_mode=True 时生效。
            knobs: 关键旋钮路径清单（kind=scan 时生效），只扫描清单内的数值字段。
            preset: 扫描预设（quick=2 窗口/8 旋钮的轻量体检，standard=4 窗口/30 旋钮）。

        Returns:
            批次摘要字典（含 robustness_id、变体数量与 scan_params 口径）。

        Raises:
            ValueError: 类型不支持、预设不支持、关键旋钮路径不存在、策略不存在、
                区间无行情或未派生出任何变体时抛出。
        """
        if kind not in ("scan", "ablate", "pool"):
            raise ValueError(f"不支持的验证类型：{kind}")
        if kind != "scan" and (knobs or preset):
            raise ValueError("--knobs / --preset 仅对 scan 类型生效")
        resolved_windows, resolved_knobs, scan_params = resolve_scan_options(
            preset, windows, max_knobs
        )
        if knobs:
            scan_params["knobs"] = list(dict.fromkeys(knobs))
        if kind == "pool":
            scan_params["pool_samples"] = pool_samples
        baseline = self._config_svc.get_config(strategy_id)
        if baseline is None:
            raise ValueError(f"基线策略 {strategy_id} 不存在")
        boundaries = _period_boundaries()
        trade_dates = self._index_bar_repo.find_all_trading_dates(
            boundaries.research_start, boundaries.research_end
        )
        if not trade_dates:
            raise ValueError("研究期内无行情数据，无法切分验证窗口")
        folds = compute_folds(trade_dates, max(1, resolved_windows))
        window_list = [
            {"label": f"W{i}", "start": fs.isoformat(), "end": fe.isoformat()}
            for i, (fs, fe) in enumerate(folds)
        ]
        scan_params["windows"] = len(window_list)

        robustness_id = uuid4().hex
        # 深拷贝：变体派生会在嵌套字典上就地改写，浅拷贝会让基线与其他变体
        # 共享同一份嵌套结构，单个越界档位即可污染整批候选
        config = deepcopy(dict(baseline.config_json or {}))
        candidates = self._build_variants(
            robustness_id,
            strategy_id,
            config,
            kind,
            pool_samples,
            resolved_knobs,
            knobs,
        )
        if not candidates:
            raise ValueError(
                f"未派生任何 {kind} 变体（配置可能缺少可扰动字段或资产池）；请检查策略配置"
            )

        variants: list[dict[str, Any]] = [
            {
                "label": "baseline",
                "kind": "baseline",
                "knob": None,
                "value": None,
                "strategy_id": strategy_id,
                "backtest_ids": {},
            }
        ]
        for candidate in candidates:
            variant_strategy_id = self._create_variant_strategy(robustness_id, baseline, candidate)
            if variant_strategy_id is None:
                continue
            variants.append(
                {
                    "label": candidate["label"],
                    "kind": candidate["kind"],
                    "knob": candidate.get("knob"),
                    "value": candidate.get("value"),
                    "strategy_id": variant_strategy_id,
                    "backtest_ids": {},
                }
            )
        if len(variants) < 2:
            raise ValueError("未派生任何有效变体（候选配置均未通过校验）")

        for variant in variants:
            for window in window_list:
                backtest_id = self._submit_backtest(
                    variant["strategy_id"],
                    date.fromisoformat(window["start"]),
                    date.fromisoformat(window["end"]),
                    robustness_id,
                    variant["label"],
                    async_mode,
                    priority,
                )
                if backtest_id is not None:
                    variant["backtest_ids"][window["label"]] = backtest_id

        model = RobustnessRunModel(
            robustness_id=robustness_id,
            strategy_id=strategy_id,
            strategy_version=baseline.version,
            baseline_config_hash=compute_config_hash(config),
            kind=kind,
            status="running",
            start_date=boundaries.research_start,
            end_date=boundaries.research_end,
            windows=window_list,
            variants=variants,
            scan_params=scan_params,
            trial_count=len(variants) - 1,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._db.add(model)
        self._db.commit()
        logger.info(
            "稳健性验证批次已创建: %s 类型=%s 变体=%d 窗口=%d",
            robustness_id,
            kind,
            len(variants) - 1,
            len(window_list),
        )
        return {
            "robustness_id": robustness_id,
            "kind": kind,
            "variants": len(variants) - 1,
            "windows": len(window_list),
            "backtests": len(variants) * len(window_list),
            "async_mode": async_mode,
            "status": "running",
            "scan_params": scan_params,
        }

    def collect(self, robustness_id: str, allow_partial: bool = False) -> dict[str, Any]:
        """汇总批次结果：等待全部回测结束并计算邻域稳定度 / 边际贡献。

        Args:
            robustness_id: 批次 ID。
            allow_partial: True 时跳过"未完成/失败窗口"，只按已完成窗口汇总，
                并把覆盖率写入 summary（B7：避免个别卡死回测让整批无法收口）。

        Returns:
            汇总字典（含 status 与 summary）。

        Raises:
            ValueError: 批次不存在时抛出。
        """
        row = self._find(robustness_id)
        if row is None:
            raise ValueError(f"稳健性验证批次 {robustness_id} 不存在")
        variants = list(row.variants or [])
        pending = 0
        missing = 0
        failed: list[str] = []
        completed = 0
        expected = 0
        for variant in variants:
            for window_label, backtest_id in (variant.get("backtest_ids") or {}).items():
                expected += 1
                detail = self._backtest_repo.find_by_id(backtest_id)
                if detail is None:
                    # 回测已被删除（C5）：既不是 pending 也不能算 completed，
                    # 否则批次会永远停在 running（"等待一个不存在的结果"）
                    missing += 1
                    failed.append(f"{variant['label']}/{window_label}:回测已删除")
                elif detail.status in ("pending", "running"):
                    pending += 1
                elif detail.status in ("failed", "cancelled"):
                    failed.append(f"{variant['label']}/{window_label}")
                else:
                    completed += 1
        if failed and not allow_partial:
            row.status = "failed"
            row.error_message = f"部分回测失败或缺失：{', '.join(failed[:5])}"
            row.finished_at = utcnow()
            row.updated_at = utcnow()
            self._db.commit()
            return {
                "robustness_id": robustness_id,
                "status": "failed",
                "failed": failed,
                "missing_backtests": missing,
            }
        if pending and not allow_partial:
            return {
                "robustness_id": robustness_id,
                "status": "running",
                "pending_backtests": pending,
            }
        if allow_partial and (pending or failed):
            logger.warning(
                "稳健性批次部分汇总: %s 已完成=%d/%d 缺失窗口=%d 失败窗口=%d",
                robustness_id,
                completed,
                expected,
                pending,
                len(failed),
            )

        summary = self._summarize(row, variants)
        if allow_partial and (pending or failed):
            # 覆盖率随汇总落库：部分汇总必须显式暴露样本缺口，
            # 否则人工复核会把"少算几个窗口"的结果当成完整口径
            summary["coverage"] = {
                "expected_windows": expected,
                "completed_windows": completed,
                "pending_windows": pending,
                "missing_windows": [item for item in failed if "回测已删除" in item],
                "failed_windows": failed,
                "is_partial": True,
            }
            row.status = "partial"
        else:
            summary["coverage"] = {
                "expected_windows": expected,
                "completed_windows": completed,
                "pending_windows": 0,
                "missing_windows": [],
                "failed_windows": [],
                "is_partial": False,
            }
            row.status = "success"
        row.summary = summary
        row.finished_at = utcnow()
        row.updated_at = utcnow()
        self._db.commit()
        return {"robustness_id": robustness_id, "status": row.status, "summary": summary}

    # ── 批次取消 / 暂停（B2） ─────────────────────────────────────────────

    def cancel(self, robustness_id: str) -> dict[str, Any]:
        """取消整批稳健性回测：未开始的直接取消，运行中的协作退出。

        Args:
            robustness_id: 批次 ID。

        Returns:
            取消结果字典（含队列与回测两侧的处理数量）。

        Raises:
            ValueError: 批次不存在时抛出。
        """
        row = self._find(robustness_id)
        if row is None:
            raise ValueError(f"稳健性验证批次 {robustness_id} 不存在")
        from quant_etf_api.infra.job_queue.queue import get_job_queue

        result = get_job_queue().cancel_batch(robustness_id, f"用户取消稳健性批次 {robustness_id}")
        # 只把"任务已被取消、永远不会再跑"的 pending 回测直接落为 cancelled；
        # 运行中的回测交给协作取消路径（主循环退出后自行落 cancelled），
        # 避免在回测仍可能写成 success 时被提前改写状态。
        stale = 0
        for variant in list(row.variants or []):
            for backtest_id in (variant.get("backtest_ids") or {}).values():
                detail = self._backtest_repo.find_by_id(backtest_id)
                if detail is not None and detail.status == "pending":
                    self._backtest_repo.mark_cancelled(backtest_id, f"批次 {robustness_id} 已取消")
                    stale += 1
        if row.status == "running":
            row.status = "cancelled"
            row.finished_at = utcnow()
            row.updated_at = utcnow()
            row.error_message = "批次已按请求取消"
            self._db.commit()
        return {
            "robustness_id": robustness_id,
            "status": row.status,
            "cancelled_jobs": result["cancelled"],
            "cancel_requested_jobs": result["requested"],
            "cancelled_backtests": stale,
        }

    def pause(self, robustness_id: str) -> dict[str, Any]:
        """暂停批次中尚未开始的任务（运行中的任务自然结束）。

        Args:
            robustness_id: 批次 ID。

        Returns:
            暂停结果字典。

        Raises:
            ValueError: 批次不存在时抛出。
        """
        if self._find(robustness_id) is None:
            raise ValueError(f"稳健性验证批次 {robustness_id} 不存在")
        from quant_etf_api.infra.job_queue.queue import get_job_queue

        paused = get_job_queue().pause_batch(robustness_id, f"批次 {robustness_id} 已暂停")
        return {"robustness_id": robustness_id, "paused_jobs": paused}

    def resume(self, robustness_id: str) -> dict[str, Any]:
        """恢复批次中被暂停的任务。

        Args:
            robustness_id: 批次 ID。

        Returns:
            恢复结果字典。

        Raises:
            ValueError: 批次不存在时抛出。
        """
        if self._find(robustness_id) is None:
            raise ValueError(f"稳健性验证批次 {robustness_id} 不存在")
        from quant_etf_api.infra.job_queue.queue import get_job_queue

        resumed = get_job_queue().resume_batch(robustness_id)
        return {"robustness_id": robustness_id, "resumed_jobs": resumed}

    def compute_statistics(
        self,
        robustness_id: str,
        n_trials: int | None = None,
        cost_bps: float | None = None,
        block: int = 20,
        n_bootstrap: int = 2000,
    ) -> dict[str, Any]:
        """计算统计显著性：CSCV-PBO、Deflated Sharpe 与块自助法置信区间。

        ``n_trials`` 留空时取该策略的试验次数台账（历史所有稳健性批次的变体
        总数），避免用"本轮试了几个"低估多重检验的严重程度。

        Args:
            robustness_id: 批次 ID。
            n_trials: 计入多重检验的试验次数，留空取台账值。
            cost_bps: 净口径成本（基点），留空取系统默认值。
            block: 自助法块长度（交易日），默认 20。
            n_bootstrap: 自助抽样次数，默认 2000。

        Returns:
            统计结果字典。

        Raises:
            ValueError: 批次不存在或尚未完成汇总时抛出。
        """
        row = self._find(robustness_id)
        if row is None:
            raise ValueError(f"稳健性验证批次 {robustness_id} 不存在")
        if row.status != "success":
            raise ValueError(
                f"批次 {robustness_id} 当前状态为 {row.status}，请先执行 collect 汇总全部回测结果"
            )
        windows = list(row.windows or [])
        variants = list(row.variants or [])
        window_labels = [w["label"] for w in windows]
        matrix: dict[str, list[float]] = {}
        for variant in variants:
            values: list[float] = []
            for label in window_labels:
                backtest_id = (variant.get("backtest_ids") or {}).get(label)
                metrics = self._metrics_of(backtest_id)
                if metrics is None or metrics.get("sharpe_ratio") is None:
                    values = []
                    break
                values.append(float(metrics["sharpe_ratio"]))
            if values:
                matrix[variant["label"]] = values

        # CSCV 需要对称切分：分块数必须为不小于 2 的偶数，否则跳过 PBO
        n_blocks = len(window_labels)
        pbo_result = (
            compute_pbo(matrix)
            if len(matrix) >= 3 and n_blocks >= 2 and n_blocks % 2 == 0
            else None
        )
        baseline_returns = self._baseline_daily_returns(row, cost_bps)
        trials = n_trials if n_trials is not None else self._trial_ledger(row.strategy_id)
        dsr = (
            deflated_sharpe_ratio(baseline_returns, trials) if len(baseline_returns) >= 3 else None
        )
        ci = (
            block_bootstrap_sharpe_ci(baseline_returns, block=block, n_bootstrap=n_bootstrap)
            if len(baseline_returns) >= 3
            else None
        )
        statistics = {
            "n_trials": trials,
            "n_windows": n_blocks,
            "cost_bps": (cost_bps if cost_bps is not None else get_settings().default_cost_bps),
            "pbo": (
                {
                    "value": round(pbo_result.pbo, 4),
                    "n_candidates": pbo_result.n_candidates,
                    "n_splits": pbo_result.n_splits,
                    "n_blocks": pbo_result.n_blocks,
                }
                if pbo_result is not None
                else None
            ),
            "deflated_sharpe": (
                {
                    "sharpe_annualized": round(dsr.sharpe_annualized, 4),
                    "expected_max_sharpe_annualized": round(dsr.expected_max_sharpe_annualized, 4),
                    "deflated_sharpe": round(dsr.deflated_sharpe, 4),
                    "n_observations": dsr.n_observations,
                    "n_trials": dsr.n_trials,
                }
                if dsr is not None
                else None
            ),
            "bootstrap": (
                {
                    "sharpe_annualized": round(ci.sharpe_annualized, 4),
                    "lower": round(ci.lower, 4),
                    "upper": round(ci.upper, 4),
                    "confidence": ci.confidence,
                    "block": ci.block,
                    "n_bootstrap": ci.n_bootstrap,
                }
                if ci is not None
                else None
            ),
        }
        row.statistics = statistics
        row.updated_at = utcnow()
        self._db.commit()
        return statistics

    # ── 变体派生 ──────────────────────────────────────────────────────────

    def _build_variants(
        self,
        robustness_id: str,
        strategy_id: str,
        config: dict[str, Any],
        kind: str,
        pool_samples: int,
        max_knobs: int,
        knobs: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """按验证类型派生候选配置（尚未落库为草稿策略）。

        Args:
            robustness_id: 批次 ID（用于生成可复现的随机种子与标签）。
            strategy_id: 基线策略 ID。
            config: 基线配置 JSON。
            kind: 验证类型。
            pool_samples: 子池抽样次数。
            max_knobs: 单旋钮扰动上限。
            knobs: 关键旋钮路径清单（kind=scan 时生效）。

        Returns:
            候选列表，元素含 label/kind/knob/value/config。
        """
        if kind == "scan":
            return build_knob_variants(config, max_knobs, knobs)
        if kind == "ablate":
            return build_ablation_variants(config)
        return self._build_pool_variants(robustness_id, strategy_id, config, pool_samples)

    def _build_pool_variants(
        self,
        robustness_id: str,
        strategy_id: str,
        config: dict[str, Any],
        pool_samples: int,
    ) -> list[dict[str, Any]]:
        """派生资产池扰动变体：随机子池、剔除常持、剔除后上市指数。

        池扰动必须改写 ``config_json.index_codes``：策略自身的 index_codes 会
        覆盖回测请求的资产范围，用请求参数改池会得到静默错误的结果。

        Args:
            robustness_id: 批次 ID，用于生成可复现的随机种子。
            strategy_id: 基线策略 ID。
            config: 基线配置 JSON。
            pool_samples: 随机子池抽样次数。

        Returns:
            候选列表。
        """
        codes = list(config.get("index_codes") or [])
        if len(codes) < 3:
            return []
        boundaries = _period_boundaries()
        candidates: list[dict[str, Any]] = []
        rng = random.Random(int(robustness_id[:8], 16))
        keep = max(2, int(round(len(codes) * POOL_KEEP_RATIO)))
        for i in range(max(0, pool_samples)):
            subset = sorted(rng.sample(codes, keep))
            variant_config = dict(config)
            variant_config["index_codes"] = subset
            candidates.append(
                {
                    "label": f"rand80_{i:02d}",
                    "kind": "pool",
                    "knob": "index_codes",
                    "value": subset,
                    "config": variant_config,
                }
            )
        most_held = self._most_held_code(strategy_id)
        if most_held and most_held in codes and len(codes) - 1 >= 2:
            variant_config = dict(config)
            variant_config["index_codes"] = [c for c in codes if c != most_held]
            candidates.append(
                {
                    "label": f"drop_hold1_{most_held}",
                    "kind": "pool",
                    "knob": "index_codes",
                    "value": variant_config["index_codes"],
                    "config": variant_config,
                }
            )
        late_listed = self._late_listed_codes(
            codes,
            boundaries.research_start,
            boundaries.research_start + timedelta(days=365),
        )
        remaining = [c for c in codes if c not in late_listed]
        if late_listed and len(remaining) >= 2:
            variant_config = dict(config)
            variant_config["index_codes"] = remaining
            candidates.append(
                {
                    "label": "no_late_listed",
                    "kind": "pool",
                    "knob": "index_codes",
                    "value": remaining,
                    "config": variant_config,
                }
            )
        return candidates

    def _most_held_code(self, strategy_id: str) -> str | None:
        """取研究期回测中持仓天数最多的指数，用于"剔除常持"型扰动。"""
        try:
            backtest = (
                self._db.query(BacktestRunModel)
                .filter(
                    BacktestRunModel.strategy_id == strategy_id,
                    BacktestRunModel.purpose == PURPOSE_RESEARCH,
                    BacktestRunModel.status == "success",
                )
                .order_by(BacktestRunModel.created_at.desc())
                .first()
            )
            if backtest is None:
                return None
            row = (
                self._db.query(
                    BacktestIndexResultModel.index_code,
                    sa.func.count().label("hold_days"),
                )
                .filter(
                    BacktestIndexResultModel.backtest_id == backtest.backtest_id,
                    BacktestIndexResultModel.target_weight > 0,
                )
                .group_by(BacktestIndexResultModel.index_code)
                .order_by(sa.desc("hold_days"))
                .first()
            )
        except Exception:
            logger.warning("统计常持指数失败", exc_info=True)
            return None
        return row[0] if row is not None else None

    def _late_listed_codes(self, codes: list[str], start: date, cutoff: date) -> list[str]:
        """找出首个行情日晚于 ``cutoff`` 的指数（后上市）。

        Args:
            codes: 候选指数代码列表。
            start: 研究期起点（保留参数以表达"研究期内"的语义）。
            cutoff: 判定为后上市的日期阈值。

        Returns:
            后上市指数代码列表；查询失败时返回空列表。
        """
        if not codes:
            return []
        try:
            rows = (
                self._db.query(
                    IndexDailyBarModel.index_code,
                    sa.func.min(IndexDailyBarModel.trade_date),
                )
                .filter(
                    IndexDailyBarModel.index_code.in_(codes),
                    IndexDailyBarModel.trade_date >= start,
                )
                .group_by(IndexDailyBarModel.index_code)
                .all()
            )
        except Exception:
            logger.warning("查询指数首个行情日失败", exc_info=True)
            return []
        return [code for code, first_date in rows if first_date is not None and first_date > cutoff]

    def _create_variant_strategy(
        self, robustness_id: str, baseline: Any, candidate: dict[str, Any]
    ) -> str | None:
        """把候选配置落库为 draft 策略，校验失败时跳过。

        Args:
            robustness_id: 批次 ID。
            baseline: 基线策略详情。
            candidate: 候选（含 config）。

        Returns:
            新建的策略 ID；校验失败或创建异常时返回 None。
        """
        variant_config = dict(candidate["config"])
        variant_config.setdefault("schema_version", "1")
        validation = self._config_svc.validate_config(variant_config)
        if not validation.valid:
            logger.info(
                "跳过无效稳健性变体 %s：%s",
                candidate["label"],
                "; ".join(validation.errors[:2]),
            )
            return None
        variant_id = build_variant_strategy_id(
            baseline.strategy_id, robustness_id, candidate["label"]
        )
        if self._config_svc.get_config(variant_id) is not None:
            return variant_id
        try:
            self._config_svc.create_config(
                StrategyConfigCreate(
                    strategy_id=variant_id,
                    display_name=(
                        f"{baseline.display_name[:40]}（稳健性变体 {candidate['label'][:40]}）"
                    )[:128],
                    version=baseline.version,
                    description=f"稳健性验证批次 {robustness_id} 的派生变体",
                    frequency=baseline.frequency,
                    config_json=variant_config,
                    status="draft",
                    # D-4：变体带来源批次元数据，支持整批精确清理
                    # （strategy prune-variants --batch <id>）
                    is_variant=True,
                    source_batch_id=robustness_id,
                )
            )
        except Exception:
            # 单条变体失败不能污染整批：回滚会话后继续处理其余变体
            self._db.rollback()
            logger.warning("创建稳健性变体策略失败：%s", variant_id, exc_info=True)
            return None
        return variant_id

    def _submit_backtest(
        self,
        strategy_id: str,
        start: date,
        end: date,
        robustness_id: str,
        label: str,
        async_mode: bool,
        priority: int = 0,
    ) -> str | None:
        """创建并（可选异步）执行单个变体回测。

        Args:
            strategy_id: 变体策略 ID。
            start: 起始日期。
            end: 截止日期。
            robustness_id: 批次 ID（写入用途说明用于留痕，并作为队列批次号）。
            label: 变体标签。
            async_mode: True 时仅入队。
            priority: 入队优先级。

        Returns:
            回测 ID；创建失败时返回 None。
        """
        try:
            summary = self._backtest_svc.create_backtest(
                BacktestCreateRequest(
                    strategy_id=strategy_id,
                    start_date=start,
                    end_date=end,
                    purpose=PURPOSE_RESEARCH,
                    purpose_reason=f"robustness {robustness_id} ({label})",
                )
            )
        except Exception:
            logger.warning("创建稳健性回测失败：%s %s~%s", strategy_id, start, end, exc_info=True)
            return None
        if async_mode:
            from quant_etf_api.infra.job_queue.queue import backtest_job_key, get_job_queue

            get_job_queue().enqueue(
                "backtest",
                {"backtest_id": summary.backtest_id},
                job_key=backtest_job_key(summary.backtest_id),
                priority=priority,
                # 批次号随任务落库，支持整批取消/暂停（B2）
                batch_id=robustness_id,
            )
        else:
            self._backtest_svc.run_backtest(summary.backtest_id)
        return summary.backtest_id

    # ── 汇总与统计辅助 ────────────────────────────────────────────────────

    def _summarize(self, row: RobustnessRunModel, variants: list[dict[str, Any]]) -> dict[str, Any]:
        """按验证类型汇总变体结果（邻域稳定度 / 边际贡献 / 池扰动分布）。

        Args:
            row: 批次 ORM 行。
            variants: 变体列表（含回测 ID 映射）。

        Returns:
            汇总字典。
        """
        windows = list(row.windows or [])
        detail_rows: list[dict[str, Any]] = []
        baseline_sharpe: float | None = None
        baseline_return: float | None = None
        for variant in variants:
            sharpes: list[float] = []
            returns: list[float] = []
            for window in windows:
                metrics = self._metrics_of((variant.get("backtest_ids") or {}).get(window["label"]))
                if metrics is None:
                    continue
                if metrics.get("sharpe_ratio") is not None:
                    sharpes.append(float(metrics["sharpe_ratio"]))
                if metrics.get("annualized_return_pct") is not None:
                    returns.append(float(metrics["annualized_return_pct"]))
            mean_sharpe = _mean(sharpes)
            mean_return = _mean(returns)
            if variant["label"] == "baseline":
                baseline_sharpe = mean_sharpe
                baseline_return = mean_return
            detail_rows.append(
                {
                    "label": variant["label"],
                    "kind": variant.get("kind"),
                    "knob": variant.get("knob"),
                    "value": variant.get("value"),
                    "sharpe_mean": _round(mean_sharpe),
                    "annualized_return_mean": _round(mean_return),
                    "windows": len(sharpes),
                }
            )

        for item in detail_rows:
            if baseline_sharpe is not None and item["sharpe_mean"] is not None:
                item["delta_sharpe"] = _round(item["sharpe_mean"] - baseline_sharpe)
            else:
                item["delta_sharpe"] = None
            if baseline_return is not None and item["annualized_return_mean"] is not None:
                item["delta_annualized_return"] = _round(
                    item["annualized_return_mean"] - baseline_return
                )
            else:
                item["delta_annualized_return"] = None

        summary: dict[str, Any] = {
            "baseline_sharpe_mean": _round(baseline_sharpe),
            "baseline_annualized_return_mean": _round(baseline_return),
            "variants": detail_rows,
        }
        perturbed = [item for item in detail_rows if item["label"] != "baseline"]
        if row.kind == "scan" and baseline_sharpe is not None:
            neighborhood = summarize_neighborhood(
                baseline_sharpe,
                [item["sharpe_mean"] for item in perturbed if item["sharpe_mean"] is not None],
            )
            summary["neighborhood"] = {
                "n_variants": neighborhood.n_variants,
                "delta_min": _round(neighborhood.delta_min),
                "delta_max": _round(neighborhood.delta_max),
                "worse_ratio": _round(neighborhood.worse_ratio),
                "reversal": neighborhood.reversal,
                "is_plateau": neighborhood.is_plateau,
            }
        elif row.kind == "ablate":
            summary["marginal"] = sorted(
                (
                    {
                        "label": item["label"],
                        "delta_sharpe": item["delta_sharpe"],
                        "delta_annualized_return": item["delta_annualized_return"],
                    }
                    for item in perturbed
                ),
                key=lambda item: item["delta_sharpe"] if item["delta_sharpe"] is not None else 0,
            )
        elif row.kind == "pool":
            deltas = [
                item["delta_sharpe"] for item in perturbed if item["delta_sharpe"] is not None
            ]
            summary["pool"] = {
                "n_variants": len(deltas),
                "delta_median": _round(_median(deltas)),
                "delta_min": _round(min(deltas)) if deltas else None,
                "delta_max": _round(max(deltas)) if deltas else None,
            }
        return summary

    def _metrics_of(self, backtest_id: str | None) -> dict[str, Any] | None:
        """读取回测汇总指标，未成功或缺失时返回 None。"""
        if not backtest_id:
            return None
        row = self._backtest_repo.find_by_id(backtest_id)
        if row is None or row.status != "success":
            return None
        return row.metrics or None

    def _baseline_daily_returns(
        self, row: RobustnessRunModel, cost_bps: float | None
    ) -> list[float]:
        """按窗口顺序拼接基线变体的逐日净收益（小数口径）。

        Args:
            row: 批次 ORM 行。
            cost_bps: 成本（基点），留空取系统默认值。

        Returns:
            拼接后的日收益率序列（小数口径），无数据时返回空列表。
        """
        window_labels = [w["label"] for w in (row.windows or [])]
        baseline = next((v for v in (row.variants or []) if v.get("label") == "baseline"), None)
        if baseline is None:
            return []
        bps = cost_bps if cost_bps is not None else get_settings().default_cost_bps
        series: list[float] = []
        for label in window_labels:
            backtest_id = (baseline.get("backtest_ids") or {}).get(label)
            if not backtest_id:
                continue
            daily = self._backtest_svc.get_daily_results(backtest_id)
            returns = [r.portfolio_return for r in daily]
            turnovers = [getattr(r, "turnover", None) for r in daily]
            series.extend(net_return_series(returns, turnovers, float(bps)))
        # 统计函数按小数口径接收收益，避免与百分比口径混用
        return [r / 100 for r in series]

    def _trial_ledger(self, strategy_id: str) -> int:
        """统计该策略历史上的试验次数（变体总数），作为多重检验的 N。

        Args:
            strategy_id: 策略 ID。

        Returns:
            试验次数（至少为 1）。
        """
        try:
            total = (
                self._db.query(sa.func.coalesce(sa.func.sum(RobustnessRunModel.trial_count), 0))
                .filter(RobustnessRunModel.strategy_id == strategy_id)
                .scalar()
            )
        except Exception:
            logger.warning("统计试验次数台账失败", exc_info=True)
            return 1
        return max(1, int(total or 0))

    def _find(self, robustness_id: str) -> RobustnessRunModel | None:
        """读取批次行，不存在时返回 None。"""
        try:
            return (
                self._db.query(RobustnessRunModel)
                .filter(RobustnessRunModel.robustness_id == robustness_id)
                .one_or_none()
            )
        except Exception:
            logger.warning("查询稳健性批次失败：%s", robustness_id, exc_info=True)
            return None

    @staticmethod
    def _to_summary(row: RobustnessRunModel) -> RobustnessSummary:
        """把批次 ORM 行转换为摘要模型。"""
        return RobustnessSummary(
            robustness_id=row.robustness_id,
            strategy_id=row.strategy_id,
            strategy_version=row.strategy_version,
            kind=row.kind,
            status=row.status,
            start_date=row.start_date,
            end_date=row.end_date,
            trial_count=row.trial_count,
            created_at=row.created_at,
            finished_at=row.finished_at,
            error_message=row.error_message,
        )


def build_knob_variants(
    config: dict[str, Any],
    max_knobs: int,
    knobs: list[str] | None = None,
) -> list[dict[str, Any]]:
    """从配置的数值叶子派生单旋钮扰动候选（粗粒度、可解释）。

    整数型参数（周期、持仓数）按 ±25% 取整、至少 1；0-1 之间的浮点（权重、
    仓位）按 ×0.5 / ×1.5（上限 1）；其余浮点按 ±25%。粗粒度档位用来寻找
    "参数高原"而不是历史最优点。

    截断顺序按**业务重要性**（``_KNOB_PRIORITY``：择时阈值 → 过滤阈值 →
    评分权重 → 风险/仓位 → 调仓），而非路径字母序；``knobs`` 显式给出
    "关键旋钮清单"时只扫这些路径（D-2）。

    Args:
        config: 基线配置 JSON。
        max_knobs: 扰动数量上限（按业务重要性截断，同优先级内按路径排序，保证可复现）。
        knobs: 可选的关键旋钮路径清单；给出时只扫描清单内的路径。

    Returns:
        候选列表，元素含 label/kind/knob/value/config。

    Raises:
        ValueError: ``knobs`` 中存在配置里不存在的数值叶子路径时抛出。
    """
    leaves = sorted(iter_numeric_leaves(config), key=lambda item: (_knob_rank(item[0]), item[0]))
    if knobs:
        known = {path for path, _ in leaves}
        unknown = [path for path in knobs if path not in known]
        if unknown:
            raise ValueError(
                "关键旋钮清单包含配置中不存在的数值字段："
                f"{', '.join(unknown)}；可用路径示例：{', '.join(sorted(known)[:5])}"
            )
        wanted = list(dict.fromkeys(knobs))
        by_path = dict(leaves)
        leaves = [(path, by_path[path]) for path in wanted]

    candidates: list[dict[str, Any]] = []
    for path, value in leaves:
        for new_value in _knob_values(path, value):
            if new_value == value:
                continue
            # 每个变体使用独立深拷贝，避免就地改写泄漏到基线与相邻变体
            variant_config = deepcopy(config)
            try:
                _set_leaf(variant_config, path, new_value)
            except (KeyError, TypeError):
                continue
            candidates.append(
                {
                    "label": f"{_slug(path)}_{_slug(str(new_value))}",
                    "kind": "knob",
                    "knob": path,
                    "value": new_value,
                    "config": variant_config,
                }
            )
            if len(candidates) >= max_knobs:
                return candidates
    return candidates


def build_ablation_variants(config: dict[str, Any]) -> list[dict[str, Any]]:
    """派生消融候选：逐个移除评分因子与过滤条件。

    只保留"删掉后模型仍然成立"的变体：评分因子至少保留 1 个，过滤规则同理。
    边际贡献由结果汇总阶段按 Δ夏普与 Δ年化收益给出。

    Args:
        config: 基线配置 JSON。

    Returns:
        候选列表。
    """
    candidates: list[dict[str, Any]] = []
    score = config.get("score") or {}
    factors = dict(score.get("factors") or {})
    if len(factors) > 1:
        for factor_id in sorted(factors):
            variant_config = deepcopy(config)
            variant_score = dict(score)
            variant_score["factors"] = {k: v for k, v in factors.items() if k != factor_id}
            variant_config["score"] = variant_score
            candidates.append(
                {
                    "label": f"ablate_score_{factor_id}",
                    "kind": "ablation",
                    "knob": f"score.factors.{factor_id}",
                    "value": None,
                    "config": variant_config,
                }
            )
    filters = config.get("filters") or {}
    rules = list(filters.get("rules") or [])
    if len(rules) > 1:
        for index, rule in enumerate(rules):
            variant_config = deepcopy(config)
            variant_filters = dict(filters)
            variant_filters["rules"] = [r for i, r in enumerate(rules) if i != index]
            variant_config["filters"] = variant_filters
            factor_label = rule.get("factor", f"rule{index}")
            candidates.append(
                {
                    "label": f"ablate_filter_{_slug(str(factor_label))}",
                    "kind": "ablation",
                    "knob": f"filters.rules[{index}].{factor_label}",
                    "value": None,
                    "config": variant_config,
                }
            )
    return candidates


def iter_numeric_leaves(node: Any, prefix: str = "") -> list[tuple[str, int | float]]:
    """递归收集配置中的数值叶子（跳过列表、布尔与版本号字段）。

    Args:
        node: 当前节点。
        prefix: 当前路径前缀。

    Returns:
        (配置路径, 数值) 列表。
    """
    leaves: list[tuple[str, int | float]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _KNOB_SKIP_KEYS:
                continue
            path = f"{prefix}.{key}" if prefix else str(key)
            leaves.extend(iter_numeric_leaves(value, path))
    elif isinstance(node, list):
        # 列表元素用 ``[下标]`` 定位（如 ``filters.rules[2].value``），
        # 否则过滤阈值这类"列表里的数值"无法被扰动
        for index, value in enumerate(node):
            if not prefix:
                continue
            leaves.extend(iter_numeric_leaves(value, f"{prefix}[{index}]"))
    elif isinstance(node, bool):
        return leaves
    elif isinstance(node, (int, float)):
        if prefix:
            leaves.append((prefix, node))
    return leaves


def _knob_values(path: str, value: int | float) -> list[int | float]:
    """给出单个数值叶子的粗粒度扰动档位。

    仓位/比例类字段（路径含 exposure / ratio / _pct）上限截到 1.0，其余
    浮点不设上限——评分权重是相对权重，允许大于 1，若超出配置约束会在
    校验阶段被自动跳过。

    Args:
        path: 配置路径，用于区分"比例类"与"权重类"字段。
        value: 当前取值。

    Returns:
        扰动后的候选取值列表。
    """
    if isinstance(value, int):
        if value < 0:
            # 负整数阈值（如过滤条件 return_5d > -5）允许继续取负档位，
            # 不能按"周期/持仓数至少为 1"的规则抬到正数
            step = max(1, int(round(abs(value) * 0.25)))
            return [value - step, value + step]
        if value <= 1:
            return [value + 1]
        step = max(1, int(round(abs(value) * 0.25)))
        return [max(1, value - step), value + step]
    if value == 0:
        return [0.05]
    if 0 < value <= 1.0:
        upper = round(value * 1.5, 4)
        if _is_ratio_like(path):
            upper = round(min(1.0, upper), 4)
        return [round(value * 0.5, 4), upper]
    return [round(value * 0.75, 4), round(value * 1.25, 4)]


def _is_ratio_like(path: str) -> bool:
    """判断配置路径是否属于"0-1 比例类"字段。"""
    lowered = path.lower()
    return (
        "exposure" in lowered or "ratio" in lowered or lowered.endswith("_pct") or "cash" in lowered
    )


def _set_leaf(config: dict[str, Any], path: str, value: int | float) -> None:
    """按点号路径写入配置叶子（就地修改，支持 ``name[下标]`` 列表定位）。

    Args:
        config: 配置字典。
        path: 点号分隔的路径。
        value: 目标值。

    Raises:
        KeyError: 路径不存在时抛出。
        TypeError: 中间节点不是字典/列表，或列表下标非法时抛出。
        IndexError: 列表下标越界时抛出。
    """
    parts = path.split(".")
    node: Any = config
    for part in parts[:-1]:
        name, index = _split_index(part)
        if index is None:
            node = node[name]
            continue
        node = node[name][index]
    name, index = _split_index(parts[-1])
    if index is None:
        if not isinstance(node, dict):
            raise TypeError(f"路径 {path} 的父节点不是字典")
        node[name] = value
        return
    if not isinstance(node, dict):
        raise TypeError(f"路径 {path} 的父节点不是字典")
    node[name][index] = value


def _split_index(part: str) -> tuple[str, int | None]:
    """拆分 ``name[下标]`` 形式的路径段。

    Args:
        part: 路径段，如 ``rules[2]`` 或 ``top_n``。

    Returns:
        (名称, 下标或 None)。
    """
    match = re.fullmatch(r"([^\[\]]+)\[(\d+)\]", part)
    if match is None:
        return part, None
    return match.group(1), int(match.group(2))


def _slug(text: str) -> str:
    """把路径或取值转换为可安全用于策略 ID 的短标签。"""
    cleaned = re.sub(r"[^0-9a-zA-Z]+", "_", text).strip("_").lower()
    return cleaned[:40] or "knob"


def build_variant_strategy_id(base_strategy_id: str, robustness_id: str, label: str) -> str:
    """生成不超过 64 字符的变体策略 ID（``strategy_config.strategy_id`` 的列宽）。

    基线 ID 与变体标签都可能较长，直接拼接会超长导致入库失败；
    这里对两段分别截断，标签被截断时追加标签哈希以保证唯一。

    Args:
        base_strategy_id: 基线策略 ID。
        robustness_id: 批次 ID。
        label: 变体标签。

    Returns:
        形如 ``<基线前20>__rb<批次前4>_<标签>`` 的策略 ID，长度 ≤ 64。
    """
    suffix = (
        label if len(label) <= 30 else f"{label[:22]}{md5(label.encode('utf-8')).hexdigest()[:8]}"
    )
    return f"{base_strategy_id[:20]}__rb{robustness_id[:4]}_{suffix}"


def _mean(values: list[float]) -> float | None:
    """计算均值，空列表返回 None。"""
    return sum(values) / len(values) if values else None


def _median(values: list[float]) -> float | None:
    """计算中位数，空列表返回 None。"""
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _round(value: float | None, digits: int = 4) -> float | None:
    """四舍五入，None 透传。"""
    return round(value, digits) if value is not None else None


def _period_boundaries() -> PeriodBoundaries:
    """从系统配置读取研究期/验证期边界。"""
    settings = get_settings()
    return PeriodBoundaries(
        research_start=date.fromisoformat(settings.research_period_start),
        research_end=date.fromisoformat(settings.research_period_end),
        validation_start=date.fromisoformat(settings.validation_period_start),
    )
