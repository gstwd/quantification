class ExchangeReferenceClient:
    def list_reference_sources(self) -> list[dict]:
        return [
            {"name": "sse", "purpose": "ETF 主数据与份额补充"},
            {"name": "szse", "purpose": "ETF 主数据与公告补充"},
        ]
