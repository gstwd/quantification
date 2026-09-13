"""扩展个股 Tushare 数据字段并新增每日指标与资金流向表。

Revision ID: 0045_tushare_stock_data
Revises: 0044_robustness_and_lifecycle
Create Date: 2026-09-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0045_tushare_stock_data"
down_revision = "0044_robustness_and_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """扩展个股表并为 Tushare 每日指标、资金流向建表。"""
    op.add_column(
        "stock_universe",
        sa.Column("ts_code", sa.String(length=16), nullable=True, comment="Tushare 股票代码"),
    )
    op.add_column(
        "stock_universe",
        sa.Column("market", sa.String(length=16), nullable=True, comment="Tushare 市场类别"),
    )
    op.add_column(
        "stock_universe",
        sa.Column("exchange", sa.String(length=8), nullable=True, comment="Tushare 交易所代码"),
    )
    op.create_index(
        "uq_stock_universe_ts_code",
        "stock_universe",
        ["ts_code"],
        unique=True,
    )

    op.add_column(
        "stock_daily_close",
        sa.Column("open", sa.Float(), nullable=True, comment="开盘价（元）"),
    )
    op.add_column(
        "stock_daily_close",
        sa.Column("high", sa.Float(), nullable=True, comment="最高价（元）"),
    )
    op.add_column(
        "stock_daily_close",
        sa.Column("low", sa.Float(), nullable=True, comment="最低价（元）"),
    )
    op.add_column(
        "stock_daily_close",
        sa.Column("pre_close", sa.Float(), nullable=True, comment="除权后昨收价（元）"),
    )
    op.add_column(
        "stock_daily_close",
        sa.Column("change", sa.Float(), nullable=True, comment="涨跌额（元）"),
    )
    op.add_column(
        "stock_daily_close",
        sa.Column("pct_chg", sa.Float(), nullable=True, comment="涨跌幅，单位 %"),
    )
    op.add_column(
        "stock_daily_close",
        sa.Column("vol", sa.Float(), nullable=True, comment="成交量（手，Tushare 原生口径）"),
    )
    op.add_column(
        "stock_daily_close",
        sa.Column("amount", sa.Float(), nullable=True, comment="成交额（千元，Tushare 原生口径）"),
    )
    op.add_column(
        "stock_daily_close",
        sa.Column("ah_vol", sa.Float(), nullable=True, comment="盘后成交量（手）"),
    )
    op.add_column(
        "stock_daily_close",
        sa.Column("ah_amount", sa.Float(), nullable=True, comment="盘后成交额（千元）"),
    )
    op.add_column(
        "stock_daily_close",
        sa.Column("adj_factor", sa.Float(), nullable=True, comment="Tushare 复权因子"),
    )
    op.alter_column(
        "stock_daily_close",
        "source",
        existing_type=sa.String(length=32),
        server_default="tushare",
        comment="数据来源",
    )

    op.create_table(
        "stock_daily_basic",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, comment="自增主键"),
        sa.Column("trade_date", sa.Date(), nullable=False, comment="交易日期"),
        sa.Column("stock_code", sa.String(length=16), nullable=False, comment="个股代码"),
        sa.Column("close", sa.Float(), nullable=True, comment="当日收盘价（元）"),
        sa.Column("turnover_rate", sa.Float(), nullable=True, comment="换手率，单位 %"),
        sa.Column("turnover_rate_f", sa.Float(), nullable=True, comment="自由流通换手率，单位 %"),
        sa.Column("volume_ratio", sa.Float(), nullable=True, comment="量比"),
        sa.Column("pe", sa.Float(), nullable=True, comment="市盈率"),
        sa.Column("pe_ttm", sa.Float(), nullable=True, comment="市盈率 TTM"),
        sa.Column("pb", sa.Float(), nullable=True, comment="市净率"),
        sa.Column("ps", sa.Float(), nullable=True, comment="市销率"),
        sa.Column("ps_ttm", sa.Float(), nullable=True, comment="市销率 TTM"),
        sa.Column("dv_ratio", sa.Float(), nullable=True, comment="股息率，单位 %"),
        sa.Column("dv_ttm", sa.Float(), nullable=True, comment="股息率 TTM，单位 %"),
        sa.Column("total_share", sa.Float(), nullable=True, comment="总股本（万股）"),
        sa.Column("float_share", sa.Float(), nullable=True, comment="流通股本（万股）"),
        sa.Column("free_share", sa.Float(), nullable=True, comment="自由流通股本（万股）"),
        sa.Column("total_mv", sa.Float(), nullable=True, comment="总市值（万元）"),
        sa.Column("circ_mv", sa.Float(), nullable=True, comment="流通市值（万元）"),
        sa.Column("limit_status", sa.Integer(), nullable=True, comment="收盘涨跌状态"),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default="tushare",
            comment="数据来源",
        ),
        sa.Column("ingested_at", sa.DateTime(), nullable=True, comment="数据入库时间（UTC）"),
        sa.UniqueConstraint("trade_date", "stock_code", name="uq_stock_daily_basic"),
    )
    op.create_index(
        "ix_stock_daily_basic_code_date",
        "stock_daily_basic",
        ["stock_code", "trade_date"],
    )

    op.create_table(
        "stock_moneyflow",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, comment="自增主键"),
        sa.Column("trade_date", sa.Date(), nullable=False, comment="交易日期"),
        sa.Column("stock_code", sa.String(length=16), nullable=False, comment="个股代码"),
        sa.Column("buy_sm_vol", sa.BigInteger(), nullable=True, comment="小单买入量（手）"),
        sa.Column("buy_sm_amount", sa.Float(), nullable=True, comment="小单买入金额（万元）"),
        sa.Column("sell_sm_vol", sa.BigInteger(), nullable=True, comment="小单卖出量（手）"),
        sa.Column("sell_sm_amount", sa.Float(), nullable=True, comment="小单卖出金额（万元）"),
        sa.Column("buy_md_vol", sa.BigInteger(), nullable=True, comment="中单买入量（手）"),
        sa.Column("buy_md_amount", sa.Float(), nullable=True, comment="中单买入金额（万元）"),
        sa.Column("sell_md_vol", sa.BigInteger(), nullable=True, comment="中单卖出量（手）"),
        sa.Column("sell_md_amount", sa.Float(), nullable=True, comment="中单卖出金额（万元）"),
        sa.Column("buy_lg_vol", sa.BigInteger(), nullable=True, comment="大单买入量（手）"),
        sa.Column("buy_lg_amount", sa.Float(), nullable=True, comment="大单买入金额（万元）"),
        sa.Column("sell_lg_vol", sa.BigInteger(), nullable=True, comment="大单卖出量（手）"),
        sa.Column("sell_lg_amount", sa.Float(), nullable=True, comment="大单卖出金额（万元）"),
        sa.Column("buy_elg_vol", sa.BigInteger(), nullable=True, comment="特大单买入量（手）"),
        sa.Column("buy_elg_amount", sa.Float(), nullable=True, comment="特大单买入金额（万元）"),
        sa.Column("sell_elg_vol", sa.BigInteger(), nullable=True, comment="特大单卖出量（手）"),
        sa.Column("sell_elg_amount", sa.Float(), nullable=True, comment="特大单卖出金额（万元）"),
        sa.Column("net_mf_vol", sa.BigInteger(), nullable=True, comment="净流入量（手）"),
        sa.Column("net_mf_amount", sa.Float(), nullable=True, comment="净流入额（万元）"),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default="tushare",
            comment="数据来源",
        ),
        sa.Column("ingested_at", sa.DateTime(), nullable=True, comment="数据入库时间（UTC）"),
        sa.UniqueConstraint("trade_date", "stock_code", name="uq_stock_moneyflow"),
    )
    op.create_index(
        "ix_stock_moneyflow_code_date",
        "stock_moneyflow",
        ["stock_code", "trade_date"],
    )


def downgrade() -> None:
    """删除 Tushare 个股明细表与扩展列。"""
    op.drop_index("ix_stock_moneyflow_code_date", table_name="stock_moneyflow")
    op.drop_table("stock_moneyflow")
    op.drop_index("ix_stock_daily_basic_code_date", table_name="stock_daily_basic")
    op.drop_table("stock_daily_basic")
    for column in (
        "adj_factor",
        "ah_amount",
        "ah_vol",
        "amount",
        "vol",
        "pct_chg",
        "change",
        "pre_close",
        "low",
        "high",
        "open",
    ):
        op.drop_column("stock_daily_close", column)
    op.alter_column(
        "stock_daily_close",
        "source",
        existing_type=sa.String(length=32),
        server_default=None,
        comment="数据来源",
    )
    op.drop_index("uq_stock_universe_ts_code", table_name="stock_universe")
    op.drop_column("stock_universe", "exchange")
    op.drop_column("stock_universe", "market")
    op.drop_column("stock_universe", "ts_code")
