"""个股元数据客户端（交易所批量股票名单，提供名称/上市日/退市状态）。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def _col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """按候选列名列表返回首个存在的列名。"""
    for name in candidates:
        if name in df.columns:
            return name
    return None


def _as_date(value: Any) -> date | None:
    """把字符串/日期/NaT 统一转换为 date，无效返回 None。"""
    if value is None or (isinstance(value, float) and value != value):
        return None
    try:
        return pd.to_datetime(value, errors="coerce").date()
    except (TypeError, ValueError):
        return None


def _rows_from_frame(
    df: pd.DataFrame,
    *,
    code_cols: list[str],
    name_cols: list[str],
    ipo_cols: list[str],
    delist_cols: list[str] | None = None,
    is_active: bool,
    source: str,
) -> list[dict[str, Any]]:
    """将交易所名单 DataFrame 归一化为个股元数据行列表。"""
    code_col = _col(df, code_cols)
    name_col = _col(df, name_cols)
    ipo_col = _col(df, ipo_cols)
    delist_col = _col(df, delist_cols or []) if delist_cols else None
    if code_col is None:
        return []
    rows: list[dict[str, Any]] = []
    for _, raw in df.iterrows():
        code = str(raw.get(code_col) or "").strip()
        if not code or not code.isdigit():
            continue
        code = code.zfill(6)
        rows.append(
            {
                "stock_code": code,
                "name_cn": str(raw.get(name_col) or "") if name_col else "",
                "ipo_date": _as_date(raw.get(ipo_col)) if ipo_col else None,
                "delist_date": _as_date(raw.get(delist_col)) if delist_col else None,
                "is_active": is_active,
                "source": source,
            }
        )
    return rows


class StockMetadataClient:
    """交易所个股名单客户端（深/沪/北 + 沪深终止上市名单）。"""

    source_name = "akshare_exchange"

    def fetch_exchange_basics(self) -> list[dict[str, Any]]:
        """拉取全部可用交易所名单并归一化。

        返回按股票代码合并后的列表；上游单个接口失败不影响其他接口，
        该接口会记录告警并继续。终止上市名单优先级高于活跃名单，
        用于把退市股票标记为 is_active=False。

        Returns:
            归一化后的 [{stock_code, name_cn, ipo_date, delist_date, is_active, source}]。
        """
        merged: dict[str, dict[str, Any]] = {}
        for rows in (
            self._fetch_sz_active(),
            self._fetch_sh_active(),
            self._fetch_bj_active(),
            self._fetch_sz_delisted(),
            self._fetch_sh_delisted(),
        ):
            for row in rows:
                code = row["stock_code"]
                merged[code] = row
        return list(merged.values())

    def _fetch_sz_active(self) -> list[dict[str, Any]]:
        """拉取深交所 A 股名单。"""
        try:
            import akshare as ak

            df = ak.stock_info_sz_name_code(symbol="A股列表")
            return _rows_from_frame(
                df,
                code_cols=["A股代码"],
                name_cols=["A股简称"],
                ipo_cols=["A股上市日期"],
                is_active=True,
                source="akshare_sz",
            )
        except Exception:
            logger.warning("深交所股票名单拉取失败，跳过", exc_info=True)
            return []

    def _fetch_sh_active(self) -> list[dict[str, Any]]:
        """拉取上交所主板与科创板 A 股名单。"""
        try:
            import akshare as ak

            frames: list[pd.DataFrame] = []
            for symbol in ("主板A股", "科创板"):
                frame = ak.stock_info_sh_name_code(symbol=symbol)
                if frame is not None and not frame.empty:
                    frames.append(frame)
            if not frames:
                return []
            merged = pd.concat(frames, ignore_index=True)
            return _rows_from_frame(
                merged,
                code_cols=["证券代码", "A_STOCK_CODE"],
                name_cols=["证券简称", "SEC_NAME_CN"],
                ipo_cols=["上市日期", "LIST_DATE"],
                is_active=True,
                source="akshare_sh",
            )
        except Exception:
            logger.warning("上交所股票名单拉取失败，跳过", exc_info=True)
            return []

    def _fetch_bj_active(self) -> list[dict[str, Any]]:
        """拉取北交所股票名单。"""
        try:
            import akshare as ak

            df = ak.stock_info_bj_name_code()
            return _rows_from_frame(
                df,
                code_cols=["证券代码"],
                name_cols=["证券简称"],
                ipo_cols=["上市日期"],
                is_active=True,
                source="akshare_bj",
            )
        except Exception:
            logger.warning("北交所股票名单拉取失败，跳过", exc_info=True)
            return []

    def _fetch_sz_delisted(self) -> list[dict[str, Any]]:
        """拉取深交所终止上市公司名单。"""
        try:
            import akshare as ak

            df = ak.stock_info_sz_delist(symbol="终止上市公司")
            return _rows_from_frame(
                df,
                code_cols=["证券代码"],
                name_cols=["证券简称"],
                ipo_cols=["上市日期"],
                delist_cols=["终止上市日期"],
                is_active=False,
                source="akshare_sz_delist",
            )
        except Exception:
            logger.warning("深交所终止上市名单拉取失败，跳过", exc_info=True)
            return []

    def _fetch_sh_delisted(self) -> list[dict[str, Any]]:
        """拉取上交所终止上市公司名单（全部板块）。"""
        try:
            import akshare as ak

            df = ak.stock_info_sh_delist(symbol="全部")
            return _rows_from_frame(
                df,
                code_cols=["公司代码", "证券代码"],
                name_cols=["公司简称", "证券简称"],
                ipo_cols=["上市日期"],
                delist_cols=["暂停上市日期", "终止上市日期"],
                is_active=False,
                source="akshare_sh_delist",
            )
        except Exception:
            logger.warning("上交所终止上市名单拉取失败，跳过", exc_info=True)
            return []
