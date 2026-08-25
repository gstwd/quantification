"""策略实时执行服务（兼容门面）。

编排逻辑已收敛到 StrategyDecisionService（统一执行入口，对应 C1），
本类保留 execute() 签名以兼容任务处理器与既有导入路径。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.engine.config import StrategyConfig
from quant_etf_api.infra.db.repositories.research_run import ResearchRunRepository
from quant_etf_api.services.strategy_decision_service import StrategyDecisionService


class StrategyExecutionService:
    """策略实时执行服务（委托统一决策服务持久化执行）。"""

    def __init__(
        self,
        db: Session,
        run_repo: ResearchRunRepository | None = None,
    ) -> None:
        """初始化策略执行服务。

        Args:
            db: SQLAlchemy 同步 Session。
            run_repo: 运行记录仓库，未提供时自动创建。
        """
        self._db = db
        self._decision_svc = StrategyDecisionService(db, run_repo=run_repo)

    def execute(
        self,
        config: StrategyConfig,
        trade_date: date,
        run_id: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        """执行单日策略信号计算并持久化（委托统一决策服务）。

        Args:
            config: 策略配置。
            trade_date: 运行对应的交易日。
            run_id: 研究运行 ID。
            params: 策略参数覆盖（暂未使用）。
        """
        self._decision_svc.run_and_persist(config, trade_date, run_id, params)
