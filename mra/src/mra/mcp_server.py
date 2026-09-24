"""菌群 MCP server：把图谱检索/文献速读/关联分析/知识路由暴露为标准 MCP 工具。

运行（stdio，宿主经 mcpServers 配置挂载）：
  python -m mra.mcp_server
环境依赖：KG_MERGED_DIR（图谱快照源）、COHORT_CONFIG（缺省 var/cohort_config.json）、
RSCRIPT_BIN（r_association 用）、ARK_API_KEY + HTTPS_PROXY（lit_search_read 用）。
工具清单：kg_resolve / kg_neighbors / kg_edge_evidence / r_association / lit_search_read /
method_query / knowledge_route / gap_check（Harness Architecture L0 标准接口面）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

mcp = MCPServer(
    name="microbiome-kg",
    description="肠道菌群知识图谱工具集：实体解析/带证据跨域检索/文献速读/队列关联分析",
)

_GRAPH = None
_CTX = None


def _graph():
    global _GRAPH
    if _GRAPH is None:
        from .kg.graph import KGGraph
        from .kg.snapshot import latest_snapshot
        root = os.environ.get("KG_SNAPSHOTS_ROOT")
        _GRAPH = KGGraph(latest_snapshot(Path(root)) if root else latest_snapshot())
    return _GRAPH


def _tools():
    global _CTX
    if _CTX is None:
        from .kg.tools import build_tool_functions
        _CTX = build_tool_functions(_graph())
    return _CTX


@mcp.tool(description="把菌名/疾病名/代谢物名解析为图谱标准实体（NCBITaxon/MeSH/ChEBI 等 ID）")
def kg_resolve(term: str) -> str:
    return json.dumps(_tools()["kg_resolve"]({"term": term}), ensure_ascii=False)


@mcp.tool(description="查询实体 k 跳内跨域关联邻居，路径携带证据（evidence_tier A/B、pmids、years、confidence）")
def kg_neighbors(term: str, hops: int = 1, categories: list[str] | None = None) -> str:
    return json.dumps(_tools()["kg_neighbors"](
        {"term": term, "hops": hops, "categories": categories}), ensure_ascii=False)


@mcp.tool(description="查询两个实体之间是否存在边及全部证据属性（tier/pmids/years/confidence）")
def kg_edge_evidence(subject: str, object: str) -> str:
    return json.dumps(_tools()["kg_edge_evidence"](
        {"subject": subject, "object": object}), ensure_ascii=False)


@mcp.tool(description=("队列关联分析：单暴露×特征表 偏 Spearman（控协变量，BH 校正）。"
                       "较重（分钟级 R 沙箱），同类参数有缓存。暴露/特征表名见部署配置。"))
def r_association(exposure: str, features: str = "species",
                  exposure_table: str | None = None, max_features: int = 100) -> str:
    from .research.loop import ResearchContext
    from .research.session import ResearchSession
    ctx = ResearchContext(graph=_graph(),
                          session=ResearchSession(question="mcp", target="mcp", run_id="mcp-call"))
    result = ctx.r_association(exposure=exposure, features=features,
                               exposure_table=exposure_table, max_features=max_features)
    return json.dumps(result, ensure_ascii=False)


@mcp.tool(description=("Method Knowledge 检索（本地零 API）：方法学规则库（零方差拒绝/样本对齐/"
                       "秩变换幅度/伪计数/特异性对照/关联≠机制等）。设计分析前应先查询。"))
def method_query(query: str, k: int = 5) -> str:
    from .knowledge.method_rules import search_method_rules
    rules = search_method_rules(query, k=k)
    return json.dumps({"source_type": "METHOD_KNOWLEDGE", "n_rules": len(rules),
                       "rules": [{key: r.get(key) for key in
                                  ("rule_id", "title", "trigger", "risk", "action",
                                   "contraindication", "validation_status")}
                                 for r in rules]}, ensure_ascii=False)


@mcp.tool(description=("Knowledge Router：Local KG 优先，miss 自动降级文献检索"
                       "（LITERATURE/EXTERNAL_LIVE，带 provenance）。"
                       "source_type 五分：LOCAL_KG/EXTERNAL_LIVE/LITERATURE/METHOD_KNOWLEDGE/CURRENT_STUDY。"))
def knowledge_route(term: str, question: str, knowledge_type: str = "auto") -> str:
    from .knowledge.router import route
    return json.dumps(route(_graph(), term, question, knowledge_type=knowledge_type),
                      ensure_ascii=False)


@mcp.tool(description=("Knowledge Gap Detector：对研究设计要素（实体×分析类型）探测知识覆盖，"
                       "返回逐项建议动作（query_local/route_to_live/consult_method_rules/"
                       "manual_design_review）。设计新研究前先跑。"))
def gap_check(entities: list[str], analysis_types: list[str]) -> str:
    from .knowledge.gap import detect_gaps
    return json.dumps(detect_gaps(_graph(), entities=entities,
                                  analysis_types=analysis_types), ensure_ascii=False)


@mcp.tool(description=("文献速读：PubMed 检索+批量结构化复读（消耗 LLM 额度，有缓存）。"
                       "question 为要回答的科学问题。"))
def lit_search_read(query: str, question: str, max_results: int = 20) -> str:
    from .litread import search_and_read
    result = search_and_read(query, question, max_results=max_results)
    return json.dumps({"n_papers": result["n_papers"],
                       "answers": [n.get("answer", "") for n in result["notes"]],
                       "relevant_pmids": [p for n in result["notes"]
                                          for p in n.get("most_relevant_pmids", [])][:10]},
                      ensure_ascii=False)


def main() -> None:
    mcp.run()  # stdio


if __name__ == "__main__":
    main()
