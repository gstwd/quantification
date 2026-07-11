"""CircuitBreaker 单元测试。"""

import time
import pytest

from quant_etf_api.infra.data_sources.circuit_breaker import CircuitBreaker


class TestCircuitBreakerInit:
    """测试 CircuitBreaker 初始化。"""

    def test_default_params(self):
        cb = CircuitBreaker()
        assert cb._failure_threshold == 3
        assert cb._cooldown_seconds == 300.0
        assert cb._half_open_max_calls == 1

    def test_custom_params(self):
        cb = CircuitBreaker(
            failure_threshold=5,
            cooldown_seconds=60.0,
            half_open_max_calls=2,
        )
        assert cb._failure_threshold == 5
        assert cb._cooldown_seconds == 60.0
        assert cb._half_open_max_calls == 2

    def test_params_clamped(self):
        """负值参数应被限制到最小值。"""
        cb = CircuitBreaker(
            failure_threshold=0,
            cooldown_seconds=-5.0,
            half_open_max_calls=0,
        )
        assert cb._failure_threshold == 1
        assert cb._cooldown_seconds == 0.01  # min clamped
        assert cb._half_open_max_calls == 1


class TestCircuitBreakerStateTransitions:
    """测试 CircuitBreaker 状态机转换。"""

    def test_initial_state_is_closed(self):
        cb = CircuitBreaker()
        assert cb.is_available("test_key")

    def test_from_closed_to_open(self):
        """连续失败 N 次后应进入 OPEN 状态。"""
        cb = CircuitBreaker(failure_threshold=3)

        # 前 2 次仍可用
        cb.record_failure("k")
        assert cb.is_available("k")
        cb.record_failure("k")
        assert cb.is_available("k")

        # 第 3 次失败后进入 OPEN
        cb.record_failure("k")
        assert not cb.is_available("k")

    def test_from_open_to_half_open_after_cooldown(self):
        """冷却到期后应从 OPEN 进入 HALF_OPEN。"""
        cb = CircuitBreaker(
            failure_threshold=2,
            cooldown_seconds=0.1,  # 短冷却用于测试
        )

        # 触发熔断
        cb.record_failure("k")
        cb.record_failure("k")
        assert not cb.is_available("k")

        # 等待冷却
        time.sleep(0.15)
        assert cb.is_available("k")  # 进入 HALF_OPEN

    def test_half_open_success_to_closed(self):
        """HALF_OPEN 中试探成功后恢复 CLOSED。"""
        cb = CircuitBreaker(
            failure_threshold=2,
            cooldown_seconds=0.05,
        )

        cb.record_failure("k")
        cb.record_failure("k")
        time.sleep(0.1)
        assert cb.is_available("k")  # HALF_OPEN

        cb.record_success("k")
        assert cb.is_available("k")  # 回到 CLOSED
        status = cb.get_status("k")
        assert status["state"] == CircuitBreaker.CLOSED

    def test_half_open_failure_back_to_open(self):
        """HALF_OPEN 中试探失败应回到 OPEN。"""
        cb = CircuitBreaker(
            failure_threshold=2,
            cooldown_seconds=0.05,
        )

        cb.record_failure("k")
        cb.record_failure("k")
        time.sleep(0.1)
        assert cb.is_available("k")

        # HALF_OPEN 中再次失败
        cb.record_failure("k")
        assert not cb.is_available("k")
        status = cb.get_status("k")
        assert status["state"] == CircuitBreaker.OPEN

    def test_half_open_max_calls(self):
        """HALF_OPEN 状态下试探次数受 half_open_max_calls 限制。"""
        cb = CircuitBreaker(
            failure_threshold=2,
            cooldown_seconds=0.01,
            half_open_max_calls=2,
        )

        # 触发熔断
        cb.record_failure("k")
        cb.record_failure("k")
        time.sleep(0.05)  # 冷却到期

        # 手动将状态设为 HALF_OPEN 并设置 half_open_calls
        with cb._lock:
            cb._states["k"]["state"] = CircuitBreaker.HALF_OPEN
            cb._states["k"]["half_open_calls"] = 2  # 已达上���

        # 第 3 次不行
        assert not cb.is_available("k")


class TestCircuitBreakerMultiKey:
    """测试多个 key 的独立熔断状态。"""

    def test_independent_states(self):
        cb = CircuitBreaker(failure_threshold=2)

        # key_a 失败
        cb.record_failure("key_a")
        cb.record_failure("key_a")
        assert not cb.is_available("key_a")

        # key_b 仍然可用
        assert cb.is_available("key_b")

    def test_reset_specific_key(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure("a")
        cb.record_failure("b")

        cb.reset("a")
        assert cb.is_available("a")
        assert not cb.is_available("b")  # b 未被重置

    def test_reset_all(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure("a")
        cb.record_failure("b")

        cb.reset()  # 无参数 = 重置全部
        assert cb.is_available("a")
        assert cb.is_available("b")


class TestCircuitBreakerGetStatus:
    """测试 get_status 方法。"""

    def test_returns_status_dict(self):
        cb = CircuitBreaker()
        status = cb.get_status("test")
        assert "key" in status
        assert status["state"] == CircuitBreaker.CLOSED
        assert status["failures"] == 0

    def test_tracks_failures(self):
        cb = CircuitBreaker()
        cb.record_failure("t")
        cb.record_failure("t")
        status = cb.get_status("t")
        assert status["failures"] == 2

    def test_cooldown_remaining_in_open_state(self):
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=3600)
        cb.record_failure("t")
        status = cb.get_status("t")
        assert status["state"] == CircuitBreaker.OPEN
        assert status["cooldown_remaining"] > 0


class TestCircuitBreakerThreadSafety:
    """测试 CircuitBreaker 的线程安全性。"""

    def test_concurrent_access(self):
        import threading

        cb = CircuitBreaker(failure_threshold=100)  # 不触发熔断
        errors: list[Exception] = []

        def worker():
            try:
                for _ in range(100):
                    cb.is_available("shared")
                    cb.record_success("shared")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
