"""WorkPlanHandler M1 demo(全 mock,不需任何 LLM key)。

情境:三步計劃,s2 第一次驗收失敗 → engine 指示 RETRY(帶 feedback)→
第二次通過 → 全部完成。展示「驗收作為推進閘門」與事件審計軌跡。

執行:python examples/demo_mock.py
"""

import json

from workplan import Action, PlanState, StepOutput, engine
from workplan.executors import MockExecutor
from workplan.models import AcceptanceCriterion, Plan, Step
from workplan.verifiers import MockVerifier
from workplan.verifiers.mock import failed

# 1) 定義計劃:每個 Step 內建 AcceptanceCriterion(完成定義,I5)
plan = Plan(
    goal="產出市場分析報告",
    steps=[
        Step(
            id="s1",
            description="蒐集三家競品的公開資料",
            acceptance=AcceptanceCriterion(description="至少三家、含來源連結"),
        ),
        Step(
            id="s2",
            description="彙整成比較表",
            acceptance=AcceptanceCriterion(description="表格涵蓋價格/功能/客群"),
        ),
        Step(
            id="s3",
            description="撰寫總結與建議",
            acceptance=AcceptanceCriterion(description="含三點可行建議"),
        ),
    ],
)

# 2) 準備元件(M1 用 mock;M4 換成真 LLM planner/judge,介面不變)
executor = MockExecutor(script={"s2": "比較表 v2(已補客群欄位)"})
verifier = MockVerifier(script={"s2": [failed("缺少客群欄位")]})  # s2 第一次 fail

# 3) adapter 迴圈:engine 算決策,呼叫方執行副作用(D2 薄殼)
dec = engine.initialize(plan, thread_id="demo-1")
while dec.action in (Action.EXECUTE, Action.RETRY, Action.VERIFY):
    state = dec.state
    step = state.current_step
    if dec.action in (Action.EXECUTE, Action.RETRY):
        if dec.action == Action.RETRY:
            print(f"  ↻ RETRY {step.id},feedback:{dec.feedback}")
        out = executor.execute(step, state)
        dec = engine.on_executed(state, out)
    else:  # VERIFY
        res = verifier.verify(step, StepOutput(content=step.output), state)
        dec = engine.on_verified(state, res)

# 4) 結果與審計
state = dec.state
print(f"\n結束 action={dec.action.value}, status={state.status}, cursor={state.cursor}")
print(f"s2 attempts={state.plan.steps[1].attempts}, notes={state.plan.steps[1].notes}")

print("\n— 計劃摘要(recitation,executor 會注入 prompt 尾端)—")
print(state.plan.render_for_recitation())

print("\n— 事件審計軌跡(state.history,I4)—")
for e in state.history:
    print(f"  {e['type']:<15} step={e['step_id'] or '-'}")

# 5) I2:任一時刻可 JSON 序列化 / 還原(M2 持久化的基礎)
restored = PlanState.from_dict(json.loads(json.dumps(state.to_dict())))
assert restored.to_dict() == state.to_dict()
print("\nJSON round-trip OK(I2)")
