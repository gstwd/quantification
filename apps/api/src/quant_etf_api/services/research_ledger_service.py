"""研究台账服务：登记假设、记录试验、关闭结论，并为多重检验提供试验次数 N。

台账回答三个问题：
1. 这个方向以前试过吗、结论是什么（新会话开工前先查 ``ledger``）；
2. 这次检验挂在哪个假设下、事先定的判定标准是什么；
3. 该策略累计做过多少次试验（Deflated Sharpe 的 N，含不落回测的研究批量评估）。

规则（状态流转、N 的拆分口径）放在 ``domain.research.ledger``；本服务只做读写编排。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from quant_etf_api.config.settings import get_settings
from quant_etf_api.domain.research import ledger as rules
from quant_etf_api.infra.db.models.core import ResearchHypothesisModel, ResearchTrialModel
from quant_etf_api.infra.db.repositories.research_ledger import ResearchLedgerRepository
from quant_etf_api.infra.time import utcnow_aware
from quant_etf_api.services.strategy_config_service import StrategyConfigService

logger = logging.getLogger(__name__)


class ResearchLedgerService:
    """研究假设与研究试验的读写编排服务。"""

    def __init__(self, db: Session) -> None:
        """初始化服务。

        Args:
            db: 数据库会话。
        """
        self._db = db
        self._repo = ResearchLedgerRepository(db)
        self._config_svc = StrategyConfigService(db)

    # ------------------------------------------------------------------
    # 假设
    # ------------------------------------------------------------------

    def add_hypothesis(
        self,
        *,
        statement: str,
        category: str,
        criteria: str,
        strategy_id: str | None = None,
        index_codes: Sequence[str] | None = None,
        tags: Sequence[str] | None = None,
        rationale: str | None = None,
        execution_models: Sequence[str] | None = None,
        parent_id: str | None = None,
        created_by: str | None = None,
        campaign_id: str | None = None,
    ) -> dict[str, Any]:
        """登记一个研究假设。

        Args:
            statement: 假设陈述。
            category: 改动类别（见 ``HYPOTHESIS_CATEGORIES``）。
            criteria: 事先写定的判定标准。
            strategy_id: 关联策略 ID（指数级假设可为空）。
            index_codes: 涉及的指数代码。
            tags: 标签。
            rationale: 提出依据。
            execution_models: 必须通过的执行模型。
            parent_id: 父假设 ID。
            created_by: 登记者标识。
            campaign_id: 所属研究战役（战役模式下由 CLI 自动带上）。

        Returns:
            新假设的字典表示。

        Raises:
            ValueError: 参数非法、策略或父假设不存在。
        """
        text = (statement or "").strip()
        if not text:
            raise ValueError("假设陈述不能为空")
        rules.validate_category(category)
        pre_registration = rules.build_pre_registration(criteria, execution_models)
        if strategy_id and self._config_svc.get_config(strategy_id) is None:
            raise ValueError(f"策略 {strategy_id} 不存在")
        if parent_id and self._repo.find_hypothesis(parent_id) is None:
            raise ValueError(f"父假设 {parent_id} 不存在")
        now = utcnow_aware()
        model = ResearchHypothesisModel(
            hypothesis_id=f"h{uuid4().hex[:12]}",
            strategy_id=strategy_id,
            index_codes=_clean_list(index_codes),
            tags=_clean_list(tags),
            category=category,
            statement=text,
            rationale=(rationale or "").strip() or None,
            pre_registration=pre_registration,
            status=rules.STATUS_OPEN,
            parent_id=parent_id,
            created_by=created_by,
            campaign_id=campaign_id,
            created_at=now,
            updated_at=now,
        )
        self._repo.add(model)
        self._db.commit()
        logger.info("登记研究假设 %s（%s）", model.hypothesis_id, category)
        return _hypothesis_dict(model)

    def require_active_hypothesis(self, hypothesis_id: str) -> ResearchHypothesisModel:
        """读取一个仍可挂试验的假设（供优化会话 / 稳健性批次创建前校验）。

        Args:
            hypothesis_id: 假设 ID。

        Returns:
            假设行。

        Raises:
            ValueError: 假设不存在或已关闭。
        """
        model = self._repo.find_hypothesis(hypothesis_id)
        if model is None:
            raise ValueError(f"假设 {hypothesis_id} 不存在")
        rules.ensure_accepts_trials(hypothesis_id, model.status)
        return model

    def get_hypothesis(self, hypothesis_id: str) -> dict[str, Any] | None:
        """查看假设详情（含其下全部试验）。

        Args:
            hypothesis_id: 假设 ID。

        Returns:
            假设字典（``trials`` 为试验列表），不存在返回 None。
        """
        model = self._repo.find_hypothesis(hypothesis_id)
        if model is None:
            return None
        trials = self._repo.list_trials(hypothesis_id=hypothesis_id, limit=500)
        result = _hypothesis_dict(model)
        result["trials"] = [_trial_dict(item) for item in trials]
        result["children"] = [
            {"hypothesis_id": child.hypothesis_id, "status": child.status}
            for child in self._repo.list_children(hypothesis_id)
        ]
        return result

    def list_hypotheses(
        self,
        *,
        strategy_id: str | None = None,
        index_code: str | None = None,
        tag: str | None = None,
        statuses: Sequence[str] | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """按条件列出假设（附各自的试验行数）。

        Args:
            strategy_id: 关联策略 ID 过滤。
            index_code: 指数代码过滤。
            tag: 标签过滤。
            statuses: 状态过滤。
            limit: 最大返回条数。

        Returns:
            假设字典列表，按登记时间倒序。

        Raises:
            ValueError: 状态值非法。
        """
        for status in statuses or []:
            if status not in rules.ALL_STATUSES:
                raise ValueError(
                    f"未知的假设状态 {status!r}，可选：{', '.join(rules.ALL_STATUSES)}"
                )
        rows = self._repo.list_hypotheses(
            strategy_id=strategy_id,
            index_code=index_code,
            tag=tag,
            statuses=list(statuses) if statuses else None,
            limit=limit,
        )
        counts = self._repo.trial_counts_by_hypothesis([row.hypothesis_id for row in rows])
        result = []
        for row in rows:
            item = _hypothesis_dict(row)
            item["trial_rows"] = counts.get(row.hypothesis_id, 0)
            result.append(item)
        return result

    def close_hypothesis(self, hypothesis_id: str, verdict: str, reason: str) -> dict[str, Any]:
        """关闭假设并写入结论。

        Args:
            hypothesis_id: 假设 ID。
            verdict: 终态（accepted/rejected/inconclusive/blocked_by_schema）。
            reason: 结论原因。

        Returns:
            更新后的假设字典。

        Raises:
            ValueError: 假设不存在或校验失败。
        """
        model = self._repo.find_hypothesis(hypothesis_id)
        if model is None:
            raise ValueError(f"假设 {hypothesis_id} 不存在")
        conclusion = rules.validate_close(model.status, verdict, reason)
        now = utcnow_aware()
        model.status = verdict
        model.conclusion = conclusion
        model.closed_at = now
        model.updated_at = now
        self._db.commit()
        logger.info("关闭研究假设 %s：%s", hypothesis_id, verdict)
        return _hypothesis_dict(model)

    def reopen_hypothesis(self, hypothesis_id: str, reason: str) -> dict[str, Any]:
        """重新打开受 schema 限制而搁置的假设。

        原结论保留在 ``conclusion`` 中并追加重开原因，便于审计。

        Args:
            hypothesis_id: 假设 ID。
            reason: 重开原因（如"已支持 5 档仓位"）。

        Returns:
            更新后的假设字典。

        Raises:
            ValueError: 假设不存在、状态不允许重开或原因为空。
        """
        model = self._repo.find_hypothesis(hypothesis_id)
        if model is None:
            raise ValueError(f"假设 {hypothesis_id} 不存在")
        rules.validate_reopen(model.status)
        text = (reason or "").strip()
        if not text:
            raise ValueError("重新打开假设必须写明原因（--reason）")
        now = utcnow_aware()
        model.conclusion = (
            f"{model.conclusion or ''}\n[重开 {now.date().isoformat()}] {text}".strip()
        )
        model.status = rules.STATUS_OPEN
        model.closed_at = None
        model.updated_at = now
        self._db.commit()
        return _hypothesis_dict(model)

    # ------------------------------------------------------------------
    # 试验
    # ------------------------------------------------------------------

    def record_trial(
        self,
        *,
        kind: str,
        strategy_id: str | None,
        trial_count: int,
        hypothesis_id: str | None = None,
        ref_id: str | None = None,
        execution_model: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        config_hashes: Sequence[str] | None = None,
        summary: Mapping[str, Any] | None = None,
        note: str | None = None,
        is_estimated: bool = False,
    ) -> dict[str, Any]:
        """记录一次试验，并把所属假设从 open 推进到 testing。

        Args:
            kind: 来源（research_batch/manual/optimization/robustness）。
            strategy_id: 试验针对的策略 ID（N 按此归属）。
            trial_count: 独立变体数。
            hypothesis_id: 所属假设 ID。
            ref_id: 关联的优化会话 / 稳健性批次 ID。
            execution_model: 执行模型。
            start_date: 评估区间起点。
            end_date: 评估区间终点。
            config_hashes: 变体配置哈希列表。
            summary: 关键指标摘要。
            note: 备注。
            is_estimated: 是否为历史补录估算值。

        Returns:
            试验字典。

        Raises:
            ValueError: 来源非法、次数为负、假设不存在或已关闭。
        """
        if kind not in rules.TRIAL_KINDS:
            raise ValueError(f"未知的试验来源 {kind!r}，可选：{', '.join(rules.TRIAL_KINDS)}")
        if not strategy_id and kind != "diagnostic":
            raise ValueError("试验必须归属一个策略（只有派生因子诊断记录可以不挂策略）")
        if trial_count < 0:
            raise ValueError("试验次数不能为负")
        hypothesis = self.require_active_hypothesis(hypothesis_id) if hypothesis_id else None
        model = ResearchTrialModel(
            trial_id=uuid4().hex,
            hypothesis_id=hypothesis_id,
            strategy_id=strategy_id,
            kind=kind,
            ref_id=ref_id,
            execution_model=execution_model,
            start_date=start_date,
            end_date=end_date,
            trial_count=int(trial_count),
            is_estimated=is_estimated,
            config_hashes=list(config_hashes) if config_hashes else None,
            summary=dict(summary) if summary else None,
            note=(note or "").strip() or None,
            created_at=utcnow_aware(),
        )
        self._repo.add(model)
        if hypothesis is not None:
            new_status = rules.status_after_trial(hypothesis.status)
            if new_status != hypothesis.status:
                hypothesis.status = new_status
                hypothesis.updated_at = utcnow_aware()
        self._db.commit()
        return _trial_dict(model)

    def record_manual_trial(
        self,
        *,
        strategy_id: str,
        trial_count: int,
        note: str,
        hypothesis_id: str | None = None,
        execution_model: str | None = None,
        is_estimated: bool = False,
    ) -> dict[str, Any]:
        """记录平台外的人工 / 原型对比，或补录历史估算的试验次数。

        Args:
            strategy_id: 策略 ID（必须存在）。
            trial_count: 试验次数（必须为正）。
            note: 依据说明（必填，如"left-side 原型 v1/v2/H2/S2 四种方案"）。
            hypothesis_id: 所属假设 ID。
            execution_model: 执行模型。
            is_estimated: 是否为估算值。

        Returns:
            试验字典。

        Raises:
            ValueError: 策略不存在、次数非正或说明为空。
        """
        if self._config_svc.get_config(strategy_id) is None:
            raise ValueError(f"策略 {strategy_id} 不存在")
        if trial_count <= 0:
            raise ValueError("人工试验次数必须为正")
        if not (note or "").strip():
            raise ValueError("人工 / 补录试验必须写明依据（--note）")
        return self.record_trial(
            kind="manual",
            strategy_id=strategy_id,
            trial_count=trial_count,
            hypothesis_id=hypothesis_id,
            execution_model=execution_model,
            note=note,
            is_estimated=is_estimated,
        )

    def record_research_batch(
        self,
        *,
        strategy_id: str,
        variants: Sequence[Mapping[str, Any]],
        windows: Sequence[Mapping[str, Any]],
        execution_model: str,
        config_hashes: Sequence[str] | None = None,
        hypothesis_id: str | None = None,
    ) -> dict[str, Any]:
        """把一次研究批量评估记入台账（不论是否挂假设都计入 N）。

        Args:
            strategy_id: 基线策略 ID。
            variants: ``ResearchBatchResult.variants``。
            windows: ``ResearchBatchResult.windows``。
            execution_model: 执行模型。
            config_hashes: 被评估变体的配置哈希。
            hypothesis_id: 所属假设 ID。

        Returns:
            试验字典。
        """
        count, summary = rules.summarize_research_batch(variants)
        start = _parse_date(windows[0].get("start")) if windows else None
        end = _parse_date(windows[-1].get("end")) if windows else None
        return self.record_trial(
            kind="research_batch",
            strategy_id=strategy_id,
            trial_count=count,
            hypothesis_id=hypothesis_id,
            execution_model=execution_model,
            start_date=start,
            end_date=end,
            config_hashes=config_hashes,
            summary=summary,
        )

    def link_session(
        self,
        *,
        kind: str,
        ref_id: str,
        strategy_id: str,
        hypothesis_id: str,
        trial_count: int,
        execution_model: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, Any]:
        """把优化会话或稳健性批次挂到假设下（关联行，不重复计入 N）。

        Args:
            kind: optimization 或 robustness。
            ref_id: 会话 / 批次 ID。
            strategy_id: 策略 ID。
            hypothesis_id: 假设 ID。
            trial_count: 变体数（仅展示）。
            execution_model: 执行模型。
            start_date: 评估区间起点。
            end_date: 评估区间终点。

        Returns:
            试验字典。

        Raises:
            ValueError: 来源不是 optimization / robustness。
        """
        if kind not in ("optimization", "robustness"):
            raise ValueError("只有优化会话与稳健性批次可以作为关联试验挂到假设下")
        return self.record_trial(
            kind=kind,
            strategy_id=strategy_id,
            trial_count=trial_count,
            hypothesis_id=hypothesis_id,
            ref_id=ref_id,
            execution_model=execution_model,
            start_date=start_date,
            end_date=end_date,
        )

    # ------------------------------------------------------------------
    # 派生因子诊断门槛
    # ------------------------------------------------------------------

    def record_derived_diagnostic(
        self,
        *,
        fingerprint: str,
        expression: str,
        diagnostic: str,
        strategy_id: str | None = None,
        summary: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """记录一次派生因子诊断（不计入 N），满足派生因子的前置门槛。

        Args:
            fingerprint: 派生因子指纹（与别名命名无关）。
            expression: 规范化表达式。
            diagnostic: 诊断命令（factor-ic / buckets）。
            strategy_id: 提供别名上下文的策略（可空）。
            summary: 关键诊断数字。

        Returns:
            试验字典。
        """
        return self.record_trial(
            kind="diagnostic",
            strategy_id=strategy_id,
            trial_count=0,
            ref_id=fingerprint,
            summary={"expression": expression, "diagnostic": diagnostic, **dict(summary or {})},
            note=f"派生因子诊断（{diagnostic}）：{expression}",
        )

    def undiagnosed_derived_factors(self, config: Any) -> list[dict[str, str]]:
        """列出配置中尚未做过诊断的派生因子。

        Args:
            config: 已解析的 StrategyConfig。

        Returns:
            [{"alias", "expression", "fingerprint"}]；全部诊断过或没有派生因子时为空。
        """
        from quant_etf_api.factors.builtins.derived import (  # noqa: PLC0415
            DERIVED_TEMPLATE_ID,
            ExpressionComputer,
        )
        from quant_etf_api.factors.catalog import get_factor_template_registry  # noqa: PLC0415

        registry = get_factor_template_registry()
        missing: list[dict[str, str]] = []
        for alias, declaration in config.factor_aliases.items():
            if declaration.template_id != DERIVED_TEMPLATE_ID:
                continue
            computer = registry.resolve(alias, config.factor_aliases).computer
            if not isinstance(computer, ExpressionComputer):
                continue
            fingerprint = computer.fingerprint()
            if not self._repo.has_diagnostic(fingerprint):
                missing.append(
                    {
                        "alias": alias,
                        "expression": computer.parsed.text,
                        "fingerprint": fingerprint,
                    }
                )
        return missing

    def ensure_derived_diagnosed(self, config: Any, where: str) -> None:
        """派生因子前置门槛：未诊断的派生因子不得进入研究批量评估或优化候选。

        Args:
            config: 已解析的 StrategyConfig。
            where: 出错位置（如变体标签），用于错误信息。

        Raises:
            ValueError: 存在未诊断的派生因子。
        """
        missing = self.undiagnosed_derived_factors(config)
        if not missing:
            return
        items = "；".join(f"{item['alias']} = {item['expression']}" for item in missing)
        raise ValueError(
            f"{where} 含未诊断的派生因子：{items}。先用 research factor-ic / research buckets "
            "对它做诊断（--strategy <提供别名的策略> --factor expr --params "
            "'{\"expr\": \"...\"}' [--aliases '<新增别名 JSON>']），诊断记录写入台账后再检验"
        )

    # ------------------------------------------------------------------
    # 试验次数 N 与台账总览
    # ------------------------------------------------------------------

    def lineage_scope(self, strategy_id: str) -> dict[str, Any]:
        """返回策略所在研究线及其成员（N、预算与验证期留痕的统计范围）。

        Args:
            strategy_id: 策略 ID。

        Returns:
            {"lineage_id": str, "members": list[str]}。
        """
        return {
            "lineage_id": self._config_svc.lineage_of(strategy_id),
            "members": self._config_svc.lineage_members(strategy_id),
        }

    def trial_breakdown(self, strategy_id: str, *, scope: str = "lineage") -> dict[str, int]:
        """按来源拆分试验次数（Deflated Sharpe 的 N）。

        缺省按研究线汇总：同一研究过程的优化候选、稳健性变体与手工归入的策略
        共享一个 N，避免在候选上跑统计时把基线名下的探索漏掉。

        Args:
            strategy_id: 策略 ID。
            scope: ``lineage``=按研究线汇总（缺省）；``strategy``=只统计该策略自身。

        Returns:
            {"robustness", "optimization", "research_batch", "manual", "estimated"}。
            查询失败时异常直接上抛：N 偏小会让 Deflated Sharpe 与试验预算静默放行，
            统计门槛必须"失败即不通过"，不能退回"至少一次试验"。
        """
        members = (
            self._config_svc.lineage_members(strategy_id) if scope == "lineage" else [strategy_id]
        )
        return rules.summarize_trial_breakdown(
            robustness=self._repo.sum_robustness_trials(members),
            optimization=self._repo.count_evaluated_optimizations(members),
            ledger=self._repo.sum_ledger_trials(members),
        )

    # ------------------------------------------------------------------
    # 试验预算
    # ------------------------------------------------------------------

    def budget_status(self, strategy_id: str) -> dict[str, Any]:
        """研究线试验预算的使用情况。

        Args:
            strategy_id: 研究线中任一策略 ID。

        Returns:
            {"lineage_id", "budget", "used", "remaining"}；used 为研究线 N 的原始和
            （不做"至少为 1"的下限处理）。
        """
        used = rules.raw_trial_sum(self.trial_breakdown(strategy_id))
        budget = int(get_settings().research_trial_budget)
        return {
            "lineage_id": self._config_svc.lineage_of(strategy_id),
            "budget": budget,
            "used": used,
            "remaining": budget - used,
        }

    def ensure_budget(
        self, strategy_id: str, planned: int, over_budget_reason: str | None = None
    ) -> dict[str, Any]:
        """在产生新试验前检查研究线预算；超预算时需要显式理由并在台账留痕。

        Args:
            strategy_id: 本次检验针对的策略 ID。
            planned: 本次预计新增的试验次数（未知时按 1 计）。
            over_budget_reason: 超预算放行理由（CLI ``--over-budget``）。

        Returns:
            预算状态字典，超预算放行时附 ``override_trial_id``。

        Raises:
            ValueError: 超预算且未给出理由。
        """
        status = self.budget_status(strategy_id)
        status["planned"] = max(1, int(planned))
        if not rules.exceeds_budget(status["used"], status["planned"], status["budget"]):
            return status
        reason = (over_budget_reason or "").strip()
        if not reason:
            raise ValueError(
                f"研究线 {status['lineage_id']} 已用 {status['used']}/{status['budget']} 次试验，"
                f"本次再加 {status['planned']} 次将超出预算。继续请加 --over-budget "
                '"<理由>"（理由写入研究台账；超出的试验照常计入 N）'
            )
        trial = self.record_trial(
            kind="manual",
            strategy_id=strategy_id,
            trial_count=0,
            note=(
                f"超预算放行（已用 {status['used']}/{status['budget']}，"
                f"本次 +{status['planned']}）：{reason}"
            ),
        )
        status["override_trial_id"] = trial["trial_id"]
        logger.warning("研究线 %s 超预算放行：%s", status["lineage_id"], reason)
        return status

    def ledger(
        self,
        *,
        strategy_id: str | None = None,
        index_code: str | None = None,
        tag: str | None = None,
        recent_closed: int = 20,
    ) -> dict[str, Any]:
        """台账总览：开工前先读，看哪些方向在做、哪些已有结论。

        Args:
            strategy_id: 策略 ID 过滤（同时给出该策略的 N 拆分）。
            index_code: 指数代码过滤。
            tag: 标签过滤。
            recent_closed: 返回最近关闭的假设条数。

        Returns:
            {filters, n_trials, n_trials_breakdown, status_counts, active, recent_closed,
            unlinked_trials}。
        """
        hypotheses = self.list_hypotheses(
            strategy_id=strategy_id, index_code=index_code, tag=tag, limit=1000
        )
        status_counts: dict[str, int] = {status: 0 for status in rules.ALL_STATUSES}
        for item in hypotheses:
            status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1
        active = [item for item in hypotheses if item["status"] in rules.ACTIVE_STATUSES]
        closed = sorted(
            (item for item in hypotheses if item["status"] in rules.CLOSED_STATUSES),
            key=lambda item: item.get("closed_at") or "",
            reverse=True,
        )
        result: dict[str, Any] = {
            "filters": {"strategy_id": strategy_id, "index_code": index_code, "tag": tag},
            "status_counts": status_counts,
            "active": active,
            "recent_closed": closed[:recent_closed],
        }
        if strategy_id:
            breakdown = self.trial_breakdown(strategy_id)
            result["n_trials"] = rules.total_trials(breakdown)
            result["n_trials_breakdown"] = breakdown
            result["n_trials_scope"] = self.lineage_scope(strategy_id)
            result["strategy_n_trials_breakdown"] = self.trial_breakdown(
                strategy_id, scope="strategy"
            )
            result["budget"] = self.budget_status(strategy_id)
            trials = self._repo.list_trials(strategy_id=strategy_id, limit=1000)
            result["unlinked_trials"] = sum(1 for item in trials if item.hypothesis_id is None)
        return result


def _clean_list(values: Sequence[str] | None) -> list[str] | None:
    """去空白、去重并保持顺序；空列表返回 None。"""
    if not values:
        return None
    seen: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.append(text)
    return seen or None


def _parse_date(value: Any) -> date | None:
    """把 ISO 日期字符串解析为 date，非法值返回 None。"""
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _iso(value: Any) -> str | None:
    """日期 / 时间转 ISO 字符串。"""
    return value.isoformat() if value is not None else None


def _hypothesis_dict(model: ResearchHypothesisModel) -> dict[str, Any]:
    """假设 ORM 行转字典。"""
    return {
        "hypothesis_id": model.hypothesis_id,
        "strategy_id": model.strategy_id,
        "index_codes": model.index_codes or [],
        "tags": model.tags or [],
        "category": model.category,
        "statement": model.statement,
        "rationale": model.rationale,
        "pre_registration": model.pre_registration,
        "status": model.status,
        "conclusion": model.conclusion,
        "parent_id": model.parent_id,
        "campaign_id": getattr(model, "campaign_id", None),
        "created_by": model.created_by,
        "created_at": _iso(model.created_at),
        "updated_at": _iso(model.updated_at),
        "closed_at": _iso(model.closed_at),
    }


def _trial_dict(model: ResearchTrialModel) -> dict[str, Any]:
    """试验 ORM 行转字典。"""
    return {
        "trial_id": model.trial_id,
        "hypothesis_id": model.hypothesis_id,
        "strategy_id": model.strategy_id,
        "kind": model.kind,
        "ref_id": model.ref_id,
        "execution_model": model.execution_model,
        "start_date": _iso(model.start_date),
        "end_date": _iso(model.end_date),
        "trial_count": model.trial_count,
        "counted_in_n": model.kind in rules.COUNTED_TRIAL_KINDS,
        "is_estimated": bool(model.is_estimated),
        "config_hashes": model.config_hashes or [],
        "summary": model.summary,
        "note": model.note,
        "created_at": _iso(model.created_at),
    }
