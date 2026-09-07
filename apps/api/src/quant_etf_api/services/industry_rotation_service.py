"""行业轮动分析服务：单日决策与逐期选择分析（研究预览）。

标准回测已收敛到 BacktestService 行业域分支；本服务只保留研究页需要的
轮动选择预览（analyze_selections）与单日决策，不再维护独立净值模拟。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from quant_etf_api.domain.industry.rebalance import month_end_dates
from quant_etf_api.engine.config import RotationConfig
from quant_etf_api.engine.rotation import IndustryRotationEngine, IndustryRotationInput
from quant_etf_api.infra.db.repositories.industry import IndustryDailyBarRepository
from quant_etf_api.services.industry_factor_service import IndustryFactorService

logger = logging.getLogger(__name__)


class IndustryRotationService:
    """基于行业因子面板执行研报三类轮动信号。"""

    def __init__(self, db: Session) -> None:
        """初始化行业轮动服务。

        Args:
            db: SQLAlchemy 同步 Session。
        """
        self._db = db
        self._factor_service = IndustryFactorService(db)
        self._bar_repo = IndustryDailyBarRepository(db)
        self._engine = IndustryRotationEngine()

    def decision(
        self,
        *,
        trade_date: date,
        rotation: RotationConfig,
        industry_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        """计算单日轮动决策（实时口径）。

        Returns:
            {trade_date, selected_codes, weights, values}。
        """
        panels = self._factor_service.build_panels(
            start=trade_date,
            end=trade_date,
            industry_codes=industry_codes,
            lookback_ratio=rotation.lookback_ratio,
            lookback_mom=rotation.lookback_mom,
            smooth_window=rotation.smooth_window,
            diffusion_lookback=rotation.diffusion_lookback,
            benchmark_exclude=rotation.benchmark_exclude,
            need_rrg=rotation.signal in {"quadrant", "diffusion_rrg"},
            need_diffusion=rotation.signal in {"diffusion", "diffusion_rrg"},
        )
        codes = panels["industry_codes"]
        data = self._input_for_date(trade_date, panels, codes)
        selected, weights = self._engine.select(rotation, data)
        values = {
            code: {
                "rs_ratio": data.rs_ratio.get(code),
                "rs_momentum": data.rs_momentum.get(code),
                "quadrant": data.quadrant.get(code),
                "diffusion": data.diffusion.get(code),
            }
            for code in codes
        }
        return {
            "trade_date": trade_date.isoformat(),
            "selected_codes": selected,
            "weights": weights,
            "values": values,
        }

    def analyze_selections(
        self,
        *,
        start: date,
        end: date,
        rotation: RotationConfig,
        industry_codes: list[str] | None = None,
        monthly: bool = True,
    ) -> dict[str, Any]:
        """只输出轮动选择序列（不做收益模拟，供调试页/API 使用）。"""
        panels, decision_dates, _targets, selections = self._prepare(
            start=start,
            end=end,
            rotation=rotation,
            industry_codes=industry_codes,
            monthly=monthly,
        )
        return {
            "selections": selections,
            "decision_dates": [d.isoformat() for d in decision_dates],
            "industry_codes": panels["industry_codes"],
        }

    def _prepare(
        self,
        *,
        start: date,
        end: date,
        rotation: RotationConfig,
        industry_codes: list[str] | None,
        monthly: bool,
    ) -> tuple[dict[str, Any], list[date], dict[date, dict[str, float]], list[dict[str, Any]]]:
        """加载面板并为决策日生成轮动目标。"""
        panels = self._factor_service.build_panels(
            start=start,
            end=end,
            industry_codes=industry_codes,
            lookback_ratio=rotation.lookback_ratio,
            lookback_mom=rotation.lookback_mom,
            smooth_window=rotation.smooth_window,
            diffusion_lookback=rotation.diffusion_lookback,
            benchmark_exclude=rotation.benchmark_exclude,
            need_rrg=rotation.signal in {"quadrant", "diffusion_rrg"},
            need_diffusion=rotation.signal in {"diffusion", "diffusion_rrg"},
        )
        trading_dates = list(panels["trading_dates"])
        if len(trading_dates) < 2:
            raise ValueError("回测区间可用交易日不足")
        decision_dates = month_end_dates(trading_dates) if monthly else trading_dates
        targets: dict[date, dict[str, float]] = {}
        selections: list[dict[str, Any]] = []
        for decision_date in decision_dates:
            data = self._input_for_date(decision_date, panels, panels["industry_codes"])
            selected, weights = self._engine.select(rotation, data)
            targets[decision_date] = weights
            selections.append(
                {
                    "trade_date": decision_date.isoformat(),
                    "selected_codes": selected,
                    "weights": weights,
                }
            )
        return panels, decision_dates, targets, selections

    @staticmethod
    def _input_for_date(
        trade_date: date,
        panels: dict[str, Any],
        codes: list[str],
    ) -> IndustryRotationInput:
        """从宽表面板中抽取单日因子输入。"""

        def row_values(panel: pd.DataFrame) -> dict[str, float | None]:
            result: dict[str, float | None] = {}
            ts = pd.Timestamp(trade_date)
            if panel.empty or ts not in panel.index:
                return {code: None for code in codes}
            row = panel.loc[ts]
            for code in codes:
                if code not in panel.columns:
                    result[code] = None
                    continue
                value = row.get(code)
                result[code] = None if pd.isna(value) else float(value)
            return result

        quadrant_raw = row_values(panels["quadrant"])
        quadrant = {
            code: (int(value) if value is not None else None)
            for code, value in quadrant_raw.items()
        }
        return IndustryRotationInput(
            trade_date=trade_date,
            industry_codes=codes,
            rs_ratio=row_values(panels["rs_ratio"]),
            rs_momentum=row_values(panels["rs_momentum"]),
            quadrant=quadrant,
            diffusion=row_values(panels["diffusion"]),
        )
