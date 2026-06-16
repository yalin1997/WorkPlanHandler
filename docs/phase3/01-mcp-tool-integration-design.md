# MCP Tool 整合設計圖(提案 / 草案)

**狀態**:✅ MVP 已落地(Tier 1+2,HTTP / fastmcp);本文為設計依據,實作細節以程式碼為準。
**前置**:[`../phase2/00-overview-and-decisions.md`](../phase2/00-overview-and-decisions.md)、[`examples/integrations/langgraph_commender.py`](../../examples/integrations/langgraph_commender.py)
**一句話**:把 WorkPlanHandler 的「計劃推進 + 驗收閘門」包成一組 **MCP tool**,讓任何 agent(Claude Code / Cursor / Codex / 自家 agent)用「呼叫工具」的方式接入,而**不必把控制流交給 LangGraph graph**。

---

## 0. 實作狀態(已落地)

本設計的 Tier 1+2 已實作完成(決策 D13–D17,見 `phase2/00`):

| 元件 | 檔案 | 說明 |
|------|------|------|
| MCP server | [`src/workplan/adapters/mcp_server.py`](../../src/workplan/adapters/mcp_server.py) | 唯一 import fastmcp;純邏輯在 `Gatekeeper` 類(離線可測),`build_server()` 延遲 import fastmcp 包成 6 個 tool |
| 持久化 | [`src/workplan/stores/json_store.py`](../../src/workplan/stores/json_store.py) | `JsonFilePlanStore`(零依賴、atomic write、per-thread lock);補上首條非 LangGraph 持久化路徑 |
| 宣告式驗收 | [`src/workplan/verifiers/builtin_checks.py`](../../src/workplan/verifiers/builtin_checks.py) | `BUILTIN_CHECKS`:non_empty/min_chars/max_chars/min_words/contains/regex_match/json_valid |
| 範例 | [`examples/integrations/mcp_gatekeeper.py`](../../examples/integrations/mcp_gatekeeper.py) | 離線 demo(零 key):弱輸出→retry+feedback→修正→advance→done |
| 測試 | `tests/test_json_store.py`、`test_builtin_checks.py`、`test_mcp_server.py`、`test_mcp_server_http.py`(@slow) | 離線單測 + fastmcp in-memory Client 協定測 + 跨實例續跑 |

**落地時與草案的出入**:SDK 實測解析為 **fastmcp 3.x**(`mcp.run(transport="http", host=, port=)`、`@mcp.tool`);驗收機制採「宣告式 check(hard)+ 選配 LLM-judge(soft)」;傳輸 localhost-only、不做認證(均為 §10 拍板結果)。

**啟動**:`pip install "workplan[mcp]"` 後 `python -m workplan.adapters.mcp_server --host 127.0.0.1 --port 8000`。

---

## 1. 為什麼要這個(問題回顧)

目前 MVP 的唯一整合路徑是 `adapters/langgraph.py`:engine 當「外迴圈」driver,host agent 只是 graph 裡的一個節點。這是**最強的閘門**(結構上跳不過),但代價是 host 必須把方向盤交給我們——對已經有自己 agent loop 的人,接入門檻高。

主流 agent(Claude Code 的 `TodoWrite`、Codex 的 `update_plan`、Cursor 的 todos)走的是相反範式:**agent 自己當 driver,計劃只是它呼叫的一個 tool**。但它們的計劃 tool 只是「便條紙」——**自己打勾、自己說做完,沒有驗收**。

本設計的目標:**用 tool 的低接入成本,保住我們的驗收閘門賣點**。核心手法 = 把計劃 tool 從「便條紙」升級成「會擋人的收件窗口」(gatekeeper tool):agent 交件 → server 端跑驗收 → 沒過不發下一步。

---

## 2. 三層整合模式(本設計聚焦 Tier 1+2)

| Tier | 模式 | 閘門強度 | 接入成本 | 對映 |
|------|------|----------|----------|------|
| 1 | **advisory tool**(只記錄+recitation,不驗收) | 無 | 極低 | = Claude Code `TodoWrite` |
| 2 | **gatekeeper tool**(server 端驗收,沒過不發下一步) | **軟但真實** | 低 | ← **本設計主推** |
| 3 | **orchestrator / graph adapter**(engine 當 driver) | 硬(結構性) | 高 | 現有 `adapters/langgraph.py` |

MCP server 同時供應 Tier 1 與 Tier 2;Tier 3 維持現狀。

---

## 3. 架構:MCP server = 與 langgraph 平行的另一個 adapter

engine 是純函式狀態機(D2/I6),它本來就把「I/O / LLM / 持久化 / interrupt」全推給呼叫方。MCP server 扮演的正是這個「呼叫方 / adapter」角色,與 `adapters/langgraph.py` 平行——**核心一行都不用動**。

```
   ┌─────────────── Host Agent(driver,有自己的 LLM)────────────────┐
   │  拆解任務 → 呼叫 workplan tools → 執行每一步 → 交件 → 拿下一步     │
   └──────────────┬─────────────────────────────────▲────────────────┘
                  │ MCP (stdio / http)               │ verdict + 下一步 + recitation
                  ▼                                  │
   ┌──────── adapters/mcp_server.py(唯一 import MCP SDK,D9)────────┐
   │  session store: thread_id → PlanState(可序列化,I2)            │
   │  submit → engine.on_executed → Verifier.verify                  │
   │         → engine.on_verified → Decision → save()(I3)           │
   └──────────────┬───────────────────────────────────────────────────┘
                  │ 純函式呼叫(零框架依賴)
                  ▼
   ┌──────── workplan 核心(engine / models / verifiers)────────────┐
   │  initialize / on_executed / on_verified / on_replanned …         │
   │  LayeredVerifier(hard→soft→human,fail-closed,D10)             │
   └──────────────────────────────────────────────────────────────────┘
```

**職責切分(關鍵設計選擇)**:
- **驗收 = 一律 server 端**。這是「閘門」之所以是閘門而非便條紙的根本——必須在我們手裡跑,agent 不能自評。programmatic check 天生 server 端;LLM-judge 由 server 自帶一顆(獨立 judge model,客觀性反而更好,沿用既有 `LLMJudgeVerifier` + `init_chat_model`)。
- **規劃 / 重規劃 = 預設交給 agent**(它有 LLM 與完整 context)。agent 把拆好的結構化 plan 透過 `start` 餵進來;需要 replan 時由 server 回 `replan_needed`,agent 再用 `replan` 補新尾巴。(亦可選配 server 自帶 planner,見 §8 待拍板 Q2。)

於是 server 對 agent 而言就是一個**外部紀律裝置**:它替 agent 記住計劃、每回合 recite、並拒絕讓它跳過驗收。

---

## 4. Tool 介面(對外契約草案)

五個 tool(+1 選配)。型別用 pseudo-schema 表示;`StepView` / `Verdict` 為共用回傳結構。

```
StepView {
  step_id: str
  description: str
  acceptance: { description: str, layer: "hard"|"soft"|"human", kind: str }
  attempt: int                 # 第幾次嘗試(retry 時 >0)
  recitation: str              # Plan.render_for_recitation():注入 prompt 尾端對抗漂移
}

Verdict {
  result: "advanced" | "retry" | "replan_needed" | "escalated" | "done" | "failed"
  passed: bool
  may_advance: bool            # false = 你還不能往下,先處理當前步
  feedback: str                # 失敗時的具體修正指引(餵回給 agent 反思)
  next_step: StepView | null   # advanced / replan 完成時帶出
  recitation: str
  audit_tail: [Event]          # 本次轉移產生的事件(選填,供 host 顯示/記錄)
}
```

| Tool | 入參 | 回傳 | 對映 engine | 說明 |
|------|------|------|-------------|------|
| `workplan.start` | `goal: str`, `plan?: PlanSpec`, `thread_id?: str` | `{ thread_id, current_step: StepView }` | `initialize()` | 建/載入 session。`plan` 由 agent 提供(D1 外部 ingest);省略則需 server planner(Q2)。回傳第一步。 |
| `workplan.current` | `thread_id` | `StepView` \| `DoneView` \| `BlockedView` | 讀 `PlanState.current_step` | 取當前步 + recitation;斷線/重開後對齊用。 |
| `workplan.submit` | `thread_id`, `output: str`, `artifacts?` | `Verdict` | `on_executed` → `verify` → `on_verified` | **閘門核心**。交件即驗收,server 決定 advance / retry / replan_needed / escalate / done。 |
| `workplan.plan` | `thread_id` | 完整 plan 視圖 | 讀 `PlanState` | 巡檢 / 重新 recite / debug。 |
| `workplan.resolve` | `thread_id`, `resolution`, `note?` | `Verdict` | `on_human_resolved` | D8 HITL:`result==escalated` 時人類 approve/reject/edit。 |
| `workplan.replan` *(選配)* | `thread_id`, `new_plan: PlanSpec` | `Verdict` | `on_replanned` | agent 收到 `replan_needed` 後補未完成尾巴(保留 DONE,D6)。 |

**設計細節**:
- `submit` 把 engine 的 `on_executed`(可能直接 `VERIFY`)與 verifier、`on_verified` 三步**在一次 tool call 內走完**,對 agent 暴露成單一原子操作「交件→拿裁決」。
- `may_advance=false` 時,`current` 仍只回**同一步**(帶上 feedback 與遞增的 `attempt`)——這就是「沒過不發下一步」的具體實作。
- retry/replan 次數用盡 → `result="escalated"`,等 `resolve`(對映 engine 的 `ESCALATE`/`blocked`)。

---

## 5. 一次完整互動(sequence:失敗一次再通過)

```
Agent  → start(goal, plan=[s1,s2,s3])
server   initialize() → EXECUTE
       ← { thread_id:"T", current_step: s1, recitation }

Agent  (執行 s1) → submit("T", output_v1)
server   on_executed → VERIFY → verifier.verify(s1) → PASS → on_verified → advance
       ← { result:"advanced", passed:true, may_advance:true, next_step: s2, recitation }

Agent  (執行 s2,但做得不到位)→ submit("T", weak_output)
server   … verify(s2) → FAIL(feedback="缺少 X") → on_verified → RETRY
       ← { result:"retry", passed:false, may_advance:false,
           feedback:"缺少 X", next_step:null, recitation }     # 卡在 s2

Agent  (依 feedback 修)→ submit("T", fixed_output)
server   … verify(s2) → PASS → advance
       ← { result:"advanced", next_step: s3, … }

… s3 通過 → submit 回 { result:"done" }
```

對照現況:`langgraph_commender.py` 裡 commender「自己覺得差不多就跳 Summary」的問題,在這裡被 `may_advance` 從**資訊面**擋住——agent 拿不到 `next_step`,除非當前步真的通過 server 端驗收。

---

## 6. 「tool 模式的閘門」能有多硬?(誠實評估)

**先講限制(必須對使用者誠實)**:MCP tool 終究是 agent 自願呼叫的。沒有任何東西能**結構性地**強迫 agent (a) 一定去呼叫 `submit`、(b) 在 `may_advance=false` 時真的停手不亂編下一步。**硬保證只存在於 Tier 3(graph)**。Tier 2 是「軟但真實」的閘門,靠三道補強把「軟」拉到實務可用:

1. **資訊槓桿(最有效)**:下一步的內容只存在 server 端,agent 不呼叫 `submit`/`current` 就拿不到。合作型 agent 在「想知道下一步要做什麼」的動機下,自然會走完驗收。
2. **Host 機制加固(可選,host-specific)**:在 Claude Code 可配一個 `Stop`/`PostToolUse` hook,在 agent 想收尾時檢查 server 端 `PlanState` 是否全 DONE,沒到就擋回去——把軟閘門接到 host 的硬 hook 上。
3. **稽核留痕(事後可究責)**:即使 agent 作弊跳步,event log(I4)會留下「未通過即推進」的軌跡,可被偵測與回放。

→ 給使用者的定位話術:**「Tier 2 用合作換低接入成本;要不可繞過的硬保證,請用 Tier 3。」**

---

## 7. 狀態與持久化(順手補上「非 LangGraph 電池」缺口)

- session 狀態就是既有的 `PlanState`(I2 全可序列化),server 端以 `thread_id` 為 key 持有;每次轉移後 `save()`(I3)。
- 持久化沿用既有 `PlanStore` Protocol(`save`/`load`)。**但目前唯一實作綁在 langgraph adapter**(D7,SqliteSaver)。本設計需要一個**框架無關的 `PlanStore`**(簡單 `JsonFilePlanStore` 或獨立 `SqlitePlanStore`)。
- ⭐ **副產品價值**:這個 standalone store 正好補上 `CLAUDE.md` 點名「持久化/續跑電池目前只透過 langgraph 供應」的缺口——**MCP 整合會順手交付第一條「非 LangGraph 一級路徑」**,而非只是多一個 adapter。

---

## 8. 打包與依賴鐵則(完全沿用 D9 模式)

```
src/workplan/adapters/
├── langgraph.py        # 現有:唯一 import langgraph
└── mcp_server.py       # 新增:唯一 import MCP SDK(fastmcp / mcp)
src/workplan/stores/    # 新增(可選):框架無關 PlanStore
└── file_store.py       # JsonFilePlanStore / SqlitePlanStore
```

```toml
[project.optional-dependencies]
mcp = ["mcp"]        # 或 fastmcp;僅 adapters/mcp_server.py 可 import
```

- 核心(engine/models/verifiers/planners)**仍禁止** import MCP SDK——鐵則不變,import-lint 規則涵蓋新檔。
- 安裝:`pip install "workplan[mcp,llm]"`(要 LLM-judge 才需 `llm`;純 programmatic 驗收只需 `mcp`)。
- engine 同步(P2),MCP handler 多為 async:adapter 內以同步呼叫 engine(純 CPU)即可;LLM-judge 的 I/O 在 adapter 層處理。

---

## 9. 接入範例(草案)

**Claude Code**(`.mcp.json` / `claude mcp add`):
```jsonc
{
  "mcpServers": {
    "workplan": { "command": "python", "args": ["-m", "workplan.adapters.mcp_server"] }
  }
}
```
之後在 system prompt / CLAUDE.md 約定:「非 trivial 任務先 `workplan.start` 建計劃;每步完成必呼叫 `workplan.submit`,`may_advance=false` 時依 feedback 修正,**不得自行宣告完成**。」(= 用 prompt 把第 6 §1 的資訊槓桿接好。)

**通用 MCP client(Cursor / Codex / 自家 agent)**:同一支 server,行為一致——這就是「萬用插頭」的價值:**包一次,跨框架。**

---

## 10. 待你拍板的決策(review 後鎖定)

| # | 問題 | 我的傾向 |
|---|------|----------|
| Q1 | 主推哪層?Tier 2(gatekeeper)為主、Tier 1 附帶? | **是**,Tier 2 為差異化主力,Tier 1 幾乎免費附帶 |
| Q2 | replan / 初始 planning 由 **agent** 還是 **server** 負責? | 預設 **agent**(它有 LLM+context);server 自帶 planner 列為選配 extra |
| Q3 | 傳輸層 MVP 先做 **stdio** 還是 **http**? | 先 **stdio**(本機、最省事、Claude Code 原生),http 留後續 |
| Q4 | SDK 選 **官方 `mcp`** 還是 **`fastmcp`**? | 先確認當前生態主流(動工前 source-driven 查證) |
| Q5 | 持久化 MVP:**JsonFile** 還是 **Sqlite**? | 先 **JsonFile**(零依賴、夠用、可序列化現成),Sqlite 視併發需求再升 |

---

## 11. 若動工的分階段計劃(暫名 M7,需求驅動)

> 沿用 M6 紀律:新增程式碼一律 TDD;先離線可跑、再接真模型。

1. **M7-1 standalone PlanStore**:`JsonFilePlanStore`(framework-agnostic),補 D7 非 langgraph 缺口。單測:save→kill→load 還原一致。
2. **M7-2 MCP server 骨架(離線)**:`adapters/mcp_server.py`,五 tool,verifier 用 programmatic（不燒 key）。整合測試:走完 §5 sequence(pass / retry / escalate / resolve)。
3. **M7-3 接 LLM-judge**:server 自帶 judge,跑一次真模型煙霧測(沿用 M6-3 probe 風格)。
4. **M7-4 接入 demo + 文件**:Claude Code `.mcp.json` 範例 + 一份繁中 quickstart;誠實標註 §6 的軟閘門定位。
5. **(選配)M7-5 host hook 加固範例**:Claude Code `Stop` hook 擋「未全 DONE 即收尾」。

---

> 本文件為 Phase 3 需求驅動提案,**尚未動工**。與 `phase2/` 鎖定規格的關係:不改任何既有決策(D1–D12 / I1–I6 全部沿用),僅新增一個與 langgraph 平行的 adapter 路徑。若採納,Q1–Q5 鎖定後併入 `phase2/00` 決策表(暫編 D13–D17)。
