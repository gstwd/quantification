"""量价 / 择时研报研究台的纯计算规则（QuantsPlaybook 第一批）。

本模块只承载因子与信号的计算口径和检验统计，不读取数据库、不依赖服务层；
研究页与离线沙箱（``docs/research/QuantsPlaybook研报引入/sandbox.py``）口径一致：

- 截面因子（华西量价）：二阶动量、动量期限差、成交额波动（标准化）、量价相关，
  以及作为基准的现有 ``return`` / ``high_low`` 口径；
- 单指数择时信号：LLT 斜率（广发）、振幅剪刀差（国信）、价量共振（华创）；
- 择时综合分：本模块只给出默认示例的配置形态与帧组装，逐日计算由调用方注入引擎的
  ``DefaultScoreCalculator.calculate_timing``，与回测同一实现（不再单独维护公式）；
- 市场级信号：GSISI 投资者情绪指数（国信）。

所有序列都在单个资产自身的交易日序列上计算，再由调用方对齐日历，避免并集日历
中的空行打断滚动窗口。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

# 单指数日线面板必须包含的列
BAR_COLUMNS = ("open", "high", "low", "close", "volume", "amount")
# 截面检验的最少资产数
MIN_CROSS_SECTION = 10
# 时序评估的未来收益窗口（交易日）
TIMING_FORWARD_DAYS = 20


@dataclass(frozen=True)
class ParamSpec:
    """研究参数声明。

    Attributes:
        name: 参数名。
        label: 中文名称。
        default: 默认值（研报默认）。
        minimum: 最小值（含）。
        maximum: 最大值（含）。
        kind: "int" 或 "float"。
    """

    name: str
    label: str
    default: float
    minimum: float
    maximum: float
    kind: str = "int"

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "name": self.name,
            "label": self.label,
            "default": self.default,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class SignalDefinition:
    """研究台因子 / 信号定义。

    Attributes:
        key: 唯一标识。
        label: 中文名称。
        source: 研报来源。
        formula: 公式说明。
        principle: 原理说明。
        required_fields: 依赖的日线字段。
        params: 参数声明。
        warmup: 由已解析参数推导所需预热交易日数的函数。
        reference_lines: 信号值轴的参考线（标签, 值）。
    """

    key: str
    label: str
    source: str
    formula: str
    principle: str
    required_fields: tuple[str, ...]
    params: tuple[ParamSpec, ...]
    warmup: Callable[[dict[str, float]], int]
    reference_lines: tuple[tuple[str, float], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典（不含函数字段）。"""
        return {
            "key": self.key,
            "label": self.label,
            "source": self.source,
            "formula": self.formula,
            "principle": self.principle,
            "required_fields": list(self.required_fields),
            "params": [p.to_dict() for p in self.params],
            "reference_lines": [{"label": lb, "value": v} for lb, v in self.reference_lines],
        }


def resolve_params(definition: SignalDefinition, raw: dict[str, Any] | None) -> dict[str, float]:
    """按定义补齐默认值并校验参数范围。

    Args:
        definition: 因子 / 信号定义。
        raw: 用户传入的参数，可为 None。

    Returns:
        已解析的参数字典（int 参数转为 int）。

    Raises:
        ValueError: 出现未声明参数、类型非法或越界。
    """
    raw = dict(raw or {})
    known = {p.name for p in definition.params}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(f"{definition.label} 不支持参数：{', '.join(unknown)}")
    resolved: dict[str, float] = {}
    for spec in definition.params:
        value = raw.get(spec.name, spec.default)
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"参数 {spec.label}（{spec.name}）必须为数字") from exc
        if spec.kind == "int":
            if not number.is_integer():
                raise ValueError(f"参数 {spec.label}（{spec.name}）必须为整数")
            number = int(number)
        if number < spec.minimum or number > spec.maximum:
            raise ValueError(
                f"参数 {spec.label}（{spec.name}）超出范围 [{spec.minimum}, {spec.maximum}]"
            )
        resolved[spec.name] = number
    return resolved


# ---------------------------------------------------------------------------
# 基础算子
# ---------------------------------------------------------------------------


def simple_return(close: pd.Series, days: int) -> pd.Series:
    """days 日累计收益率（小数）。"""
    return close / close.shift(days) - 1


def weighted_moving_average(values: pd.Series, window: int) -> pd.Series:
    """线性加权移动平均（权重 1..window，与 talib.WMA 一致），窗口内含 NaN 时为 NaN。

    Args:
        values: 输入序列。
        window: 窗口长度（>= 1）。

    Returns:
        与输入同索引的 WMA 序列。
    """
    window = max(int(window), 1)
    arr = values.to_numpy(dtype=float)
    out = np.full(arr.shape, np.nan)
    if len(arr) >= window:
        weights = np.arange(1, window + 1, dtype=float)
        conv = np.convolve(arr, weights[::-1], mode="valid") / weights.sum()
        out[window - 1 :] = conv
    return pd.Series(out, index=values.index)


def hull_moving_average(values: pd.Series, window: int) -> pd.Series:
    """Hull 均线：WMA(2·WMA(x, n/2) − WMA(x, n), √n)。"""
    half = weighted_moving_average(values, int(window * 0.5))
    full = weighted_moving_average(values, window)
    return weighted_moving_average(2 * half - full, int(math.sqrt(window)))


def low_latency_trendline(price: pd.Series, period: int) -> pd.Series:
    """广发低延迟趋势线 LLT（二阶 IIR 滤波），前两期以价格初始化。

    LLT_t = (α−α²/4)P_t + (α²/2)P_{t−1} − (α−3α²/4)P_{t−2}
            + 2(1−α)LLT_{t−1} − (1−α)²LLT_{t−2}，α = 2/(period+1)。

    Args:
        price: 价格序列（不含 NaN）。
        period: 平滑周期 d。

    Returns:
        LLT 序列。
    """
    p = price.to_numpy(dtype=float)
    out = np.full(p.shape, np.nan)
    if len(p) == 0:
        return pd.Series(out, index=price.index)
    a = 2.0 / (period + 1)
    out[0] = p[0]
    if len(p) > 1:
        out[1] = p[1]
    c0, c1, c2 = a - a * a / 4, a * a / 2, a - 3 * a * a / 4
    d1, d2 = 2 * (1 - a), (1 - a) ** 2
    for t in range(2, len(p)):
        out[t] = c0 * p[t] + c1 * p[t - 1] - c2 * p[t - 2] + d1 * out[t - 1] - d2 * out[t - 2]
    return pd.Series(out, index=price.index)


def clean_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """规范单指数日线：非正数置空；OHL 任一缺失或 high < low 时三者整体置空。

    Args:
        frame: 含 BAR_COLUMNS 的日线（按日期升序）。

    Returns:
        清洗后的副本。
    """
    df = frame.reindex(columns=list(BAR_COLUMNS)).astype(float)
    df = df.where(df > 0)
    bad = df[["open", "high", "low"]].isna().any(axis=1) | (df["high"] < df["low"])
    df.loc[bad, ["open", "high", "low"]] = np.nan
    return df


def field_coverage(frame: pd.DataFrame) -> dict[str, float]:
    """计算清洗后各字段的有效占比（OHL 作为整体）。"""
    if frame.empty:
        return {"close": 0.0, "ohl": 0.0, "volume": 0.0, "amount": 0.0}
    return {
        "close": float(frame["close"].notna().mean()),
        "ohl": float(frame["open"].notna().mean()),
        "volume": float(frame["volume"].notna().mean()),
        "amount": float(frame["amount"].notna().mean()),
    }


# ---------------------------------------------------------------------------
# 截面因子
# ---------------------------------------------------------------------------


def factor_second_order_momentum(df: pd.DataFrame, p: dict[str, float]) -> pd.Series:
    """二阶动量：EMA(dev_t − dev_{t−d2}, d3)，dev = (close − MA(d1)) / MA(d1)。"""
    close = df["close"].dropna()
    ma = close.rolling(int(p["d1"])).mean()
    dev = (close - ma) / ma
    value = (dev - dev.shift(int(p["d2"]))).ewm(span=int(p["d3"]), adjust=False).mean()
    return value.reindex(df.index)


def factor_momentum_term_spread(df: pd.DataFrame, p: dict[str, float]) -> pd.Series:
    """动量期限差：ret(d1) − ret(d2)。"""
    close = df["close"].dropna()
    value = simple_return(close, int(p["d1"])) - simple_return(close, int(p["d2"]))
    return value.reindex(df.index)


def factor_amount_volatility(df: pd.DataFrame, p: dict[str, float]) -> pd.Series:
    """成交额波动（标准化）：−Std(amount, d) / Mean(amount, d)。"""
    d = int(p["d"])
    amount = df["amount"]
    return -(amount.rolling(d).std() / amount.rolling(d).mean())


def factor_price_volume_corr(df: pd.DataFrame, p: dict[str, float]) -> pd.Series:
    """量价相关：−Corr(close, volume, d)。"""
    return -df["close"].rolling(int(p["d"])).corr(df["volume"])


def factor_return(df: pd.DataFrame, p: dict[str, float]) -> pd.Series:
    """现有 return 因子：N 日累计收益率（%）。"""
    close = df["close"].dropna()
    return (simple_return(close, int(p["period"])) * 100).reindex(df.index)


def factor_high_low(df: pd.DataFrame, p: dict[str, float]) -> pd.Series:
    """现有 high_low 因子：(N 日最高收盘 − 最低收盘) / 最低收盘 × 100。"""
    close = df["close"].dropna()
    n = int(p["period"])
    hi, lo = close.rolling(n).max(), close.rolling(n).min()
    return ((hi - lo) / lo * 100).reindex(df.index)


CROSS_SECTION_FACTORS: dict[str, tuple[SignalDefinition, Callable[..., pd.Series]]] = {
    "second_order_momentum": (
        SignalDefinition(
            key="second_order_momentum",
            label="二阶动量",
            source="华西《行业有效量价因子与行业轮动》",
            formula="dev = (C − MA(C,d1)) / MA(C,d1)；因子 = EMA(dev_t − dev_{t−d2}, d3)",
            principle=(
                "乖离率的变化速度：价格相对均线的偏离正在扩大（加速）时取值高。"
                "相比一阶动量更早反映趋势强化/衰竭，但与短期收益高度同源。"
            ),
            required_fields=("close",),
            params=(
                ParamSpec("d1", "均线窗口", 20, 2, 250),
                ParamSpec("d2", "差分间隔", 5, 1, 60),
                ParamSpec("d3", "EMA 平滑", 10, 1, 60),
            ),
            warmup=lambda p: int(p["d1"] + p["d2"] + 3 * p["d3"]),
        ),
        factor_second_order_momentum,
    ),
    "momentum_term_spread": (
        SignalDefinition(
            key="momentum_term_spread",
            label="动量期限差",
            source="华西《行业有效量价因子与行业轮动》",
            formula="ret(d1) − ret(d2)，ret(d) = C_t / C_{t−d} − 1",
            principle=(
                "长周期动量减去短周期动量：长期强、近期回调的资产取值高，"
                "刻画“趋势中的回撤”这类中期动量 + 短期反转组合。"
            ),
            required_fields=("close",),
            params=(
                ParamSpec("d1", "长周期", 60, 5, 250),
                ParamSpec("d2", "短周期", 20, 1, 120),
            ),
            warmup=lambda p: int(max(p["d1"], p["d2"])),
        ),
        factor_momentum_term_spread,
    ),
    "amount_volatility": (
        SignalDefinition(
            key="amount_volatility",
            label="成交额波动（标准化）",
            source="华西《行业有效量价因子与行业轮动》",
            formula="−Std(amount, d) / Mean(amount, d)",
            principle=(
                "成交额变异系数取负：成交稳定（资金持续流入、而非脉冲式炒作）的指数取值高。"
                "原版用原始标准差，跨指数量级不可比，这里除以均值做标准化。"
            ),
            required_fields=("amount",),
            params=(ParamSpec("d", "窗口", 20, 5, 250),),
            warmup=lambda p: int(p["d"]),
        ),
        factor_amount_volatility,
    ),
    "price_volume_corr": (
        SignalDefinition(
            key="price_volume_corr",
            label="量价相关",
            source="华西《行业有效量价因子与行业轮动》",
            formula="−Corr(close, volume, d)",
            principle=(
                "价格与成交量的滚动相关取负：放量下跌 / 缩量上涨（量价背离）时取值高，"
                "对应筹码换手充分后的反转倾向。"
            ),
            required_fields=("close", "volume"),
            params=(ParamSpec("d", "窗口", 20, 5, 250),),
            warmup=lambda p: int(p["d"]),
        ),
        factor_price_volume_corr,
    ),
    "return": (
        SignalDefinition(
            key="return",
            label="N 日收益率（现有基准）",
            source="系统现有因子 return",
            formula="(C_t / C_{t−N} − 1) × 100",
            principle="主策略 r60v90 的核心评分因子（return_17d / return_5d），作为对照基准。",
            required_fields=("close",),
            params=(ParamSpec("period", "周期", 17, 1, 250),),
            warmup=lambda p: int(p["period"]),
        ),
        factor_return,
    ),
    "high_low": (
        SignalDefinition(
            key="high_low",
            label="N 日区间宽度（现有基准）",
            source="系统现有因子 high_low",
            formula="(N 日最高收盘 − 最低收盘) / 最低收盘 × 100",
            principle="主策略以负权重使用 high_low_21d / high_low_63d，作为对照基准。",
            required_fields=("close",),
            params=(ParamSpec("period", "周期", 21, 2, 250),),
            warmup=lambda p: int(p["period"]),
        ),
        factor_high_low,
    ),
}

# 截面相关性对照：现有主策略评分因子（模板, 参数, 标签）
EXISTING_SCORE_REFERENCES: tuple[tuple[str, dict[str, float], str], ...] = (
    ("return", {"period": 17}, "return_17d"),
    ("return", {"period": 5}, "return_5d"),
    ("high_low", {"period": 21}, "high_low_21d"),
    ("high_low", {"period": 63}, "high_low_63d"),
)


# ---------------------------------------------------------------------------
# 单指数择时信号：返回列 value / on 及若干中间分量
# ---------------------------------------------------------------------------


def signal_llt(df: pd.DataFrame, p: dict[str, float]) -> pd.DataFrame:
    """LLT 斜率择时：value = ΔLLT / LLT（相对斜率），on = 斜率 > 0。"""
    close = df["close"].dropna()
    line = low_latency_trendline(close, int(p["d"]))
    slope = line.diff() / line
    # LLT 由价格初始化，前 d 期尚未收敛，视为预热
    slope.iloc[: int(p["d"])] = np.nan
    out = pd.DataFrame({"value": slope, "llt": line.where(slope.notna())})
    out["on"] = (slope > 0).astype(float).where(slope.notna())
    return out.reindex(df.index)


def signal_amplitude_scissors(df: pd.DataFrame, p: dict[str, float]) -> pd.DataFrame:
    """振幅剪刀差：value = MA((H−O)/O − (O−L)/O, d)，on = value > 0。"""
    o, h, low = df["open"], df["high"], df["low"]
    up = (h - o) / o
    down = (o - low) / o
    d = int(p["d"])
    value = (up - down).rolling(d, min_periods=d).mean()
    out = pd.DataFrame(
        {
            "value": value,
            "up_ma": up.rolling(d, min_periods=d).mean(),
            "down_ma": down.rolling(d, min_periods=d).mean(),
        }
    )
    out["on"] = (value > 0).astype(float).where(value.notna())
    return out


def signal_pv_resonance(df: pd.DataFrame, p: dict[str, float]) -> pd.DataFrame:
    """华创价量共振：value = price_mom × vol_mom，on = value > 分市场阈值。

    price_mom = HMA(C, bma) / HMA(C, bma).shift(lag)；
    vol_mom = HMA(V, 5) / HMA(V, ama)；
    MA(C, fast) > MA(C, slow) 为多头市场，阈值取 bull_threshold，否则 bear_threshold。
    """
    sub = df[["close", "volume"]].dropna()
    bma_line = hull_moving_average(sub["close"], int(p["bma_window"]))
    price_mom = bma_line / bma_line.shift(int(p["lag"]))
    vol_mom = hull_moving_average(sub["volume"], 5) / hull_moving_average(
        sub["volume"], int(p["ama_window"])
    )
    value = price_mom * vol_mom
    fast = sub["close"].rolling(int(p["fast_window"])).mean()
    slow = sub["close"].rolling(int(p["slow_window"])).mean()
    threshold = pd.Series(
        np.where(fast > slow, p["bull_threshold"], p["bear_threshold"]), index=sub.index
    ).where(slow.notna())
    out = pd.DataFrame(
        {"value": value, "threshold": threshold, "price_mom": price_mom, "vol_mom": vol_mom}
    )
    valid = value.notna() & threshold.notna()
    out["on"] = (value > threshold).astype(float).where(valid)
    return out.reindex(df.index)


# 默认示例（主策略 r60v90 的择时块）的进攻阈值；防守阈值作为可调参数
EXISTING_COMPOSITE_OFFENSIVE = 65.0
# 引擎择时数据帧中各择时因子变换后得分列的前缀
ENGINE_FACTOR_COLUMN_PREFIX = "score:"


def existing_composite_timing_block(p: dict[str, float], proxy_code: str) -> dict[str, Any]:
    """构造"现有择时综合分"默认示例的策略配置片段（timing + factor_aliases）。

    默认示例与主策略 r60v90 的 timing 块同构：0.6 × momentum_score(N 日收益%) +
    0.4 × drawdown_score(M 日回撤%)，进攻阈值 65、防守阈值可调。这里只给出配置
    形态，计算一律交给引擎的 ``DefaultScoreCalculator.calculate_timing``，研究台与
    回测不再维护两份综合分实现。

    Args:
        p: 已解析参数（return_period / drawdown_period / defensive）。
        proxy_code: 择时代理指数代码（默认示例取所选指数本身）。

    Returns:
        可并入 ``StrategyConfig`` 的 ``{"timing": ..., "factor_aliases": ...}`` 字典。
    """
    ret_ref = f"return_{int(p['return_period'])}d"
    dd_ref = f"drawdown_{int(p['drawdown_period'])}d"
    return {
        "timing": {
            "factors": {ret_ref: 0.6, dd_ref: 0.4},
            "transforms": {ret_ref: "momentum_score", dd_ref: "drawdown_score"},
            "thresholds": {
                "offensive": EXISTING_COMPOSITE_OFFENSIVE,
                "defensive": float(p["defensive"]),
            },
            "proxy_index_codes": [proxy_code],
        },
        "factor_aliases": {
            ret_ref: {"template_id": "return", "params": {"period": int(p["return_period"])}},
            dd_ref: {"template_id": "drawdown", "params": {"period": int(p["drawdown_period"])}},
        },
    }


def signal_engine_timing(
    index: pd.DatetimeIndex,
    evaluate: Callable[[pd.Timestamp], tuple[float, str, dict[str, Any]]],
    factor_refs: Sequence[str],
) -> pd.DataFrame:
    """逐日调用引擎择时计算，组装研究台择时数据帧。

    ``evaluate`` 由调用方注入（服务层用引擎 ``DefaultScoreCalculator.calculate_timing``
    逐日计算），返回 (综合分, regime, 明细)，与回测主循环同一实现。

    降级日（择时因子全部缺失）引擎按中性 regime 运行并给 0 分占位；研究台把它们
    视为"无信号"（综合分、regime、on 均为空），避免 0 分污染 IC 与分布统计。

    Args:
        index: 交易日索引。
        evaluate: 单日择时计算函数。
        factor_refs: 择时因子引用名（决定输出的分量列）。

    Returns:
        列 value（综合分）/ regime / on（非防守 = 1）/ degraded / ``score:<引用名>``
        （各因子变换后得分）的数据帧，索引与 ``index`` 一致。
    """
    columns = ["value", "regime", "degraded"] + [
        f"{ENGINE_FACTOR_COLUMN_PREFIX}{ref}" for ref in factor_refs
    ]
    records: list[dict[str, Any]] = []
    for ts in index:
        composite, regime, details = evaluate(ts)
        degraded = bool(details.get("degraded"))
        record: dict[str, Any] = {
            "value": np.nan if degraded else float(composite),
            "regime": None if degraded else regime,
            "degraded": degraded,
        }
        for ref in factor_refs:
            item = details.get(ref)
            transformed = item.get("transformed") if isinstance(item, dict) else None
            record[f"{ENGINE_FACTOR_COLUMN_PREFIX}{ref}"] = (
                np.nan if transformed is None else float(transformed)
            )
        records.append(record)
    out = pd.DataFrame(records, index=index, columns=columns)
    # regime 保持 object 列且缺失为 None（DataFrame 构造会把 None 转成 NaN）
    out["regime"] = pd.Series([r["regime"] for r in records], index=index, dtype=object)
    out["degraded"] = out["degraded"].astype(bool)
    out["on"] = (out["regime"] != "defensive").astype(float).where(~out["degraded"])
    return out


TIMING_SIGNALS: dict[str, SignalDefinition] = {
    "existing_composite": SignalDefinition(
        key="existing_composite",
        label="择时综合分（引擎口径）",
        source="默认示例 = 主策略 r60v90 的 Timing 块；也可选择任一策略的 timing 块",
        formula=(
            "引擎 DefaultScoreCalculator：Σ|w|·d / Σ|w|，d = transform(因子)，负权重取 100 − d；"
            "默认示例 0.6 × momentum_score(ret_N%) + 0.4 × drawdown_score(dd_M%)，"
            "≥ 65 进攻、≤ 防守阈值防守；非防守视为持仓"
        ),
        principle=(
            "与回测逐日调用的是同一个引擎实现（因子现算 + calculate_timing），研究台评估的"
            "就是引擎实际执行的择时口径。默认示例的 90 日收益与 60 日回撤都是滞后指标，"
            "用来对照新信号的领先性与回撤控制。"
        ),
        required_fields=("close",),
        params=(
            ParamSpec("return_period", "收益周期", 90, 5, 250),
            ParamSpec("drawdown_period", "回撤周期", 60, 5, 250),
            ParamSpec("defensive", "防守阈值", 40, 0, 100, "float"),
        ),
        warmup=lambda p: int(max(p["return_period"], p["drawdown_period"])),
        reference_lines=(("防守 40", 40.0), ("进攻 65", 65.0)),
    ),
    "llt": SignalDefinition(
        key="llt",
        label="LLT 低延迟趋势线",
        source="广发《低延迟趋势线与交易择时》",
        formula=(
            "LLT_t = (α−α²/4)P_t + (α²/2)P_{t−1} − (α−3α²/4)P_{t−2} + 2(1−α)LLT_{t−1} "
            "− (1−α)²LLT_{t−2}，α = 2/(d+1)；信号 = LLT 斜率 > 0"
        ),
        principle=(
            "用二阶 IIR 滤波替代均线，在同等平滑度下显著降低滞后。"
            "本质仍是趋势跟随：斜率翻正持有、翻负空仓，震荡市会频繁来回。"
        ),
        required_fields=("close",),
        params=(ParamSpec("d", "平滑周期", 30, 5, 120),),
        warmup=lambda p: int(p["d"] * 2),
        reference_lines=(("0", 0.0),),
    ),
    "amplitude_scissors": SignalDefinition(
        key="amplitude_scissors",
        label="振幅剪刀差",
        source="国信《基于相对强弱下单向波动差值应用》",
        formula="MA((H−O)/O − (O−L)/O, d)；> 0 持仓",
        principle=(
            "把日内振幅拆成开盘后向上与向下两段：上行波动持续大于下行波动，"
            "说明盘中买方更强。需要完整 OHLC，主题指数开高低缺失时无法计算。"
        ),
        required_fields=("open", "high", "low"),
        params=(ParamSpec("d", "平滑窗口", 60, 5, 250),),
        warmup=lambda p: int(p["d"]),
        reference_lines=(("0", 0.0),),
    ),
    "pv_resonance": SignalDefinition(
        key="pv_resonance",
        label="价量共振",
        source="华创《成交量的奥秘：另类价量共振指标》",
        formula=(
            "price_mom = HMA(C,bma)/HMA(C,bma)_{t−lag}；vol_mom = HMA(V,5)/HMA(V,ama)；"
            "指标 = price_mom × vol_mom；MA(C,fast) > MA(C,slow) 时阈值取多头值，否则取空头值"
        ),
        principle=(
            "价格动量与量能同时放大时才开仓（共振）。分市场阈值让空头市场需要更强的共振，"
            "从而在下跌段大幅减少持仓；代价是信号翻转频繁。"
        ),
        required_fields=("close", "volume"),
        params=(
            ParamSpec("bma_window", "价格 HMA 窗口", 50, 5, 200),
            ParamSpec("ama_window", "量能 HMA 长窗口", 100, 10, 250),
            ParamSpec("lag", "价格动量滞后", 3, 1, 20),
            ParamSpec("fast_window", "市场划分短均线", 5, 2, 60),
            ParamSpec("slow_window", "市场划分长均线", 90, 20, 250),
            ParamSpec("bull_threshold", "多头阈值", 1.125, 0.5, 2.0, "float"),
            ParamSpec("bear_threshold", "空头阈值", 1.275, 0.5, 2.0, "float"),
        ),
        warmup=lambda p: int(max(p["ama_window"], p["slow_window"], p["bma_window"]) + 20),
        reference_lines=(("1.0", 1.0),),
    ),
}


# ---------------------------------------------------------------------------
# 市场级：GSISI
# ---------------------------------------------------------------------------

GSISI_DEFINITION = SignalDefinition(
    key="gsisi",
    label="GSISI 投资者情绪指数",
    source="国信《投资者情绪指数择时模型》",
    formula=(
        "每周：申万一级行业周收益 r_i 与其滚动 N 周 β_i（相对代理指数）的截面 Spearman 秩相关；"
        "> 临界值看多、< −临界值看空、中间保持上一状态"
    ),
    principle=(
        "情绪高涨时高 β 行业涨得更多，秩相关为正；恐慌时高 β 行业跌得更多，秩相关为负。"
        "31 个行业时 5% 显著性临界值约 0.301。"
    ),
    required_fields=("close",),
    params=(
        ParamSpec("beta_weeks", "β 滚动周数", 52, 8, 156),
        ParamSpec("threshold", "临界值", 0.301, 0.05, 0.9, "float"),
    ),
    warmup=lambda p: int(p["beta_weeks"] * 5 + 10),
    reference_lines=(("+临界", 0.301), ("0", 0.0), ("−临界", -0.301)),
)


def gsisi_weekly(
    industry_close: pd.DataFrame, proxy_close: pd.Series, beta_weeks: int
) -> pd.DataFrame:
    """计算周频 GSISI。

    Args:
        industry_close: 日期 × 行业代码的收盘价宽表。
        proxy_close: 代理指数收盘价。
        beta_weeks: β 滚动窗口（周，含当周）。

    Returns:
        以每周最后一个交易日为索引的数据帧，列 value（秩相关）与 n_industries。
    """
    if industry_close.empty or proxy_close.empty:
        return pd.DataFrame(columns=["value", "n_industries"], dtype=float)
    ind = industry_close.sort_index()
    ind.index = pd.DatetimeIndex(ind.index)
    prx = proxy_close.sort_index()
    prx.index = pd.DatetimeIndex(prx.index)
    week_key = ind.index.to_period("W-FRI")
    ind_w = ind.groupby(week_key).last()
    week_end = ind.index.to_series().groupby(week_key).max()
    prx_w = prx.groupby(prx.index.to_period("W-FRI")).last().reindex(ind_w.index)
    ind_r = ind_w.pct_change(fill_method=None)
    mkt_r = prx_w.pct_change(fill_method=None)
    cov = ind_r.rolling(beta_weeks).cov(mkt_r)
    beta = cov.div(mkt_r.rolling(beta_weeks).var(), axis=0)
    valid = ind_r.notna() & beta.notna()
    rank_r = ind_r.where(valid).rank(axis=1)
    rank_b = beta.where(valid).rank(axis=1)
    value = rank_r.corrwith(rank_b, axis=1)
    count = valid.sum(axis=1)
    value = value.where(count >= MIN_CROSS_SECTION)
    out = pd.DataFrame({"value": value, "n_industries": count.astype(float)})
    out.index = pd.DatetimeIndex(week_end.reindex(out.index).to_numpy())
    return out


def hysteresis_state(value: pd.Series, upper: float, lower: float) -> pd.Series:
    """阈值滞回状态：> upper 置 1，< lower 置 0，中间保持上一状态（初始 NaN）。"""
    state = pd.Series(np.nan, index=value.index)
    state[value > upper] = 1.0
    state[value < lower] = 0.0
    return state.ffill()


# ---------------------------------------------------------------------------
# 检验统计
# ---------------------------------------------------------------------------


def cross_section_rank_corr(
    a: pd.DataFrame, b: pd.DataFrame, min_n: int = MIN_CROSS_SECTION
) -> pd.Series:
    """逐日截面 Spearman 相关（共同非空样本 < min_n 时为 NaN）。"""
    mask = a.notna() & b.notna()
    ra = a.where(mask).rank(axis=1)
    rb = b.where(mask).rank(axis=1)
    ra = ra.sub(ra.mean(axis=1), axis=0)
    rb = rb.sub(rb.mean(axis=1), axis=0)
    den = np.sqrt((ra**2).sum(axis=1) * (rb**2).sum(axis=1))
    out = (ra * rb).sum(axis=1) / den.replace(0, np.nan)
    return out.where(mask.sum(axis=1) >= min_n)


def tercile_mean_returns(factor: pd.DataFrame, forward: pd.DataFrame) -> list[float | None]:
    """三分组平均未来收益（G1 最低分组 … G3 最高分组），按调仓日等权平均。"""
    mask = factor.notna() & forward.notna()
    enough = mask.sum(axis=1) >= MIN_CROSS_SECTION
    mask = mask & enough.to_numpy()[:, None]
    pct = factor.where(mask).rank(axis=1, pct=True)
    result: list[float | None] = []
    for lo, hi in ((0.0, 1 / 3), (1 / 3, 2 / 3), (2 / 3, 1.0001)):
        sel = (pct > lo) & (pct <= hi)
        mean = forward.where(sel).mean(axis=1).mean()
        result.append(None if pd.isna(mean) else float(mean))
    return result


def week_end_dates(calendar: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """取日历中每周最后一个交易日。"""
    if len(calendar) == 0:
        return calendar
    series = calendar.to_series()
    return pd.DatetimeIndex(series.groupby(calendar.to_period("W-FRI")).max().to_numpy())


def time_series_spearman(a: pd.Series, b: pd.Series, min_n: int = 30) -> float | None:
    """时间序列 Spearman 相关（共同非空样本不足时返回 None）。"""
    joined = pd.concat([a, b], axis=1).dropna()
    if len(joined) < min_n:
        return None
    value = joined.iloc[:, 0].rank().corr(joined.iloc[:, 1].rank())
    return None if pd.isna(value) else float(value)


def performance_summary(returns: pd.Series) -> dict[str, float | None]:
    """日收益序列的累计收益、年化收益、最大回撤与夏普。"""
    returns = returns.dropna()
    if returns.empty:
        return {"total": None, "annualized": None, "max_drawdown": None, "sharpe": None}
    nav = (1 + returns).cumprod()
    std = float(returns.std())
    return {
        "total": float(nav.iloc[-1] - 1),
        "annualized": float(nav.iloc[-1] ** (252 / len(returns)) - 1),
        "max_drawdown": float((nav / nav.cummax() - 1).min()),
        "sharpe": float(returns.mean() / std * math.sqrt(252)) if std > 0 else None,
    }


def position_returns(on: pd.Series, close: pd.Series) -> tuple[pd.Series, pd.Series]:
    """按 t+1 收盘成交口径计算择时持仓收益与基准收益。

    t 日收盘出信号、t+1 收盘成交，因此 t 日收益乘以 on_{t−2}；空仓收益为 0，未计成本。

    Args:
        on: 0/1 持仓状态（与 close 同索引）。
        close: 收盘价。

    Returns:
        (择时日收益, 基准日收益)。
    """
    ret = close.pct_change(fill_method=None)
    return on.shift(2).fillna(0.0) * ret, ret
