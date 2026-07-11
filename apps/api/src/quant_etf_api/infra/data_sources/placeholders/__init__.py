"""占位符子包 — 未来数据源的预留占位适配器。

占位符适配器声明了预期支持的能力，但当前不实现实际数据获取。
当对应的数据源凭证配置后，is_available 返回 True，但方法调用会抛出
明确的 DataSourceUnavailableError，提示该数据源尚未实现。
"""

from __future__ import annotations
