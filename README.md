<div align="center">

**A multi-layer gut microbiome knowledge platform**

Literature-curated knowledge graphs × cohort-scale multi-omics — an integrated
toolkit for microbiome-driven scientific discovery.

</div>

## What this is

A research tool that combines three layers of gut-microbiome knowledge:

1. **Literature layer** — a provenance-complete knowledge graph of
   microbe–disease/drug/metabolite relations, built from curated databases
   (Maier 2018, BugSigDB) and LLM-extracted assertions with cross-family
   model adjudication. Every edge is traceable to its source.
2. **Observation layer** (in development) — cross-cohort metagenomic
   measurements (species abundances, pathway activities) from public aging
   cohorts, physically separated from the literature graph and joined at
   query time.
3. **Method kernel** (in development) — mechanism-chain projection: literature
   prior chains (microbe → metabolite → host gene → pathway) scored against
   measured pathway activities to generate reproducible, testable hypotheses.

## Current release (early stage)

| Artifact | Content |
|---|---|
| `data/literature_layer/` | 9,329 edges / 2,599 nodes / 985 LLM assertions (license-clean subset; every edge with 4-column provenance) |
| `schema/` | LinkML schema with CURIE-pattern validation |

The full graph (~20,036 edges incl. KEGG/gutMGene/gutMDisorder layers) is
rebuilt locally via ETL scripts against original sources (see
`DATA_LICENSE.md`).

## Roadmap

- [ ] v0.1: tool release — config-driven pipeline CLI (governed extraction → merge → release gates)
- [ ] v0.2: observation layer + aging-cohort analysis (22 cohorts, 5,000+ samples)
- [ ] v0.3: mechanism-chain projection method + benchmark against single-edge baselines

## License

- Code: MIT (see `LICENSE`)
- Data: CC BY-NC 4.0 candidate (see `DATA_LICENSE.md`; upstream-bound)

## Citation

TBD (manuscript in preparation)
