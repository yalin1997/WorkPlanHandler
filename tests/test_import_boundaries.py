"""D9 import-lint:守住依賴方向鐵則(M4 精緻化白名單)。

依賴方向(衝突以 CLAUDE.md / docs/phase2/00 為準):

  - ``langgraph`` 只准出現在 ``adapters/langgraph.py``(唯一檔)。
  - ``langchain`` / ``anthropic`` 只准出現在 LLM 元件兩檔:
    ``planners/llm_planner.py`` 與 ``verifiers/llm_judge.py``(D4)。
  - 其餘 core 檔一律不得 import 上述框架,也不得 import ``adapters``。

本檔以 ast 解析(非 grep)檢查,**不需要安裝任何框架也要能全綠**
——這正是 D9 的精神:純核心環境一切正常。
"""

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

CORE_DIR = Path(__file__).parent.parent / "src" / "workplan"

# LLM binding 模組(judge/planner 接 LLM 的客戶端;D4 嚴格限兩檔)
LLM_BINDING = {"langchain", "anthropic", "langchain_anthropic"}
# langchain_core 是 langgraph 的轉移依賴(RunnableConfig 等),adapter 合法使用
LANGCHAIN_FAMILY = LLM_BINDING | {"langchain_core"}
# 整套框架(含 langgraph),供「除白名單外一律禁止」的核心檢查
ALL_FRAMEWORKS = LANGCHAIN_FAMILY | {"langgraph"}

# 白名單:檔案(相對 CORE_DIR 的 posix 路徑)→ 允許的框架頂層模組集合
FRAMEWORK_WHITELIST = {
    "adapters/langgraph.py": {"langgraph", "langchain_core"},
    "planners/llm_planner.py": LANGCHAIN_FAMILY,
    "verifiers/llm_judge.py": LANGCHAIN_FAMILY,
}


def iter_all_files():
    """src/workplan/ 下所有 .py(含 adapters/)。"""
    yield from sorted(CORE_DIR.rglob("*.py"))


def iter_core_files():
    """core 檔(排除 adapters/ 子樹;LLM 兩檔仍屬 core,但有白名單豁免)。"""
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


def test_framework_imports_respect_whitelist():
    """每個檔案的框架 import 必須落在白名單內(D9/D4 精緻化)。

    langgraph 只准 adapters/langgraph.py;langchain/anthropic 只准
    llm_planner.py + llm_judge.py;其餘檔案一律不得 import 框架。
    """
    violations = []
    for path in iter_all_files():
        rel = path.relative_to(CORE_DIR).as_posix()
        allowed = FRAMEWORK_WHITELIST.get(rel, set())
        bad = (imported_top_modules(path) & ALL_FRAMEWORKS) - allowed
        if bad:
            violations.append(f"{rel}: {sorted(bad)}(允許:{sorted(allowed) or '無'})")
    assert not violations, "違規 import 框架模組(超出白名單):\n" + "\n".join(violations)


def test_only_whitelisted_files_touch_langchain():
    """LLM binding(langchain/anthropic)的足跡僅限 LLM 兩檔(D4 正向檢查)。

    langchain_core 不在此列(它是 langgraph 的轉移依賴,adapter 合法使用)。
    """
    touchers = set()
    for path in iter_all_files():
        if imported_top_modules(path) & LLM_BINDING:
            touchers.add(path.relative_to(CORE_DIR).as_posix())
    assert touchers <= {"planners/llm_planner.py", "verifiers/llm_judge.py"}, (
        f"LLM binding 出現在非白名單檔:{sorted(touchers)}"
    )


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
    bad = imported_top_modules(init_path) & ALL_FRAMEWORKS
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

        # 子套件 __init__ 不得 eager import LLM 元件(否則此處會炸):
        import workplan.planners       # ExternalPlanner / MockPlanner(零依賴)
        import workplan.verifiers      # Layered/Programmatic/HumanGate/Mock(零依賴)
        from workplan.planners import ExternalPlanner
        from workplan.errors import ReplanNotSupported

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
