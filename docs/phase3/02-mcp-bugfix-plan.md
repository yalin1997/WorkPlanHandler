# MCP gatekeeper Bug 修復計畫(M7 收尾)

**狀態**:草案,待 review 後實作。**本文只規劃,尚未動 code。**
**範圍**:`adapters/mcp_server.py` 的 MCP gatekeeper(程式碼現於分支 `claude/M7_mcp_server_integration`,實作時併入開發分支)。
**前置**:[`01-mcp-tool-integration-design.md`](01-mcp-tool-integration-design.md)、[`../phase2/00-overview-and-decisions.md`](../phase2/00-overview-and-decisions.md)(I5 / fail-closed 鐵則)。

兩個 bug 在 code review 中確認:**B1 fail-open(致命)**、**B2 read-modify-write race**。本文逐項給「根因 / 修法 / 改哪些檔 / 測試 / 驗收標準」。

---

## 已拍板決策

| # | 決策 | 結論 |
|---|------|------|
| F1 | 驗收層可配置 | server 可選啟用 **hard / soft(llm_judge)/ human** 三層;judge_model 給定才有 soft;human 層要掛 resolve 流程 |
| F2 | 關閉閘門的入口 | **server 全局一個開關**:`mode = "gated"`(預設)/ `"advisory"`;**不做** per-step 粒度 |
| F3 | fail-open 根因怎麼修 | 在 **adapter 層(`start` 時)做能力校驗 + fail-closed**,**不改** core `LayeredVerifier` 語意(守 I5、不擾動 langgraph 路徑) |
| F4 | race 怎麼修 | **Gatekeeper 全程持 per-thread 鎖**包住 `start/submit/resolve/replan`;in-process only,多 process 限制誠實標註 |

---

## B1 — fail-open:宣告了 server 擋不住的驗收 → 零驗收放行(🔴 致命)

### 問題
某步 acceptance `kind="llm_judge"`(或 `"human"`),但預設 server verifier 只有 hard 層
(`build_server` → `LayeredVerifier([("hard", ProgrammaticVerifier(...), True)])`)。

### 根因(追蹤路徑)
1. `_layer_applies("hard", crit)` 回 `"check" in crit.spec`;llm_judge 步的 spec 只有 `rubric`、無 `check` → hard 層被跳過(`verifiers/base.py:31-35`)。
2. 沒有其他層存在 → `LayeredVerifier.verify` 迴圈空轉 → `scores` 空 → **回 `passed=True, score=1.0`**(`verifiers/base.py:84-90`,「全部 required 層通過」的空集合誤判)。
3. `submit` 收到 pass → `may_advance=true` 推進。**fail-open,違反 fail-closed 鐵則與 gatekeeper 賣點。**
4. 兜底也瞎:`_is_gated()` 對 `kind in ("human","llm_judge")` 一律回 `True`(`adapters/mcp_server.py:58-62`),只看「宣告」不問「server 擋不擋得住」→ `start` 不發 warning。

> 關鍵分辨:**operator 主動關閘門(advisory)= OK**;**某步宣告了閘門、server 卻偷偷沒擋 = 必須 fail-closed**。

### 修法(F1 + F2 + F3)
1. **server 宣告能力集**:`build_server` 依參數決定可用層——`hard` 永遠在(離線);`soft` 僅當 `judge_model` 給定;`human` 僅當啟用 human 層(掛 `HumanGateVerifier`,對映既有 resolve 流程)。把「能力集」存進 `Gatekeeper`。
2. **`start` 時能力校驗(fail-closed)**:逐步比對「宣告的 acceptance」vs「能力集」。宣告了卻無法執行 → **`raise ValueError`**(明確訊息:哪一步、宣告什麼、server 缺什麼),agent 無法用假閘門開跑。
3. **修 `_is_gated`**:改成查「實際能力集」而非只看宣告的 kind;llm_judge/human 在 server 無對應層時應視為「未設閘門」並進 warning(與校驗一致)。
4. **新增 advisory 全局開關(F2)**:`mode="advisory"` 時 → 不跑驗收、`may_advance` 恆 true、所有步只記錄(Tier 1,= TodoWrite 式由 agent 自決);此模式**跳過** start 校驗(因為是 operator 有意識地關)。`Verdict`/`warnings` 明示「advisory:無 server 端閘門」。
5. **驗收層可配置(F1)**:`build_server` 新增參數讓 operator 選要啟用哪些層(預設 hard;給 judge_model 加 soft;明示開 human)。

> **刻意不改 core**:`LayeredVerifier` 的「空集合視為過」對 langgraph 路徑是合規的(I5:沒定義 hard 驗收的步不該被擋)。根因修在 adapter 的 `start` 校驗,守住薄殼邊界(D2/D9)。

### 改哪些檔
- `src/workplan/adapters/mcp_server.py`:`build_server`(能力集 / 層配置 / mode)、`Gatekeeper.__init__`(存能力集 + mode)、`Gatekeeper.start`(校驗)、`_is_gated`(查能力集)、advisory 分支於 `submit`/`_verdict`。
- `docs/phase3/01-...md`:補 advisory 模式 + 能力校驗到 §4/§6。
- `CHANGELOG.md`。

### 測試(TDD,先寫測試)
- `start` 宣告 llm_judge 但無 judge_model → **raise**(fail-closed)。
- `start` 宣告 human 但未開 human 層 → **raise**。
- `mode="advisory"` → `submit` 恆 `may_advance=true`、warnings 標明 advisory、不跑 check。
- 配 judge_model 啟用 soft → llm_judge 步真的過 soft 層(沿用 stub judge)。
- `_is_gated` 在「宣告 llm_judge 但無 judge」時回 False 並進 warning。
- 迴歸:既有純 hard programmatic 行為不變。

### 驗收標準
任何「宣告了 server 擋不住的驗收」的步,在 `gated` 模式下**絕不可能**在未實際跑驗收下 `may_advance=true`;要關閘門只有唯一正當入口 `mode="advisory"`。

---

## B2 — read-modify-write race(🟠 並發下 lost update)

### 問題 / 根因
`Gatekeeper.submit` 是 `load() →(算)→ save()`,而 `JsonFilePlanStore` 的 per-thread lock
**只各別保護單次 save/load**(`stores/json_store.py:59-73`),load 放鎖後 save 才重新上鎖,
中間有空窗。**同一 thread_id 的兩個並發請求**會讀到同一基準、後者覆蓋前者 → lost update / 重複推進。

> 釐清常見誤解:code **早已是 per-thread-ID 一檔**;race **不在不同 thread 之間**,分檔救不了。根因是 read-modify-write 橫跨兩次獨立上鎖。

### 修法(F4)
- **Gatekeeper 自持 per-thread 鎖**(`dict[str, threading.Lock]` + guard,或 `RLock`),用 `with self._lock_for(tid):` 包住整個 `start`/`submit`/`resolve`/`replan`(load+compute+save 全程持鎖)。
- 限制誠實標註:`threading.Lock` **只在單一 process 內有效**;多 worker process 需檔案鎖(flock)/CAS,列為需求驅動的後續(YAGNI)。
- 同步把設計文件 §7、CHANGELOG 的「HTTP server 多請求安全」改成誠實範圍:**單 process 內、同 thread 並發安全**。

### 改哪些檔
- `src/workplan/adapters/mcp_server.py`:`Gatekeeper` 加鎖機制 + 四個方法包鎖。
- `docs/phase3/01-...md` §7、`CHANGELOG.md`:修正並發承諾措辭。

### 測試
- 並發 submit 同 thread_id(`threading` 起 2 條)→ 最終 state 一致、無 lost update(cursor / attempts 不錯亂)。
- 不同 thread_id 並發互不阻塞(維持原行為)。

### 驗收標準
同 thread_id 並發 submit 不再有 lost update;不同 thread_id 維持平行不阻塞。

---

## 實作順序(沿用 M6 TDD 紀律)

1. **B1-a** 能力集 + `build_server` 層配置 + mode 開關(先測試)。
2. **B1-b** `start` 能力校驗 + `_is_gated` 修正 + advisory 分支。
3. **B2** Gatekeeper 全程持鎖 + 並發測試。
4. **文件** 設計文件 §4/§6/§7 + CHANGELOG 同步;誠實標註 advisory 與並發範圍。
5. 全套 `pytest -q -m "not slow"` 綠 + ruff;真 HTTP 煙霧測手動覆驗。

> 每步先寫測試再實作;先離線可跑(零 key),soft 層用既有 stub judge。
