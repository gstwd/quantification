"""因子评估模块：IC/IR 分析与因子相关性矩阵。

提供因子效力评估的核心计算逻辑：
- Rank IC：因子值与下期收益的 Spearman 秩相关系数
- IC 时间序列与汇总统计（IC 均值、IC 标准差、IC_IR、IC>0 占比）
- 因子间截面 Rank 相关矩阵
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from scipy.stats import spearmanr
from sqlalchemy.orm import Session

from quant_etf_api.infra.db.models.core import (
    EtfDailyBarModel,
    EtfFactorValueModel,
)

logger = logging.getLogger(__name__)


def calc_rank_ic(
    db: Session,
    factor_id: str,
    trade_date: date,
    forward_days: int = 1,
) -> float | None:
    """计算单日 Rank IC（因子值与下期收益的 Spearman 秩相关系数）。

    流程：
    1. 取 trade_date 当日所有 ETF 的因子值
    2. 取 trade_date + forward_days 个交易日后的收盘价（或最近可用日期）
    3. 计算 forward_days 收益率
    4. 计算因子值与收益率的 Spearman 相关系数

    Args:
        db: SQLAlchemy 同步 Session。
        factor_id: 因子标识。
        trade_date: 因子值对应的交易日。
        forward_days: 前瞻天数，用于计算下期收益率。

    Returns:
        Rank IC 值（-1 到 1），数据不足时返回 None。
    """
    # 获取当日因子值
    factor_rows = (
        db.query(EtfFactorValueModel.etf_code, EtfFactorValueModel.factor_value_numeric)
        .filter(
            EtfFactorValueModel.factor_id == factor_id,
            EtfFactorValueModel.trade_date == trade_date,
            EtfFactorValueModel.strategy_id.is_(None),
            EtfFactorValueModel.factor_value_numeric.isnot(None),
        )
        .all()
    )
    if len(factor_rows) < 3:
        return None

    factor_dict = {r[0]: r[1] for r in factor_rows}
    etf_codes = list(factor_dict.keys())

    # 获取 trade_date 当日收盘价
    current_bars = (
        db.query(EtfDailyBarModel.etf_code, EtfDailyBarModel.close_price)
        .filter(
            EtfDailyBarModel.trade_date == trade_date,
            EtfDailyBarModel.etf_code.in_(etf_codes),
            EtfDailyBarModel.close_price.isnot(None),
        )
        .all()
    )
    current_dict = {r[0]: r[1] for r in current_bars}

    # 获取 forward 日期后的收盘价（取 trade_date 之后第 forward_days 个交易日）
    forward_bars = (
        db.query(EtfDailyBarModel.etf_code, EtfDailyBarModel.close_price)
        .filter(
            EtfDailyBarModel.trade_date > trade_date,
            EtfDailyBarModel.etf_code.in_(etf_codes),
            EtfDailyBarModel.close_price.isnot(None),
        )
        .order_by(EtfDailyBarModel.trade_date.asc())
        .all()
    )
    # 按 ETF 分组，取每个 ETF 的第 forward_days 条记录
    forward_dict: dict[str, float] = {}
    etf_count: dict[str, int] = {}
    for code, price in forward_bars:
        etf_count[code] = etf_count.get(code, 0) + 1
        if etf_count[code] == forward_days:
            forward_dict[code] = price

    # 构建配对序列
    factor_vals = []
    returns = []
    for code in etf_codes:
        if code in current_dict and code in forward_dict and current_dict[code] > 0:
            ret = (forward_dict[code] / current_dict[code] - 1) * 100
            factor_vals.append(factor_dict[code])
            returns.append(ret)

    if len(factor_vals) < 3:
        return None

    ic, _ = spearmanr(factor_vals, returns)
    return round(float(ic), 4) if ic == ic else None  # 排除 NaN


def calc_ic_series(
    db: Session,
    factor_id: str,
    start_date: date,
    end_date: date,
    forward_days: int = 1,
) -> list[dict[str, Any]]:
    """计算因子在指定时间范围内的 IC 时间序列。

    Args:
        db: SQLAlchemy 同步 Session。
        factor_id: 因子标识。
        start_date: 起始日期（含）。
        end_date: 截止日期（含）。
        forward_days: 前瞻天数。

    Returns:
        按日期升序排列的 IC 序列，每项包含 trade_date 和 ic。
    """
    # 获取有因子值的日期列表
    dates = (
        db.query(EtfFactorValueModel.trade_date)
        .filter(
            EtfFactorValueModel.factor_id == factor_id,
            EtfFactorValueModel.trade_date >= start_date,
            EtfFactorValueModel.trade_date <= end_date,
            EtfFactorValueModel.strategy_id.is_(None),
        )
        .distinct()
        .order_by(EtfFactorValueModel.trade_date.asc())
        .all()
    )

    result = []
    for (d,) in dates:
        ic = calc_rank_ic(db, factor_id, d, forward_days)
        if ic is not None:
            result.append({"trade_date": str(d), "ic": ic})

    return result


def calc_ic_summary(
    db: Session,
    factor_id: str,
    start_date: date,
    end_date: date,
    forward_days: int = 1,
) -> dict[str, Any]:
    """汇总因子 IC 统计信息。

    Args:
        db: SQLAlchemy 同步 Session。
        factor_id: 因子标识。
        start_date: 起始日期（含）。
        end_date: 截止日期（含）。
        forward_days: 前瞻天数。

    Returns:
        包含 ic_mean / ic_std / ic_ir / ic_positive_ratio / count 的字典。
    """
    series = calc_ic_series(db, factor_id, start_date, end_date, forward_days)
    if not series:
        return {
            "ic_mean": None,
            "ic_std": None,
            "ic_ir": None,
            "ic_positive_ratio": None,
            "count": 0,
        }

    ic_values = [s["ic"] for s in series]
    n = len(ic_values)
    mean = sum(ic_values) / n
    variance = sum((x - mean) ** 2 for x in ic_values) / (n - 1) if n > 1 else 0.0
    std = variance**0.5
    ic_ir = round(mean / std, 4) if std > 0 else None
    positive_count = sum(1 for x in ic_values if x > 0)

    return {
        "ic_mean": round(mean, 4),
        "ic_std": round(std, 4),
        "ic_ir": ic_ir,
        "ic_positive_ratio": round(positive_count / n, 4),
        "count": n,
    }


def calc_factor_correlation_matrix(
    db: Session,
    trade_date: date,
    factor_ids: list[str] | None = None,
) -> dict[str, Any]:
    """计算因子间截面 Rank 相关矩阵。

    对指定日期的所有 ETF，计算各因子值之间的 Spearman 相关系数。

    Args:
        db: SQLAlchemy 同步 Session。
        trade_date: 交易日。
        factor_ids: 要计算的因子列表，None 表示所有有数据的因子。

    Returns:
        包含 factor_ids 和 matrix（二维列表）的字典。
    """
    # 查询当日所有因子值
    query = (
        db.query(
            EtfFactorValueModel.etf_code,
            EtfFactorValueModel.factor_id,
            EtfFactorValueModel.factor_value_numeric,
        )
        .filter(
            EtfFactorValueModel.trade_date == trade_date,
            EtfFactorValueModel.strategy_id.is_(None),
            EtfFactorValueModel.factor_value_numeric.isnot(None),
        )
    )
    if factor_ids:
        query = query.filter(EtfFactorValueModel.factor_id.in_(factor_ids))

    rows = query.all()

    # 按 (etf_code, factor_id) 组织数据
    data: dict[str, dict[str, float]] = {}
    all_factor_ids: set[str] = set()
    for etf_code, fid, value in rows:
        all_factor_ids.add(fid)
        if etf_code not in data:
            data[etf_code] = {}
        data[etf_code][fid] = value

    sorted_factor_ids = sorted(all_factor_ids)
    if len(sorted_factor_ids) < 2:
        return {"factor_ids": sorted_factor_ids, "matrix": []}

    # 只保留所有因子都有值的 ETF
    valid_etfs = [
        code
        for code, vals in data.items()
        if all(fid in vals for fid in sorted_factor_ids)
    ]
    if len(valid_etfs) < 3:
        return {"factor_ids": sorted_factor_ids, "matrix": []}

    # 构建各因子的值向量
    vectors = {
        fid: [data[code][fid] for code in valid_etfs] for fid in sorted_factor_ids
    }

    # 计算相关矩阵
    n = len(sorted_factor_ids)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        matrix[i][i] = 1.0
        for j in range(i + 1, n):
            corr, _ = spearmanr(vectors[sorted_factor_ids[i]], vectors[sorted_factor_ids[j]])
            val = round(float(corr), 4) if corr == corr else 0.0
            matrix[i][j] = val
            matrix[j][i] = val

    return {
        "factor_ids": sorted_factor_ids,
        "matrix": matrix,
        "etf_count": len(valid_etfs),
        "trade_date": str(trade_date),
    }
