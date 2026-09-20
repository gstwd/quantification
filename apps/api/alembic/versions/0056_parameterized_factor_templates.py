"""参数化因子：删除 index_factor_value，factor_definition 增加参数模式

Revision ID: 0056_parameterized_factor_templates
Revises: 0055_execution_model_caliber
Create Date: 2026-09-20

因子值改为按策略实际参数现算，不再持久化：

- 删除 ``index_factor_value`` 表（含唯一约束与两个部分唯一索引）；
  该表是因子值的唯一存储，删除后历史因子值不可恢复。
- ``factor_definition`` 增加 ``parameter_schema`` 列，登记模板的可覆盖参数；
  ``default_params`` 语义收敛为模板固有参数口径。
- 清空 ``factor_definition`` 旧行（固定参数因子定义），由
  ``python -m quant_etf_api.cli init-factors`` 重新写入模板目录。
  ``index_factor_value`` 曾是唯一引用该表的外键，删表后可安全重建目录。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0056_parameterized_factor_templates"
down_revision = "0055_execution_model_caliber"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 因子值表及其索引/约束随表一并删除
    op.drop_table("index_factor_value")

    op.add_column(
        "factor_definition",
        sa.Column(
            "parameter_schema",
            sa.JSON,
            nullable=True,
            comment="模板可覆盖参数声明：参数名 → {type, minimum, maximum, default, description}",
        ),
    )
    op.execute("DELETE FROM factor_definition")


def downgrade() -> None:
    # 结构回滚：重建空的 index_factor_value；历史行数据不可恢复
    op.create_table(
        "index_factor_value",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True, comment="自增主键"),
        sa.Column("trade_date", sa.Date, nullable=False, comment="交易日期"),
        sa.Column(
            "index_code",
            sa.String(32),
            sa.ForeignKey("benchmark_index.index_code"),
            nullable=False,
            comment="指数代码，外键关联 benchmark_index",
        ),
        sa.Column(
            "factor_id",
            sa.String(64),
            sa.ForeignKey("factor_definition.factor_id"),
            nullable=False,
            comment="因子 ID，外键关联 factor_definition",
        ),
        sa.Column("factor_value_numeric", sa.Float, comment="因子数值"),
        sa.Column("factor_value_text", sa.String(128), comment="因子文本值"),
        sa.Column("factor_payload", sa.JSON, comment="因子计算中间数据"),
        sa.Column("strategy_id", sa.String(64), comment="产生该因子值的策略 ID，NULL 表示通用因子"),
        sa.Column(
            "params_hash",
            sa.String(64),
            nullable=False,
            server_default="",
            comment="参数指纹（规范化参数字典 sha256），非参数化因子为空串",
        ),
        sa.Column("params", sa.JSON, comment="计算参数字典，非参数化因子为 NULL"),
        sa.UniqueConstraint(
            "trade_date",
            "index_code",
            "factor_id",
            "strategy_id",
            name="uq_index_factor_value",
        ),
    )
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

    op.drop_column("factor_definition", "parameter_schema")
