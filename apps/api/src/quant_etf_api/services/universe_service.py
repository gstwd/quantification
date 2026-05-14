from datetime import date

from quant_etf_api.schemas.etf import EtfDetail

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


class UniverseService:
    def list_etfs(self) -> list[EtfDetail]:
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
        return next((item for item in self.list_etfs() if item.etf_code == etf_code), None)
