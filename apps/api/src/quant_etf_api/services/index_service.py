from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from quant_etf_api.infra.clients.akshare_index import AkShareIndexClient
from quant_etf_api.infra.db.models.core import BenchmarkIndexModel
from quant_etf_api.infra.db.repositories.benchmark_index import BenchmarkIndexRepository
from quant_etf_api.schemas.market_data import BenchmarkIndex

logger = logging.getLogger(__name__)


class IndexService:
    """基准指数管理服务，提供指数的增删查功能。"""

    def __init__(self, db: Session) -> None:
        """初始化指数管理服务。

        Args:
            db: SQLAlchemy 同步 Session。
        """
        self._db = db
        self._repo = BenchmarkIndexRepository(db)

    def list_indexes(self) -> list[BenchmarkIndex]:
        """列出所有活跃的基准指数。

        Returns:
            按代码升序排列的活跃基准指数列表（已停用的不会返回）
        """
        rows = self._repo.find_active()
        return [BenchmarkIndex(index_code=r.index_code, index_name=r.name_cn) for r in rows]

    def add_index(self, index_code: str, name_cn: str | None = None) -> BenchmarkIndex:
        """添加基准指数。

        先尝试从 AkShare 自动获取名称，失败时使用 name_cn 参数作为手动兜底，
        两者都没有则拒绝添加。

        如果指数已存在但已停用（is_active=False），则重新激活并更新名称。

        Args:
            index_code: 指数代码，如 '000300'
            name_cn: 中文名称（可选，自动获取失败时的兜底值）

        Returns:
            新增或重新激活的基准指数信息

        Raises:
            ValueError: 指数已处于活跃状态、代码无效或无法获取名称
        """
        existing = self._repo.find_by_code(index_code)
        if existing and existing.is_active:
            raise ValueError(f"指数 {index_code} 已存在")

        # 尝试自动获取名称（仅在有网络或 reactivate 需要更新名称时）
        auto_name: str | None = None
        try:
            auto_name = AkShareIndexClient().fetch_index_name(index_code)
        except Exception:
            logger.warning("自动获取指数 %s 名称失败", index_code)

        final_name = auto_name or name_cn
        # reactivate 场景：不强制要求名称
        if existing and not existing.is_active:
            if final_name:
                existing.name_cn = final_name
            existing.is_active = True
            try:
                self._db.commit()
                self._db.refresh(existing)
                logger.info("指数 %s 已重新激活", index_code)
                return BenchmarkIndex(index_code=existing.index_code, index_name=existing.name_cn)
            except Exception:
                self._db.rollback()
                logger.error("重新激活指数 %s 失败", index_code, exc_info=True)
                raise

        # 全新添加场景：必须有名称
        if not final_name:
            raise ValueError(f"无法获取指数 {index_code} 的名称，请手动输入")

        try:
            row = BenchmarkIndexModel(
                index_code=index_code,
                name_cn=final_name,
                exchange="CN",
            )
            self._db.add(row)
            self._db.commit()
            self._db.refresh(row)
            return BenchmarkIndex(index_code=row.index_code, index_name=row.name_cn)
        except Exception:
            self._db.rollback()
            logger.error("添加指数 %s 失败", index_code, exc_info=True)
            raise

    def remove_index(self, index_code: str) -> None:
        """停用基准指数（软删除）。

        将 is_active 设为 False，保留历史数据关联。
        策略引擎和回测服务已通过 is_active=True 过滤，停用后自动不再参与计算。

        Args:
            index_code: 指数代码

        Raises:
            ValueError: 指数不存在或已停用
        """
        row = self._repo.find_by_code(index_code)
        if not row:
            raise ValueError(f"指数 {index_code} 不存在")
        if not row.is_active:
            raise ValueError(f"指数 {index_code} 已停用")
        try:
            row.is_active = False
            self._db.commit()
            logger.info("指数 %s 已停用（软删除）", index_code)
        except Exception:
            self._db.rollback()
            logger.error("停用指数 %s 失败", index_code, exc_info=True)
            raise

    def ensure_index_exists(self, index_code: str, name_cn: str | None = None) -> None:
        """确保活跃指数存在，不存在则自动创建，已停用则重新激活。

        幂等操作，用于 ETF 添加时自动关联跟踪指数。

        Args:
            index_code: 指数代码
            name_cn: 中文名称兜底值
        """
        existing = self._repo.find_by_code(index_code)
        if existing and existing.is_active:
            return
        if existing and not existing.is_active:
            # 已停用则重新激活
            existing.is_active = True
            if name_cn:
                existing.name_cn = name_cn
            try:
                self._db.commit()
                logger.info("指数 %s 已重新激活（通过 ETF 关联）", index_code)
            except Exception:
                self._db.rollback()
                logger.warning("重新激活指数 %s 失败", index_code, exc_info=True)
            return

        auto_name: str | None = None
        try:
            auto_name = AkShareIndexClient().fetch_index_name(index_code)
        except Exception:
            logger.warning("自动获取指数 %s 名称失败", index_code)

        final_name = auto_name or name_cn or index_code
        try:
            row = BenchmarkIndexModel(
                index_code=index_code,
                name_cn=final_name,
                exchange="CN",
            )
            self._db.add(row)
            self._db.commit()
            logger.info("自动添加跟踪指数: %s (%s)", index_code, final_name)
        except Exception:
            self._db.rollback()
            logger.warning("自动添加指数 %s 失败", index_code, exc_info=True)
