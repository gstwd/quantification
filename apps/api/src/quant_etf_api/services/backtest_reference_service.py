"""回测引用完整性服务（C5）。

回测结果会被多处引用：

- ``robustness_run.variants[*].backtest_ids``（JSONB，无法加外键）；
- ``strategy_optimization.baseline_backtest_id / candidate_backtest_id``（已成外键）；
- ``strategy_optimization.fold_backtests[*].{baseline,candidate}_backtest_id``（JSONB）；
- ``strategy_lifecycle.research_backtest_id / validation_backtest_id``（已成外键）。

删除了 ``backtest_run`` 行之后，JSONB 里的引用无法由数据库约束保护，会变成
"静默 None"（例如 ``robustness collect`` 把不存在的回测当成"仍在排队"，批次
永久停在 running）。本服务把这类悬挂引用变成**可检测、可标记、可清理**的对象。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.infra.db.models.core import (
    BacktestRunModel,
    RobustnessRunModel,
    StrategyOptimizationModel,
)

logger = logging.getLogger(__name__)

#: 引用持有者标识
HOLDER_ROBUSTNESS_VARIANTS = "robustness_run.variants"
HOLDER_OPTIMIZATION = "strategy_optimization"
HOLDER_OPTIMIZATION_FOLDS = "strategy_optimization.fold_backtests"


class BacktestReferenceService:
    """回测引用（含 JSONB 引用）的检测与清理服务。"""

    def __init__(self, db: Session) -> None:
        """初始化服务。

        Args:
            db: SQLAlchemy 同步 Session。
        """
        self._db = db

    def existing_ids(self) -> set[str]:
        """返回当前存在的全部回测 ID 集合。"""
        rows = self._db.query(BacktestRunModel.backtest_id).all()
        return {row[0] for row in rows}

    def scan_holders(self) -> list[dict[str, Any]]:
        """扫描全部引用持有者，返回每条引用记录（含引用的回测 ID 列表）。

        Returns:
            [{holder, holder_id, field, backtest_ids, extra}] 列表。
        """
        holders: list[dict[str, Any]] = []
        for row in self._db.query(RobustnessRunModel).all():
            for variant in list(row.variants or []):
                ids = [bid for bid in (variant.get("backtest_ids") or {}).values() if bid]
                if not ids:
                    continue
                holders.append(
                    {
                        "holder": HOLDER_ROBUSTNESS_VARIANTS,
                        "holder_id": row.robustness_id,
                        "field": f"variants[{variant.get('label')}].backtest_ids",
                        "backtest_ids": ids,
                        "extra": {"label": variant.get("label")},
                    }
                )
        for opt in self._db.query(StrategyOptimizationModel).all():
            direct = [bid for bid in (opt.baseline_backtest_id, opt.candidate_backtest_id) if bid]
            if direct:
                holders.append(
                    {
                        "holder": HOLDER_OPTIMIZATION,
                        "holder_id": opt.optimization_id,
                        "field": "baseline_backtest_id/candidate_backtest_id",
                        "backtest_ids": direct,
                        "extra": {},
                    }
                )
            for fold in list(opt.fold_backtests or []):
                fold_ids = [
                    bid
                    for bid in (
                        fold.get("baseline_backtest_id"),
                        fold.get("candidate_backtest_id"),
                    )
                    if bid
                ]
                if not fold_ids:
                    continue
                holders.append(
                    {
                        "holder": HOLDER_OPTIMIZATION_FOLDS,
                        "holder_id": opt.optimization_id,
                        "field": f"fold_backtests[{fold.get('fold')}]",
                        "backtest_ids": fold_ids,
                        "extra": {"fold": fold.get("fold")},
                    }
                )
        return holders

    def find_referencing_holders(self, backtest_id: str) -> list[dict[str, Any]]:
        """返回引用了指定回测的全部持有者。

        Args:
            backtest_id: 回测标识。

        Returns:
            持有者记录列表（``missing_backtest_ids`` 字段已剔除）。
        """
        hits: list[dict[str, Any]] = []
        for holder in self.scan_holders():
            if backtest_id in holder["backtest_ids"]:
                hits.append(
                    {
                        "holder": holder["holder"],
                        "holder_id": holder["holder_id"],
                        "field": holder["field"],
                    }
                )
        return hits

    def find_dangling_references(self, limit: int = 200) -> dict[str, Any]:
        """汇总全部悬挂引用（引用的回测已不存在）。

        Args:
            limit: 返回条目上限。

        Returns:
            {total, items: [{holder, holder_id, field, missing_backtest_ids}]}。
        """
        existing = self.existing_ids()
        items: list[dict[str, Any]] = []
        total = 0
        for holder in self.scan_holders():
            missing = sorted(set(holder["backtest_ids"]) - existing)
            if not missing:
                continue
            total += len(missing)
            if len(items) < limit:
                items.append(
                    {
                        "holder": holder["holder"],
                        "holder_id": holder["holder_id"],
                        "field": holder["field"],
                        "missing_backtest_ids": missing,
                    }
                )
        return {"total": total, "items": items}

    def missing_ids_for(self, backtest_ids: list[str]) -> list[str]:
        """返回给定 ID 列表中已不存在的部分（保序、去重）。

        Args:
            backtest_ids: 待检查的回测 ID 列表。

        Returns:
            已不存在的回测 ID 列表。
        """
        candidates = [bid for bid in backtest_ids if bid]
        if not candidates:
            return []
        existing = self._db.query(BacktestRunModel.backtest_id).filter(
            BacktestRunModel.backtest_id.in_(candidates)
        )
        existing_ids = {row[0] for row in existing.all()}
        seen: set[str] = set()
        missing: list[str] = []
        for bid in candidates:
            if bid not in existing_ids and bid not in seen:
                seen.add(bid)
                missing.append(bid)
        return missing
