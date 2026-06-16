"""M6-4:public API 表面契約(stable surface)。

MVP(0.1.0)對外整合者依賴的公開介面在此釘死。任何**新增**公開名稱、
**移除**或**改名**現有名稱,都應是有意識的破壞性變更——本測試會逼你正面
面對它(更新此清單 = 明確記錄一次 API 變動)。

兩層公開面(對應 D9 零依賴邊界):
  1) 頂層 ``workplan.*``:純核心,零框架依賴,永遠可 import。
  2) 顯式路徑 optional:adapter / LLM 元件,需對應 extra,刻意不經 __init__
     eager import(見 tests/test_import_boundaries.py)。本檔只在 extra 存在時驗證。
"""

from __future__ import annotations

import importlib

import pytest

import workplan

# ── 第 1 層:頂層純核心公開面(釘死)──────────────────────────────
EXPECTED_PUBLIC = {
    "engine",
    "Action",
    "Decision",
    "MAX_REPLANS",
    "WorkPlanError",
    "PlanIntegrityError",
    "IllegalTransitionError",
    "ReplanNotSupported",
    "Event",
    "EventType",
    "AcceptanceCriterion",
    "HumanGate",
    "Plan",
    "PlanState",
    "Step",
    "StepStatus",
    "Executor",
    "Planner",
    "PlanStore",
    "StepOutput",
    "Verifier",
    "VerificationResult",
}


def test_public_surface_matches_contract():
    """``workplan.__all__`` 與釘死清單完全一致(防止無意間增刪公開名)。"""
    assert set(workplan.__all__) == EXPECTED_PUBLIC


def test_every_public_name_is_importable():
    """``__all__`` 列的每個名稱都真的能從 ``workplan`` 取到(無虛列)。"""
    missing = [name for name in workplan.__all__ if not hasattr(workplan, name)]
    assert not missing, f"__all__ 列了但取不到:{missing}"


def test_version_is_released_0_1_0():
    """MVP 版號釘為 0.1.0(非 .devN);git install 的對外版本契約。"""
    assert workplan.__version__ == "0.1.0"


# ── 第 2 層:顯式路徑 optional 公開面(僅在 extra 存在時驗證)────────
def test_optional_public_paths_when_extras_present():
    """adapter / LLM / 橋接元件的顯式 import 路徑穩定(裝了 extra 才驗)。"""
    optional = {
        "workplan.adapters.langgraph": ("WorkPlanRunner",),
        "workplan.planners.llm_planner": ("LLMPlanner",),
        "workplan.verifiers.llm_judge": ("LLMJudgeVerifier",),
        "workplan.planners.external": ("ExternalPlanner",),
        "workplan.executors.callable": ("CallableExecutor",),
        "workplan.audit": ("to_event_log", "to_markdown", "write_audit"),
        "workplan.stores.json_store": ("JsonFilePlanStore",),
        "workplan.verifiers.builtin_checks": ("BUILTIN_CHECKS",),
        "workplan.adapters.mcp_server": ("Gatekeeper", "build_server", "main"),
    }
    for mod_name, names in optional.items():
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            pytest.skip(f"{mod_name} 需要對應 extra,未安裝")
        for name in names:
            assert hasattr(mod, name), f"{mod_name}.{name} 公開路徑遺失"
