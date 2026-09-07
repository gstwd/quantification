"""指数成分数据客户端（免费源：baostock PIT + AkShare 当前快照）。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)


class BaostockIndexMemberClient:
    """按日期查询沪深300/中证500/上证50 历史成分（baostock 免费源）。"""

    _SUPPORTED_CODES = {
        "000300": "query_hs300_stocks",
        "000905": "query_zz500_stocks",
        "000016": "query_sz50_stocks",
    }

    def fetch_pit(self, index_code: str, trade_date: date) -> list[dict[str, Any]]:
        """查询指数在指定交易日的成分股列表。

        Args:
            index_code: 指数代码（000300/000905/000016）。
            trade_date: 查询日期。

        Returns:
            含 stock_code 的字典列表；接口失败返回空列表并记录日志。
        """
        method = self._SUPPORTED_CODES.get(index_code)
        if method is None:
            logger.warning("baostock 不支持指数 %s 的历史成分查询", index_code)
            return []
        try:
            import baostock as bs

            login = bs.login()
            if login is None or getattr(login, "error_code", "1") != "0":
                logger.warning("baostock 登录失败，跳过 %s %s", index_code, trade_date)
                return []
            try:
                result = getattr(bs, method)(date=trade_date.isoformat())
                rows: list[dict[str, Any]] = []
                if result is not None and getattr(result, "error_code", "1") == "0":
                    while result.next():
                        record = result.get_row_data()
                        fields = result.fields
                        data = dict(zip(fields, record))
                        code = str(data.get("code", "")).replace("sh.", "").replace("sz.", "")
                        if code:
                            rows.append({"stock_code": code.zfill(6)})
                return rows
            finally:
                bs.logout()
        except Exception:
            logger.exception("baostock 拉取指数成分失败: %s %s", index_code, trade_date)
            return []


class AkShareIndexMemberClient:
    """AkShare 当前成分/权重快照（中证官网优先，新浪兜底）。"""

    def fetch_current_snapshot(self, index_code: str) -> list[dict[str, Any]]:
        """获取指数当前成分（含权重时写入 weight，0-1）。"""
        try:
            import akshare as ak

            try:
                frame = ak.index_stock_cons_weight_csindex(symbol=index_code)
                return self._rows_from_frame(frame, has_weight=True)
            except Exception:
                frame = ak.index_stock_cons_csindex(symbol=index_code)
                return self._rows_from_frame(frame, has_weight=False)
        except Exception:
            logger.warning("AkShare 当前成分获取失败: index=%s", index_code, exc_info=True)
            return []

    @staticmethod
    def _rows_from_frame(frame: Any, *, has_weight: bool) -> list[dict[str, Any]]:
        """把 AkShare 返回的成分表归一化为 {stock_code, weight} 行。"""
        if frame is None or frame.empty:
            return []
        rows: list[dict[str, Any]] = []
        for _, record in frame.iterrows():
            code = str(record.get("成分券代码", "")).zfill(6)
            if not code or code == "000000":
                continue
            row: dict[str, Any] = {"stock_code": code}
            if has_weight:
                weight = record.get("权重")
                row["weight"] = float(weight) / 100.0 if weight is not None else None
            rows.append(row)
        return rows
