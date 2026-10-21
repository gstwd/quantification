"""研究战役驱动器：逐步启动全新的 headless Claude Code 会话执行战役。

每一步：``next_task`` 取任务卡 → ``begin_step`` 登记 → 以 ``claude -p`` 启动 agent
（独立会话、无上一步的上下文）→ ``finalize_step`` 收口。直到战役触顶停止、
研究员判定没有新方向，或达到本次调用的步数上限。

安全边界分两层：
1. 进程环境带 ``QUANT_CAMPAIGN_ID``，CLI 据此进入战役模式（命令白名单、研究线范围、
   评审前置、禁止 promote / 非严格接受 / 超预算）；
2. agent 以 ``dontAsk`` 权限模式、``--setting-sources user`` 运行（不合并仓库的项目 /
   本地权限设置），只放行 ``python -m quant_etf_api.cli``、白名单路径的读取（平台契约、
   技能手册、源码、本战役目录），以及写入 ``candidates/<战役>/``；其他命令一律拒绝。
   超时或中断时结束 agent 的整棵进程树。

只依赖 CLI + 数据库，不需要 API 服务或队列 worker。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from quant_etf_api.domain.research.campaign import SKILL_DIR, build_agent_prompt
from quant_etf_api.infra.db.base import SessionLocal
from quant_etf_api.services.research_campaign_service import ResearchCampaignService

logger = logging.getLogger(__name__)

# 仓库根目录与 API 目录（本文件位于 apps/api/src/quant_etf_api/services/）
REPO_ROOT = Path(__file__).resolve().parents[5]
API_DIR = Path(__file__).resolve().parents[3]

# 宿主 Claude Code 会话注入的环境变量（驱动器在宿主会话内启动时需要清理）
_HOST_SESSION_KEYS = frozenset({"CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "ANTHROPIC_BASE_URL"})
_HOST_SESSION_PREFIXES = ("CLAUDE_CODE_", "CLAUDE_AGENT_SDK_", "CLAUDE_PREVIEW_")


@dataclass
class DriverConfig:
    """驱动器参数。

    Attributes:
        max_steps: 本次调用最多执行的步数（None 表示直到战役停止）。
        workers: 传给 agent 的并行回测进程数（``QUANT_ETF_CLI_WORKERS``）。
        step_timeout_hours: 单步 agent 会话的超时（小时）。
        claude_bin: claude 可执行文件。
        recover: 遇到上次中断遗留的运行中步骤时是否将其标记为失败后继续。
    """

    max_steps: int | None = None
    workers: int = 4
    step_timeout_hours: float = 3.0
    claude_bin: str = "claude"
    recover: bool = False


def load_env_file(path: Path) -> dict[str, str]:
    """读取 .env 文件中的键值（agent 在仓库根目录运行，需显式透传数据库等配置）。

    Args:
        path: .env 路径。

    Returns:
        键值字典；文件不存在时为空。
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


# agent 可读的位置（相对仓库根目录）：平台契约、技能手册、源码与本战役工作目录。
# 不放行 candidates/ 下其他目录：旧研究产物不应左右新战役（研究从台账重新开始）。
_READABLE_PATHS: tuple[str, ...] = (
    "./AGENTS.md",
    "./CLAUDE.md",
    "./README.md",
    "./docs/**",
    "./.claude/skills/**",
    "./.agents/skills/**",
    "./apps/api/src/**",
)


def permission_settings(campaign_id: str) -> dict[str, Any]:
    """agent 会话的权限设置：只放行平台 CLI、白名单路径读取与战役工作目录写入。

    驱动器以 ``--setting-sources user`` 启动 agent，不合并仓库的项目 / 本地设置
    （其中可能放行 curl 等命令），权限以本文件为准。

    Args:
        campaign_id: 战役 ID。

    Returns:
        Claude Code settings 字典。
    """
    workspace = f"./candidates/{campaign_id}/**"
    return {
        "permissions": {
            "allow": [
                "Bash(python -m quant_etf_api.cli:*)",
                *(f"Read({path})" for path in (*_READABLE_PATHS, workspace)),
                f"Edit({workspace})",
                f"Write({workspace})",
            ],
            "deny": ["Bash(git:*)", "WebFetch", "WebSearch"],
        }
    }


def build_prompt(card: dict[str, Any]) -> str:
    """把任务卡组装成 agent 的输入提示。

    Args:
        card: ``next_task`` 返回的任务卡。

    Returns:
        提示文本。
    """
    return build_agent_prompt(card, skill_dir=SKILL_DIR, desktop=False)


def build_env(campaign_id: str, workers: int) -> dict[str, str]:
    """组装 agent 进程环境：战役模式标记、数据库配置、虚拟环境 python 优先。

    Args:
        campaign_id: 战役 ID。
        workers: 并行回测进程数。

    Returns:
        环境变量字典。
    """
    env = dict(os.environ)
    for key, value in load_env_file(API_DIR / ".env").items():
        env.setdefault(key, value)
    env["QUANT_CAMPAIGN_ID"] = campaign_id
    env["QUANT_ETF_CLI_WORKERS"] = str(workers)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    # 驱动器自身可能运行在 Claude Code 会话里（例如桌面端）：宿主会话的标记、
    # 宿主代理地址与"由宿主刷新鉴权"开关会让子进程以为自己是宿主的子会话，
    # 从而不用本机 CLI 的登录态而报"Not logged in"。只在检测到宿主会话时清理，
    # 不动用户在终端里自行配置的变量。
    if env.get("CLAUDECODE") or env.get("CLAUDE_CODE_CHILD_SESSION"):
        for key in list(env):
            if key in _HOST_SESSION_KEYS or key.startswith(_HOST_SESSION_PREFIXES):
                env.pop(key, None)
    return env


def parse_result(stdout: str) -> dict[str, Any]:
    """解析 ``claude -p --output-format json`` 的输出。

    Args:
        stdout: 进程标准输出。

    Returns:
        结果字典；解析失败时返回 {"is_error": True, "result": 原文截断}。
    """
    text = (stdout or "").strip()
    start = text.find("{")
    if start >= 0:
        try:
            data = json.loads(text[start:])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {"is_error": True, "result": text[-2000:]}


def ensure_logged_in(
    claude: str,
    env: dict[str, str],
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """启动前确认本机 Claude Code CLI 已登录（未登录时每一步都会立刻失败并停掉战役）。

    Args:
        claude: claude 可执行文件。
        env: agent 进程环境（与实际启动时一致）。
        runner: 子进程执行函数。

    Raises:
        ValueError: 明确报告未登录。状态命令本身不可用时不阻断（交给首步暴露）。
    """
    try:
        proc = runner(
            [claude, "auth", "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("无法检查 Claude Code 登录状态", exc_info=True)
        return
    status = parse_result(proc.stdout)
    if status.get("loggedIn") is False:
        raise ValueError(
            "本机 Claude Code CLI 未登录（claude auth status: loggedIn=false）。"
            "请先在终端运行 `claude auth login`（或启动 claude 后执行 /login），再重新运行驱动器"
        )


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    """结束 agent 进程及其派生的全部子进程（并行回测等）。

    Args:
        proc: agent 进程。
    """
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=60,
                check=False,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("结束 agent 进程树失败，退回只结束主进程", exc_info=True)
    if proc.poll() is None:
        proc.kill()


def run_agent(
    cmd: list[str],
    *,
    input: str | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
    **_: Any,
) -> subprocess.CompletedProcess[str]:
    """启动 agent 并等待结束；超时或中断时结束整棵进程树。

    签名与 ``subprocess.run(..., capture_output=True, text=True)`` 兼容（多余参数忽略）。
    ``subprocess.run`` 超时只杀主进程，agent 派生的回测子进程会在 Windows 上残留。

    Args:
        cmd: 命令行。
        input: 标准输入文本。
        cwd: 工作目录。
        env: 环境变量。
        timeout: 超时秒数。

    Returns:
        进程结果（UTF-8 文本输出）。

    Raises:
        subprocess.TimeoutExpired: 超时（进程树已结束）。
    """
    group_kwargs: dict[str, Any] = (
        {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        env=env,
        **group_kwargs,
    )
    try:
        stdout, stderr = proc.communicate(input, timeout=timeout)
    except BaseException:
        # 超时与人工中断都要收掉进程树，再把异常交给调用方
        _kill_tree(proc)
        try:
            proc.communicate(timeout=30)
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _finalize(
    session_factory: Callable[[], Any],
    step_id: str,
    exit_code: int,
    result: dict[str, Any],
    session_id: str,
    error: str | None,
) -> dict[str, Any]:
    """在新的数据库会话中收口步骤。

    Args:
        session_factory: 数据库会话工厂。
        step_id: 步骤 ID。
        exit_code: agent 退出码。
        result: agent 输出解析结果。
        session_id: 预分配的 agent 会话 ID。
        error: 驱动器侧错误。

    Returns:
        步骤字典。
    """
    db = session_factory()
    try:
        return ResearchCampaignService(db).finalize_step(
            step_id,
            exit_code=exit_code,
            cost_usd=result.get("total_cost_usd"),
            agent_session_id=result.get("session_id") or session_id,
            error=error,
        )
    finally:
        db.close()


def run_campaign(
    campaign_id: str,
    config: DriverConfig,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_agent,
    session_factory: Callable[[], Any] = SessionLocal,
    echo: Callable[[str], None] = print,
) -> dict[str, Any]:
    """驱动战役直到停止或达到步数上限。

    Args:
        campaign_id: 战役 ID。
        config: 驱动器参数。
        runner: 子进程执行函数（测试可替换）。
        session_factory: 数据库会话工厂（测试可替换）。
        echo: 进度输出函数。

    Returns:
        {"campaign_id", "steps_run", "last_action", "status"}。
    """
    if os.environ.get("QUANT_CAMPAIGN_ID"):
        raise ValueError("驱动器不能在战役模式内启动（禁止 agent 嵌套驱动战役）")
    claude = shutil.which(config.claude_bin) or config.claude_bin
    ensure_logged_in(claude, build_env(campaign_id, config.workers), runner)
    workspace = REPO_ROOT / "candidates" / campaign_id
    (workspace / "steps").mkdir(parents=True, exist_ok=True)
    settings_path = workspace / "claude-settings.json"
    settings_path.write_text(
        json.dumps(permission_settings(campaign_id), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    steps_run = 0
    last: dict[str, Any] = {}
    while config.max_steps is None or steps_run < config.max_steps:
        db = session_factory()
        try:
            svc = ResearchCampaignService(db)
            last = svc.next_task(campaign_id)
            if last["action"] == "wait":
                if not config.recover:
                    raise ValueError(
                        f"战役有未收口的运行中步骤 {last['step_id']}（上次中断？）；"
                        "确认没有 agent 仍在运行后加 --recover 继续"
                    )
                svc.finalize_step(last["step_id"], exit_code=-1, error="驱动器恢复：上次运行中断")
                continue
            if last["action"] != "run":
                break
            card = last["card"]
            step_plan = card["next"]
            campaign = svc.require(campaign_id)
            session_id = str(uuid4())
            step = svc.begin_step(
                campaign_id,
                role=step_plan["role"],
                round_no=step_plan["round"],
                agent_session_id=session_id,
                optimization_id=step_plan.get("optimization_id"),
                hypothesis_id=step_plan.get("hypothesis_id"),
            )
            model = campaign.model
            max_usd = campaign.max_step_usd
        finally:
            db.close()

        echo(
            f"[campaign {campaign_id}] 第 {step_plan['round']} 轮 {step_plan['role']} 开始（session {session_id}）"
        )
        cmd = [
            claude,
            "-p",
            "--output-format",
            "json",
            "--session-id",
            session_id,
            "--permission-mode",
            "dontAsk",
            "--setting-sources",
            "user",
            "--strict-mcp-config",
            "--settings",
            str(settings_path),
        ]
        if model:
            cmd += ["--model", model]
        if max_usd:
            cmd += ["--max-budget-usd", str(max_usd)]
        log_base = (
            workspace
            / "steps"
            / f"r{step_plan['round']:02d}_{step_plan['role']}_{step['step_id'][:8]}"
        )
        exit_code = -1
        error: str | None = None
        result: dict[str, Any] = {}
        try:
            proc = runner(
                cmd,
                input=build_prompt(card),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(REPO_ROOT),
                env=build_env(campaign_id, config.workers),
                timeout=config.step_timeout_hours * 3600,
            )
            exit_code = proc.returncode
            log_base.with_suffix(".json").write_text(proc.stdout or "", encoding="utf-8")
            if proc.stderr:
                log_base.with_suffix(".stderr.txt").write_text(proc.stderr, encoding="utf-8")
            result = parse_result(proc.stdout)
            if result.get("is_error"):
                error = f"agent 报错：{str(result.get('result'))[:500]}"
        except subprocess.TimeoutExpired:
            error = f"agent 超时（>{config.step_timeout_hours:g} 小时）"
        except OSError as exc:
            error = f"无法启动 agent：{exc}"
        except KeyboardInterrupt:
            # 人工中断：先把步骤收口为失败，免得下次只能 --recover
            _finalize(session_factory, step["step_id"], -1, {}, session_id, "驱动器被人工中断")
            echo(f"[campaign {campaign_id}] 驱动器被中断，本步已标记失败")
            raise

        final = _finalize(session_factory, step["step_id"], exit_code, result, session_id, error)
        steps_run += 1
        echo(
            f"[campaign {campaign_id}] 第 {step_plan['round']} 轮 {step_plan['role']} 结束："
            f"{final['status']} {final.get('outcome') or ''} "
            f"cost=${(final.get('cost_usd') or 0):.2f} {final.get('error') or ''}"
        )

    db = session_factory()
    try:
        status = ResearchCampaignService(db).require(campaign_id).status
    finally:
        db.close()
    return {
        "campaign_id": campaign_id,
        "steps_run": steps_run,
        "last_action": last.get("action"),
        "stop_reason": last.get("reason"),
        "status": status,
    }
