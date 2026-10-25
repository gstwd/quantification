"""量价 / 择时研报研究台服务（只读）。

为因子中心下的研究页提供即时计算：读取指数日线 / 申万行业日线，调用
``domain.research.signal_lab`` 的纯函数计算因子与信号，并组装检验统计。
择时综合分（默认示例或任一策略的 timing 块）走引擎实现：按回测口径现算择时因子，
逐日构建引擎上下文并调用 ``DefaultScoreCalculator.calculate_timing``，研究台评估的
就是引擎实际执行的择时口径。
不创建策略、不写任何因子表、不触发回测；区间触及验证期时只提示、不阻断。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from quant_etf_api.domain.research import signal_lab as lab
from quant_etf_api.domain.research.timing import count_regime_flips, regime_shares
from quant_etf_api.domain.strategies.timing_state import TimingState
from quant_etf_api.engine.config import ScoreConfig, StrategyConfig, TimingConfig
from quant_etf_api.engine.context_builder import ContextBuilder
from quant_etf_api.engine.factor_provider import FactorProvider
from quant_etf_api.engine.score import DefaultScoreCalculator
from quant_etf_api.engine.timing import evaluate_timing
from quant_etf_api.factors.catalog import FactorResolutionError, get_factor_template_registry
from quant_etf_api.infra.db.repositories.benchmark_index import BenchmarkIndexRepository
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.infra.db.repositories.index_valuation import IndexValuationRepository
from quant_etf_api.infra.db.repositories.industry import (
    IndustryDailyBarRepository,
    IndustryUniverseRepository,
)
from quant_etf_api.infra.db.repositories.macro_indicator import MacroIndicatorRepository
from quant_etf_api.infra.db.repositories.market_indicator import MarketIndicatorRepository
from quant_etf_api.infra.db.repositories.strategy_config import StrategyConfigRepository
from quant_etf_api.schemas.signal_lab import (
    SignalLabCatalog,
    SignalLabComponent,
    SignalLabCorrelation,
    SignalLabCoverage,
    SignalLabCrossSectionResponse,
    SignalLabDefinition,
    SignalLabICSummary,
    SignalLabIndexOption,
    SignalLabIssue,
    SignalLabLatestItem,
    SignalLabPerformance,
    SignalLabStrategyOption,
    SignalLabTercile,
    SignalLabTimingMetrics,
    SignalLabTimingResponse,
    SignalLabYearIC,
    SignalLabYearStat,
)
from quant_etf_api.services.period_config import load_period_boundaries
from quant_etf_api.services.strategy_config_service import StrategyConfigService

# 单次查询自然日上限（约 10 年）
MAX_RANGE_DAYS = 3700
# 字段覆盖门槛：低于该占比的指数在截面检验中剔除
COVERAGE_MIN = 0.95
DEFAULT_PROXY = "000300"
# 走引擎实现的择时信号键（默认示例或指定策略的 timing 块）
ENGINE_TIMING_KEY = "existing_composite"
# 引擎择时数据帧在请求起点之前多算的自然日：覆盖 t+1 收盘持仓口径的两日信号滞后
_ENGINE_FRAME_BUFFER_DAYS = 10
# 引擎口径下评估指数日线在请求起点之前的加载余量（自然日）：覆盖过去 20 日收益与持仓滞后
_ENGINE_EVAL_BUFFER_DAYS = 45
# 中间分量展示配置：信号 → [(列名, 标签, 轴)]；引擎择时的分量按 timing 块动态生成
_COMPONENTS: dict[str, list[tuple[str, str, str]]] = {
    "llt": [("llt", "LLT 趋势线", "price")],
    "amplitude_scissors": [
        ("up_ma", "上行波动均值 (H−O)/O", "aux"),
        ("down_ma", "下行波动均值 (O−L)/O", "aux"),
    ],
    "pv_resonance": [
        ("threshold", "开仓阈值", "signal"),
        ("price_mom", "价能 price_mom", "aux"),
        ("vol_mom", "量能 vol_mom", "aux"),
    ],
}
_OFFLINE_NOTES = [
    "NH−NL% 净新高占比需要全市场个股后复权高低价逐股滚动（约千万行），按请求现算超过 5 分钟，"
    "未放入本页；离线结论见 docs/research/QuantsPlaybook研报引入/report.md。",
    "本页参数可调仅用于理解原理与观察敏感性；晋升判定使用研报默认参数，禁止据此网格搜索。",
]


class SignalLabError(ValueError):
    """研究台请求参数或数据错误（映射为 HTTP 422）。"""


class SignalLabNotFound(LookupError):
    """请求的指数不存在（映射为 HTTP 404）。"""


@dataclass
class _Loaded:
    """已加载的单指数日线与名称。"""

    bars: pd.DataFrame
    name: str


def _num(value: Any) -> float | None:
    """将数值转为 JSON 安全的 float（NaN/inf → None）。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _series_list(series: pd.Series) -> list[float | None]:
    """序列转 JSON 安全列表。"""
    return [_num(v) for v in series.to_numpy()]


def _definition(
    raw: lab.SignalDefinition, params: dict[str, float] | None = None
) -> SignalLabDefinition:
    """领域定义转为 API 模型；GSISI / 择时综合分的参考线随阈值参数变化。"""
    data = raw.to_dict()
    if raw.key == "gsisi" and params:
        t = float(params["threshold"])
        data["reference_lines"] = [
            {"label": f"+{t:g}", "value": t},
            {"label": "0", "value": 0.0},
            {"label": f"−{t:g}", "value": -t},
        ]
    if raw.key == ENGINE_TIMING_KEY and params:
        data["reference_lines"] = _threshold_lines(
            lab.EXISTING_COMPOSITE_OFFENSIVE, float(params["defensive"])
        )
    return SignalLabDefinition.model_validate(data)


def _threshold_lines(offensive: float, defensive: float) -> list[dict[str, Any]]:
    """择时综合分的进攻 / 防守阈值参考线。"""
    return [
        {"label": f"防守 {defensive:g}", "value": defensive},
        {"label": f"进攻 {offensive:g}", "value": offensive},
    ]


def _strategy_definition(config: StrategyConfig) -> SignalLabDefinition:
    """按策略 timing 块生成择时综合分的展示定义（公式、阈值与代理指数）。

    Args:
        config: 含 timing 块的策略配置。

    Returns:
        API 定义模型（无可调参数，参考线为策略阈值）。
    """
    base = lab.TIMING_SIGNALS[ENGINE_TIMING_KEY]
    timing = config.timing
    if timing is None:
        raise SignalLabError(f"策略 {config.strategy_id} 未配置择时（timing）")
    aliases = "；".join(
        f"{ref} = {alias.template_id}({', '.join(f'{k}={v}' for k, v in alias.params.items())})"
        for ref in timing.factor_refs()
        if (alias := config.factor_aliases.get(ref)) is not None
    )
    proxies = ", ".join(timing.proxy_index_codes)
    if timing.factor_proxies:
        proxies += "（" + "；".join(
            f"{ref} 用 {', '.join(codes)}" for ref, codes in timing.factor_proxies.items()
        ) + "）"
    # 参考线两种模式都要用：规则模式下阈值取配置默认值，不能只在加权分支里赋值
    thresholds = timing.thresholds
    if timing.combine == "rules":
        formula = f"规则模式：{_rules_text(timing)}；代理指数 {proxies}"
    else:
        terms = " + ".join(
            f"{weight:g} × {timing.transforms.get(ref, '原值')}({ref})"
            for ref, weight in timing.factors.items()
        )
        formula = (
            f"Σ|w|·d / Σ|w|（负权重取 100 − d）：{terms}；≥ {thresholds.offensive:g} 进攻、"
            f"≤ {thresholds.defensive:g} 防守；代理指数 {proxies}"
        )
    state_text = _state_text(timing)
    if state_text:
        formula = f"{formula}；状态层：{state_text}"
    return SignalLabDefinition(
        key=base.key,
        label=f"策略择时：{config.display_name}",
        source=f"策略 {config.strategy_id}（v{config.version}）的 timing 块",
        formula=f"{formula}；{aliases}" if aliases else formula,
        principle=base.principle,
        required_fields=list(base.required_fields),
        params=[],
        reference_lines=_threshold_lines(thresholds.offensive, thresholds.defensive),
    )


def _timing_only_config(
    strategy_id: str,
    display_name: str,
    version: str,
    blocks: dict[str, Any],
) -> StrategyConfig:
    """构造只含择时块的引擎配置：其余模块不参与择时，避免为代理指数多算评分因子。

    Args:
        strategy_id: 策略 ID（仅作标识）。
        display_name: 展示名。
        version: 版本号。
        blocks: ``timing`` 与 ``factor_aliases`` 片段（dict 或已解析模型）。

    Returns:
        StrategyConfig（评分因子为空）。
    """
    return StrategyConfig(
        strategy_id=strategy_id,
        display_name=display_name,
        version=version,
        score=ScoreConfig(factors={}),
        **blocks,
    )


def _rules_text(timing: TimingConfig) -> str:
    """把规则模式的进攻 / 防守规则组转成可读文本（研究台定义展示用）。"""
    parts: list[str] = []
    rules = timing.rules
    for regime, label in (("defensive", "防守"), ("offensive", "进攻")):
        group = getattr(rules, regime) if rules else None
        if group is None or not group.rules:
            continue
        joiner = " 且 " if group.logic == "AND" else " 或 "
        conds = joiner.join(
            f"{rule.factor} {rule.op} {rule.compare_to if rule.compare_to else rule.value}"
            for rule in group.rules
        )
        parts.append(f"{label}：{conds}")
    parts.append("都不满足为中性（防守优先）")
    return "；".join(parts)


def _state_text(timing: TimingConfig) -> str:
    """把非恒等的状态层配置转成可读文本；恒等时返回空串。"""
    state = timing.state
    if state.is_identity:
        return ""
    parts: list[str] = []
    if state.hysteresis > 0:
        parts.append(f"滞回 {state.hysteresis:g} 分")
    if state.hold_on_neutral:
        parts.append("无信号维持前值")
    if state.confirm_days > 1:
        parts.append(f"连续 {state.confirm_days} 日确认")
    if state.min_hold_days > 0:
        parts.append(f"最短持有 {state.min_hold_days} 日")
    return "、".join(parts)


class SignalLabService:
    """量价 / 择时研报研究台服务。"""

    def __init__(self, db: Session) -> None:
        """初始化服务。

        Args:
            db: 数据库会话（只读使用）。
        """
        self._db = db
        self._boundaries = load_period_boundaries()

    # ------------------------------------------------------------------ 目录

    def catalog(self) -> SignalLabCatalog:
        """返回研究台目录。"""
        indexes = [
            SignalLabIndexOption(index_code=row.index_code, index_name=row.name_cn)
            for row in BenchmarkIndexRepository(self._db).find_active()
        ]
        # 可选的策略择时口径：启用、非变体、且配置了 timing 块的策略
        timing_strategies = [
            SignalLabStrategyOption(
                strategy_id=row.strategy_id, display_name=row.display_name, version=row.version
            )
            for row in StrategyConfigRepository(self._db).find_all_active()
            if not getattr(row, "is_variant", False) and (row.config_json or {}).get("timing")
        ]
        return SignalLabCatalog(
            cross_section_factors=[
                _definition(defn) for defn, _ in lab.CROSS_SECTION_FACTORS.values()
            ],
            timing_signals=[
                _definition(defn, lab.resolve_params(defn, None))
                for defn in lab.TIMING_SIGNALS.values()
            ],
            market_signals=[_definition(lab.GSISI_DEFINITION)],
            timing_strategies=timing_strategies,
            indexes=indexes,
            research_start=self._boundaries.research_start,
            research_end=self._boundaries.research_end,
            validation_start=self._boundaries.validation_start,
            default_proxy=DEFAULT_PROXY,
            max_range_days=MAX_RANGE_DAYS,
            offline_notes=list(_OFFLINE_NOTES),
        )

    # ------------------------------------------------------------------ 数据加载

    def _validate_range(self, start: date, end: date) -> None:
        """校验日期区间。"""
        if start > end:
            raise SignalLabError("开始日期不能晚于结束日期")
        if (end - start).days > MAX_RANGE_DAYS:
            raise SignalLabError(f"单次查询最多支持 {MAX_RANGE_DAYS} 个自然日")

    def _fetch_end(self, end: date, forward_days: int) -> date:
        """计算前向收益所需的数据截止日：研究期内请求不越过研究期末端。"""
        fetch_end = end + timedelta(days=int(forward_days * 1.6) + 10)
        if end <= self._boundaries.research_end:
            fetch_end = min(fetch_end, self._boundaries.research_end)
        return fetch_end

    @staticmethod
    def _load_start(start: date, warmup_days: int) -> date:
        """按预热交易日数推算加载起点（交易日 ≈ 自然日 / 1.45）。"""
        return start - timedelta(days=int(warmup_days * 1.5) + 30)

    def _load_bars(self, codes: list[str], start: date, end: date) -> dict[str, _Loaded]:
        """加载多个指数的日线（清洗后），返回 {代码: 日线}。"""
        names = {
            row.index_code: row.name_cn
            for row in BenchmarkIndexRepository(self._db).find_active_by_codes(codes)
        }
        rows = IndexDailyBarRepository(self._db).find_all_date_range(start, end, codes)
        records = [
            {
                "index_code": code,
                "trade_date": pd.Timestamp(day),
                "open": row.open_price,
                "high": row.high_price,
                "low": row.low_price,
                "close": row.close_price,
                "volume": row.volume,
                "amount": row.turnover,
            }
            for (code, day), row in rows.items()
        ]
        result: dict[str, _Loaded] = {}
        if not records:
            return result
        frame = pd.DataFrame.from_records(records)
        for code, grp in frame.groupby("index_code"):
            bars = grp.set_index("trade_date").sort_index()
            result[str(code)] = _Loaded(lab.clean_bars(bars), names.get(str(code), ""))
        return result

    def _coverage(
        self, code: str, loaded: _Loaded | None, start: date, end: date
    ) -> SignalLabCoverage:
        """计算单指数在 [start, end] 内的字段覆盖。"""
        if loaded is None:
            return SignalLabCoverage(index_code=code, used=False, reason="区间内无日线")
        part = loaded.bars.loc[pd.Timestamp(start) : pd.Timestamp(end)]
        cov = lab.field_coverage(part)
        return SignalLabCoverage(
            index_code=code,
            index_name=loaded.name,
            first_date=part.index.min().date() if len(part) else None,
            last_date=part.index.max().date() if len(part) else None,
            rows=len(part),
            **cov,
        )

    @staticmethod
    def _field_issue(cov: SignalLabCoverage, fields: tuple[str, ...]) -> str | None:
        """检查所需字段覆盖，返回不足原因（满足时为 None）。"""
        mapping = {"close": cov.close, "open": cov.ohl, "high": cov.ohl, "low": cov.ohl}
        mapping.update({"volume": cov.volume, "amount": cov.amount})
        lacking = sorted({f for f in fields if mapping[f] < COVERAGE_MIN})
        if not lacking:
            return None
        label = {"open": "OHL", "high": "OHL", "low": "OHL"}
        parts = sorted({f"{label.get(f, f)} {mapping[f] * 100:.1f}%" for f in lacking})
        return f"覆盖不足（< {COVERAGE_MIN * 100:.0f}%）：{'，'.join(parts)}"

    def _validation_issues(self, end: date) -> tuple[bool, list[SignalLabIssue]]:
        """区间触及验证期时生成提示。"""
        if not self._boundaries.is_validation_scope(end):
            return False, []
        return True, [
            SignalLabIssue(
                level="warn",
                code="validation_period",
                message=(
                    f"区间包含验证期（{self._boundaries.validation_start.isoformat()} 起）数据："
                    "仅供观察，不得据此调参或修改晋升结论。"
                ),
            )
        ]

    # ------------------------------------------------------------------ 择时

    def timing(
        self,
        index_code: str,
        signal_key: str,
        start: date,
        end: date,
        raw_params: dict[str, Any],
        strategy_id: str | None = None,
    ) -> SignalLabTimingResponse:
        """计算单指数择时信号及评估指标。

        择时综合分（``existing_composite``）走引擎实现：未指定策略时为默认示例
        （主策略 r60v90 口径，参数可调，以所选指数为择时代理）；指定 ``strategy_id``
        时读取该策略的 timing 块（含代理指数与阈值），参数不可覆盖。

        Args:
            index_code: 评估指数代码（持仓收益在该指数上计算）。
            signal_key: 择时信号键。
            start: 起始日期。
            end: 截止日期。
            raw_params: 信号参数。
            strategy_id: 可选策略 ID，仅对择时综合分生效。

        Raises:
            SignalLabError: 信号未知、参数非法、区间非法或策略未配置择时。
            SignalLabNotFound: 指数或策略不存在。
        """
        self._validate_range(start, end)
        definition = lab.TIMING_SIGNALS.get(signal_key)
        if definition is None:
            raise SignalLabError(f"未知择时信号：{signal_key}")
        if strategy_id and signal_key != ENGINE_TIMING_KEY:
            raise SignalLabError("strategy_id 只适用于择时综合分（existing_composite）")
        fetch_end = self._fetch_end(end, lab.TIMING_FORWARD_DAYS)
        notes: list[str] = []
        if signal_key == ENGINE_TIMING_KEY:
            if strategy_id:
                if raw_params:
                    raise SignalLabError("按策略 timing 块计算时参数取自策略配置，不接受参数覆盖")
                timing_config = self._strategy_timing_config(strategy_id)
                params: dict[str, float] = {}
                api_definition = _strategy_definition(timing_config)
                timing_block = timing_config.timing
                proxies = ", ".join(timing_block.proxy_index_codes) if timing_block else ""
                notes.append(
                    f"择时因子在策略代理指数 {proxies} 上按回测口径现算，持仓收益在所选指数 "
                    f"{index_code} 上评估。"
                )
            else:
                params = self._resolve(definition, raw_params)
                timing_config = self._default_timing_config(params, index_code)
                api_definition = _definition(definition, params)
                notes.append("默认示例以所选指数为择时代理（主策略配置的代理为 000300）。")
            loaded = self._load_bars(
                [index_code], start - timedelta(days=_ENGINE_EVAL_BUFFER_DAYS), fetch_end
            ).get(index_code)
            if loaded is None:
                raise SignalLabNotFound(f"指数 {index_code} 在区间内无日线数据")
            frame = self._engine_timing_frame(timing_config, loaded.bars.index, start)
            components = self._engine_components(timing_config)
            notes.append(
                "综合分由引擎 DefaultScoreCalculator.calculate_timing 逐日计算（与回测主循环同一"
                "实现）；择时因子全部缺失的降级日引擎按中性 regime 运行，研究台按无信号处理。"
            )
        else:
            params = self._resolve(definition, raw_params)
            load_start = self._load_start(start, definition.warmup(params))
            loaded = self._load_bars([index_code], load_start, fetch_end).get(index_code)
            if loaded is None:
                raise SignalLabNotFound(f"指数 {index_code} 在区间内无日线数据")
            frame = self._compute_timing(signal_key, loaded.bars, params)
            components = _COMPONENTS.get(signal_key, [])
            api_definition = _definition(definition)
        coverage = self._coverage(index_code, loaded, start, end)
        issues: list[SignalLabIssue] = []
        reason = self._field_issue(coverage, definition.required_fields)
        if reason:
            coverage.used = False
            coverage.reason = reason
            issues.append(SignalLabIssue(level="warn", code="field_coverage", message=reason))
        return self._timing_response(
            definition=api_definition,
            params=params,
            index_code=index_code,
            loaded=loaded,
            frame=frame,
            start=start,
            end=end,
            components=components,
            coverage=coverage,
            issues=issues,
            notes=notes,
            strategy_id=strategy_id or None,
        )

    # ------------------------------------------------------------------ 引擎择时

    def _strategy_timing_config(self, strategy_id: str) -> StrategyConfig:
        """读取策略配置并抽出择时块（只读）。

        Raises:
            SignalLabNotFound: 策略不存在。
            SignalLabError: 配置无法解析或未配置择时。
        """
        if StrategyConfigRepository(self._db).find_by_id(strategy_id) is None:
            raise SignalLabNotFound(f"策略 {strategy_id} 不存在")
        config_svc = StrategyConfigService(self._db)
        if config_svc.is_composite(strategy_id):
            raise SignalLabError(f"{strategy_id} 是组合策略：请对其成员策略做信号诊断")
        config = config_svc.get_parsed_config(strategy_id)
        if config is None:
            raise SignalLabError(f"策略 {strategy_id} 的配置无法解析")
        if config.timing is None:
            raise SignalLabError(f"策略 {strategy_id} 未配置择时（timing）")
        return _timing_only_config(
            config.strategy_id,
            config.display_name,
            config.version,
            {"timing": config.timing, "factor_aliases": config.factor_aliases},
        )

    @staticmethod
    def _default_timing_config(params: dict[str, float], proxy_code: str) -> StrategyConfig:
        """默认示例（主策略 r60v90 择时块）的引擎配置。"""
        return _timing_only_config(
            "signal_lab_existing_composite",
            "择时综合分默认示例",
            "1.0.0",
            lab.existing_composite_timing_block(params, proxy_code),
        )

    @staticmethod
    def _engine_components(config: StrategyConfig) -> list[tuple[str, str, str]]:
        """引擎择时的中间分量：各择时因子变换后得分（无变换的原值放辅助轴）。"""
        timing = config.timing
        if timing is None:
            return []
        components: list[tuple[str, str, str]] = []
        for ref in timing.factor_refs():
            transform = timing.transforms.get(ref) if timing.combine == "weighted" else None
            components.append(
                (
                    f"{lab.ENGINE_FACTOR_COLUMN_PREFIX}{ref}",
                    f"{ref} 得分（{transform}）" if transform else f"{ref} 原值",
                    "signal" if transform else "aux",
                )
            )
        return components

    def _engine_timing_frame(
        self, config: StrategyConfig, bars_index: pd.DatetimeIndex, start: date
    ) -> pd.DataFrame:
        """用引擎逐日计算择时综合分与 regime，对齐到评估指数的交易日。

        因子按回测口径现算（``FactorProvider.precompute_backtest_factors``，代理指数
        行情向前预加载模板回望窗口；市场级模板补充全市场行情），再逐日经
        ``ContextBuilder`` 取代理指数的择时因子、调用
        ``DefaultScoreCalculator.calculate_timing``，与回测主循环同一条实现。

        Args:
            config: 只含择时块的引擎配置。
            bars_index: 评估指数的交易日索引（帧按它对齐）。
            start: 请求起点；只在起点前留出少量缓冲日计算。

        Returns:
            ``lab.signal_engine_timing`` 的数据帧，索引为 ``bars_index``。

        Raises:
            SignalLabError: 择时因子引用无法解析。
        """
        timing = config.timing
        if timing is None or len(bars_index) == 0:
            return lab.signal_engine_timing(pd.DatetimeIndex([]), lambda _ts: (0.0, "", {}), [])
        calendar = bars_index[
            bars_index >= pd.Timestamp(start) - pd.Timedelta(days=_ENGINE_FRAME_BUFFER_DAYS)
        ]
        provider = FactorProvider(db=self._db, registry=get_factor_template_registry())
        try:
            instances = provider.resolve_instances(config)
        except FactorResolutionError as exc:
            raise SignalLabError(f"择时因子无法解析：{exc}") from exc
        # 实例回望：派生因子含依赖回望与时序窗口（模板级回望恒为 0）
        lookback = max((inst.lookback_days for inst in instances.values()), default=0)
        first_day = calendar[0].date() if len(calendar) else start
        load_start = first_day - timedelta(days=lookback + 10)
        load_end = bars_index[-1].date()
        proxies = timing.all_proxy_codes()
        codes = list(proxies)
        if any(inst.template.market_scope for inst in instances.values()):
            # 市场级模板（如市场宽度）需要全市场行情，口径与回测补充加载一致
            codes.extend(
                row.index_code
                for row in BenchmarkIndexRepository(self._db).find_for_period(load_start)
                if row.index_code not in codes
            )
        all_bars = IndexDailyBarRepository(self._db).find_all_date_range(
            load_start, load_end, index_codes=codes
        )
        valuation = IndexValuationRepository(self._db).find_range(load_start, load_end, proxies)
        macro = MacroIndicatorRepository(self._db).find_all_as_map()
        macro.update(MarketIndicatorRepository(self._db).find_all_as_map())
        matrix = provider.precompute_backtest_factors(
            config, [ts.date() for ts in calendar], proxies, all_bars, valuation, macro
        )
        builder = ContextBuilder(self._db, factor_provider=provider)
        calculator = DefaultScoreCalculator()
        # 状态层按交易日顺序推进（signal_engine_timing 逐日顺序调用），与回测主循环一致
        state_box: list[TimingState | None] = [None]

        def _evaluate(ts: pd.Timestamp) -> tuple[float, str, dict[str, Any]]:
            """单日：构建引擎上下文，调用引擎择时评估（原始判定 + 状态层）。"""
            day = ts.date()
            context = builder.build(
                config,
                day,
                index_codes=[],
                all_bars=all_bars,
                precomputed_factors=matrix.values.get(day, {}),
                timing_state=state_box[0],
            )
            signal, state_box[0] = evaluate_timing(calculator, timing, context)
            composite = signal.factors.get("composite_score")
            return (float(composite) if composite is not None else 0.0), signal.regime, (
                signal.factors
            )

        frame = lab.signal_engine_timing(calendar, _evaluate, timing.factor_refs())
        return frame.reindex(bars_index)

    def market(
        self, signal_key: str, proxy_code: str, start: date, end: date, raw_params: dict[str, Any]
    ) -> SignalLabTimingResponse:
        """计算市场级信号（GSISI），在代理指数上评估。

        Raises:
            SignalLabError: 信号未知、参数非法或数据不足。
            SignalLabNotFound: 代理指数不存在。
        """
        self._validate_range(start, end)
        if signal_key != lab.GSISI_DEFINITION.key:
            raise SignalLabError(f"未知市场级信号：{signal_key}")
        definition = lab.GSISI_DEFINITION
        params = self._resolve(definition, raw_params)
        load_start = self._load_start(start, definition.warmup(params))
        fetch_end = self._fetch_end(end, lab.TIMING_FORWARD_DAYS)
        loaded = self._load_bars([proxy_code], load_start, fetch_end).get(proxy_code)
        if loaded is None:
            raise SignalLabNotFound(f"代理指数 {proxy_code} 在区间内无日线数据")
        industry_codes = IndustryUniverseRepository(self._db).find_active_codes(None)
        rows = IndustryDailyBarRepository(self._db).find_range(
            load_start, fetch_end, industry_codes
        )
        ind = pd.DataFrame(
            [
                {
                    "trade_date": pd.Timestamp(r.trade_date),
                    "code": r.industry_code,
                    "close": r.close_price,
                }
                for r in rows
                if r.close_price is not None
            ]
        )
        if ind.empty:
            raise SignalLabError("区间内无申万行业日线，无法计算 GSISI")
        panel = ind.pivot(index="trade_date", columns="code", values="close").astype(float)
        weekly = lab.gsisi_weekly(panel, loaded.bars["close"].dropna(), int(params["beta_weeks"]))
        t = float(params["threshold"])
        state = lab.hysteresis_state(weekly["value"].dropna(), t, -t)
        calendar = loaded.bars.index
        frame = pd.DataFrame(
            {
                "value": weekly["value"].reindex(calendar, method="ffill"),
                "on": state.reindex(calendar, method="ffill"),
                "n_industries": weekly["n_industries"].reindex(calendar, method="ffill"),
            }
        )
        coverage = self._coverage(proxy_code, loaded, start, end)
        notes = [
            f"行业数 {panel.shape[1]}（活跃申万一级）；周值在下一周内前向填充为日频展示。",
            "临界值 0.301 对应 31 个行业的 Spearman 5% 显著性水平；行业数变化时需相应调整。",
        ]
        return self._timing_response(
            definition=_definition(definition, params),
            params=params,
            index_code=proxy_code,
            loaded=loaded,
            frame=frame,
            start=start,
            end=end,
            components=[("n_industries", "有效行业数", "aux")],
            coverage=coverage,
            issues=[],
            notes=notes,
        )

    @staticmethod
    def _resolve(definition: lab.SignalDefinition, raw: dict[str, Any]) -> dict[str, float]:
        """解析参数，领域层 ValueError 转为 SignalLabError。"""
        try:
            return lab.resolve_params(definition, raw)
        except ValueError as exc:
            raise SignalLabError(str(exc)) from exc

    @staticmethod
    def _compute_timing(key: str, bars: pd.DataFrame, params: dict[str, float]) -> pd.DataFrame:
        """按信号键计算研报择时数据帧（列 value / on / 分量）；择时综合分走引擎，不在此处。"""
        if key == "llt":
            return lab.signal_llt(bars, params)
        if key == "amplitude_scissors":
            return lab.signal_amplitude_scissors(bars, params)
        return lab.signal_pv_resonance(bars, params)

    def _timing_response(
        self,
        *,
        definition: SignalLabDefinition,
        params: dict[str, float],
        index_code: str,
        loaded: _Loaded,
        frame: pd.DataFrame,
        start: date,
        end: date,
        components: list[tuple[str, str, str]],
        coverage: SignalLabCoverage,
        issues: list[SignalLabIssue],
        notes: list[str],
        strategy_id: str | None = None,
    ) -> SignalLabTimingResponse:
        """组装择时响应：展示区间序列 + 评估指标。

        与"现有择时综合分"的相关 / 同向对照同样走引擎默认示例（所选指数为代理）；
        引擎择时帧（含 regime 列）额外输出 regime 序列、占比与翻转频率。
        """
        bars = loaded.bars
        close_all = bars["close"]
        on_all = frame["on"]
        strat_all, bench_all = lab.position_returns(on_all, close_all)
        fwd = close_all.shift(-lab.TIMING_FORWARD_DAYS) / close_all - 1
        past = close_all / close_all.shift(lab.TIMING_FORWARD_DAYS) - 1

        window = slice(pd.Timestamp(start), pd.Timestamp(end))
        close = close_all.loc[window]
        value = frame["value"].loc[window]
        on = on_all.loc[window]
        strat = strat_all.loc[window]
        bench = bench_all.loc[window]
        if len(close) == 0:
            raise SignalLabError("请求区间内没有交易日数据")
        # 区间首日不计收益，净值从 1 开始
        strat.iloc[0] = 0.0
        bench.iloc[0] = 0.0

        is_existing = definition.key == ENGINE_TIMING_KEY
        years = max((pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25, 1 / 365.25)
        on_valid = on.dropna()
        flips = float(on_valid.diff().abs().sum()) / years if len(on_valid) > 1 else None
        agree = None
        corr_existing = None
        if not is_existing:
            ref = self._engine_timing_frame(
                self._default_timing_config(
                    lab.resolve_params(lab.TIMING_SIGNALS[ENGINE_TIMING_KEY], None), index_code
                ),
                bars.index,
                start,
            )
            ref_on = ref["on"].loc[window]
            both = on.notna() & ref_on.notna()
            agree = float((on[both] == ref_on[both]).mean()) if both.any() else None
            corr_existing = lab.time_series_spearman(value, ref["value"].loc[window])
        # 引擎择时帧带 regime 列：缺失（降级 / 预热）记 None
        regimes: list[str | None] = []
        if "regime" in frame.columns:
            regimes = [r if isinstance(r, str) else None for r in frame["regime"].loc[window]]
        metrics = SignalLabTimingMetrics(
            flips_per_year=_num(flips),
            on_ratio=_num(on.mean()),
            ic_fwd20=lab.time_series_spearman(value, fwd.loc[window]),
            ic_past20=lab.time_series_spearman(value, past.loc[window]),
            corr_existing=corr_existing,
            agree_existing=agree,
            strategy=SignalLabPerformance(**lab.performance_summary(strat.iloc[1:])),
            benchmark=SignalLabPerformance(**lab.performance_summary(bench.iloc[1:])),
            regime_flips_per_year=(
                _num(count_regime_flips(regimes) / years) if any(regimes) else None
            ),
            regime_shares=regime_shares(regimes),
        )
        yearly: list[SignalLabYearStat] = []
        for year, idx in close.groupby(close.index.year).groups.items():
            s, b = strat.loc[idx].fillna(0.0), bench.loc[idx].fillna(0.0)
            yearly.append(
                SignalLabYearStat(
                    year=int(year),
                    strategy_return=_num((1 + s).prod() - 1),
                    benchmark_return=_num((1 + b).prod() - 1),
                    on_ratio=_num(on.loc[idx].mean()),
                )
            )
        includes_validation, val_issues = self._validation_issues(end)
        if value.isna().all():
            issues.append(
                SignalLabIssue(
                    level="error",
                    code="no_signal",
                    message="区间内信号全部为空：所需字段缺失或预热不足。",
                )
            )
        else:
            first_valid = value.first_valid_index()
            if first_valid is not None and first_valid > close.index[0]:
                issues.append(
                    SignalLabIssue(
                        level="info",
                        code="warmup",
                        message=f"信号自 {first_valid.date().isoformat()} 起有效（预热或数据起点所致）。",
                    )
                )
            if "degraded" in frame.columns and first_valid is not None:
                degraded = frame["degraded"].loc[first_valid : pd.Timestamp(end)].eq(True)
                if degraded.any():
                    issues.append(
                        SignalLabIssue(
                            level="warn",
                            code="timing_degraded",
                            message=(
                                f"有效期内 {int(degraded.sum())} 个交易日择时因子全部缺失："
                                "引擎在这些交易日按中性 regime 运行，本页按无信号处理。"
                            ),
                        )
                    )
        return SignalLabTimingResponse(
            signal=definition,
            params=params,
            index_code=index_code,
            index_name=loaded.name,
            strategy_id=strategy_id,
            start=start,
            end=end,
            includes_validation=includes_validation,
            dates=[d.date().isoformat() for d in close.index],
            close=_series_list(close),
            value=_series_list(value),
            on=_series_list(on),
            regime=regimes,
            components=[
                SignalLabComponent(
                    key=col, label=label, axis=axis, values=_series_list(frame[col].loc[window])
                )
                for col, label, axis in components
                if col in frame.columns
            ],
            strategy_nav=_series_list((1 + strat.fillna(0.0)).cumprod()),
            benchmark_nav=_series_list((1 + bench.fillna(0.0)).cumprod()),
            metrics=metrics,
            yearly=yearly,
            coverage=coverage,
            issues=val_issues + issues,
            notes=notes
            + [
                "持仓口径：t 日收盘信号、t+1 收盘成交（t 日收益乘 on_{t−2}），空仓收益为 0，未计交易成本。",
                f"IC fwd20 / past20 为信号值与未来 / 过去 {lab.TIMING_FORWARD_DAYS} 日收益的时序 "
                "Spearman 相关；past20 越高说明信号越滞后。",
            ],
        )

    # ------------------------------------------------------------------ 截面

    def cross_section(
        self,
        factor_key: str,
        start: date,
        end: date,
        horizon: int,
        index_codes: list[str] | None,
        raw_params: dict[str, Any],
    ) -> SignalLabCrossSectionResponse:
        """计算截面因子的周频 Rank IC、分组收益与相关性。

        Raises:
            SignalLabError: 因子未知、参数非法、区间非法或样本不足。
        """
        self._validate_range(start, end)
        if horizon < 1 or horizon > 60:
            raise SignalLabError("未来收益窗口需在 1~60 个交易日之间")
        entry = lab.CROSS_SECTION_FACTORS.get(factor_key)
        if entry is None:
            raise SignalLabError(f"未知截面因子：{factor_key}")
        definition, compute = entry
        params = self._resolve(definition, raw_params)
        repo = BenchmarkIndexRepository(self._db)
        if index_codes:
            codes = [row.index_code for row in repo.find_active_by_codes(index_codes)]
        else:
            codes = [row.index_code for row in repo.find_active()]
        if len(codes) < lab.MIN_CROSS_SECTION:
            raise SignalLabError(f"截面检验至少需要 {lab.MIN_CROSS_SECTION} 个有效指数")

        ref_specs = [
            (lab.CROSS_SECTION_FACTORS[tpl], p, label)
            for tpl, p, label in lab.EXISTING_SCORE_REFERENCES
        ]
        warmup = max([definition.warmup(params)] + [d.warmup(p) for (d, _), p, _ in ref_specs])
        loaded = self._load_bars(
            codes, self._load_start(start, warmup), self._fetch_end(end, horizon)
        )
        coverage: list[SignalLabCoverage] = []
        used: list[str] = []
        for code in codes:
            cov = self._coverage(code, loaded.get(code), start, end)
            if cov.used:
                reason = self._field_issue(cov, definition.required_fields)
                if reason:
                    cov.used, cov.reason = False, reason
            if cov.used:
                used.append(code)
            coverage.append(cov)

        calendar = pd.DatetimeIndex(
            sorted({d for code in codes if code in loaded for d in loaded[code].bars.index})
        )

        def _wide(fn: Any, p: dict[str, float], members: list[str]) -> pd.DataFrame:
            """逐指数计算并对齐到并集日历。"""
            cols = {c: fn(loaded[c].bars, p) for c in members if c in loaded}
            return pd.DataFrame(cols, index=calendar).reindex(columns=members)

        factor = _wide(compute, params, used)
        forward = _wide(lambda b, _p: b["close"].shift(-horizon) / b["close"] - 1, {}, used)
        in_range = calendar[(calendar >= pd.Timestamp(start)) & (calendar <= pd.Timestamp(end))]
        rebal = lab.week_end_dates(in_range)
        f_r, y_r = factor.reindex(rebal), forward.reindex(rebal)
        ic = lab.cross_section_rank_corr(f_r, y_r)
        n_assets = (f_r.notna() & y_r.notna()).sum(axis=1)
        valid_ic = ic.dropna()
        std = float(valid_ic.std()) if len(valid_ic) > 1 else None
        mean = float(valid_ic.mean()) if len(valid_ic) else None
        summary = SignalLabICSummary(
            mean=_num(mean),
            std=_num(std),
            icir=_num(mean / std) if mean is not None and std else None,
            positive_ratio=_num((valid_ic > 0).mean()) if len(valid_ic) else None,
            n_periods=len(valid_ic),
            avg_assets=_num(n_assets[ic.notna()].mean()) if len(valid_ic) else None,
        )
        yearly = [
            SignalLabYearIC(
                year=int(year), ic_mean=_num(grp.mean()), n_periods=int(grp.notna().sum())
            )
            for year, grp in ic.groupby(ic.index.year)
        ]
        terc = lab.tercile_mean_returns(f_r, y_r)
        terciles = [
            SignalLabTercile(
                group=f"G{i + 1}",
                mean_return=_num(v),
                annualized=_num(v * 252 / horizon) if v is not None else None,
            )
            for i, v in enumerate(terc)
        ]
        correlations: list[SignalLabCorrelation] = []
        for (ref_def, ref_fn), ref_p, label in ref_specs:
            if ref_def.key == definition.key and ref_p == params:
                continue
            ref_wide = _wide(ref_fn, ref_p, used).reindex(rebal)
            corr = lab.cross_section_rank_corr(f_r, ref_wide)
            correlations.append(SignalLabCorrelation(label=label, corr=_num(corr.mean())))

        latest_date: str | None = None
        latest: list[SignalLabLatestItem] = []
        has_values = f_r.notna().sum(axis=1) >= lab.MIN_CROSS_SECTION
        if has_values.any():
            last = has_values[has_values].index[-1]
            latest_date = last.date().isoformat()
            row = f_r.loc[last]
            ranks = row.rank(ascending=False, method="min")
            for code in used:
                latest.append(
                    SignalLabLatestItem(
                        index_code=code,
                        index_name=loaded[code].name if code in loaded else "",
                        value=_num(row.get(code)),
                        rank=int(ranks[code]) if pd.notna(ranks.get(code)) else None,
                        forward_return=_num(y_r.loc[last].get(code)),
                    )
                )
            latest.sort(key=lambda item: (item.rank is None, item.rank or 0))

        includes_validation, issues = self._validation_issues(end)
        excluded = [c for c in coverage if not c.used]
        if excluded:
            issues.append(
                SignalLabIssue(
                    level="info",
                    code="excluded_indexes",
                    message=f"{len(excluded)} 个指数因字段覆盖不足或无数据被剔除，详见覆盖表。",
                )
            )
        if not len(valid_ic):
            issues.append(
                SignalLabIssue(
                    level="error",
                    code="no_ic",
                    message=f"没有任何调仓日满足 ≥ {lab.MIN_CROSS_SECTION} 个有效样本。",
                )
            )
        return SignalLabCrossSectionResponse(
            factor=_definition(definition),
            params=params,
            horizon=horizon,
            start=start,
            end=end,
            includes_validation=includes_validation,
            dates=[d.date().isoformat() for d in rebal],
            ic=_series_list(ic),
            cum_ic=_series_list(ic.fillna(0.0).cumsum()),
            n_assets=[int(v) for v in n_assets.to_numpy()],
            summary=summary,
            yearly=yearly,
            terciles=terciles,
            correlations=correlations,
            latest_date=latest_date,
            latest=latest,
            coverage=coverage,
            issues=issues,
            notes=[
                "调仓日为每周最后一个交易日；未来收益为 close→close，按各指数自身交易日计算。",
                f"每期截面至少 {lab.MIN_CROSS_SECTION} 个有效样本；h > 5 时相邻期样本重叠，"
                "IC 序列自相关，ICIR 会被高估。",
                "三分组按因子截面百分位等分，G3 为高分组；年化 = 平均 h 日收益 × 252 / h。",
            ],
        )
