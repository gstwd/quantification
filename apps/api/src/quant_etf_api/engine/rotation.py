"""行业轮动选择引擎（申万一级行业域专用，可选模块）。

仅在 StrategyConfig.rotation 非空时启用；语义对齐研报三类信号：
- quadrant：保留象限内行业，超 top_n 按到 (100,100) 距离取最远 top_n；
- diffusion：扩散指标 top_n；
- diffusion_rrg：扩散 top_n 后剔除非保留象限行业，不补足。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from quant_etf_api.engine.config import RotationConfig

logger = logging.getLogger(__name__)


@dataclass
class IndustryRotationInput:
    """单日行业轮动输入（由行业因子服务构建后注入引擎上下文）。"""

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
        config: RotationConfig,
        data: IndustryRotationInput,
    ) -> tuple[list[str], dict[str, float]]:
        """执行单日行业选择，返回 (选中行业代码, 等权权重)。

        Args:
            config: rotation 配置。
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
            raise ValueError(f"未知 rotation.signal: {config.signal}")
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
        config: RotationConfig,
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
        config: RotationConfig,
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
        config: RotationConfig,
        data: IndustryRotationInput,
        codes: list[str],
    ) -> list[str]:
        """信号 C：扩散 top_n 先选，再剔除三四象限，不补足。"""
        top = IndustryRotationEngine._select_diffusion(config, data, codes)
        keep = set(config.keep_quadrants)
        return [code for code in top if data.quadrant.get(code) in keep]
