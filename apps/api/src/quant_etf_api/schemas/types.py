from __future__ import annotations
from datetime import datetime, timezone
from typing import Annotated

from pydantic import AwareDatetime, BeforeValidator


def _ensure_utc(v: object) -> datetime:
    """将 naive datetime 或 ISO 字符串标记为 UTC，确保序列化时带时区后缀。

    SQLAlchemy 的 DateTime 列读回的是 naive datetime（无 tzinfo），
    该函数将其统一标记为 UTC，让 Pydantic 序列化时输出 +00:00 后缀。
    """
    if isinstance(v, str):
        dt = datetime.fromisoformat(v)
    elif isinstance(v, datetime):
        dt = v
    else:
        raise TypeError(f"期望 datetime 或 str，实际为 {type(v).__name__}")
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# 用于所有时间戳字段的类型别名：输入可以是 naive UTC datetime，输出序列化带 +00:00
UtcDatetime = Annotated[AwareDatetime, BeforeValidator(_ensure_utc)]
