"""策略领域模型（纯领域逻辑，无外部依赖）。

定义策略执行的核心数据结构：
- StrategyContextData：策略执行上下文，包含基准指数、份额变化等数据
- StrategyResult：单只 ETF 的策略执行结果，包含信号得分、因子值等
- TimingSignal：市场择时信号，输出 regime + confidence
- AssetRanking：资产轮动排名项，含动量/估值排名
- AllocationPlan：仓位分配方案，含目标仓位和现金比例
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class StrategyContextData:
    """策略执行上下文，由服务层构建后传入插件。

    Attributes:
        benchmark_changes: 基准指数当日涨跌幅，key=指数代码，value=涨跌幅（%）。
        share_changes: ETF 份额变化数据，key=ETF 代码，value=包含 share_delta_pct 等字段的字典。
        extra: 扩展字段，各插件可自定义存放额外上下文数据。
    """

    benchmark_changes: dict[str, float] = field(default_factory=dict)
    share_changes: dict[str, dict[str, float | None]] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyResult:
    """单只 ETF 的策略执行结果。

    Attributes:
        trade_date: 交易日。
        etf_code: ETF 代码。
        strategy_id: 策略标识。
        signal_score: 综合得分，0-100。
        signal_level: 信号等级：HIGH / MID / LOW。
        signal_label: 信号中文标签：高确信 / 中等关注 / 正常。
        factor_values: 各因子计算结果列表。
        payload: 计算中间数据，用于解释和调试。
        tags: 标签，如跟踪指数名称。
    """

    trade_date: date
    etf_code: str
    strategy_id: str
    signal_score: float
    signal_level: str
    signal_label: str
    factor_values: list[dict[str, Any]] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


@dataclass
class TimingSignal:
    """市场择时信号，由策略插件的 assess_market_timing() 方法输出。

    综合估值、趋势、量能等指标判断当前市场环境，
    输出三种 regime：offensive（进攻）、defensive（防守）、neutral（观望）。

    Attributes:
        regime: 市场状态枚举，offensive / defensive / neutral。
        confidence: 信号确信度，0-100，越高表示判断越确定。
        label: 中文标签，进攻 / 防守 / 观望。
        factors: 驱动因素详情，用于解释和调试。
    """

    regime: str
    confidence: float
    label: str
    factors: dict[str, Any] = field(default_factory=dict)


@dataclass
class AssetRanking:
    """资产轮动排名项，由策略插件的 rank_assets() 方法输出。

    每只 ETF 的综合排名信息，包含动量排名和估值排名。

    Attributes:
        etf_code: ETF 代码。
        name_cn: ETF 中文名称。
        category: 板块分类，如 宽基 / 科技 / 消费 / 医药 / 金融。
        score: 综合排名分，越高越值得配置。
        momentum_rank: 动量排名（1=最强）。
        valuation_rank: 估值排名（1=最便宜）。
        details: 评分细节，用于解释和调试。
    """

    etf_code: str
    name_cn: str
    category: str
    score: float
    momentum_rank: int = 0
    valuation_rank: int = 0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class AllocationPlan:
    """仓位分配方案，由策略插件的 allocate_positions() 方法输出。

    根据择时信号和资产排名，确定每只 ETF 的目标仓位比例。

    Attributes:
        positions: 每只 ETF 的目标仓位比例，key=ETF 代码，value=0~1。
        total_exposure: 总仓位比例，0~1，等于 positions 之和。
        cash_ratio: 现金比例，1 - total_exposure。
        reasoning: 分配理由说明。
    """

    positions: dict[str, float] = field(default_factory=dict)
    total_exposure: float = 0.0
    cash_ratio: float = 1.0
    reasoning: str = ""
