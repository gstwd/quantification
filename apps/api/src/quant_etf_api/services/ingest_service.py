from datetime import date, datetime

from quant_etf_api.schemas.market_data import DailyBar, ShareSnapshot


class IngestService:
    def latest_trade_date(self) -> date:
        return date.today()

    def get_daily_bars(self, etf_code: str, limit: int = 30) -> list[DailyBar]:
        today = date.today()
        return [
            DailyBar(
                trade_date=today,
                code=etf_code,
                open_price=4.0,
                high_price=4.1,
                low_price=3.95,
                close_price=4.05,
                change_pct=0.62,
                volume=123456.0,
                turnover=456789000.0,
                source="stub",
                ingested_at=datetime.utcnow(),
            )
        ]

    def get_share_history(self, etf_code: str, limit: int = 30) -> list[ShareSnapshot]:
        today = date.today()
        return [
            ShareSnapshot(
                trade_date=today,
                etf_code=etf_code,
                shares_total=380.2,
                shares_delta=5.1,
                shares_delta_pct=1.36,
                nav=4.02,
                aum=1528.0,
                source="stub",
                ingested_at=datetime.utcnow(),
            )
        ]
