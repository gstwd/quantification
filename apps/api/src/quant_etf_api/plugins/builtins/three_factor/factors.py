def volume_probability(volume_ratio: float) -> float:
    if volume_ratio < 0.5:
        return max(0.0, volume_ratio / 0.5 * 5)
    if volume_ratio < 1.0:
        return 5 + (volume_ratio - 0.5) / 0.5 * 12
    if volume_ratio < 1.3:
        return 17 + (volume_ratio - 1.0) / 0.3 * 18
    if volume_ratio < 1.5:
        return 35 + (volume_ratio - 1.3) / 0.2 * 20
    if volume_ratio < 2.0:
        return 55 + (volume_ratio - 1.5) / 0.5 * 25
    if volume_ratio < 3.0:
        return 80 + (volume_ratio - 2.0) / 1.0 * 15
    if volume_ratio < 5.0:
        return 95 + (volume_ratio - 3.0) / 2.0 * 3
    return min(100.0, 98 + (volume_ratio - 5.0) / 5.0 * 2)


def direction_probability(change_pct: float, etf_5d: float, index_5d: float, volume_ratio: float, index_change_pct: float) -> float:
    rally_discount = 1.0
    if index_change_pct > 2.0:
        rally_discount = 0.60
    elif index_change_pct > 1.5:
        rally_discount = 0.70
    elif index_change_pct > 1.0:
        rally_discount = 0.80
    elif index_change_pct > 0.5:
        rally_discount = 0.90

    if change_pct > 0.3 and index_5d < -1:
        f1 = 95
    elif change_pct > 0 and index_5d < -0.5:
        f1 = 85
    elif change_pct > 0 and index_5d < 0:
        f1 = 70
    elif abs(change_pct) < 0.15 and index_5d < -1:
        f1 = 80
    elif abs(change_pct) < 0.3 and index_5d < -0.5:
        f1 = 65
    elif change_pct > 1 and volume_ratio > 1.5 and index_change_pct > 1:
        f1 = 25
    elif change_pct > 1 and volume_ratio > 1.5:
        f1 = 45
    elif change_pct > 0.5 and volume_ratio > 1.3 and index_change_pct > 1:
        f1 = 35
    elif change_pct > 0.5 and volume_ratio > 1.3:
        f1 = 50
    elif change_pct > 0:
        f1 = 40
    elif change_pct < -1.5 and volume_ratio > 2:
        f1 = 8
    elif change_pct < -0.5 and volume_ratio > 1.5:
        f1 = 15
    else:
        f1 = 25

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

    raw = f1 * 0.4 + f2 * 0.3 + f3 * 0.2 + 35 * 0.1
    return round(raw * rally_discount, 1)


def share_probability(share_delta_pct: float | None) -> float | None:
    if share_delta_pct is None:
        return None
    if share_delta_pct > 10:
        return 95.0
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
    return max(0.0, 5 + (share_delta_pct + 5) / 5 * 5)


def composite_probability(volume_prob: float, direction_prob: float, share_prob: float | None) -> float:
    if share_prob is None:
        return round(volume_prob * 0.7 + direction_prob * 0.3, 1)
    return round(volume_prob * 0.5 + direction_prob * 0.2 + share_prob * 0.3, 1)


def signal_level(score: float) -> tuple[str, str]:
    if score >= 70:
        return "HIGH", "高确信"
    if score >= 50:
        return "MID", "中等关注"
    return "LOW", "正常"
