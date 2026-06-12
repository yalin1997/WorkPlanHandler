# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 專案目標(Project Goal)

**WorkPlanHandler** 是一個為 agent planner 設計的「**長任務執行管理模組**」(execution management module for long-horizon agent plans)。它要解決的三件事:

1. **計劃遵循(plan adherence)** — 讓 agent 在數十步以上的長任務中照計劃走,不在長 context 中漂移/遺忘目標。
2. **階段性任務完成(staged completion)** — 把大任務拆成可獨立驗收的子任務,逐步推進。
3. **驗收目標達成(acceptance verification)** — 每個階段有明確「完成定義(DoD)」並自動檢核;**驗收是流程推進的閘門(gate),不是事後評測**——這是本專案的差異化賣點。

設計核心是 **framework-agnostic 可插拔模組**:核心邏輯零框架依賴,透過 adapter 掛載到 LangGraph(首發)等 Python agent 套件。

## 目前狀態:這是「設計/規格」階段的 repo,尚無可執行實作

**動手前必讀**:目前 repo 內容**幾乎全是設計文件 + 介面草圖**,沒有可運作的 engine、沒有測試、沒有 build 工具。

- `src/workplan/` 只有 `models.py` / `protocols.py`,是**介面草圖(interface sketch)**,非功能成品(見檔頭 `⚠️` 註記)。`engine.py`、`verifiers/`、`planners/`、`executors/`、`adapters/` 等**都還不存在**,僅在規格中定義。
- 沒有 `pyproject.toml`、沒有 `tests/`、沒有 CI(這是刻意的決策 D12:規劃階段只寫規格)。
- 文件以**繁體中文**撰寫(技術名詞保留英文)。

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

## 實作順序(里程碑,M1–M3 不需 LLM key)

依「純 mock 首發」(D3)排序,先證明骨架再接真 LLM:
- **M1** models+events+engine + 六路徑單測(pass/fail/retry/replan/insert/escalate),全用 mock 元件。
- **M2** SqliteSaver + langgraph adapter;驗收重點 = **kill process 後同 thread_id 續跑**。
- **M3** 分層 verifier + human gate(`interrupt()`)。
- **M4** 真 LLM planner/judge;**M5** 審計輸出 + E2E demo。

## 開發指令

目前**尚未建立** build/test/lint 工具鏈(D12)。唯一可跑的健全性檢查是介面草圖的 import:

```bash
python3 -c "import sys; sys.path.insert(0,'src'); from workplan import Plan, Step, AcceptanceCriterion, PlanState; print('ok')"
```

開始 M1 實作時,依 `docs/phase2/00 §3` 建立 `pyproject.toml`(核心 `dependencies = []`,adapter/llm/dev 為 optional extras)與 `pytest`。屆時單一測試以 `pytest tests/test_engine.py::test_retry -q` 形式執行。

## Git / PR 慣例

- 開發分支:`claude/agent-task-execution-survey-ass5p3`(在此開發、commit、push)。
- `main` 是 PR 的 base(目前是一個空 root commit,因 repo 起始為空)。PR 對 `main` 開。
- 文件與 commit message 用繁體中文;與需求方互動採**階段問答**釐清需求後再動工。
