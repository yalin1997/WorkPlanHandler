"""M2 DoD:LangGraph adapter E2E 測試(全 mock,不呼叫 LLM,D3)。

重要:所有 sqlite db_path 一律使用 pytest 的 ``tmp_path`` fixture——
本 repo 位於 WSL2 的 /mnt/c(9P 檔案系統),sqlite 檔案鎖在其上不可靠;
tmp_path 落在原生 ext4(/tmp),可避免假性鎖死/毀損。

主 DoD(A5/A6):kill process 後,同 db_path + thread_id 可 resume 續跑到 done。
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("langgraph")
pytest.importorskip("langgraph.checkpoint.sqlite")

from conftest import event_types, make_plan, make_step  # noqa: E402

from workplan.adapters.langgraph import (  # noqa: E402
    RunResult,
    WorkPlanRunner,
    build_graph,
)
from workplan.executors import MockExecutor  # noqa: E402
from workplan.models import AcceptanceCriterion, Plan, Step, StepStatus  # noqa: E402
from workplan.planners import MockPlanner  # noqa: E402
from workplan.verifiers import (  # noqa: E402
    HumanGateVerifier,
    LayeredVerifier,
    MockVerifier,
    ProgrammaticVerifier,
)
from workplan.verifiers.mock import failed, needs_human  # noqa: E402

PER_STEP_EVENTS = ["step_started", "step_output", "verify_passed", "step_done"]


def make_runner(tmp_path: Path, **kwargs) -> WorkPlanRunner:
    kwargs.setdefault("executor", MockExecutor())
    kwargs.setdefault("verifier", MockVerifier())
    kwargs.setdefault("planner", None)
    kwargs.setdefault("db_path", str(tmp_path / "wp.sqlite"))
    kwargs.setdefault("max_replans", 2)
    return WorkPlanRunner(**kwargs)


# ---------------------------------------------------------------- A1
def test_build_graph_compiles():
    """圖能編譯,且至少含規格指定的五個節點。"""
    app = build_graph(
        executor=MockExecutor(), verifier=MockVerifier(), checkpointer=None
    )
    expected = {"plan", "execute", "verify", "replan", "human"}
    assert set(app.get_graph().nodes) >= expected


# ---------------------------------------------------------------- A2
def test_e2e_happy_path(tmp_path):
    """3 步全過:thread_id 自動生成、事件序與 M1 T1 完全一致。"""
    with make_runner(tmp_path) as runner:
        res = runner.run(plan=make_plan("s1", "s2", "s3"), thread_id=None)

    assert isinstance(res, RunResult)
    assert res.thread_id  # 未給 thread_id 時自動生成,非空
    assert res.status == "done"
    assert res.state.cursor == 3
    assert all(s.status == StepStatus.DONE for s in res.state.plan.steps)
    assert event_types(res.state) == (
        ["plan_created"] + PER_STEP_EVENTS * 3 + ["run_completed"]
    )


# ---------------------------------------------------------------- A3
def test_retry_path(tmp_path):
    """s2 第一次驗收 fail → 同步 retry 後通過(M1 T2 的 adapter 版)。"""
    verifier = MockVerifier(script={"s2": [failed("第一次差一點")]})
    with make_runner(tmp_path, verifier=verifier) as runner:
        res = runner.run(plan=make_plan("s1", "s2", "s3"))

    assert res.status == "done"
    assert res.state.plan.steps[1].attempts == 1
    retried = [e for e in res.state.history if e["type"] == "step_retried"]
    assert len(retried) == 1
    assert retried[0]["step_id"] == "s2"


# ---------------------------------------------------------------- A4
def test_replan_path(tmp_path):
    """s2 連 fail 兩次 → replan 改走 s2b/s3b;DONE 前綴保留(D6)。"""
    verifier = MockVerifier(script={"s2": [failed("不行"), failed("還是不行")]})
    tail = Plan(
        goal="測試目標",
        steps=[make_step("s2b"), make_step("s3b")],
        revision_note="s2 驗收不可達,改走 s2b/s3b",
    )
    planner = MockPlanner(tails=[tail])
    with make_runner(tmp_path, verifier=verifier, planner=planner) as runner:
        res = runner.run(plan=make_plan("s1", "s2", "s3"))

    assert res.status == "done"
    assert res.state.plan.version == 2
    assert res.state.replans == 1
    # s1 仍 DONE 且 id 保留在原位
    assert res.state.plan.steps[0].id == "s1"
    assert res.state.plan.steps[0].status == StepStatus.DONE
    assert all(s.origin == "replan" for s in res.state.plan.steps[1:])


# ---------------------------------------------------------------- A5(主 DoD)
class CrashingExecutor:
    """包一層 MockExecutor:step.id=="s4" 第一次呼叫時 raise,模擬 crash。"""

    def __init__(self, crash_step_id: str = "s4") -> None:
        self.inner = MockExecutor()
        self.crash_step_id = crash_step_id
        self.crashed = False

    def execute(self, step, state):
        if step.id == self.crash_step_id and not self.crashed:
            self.crashed = True
            raise RuntimeError("模擬 crash")
        return self.inner.execute(step, state)

    @property
    def calls(self):
        return self.inner.calls


def test_kill_and_resume_in_process(tmp_path):
    """主 DoD:s4 執行中 crash → 丟棄全部記憶體物件 → 同 db_path resume 續跑。"""
    db_path = str(tmp_path / "wp.sqlite")
    tid = "crash-thread"

    # ---- 第一條命:跑到 s4 raise ----
    runner = make_runner(tmp_path, executor=CrashingExecutor(), db_path=db_path)
    with pytest.raises(RuntimeError, match="模擬 crash"):
        runner.run(plan=make_plan("s1", "s2", "s3", "s4", "s5"), thread_id=tid)
    runner.close()  # 丟棄全部記憶體物件,模擬 process 死亡

    # ---- 第二條命:同 db_path、全新 mock 元件 ----
    # mock 的記憶體狀態(calls 計數等)跨「process」不存在,
    # 故腳本一律以 resume 後的視角配置(s1–s3 不會再被呼叫)。
    executor2 = MockExecutor()
    runner2 = make_runner(tmp_path, executor=executor2, db_path=db_path)
    res = runner2.resume(tid)
    runner2.close()

    assert res.status == "done"
    assert res.state.cursor == 5
    # s1–s3 已 DONE 不重跑;durability=sync 下 crash 節點沒寫入半成品,
    # s4 從頭重跑一次屬 at-least-once 語意。
    assert set(executor2.calls) == {"s4", "s5"}

    # ---- 對照組:同形 plan 不中斷跑一次,最終事件序必須完全相同 ----
    with make_runner(
        tmp_path, db_path=str(tmp_path / "control.sqlite")
    ) as control_runner:
        control = control_runner.run(plan=make_plan("s1", "s2", "s3", "s4", "s5"))
    assert event_types(res.state) == event_types(control.state)


# ---------------------------------------------------------------- A6
_DRIVER_TEMPLATE = """\
import json
import os
import sys

sys.path.insert(0, @SRC@)  # 確保 subprocess 找得到 workplan

from workplan.adapters.langgraph import WorkPlanRunner
from workplan.executors import MockExecutor
from workplan.models import AcceptanceCriterion, Plan, Step
from workplan.verifiers import MockVerifier


def make_plan():
    steps = [
        Step(
            id="s%d" % i,
            description="步驟 s%d" % i,
            acceptance=AcceptanceCriterion(description="s%d 完成" % i),
        )
        for i in range(1, 6)
    ]
    return Plan(goal="subprocess 測試", steps=steps)


class KillExecutor:
    # s4 執行時直接 os._exit(9):真 kill,連 finally 都不跑
    def __init__(self):
        self.inner = MockExecutor()

    def execute(self, step, state):
        if step.id == "s4":
            os._exit(9)
        return self.inner.execute(step, state)


def main():
    phase, db, tid = sys.argv[1], sys.argv[2], sys.argv[3]
    if phase == "phase1":
        runner = WorkPlanRunner(
            executor=KillExecutor(),
            verifier=MockVerifier(),
            planner=None,
            db_path=db,
            max_replans=2,
        )
        runner.run(plan=make_plan(), thread_id=tid)
    else:  # phase2:同 db resume 續跑到 done
        with WorkPlanRunner(
            executor=MockExecutor(),
            verifier=MockVerifier(),
            planner=None,
            db_path=db,
            max_replans=2,
        ) as runner:
            res = runner.resume(tid)
            print(json.dumps({"status": res.status, "cursor": res.state.cursor}))


main()
"""


@pytest.mark.slow
def test_kill_and_resume_subprocess(tmp_path):
    """真 kill(os._exit(9))後另一個 process 同 db resume 到 done。"""
    src_dir = Path(__file__).resolve().parents[1] / "src"
    db = str(tmp_path / "wp.sqlite")
    tid = "subproc-thread"
    script = tmp_path / "driver.py"
    script.write_text(
        textwrap.dedent(_DRIVER_TEMPLATE).replace("@SRC@", repr(str(src_dir))),
        encoding="utf-8",
    )

    # phase1:於 s4 內 os._exit(9),預期 returncode==9
    p1 = subprocess.run(
        [sys.executable, str(script), "phase1", db, tid],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert p1.returncode == 9, f"phase1 stderr:\n{p1.stderr}"

    # phase2:同 db resume,stdout 末行為 JSON 結果
    p2 = subprocess.run(
        [sys.executable, str(script), "phase2", db, tid],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert p2.returncode == 0, f"phase2 stderr:\n{p2.stderr}"
    payload = json.loads(p2.stdout.strip().splitlines()[-1])
    assert payload["status"] == "done"
    assert payload["cursor"] == 5


# ---------------------------------------------------------------- A7
def test_escalate_interrupt_smoke(tmp_path):
    """human gate 冒煙測試:escalate → interrupt → approved resume。

    完整 HITL 矩陣(rejected/edited 等)留待 M3;此處僅驗 adapter 的
    interrupt 翻譯與 resume 通路。
    """
    verifier = MockVerifier(script={"s2": [needs_human("需要人工確認")]})
    with make_runner(tmp_path, verifier=verifier) as runner:
        res = runner.run(plan=make_plan("s1", "s2", "s3"))
        tid = res.thread_id

        assert res.interrupted is True
        assert res.interrupt_payload is not None
        assert res.interrupt_payload["type"] == "human_gate"
        assert runner.get_state(tid).status == "blocked"

        res2 = runner.resume(tid, resolution="approved", note="人工放行")

    assert res2.status == "done"
    assert res2.state.cursor == 3


# ---------------------------------------------------------------- A8
def test_two_threads_isolated(tmp_path):
    """同一 Runner 先後跑兩個 thread,狀態互不污染。"""
    with make_runner(tmp_path) as runner:
        res_a = runner.run(plan=make_plan("a1", "a2", goal="目標A"), thread_id="tA")
        res_b = runner.run(
            plan=make_plan("b1", "b2", "b3", goal="目標B"), thread_id="tB"
        )

        assert res_a.status == "done"
        assert res_b.status == "done"

        state_a = runner.get_state("tA")
        state_b = runner.get_state("tB")

    assert state_a.plan.goal == "目標A"
    assert state_a.cursor == 2
    assert [s.id for s in state_a.plan.steps] == ["a1", "a2"]
    assert state_b.plan.goal == "目標B"
    assert state_b.cursor == 3
    assert [s.id for s in state_b.plan.steps] == ["b1", "b2", "b3"]


# ================================================================== M3
# 分層驗收 + 完整 HITL 矩陣經 adapter 跑通(A7 已驗 approved;此處補
# LayeredVerifier 整合、rejected/edited 兩條 HITL 路徑、高風險步驟才掛人。
# ==================================================================


def hard_check_step(step_id: str, check_name: str) -> Step:
    """帶 hard 驗收的步驟;check 以「註冊名」(str)引用——經 adapter
    持久化時 spec 必須保持純 JSON,不能塞 callable(I2)。"""
    return Step(
        id=step_id,
        description=f"步驟 {step_id}",
        acceptance=AcceptanceCriterion(
            description=f"{step_id} 完成",
            kind="programmatic",
            spec={"check": check_name},
            layer="hard",
        ),
    )


# ---------------------------------------------------------------- A9
def test_layered_verifier_through_graph_retry(tmp_path):
    """LayeredVerifier 經 adapter 跑通:hard 層首次 fail → 短路 → retry →
    第二次 pass → done。驗分層結果能直接驅動 engine 路由,且 VERIFY_FAILED
    記到 hard 層。"""
    calls = {"n": 0}

    def flaky(out, st):
        calls["n"] += 1
        if calls["n"] == 1:
            return (False, 0.0, "hard:首次缺必填欄位")
        return (True, 1.0, "")

    soft = MockVerifier()  # hard 過後才跑
    verifier = LayeredVerifier(
        layers=[
            ("hard", ProgrammaticVerifier(checks={"flaky": flaky}), True),
            ("soft", soft, True),
        ]
    )
    plan = Plan(
        goal="分層驗收",
        steps=[make_step("s1"), hard_check_step("s2", "flaky"), make_step("s3")],
    )

    with make_runner(tmp_path, verifier=verifier) as runner:
        res = runner.run(plan=plan)

    assert res.status == "done"
    assert res.state.plan.steps[1].attempts == 1  # s2 retry 一次
    retried = [e for e in res.state.history if e["type"] == "step_retried"]
    assert len(retried) == 1 and retried[0]["step_id"] == "s2"
    vf = [e for e in res.state.history if e["type"] == "verify_failed"]
    assert len(vf) == 1 and vf[0]["payload"]["layer"] == "hard"


# ---------------------------------------------------------------- A10
def test_hitl_rejected_terminates_run(tmp_path):
    """HITL rejected:human gate → interrupt → resume(rejected) → 整個 run 失敗。"""
    verifier = MockVerifier(script={"s2": [needs_human("需人工確認 s2")]})
    with make_runner(tmp_path, verifier=verifier) as runner:
        res = runner.run(plan=make_plan("s1", "s2", "s3"))
        tid = res.thread_id
        assert res.interrupted is True

        res2 = runner.resume(tid, resolution="rejected", note="不予放行")

    assert res2.status == "failed"
    assert res2.state.plan.steps[1].status == StepStatus.FAILED
    assert res2.state.cursor == 1  # 停在 s2,未推進
    rf = [e for e in res2.state.history if e["type"] == "run_failed"]
    assert len(rf) == 1 and rf[0]["payload"]["reason"] == "不予放行"


# ---------------------------------------------------------------- A11
def test_hitl_edited_retries_then_completes(tmp_path):
    """HITL edited:human gate → resume(edited) → 帶人工指示重做本步 → done。"""
    verifier = MockVerifier(script={"s2": [needs_human("需補充客群欄位")]})
    with make_runner(tmp_path, verifier=verifier) as runner:
        res = runner.run(plan=make_plan("s1", "s2", "s3"))
        tid = res.thread_id
        assert res.interrupted is True

        res2 = runner.resume(tid, resolution="edited", note="補上客群欄位後重做")

    assert res2.status == "done"
    assert res2.state.cursor == 3
    assert "補上客群欄位後重做" in res2.state.plan.steps[1].notes
    resolved = [e for e in res2.state.history if e["type"] == "human_resolved"]
    assert len(resolved) == 1 and resolved[0]["payload"]["resolution"] == "edited"


# ---------------------------------------------------------------- A12
def test_layered_human_gate_only_for_high_risk_step(tmp_path):
    """LayeredVerifier 掛 human 層:只有 kind=='human' 的高風險步驟觸發
    interrupt;一般步驟照常經 soft 層通過(視步驟風險才掛人)。"""
    verifier = LayeredVerifier(
        layers=[
            ("soft", MockVerifier(), True),
            ("human", HumanGateVerifier(), False),
        ]
    )
    risky = Step(
        id="s2",
        description="發佈到正式環境",
        acceptance=AcceptanceCriterion(
            description="需人工核准發佈", kind="human", layer="human"
        ),
    )
    plan = Plan(
        goal="含高風險步驟的計劃",
        steps=[make_step("s1"), risky, make_step("s3")],
    )

    with make_runner(tmp_path, verifier=verifier) as runner:
        res = runner.run(plan=plan)
        tid = res.thread_id
        assert res.interrupted is True  # s1 通過(human 層不適用),卡在 s2
        assert res.interrupt_payload["step_id"] == "s2"
        assert runner.get_state(tid).cursor == 1

        res2 = runner.resume(tid, resolution="approved", note="核准發佈")

    assert res2.status == "done"
    assert res2.state.cursor == 3
