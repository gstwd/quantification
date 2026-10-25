"""研究战役仓库：战役、步骤与战役内假设的查询（事务由调用方管理）。"""

from __future__ import annotations

from quant_etf_api.infra.db.models.core import (
    BacktestRunModel,
    ResearchCampaignModel,
    ResearchCampaignStepModel,
    ResearchHypothesisModel,
    RobustnessRunModel,
)
from quant_etf_api.infra.db.repositories.base import BaseRepository


class ResearchCampaignRepository(BaseRepository):
    """research_campaign / research_campaign_step 表的查询仓库。"""

    def find(self, campaign_id: str) -> ResearchCampaignModel | None:
        """按主键查询战役。

        Args:
            campaign_id: 战役 ID。

        Returns:
            战役行，不存在返回 None。
        """
        return self._db.get(ResearchCampaignModel, campaign_id)

    def add(self, model: ResearchCampaignModel | ResearchCampaignStepModel) -> None:
        """把战役或步骤行加入会话（不提交）。

        Args:
            model: ORM 行。
        """
        self._db.add(model)

    def list_campaigns(
        self, status: str | None = None, limit: int = 50
    ) -> list[ResearchCampaignModel]:
        """按创建时间倒序列出战役。

        Args:
            status: 状态过滤。
            limit: 最大条数。

        Returns:
            战役行列表。
        """
        query = self._db.query(ResearchCampaignModel)
        if status:
            query = query.filter(ResearchCampaignModel.status == status)
        return query.order_by(ResearchCampaignModel.created_at.desc()).limit(limit).all()

    def list_steps(self, campaign_id: str) -> list[ResearchCampaignStepModel]:
        """按开始时间升序列出战役的全部步骤。

        Args:
            campaign_id: 战役 ID。

        Returns:
            步骤行列表。
        """
        return (
            self._db.query(ResearchCampaignStepModel)
            .filter(ResearchCampaignStepModel.campaign_id == campaign_id)
            .order_by(ResearchCampaignStepModel.started_at.asc())
            .all()
        )

    def find_step(self, step_id: str) -> ResearchCampaignStepModel | None:
        """按主键查询步骤。

        Args:
            step_id: 步骤 ID。

        Returns:
            步骤行，不存在返回 None。
        """
        return self._db.get(ResearchCampaignStepModel, step_id)

    def find_running_step(self, campaign_id: str) -> ResearchCampaignStepModel | None:
        """查询战役当前运行中的步骤。

        Args:
            campaign_id: 战役 ID。

        Returns:
            运行中的步骤行，没有时返回 None。
        """
        return (
            self._db.query(ResearchCampaignStepModel)
            .filter(
                ResearchCampaignStepModel.campaign_id == campaign_id,
                ResearchCampaignStepModel.status == "running",
            )
            .order_by(ResearchCampaignStepModel.started_at.desc())
            .first()
        )

    def other_active_campaigns(self, campaign_id: str) -> list[str]:
        """查询其他有运行中步骤的战役（同一时间只允许一个战役在跑）。

        Args:
            campaign_id: 当前战役 ID。

        Returns:
            其他活跃战役 ID 列表。
        """
        rows = (
            self._db.query(ResearchCampaignStepModel.campaign_id)
            .join(
                ResearchCampaignModel,
                ResearchCampaignModel.campaign_id == ResearchCampaignStepModel.campaign_id,
            )
            .filter(
                ResearchCampaignStepModel.status == "running",
                ResearchCampaignStepModel.campaign_id != campaign_id,
                ResearchCampaignModel.status == "running",
            )
            .distinct()
            .all()
        )
        return [str(row[0]) for row in rows]

    def list_hypotheses(self, campaign_id: str) -> list[ResearchHypothesisModel]:
        """按登记时间升序列出战役内的假设。

        Args:
            campaign_id: 战役 ID。

        Returns:
            假设行列表。
        """
        return (
            self._db.query(ResearchHypothesisModel)
            .filter(ResearchHypothesisModel.campaign_id == campaign_id)
            .order_by(ResearchHypothesisModel.created_at.asc())
            .all()
        )

    def has_review(self, campaign_id: str, optimization_id: str, verdict: str) -> bool:
        """战役内是否已有评审员对该优化会话给出指定结论。

        只认运行中（评审员刚 review、随后 finish）与已完成的评审步骤：失败步骤里
        留下的结论不可信（agent 中途出错），其重试步骤必须重新评审，不能沿用。

        Args:
            campaign_id: 战役 ID。
            optimization_id: 优化会话 ID。
            verdict: 评审结论。

        Returns:
            存在时为 True。
        """
        row = (
            self._db.query(ResearchCampaignStepModel.step_id)
            .filter(
                ResearchCampaignStepModel.campaign_id == campaign_id,
                ResearchCampaignStepModel.role == "reviewer",
                ResearchCampaignStepModel.optimization_id == optimization_id,
                ResearchCampaignStepModel.outcome == verdict,
                ResearchCampaignStepModel.status.in_(("running", "done")),
            )
            .first()
        )
        return row is not None

    def latest_review(self, optimization_id: str) -> ResearchCampaignStepModel | None:
        """查询某优化会话最近一次评审员结论（任意战役）。

        Args:
            optimization_id: 优化会话 ID。

        Returns:
            评审步骤行；没有评审时为 None。
        """
        return (
            self._db.query(ResearchCampaignStepModel)
            .filter(
                ResearchCampaignStepModel.role == "reviewer",
                ResearchCampaignStepModel.optimization_id == optimization_id,
                ResearchCampaignStepModel.outcome.isnot(None),
            )
            .order_by(ResearchCampaignStepModel.started_at.desc())
            .first()
        )

    def find_desktop_running_step(self) -> ResearchCampaignStepModel | None:
        """查询桌面编排模式下运行中的步骤（有编排会话 ID 的 running 步骤）。

        Returns:
            最近开始的一条；没有时为 None。
        """
        return (
            self._db.query(ResearchCampaignStepModel)
            .filter(
                ResearchCampaignStepModel.status == "running",
                ResearchCampaignStepModel.orchestrator_session_id.isnot(None),
            )
            .order_by(ResearchCampaignStepModel.started_at.desc())
            .first()
        )

    def robustness_strategy(self, robustness_id: str) -> str | None:
        """查询稳健性批次所属的策略 ID（战役按研究线限定批次操作）。

        Args:
            robustness_id: 批次 ID。

        Returns:
            策略 ID；批次不存在时为 None。
        """
        row = (
            self._db.query(RobustnessRunModel.strategy_id)
            .filter(RobustnessRunModel.robustness_id == robustness_id)
            .first()
        )
        return None if row is None else str(row[0])

    def backtest_strategy(self, backtest_id: str) -> str | None:
        """查询回测所属的策略 ID（战役按研究线限定回测操作）。

        Args:
            backtest_id: 回测 ID。

        Returns:
            策略 ID；回测不存在时为 None。
        """
        row = (
            self._db.query(BacktestRunModel.strategy_id)
            .filter(BacktestRunModel.backtest_id == backtest_id)
            .first()
        )
        return None if row is None else str(row[0])

    def hypothesis_campaign(self, hypothesis_id: str) -> tuple[bool, str | None]:
        """查询假设是否存在及其所属战役。

        Args:
            hypothesis_id: 假设 ID。

        Returns:
            (是否存在, 战役 ID)；非战役内登记的假设战役 ID 为 None。
        """
        row = (
            self._db.query(ResearchHypothesisModel.campaign_id)
            .filter(ResearchHypothesisModel.hypothesis_id == hypothesis_id)
            .first()
        )
        if row is None:
            return False, None
        return True, row[0]

    def increment_diagnostics(self, campaign_id: str) -> None:
        """战役诊断计数原子 +1（不提交）。

        Args:
            campaign_id: 战役 ID。
        """
        self._db.query(ResearchCampaignModel).filter(
            ResearchCampaignModel.campaign_id == campaign_id
        ).update(
            {ResearchCampaignModel.diagnostics_used: ResearchCampaignModel.diagnostics_used + 1},
            synchronize_session=False,
        )

    def count_running(self, lineage_id: str) -> int:
        """统计研究线上运行中的战役数。

        Args:
            lineage_id: 研究线 ID。

        Returns:
            战役数。
        """
        return (
            self._db.query(ResearchCampaignModel)
            .filter(
                ResearchCampaignModel.lineage_id == lineage_id,
                ResearchCampaignModel.status == "running",
            )
            .count()
        )
