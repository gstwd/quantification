from datetime import date

from quant_etf_api.schemas.signal import FactorRow, SignalRow


class SignalService:
    def latest_signals(self, strategy_id: str) -> list[SignalRow]:
        return [
            SignalRow(
                trade_date=date.today(),
                etf_code="510300",
                strategy_id=strategy_id,
                signal_score=72.5,
                signal_level="HIGH",
                signal_label="高确信",
                signal_payload={"volume_prob": 82.0, "direction_prob": 61.0, "share_prob": 74.0},
            )
        ]

    def factor_rows(self, etf_code: str, trade_date: date) -> list[FactorRow]:
        return [
            FactorRow(
                trade_date=trade_date,
                etf_code=etf_code,
                factor_id="volume_ratio_20d",
                factor_value_numeric=1.86,
                factor_payload={"window": 20},
                strategy_id="three_factor_guard",
            )
        ]
