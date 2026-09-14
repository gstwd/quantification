"""回测仓库过滤表达式的编译校验（C1）。

``backtest_run.params`` 在 ORM 中是通用 ``JSON`` 列，其比较器没有 PostgreSQL
专有的 ``astext``（历史缺陷：`params["key"].astext` 直接 AttributeError，
导致按日历来源过滤整个查询失败并静默返回空列表）。这里锁定表达式可在
PostgreSQL 方言下编译，并生成 ``->>`` 取值操作符。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from quant_etf_api.infra.db.models.core import BacktestRunModel
from quant_etf_api.infra.db.repositories.backtest import calendar_source_filter


class TestCalendarSourceFilter:
    """日历来源过滤表达式。"""

    def test_compiles_to_json_arrow_operator(self) -> None:
        """表达式在 PG 方言下编译为 ->> 文本取值。"""
        statement = select(BacktestRunModel.backtest_id).where(calendar_source_filter("upstream"))
        compiled = statement.compile(dialect=postgresql.dialect())
        assert "->>" in str(compiled)
        # 键名同样以绑定参数传入
        assert "_calendar_source" in compiled.params.values()

    def test_binds_value_as_parameter(self) -> None:
        """过滤值以绑定参数传入，不做字符串拼接。"""
        statement = select(BacktestRunModel.backtest_id).where(calendar_source_filter("database"))
        compiled = statement.compile(dialect=postgresql.dialect())
        assert "database" in compiled.params.values()
