"""IngestService 读穿透路径单元测试。

验证查询未命中时不再在请求线程同步抓取外部 API，
而是入队 data_fill 后台任务并立即返回空列表。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from quant_etf_api.services.ingest_service import IngestService


def _make_service(db: MagicMock) -> IngestService:
    """构建测试用 IngestService。"""
    return IngestService(db)


class TestReadThroughEnqueue:
    """读穿缓存缺数时入队补数行为测试。"""

    @pytest.mark.parametrize(
        ("method_name", "args", "expected_key"),
        [
            ("get_daily_bars", ("510300",), "bars:510300"),
            ("get_share_history", ("510300",), "shares:510300"),
            ("get_index_daily_bars", ("000300",), "index_bars:000300"),
            ("get_index_valuation", ("000300",), "index_valuation:000300"),
            ("get_macro_indicators", ("cpi",), "macro"),
        ],
    )
    def test_empty_result_enqueues_data_fill(
        self, monkeypatch, method_name: str, args: tuple, expected_key: str
    ) -> None:
        """查询为空时应入队 data_fill 且不调用任何外部客户端。"""
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        svc = _make_service(db)

        fake_queue = MagicMock()
        monkeypatch.setattr(
            "quant_etf_api.infra.job_queue.queue.get_job_queue", lambda: fake_queue
        )

        result = getattr(svc, method_name)(*args)

        assert result == []
        assert fake_queue.enqueue.call_count == 1
        job_type, payload = fake_queue.enqueue.call_args.args
        assert job_type == "data_fill"
        assert payload["resource"] == expected_key.split(":")[0]
        assert fake_queue.enqueue.call_args.kwargs["job_key"] == expected_key
        assert fake_queue.enqueue.call_args.kwargs["max_attempts"] == 2
