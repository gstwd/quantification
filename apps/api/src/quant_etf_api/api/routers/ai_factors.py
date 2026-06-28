"""AI 因子 API 路由。

提供以下端点：
- POST /ai-factors/collect: 触发新闻采集
- POST /ai-factors/analyze: 触发完整 AI 分析链路
- GET /ai-factors/sentiment/{date}: 查询某日情绪聚合
- GET /ai-factors/summary/{index_code}: 某指数情绪摘要
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.config.settings import Settings, get_settings
from quant_etf_api.infra.ai.client import AIClient
from quant_etf_api.infra.db.repositories.news_item import (
    DailySentimentAggregateRepository,
    NewsItemRepository,
)
from quant_etf_api.schemas.ai_factor import (
    AIAnalysisRunResponse,
    DailySentimentResponse,
    NewsItemResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai-factors", tags=["AI 因子"])


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
    market_context: str = Query(default="", description="市场背景描述"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AIAnalysisRunResponse:
    """触发完整 AI 分析链路（新闻采集 → AI 分析 → 聚合）。

    该接口为同步执行，适合手动触发或定时任务调用。
    """
    from quant_etf_api.ai_factors.service import AIFactorService

    client = AIClient.from_settings(settings)
    valid, error = client.validate()
    if not valid:
        return AIAnalysisRunResponse(status="failed", error=error)

    service = AIFactorService(db, client)
    try:
        stats = service.run_full_pipeline(
            target_date=target_date,
            market_context=market_context,
        )
        return AIAnalysisRunResponse(status="success", **stats)
    except Exception as e:
        logger.exception("AI 分析链路失败")
        return AIAnalysisRunResponse(status="failed", error=str(e))


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


def _raw_to_news_rows(items: list, crawl_date: date) -> list[dict[str, Any]]:
    """将 RawNewsItem 转为 DB 行。"""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = []
    for item in items:
        rows.append({
            "id": None,
            "source_id": item.source_id,
            "source_name": item.source_name,
            "title": item.title,
            "url": item.url,
            "rank": item.ranks[0] if item.ranks else None,
            "crawl_date": crawl_date,
            "appear_count": item.appear_count,
            "raw_payload": None,
            "created_at": now,
        })
    return rows
