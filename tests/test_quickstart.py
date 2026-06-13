"""M6-5:整合 quickstart 範例的離線測試(stub LLM,不燒 key)。

證明 examples/quickstart_integration.py 真的跑得通,且示範到的三個賣點成立:
  - acceptance-as-gate:s2 的 hard 驗收攔下漏「客群」的首稿;
  - retry feedback:失敗的可行動 feedback 回到下一次執行的 prompt;
  - recitation 注入:計劃摘要被接到 prompt 尾端(對抗長任務目標漂移)。

依賴 langgraph extra;未裝則整檔 skip。
"""

from __future__ import annotations

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langgraph.checkpoint.sqlite")

from examples.quickstart_integration import run_quickstart  # noqa: E402


def test_quickstart_runs_to_done_with_gate_and_retry(tmp_path):
    """端到端:照計劃推進、s2 經一次 retry 後通過、最終完成。"""
    db = str(tmp_path / "qs.sqlite")
    res = run_quickstart(db_path=db)

    assert res.status == "done"
    assert res.state.cursor == 3  # 三步全 DONE

    s2 = res.state.plan.steps[1]
    assert s2.attempts >= 1, "s2 首稿漏『客群』應至少觸發一次 retry"
    assert s2.notes, "retry 應留下 feedback note"
    assert "客群" in str(s2.output), "修正後的 s2 產出應含『客群』欄位"


def test_quickstart_injects_recitation_and_feedback(tmp_path):
    """spy 攔截每次送進 LLM 的 prompt,驗證 recitation 與 feedback 確實注入。"""
    seen: list[str] = []

    def spy_llm(prompt: str) -> str:
        seen.append(prompt)
        # 沿用範例的確定性行為:見到 feedback 區段才補上缺漏欄位
        base = "市場分析:競品比較。"
        return base + (" 目標客群:上班族。" if "請修正" in prompt else "")

    res = run_quickstart(llm_call=spy_llm, db_path=str(tmp_path / "qs2.sqlite"))

    assert res.status == "done"
    # recitation:至少一個 prompt 帶了計劃摘要(CallableExecutor 預設注入)
    assert any("計劃摘要" in p for p in seen), "recitation 未注入 prompt"
    # retry feedback:s2 第二次執行的 prompt 應帶回「請修正」的 feedback 區段
    assert any("請修正" in p for p in seen), "retry feedback 未回流到 prompt"
