"""文本清洗与标准化工具。

从 TrendRadar 项目的 RSS parser 和 URL 工具中提取核心清洗逻辑。
"""

from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


class TextCleaner:
    """文本清洗器，提供标题清洗、URL 规范化和金融相关性判断。

    所有方法均为静态方法，无状态，可直接调用。
    """

    # ---- HTML / 实体处理 ----

    # 常见 HTML 实体映射（非标准实体不在此列表的由 html.unescape 处理）
    _HTML_ENTITIES: dict[str, str] = {
        "&nbsp;": " ",
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&apos;": "'",
        "&#39;": "'",
        "&#x27;": "'",
    }

    # ---- URL 规范化 ----

    # 需要移除的动态/追踪参数（来自 TrendRadar 的 url.py）
    _TRACKING_PARAMS: set[str] = {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "spm",
        "from",
        "source",
        "track_id",
        "ref",
        "refer",
        "share_id",
        "band_rank",  # 微博 band_rank
        "_tj_",
    }

    # ---- 金融关键词词典 ----

    _FINANCE_KEYWORDS: frozenset[str] = frozenset(
        [
            # A 股核心
            "A股",
            "沪指",
            "深指",
            "创业板",
            "科创板",
            "沪深300",
            "上证",
            "大盘",
            "牛市",
            "熊市",
            "涨停",
            "跌停",
            "IPO",
            "上市",
            "退市",
            "股市",
            "股票",
            "证券",
            "交易所",
            # 监管/政策
            "央行",
            "降准",
            "降息",
            "加息",
            "利率",
            "证监会",
            "银保监",
            "政治局",
            "国务院",
            "发改委",
            "财政部",
            "央行行长",
            "货币政策",
            "财政政策",
            "宏观调控",
            # 经济
            "GDP",
            "CPI",
            "PMI",
            "通胀",
            "人民币",
            "汇率",
            "进出口",
            "贸易战",
            "经济",
            "增长",
            "衰退",
            "通缩",
            "关税",
            "制裁",
            "供应链",
            # 行业
            "房地产",
            "新能源",
            "半导体",
            "芯片",
            "人工智能",
            "AI",
            "光伏",
            "锂电",
            "汽车",
            "医药",
            "消费",
            "金融",
            "银行",
            "保险",
            "信托",
            "债券",
            "黄金",
            "原油",
            "大宗商品",
            "煤炭",
            "钢铁",
            "有色",
            "基建",
            "军工",
            "数字经济",
            "机器人",
            "低空经济",
            # 公司/业绩
            "财报",
            "利润",
            "营收",
            "估值",
            "市盈率",
            "分红",
            "回购",
            "减持",
            "增持",
            "净利润",
            "营收增长",
            "亏损",
            "裁员",
            # 市场
            "ETF",
            "基金",
            "量化",
            "外资",
            "北向",
            "南向",
            "融资融券",
            "做空",
            "做多",
            "多头",
            "空头",
            "成交量",
            "放量",
            "缩量",
            # 国际
            "美联储",
            "美联储加息",
            "美债",
            "美元",
            "美股",
            "港股",
            "日股",
            "欧股",
            "新兴市场",
            "华尔街",
            "摩根",
            "高盛",
        ]
    )

    @classmethod
    def clean_title(cls, text: str) -> str:
        """清洗新闻标题：去除 HTML 标签、实体编码、多余空白和控制字符。

        Args:
            text: 原始标题文本。

        Returns:
            清洗后的标题，如果输入为空则返回空字符串。
        """
        if not text:
            return ""

        # 1. 去除 HTML 标签
        cleaned = re.sub(r"<[^>]*>", "", text)

        # 2. 去除 HTML 实体
        for entity, replacement in cls._HTML_ENTITIES.items():
            cleaned = cleaned.replace(entity, replacement)
        cleaned = html.unescape(cleaned)

        # 3. 去除控制字符（保留换行符，后续会处理）
        cleaned = "".join(ch for ch in cleaned if unicodedata.category(ch)[0] != "C" or ch == "\n")

        # 4. 规范化空白
        cleaned = re.sub(r"\s+", " ", cleaned)

        # 5. 去除首尾空白
        return cleaned.strip()

    @classmethod
    def normalize_url(cls, url: str) -> str:
        """规范化 URL：去除追踪参数、标准化 scheme。

        Args:
            url: 原始 URL 字符串。

        Returns:
            规范化后的 URL，输入为空或无效时返回空字符串。
        """
        if not url:
            return ""

        try:
            parsed = urlparse(url.strip())
        except Exception:
            return ""

        # 移除追踪参数
        query_params = parse_qs(parsed.query, keep_blank_values=True)
        clean_params = {
            k: v for k, v in query_params.items() if k.lower() not in cls._TRACKING_PARAMS
        }

        # 重建 URL（移除 fragment）
        clean_query = urlencode(clean_params, doseq=True) if clean_params else ""
        return urlunparse(
            (
                parsed.scheme or "https",
                parsed.netloc,
                parsed.path or "/",
                parsed.params,
                clean_query,
                "",  # 移除 fragment
            )
        )

    @classmethod
    def is_finance_related(cls, text: str) -> bool:
        """快速判断文本是否与金融/A 股市场相关。

        基于预定义金融关键词词典做子串匹配，O(n) 复杂度。

        Args:
            text: 待判断的文本。

        Returns:
            True 表示可能相关（需进一步 AI 分析），False 表示大概率无关。
        """
        if not text:
            return False

        text_upper = text.upper()
        for keyword in cls._FINANCE_KEYWORDS:
            if keyword.upper() in text_upper:
                return True
        return False

    @classmethod
    def truncate(cls, text: str, max_chars: int = 100) -> str:
        """截断文本到指定长度，超出部分以 "..." 替换。

        Args:
            text: 原始文本。
            max_chars: 最大字符数。

        Returns:
            截断后的文本。
        """
        if not text:
            return ""
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rstrip() + "..."
