# 規格 03:Verifiers(驗收,分層閘門)

**元件**:`workplan/verifiers/`
**相關決策**:D10(分層 hard→soft→human)、D4(judge 用 init_chat_model 預設 Claude)、D8(human gate)、I5(驗收前置)
**理論依據**:survey §3(LLM-as-Judge、LLM-Modulo critics、ChatHTN verifier-as-task)

---

## 1. 職責

把一個 `Step` 的 `output` 對照其 `AcceptanceCriterion`,判定是否達成「完成定義」,回傳 `VerificationResult(passed, score, feedback, needs_human)`。**這是本專案的差異化賣點**:驗收是流程推進的閘門,不是事後評測。

---

## 2. 協定(已有草圖)

```python
class Verifier(Protocol):
    def verify(self, step, output, state) -> VerificationResult: ...
```

`VerificationResult`(規格見 protocols):`passed: bool`、`score: float∈[0,1]`、`feedback: str`(失敗時餵回 engine→executor/planner 做反思)、`needs_human: bool`(觸發 D8)。

**契約**:
- `feedback` 在 `passed=False` 時**必填且需可行動**(指出「缺什麼/錯哪」,而非只說「不通過」)——因為它會變成 Reflexion 式 verbal feedback。
- verifier **不得有副作用**(programmatic 的 callable 例外,但應冪等)。

---

## 3. 內建實作

### 3.1 `ProgrammaticVerifier`(hard,最可信,優先)

```python
class ProgrammaticVerifier(Verifier):
    """跑使用者提供的判定函式。criterion.spec = {"check": Callable[[output, state], CheckResult]}"""
```
- `check` 回傳 `(passed: bool, score: float, feedback: str)` 或 raise → 視為 fail 並把例外訊息當 feedback。
- 用途:單元測試、CI 退出碼、schema/assertion、字數/正則/URL 檢查、PDDL VAL(Phase 3)。
- `score` 對 hard 驗收通常是 0/1。

### 3.2 `LLMJudgeVerifier`(soft,開放式輸出)

```python
class LLMJudgeVerifier(Verifier):
    """criterion.spec = {"rubric": str}; 用 init_chat_model(預設 Claude)評分。"""
    def __init__(self, model=None):   # D4:model 預設 init_chat_model("anthropic:claude-...")
```
**流程**(遵循 survey §3.1「先定 rubric 再評」最佳實務,I5):
1. rubric 已在**規劃期**寫入 `criterion.spec["rubric"]`(不在此臨時生成,避免合理化)。
2. 給 judge:`rubric + step.description + output(+ 必要 trajectory 摘要)`,要求回傳**結構化** `{score:0~1, passed:bool, feedback:str}`(用 tool-use / JSON schema 強制結構)。
3. `passed = score >= criterion.threshold`。
- **偏誤緩解**(survey §3.1):prompt 模板固定欄位順序避免 position bias;可選 `n_votes` 多次取中位數(預設 1,Phase 3 擴充);judge 模型與 executor 模型應可不同以減 self-preference。

### 3.3 `HumanGateVerifier`(human,D8)

```python
class HumanGateVerifier(Verifier):
    """直接回 needs_human=True,把判定交給人;由 adapter 翻成 interrupt()。"""
    def verify(self, step, output, state):
        return VerificationResult(passed=False, needs_human=True,
                                  feedback=f"待人工確認:{step.acceptance.description}")
```
- 用於高風險/不可逆步驟(付款、發佈、刪除)。
- engine 收到 `needs_human` → `_escalate`(規格 02 §3);人工裁決經 `on_human_resolved` 回流。

### 3.4 `LayeredVerifier`(D10 分層閘門,核心)

把多個 verifier 依 `hard → soft → human` 串接,**任一 required 層失敗即短路**:

```python
class LayeredVerifier(Verifier):
    def __init__(self, layers: list[tuple[str, Verifier, bool]]):
        # [("hard", ProgrammaticVerifier(...), required=True),
        #  ("soft", LLMJudgeVerifier(...),     required=True),
        #  ("human",HumanGateVerifier(),       required=False)]  # 視步驟風險才掛
```
判定邏輯:
```
for layer_name, v, required in layers(依 hard→soft→human 排序):
    r = v.verify(step, output, state)
    if r.needs_human:  return r            # 立即交人(短路)
    if not r.passed and required:
        return r                           # required 層失敗即短路(省下後續 LLM 成本)
    aggregate score
return VerificationResult(passed=True, score=min(layer_scores), feedback="")
```
- **為何 hard 先跑**:hard 便宜且可信;先擋掉明顯不合格者,避免浪費 judge 的 token(survey §3.2 結論)。
- `required=False` 層為 advisory:不通過只記事件、不擋推進(留給人看趨勢)。
- 回傳 `layer` 資訊寫入 `VERIFY_FAILED.payload.layer`(規格 01 §4.1)。

> `CompositeVerifier`(提案中的名字)以 `LayeredVerifier` 實現;若未來要「加權分數」模式,另開 `WeightedVerifier`,不污染分層語意。

---

## 4. 驗收條件從哪來?(I5)

- **內建 planner 路徑**(規格 04):planner 在產 `Step` 時,**同時**產出對應的 `AcceptanceCriterion`(含 rubric 文字 / hard check 規格)。
- **外部 Plan 路徑**:上游 agent 傳入的 `Step` 必須自帶 `acceptance`;若缺,模組以「預設 soft rubric:輸出需切合 step.description」補上並記 warning 事件。
- **replan 改驗收**(D6):replan 可修訂尾巴步驟的 `acceptance`,但 `version++` 並留 `PLAN_REVISED` 事件。

---

## 5. 錯誤處理

| 情況 | 行為 |
|------|------|
| judge LLM 呼叫失敗/逾時 | 重試 1 次;再失敗 → `passed=False, feedback="judge 不可用"`,**不**當作通過(fail-closed) |
| judge 回傳非結構化 | 解析失敗視為 fail,feedback 記原始回應摘要 |
| programmatic check raise | 視為 fail,例外訊息入 feedback |
| hard 與 soft 衝突(hard pass、soft fail) | 依分層:soft required 則整體 fail;soft advisory 則 pass + 記事件 |

**原則:驗收一律 fail-closed**(不確定就不放行),因為放錯比擋錯成本高(尤其 D8 場景)。

---

## 6. 測試案例

- 各 verifier 單測:固定 output 下判定正確、feedback 可行動。
- `LayeredVerifier`:hard fail 時 **judge 不被呼叫**(用 mock judge 斷言呼叫次數=0,驗短路省成本)。
- `needs_human` 短路:human 層在前面層 pass 後才觸發。
- judge fail-closed:mock judge 拋例外 → 結果 `passed=False`。
- threshold 邊界:score==threshold 視為 pass。
