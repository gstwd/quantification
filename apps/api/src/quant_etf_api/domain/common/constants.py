"""领域层常量定义。

策略引擎、回测、信号判定等模块共用的阈值和标签常量，
避免硬编码散落在多处导致的维护不一致。
"""

from __future__ import annotations

# 信号等级判定阈值（百分制得分）
SIGNAL_THRESHOLD_HIGH = 70
SIGNAL_THRESHOLD_MID = 50

# 信号等级中文标签
SIGNAL_LABELS: dict[str, str] = {
    "HIGH": "推荐配置",
    "MID": "可选配置",
    "LOW": "暂不配置",
}
