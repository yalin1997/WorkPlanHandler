"""WorkPlanHandler — Verifier 實作(D10)。

M1 僅含 MockVerifier;LayeredVerifier / programmatic / llm_judge /
human_gate 於 M3/M4 實作(規格 03)。
"""
from .mock import MockVerifier

__all__ = ["MockVerifier"]
