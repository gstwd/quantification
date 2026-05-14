from quant_etf_api.config.settings import Settings, get_settings


def settings_dependency() -> Settings:
    return get_settings()
