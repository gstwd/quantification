"""NewsSourceManager 单元测试。"""


from quant_etf_api.infra.news_sources.base import (
    BaseNewsSearchProvider,
    NewsSearchResponse,
    NewsSearchResult,
)
from quant_etf_api.infra.news_sources.manager import NewsSourceManager


# ---- Mock Provider ----


class _MockNewsProvider(BaseNewsSearchProvider):
    """模拟新闻搜索提供者。"""

    def __init__(
        self,
        name: str = "Mock",
        api_keys: list[str] | None = None,
        should_fail: bool = False,
        results: list[NewsSearchResult] | None = None,
    ):
        api_keys = api_keys or ["mock-key"]
        super().__init__(api_keys, name)
        self._should_fail = should_fail
        self._results = results or []

    def _do_search(self, query, api_key, max_results, days=7, **kwargs):
        if self._should_fail:
            return NewsSearchResponse(
                query=query,
                results=[],
                provider=self.name,
                success=False,
                error_message="模拟搜索失败",
            )
        return NewsSearchResponse(
            query=query,
            results=self._results,
            provider=self.name,
            success=True,
        )


class _MockEmptyProvider(BaseNewsSearchProvider):
    """模拟返回空结果的提供者。"""

    def __init__(self, name: str = "Empty"):
        super().__init__(["empty-key"], name)

    def _do_search(self, query, api_key, max_results, days=7, **kwargs):
        return NewsSearchResponse(
            query=query,
            results=[],
            provider=self.name,
            success=True,
        )


def _make_result(title: str, url: str = "") -> NewsSearchResult:
    return NewsSearchResult(
        title=title,
        snippet=f"snippet of {title}",
        url=url or f"https://example.com/{title}",
        source="test",
    )


# ---- Tests ----


class TestNewsSourceManagerInit:
    """测试 NewsSourceManager 初始化。"""

    def test_empty_init(self):
        mgr = NewsSourceManager()
        assert not mgr.is_available
        assert mgr.available_providers == []

    def test_init_with_keys(self):
        mgr = NewsSourceManager(tavily_keys=["tvly-xxx"])
        assert mgr.is_available
        assert "Tavily" in mgr.available_providers

    def test_init_with_empty_keys(self):
        """空字符串 key 应被过滤。"""
        mgr = NewsSourceManager(tavily_keys=["  ", ""])
        assert not mgr.is_available


class TestNewsSearchBaseProvider:
    """测试 BaseNewsSearchProvider 基础功能。"""

    def test_is_available_with_keys(self):
        provider = _MockNewsProvider(api_keys=["key1", "key2"])
        assert provider.is_available

    def test_is_available_without_keys(self):
        """api_keys 为空列表时应不可用。"""
        # _MockNewsProvider 的 __init__ 中 api_keys or ["mock-key"] 会回退，
        # 所以在父类层面测试：空 key 列表不应通过 is_available
        # 直接使用 BaseNewsSearchProvider 子类验证
        class _EmptyProvider(BaseNewsSearchProvider):
            def _do_search(self, query, api_key, max_results, days=7, **kwargs):
                return NewsSearchResponse(query=query, results=[], provider="t", success=True)
        empty = _EmptyProvider([], "EmptyKeys")
        assert not empty.is_available

    def test_round_robin_keys(self):
        provider = _MockNewsProvider(api_keys=["k1", "k2", "k3"], results=[_make_result("n1")])
        # 调用 3 次，应轮询 3 个 key
        for _ in range(3):
            resp = provider.search("test")
            assert resp.success

    def test_key_error_skip(self):
        """错误过多的 Key 应被暂时跳过。"""
        provider = _MockNewsProvider(api_keys=["bad", "good"], name="Test")
        # 记录 bad key 的多次错误
        for _ in range(4):
            provider._record_error("bad")
        # get_next_key 应跳过 bad 返回 good
        next_key = provider._get_next_key()
        assert next_key == "good"


class TestNewsSourceManagerSearch:
    """测试 NewsSourceManager.search()。"""

    def test_single_provider_succeeds(self):
        results = [_make_result("新闻1"), _make_result("新闻2")]
        provider = _MockNewsProvider(api_keys=["k1"], results=results)
        # 直接注入 _providers 避免依赖外部库
        mgr = NewsSourceManager()
        mgr._providers = [provider]

        resp = mgr.search("测试")
        assert resp.success
        assert len(resp.results) == 2
        assert resp.provider == "Mock"

    def test_failover_to_second(self):
        """第一个提供者失败，自动切换到第二个。"""
        fail = _MockNewsProvider(name="Fail", api_keys=["k1"], should_fail=True)
        good = _MockNewsProvider(name="Good", api_keys=["k2"], results=[_make_result("ok")])
        mgr = NewsSourceManager()
        mgr._providers = [fail, good]

        resp = mgr.search("test")
        assert resp.success
        assert resp.provider == "Good"

    def test_all_fail(self):
        fail1 = _MockNewsProvider(name="F1", api_keys=["k1"], should_fail=True)
        fail2 = _MockNewsProvider(name="F2", api_keys=["k2"], should_fail=True)
        mgr = NewsSourceManager()
        mgr._providers = [fail1, fail2]

        resp = mgr.search("test")
        assert not resp.success
        assert "所有新闻源" in (resp.error_message or "")

    def test_empty_provider_skipped(self):
        """结果为空但 success=True 的提供者应被跳过（空 ≠ 成功）。"""
        empty = _MockEmptyProvider()
        good = _MockNewsProvider(name="Good", api_keys=["k2"], results=[_make_result("found")])
        mgr = NewsSourceManager()
        mgr._providers = [empty, good]

        resp = mgr.search("test")
        assert resp.success
        assert resp.provider == "Good"

    def test_no_providers(self):
        mgr = NewsSourceManager()
        resp = mgr.search("test")
        assert not resp.success
        assert "未配置" in (resp.error_message or "")


class TestNewsSourceManagerMultiSearch:
    """测试批量搜索。"""

    def test_search_multi(self):
        results = [_make_result(f"结果{i}") for i in range(3)]
        provider = _MockNewsProvider(api_keys=["k1"], results=results)
        mgr = NewsSourceManager()
        mgr._providers = [provider]

        responses = mgr.search_multi(["q1", "q2", "q3"])
        assert len(responses) == 3
        for r in responses:
            assert r.success
            assert len(r.results) == 3
