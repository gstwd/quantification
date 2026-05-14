from datetime import date


class SystemService:
    def status(self) -> dict:
        return {
            "asset_scope": "a_share_etf",
            "frequency": "daily",
            "latest_trade_date": str(date.today()),
            "data_sources": ["tencent", "eastmoney"],
            "frontend": "vue3",
            "database": "postgresql",
        }
