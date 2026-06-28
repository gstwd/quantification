"""seed etf_universe with 18 major A-share ETFs

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

SEED_CODES = [
    "510050",
    "510300",
    "510310",
    "510330",
    "510500",
    "510880",
    "512010",
    "512100",
    "512170",
    "512880",
    "588000",
    "159865",
    "159845",
    "159915",
    "159919",
    "159920",
    "159949",
    "159995",
]


def upgrade() -> None:
    etfs = [
        {
            "etf_code": "510050",
            "exchange": "SSE",
            "name_cn": "华夏上证50ETF",
            "tracking_index_code": "000016",
            "tracking_index_name": "上证50",
            "fund_company": "华夏基金",
            "category": "broad_index",
        },
        {
            "etf_code": "510300",
            "exchange": "SSE",
            "name_cn": "华泰柏瑞沪深300ETF",
            "tracking_index_code": "000300",
            "tracking_index_name": "沪深300",
            "fund_company": "华泰柏瑞",
            "category": "broad_index",
        },
        {
            "etf_code": "510310",
            "exchange": "SSE",
            "name_cn": "易方达沪深300ETF",
            "tracking_index_code": "000300",
            "tracking_index_name": "沪深300",
            "fund_company": "易方达基金",
            "category": "broad_index",
        },
        {
            "etf_code": "510330",
            "exchange": "SSE",
            "name_cn": "华夏沪深300ETF",
            "tracking_index_code": "000300",
            "tracking_index_name": "沪深300",
            "fund_company": "华夏基金",
            "category": "broad_index",
        },
        {
            "etf_code": "510500",
            "exchange": "SSE",
            "name_cn": "南方中证500ETF",
            "tracking_index_code": "000905",
            "tracking_index_name": "中证500",
            "fund_company": "南方基金",
            "category": "broad_index",
        },
        {
            "etf_code": "510880",
            "exchange": "SSE",
            "name_cn": "华泰柏瑞红利ETF",
            "tracking_index_code": "000015",
            "tracking_index_name": "红利指数",
            "fund_company": "华泰柏瑞",
            "category": "smart_beta",
        },
        {
            "etf_code": "512010",
            "exchange": "SSE",
            "name_cn": "华夏中证医药ETF",
            "tracking_index_code": "000933",
            "tracking_index_name": "中证医药",
            "fund_company": "华夏基金",
            "category": "sector",
        },
        {
            "etf_code": "512100",
            "exchange": "SSE",
            "name_cn": "南方中证1000ETF",
            "tracking_index_code": "000852",
            "tracking_index_name": "中证1000",
            "fund_company": "南方基金",
            "category": "broad_index",
        },
        {
            "etf_code": "512170",
            "exchange": "SSE",
            "name_cn": "华宝中证医疗ETF",
            "tracking_index_code": "399989",
            "tracking_index_name": "中证医疗",
            "fund_company": "华宝基金",
            "category": "sector",
        },
        {
            "etf_code": "512880",
            "exchange": "SSE",
            "name_cn": "国泰中证全指证券公司ETF",
            "tracking_index_code": "399975",
            "tracking_index_name": "证券公司",
            "fund_company": "国泰基金",
            "category": "sector",
        },
        {
            "etf_code": "588000",
            "exchange": "SSE",
            "name_cn": "华夏上证科创板50成分ETF",
            "tracking_index_code": "000688",
            "tracking_index_name": "科创50",
            "fund_company": "华夏基金",
            "category": "broad_index",
        },
        {
            "etf_code": "159865",
            "exchange": "SZSE",
            "name_cn": "国泰中证畜牧养殖ETF",
            "tracking_index_code": "930707",
            "tracking_index_name": "中证畜牧",
            "fund_company": "国泰基金",
            "category": "sector",
        },
        {
            "etf_code": "159845",
            "exchange": "SZSE",
            "name_cn": "华夏中证科创创业50ETF",
            "tracking_index_code": "931643",
            "tracking_index_name": "科创创业50",
            "fund_company": "华夏基金",
            "category": "broad_index",
        },
        {
            "etf_code": "159915",
            "exchange": "SZSE",
            "name_cn": "易方达创业板ETF",
            "tracking_index_code": "399006",
            "tracking_index_name": "创业板指",
            "fund_company": "易方达基金",
            "category": "broad_index",
        },
        {
            "etf_code": "159919",
            "exchange": "SZSE",
            "name_cn": "嘉实沪深300ETF",
            "tracking_index_code": "000300",
            "tracking_index_name": "沪深300",
            "fund_company": "嘉实基金",
            "category": "broad_index",
        },
        {
            "etf_code": "159920",
            "exchange": "SZSE",
            "name_cn": "华夏恒生ETF",
            "tracking_index_code": "HSI",
            "tracking_index_name": "恒生指数",
            "fund_company": "华夏基金",
            "category": "cross_border",
        },
        {
            "etf_code": "159949",
            "exchange": "SZSE",
            "name_cn": "华安创业板50ETF",
            "tracking_index_code": "399673",
            "tracking_index_name": "创业板50",
            "fund_company": "华安基金",
            "category": "broad_index",
        },
        {
            "etf_code": "159995",
            "exchange": "SZSE",
            "name_cn": "华夏中证半导体ETF",
            "tracking_index_code": "990001",
            "tracking_index_name": "芯片",
            "fund_company": "华夏基金",
            "category": "sector",
        },
    ]

    for e in etfs:
        op.execute(
            sa.text(
                """INSERT INTO etf_universe
                (etf_code, exchange, name_cn, tracking_index_code, tracking_index_name,
                 fund_company, category, is_a_share_etf, is_active, data_source)
                VALUES (:code, :exchange, :name_cn, :idx_code, :idx_name,
                        :company, :category, TRUE, TRUE, 'seed')
                ON CONFLICT (etf_code) DO NOTHING"""
            ).bindparams(
                code=e["etf_code"],
                exchange=e["exchange"],
                name_cn=e["name_cn"],
                idx_code=e["tracking_index_code"],
                idx_name=e["tracking_index_name"],
                company=e["fund_company"],
                category=e["category"],
            )
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM etf_universe WHERE etf_code = ANY(:codes) AND data_source = 'seed'"
        ).bindparams(codes=SEED_CODES)
    )
