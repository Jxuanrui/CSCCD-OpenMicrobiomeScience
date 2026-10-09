<div align="center">

<img src="docs/images/01-header.jpg" alt="Gut Microbiome Knowledge Graph" width="880">

<br/>
<br/>

**A gut-microbiome knowledge graph.**

89K papers in, 20,309 provenance-carrying edges out — every claim traceable to a PMID, every release gated by pre-registered quality thresholds.

<br/>

[![CI](https://github.com/Jxuanrui/CSCCD-OpenMicrobiomeScience/actions/workflows/mra-tests.yml/badge.svg)](https://github.com/Jxuanrui/CSCCD-OpenMicrobiomeScience/actions/workflows/mra-tests.yml)

[Quickstart](#quickstart) · [Use cases](#use-cases) · [How it works](#how-it-works) · [Quality gates](#quality-gates--metrics) · [Changelog](CHANGELOG.md)

</div>

<br/>

## What it is

A local knowledge layer for gut-microbiome research: curated databases (drug–microbe sensitivity, microbe–disease signatures, host-gene regulation) reconciled with LLM-extracted relations from 89,404 PubMed abstracts. It answers "which microbes, which diseases, what evidence" with a PMID on every claim — and it refuses to answer when the graph has no evidence.

- **Every edge carries provenance.** Four columns (`source_id / retrieved_at / version / knowledge_layer`) on all 20,309 edges; every PMID cited in QA is greppable in the serving TSVs.
- **Quality gates are pre-registered and test-locked.** Thresholds (context precision ≥ 0.85, flip rate ≤ 0.15, …) live in constants asserted by tests — they cannot drift between evaluation rounds.
- **Cross-family judging.** Extraction (DeepSeek) and adjudication (GLM) use different model families, so one model's bias cannot stamp its own work.
- **Honest refusals.** The QA layer answers only from graph context and says so when the graph lacks the relation — verified by a test that asks for a relation the graph does not have.
- **Fully rebuildable.** The serving KG is git-tracked; the live Neo4j graph and the QA index are both one command away from reconstruction.

## Use cases

| You want to… | The graph gives you |
| ------------ | ------------------- |
| Check what the literature says about a microbe | Multi-hop fan-out across diseases, metabolites, drugs, genes and pathways, each edge with tier + PMIDs — minutes instead of an afternoon of PubMed searches. |
| Draft an introduction or review section | Evidence-backed sentences ("*F. prausnitzii* is decreased in Crohn's disease — PMIDs …") you can cite directly. |
| Pick a hypothesis worth bench time | RotatE link predictions ranked by score, always labelled `predicted` — a shortlist of untested microbe–disease–metabolite triples. |
| Ask questions in natural language | Grounded QA: entity questions return a numbered evidence list with verbatim PMIDs; anything the graph cannot support returns "insufficient evidence" instead of a guess. |
| Build on it programmatically | Plain TSVs (`data/merged/candidate_v3/`) plus a Streamlit browser — no lock-in, load it into pandas/Neo4j/anything. |

## Quickstart

```bash
git clone https://github.com/Jxuanrui/CSCCD-OpenMicrobiomeScience.git
cd CSCCD-OpenMicrobiomeScience

# multi-hop fan-out query (no services needed)
python3 src/05_analysis/graph_analysis.py query \
    --microbe "Faecalibacterium prausnitzii" --hops 2

# grounded QA (local embeddings; needs one LLM endpoint in .env)
.venv-lightrag/bin/python src/06_qa/lightrag_qa.py ask \
    "Which diseases are associated with Faecalibacterium prausnitzii?"

# interactive browser (fan-out / network / communities / predictions / review)
python3 -m streamlit run src/05_analysis/kg_browser.py

# test suite
cd mra && uv sync && uv run pytest -q
```

## First question

```text
$ python3 src/05_analysis/graph_analysis.py query --microbe "Faecalibacterium prausnitzii" --hops 1
[query] Faecalibacterium prausnitzii (NCBITaxon:853) — 97 cross-domain neighbours
  by category: {Metabolite: 21, Disease: 39, Gene: 37}
1  Disease  Colitis, Ulcerative  MESH:D003093  decreases_abundance_in  A  26296733|27833384|28039159
```

Every row carries the evidence tier (`A` curated · `B` ≥2 papers · `C` single paper pending review · `predicted` = hypothesis, never a claim) and the PMIDs behind it.

## What you can do

| Task | What happens |
| ---- | ------------- |
| **Query the graph** | Single-microbe multi-hop fan-out across 7 entity types, with per-edge tier + PMID. |
| **Ask questions** | Grounded QA over the serving KG: entity questions get a deterministic evidence list; the rest get hybrid retrieval. |
| **Generate hypotheses** | RotatE link prediction (`rotate_predictions.tsv`) — always labelled `evidence=predicted`. |
| **Run governed research** | The `mra/` research loop plans → executes → audits → files evidence, with R-side statistics behind an audit gate that rejects constant exposures. |
| **Audit any release** | Release-gate reports, write-key ledger, snapshot manifests and sha256-checked archives are all in-repo. |
| **Rebuild everything** | Serving KG is git-tracked; `neo4j_materialize.py` rebuilds the live graph from TSVs. |

## How it works

```text
89,404 PubMed abstracts (PubTator 3.0)
  → S1 relation classification · S2 three-vote confirmation · S3 cross-family judge (DeepSeek ↔ GLM)
      → veto rules (subject-attribution, weak-predicate downgrade, multi-food marking)
  → merge_qc (stage=3 contract: only fully validated rows enter)
      → release gate · 41 machine assertions · pre-registered thresholds (test-locked)
  → serving: data/merged/candidate_v3 (git-tracked) → Neo4j + LightRAG index
```

- **Write guard.** Writes under `data/merged` require an authorization key + `execution_id`; keys are one-time and every use lands in an audit ledger.
- **Knowledge layers.** `local_kg_curated` / `local_kg_llm_extracted` live in the graph; research findings stay in the research workspace and never flow back into the KG.
- **STOP rule.** Evaluation cycles cap at 2 iterations; v3 shipped at 91.3% context precision (blind annotation) rather than chase a longer tail.

## Quality gates & metrics

| Gate (pre-registered, test-locked) | Threshold | v3 result |
| --- | --- | --- |
| Context precision (blind-annotated) | ≥ 0.85 | **0.913** (42/46) |
| Food dimension precision | ≥ 0.85 | 0.933 (Wilson [0.84, 0.98]) |
| Machine release checks | 41 PASS / 0 FAIL | **41 / 0** |
| Provenance completeness | 4 columns on every edge | 20,309 / 20,309 |
| Link prediction baseline | report only | RotatE hits@10 = 1.0%, MRR = 0.0073 (raw, n=200) |

| Compared with | [MicrobiomeKG](https://www.frontiersin.org/journals/systems-biology/articles/10.3389/fsysb.2025.1544432/full) | [MINERVA](https://academic.oup.com/bib/article/26/5/bbaf472/8261764) |
| --- | --- | --- |
| Per-edge provenance | ✅ 4-column | partial | PMID link only |
| Pre-registered gates | ✅ test-locked | ❌ | ❌ |
| Cross-family judge | ✅ DS + GLM | ❌ | ❌ |
| Context precision | 91.3% (blind) | unreported* | unreported* |

\* As stated in their publications; not independently verified by us.

Known limitations are recorded per release in the [CHANGELOG](CHANGELOG.md) (anatomical_site 75% n=4; geography 0% n=2; 1,473 isolated lexicon entities; corpus at 68.8% of the 130K pre-registered target — deferred to v4).

## Data sources & acknowledgements

| Source | Use | License |
| --- | --- | --- |
| Maier 2018 *Nat Chem Biol* ST3 | drug–microbe sensitivity | CC BY 4.0 |
| BugSigDB | microbe–disease signatures | CC BY 4.0 |
| gutMGene / gutMDisorder | diet/disease–microbe, host-gene | academic (cite paper) |
| KEGG REST | pathway membership | academic-only ([terms](https://www.kegg.jp/kegg/legal.html)) |
| PubTator 3.0 / NLM MeSH 2026 | corpus, vocabulary | public / public domain |
| DiMB-RE (JAMIA 2025) | relation-extraction evaluation | paper CC BY 4.0; repo unlabelled |
| Europe PMC OA | full-text whitelist per paper | per-paper CC BY/CC0/PD |

Full registry with retrieval dates and versions: [`data/registry/source_registry.tsv`](data/registry/source_registry.tsv). The `radar/` daily literature radar is MIT (© 2026 yin-huamin).

## Repository

```text
src/01_seed_etl      curated source ETL
src/02_pubtator      corpus fetcher
src/03_llm_relation  S1→S2→S3 classification + veto rules
src/04_merge_qc      merge, QC, provenance backfill
src/05_analysis      fan-out queries · browser · RotatE
src/06_qa            grounded QA (LightRAG, dual-path)
src/07_capability    write guard · release gate · review prep · contracts
src/08_route_eval    role gold · MeSH normalisation · cross-family eval
mra/                 research loop, governance, knowledge base (uv, Python 3.10/3.11)
schema/              LinkML schema (EvidenceTier A/B/C, KnowledgeLayer)
skills/              23 agent skills
data/merged/candidate_v3/   serving KG (git-tracked)
data/archive/               sha256-manifested history
```

```bash
python3 -m pytest src/ -q                              # pipeline contract tests
python3 src/07_capability/test_merged_dir_defaults.py  # path-defaults contract
cd mra && uv sync && uv run pytest -q                  # research-loop suite
```

## License

TBD — code and data will be licensed separately (the data side is constrained to non-commercial terms by academic-only upstreams, incl. KEGG). See the [data source table](#data-sources--acknowledgements).

## Citation

TBD (paper in preparation).
