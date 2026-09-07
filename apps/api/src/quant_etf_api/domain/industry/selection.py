"""申万行业轮动纯选择逻辑（研究工作台与指数级因子共用的领域层）。

研报三类信号的选择规则只依赖行业因子面板输入，不依赖策略配置，
因此从策略引擎中下沉到领域层：策略引擎不再消费行业轮动选择，
研究工作台（/industry/rotation）与指数级 RRG 因子内部复用本模块。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

logger = logging.getLogger(__name__)


@dataclass
class IndustrySelectionConfig:
    """行业轮动选择参数（研报三类信号，与旧 RotationConfig 语义一致）。

    Attributes:
        signal: 信号类型：quadrant=纯 RRG 象限、diffusion=纯扩散 top_n、
            diffusion_rrg=扩散 top_n 后剔除三四象限。
        top_n: 每日最多选中行业数。
        keep_quadrants: 保留象限集合（1=领先/2=改善/3=滞后/4=疲软）。
        benchmark_exclude: 从 RRG 行业等权基准中剔除的行业代码。
        lookback_ratio: RS-Ratio 比率回看天数。
        lookback_mom: RS-Momentum 比率回看天数。
        smooth_window: MA 平滑窗口。
        diffusion_lookback: 扩散指标上涨判定回看天数。
    """

    signal: str = "diffusion_rrg"
    top_n: int = 6
    keep_quadrants: list[int] = field(default_factory=lambda: [1, 2])
    benchmark_exclude: list[str] = field(default_factory=list)
    lookback_ratio: int = 220
    lookback_mom: int = 60
    smooth_window: int = 20
    diffusion_lookback: int = 220


@dataclass
class IndustryRotationInput:
    """单日行业轮动输入（由行业因子面板构建后供选择引擎消费）。"""

    trade_date: date
    industry_codes: list[str] = field(default_factory=list)
    rs_ratio: dict[str, float | None] = field(default_factory=dict)
    rs_momentum: dict[str, float | None] = field(default_factory=dict)
    quadrant: dict[str, int | None] = field(default_factory=dict)
    diffusion: dict[str, float | None] = field(default_factory=dict)


class IndustryRotationEngine:
    """按研报信号规则从行业因子面板中选出目标行业并等权分配。"""

    def select(
        self,
        config: IndustrySelectionConfig,
        data: IndustryRotationInput,
    ) -> tuple[list[str], dict[str, float]]:
        """执行单日行业选择，返回 (选中行业代码, 等权权重)。

        Args:
            config: 行业选择参数（signal/top_n/keep_quadrants 等）。
            data: 当日各行业因子值输入。

        Returns:
            (selected_codes, weights) 元组；无有效选中时为空列表与空 dict。
        """
        codes = data.industry_codes
        if config.signal == "quadrant":
            selected = self._select_quadrant(config, data, codes)
        elif config.signal == "diffusion":
            selected = self._select_diffusion(config, data, codes)
        elif config.signal == "diffusion_rrg":
            selected = self._select_diffusion_with_rrg(config, data, codes)
        else:
            raise ValueError(f"未知 signal: {config.signal}")
        weights: dict[str, float] = {}
        if selected:
            weight = round(1.0 / len(selected), 6)
            weights = {code: weight for code in selected}
        logger.info(
            "[rotation] %s signal=%s 选中 %s 个行业 %s",
            data.trade_date,
            config.signal,
            len(selected),
            selected,
        )
        return selected, weights

    @staticmethod
    def _select_quadrant(
        config: IndustrySelectionConfig,
        data: IndustryRotationInput,
        codes: list[str],
    ) -> list[str]:
        """信号 A：象限过滤 + 距中心距离取 top_n。"""
        keep = set(config.keep_quadrants)
        scored: list[tuple[float, str]] = []
        for code in codes:
            quad = data.quadrant.get(code)
            ratio = data.rs_ratio.get(code)
            momentum = data.rs_momentum.get(code)
            if quad not in keep or ratio is None or momentum is None:
                continue
            distance = ((ratio - 100.0) ** 2 + (momentum - 100.0) ** 2) ** 0.5
            scored.append((distance, code))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [code for _, code in scored[: config.top_n]]

    @staticmethod
    def _select_diffusion(
        config: IndustrySelectionConfig,
        data: IndustryRotationInput,
        codes: list[str],
    ) -> list[str]:
        """信号 B：扩散指标 top_n（warm-up/缺失行业不参与）。"""
        scored = [
            (value, code) for code in codes if (value := data.diffusion.get(code)) is not None
        ]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [code for _, code in scored[: config.top_n]]

    @staticmethod
    def _select_diffusion_with_rrg(
        config: IndustrySelectionConfig,
        data: IndustryRotationInput,
        codes: list[str],
    ) -> list[str]:
        """信号 C：扩散 top_n 先选，再剔除三四象限，不补足。"""
        top = IndustryRotationEngine._select_diffusion(config, data, codes)
        keep = set(config.keep_quadrants)
        return [code for code in top if data.quadrant.get(code) in keep]
