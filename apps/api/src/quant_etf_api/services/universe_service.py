from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from quant_etf_api.infra.clients.eastmoney import EastmoneyClient
from quant_etf_api.infra.db.models.core import EtfUniverseModel
from quant_etf_api.schemas.etf import EtfCreateRequest, EtfDetail

logger = logging.getLogger(__name__)

# DB 不可用时的降级数据（不再自动写入 DB，仅作内存兜底）
SEED_ETFS = [
    {
        "etf_code": "510300",
        "exchange": "SSE",
        "name_cn": "华泰柏瑞沪深300ETF",
        "tracking_index_code": "000300",
        "tracking_index_name": "沪深300",
        "category": "broad_index",
        "fund_company": "华泰柏瑞",
    },
    {
        "etf_code": "510050",
        "exchange": "SSE",
        "name_cn": "华夏上证50ETF",
        "tracking_index_code": "000016",
        "tracking_index_name": "上证50",
        "category": "broad_index",
        "fund_company": "华夏基金",
    },
    {
        "etf_code": "510500",
        "exchange": "SSE",
        "name_cn": "南方中证500ETF",
        "tracking_index_code": "000905",
        "tracking_index_name": "中证500",
        "category": "broad_index",
        "fund_company": "南方基金",
    },
    {
        "etf_code": "159919",
        "exchange": "SZSE",
        "name_cn": "嘉实沪深300ETF",
        "tracking_index_code": "000300",
        "tracking_index_name": "沪深300",
        "category": "broad_index",
        "fund_company": "嘉实基金",
    },
]


def detect_exchange(etf_code: str) -> str:
    if etf_code.startswith(("5", "6")):
        return "SSE"
    if etf_code.startswith(("1", "3")):
        return "SZSE"
    raise ValueError(f"无法识别交易所，ETF 代码前缀不合法: {etf_code}")


def _row_to_detail(row: EtfUniverseModel) -> EtfDetail:
    return EtfDetail(
        etf_code=row.etf_code,
        exchange=row.exchange,
        name_cn=row.name_cn,
        fund_full_name=row.fund_full_name,
        tracking_index_code=row.tracking_index_code,
        tracking_index_name=row.tracking_index_name,
        fund_company=row.fund_company,
        listing_date=row.listing_date,
        data_source=row.data_source,
        updated_at=row.updated_at,
        is_active=row.is_active,
        is_a_share_etf=row.is_a_share_etf,
        category=row.category,
    )


class UniverseService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_etfs(self) -> list[EtfDetail]:
        try:
            rows = (
                self._db.query(EtfUniverseModel)
                .filter(EtfUniverseModel.is_active.is_(True))
                .order_by(EtfUniverseModel.etf_code)
                .all()
            )
            return [_row_to_detail(r) for r in rows]
        except Exception:
            logger.warning("DB query failed, returning stub ETF list", exc_info=True)
            return [
                EtfDetail(
                    fund_full_name=item["name_cn"],
                    listing_date=date(2012, 1, 1),
                    data_source="seed",
                    updated_at=None,
                    is_active=True,
                    is_a_share_etf=True,
                    **item,
                )
                for item in SEED_ETFS
            ]

    def get_etf(self, etf_code: str) -> EtfDetail | None:
        try:
            row = self._db.get(EtfUniverseModel, etf_code)
            return _row_to_detail(row) if row else None
        except Exception:
            logger.warning("DB query failed for etf_code=%s", etf_code, exc_info=True)
            match = next((item for item in SEED_ETFS if item["etf_code"] == etf_code), None)
            if match is None:
                return None
            return EtfDetail(
                fund_full_name=match["name_cn"],
                listing_date=date(2012, 1, 1),
                data_source="seed",
                updated_at=None,
                is_active=True,
                is_a_share_etf=True,
                **match,
            )

    def add_etf(self, req: EtfCreateRequest) -> EtfDetail:
        try:
            row = self._db.get(EtfUniverseModel, req.etf_code)
            if row and row.is_active:
                raise ValueError(f"ETF {req.etf_code} 已存在")

            # 从东方财富拉取基金基本信息，失败时使用代码作为名称兜底
            try:
                info = EastmoneyClient().fetch_fund_info(req.etf_code)
            except Exception:
                logger.warning("Failed to fetch fund info for %s, using defaults", req.etf_code)
                info = None

            name_cn = info.name_cn if info else req.etf_code
            fund_full_name = info.fund_full_name if info else req.etf_code
            fund_company = info.fund_company if info else None
            tracking_index_name = info.tracking_index_name or "未知指数" if info else "未知指数"
            tracking_index_code = info.tracking_index_code if info else None

            if row:
                # 重新激活已下架的 ETF 并更新信息
                row.is_active = True
                row.name_cn = name_cn
                row.fund_full_name = fund_full_name
                row.fund_company = fund_company
                row.tracking_index_name = tracking_index_name
                row.tracking_index_code = tracking_index_code
                row.data_source = "manual"
            else:
                exchange = detect_exchange(req.etf_code)
                row = EtfUniverseModel(
                    etf_code=req.etf_code,
                    exchange=exchange,
                    name_cn=name_cn,
                    fund_full_name=fund_full_name,
                    tracking_index_name=tracking_index_name,
                    tracking_index_code=tracking_index_code,
                    fund_company=fund_company,
                    category="broad_index",
                    is_active=True,
                    is_a_share_etf=True,
                    data_source="manual",
                )
                self._db.add(row)
            self._db.commit()
            self._db.refresh(row)
            return _row_to_detail(row)
        except ValueError:
            raise
        except Exception:
            self._db.rollback()
            logger.error("Failed to add ETF %s", req.etf_code, exc_info=True)
            raise

    def remove_etf(self, etf_code: str) -> None:
        try:
            row = self._db.get(EtfUniverseModel, etf_code)
            if not row or not row.is_active:
                raise ValueError(f"ETF {etf_code} 不存在或已下架")
            row.is_active = False
            self._db.commit()
        except ValueError:
            raise
        except Exception:
            self._db.rollback()
            logger.error("Failed to remove ETF %s", etf_code, exc_info=True)
            raise
