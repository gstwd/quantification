"""策略优化服务：管理 AI 自动优化闭环的会话生命周期。

一次优化会话包含：基线策略、草稿候选策略、评估区间与验证窗口、
全区间与逐折回测 ID、绩效指标与聚合统计、最终优化报告。
系统不内置参数搜索，"训练"由 agent 在轮次之间修改候选配置完成，
本服务只负责静态配置的滚动样本外验证与审计记录。
"""

from __future__ import annotations

import difflib
import json
import logging
import statistics
from datetime import date
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from quant_etf_api.domain.research.walk_forward import compute_folds
from quant_etf_api.engine.config import StrategyConfig
from quant_etf_api.infra.db.base import utcnow
from quant_etf_api.infra.db.models.core import StrategyOptimizationModel
from quant_etf_api.infra.db.repositories.backtest import BacktestRepository
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.infra.db.repositories.optimization import OptimizationRepository
from quant_etf_api.schemas.backtest import BacktestCreateRequest
from quant_etf_api.schemas.strategy import StrategyConfigCreate, StrategyConfigUpdate
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.strategy_config_service import (
    StrategyConfigService,
    compute_config_hash,
)

logger = logging.getLogger(__name__)

# 逐折聚合与报告关注的绩效指标
_FOLD_METRICS: list[str] = [
    "annualized_return_pct",
    "cumulative_return_pct",
    "max_drawdown_pct",
    "sharpe_ratio",
    "sortino_ratio",
    "calmar_ratio",
    "win_rate_pct",
    "benchmark_return_pct",
    "excess_return_pct",
]

# 报告逐折明细中展示的核心指标
_FOLD_DISPLAY_METRICS: list[str] = [
    "annualized_return_pct",
    "cumulative_return_pct",
    "max_drawdown_pct",
    "sharpe_ratio",
]


def _mean(values: list[float]) -> float | None:
    """计算均值，空列表返回 None。"""
    return statistics.mean(values) if values else None


def _median(values: list[float]) -> float | None:
    """计算中位数，空列表返回 None。"""
    return statistics.median(values) if values else None


class OptimizationService:
    """策略优化会话服务，提供 start/evaluate/report/finish 等生命周期操作。"""

    def __init__(self, db: Session) -> None:
        """初始化优化服务。

        Args:
            db: SQLAlchemy Session。
        """
        self._db = db
        self._repo = OptimizationRepository(db)
        self._backtest_repo = BacktestRepository(db)
        self._index_bar_repo = IndexDailyBarRepository(db)
        self._config_svc = StrategyConfigService(db)

    # ── 生命周期 ──────────────────────────────────────────────────────────

    def start(
        self,
        strategy_id: str,
        candidate_config: dict[str, Any],
        hypothesis: str,
        start_date: date,
        end_date: date,
        folds: int = 4,
        candidate_strategy_id: str | None = None,
        candidate_version: str | None = None,
    ) -> dict[str, Any]:
        """开始一次优化会话：创建草稿候选策略并登记会话记录。

        Args:
            strategy_id: 基线策略 ID。
            candidate_config: 候选策略配置 JSON。
            hypothesis: 本轮优化的假设与改动意图。
            start_date: 评估区间起始日期（含）。
            end_date: 评估区间截止日期（含）。
            folds: 验证窗口数量，默认 4。
            candidate_strategy_id: 候选策略 ID，缺省按基线 ID + 会话短 ID 生成。
            candidate_version: 候选版本，缺省继承基线版本。

        Returns:
            会话摘要字典。

        Raises:
            ValueError: 参数不合法、基线/候选校验失败或候选 ID 已存在。
        """
        hypothesis = (hypothesis or "").strip()
        if not hypothesis:
            raise ValueError("hypothesis 不能为空")
        if start_date > end_date:
            raise ValueError("start_date 不能晚于 end_date")
        if folds < 1:
            raise ValueError("folds 必须 >= 1")

        baseline = self._config_svc.get_config(strategy_id)
        if baseline is None:
            raise ValueError(f"基线策略 {strategy_id} 不存在")
        baseline_parsed = self._config_svc.get_parsed_config(strategy_id)
        if baseline_parsed is None:
            raise ValueError(f"基线策略 {strategy_id} 配置解析失败")
        if baseline_parsed.portfolio is None:
            raise ValueError("基线策略未配置 portfolio 模块，无法回测优化")

        validation = self._config_svc.validate_config(candidate_config)
        if not validation.valid:
            raise ValueError(f"候选配置校验失败: {'; '.join(validation.errors)}")
        candidate_config = dict(candidate_config)
        candidate_config.setdefault("schema_version", "1")
        try:
            candidate_parsed = StrategyConfig(
                strategy_id="_candidate_",
                display_name="_candidate_",
                **candidate_config,
            )
        except Exception as exc:
            raise ValueError(f"候选配置解析失败: {exc}") from exc
        if candidate_parsed.portfolio is None:
            raise ValueError("候选策略未配置 portfolio 模块，无法回测")

        optimization_id = uuid4().hex
        cand_id = candidate_strategy_id or f"{strategy_id}__opt_{optimization_id[:8]}"
        if self._config_svc.get_config(cand_id) is not None:
            raise ValueError(f"候选策略 {cand_id} 已存在")
        cand_version = candidate_version or baseline.version

        self._config_svc.create_config(
            StrategyConfigCreate(
                strategy_id=cand_id,
                display_name=f"{baseline.display_name}（优化候选）",
                version=cand_version,
                description=f"基于 {baseline.display_name} 的优化候选",
                frequency=baseline.frequency,
                config_json=candidate_config,
                status="draft",
            )
        )

        model = StrategyOptimizationModel(
            optimization_id=optimization_id,
            strategy_id=strategy_id,
            baseline_version=baseline.version,
            baseline_config_hash=compute_config_hash(baseline.config_json),
            candidate_strategy_id=cand_id,
            candidate_version=cand_version,
            candidate_config_hash=compute_config_hash(candidate_config),
            hypothesis=hypothesis,
            status="running",
            start_date=start_date,
            end_date=end_date,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._repo.create(model)
        logger.info(
            "优化会话开始: %s 基线=%s 候选=%s",
            optimization_id,
            strategy_id,
            cand_id,
        )
        return self._to_dict(model)

    def evaluate(
        self,
        optimization_id: str,
        folds: int | None = None,
        async_mode: bool = False,
    ) -> dict[str, Any]:
        """评估会话：运行全区间与逐折回测并汇总滚动样本外指标。

        Args:
            optimization_id: 优化会话 ID。
            folds: 验证窗口数量，缺省复用会话已有折数，否则默认 4。
            async_mode: True 时仅入队回测任务，由服务端 worker 执行。

        Returns:
            会话摘要字典。

        Raises:
            ValueError: 会话不存在、已结束、区间无数据或回测失败。
        """
        session = self._repo.find_by_id(optimization_id)
        if session is None:
            raise ValueError(f"优化会话 {optimization_id} 不存在")
        if session.status not in ("running", "evaluated"):
            raise ValueError(f"会话已结束（{session.status}），无法再次评估")

        k = folds if folds is not None else (len(session.folds) if session.folds else 4)
        trade_dates = self._index_bar_repo.find_all_trading_dates(
            session.start_date, session.end_date
        )
        if not trade_dates:
            raise ValueError("评估区间内无行情数据，无法切分验证窗口")
        fold_list = [
            {"start": fs.isoformat(), "end": fe.isoformat()}
            for fs, fe in compute_folds(trade_dates, k)
        ]

        baseline_full = self._run_backtest(
            session.strategy_id, session.start_date, session.end_date, optimization_id, async_mode
        )
        candidate_full = self._run_backtest(
            session.candidate_strategy_id,
            session.start_date,
            session.end_date,
            optimization_id,
            async_mode,
        )

        fold_backtests: list[dict[str, Any]] = []
        for i, fold in enumerate(fold_list):
            fs = date.fromisoformat(fold["start"])
            fe = date.fromisoformat(fold["end"])
            b_id = self._run_backtest(
                session.strategy_id, fs, fe, optimization_id, async_mode
            )
            c_id = self._run_backtest(
                session.candidate_strategy_id, fs, fe, optimization_id, async_mode
            )
            fold_backtests.append(
                {
                    "fold": i,
                    "start": fold["start"],
                    "end": fold["end"],
                    "baseline_backtest_id": b_id,
                    "candidate_backtest_id": c_id,
                }
            )

        self._repo.update(
            optimization_id,
            folds=fold_list,
            baseline_backtest_id=baseline_full,
            candidate_backtest_id=candidate_full,
            fold_backtests=fold_backtests,
            updated_at=utcnow(),
        )

        if async_mode:
            logger.info(
                "优化会话 %s 已入队 %d 个回测任务", optimization_id, 2 + 2 * len(fold_list)
            )
            return self._to_dict(self._repo.find_by_id(optimization_id))

        self._finalize(session)
        return self._to_dict(self._repo.find_by_id(optimization_id))

    def finish(
        self,
        optimization_id: str,
        verdict: str,
        report_text: str | None = None,
        promote: bool = False,
        strict: bool = False,
    ) -> dict[str, Any]:
        """结束会话：记录结论与报告，可选将候选配置提升为基线。

        Args:
            optimization_id: 优化会话 ID。
            verdict: 结论，accept=接受，reject=拒绝。
            report_text: 最终优化报告 Markdown，可选。
            promote: verdict=accept 时是否把候选配置写回基线策略。
            strict: 是否强制验收清单全部通过才允许 accept。

        Returns:
            会话摘要字典。

        Raises:
            ValueError: 会话状态不合法、严格验收未通过或 promote 失败。
        """
        if verdict not in ("accept", "reject"):
            raise ValueError("verdict 必须为 accept 或 reject")
        session = self._repo.find_by_id(optimization_id)
        if session is None:
            raise ValueError(f"优化会话 {optimization_id} 不存在")
        if session.status not in ("running", "evaluated"):
            raise ValueError(f"会话当前状态为 {session.status}，无法结束")

        if session.fold_summary is None:
            finalized = self._finalize_if_ready(session)
            if not finalized:
                raise ValueError("会话仍有回测未完成，无法结束")

        if verdict == "accept" and strict:
            failed = [item for item in self._acceptance_checklist(session) if not item["pass"]]
            if failed:
                details = "; ".join(item["description"] for item in failed)
                raise ValueError(f"严格验收未通过: {details}")

        fields: dict[str, Any] = {
            "status": "accepted" if verdict == "accept" else "rejected",
            "finished_at": utcnow(),
            "updated_at": utcnow(),
        }
        if report_text is not None:
            fields["report"] = report_text

        if promote and verdict == "accept":
            candidate = self._config_svc.get_config(session.candidate_strategy_id)
            if candidate is None:
                raise ValueError("候选策略不存在，无法 promote")
            self._config_svc.update_config(
                session.strategy_id,
                StrategyConfigUpdate(
                    config_json=candidate.config_json,
                    version=session.candidate_version,
                ),
            )
            logger.info(
                "优化会话 %s promote：候选配置已写回基线 %s",
                optimization_id,
                session.strategy_id,
            )

        self._repo.update(optimization_id, **fields)
        return self._to_dict(self._repo.find_by_id(optimization_id))

    # ── 查询与报告 ────────────────────────────────────────────────────────

    def show(self, optimization_id: str) -> dict[str, Any] | None:
        """返回会话详情；异步回测全部完成后自动补齐指标汇总。"""
        session = self._repo.find_by_id(optimization_id)
        if session is None:
            return None
        if session.fold_summary is None:
            self._finalize_if_ready(session)
            session = self._repo.find_by_id(optimization_id)
        return self._to_dict(session)

    def list(
        self,
        strategy_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """返回会话列表摘要，按创建时间倒序。"""
        rows = self._repo.find_all(strategy_id=strategy_id, limit=limit)
        return [self._to_dict(r) for r in rows]

    def generate_report(self, optimization_id: str) -> str:
        """生成优化报告 Markdown 骨架，供 agent 补充分析结论。

        Args:
            optimization_id: 优化会话 ID。

        Returns:
            Markdown 报告全文。
        """
        session = self._repo.find_by_id(optimization_id)
        if session is None:
            raise ValueError(f"优化会话 {optimization_id} 不存在")
        if session.fold_summary is None:
            self._finalize_if_ready(session)
            session = self._repo.find_by_id(optimization_id)

        baseline = self._config_svc.get_config(session.strategy_id)
        candidate = self._config_svc.get_config(session.candidate_strategy_id)
        lines: list[str] = []
        lines.append("# 策略优化报告")
        lines.append("")
        lines.append("## 会话信息")
        lines.append("")
        lines.append("| 字段 | 值 |")
        lines.append("| --- | --- |")
        lines.append(f"| 优化会话 | {session.optimization_id} |")
        lines.append(f"| 基线策略 | {session.strategy_id} (v{session.baseline_version}) |")
        lines.append(
            f"| 候选策略 | {session.candidate_strategy_id} (v{session.candidate_version}) |"
        )
        lines.append(f"| 状态 | {session.status} |")
        lines.append(f"| 评估区间 | {session.start_date} ~ {session.end_date} |")
        lines.append(f"| 验证窗口数 | {len(session.folds or [])} |")
        lines.append("")
        lines.append("## 假设")
        lines.append("")
        lines.append(session.hypothesis)
        lines.append("")
        lines.append("## 配置变更")
        lines.append("")
        if baseline is not None and candidate is not None:
            old_text = json.dumps(
                baseline.config_json, ensure_ascii=False, indent=2, sort_keys=True
            ).splitlines()
            new_text = json.dumps(
                candidate.config_json, ensure_ascii=False, indent=2, sort_keys=True
            ).splitlines()
            diff = difflib.unified_diff(
                old_text,
                new_text,
                fromfile=f"基线 {session.strategy_id}",
                tofile=f"候选 {session.candidate_strategy_id}",
                lineterm="",
            )
            lines.append("```diff")
            lines.extend(diff)
            lines.append("```")
        else:
            lines.append("（基线或候选配置不可用）")
        lines.append("")
        lines.append("## 全区间绩效")
        lines.append("")
        lines.append("| 指标 | 基线 | 候选 |")
        lines.append("| --- | --- | --- |")
        full = session.metrics_full or {}
        for metric in _FOLD_METRICS:
            base_val = (full.get("baseline") or {}).get(metric)
            cand_val = (full.get("candidate") or {}).get(metric)
            lines.append(f"| {metric} | {_fmt_metric(base_val)} | {_fmt_metric(cand_val)} |")
        lines.append("")
        lines.append("## 滚动样本外验证")
        lines.append("")
        lines.append("### 逐折核心指标")
        lines.append("")
        lines.append("| 折 | 区间 | 指标 | 基线 | 候选 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for fold in session.metrics_folds or []:
            fold_base = fold.get("baseline") or {}
            fold_cand = fold.get("candidate") or {}
            for metric in _FOLD_DISPLAY_METRICS:
                lines.append(
                    "| "
                    f"{fold.get('fold')} | {fold.get('start')} ~ {fold.get('end')} "
                    f"| {metric} | {_fmt_metric(fold_base.get(metric))} "
                    f"| {_fmt_metric(fold_cand.get(metric))} |"
                )
        lines.append("")
        lines.append("### 逐折聚合")
        lines.append("")
        lines.append("| 指标 | 基线均值 | 候选均值 | 基线中位数 | 候选中位数 | 候选胜出 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        summary_metrics = (session.fold_summary or {}).get("metrics", {})
        for metric in _FOLD_METRICS:
            row = summary_metrics.get(metric, {})
            lines.append(
                "| "
                f"{metric} | {_fmt_metric(row.get('baseline_mean'))} "
                f"| {_fmt_metric(row.get('candidate_mean'))} "
                f"| {_fmt_metric(row.get('baseline_median'))} "
                f"| {_fmt_metric(row.get('candidate_median'))} "
                f"| {row.get('candidate_wins')}/{row.get('total_folds', '-')} |"
            )
        lines.append("")
        lines.append("## 验收清单")
        lines.append("")
        for item in self._acceptance_checklist(session):
            mark = "x" if item["pass"] else " "
            lines.append(f"- [{mark}] {item['description']}")
        lines.append("")
        lines.append("## 分析结论")
        lines.append("")
        lines.append("（待填写：本轮假设是否成立、数据支持与风险、下一步优化方向）")
        lines.append("")
        return "\n".join(lines)

    # ── 内部辅助 ──────────────────────────────────────────────────────────

    def _run_backtest(
        self,
        strategy_id: str,
        start: date,
        end: date,
        optimization_id: str,
        async_mode: bool,
    ) -> str:
        """创建并（同步）执行单个回测，或异步入队。

        Args:
            strategy_id: 策略 ID。
            start: 回测起始日期。
            end: 回测截止日期。
            optimization_id: 关联的优化会话 ID。
            async_mode: True 时仅入队。

        Returns:
            回测 ID。

        Raises:
            ValueError: 回测创建或同步执行失败。
        """
        svc = BacktestService(self._db)
        summary = svc.create_backtest(
            BacktestCreateRequest(
                strategy_id=strategy_id,
                start_date=start,
                end_date=end,
            ),
            optimization_id=optimization_id,
        )
        if async_mode:
            from quant_etf_api.infra.job_queue.queue import get_job_queue

            get_job_queue().enqueue("backtest", {"backtest_id": summary.backtest_id})
            return summary.backtest_id
        svc.run_backtest(summary.backtest_id)
        row = self._backtest_repo.find_by_id(summary.backtest_id)
        if row is None or row.status != "success":
            message = row.error_message if row is not None else "回测记录不存在"
            raise ValueError(f"回测 {summary.backtest_id} 执行失败: {message}")
        return summary.backtest_id

    def _finalize(self, session: StrategyOptimizationModel) -> None:
        """同步评估后汇总全区间与逐折指标并落库。"""
        metrics_full = {
            "baseline": self._collect_metrics(session.baseline_backtest_id),
            "candidate": self._collect_metrics(session.candidate_backtest_id),
        }
        metrics_folds: list[dict[str, Any]] = []
        for fold in session.fold_backtests or []:
            metrics_folds.append(
                {
                    "fold": fold["fold"],
                    "start": fold["start"],
                    "end": fold["end"],
                    "baseline": self._collect_metrics(fold["baseline_backtest_id"]),
                    "candidate": self._collect_metrics(fold["candidate_backtest_id"]),
                }
            )
        self._repo.update(
            session.optimization_id,
            metrics_full=metrics_full,
            metrics_folds=metrics_folds,
            fold_summary=_compute_fold_summary(metrics_folds),
            status="evaluated",
            updated_at=utcnow(),
        )

    def _finalize_if_ready(self, session: StrategyOptimizationModel) -> bool:
        """异步模式下所有回测终态后补齐指标汇总。

        Returns:
            是否已进入终态（成功补齐或标记失败）。
        """
        ids: list[str] = []
        if session.baseline_backtest_id:
            ids.append(session.baseline_backtest_id)
        if session.candidate_backtest_id:
            ids.append(session.candidate_backtest_id)
        for fold in session.fold_backtests or []:
            ids.append(fold["baseline_backtest_id"])
            ids.append(fold["candidate_backtest_id"])
        if not ids:
            return False

        rows = [self._backtest_repo.find_by_id(bid) for bid in ids]
        if any(r is None or r.status not in ("success", "failed") for r in rows):
            return False
        if any(r.status == "failed" for r in rows):
            self._repo.update(
                session.optimization_id,
                status="failed",
                finished_at=utcnow(),
                updated_at=utcnow(),
            )
            return True
        self._finalize(session)
        return True

    def _collect_metrics(self, backtest_id: str | None) -> dict[str, Any] | None:
        """读取回测汇总指标，未成功或缺失时返回 None。"""
        if not backtest_id:
            return None
        row = self._backtest_repo.find_by_id(backtest_id)
        if row is None or row.status != "success":
            return None
        return row.metrics or None

    def _acceptance_checklist(
        self, session: StrategyOptimizationModel
    ) -> list[dict[str, Any]]:
        """按默认阈值计算验收清单（严格模式强制全部通过）。"""
        summary_metrics = (session.fold_summary or {}).get("metrics", {})
        sharpe = summary_metrics.get("sharpe_ratio", {})
        drawdown = summary_metrics.get("max_drawdown_pct", {})
        cumulative = summary_metrics.get("cumulative_return_pct", {})
        total_folds = int(sharpe.get("total_folds") or 0)

        items: list[dict[str, Any]] = []
        items.append(
            self._check_item(
                "sharpe_nonnegative",
                "验证窗平均夏普 ≥ 基线（Δ≥0）",
                _ge(sharpe.get("candidate_mean"), sharpe.get("baseline_mean")),
            )
        )
        base_dd = drawdown.get("baseline_mean")
        cand_dd = drawdown.get("candidate_mean")
        items.append(
            self._check_item(
                "drawdown_limited",
                "验证窗平均最大回撤劣化 ≤ 2pct",
                cand_dd is None or base_dd is None or cand_dd <= (base_dd + 2.0),
            )
        )
        wins = int(sharpe.get("candidate_wins") or 0)
        threshold = (total_folds + 1) // 2
        items.append(
            self._check_item(
                "sharpe_majority",
                f"夏普胜出折数 ≥ 50%（{wins}/{total_folds}）",
                total_folds > 0 and wins >= threshold,
            )
        )
        items.append(
            self._check_item(
                "return_nonnegative",
                "验证窗平均累计收益 ≥ 基线（Δ≥0）",
                _ge(cumulative.get("candidate_mean"), cumulative.get("baseline_mean")),
            )
        )
        return items

    @staticmethod
    def _check_item(key: str, description: str, passed: bool) -> dict[str, Any]:
        """构造单条验收清单项。"""
        return {
            "key": key,
            "description": description,
            "pass": passed,
        }

    @staticmethod
    def _to_dict(model: StrategyOptimizationModel) -> dict[str, Any]:
        """将 ORM 行转换为字典（供 CLI JSON 输出）。"""
        return {column.name: getattr(model, column.name) for column in model.__table__.columns}


def _compute_fold_summary(metrics_folds: list[dict[str, Any]]) -> dict[str, Any]:
    """计算逐折指标的均值、中位数与候选胜出折数。

    Args:
        metrics_folds: 逐折指标列表。

    Returns:
        聚合统计字典，形如 {"total_folds": n, "metrics": {指标: {...}}}。
    """
    summary: dict[str, Any] = {"total_folds": len(metrics_folds), "metrics": {}}
    for metric in _FOLD_METRICS:
        baseline_values: list[float] = []
        candidate_values: list[float] = []
        for fold in metrics_folds:
            base_val = (fold.get("baseline") or {}).get(metric)
            cand_val = (fold.get("candidate") or {}).get(metric)
            if isinstance(base_val, (int, float)):
                baseline_values.append(float(base_val))
            if isinstance(cand_val, (int, float)):
                candidate_values.append(float(cand_val))
        summary["metrics"][metric] = {
            "baseline_mean": _mean(baseline_values),
            "candidate_mean": _mean(candidate_values),
            "baseline_median": _median(baseline_values),
            "candidate_median": _median(candidate_values),
            "candidate_wins": sum(
                1 for a, b in zip(baseline_values, candidate_values) if b > a
            ),
            "total_folds": len(metrics_folds),
        }
    return summary


def _fmt_metric(value: Any) -> str:
    """格式化指标值，None 显示为 '-'。"""
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _ge(candidate: Any, baseline: Any) -> bool:
    """候选 >= 基线，任一缺失视为不通过。"""
    if candidate is None or baseline is None:
        return False
    return candidate >= baseline
