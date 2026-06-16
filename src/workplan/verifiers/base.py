"""LayeredVerifier(D10 分層閘門,規格 03 §3.4——M3 核心)。

把多個 verifier 依 hard → soft → human 串接成一道閘門:

  - hard 便宜且可信,先跑;**任一 required 層失敗即短路**,省下後續
    soft 層(LLM judge)的 token(規格 03 §3.4)。
  - 任一層回 ``needs_human`` 立即交人(短路)。
  - ``required=False`` 層為 advisory:不通過不擋推進,但其 feedback 會
    併入最終結果,由 engine 寫進 VERIFY_PASSED 事件(留給人看趨勢)。
  - 任一層 verifier 拋例外 → fail-closed(不確定就不放行,規格 03 §5)。

層的「逐步適用性」:layers 是整張圖共用的靜態組合,但哪些層對哪一步
生效由該步的 AcceptanceCriterion 決定(「視步驟風險才掛」的具體化):

  - hard  層:僅當 ``criterion.spec`` 含 "check" 時跑(沒定義 hard 驗收
    的步驟不因此被 fail-closed 擋下——標準在規劃期就該定,I5)。
  - soft  層:一律跑(rubric 缺省時 M4 的 llm_judge 回退用 description)。
  - human 層:僅當 ``criterion.kind == "human"`` 時跑(高風險步驟才交人)。
"""

from __future__ import annotations

from dataclasses import replace

from ..models import AcceptanceCriterion, PlanState, Step
from ..protocols import StepOutput, VerificationResult, Verifier

_LAYER_ORDER = {"hard": 0, "soft": 1, "human": 2}


def _layer_applies(name: str, criterion: AcceptanceCriterion) -> bool:
    if name == "hard":
        return "check" in criterion.spec
    if name == "human":
        return criterion.kind == "human"
    return True  # soft


class LayeredVerifier:
    """Args:
    layers: ``[(layer_name, verifier, required), ...]``;
            layer_name ∈ {"hard","soft","human"},建構時依此順序穩定排序。
    """

    def __init__(self, layers: list[tuple[str, Verifier, bool]]) -> None:
        unknown = [name for name, _, _ in layers if name not in _LAYER_ORDER]
        if unknown:
            raise ValueError(
                f"未知的 layer 名稱:{unknown}(允許:{sorted(_LAYER_ORDER)})"
            )
        self.layers = sorted(layers, key=lambda t: _LAYER_ORDER[t[0]])

    def verify(
        self, step: Step, output: StepOutput, state: PlanState
    ) -> VerificationResult:
        advisories: list[str] = []
        scores: list[tuple[float, str]] = []  # (score, layer_name)

        for name, verifier, required in self.layers:
            if not _layer_applies(name, step.acceptance):
                continue
            try:
                result = verifier.verify(step, output, state)
            except Exception as exc:  # fail-closed:層內例外不放行
                return VerificationResult(
                    passed=False,
                    score=0.0,
                    feedback=f"{name} 層 verifier 拋出例外(fail-closed):{exc!r}",
                    layer=name,
                )
            if result.needs_human:  # 立即交人(短路)
                return replace(result, layer=name)
            if not result.passed:
                if required:  # required 層失敗即短路(後續層不跑、不花 token)
                    return replace(result, layer=name)
                advisories.append(f"[advisory:{name}] {result.feedback or '未通過'}")
                continue
            scores.append((result.score, name))

        # 全部 required 層通過。整體分數取保守值 min(規格 03 §3.4)。
        score, layer = min(scores) if scores else (1.0, "soft")
        return VerificationResult(
            passed=True,
            score=score,
            feedback=";".join(advisories),
            layer=layer,
        )
