# Harness Architecture v1.0 + KG Layer Requirements（图谱侧同步文档 · 2026-09-24）

> 本文档取代 KG_FOOD_NODE_ACTION_SUMMARY.md 作为图谱侧同步主文档。
> 定位声明（最高约束见 HARNESS_ARCHITECTURE_V1.md）：**KG 是 Scientific Research
> Harness 的 Local Knowledge Layer，不是研究结果仓库。**

## 1. 核心边界

- KG 只承载外部既有的、经过治理的稳定知识（curated Tier A / llm_extracted Tier B，
  Tier-C/predicted 拒入——维持现状）。
- **Research Evidence 与 KG 结构隔离**：课题结果（atlas 命中/findings/假设）一律进
  Research Workspace（AgentLab/…/events.jsonl，Evidence.source_type=CURRENT_STUDY
  已在 schema 层锁定），**禁止 current-study findings 自动生成 KG 边**；唯一升级
  通道 = mra knowledge store 的 curated ingestion 状态机（candidate→verified 须人工 approved）。

## 2. 图谱侧重点需求（五项）

| # | 需求 | 说明 |
|---|---|---|
| 1 | **provenance** | 每边带 source_type/evidence_tier/pmids/years/confidence（现有口径维持）；新 ETL 源建议按 mra SourceDescriptor 字段自描述（license 三层/transport/schema/审核） |
| 2 | **entity / relation query** | kg_resolve / kg_neighbors / kg_edge_evidence 三个查询面维持稳定契约（Router 与 MCP 客户端依赖） |
| 3 | **knowledge_layer 标识** | 图谱自描述"我是 LOCAL_KG 层"；节点/边不携带 CURRENT_STUDY 域数据 |
| 4 | **router compatibility** | Router 判 local miss 依赖 resolve 结果；Food 节点与噬菌体实体（见下）落地后显著减少伪 miss |
| 5 | **Research Evidence 隔离** | 见上"核心边界"，双向：KG 不收研究结果，研究结果不冒充 KG |

## 3. 实体域缺口（Gap Detector 实测暴露）

- **Food 节点**（需求单在途）：首批 16 类对齐 atlas food_node_hints；标准名+中文别名+稳定 ID。
- **噬菌体实体**：Gap Detector 实测 S. phage YMC-2011 等 miss（现只能降级文献通道）；
  已知 phage–host relation 若入图，建议 predicate 与 Microbe–Microbe 关系区分（如
  `infected_by`），Tier B 起步。

## 4. 接口约定

- mra 侧对图谱只读（快照消费，sha256+manifest）；图谱管线更新后通知换快照 id。
- 查询接口变更需双侧联调（mra tests/kg 有契约测试）。
