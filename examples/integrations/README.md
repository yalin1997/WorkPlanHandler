# 整合紀錄(Integration Records)

這個資料夾存放 WorkPlanHandler 與各種實際 agent 架構的整合範例。
每個範例都可離線執行(使用 stub LLM),並附有架構說明與整合重點。

## 範例清單

| 檔案 | 情境 | 解決的問題 |
|------|------|-----------|
| [`langgraph_commender.py`](langgraph_commender.py) | LangGraph 架構:planner → commender → tool/summary | Commender 提早結束、不把計劃做完 |

## 執行方式

```bash
# 需要 workplan[langgraph] extra
python examples/integrations/langgraph_commender.py
```

## 如何新增一個整合紀錄

1. 在此資料夾新增一個 `.py` 檔,命名格式:`<框架>_<情境>.py`
2. 在本 README 的範例清單加一行
3. 檔頭 docstring 說明:原始架構、遇到的問題、整合方式、離線執行指令
