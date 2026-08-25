from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy.orm import Session

from quant_etf_api.infra.clients.akshare_fund import (
    AkShareFundClient,
    map_fund_type_to_category,
)
from quant_etf_api.infra.clients.akshare_index import AkShareIndexClient
from quant_etf_api.infra.db.base import utcnow
from quant_etf_api.infra.db.models.core import EtfUniverseModel
from quant_etf_api.infra.db.repositories.etf_universe import EtfUniverseRepository
from quant_etf_api.schemas.etf import EtfCreateRequest, EtfDetail
from quant_etf_api.services.index_service import IndexService
from quant_etf_api.services.run_service import RunService

logger = logging.getLogger(__name__)


def _parse_date(date_str: str | None) -> date | None:
    """将 'YYYY-MM-DD' 格式字符串解析为 date 对象。

    Args:
        date_str: 日期字符串

    Returns:
        date 对象，解析失败时返回 None
    """
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def detect_exchange(etf_code: str) -> str:
    if etf_code.startswith(("5", "6")):
        return "SSE"
    if etf_code.startswith(("1", "3")):
        return "SZSE"
    raise ValueError(f"无法识别交易所，ETF 代码前缀不合法: {etf_code}")


def _row_to_detail(row: EtfUniverseModel) -> EtfDetail:
    return EtfDetail(
        etf_code=row.etf_code,
        exchange=row.exchange,
        name_cn=row.name_cn,
        fund_full_name=row.fund_full_name,
        tracking_index_code=row.tracking_index_code,
        tracking_index_name=row.tracking_index_name,
        fund_company=row.fund_company,
        listing_date=row.listing_date,
        data_source=row.data_source,
        updated_at=row.updated_at,
        is_active=row.is_active,
        is_a_share_etf=row.is_a_share_etf,
        category=row.category,
    )


class UniverseService:
    def __init__(
        self,
        db: Session,
        universe_repo: EtfUniverseRepository | None = None,
        run_svc: RunService | None = None,
    ) -> None:
        """初始化 ETF 池服务。

        Args:
            db: SQLAlchemy 同步 Session。
            universe_repo: ETF 池仓库，未提供时自动创建。
            run_svc: 运行记录服务，未提供时自动创建。
        """
        self._db = db
        self._universe_repo = universe_repo or EtfUniverseRepository(db)
        self._run_svc = run_svc or RunService(db)

    def list_etfs(self, offset: int = 0, limit: int = 50) -> tuple[list[EtfDetail], int]:
        """分页查询活跃 ETF 列表。

        Args:
            offset: 偏移量。
            limit: 每页最大条数。

        Returns:
            (items, total) 元组。
        """
        try:
            rows, total = self._universe_repo.find_active_paginated(offset=offset, limit=limit)
            return [_row_to_detail(r) for r in rows], total
        except Exception:
            logger.warning("list_etfs DB query failed", exc_info=True)
            return [], 0

    def get_etf(self, etf_code: str) -> EtfDetail | None:
        try:
            row = self._universe_repo.find_by_code(etf_code)
            return _row_to_detail(row) if row else None
        except Exception:
            logger.warning("DB query failed for etf_code=%s", etf_code, exc_info=True)
            return None

    def add_etf(self, req: EtfCreateRequest) -> EtfDetail:
        """添加 ETF 到研究池。

        自动从 AkShare 获取基金档案信息（名称、公司、成立日期、跟踪指数等），
        获取失败时使用代码作为名称兜底。如果获取到跟踪指数，会自动将其加入基准指数表。

        Args:
            req: 包含 etf_code 的创建请求

        Returns:
            新增的 EtfDetail

        Raises:
            ValueError: ETF 已存在或代码格式不合法
        """
        try:
            row = self._db.get(EtfUniverseModel, req.etf_code)
            if row and row.is_active:
                raise ValueError(f"ETF {req.etf_code} 已存在")

            # 从 AkShare 拉取基金档案信息，失败时使用代码作为名称兜底
            try:
                info = AkShareFundClient().fetch_etf_info(req.etf_code)
            except Exception:
                logger.warning("获取 %s 基金信息失败，使用默认值", req.etf_code)
                info = None

            name_cn = info.name_cn if info else req.etf_code
            fund_full_name = info.fund_full_name if info else req.etf_code
            fund_company = info.fund_company if info else None
            tracking_index_name = info.tracking_index_name or "未知指数" if info else "未知指数"
            category = map_fund_type_to_category(info.fund_type) if info else "broad_index"
            listing_date = _parse_date(info.establishment_date) if info else None

            # 通过跟踪指数名称反查指数代码
            tracking_index_code: str | None = None
            if info and info.tracking_index_name:
                try:
                    tracking_index_code = AkShareIndexClient().find_index_code_by_name(
                        info.tracking_index_name
                    )
                except Exception:
                    logger.warning("反查跟踪指数代码失败: %s", info.tracking_index_name)

            if row:
                # 重新激活已下架的 ETF 并更新信息
                row.is_active = True
                row.name_cn = name_cn
                row.fund_full_name = fund_full_name
                row.fund_company = fund_company
                row.tracking_index_name = tracking_index_name
                row.tracking_index_code = tracking_index_code
                row.category = category
                row.listing_date = listing_date
                row.data_source = "manual"
            else:
                exchange = detect_exchange(req.etf_code)
                row = EtfUniverseModel(
                    etf_code=req.etf_code,
                    exchange=exchange,
                    name_cn=name_cn,
                    fund_full_name=fund_full_name,
                    tracking_index_name=tracking_index_name,
                    tracking_index_code=tracking_index_code,
                    fund_company=fund_company,
                    category=category,
                    listing_date=listing_date,
                    is_active=True,
                    is_a_share_etf=True,
                    data_source="manual",
                )
                self._db.add(row)
            self._db.commit()
            self._db.refresh(row)

            # 自动将跟踪指数加入基准指数表
            if row.tracking_index_code:
                try:
                    IndexService(self._db).ensure_index_exists(
                        row.tracking_index_code, row.tracking_index_name
                    )
                except Exception:
                    logger.warning(
                        "自动关联跟踪指数 %s 失败", row.tracking_index_code, exc_info=True
                    )

            # 后台拉取该 ETF 从成立至今的全量历史日线：统一走任务队列，
            # 不再裸启 daemon 线程（与 P2 的统一后台任务模型保持一致）
            etf_code_for_bg = req.etf_code
            from quant_etf_api.infra.job_queue.queue import get_job_queue

            get_job_queue().enqueue(
                "data_fill",
                {"resource": "bars", "code": etf_code_for_bg},
                job_key=f"bars:{etf_code_for_bg}",
                max_attempts=2,
            )

            return _row_to_detail(row)
        except ValueError:
            raise
        except Exception:
            self._db.rollback()
            logger.error("添加 ETF %s 失败", req.etf_code, exc_info=True)
            raise

    def remove_etf(self, etf_code: str) -> None:
        try:
            row = self._db.get(EtfUniverseModel, etf_code)
            if not row or not row.is_active:
                raise ValueError(f"ETF {etf_code} 不存在或已下架")
            row.is_active = False
            self._db.commit()
        except ValueError:
            raise
        except Exception:
            self._db.rollback()
            logger.error("Failed to remove ETF %s", etf_code, exc_info=True)
            raise

    def refresh_all(self, run_id: str) -> None:
        """遍历活跃 ETF 池，从 AkShare 刷新元数据并更新变更字段。

        每只 ETF 的处理结果写入 ResearchRunItem，
        全部完成后更新 research_run 状态与指标。
        """
        start_time = utcnow()
        try:
            self._run_svc.mark_running(run_id)

            etfs = self._universe_repo.find_all_active()

            if not etfs:
                self._run_svc.mark_success(run_id, metrics={"total": 0, "message": "无活跃 ETF"})
                return

            updated_count = 0
            unchanged_count = 0
            failed_count = 0

            for etf in etfs:
                etf_code = etf.etf_code
                item_status = "success"
                item_message = ""

                try:
                    info = AkShareFundClient().fetch_etf_info(etf_code)
                    if info is None:
                        item_status = "skipped"
                        item_message = "未获取到基金信息"
                        unchanged_count += 1
                    else:
                        changed = False
                        changes: list[str] = []

                        if info.name_cn and info.name_cn != etf.name_cn:
                            changes.append(f"名称: {etf.name_cn} → {info.name_cn}")
                            etf.name_cn = info.name_cn
                            changed = True
                        if info.fund_full_name and info.fund_full_name != etf.fund_full_name:
                            changes.append(f"全称: {etf.fund_full_name} → {info.fund_full_name}")
                            etf.fund_full_name = info.fund_full_name
                            changed = True
                        if info.fund_company and info.fund_company != etf.fund_company:
                            changes.append(f"基金公司: {etf.fund_company} → {info.fund_company}")
                            etf.fund_company = info.fund_company
                            changed = True
                        if (
                            info.tracking_index_name
                            and info.tracking_index_name != etf.tracking_index_name
                        ):
                            changes.append(
                                f"跟踪指数: {etf.tracking_index_name} → {info.tracking_index_name}"
                            )
                            etf.tracking_index_name = info.tracking_index_name
                            changed = True

                        new_category = map_fund_type_to_category(info.fund_type)
                        if new_category != "other" and new_category != etf.category:
                            changes.append(f"类别: {etf.category} → {new_category}")
                            etf.category = new_category
                            changed = True

                        new_listing_date = _parse_date(info.establishment_date)
                        if new_listing_date and new_listing_date != etf.listing_date:
                            changes.append(f"成立日期: {etf.listing_date} → {new_listing_date}")
                            etf.listing_date = new_listing_date
                            changed = True

                        if changed:
                            etf.updated_at = utcnow()
                            item_message = "; ".join(changes)
                            updated_count += 1
                        else:
                            item_message = "无变化"
                            unchanged_count += 1

                        self._db.commit()
                except Exception as e:
                    self._db.rollback()
                    item_status = "failed"
                    item_message = str(e)[:500]
                    failed_count += 1
                    logger.warning("ETF %s 元数据刷新失败: %s", etf_code, e)

                try:
                    from quant_etf_api.infra.db.repositories.research_run import (
                        ResearchRunRepository,
                    )

                    ResearchRunRepository(self._db).add_item(
                        run_id=run_id,
                        etf_code=etf_code,
                        status=item_status,
                        message=item_message or None,
                    )
                except Exception:
                    self._db.rollback()
                    logger.warning("写入 ResearchRunItem 失败: %s", etf_code, exc_info=True)

            self._run_svc.mark_success(
                run_id,
                metrics={
                    "total": len(etfs),
                    "updated": updated_count,
                    "unchanged": unchanged_count,
                    "failed": failed_count,
                    "duration_seconds": round((utcnow() - start_time).total_seconds(), 1),
                },
            )

        except Exception as e:
            self._db.rollback()
            logger.warning("refresh_all 整体失败: %s", e, exc_info=True)
            self._run_svc.mark_failed(run_id, str(e)[:1000])
