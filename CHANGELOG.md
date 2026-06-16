# CHANGELOG

## Phase 3(進行中)— MCP gatekeeper 整合

把驗收閘門包成一組 **MCP tool(HTTP / fastmcp)**,讓 agent 自己當 driver、用「呼叫工具」接入(對比 LangGraph adapter 的「交出控制流」),同時保住「沒過不發下一步」的驗收賣點。設計與決策見 [`docs/phase3/01-mcp-tool-integration-design.md`](docs/phase3/01-mcp-tool-integration-design.md)(決策 D13–D17)。

- **6 個 tool**(`start`/`submit`/`current`/`plan`/`resolve`/`replan`):`submit` 在 server 端跑驗收,回 `may_advance`——不通過拿不到下一步(資訊槓桿;閘門為「軟但真實」,硬保證仍走 LangGraph adapter)。
- **新元件**:`adapters/mcp_server.py`(唯一 import fastmcp;純邏輯 `Gatekeeper` 離線可測)、`stores/json_store.py`(`JsonFilePlanStore`,首條非 LangGraph 持久化路徑)、`verifiers/builtin_checks.py`(宣告式 check 註冊表,agent 經 JSON 引用)。
- **驗收機制**:宣告式 check(hard)+ 選配 LLM-judge(soft);純宣告式零 key、離線可測。
- **打包**:`pip install "workplan[mcp]"`;啟動 `python -m workplan.adapters.mcp_server`。
- **測試**:離線單測 + fastmcp in-memory Client 協定測 + 跨實例續跑(@slow);真 HTTP 連線已手動煙霧測通過。

---

## v0.1.0 — 最小可整合 MVP(M1–M6 完成)

> **77 測試全綠**。M6-3 真 LLM 實測已以 Gemini-3.5-flash 跑完,結果見 `m6_probe_out/m6_probe_record.json`。

### 里程碑

| 里程碑 | 內容 | 狀態 |
|--------|------|------|
| **M1** | models + events + engine + mock 元件 + T1–T8 測試 | ✅ 完成 |
| **M2** | SqliteSaver + LangGraph adapter(kill process 後同 thread_id 續跑) | ✅ 完成 |
| **M3** | 分層 verifier(hard→soft→human,任一 required 層失敗即短路)+ human gate(`interrupt()`)+ 完整 HITL 矩陣 | ✅ 完成 |
| **M4** | 真 LLM planner / judge(provider-agnostic 模型注入)+ ExternalPlanner ingest;LLM 元件以 stub 離線測試 | ✅ 完成 |
| **M5** | 審計輸出(`audit/`:JSON 事件流 + Markdown 摘要 + 寫檔)+ `CallableExecutor`(通用橋接,recitation 注入 + retry feedback)+ 整合 E2E demo + 真 LLM 階段研究報告 demo | ✅ 完成 |
| **M6** | 最小可整合 MVP:CI(GitHub Actions)+ 乾淨環境 git install 測試 + 真 LLM 跨 provider 穩定度測試 + 釘 public API `0.1.0` + 整合 quickstart + 誠實定位文件 | ✅ 完成 |

**MVP 定位(誠實)**:首版 MVP = **LangGraph 外掛**——持久化/HITL/續跑等電池透過 `adapters/langgraph.py` 供應;framework-agnostic 純核心雖零依賴可獨立使用,非 LangGraph 一級整合路徑留待需求驅動。散布走 git install(未上 PyPI)。

**真 LLM 實測結果(M6-3,Gemini-3.5-flash)**:judge 重現性 spread=0.0(5 次完全一致)、planner 結構化輸出穩定可用、fail-closed 行為符合預期。OpenAI 尚未實測。

**Phase 3(需求驅動)**:DAG 並行、Temporal exactly-once、`LangChainToolExecutor` 等。在沒有真實使用情境前不預先投入(YAGNI)。規格見 `docs/phase2/00 §4.2`。

---

### 開發流程 Log

| 日期 | Commit | 里程碑 | 內容 |
|------|--------|--------|------|
| 2026-06-12 | `5d2e559` | Phase 1 | 技術棧/論文 survey 報告(`docs/01-survey.md`)+ MVP 實作提案(`docs/02-mvp-proposal.md`)+ 核心抽象介面草圖 |
| 2026-06-12 | `3ccf997` | — | 加入 `.gitignore`,移除誤 commit 的 `__pycache__` |
| 2026-06-12 | `5fdd4d9` | Phase 2 規劃 | 逐元件實作規格書(`docs/phase2/00`–`05`):決策表 D1–D12、不變量 I1–I6、資料模型/engine/verifier/planner/adapter 規格 |
| 2026-06-12 | `7bb888d` | — | 加入 `CLAUDE.md`(專案導覽與架構鐵則) |
| 2026-06-12 | `8990e50` | **M1** | 核心實作:純函式狀態機 `engine.py`(6 個 reducer + RETRY→REPLAN→ESCALATE 路由)、完整資料模型、13 種事件、mock 三件套、`pyproject.toml`(核心零依賴)、T1–T8 測試全綠 |
| 2026-06-12 | `a7679cf` | — | 加入 pre-commit hooks(pre-commit-hooks v6 + ruff check/format),統一全 repo 程式碼格式 |
| 2026-06-12 | `76e3853` | — | README 補使用方式與開發 log;新增 `examples/demo_mock.py` |
| 2026-06-12 | `d55e1bc` | **M2** | LangGraph adapter + SQLite 持久化:`adapters/langgraph.py`(StateGraph 五節點 + SqliteSaver + interrupt)、`WorkPlanRunner` 門面、A1–A8 測試(含 in-process 與 subprocess 真 kill 續跑)、D9 import 邊界守門測試、`examples/demo_resume.py`。三個 sub-agent 平行開發後整合 |
| 2026-06-13 | `0f36827` | **M3** | 分層驗收閘門:`LayeredVerifier`、`ProgrammaticVerifier`、`HumanGateVerifier`;V1–V11 verifier 單測 + A9–A12 adapter 整合;`examples/demo_layered.py` |
| 2026-06-13 | `dffe0ad` | **M4** | 真 LLM planner / judge:`LLMJudgeVerifier`(soft 層 fail-closed)、`LLMPlanner`(make_plan + replan + decompose)、`ExternalPlanner`(D1 ingest);stub 離線測試;`examples/demo_llm_injection.py` |
| 2026-06-13 | _(M5)_ | **M5** | `audit/render.py`(事件流 + Markdown 摘要,零依賴)、`CallableExecutor`(recitation 注入 + retry feedback,零依賴);`test_audit.py` / `test_executors.py` / `test_research_e2e.py`;`examples/demo_e2e.py` + `examples/demo_research_llm.py` |
| 2026-06-14 | _(M6)_ | **M6** | CI(`.github/workflows/ci.yml`)、乾淨安裝驗證腳本、真 LLM probe(`m6_probe_out/`)、釘 `protocols.py` public API 0.1.0、整合 quickstart(`examples/quickstart_integration.py`)、誠實定位文件(`docs/guide.md`) |
