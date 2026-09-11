"""统一数据健康快照仓库。"""

from __future__ import annotations

from quant_etf_api.infra.db.models.core import DataHealthSnapshotModel
from quant_etf_api.infra.db.repositories.base import BaseRepository


class DataHealthSnapshotRepository(BaseRepository):
    """数据健康快照只读查询仓库。"""

    def find_one(self, dataset_key: str, partition_key: str) -> DataHealthSnapshotModel | None:
        """查询一个数据集分区的当前健康快照。"""
        return (
            self._db.query(DataHealthSnapshotModel)
            .filter_by(dataset_key=dataset_key, partition_key=partition_key)
            .one_or_none()
        )

    def find_by_dataset_and_partitions(
        self,
        dataset_key: str,
        partition_keys: list[str],
    ) -> dict[str, DataHealthSnapshotModel]:
        """按数据集和分区批量查询当前健康快照。"""
        if not partition_keys:
            return {}
        rows = (
            self._db.query(DataHealthSnapshotModel)
            .filter(
                DataHealthSnapshotModel.dataset_key == dataset_key,
                DataHealthSnapshotModel.partition_key.in_(partition_keys),
            )
            .all()
        )
        return {row.partition_key: row for row in rows}
