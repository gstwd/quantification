"""领域层常量定义。

策略引擎、回测、信号判定等模块共用的阈值和标签常量，
避免硬编码散落在多处导致的维护不一致。
"""

from __future__ import annotations

# 信号等级判定阈值（百分制得分，absolute 模式使用）
SIGNAL_THRESHOLD_HIGH = 70
SIGNAL_THRESHOLD_MID = 50

# Z-Score 模式下的信号等级阈值
# zscore 将 raw_score 标准化为均值 50、标准差 10 的分布，
# 降低阈值以适配低尾部概率（z=1 约对应 top 16%，z=0 约对应 top 50%）
SIGNAL_THRESHOLD_HIGH_ZSCORE = 60
SIGNAL_THRESHOLD_MID_ZSCORE = 50

# 信号等级中文标签
SIGNAL_LABELS: dict[str, str] = {
    "HIGH": "推荐配置",
    "MID": "可选配置",
    "LOW": "暂不配置",
}
