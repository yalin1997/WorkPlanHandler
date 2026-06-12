# Survey 報告:Agent 長任務執行管理的技術棧與理論

**日期**:2026-06-12
**範圍**:支援 agent planner 制定並執行長任務(long-horizon task)的執行管理技術,聚焦於「計劃遵循 / 階段驗收 / 可恢復執行」三大需求,並評估與 LangGraph 等 Python agent 套件的整合可行性。

---

## 0. TL;DR(給趕時間的人)

長任務 agent 的失敗模式幾乎都來自同一組原因:**(a) LLM 無法可靠地一次規劃完整長計劃、(b) 在長 context 中目標漂移/遺忘、(c) 沒有把「完成」定義清楚也沒檢核、(d) 中途失敗無法恢復**。

對應的四大解法,已在學術與業界各自獨立收斂:

| 失敗模式 | 解法支柱 | 代表技術 |
|---------|---------|---------|
| 規劃不可靠 | **Plan / Execute 解耦** + 外部驗證 | Plan-and-Act、ADaPT、LLM-Modulo、HTN |
| 目標漂移 | **結構化計劃 + 持續複誦 (recitation)** | Manus `todo.md`、TodoWrite、file-based planning |
| 無驗收 | **Verifier-as-gate / LLM-as-Judge** | ChatHTN verifier、rubric 評分、VAL/PDDL |
| 無法恢復 | **持久化狀態 + durable execution** | LangGraph checkpointer、Temporal |

**結論**:這四支柱可組成一個可插拔模組。沒有任何現成套件「同時、以驗收為核心」把四者整合好——這是 WorkPlanHandler 的市場空缺。

---

## 1. 問題定義(Problem Framing)

### 1.1 什麼是「長任務(long-horizon task)」?

操作型定義:單一目標需要 **數十次以上工具呼叫 / LLM 決策步驟** 才能完成的任務。Manus 團隊公開資料指出,其典型任務平均約 **50 次工具呼叫**;而「extremely long-horizon」研究(KLong, 2026)則處理跨數百步、需長期記憶的互動任務。

長任務的本質困難:
- **誤差累積(error accumulation)**:每步成功率 p,N 步全對的機率 ≈ p^N,N 大時急遽崩潰 → 需要**逐步驗收**把錯誤就地攔截,而非到最後才發現。
- **Context 壓力與目標漂移**:長 loop 中模型容易偏題、遺忘早期目標(Manus 明確點名此問題)。
- **不可逆副作用**:長任務常含真實世界寫入操作(寫檔、呼叫 API、付款),失敗成本高 → 需要 checkpoint 與 human-in-the-loop 閘門。

### 1.2 本模組要解的三件事(對應專案驗收目標)

1. **計劃遵循**:agent 真的照計劃走,而非每步自由發揮後漂走。
2. **階段性任務完成**:把大任務拆成 staged subtasks,逐一推進並追蹤進度。
3. **驗收目標達成**:每階段有明確「完成定義」,並能(半)自動判定是否通過。

---

## 2. 規劃範式(Planning Paradigms)——學術理論

### 2.1 為什麼要把「規劃」和「執行」解耦?

核心發現(Kambhampati 等人,LLM-Modulo):**LLM 不是好的 planner,但是好的 plan 元件提供者**。實驗顯示 GPT-4 直接產生的 PDDL 計劃中**不到 12% 可直接執行**。因此主流做法是「**Planner 產出高階計劃 → Executor 落地為環境動作**」的兩段式架構,並輔以外部驗證。

| 範式 | 一句話 | 對本專案的啟發 |
|------|--------|----------------|
| **ReAct** (Yao 2022) | Reasoning + Acting 交錯,邊想邊做 | 適合「單步」反應,但缺全局計劃,長任務易漂移 → 不能只靠它 |
| **Plan-and-Solve / Plan-and-Execute** | 先產生完整計劃再逐步執行 | 本模組的骨架;LangGraph 官方有對應教學 |
| **Plan-and-Act** (ICML 2025) | Planner 出結構化高階計劃、Executor 翻譯成動作,並可動態更新 | **核心參考架構**;驗證「結構化計劃 + 解耦」對 long-horizon 有效 |
| **ADaPT** (As-Needed Decomposition) | 遞迴式「需要時才拆解」,執行不了就再分解 | 支援**動態深度的階層拆解**;但其弱點是無法修正已成功步驟的錯誤 |
| **HTN(Hierarchical Task Network)** | 高階 compound task 遞迴分解為 primitive task | 提供「階段性任務」的形式化骨架 |
| **ChatHTN** (2025) | 符號 HTN core + LLM 補位,並插入**明確 verifier task** | **驗收即任務節點**的設計範本 |
| **LLM-Modulo** (Kambhampati) | LLM 生成 + 外部 critics(含 VAL/PDDL)迭代修正 | **驗收驅動 / verifier-as-critic** 的理論基礎,travel planning 提升 6× |
| **Tree-of-Thoughts / LLM-Compiler** | 搜尋式 / DAG 並行規劃 | 進階版:子任務 DAG 依賴與並行執行 |

> **設計取捨**:純符號 HTN/PDDL 嚴謹但建模成本高、難覆蓋開放任務;純 LLM 規劃靈活但不可靠。**主流共識是 neuro-symbolic 折衷:LLM 出計劃,符號/程式化 verifier 把關。** 本模組採此路線,但把「verifier」抽象成可插拔介面(可以是 LLM-as-Judge、可以是單元測試、可以是 PDDL VAL)。

### 2.2 自我修正與重規劃(Self-Correction / Replanning)

長任務一定會出錯,關鍵是**如何就地修正並重規劃**:

| 技術 | 機制 | 限制(重要) |
|------|------|-------------|
| **Reflexion** (Shinn 2023) | 把失敗轉成 verbal feedback 存入記憶,下次改進 | 只在**任務結束後**才偵測錯誤;需要把 scalar reward 轉成文字 |
| **Self-Refine / Self-Check** | LLM 自我批判並修訂輸出(internal feedback) | 自評有偏誤,易過度自信 |
| **ReflAct** (2025) | 以「goal-state reflection」做 world-grounded 決策 | 需 grounding 訊號 |
| **Devil's Advocate**(Anticipatory Reflection) | 行動前先預想可能失誤 | 增加 token 成本 |
| **AdaPlanner** | 把成功程式存入 skill memory 供日後重用 | 偏 code 任務 |

> **設計取捨**:internal feedback(自評)便宜但不可靠;external feedback(工具/環境/測試結果)昂貴但可信。**最佳實務是優先用 external/grounded 訊號當 verifier,LLM 自評只當補充。** 並且——這點關鍵——**驗收要在每階段就做(就地攔截),不要學 Reflexion 等到全部跑完。**

---

## 3. 驗收與驗證(Acceptance & Verification)

這是本專案的差異化重點,也是多數現成框架最弱的一環。

### 3.1 LLM-as-Judge / Rubric 評分

- **做法**:給 judge LLM 一份 rubric(評分準則)+ 任務描述 + agent 的完整輸出/軌跡(trajectory)+(可選)參考答案,回傳分數與評語。
- **評分維度**(來自 agentic evaluation 文獻):task completion quality、tool selection rationale、planning effectiveness;每維拆子維度 1–10 分後正規化到 [0,1]。
- **關鍵最佳實務**:**先「只看任務」生成 rubric,再拿 rubric 去評 trajectory**(分離 rubric 生成與評分,避免 judge 看到答案後合理化)。這對本模組設計「驗收條件」的產生時機很有指導性:**驗收條件應在規劃階段就定義,而非執行後才補。**
- **已知偏誤**:position bias、verbosity bias、self-preference bias → 需要校準 / 多 judge / 人在迴圈。

### 3.2 形式化 / 程式化驗證(更強保證)

- **PDDL + VAL**:對可形式化的任務,用 plan validator 給**可靠 soundness check**(LLM-Modulo 的 critic 之一)。
- **VeriLA**(2025):human-centered、可解釋的 agent failure 驗證框架,把驗證拆成可被人理解的單元。
- **Plan Verification for Embodied Task Completion**(2025):針對 embodied agent 的計劃驗證。
- **可執行驗證**:對 coding 任務,最強的 verifier 就是**單元測試 / CI**;對資料任務則是 schema/assertion 檢查。

> **設計結論**:驗收不該綁死單一機制。WorkPlanHandler 把 `Verifier` 抽象為可插拔策略,允許**分級驗收**:
> 1. **硬驗收(hard / programmatic)**:測試、assertion、PDDL VAL → 可信、優先。
> 2. **軟驗收(soft / LLM-judge)**:rubric 評分 → 處理開放式輸出。
> 3. **人工驗收(human gate)**:高風險步驟卡關等人確認。

### 3.3 「驗收即閘門(Verifier-as-Gate)」

把驗收結果當作**流程推進的條件**,而非事後報告:
- 通過 → 推進下一階段。
- 失敗 → 觸發重規劃(replan)或重試(retry,帶 reflection 反饋)。
- 連續失敗 N 次 → 升級(escalate)給人或標記 blocked。

ChatHTN 把 verifier 設計成計劃中的明確節點,正是這個概念的學術背書。

---

## 4. 狀態、記憶體與可恢復執行(State / Memory / Durability)

### 4.1 結構化計劃即「外部化記憶 + 注意力錨」(業界最重要的工程洞見)

- **Manus `todo.md` 模式**:把計劃寫成一個 live checklist 檔,每完成一步就勾掉並**重寫到 context 尾端**。Manus 稱之為「**recitation(複誦)**」——刻意把目標反覆推到 context 末端,對抗長 loop 的目標漂移。
- **TodoWrite / planning-with-files**:Spring AI 的 `TodoWriteTool`、開源 `planning-with-files` skill(支援 60+ agents)都採用「**檔案化、可崩潰恢復的 markdown 計劃 + 確定性的完成閘門**」。
- **file system as context**(Manus):把檔案系統當無限、持久、可被 agent 自身操作的外部記憶。

> **設計結論**:計劃不只是控制流,**它本身就是一種記憶機制**。WorkPlanHandler 的 `Plan` 物件要能(a)序列化持久化、(b)以精簡摘要注入 context 尾端(recitation)、(c)被 agent 讀寫更新。

### 4.2 記憶體架構(Memory Architecture)

文獻把 agent 記憶分為三個正交子空間:
- **Working memory(工作/短期)**:當前 session、單次 trial 的決策上下文。
- **Episodic memory(情節)**:有**時間順序**的事件軌跡;沒有 temporal edges 與 episode 邊界,agent 無法回答「先後」問題。
- **Semantic memory(語意)**:跨任務的知識/經驗。

進階研究(2026)強調長任務記憶需要**明確的 consolidation、temporal 結構、有界成長(bounded growth)**,而非無限累積。對本模組:**進度狀態(哪些步驟做完/通過)屬 working,軌跡與失敗教訓屬 episodic,可重用的 skill/rubric 屬 semantic。**

### 4.3 持久化與 Durable Execution

| 方案 | 機制 | 適用 |
|------|------|------|
| **LangGraph Checkpointer** | 每個 super-step 後快照整個 state(JSON 序列化 + thread_id + 版本 hash),崩潰後讀最後 checkpoint 續跑;支援 `exit`/`async`/`sync` 三種 durability 模式、time-travel、human-in-the-loop | **首選整合對象**;v1.0(2025 末)已成 LangChain 預設 runtime,有 PostgresSaver |
| **Temporal** | workflow(確定性)/activity(副作用)分離,記錄 immutable event history,worker 崩潰後 replay 跳過已完成 activity;可跑數天/數年、內建 retry/timer/signal | 重量級、production 級可靠度;適合 Phase 3 後端選項 |
| **DynamoDB / Postgres saver** | 把 checkpoint 落到雲端 KV/DB | 雲端部署 |

> **重要釐清**(Diagrid 觀點):**checkpoint ≠ durable execution**。Checkpoint 解決「狀態快照與續跑」,但 Temporal 級的 durable execution 還保證「精確一次的副作用、確定性 replay」。本模組設計時把**持久化抽象成 `PlanStore` 介面**,MVP 用 LangGraph checkpointer,未來可換 Temporal 後端。

---

## 5. 框架與工具現況(Framework Landscape, 2025–2026)

| 框架 | 編排模型 | 計劃/執行 | 持久化 | 驗收支援 | 對本專案 |
|------|----------|-----------|--------|----------|----------|
| **LangGraph** (v1.0, 2025末) | 有向圖 + 條件邊 | graph 本身即計劃;官方 plan-and-execute 教學 | ✅ 內建 checkpointer(強) | ⚠️ 需自建 | **首選整合 target**;圖結構 = 計劃、可審計、可 rollback |
| **CrewAI** (0.95) | 角色制 crew + process | role/task DSL,易上手 | ⚠️ 較弱 | ⚠️ 需自建 | 可做 adapter,但狀態控制較淺 |
| **AutoGen** (1.0 GA, 事件驅動 v2) | 多 agent 對話 / GroupChat | 對話式湧現計劃 | 中 | ⚠️ 需自建 | 對話式,計劃結構性弱 |
| **OpenAI Agents SDK** (2025/03) | 顯式 handoff | 取代 Swarm;有 planning module | 中 | ⚠️ 需自建 | handoff 模型,可做 adapter |
| **Temporal** | workflow/activity | 程式即編排 | ✅✅ 最強 | N/A(基礎設施) | Phase 3 durable 後端 |
| **Claude Agent SDK** | — | — | Memory API(beta) | — | 記憶體 API 可參考 |

**現況判讀**:
- 主流框架都把「**圖/狀態機 + checkpoint**」做得不錯(尤其 LangGraph),但**「驗收驅動的階段推進」幾乎都要使用者自建**。
- 業界生產系統(Manus/Devin/Claude Code)不約而同採用「**結構化 todo 計劃 + 逐步勾選 + 完成閘門**」,但這些是各自閉源/內嵌的實作,**沒有 framework-agnostic、可插拔的標準模組**。

---

## 6. 缺口分析與本專案定位(Gap → Opportunity)

把上面所有線索疊起來,可見一個清楚的共識骨架:

```
結構化計劃(structured plan)         ← Plan-and-Act / Manus todo.md / HTN
   + 持久化狀態(durable state)        ← LangGraph checkpointer / Temporal
   + 驗收閘門(verifier-as-gate)       ← LLM-Modulo critics / ChatHTN verifier / LLM-as-Judge
   + 重規劃迴圈(replan loop)          ← Reflexion / ADaPT / Plan-and-Act dynamic update
```

**目前缺的東西(WorkPlanHandler 的定位)**:
1. **以「驗收」為一等公民(first-class)**:多數框架把驗收當事後評測,本模組把它當**流程推進的閘門**。
2. **Framework-agnostic 核心 + adapter**:核心抽象不綁 LangGraph;以 adapter 掛載到 LangGraph(MVP)、未來 CrewAI/AutoGen/Temporal。
3. **計劃即記憶(plan-as-memory)**:內建 recitation 與 checkpoint,直接解決目標漂移。
4. **分級可插拔 verifier**:hard(測試/PDDL)/ soft(LLM-judge)/ human gate 三層,使用者按任務挑選。

---

## 7. 對 MVP 的設計建議(承接至 `02-mvp-proposal.md`)

1. **採 Plan-and-Execute + 解耦** 為骨架(Plan-and-Act 為理論依據)。
2. **計劃用結構化資料模型**(非自由文字):`Plan = [Step]`,每個 `Step` 內含 `AcceptanceCriterion`。這是把「驗收目標」變成一等公民的關鍵。
3. **Verifier 抽象為 Strategy 介面**,內建 LLM-Judge 與 programmatic(callable/test)兩種實作。
4. **以 LangGraph 圖實作執行迴圈**:`plan → execute_step → verify → (advance | replan | escalate)`,並用其 checkpointer 做持久化。
5. **計劃狀態持久化 + recitation**:每步把精簡計劃摘要注入 context 尾端。
6. **重試/重規劃策略**:失敗帶 reflection 反饋重試 K 次;仍失敗則重規劃;再不行 escalate。
7. **驗收條件在規劃期生成**(遵循 rubric 最佳實務:先定標準再評)。

> 詳細架構、核心抽象介面、LangGraph 整合方式、里程碑與評測計畫見 **[`02-mvp-proposal.md`](02-mvp-proposal.md)**。

---

## 8. 風險與未解問題(供 Phase 2/3 持續追蹤)

- **LLM-as-Judge 可信度**:自評偏誤如何校準?→ 多 judge / 人在迴圈 / 盡量用 hard verifier。
- **驗收條件本身可能錯**:planner 定的 acceptance criterion 不完整怎麼辦?→ 允許 replan 時修訂 criteria;保留 human override。
- **持久化粒度 vs 成本**:每步 checkpoint 的儲存/延遲成本。→ 用 LangGraph 的 `async` durability 模式折衷。
- **不可逆副作用**:重試/replay 時的 side-effect 冪等性。→ 高風險步驟標記 human gate;長期看 Temporal 的 exactly-once。
- **評測標準**:長任務成功率怎麼客觀量化?→ Phase 3 採用 long-horizon benchmark(如 EcoGym / Robotouille / 自建驗收集)。

---

*參考文獻見 [`references.md`](references.md)。*
