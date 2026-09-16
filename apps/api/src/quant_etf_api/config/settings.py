from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QUANT_ETF_", env_file=".env", extra="ignore")

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
    schedule_time: str = Field(
        default="17:30", description="每日全局数据自动同步触发时间（HH:MM），默认收盘后 17:30"
    )
    ai_analysis_enabled: bool = Field(
        default=True, description="是否在日频调度中自动触发 AI 舆情分析"
    )
    ai_schedule_time: str = Field(
        default="23:30",
        description="AI 舆情分析触发时间（HH:MM），默认夜里 23:30，覆盖当天全部新闻",
    )

    # 后台任务队列（B1/B6：按任务类别分 lane，各自绑定独立的并发预算）
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

    # 回测与监控的默认交易成本（单边，基点）：系统回测为毛收益口径，
    # 净口径指标按"单边换手率 × cost_bps"在读取路径折算
    default_cost_bps: float = Field(
        default=10.0, ge=0.0, description="默认单边交易成本（基点），用于净口径指标折算"
    )
    # 净口径多档并列（C3）：读取路径按该梯子现算，无需重跑回测；0 表示毛口径
    stability_cost_ladder: list[float] = Field(
        default_factory=lambda: [0.0, 10.0, 20.0, 30.0, 50.0],
        description=("回测详情并列展示的净口径成本档位（基点，JSON 数组）；0 表示毛口径"),
    )
    # 有效候选池时间线（C6）：剔除明细最多保留多少条指数区间
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

    # LLM 配置（可选，不配置则 AI 分析功能不可用）
    llm_api_key: str | None = Field(default=None, description="LLM API Key")
    llm_base_url: str | None = Field(
        default=None, description="LLM API Base URL（OpenAI 兼容接口）"
    )
    llm_model: str = Field(
        default="deepseek/deepseek-chat", description="默认模型标识（LiteLLM 格式：provider/model）"
    )
    llm_max_tokens: int = Field(default=4096, description="AI 分析最大输出 token 数")
    llm_temperature: float = Field(default=0.3, description="AI 分析温度参数")
    llm_timeout_seconds: int = Field(default=60, description="LLM 请求超时时间")
    ai_max_analysis_items: int = Field(
        default=150,
        description="单次 AI 分析最大新闻条数，超出部分仅计算关注度不调用 LLM",
    )

    # ========== 新闻多源搜索配置 ==========

    tavily_api_keys: list[str] = Field(
        default_factory=list,
        description="Tavily 搜索 API Key 列表（支持多 Key 轮询）",
    )
    bocha_api_keys: list[str] = Field(
        default_factory=list,
        description="Bocha AI 搜索 API Key 列表（中文搜索优化）",
    )
    brave_api_keys: list[str] = Field(
        default_factory=list,
        description="Brave Search API Key 列表",
    )
    serpapi_api_keys: list[str] = Field(
        default_factory=list,
        description="SerpAPI Key 列表（Google News 搜索）",
    )
    anspire_api_keys: list[str] = Field(
        default_factory=list,
        description="Anspire Search API Key 列表（实时智能搜索）",
    )
    searxng_urls: list[str] = Field(
        default_factory=list,
        description="SearXNG 自建实例 URL 列表（无配额兜底搜索）",
    )

    # ========== 社交媒体情绪配置 ==========

    social_sentiment_api_key: str | None = Field(
        default=None,
        description="社交媒体情绪 API Key（api.adanos.org），用于美股情绪分析",
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
