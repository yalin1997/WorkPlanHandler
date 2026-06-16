"""WorkPlanHandler — 框架 adapters(可插拔掛載點)。

本子套件本身不 import 任何框架;每個 adapter 模組自行宣告其額外依賴,
缺依賴時於 import 該模組當下報錯(D9)。

用法(LangGraph,需 `pip install 'workplan[langgraph]'`)::

    from workplan.adapters.langgraph import WorkPlanRunner
"""
