"""行业数据管理（P03）只读端点边界测试。"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from quant_etf_api.api.routers.industry import (
    get_diffusion_series,
    get_rrg_series,
    industry_daily_bars,
    industry_quality_detail,
)


def test_bars_inverted_range_returns_422() -> None:
    """K 线端点日期区间颠倒返回 422，不触达仓库。"""
    db = MagicMock()
    with pytest.raises(HTTPException) as exc_info:
        industry_daily_bars(
            "801010",
            start_date=date(2024, 1, 10),
            end_date=date(2024, 1, 2),
            db=db,
        )
    assert exc_info.value.status_code == 422


def test_quality_unknown_industry_returns_404(monkeypatch) -> None:
    """未知行业的质量详情端点返回 404。"""
    fake_service = MagicMock()
    fake_service.industry_quality_detail.side_effect = ValueError(
        "行业 999999 不在 industry_universe 中"
    )
    monkeypatch.setattr(
        "quant_etf_api.api.routers.industry.IndustryDataService",
        lambda db: fake_service,
    )
    with pytest.raises(HTTPException) as exc_info:
        industry_quality_detail("999999", db=MagicMock())
    assert exc_info.value.status_code == 404


def test_rrg_range_exceeds_cap_returns_422() -> None:
    """RRG 单次查询超过自然日上限时返回 422，不触达服务层。"""
    with pytest.raises(HTTPException) as exc_info:
        get_rrg_series(
            start=date(2012, 1, 1),
            end=date(2026, 9, 1),
            db=MagicMock(),
        )
    assert exc_info.value.status_code == 422
    assert "最多支持" in str(exc_info.value.detail)


def test_diffusion_range_exceeds_cap_returns_422() -> None:
    """扩散单次查询超过自然日上限时返回 422，不触达服务层。"""
    with pytest.raises(HTTPException) as exc_info:
        get_diffusion_series(
            start=date(2018, 1, 1),
            end=date(2026, 9, 1),
            db=MagicMock(),
        )
    assert exc_info.value.status_code == 422
    assert "最多支持" in str(exc_info.value.detail)
