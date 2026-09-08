"""Tushare 数据源实测脚本（只读，不写库）。

逐个验证本系统每个数据源在 Tushare 侧的权限、返回结构与字段兼容性，
并与线上库已有数据抽样对比。运行方式：

    cd apps/api
    .venv/Scripts/python.exe scripts/tushare_live_probe.py
"""

from __future__ import annotations

import sys
import time
from datetime import date, timedelta

from quant_etf_api.config.settings import get_settings
from quant_etf_api.infra.clients.index_daily_common import is_ohlc_complete
from quant_etf_api.infra.clients.tushare_index import TushareIndexClient
from quant_etf_api.infra.clients.tushare_market import (
    TushareIndexBasicClient,
    TushareIndexMemberClient,
    TushareIndexValuationClient,
    TushareMacroClient,
    TushareStockClient,
)


def _section(title: str) -> None:
    """打印分区标题。"""
    print(f"\n{'=' * 12} {title} {'=' * 12}")


def _ok(text: str) -> None:
    """打印通过项。"""
    print(f"[OK] {text}")


def _warn(text: str) -> None:
    """打印告警项。"""
    print(f"[WARN] {text}")


def _fail(text: str) -> None:
    """打印失败项。"""
    print(f"[FAIL] {text}")


def _db_latest_bars(db, index_code: str):
    """查询库内某指数最新日线行。"""
    from quant_etf_api.infra.db.models.core import IndexDailyBarModel

    return (
        db.query(IndexDailyBarModel)
        .filter(IndexDailyBarModel.index_code == index_code)
        .order_by(IndexDailyBarModel.trade_date.desc())
        .first()
    )


def _db_latest_valuation(db, index_code: str):
    """查询库内某指数最新估值行。"""
    from quant_etf_api.infra.db.models.core import IndexValuationModel

    return (
        db.query(IndexValuationModel)
        .filter(IndexValuationModel.index_code == index_code)
        .order_by(IndexValuationModel.trade_date.desc())
        .first()
    )


def probe_trading_calendar() -> None:
    """实测交易日历（trade_cal，Tushare 优先加载）。"""
    _section("0. 交易日历 trade_cal")
    from quant_etf_api.infra.trading_calendar import TradingCalendar

    try:
        calendar = TradingCalendar()
        days = calendar.get_trading_days_set()
        if days is None:
            _fail("交易日历加载结果为空（两个上游均失败）")
            return
        _ok(f"交易日历加载 {len(days)} 天")
        if date(2026, 9, 8) in days:
            _ok("2026-09-08 被识别为交易日")
        else:
            _warn("2026-09-08 不在交易日集合中，请核对日历口径")
    except Exception as exc:
        _fail(f"交易日历加载异常：{exc}")


def probe_index_daily(db) -> None:
    """实测指数日线（index_daily）并抽样对比库内收盘。"""
    _section("1. 指数日线 index_daily")
    client = TushareIndexClient()
    if not client.is_configured():
        _fail("未配置 Token")
        return
    end = date(2026, 9, 8)
    start = end - timedelta(days=30)
    bars = client.fetch_index_daily(
        "000300", start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    )
    if not bars:
        _fail("000300 返回空")
        return
    _ok(f"000300 最近 30 日返回 {len(bars)} 条，OHLC 完整={is_ohlc_complete(bars)}")
    latest = bars[-1]
    print(
        f"    最新：{latest.trade_date} close={latest.close_price:.4f} "
        f"volume(手)={latest.volume:.0f} turnover(元)={latest.turnover:.2f}"
    )
    db_row = _db_latest_bars(db, "000300")
    if db_row is not None and db_row.trade_date == latest.trade_date:
        diff = abs(db_row.close_price - latest.close_price)
        _ok(f"与库内同日收盘差={diff:.4f}（库 source={db_row.source}）")
    else:
        _warn(f"库内最新 {db_row.trade_date if db_row else None} 与 Tushare 不同日，跳过对比")


def probe_index_valuation(db) -> None:
    """实测指数估值（index_dailybasic）并抽样对比库内 PE。"""
    _section("2. 指数估值 index_dailybasic")
    client = TushareIndexValuationClient()
    if not client.is_configured():
        _fail("未配置 Token")
        return
    if not client.supports("000300"):
        _fail("000300 不在支持范围")
        return
    started = time.perf_counter()
    values = client.fetch_index_valuation("000300")
    elapsed = time.perf_counter() - started
    if not values:
        _fail("000300 估值返回空")
        return
    _ok(f"000300 全量历史返回 {len(values)} 条（{elapsed:.1f}s），source=tushare")
    latest = values[-1]
    print(
        f"    最新：{latest.trade_date} pe(pe_ttm)={latest.pe} "
        f"pe_percentile={latest.pe_percentile} pb={latest.pb}"
    )
    db_row = _db_latest_valuation(db, "000300")
    if db_row is not None and db_row.trade_date == latest.trade_date:
        print(
            f"    库内同日：pe={db_row.pe} pb={db_row.pb} "
            f"percentile={db_row.pe_percentile} source={db_row.source}"
        )


def probe_macro() -> None:
    """实测宏观指标（cn_cpi / cn_pmi / shibor_lpr）。"""
    _section("3. 宏观指标 cn_cpi / cn_pmi / shibor_lpr")
    client = TushareMacroClient()
    if not client.is_configured():
        _fail("未配置 Token")
        return
    try:
        cpi = client.fetch_cpi_monthly()
        cpi = sorted(cpi, key=lambda row: (row.period_date or "", row.period))
        _ok(f"CPI 返回 {len(cpi)} 条；最新 {cpi[-1].period} 同比 {cpi[-1].value}%")
    except Exception as exc:
        _fail(f"CPI 失败：{exc}")
    try:
        pmi = client.fetch_pmi()
        pmi = sorted(pmi, key=lambda row: (row.period_date or "", row.period))
        _ok(f"PMI 返回 {len(pmi)} 条；最新 {pmi[-1].period} 制造业 {pmi[-1].value}")
    except Exception as exc:
        _fail(f"PMI 失败：{exc}")
    try:
        lpr = client.fetch_lpr()
        if lpr:
            codes = sorted({row.indicator_code for row in lpr})
            _ok(f"LPR 返回 {len(lpr)} 条，含 {codes}")
        else:
            _warn("LPR 返回空（2000 积分档位约 1 次/小时，生产链路自动回退 AkShare）")
    except Exception as exc:
        _fail(f"LPR 失败：{exc}")


def probe_index_member() -> None:
    """实测指数成分（index_weight）。"""
    _section("4. 指数成分 index_weight")
    client = TushareIndexMemberClient()
    if not client.is_configured():
        _fail("未配置 Token")
        return
    today = date(2026, 9, 8)
    for code in ("000300", "000905", "000852", "H30269"):
        rows = client.fetch_weight_snapshot(code, today)
        if rows:
            sample = next((r for r in rows if r.get("weight") is not None), None)
            text = f"权重样本 {sample}" if sample else "无权重列"
            _ok(f"{code} 成分 {len(rows)} 只，{text}")
        else:
            _warn(f"{code} Tushare 未覆盖（生产链路会回退 AkShare/Baostock）")


def probe_stock(db) -> None:
    """实测个股日线与元数据（daily / stock_basic）。"""
    _section("5. 个股日线收盘 daily")
    client = TushareStockClient()
    if not client.is_configured():
        _fail("未配置 Token")
        return
    trade_date = date(2026, 9, 8)
    rows = client.fetch_close_by_trade_date(trade_date)
    if not rows:
        _fail(f"{trade_date} 全市场收盘返回空")
    else:
        sh = sum(1 for r in rows if r["stock_code"].startswith(("60", "68", "90")))
        sz = sum(1 for r in rows if r["stock_code"].startswith(("00", "30")))
        bj = len(rows) - sh - sz
        _ok(f"{trade_date} 全市场 {len(rows)} 只（沪 {sh} / 深 {sz} / 京 {bj}）")
        from quant_etf_api.infra.db.models.industry import StockDailyCloseModel

        db_count = (
            db.query(StockDailyCloseModel)
            .filter(StockDailyCloseModel.trade_date == trade_date)
            .count()
        )
        _ok(f"库内同日收盘行数 {db_count}（AkShare 快照口径）")

    _section("6. 个股元数据 stock_basic")
    basics = client.fetch_stock_basics()
    if not basics:
        _fail("stock_basic 返回空")
        return
    active = sum(1 for row in basics if row["is_active"])
    delisted = len(basics) - active
    _ok(f"stock_basic 返回 {len(basics)} 行（上市 {active} / 退市 {delisted}）")


def probe_index_basic() -> None:
    """实测指数基本信息（index_basic，名称查询）。"""
    _section("7. 指数名称 index_basic")
    client = TushareIndexBasicClient()
    if not client.is_configured():
        _fail("未配置 Token")
        return
    name = client.fetch_index_name("000300")
    if name:
        _ok(f"000300 名称={name}")
    else:
        _warn("000300 名称查询为空（生产链路回退 AkShare）")


def probe_sw_daily() -> None:
    """实测申万行业日线权限（预期无权限，验证降级必要性）。"""
    _section("8. 申万行业日线 sw_daily")
    settings = get_settings()
    import tushare as ts

    ts.set_token(settings.tushare_token)
    try:
        ts.pro_api().sw_daily(
            ts_code="801010.SI", start_date="20260901", end_date="20260908"
        )
        _ok("sw_daily 可用（当前账号已具备权限）")
    except Exception as exc:
        _fail(f"sw_daily 无权限：{exc}；行业日线维持申万官网/AkShare 数据源")


def main() -> int:
    """执行全部数据源实测并输出结果。"""
    settings = get_settings()
    if not settings.tushare_token:
        print("未配置 QUANT_ETF_TUSHARE_TOKEN，先配置 .env 再运行")
        return 1
    from quant_etf_api.infra.db.base import SessionLocal

    db = SessionLocal()
    try:
        probe_trading_calendar()
        probe_index_daily(db)
        probe_index_valuation(db)
        probe_macro()
        probe_index_member()
        probe_stock(db)
        probe_index_basic()
        probe_sw_daily()
    finally:
        db.close()
    print("\n探测完成（只读，未写库）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
