"""AI 因子 API 路由。

提供以下端点：
- POST /ai-factors/collect: 触发新闻采集
- POST /ai-factors/analyze: 触发完整 AI 分析链路（异步，返回 run_id）
- GET /ai-factors/sentiment/{date}: 查询某日情绪聚合
- GET /ai-factors/summary/{index_code}: 某指数情绪摘要
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.api.executor import get_bg_executor
from quant_etf_api.config.settings import Settings, get_settings
from quant_etf_api.infra.ai.client import AIClient
from quant_etf_api.infra.db.base import SessionLocal
from quant_etf_api.infra.db.models.core import ResearchRunModel
from quant_etf_api.infra.db.repositories.news_item import (
    AISentimentResultRepository,
    DailySentimentAggregateRepository,
    NewsItemRepository,
)
from quant_etf_api.schemas.ai_factor import (
    AIAnalysisRunResponse,
    DailySentimentResponse,
    MarketSynthesisResponse,
    TagNewsItemResponse,
)
from quant_etf_api.services.run_service import RunService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-factors", tags=["AI 因子"])


# ---------------------------------------------------------------------------
# 后台任务函数
# ---------------------------------------------------------------------------


def _run_ai_analysis_bg(run_id: str) -> None:
    """在独立 Session 中执行 AI 分析，避免与请求 Session 冲突。

    Args:
        run_id: 运行记录 ID。
    """
    db = SessionLocal()
    try:
        RunService(db).mark_running(run_id)
        from quant_etf_api.ai_factors.service import AIFactorService

        settings = get_settings()
        client = AIClient.from_settings(settings)
        today = date.today()
        service = AIFactorService(db, client)
        stats = service.run_full_pipeline(
            target_date=today,
        )
        RunService(db).mark_success(run_id, metrics=stats)
    except Exception as e:
        logger.exception("AI 分析任务异常: run_id=%s", run_id)
        RunService(db).mark_failed(
            run_id,
            f"AI 分析异常: {type(e).__name__}: {e!s}",
        )
    finally:
        db.close()


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------


@router.post("/collect", response_model=AIAnalysisRunResponse)
def trigger_collect(
    background_tasks: BackgroundTasks,
    platform_ids: list[str] | None = Query(default=None, description="指定平台 ID，默认全部"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AIAnalysisRunResponse:
    """触发新闻采集（同步执行，小数据量）。"""
    from quant_etf_api.ai_factors.data.collector import NewsCollector

    collector = NewsCollector()
    try:
        items = collector.fetch_hotlist(platform_ids)
        if not items:
            return AIAnalysisRunResponse(status="success", collected=0)

        news_repo = NewsItemRepository(db)
        rows = _raw_to_news_rows(items, date.today())
        saved = news_repo.save_batch(rows)

        return AIAnalysisRunResponse(
            status="success",
            collected=len(items),
            saved=saved,
        )
    except Exception as e:
        logger.exception("新闻采集失败")
        return AIAnalysisRunResponse(status="failed", error=str(e))


@router.post("/analyze", response_model=AIAnalysisRunResponse)
def trigger_analyze(
    background_tasks: BackgroundTasks,
    target_date: date | None = Query(default=None, description="目标交易日，默认今天"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AIAnalysisRunResponse:
    """触发完整 AI 分析链路（新闻采集 → AI 分析 → 聚合）。

    该接口为异步模式：创建运行记录后立即返回 run_id，
    实际分析在后台线程池中执行。通过 GET /runs/{run_id} 查询进度。

    同一交易日同时只允许一个 running 任务：若已有 pending/running 记录，
    返回 rejected 并提示已有 run_id。
    """
    today = target_date or date.today()

    # 互斥检查：当天不能有两个 pending/running 的 AI 分析任务
    existing = (
        db.query(ResearchRunModel)
        .filter(
            ResearchRunModel.run_type == "ai_analysis",
            ResearchRunModel.trade_date == today,
            ResearchRunModel.status.in_(["pending", "running"]),
        )
        .first()
    )
    if existing is not None:
        return AIAnalysisRunResponse(
            status="rejected",
            error=f"已有进行中的 AI 分析任务（run_id={existing.run_id}）",
            run_id=existing.run_id,
        )

    # 校验 LLM 配置
    client = AIClient.from_settings(settings)
    valid, error = client.validate()
    if not valid:
        return AIAnalysisRunResponse(status="failed", error=error)

    # 创建运行记录并提交后台执行
    summary = RunService(db).create_run("ai_analysis", None, today)
    get_bg_executor().submit(_run_ai_analysis_bg, summary.run_id)

    return AIAnalysisRunResponse(
        status="accepted",
        run_id=summary.run_id,
    )


@router.get("/sentiment/{query_date}", response_model=list[DailySentimentResponse])
def get_sentiment(
    query_date: date,
    asset_tag: str | None = Query(default=None, description="限定资产标签"),
    db: Session = Depends(get_db),
) -> list[DailySentimentResponse]:
    """查询指定日期的 AI 情绪聚合数据。"""
    repo = DailySentimentAggregateRepository(db)
    rows = repo.find_by_date_range(query_date, query_date, asset_tag=asset_tag)
    return [
        DailySentimentResponse(
            trade_date=r.trade_date,
            asset_tag=r.asset_tag,
            avg_sentiment=r.avg_sentiment or 0.0,
            weighted_sentiment=r.weighted_sentiment or 0.0,
            total_attention=r.total_attention or 0.0,
            news_count=r.news_count,
            top_topics=r.top_topics or [],
            positive_ratio=r.positive_ratio or 0.0,
            negative_ratio=r.negative_ratio or 0.0,
        )
        for r in rows
    ]


@router.get("/sentiment/{query_date}/news", response_model=list[TagNewsItemResponse])
def get_sentiment_news(
    query_date: date,
    asset_tag: str = Query(description="资产标签"),
    db: Session = Depends(get_db),
) -> list[TagNewsItemResponse]:
    """查询指定日期的指定资产标签下的所有新闻明细。

    返回该标签下每条新闻的标题、链接、来源、情绪分和关注度分。
    """
    repo = AISentimentResultRepository(db)
    rows = repo.find_news_by_tag(query_date, asset_tag)
    return [
        TagNewsItemResponse(
            title=r["title"],
            url=r["url"],
            source_name=r["source_name"],
            sentiment_score=r["sentiment_score"],
            attention_score=r["attention_score"],
        )
        for r in rows
    ]


@router.get("/summary/{index_code}", response_model=list[DailySentimentResponse])
def get_index_summary(
    index_code: str,
    days: int = Query(default=7, ge=1, le=60, description="回望天数"),
    db: Session = Depends(get_db),
) -> list[DailySentimentResponse]:
    """查询某指数最近 N 天的情绪摘要。"""
    from datetime import timedelta

    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    repo = DailySentimentAggregateRepository(db)
    rows = repo.find_by_date_range(start_date, end_date, asset_tag=index_code)
    return [
        DailySentimentResponse(
            trade_date=r.trade_date,
            asset_tag=r.asset_tag,
            avg_sentiment=r.avg_sentiment or 0.0,
            weighted_sentiment=r.weighted_sentiment or 0.0,
            total_attention=r.total_attention or 0.0,
            news_count=r.news_count,
            top_topics=r.top_topics or [],
            positive_ratio=r.positive_ratio or 0.0,
            negative_ratio=r.negative_ratio or 0.0,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 市场研判
# ---------------------------------------------------------------------------


@router.get(
    "/synthesis/{trade_date}",
    response_model=MarketSynthesisResponse | None,
)
def get_synthesis(
    trade_date: date,
    db: Session = Depends(get_db),
) -> MarketSynthesisResponse | None:
    """查询指定日期的市场综合研判。"""
    from quant_etf_api.infra.db.repositories.news_item import MarketSynthesisRepository

    repo = MarketSynthesisRepository(db)
    row = repo.find_by_date(trade_date)
    if row is None:
        return None
    return MarketSynthesisResponse(
        trade_date=row.trade_date,
        content=row.content,
        key_topics=row.key_topics or [],
        risk_notes=row.risk_notes,
        sentiment_summary=row.sentiment_summary or {},
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


@router.get(
    "/synthesis-range",
    response_model=list[MarketSynthesisResponse],
)
def get_synthesis_range(
    start: date = Query(description="起始日期（含）"),
    end: date = Query(description="截止日期（含）"),
    db: Session = Depends(get_db),
) -> list[MarketSynthesisResponse]:
    """查询日期范围内的市场综合研判。"""
    from quant_etf_api.infra.db.repositories.news_item import MarketSynthesisRepository

    repo = MarketSynthesisRepository(db)
    rows = repo.find_by_date_range(start, end)
    return [
        MarketSynthesisResponse(
            trade_date=r.trade_date,
            content=r.content,
            key_topics=r.key_topics or [],
            risk_notes=r.risk_notes,
            sentiment_summary=r.sentiment_summary or {},
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 辅助端点
# ---------------------------------------------------------------------------


@router.get("/latest-data-date")
def get_latest_data_date(
    db: Session = Depends(get_db),
) -> dict[str, str | None]:
    """获取最新有情绪数据的交易日，供前端快捷日期选择使用。"""
    from quant_etf_api.infra.db.models.core import DailySentimentAggregateModel
    from sqlalchemy import func

    latest = (
        db.query(func.max(DailySentimentAggregateModel.trade_date)).scalar()
    )
    return {"trade_date": latest.isoformat() if latest else None}


def _raw_to_news_rows(items: list, crawl_date: date) -> list[dict[str, Any]]:
    """将 RawNewsItem 转为 DB 行。"""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = []
    for item in items:
        rows.append(
            {
                "source_id": item.source_id,
                "source_name": item.source_name,
                "title": item.title,
                "url": item.url,
                "rank": item.ranks[0] if item.ranks else None,
                "crawl_date": crawl_date,
                "appear_count": item.appear_count,
                "raw_payload": None,
                "created_at": now,
            }
        )
    return rows
