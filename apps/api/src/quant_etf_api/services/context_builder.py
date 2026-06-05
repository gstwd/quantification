"""向后兼容 shim：ContextBuilder 已迁移至 engine/context_builder.py。

保留此模块以避免破坏现有 import 路径。
新代码请直接从 engine.context_builder 导入。
"""

from __future__ import annotations

from quant_etf_api.engine.context_builder import ContextBuilder  # noqa: F401
