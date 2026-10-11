"""IngestService 读路径单元测试。

验证 GET 查询未命中时既不在请求线程同步抓取外部 API，也不再入队
data_fill 后台补数任务：外部数据只能经统一数据管理入口写入。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from quant_etf_api.services.ingest_service import IngestService


def _make_service(db: MagicMock) -> IngestService:
    """构建测试用 IngestService。"""
    return IngestService(db)


class TestReadPathIsReadOnly:
    """读路径缺数时保持只读的行为测试。"""

    @pytest.mark.parametrize(
        ("method_name", "args"),
        [
            ("get_index_daily_bars", ("000300",)),
            ("get_index_valuation", ("000300",)),
            ("get_macro_indicators", ("cpi",)),
        ],
    )
    def test_empty_result_does_not_enqueue_fill(
        self, monkeypatch, method_name: str, args: tuple
    ) -> None:
        """查询为空时直接返回空列表，不入队任何任务、不调用外部客户端。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        svc = _make_service(db)

        fake_queue = MagicMock()
        monkeypatch.setattr(
            "quant_etf_api.infra.job_queue.queue.get_job_queue", lambda: fake_queue
        )

        result = getattr(svc, method_name)(*args)

        assert result == []
        fake_queue.enqueue.assert_not_called()
        db.commit.assert_not_called()
