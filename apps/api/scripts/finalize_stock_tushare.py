"""个股 Tushare 回填收尾：等待回填、补齐缺口、校验后执行旧数据清理。

该脚本供长时后台回填使用：
1. 可选等待指定 PID 退出；
2. 对 2013-01-01 起的所有交易日做最终缺口补齐；
3. 只有所有数据集都覆盖、且未补齐的日期均为上游天然空数据时才执行清理；
4. 默认只预演清理，必须显式传入 --execute-cleanup 才真正删除。

运行方式（示例）：
    .venv/Scripts/python.exe scripts/finalize_stock_tushare.py \
        --wait-pid 32040 --execute-cleanup
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import date

from quant_etf_api.infra.db.base import SessionLocal
from quant_etf_api.services.stock_data_service import StockDataService


def _wait_for_pid(pid: int) -> None:
    """等待指定进程退出。

    Args:
        pid: 后台回填进程 PID。
    """
    try:
        import psutil  # noqa: PLC0415
    except ImportError:
        # psutil 不可用时退化为 tasklist 轮询，避免阻塞收尾流程
        while True:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                check=False,
            )
            if str(pid) not in result.stdout:
                return
            time.sleep(30)
    process = psutil.Process(pid)
    print(f"等待回填进程 {pid} 退出...", flush=True)
    process.wait()
    print(f"回填进程 {pid} 已退出，开始最终校验。", flush=True)


def _parse_date(value: str) -> date:
    """把 YYYYMMDD 字符串解析为 date。"""
    return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))


def _json_default(value: object) -> str:
    """把 date 等不可序列化对象转为 ISO 字符串。"""
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def main() -> int:
    """执行回填收尾与可选清理。

    Returns:
        0 表示成功；2 表示存在未补齐日期，拒绝清理；1 表示运行异常。
    """
    parser = argparse.ArgumentParser(description="个股 Tushare 回填收尾与清理")
    parser.add_argument("--start", default="20130101", help="回填起点 YYYYMMDD")
    parser.add_argument("--end", default=None, help="回填终点 YYYYMMDD，默认最近交易日")
    parser.add_argument("--wait-pid", type=int, default=None, help="等待该 PID 退出后再校验")
    parser.add_argument(
        "--execute-cleanup",
        action="store_true",
        help="校验通过后真正执行清理；缺省仅预演",
    )
    args = parser.parse_args()

    if args.wait_pid is not None:
        _wait_for_pid(args.wait_pid)

    db = SessionLocal()
    try:
        service = StockDataService(db)
        start = _parse_date(args.start)
        end = _parse_date(args.end) if args.end else service.latest_trading_day()
        datasets = list(service._ALL_DATASETS)

        print(f"校验区间 {start} ~ {end}", flush=True)
        missing = service.missing_trade_dates(start, end, datasets=datasets)
        missing_keys = sorted(key for key, values in missing.items() if values)
        upstream_empty: dict[str, set[date]] = {key: set() for key in datasets}
        if missing_keys:
            print(
                "发现缺口，先执行最终补齐: "
                + ", ".join(f"{key}={len(missing[key])}" for key in missing_keys),
                flush=True,
            )
            result = service.sync_range(start, end, datasets=missing_keys)
            for key, values in result.get("upstream_empty", {}).items():
                upstream_empty.setdefault(key, set()).update(values)
            missing = service.missing_trade_dates(start, end, datasets=datasets)

        blockers = {
            key: [
                trade_date
                for trade_date in values
                if trade_date not in upstream_empty.get(key, set())
            ]
            for key, values in missing.items()
        }
        blockers = {key: values for key, values in blockers.items() if values}
        empty_summary = {
            key: sorted(values) for key, values in upstream_empty.items() if values
        }
        if blockers:
            summary = {
                "status": "blocked",
                "reason": "存在未补齐且非上游空数据的交易日，已跳过清理",
                "blockers": blockers,
                "upstream_empty": empty_summary,
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))
            return 2

        dry_run = not args.execute_cleanup
        legacy = service.prune_before(start, dry_run=dry_run)
        scoped = service.prune_non_tushare(start, dry_run=dry_run)
        summary = {
            "status": "success",
            "cleanup_executed": args.execute_cleanup,
            "start": start,
            "end": end,
            "upstream_empty": empty_summary,
            "legacy_cleanup": legacy,
            "scope_cleanup": scoped,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {"status": "failed", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
                indent=2,
            ),
            flush=True,
        )
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
