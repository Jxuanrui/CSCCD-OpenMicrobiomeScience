![Gut Microbiome Knowledge Graph](docs/images/01-header.jpg)

# Gut Microbiome Knowledge Graph

> A provenance-complete, audit-ready knowledge graph connecting gut microbiome to diseases, drugs, foods, metabolites, genes and pathways — every assertion traceable to its source, every release gated by pre-registered quality thresholds.

## Quick Start

```bash
# Clone
git clone https://github.com/Jxuanrui/CSCCD-OpenMicrobiomeScience.git
cd CSCCD-OpenMicrobiomeScience

# Install dependencies
pip install -r requirements.txt

# Browse the graph (no Neo4j required — reads TSV directly)
streamlit run src/05_analysis/kg_browser.py --server.port 8765

# Query with Neo4j (requires running instance)
python3 src/05_analysis/graph_analysis.py
```

## What's Inside

| Layer | Count | Description |
|---|---|---|
| **Entity nodes** | 6,840 | Microbe / Disease / Drug / Metabolite / Gene / Pathway / Food |
| **Canonical edges** | 20,309 | 13 predicate types (82 条带 LLM 断言链接) |
| **Assertion nodes** | 985 | Each with 15-dim context + provenance + evidence tier |
| **Corpus** | 89,404 | PubTator 3.0 papers, 12 disease domains |
| **Sources** | 6 | Curated (Tier A) + LLM-extracted (Tier B/C) |

## Quality Metrics

| Metric | Value | Method |
|---|---|---|
| Context precision | **91.3%** | User blind annotation, 13 dimensions |
| Food precision | **59/60** | Two-round blind annotation |
| Model (classification) | 80% | DeepSeek, test_100 |
| Model (judge) | 90% | Cross-family verification |

## Architecture

```
Curated Sources (5 DB) ──┐
                         ├──→ merge_qc ──→ candidate_v3 ──→ Neo4j ──→ serving
PubTator (89K papers) ──→ LLM Pipeline (S1→S2→S3→v7) ──┘
```

## Provenance & Governance

Every assertion carries:
- `source_id` — which database/pipeline produced it
- `retrieved_at` — when the source was last synced
- `version` — source version
- `knowledge_layer` — local_kg_curated / local_kg_llm_extracted

Releases pass through a **three-gate system**: automated checks → independent review → user authorization.

## Comparison with Similar Projects

| Feature | This KG | [MicrobiomeKG](https://www.frontiersin.org/journals/systems-biology/articles/10.3389/fsysb.2025.1544432/full) (PMC12425944) | [MINERVA](https://academic.oup.com/bib/article/26/5/bbaf472/8261764) (PMC12454267) |
|---|---|---|---|
| Provenance per-edge | ✅ 4-column | Partial | PMID link only |
| Pre-registered gates | ✅ Test-locked | ❌ | ❌ |
| Cross-family judge | ✅ DS+GLM | ❌ | ❌ |
| Entity types | 7 | 6 | 2 |
| Context precision | 91.3% | Unreported* | Unreported* |

## Schema

Defined in [LinkML](schema/microbiome_kg.linkml.yaml). Key enums:
- `EvidenceTier`: A (curated) / B (≥2 papers) / C (single paper)
- `KnowledgeLayer`: local_kg_curated / local_kg_llm_extracted

## 执行规则

1. **计划→执行→审核**：改动先列计划再执行，执行后由监工独立审查；质量门禁以监工裁决为准（有条件通过须先整改后开工）
2. **修改 src/ 前**必须在 README 进展日志中注明
3. **新建路径**（目录/数据管线/工具）先报审获准再创建，禁止未审先建
4. **单一说明文档**：每项机制只保留一份说明文档，其余并入或删除，禁止多份并存漂移
5. **MVP 先行**：新功能先做最小可用版本并通过验收，再考虑扩展
6. **P0 冻结**：发布周期内 P0 问题未修完不开新轨道（v1.2+ 分级：P0 必改 → P1 强烈建议 → P2 可延后）
7. **执行模型**：单仓库、单 worktree、单路径开发；多终端并行须先经用户与监工批准
8. **API 密钥**只走 `.env`（gitignored），永不入 git/crontab
9. **data/merged 写入**须过 write_guard 闸门（授权键+execution_id+审计账本）
10. **不可逆操作**（删除/覆盖）先备份、再报审、后执行
11. **进程操作**按精确 PID，禁宽匹配 kill

## 进展日志（摘要）

- 2026-10-07 **C2 LightRAG 重建完成**：candidate_v3 全量索引（6,840 实体/20,309 关系，本地嵌入零 API 费）；lightrag_qa.py 修复（语法错误/BIGMODEL 废键/旧路径）+ 接地双路径（实体型问题确定性证据清单，PMID 保证真实；非实体问题 mix 向量检索+预置关键词）；缓存投毒根因定位（llm_response_cache 平铺 dict）
- 2026-10-07 **监工收尾审 P0/P1 整改**：hits@10 1.5%→1.0% 修正；CHANGELOG 倒序；节点口径 7826=6840+985+1 说明；孤立实体 1,473 披露；130K 缺口披露；执行规则对齐 6 条治理规则；graph_analysis.py 改读 KG_MERGED_DIR（默认 candidate_v3，原根路径为旧数据）
- 2026-10-07 **v3.0.0 发布**：B3-B6 全链（985 断言/91.3%/机检 41PASS/物化/serving 切换）
- 2026-10-06 **B4-B6**：merge_qc + context 重算 + B5 盲标（91.3%）+ B6 发布
- 2026-10-05 **B3 全链**：DeepSeek 官方 API S1+S2+S3+v7（24 分钟完成）
- 2026-10-02 **T1 启动**：⑤真算/E1 阈值统一/CI/LightRAG venv/B0 预注册/B1 语料扩容
- 2026-10-02 **v1.3.0 发布**：U3 三合一（⑦授权+serving 切换+worktree 删除）
- 2026-09-30 **阶段二定版**：test_100 D 84%/mesh 86%
- 2026-09-30 **跨家族复核**：DeepSeek 82% 独立验证
- 2026-09-29 **安全事件 R1**：密钥轮换+git 历史重写+pre-commit 扫描

## Project Structure

```
├── src/                    # Pipeline code
│   ├── 01_seed_etl/       # Curated source ETL
│   ├── 02_pubtator/       # PubTator corpus fetcher
│   ├── 03_llm_relation/   # LLM classification (S1→S2→S3→v7)
│   ├── 04_merge_qc/       # Merge + QC + provenance backfill
│   ├── 05_analysis/       # Multi-hop / communities / RotatE / browser
│   ├── 06_qa/             # LightRAG QA (rebuilt on candidate_v3, dual grounding paths)
│   ├── 07_capability/     # write_guard / release_gate / review_prep
│   ├── 07_monitor/         # Cron audit
│   └── 08_route_eval/     # Role gold / mesh_normalize / cross-family
├── mra/                    # MRA subsystem (agent platform / governance)
├── schema/                 # LinkML schema
├── skills/                 # 23 agent skills
├── artifact_engine/        # Literature artifact engine
├── radar/                  # Meta-SeuBiomed
└── data/                   # Runtime data (gitignored)
```

## License

TBD

## Citation

TBD (paper under preparation)

## Known Limitations

- `anatomical_site` 75% (n=4, not statistically significant)
- `geography` 0% (n=2, title-derived legacy issue)
- RotatE hits@10 = 1.0%, MRR = 0.0073 (raw setting, test set 200; simplified self-contained baseline, not production-grade PyKEEN; see `candidate_v3/rotate_report.json`)
- Neo4j node census: 7,826 = 6,840 Entity + 985 RelationAssertion + 1 MaterializationProvenance (per-run audit node, `neo4j_materialize.py`)
- 1,473 entity nodes (21.5%) are isolated (no edges); RotatE embeddings cover only the 5,367 entities appearing in triples
- Corpus 89,404 papers vs pre-registered 130K target (68.8%) — gap deferred to v4
- LightRAG QA rebuilt on candidate_v3 (2026-10-07): entity questions use deterministic evidence lists (verbatim-real PMIDs); vector path for the rest. GLM's verbatim-citation compliance on free-form RAG context is imperfect — that's why the deterministic path exists
- CI `src/**` trigger path pending PAT workflow scope update

---

> **GitHub**: https://github.com/Jxuanrui/CSCCD-OpenMicrobiomeScience

> *Comparison data for MicrobiomeKG/MINERVA based on their published papers; individual metrics unverified by our team (candidate_research).
