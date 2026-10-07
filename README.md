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
| **Nodes** | 7,826 | Microbe / Disease / Drug / Metabolite / Gene / Pathway / Food |
| **Edges** | 20,309 | 13 predicate types (alleviates, produces, aggravates, ...) |
| **Assertions** | 985 | Each with 15-dim context + provenance + evidence tier |
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

| Feature | This KG | [MicrobiomeKG](https://frontiersin.org) | [MINERVA](https://academic.oup.com) |
|---|---|---|---|
| Provenance per-edge | ✅ 4-column | Partial | PMID link only |
| Pre-registered gates | ✅ Test-locked | ❌ | ❌ |
| Cross-family judge | ✅ DS+GLM | ❌ | ❌ |
| Entity types | 7 | 6 | 2 |
| Context precision | 91.3% | Unreported | Unreported |

## Schema

Defined in [LinkML](schema/microbiome_kg.linkml.yaml). Key enums:
- `EvidenceTier`: A (curated) / B (≥2 papers) / C (single paper)
- `KnowledgeLayer`: local_kg_curated / local_kg_llm_extracted

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

> **Status**: Neo4j (bolt://127.0.0.1:17687) ✅ | Streamlit (127.0.0.1:8765) ✅ | Audit cron ✅
