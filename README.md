# CSCCD-OpenMicrobiomeScience

**A multi-layer gut microbiome knowledge platform** — literature-curated knowledge
graphs × cohort-scale multi-omics, with a deterministic, evidence-traceable toolkit
for microbiome-driven scientific discovery.

## What's in this repository

1. **Literature layer (data)** — a provenance-complete knowledge graph of
   microbe–disease/drug/metabolite relations (`data/literature_layer/`), built from
   license-clean curated sources (Maier 2018 ST3, BugSigDB) and LLM-extracted
   assertions with cross-model adjudication. Every edge is traceable to its source.
   See `DATA_LICENSE.md` and `PROVENANCE.md`.
2. **Observation layer (in development)** — cross-cohort metagenomic measurements,
   physically separated from the literature graph.
3. **kg-tool (code)** — the deterministic toolkit (`src/kg_tool/`, `src/06_tool/`):
   one command (`kg run`) from disease profile to ranked, fully traceable mechanism
   hypothesis cards (microbe → metabolite → gene → pathway). Zero LLM scoring,
   byte-reproducible outputs, MCP socket for AI assistants.

## Quick start (kg-tool)

See [QUICKSTART.md](QUICKSTART.md). Install: `pip install -e .` (deps: typer /
requests / mcp, pinned). Data packages are distributed separately via `kg init`.

## Testing

`pytest -m "not needs_data"` (public CI profile; data-dependent tests require local
data legs).

## License

Code: MIT. Data: CC BY-NC 4.0 (per-layer details in `DATA_LICENSE.md`).
