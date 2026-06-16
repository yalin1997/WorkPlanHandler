# 整合紀錄(Integration Records)

這個資料夾存放 WorkPlanHandler 與各種實際 agent 架構的整合範例。
每個範例都可離線執行(使用 stub LLM),並附有架構說明與整合重點。

## 範例清單

| 檔案 | 情境 | 解決的問題 |
|------|------|-----------|
| [`langgraph_commender.py`](langgraph_commender.py) | LangGraph 架構:planner → commender → tool/summary | Commender 提早結束、不把計劃做完 |
| [`mcp_gatekeeper.py`](mcp_gatekeeper.py) | MCP tool:agent 自己當 driver,每步 `submit` 交件受驗 | 「便條紙」式計劃缺真驗收,弱輸出被放行 |
| [`mcp_gatekeeper_modes.py`](mcp_gatekeeper_modes.py) | MCP 驗收層配置:hard/soft 層 + advisory 開關 | 宣告了 server 擋不住的驗收層 → fail-open(B1);關閘門需有正當入口 |

## 執行方式

```bash
# 需要 workplan[langgraph] extra
python examples/integrations/langgraph_commender.py

# MCP gatekeeper:純核心即可跑(離線),真 server 需 workplan[mcp]
python examples/integrations/mcp_gatekeeper.py
python examples/integrations/mcp_gatekeeper_modes.py   # 層配置 / advisory;soft 層需 workplan[llm]
```

## 如何新增一個整合紀錄

1. 在此資料夾新增一個 `.py` 檔,命名格式:`<框架>_<情境>.py`
2. 在本 README 的範例清單加一行
3. 檔頭 docstring 說明:原始架構、遇到的問題、整合方式、離線執行指令
