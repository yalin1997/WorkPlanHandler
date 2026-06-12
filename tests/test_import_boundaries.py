"""D9 import-lint:守住依賴方向鐵則。

core 模組(src/workplan/ 除 adapters/ 外的一切)禁止 import
langgraph / langchain / anthropic,也禁止 import adapters;
唯一可 import langgraph 的檔案是 src/workplan/adapters/langgraph.py。

本檔以 ast 解析(非 grep)檢查,**不需要安裝 langgraph 也要能全綠**
——這正是 D9 的精神:純核心環境一切正常。
"""

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

CORE_DIR = Path(__file__).parent.parent / "src" / "workplan"

FORBIDDEN_FRAMEWORKS = {"langgraph", "langchain", "anthropic", "langchain_anthropic"}


def iter_core_files():
    """所有 core 檔(排除 adapters/ 子樹)。"""
    for path in sorted(CORE_DIR.rglob("*.py")):
        if "adapters" in path.relative_to(CORE_DIR).parts:
            continue
        yield path


def imported_top_modules(path: Path) -> set[str]:
    """以 ast 收集檔案中所有 import 的頂層模組名。

    - ``ast.Import``:取 ``alias.name`` 的第一段。
    - ``ast.ImportFrom``:取 ``node.module`` 的第一段;相對 import
      (``node.level > 0``)時 module 名也要納入,例如
      ``from .adapters import x`` 的 module 即 "adapters",必須抓得到。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                modules.add(node.module.split(".")[0])
    return modules


def test_core_has_no_framework_imports():
    """core 檔不得 import langgraph/langchain/anthropic(D9)。"""
    violations = []
    for path in iter_core_files():
        bad = imported_top_modules(path) & FORBIDDEN_FRAMEWORKS
        if bad:
            violations.append(f"{path.relative_to(CORE_DIR)}: {sorted(bad)}")
    assert not violations, "core 檔違規 import 框架模組:\n" + "\n".join(violations)


def test_core_does_not_import_adapters():
    """core 檔(含 __init__.py)不得 import adapters(絕對或相對皆禁)。"""
    violations = []
    for path in iter_core_files():
        if "adapters" in imported_top_modules(path):
            violations.append(str(path.relative_to(CORE_DIR)))
    assert not violations, "core 檔違規 import adapters:\n" + "\n".join(violations)


def test_adapters_init_is_dependency_free():
    """adapters/__init__.py(若存在)必須零框架依賴。

    這讓 ``import workplan.adapters`` 在未安裝 langgraph 的純核心環境
    不會炸;框架 import 只允許出現在 adapters/langgraph.py。
    """
    init_path = CORE_DIR / "adapters" / "__init__.py"
    if not init_path.exists():
        pytest.skip("adapters 尚未建立")
    bad = imported_top_modules(init_path) & FORBIDDEN_FRAMEWORKS
    assert not bad, f"adapters/__init__.py 違規 import 框架模組:{sorted(bad)}"


@pytest.mark.slow
def test_workplan_imports_without_langgraph():
    """動態驗證:封鎖框架模組後,核心 import 路徑仍全通(D9)。

    在子行程中安裝 MetaPathFinder,對 langgraph/langchain/anthropic
    開頭的 import 直接 raise ImportError,再 import workplan 核心。
    """
    code = textwrap.dedent(
        """
        import sys
        from importlib.abc import MetaPathFinder

        BLOCKED = ("langgraph", "langchain", "anthropic")

        class Blocker(MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0].startswith(BLOCKED):
                    raise ImportError(f"blocked by D9 test: {fullname}")
                return None

        sys.meta_path.insert(0, Blocker())

        import workplan
        from workplan import engine, Plan, PlanState

        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        "核心 import 在無框架環境下失敗:\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "ok" in result.stdout
