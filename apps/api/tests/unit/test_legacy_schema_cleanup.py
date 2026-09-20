"""遗留表清理的回归测试。"""

from __future__ import annotations

from quant_etf_api.infra.db.base import Base
from quant_etf_api.infra.db import models  # noqa: F401


def test_legacy_tables_are_not_registered_in_runtime_metadata() -> None:
    """运行时 ORM 元数据不得重新登记已清理的历史表。"""
    assert "source_payload_log" not in Base.metadata.tables
    assert "signal_definition" not in Base.metadata.tables
