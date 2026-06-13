# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 專案目標(Project Goal)

**WorkPlanHandler** 是一個為 agent planner 設計的「**長任務執行管理模組**」(execution management module for long-horizon agent plans)。它要解決的三件事:

1. **計劃遵循(plan adherence)** — 讓 agent 在數十步以上的長任務中照計劃走,不在長 context 中漂移/遺忘目標。
2. **階段性任務完成(staged completion)** — 把大任務拆成可獨立驗收的子任務,逐步推進。
3. **驗收目標達成(acceptance verification)** — 每個階段有明確「完成定義(DoD)」並自動檢核;**驗收是流程推進的閘門(gate),不是事後評測**——這是本專案的差異化賣點。

設計核心是 **framework-agnostic 可插拔模組**:核心邏輯零框架依賴,透過 adapter 掛載到 LangGraph(首發)等 Python agent 套件。

## 目前狀態:M1–M5 已實作完成(MVP 機制達標,真實化待 M6)

**動手前必讀**:本 repo **已非規格階段**——核心機制 M1–M5 全部實作完成,68 個測試全綠。

- `src/workplan/` 已有完整實作:`engine.py`(純函式狀態機)、`models.py`/`events.py`、`verifiers/`(Layered/Programmatic/HumanGate/Mock/LLMJudge)、`planners/`(Mock/LLM/External)、`executors/`(Mock/Callable)、`audit/`(render)、`adapters/langgraph.py`。`protocols.py` 仍標「介面草圖」字樣但已是落地契約。
- 已有 `pyproject.toml`(核心 `dependencies=[]` + optional extras)、`tests/`(pytest)、pre-commit(ruff)。**尚無 CI**——這是 M6 待補項(D12「動工再建」已到期)。
- **驗證深度提醒**:M4/M5 的 LLM 元件(planner/judge)至今**只用離線 stub 測過,從未對真模型驗證**。stub 證明「接線正確」,不等於「真效果」(judge 重現性、結構化輸出穩定性、recitation 抗漂移)。真 LLM 端到端實測排在 M6。
- 文件以**繁體中文**撰寫(技術名詞保留英文)。

> 里程碑現況見 `README.md` 進度表與「開發流程 Log」;規格仍以 `docs/phase2/` 為準(衝突以 `00` 決策表為最終依據)。

## 唯一的設計權威來源(Source of Truth)

實作任何東西前,**先讀 [`docs/phase2/00-overview-and-decisions.md`](docs/phase2/00-overview-and-decisions.md)**。它含一張決策表(**D1–D12** 為與需求方確認的決策,**P1–P3** 為已定預設),以及跨元件不變量 I1–I6。所有元件規格都從這些決策推導,衝突時以此為準。

文件閱讀地圖:
- `docs/01-survey.md` + `docs/references.md` — 技術/論文 survey(為何這樣設計的理論依據)。
- `docs/02-mvp-proposal.md` — MVP 架構與里程碑提案。
- `docs/phase2/00`…`05` — 逐元件實作規格(資料模型→engine→verifiers→planner/executor→langgraph adapter)。`docs/phase2/README.md` 是索引。

## 架構大圖(實作時要守的骨架)

四支柱:**結構化計劃 + 持久化狀態 + 驗收閘門 + 重規劃迴圈**。資料流:

```
Planner ──Plan──▶ Engine(純函式狀態機)◀──Executor
  (可插拔)          │ 路由 Action               (副作用都在這)
                    ▼
                 Verifier(分層 hard→soft→human)
                    │
              Events/Audit ──(JSON 事件流 + Markdown 摘要)
                    │  via adapter
          adapters/langgraph(StateGraph + SqliteSaver + interrupt)
```

關鍵設計(理解需跨多份規格):

- **薄殼策略(D2)**:`engine.py` 是**純函式狀態機**——不做 I/O、不呼叫 LLM、不依賴任何框架。它只把 `(state, 外部結果)` 算成 `(新 state, Action, events)`。LLM 呼叫、工具執行、持久化、`interrupt()` 全由 **adapter** 負責。這讓 engine 可純單元測試、換框架時不動核心。
- **engine 是一組 `on_*` reducer**(`initialize`/`on_executed`/`on_verified`/`on_replanned`/`on_human_resolved`),每個對映 LangGraph 的一個節點。路由心臟在 `on_verified`:pass→advance、fail→retry(帶 feedback)→replan→escalate。
- **驗收分層(D10)**:`LayeredVerifier` 依 hard→soft→human 串接,任一 required 層失敗即短路(hard 便宜先跑,省 LLM token)。驗收一律 **fail-closed**(不確定就不放行)。
- **計劃即記憶**:`Plan.render_for_recitation()` 由 executor 注入 prompt 尾端,對抗長任務目標漂移(Manus recitation 模式)。
- **replan 語意(D6)**:保留 `DONE` 步、只重生未完成尾巴、可改驗收條件、`version++`。動態插步(D5)用 `insert_steps`(version 不變),與 replan 區分。

## 鐵則:依賴方向(實作時務必遵守)

`engine.py` / `models.py` / `events.py` / `verifiers/` / `planners/` / `executors/` **禁止** `import langgraph`、`import anthropic`,或 import `adapters/`。**唯一**可 `import langgraph` 的檔案是 `src/workplan/adapters/langgraph.py`(D9)。LLM 透過 LangChain `init_chat_model` 綁定、預設 Claude,且只出現在 `planners/llm_planner.py` 與 `verifiers/llm_judge.py`(D4)。

打包對應此鐵則:核心零依賴,adapter/LLM 為 optional extra(`pip install workplan[langgraph,llm]`)。

## 里程碑(M1–M5 ✅ 已完成;M6 進行中規劃)

依「純 mock 首發」(D3)排序,先證明骨架再接真 LLM:
- **M1 ✅** models+events+engine + 六路徑單測(pass/fail/retry/replan/insert/escalate),全用 mock 元件。
- **M2 ✅** SqliteSaver + langgraph adapter;驗收重點 = **kill process 後同 thread_id 續跑**。
- **M3 ✅** 分層 verifier + human gate(`interrupt()`)。
- **M4 ✅** 真 LLM planner/judge(provider-agnostic 注入);**M5 ✅** 審計輸出 + CallableExecutor + E2E demo。
- **M6(硬化與真實化,新增)** 真 LLM 端到端一次性實測(燒 key 驗證 judge/planner)+ CI(GitHub Actions)+ 乾淨環境安裝測試 + 文件同步。作為 MVP 收尾與 Phase 3 的閘門,細節見 `docs/phase2/00 §4`。
- **Phase 3(需求驅動,非排期驅動)** DAG 並行、Temporal exactly-once、`LangChainToolExecutor`/子 agent executor。詳見 `docs/phase2/00 §4`;在沒有真實使用情境前不預先投入(YAGNI)。

## 開發指令

工具鏈已建立(pytest + ruff + pre-commit)。建議用 uv 建環境:

```bash
uv venv .venv
uv pip install -p .venv -e ".[langgraph,llm,dev]"   # 完整(含 adapter 與 LLM extras)

.venv/bin/pytest -q                 # 全套(68 測試)
.venv/bin/pytest -q -m "not slow"   # 日常(跳過 subprocess 級真 kill 測試)
.venv/bin/pytest tests/test_engine.py::test_retry -q   # 單一測試
.venv/bin/ruff check . && .venv/bin/ruff format --check .   # lint/format(pre-commit 也會跑)
```

純核心(零依賴,M1 mock demo 即可跑)只需 `uv pip install -p .venv -e ".[dev]"`。
demo 在 `examples/`(`demo_mock`/`demo_resume`/`demo_layered`/`demo_llm_injection`/`demo_e2e`/`demo_research_llm`)。

## Git / PR 慣例

- 開發分支:每個里程碑用獨立 `claude/<task>` 分支開發、commit、push(如 M5 用 `claude/m5-milestone-planning-ha33w9`)。實際分支以當次任務指示為準。
- `main` 是 PR 的 base。PR 對 `main` 開;**未經明確要求不自動開 PR**。
- 文件與 commit message 用繁體中文;與需求方互動採**階段問答**釐清需求後再動工。
