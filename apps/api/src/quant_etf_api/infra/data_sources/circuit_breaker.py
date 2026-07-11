"""熔断器 — 管理数据源的健康状态和自动恢复。

实现 CLOSED → OPEN → HALF_OPEN 三态状态机，参考 DSA 项目的
CircuitBreaker 设计，适配本项目的代码规范。
"""

from __future__ import annotations

import threading
import time
from typing import Any


class CircuitBreaker:
    """熔断器，管理数据源的健康状态。

    状态机：
        CLOSED（正常）—连续失败 N 次 → OPEN（熔断）
        OPEN（熔断）—冷却到期 → HALF_OPEN（半开，试探）
        HALF_OPEN —试探成功 → CLOSED
        HALF_OPEN —试探失败 → OPEN

    Attributes:
        failure_threshold: 连续失败次数阈值，达到后进入 OPEN 状态。
        cooldown_seconds: 熔断冷却时间（秒），OPEN 状态持续此时间后进入 HALF_OPEN。
        half_open_max_calls: HALF_OPEN 状态下允许的最大试探次数。
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 300.0,
        half_open_max_calls: int = 1,
    ) -> None:
        """初始化熔断器。

        Args:
            failure_threshold: 连续失败次数阈值（默认 3）。
            cooldown_seconds: 冷却时间秒数（默认 300 秒）。
            half_open_max_calls: HALF_OPEN 状态下最大试探次数（默认 1）。
        """
        self._failure_threshold = max(1, failure_threshold)
        self._cooldown_seconds = max(0.01, cooldown_seconds)
        self._half_open_max_calls = max(1, half_open_max_calls)

        # 内部状态：{key: {"state", "failures", "last_failure_ts", "half_open_calls"}}
        self._states: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def is_available(self, key: str) -> bool:
        """检查指定 key 对应的数据源是否可用（可以尝试调用）。

        Args:
            key: 数据源标识（如 "index_daily:cn:akshare_index"）。

        Returns:
            True 表示可以尝试调用，False 表示已熔断。
        """
        with self._lock:
            state = self._states.get(key)
            if state is None:
                # 首次访问，初始化为 CLOSED
                self._states[key] = {
                    "state": self.CLOSED,
                    "failures": 0,
                    "last_failure_ts": 0.0,
                    "half_open_calls": 0,
                }
                return True

            current_state = state["state"]

            if current_state == self.CLOSED:
                return True

            if current_state == self.OPEN:
                elapsed = time.time() - state["last_failure_ts"]
                if elapsed >= self._cooldown_seconds:
                    # 冷却到期，转为 HALF_OPEN
                    state["state"] = self.HALF_OPEN
                    state["half_open_calls"] = 0
                    return True
                return False

            if current_state == self.HALF_OPEN:
                if state["half_open_calls"] < self._half_open_max_calls:
                    state["half_open_calls"] += 1
                    return True
                return False

            return True

    def record_success(self, key: str) -> None:
        """记录一次成功调用，将指定 key 的状态重置为 CLOSED。

        Args:
            key: 数据源标识。
        """
        with self._lock:
            self._ensure_key(key)
            self._states[key]["state"] = self.CLOSED
            self._states[key]["failures"] = 0
            self._states[key]["half_open_calls"] = 0

    def record_failure(self, key: str, error: str = "") -> None:
        """记录一次失败调用，可能触发熔断。

        Args:
            key: 数据源标识。
            error: 错误描述（仅用于调试）。
        """
        with self._lock:
            self._ensure_key(key)
            state = self._states[key]
            state["failures"] += 1
            state["last_failure_ts"] = time.time()

            if state["state"] == self.HALF_OPEN:
                # HALF_OPEN 中试探失败，直接回到 OPEN
                state["state"] = self.OPEN
            elif state["failures"] >= self._failure_threshold:
                state["state"] = self.OPEN

    def reset(self, key: str | None = None) -> None:
        """重置指定 key（或全部 key）的熔断状态。

        Args:
            key: 要重置的数据源标识，None 表示重置全部。
        """
        with self._lock:
            if key is None:
                self._states.clear()
            elif key in self._states:
                del self._states[key]

    def get_status(self, key: str) -> dict[str, Any]:
        """获取指定 key 的熔断状态详情（用于诊断）。

        Args:
            key: 数据源标识。

        Returns:
            包含 state、failures、last_failure_ts 的字典。
        """
        with self._lock:
            self._ensure_key(key)
            s = self._states[key]
            return {
                "key": key,
                "state": s["state"],
                "failures": s["failures"],
                "last_failure_ts": s["last_failure_ts"],
                "cooldown_remaining": max(
                    0.0,
                    self._cooldown_seconds - (time.time() - s["last_failure_ts"]),
                )
                if s["state"] == self.OPEN
                else 0.0,
            }

    def _ensure_key(self, key: str) -> None:
        """确保 key 存在初始化状态（需在持有锁时调用）。"""
        if key not in self._states:
            self._states[key] = {
                "state": self.CLOSED,
                "failures": 0,
                "last_failure_ts": 0.0,
                "half_open_calls": 0,
            }
