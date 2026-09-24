# Changelog

## v1.0.0 (2026-09-24) — Scientific Research Harness v1

### Summary

Model-agnostic Scientific Research Harness：为通用基础模型提供知识、工具、记忆、治理、证据追踪，使其能够可靠执行专业科研任务。upstream runtime = DeepSeek Harness (dsh) @ `46a7f68` (v0.1.7-rc.1, MIT)；Scientific Core 完全独立（零 dsh import）。

### Architecture Milestones

| Phase | Status | Commit Range |
|---|---|---|
| S0 Upstream Freeze | ✅ | `3289bb6..ef39b3d` |
| S1 ACP/MCP Vertical Slice | ✅ | `677d9e9` |
| S2 Scientific Capability Registry | ✅ | `1b018f5..4cb3384` |
| G8 Phase 1 Protocol Portability | ✅ | `0faaf45` |
| Compute-Evidence Separation | ✅ | `2af2552` |
| GovernanceDecision First-class | ✅ | `794f37b` |
| G8 Phase 2 Agent-runtime Portability | ✅ | `d37ca3f` |
| G2 Scientific Planning/Governance | ✅ | `a7054de` |
| Golden Benchmark (3 branches) | ✅ | `9752ee9` |
| H5 System Evaluation (all A) | ✅ | `81a5d3f` |
| v1 Freeze + B1 Literature B→A | ✅ | `82c6a64` |

### v1 Frozen Contracts (9 items)

1. Capability Registry (16-field schema, COMPUTE_ONLY 4-level semantics)
2. CandidateResult (universal envelope, 21 fields)
3. GovernanceDecision (first-class ledger object, 15 fields, 6-verification)
4. Evidence + 6 state-transition invariants
5. ResearchTask (12-field contract)
6. ResearchPlan (versioned, append-only, capability-facing steps)
7. LoopEvent (stage transitions / gate verdicts / terminals)
8. ScientificLoop (7-stage state machine, 4-gate interleaved)
9. Workspace (append-only JSONL, replay)

### Capability Inventory (17 capabilities / 20 implementations)

| capability_id | implementations | side_effect |
|---|---|---|
| method.query | mra.method_rules, mcp.mra | READ_ONLY |
| knowledge.route | mra.router, mcp.mra | READ_ONLY |
| gap.check | mra.gap, mcp.mra | READ_ONLY |
| kg.resolve | mra.kg | READ_ONLY |
| kg.neighbors | mra.kg | READ_ONLY |
| kg.edge_evidence | mra.kg | READ_ONLY |
| vec.query | mra.vecstore | READ_ONLY |
| literature.search | mra.litread | READ_ONLY |
| association.partial_spearman | mra.r | COMPUTE_ONLY |
| atlas.single_exposure_scan | mra.r | COMPUTE_ONLY |
| diversity.alpha_shannon | mra.numpy | COMPUTE_ONLY |
| workspace.record_execution | mra.workspace | WORKSPACE_WRITE (guarded) |
| workspace.record_evidence | mra.workspace | WORKSPACE_WRITE (guarded) |
| workspace.revise_evidence | mra.workspace | WORKSPACE_WRITE (guarded) |
| workspace.mark_downgraded | mra.workspace | WORKSPACE_WRITE (guarded) |
| workspace.mark_refuted | mra.workspace | WORKSPACE_WRITE (guarded) |
| workspace.set_canonical | mra.workspace | WORKSPACE_WRITE (governed) |

### Method Knowledge Base (16 rules, all structured)

All rules validated in real study (Food-Pathway-Phage campaign). Includes: sample alignment, zero-variance guard, rank-vs-magnitude, pseudocount floor, compositionality recheck, module-score axis, specificity control, association≠mechanism, parallel≠mediation, negative-evidence downgrade, deterministic tie-break, canonical versioning, multiple-testing, compositional zero, batch dual-track, Goldberg screening.

### Baseline Metrics (H5 Golden Benchmark)

| Metric | Value |
|---|---|
| Governance Bypass Rate | 0 |
| Illegal State Transition Rate | 0 |
| Unsupported Claim Rate | 0 |
| Replay Fidelity | true |
| Provenance Completeness | true |
| Runtime Portability Modifications | 0 |
| Semantic Consistency (cross-runtime) | fully consistent |

### Golden Benchmark Baseline

- Case: food-pathway-phage-golden-v1
- Standalone: 35 events, ledger_sha256=25621616e8a97d6f
- OpenCode (kimi-k3): 25 events
- Branches: A=evidence_formed, B=attenuated/downgraded, C=refuted_specificity_control
- Evidence terminal state: refuted (correct cascade B→C)

### Versions

| Component | Version |
|---|---|
| Upstream (DeepSeek Harness) | v0.1.7-rc.1 @ 46a7f68 (MIT) |
| Graph snapshot | 2026-09-24-v2 (6633 nodes / 20799 edges) |
| Governance policy | scientific-governance@1.1.0 |
| Method KB | 16 rules (all structured, seed-v1 revision) |
| Tests | 394 passed / 2 skipped |

### Portability Verified

- Runtime/Agent execution: standalone, dsh (DeepSeek Harness), OpenCode (kimi-k3)
- Protocol reference client: MCP Inspector
- Transport: CLI, MCP (stdio), ACP
- Zero modifications to Scientific Core / Registry / Workspace / Governance across all paths

### Known Limitations (B/C class from H5)

**B (supported, not yet demonstrated)**: complex multi-step planning (>5 steps), long-horizon recovery, concurrent task isolation, multi-omics extension.

**C (production gaps)**: larger benchmark set, external knowledge instability handling, credential/permission hardening, cost/latency budget, human review boundary, multi-center extension.
