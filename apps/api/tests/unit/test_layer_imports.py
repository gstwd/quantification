"""分层导入守卫：把 AGENTS.md「架构与职责」里的层间依赖方向固定成可执行检查。

用 AST 扫描 ``src/quant_etf_api`` 下各层的全部 import（含函数体内的延迟导入、
相对导入），按层规则判定违例：

- ``domain/``：纯规则，只能依赖本层与第三方计算库；禁止 SQLAlchemy、FastAPI，以及
  ``quant_etf_api`` 下除 ``domain`` 外的任何模块；
- ``engine/``、``factors/``：不得依赖服务层、HTTP 层与 CLI；
- ``services/``：不得依赖 HTTP 层（FastAPI / ``api``）与 CLI 入口；
- ``infra/``（任务队列处理器与调度器除外——它们按设计向服务层分派任务）：不得依赖
  服务层、引擎、因子层、HTTP 层与 CLI。

``_KNOWN_EXCEPTIONS`` 是冻结时已存在的历史例外，只允许减少、不允许新增；某条例外
被消除后必须同步删除，否则 ``test_known_exceptions_still_exist`` 失败。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

PACKAGE = "quant_etf_api"
PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / PACKAGE

_HTTP = ("fastapi", "starlette")
_ENTRYPOINTS = (
    f"{PACKAGE}.api",
    f"{PACKAGE}.cli",
    f"{PACKAGE}.cli_pool",
    f"{PACKAGE}.main",
    f"{PACKAGE}.worker",
)


@dataclass(frozen=True)
class LayerRule:
    """单层导入规则。

    Attributes:
        layer: 层目录（相对包根，如 ``domain``）。
        forbidden: 禁止导入的模块前缀。
        allowed_internal: 非空时，本包内只允许导入这些前缀（白名单模式）。
        exempt: 层内豁免本规则的子目录（相对包根）。
    """

    layer: str
    forbidden: tuple[str, ...]
    allowed_internal: tuple[str, ...] = ()
    exempt: tuple[str, ...] = ()


_RULES: tuple[LayerRule, ...] = (
    LayerRule(
        layer="domain",
        forbidden=("sqlalchemy", *_HTTP),
        allowed_internal=(f"{PACKAGE}.domain",),
    ),
    LayerRule(
        layer="engine",
        forbidden=(*_HTTP, f"{PACKAGE}.services", *_ENTRYPOINTS),
    ),
    LayerRule(
        layer="factors",
        forbidden=(*_HTTP, f"{PACKAGE}.services", *_ENTRYPOINTS),
    ),
    LayerRule(
        layer="services",
        forbidden=(*_HTTP, *_ENTRYPOINTS),
    ),
    LayerRule(
        layer="infra",
        forbidden=(
            *_HTTP,
            f"{PACKAGE}.services",
            f"{PACKAGE}.engine",
            f"{PACKAGE}.factors",
            *_ENTRYPOINTS,
        ),
        exempt=("infra/job_queue", "infra/scheduler"),
    ),
)

# 冻结时已存在的历史例外：(源文件相对包根路径, 被导入模块)。只减不增。
_KNOWN_EXCEPTIONS: frozenset[tuple[str, str]] = frozenset(
    {
        # 成分面板因子现算时延迟导入面板服务（因子层 → 服务层）
        ("factors/compute.py", f"{PACKAGE}.services.index_factor_panel_service"),
        # 稳健性扫描并行执行复用 CLI 子进程池（服务层 → CLI）
        ("services/robustness_service.py", f"{PACKAGE}.cli_pool"),
    }
)


def _module_name(path: Path) -> str:
    """把源文件路径转换为点分模块名。

    Args:
        path: 包内源文件路径。

    Returns:
        形如 ``quant_etf_api.domain.x`` 的模块名（``__init__`` 取所在包名）。
    """
    parts = [PACKAGE, *path.relative_to(PACKAGE_ROOT).with_suffix("").parts]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(path: Path, source: str | None = None) -> set[str]:
    """收集一个源文件的全部导入目标（含延迟导入，相对导入解析为绝对名）。

    Args:
        path: 源文件路径（决定模块名与相对导入的解析基准）。
        source: 可选的源码文本；缺省时读取 ``path``（自检用例传入虚拟源码）。

    Returns:
        被导入模块的点分名集合；``from quant_etf_api import services`` 这类从包根导入
        子包的写法展开为 ``quant_etf_api.services``，其余 ``from x import y`` 记为 ``x``。
    """
    text = source if source is not None else path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    module = _module_name(path)
    package = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base_parts = package.split(".")[: len(package.split(".")) - (node.level - 1)]
                base = ".".join([*base_parts, node.module] if node.module else base_parts)
            else:
                base = node.module or ""
            if base == PACKAGE:
                found.update(f"{base}.{alias.name}" for alias in node.names)
            else:
                found.add(base)
    return found


def _matches(name: str, prefix: str) -> bool:
    """判断模块名是否落在前缀之下（按点分段匹配，``cli`` 不匹配 ``cli_pool``）。"""
    return name == prefix or name.startswith(prefix + ".")


def _violations(rule: LayerRule) -> set[tuple[str, str]]:
    """按规则扫描一层，返回 (源文件, 被导入模块) 违例集合。

    Args:
        rule: 层规则。

    Returns:
        违例集合。
    """
    hits: set[tuple[str, str]] = set()
    for path in sorted((PACKAGE_ROOT / rule.layer).rglob("*.py")):
        rel = path.relative_to(PACKAGE_ROOT).as_posix()
        if any(rel.startswith(prefix + "/") for prefix in rule.exempt):
            continue
        for name in _imports(path):
            forbidden = any(_matches(name, prefix) for prefix in rule.forbidden)
            outside_whitelist = (
                bool(rule.allowed_internal)
                and _matches(name, PACKAGE)
                and not any(_matches(name, prefix) for prefix in rule.allowed_internal)
            )
            if forbidden or outside_whitelist:
                hits.add((rel, name))
    return hits


class TestLayerImports:
    """层间依赖方向守卫。"""

    @pytest.mark.parametrize("rule", _RULES, ids=lambda r: r.layer)
    def test_no_new_layer_violations(self, rule: LayerRule) -> None:
        """各层不得新增违反依赖方向的导入（历史例外见 ``_KNOWN_EXCEPTIONS``）。"""
        new = sorted(_violations(rule) - _KNOWN_EXCEPTIONS)
        assert not new, (
            f"{rule.layer}/ 新增了违反分层的导入（见 AGENTS.md「架构与职责」）：\n"
            + "\n".join(f"  {src} → {target}" for src, target in new)
        )

    def test_known_exceptions_still_exist(self) -> None:
        """历史例外被消除后必须从清单删除，清单只减不增。"""
        current = set().union(*(_violations(rule) for rule in _RULES))
        stale = sorted(_KNOWN_EXCEPTIONS - current)
        assert not stale, "以下历史例外已不存在，请从 _KNOWN_EXCEPTIONS 删除：\n" + "\n".join(
            f"  {src} → {target}" for src, target in stale
        )

    def test_scanner_detects_lazy_and_relative_imports(self) -> None:
        """扫描器能识别函数体内的延迟导入与相对导入（防止守卫自身失效）。"""
        source = (
            "from __future__ import annotations\n"
            "def f() -> None:\n"
            "    from ..services.x import Y\n"
            "    import sqlalchemy.orm\n"
        )
        probe = PACKAGE_ROOT / "domain" / "_layer_probe.py"
        found = _imports(probe, source)
        assert f"{PACKAGE}.services.x" in found
        assert "sqlalchemy.orm" in found
