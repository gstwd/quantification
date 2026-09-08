"""指数历史成分（PIT）回填维护脚本。

按指数逐个调用 IndexMembershipDataService.backfill_pit，每个指数独立提交，
实时打印进度，任一指数失败不会中断其余指数。默认回填全部活跃指数：

    cd apps/api
    .venv/Scripts/python.exe -u scripts/backfill_index_members_pit.py

可用参数：--index-codes 逗号分隔子集、--start、--end（YYYY-MM-DD）。
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta

from quant_etf_api.infra.db.base import SessionLocal
from quant_etf_api.infra.time import today_cn


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="指数 PIT 成分回填")
    parser.add_argument("--index-codes", default="", help="逗号分隔指数代码，默认全部活跃指数")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2013, 1, 1))
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=None,
        help="回填终点（默认上一完整自然月月末，避免与当前快照重叠）",
    )
    return parser.parse_args()


def main() -> int:
    """执行全量 PIT 回填并输出逐指数进度。"""
    args = _parse_args()
    end = args.end or (today_cn().replace(day=1) - timedelta(days=1))
    db = SessionLocal()
    try:
        from quant_etf_api.infra.db.models.core import BenchmarkIndexModel
        from quant_etf_api.services.index_membership_data_service import (
            IndexMembershipDataService,
        )

        if args.index_codes:
            codes = [code.strip() for code in args.index_codes.split(",") if code.strip()]
        else:
            codes = [
                row[0]
                for row in db.query(BenchmarkIndexModel.index_code)
                .filter(BenchmarkIndexModel.is_active.is_(True))
                .order_by(BenchmarkIndexModel.index_code)
                .all()
            ]
        print(f"待回填指数 {len(codes)} 个: {codes}", flush=True)
        summary: dict[str, dict] = {}
        for index_code in codes:
            started = time.perf_counter()
            try:
                result = IndexMembershipDataService(db).backfill_pit(
                    [index_code],
                    start=args.start,
                    end=end,
                )
                rows = int(result["items"].get(index_code) or 0)
                errors = len(result.get("errors") or [])
                summary[index_code] = {"rows": rows, "errors": errors}
                print(
                    f"[{index_code}] rows={rows} errors={errors} "
                    f"elapsed={time.perf_counter() - started:.1f}s",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                summary[index_code] = {"rows": 0, "errors": 1, "message": str(exc)[:200]}
                print(
                    f"[{index_code}] FAILED: {type(exc).__name__}: {exc}",
                    flush=True,
                )
        total_rows = sum(item.get("rows", 0) for item in summary.values())
        print(f"\n回填完成：{len(summary)} 个指数，共写入 {total_rows} 行", flush=True)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
