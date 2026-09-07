"""指数成分事件表与指数因子参数指纹。

- index_member_event：指数成分股事件（PIT 取样或当前快照），支撑
  rrg_industry_match_score / index_diffusion_ratio 两个指数级因子；
- index_factor_value 增加 params_hash/params 列与
  (trade_date, index_code, factor_id, params_hash) 部分唯一索引，
  与 industry_factor_value 的参数指纹设计对齐，避免同 factor_id
  不同参数互相覆盖。

Revision ID: 0033_index_member_and_generic_factor_params
Revises: 0032_factor_meta_industry_params
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033_index_member_and_generic_factor_params"
down_revision = "0032_factor_meta_industry_params"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建指数成分事件表并为 index_factor_value 增加参数指纹。"""
    op.create_table(
        "index_member_event",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "index_code",
            sa.String(16),
            sa.ForeignKey("benchmark_index.index_code"),
            nullable=False,
            comment="指数代码",
        ),
        sa.Column("stock_code", sa.String(16), nullable=False, comment="个股代码"),
        sa.Column(
            "start_date",
            sa.Date(),
            nullable=False,
            comment="成分生效起始日（PIT 取样日或当前快照日）",
        ),
        sa.Column(
            "end_date",
            sa.Date(),
            nullable=True,
            comment="成分失效日，NULL=截至最新仍有效",
        ),
        sa.Column(
            "weight",
            sa.Float(),
            nullable=True,
            comment="成分权重（0-1），快照源提供权重时使用，否则等权",
        ),
        sa.Column("source", sa.String(32), nullable=False, comment="数据来源"),
        sa.Column(
            "snapshot_type",
            sa.String(16),
            nullable=False,
            comment="pit=按日期取样的历史成分，current_snapshot=当前快照",
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_index_member_event_index_date",
        "index_member_event",
        ["index_code", "start_date"],
    )
    op.create_unique_constraint(
        "uq_index_member_event",
        "index_member_event",
        ["index_code", "stock_code", "start_date"],
    )
    op.add_column(
        "index_factor_value",
        sa.Column(
            "params_hash",
            sa.String(64),
            nullable=False,
            server_default="",
            comment="参数指纹（规范化参数字典 sha256），区分同 factor_id 不同参数计算",
        ),
    )
    op.add_column(
        "index_factor_value",
        sa.Column(
            "params",
            sa.JSON(),
            nullable=True,
            comment="计算参数字典（lookback/smooth 等）",
        ),
    )
    # 重建 builtin 唯一索引：限定 params_hash=''，与参数化变体索引互不重叠
    op.drop_index("uq_index_factor_value_builtin", table_name="index_factor_value")
    op.create_index(
        "uq_index_factor_value_builtin",
        "index_factor_value",
        ["trade_date", "index_code", "factor_id"],
        unique=True,
        postgresql_where=sa.text("strategy_id IS NULL AND params_hash = ''"),
    )
    op.create_index(
        "uq_index_factor_value_params",
        "index_factor_value",
        ["trade_date", "index_code", "factor_id", "params_hash"],
        unique=True,
        postgresql_where=sa.text("strategy_id IS NULL AND params_hash <> ''"),
    )
    # 旧行业轮动策略统一退役禁用（保留历史配置，不再出现在启用列表/调度中）
    op.execute(
        "UPDATE strategy_config SET status='disabled' "
        "WHERE (config_json->>'asset_domain') = 'industry' "
        "OR (config_json::jsonb) ? 'rotation'"
    )


def downgrade() -> None:
    """删除指数成分事件表与参数指纹索引/列。"""
    op.drop_index(
        "uq_index_factor_value_params",
        table_name="index_factor_value",
        postgresql_where=sa.text("strategy_id IS NULL AND params_hash <> ''"),
    )
    op.drop_index("uq_index_factor_value_builtin", table_name="index_factor_value")
    op.create_index(
        "uq_index_factor_value_builtin",
        "index_factor_value",
        ["trade_date", "index_code", "factor_id"],
        unique=True,
        postgresql_where=sa.text("strategy_id IS NULL"),
    )
    op.drop_column("index_factor_value", "params")
    op.drop_column("index_factor_value", "params_hash")
    op.drop_constraint("uq_index_member_event", "index_member_event", type_="unique")
    op.drop_index("ix_index_member_event_index_date", table_name="index_member_event")
    op.drop_table("index_member_event")
