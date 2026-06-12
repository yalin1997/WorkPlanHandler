# 參考文獻與資料來源

> 蒐集於 2026-06-12。分類對應 `01-survey.md` 各章節。

## 規劃範式與長任務(§2)

- Plan-and-Act: Improving Planning of Agents for Long-Horizon Tasks (ICML 2025) — https://arxiv.org/html/2503.09572v3 ／ https://github.com/SqueezeAILab/plan-and-act
- Beyond Entangled Planning: Task-Decoupled Planning for Long-Horizon Agents — https://arxiv.org/pdf/2601.07577
- A Goal Without a Plan Is Just a Wish: Global Planner Training for Long-Horizon Agent Tasks — https://arxiv.org/pdf/2510.05608
- Training LLM Agent for Extremely Long-horizon Tasks (KLong) — https://arxiv.org/pdf/2602.17547
- V-CAGE: Context-Aware Generation and Verification for Scalable Long-Horizon Embodied Tasks — https://arxiv.org/pdf/2601.15164
- LLM-Agents-Papers(論文彙整) — https://github.com/AGI-Edgerunners/LLM-Agents-Papers

## 階層規劃 / HTN / 神經符號(§2.1)

- LLMs Can't Plan, But Can Help Planning in LLM-Modulo Frameworks (Kambhampati, Valmeekam) — https://www.semanticscholar.org/paper/b156004675ad3aa5e39a56928afc530aec191044
- LLM-Modulo Framework(概念整理) — https://api.emergentmind.com/topics/llm-modulo-framework
- ChatHTN: Interleaving Approximate (LLM) and Symbolic HTN Planning — https://arxiv.org/pdf/2505.11814
- Hierarchical Task Network Planning(概念) — https://www.emergentmind.com/topics/hierarchical-task-network-htn-planning-framework
- LLMs as Planning Formalizers: A Survey — https://arxiv.org/html/2503.18971v2
- Online Learning of HTN Methods for integrated LLM-HTN Planning — https://arxiv.org/html/2511.12901

## 自我修正 / 反思 / 重規劃(§2.2)

- Reflexion: Language Agents with Verbal Reinforcement Learning (Shinn et al., 2023) — https://arxiv.org/pdf/2303.11366
- ReflAct: World-Grounded Decision Making in LLM Agents via Goal-State Reflection (2025) — https://arxiv.org/pdf/2505.15182
- Reflect before Act: Proactive Error Correction in Language Models — https://arxiv.org/pdf/2509.18607
- Devil's Advocate: Anticipatory Reflection for LLM Agents — https://arxiv.org/pdf/2405.16334
- AutoManual: Constructing Instruction Manuals by LLM Agents — https://arxiv.org/pdf/2405.16247
- Self-Reflection in LLM Agents: Effects on Problem-Solving Performance — https://www.semanticscholar.org/paper/4de6bace974ae5e6b092077950180a86ce48fe6b

## 驗收 / 驗證 / 評測(§3)

- VeriLA: A Human-Centered Evaluation Framework for Interpretable Verification of LLM Agent Failures (2025) — https://arxiv.org/pdf/2503.12651
- Plan Verification for LLM-Based Embodied Task Completion Agents (2025) — https://arxiv.org/pdf/2509.02761
- Beyond Task Completion: An Assessment Framework for Evaluating Agentic AI Systems — https://arxiv.org/pdf/2512.12791
- The Art of Building Verifiers for Computer Use Agents — https://arxiv.org/pdf/2604.06240
- MCP-Bench: Benchmarking Tool-Using LLM Agents with Complex Real-World Tasks — https://arxiv.org/pdf/2508.20453
- LLM-as-a-Judge 指南(Confident AI) — https://www.confident-ai.com/blog/why-llm-as-a-judge-is-the-best-llm-evaluation-method
- Rubric-Based Evaluations & LLM-as-a-Judge(方法與偏誤) — https://medium.com/@adnanmasood/rubric-based-evals-llm-as-a-judge-methodologies-and-empirical-validation-in-domain-context-71936b989e80

## 記憶體架構(§4.2)

- Memory Mechanisms in LLM Agents(概念整理) — https://www.emergentmind.com/topics/memory-mechanisms-in-llm-based-agents
- Empowering Working Memory for Large Language Model Agents — https://arxiv.org/pdf/2312.17259
- Continuum Memory Architectures for Long-Horizon LLM Agents — https://arxiv.org/html/2601.09913v1
- Multi-Layered Memory Architectures for LLM Agents — https://arxiv.org/html/2603.29194
- From Lossy to Verified: A Provenance-Aware Tiered Memory for Agents — https://arxiv.org/pdf/2602.17913

## 持久化 / Durable Execution(§4.3)

- LangGraph Persistence(官方文件) — https://docs.langchain.com/oss/python/langgraph/persistence
- LangGraph(GitHub) — https://github.com/langchain-ai/langgraph
- Build durable AI agents with LangGraph and Amazon DynamoDB(AWS) — https://aws.amazon.com/blogs/database/build-durable-ai-agents-with-langgraph-and-amazon-dynamodb/
- Checkpoints Are Not Durable Execution(Diagrid,重要對比觀點) — https://www.diagrid.io/blog/checkpoints-are-not-durable-execution-why-langgraph-crewai-google-adk-and-others-fall-short-for-production-agent-workflows
- Durable Execution in LangGraph(Vadim's blog) — https://vadim.blog/durable-execution-agents-that-survive-failure-and-resume-where-they-left-off
- Temporal for AI Agents: Durable Execution Guide — https://effloow.com/articles/temporal-ai-agents-durable-execution-guide-2026
- Of course you can build dynamic AI agents with Temporal — https://temporal.io/blog/of-course-you-can-build-dynamic-ai-agents-with-temporal

## 框架現況(§5)

- AutoGen vs CrewAI vs LangGraph vs OpenAI Agents(Galileo) — https://galileo.ai/blog/autogen-vs-crewai-vs-langgraph-vs-openai-agents-framework
- Comparing Open-Source AI Agent Frameworks(Langfuse) — https://langfuse.com/blog/2025-03-19-ai-agent-comparison
- CrewAI vs LangGraph vs AutoGen vs OpenAgents(2026) — https://openagents.org/blog/posts/2026-02-23-open-source-ai-agent-frameworks-compared

## 業界生產模式:計劃即記憶 / todo 模式(§4.1)

- Context Engineering for AI Agents: Lessons from Building Manus — https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus
- Manus AI agent 技術調查(architecture/tool orchestration) — https://gist.github.com/renschni/4fbc70b31bad8dd57f3370239dccd58f
- How Agents Plan Tasks with To-Do Lists(Towards Data Science) — https://towardsdatascience.com/how-agents-plan-tasks-with-to-do-lists/
- Spring AI Agentic Patterns: TodoWrite — https://spring.io/blog/2026/01/20/spring-ai-agentic-patterns-3-todowrite/
- planning-with-files(檔案化計劃 skill,支援 60+ agents) — https://github.com/othmanadi/planning-with-files
- Plans vs tasks: how AI agents think before they act — https://crabtalk.ai/blog/plans-vs-tasks-agent-design

## 長任務 benchmark(§8)

- EcoGym: Evaluating LLMs for Long-Horizon Plan-and-Execute in Interactive Economies — https://arxiv.org/pdf/2602.09514
- Robotouille: An Asynchronous Planning Benchmark for LLM Agents(搜尋結果提及)

> 註:部分 arXiv 編號為近期 preprint;引用時請以各論文最終版本為準。
