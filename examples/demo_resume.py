"""WorkPlanHandler M2 demo:kill 後同 thread_id 續跑(需安裝 workplan[langgraph])。

情境:5 步計劃,executor 在 s4 第一次執行時 crash(模擬 process 被 kill)。
之後「另起一個 Runner」(等同重啟 process)以同一個 thread_id resume——
SqliteSaver 從磁碟讀回 PlanState,s1–s3 不重跑,從 s4 接續到完成。

執行:python examples/demo_resume.py
注意:SQLite db 放 /tmp(WSL2 下 /mnt/c 的檔案鎖不可靠)。
"""

import tempfile
import uuid
from pathlib import Path

from workplan.adapters.langgraph import WorkPlanRunner
from workplan.executors import MockExecutor
from workplan.models import AcceptanceCriterion, Plan, Step
from workplan.verifiers import MockVerifier


def make_plan() -> Plan:
    return Plan(
        goal="五階段資料處理管線",
        steps=[
            Step(
                id=f"s{i}",
                description=f"階段 {i}",
                acceptance=AcceptanceCriterion(description=f"階段 {i} 產出齊全"),
            )
            for i in range(1, 6)
        ],
    )


class CrashOnceExecutor(MockExecutor):
    """s4 第一次執行時直接 raise,模擬 process 在該步被 kill。"""

    def execute(self, step, state):
        if step.id == "s4":
            raise RuntimeError("(模擬)process 在 s4 執行中被 kill")
        return super().execute(step, state)


db = str(Path(tempfile.mkdtemp()) / "workplan.sqlite")
tid = uuid.uuid4().hex

# ── 第一條命:跑到 s4 時 crash ──────────────────────────────────────
runner1 = WorkPlanRunner(
    executor=CrashOnceExecutor(), verifier=MockVerifier(), db_path=db
)
try:
    runner1.run(plan=make_plan(), thread_id=tid)
except RuntimeError as e:
    print(f"第一條命:{e}")
finally:
    runner1.close()  # 記憶體物件全部丟棄,狀態只剩 sqlite 檔案

# ── 第二條命:全新 Runner、同 db、同 thread_id ──────────────────────
executor2 = MockExecutor()
with WorkPlanRunner(executor=executor2, verifier=MockVerifier(), db_path=db) as runner2:
    before = runner2.get_state(tid)
    print(f"重啟後讀回 checkpoint:cursor={before.cursor}(s1–s3 已 DONE,從 s4 接續)")

    result = runner2.resume(tid)  # invoke(None):從最後 checkpoint 續跑

print(f"續跑結果:status={result.status}, cursor={result.state.cursor}")
print(f"第二條命實際執行過的步驟:{sorted(executor2.calls)}(s1–s3 未重跑)")
print("\n— 最終計劃 —")
print(result.state.plan.render_for_recitation())
