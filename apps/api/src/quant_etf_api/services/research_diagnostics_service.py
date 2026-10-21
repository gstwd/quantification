"""研究诊断服务（只读）：把研究台、因子 IC 与分桶收益诊断暴露给 CLI。

AI 在 CLI-only 环境下探索策略时，过去每轮都在 ``candidates/`` 里手写 pandas 原型
（分桶收益、IC、择时信号评估），口径与平台不一致。本服务统一入口：

- 研究台（``SignalLabService``）与因子 IC（``factors.evaluation.analyze_ic``）原样复用；
- 分桶收益统计的计算放在 ``domain.research.diagnostics`` 纯函数，这里只负责取数；
- 研究期边界为硬约束：区间必须落在研究期内（与 ``purpose=research`` 回测、优化会话
  同一条规则），前瞻收益所需行情也不越过研究期末端，避免验证期数据经由前瞻窗口泄漏。

不写库、不入队、不创建策略或回测。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from quant_etf_api.domain.research import diagnostics as diag
from quant_etf_api.engine.config import FactorAliasConfig
from quant_etf_api.factors.builtins.derived import DERIVED_TEMPLATE_ID
from quant_etf_api.domain.research.periods import (
    PURPOSE_RESEARCH,
    PeriodBoundaries,
    validate_backtest_period,
)
from quant_etf_api.factors.catalog import (
    FactorInstance,
    FactorResolutionError,
    FactorTemplateRegistry,
    get_factor_template_registry,
)
from quant_etf_api.factors.compute import FactorComputeService
from quant_etf_api.factors.evaluation import MIN_CROSS_SECTION_N, analyze_ic
from quant_etf_api.factors.templates import FactorParameterError
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.infra.db.repositories.strategy_config import StrategyConfigRepository
from quant_etf_api.services.period_config import load_period_boundaries
from quant_etf_api.services.signal_lab_service import SignalLabService
from quant_etf_api.services.strategy_config_service import StrategyConfigService

# 分桶诊断单个持有期上限（交易日）
MAX_BUCKET_HORIZON = 120
# 因子 IC 前瞻天数上限，与 HTTP 接口一致
MAX_IC_FORWARD_DAYS = 20


class ResearchDiagnosticsService:
    """研究诊断服务：研究期守卫 + 复用研究台 / 因子 IC + 分桶收益取数。"""

    def __init__(
        self,
        db: Session,
        boundaries: PeriodBoundaries | None = None,
        registry: FactorTemplateRegistry | None = None,
    ) -> None:
        """初始化服务。

        Args:
            db: 数据库会话（只读使用）。
            boundaries: 研究期/验证期边界，None 时读取系统配置。
            registry: 因子模板注册表，None 时使用进程级单例。
        """
        self._db = db
        self._boundaries = boundaries or load_period_boundaries()
        self._registry = registry or get_factor_template_registry()

    # ------------------------------------------------------------------ 研究期守卫

    def research_window(self, start: date | None, end: date | None) -> tuple[date, date]:
        """补全并校验研究区间：缺省取研究期全段，越过研究期末端直接拒绝。

        Args:
            start: 起始日期，None 时取研究期起点。
            end: 截止日期，None 时取研究期末端。

        Returns:
            (start, end)。

        Raises:
            ValueError: 区间倒置、早于研究期起点或越过研究期末端时抛出。
        """
        start = start or self._boundaries.research_start
        end = end or self._boundaries.research_end
        # 与 purpose=research 回测、优化会话共用同一条边界规则，只改写错误前缀
        try:
            validate_backtest_period(self._boundaries, PURPOSE_RESEARCH, start, end)
        except ValueError as exc:
            raise ValueError(f"研究诊断区间不合法（与研究类回测同一边界）：{exc}") from exc
        return start, end

    # ------------------------------------------------------------------ 研究台

    def signal_lab_catalog(self) -> dict[str, Any]:
        """研究台目录（``SignalLabService.catalog``）。"""
        return SignalLabService(self._db).catalog().model_dump(mode="json")

    def signal_lab_timing(
        self,
        index_code: str,
        signal_key: str,
        start: date | None,
        end: date | None,
        params: dict[str, Any],
        strategy_id: str | None = None,
    ) -> dict[str, Any]:
        """研究台单指数择时信号评估（``SignalLabService.timing``）。

        Raises:
            ValueError: 区间越界或研究台参数错误。
            LookupError: 指数或策略不存在。
        """
        start, end = self.research_window(start, end)
        result = SignalLabService(self._db).timing(
            index_code, signal_key, start, end, params, strategy_id=strategy_id
        )
        return result.model_dump(mode="json")

    def signal_lab_market(
        self,
        signal_key: str,
        proxy_code: str,
        start: date | None,
        end: date | None,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """研究台市场级信号评估（``SignalLabService.market``）。

        Raises:
            ValueError: 区间越界或研究台参数错误。
            LookupError: 代理指数不存在。
        """
        start, end = self.research_window(start, end)
        result = SignalLabService(self._db).market(signal_key, proxy_code, start, end, params)
        return result.model_dump(mode="json")

    def signal_lab_cross_section(
        self,
        factor_key: str,
        start: date | None,
        end: date | None,
        horizon: int,
        index_codes: list[str] | None,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """研究台截面因子周频 Rank IC（``SignalLabService.cross_section``）。

        Raises:
            ValueError: 区间越界或研究台参数错误。
        """
        start, end = self.research_window(start, end)
        result = SignalLabService(self._db).cross_section(
            factor_key, start, end, horizon, index_codes, params
        )
        return result.model_dump(mode="json")

    # ------------------------------------------------------------------ 因子解析

    def resolve_factor(
        self,
        factor_id: str | None,
        params: dict[str, Any] | None,
        strategy_id: str | None = None,
        ref: str | None = None,
        extra_aliases: dict[str, Any] | None = None,
    ) -> tuple[FactorInstance, str | None]:
        """解析诊断对象：模板 ID + 参数，或策略中的因子引用（别名 → 模板 + 参数）。

        派生因子（``--factor expr --params '{"expr": ...}'``）的引用按别名上下文解析：
        给了 ``strategy_id`` 时取该策略的 ``factor_aliases``，再叠加 ``extra_aliases``
        （研究批量评估变体里新增、尚未进入任何策略的别名）。

        Args:
            factor_id: 因子模板 ID（与 ``strategy_id``/``ref`` 二选一）。
            params: 模板参数覆盖（仅与 ``factor_id`` 一起使用）。
            strategy_id: 策略 ID；给定时按其 ``factor_aliases`` 解析 ``ref``。
            ref: 策略中的因子引用名（别名或模板 ID）。

        Returns:
            (因子实例, 推荐的因子计算指数)；推荐指数仅在 ``ref`` 是该策略择时因子时
            给出（取其代理指数首位），否则为 None。

        Raises:
            ValueError: 参数组合非法、模板/别名无法解析或参数不合法。
            LookupError: 策略不存在。
        """
        if ref:
            if not strategy_id:
                raise ValueError("按策略解析因子时 --strategy 与 --ref 必须同时给出")
            if factor_id or params:
                raise ValueError("--strategy/--ref 与 --factor/--params 只能二选一")
            if StrategyConfigRepository(self._db).find_by_id(strategy_id) is None:
                raise LookupError(f"策略 {strategy_id} 不存在")
            config = StrategyConfigService(self._db).get_parsed_config(strategy_id)
            if config is None:
                raise ValueError(f"策略 {strategy_id} 的配置无法解析")
            try:
                instance = self._registry.resolve(ref, config.factor_aliases)
            except (FactorResolutionError, FactorParameterError) as exc:
                raise ValueError(f"策略 {strategy_id} 的因子引用 {ref} 无法解析：{exc}") from exc
            timing = config.timing
            proxy = None
            if timing is not None and ref in timing.factor_refs():
                proxies = timing.proxies_for(ref)
                proxy = proxies[0] if proxies else None
            return instance, proxy
        if not factor_id:
            if strategy_id:
                raise ValueError(
                    "按策略解析因子时 --strategy 与 --ref 必须同时给出"
                    "（派生因子用 --strategy + --factor expr --params）"
                )
            raise ValueError("必须给出 --factor <模板ID>，或 --strategy <ID> --ref <因子引用>")
        if factor_id == DERIVED_TEMPLATE_ID:
            return self._resolve_derived(params or {}, strategy_id, extra_aliases or {}), None
        if strategy_id or extra_aliases:
            raise ValueError("--strategy / --aliases 只用于 --ref 或派生因子 --factor expr")
        try:
            return self._registry.resolve_params(factor_id, params or {}), None
        except (FactorResolutionError, FactorParameterError) as exc:
            raise ValueError(str(exc)) from exc

    def _resolve_derived(
        self, params: dict[str, Any], strategy_id: str | None, extra_aliases: dict[str, Any]
    ) -> FactorInstance:
        """按别名上下文解析派生因子表达式。

        Args:
            params: ``{"expr": ...}``。
            strategy_id: 提供别名上下文的策略。
            extra_aliases: 额外别名声明 ``{别名: {"template_id", "params"}}``。

        Returns:
            已绑定依赖的派生因子实例（实例 ID 为 ``expr``）。

        Raises:
            ValueError: 策略不存在、别名声明或表达式不合法。
            LookupError: 策略不存在。
        """
        aliases: dict[str, Any] = {}
        if strategy_id:
            if StrategyConfigRepository(self._db).find_by_id(strategy_id) is None:
                raise LookupError(f"策略 {strategy_id} 不存在")
            config = StrategyConfigService(self._db).get_parsed_config(strategy_id)
            if config is None:
                raise ValueError(f"策略 {strategy_id} 的配置无法解析")
            aliases.update(config.factor_aliases)
        try:
            for name, declaration in extra_aliases.items():
                aliases[name] = FactorAliasConfig(**declaration)
            aliases[DERIVED_TEMPLATE_ID] = FactorAliasConfig(
                template_id=DERIVED_TEMPLATE_ID, params=params
            )
            return self._registry.resolve(DERIVED_TEMPLATE_ID, aliases)
        except (FactorResolutionError, FactorParameterError, TypeError) as exc:
            raise ValueError(f"派生因子无法解析：{exc}") from exc
        except ValueError as exc:
            raise ValueError(f"派生因子别名声明不合法：{exc}") from exc

    @staticmethod
    def _factor_meta(instance: FactorInstance) -> dict[str, Any]:
        """诊断输出中的因子身份信息。"""
        return {
            "ref": instance.instance_id,
            "template_id": instance.template_id,
            "label": instance.label,
            "params": dict(instance.params),
        }

    # ------------------------------------------------------------------ 因子 IC

    def factor_ic(
        self,
        instance: FactorInstance,
        start: date | None,
        end: date | None,
        forward_days: int = 1,
        min_n: int = MIN_CROSS_SECTION_N,
    ) -> dict[str, Any]:
        """截面 Rank IC（``analyze_ic``），观测截止日收缩到前瞻窗口不越过研究期末端。

        ``analyze_ic`` 会在截止日之后预取约 60 个自然日行情构造前瞻收益；研究用途下
        末段观测的前瞻窗口会落进验证期，因此把观测截止日收缩为"研究期末端往前第
        ``forward_days`` 个交易日"。

        Args:
            instance: 因子实例。
            start: 起始日期，None 时取研究期起点。
            end: 截止日期，None 时取研究期末端。
            forward_days: 前瞻交易日数（1~20）。
            min_n: 有效 IC 所需最小横截面指数数量。

        Returns:
            ``{"factor", "start", "end", "observation_end", "forward_days", "summary",
            "yearly", "series"}``。

        Raises:
            ValueError: 区间越界、参数非法或研究期内交易日不足。
        """
        start, end = self.research_window(start, end)
        if not 1 <= forward_days <= MAX_IC_FORWARD_DAYS:
            raise ValueError(f"前瞻天数需在 1~{MAX_IC_FORWARD_DAYS} 个交易日之间")
        if min_n < 2:
            raise ValueError("最小横截面数量需 ≥ 2")
        calendar = IndexDailyBarRepository(self._db).find_all_trading_dates(
            start, self._boundaries.research_end
        )
        if len(calendar) <= forward_days:
            raise ValueError("研究期内交易日不足以构造前瞻收益")
        observation_end = min(end, calendar[-1 - forward_days])
        result = analyze_ic(
            self._db, instance, start, observation_end, forward_days, min_n, self._registry
        )
        return {
            "factor": self._factor_meta(instance),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "observation_end": observation_end.isoformat(),
            "forward_days": forward_days,
            "summary": result["summary"],
            "yearly": diag.yearly_ic(result["series"]),
            "series": result["series"],
            "notes": [
                "Rank IC 为当日 point-in-time 指数集合上因子值与未来 forward_days 日"
                " close→close 收益的 Spearman 相关；显著性看 summary.t_stat（已按重叠折算）。",
                "观测截止日已收缩到前瞻窗口不越过研究期末端（observation_end）。",
            ],
        }

    # ------------------------------------------------------------------ 分桶收益

    def _price_frame(self, index_code: str, start: date, end: date) -> pd.DataFrame:
        """加载单指数开 / 收盘价（原始行情，不前向填充），索引为交易日。"""
        rows = IndexDailyBarRepository(self._db).find_all_date_range(start, end, [index_code])
        records = [
            {"trade_date": pd.Timestamp(day), "open": row.open_price, "close": row.close_price}
            for (_code, day), row in rows.items()
        ]
        if not records:
            return pd.DataFrame(columns=["open", "close"], dtype=float)
        frame = pd.DataFrame.from_records(records).set_index("trade_date").sort_index()
        return frame.astype(float)

    def bucket_returns(
        self,
        instance: FactorInstance,
        factor_index: str,
        start: date | None,
        end: date | None,
        *,
        horizons: Sequence[int],
        basis: str = diag.BASIS_T1_OPEN,
        return_index: str | None = None,
        cut_points: Sequence[float] | None = None,
        quantiles: int | None = None,
        split_at: date | None = None,
        split_halves: bool = False,
    ) -> dict[str, Any]:
        """按因子值分桶统计未来 N 日收益（单指数时序诊断）。

        因子值由 ``FactorComputeService.compute_series`` 在 ``factor_index`` 上现算（与因子
        中心同一实现）；收益在 ``return_index``（缺省同 ``factor_index``）上按 ``basis`` 构造，
        行情只加载到研究期末端，前瞻窗口越界的样本自然剔除。

        Args:
            instance: 因子实例。
            factor_index: 计算因子值的指数代码（择时因子通常为代理指数）。
            start: 起始日期，None 时取研究期起点。
            end: 截止日期，None 时取研究期末端。
            horizons: 持有交易日数列表。
            basis: 收益口径，见 ``diagnostics.RETURN_BASES``。
            return_index: 计算收益的指数代码，None 时同 ``factor_index``。
            cut_points: 分桶内部切点，与 ``quantiles`` 二选一。
            quantiles: 分位数桶数，与 ``cut_points`` 二选一。
            split_at: 分段日（后段首日，含），给定时输出前后两段与一致性。
            split_halves: 未给 ``split_at`` 时是否按样本对半切分。

        Returns:
            分桶诊断字典（``diagnostics.bucket_forward_returns`` 的结果 + 元数据）。

        Raises:
            ValueError: 区间越界、参数非法或数据不足。
        """
        start, end = self.research_window(start, end)
        clean = sorted({int(h) for h in horizons})
        if not clean or clean[0] < 1 or clean[-1] > MAX_BUCKET_HORIZON:
            raise ValueError(f"持有期需在 1~{MAX_BUCKET_HORIZON} 个交易日之间")
        if basis not in diag.RETURN_BASES:
            raise ValueError(f"未知的收益口径：{basis}，可选值为 {', '.join(diag.RETURN_BASES)}")
        if split_at is not None and not start < split_at <= end:
            raise ValueError("分段日必须落在 (start, end] 区间内")
        return_code = return_index or factor_index

        rows = FactorComputeService(self._db, self._registry).compute_series(
            instance, factor_index, start, end
        )
        factor = pd.Series(
            {pd.Timestamp(r.trade_date): r.factor_value_numeric for r in rows}, dtype=float
        ).sort_index()
        if factor.dropna().empty:
            raise ValueError(
                f"因子 {instance.instance_id} 在指数 {factor_index} 的区间内没有有效值"
            )

        # 前瞻行情只加载到研究期末端：末段样本前瞻不足时为 NaN，自动剔除
        fetch_end = min(
            end + timedelta(days=int((clean[-1] + 1) * 1.6) + 15), self._boundaries.research_end
        )
        prices = self._price_frame(return_code, start, fetch_end)
        if prices.empty:
            raise ValueError(f"指数 {return_code} 在区间内没有日线数据")
        returns = {
            h: diag.forward_returns(prices["open"], prices["close"], h, basis) for h in clean
        }
        result = diag.bucket_forward_returns(
            factor,
            returns,
            cut_points=cut_points,
            quantiles=quantiles,
            split_at=pd.Timestamp(split_at) if split_at is not None else None,
            split_halves_flag=split_halves,
        )
        valid_factor = factor.dropna()
        return {
            "factor": self._factor_meta(instance),
            "factor_index": factor_index,
            "return_index": return_code,
            "basis": basis,
            "horizons": clean,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "factor_days": len(factor),
            "factor_valid_days": len(valid_factor),
            "first_valid_date": valid_factor.index[0].date().isoformat(),
            **result,
            "notes": [
                "收益口径：t_plus_1_open = open[T+1]→open[T+1+N]；t_plus_1_close = "
                "close[T+1]→close[T+1+N]；close_to_close = close[T]→close[T+N]（%），"
                "不计交易成本。",
                "t_stat 按 effective_n = n // N 折算前瞻窗口重叠（与因子 IC 同口径），"
                "t_stat_raw 为未折算值；low_sample 表示桶内样本 < "
                f"{diag.MIN_BUCKET_SAMPLES}。",
                "分位数分桶的边界取自全样本（含后段），只用于描述性诊断；分段一致性沿用"
                "同一组边界。",
                "前瞻行情不越过研究期末端，末段前瞻不足的样本已剔除。",
            ],
        }
