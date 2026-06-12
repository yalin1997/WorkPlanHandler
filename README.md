# WorkPlanHandler

> 為 agent planner 設計的「長任務執行管理模組」(Execution Management Module for Long-Horizon Agent Plans)

## 專案目標

研發一個可支援 agent planner 制定並執行**長任務**的執行管理模組，確保 agent 能：

1. **照著計劃進行**(plan adherence)——不在長 context 中漂移、遺忘目標
2. **完成各階段性任務**(staged task completion)——把大任務拆成可驗收的子任務並逐步推進
3. **達成驗收目標**(acceptance verification)——每個階段都有明確的「完成定義 / Definition of Done」並自動檢核

## 設計需求

- **可插拔(pluggable)**:以 framework-agnostic 的核心抽象為主,可掛載到 LangGraph 等 Python 常用 agent 開發套件
- **狀態持久化與可恢復**:長任務可能跨越數小時/數天,需可 checkpoint、可中斷續跑
- **驗收驅動(acceptance-driven)**:以「驗收條件」作為流程推進的閘門(gate),而非單純跑完步驟

## 開發階段

| 階段 | 內容 | 驗收項目 | 狀態 |
|------|------|----------|------|
| **Phase 1** | 技術棧 / 論文理論 survey | Survey 報告 + MVP 實作提案 | ✅ 已交付 |
| **Phase 2 規劃** | 各元件規格書與實作計劃 | [`docs/phase2/`](docs/phase2/) 規格書 | ✅ 已交付 |
| Phase 2 實作 | MVP 實作 (核心抽象 + LangGraph adapter) | 可跑通 plan→execute→verify→replan 迴圈 | 待動工 |
| Phase 3 | 驗收與記憶體強化、評測 benchmark | 量化評測報告 | 規劃中 |

## Phase 1 交付物

| 文件 | 說明 |
|------|------|
| [`docs/01-survey.md`](docs/01-survey.md) | **技術棧與論文理論 Survey 報告** |
| [`docs/02-mvp-proposal.md`](docs/02-mvp-proposal.md) | **MVP 實作提案**(架構、核心抽象、LangGraph 整合、里程碑) |
| [`docs/references.md`](docs/references.md) | 參考文獻與資料來源 |
| [`src/workplan/`](src/workplan/) | MVP 核心抽象的「介面草圖」(interface sketch,非可執行成品) |

## 一句話結論

> 業界主流(LangGraph / Manus / Devin)與學術前緣(Plan-and-Act、LLM-Modulo、Reflexion/ADaPT)的共識正在收斂成同一個骨架:
> **結構化計劃 (structured plan) + 持久化狀態 (durable state) + 驗收閘門 (verifier-as-gate) + 重規劃迴圈 (replan loop)**。
> 目前缺的是一個「**framework-agnostic、以驗收為核心、可插拔**」的整合模組——這正是 WorkPlanHandler 的定位。
