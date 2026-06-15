"""MCP gatekeeper 端到端測試:走真正的 MCP 協定(fastmcp in-memory Client)。

驗證 tool 註冊、參數/回傳 schema 序列化、以及跨 server 實例經 JsonFilePlanStore
續跑。標記 @slow:需 ``[mcp]`` extra(fastmcp);CI 日常 ``-m 'not slow'`` 跳過。

註:fastmcp 的 HTTP transport 與 in-memory Client 共用同一套 tool manager,
差別僅在傳輸層;in-memory 測試覆蓋了 tool 邏輯 + 協定序列化的真實風險面,
且不依賴開真實 port(避免 CI flakiness)。真實 HTTP 連線於開發手動煙霧測。
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastmcp")  # 未裝 [mcp] 則跳過

from fastmcp import Client  # noqa: E402

from workplan.adapters.mcp_server import build_server  # noqa: E402
from workplan.stores.json_store import JsonFilePlanStore  # noqa: E402

pytestmark = pytest.mark.slow

PLAN = [
    {
        "id": "s1",
        "description": "蒐集競品資料",
        "acceptance": {
            "layer": "hard",
            "kind": "programmatic",
            "spec": {"check": "min_words", "args": {"n": 3}},
        },
    },
    {
        "id": "s2",
        "description": "提出策略建議",
        "acceptance": {
            "layer": "hard",
            "kind": "programmatic",
            "spec": {"check": "contains", "args": {"all": ["建議"]}},
        },
    },
]


async def _run_sequence(tmp_path):
    server = build_server(store=JsonFilePlanStore(root=tmp_path))
    async with Client(server) as client:
        tools = sorted(t.name for t in await client.list_tools())
        assert tools == ["current", "plan", "replan", "resolve", "start", "submit"]

        start = (
            await client.call_tool("start", {"goal": "市場分析", "plan": PLAN})
        ).data
        tid = start["thread_id"]
        assert start["current_step"]["step_id"] == "s1"

        weak = (
            await client.call_tool("submit", {"thread_id": tid, "output": "短"})
        ).data
        assert weak["result"] == "retry" and weak["may_advance"] is False

        ok = (
            await client.call_tool("submit", {"thread_id": tid, "output": "競品 A B C"})
        ).data
        assert ok["result"] == "advanced" and ok["next_step"]["step_id"] == "s2"

        done = (
            await client.call_tool(
                "submit", {"thread_id": tid, "output": "策略建議如下"}
            )
        ).data
        assert done["result"] == "done"
    return tid


def test_mcp_protocol_round_trip(tmp_path):
    asyncio.run(_run_sequence(tmp_path))


async def _resume_across_instances(tmp_path):
    # 第一個 server 實例:跑到 s2
    s1 = build_server(store=JsonFilePlanStore(root=tmp_path))
    async with Client(s1) as c1:
        tid = (await c1.call_tool("start", {"goal": "g", "plan": PLAN})).data[
            "thread_id"
        ]
        await c1.call_tool("submit", {"thread_id": tid, "output": "競品 A B C"})

    # 全新 server 實例,同一個 store root → 應從 s2 續跑(狀態在 JSON 檔)
    s2 = build_server(store=JsonFilePlanStore(root=tmp_path))
    async with Client(s2) as c2:
        cur = (await c2.call_tool("current", {"thread_id": tid})).data
        assert cur["status"] == "running"
        assert cur["step"]["step_id"] == "s2"
        done = (
            await c2.call_tool("submit", {"thread_id": tid, "output": "策略建議"})
        ).data
        assert done["result"] == "done"


def test_resume_across_server_instances(tmp_path):
    asyncio.run(_resume_across_instances(tmp_path))
