"""CLI 本地并行回测池：只用 CLI 子进程 + 数据库，不依赖后台队列与 API 服务。

为什么需要它：回测是 CPU + 数据库混合任务，单进程串行是 AI 优化闭环的主要瓶颈
（一次 `robustness standard` 是 120+ 条回测，串行要数小时）。平台原有的并行路径
有两处依赖：

- ``--async`` 只是把任务写进 ``background_job``，执行要靠 uvicorn 多 worker 或
  独立的 ``queue worker`` 进程——AI 侧"只跑 CLI"时这条链路不存在；
- ``ProcessPoolExecutor`` 用管道/命名管道做进程间通信，在受限沙箱里会被拒绝
  （实测 ``WinError 5``），一旦失败整批任务全部不执行。

本模块给出第三条路：**父进程只负责"派生 + 等待"，子进程各自连库写自己的回测行**。

- 每个子进程是一条独立的 ``python -m quant_etf_api.cli backtest execute <id>``，
  无管道通信（``stdout`` 直通或重定向到日志文件），因此不受管道限制；
- 进度与结果都落在数据库（``backtest_run`` / ``backtest_daily_result``），
  父进程不需要读子进程输出，**父进程被中断后重跑也不会丢已完成的回测**；
- 并发度由调用方给定，默认按 CPU 与内存预算推导（``resolve_workers``）。

并发度不是越大越好：每个子进程要各自加载行情与因子缓存（十年 × 数十个指数，
数百 MB 量级），并发度过高会先撞内存与数据库连接预算，而不是先跑满 CPU。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# 单进程内存预算的保守估计（MB）：十年 × 数十指数的行情 + 因子预计算缓存
_ESTIMATED_MB_PER_WORKER = 1200
# 留出一个核给父进程与数据库交互，避免父进程被饿死
_RESERVED_CORES = 1
# 默认上限：再多的并发会先撞内存与数据库连接预算
_DEFAULT_WORKER_CAP = 8
# 并发度环境变量（供人/agent 覆盖推导结果）
_WORKERS_ENV = "QUANT_ETF_CLI_WORKERS"


def resolve_workers(requested: int | None, task_count: int | None = None) -> int:
    """推导本次并行度。

    优先级：显式参数 > 环境变量 ``QUANT_ETF_CLI_WORKERS`` > 自动推导。
    自动推导取 CPU 核数减一与内存预算上限的较小值，再用
    :data:`_DEFAULT_WORKER_CAP` 封顶。

    Args:
        requested: 调用方显式指定的并发数；None 或 <1 时走自动推导。
        task_count: 待执行任务数；给定时不返回超过它的并发数。

    Returns:
        归一化后的并发数（至少 1）。
    """
    if requested is not None and requested > 0:
        workers = requested
    else:
        env_value = os.environ.get(_WORKERS_ENV)
        if env_value and env_value.strip().isdigit():
            workers = int(env_value)
        else:
            workers = _auto_workers()
    if task_count is not None:
        workers = min(workers, max(1, task_count))
    return max(1, workers)


def _auto_workers() -> int:
    """按 CPU 核数与内存预算推导默认并发度。"""
    cpu = os.cpu_count() or 2
    by_cpu = max(1, cpu - _RESERVED_CORES)
    by_memory = max(1, _available_memory_mb() // _ESTIMATED_MB_PER_WORKER)
    return max(1, min(by_cpu, by_memory, _DEFAULT_WORKER_CAP))


def _available_memory_mb() -> int:
    """读取可用物理内存（MB）；平台不支持时返回一个保守值。"""
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        return _ESTIMATED_MB_PER_WORKER * 4
    try:
        return int(psutil.virtual_memory().available / (1024 * 1024))
    except Exception:  # noqa: BLE001 - 读取内存失败不应阻塞并行推导
        return _ESTIMATED_MB_PER_WORKER * 4


@dataclass
class PoolOutcome:
    """一次并行执行的汇总结果。

    Attributes:
        succeeded: 退出码为 0 的任务 ID。
        failed: ``[(任务 ID, 退出码)]``，退出码非 0 的任务。
        workers: 实际使用的并发度。
    """

    succeeded: list[str] = field(default_factory=list)
    failed: list[tuple[str, int]] = field(default_factory=list)
    workers: int = 1

    @property
    def total(self) -> int:
        """本次执行的任务总数。"""
        return len(self.succeeded) + len(self.failed)


def cli_command(args: Sequence[str], prefix: Sequence[str] | None = None) -> list[str]:
    """构造一条指向本 CLI 的子命令（沿用当前解释器）。

    Args:
        args: 子命令参数（不含 ``-m quant_etf_api.cli``）。
        prefix: 命令前缀覆盖；None 时用 ``<当前解释器> -m quant_etf_api.cli``。

    Returns:
        可直接交给 ``subprocess`` 的完整命令列表。
    """
    base = list(prefix) if prefix is not None else [sys.executable, "-m", "quant_etf_api.cli"]
    return [*base, *args]


def run_parallel(
    commands: Iterable[tuple[str, Sequence[str]]],
    *,
    workers: int,
    workdir: Path | str | None = None,
    log_dir: Path | str | None = None,
    log_name: Callable[[str], str] | None = None,
    prefix: Sequence[str] | None = None,
) -> PoolOutcome:
    """并行执行一批 CLI 子命令。

    父进程只做派生与等待：子进程的 stdout/stderr 直通当前终端（``inherit``），
    因此不会触发"程序间管道"限制；父进程不解析子进程输出，
    成败以退出码为准，真实结果由数据库承载。

    Args:
        commands: ``(任务 ID, 命令参数列表)`` 序列；任务 ID 用于日志与汇总。
        workers: 并发度（由 :func:`resolve_workers` 归一化后的值）。
        workdir: 子进程工作目录（默认继承当前目录）。
        log_dir: 给定时把每个子进程输出重定向到该目录下的独立文件，
            避免并发输出互相穿插（长批次推荐）。
        log_name: 由任务 ID 生成日志文件名的函数；None 时用 ``<任务 ID>.log``。
        prefix: 命令前缀覆盖，透传给 :func:`cli_command`（测试或复用其它入口时用）。

    Returns:
        :class:`PoolOutcome`。

    Raises:
        KeyboardInterrupt: 收到中断时终止全部在跑的子进程后重新抛出。
    """
    tasks = list(commands)
    if not tasks:
        return PoolOutcome(workers=1)
    worker_count = resolve_workers(workers, len(tasks))
    log_path = Path(log_dir) if log_dir is not None else None
    if log_path is not None:
        log_path.mkdir(parents=True, exist_ok=True)

    logger.info("本地并行执行 %d 条 CLI 任务，并发进程=%d", len(tasks), worker_count)
    outcome = PoolOutcome(workers=worker_count)
    lock = threading.Lock()
    active: list[subprocess.Popen[bytes]] = []

    def _run(task: tuple[str, Sequence[str]]) -> None:
        """执行单个任务并记录结果。"""
        task_id, args = task
        stdout: object = subprocess.DEVNULL
        handle = None
        if log_path is not None:
            name = log_name(task_id) if log_name else f"{task_id}.log"
            handle = open(log_path / name, "w", encoding="utf-8")
            stdout = handle
        try:
            proc = subprocess.Popen(
                cli_command(args, prefix),
                cwd=str(workdir) if workdir is not None else None,
                stdout=stdout,
                stderr=subprocess.STDOUT,
            )
        except Exception as exc:  # noqa: BLE001 - 派生失败按任务失败计入，不中断整批
            if handle is not None:
                handle.close()
            logger.warning("派生子进程失败：%s %s", task_id, exc)
            with lock:
                outcome.failed.append((task_id, -1))
            return
        with lock:
            active.append(proc)
        try:
            code = proc.wait()
        finally:
            with lock:
                if proc in active:
                    active.remove(proc)
            if handle is not None:
                handle.close()
        with lock:
            if code == 0:
                outcome.succeeded.append(task_id)
            else:
                outcome.failed.append((task_id, code))

    try:
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            list(pool.map(_run, tasks))
    except KeyboardInterrupt:
        # 父进程被打断时终止仍在跑的子进程，避免孤儿进程继续写库
        with lock:
            survivors = list(active)
        for proc in survivors:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001 - 终止失败不应掩盖中断本身
                logger.warning("终止子进程失败: pid=%s", proc.pid, exc_info=True)
        raise
    return outcome
