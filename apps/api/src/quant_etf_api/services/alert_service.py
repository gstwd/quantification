"""站内告警服务：记录、查询与标记已读。

告警只是提醒，不触发任何自动调参、自动上下线或交易动作。写入按 ``dedupe_key`` 幂等：
同一事件（如同一策略同一天的健康恶化）重复上报只保留第一条。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from quant_etf_api.infra.db.models.core import SystemAlertModel
from quant_etf_api.infra.time import utcnow_aware

logger = logging.getLogger(__name__)

KIND_HEALTH_DEGRADED = "health_degraded"
KIND_REFRESH_FAILED = "lifecycle_refresh_failed"
KIND_DATA_SYNC_FAILED = "data_sync_failed"

# 健康等级的严重度顺序（UNKNOWN 表示数据不足，不参与恶化判定）
HEALTH_ORDER: dict[str, int] = {"HEALTHY": 0, "WATCH": 1, "WARNING": 2, "CRITICAL": 3}


def is_degraded(previous: str | None, current: str | None) -> bool:
    """健康等级是否变差（任一侧未知时不判定）。

    Args:
        previous: 刷新前等级。
        current: 刷新后等级。

    Returns:
        变差时为 True。
    """
    if previous not in HEALTH_ORDER or current not in HEALTH_ORDER:
        return False
    return HEALTH_ORDER[current] > HEALTH_ORDER[previous]


def severity_of_health(level: str) -> str:
    """健康等级 → 告警严重程度。"""
    return {"CRITICAL": "critical", "WARNING": "warning"}.get(level, "info")


class AlertService:
    """system_alert 的读写服务（写入自行提交）。"""

    def __init__(self, db: Session) -> None:
        """初始化。

        Args:
            db: 数据库会话。
        """
        self._db = db

    def raise_alert(
        self,
        *,
        kind: str,
        severity: str,
        title: str,
        dedupe_key: str,
        strategy_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> bool:
        """幂等写入一条告警。

        Args:
            kind: 告警类型。
            severity: info / warning / critical。
            title: 标题。
            dedupe_key: 去重键。
            strategy_id: 相关策略。
            detail: 结构化明细。

        Returns:
            是否新写入（重复事件返回 False）。
        """
        statement = (
            pg_insert(SystemAlertModel)
            .values(
                kind=kind,
                severity=severity,
                strategy_id=strategy_id,
                title=title[:200],
                detail=detail or {},
                dedupe_key=dedupe_key[:128],
                created_at=utcnow_aware(),
            )
            .on_conflict_do_nothing(constraint="uq_system_alert_dedupe")
        )
        result = self._db.execute(statement)
        self._db.commit()
        inserted = bool(getattr(result, "rowcount", 0))
        if inserted:
            logger.info("[alert] %s %s: %s", severity, kind, title)
        return inserted

    def list_alerts(self, *, unread_only: bool = False, limit: int = 50) -> list[SystemAlertModel]:
        """按时间倒序查询告警。

        Args:
            unread_only: 只返回未读。
            limit: 条数上限。

        Returns:
            告警行。
        """
        query = self._db.query(SystemAlertModel)
        if unread_only:
            query = query.filter(SystemAlertModel.read_at.is_(None))
        return query.order_by(SystemAlertModel.created_at.desc()).limit(limit).all()

    def unread_count(self) -> int:
        """未读告警数。"""
        return int(
            self._db.query(SystemAlertModel).filter(SystemAlertModel.read_at.is_(None)).count()
        )

    def mark_read(self, alert_id: int) -> bool:
        """标记单条已读。

        Args:
            alert_id: 告警 ID。

        Returns:
            告警存在时为 True。
        """
        row = self._db.get(SystemAlertModel, alert_id)
        if row is None:
            return False
        if row.read_at is None:
            row.read_at = utcnow_aware()
            self._db.commit()
        return True

    def mark_all_read(self) -> int:
        """全部标记已读。

        Returns:
            本次标记的条数。
        """
        count = (
            self._db.query(SystemAlertModel)
            .filter(SystemAlertModel.read_at.is_(None))
            .update({SystemAlertModel.read_at: utcnow_aware()}, synchronize_session=False)
        )
        self._db.commit()
        return int(count or 0)
