# CSCCD-OpenMicrobiomeScience

[![License: MIT](https://img.shields.io/badge/Code-MIT-yellow.svg)](LICENSE)
[![Data: CC BY-NC 4.0](https://img.shields.io/badge/Data-CC%20BY--NC%204.0-blue.svg)](DATA_LICENSE.md)
[![Tests](https://img.shields.io/badge/tests-299%20passed-brightgreen.svg)](#testing)
[![Python 3.10](https://img.shields.io/badge/python-3.10-3776ab.svg)](pyproject.toml)

**A multi-layer gut microbiome knowledge platform** — literature-curated knowledge
graphs x cohort-scale multi-omics, with a deterministic, evidence-traceable toolkit
for microbiome-driven hypothesis generation.

> Input a disease. Get ranked mechanism hypothesis cards
> (microbe -> metabolite -> gene -> pathway), each traceable to the exact
> publications (PMIDs) that support every step. Zero LLM scoring, byte-reproducible.

## Why this tool

LLM-based literature mining is powerful but opaque and hard to reproduce. This
platform takes the opposite trade-off: **AI reads the literature; mathematics does
all the judging.** Every number the tool outputs can be traced to a machine
artifact, and the same input always produces byte-identical output.

| Property | How it is guaranteed |
|---|---|
| Deterministic | No LLM in scoring; no RNG; byte-identical reruns (fingerprint-gated) |
| Evidence-traceable | Every edge carries source PMIDs; every card cites its chain |
| Multi-source synthesis | Evidence from curated databases (gutMGene, BugSigDB) fused via a harmonic-sum score (Open Targets-style) |
| Self-auditing | Null-control arm per run (empty evidence must yield zero votes); three-layer QC; golden fingerprint baselines |
| AI-native | `kg mcp` exposes the tool as an MCP server for AI assistants |

## Architecture

```
116k PubMed abstracts (PubTator3 annotations)
        |
        v   extraction + entity alignment (NCBI taxonomy, MeSH, ChEBI)
Knowledge graph  (3,683 edges; microbe/metabolite/gene/pathway/disease)
        |
        v   kg run --disease <key> --cohort <key>
Disease subgraph -> interaction audit -> 43,773 mechanism chains
        |
        v   four votes + multi-source evidence synthesis (harmonic sum)
Ranked hypothesis cards + HTML report + null-control arm
```

## Installation

```bash
pip install -e .          # deps: typer, requests, mcp (pinned)
pytest -m "not needs_data"   # 299 tests, no data required
```

Data packages (graph snapshots, cohort observations) are distributed separately;
see `QUICKSTART.md`.

## Quick start

```bash
kg run --disease htn --cohort htn --tier2-batch <dir> --out-dir out/
kg explain --subgraph-dir out/subgraph     # plain-language card explanations
kg report --subgraph-dir out/subgraph --out report.html
```

## Command overview (24 subcommands)

| Group | Commands |
|---|---|
| Pipeline | `run`, `ingest`, `subgraph`, `interact`, `deepchain`, `inference`, `hypothesize` |
| Voting & sidecars | `causal-marks`, `graph-scores`, `proximity`, `shuffle-control`, `hop1-shuffle`, `degree-matched-null`, `gene-graft` |
| Validation & discovery | `crosswalk`, `timeslice`, `discover`, `counterfactual` |
| Interfaces | `explain`, `report`, `recipes`, `mcp`, `delivery`, `bridge` |

Highlights:

- `kg discover` — Swanson-style open discovery: candidate associations implied by
  shared intermediates, with explicit "candidate, not proven" disclaimers
- `kg timeslice` — temporal validation: rebuild the graph as of a cutoff year and
  test against associations curated only later
- `kg counterfactual` — for each top hypothesis card, which single supporting edge
  is load-bearing (remove-and-rerank)
- `kg crosswalk` — cross-identifier bridges (NCBI taxid / MeSH / curated DBs) with
  per-edge evidence

## Data layers

- `data/literature_layer/` — license-clean curated subset (Maier 2018 ST3,
  BugSigDB ODC-BY 1.0, plus model-extracted assertions), LinkML schema,
  per-edge provenance. See `DATA_LICENSE.md`, `PROVENANCE.md`.
- Observation layer (in development) — cross-cohort metagenomic measurements,
  physically separated from the literature graph.

## Evaluation summary

Machine-generated, fingerprint-gated numbers (full artifacts in release notes):

- Edge-level precision vs curated silver standard (coverage-restricted lower
  bound): **10.11%** (54/534 strict pairs; Wilson 95% CI 7.8-13.0%)
- Direction consistency vs curated alteration records: 27/46 comparable pairs
- Predicate-language faithfulness audit: over-reading rate 6.09% across 3,956 edges
- Null-control arm: zero votes when evidence is emptied (machine-asserted every run)

## Reproducibility

All scientific artifacts carry SHA-256 fingerprints and frozen golden baselines.
Reruns are byte-identical. See `QUICKSTART.md` for the checkpoint-resume flow.

## Testing

```bash
pytest -m "not needs_data"   # public profile: 299 passed, 5 skipped
pytest                       # full profile requires local data legs
```

## Citation

Version 0.4.0. Paper in preparation; please cite the release tag until then.

## License

- Code: MIT (`LICENSE`)
- Data: CC BY-NC 4.0; layer-specific terms in `DATA_LICENSE.md`
  (BugSigDB-derived subset: ODC-BY 1.0, cite BugSigDB, Nat Biotechnol 2024)
