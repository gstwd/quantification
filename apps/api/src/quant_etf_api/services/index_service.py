from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from quant_etf_api.infra.clients.akshare_index import AkShareIndexClient
from quant_etf_api.infra.db.models.core import BenchmarkIndexModel
from quant_etf_api.schemas.market_data import BenchmarkIndex

logger = logging.getLogger(__name__)


class IndexService:
    """基准指数管理服务，提供指数的增删查功能。"""

    def __init__(self, db: Session) -> None:
        self._db = db

    def list_indexes(self) -> list[BenchmarkIndex]:
        """列出所有基准指数。

        Returns:
            按代码升序排列的基准指数列表
        """
        rows = self._db.query(BenchmarkIndexModel).order_by(BenchmarkIndexModel.index_code).all()
        return [BenchmarkIndex(index_code=r.index_code, index_name=r.name_cn) for r in rows]

    def add_index(self, index_code: str, name_cn: str | None = None) -> BenchmarkIndex:
        """添加基准指数。

        先尝试从 AkShare 自动获取名称，失败时使用 name_cn 参数作为手动兜底，
        两者都没有则拒绝添加。

        Args:
            index_code: 指数代码，如 '000300'
            name_cn: 中文名称（可选，自动获取失败时的兜底值）

        Returns:
            新增的基准指数信息

        Raises:
            ValueError: 指数已存在、代码无效或无法获取名称
        """
        existing = self._db.get(BenchmarkIndexModel, index_code)
        if existing:
            raise ValueError(f"指数 {index_code} 已存在")

        # 尝试自动获取名称
        auto_name: str | None = None
        try:
            auto_name = AkShareIndexClient().fetch_index_name(index_code)
        except Exception:
            logger.warning("自动获取指数 %s 名称失败", index_code)

        final_name = auto_name or name_cn
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
        """删除基准指数。

        Args:
            index_code: 指数代码

        Raises:
            ValueError: 指数不存在
        """
        row = self._db.get(BenchmarkIndexModel, index_code)
        if not row:
            raise ValueError(f"指数 {index_code} 不存在")
        try:
            self._db.delete(row)
            self._db.commit()
        except Exception:
            self._db.rollback()
            logger.error("删除指数 %s 失败", index_code, exc_info=True)
            raise

    def ensure_index_exists(self, index_code: str, name_cn: str | None = None) -> None:
        """确保指数存在，不存在则自动创建。

        幂等操作，用于 ETF 添加时自动关联跟踪指数。

        Args:
            index_code: 指数代码
            name_cn: 中文名称兜底值
        """
        existing = self._db.get(BenchmarkIndexModel, index_code)
        if existing:
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
