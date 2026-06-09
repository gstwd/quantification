"""统一的信号等级判定逻辑。

所有需要判定信号等级（HIGH/MID/LOW）的模块统一调用此处函数，
避免判定逻辑散落多处导致不一致。
"""

from __future__ import annotations

from quant_etf_api.domain.common.constants import (
    SIGNAL_LABELS,
    SIGNAL_THRESHOLD_HIGH,
    SIGNAL_THRESHOLD_HIGH_ZSCORE,
    SIGNAL_THRESHOLD_MID,
    SIGNAL_THRESHOLD_MID_ZSCORE,
)


def determine_signal_level(
    score: float,
    target_weight: float = 0.0,
    has_positions: bool = False,
    timing_regime: str | None = None,
    scoring_mode: str | None = None,
) -> tuple[str, str]:
    """统一的信号等级判定。

    规则：
    1. 择时 defensive 且为配置模式 → 全部 LOW（防守减仓）。
    2. 配置模式：target_weight > 0 时按得分阈值判定 HIGH/MID，否则 LOW。
    3. 根据 scoring_mode 选择阈值：
       - zscore: 得分已标准化为均值 50 标准差 10 的分布，使用较低阈值
       - absolute/rank: 使用默认阈值 70/50

    Args:
        score: 综合得分（0-100）。
        target_weight: 目标权重（配置模式下使用）。
        has_positions: 是否为配置模式（有 portfolio 配置）。
        timing_regime: 择时 regime，None 表示无择时。
        scoring_mode: 评分模式（absolute/rank/zscore），用于选择阈值。

    Returns:
        (level, label) 元组，如 ("HIGH", "推荐配置")。
    """
    # 根据评分模式选择阈值
    if scoring_mode == "zscore":
        thresh_high = SIGNAL_THRESHOLD_HIGH_ZSCORE
        thresh_mid = SIGNAL_THRESHOLD_MID_ZSCORE
    else:
        thresh_high = SIGNAL_THRESHOLD_HIGH
        thresh_mid = SIGNAL_THRESHOLD_MID

    # 防守 regime 下全部降为 LOW
    if has_positions and timing_regime == "defensive":
        return "LOW", "防守减仓"

    # 配置模式：有仓位时按阈值判定，无仓位为 LOW
    if has_positions:
        if target_weight > 0:
            level = "HIGH" if score >= thresh_high else "MID"
            return level, SIGNAL_LABELS[level]
        return "LOW", SIGNAL_LABELS["LOW"]

    # 信号模式：按得分阈值判定
    if score > 0:
        if score >= thresh_high:
            return "HIGH", SIGNAL_LABELS["HIGH"]
        if score >= thresh_mid:
            return "MID", SIGNAL_LABELS["MID"]
    return "LOW", SIGNAL_LABELS["LOW"]
