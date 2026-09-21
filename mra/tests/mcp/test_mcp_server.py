"""MCP server 行为检查：真实 stdio 客户端往返（列工具 + 调用 kg_resolve）。"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mra.kg.snapshot import create_snapshot

NODES = "id\tname\tcategory\taliases\txrefs\ttax_rank\n"
EDGES = ("subject\tpredicate\tobject\tsource_type\tevidence_tier\tpmids\tyears\t"
         "support_count\tconfidence\tpolarity\tlast_updated\n")


def _make_snapshot(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "merged_nodes.tsv").write_text(
        NODES + "NCBITaxon:1\tFaecalibacterium prausnitzii\tMicrobe\tF. prausnitzii\t\tspecies\n",
        encoding="utf-8")
    (src / "merged_edges.tsv").write_text(EDGES, encoding="utf-8")
    return create_snapshot(source=src, root=tmp_path / "snaps", snapshot_id="mcp-fx").parent


def test_stdio_roundtrip_list_and_call(tmp_path):
    snaps_root = _make_snapshot(tmp_path)
    env = {**os.environ, "KG_SNAPSHOTS_ROOT": str(snaps_root)}

    async def run():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "mra.mcp_server"], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                names = {t.name for t in tools}
                assert {"kg_resolve", "kg_neighbors", "kg_edge_evidence",
                        "r_association", "lit_search_read"} <= names
                result = await session.call_tool("kg_resolve",
                                                 {"term": "F. prausnitzii"})
                text = "".join(getattr(c, "text", "") for c in result.content)
                assert "NCBITaxon:1" in text

    asyncio.run(run())


def test_module_imports_clean():
    from mra import mcp_server  # noqa: F401 —— 导入无副作用（server 不自动 run）
