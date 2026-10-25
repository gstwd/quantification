"""策略配置服务：管理策略配置的 CRUD 操作。"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.engine.config import (
    SUPPORTED_SCHEMA_VERSIONS,
    CompositeStrategyConfig,
    FilterConfig,
    PortfolioConfig,
    RankConfig,
    StrategyConfig,
    TimingConfig,
    is_composite_config,
    iter_factor_refs,
)
from quant_etf_api.engine.portfolio import _DEFAULT_TIMING_EXPOSURE
from quant_etf_api.engine.transforms import is_valid_transform, transform_hints
from quant_etf_api.factors.catalog import (
    STRATEGY_SCOPED_VALUATION_TEMPLATE_IDS,
    FactorResolutionError,
    FactorTemplateRegistry,
    get_factor_template_registry,
)
from quant_etf_api.factors.templates import FactorParameterError
from quant_etf_api.infra.db.models.core import StrategyConfigModel, StrategyLifecycleModel
from quant_etf_api.infra.db.repositories.factor_definition import FactorDefinitionRepository
from quant_etf_api.infra.db.repositories.research_ledger import ResearchLedgerRepository
from quant_etf_api.infra.db.repositories.strategy_config import StrategyConfigRepository
from quant_etf_api.infra.db.repositories.strategy_research import StrategyResearchRepository
from quant_etf_api.infra.time import utcnow_aware
from quant_etf_api.schemas.strategy import (
    StrategyConfigCreate,
    StrategyConfigUpdate,
    StrategyDetail,
    StrategySummary,
    StrategyValidationResult,
)

logger = logging.getLogger(__name__)


def _backtest_preload_days(registry: FactorTemplateRegistry) -> int:
    """回测行情预加载的自然日数（与 ``BacktestService._get_lookback_days`` 同一口径）。

    Args:
        registry: 因子模板注册表。

    Returns:
        全部模板（不含按策略单独扩展估值预加载的模板）默认参数回望的最大值。
    """
    return max(
        (
            template.lookback_days()
            for template in registry.all()
            if template.template_id not in STRATEGY_SCOPED_VALUATION_TEMPLATE_IDS
        ),
        default=90,
    )


def compute_config_hash(config_json: dict[str, Any]) -> str:
    """计算策略配置的规范化 sha256 哈希。

    使用 sort_keys 与固定分隔符保证同一配置在不同环境下哈希一致，
    用于回测快照与优化会话的前后版本比对。

    Args:
        config_json: 策略配置 JSON（仅引擎配置）。

    Returns:
        64 位十六进制 sha256 哈希。
    """
    canonical = json.dumps(
        config_json,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class StrategyConfigService:
    """策略配置服务，提供配置的增删改查和校验。"""

    def __init__(self, db: Session) -> None:
        """初始化策略配置服务。

        Args:
            db: SQLAlchemy Session。
        """
        self._db = db
        self._repo = StrategyConfigRepository(db)
        self._research_repo = StrategyResearchRepository(db)
        self._ledger_repo = ResearchLedgerRepository(db)

    def list_configs(self) -> list[StrategySummary]:
        """返回所有启用的策略配置摘要。"""
        return [self._to_summary(r) for r in self._repo.find_all_active()]

    @staticmethod
    def _to_summary(row: Any) -> StrategySummary:
        """把策略 ORM 行转换为列表摘要（含变体标记与验证期留痕元数据）。

        Args:
            row: ``strategy_config`` ORM 行。

        Returns:
            StrategySummary。
        """
        return StrategySummary(
            strategy_id=row.strategy_id,
            display_name=row.display_name,
            version=row.version,
            frequency=row.frequency,
            description=row.description or "",
            status=row.status,
            is_starred=row.is_starred,
            index_codes=(row.config_json or {}).get("index_codes", []),
            is_variant=getattr(row, "is_variant", False),
            source_batch_id=getattr(row, "source_batch_id", None),
            lineage_id=getattr(row, "lineage_id", None) or row.strategy_id,
            validation_consumed_at=getattr(row, "validation_consumed_at", None),
            validation_consumed_note=getattr(row, "validation_consumed_note", None),
        )

    def get_config(self, strategy_id: str) -> StrategyDetail | None:
        """获取策略配置详情。"""
        row = self._repo.find_by_id(strategy_id)
        if row is None:
            return None
        return StrategyDetail(
            strategy_id=row.strategy_id,
            display_name=row.display_name,
            version=row.version,
            frequency=row.frequency,
            description=row.description or "",
            status=row.status,
            is_starred=row.is_starred,
            config_json=row.config_json,
            # 详情顶层 index_codes 必须与 config_json 一致（F-11）：
            # 之前详情不填这个字段，调用方读到的永远是 []，
            # 而列表接口返回的是真实资产池，两边口径不一致
            index_codes=(row.config_json or {}).get("index_codes", []),
            created_at=row.created_at,
            updated_at=row.updated_at,
            is_variant=getattr(row, "is_variant", False),
            source_batch_id=getattr(row, "source_batch_id", None),
            lineage_id=getattr(row, "lineage_id", None) or row.strategy_id,
            validation_consumed_at=getattr(row, "validation_consumed_at", None),
            validation_consumed_note=getattr(row, "validation_consumed_note", None),
        )

    def lineage_of(self, strategy_id: str) -> str:
        """返回策略所在研究线 ID（策略不存在或未设研究线时取自身 ID）。

        Args:
            strategy_id: 策略标识。

        Returns:
            研究线 ID。
        """
        row = self._repo.find_by_id(strategy_id)
        if row is None:
            return strategy_id
        return getattr(row, "lineage_id", None) or row.strategy_id

    def lineage_members(self, strategy_id: str) -> list[str]:
        """返回策略所在研究线的全部成员策略 ID（至少含自身）。

        Args:
            strategy_id: 策略标识。

        Returns:
            成员策略 ID 列表。
        """
        lineage_id = self.lineage_of(strategy_id)
        members = [row.strategy_id for row in self._repo.find_lineage_members(lineage_id)]
        if strategy_id not in members:
            members.append(strategy_id)
        return members

    def _resolve_lineage(self, target: str) -> str:
        """把"归入某策略的研究线"解析为研究线根 ID。

        Args:
            target: 已有策略 ID（可以是研究线中任一成员）。

        Returns:
            研究线 ID。

        Raises:
            ValueError: 目标策略不存在。
        """
        row = self._repo.find_by_id(target)
        if row is None:
            raise ValueError(f"研究线目标策略 {target} 不存在")
        return getattr(row, "lineage_id", None) or row.strategy_id

    def _inherit_validation_consumed(self, model: StrategyConfigModel, lineage_id: str) -> None:
        """新成员加入研究线时继承该线的验证期消费留痕。

        同一研究线的成员共享研究过程：只要线上任一策略看过验证期，
        新加入的策略也不能再把验证期当作样本外。

        Args:
            model: 新加入的策略行。
            lineage_id: 研究线 ID。
        """
        if model.validation_consumed_at is not None:
            return
        for member in self._repo.find_lineage_members(lineage_id):
            if member.strategy_id != model.strategy_id and member.validation_consumed_at:
                model.validation_consumed_at = member.validation_consumed_at
                model.validation_consumed_note = (
                    f"继承研究线 {lineage_id} 的验证期消费留痕（{member.strategy_id}）"
                )
                return

    def create_config(self, req: StrategyConfigCreate) -> StrategyDetail:
        """创建策略配置。

        Args:
            req: 创建请求。

        Returns:
            创建后的策略详情。

        Raises:
            ValueError: 策略 ID 已存在。
        """
        existing = self._repo.find_by_id(req.strategy_id)
        if existing is not None:
            raise ValueError(f"策略 {req.strategy_id} 已存在")

        # 校验配置（组合策略按成员与权重规则校验）
        validation = (
            self.validate_composite(req.config_json, req.strategy_id)
            if is_composite_config(req.config_json)
            else self.validate_config(req.config_json)
        )
        if not validation.valid:
            raise ValueError(f"配置校验失败: {'; '.join(validation.errors)}")

        # 归一化：config_json 未显式声明 schema_version 时写入当前版本
        config_json = dict(req.config_json)
        config_json.setdefault("schema_version", "2")

        lineage_id = (
            self._resolve_lineage(req.lineage_id) if req.lineage_id else req.strategy_id
        )
        model = StrategyConfigModel(
            strategy_id=req.strategy_id,
            display_name=req.display_name,
            version=req.version,
            description=req.description,
            frequency=req.frequency,
            config_json=config_json,
            status=req.status,
            is_variant=req.is_variant,
            source_batch_id=req.source_batch_id,
            lineage_id=lineage_id,
        )
        if lineage_id != req.strategy_id:
            self._inherit_validation_consumed(model, lineage_id)
        self._repo.upsert(model)
        self._db.commit()

        return self.get_config(req.strategy_id)

    def update_config(self, strategy_id: str, req: StrategyConfigUpdate) -> StrategyDetail | None:
        """更新策略配置。

        Args:
            strategy_id: 策略标识。
            req: 更新请求。

        Returns:
            更新后的策略详情，不存在返回 None。
        """
        existing = self._repo.find_by_id(strategy_id)
        if existing is None:
            return None

        if req.config_json is not None:
            # LIVE 状态冻结：已上线的策略不允许直接改配置，否则生命周期监控的
            # 对象会被静默替换，历史快照与诊断结论失去意义。需要变更时先下线，
            # 或走候选策略 promote 新版本。
            live = (
                self._db.query(StrategyLifecycleModel)
                .filter(
                    StrategyLifecycleModel.strategy_id == strategy_id,
                    StrategyLifecycleModel.lifecycle_status == "LIVE",
                )
                .one_or_none()
            )
            if live is not None:
                raise ValueError(
                    f"策略 {strategy_id} 处于上线（LIVE）状态，配置已冻结，"
                    "不允许直接修改 config_json；"
                    "如需变更请先将生命周期状态改为 SUSPENDED，"
                    "或走优化会话生成候选策略后 promote 新版本。"
                )
            validation = (
                self.validate_composite(req.config_json, strategy_id)
                if is_composite_config(req.config_json)
                else self.validate_config(req.config_json)
            )
            if not validation.valid:
                raise ValueError(f"配置校验失败: {'; '.join(validation.errors)}")
            # 归一化：保留已显式声明的 schema_version，缺失时写入当前版本
            req.config_json = dict(req.config_json)
            req.config_json.setdefault("schema_version", "2")

        if req.display_name is not None:
            existing.display_name = req.display_name
        if req.version is not None:
            existing.version = req.version
        if req.description is not None:
            existing.description = req.description
        if req.frequency is not None:
            existing.frequency = req.frequency
        if req.config_json is not None:
            existing.config_json = req.config_json
        if req.status is not None:
            existing.status = req.status
        if req.lineage_id is not None:
            self._move_lineage(existing, req.lineage_id)

        self._db.commit()
        return self.get_config(strategy_id)

    def _move_lineage(self, existing: StrategyConfigModel, target: str) -> None:
        """把策略改归到目标策略所在的研究线。

        研究线根若已有其他成员则拒绝改归：成员仍指向旧根 ID，静默拆线会让
        N 与验证期留痕按错误的分组统计。普通成员自身已有试验时同样拒绝移出：
        试验按策略归属，移出会把这些试验从原研究线的 N 中拿走（等于重置 N）。
        独立策略归入其他研究线、无试验的成员改归不受限制。

        Args:
            existing: 待改归的策略行。
            target: 目标研究线中的任一策略 ID。

        Raises:
            ValueError: 目标不存在、待改归策略是仍有成员的研究线根，或成员自身已有试验。
        """
        new_line = self._resolve_lineage(target)
        old_line = getattr(existing, "lineage_id", None) or existing.strategy_id
        if new_line == old_line:
            return
        others = [
            row.strategy_id
            for row in self._repo.find_lineage_members(old_line)
            if row.strategy_id != existing.strategy_id
        ]
        if others and old_line == existing.strategy_id:
            raise ValueError(
                f"策略 {existing.strategy_id} 是研究线根，仍有成员 {', '.join(others[:5])}，"
                "不能改归其他研究线"
            )
        if others:
            own_trials = self._own_trial_count(existing.strategy_id)
            if own_trials > 0:
                raise ValueError(
                    f"策略 {existing.strategy_id} 已在研究线 {old_line} 上产生 {own_trials} 次试验，"
                    "移出会把这些试验从原研究线的 N 中拿走，不能改归"
                )
        existing.lineage_id = new_line
        self._inherit_validation_consumed(existing, new_line)

    def _own_trial_count(self, strategy_id: str) -> int:
        """统计归属于该策略自身的试验次数（稳健性变体 + 已评估会话 + 台账计入项）。

        Args:
            strategy_id: 策略 ID。

        Returns:
            试验次数（不做"至少 1"的下限处理）。
        """
        members = [strategy_id]
        return (
            self._ledger_repo.sum_robustness_trials(members)
            + self._ledger_repo.count_evaluated_optimizations(members)
            + sum(self._ledger_repo.sum_ledger_trials(members).values())
        )

    def delete_config(self, strategy_id: str) -> bool:
        """删除策略配置。

        Args:
            strategy_id: 策略标识。

        Returns:
            是否成功删除。
        """
        result = self._repo.delete_by_id(strategy_id)
        if result:
            self._db.commit()
        return result

    def clear_research_data(self, strategy_id: str) -> dict[str, int] | None:
        """原子地清理策略及关联 draft 策略的研究数据，保留基线策略配置。"""
        if self._repo.find_by_id(strategy_id) is None:
            return None
        try:
            strategy_ids = {strategy_id}
            draft_strategy_ids: set[str] = set()
            while True:
                discovered = self._research_repo.find_related_draft_strategy_ids(strategy_ids)
                new_drafts = discovered - strategy_ids
                if not new_drafts:
                    break
                strategy_ids.update(new_drafts)
                draft_strategy_ids.update(new_drafts)

            deleted = self._research_repo.delete_by_strategies(strategy_ids)
            deleted["draft_strategy_config"] = self._research_repo.delete_draft_strategy_configs(
                draft_strategy_ids
            )
            self._db.commit()
            return deleted
        except Exception:
            self._db.rollback()
            raise

    # ── 变体策略清理（D-4） ────────────────────────────────────────────────

    def list_variants(self, batch_id: str) -> list[StrategySummary]:
        """列出某个稳健性验证批次派生的变体草稿策略。

        Args:
            batch_id: 稳健性验证批次 ID。

        Returns:
            变体策略摘要列表（按策略 ID 升序）。
        """
        return [self._to_summary(row) for row in self._repo.find_variants(batch_id)]

    def prune_variants(
        self, batch_id: str, *, dry_run: bool = True, force: bool = False
    ) -> dict[str, Any]:
        """清理某个稳健性验证批次派生的变体草稿策略。

        一次 ``robustness scan`` 会批量创建 ``<基线>__rbXXXX_*`` 形式的草稿策略，
        历史上只能按 ID 前缀人工猜测（D-4）。现在变体带 ``source_batch_id``
        元数据，可以整批精确清理：

        - 默认预演（``dry_run=True``），只返回将被删除的策略；
        - 变体仍被回测引用时不删（避免留下指向不存在策略的回测记录），
          需显式 ``force=True`` 才连带删除该变体的回测；
        - **不删除稳健性批次本身**：批次是"试验次数台账"的证据，
          Deflated Sharpe 的 N 依赖它，删掉变体策略不等于抹掉历史试验。

        Args:
            batch_id: 稳健性验证批次 ID。
            dry_run: True 时只统计不写库（默认）。
            force: True 时连带删除变体名下的回测记录。

        Returns:
            清理结果字典：``batch_id`` / ``dry_run`` / ``force`` / ``matched`` /
            ``deleted`` / ``skipped``。
        """
        rows = self._repo.find_variants(batch_id)
        result: dict[str, Any] = {
            "batch_id": batch_id,
            "dry_run": dry_run,
            "force": force,
            "matched": len(rows),
            "deleted": [],
            "skipped": [],
        }
        if not rows:
            result["message"] = (
                f"批次 {batch_id} 没有带 source_batch_id 标记的变体策略"
                "（历史批次可用 migration 0049 的 description 回填后再试）"
            )
            return result

        backtest_ids_by_strategy = self._backtest_ids_by_strategy([row.strategy_id for row in rows])
        for row in rows:
            backtest_ids = backtest_ids_by_strategy.get(row.strategy_id, [])
            if backtest_ids and not force:
                result["skipped"].append(
                    {
                        "strategy_id": row.strategy_id,
                        "reason": (
                            f"仍有 {len(backtest_ids)} 条回测引用该变体；"
                            "确认后可用 --force 连带删除，或先执行 backtest delete"
                        ),
                        "backtest_ids": backtest_ids[:5],
                    }
                )
                continue
            if dry_run:
                result["deleted"].append(
                    {
                        "strategy_id": row.strategy_id,
                        "display_name": row.display_name,
                        "backtests": len(backtest_ids),
                    }
                )
                continue
            failed = self._delete_variant(row.strategy_id, backtest_ids, force=force)
            if failed is not None:
                result["skipped"].append({"strategy_id": row.strategy_id, "reason": failed})
                continue
            result["deleted"].append(
                {
                    "strategy_id": row.strategy_id,
                    "display_name": row.display_name,
                    "backtests": len(backtest_ids),
                }
            )
        return result

    def _backtest_ids_by_strategy(self, strategy_ids: list[str]) -> dict[str, list[str]]:
        """按策略统计其名下回测 ID（局部导入避免服务层循环依赖）。

        Args:
            strategy_ids: 策略 ID 列表。

        Returns:
            策略 ID → 回测 ID 列表。
        """
        from quant_etf_api.infra.db.models.core import BacktestRunModel

        grouped: dict[str, list[str]] = {}
        if not strategy_ids:
            return grouped
        try:
            rows = (
                self._db.query(BacktestRunModel.backtest_id, BacktestRunModel.strategy_id)
                .filter(BacktestRunModel.strategy_id.in_(strategy_ids))
                .all()
            )
        except Exception:
            logger.warning("统计变体策略的回测引用失败", exc_info=True)
            return grouped
        for backtest_id, strategy_id in rows:
            grouped.setdefault(strategy_id, []).append(backtest_id)
        return grouped

    def _delete_variant(
        self, strategy_id: str, backtest_ids: list[str], *, force: bool
    ) -> str | None:
        """删除单个变体策略（可选连带删除其回测）。

        Args:
            strategy_id: 变体策略 ID。
            backtest_ids: 该变体名下的回测 ID。
            force: 是否连带删除回测。

        Returns:
            失败原因；成功返回 None。
        """
        for backtest_id in backtest_ids:
            if not force:
                return f"回测 {backtest_id} 仍引用该变体"
            try:
                # 局部导入：backtest 服务反向依赖 strategy 配置服务
                from quant_etf_api.services.backtest_service import BacktestService

                BacktestService(self._db).delete_backtest(backtest_id, force=True)
            except Exception as exc:
                self._db.rollback()
                return f"删除回测 {backtest_id} 失败：{exc}"
        try:
            deleted = self._repo.delete_by_id(strategy_id)
            self._db.commit()
        except Exception as exc:
            self._db.rollback()
            return f"删除策略失败：{exc}"
        return None if deleted else f"策略 {strategy_id} 不存在"

    # ── 验证期消费留痕（D-5） ──────────────────────────────────────────────

    def mark_validation_consumed(self, strategy_id: str, note: str | None = None) -> bool:
        """记录"该策略的验证期数据已被消费"（D-5）。

        验证期数据只能用于否决、不能用于确认。回测级留痕（``backtests`` 接口）
        只能证明"跑过验证期回测"，无法说明"验证期是否已被研究者消费"；
        本方法把该状态记到策略元数据上，供研究流程判断后续验证期证据的效力。

        Args:
            strategy_id: 策略标识。
            note: 消费说明（首次消费来源或人工备注）。

        Returns:
            是否写入成功（策略不存在返回 False）。
        """
        consumed_at = utcnow_aware()
        consumed = self._repo.mark_validation_consumed(strategy_id, note, consumed_at=consumed_at)
        if not consumed:
            return False
        # 验证期消费按研究线生效：同线其他成员一并留痕（只写首次时间，不覆盖已有记录）
        lineage_id = self.lineage_of(strategy_id)
        for member in self._repo.find_lineage_members(lineage_id):
            if member.strategy_id != strategy_id and member.validation_consumed_at is None:
                member.validation_consumed_at = consumed_at
                member.validation_consumed_note = (
                    f"同研究线 {lineage_id} 的 {strategy_id} 已消费验证期：{note or '未填写说明'}"
                )
        self._db.commit()
        logger.info("验证期消费留痕: strategy_id=%s note=%s", strategy_id, note)
        return True

    def validate_config(self, config_json: dict[str, Any]) -> StrategyValidationResult:
        """校验策略配置 JSON 是否合法（含因子 ID 与变换函数校验）。

        Args:
            config_json: 策略配置 JSON（仅含引擎配置，不含 strategy_id/display_name 等元数据字段）。

        Returns:
            校验结果。
        """
        if is_composite_config(config_json):
            return self.validate_composite(config_json)
        try:
            # strategy_id 和 display_name 存储在顶层列中，校验时补入占位值
            validation_input = {
                "strategy_id": "_validate_",
                "display_name": "_validate_",
                # 配置未声明 schema_version 时按当前版本校验
                "schema_version": config_json.get("schema_version", "2"),
                **config_json,
            }
            config = StrategyConfig(**validation_input)
        except Exception as e:
            return StrategyValidationResult(valid=False, errors=[f"配置解析失败: {e}"])

        return self.validate_parsed(config)

    def validate_composite(
        self, config_json: dict[str, Any], strategy_id: str = "_validate_"
    ) -> StrategyValidationResult:
        """校验组合策略配置：结构与权重、成员存在且为有效的普通策略。

        Args:
            config_json: 组合策略 config_json（``kind=composite``）。
            strategy_id: 组合策略自身 ID（用于拒绝"包含自身"）。

        Returns:
            校验结果。
        """
        try:
            composite = CompositeStrategyConfig(
                **{"strategy_id": strategy_id, "display_name": "_validate_", **config_json}
            )
        except Exception as e:
            return StrategyValidationResult(valid=False, errors=[f"组合策略配置解析失败: {e}"])
        errors: list[str] = []
        warnings: list[str] = []
        for member in composite.members:
            row = self._repo.find_by_id(member.strategy_id)
            if row is None:
                errors.append(f"成员策略 {member.strategy_id} 不存在")
                continue
            if is_composite_config(row.config_json):
                errors.append(f"成员策略 {member.strategy_id} 是组合策略：不支持嵌套组合")
                continue
            parsed = self.get_parsed_config(member.strategy_id)
            if parsed is None:
                errors.append(f"成员策略 {member.strategy_id} 配置解析失败")
                continue
            result = self.validate_parsed(parsed)
            errors.extend(f"成员 {member.strategy_id}：{err}" for err in result.errors)
        if composite.cash_weight > 0:
            warnings.append(
                f"组合保留 {composite.cash_weight:.0%} 现金（收益按 0 计），成员各自的择时仓位之外再打折"
            )
        return StrategyValidationResult(valid=not errors, errors=errors, warnings=warnings)

    def get_composite_config(self, strategy_id: str) -> CompositeStrategyConfig | None:
        """获取组合策略配置；策略不存在或不是组合策略时返回 None。

        Args:
            strategy_id: 策略标识。

        Returns:
            组合策略配置。
        """
        row = self._repo.find_by_id(strategy_id)
        if row is None or not is_composite_config(row.config_json):
            return None
        return CompositeStrategyConfig(
            strategy_id=row.strategy_id,
            display_name=row.display_name,
            version=row.version,
            description=row.description or "",
            frequency=row.frequency,
            **row.config_json,
        )

    def is_composite(self, strategy_id: str) -> bool:
        """策略是否为组合策略。

        Args:
            strategy_id: 策略标识。

        Returns:
            组合策略时为 True；策略不存在时为 False。
        """
        row = self._repo.find_by_id(strategy_id)
        return row is not None and is_composite_config(getattr(row, "config_json", None))

    def require_single(self, strategy_id: str, action: str) -> None:
        """拒绝对组合策略执行只支持普通策略的操作（给出可操作的提示）。

        Args:
            strategy_id: 策略标识。
            action: 操作名称，如"稳健性扫描"。

        Raises:
            ValueError: 策略为组合策略时抛出。
        """
        if self.is_composite(strategy_id):
            raise ValueError(
                f"{strategy_id} 是组合策略，{action}只支持普通策略：请对其成员策略分别执行"
            )

    def build_composite_snapshot(
        self, composite: CompositeStrategyConfig
    ) -> tuple[dict[str, Any], str]:
        """构造组合策略回测快照：组合配置 + 各成员当时的配置快照与哈希。

        成员快照随组合回测固化，成员之后被修改不会改变这次回测的结果；组合哈希
        覆盖组合配置与全部成员哈希。

        Args:
            composite: 组合策略配置。

        Returns:
            (快照, 组合配置哈希)。

        Raises:
            ValueError: 成员不存在时抛出。
        """
        members: dict[str, Any] = {}
        for member in composite.members:
            detail = self.get_config(member.strategy_id)
            if detail is None:
                raise ValueError(f"成员策略 {member.strategy_id} 不存在")
            members[member.strategy_id] = {
                "strategy_id": detail.strategy_id,
                "display_name": detail.display_name,
                "version": detail.version,
                "frequency": detail.frequency,
                "config_json": detail.config_json,
                "config_hash": compute_config_hash(detail.config_json),
            }
        config_json = composite.model_dump(
            exclude={"strategy_id", "display_name", "version", "description", "frequency"}
        )
        snapshot = {
            "strategy_id": composite.strategy_id,
            "display_name": composite.display_name,
            "version": composite.version,
            "frequency": composite.frequency,
            "config_json": config_json,
            "members": members,
        }
        digest = compute_config_hash(
            {
                "composite": config_json,
                "members": {sid: item["config_hash"] for sid, item in members.items()},
            }
        )
        return snapshot, digest

    @staticmethod
    def parse_composite_snapshot(
        snapshot: dict[str, Any],
    ) -> tuple[CompositeStrategyConfig, dict[str, StrategyConfig]] | None:
        """从组合回测快照重建组合配置与成员配置。

        Args:
            snapshot: :meth:`build_composite_snapshot` 写入的快照。

        Returns:
            (组合配置, 成员 ID → 成员配置)；不是组合快照或解析失败时为 None。
        """
        config_json = snapshot.get("config_json") or {}
        if not is_composite_config(config_json):
            return None
        try:
            composite = CompositeStrategyConfig(
                strategy_id=snapshot.get("strategy_id", "_snapshot_"),
                display_name=snapshot.get("display_name", ""),
                **{k: v for k, v in config_json.items() if k not in ("strategy_id", "display_name")},
            )
        except Exception:
            logger.exception("解析组合回测快照失败")
            return None
        members: dict[str, StrategyConfig] = {}
        for member in composite.members:
            member_snapshot = (snapshot.get("members") or {}).get(member.strategy_id)
            parsed = (
                StrategyConfigService.parse_snapshot(member_snapshot) if member_snapshot else None
            )
            if parsed is None:
                return None
            members[member.strategy_id] = parsed
        return composite, members

    def validate_parsed(self, config: StrategyConfig) -> StrategyValidationResult:
        """校验已解析的 StrategyConfig（供运行时路径复用，避免二次解析）。

        在结构校验基础上，额外校验：
        - factor_aliases 声明的模板存在、启用、参数合法
        - 各模块引用的因子引用可解析（别名或模板 ID），且模板 usage 覆盖消费模块
        - transforms 引用的变换函数均存在于引擎变换注册表
        - 择时因子原值不在 0-100 评分空间时必须配置变换函数

        Args:
            config: 已解析的策略配置。

        Returns:
            校验结果。
        """
        errors, warnings = self._structural_validation(config)
        registry = get_factor_template_registry()
        active_by_id = {
            row.factor_id: row
            for row in FactorDefinitionRepository(self._db).find_active()
        }
        errors.extend(self._factor_alias_errors(config, registry, active_by_id, warnings))
        errors.extend(self._factor_reference_errors(config, registry, active_by_id))
        errors.extend(self._transform_validation_errors(config))
        errors.extend(self._timing_scale_errors(config, registry))
        return StrategyValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    @staticmethod
    def _structural_validation(config: StrategyConfig) -> tuple[list[str], list[str]]:
        """结构字段校验（不依赖数据库，纯静态）。

        Args:
            config: 已解析的策略配置。

        Returns:
            (errors, warnings) 二元组。
        """
        errors: list[str] = []
        warnings: list[str] = []

        # 必填字段校验
        if not config.strategy_id:
            errors.append("strategy_id 不能为空")
        if not config.display_name:
            errors.append("display_name 不能为空")
        if not config.score.factors:
            errors.append("score.factors 不能为空")
        if config.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            errors.append(
                f"不支持的配置 schema_version '{config.schema_version}'，"
                f"可用: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
            )

        # 择时代理指数校验
        if config.timing and not config.timing.proxy_index_codes:
            errors.append("timing.proxy_index_codes 不能为空")
        if (
            config.timing
            and config.timing.thresholds.defensive > config.timing.thresholds.offensive
        ):
            errors.append("timing.thresholds.defensive 不能大于 offensive")
        if config.timing:
            timing_errors, timing_warnings = StrategyConfigService._timing_mode_issues(
                config.timing
            )
            errors.extend(timing_errors)
            warnings.extend(timing_warnings)

        # 顶层与各 regime 覆盖块共用同一套模块校验（枚举取值已由 Pydantic Literal 约束，
        # 此处只做跨字段规则），避免"regime 覆盖里写错只在运行时才暴露"
        blocks: list[tuple[str, FilterConfig | None, RankConfig | None, PortfolioConfig | None]] = [
            ("", config.filters, config.rank, config.portfolio)
        ]
        blocks.extend(
            (f"regime_rules.{regime}.", rule.filters, rule.rank, rule.portfolio)
            for regime, rule in config.regime_rules.items()
        )
        for prefix, filters, rank, portfolio in blocks:
            errors.extend(StrategyConfigService._filter_block_errors(prefix, filters))
            errors.extend(StrategyConfigService._rank_block_errors(prefix, rank))
            errors.extend(StrategyConfigService._portfolio_block_errors(prefix, portfolio))
        if config.timing:
            # 顶层组合会被三种 regime 读取；regime 覆盖块只在本 regime 下生效
            warnings.extend(
                StrategyConfigService._partial_timing_exposure_warnings(
                    "", config.portfolio, tuple(_DEFAULT_TIMING_EXPOSURE)
                )
            )
            for regime, rule in config.regime_rules.items():
                warnings.extend(
                    StrategyConfigService._partial_timing_exposure_warnings(
                        f"regime_rules.{regime}.", rule.portfolio, (regime,)
                    )
                )
                if config.timing.tiers and rule.portfolio and rule.portfolio.timing_exposure:
                    warnings.append(
                        f"regime_rules.{regime}.portfolio.timing_exposure 只在未命中仓位档位时"
                        "生效：命中 timing.tiers 时目标仓位取档位 exposure"
                    )
        if config.regime_rules and config.timing is None:
            warnings.append(
                "已配置 regime_rules 但未配置择时（timing）：regime 覆盖永远不会生效"
            )

        # 评分权重校验
        for factor_id, weight in config.score.factors.items():
            if weight == 0:
                warnings.append(f"因子 {factor_id} 权重为 0，将不参与评分")

        # 风控配置校验
        if config.risk:
            if config.risk.max_asset_weight <= 0 or config.risk.max_asset_weight > 1:
                errors.append("max_asset_weight 必须在 (0, 1] 范围内")
            if config.risk.min_cash_ratio < 0 or config.risk.min_cash_ratio >= 1:
                errors.append("min_cash_ratio 必须在 [0, 1) 范围内")
            if config.risk.max_portfolio_exposure > 1 - config.risk.min_cash_ratio:
                errors.append("max_portfolio_exposure 不能大于 1 - min_cash_ratio")
            # 赢家通吃把全部仓位给一个资产：单资产上限低于择时仓位时会被静默裁剪
            portfolios = [config.portfolio] + [
                rule.portfolio for rule in config.regime_rules.values() if rule.portfolio
            ]
            for portfolio in portfolios:
                if portfolio.method != "winner_take_all":
                    continue
                exposures = [portfolio.default_exposure]
                if config.timing:
                    # 未配置 timing_exposure 时引擎按默认三档表取仓位（唯一来源）
                    exposures.extend(
                        (portfolio.timing_exposure or {}).values()
                        or _DEFAULT_TIMING_EXPOSURE.values()
                    )
                    # 多档仓位命中时直接取档位仓位；动态档位取其上限
                    exposures.extend(
                        tier.exposure if tier.exposure is not None else tier.max_exposure
                        for tier in config.timing.tiers
                    )
                if config.risk.max_asset_weight < max(exposures):
                    warnings.append(
                        f"winner_take_all 的目标仓位最高 {max(exposures):.0%}，但 "
                        f"risk.max_asset_weight={config.risk.max_asset_weight:.0%}："
                        "单一持仓会被裁剪，超出部分留作现金"
                    )
                    break

        # 调仓配置校验（warning 级）：不阻塞历史配置，但显式提示口径歧义
        if config.rebalance:
            for leg_name, schedule in (
                ("selection", config.rebalance.selection),
                ("risk", config.rebalance.risk),
            ):
                if (
                    schedule is not None
                    and schedule.day_of_month is not None
                    and schedule.day_of_month > 28
                ):
                    warnings.append(
                        f"rebalance.{leg_name}.day_of_month={schedule.day_of_month} 超出 28："
                        "月末日期随月份变化，系统按'当月最后一日'处理，建议改用 ≤28 的日期"
                    )
            if config.rebalance.risk is not None and config.timing is None:
                warnings.append(
                    "已启用风险腿但未配置择时（timing）：风险腿只会把总仓位缩放到 "
                    "portfolio.default_exposure，不会随市场 regime 变化；"
                    "如需择时降仓请在择时模块配置因子与阈值"
                )
            if config.rebalance.selection != config.rebalance.risk:
                warnings.append(
                    "选股腿与风险腿日程不同：选股腿日只替换成分、维持当前总仓位，"
                    "总仓位仅在风险腿日按择时目标调整"
                )
            band = config.rebalance.band
            daily_legs = (
                config.rebalance.selection.frequency == "daily"
                and config.rebalance.risk.frequency == "daily"
            )
            if band is not None and band.max_drift is not None and daily_legs:
                warnings.append(
                    "rebalance.band.max_drift 只在非调仓日检查：两腿都是每日调仓时永远不会触发"
                )

        # 路径依赖风控叠加层：参数组合本身无效时提示
        if config.risk and config.risk.drawdown_stop is not None:
            if config.risk.drawdown_stop.exposure >= config.risk.max_portfolio_exposure:
                warnings.append(
                    "risk.drawdown_stop.exposure 不低于组合总仓位上限：回撤止损触发后不会降仓"
                )
        if config.needs_path_state:
            warnings.append(
                "配置含路径依赖功能（排名缓冲带 / 偏离调仓 / 风控叠加层）：实时决策需回放历史"
                "重建持仓与净值状态，结果取决于回放起点（LIVE 取上线日，其余约一年）"
            )

        return errors, warnings

    @staticmethod
    def _filter_block_errors(
        prefix: str, filters: FilterConfig | None, block: str = "filters"
    ) -> list[str]:
        """校验一个过滤配置块（或同结构的择时规则组）的跨字段规则。

        Args:
            prefix: 配置路径前缀（顶层为空串，regime 覆盖为 ``regime_rules.<regime>.``）。
            filters: 过滤配置，None 表示未配置。
            block: 规则所在块的路径名，过滤为 ``filters``，择时规则组为
                ``timing.rules.<regime>``。

        Returns:
            错误信息列表。
        """
        if filters is None:
            return []
        errors: list[str] = []
        for index, rule in enumerate(filters.rules):
            where = f"{prefix}{block}.rules[{index}]"
            # 跨因子比较校验：compare_to 与 value 二选一
            has_value = rule.value is not None
            has_compare = bool(rule.compare_to)
            if has_value and has_compare:
                errors.append(f"{where} 不能同时设置 value 和 compare_to，只能二选一")
            if not has_value and not has_compare:
                errors.append(f"{where} 必须设置 value 或 compare_to 其中之一")
            if has_compare:
                if rule.op == "between":
                    errors.append(f"{where}：between 操作符不支持跨因子比较（compare_to）")
            elif rule.op == "between":
                if not isinstance(rule.value, list) or len(rule.value) != 2:
                    errors.append(f"{where}：between 操作符需要 [min, max] 两元素 list")
                elif rule.value[0] > rule.value[1]:
                    errors.append(f"{where}：between 的下限不能大于上限")
            elif isinstance(rule.value, list):
                errors.append(f"{where}：{rule.op} 操作符需要单个数值 value")
        for index, group in enumerate(filters.groups):
            where = f"{prefix}{block}.groups[{index}]"
            if group.is_empty:
                errors.append(f"{where} 是空规则组：至少需要一条规则或一个非空子组")
                continue
            # 子组沿用同一套跨字段校验，路径逐层展开
            errors.extend(
                StrategyConfigService._filter_block_errors("", group, block=where)
            )
        return errors

    @staticmethod
    def _rank_block_errors(prefix: str, rank: RankConfig | None) -> list[str]:
        """校验一个排名配置块的跨字段规则。

        Args:
            prefix: 配置路径前缀。
            rank: 排名配置，None 表示未覆盖。

        Returns:
            错误信息列表。
        """
        if rank is None:
            return []
        if rank.top_n is not None and rank.bottom_n is not None:
            return [f"{prefix}rank：top_n 和 bottom_n 不能同时设置"]
        return []

    @staticmethod
    def _portfolio_block_errors(prefix: str, portfolio: PortfolioConfig | None) -> list[str]:
        """校验一个组合配置块的择时仓位映射。

        Args:
            prefix: 配置路径前缀。
            portfolio: 组合配置，None 表示未覆盖。

        Returns:
            错误信息列表。
        """
        if portfolio is None or not portfolio.timing_exposure:
            return []
        errors: list[str] = []
        valid_regimes = {"offensive", "neutral", "defensive"}
        unknown_regimes = set(portfolio.timing_exposure) - valid_regimes
        if unknown_regimes:
            errors.append(
                f"{prefix}portfolio.timing_exposure 包含未知 regime: "
                + ", ".join(sorted(unknown_regimes))
            )
        for regime, exposure in portfolio.timing_exposure.items():
            if not 0.0 <= exposure <= 1.0:
                errors.append(f"{prefix}portfolio.timing_exposure.{regime} 必须在 [0, 1] 范围内")
        return errors

    @staticmethod
    def _partial_timing_exposure_warnings(
        prefix: str, portfolio: PortfolioConfig | None, regimes: tuple[str, ...]
    ) -> list[str]:
        """提示 timing_exposure 只配置了部分 regime 的口径歧义。

        引擎对 timing_exposure 中缺失的 regime 回退到 ``default_exposure``，而不是
        默认三档表；只写了两档时，缺的那一档会静默按默认仓位运行。

        Args:
            prefix: 配置路径前缀。
            portfolio: 组合配置，None 表示未覆盖。
            regimes: 该组合块会被读取的 regime（顶层为三档，regime 覆盖块为本 regime）。

        Returns:
            警告信息列表；未配置 timing_exposure 或所需档位齐全时为空。
        """
        if portfolio is None or not portfolio.timing_exposure:
            return []
        missing = [regime for regime in regimes if regime not in portfolio.timing_exposure]
        if not missing:
            return []
        detail = "、".join(
            f"{regime}（默认三档表为 {_DEFAULT_TIMING_EXPOSURE[regime]:.0%}）" for regime in missing
        )
        return [
            f"{prefix}portfolio.timing_exposure 缺少 {detail}：缺失档位回退到 "
            f"default_exposure={portfolio.default_exposure:.0%}，而不是默认三档表；"
            "建议把 offensive / neutral / defensive 三档都写全"
        ]

    def _factor_alias_errors(
        self,
        config: StrategyConfig,
        registry: FactorTemplateRegistry,
        active_by_id: dict[str, Any],
        warnings: list[str],
    ) -> list[str]:
        """校验 factor_aliases 声明的模板存在、启用且参数合法。

        Args:
            config: 已解析的策略配置。
            registry: 因子模板注册表。
            active_by_id: 数据库侧已启用的模板定义，key=模板 ID。
            warnings: 追加非阻塞提示的列表。

        Returns:
            错误信息列表；全部别名合法时为空。
        """
        if not config.factor_aliases:
            return []
        referenced = set(self._collect_referenced_factors(config))
        errors: list[str] = []
        for alias, alias_config in config.factor_aliases.items():
            template = registry.get(alias_config.template_id)
            if template is None:
                errors.append(
                    f"别名 {alias} 指向未知模板 {alias_config.template_id}：可用模板见 GET /factors"
                )
                continue
            try:
                template.resolve_params(alias_config.params)
            except FactorParameterError as exc:
                errors.append(f"别名 {alias} 参数不合法：{exc}")
            if template.template_id not in active_by_id:
                errors.append(
                    f"别名 {alias} 指向的模板 {template.template_id} 已停用或未同步："
                    "请运行 POST /factors/init 同步并确保 is_active=true"
                )
            if alias not in referenced:
                warnings.append(f"别名 {alias} 已声明但未被任何模块引用")
        return errors

    @staticmethod
    def _collect_referenced_factors(config: StrategyConfig) -> list[str]:
        """收集策略各模块引用到的全部因子引用名。

        Args:
            config: 已解析的策略配置。

        Returns:
            去重后的引用名列表。
        """
        refs = {ref for _module_key, _label, ref in iter_factor_refs(config)}
        return sorted(refs)

    def _factor_reference_errors(
        self,
        config: StrategyConfig,
        registry: FactorTemplateRegistry,
        active_by_id: dict[str, Any],
    ) -> list[str]:
        """校验策略引用的全部因子可解析、模板启用且用途与消费模块匹配。

        Args:
            config: 已解析的策略配置。
            registry: 因子模板注册表。
            active_by_id: 数据库侧已启用的模板定义，key=模板 ID。

        Returns:
            错误信息列表。
        """
        # 各消费点 → 因子引用；usage 校验按消费点匹配（引用清单与因子加载同源）
        errors: list[str] = []
        seen: set[tuple[str, str]] = set()
        for module_key, module_label, ref in iter_factor_refs(config):
            if (module_key, ref) in seen:
                continue
            seen.add((module_key, ref))
            try:
                instance = registry.resolve(ref, config.factor_aliases)
            except FactorResolutionError as exc:
                errors.append(str(exc))
                continue
            template = instance.template
            if template.template_id == "expr":
                limit = _backtest_preload_days(registry)
                if instance.lookback_days > limit:
                    errors.append(
                        f"派生因子 {ref} 需要回望约 {instance.lookback_days} 个自然日（依赖回望 + "
                        f"时序窗口），超过回测行情预加载 {limit} 日：请缩短时序窗口或依赖周期"
                    )
            usage = list(template.usage)
            # 组合波动率因子（inverse_vol）逐资产取值，用途按评分口径校验
            usage_key = "score" if module_key == "portfolio" else module_key
            if usage and usage_key not in usage:
                errors.append(
                    f"因子 {ref}（模板 {template.template_id}）不适用于{module_label}位置"
                    f"（usage={usage}），请调整配置或选择其他因子"
                )
            if template.template_id not in active_by_id:
                errors.append(
                    f"因子 {ref} 指向的模板 {template.template_id} 已停用或未同步："
                    "请运行 POST /factors/init 同步并确保 is_active=true"
                )
        return errors

    @staticmethod
    def _transform_validation_errors(config: StrategyConfig) -> list[str]:
        """校验 transforms 引用的变换函数均存在。

        覆盖评分、择时及 regime 条件化配置中的 transforms，
        避免未知变换名在引擎执行时才抛出 KeyError。

        Args:
            config: 已解析的策略配置。

        Returns:
            错误信息列表。
        """
        transforms: dict[str, str] = {}
        if config.score:
            transforms.update(config.score.transforms)
        if config.timing:
            transforms.update(config.timing.transforms)
        for regime_rule in config.regime_rules.values():
            if regime_rule.score:
                transforms.update(regime_rule.score.transforms)

        errors: list[str] = []
        for factor_id, transform_name in transforms.items():
            if transform_name and not is_valid_transform(transform_name):
                errors.append(
                    f"未知变换函数 '{transform_name}'（用于因子 {factor_id}），"
                    f"可用: {transform_hints()}"
                )
        return errors

    @staticmethod
    def _timing_mode_issues(timing: TimingConfig) -> tuple[list[str], list[str]]:
        """校验择时综合方式、规则组、按因子代理与状态层的跨字段规则。

        Args:
            timing: 择时配置。

        Returns:
            (错误列表, 警告列表)。
        """
        errors: list[str] = []
        warnings: list[str] = []
        if timing.combine == "weighted":
            if not timing.factors:
                errors.append("timing.factors 不能为空（加权模式 combine=weighted）")
            if timing.rules is not None:
                warnings.append("timing.rules 仅在 combine=rules 时生效，当前加权模式会忽略")
        else:
            groups = timing.rules
            has_groups = groups is not None and any(
                group is not None and group.rules for group in (groups.offensive, groups.defensive)
            )
            if timing.tiers and has_groups:
                errors.append(
                    "规则模式下 timing.tiers 与 timing.rules.offensive / defensive 互斥："
                    "配置多档仓位时由档位的 regime 决定市场状态，请删除进攻 / 防守规则组"
                )
            elif not timing.tiers and not has_groups:
                errors.append(
                    "规则模式（combine=rules）必须在 timing.rules.offensive / defensive "
                    "中至少配置一条规则（或改用 timing.tiers 多档仓位）"
                )
            if groups is not None:
                for regime in ("offensive", "defensive"):
                    errors.extend(
                        StrategyConfigService._filter_block_errors(
                            "", getattr(groups, regime), block=f"timing.rules.{regime}"
                        )
                    )
            if timing.factors:
                warnings.append("规则模式（combine=rules）不使用 timing.factors 权重，已忽略")
            if timing.transforms:
                warnings.append("规则模式比较因子原值，timing.transforms 不生效")
            if timing.state.hysteresis > 0:
                warnings.append(
                    "timing.state.hysteresis 只对加权综合分生效，规则模式下不起作用；"
                    "可改用 confirm_days / hold_on_neutral 抑制翻转"
                )
        tier_errors, tier_warnings = StrategyConfigService._timing_tier_issues(timing)
        errors.extend(tier_errors)
        warnings.extend(tier_warnings)
        dynamic_errors, dynamic_warnings = StrategyConfigService._timing_dynamic_issues(timing)
        errors.extend(dynamic_errors)
        warnings.extend(dynamic_warnings)
        refs = set(timing.market_refs())
        for ref, codes in timing.factor_proxies.items():
            if not codes:
                errors.append(f"timing.factor_proxies.{ref} 不能为空列表")
            if ref not in refs:
                warnings.append(f"timing.factor_proxies.{ref} 不是择时实际使用的因子，已忽略")
        return errors, warnings

    @staticmethod
    def _timing_tier_issues(timing: TimingConfig) -> tuple[list[str], list[str]]:
        """校验多档仓位（timing.tiers）在两种综合方式下的命中条件。

        - 加权模式：每档必须给 ``min_score`` 且严格降序（首个命中即生效，乱序会让
          低档位永远命不中）；不得给规则；``regime`` 由阈值判定、档位上的值被忽略；
          最低档下限 > 0 时提示未覆盖区间回退 regime 仓位表。
        - 规则模式：每档必须声明 ``regime`` 并至少一条规则（规则跨字段校验与过滤
          规则一致）；``min_score`` 被忽略；``hold_on_neutral`` 会把命中 neutral 档位
          也当作无信号沿用前值，需提示。

        Args:
            timing: 择时配置。

        Returns:
            (错误列表, 警告列表)；未配置档位时均为空。
        """
        errors: list[str] = []
        warnings: list[str] = []
        if not timing.tiers:
            return errors, warnings
        if timing.combine == "weighted":
            previous: float | None = None
            for index, tier in enumerate(timing.tiers):
                where = f"timing.tiers[{index}]"
                if not tier.as_rule_group().is_empty:
                    errors.append(
                        f"{where}：加权模式按综合分匹配档位，不能配置 rules / groups"
                        "（规则档位需 combine=rules）"
                    )
                if tier.regime is not None:
                    warnings.append(
                        f"{where}.regime 在加权模式下被忽略：regime 由 timing.thresholds 判定"
                    )
                if tier.min_score is None:
                    errors.append(f"{where}.min_score 必填（加权模式：综合分 ≥ min_score 命中）")
                    continue
                if previous is not None and tier.min_score >= previous:
                    errors.append(
                        f"{where}.min_score={tier.min_score:g} 必须小于上一档的 {previous:g}："
                        "档位按顺序首个命中生效，需按 min_score 严格降序排列"
                    )
                previous = tier.min_score
            if previous is not None and previous > 0:
                warnings.append(
                    f"综合分低于 {previous:g} 时未命中任何档位，按 regime 查 "
                    "portfolio.timing_exposure（或默认三档表）决定仓位"
                )
            return errors, warnings

        for index, tier in enumerate(timing.tiers):
            where = f"timing.tiers[{index}]"
            if tier.regime is None:
                errors.append(
                    f"{where}.regime 必填（规则模式：命中该档时的 offensive / neutral / defensive）"
                )
            if tier.as_rule_group().is_empty:
                errors.append(f"{where} 至少需要一条命中规则（或非空子规则组）")
            if tier.min_score is not None:
                warnings.append(f"{where}.min_score 仅在加权模式生效，规则模式下被忽略")
            errors.extend(
                StrategyConfigService._filter_block_errors("", tier.as_rule_group(), block=where)
            )
        if timing.state.hold_on_neutral and any(tier.regime == "neutral" for tier in timing.tiers):
            warnings.append(
                "timing.state.hold_on_neutral 对 neutral 档位同样生效：命中 neutral 档位时"
                "也会沿用上一决策日的 regime 与档位，中性档位的仓位不会生效；"
                "只想让灰区维持仓位时，改用该档位的 hold: true"
            )
        return errors, warnings

    @staticmethod
    def _timing_dynamic_issues(timing: TimingConfig) -> tuple[list[str], list[str]]:
        """校验动态档位、步长限制与条件仓位上限。

        Args:
            timing: 择时配置。

        Returns:
            (错误列表, 警告列表)。
        """
        errors: list[str] = []
        warnings: list[str] = []
        if timing.state.max_exposure_step is not None and not timing.tiers:
            warnings.append(
                "timing.state.max_exposure_step 只约束由 timing.tiers 决定的仓位；"
                "未配置档位时不起作用"
            )
        for index, tier in enumerate(timing.tiers):
            if tier.exposure_delta == 0:
                warnings.append(
                    f"timing.tiers[{index}].exposure_delta=0 等同于 hold，建议直接用 hold: true"
                )
        for index, cap in enumerate(timing.exposure_caps):
            where = f"timing.exposure_caps[{index}]"
            if cap.as_rule_group().is_empty:
                errors.append(f"{where} 至少需要一条命中规则（或非空子规则组）")
            errors.extend(
                StrategyConfigService._filter_block_errors("", cap.as_rule_group(), block=where)
            )
            if cap.max_exposure >= 1.0:
                warnings.append(f"{where}.max_exposure=100% 不会封顶任何仓位")
        return errors, warnings

    @staticmethod
    def _timing_scale_errors(config: StrategyConfig, registry: FactorTemplateRegistry) -> list[str]:
        """校验择时因子的量纲：原值不在 0-100 评分空间的因子必须配置变换函数。

        择时综合分在 0-100 空间加权并与阈值比较；RSRS 的 z 值、收益率百分比等
        原值直接参与加权会让综合分静默失真。模板声明 ``native_0_100`` 的因子
        （RSI、市场宽度、估值百分位等）可不配变换。regime_rules 不含择时配置，
        无需检查。引用无法解析的错误已由引用校验报告，这里跳过。

        Args:
            config: 已解析的策略配置。
            registry: 因子模板注册表。

        Returns:
            错误信息列表。
        """
        if config.timing is None or config.timing.combine != "weighted":
            # 规则模式比较原值，不在 0-100 空间合成，无需量纲检查
            return []
        errors: list[str] = []
        for ref in config.timing.factors:
            if config.timing.transforms.get(ref):
                continue
            try:
                template = registry.resolve(ref, config.factor_aliases).template
            except FactorResolutionError:
                continue
            if template.native_0_100:
                continue
            errors.append(
                f"择时因子 {ref}（模板 {template.template_id}）原值不在 0-100 评分空间，"
                f"必须在 timing.transforms 中配置变换函数（可用: {transform_hints()}），"
                "否则择时综合分会静默失真"
            )
        return errors

    def get_parsed_config(self, strategy_id: str) -> StrategyConfig | None:
        """获取解析后的 StrategyConfig 对象。

        Args:
            strategy_id: 策略标识。

        Returns:
            解析后的配置对象，不存在返回 None。

        """
        row = self._repo.find_by_id(strategy_id)
        if row is None:
            return None
        if is_composite_config(row.config_json):
            # 组合策略没有引擎管线配置：由 get_composite_config 读取
            return None
        try:
            full_config = {
                "strategy_id": row.strategy_id,
                "display_name": row.display_name,
                "version": row.version,
                "description": row.description or "",
                "frequency": row.frequency,
                **row.config_json,
            }
            return StrategyConfig(**full_config)
        except Exception:
            logger.exception("解析策略配置 %s 失败", strategy_id)
            return None

    @staticmethod
    def parse_snapshot(snapshot: dict[str, Any]) -> StrategyConfig | None:
        """从回测配置快照重建 StrategyConfig 对象。

        快照包含 strategy_id/display_name/version/frequency 等元数据与
        config_json；解析失败（如快照损坏）时返回 None。调用方必须把 None 视为
        错误，不得回退到当前配置——否则结果与快照哈希记录的配置不一致。

        Args:
            snapshot: 回测快照字典。

        Returns:
            解析后的配置对象，失败返回 None。

        """
        config_json = snapshot.get("config_json") or {}
        if is_composite_config(config_json):
            # 组合策略快照由 parse_composite_snapshot 解析
            return None
        try:
            full_config = {
                "strategy_id": snapshot.get("strategy_id", "_snapshot_"),
                "display_name": snapshot.get("display_name", ""),
                "version": snapshot.get("version", "1.0.0"),
                "description": snapshot.get("description", ""),
                "frequency": snapshot.get("frequency", "daily"),
                **config_json,
            }
            return StrategyConfig(**full_config)
        except Exception:
            logger.exception("解析回测配置快照失败")
            return None
