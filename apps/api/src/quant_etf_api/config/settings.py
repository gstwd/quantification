from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# apps/api/.env 的绝对路径：从仓库根目录运行 CLI（如研究战役的 agent 会话）时也读同一份配置，
# 否则会静默连到缺省的本机数据库。工作目录下的 .env 排在后面，存在时优先。
_API_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="QUANT_ETF_", env_file=(str(_API_ENV_FILE), ".env"), extra="ignore"
    )

    app_name: str = "Quant ETF Research Platform"
    app_env: str = Field(default="development")
    app_host: str = Field(default="0.0.0.0")
    app_port: int = Field(default=8000)
    api_prefix: str = Field(default="/api")
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/quant_etf"
    )

    # 定时调度
    schedule_enabled: bool = Field(default=True, description="是否启用每日自动全局数据同步")
    lifecycle_auto_refresh_enabled: bool = Field(
        default=True,
        description="每日数据同步完成后是否自动刷新 LIVE 策略健康快照（只做诊断与站内告警）",
    )
    lifecycle_scheduled_keep: int = Field(
        default=5,
        ge=1,
        description="每条 LIVE 策略保留的定时监控回测条数（手动刷新产生的不清理）",
    )
    # 研究数据瘦身与历史留存（只删逐指数明细、已结束任务与已读告警，不动台账与 N）
    campaign_auto_cleanup_enabled: bool = Field(
        default=True,
        description="研究战役结束时是否自动清理：已结束任务记录、步骤日志与回测逐指数明细",
    )
    job_history_retention_days: int = Field(
        default=30,
        ge=1,
        description="每日数据同步后删除早于该天数的已结束后台任务记录",
    )
    alert_retention_days: int = Field(
        default=30,
        ge=1,
        description="每日数据同步后删除早于该天数的已读站内告警（未读告警不删除）",
    )
    schedule_time: str = Field(
        default="17:30", description="每日全局数据自动同步触发时间（HH:MM），默认收盘后 17:30"
    )

    # 后台任务队列（按任务类别分 lane，各自绑定独立的并发预算）
    # 回测是 CPU + 数据库混合型任务，实测单进程内并发 4 条比串行慢约 20 倍，
    # 因此回测 lane 默认并发 1（串行），且与摄取/其它任务互不抢占 worker。
    job_queue_workers: int = Field(
        default=2,
        ge=1,
        description="后台任务队列通用 lane 的 worker 线程数（因子/摄取/其它任务）",
    )
    job_queue_backtest_workers: int = Field(
        default=1,
        ge=1,
        description="回测 lane 的 worker 线程数（回测并发预算，默认串行）",
    )
    job_queue_embedded: bool = Field(
        default=True,
        description=(
            "是否在 API 进程内启动 worker 线程；部署独立 worker 进程"
            "（python -m quant_etf_api.worker）时应设为 false，避免两处同时消费"
        ),
    )
    job_poll_interval_seconds: float = Field(
        default=1.0, gt=0, description="任务队列空转时的轮询间隔（秒）"
    )
    job_heartbeat_interval_seconds: float = Field(
        default=15.0,
        gt=0,
        description="worker 执行任务时更新 background_job.heartbeat_at 的间隔（秒）",
    )
    job_stuck_timeout_seconds: float = Field(
        default=1800.0,
        gt=0,
        description="运行期僵尸任务判定阈值（秒）：心跳超过该时长未更新即视为卡死",
    )
    job_zombie_scan_enabled: bool = Field(
        default=False,
        description="是否启用运行期僵尸任务扫描线程（按心跳超时回收 running 任务）",
    )
    job_zombie_scan_interval_seconds: float = Field(
        default=60.0,
        gt=0,
        description="僵尸任务扫描线程的扫描间隔（秒）",
    )
    job_max_runtime_seconds: float = Field(
        default=7200.0,
        ge=0,
        description=(
            "单个任务最长运行时间（秒），超过即被僵尸扫描回收；0 表示不限制。"
            "默认 2 小时，用于兜住『卡在数据库锁上但心跳正常』的异常长任务"
        ),
    )

    # 日志配置
    log_level: str = Field(default="INFO", description="日志级别：DEBUG / INFO / WARNING / ERROR")
    log_file: str | None = Field(
        default=None, description="JSON 日志文件路径，留空则仅输出到控制台"
    )

    # 研究期 / 验证期边界（用户约定）：研究期固定为 2016-01-01 ~ 2025-12-31，
    # 2026-01-01 起为验证期（上线后的实盘验收区间）。边界写成系统常量而非人工
    # 记忆，研究类回测与优化会话越过研究期末端会被直接拒绝。
    research_period_start: str = Field(
        default="2016-01-01", description="研究期起始日（含），所有研究类回测的下限"
    )
    research_period_end: str = Field(
        default="2025-12-31", description="研究期截止日（含），研究类回测不得越过该日期"
    )
    validation_period_start: str = Field(
        default="2026-01-01", description="验证期起始日（含），仅允许验证/监控类回测使用"
    )

    # 回测与监控的默认交易成本（基点，按单边换手折算的往返成本）：系统回测为毛收益口径，
    # 净口径指标按"单边换手率 × cost_bps"在读取路径折算
    default_cost_bps: float = Field(
        default=0.5, ge=0.0, description="默认往返交易成本（基点，按单边换手折算），用于净口径指标折算"
    )
    # 净口径多档并列：读取路径按该梯子现算，无需重跑回测；0 表示毛口径
    stability_cost_ladder: list[float] = Field(
        default_factory=lambda: [0.0, 0.5, 5.0, 10.0],
        description=("回测详情并列展示的净口径成本档位（基点，JSON 数组）；0 表示毛口径"),
    )
    # 有效候选池时间线：剔除明细最多保留多少条指数区间
    candidate_pool_exclusion_limit: int = Field(
        default=50,
        ge=1,
        description="回测有效候选池剔除明细（指数—日期区间）保留条数上限，超出按天数截断",
    )
    # 参数邻域稳定度容差（指标绝对值，默认夏普单位）：全部扰动变体的 |Δ| 都在该
    # 范围内才算"落在参数高原"；同时用于判定方向反转（两侧落差都超过容差）
    robustness_neighborhood_tolerance: float = Field(
        default=0.1,
        gt=0.0,
        description="参数邻域扰动的稳定度容差（|Δ指标| ≤ 容差记为仍在高原内）",
    )

    # 研究线试验预算：一条研究线（基线 + 派生候选/变体）累计试验次数 N 的上限。
    # 超出后 research batch / optimization start / robustness 拒绝执行，
    # 需 --over-budget "<理由>" 显式放行并在研究台账留痕
    research_trial_budget: int = Field(
        default=100, ge=1, description="单条研究线的试验次数预算（超出需显式放行并留痕）"
    )
    # 优化验收清单的统计门槛（finish 默认强制）
    acceptance_min_deflated_sharpe: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        description=(
            "验收第 8 项：候选 − 基线逐日超额净收益的 Deflated Sharpe 下限（N 取研究线累计值；"
            "门槛的实际假阳性率与功效用 research dsr-calibrate 校准）"
        ),
    )
    acceptance_min_diff_episodes: int = Field(
        default=8,
        ge=1,
        description="验收第 10 项：候选与基线逐日收益出现差异的独立区段数下限",
    )
    acceptance_min_diff_days: int = Field(
        default=40,
        ge=1,
        description="验收第 10 项：候选与基线逐日收益出现差异的交易日数下限",
    )

    # ========== 多数据源指数日线摄取 ==========

    tickflow_api_key: str | None = Field(
        default=None,
        description=(
            "TickFlow API Key；未配置时自动使用免费服务（free-api.tickflow.org，"
            "无需注册，仅提供历史日 K 线）"
        ),
    )
    tushare_token: str | None = Field(
        default=None,
        description=(
            "Tushare Pro API Token，配置后指数日线/估值/宏观/成分/个股等数据源优先走 tushare"
        ),
    )
    index_daily_source_order: str = Field(
        default="tushare,akshare,tickflow,baostock",
        description=(
            "指数日线多数据源优先级（逗号分隔），可选 akshare/tickflow/tushare/baostock；"
            "tushare 未配置 Token 时自动跳过，akshare 内部仍有五级降级链"
        ),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
