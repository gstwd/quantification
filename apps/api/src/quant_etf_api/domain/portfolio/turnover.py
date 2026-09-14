"""换手率计算（纯领域逻辑）。"""

from __future__ import annotations

#: 历史换手口径：调仓日当 ``prev`` 或 ``new`` 为空时换手记为 0，
#: 因此"清仓型 / 建仓型"调仓腿未被计入成本（C2 记录的低估来源）。
TURNOVER_MODEL_LEGACY = "legacy_v1"

#: 当前换手口径：统一按 ``Σ|Δw| / 2`` 计算，清仓腿（旧仓位 → 空仓）
#: 与建仓腿（空仓 → 新仓位）全额计入。
TURNOVER_MODEL_DELTA_W = "delta_w_v2"


def compute_turnover(
    prev_positions: dict[str, float],
    curr_positions: dict[str, float],
) -> float:
    """计算换手率（两日仓位变动的绝对值之和 / 2）。

    该口径天然覆盖清仓与建仓腿：

    - 建仓（``prev`` 为空）：结果为 ``Σ|新仓位| / 2``（现金买入的换手）；
    - 清仓（``new`` 为空）：结果为 ``Σ|旧仓位| / 2``（持仓卖出的换手）。

    历史上调用方在 ``prev`` 或 ``new`` 为空时跳过本函数并把换手记为 0，
    导致净口径成本被系统性低估（C2）；现在统一按本函数计算。

    Args:
        prev_positions: 前日仓位权重，key=资产代码。
        curr_positions: 当日目标仓位权重，key=资产代码。

    Returns:
        换手率，范围 0-1。
    """
    all_codes = set(prev_positions.keys()) | set(curr_positions.keys())
    turnover = 0.0
    for code in all_codes:
        prev_w = prev_positions.get(code, 0.0)
        curr_w = curr_positions.get(code, 0.0)
        turnover += abs(curr_w - prev_w)
    return turnover / 2.0
