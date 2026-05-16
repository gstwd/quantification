def volume_probability(volume_ratio: float) -> float:
    # 分段线性映射：量比越高，量能概率越大
    # 量比 < 0.5 属于极度缩量，概率接近 0
    if volume_ratio < 0.5:
        return max(0.0, volume_ratio / 0.5 * 5)
    # 量比 0.5-1.0 为温和缩量，概率 5-17
    if volume_ratio < 1.0:
        return 5 + (volume_ratio - 0.5) / 0.5 * 12
    # 量比 1.0-1.3 为温和放量，概率 17-35
    if volume_ratio < 1.3:
        return 17 + (volume_ratio - 1.0) / 0.3 * 18
    # 量比 1.3-1.5 为明显放量，概率 35-55
    if volume_ratio < 1.5:
        return 35 + (volume_ratio - 1.3) / 0.2 * 20
    # 量比 1.5-2.0 为显著放量，概率 55-80
    if volume_ratio < 2.0:
        return 55 + (volume_ratio - 1.5) / 0.5 * 25
    # 量比 2.0-3.0 为大幅放量，概率 80-95
    if volume_ratio < 3.0:
        return 80 + (volume_ratio - 2.0) / 1.0 * 15
    # 量比 3.0-5.0 为极端放量，概率 95-98
    if volume_ratio < 5.0:
        return 95 + (volume_ratio - 3.0) / 2.0 * 3
    # 量比 > 5.0 封顶 100
    return min(100.0, 98 + (volume_ratio - 5.0) / 5.0 * 2)


def direction_probability(
    change_pct: float, etf_5d: float, index_5d: float, volume_ratio: float, index_change_pct: float
) -> float:
    # 大盘当日强势上涨时，ETF 跟涨可信度下降（追涨风险高）
    rally_discount = 1.0
    if index_change_pct > 2.0:
        rally_discount = 0.60  # 大盘涨超 2%，折扣至 60%
    elif index_change_pct > 1.5:
        rally_discount = 0.70
    elif index_change_pct > 1.0:
        rally_discount = 0.80
    elif index_change_pct > 0.5:
        rally_discount = 0.90

    # f1：ETF 当日涨跌 vs 大盘近 5 日走势（逆势上涨得分高）
    if change_pct > 0.3 and index_5d < -1:
        f1 = 95  # 大盘弱势中 ETF 明显上涨，强势信号
    elif change_pct > 0 and index_5d < -0.5:
        f1 = 85
    elif change_pct > 0 and index_5d < 0:
        f1 = 70
    elif abs(change_pct) < 0.15 and index_5d < -1:
        f1 = 80  # 大盘弱势中 ETF 横盘，相对抗跌
    elif abs(change_pct) < 0.3 and index_5d < -0.5:
        f1 = 65
    elif change_pct > 1 and volume_ratio > 1.5 and index_change_pct > 1:
        f1 = 25  # 大盘强势中 ETF 大涨放量，追涨风险高
    elif change_pct > 1 and volume_ratio > 1.5:
        f1 = 45
    elif change_pct > 0.5 and volume_ratio > 1.3 and index_change_pct > 1:
        f1 = 35
    elif change_pct > 0.5 and volume_ratio > 1.3:
        f1 = 50
    elif change_pct > 0:
        f1 = 40
    elif change_pct < -1.5 and volume_ratio > 2:
        f1 = 8  # 大幅放量下跌，恐慌抛售信号
    elif change_pct < -0.5 and volume_ratio > 1.5:
        f1 = 15
    else:
        f1 = 25

    # f2：ETF 5 日收益 vs 大盘 5 日收益的超额收益（正超额得分高）
    gap = etf_5d - index_5d
    if gap > 3:
        f2 = 95
    elif gap > 2:
        f2 = 85
    elif gap > 1.2:
        f2 = 75
    elif gap > 0.6:
        f2 = 60
    elif gap > 0.2:
        f2 = 50
    elif gap > -0.2:
        f2 = 40
    elif gap > -0.6:
        f2 = 30
    else:
        f2 = 15

    # f3：大盘近 5 日走势（大盘越弱，ETF 抗跌性越值得关注）
    if index_5d < -4:
        f3 = 95
    elif index_5d < -3:
        f3 = 90
    elif index_5d < -2:
        f3 = 80
    elif index_5d < -1:
        f3 = 70
    elif index_5d < -0.5:
        f3 = 55
    elif index_5d < 0:
        f3 = 45
    elif index_5d < 1:
        f3 = 35
    elif index_5d < 3:
        f3 = 20
    else:
        f3 = 10

    # 三子因子加权合成：f1 占 40%，f2 占 30%，f3 占 20%，基础分 35 占 10%
    raw = f1 * 0.4 + f2 * 0.3 + f3 * 0.2 + 35 * 0.1
    return round(raw * rally_discount, 1)


def share_probability(share_delta_pct: float | None) -> float | None:
    if share_delta_pct is None:
        return None
    # 份额变化率 > 10% 为极端净申购，概率封顶 95
    if share_delta_pct > 10:
        return 95.0
    # 分段线性映射：净申购（正值）得分高，净赎回（负值）得分低
    if share_delta_pct > 5:
        return 80 + (share_delta_pct - 5) / 5 * 15
    if share_delta_pct > 3:
        return 65 + (share_delta_pct - 3) / 2 * 15
    if share_delta_pct > 1:
        return 45 + (share_delta_pct - 1) / 2 * 20
    if share_delta_pct > 0:
        return 30 + share_delta_pct * 15
    if share_delta_pct > -1:
        return 15 + (share_delta_pct + 1) * 15
    if share_delta_pct > -5:
        return 5 + (share_delta_pct + 5) / 4 * 10
    # 份额大幅净赎回，概率趋近 0
    return max(0.0, 5 + (share_delta_pct + 5) / 5 * 5)


def composite_probability(
    volume_prob: float, direction_prob: float, share_prob: float | None
) -> float:
    # 无份额数据时退化为双因子：量能 70% + 方向 30%
    if share_prob is None:
        return round(volume_prob * 0.7 + direction_prob * 0.3, 1)
    # 三因子加权：量能 50% + 份额 30% + 方向 20%
    return round(volume_prob * 0.5 + direction_prob * 0.2 + share_prob * 0.3, 1)


def signal_level(score: float) -> tuple[str, str]:
    # 高确信：综合得分 ≥ 70，值得重点关注
    if score >= 70:
        return "HIGH", "高确信"
    # 中等关注：综合得分 50-69，可跟踪观察
    if score >= 50:
        return "MID", "中等关注"
    # 正常：综合得分 < 50，无明显信号
    return "LOW", "正常"
