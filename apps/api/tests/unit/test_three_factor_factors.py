from quant_etf_api.plugins.builtins.three_factor.factors import (
    composite_probability,
    direction_probability,
    share_probability,
    signal_level,
    volume_probability,
)


class TestVolumeProbability:
    def test_zero_volume(self) -> None:
        assert volume_probability(0.0) == 0.0

    def test_below_half(self) -> None:
        assert volume_probability(0.25) == 2.5

    def test_at_one(self) -> None:
        assert volume_probability(1.0) == 17.0

    def test_at_two(self) -> None:
        assert volume_probability(2.0) == 80.0

    def test_above_five_capped(self) -> None:
        assert volume_probability(100.0) <= 100.0

    def test_monotonic(self) -> None:
        ratios = [0.0, 0.5, 1.0, 1.3, 1.5, 2.0, 3.0, 5.0, 8.0]
        probs = [volume_probability(r) for r in ratios]
        assert probs == sorted(probs)


class TestShareProbability:
    def test_none_returns_none(self) -> None:
        assert share_probability(None) is None

    def test_large_increase(self) -> None:
        assert share_probability(12.0) == 95.0

    def test_small_increase(self) -> None:
        p = share_probability(0.5)
        assert p is not None
        assert 30 <= p <= 45

    def test_decrease(self) -> None:
        p = share_probability(-3.0)
        assert p is not None
        assert p < 15

    def test_large_decrease_floored(self) -> None:
        p = share_probability(-20.0)
        assert p is not None
        assert p >= 0.0


class TestCompositeProbability:
    def test_with_share(self) -> None:
        assert composite_probability(80.0, 60.0, 70.0) == 73.0

    def test_without_share_falls_back_to_two_factor(self) -> None:
        result = composite_probability(80.0, 60.0, None)
        assert result == round(80.0 * 0.7 + 60.0 * 0.3, 1)

    def test_weights_sum_to_one(self) -> None:
        assert composite_probability(100.0, 100.0, 100.0) == 100.0


class TestSignalLevel:
    def test_high(self) -> None:
        assert signal_level(75.0) == ("HIGH", "高确信")

    def test_mid(self) -> None:
        assert signal_level(55.0) == ("MID", "中等关注")

    def test_low(self) -> None:
        assert signal_level(30.0) == ("LOW", "正常")

    def test_boundary_70(self) -> None:
        assert signal_level(70.0)[0] == "HIGH"

    def test_boundary_50(self) -> None:
        assert signal_level(50.0)[0] == "MID"


class TestDirectionProbability:
    def test_returns_float(self) -> None:
        result = direction_probability(0.8, 1.5, -1.2, 1.8, -0.4)
        assert isinstance(result, float)

    def test_rally_discount_applied(self) -> None:
        no_rally = direction_probability(0.5, 1.0, -1.0, 1.5, 0.0)
        big_rally = direction_probability(0.5, 1.0, -1.0, 1.5, 2.5)
        assert big_rally < no_rally

    def test_counter_trend_high(self) -> None:
        result = direction_probability(0.5, 1.0, -2.0, 1.8, -0.3)
        assert result > 50
