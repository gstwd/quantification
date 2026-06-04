"""CLI 命令行工具，提供因子定义初始化等功能。"""

from __future__ import annotations

import argparse
import logging
import sys

from quant_etf_api.config.logging_config import setup_logging
from quant_etf_api.factors.registry import build_default_factor_registry
from quant_etf_api.factors.service import FactorService
from quant_etf_api.infra.db.base import SessionLocal

logger = logging.getLogger(__name__)


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


def main() -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="量化研究平台 CLI 工具")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    subparsers.add_parser("init-factors", help="将代码中的因子元数据同步到数据库")

    args = parser.parse_args()

    if args.command == "init-factors":
        init_factors()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
