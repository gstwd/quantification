"""过拟合风险统计纯函数测试（CSCV-PBO / Deflated Sharpe / 自助法 / 邻域稳定度）。"""

from __future__ import annotations

import copy

import pytest

from quant_etf_api.domain.research.robustness import (
    annualized_sharpe,
    block_bootstrap_sharpe_ci,
    compute_pbo,
    deflated_sharpe_ratio,
    skewness_and_kurtosis,
    summarize_neighborhood,
)
from quant_etf_api.services.robustness_service import (
    build_variant_strategy_id,
    build_ablation_variants,
    build_knob_variants,
    resolve_scan_options,
)


def _daily_returns(
    count: int, mean: float, sigma: float = 0.01, seed: int = 12345
) -> list[float]:
    """用固定种子的线性同余发生器构造确定性日收益序列（小数口径）。

    使用伪随机而非正弦序列：正弦序列的夏普会高到让 Deflated Sharpe 饱和在 1，
    无法检验"试验次数越多显著性越低"这一性质。
    """
    values: list[float] = []
    state = seed
    for _ in range(count):
        state = (1103515245 * state + 12345) % (2**31)
        uniform = state / (2**31)
        values.append(mean + sigma * (uniform * 2 - 1))
    return values


class TestPbo:
    """CSCV 回测过拟合概率。"""

    def test_dominant_candidate_gives_zero_pbo(self) -> None:
        """样本内外都最优的候选不应被判为过拟合。"""
        result = compute_pbo(
            {
                "A": [3.0, 3.0, 3.0, 3.0],
                "B": [1.0, 1.0, 1.0, 1.0],
                "C": [0.0, 0.0, 0.0, 0.0],
            }
        )
        assert result.pbo == 0.0
        assert result.n_candidates == 3
        assert result.n_splits == 6  # C(4,2)

    def test_alternating_winner_produces_high_pbo(self) -> None:
        """只在部分分块领先的候选应被判为过拟合。"""
        result = compute_pbo(
            {
                "A": [3.0, 3.0, 0.0, 0.0],
                "B": [0.0, 0.0, 3.0, 3.0],
                "C": [1.0, 1.0, 1.0, 1.0],
            }
        )
        assert 0.0 < result.pbo <= 1.0
        assert result.pbo > 0.5

    def test_insufficient_candidates_returns_zero(self) -> None:
        """有效候选少于 3 个时返回 pbo=None 并说明原因（F-14）。"""
        result = compute_pbo(
            {"A": [1.0, 2.0, 3.0, 4.0], "B": [2.0, 1.0, 2.5, 3.5]}
        )
        assert result.pbo is None
        assert result.n_candidates == 2
        assert result.reason is not None and "候选" in result.reason

    def test_too_few_blocks_returns_none(self) -> None:
        """分块数少于 MIN_PBO_BLOCKS 时不能输出 0（会被读成"没有过拟合"，F-14）。"""
        result = compute_pbo(
            {"A": [3.0, 1.0], "B": [1.0, 3.0], "C": [2.0, 2.0]}
        )
        assert result.pbo is None
        assert result.n_splits == 0
        assert result.n_blocks == 2
        assert result.reason is not None and "分块" in result.reason

    def test_odd_blocks_raise(self) -> None:
        """分块数为奇数时抛 ValueError（无法做对称切分）。"""
        with pytest.raises(ValueError):
            compute_pbo({"A": [1.0, 2.0, 3.0], "B": [3.0, 2.0, 1.0], "C": [2.0, 2.0, 2.0]})


class TestDeflatedSharpe:
    """Deflated Sharpe 与试验次数的关系。"""

    def test_more_trials_lower_significance(self) -> None:
        """试验次数越多，同样夏普的显著性越低（多重检验折减）。"""
        returns = _daily_returns(500, mean=0.0004)
        one = deflated_sharpe_ratio(returns, n_trials=1)
        many = deflated_sharpe_ratio(returns, n_trials=100)
        assert 0.0 <= many.deflated_sharpe <= 1.0
        assert many.deflated_sharpe < one.deflated_sharpe
        assert many.expected_max_sharpe_annualized > one.expected_max_sharpe_annualized
        assert many.n_observations == 500

    def test_invalid_trials_raises(self) -> None:
        """试验次数必须 ≥ 1。"""
        with pytest.raises(ValueError):
            deflated_sharpe_ratio(_daily_returns(50, 0.0004), n_trials=0)


class TestBootstrap:
    """块自助法置信区间。"""

    def test_reproducible_and_ordered(self) -> None:
        """同一输入应得到可复现且有序的置信区间。"""
        returns = _daily_returns(300, mean=0.0004)
        first = block_bootstrap_sharpe_ci(returns, block=20, n_bootstrap=200)
        second = block_bootstrap_sharpe_ci(returns, block=20, n_bootstrap=200)
        assert first.lower == second.lower
        assert first.upper == second.upper
        assert first.lower < first.upper
        assert first.sharpe_annualized == pytest.approx(annualized_sharpe(returns))

    def test_invalid_parameters_raise(self) -> None:
        """块长度、抽样次数与置信水平做参数校验。"""
        returns = _daily_returns(50, 0.0004)
        with pytest.raises(ValueError):
            block_bootstrap_sharpe_ci(returns, block=0)
        with pytest.raises(ValueError):
            block_bootstrap_sharpe_ci(returns, n_bootstrap=0)
        with pytest.raises(ValueError):
            block_bootstrap_sharpe_ci(returns, confidence=1.0)


class TestNeighborhood:
    """参数邻域稳定度。"""

    def test_plateau(self) -> None:
        """邻域内变化都在容差内时判定为参数高原。"""
        summary = summarize_neighborhood(0.5, [0.45, 0.55], tolerance=0.1)
        assert summary.is_plateau is True
        assert summary.reversal is False
        assert summary.worse_ratio == pytest.approx(0.5)

    def test_reversal_detected(self) -> None:
        """既有明显更好又有明显更差的变体时应提示方向反转。"""
        summary = summarize_neighborhood(0.5, [0.9, 0.1], tolerance=0.1)
        assert summary.reversal is True
        assert summary.is_plateau is False
        assert summary.delta_min == pytest.approx(-0.4)
        assert summary.delta_max == pytest.approx(0.4)

    def test_empty_variants(self) -> None:
        """无有效变体时不报错，且不能把"没算出东西"判成参数高原（F-3）。"""
        summary = summarize_neighborhood(0.5, [])
        assert summary.n_variants == 0
        assert summary.is_plateau is None
        assert summary.worse_ratio is None
        assert summary.reversal is None


class TestVariantGeneration:
    """单旋钮与消融变体的派生规则。"""

    _CONFIG = {
        "schema_version": "1",
        "index_codes": ["000300", "000905"],
        "score": {"factors": {"return_20d": 1.0, "volatility_20d": 0.5}, "scoring_mode": "zscore"},
        "filters": {"logic": "AND", "rules": [{"factor": "return_20d", "op": "gte", "value": 0}]},
        "rank": {"top_n": 3},
        "rebalance": {"frequency": "weekly", "day_of_week": 2},
        "portfolio": {"method": "equal_weight", "default_exposure": 0.8},
    }

    def test_knob_variants_cover_keys_and_respect_ratio_cap(self) -> None:
        """单旋钮变体应覆盖权重、持仓数、调仓星期与仓位，并遵守比例上限。"""
        variants = build_knob_variants(self._CONFIG, max_knobs=50)
        labels = {item["label"] for item in variants}
        assert "rank_top_n_2" in labels
        assert "rebalance_day_of_week_1" in labels
        assert "score_factors_return_20d_1_5" in labels
        # 比例类字段（default_exposure）上限截到 1.0，不产生 1.2
        exposure_values = {
            item["value"] for item in variants if item["knob"] == "portfolio.default_exposure"
        }
        assert exposure_values == {0.4, 1.0}

    def test_knob_variants_respect_max_limit(self) -> None:
        """扰动数量受上限约束。"""
        variants = build_knob_variants(self._CONFIG, max_knobs=3)
        assert len(variants) == 3

    def test_ablation_variants_remove_one_at_a_time(self) -> None:
        """消融变体逐个移除评分因子与过滤条件，且保留至少一个因子。"""
        variants = build_ablation_variants(self._CONFIG)
        labels = {item["label"] for item in variants}
        assert labels == {"ablate_score_return_20d", "ablate_score_volatility_20d"}
        for item in variants:
            factors = item["config"]["score"]["factors"]
            assert len(factors) == 1

    def test_ablation_skips_single_factor_model(self) -> None:
        """只有一个因子时不做评分因子消融（否则模型不成立）。"""
        config = {
            "score": {"factors": {"return_20d": 1.0}},
            "filters": {"rules": [{"factor": "return_20d"}]},
        }
        assert build_ablation_variants(config) == []

    def test_ablation_labels_unique_for_same_factor_rules(self) -> None:
        """同一因子的多条过滤规则必须派生不同标签（F-2）。

        历史缺陷：标签只含因子名，v8 的两条 ``close_price`` 规则
        （ma_10d / ma_17d）派生同一个变体 ID，第二个消融静默复用了第一份配置，
        汇总里出现"knob 不同但指标完全相同"的两行。
        """
        config = {
            "score": {"factors": {"return_20d": 1.0, "volatility_20d": 0.5}},
            "filters": {
                "logic": "AND",
                "rules": [
                    {"factor": "close_price", "op": "gt", "compare_to": "ma_17d"},
                    {"factor": "close_price", "op": "gt", "compare_to": "ma_10d"},
                ],
            },
        }
        variants = build_ablation_variants(config)
        labels = [item["label"] for item in variants if item["kind"] == "ablation"]
        assert len(labels) == len(set(labels))
        assert "ablate_filter_rules0_close_price" in labels
        assert "ablate_filter_rules1_close_price" in labels
        # 两个变体的配置确实不同：一个保留 ma_10d，一个保留 ma_17d
        by_label = {item["label"]: item["config"] for item in variants}
        assert by_label["ablate_filter_rules0_close_price"]["filters"]["rules"] == [
            {"factor": "close_price", "op": "gt", "compare_to": "ma_10d"}
        ]
        assert by_label["ablate_filter_rules1_close_price"]["filters"]["rules"] == [
            {"factor": "close_price", "op": "gt", "compare_to": "ma_17d"}
        ]

    def test_knob_variants_do_not_mutate_input_config(self) -> None:
        """派生单旋钮变体不得就地改写输入配置。

        历史缺陷：变体与基线共享嵌套字典，某个越界档位（如把
        ``risk.max_portfolio_exposure`` 从 1 扰动到 2）会顺带污染全部候选，
        导致整批变体在校验阶段被判定无效——``robustness scan`` 因此报
        "未派生任何有效变体"。
        """
        config = copy.deepcopy(self._CONFIG)
        build_knob_variants(config, max_knobs=50)
        assert config == self._CONFIG

    def test_knob_variants_are_independent(self) -> None:
        """单个越界档位只影响自身变体，不得泄漏到其他变体。"""
        config = copy.deepcopy(self._CONFIG)
        config["risk"] = {"max_asset_weight": 0.5, "max_portfolio_exposure": 1}
        variants = build_knob_variants(config, max_knobs=50)
        assert any(item["knob"] == "risk.max_portfolio_exposure" for item in variants)
        for item in variants:
            if item["knob"] == "risk.max_portfolio_exposure":
                continue
            assert item["config"]["risk"]["max_portfolio_exposure"] == 1

    def test_knob_variants_include_filter_rule_thresholds(self) -> None:
        """过滤规则里的数值阈值也要参与扰动（列表路径需可定位）。"""
        config = copy.deepcopy(self._CONFIG)
        config["filters"]["rules"][0]["value"] = -5
        variants = build_knob_variants(config, max_knobs=50)
        knobs = {item["knob"] for item in variants}
        assert "filters.rules[0].value" in knobs
        values = {
            item["value"]
            for item in variants
            if item["knob"] == "filters.rules[0].value"
        }
        assert values == {-6, -4}
        # 扰动只落在目标叶子上，其余结构保持一致
        for item in variants:
            if item["knob"] != "filters.rules[0].value":
                continue
            assert item["config"]["filters"]["logic"] == "AND"
            assert len(item["config"]["filters"]["rules"]) == 1
            assert item["config"]["rank"]["top_n"] == 3

    def test_ablation_variants_do_not_mutate_input_config(self) -> None:
        """消融变体同样不得改写输入配置。"""
        config = copy.deepcopy(self._CONFIG)
        build_ablation_variants(config)
        assert config == self._CONFIG


class TestKnobPriorityAndPresets:
    """D-2：截断顺序按业务重要性，支持关键旋钮清单与轻量体检预设。"""

    _CONFIG = {
        "schema_version": "1",
        "score": {"factors": {"return_20d": 1.0}},
        "rank": {"top_n": 3},
        "risk": {"max_asset_weight": 0.5},
        "timing": {"thresholds": {"offensive": 65, "defensive": 40}},
        "rebalance": {"day_of_week": 2},
    }

    def test_timing_thresholds_are_scanned_first(self) -> None:
        """择时阈值优先于字母序更小的其他字段被扫描。

        历史行为按路径字母序截断，``timing.thresholds.*`` 永远排在最后被截掉，
        而它恰恰是最容易被调到"刚好"的参数。
        """
        variants = build_knob_variants(self._CONFIG, max_knobs=2)
        assert variants
        assert all(item["knob"].startswith("timing.thresholds") for item in variants)
        # 非择时字段（按字母序排在前面的 rank/risk/rebalance）不会先被扫描
        assert not any(item["knob"].startswith("rank.") for item in variants)

    def test_explicit_knobs_limit_scope(self) -> None:
        """给出关键旋钮清单时只扫描清单内的路径。"""
        variants = build_knob_variants(
            self._CONFIG, max_knobs=50, knobs=["rank.top_n", "risk.max_asset_weight"]
        )
        assert {item["knob"] for item in variants} == {"rank.top_n", "risk.max_asset_weight"}

    def test_unknown_knob_path_rejected(self) -> None:
        """清单里出现配置中不存在的路径时直接报错，而不是静默少扫。"""
        with pytest.raises(ValueError, match="不存在的数值字段"):
            build_knob_variants(self._CONFIG, max_knobs=50, knobs=["timing.unknown_knob"])

    def test_quick_preset_is_lightweight(self) -> None:
        """quick 预设 = 2 窗口 / 8 旋钮的轻量体检（写进优化验收清单）。"""
        windows, max_knobs, scan_params = resolve_scan_options("quick", None, None)
        assert (windows, max_knobs) == (2, 8)
        assert scan_params["preset"] == "quick"

    def test_explicit_values_override_preset(self) -> None:
        """显式参数优先于预设；两者都未给时等价于 standard。"""
        windows, max_knobs, params = resolve_scan_options("quick", 3, 12)
        assert (windows, max_knobs) == (3, 12)
        assert params["preset"] == "quick"
        default_windows, default_knobs, default_params = resolve_scan_options(None, None, None)
        assert (default_windows, default_knobs) == (4, 30)
        assert default_params["preset"] == "standard"

    def test_unknown_preset_rejected(self) -> None:
        """未知预设直接报错。"""
        with pytest.raises(ValueError, match="不支持的扫描预设"):
            resolve_scan_options("turbo", None, None)


class TestDistributionShape:
    """偏度/峰度辅助函数。"""

    def test_kurtosis_of_short_series_defaults(self) -> None:
        """样本不足时返回正态峰度 3。"""
        assert skewness_and_kurtosis([0.01]) == (0.0, 3.0)


class TestVariantStrategyId:
    """变体策略 ID 的长度与唯一性（strategy_id 列宽 64）。"""

    def test_short_label_keeps_length_within_limit(self) -> None:
        """长基线 ID + 长标签时仍不超过 64 字符。"""
        variant_id = build_variant_strategy_id(
            "broad3_momentum_rotation",
            "7fead4e55d0246b096a2561ace5ca328",
            "portfolio_timing_exposure_defensive_0_25",
        )
        assert len(variant_id) <= 64

    def test_truncated_labels_stay_unique(self) -> None:
        """被截断的长标签通过哈希后缀保持唯一。"""
        base = "b" * 40
        shared_prefix = "portfolio_timing_exposure_defensive_"
        first = build_variant_strategy_id(base, "abcd1234", f"{shared_prefix}a")
        second = build_variant_strategy_id(base, "abcd1234", f"{shared_prefix}b")
        assert first != second
        assert len(first) <= 64 and len(second) <= 64
