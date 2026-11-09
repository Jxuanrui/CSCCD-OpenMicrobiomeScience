# Data License

## Data in `data/literature_layer/` — CC BY-NC 4.0 (candidate, pending final upstream verification)

This release contains only layers whose upstream licenses permit redistribution:

| Layer | Source | Upstream license |
|---|---|---|
| Drug–microbe sensitivity | Maier et al. 2018 Nat Chem Biol ST3 | CC BY 4.0 |
| Microbe–disease signatures | BugSigDB | CC BY 4.0 |
| LLM-extracted relations | our pipeline (985 assertions; 82 in canonical graph) | ours |

## NOT included (license-restricted; rebuild locally via scripts)

The following layers are part of the full knowledge graph but their upstream
terms (academic-use-only / redistribution not explicitly granted) prevent
redistribution. The full graph (~20,036 edges) is rebuilt locally by running
the ETL scripts against the original sources:

| Layer | Source | Restriction |
|---|---|---|
| Pathway membership | KEGG REST | academic-only (https://www.kegg.jp/kegg/legal.html) |
| Microbe–gene / metabolite–gene | gutMGene | academic use, redistribution not explicit |
| Microbe–disease (dysbiosis) | gutMDisorder | academic use, redistribution not explicit |

Rebuild tooling ships with the v0.1 tool release (see roadmap in README).
