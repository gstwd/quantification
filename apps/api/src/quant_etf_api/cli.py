"""CLI 命令行工具，提供因子定义初始化、指数种子数据同步等功能。"""

from __future__ import annotations

import argparse
import logging
import sys

from quant_etf_api.config.logging_config import setup_logging
from quant_etf_api.factors.registry import build_default_factor_registry
from quant_etf_api.factors.service import FactorService
from quant_etf_api.infra.db.base import SessionLocal
from quant_etf_api.services.index_service import IndexService

logger = logging.getLogger(__name__)

# legulegu 估值数据源支持的 12 个指数（index_code → 中文名称）
_DEFAULT_INDEXES: list[tuple[str, str]] = [
    ("000300", "沪深300"),
    ("000016", "上证50"),
    ("000905", "中证500"),
    ("000852", "中证1000"),
    ("000906", "中证800"),
    ("000009", "中证380"),
    ("000010", "中证180"),
    ("399330", "中证100"),
    ("399673", "创业板50"),
    ("399324", "中证红利"),
    ("000015", "上证红利"),
    ("000903", "上证100"),
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
        svc = FactorService(db, registry)
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
    """将默认指数种子数据同步到数据库（幂等，已存在的跳过）。"""
    setup_logging()
    db = SessionLocal()
    try:
        svc = IndexService(db)
        for code, name in _DEFAULT_INDEXES:
            try:
                svc.ensure_index_exists(code, name_cn=name)
            except Exception:
                logger.warning("指数 %s 同步异常", code, exc_info=True)
        print(f"指数种子同步完成: 共处理 {len(_DEFAULT_INDEXES)} 个指数")
    except Exception:
        logger.error("指数种子同步失败", exc_info=True)
        print("指数种子同步失败，请查看日志", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


def main() -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="量化研究平台 CLI 工具")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    subparsers.add_parser("init-factors", help="将代码中的因子元数据同步到数据库")
    subparsers.add_parser("init-indexes", help="将默认指数种子数据同步到数据库")

    args = parser.parse_args()

    if args.command == "init-factors":
        init_factors()
    elif args.command == "init-indexes":
        init_indexes()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
