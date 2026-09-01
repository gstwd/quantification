from __future__ import annotations

import os
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Generator, Optional

import pandas as pd

from quant_etf_api.infra.clients.base import BaseDataClient, HealthStatus
from quant_etf_api.infra.clients.index_daily_common import (
    IndexDailyBar,
    _build_index_bars,
    _incremental_start_date,
    index_code_market_prefix,
)

# 连接失败后的冷却时间（秒）：短期内不再尝试该数据源，避免拖慢降级链
_PYTDX_CONNECTION_COOLDOWN_SECONDS = 15.0

# pytdx get_index_bars 单次最多返回 800 根 K 线，全量历史需按 start 偏移分页
_PYTDX_MAX_BARS_PER_CALL = 800


def _parse_hosts_from_env() -> Optional[list[tuple[str, int]]]:
    """从环境变量构建通达信服务器列表。

    优先级：
    1. PYTDX_SERVERS：逗号分隔 "ip:port,ip:port"；
    2. PYTDX_HOST + PYTDX_PORT：单个服务器；
    3. 均未配置时返回 None（调用方使用内置 DEFAULT_HOSTS）。

    Returns:
        服务器列表 [(host, port), ...]；未配置时返回 None。
    """
    servers = os.getenv("PYTDX_SERVERS", "").strip()
    if servers:
        result: list[tuple[str, int]] = []
        for part in servers.split(","):
            part = part.strip()
            if ":" in part:
                host, port_str = part.rsplit(":", 1)
                host, port_str = host.strip(), port_str.strip()
                if host and port_str:
                    try:
                        result.append((host, int(port_str)))
                    except ValueError:
                        pass
        if result:
            return result

    host = os.getenv("PYTDX_HOST", "").strip()
    port_str = os.getenv("PYTDX_PORT", "").strip()
    if host and port_str:
        try:
            return [(host, int(port_str))]
        except ValueError:
            return None

    return None


class PytdxIndexClient(BaseDataClient):
    """通达信指数日线客户端（基于 pytdx SDK）。

    仅使用 pytdx 的指数日 K 线功能（TdxHq_API.get_index_bars，category=9），
    不涉及个股、分时、财务等其余方法。数据来自通达信行情服务器：
    - 免费直连、无配额限制，覆盖上证/深证主流指数；
    - 成交量单位为手、成交额单位为元，与 index_daily_bar 表口径一致，无需换算；
    - 单次最多返回 800 根，本客户端按 start 偏移自动分页拉取全量/增量区间。

    连接策略参考 data_provider 的实现精髓：多服务器轮询 + 失败冷却，
    但仅保留指数日线所需的最小实现。
    """

    source_name = "pytdx_index"

    # 默认通达信行情服务器列表（指数行情与个股共用同一组服务器）
    DEFAULT_HOSTS: list[tuple[str, int]] = [
        ("119.147.212.81", 7709),  # 深圳
        ("112.74.214.43", 7727),  # 深圳
        ("221.231.141.60", 7709),  # 上海
        ("101.227.73.20", 7709),  # 上海
        ("101.227.77.254", 7709),  # 上海
        ("14.215.128.18", 7709),  # 广州
        ("59.173.18.140", 7709),  # 武汉
        ("180.153.39.51", 7709),  # 杭州
    ]

    def __init__(self, hosts: Optional[list[tuple[str, int]]] = None) -> None:
        """初始化通达信指数客户端。

        Args:
            hosts: 服务器列表 [(host, port), ...]。未传入时优先使用环境变量
                PYTDX_SERVERS / PYTDX_HOST+PYTDX_PORT，否则使用内置 DEFAULT_HOSTS。
        """
        super().__init__()
        if hosts is not None:
            self._hosts = hosts
        else:
            env_hosts = _parse_hosts_from_env()
            self._hosts = env_hosts if env_hosts else self.DEFAULT_HOSTS
        self._current_host_idx = 0
        self._unavailable_until = 0.0
        self._last_unavailable_reason = ""

    def _is_in_connection_cooldown(self) -> bool:
        """判断当前是否处于连接冷却期。"""
        return time.time() < self._unavailable_until

    def _mark_connection_cooldown(self, reason: str) -> None:
        """标记连接冷却并记录原因。"""
        self._unavailable_until = time.time() + _PYTDX_CONNECTION_COOLDOWN_SECONDS
        self._last_unavailable_reason = reason
        self._logger.info(
            "Pytdx 连接失败，进入冷却 %.0fs: %s",
            _PYTDX_CONNECTION_COOLDOWN_SECONDS,
            reason,
        )

    @staticmethod
    def _get_tdx_api():
        """延迟加载 pytdx 模块，未安装时返回 None。"""
        try:
            from pytdx.hq import TdxHq_API

            return TdxHq_API
        except ImportError:
            return None

    @contextmanager
    def _pytdx_session(self) -> Generator:
        """Pytdx 连接上下文管理器：进入自动连接、退出自动断开。

        Yields:
            已连接的 TdxHq_API 实例。

        Raises:
            RuntimeError: pytdx 未安装或所有服务器均连接失败。
        """
        if self._is_in_connection_cooldown():
            raise RuntimeError(
                f"Pytdx 暂时不可用: {self._last_unavailable_reason or 'connection cooldown'}"
            )

        TdxHq_API = self._get_tdx_api()
        if TdxHq_API is None:
            raise RuntimeError("pytdx 库未安装")

        api = TdxHq_API()
        connected = False
        try:
            # 轮询所有服务器直到连接成功（从上一次成功的服务器开始）
            for i in range(len(self._hosts)):
                host_idx = (self._current_host_idx + i) % len(self._hosts)
                host, port = self._hosts[host_idx]
                try:
                    if api.connect(host, port, time_out=5):
                        connected = True
                        self._current_host_idx = host_idx
                        self._logger.debug("Pytdx 连接成功: %s:%s", host, port)
                        break
                except Exception as e:
                    self._logger.debug("Pytdx 连接 %s:%s 失败: %s", host, port, e)
                    continue
            if not connected:
                self._mark_connection_cooldown("Pytdx 无法连接任何服务器")
                raise RuntimeError("Pytdx 无法连接任何服务器")
            yield api
        finally:
            try:
                api.disconnect()
            except Exception as e:
                self._logger.warning("Pytdx 断开连接时出错: %s", e)

    def _fetch_index_daily(
        self,
        index_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[IndexDailyBar]:
        """通过 pytdx 拉取指数日线（通达信源）。

        按 800 根/次分页拉取（start 从 0 递增，0 表示最新），直到覆盖
        start_date 之前的历史或返回空数据，随后统一本地按日期过滤。

        Args:
            index_code: 指数代码，如 000300。
            start_date: 起始日 'YYYYMMDD'，本地过滤下限（含），None 表示最早。
            end_date: 结束日 'YYYYMMDD'，本地过滤上限（含），None 表示最新。

        Returns:
            按日期升序排列的日线数据列表。
        """
        endpoint = "get_index_bars"
        market = 1 if index_code_market_prefix(index_code) == "sh" else 0
        self._log_request(endpoint, {"index_code": index_code, "market": market})
        start = time.perf_counter()
        start_d = datetime.strptime(start_date, "%Y%m%d").date() if start_date else None
        try:
            with self._pytdx_session() as api:
                chunks: list[list[dict]] = []
                offset = 0
                while True:
                    data = api.get_index_bars(
                        category=9,
                        market=market,
                        code=index_code,
                        start=offset,
                        count=_PYTDX_MAX_BARS_PER_CALL,
                    )
                    if not data:
                        break
                    chunks.append(data)
                    if len(data) < _PYTDX_MAX_BARS_PER_CALL:
                        # 已到达最早历史，无需继续分页
                        break
                    if start_d and self._chunk_oldest_date(data) <= start_d:
                        # 已覆盖请求起点，无需继续分页
                        break
                    offset += len(data)

            if not chunks:
                elapsed = (time.perf_counter() - start) * 1000
                self._log_response(endpoint, 0, elapsed)
                return []

            rows = [row for chunk in chunks for row in chunk]
            df = pd.DataFrame(rows)
            # pytdx 按最新在前返回，统一转 datetime 后升序，保证涨跌幅计算顺序正确
            df["datetime"] = pd.to_datetime(df["datetime"])
            df = df.sort_values("datetime").reset_index(drop=True)
            bars = _build_index_bars(
                df,
                date_col="datetime",
                open_col="open",
                close_col="close",
                high_col="high",
                low_col="low",
                volume_col="vol",
                amount_col="amount",
                start_date=start_date,
                end_date=end_date,
            )
            elapsed = (time.perf_counter() - start) * 1000
            self._log_response(endpoint, len(bars), elapsed)
            return bars
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            self._log_error(endpoint, e, elapsed)
            raise

    @staticmethod
    def _chunk_oldest_date(data: list[dict]) -> date:
        """返回当前分块中最早一根 K 线的日期（判断分页是否已覆盖起点）。

        Args:
            data: pytdx 返回的 K 线字典列表（按最新在前排序）。

        Returns:
            分块中最旧的日期。
        """
        oldest = pd.to_datetime(data[-1].get("datetime"))
        if hasattr(oldest, "date"):
            return oldest.date()
        return date.fromisoformat(str(oldest)[:10])

    def fetch_index_daily(
        self,
        index_code: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[IndexDailyBar]:
        """拉取指数日线 OHLCV 数据（pytdx 单源入口）。

        Args:
            index_code: 指数代码，如 000300。
            start_date: 起始日 'YYYYMMDD'，None 表示最早。
            end_date: 结束日 'YYYYMMDD'，None 表示最新。

        Returns:
            按日期升序排列的日线数据列表。
        """
        return self._fetch_index_daily(index_code, start_date, end_date)

    def fetch_index_daily_since(self, index_code: str, since_date: date) -> list[IndexDailyBar]:
        """增量拉取指数日线：仅拉取 since_date 之前的缓冲窗口到最新。

        Args:
            index_code: 指数代码，如 000300。
            since_date: 起始日期（不含，即仅拉取该日之后的数据）。

        Returns:
            按日期升序排列的日线数据列表（含缓冲窗口内的历史行）。
        """
        start = _incremental_start_date(since_date)
        return self.fetch_index_daily(index_code, start_date=start.strftime("%Y%m%d"))

    def health_check(self) -> HealthStatus:
        """通过拉取上证指数最近一周数据检测连通性。"""
        try:
            start = time.perf_counter()
            start_date = (date.today() - timedelta(days=7)).strftime("%Y%m%d")
            bars = self.fetch_index_daily("000001", start_date=start_date)
            elapsed = (time.perf_counter() - start) * 1000
            ok = len(bars) > 0
            return HealthStatus(
                healthy=ok,
                message="pytdx 指数接口可达" if ok else "pytdx 指数接口返回空数据",
                latency_ms=elapsed,
            )
        except Exception as e:
            return HealthStatus(healthy=False, message=str(e))
