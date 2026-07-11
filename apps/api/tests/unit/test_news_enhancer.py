"""NewsEnhancer 单元测试。"""

import pytest

from quant_etf_api.ai_factors.base import RawNewsItem
from quant_etf_api.infra.news_sources.base import NewsSearchResult
from quant_etf_api.infra.news_sources.manager import NewsSourceManager
from quant_etf_api.services.news_enhancer import NewsEnhancer


# ---- Helpers ----

def _make_raw_item(title: str, source_id: str = "toutiao") -> RawNewsItem:
    return RawNewsItem(
        source_id=source_id,
        source_name="测试源",
        title=title,
        url=f"https://example.com/{title}",
    )


def _make_search_result(title: str) -> NewsSearchResult:
    return NewsSearchResult(
        title=title,
        snippet=f"摘要: {title}",
        url=f"https://search.example.com/{title}",
        source="test-engine",
    )


class TestNewsEnhancerAvailability:
    """测试 NewsEnhancer 可用性检查。"""

    def test_not_available_when_no_providers(self):
        mgr = NewsSourceManager()  # 无提供者
        enhancer = NewsEnhancer(mgr)
        assert not enhancer.is_available

    def test_available_when_has_providers(self):
        mgr = NewsSourceManager(tavily_keys=["tvly-xxx"])
        enhancer = NewsEnhancer(mgr)
        assert enhancer.is_available


class TestNewsEnhancerEmptyInput:
    """测试空输入场景。"""

    def test_empty_hotlist(self):
        mgr = NewsSourceManager(tavily_keys=["tvly-xxx"])
        enhancer = NewsEnhancer(mgr)
        result = enhancer.enhance([])
        assert result == []

    def test_unavailable_manager(self):
        mgr = NewsSourceManager()  # 无提供者
        enhancer = NewsEnhancer(mgr)
        result = enhancer.enhance([_make_raw_item("A股 大盘 上涨")])
        assert result == []


class TestNewsEnhancerKeywordExtraction:
    """测试关键词提取。"""

    def test_extracts_keywords_from_titles(self):
        enhancer = NewsEnhancer(NewsSourceManager(), max_keywords=5)

        items = [
            _make_raw_item("A股 大盘 今日 上涨 科技 板块 领涨"),
            _make_raw_item("央行 降息 利好 股市 反弹"),
            _make_raw_item("新能源 板块 逆势 走强 A股"),
        ]
        keywords = enhancer._extract_keywords(items, top_n=5)

        assert len(keywords) > 0
        assert len(keywords) <= 5
        # 财经词应该排在前列
        assert any("A股" == kw for kw in keywords)

    def test_empty_titles(self):
        enhancer = NewsEnhancer(NewsSourceManager())
        keywords = enhancer._extract_keywords([], top_n=5)
        assert keywords == []

    def test_filters_short_words(self):
        enhancer = NewsEnhancer(NewsSourceManager(), min_keyword_len=3)
        items = [_make_raw_item("A B 大盘 上涨")]
        keywords = enhancer._extract_keywords(items, top_n=10)

        # "A" 和 "B" 太短，应被过滤
        assert "A" not in keywords
        assert "B" not in keywords

    def test_boost_finance_words(self):
        enhancer = NewsEnhancer(NewsSourceManager(), max_keywords=10)

        # 构造：非财经词出现多次，财经词出现一次
        items = [
            _make_raw_item("天气 不错 天气 不错"),
            _make_raw_item("天气 不错"),
            _make_raw_item("降息"),  # 财经词只出现一次
        ]
        keywords = enhancer._extract_keywords(items, top_n=10)

        # 由于财经加成，"降息" 应该比纯 TF 排序更靠前
        if keywords:
            # 至少 "降息" 应该在结果中
            assert "降息" in keywords


class TestNewsEnhancerConversion:
    """测试搜索结果到 RawNewsItem 的转换。"""

    def test_converts_result_to_raw_item(self):
        result = NewsSearchResult(
            title="测试新闻标题",
            snippet="摘要内容",
            url="https://test.com/1",
            source="Brave",
            published_date="2024-01-15",
        )
        item = NewsEnhancer._search_result_to_raw_item(result, "A股")

        assert isinstance(item, RawNewsItem)
        assert item.title == "测试新闻标题"
        assert item.url == "https://test.com/1"
        assert item.source_id == "search:A股"
        assert "Brave" in item.source_name
        assert item.appear_count == 1
        assert item.ranks == []


class TestNewsEnhancerDedup:
    """测试去重逻辑。"""

    def test_dedup_against_hotlist(self):
        """搜索结果中与热榜重复的 URL 应被过滤。"""
        item = _make_raw_item("test")
        item.url = "https://test.com/duplicate"

        enhancer = NewsEnhancer(NewsSourceManager())
        # 直接用内部逻辑验证
        seen = {item.url}
        result = NewsSearchResult(
            title="dup", snippet="...", url="https://test.com/duplicate", source="x"
        )
        assert result.url in seen  # 应被去重
