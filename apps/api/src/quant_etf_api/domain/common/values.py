"""领域值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DateRange:
    """日期范围值对象。

    Raises:
        ValueError: 起始日期晚于结束日期时抛出。
    """

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"起始日期 {self.start} 不可晚于结束日期 {self.end}")
