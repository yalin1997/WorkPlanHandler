"""M6-3:真 LLM 穩定度 probe 的離線單測(TDD 核心邏輯,不燒 key)。

probe 的「量測聚合」是純函式,可在無 key 下完整測試;真連線的 main()
由人手動跑(燒 key)。這裡釘住:
  - summarize() 正確算出 mean/min/max/spread 與 non-pass 計數;
  - run_judge_probe() 對注入的 model 連跑 N 次並聚合(以 stub 驗接線)。
"""

from __future__ import annotations

import pytest

from scripts.m6_real_llm_probe import JudgeProbeReport, summarize


def test_summarize_pure_logic():
    report = summarize(scores=[0.9, 0.8, 1.0, 0.9], passed=[True, True, True, True])
    assert isinstance(report, JudgeProbeReport)
    assert report.n == 4
    assert report.score_min == 0.8
    assert report.score_max == 1.0
    assert abs(report.spread - 0.2) < 1e-9
    assert abs(report.mean - 0.9) < 1e-9
    assert report.non_pass_count == 0


def test_summarize_counts_non_pass():
    """non-pass(含 fail-closed)應被計數,供判讀 judge 可信度。"""
    report = summarize(scores=[0.9, 0.0, 0.85], passed=[True, False, True])
    assert report.n == 3
    assert report.non_pass_count == 1
    assert report.score_min == 0.0


def test_run_judge_probe_wiring_with_stub():
    """以 stub model 驗證 run_judge_probe 連跑 N 次並聚合(不燒 key)。"""
    pytest.importorskip("langchain", reason="需要 llm extra")
    # 重用 demo 的 StubChatModel:固定回 score=0.9 / passed=True
    from examples.demo_research_llm import StubChatModel
    from scripts.m6_real_llm_probe import run_judge_probe
    from workplan.verifiers.llm_judge import JudgeVerdict, LLMJudgeVerifier

    judge = LLMJudgeVerifier(
        model=StubChatModel(JudgeVerdict(score=0.9, passed=True, feedback="ok"))
    )
    report = run_judge_probe(judge, runs=5)
    assert report.n == 5
    assert report.spread == 0.0  # 確定性 stub:零變異
    assert report.non_pass_count == 0
