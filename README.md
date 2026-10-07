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

1. **修改 src/ 前**必须在 README 进展日志中注明
2. **API 密钥**只走 `.env`（gitignored），永不入 git/crontab
3. **data/merged 写入**须过 write_guard 闸门（授权键+execution_id+审计账本）
4. **不可逆操作**（删除/覆盖）先备份、再报审、后执行
5. **进程操作**按精确 PID，禁宽匹配 kill

## 进展日志（摘要）

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
│   ├── 06_qa/             # LightRAG QA (under repair)
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
- RotatE hits@10 = 1.5% (simplified baseline, not production-grade PyKEEN)
- LightRAG QA index under repair (numpy/jax conflict)
- CI `src/**` trigger path pending PAT workflow scope update

---

> **GitHub**: https://github.com/Jxuanrui/CSCCD-OpenMicrobiomeScience

> *Comparison data for MicrobiomeKG/MINERVA based on their published papers; individual metrics unverified by our team (candidate_research).
