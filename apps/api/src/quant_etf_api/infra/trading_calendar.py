"""A 股交易日历模块。

严格口径（C1）：只接受**真实日历**，数据来源按优先级为
1. 上游数据源：Tushare Pro（trade_cal）优先，未配置 Token 或失败时回退 AkShare；
2. 本地 `trading_calendar` 表快照（由数据管理页同步写入）。

两者都拿不到有效日历时，`is_trading_day` / `latest_trading_day` /
`next_trading_day` / `trading_days_between` 一律抛
``TradingCalendarUnavailableError``，**不再降级为周末判断**——按星期猜交易日
会在长假后错位调仓日，使同一策略配置产生两套不可复现的结果。

负缓存：上游加载失败时只缓存 60 秒（`_NEGATIVE_CACHE_TTL`），避免一次瞬时
网络故障把日历冻结一整天。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.config.settings import get_settings
from quant_etf_api.domain.common.trading_calendar import (
    TradingCalendarLike,
    TradingCalendarUnavailableError,
)
from quant_etf_api.infra.time import today_cn

logger = logging.getLogger(__name__)

__all__ = [
    "DbTradingCalendar",
    "TradingCalendar",
    "TradingCalendarUnavailableError",
    "load_db_trading_days",
    "resolve_trading_calendar",
]

# 缓存 TTL：1 天（成功）
_CACHE_TTL = timedelta(days=1)
# 负缓存 TTL：60 秒（失败），避免瞬时故障冻结日历一整天
_NEGATIVE_CACHE_TTL = timedelta(seconds=60)

# 进程级缓存
_cache: dict[str, Any] = {
    "trading_days": None,  # set[date] | None
    "loaded_at": None,  # datetime | None
    "source": None,  # "tushare" | "akshare" | None
}

# 缓存加载锁：防止多线程并发调用 akshare → mini_racer（V8），
# V8 在 Windows 上非线程安全，并发初始化会触发 partition_address_space 崩溃。
_cache_lock = threading.Lock()


def _load_from_akshare() -> set[date] | None:
    """从 AkShare 加载 A 股交易日历。

    Returns:
        交易日集合，加载失败时返回 None。
    """
    try:
        import akshare as ak

        df = ak.tool_trade_date_hist_sina()
        if df is None or df.empty:
            logger.warning("AkShare 交易日历返回空数据")
            return None

        # trade_date 列格式为 'YYYY-MM-DD'
        trading_days: set[date] = set()
        for _, row in df.iterrows():
            try:
                td = datetime.strptime(str(row["trade_date"]), "%Y-%m-%d").date()
                trading_days.add(td)
            except (ValueError, KeyError):
                continue

        if not trading_days:
            logger.warning("AkShare 交易日历解析后为空")
            return None

        logger.info("从 AkShare 加载交易日历成功，共 %d 个交易日", len(trading_days))
        return trading_days
    except Exception:
        logger.warning("AkShare 交易日历加载失败", exc_info=True)
        return None


def _load_from_tushare() -> set[date] | None:
    """从 Tushare Pro 加载 A 股交易日历（上交所口径）。

    按 5 年窗口分页拉取，规避单次调用行数上限；返回开市日集合。

    Returns:
        交易日集合，未配置 Token 或加载失败时返回 None。
    """
    token = get_settings().tushare_token
    if not token:
        return None
    try:
        import tushare as ts

        ts.set_token(token)
        pro = ts.pro_api()
        current_year = today_cn().year
        trading_days: set[date] = set()
        for year_start in range(1990, current_year + 1, 5):
            year_end = min(year_start + 4, current_year)
            df = pro.trade_cal(
                exchange="SSE",
                start_date=f"{year_start}0101",
                end_date=f"{year_end}1231",
                is_open="1",
                fields="cal_date",
            )
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                try:
                    trading_days.add(datetime.strptime(str(row["cal_date"]), "%Y%m%d").date())
                except (ValueError, KeyError):
                    continue
            # 节流：避免触发接口频次限制
            time.sleep(0.2)
        if not trading_days:
            logger.warning("Tushare 交易日历解析后为空，回退到 AkShare")
            return None
        logger.info("从 Tushare 加载交易日历成功，共 %d 个交易日", len(trading_days))
        return trading_days
    except Exception:
        logger.warning("Tushare 交易日历加载失败，回退到 AkShare", exc_info=True)
        return None


def _load_from_upstream() -> tuple[set[date] | None, str | None]:
    """按"Tushare 优先、AkShare 兜底"加载交易日历。

    Returns:
        (交易日集合, 来源名) 元组；两者均失败时返回 (None, None)。
    """
    days = _load_from_tushare()
    if days:
        return days, "tushare"
    days = _load_from_akshare()
    if days:
        return days, "akshare"
    return None, None


def _get_cached_trading_days() -> set[date] | None:
    """获取缓存的交易日集合，缓存过期时重新加载。

    使用双重检查锁定（DCL）模式：先无锁检查缓存命中（快速路径），
    缓存未命中时加锁并再次检查，避免多线程并发调用 akshare。
    失败结果使用 60 秒的短负缓存，避免瞬时故障长时间阻断。

    Returns:
        交易日集合，加载失败时返回 None。
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cached = _cache["trading_days"]
    loaded_at = _cache["loaded_at"]

    # 快速路径：缓存有效，无需加锁
    if loaded_at is not None:
        ttl = _CACHE_TTL if cached is not None else _NEGATIVE_CACHE_TTL
        if now - loaded_at < ttl:
            return cached

    # 慢速路径：缓存过期或未初始化，加锁加载
    with _cache_lock:
        # 再次检查：可能在等待锁期间已被其他线程填充
        loaded_at = _cache["loaded_at"]
        if loaded_at is not None:
            ttl = _CACHE_TTL if _cache["trading_days"] is not None else _NEGATIVE_CACHE_TTL
            if now - loaded_at < ttl:
                return _cache["trading_days"]

        trading_days, source = _load_from_upstream()
        _cache["trading_days"] = trading_days
        _cache["loaded_at"] = now
        _cache["source"] = source
        return trading_days


def _refresh_cache() -> set[date] | None:
    """强制刷新缓存。"""
    _cache["trading_days"] = None
    _cache["loaded_at"] = None
    _cache["source"] = None
    return _get_cached_trading_days()


def _unavailable(detail: str) -> TradingCalendarUnavailableError:
    """构造统一的"日历不可用"异常（含修复指引）。"""
    return TradingCalendarUnavailableError(
        f"交易日历不可用：{detail}。"
        "请检查网络与数据源（Tushare/AkShare），并在「数据管理」页同步交易日历数据；"
        "系统不允许按星期近似判断交易日"
    )


class TradingCalendar:
    """A 股交易日历（严格口径）。

    提供交易日判断、最近交易日查询、交易日区间生成等功能。
    数据来源：Tushare Pro trade_cal（优先）/ AkShare（兜底），缓存 TTL=1 天。
    上游不可用时**抛错**，不回退周末判断；需要离线兜底时请使用
    ``resolve_trading_calendar()``（回退到本地 trading_calendar 表）。

    用法::

        cal = TradingCalendar()
        cal.is_trading_day(today_cn())
        cal.latest_trading_day()
    """

    @property
    def source(self) -> str | None:
        """返回本次缓存的日历来源（tushare/akshare），未知时为 None。"""
        return _cache["source"]

    def is_trading_day(self, target: date) -> bool:
        """判断指定日期是否为 A 股交易日。

        Args:
            target: 目标日期。

        Returns:
            True 表示是交易日。

        Raises:
            TradingCalendarUnavailableError: 上游数据源不可用时抛出。
        """
        trading_days = _get_cached_trading_days()
        if trading_days is None:
            raise _unavailable("Tushare 与 AkShare 均加载失败")
        return target in trading_days

    def latest_trading_day(self, reference: date | None = None) -> date:
        """获取指定日期（含）之前最近的交易日。

        Args:
            reference: 参考日期，默认今天。

        Returns:
            最近的交易日日期。

        Raises:
            TradingCalendarUnavailableError: 上游数据源不可用，或回溯 30 天未找到交易日时抛出。
        """
        target = reference or today_cn()
        trading_days = _get_cached_trading_days()
        if trading_days is None:
            raise _unavailable("Tushare 与 AkShare 均加载失败")

        # 从参考日期向前查找
        for i in range(30):  # 最多回溯 30 天（覆盖最长节假日）
            d = target - timedelta(days=i)
            if d in trading_days:
                return d
        raise _unavailable(f"从 {target} 回溯 30 天未找到交易日（上游日历可能不完整）")

    def next_trading_day(self, reference: date | None = None) -> date:
        """获取指定日期（不含）之后最近的下一个交易日。

        Args:
            reference: 参考日期，默认今天。

        Returns:
            下一个交易日日期。

        Raises:
            TradingCalendarUnavailableError: 上游数据源不可用，或向前 30 天未找到交易日时抛出。
        """
        target = (reference or today_cn()) + timedelta(days=1)
        trading_days = _get_cached_trading_days()
        if trading_days is None:
            raise _unavailable("Tushare 与 AkShare 均加载失败")

        for i in range(30):
            d = target + timedelta(days=i)
            if d in trading_days:
                return d
        raise _unavailable(f"从 {target} 向前 30 天未找到交易日（上游日历可能不完整）")

    def trading_days_between(self, start: date, end: date) -> list[date]:
        """获取两个日期之间（含起止）的所有交易日。

        Args:
            start: 起始日期。
            end: 结束日期。

        Returns:
            交易日列表，按日期升序排列。

        Raises:
            TradingCalendarUnavailableError: 上游数据源不可用时抛出。
        """
        trading_days = _get_cached_trading_days()
        if trading_days is None:
            raise _unavailable("Tushare 与 AkShare 均加载失败")
        return sorted(d for d in trading_days if start <= d <= end)

    def refresh(self) -> None:
        """强制刷新交易日历缓存。"""
        _refresh_cache()

    def get_trading_days_set(self) -> set[date] | None:
        """获取原始交易日集合（显式探测 API，不抛错）。

        本方法保留"返回 None"语义，供 ``resolve_trading_calendar()`` 判定上游
        可用性；需要"必须拿到日历"语义的调用方请使用 ``require_trading_days()``。

        Returns:
            交易日集合；上游不可用时返回 None。
        """
        return _get_cached_trading_days()

    def require_trading_days(self) -> set[date]:
        """获取交易日集合，不可用时抛错（严格口径 C1）。

        供"必须有日历才能继续"的调用方使用，避免各自写
        ``days = get_trading_days_set(); if days is None: raise ...``。

        Returns:
            交易日集合。

        Raises:
            TradingCalendarUnavailableError: 上游数据源不可用时抛出。
        """
        trading_days = _get_cached_trading_days()
        if trading_days is None:
            raise _unavailable("Tushare 与 AkShare 均加载失败")
        return trading_days


class DbTradingCalendar:
    """基于本地 `trading_calendar` 表快照的交易日历（无网络依赖）。

    用于上游数据源不可用时的确定性兜底：快照由数据管理页从上游同步而来，
    因此仍然是**真实日历**，而不是按星期推算的近似值。
    """

    #: 来源标识，写入回测口径指纹
    source = "database"

    def __init__(self, trading_days: set[date]) -> None:
        """初始化本地日历。

        Args:
            trading_days: 交易日集合（来自 trading_calendar 表）。

        Raises:
            ValueError: 交易日集合为空时抛出。
        """
        if not trading_days:
            raise ValueError("本地交易日历为空，无法构建日历快照实现")
        self._days = trading_days

    def is_trading_day(self, day: date) -> bool:
        """判断指定日期是否为 A 股交易日。

        Args:
            day: 待判断的日期。

        Returns:
            True 表示交易日。
        """
        return day in self._days

    def latest_trading_day(self, day: date) -> date:
        """返回不晚于指定日期的最近交易日。

        Args:
            day: 参考日期。

        Returns:
            最近交易日。

        Raises:
            TradingCalendarUnavailableError: 回溯 30 天仍未找到交易日时抛出。
        """
        for i in range(30):
            candidate = day - timedelta(days=i)
            if candidate in self._days:
                return candidate
        raise _unavailable(f"本地日历快照中从 {day} 回溯 30 天未找到交易日")

    def coverage(self) -> tuple[date, date]:
        """返回本地日历覆盖范围（最早/最晚交易日）。"""
        return min(self._days), max(self._days)


def load_db_trading_days(db: Session) -> set[date] | None:
    """读取本地 trading_calendar 表的交易日集合。

    Args:
        db: SQLAlchemy 同步 Session。

    Returns:
        交易日集合；表为空时返回 None。
    """
    from quant_etf_api.infra.db.models.core import TradingCalendarModel

    try:
        rows = (
            db.query(TradingCalendarModel.trade_date)
            .filter(TradingCalendarModel.is_trading_day.is_(True))
            .all()
        )
    except Exception:
        logger.warning("读取本地交易日历表失败", exc_info=True)
        return None
    days = {row[0] for row in rows}
    return days or None


def resolve_trading_calendar(
    db: Session,
    *,
    required_range: tuple[date, date] | None = None,
) -> tuple[TradingCalendarLike, str]:
    """严格解析交易日历（C1）。

    优先级：上游数据源 → 本地 `trading_calendar` 表快照 → 抛错。
    不存在任何"按星期近似"的降级路径。

    Args:
        db: SQLAlchemy 同步 Session（用于读取本地日历表）。
        required_range: 需要覆盖的日期区间（含起止）；本地快照未覆盖该区间时视为不可用。

    Returns:
        (交易日历, 来源标识) 元组，来源为 "upstream" 或 "database"。

    Raises:
        TradingCalendarUnavailableError: 上游与本地表均无法提供有效日历时抛出。
    """
    upstream = TradingCalendar()
    if upstream.get_trading_days_set():
        return upstream, "upstream"

    db_days = load_db_trading_days(db)
    if db_days:
        if required_range is not None:
            start, end = required_range
            if not any(start <= day <= end for day in db_days):
                raise _unavailable(f"本地 trading_calendar 表未覆盖所需区间 {start}~{end}")
        return DbTradingCalendar(db_days), "database"

    raise _unavailable("Tushare 与 AkShare 均加载失败，且本地 trading_calendar 表无数据")
