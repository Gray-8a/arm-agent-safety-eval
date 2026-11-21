"""单一收口：全项目只有 executor.py 允许调 mj_step。

这不是代码风格问题。gate 有效性完全依赖"没有别的路径能动仿真"这个事实。
靠自觉守不住，所以扫 AST。谁在别处加一行 mj_step，CI 直接红。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
ALLOWED = {"executor.py"}
STEP_NAMES = {"mj_step", "mj_step1", "mj_step2"}


def _python_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _step_sites(tree: ast.AST) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in STEP_NAMES:
            hits.append((node.lineno, node.attr))
        elif isinstance(node, ast.Name) and node.id in STEP_NAMES:
            hits.append((node.lineno, node.id))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in STEP_NAMES:
                    hits.append((node.lineno, alias.name))
    return hits


def test_mj_step_only_in_executor():
    offenders: list[str] = []
    for path in _python_files():
        if path.name in ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, name in _step_sites(tree):
            offenders.append(f"{path.relative_to(SRC)}:{lineno} -> {name}")

    assert not offenders, (
        "只有 executor.py 能调 mj_step，以下地方绕过了安全门：\n  " + "\n  ".join(offenders))


def test_executor_actually_calls_mj_step():
    """反向检查：确保这条规则不是在空转。"""
    path = SRC / "arm_eval" / "executor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert _step_sites(tree), "executor.py 里居然没有 mj_step，规则失效了"
