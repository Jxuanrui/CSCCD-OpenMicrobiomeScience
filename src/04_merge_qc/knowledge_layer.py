#!/usr/bin/env python3
"""knowledge_layer 五枚举（Phase 2 知识可审计化，2026-09-30 设计定稿）。

四层知识架构（HARNESS_ARCHITECTURE_V1）中本图谱是 Local Knowledge Layer：
只承载研究开始前已有的治理知识，绝不存当前研究结果。为让每条知识
（图边/图节点/向量块）自述层归属、并向消费方显式声明边界，定义五枚举：

  local_kg_curated        本地图谱·策展知识（Tier A，登记策展源）
  local_kg_llm_extracted  本地图谱·文献抽取知识（Tier B，LLM 管线产出）
  live_knowledge          实时知识层（本图禁存——Router 在线查询域）
  research_evidence       研究证据层（本图禁存——AgentLab 研究工作区域）
  method_knowledge        方法知识层（本图禁存）

后三个枚举是"边界哨兵"：本仓任何数据面（TSV/Neo4j/向量库）出现即说明
层边界被突破（例如研究结果混入本地知识），校验器必须报错。这与
AgentLab 侧 Evidence.source_type=CURRENT_STUDY 的 schema 锁定构成双向隔离。
"""
from __future__ import annotations

# 五枚举全集（架构层自描述词汇表）
KNOWLEDGE_LAYERS = frozenset({
    "local_kg_curated",
    "local_kg_llm_extracted",
    "live_knowledge",
    "research_evidence",
    "method_knowledge",
})

# 本图谱数据面允许出现的子集（超出即边界违规）
KG_ALLOWED_LAYERS = frozenset({"local_kg_curated", "local_kg_llm_extracted"})

# 边界哨兵：只允许出现在其他层，绝不允许出现在本图
FORBIDDEN_IN_KG = KNOWLEDGE_LAYERS - KG_ALLOWED_LAYERS


def is_valid_layer(value: str) -> bool:
    return value in KNOWLEDGE_LAYERS


def check_layers(values, *, context: str = "") -> list[str]:
    """校验一批 knowledge_layer 取值；返回违规消息列表（空=通过）。

    规则：取值必须在五枚举全集内，且不得落入 KG 禁存层。
    """
    problems = []
    for i, v in enumerate(values):
        if not is_valid_layer(v):
            problems.append(f"{context}[{i}] 非法枚举值 {v!r}（全集：{sorted(KNOWLEDGE_LAYERS)}）")
        elif v in FORBIDDEN_IN_KG:
            problems.append(f"{context}[{i}] 层边界违规：{v!r} 禁止出现在本图数据面")
    return problems
