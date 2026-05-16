from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class HealthStatus:
    """数据源健康检测结果。

    Attributes:
        healthy: 数据源是否可达
        message: 状态描述或错误信息
        latency_ms: 检测耗时（毫秒），失败时为 None
    """

    healthy: bool
    message: str
    latency_ms: float | None = None


class BaseDataClient(ABC):
    """数据源客户端抽象基类。

    所有数据源客户端（腾讯、东方财富、AkShare 等）均需继承此类，
    实现 source_name 属性和 health_check 方法，以提供统一的日志、健康检测和调用规范。
    """

    @property
    @abstractmethod
    def source_name(self) -> str:
        """数据源唯一标识，如 'tencent'、'eastmoney'、'akshare_index'。

        用于日志标识和 source_payload_log 表中的 source_name 字段。
        """
        ...

    def __init__(self) -> None:
        self._logger = logging.getLogger(f"client.{self.source_name}")

    def health_check(self) -> HealthStatus:
        """快速连通性检测，子类应按需覆盖以提供真实的可达性验证。

        Returns:
            HealthStatus，默认返回健康（未实现真实检测）。
        """
        return HealthStatus(healthy=True, message="未实现健康检查")

    def _log_request(self, endpoint: str, params: dict[str, Any] | None = None) -> None:
        """记录数据源请求（DEBUG 级别）。

        Args:
            endpoint: 调用的接口名称
            params: 请求参数
        """
        self._logger.debug(
            "请求 %s endpoint=%s params=%s",
            self.source_name,
            endpoint,
            params,
        )

    def _log_response(self, endpoint: str, record_count: int, elapsed_ms: float) -> None:
        """记录数据源响应（INFO 级别）。

        Args:
            endpoint: 调用的接口名称
            record_count: 返回记录数
            elapsed_ms: 耗时（毫秒）
        """
        self._logger.info(
            "完成 %s endpoint=%s records=%d elapsed=%.0fms",
            self.source_name,
            endpoint,
            record_count,
            elapsed_ms,
        )

    def _log_error(self, endpoint: str, error: Exception, elapsed_ms: float) -> None:
        """记录数据源错误（WARNING 级别）。

        Args:
            endpoint: 调用的接口名称
            error: 异常对象
            elapsed_ms: 耗时（毫秒）
        """
        self._logger.warning(
            "失败 %s endpoint=%s error=%s elapsed=%.0fms",
            self.source_name,
            endpoint,
            error,
            elapsed_ms,
        )
