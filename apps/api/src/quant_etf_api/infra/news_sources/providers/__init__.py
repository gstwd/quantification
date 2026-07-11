"""搜索提供者子包 — 各新闻搜索引擎的具体实现。

每个提供者仅在配置了对应 API Key 时激活（is_available=True）。
所有提供者遵循 BaseNewsSearchProvider 接口。
"""

from __future__ import annotations
