"""量价 / 择时研报研究台服务（只读）。

为因子中心下的研究页提供即时计算：读取指数日线 / 申万行业日线，调用
``domain.research.signal_lab`` 的纯函数计算因子与信号，并组装检验统计。
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
from quant_etf_api.engine.transforms import drawdown_score, momentum_score
from quant_etf_api.infra.db.repositories.benchmark_index import BenchmarkIndexRepository
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.infra.db.repositories.industry import (
    IndustryDailyBarRepository,
    IndustryUniverseRepository,
)
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
    SignalLabTercile,
    SignalLabTimingMetrics,
    SignalLabTimingResponse,
    SignalLabYearIC,
    SignalLabYearStat,
)
from quant_etf_api.services.period_config import load_period_boundaries

# 单次查询自然日上限（约 10 年）
MAX_RANGE_DAYS = 3700
# 字段覆盖门槛：低于该占比的指数在截面检验中剔除
COVERAGE_MIN = 0.95
DEFAULT_PROXY = "000300"
# 中间分量展示配置：信号 → [(列名, 标签, 轴)]
_COMPONENTS: dict[str, list[tuple[str, str, str]]] = {
    "existing_composite": [
        ("momentum_score", "动量得分", "signal"),
        ("drawdown_score", "回撤得分", "signal"),
    ],
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
    """领域定义转为 API 模型；GSISI 参考线随临界值参数变化。"""
    data = raw.to_dict()
    if raw.key == "gsisi" and params:
        t = float(params["threshold"])
        data["reference_lines"] = [
            {"label": f"+{t:g}", "value": t},
            {"label": "0", "value": 0.0},
            {"label": f"−{t:g}", "value": -t},
        ]
    return SignalLabDefinition.model_validate(data)


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
        return SignalLabCatalog(
            cross_section_factors=[
                _definition(defn) for defn, _ in lab.CROSS_SECTION_FACTORS.values()
            ],
            timing_signals=[_definition(defn) for defn in lab.TIMING_SIGNALS.values()],
            market_signals=[_definition(lab.GSISI_DEFINITION)],
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
        self, index_code: str, signal_key: str, start: date, end: date, raw_params: dict[str, Any]
    ) -> SignalLabTimingResponse:
        """计算单指数择时信号及评估指标。

        Raises:
            SignalLabError: 信号未知、参数非法或区间非法。
            SignalLabNotFound: 指数不存在。
        """
        self._validate_range(start, end)
        definition = lab.TIMING_SIGNALS.get(signal_key)
        if definition is None:
            raise SignalLabError(f"未知择时信号：{signal_key}")
        params = self._resolve(definition, raw_params)
        load_start = self._load_start(start, definition.warmup(params))
        loaded = self._load_bars(
            [index_code], load_start, self._fetch_end(end, lab.TIMING_FORWARD_DAYS)
        ).get(index_code)
        if loaded is None:
            raise SignalLabNotFound(f"指数 {index_code} 在区间内无日线数据")
        frame = self._compute_timing(signal_key, loaded.bars, params)
        coverage = self._coverage(index_code, loaded, start, end)
        issues: list[SignalLabIssue] = []
        reason = self._field_issue(coverage, definition.required_fields)
        if reason:
            coverage.used = False
            coverage.reason = reason
            issues.append(SignalLabIssue(level="warn", code="field_coverage", message=reason))
        return self._timing_response(
            definition=_definition(definition),
            params=params,
            index_code=index_code,
            loaded=loaded,
            frame=frame,
            start=start,
            end=end,
            components=_COMPONENTS.get(signal_key, []),
            coverage=coverage,
            issues=issues,
            notes=[],
        )

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
        """按信号键计算择时数据帧（列 value / on / 分量）。"""
        if key == "existing_composite":
            return lab.signal_existing_composite(bars, params, momentum_score, drawdown_score)
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
    ) -> SignalLabTimingResponse:
        """组装择时响应：展示区间序列 + 评估指标。"""
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

        ref = lab.signal_existing_composite(
            bars,
            lab.resolve_params(lab.TIMING_SIGNALS["existing_composite"], None),
            momentum_score,
            drawdown_score,
        )
        is_existing = definition.key == "existing_composite"
        years = max((pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25, 1 / 365.25)
        on_valid = on.dropna()
        flips = float(on_valid.diff().abs().sum()) / years if len(on_valid) > 1 else None
        agree = None
        if not is_existing:
            ref_on = ref["on"].loc[window]
            both = on.notna() & ref_on.notna()
            agree = float((on[both] == ref_on[both]).mean()) if both.any() else None
        metrics = SignalLabTimingMetrics(
            flips_per_year=_num(flips),
            on_ratio=_num(on.mean()),
            ic_fwd20=lab.time_series_spearman(value, fwd.loc[window]),
            ic_past20=lab.time_series_spearman(value, past.loc[window]),
            corr_existing=None
            if is_existing
            else lab.time_series_spearman(value, ref["value"].loc[window]),
            agree_existing=agree,
            strategy=SignalLabPerformance(**lab.performance_summary(strat.iloc[1:])),
            benchmark=SignalLabPerformance(**lab.performance_summary(bench.iloc[1:])),
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
        return SignalLabTimingResponse(
            signal=definition,
            params=params,
            index_code=index_code,
            index_name=loaded.name,
            start=start,
            end=end,
            includes_validation=includes_validation,
            dates=[d.date().isoformat() for d in close.index],
            close=_series_list(close),
            value=_series_list(value),
            on=_series_list(on),
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
