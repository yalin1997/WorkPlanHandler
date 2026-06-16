"""WorkPlanHandler M3 demo:分層驗收(hard→soft→human)+ HITL(需安裝 workplan[langgraph])。

情境:三步計劃經 LangGraph adapter 跑——
  s1 一般步驟:只跑 soft 層,直接通過。
  s2 帶 hard check:首次「缺必填欄位」→ hard 層短路(soft 不被呼叫)→
     retry → 補齊後通過。展示「hard 便宜先跑、擋掉明顯不合格者省 LLM token」。
  s3 高風險步驟(kind="human"):soft 過後 human 層觸發 interrupt(),
     人工 approved 後續跑完成。展示「視步驟風險才掛人」+ 可 resume。

執行:python examples/demo_layered.py
注意:SQLite db 放 /tmp(WSL2 下 /mnt/c 的檔案鎖不可靠)。
"""

import tempfile
import uuid
from pathlib import Path

from workplan.adapters.langgraph import WorkPlanRunner
from workplan.executors import MockExecutor
from workplan.models import AcceptanceCriterion, Plan, Step
from workplan.verifiers import (
    HumanGateVerifier,
    LayeredVerifier,
    MockVerifier,
    ProgrammaticVerifier,
)

# ── hard check:s2 第一次輸出視為「缺欄位」,第二次補齊 ──────────────
_seen: dict[str, int] = {}


def has_required_fields(output, state) -> tuple[bool, float, str]:
    n = _seen.get("s2", 0) + 1
    _seen["s2"] = n
    if n == 1:
        return (False, 0.0, "hard:比較表缺『客群』必填欄位")
    return (True, 1.0, "")


def make_plan() -> Plan:
    return Plan(
        goal="產出市場分析報告(含高風險發佈步驟)",
        steps=[
            Step(
                id="s1",
                description="蒐集競品資料",
                acceptance=AcceptanceCriterion(description="至少三家、含來源"),
            ),
            Step(
                id="s2",
                description="彙整成比較表",
                acceptance=AcceptanceCriterion(
                    description="表格涵蓋價格/功能/客群",
                    kind="programmatic",
                    spec={"check": "has_required_fields"},  # 註冊名,保持 JSON 可序列化
                    layer="hard",
                ),
            ),
            Step(
                id="s3",
                description="發佈報告到正式管道",
                acceptance=AcceptanceCriterion(
                    description="需人工核准後才發佈", kind="human", layer="human"
                ),
            ),
        ],
    )


# 分層 verifier:hard(註冊 check)→ soft(mock)→ human(高風險步才掛)
verifier = LayeredVerifier(
    layers=[
        (
            "hard",
            ProgrammaticVerifier(checks={"has_required_fields": has_required_fields}),
            True,
        ),
        ("soft", MockVerifier(), True),
        ("human", HumanGateVerifier(), False),
    ]
)

db = str(Path(tempfile.mkdtemp()) / "workplan.sqlite")
tid = uuid.uuid4().hex

with WorkPlanRunner(executor=MockExecutor(), verifier=verifier, db_path=db) as runner:
    res = runner.run(plan=make_plan(), thread_id=tid)

    print("— 第一段:跑到 s3 高風險步驟,卡在 human gate —")
    print(f"interrupted={res.interrupted}, status={res.status}")
    print(f"interrupt 提問:{res.interrupt_payload}")

    s2 = res.state.plan.steps[1]
    print(f"\ns2 attempts={s2.attempts}(hard 層首次短路 → retry),notes={s2.notes}")

    # 人工核准發佈 → resume 續跑到完成
    res2 = runner.resume(tid, resolution="approved", note="主管已核准發佈")

print("\n— 第二段:人工核准後 resume —")
print(f"status={res2.status}, cursor={res2.state.cursor}")

print("\n— 事件審計軌跡(節錄關鍵事件)—")
for e in res2.state.history:
    if e["type"] in (
        "verify_failed",
        "step_retried",
        "escalated",
        "human_resolved",
        "run_completed",
    ):
        layer = e["payload"].get("layer", "")
        extra = f" layer={layer}" if layer else ""
        print(f"  {e['type']:<15} step={e['step_id'] or '-'}{extra}")
