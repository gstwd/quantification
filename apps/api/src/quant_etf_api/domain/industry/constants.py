"""申万一级行业相关常量（2021 版分类体系）。"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

# 申万一级行业指数代码 → 中文名称（与 AkShare sw_index_first_info / 申万官网一致）
SW_L1_NAMES: dict[str, str] = {
    "801010": "农林牧渔",
    "801030": "基础化工",
    "801040": "钢铁",
    "801050": "有色金属",
    "801080": "电子",
    "801110": "家用电器",
    "801120": "食品饮料",
    "801130": "纺织服饰",
    "801140": "轻工制造",
    "801150": "医药生物",
    "801160": "公用事业",
    "801170": "交通运输",
    "801180": "房地产",
    "801200": "商贸零售",
    "801210": "社会服务",
    "801230": "综合",
    "801710": "建筑材料",
    "801720": "建筑装饰",
    "801730": "电力设备",
    "801740": "国防军工",
    "801750": "计算机",
    "801760": "传媒",
    "801770": "通信",
    "801780": "银行",
    "801790": "非银金融",
    "801880": "汽车",
    "801890": "机械设备",
    "801950": "煤炭",
    "801960": "石油石化",
    "801970": "环保",
    "801980": "美容护理",
}

# 排序后的全部申万一级行业代码
SW_L1_INDUSTRIES: list[str] = sorted(SW_L1_NAMES.keys())

# 研报口径：申万体系仅剔除"综合"，中信体系额外剔除"综合金融"
SW_EXCLUDED_INDUSTRY_CODES: frozenset[str] = frozenset({"801230"})

# 申万 2021 版行业分类 6 位代码前两位 → 一级行业指数代码。
# 该映射由 31 个申万一级行业指数官方成分与申万分类历史文件交叉推导，无冲突。
SW_CLASSIFICATION_PREFIX_TO_L1: dict[str, str] = {
    "11": "801010",  # 农林牧渔
    "22": "801030",  # 基础化工
    "23": "801040",  # 钢铁
    "24": "801050",  # 有色金属
    "27": "801080",  # 电子
    "28": "801880",  # 汽车
    "33": "801110",  # 家用电器
    "34": "801120",  # 食品饮料
    "35": "801130",  # 纺织服饰
    "36": "801140",  # 轻工制造
    "37": "801150",  # 医药生物
    "41": "801160",  # 公用事业
    "42": "801170",  # 交通运输
    "43": "801180",  # 房地产
    "45": "801200",  # 商贸零售
    "46": "801210",  # 社会服务
    "48": "801780",  # 银行
    "49": "801790",  # 非银金融
    "51": "801230",  # 综合
    "61": "801710",  # 建筑材料
    "62": "801720",  # 建筑装饰
    "63": "801730",  # 电力设备
    "64": "801890",  # 机械设备
    "65": "801740",  # 国防军工
    "71": "801750",  # 计算机
    "72": "801760",  # 传媒
    "73": "801770",  # 通信
    "74": "801950",  # 煤炭
    "75": "801960",  # 石油石化
    "76": "801970",  # 环保
    "77": "801980",  # 美容护理
}

# 申万分类体系切换日：该日之前的行业分类编码体系与现行不同，不做映射
SW_CLASSIFICATION_EPOCH: date = date(2014, 2, 21)

# 申万官网分类历史文件的"回写日"过滤下界：仅采纳 2021 版体系回写后的成分事件，
# 保证 6 位分类码可按现行一级前缀映射（退市且未回写的早期股票不计入历史成分）。
SW_CLASSIFICATION_REMAP_SINCE: date = date(2021, 7, 1)

# 行业域因子 ID 白名单
INDUSTRY_FACTOR_IDS: frozenset[str] = frozenset(
    {
        "rrg_rs_ratio",
        "rrg_rs_momentum",
        "rrg_quadrant",
        "diffusion_count_ratio",
    }
)

# 调试研究接口单次允许的最大自然日跨度：超长区间会放大响应体与逐点展开成本。
# RRG 需要更久历史展示轨迹，扩散受个股逐行业加载成本约束，上限略低。
RRG_LAB_MAX_RANGE_DAYS: int = 3700
DIFFUSION_LAB_MAX_RANGE_DAYS: int = 2200

# 行业因子默认参数（研报复刻口径）：RS-Ratio 回看/RS-Momentum 回看/
# 平滑窗口/扩散上涨判定回看；benchmark_exclude 默认剔除"综合"。
DEFAULT_LOOKBACK_RATIO = 220
DEFAULT_LOOKBACK_MOM = 60
DEFAULT_SMOOTH_WINDOW = 20
DEFAULT_DIFFUSION_LOOKBACK = 220


def canonical_industry_params(
    *,
    lookback_ratio: int = DEFAULT_LOOKBACK_RATIO,
    lookback_mom: int = DEFAULT_LOOKBACK_MOM,
    smooth_window: int = DEFAULT_SMOOTH_WINDOW,
    diffusion_lookback: int = DEFAULT_DIFFUSION_LOOKBACK,
    benchmark_exclude: list[str] | None = None,
) -> dict[str, Any]:
    """生成行业因子规范化参数字典（含基准剔除清单，排序稳定）。

    Args:
        lookback_ratio: RS-Ratio 比率回看天数。
        lookback_mom: RS-Momentum 比率回看天数。
        smooth_window: MA 平滑窗口。
        diffusion_lookback: 扩散上涨判定回看天数。
        benchmark_exclude: RRG 行业等权基准剔除行业代码列表。

    Returns:
        供行业因子值表 params 落库与指纹计算的规范化字典。
    """
    return {
        "lookback_ratio": int(lookback_ratio),
        "lookback_mom": int(lookback_mom),
        "smooth_window": int(smooth_window),
        "diffusion_lookback": int(diffusion_lookback),
        "benchmark_exclude": sorted(
            benchmark_exclude or list(SW_EXCLUDED_INDUSTRY_CODES)
        ),
    }


def industry_params_hash(
    *,
    lookback_ratio: int = DEFAULT_LOOKBACK_RATIO,
    lookback_mom: int = DEFAULT_LOOKBACK_MOM,
    smooth_window: int = DEFAULT_SMOOTH_WINDOW,
    diffusion_lookback: int = DEFAULT_DIFFUSION_LOOKBACK,
    benchmark_exclude: list[str] | None = None,
) -> str:
    """计算行业因子参数指纹（规范化 JSON 的 sha256）。

    Args:
        lookback_ratio: RS-Ratio 比率回看天数。
        lookback_mom: RS-Momentum 比率回看天数。
        smooth_window: MA 平滑窗口。
        diffusion_lookback: 扩散上涨判定回看天数。
        benchmark_exclude: RRG 行业等权基准剔除行业代码列表。

    Returns:
        64 位十六进制 sha256 哈希。
    """
    params = canonical_industry_params(
        lookback_ratio=lookback_ratio,
        lookback_mom=lookback_mom,
        smooth_window=smooth_window,
        diffusion_lookback=diffusion_lookback,
        benchmark_exclude=benchmark_exclude,
    )
    canonical = json.dumps(
        params,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# 申万一级行业指数在系统内的归一化代码：去掉交易所后缀（如 801010.SI → 801010）
def normalize_sw_code(code: str) -> str:
    """将申万指数代码归一化为纯 6 位代码。

    Args:
        code: 指数代码，如 801010 或 801010.SI。

    Returns:
        纯 6 位代码，如 801010。
    """
    return code.split(".")[0]
