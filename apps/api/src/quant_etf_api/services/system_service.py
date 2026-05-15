from datetime import date


class SystemService:
    def status(self) -> dict:
        # 返回平台基本配置信息，供前端展示系统状态
        return {
            "asset_scope": "a_share_etf",       # 仅覆盖 A 股 ETF，不含个股
            "frequency": "daily",               # 日频策略，不支持分钟级
            "latest_trade_date": str(date.today()),
            "data_sources": ["tencent", "eastmoney"],
            "frontend": "vue3",
            "database": "postgresql",
        }
