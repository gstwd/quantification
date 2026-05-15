from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from quant_etf_api.infra.db.models.core import EtfUniverseModel
from quant_etf_api.schemas.etf import EtfDetail

logger = logging.getLogger(__name__)

# 内置种子 ETF，覆盖四只主流宽基 ETF，应用启动时自动 upsert 到 DB
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

    def _seed(self) -> None:
        try:
            # on_conflict_do_nothing 保证幂等：已存在的 ETF 不会被覆盖
            stmt = insert(EtfUniverseModel).values(
                [
                    {
                        **item,
                        "fund_full_name": item["name_cn"],
                        "listing_date": date(2012, 1, 1),
                        "is_active": True,
                        "is_a_share_etf": True,
                        "data_source": "seed",
                    }
                    for item in SEED_ETFS
                ]
            ).on_conflict_do_nothing(index_elements=["etf_code"])
            self._db.execute(stmt)
            self._db.commit()
        except Exception:
            self._db.rollback()
            logger.warning("Seed failed, continuing with stub data", exc_info=True)

    def list_etfs(self) -> list[EtfDetail]:
        try:
            rows = self._db.query(EtfUniverseModel).filter(EtfUniverseModel.is_active.is_(True)).order_by(EtfUniverseModel.etf_code).all()
            if not rows:
                # DB 为空时触发一次种子数据写入（首次部署场景）
                self._seed()
                rows = self._db.query(EtfUniverseModel).filter(EtfUniverseModel.is_active.is_(True)).order_by(EtfUniverseModel.etf_code).all()
            return [_row_to_detail(r) for r in rows]
        except Exception:
            logger.warning("DB query failed, returning stub ETF list", exc_info=True)
            # DB 不可用时降级返回内存中的种子数据
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
            # DB 不可用时从种子数据中查找
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
