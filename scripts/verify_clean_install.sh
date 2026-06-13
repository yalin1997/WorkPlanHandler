#!/usr/bin/env bash
# M6-2:乾淨環境 git install 驗證(docs/phase2/00 §4.1)。
#
# 在一個全新 venv 裡驗證對外安裝路徑與 optional extras 邊界:
#   階段 1(純核心):只裝套件本體,確認
#       - `import workplan` 成功;
#       - LLM 元件(planners.llm_planner / verifiers.llm_judge)在無 llm extra 時
#         import 失敗(證明未被 eager import 拖進零依賴核心,D9);
#       - 頂層框架(langgraph / langchain)在純核心環境裝不進來。
#   階段 2(完整 extras):裝 [langgraph,llm],確認 adapter 與 LLM 元件可 import。
#
# 用法:
#   scripts/verify_clean_install.sh                # 從本地 checkout 安裝(離線,等同 git install 的 build 路徑)
#   scripts/verify_clean_install.sh "git+https://github.com/yalin1997/WorkPlanHandler.git@<branch>"
#                                                  # 從真實 git URL 安裝(對外使用者實際走的路徑)
#
# 注意:本地 checkout 與 git+URL 走相同 setuptools build backend,產物一致;
# 因此本地模式足以在 CI/離線驗證打包正確性,git 模式用於最終對外煙霧測。
set -euo pipefail

SRC="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "== 來源:$SRC"
echo "== 暫存 venv:$WORKDIR/venv"
# 用 uv 建立隔離 venv(本 repo 推薦工具、與 CI 一致);uv pip 等同 pip 的安裝語意。
uv venv --quiet "$WORKDIR/venv"
PY="$WORKDIR/venv/bin/python"

echo
echo "== 階段 1:純核心安裝(無 extras)=="
uv pip install --quiet --python "$PY" "$SRC"
"$PY" - <<'PYCODE'
import importlib, sys

# 1) 核心可 import
import workplan
from workplan import engine, Plan, PlanState, Action  # noqa: F401
print(f"  核心 import OK,version={workplan.__version__}")

# 2) LLM 元件在無 llm extra 時必須 import 失敗(未被 eager import,D9)
for mod in ("workplan.planners.llm_planner", "workplan.verifiers.llm_judge"):
    try:
        importlib.import_module(mod)
    except ImportError:
        print(f"  {mod} 正確 import 失敗(無 llm extra)")
    else:
        sys.exit(f"邊界破口:{mod} 在純核心環境竟可 import(llm extra 未隔離)")

# 3) 純核心環境不該有 langgraph / langchain
for fw in ("langgraph", "langchain"):
    try:
        importlib.import_module(fw)
    except ImportError:
        print(f"  {fw} 不在純核心環境(正確)")
    else:
        sys.exit(f"邊界破口:純核心竟裝有 {fw}")
PYCODE

echo
echo "== 階段 2:完整 extras 安裝([langgraph,llm])=="
# pip 對「本地路徑 + extras」語法為 "<path>[extras]";git+URL 則為 "<url>#egg=workplan[extras]"
if [[ "$SRC" == git+* ]]; then
  SPEC="${SRC}#egg=workplan[langgraph,llm]"
else
  SPEC="${SRC}[langgraph,llm]"
fi
uv pip install --quiet --python "$PY" "$SPEC"
"$PY" - <<'PYCODE'
import workplan.adapters.langgraph  # noqa: F401
from workplan.adapters.langgraph import WorkPlanRunner  # noqa: F401
from workplan.planners.llm_planner import LLMPlanner  # noqa: F401
from workplan.verifiers.llm_judge import LLMJudgeVerifier  # noqa: F401
print("  adapter + LLM 元件 import OK(extras 邊界正確)")
PYCODE

echo
echo "== 乾淨環境 git install 驗證通過 =="
