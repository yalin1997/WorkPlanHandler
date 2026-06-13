"""WorkPlanHandler M5 整合 E2E demo(全 mock,不需 key;需 workplan[langgraph])。

單一腳本串起 doc 00 §5「整體 MVP DoD」的五項:

  1. 全程照計劃線性推進(cursor 前進)。
  2. 第 3 步前兩次故意失敗 → retry 帶 feedback → 第三次通過。
  3. 第 4 步執行中 crash → 同 db + 同 thread_id「重啟」續跑(checkpoint 級)。
     (真正的 kill -9 續跑由 examples/demo_resume.py + subprocess 測試覆蓋;
      此處用「丟棄 runner、另起 runner」在單一腳本內模擬重啟。)
  4. 第 5 步為高風險步驟(human gate)→ interrupt() → 人工核准後完成。
  5. 輸出 JSON 事件流 + Markdown 驗收摘要(audit trail)。

執行:python examples/demo_e2e.py
注意:SQLite db 放 /tmp(WSL2 下 /mnt/c 的檔案鎖不可靠)。
"""

import tempfile
import uuid
from pathlib import Path

from workplan.adapters.langgraph import WorkPlanRunner
from workplan.audit import to_event_log, to_markdown, write_audit
from workplan.executors import MockExecutor
from workplan.models import AcceptanceCriterion, Plan, Step
from workplan.verifiers import HumanGateVerifier, LayeredVerifier, MockVerifier


def make_plan() -> Plan:
    steps = [
        Step(
            id=f"s{i}",
            description=f"階段 {i}",
            acceptance=AcceptanceCriterion(description=f"階段 {i} 產出齊全"),
        )
        for i in range(1, 6)
    ]
    # 第 3 步允許多次重試(前兩次失敗、第三次過,需 max_attempts ≥ 3)
    steps[2].max_attempts = 3
    # 第 5 步為高風險步驟:掛 human gate(視步驟風險才交人)
    steps[4].acceptance = AcceptanceCriterion(
        description="需人工核准後才完成", kind="human", layer="human"
    )
    return Plan(goal="五階段長任務(含失敗/中斷/人工關卡)", steps=steps)


class CrashOnceExecutor(MockExecutor):
    """s3 前兩次回執行錯誤(驗 retry);s4 第一次直接 raise(模擬 process 被 kill)。"""

    def __init__(self):
        super().__init__(fail_first={"s3": 2})
        self._crashed_s4 = False

    def execute(self, step, state):
        if step.id == "s4" and not self._crashed_s4:
            self._crashed_s4 = True
            raise RuntimeError("(模擬)process 在 s4 執行中被 kill")
        return super().execute(step, state)


def verifier() -> LayeredVerifier:
    # soft 一律跑(mock 過);human 僅高風險步驟(kind=human)觸發
    return LayeredVerifier(
        layers=[("soft", MockVerifier(), True), ("human", HumanGateVerifier(), False)]
    )


db = str(Path(tempfile.mkdtemp()) / "workplan_e2e.sqlite")
tid = uuid.uuid4().hex

# ── 第一條命:跑到 s4 時 crash(s1–s2 過、s3 兩次失敗後第三次過)─────────
print("=== 第一段:啟動,s3 retry,s4 crash ===")
runner1 = WorkPlanRunner(executor=CrashOnceExecutor(), verifier=verifier(), db_path=db)
try:
    runner1.run(plan=make_plan(), thread_id=tid)
except RuntimeError as e:
    print(f"  捕捉到 crash:{e}")
finally:
    runner1.close()  # 記憶體狀態全丟,只剩 sqlite 檔

# ── 第二條命:全新 runner、同 db、同 thread_id,resume 續跑 ──────────────
print("\n=== 第二段:重啟續跑(checkpoint resume)→ 卡在 s5 human gate ===")
with WorkPlanRunner(
    executor=MockExecutor(), verifier=verifier(), db_path=db
) as runner2:
    before = runner2.get_state(tid)
    print(f"  重啟後讀回 checkpoint:cursor={before.cursor}(s1–s3 已 DONE,從 s4 接續)")

    res = runner2.resume(tid)  # 從中斷的 s4 接續,推進到 s5 human gate
    print(f"  interrupted={res.interrupted}, status={res.status}")
    print(f"  interrupt 提問:{res.interrupt_payload}")

    s3 = res.state.plan.steps[2]
    print(
        f"  s3 attempts={s3.attempts}(前兩次失敗 → retry → 第三次過),notes={s3.notes}"
    )

    # ── 人工核准 → resume 到完成 ──
    print("\n=== 第三段:人工核准 s5 → 完成 ===")
    final = runner2.resume(tid, resolution="approved", note="主管已核准")
    print(f"  status={final.status}, cursor={final.state.cursor}")

# ── 審計輸出(§5 第 5 項)──────────────────────────────────────────────
out_dir = Path(tempfile.mkdtemp())
json_path, md_path = write_audit(final.state, out_dir, basename="e2e_audit")
print("\n=== 審計輸出 ===")
print(f"  JSON 事件流({len(to_event_log(final.state))} 筆):{json_path}")
print(f"  Markdown 摘要:{md_path}")
print("\n— Markdown 驗收摘要 —")
print(to_markdown(final.state))
