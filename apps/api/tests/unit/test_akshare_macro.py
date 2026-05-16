"""测试 AkShareMacroClient 宏观数据接口。

所有测试使用真实 AkShare 调用，不做 mock。
执行方式: pytest tests/unit/test_akshare_macro.py -v
"""

import pytest

from quant_etf_api.infra.clients.akshare_macro import (
    AkShareMacroClient,
    MacroIndicator,
)


@pytest.fixture(scope="module")
def client() -> AkShareMacroClient:
    return AkShareMacroClient()


class TestSourceName:
    """source_name 属性。"""

    def test_source_name(self, client: AkShareMacroClient) -> None:
        assert client.source_name == "akshare_macro"


class TestCPI:
    """CPI 月度数据。"""

    def test_fetch_cpi_returns_data(self, client: AkShareMacroClient) -> None:
        indicators = client.fetch_cpi_monthly()
        assert len(indicators) > 0
        assert all(i.indicator_code == "cpi" for i in indicators)
        assert all(i.unit == "%" for i in indicators)
        # 验证 period 格式为 YYYY-MM
        for i in indicators[:5]:
            assert "-" in i.period, f"period 格式错误: {i.period}"


class TestPMI:
    """PMI 月度数据。"""

    def test_fetch_pmi_returns_data(self, client: AkShareMacroClient) -> None:
        indicators = client.fetch_pmi()
        assert len(indicators) > 0
        assert all(i.indicator_code == "pmi" for i in indicators)
        # period 格式应为 YYYY-MM
        for i in indicators[:5]:
            assert len(i.period) == 7, f"period 格式错误: {i.period}"


class TestLPR:
    """LPR 利率数据。"""

    def test_fetch_lpr_returns_data(self, client: AkShareMacroClient) -> None:
        indicators = client.fetch_lpr()
        assert len(indicators) > 0
        codes = {i.indicator_code for i in indicators}
        assert "lpr1y" in codes, "应包含 1 年期 LPR"
        assert "lpr5y" in codes, "应包含 5 年期 LPR"


class TestFetchAll:
    """fetch_all 便捷方法。"""

    def test_fetch_all_returns_combined(self, client: AkShareMacroClient) -> None:
        indicators = client.fetch_all()
        assert len(indicators) > 0
        codes = {i.indicator_code for i in indicators}
        assert "cpi" in codes, "fetch_all 应包含 CPI"
        assert "pmi" in codes, "fetch_all 应包含 PMI"
        assert "lpr1y" in codes or "lpr5y" in codes, "fetch_all 应包含 LPR"
        # 验证每条数据都有必要字段
        for i in indicators[:5]:
            assert isinstance(i, MacroIndicator)
            assert i.value is not None


class TestHealthCheck:
    """连通性检测。"""

    def test_health_check_healthy(self, client: AkShareMacroClient) -> None:
        status = client.health_check()
        assert status.healthy is True
