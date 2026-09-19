"""CLI 本地并行回测池单元测试。

覆盖两件事：

1. 并发度推导（显式参数 > 环境变量 > 按 CPU/内存自动，且不超过任务数）；
2. 并行执行契约——**不依赖管道**，成败以退出码为准，失败任务不吞。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from quant_etf_api import cli_pool
from quant_etf_api.cli_pool import (
    cli_command,
    resolve_workers,
    run_parallel,
)


class TestResolveWorkers:
    """并发度推导。"""

    def test_explicit_value_wins(self) -> None:
        """显式传入优先，且至少为 1。"""
        assert resolve_workers(5) == 5
        assert resolve_workers(1) == 1

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量可覆盖自动推导结果。"""
        monkeypatch.setenv(cli_pool._WORKERS_ENV, "3")
        assert resolve_workers(None) == 3

    def test_auto_is_capped_and_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """自动推导不超过默认上限，也不超过待执行任务数。"""
        monkeypatch.delenv(cli_pool._WORKERS_ENV, raising=False)
        auto = resolve_workers(None)
        assert 1 <= auto <= cli_pool._DEFAULT_WORKER_CAP
        assert resolve_workers(None, task_count=1) == 1

    def test_non_positive_request_falls_back_to_auto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """0/负数表示"自动"，不是"串行"。"""
        monkeypatch.delenv(cli_pool._WORKERS_ENV, raising=False)
        assert resolve_workers(0) == resolve_workers(None)
        assert resolve_workers(-3) == resolve_workers(None)


class TestRunParallel:
    """并行执行契约。

    用 ``prefix=[sys.executable]`` 直接跑内联脚本，避开 CLI 入口的导入开销，
    同时保持"派生独立子进程 + 只看退出码"的被测契约不变。
    """

    _PREFIX = [sys.executable]

    def test_runs_all_tasks_and_reports_success(self, tmp_path: Path) -> None:
        """全部任务被执行，且并发（总耗时远小于串行）。"""
        markers = tmp_path / "markers"
        markers.mkdir()
        script = (
            "import sys, time, pathlib;"
            "p = pathlib.Path(sys.argv[1]);"
            "time.sleep(0.2);"
            "p.write_text('ok', encoding='utf-8')"
        )
        commands = [
            (f"t{i}", ["-c", script, str(markers / f"m{i}.txt")]) for i in range(4)
        ]
        started = time.monotonic()
        outcome = run_parallel(commands, workers=4, prefix=self._PREFIX)
        elapsed = time.monotonic() - started

        assert sorted(outcome.succeeded) == ["t0", "t1", "t2", "t3"]
        assert outcome.failed == []
        assert outcome.workers == 4
        assert all((markers / f"m{i}.txt").exists() for i in range(4))
        # 4 个 0.2s 任务在 4 并发下应远快于串行的 0.8s
        assert elapsed < 0.7

    def test_failure_recorded_without_stopping_batch(self) -> None:
        """单条失败按退出码记录，不中断整批。"""
        commands = [
            ("ok", ["-c", "raise SystemExit(0)"]),
            ("bad", ["-c", "raise SystemExit(7)"]),
        ]
        outcome = run_parallel(commands, workers=2, prefix=self._PREFIX)

        assert outcome.succeeded == ["ok"]
        assert outcome.failed == [("bad", 7)]
        assert outcome.total == 2

    def test_empty_task_list_is_noop(self) -> None:
        """空任务列表直接返回，不派生任何进程。"""
        outcome = run_parallel([], workers=4)
        assert outcome.total == 0
        assert outcome.workers == 1

    def test_log_dir_captures_child_output(self, tmp_path: Path) -> None:
        """给了 --log-dir 时子进程输出落独立文件（并发输出不互相穿插）。"""
        outcome = run_parallel(
            [("t1", ["-c", "print('hello-pool')"])],
            workers=1,
            log_dir=tmp_path,
            prefix=self._PREFIX,
        )
        assert outcome.succeeded == ["t1"]
        assert "hello-pool" in (tmp_path / "t1.log").read_text(encoding="utf-8")

    def test_cli_command_uses_current_interpreter(self) -> None:
        """子命令沿用当前解释器与 CLI 模块入口。"""
        command = cli_command(["backtest", "execute", "bt-1"])
        assert command[0] == sys.executable
        assert command[1:3] == ["-m", "quant_etf_api.cli"]
        assert command[3:] == ["backtest", "execute", "bt-1"]
        assert cli_command(["x"], prefix=["py"]) == ["py", "x"]


class TestNoPipeDependency:
    """并行池不得依赖管道（受限沙箱里管道会被拒绝）。"""

    def test_popen_called_without_pipes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """派生参数里不能出现 subprocess.PIPE。"""
        captured: list[dict[str, object]] = []
        real_popen = subprocess.Popen

        class _FakeProc:
            """最小进程替身：wait() 立即返回成功。"""

            pid = 1

            def wait(self) -> int:
                return 0

            def terminate(self) -> None:
                return None

        def _fake_popen(args: object, **kwargs: object) -> _FakeProc:
            captured.append(dict(kwargs))
            return _FakeProc()

        monkeypatch.setattr(cli_pool.subprocess, "Popen", _fake_popen)
        run_parallel([("t1", ["echo", "hi"])], workers=1)

        assert len(captured) == 1
        assert captured[0].get("stdout") != subprocess.PIPE
        assert "stdin" not in captured[0]
        assert real_popen is not None  # 保留引用，避免被误优化掉
        assert os.name  # 环境自检
