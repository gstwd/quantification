"""CLI 命令行工具，提供因子定义初始化、指数种子数据同步等功能。"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from typing import Any

from quant_etf_api.config.logging_config import setup_logging
from quant_etf_api.factors.registry import build_default_factor_registry
from quant_etf_api.infra.clients.akshare_index import _PE_PB_NAME_MAP, _calc_percentile
from quant_etf_api.infra.db.base import SessionLocal
from quant_etf_api.infra.db.models.core import IndexValuationModel
from quant_etf_api.infra.db.repositories.benchmark_index import BenchmarkIndexRepository
from quant_etf_api.services.factor_admin_service import FactorAdminService
from quant_etf_api.services.index_service import IndexService

logger = logging.getLogger(__name__)

# legulegu 估值数据源支持的 12 个指数（index_code → 中文名称）
_DEFAULT_INDEXES: list[tuple[str, str]] = [
    ("000300", "沪深300"),
    ("000016", "上证50"),
    ("000905", "中证500"),
    ("000852", "中证1000"),
    ("000906", "中证800"),
    ("000009", "上证380"),
    ("000010", "上证180"),
    ("399330", "深证100"),
    ("399673", "创业板50"),
    ("399324", "深证红利"),
    ("000015", "上证红利"),
    ("000903", "中证100"),
]


def init_factors() -> None:
    """将代码中的因子元数据同步到数据库。

    同步策略：
    - 代码中有、DB 中没有 → INSERT（新因子）
    - 代码和 DB 都有 → 仅更新 version、required_data
    - DB 中有、代码中没有 → 设为 is_active=False
    """
    setup_logging()
    db = SessionLocal()
    try:
        registry = build_default_factor_registry()
        svc = FactorAdminService(db, registry)
        result = svc.sync_factor_definitions()
        print(
            f"因子定义同步完成: 新增={result['new']} 更新={result['updated']} 停用={result['deactivated']}"
        )
    except Exception:
        logger.error("因子定义同步失败", exc_info=True)
        print("因子定义同步失败，请查看日志", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


def init_indexes() -> None:
    """将默认指数种子数据同步到数据库（幂等，已存在的修正名称）。"""
    setup_logging()
    db = SessionLocal()
    try:
        svc = IndexService(db)
        index_repo = BenchmarkIndexRepository(db)
        added = 0
        updated = 0
        for code, name in _DEFAULT_INDEXES:
            try:
                existing = index_repo.find_by_code(code)
                if existing is None:
                    svc.ensure_index_exists(code, name_cn=name)
                    added += 1
                elif existing.name_cn != name:
                    existing.name_cn = name
                    updated += 1
            except Exception:
                logger.warning("指数 %s 同步异常", code, exc_info=True)
        if updated:
            db.commit()
        print(f"指数种子同步完成: 新增={added} 修正={updated}")
    except Exception:
        logger.error("指数种子同步失败", exc_info=True)
        print("指数种子同步失败，请查看日志", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


def recompute_valuation_percentiles() -> None:
    """按统一口径重算 index_valuation 历史百分位并回填 source。

    B8 修复的一次性数据修复命令（幂等，可重复执行）：
    - 百分位改用 rank / (n - 1) * 100 的统一算法（含当日、最高值可达 100）；
    - source 回填为真实来源（legulegu / csindex）。
    仅基于已入库的 pe/pb 原始值重算，不重新拉取外部数据。
    更新使用 bulk_update_mappings 分批提交，避免远程库逐行 ORM flush 过慢。
    """
    setup_logging()
    db = SessionLocal()
    try:
        rows = (
            db.query(IndexValuationModel)
            .order_by(
                IndexValuationModel.index_code,
                IndexValuationModel.trade_date,
            )
            .all()
        )
        by_code: dict[str, list[IndexValuationModel]] = {}
        for row in rows:
            by_code.setdefault(row.index_code, []).append(row)

        mappings: list[dict[str, Any]] = []
        for index_code, code_rows in by_code.items():
            source = "legulegu" if index_code in _PE_PB_NAME_MAP else "csindex"
            pe_series: list[tuple[date, float]] = []
            pb_series: list[tuple[date, float]] = []
            for r in code_rows:
                if r.pe is not None:
                    pe_series.append((r.trade_date, r.pe))
                if r.pb is not None:
                    pb_series.append((r.trade_date, r.pb))
            pe_map = _calc_percentile(pe_series)
            pb_map = _calc_percentile(pb_series)
            for row in code_rows:
                new_pe_pct = pe_map.get(row.trade_date)
                new_pb_pct = pb_map.get(row.trade_date)
                if (
                    row.pe_percentile != new_pe_pct
                    or row.pb_percentile != new_pb_pct
                    or row.source != source
                ):
                    mappings.append(
                        {
                            "id": row.id,
                            "pe_percentile": new_pe_pct,
                            "pb_percentile": new_pb_pct,
                            "source": source,
                        }
                    )

        batch_size = 5000
        for i in range(0, len(mappings), batch_size):
            db.bulk_update_mappings(IndexValuationModel, mappings[i : i + batch_size])
        db.commit()
        print(f"估值百分位重算完成: 指数={len(by_code)} 更新行={len(mappings)}")
    except Exception:
        db.rollback()
        logger.error("估值百分位重算失败", exc_info=True)
        print("估值百分位重算失败，请查看日志", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


def main() -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="量化研究平台 CLI 工具")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    subparsers.add_parser("init-factors", help="将代码中的因子元数据同步到数据库")
    subparsers.add_parser("init-indexes", help="将默认指数种子数据同步到数据库")
    subparsers.add_parser(
        "recompute-valuation-percentiles",
        help="按统一口径重算 index_valuation 历史百分位并回填 source",
    )

    args = parser.parse_args()

    if args.command == "init-factors":
        init_factors()
    elif args.command == "init-indexes":
        init_indexes()
    elif args.command == "recompute-valuation-percentiles":
        recompute_valuation_percentiles()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
