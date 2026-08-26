"""资产宇宙解析（纯领域逻辑）。

负责把活跃指数行或原始字典转换为引擎可用的 universe 列表，
并将 universe_filter（all/subset）规则应用到行集合上。
"""

from __future__ import annotations

from typing import Any, Protocol


class UniverseRow(Protocol):
    """universe 行协议：index_code + name_cn。"""

    index_code: str
    name_cn: str


def build_universe_items(
    rows: list[Any],
    index_codes: list[str] | None = None,
) -> list[dict[str, Any]]:
    """将指数行转换为引擎 universe 列表。

    引擎层以 "index_code" 作为资产主键。
    所有 universe 构建统一走此函数，避免各层自行拼接。

    Args:
        rows: 指数行列表（含 index_code/name_cn 属性或 dict）。
        index_codes: 可选过滤，非空时仅保留这些指数。

    Returns:
        universe 字典列表，每项含 index_code/name_cn/category。
    """
    allowed = set(index_codes) if index_codes else None
    items: list[dict[str, Any]] = []
    for r in rows:
        if isinstance(r, dict):
            code = r.get("index_code")
            name = r.get("name_cn", code or "")
        else:
            code = getattr(r, "index_code", None)
            name = getattr(r, "name_cn", code or "")
        if not code:
            continue
        if allowed is not None and code not in allowed:
            continue
        items.append(
            {
                "index_code": code,
                "name_cn": name,
                "category": "broad_index",
            }
        )
    return items


def filter_universe_rows(
    rows: list[Any],
    universe_filter: dict[str, Any],
) -> list[Any]:
    """按 universe_filter 过滤指数行（all/subset 两种模式）。

    Args:
        rows: 指数行列表。
        universe_filter: {"mode": "all"} 或 {"mode": "subset", "index_codes": [...]}。

    Returns:
        过滤后的指数行列表。
    """
    if universe_filter.get("mode") != "subset":
        return rows
    codes = set(universe_filter.get("index_codes") or [])
    if not codes:
        return rows

    def _code_of(r: Any) -> str:
        if isinstance(r, dict):
            return r.get("index_code") or ""
        return getattr(r, "index_code", None) or ""

    return [r for r in rows if _code_of(r) in codes]
