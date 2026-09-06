"""申万行业常量与分类映射测试。"""

from __future__ import annotations

from quant_etf_api.domain.industry.constants import (
    SW_CLASSIFICATION_EPOCH,
    SW_CLASSIFICATION_PREFIX_TO_L1,
    SW_CLASSIFICATION_REMAP_SINCE,
    SW_EXCLUDED_INDUSTRY_CODES,
    SW_L1_INDUSTRIES,
    SW_L1_NAMES,
    normalize_sw_code,
)


def test_sw_l1_count_and_exclusions() -> None:
    """申万一级共 31 个行业，综合为基准剔除项。"""
    assert len(SW_L1_NAMES) == 31
    assert len(SW_L1_INDUSTRIES) == 31
    assert SW_EXCLUDED_INDUSTRY_CODES == frozenset({"801230"})


def test_prefix_mapping_known_samples() -> None:
    """已知样本：银行 48 → 801780，医药 37 → 801150，综合 51 → 801230。"""
    assert SW_CLASSIFICATION_PREFIX_TO_L1["48"] == "801780"
    assert SW_CLASSIFICATION_PREFIX_TO_L1["37"] == "801150"
    assert SW_CLASSIFICATION_PREFIX_TO_L1["51"] == "801230"
    assert len(SW_CLASSIFICATION_PREFIX_TO_L1) == 31


def test_classification_cutoffs() -> None:
    """体系切换日与 2021 版回写日固定为预期值。"""
    assert SW_CLASSIFICATION_EPOCH.isoformat() == "2014-02-21"
    assert SW_CLASSIFICATION_REMAP_SINCE.isoformat() == "2021-07-01"


def test_normalize_sw_code() -> None:
    """归一化去掉 .SI 后缀。"""
    assert normalize_sw_code("801010.SI") == "801010"
    assert normalize_sw_code("801010") == "801010"
