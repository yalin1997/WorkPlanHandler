"""WorkPlanHandler — Verifier 實作(D10)。

M1:MockVerifier;M3:LayeredVerifier / ProgrammaticVerifier /
HumanGateVerifier;M4:LLMJudgeVerifier(soft 層真 LLM,規格 03)。

LLMJudgeVerifier **刻意不在此 eager import**——它依賴 langchain(optional
extra),eager import 會把 langchain 拖進零依賴核心、破壞 D9。請以顯式路徑取用:

    from workplan.verifiers.llm_judge import LLMJudgeVerifier
"""

from .base import LayeredVerifier
from .human_gate import HumanGateVerifier
from .mock import MockVerifier
from .programmatic import ProgrammaticVerifier

__all__ = [
    "HumanGateVerifier",
    "LayeredVerifier",
    "MockVerifier",
    "ProgrammaticVerifier",
]
