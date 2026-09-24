# Changelog

## v1.2.0 candidate（Unreleased）— P1 Budget / Resource Metering

> v1.1.0 为不可变发布基线；P1 及后续行为变化进入本 candidate。
> 核心原则：**预算耗尽必须让 Agent 学会停下来，而不是学会绕过预算
> 继续完成任务。**

### P1 落地内容（Production Hardening Phase 2 · P1）

- **ResourceUsage 一等计量对象**（`mra/resources.py`，新账本记录类型）：
  research_task_id / plan_id / step_id / capability_id / execution_id /
  model_id / provider / model_calls / input-output-total_tokens /
  external_api_calls / compute-wall_duration_ms / retry_count /
  cache_hit-miss_count / estimated_cost(+currency/pricing_source/
  pricing_version/calculated_at) / measured_at / kind
  （execution | cache_hit | idempotent_retry）。计量 ≠ 成本追踪：compute、
  外部检索、重试、运行时、缓存与 tokens 同为一等维度。
- **ResourceBudget 预算对象**（新账本记录类型，绑 task scope）：九个
  max_* 维度，**未配置=unbounded 而非 0**；修订 append-only
  （supersedes_budget_id + reason）。**不破坏 frozen ResearchTask v1**——
  预算经 ledger 记录挂接（runtime/governance metadata）。
- **Pre/Post 双门**：execute_step 在（免费幂等复用检查之后）执行前做
  Budget Gate——任一配置维度已耗尽 → terminal
  `resource_budget_exhausted`（新增合法停止状态）+ LoopStopped，禁止开启
  新的高成本操作；执行后 Post-accounting 记 ResourceUsage（wall/compute
  实测 + executor 上报的 model/external/retry 用量 + literature.* 的
  from_cache 推导 external=0/cache_hit=1）。
- **预算停止摘要**（`budget_stop_summary`）：已完成/未完成步骤、停止
  原因（exhausted 维度）、usage totals、预算、可复用 candidates/evidence。
- **防绕过**：同 task 静默预算重置被拒（须显式 supersedes + reason）；
  plan revision / retry 天然继承 task 预算；child task 无自有预算时沿
  parent_task_id 链继承（保守默认，不能靠派生任务拿新预算）；
  `resource_usage(include_children=True)` 把子任务用量并入父口径。
- **幂等与计费一致但不混同**：真正重执行→增 usage；幂等 retry
  （候选/裁决/commit 复用路径）→零记账零计费；cache hit→计数不按
  API 全量计费。
- **Replay 正确**：totals 永远由账本聚合重建——restart 后预算不重置
  是构造性保证（Case D 实测）。
- **事实/派生分离**：`estimate_cost(totals, pricing, source, version)` 按
  价目表派生；价格变化产生新派生值，历史 usage 永不被反向改写。
- **凭据纪律延续**：ResourceUsage 为 typed schema（extra=forbid），
  provider/model 可记、api_key/token 无处安放；append 级扫描继续覆盖。
- **观测出口（无 dashboard）**：`Workspace.resource_usage(task_id)` →
  totals / by_capability / by_model / budget(+继承链) / verdict / reusable。

### Golden Budget Cases A–E（`tests/test_p1_budget_metering.py`，12 用例全绿）

- A 正常预算：task_completed，usage < budget
- B 模型预算耗尽：合法停止、不再请求模型、摘要完整
- C External 预算耗尽：停止态= resource_budget_exhausted 而**非**
  evidence_insufficient（"没预算继续查"≠"没有文献证据"）
- D Recovery：crash → restart → 预算余额正确继承（不重置）
- E Idempotent retry：科研事件零新增 + 资源零重复计费（模型真实调用数=1）
- 治理不变式：静默预算重置拒/错误取代目标拒/合法修订 OK；plan revision
  与 child 继承；计量重建一致；cache hit 不全量计费；计价分离；
  凭据拒入；零字段预算=unbounded。

### 量化指标（P1 验收口径，全部测试锁定）

Usage reconstruction mismatch = 0；Budget bypass = 0；Post-restart budget
reset = 0；Idempotent retry double-charge = 0；Unmetered model execution = 0
（loop 产出面）；Unauthorized budget mutation = 0。

- Tests：452 → **464 passed / 2 skipped**（+12 P1 用例）
- 已知边界：planner/dsh adapter 侧的 model 调用计量经公开入口
  `loop.record_usage()` 注入（loop 内 execute 面已自动计量）；capabilit
  实现内自发的外部调用须经 `resource_usage` 扩展上报（literature.* 已接）。

## v1.1.0 (2026-09-24) — Identity, Idempotency, Recovery & Production Hardening

> **正式定位**：Scientific Research Harness v1.1.0。
> Production-ready for：single-operator / single-project / single-host /
> governed scientific workflows / internal Scientific Workspace state /
> READ_ONLY + COMPUTE_ONLY + governed WORKSPACE_WRITE。
> **Not yet production-ready for**：untrusted multi-user operation /
> distributed execution / multi-tenant deployment / **EXTERNAL_WRITE** /
> autonomous irreversible actions。
> v1.1.0 production-ready scope **不包含 EXTERNAL_WRITE**（Registry 中实现数=0）；
> 首个 EXTERNAL_WRITE capability 注册前必须实现并验证三分类恢复：
> idempotent API→幂等键重试 / queryable state→verify-before-retry /
> irreversible-unknown→不自动重试→`recovery_requires_review`。

### Release Blockers（Conditional Go 两项，已落地）

- **graph_snapshot_id 进科研 provenance**（可重复性核心信息）：
  `CandidateResult` / `Evidence` 新增加性字段 `graph_snapshot_id`；ScientificLoop
  显式参数或从 `kg.snapshot.latest_snapshot()` 解析并给候选/证据打标；
  `Workspace.lineage()` 暴露。快照内容指纹/行数/时间在快照 manifest
  （`var/kg_snapshots/<snapshot_id>/manifest.json`）。测试锁定：同一
  ResearchTask 在 snapshot A 原始结论 vs snapshot B 版本化重算，lineage
  可明确区分知识上下文。
- **Ledger durable append**（scientific commit ack ≈ durable commit）：
  durable 模式（**生产默认**）write→flush→fsync 成功后才返回；fsync/写入
  失败 → 回滚未确认尾部并抛出（replay 不视为已提交、seq 不消耗、无假成功
  通道）；回滚失败（磁盘满）则残留被 fail-closed 检出。`durability_mode`：
  durable/buffered（`MRA_LEDGER_DURABILITY` 或构造参数；buffered 供批量
  导入/测试）。全量测试套件在 durable 默认下 ~15s，无性能顾虑。

### Human Review Boundary（v1.1.0 范围声明）

- **Fully automatic**：READ_ONLY 查询 / COMPUTE_ONLY / CandidateResult / ordinary planning
- **Governed automatic**：Evidence creation / downgrade / refute
- **Human-controlled or unavailable**：EXTERNAL_WRITE / irreversible asset
  mutation / permission escalation / destructive operation

### Migration / Compatibility（v1.0.0 → v1.1.0）

- **加性 optional 字段**（旧账本完全可读可 replay，全部缺省兼容）：
  `CandidateResult.research_task_id` / `CandidateResult.graph_snapshot_id`；
  `GovernanceDecision.research_task_id`；`Evidence.graph_snapshot_id`。
- **返回信封加性键**：`already_committed` / `already_applied` / `event_seq` / `reused`。
- **行为变化**（v1 versioning policy 据此进入 v1.1.0）：
  1. 同 decision + 同内容重复提交 `record_evidence` → `already_committed`
     零新事件（v1.0.0 会追加一条冗余 revision）；
  2. `Workspace.append` 默认 durable（v1.0.0 为 buffered 单写）；
  3. task scope 内重复 `analysis_id` 候选 append 硬拒；无域裁决遇跨 task
     同名候选拒绝歧义解析（v1.0.0 依赖调用方约定不重名）。
- **升级动作**：无（加性演进，无 schema 迁移）；依赖 v1.0.0 行为的调用方
  仅需注意上述三点语义收紧。

### Release Gate（全项满足）

Tests：447 → **452 passed / 2 skipped**（+5：RB1 快照区分×1、RB2
durability×4）。Golden / B3 / B4 回归全绿；credential leakage = 0；
governance bypass = 0；duplicate semantic commit = 0；ledger corruption
fail-closed；declared production scope 内无 critical blocker。
明细见 `PRODUCTION_READINESS_REVIEW.md`。

### Production-readiness Hardening（评审第 4/5/6/10/12 节落地项）

- **凭据纪律（结构性）**：`Workspace.append` 对任何账本记录递归扫描疑似
  凭据键名（api_key/token/secret/password 等精确匹配）→ 硬拒；业务字段
  （api_key_used/n_tokens）不误伤。测试：3 负路径（候选 provenance /
  Evidence 深层嵌套 / 误伤对照）。
- **磁盘故障注入**：write failure（目录+文件只读）→ append 响亮失败、
  账本零污染、恢复后状态一致。
- **账本损坏 fail-closed 政策**：半行损坏后读与写均拒绝——恢复逻辑不得
  在不确定状态上猜测成功；政策：人工检视 + 备份恢复，禁止自动"修复"。
- **外部知识不稳定 A–E（litread 故障注入，零真实网络）**：timeout 抛出
  （≠no evidence）且不落缓存；unavailable（异常）与 negative result
  （空命中）结构性分离；malformed 不入 provenance/缓存；缓存命中
  from_cache=True 明示并保留原始 retrieved_at 与 raw_response_sha256
  （cached ≠ current live）。
- **血缘报告层（observability 最小版）**：`Workspace.lineage(task_id)`
  只读查询全链 Task→Plan→候选→裁决→证据(含 mutation)→terminal，版本
  随行（capability/implementation/model/client/policy）。
- **Benchmark Case 005（No-valid-conclusion）**：证据不足 →
  evidence_insufficient 诚实终止，零强产证据。
- 详见 `PRODUCTION_READINESS_REVIEW.md`（分项判级 + 四部输出）。

### B4.1 — Identity & Idempotency Hardening

原则：**same scientific command + retry = same scientific state**；恢复重试
不得被误认为新的科研事件。

- **幂等 Evidence commit**：`workspace.record_evidence` 对同
  (evidence_id, 提交内容, decision) 的重试返回 `already_committed` +
  既有事件引用，不再新增 append-only 修订（内容经 Evidence 模型归一化并
  剥离 governance/created_at 后比较）。只有新 GovernanceDecision 或新
  科学内容才产生新事件。**行为变化**：此前同 decision 同内容重复提交会
  产生一条冗余 revision。
- **mutation 同义去重**：`revise / mark_downgraded / mark_refuted /
  set_canonical` 对"目标状态 + reason 均未变化"的重复请求返回
  `already_applied`，零新事件；新 rationale 合法产生新事件。
- **task-scoped 身份域（结构性，非调用方约定）**：`CandidateResult` 与
  `GovernanceDecision` 新增加性可选字段 `research_task_id`；正式 lookup
  身份为 `(research_task_id, analysis_id)`。`Workspace.append` 对同
  task scope 内重复 analysis_id 的候选**硬拒**（isolation invariant 违例）；
  跨 task 同名合法；无域裁决遇跨 task 同名候选拒绝歧义解析；legacy 无域
  条目行为不变。loop 产出的候选/裁决一律 task 打标。
- **loop 幂等重驱动**：`adopt_plan`（同 plan_id+version 已采纳 → reused）、
  `execute_step`（COMPUTE_ONLY + deterministic 已有候选 → 复用零重算；
  非确定性重算须版本化 analysis_id，否则被唯一性守卫硬拒）、
  `evaluate_and_commit`（已有有效等价裁决 → 复用）、`commit_evidence`
  （已提交 → already_committed；新增 evidence_id 参数支持多步任务）。
- **B4 指标口径收紧**：Cross-task evidence mutation 类指标一律加
  "Unauthorized" 限定（合法跨任务推翻须显式 target/provenance/actor/
  rationale/GovernanceDecision/supporting lineage）。
- 测试：`tests/test_b41_identity_idempotency.py`（幂等 4 用例 + 身份域 4 用例）。

### B3 — Long-horizon Recovery（全规格重做，B4.1 之后）

`tests/test_h5_long_horizon_recovery.py` 按 Case A–F 全规格实证：planning
后中断（plan version/supersedes lineage/current_stage 恢复、不重建同版计划）、
compute-governance 窗口（deterministic 候选复用、fingerprint 不变）、
governance-evidence 窗口（有效裁决复用、零重复 decision）、**evidence commit
后 ACK 丢失（Evidence 数量不增加——retry ≠ 新科研事实）**、多步计划 s1-s4
完成后从 s5 续跑（零重复事件、不跳依赖）、mutation 后同义重试去重。
执行状态由账本派生（候选/有效裁决/Evidence 在场即完成），**未引入第二套
checkpoint/workflow engine**。量化指标全零：Duplicate CandidateResult /
GovernanceDecision / Evidence commit、Lost lineage、Resume-from-wrong-stage、
Illegal re-execution、Replay mismatch。

**证明边界**：B3 验证的是 Scientific Workspace 内部状态的 long-horizon
recovery；EXTERNAL_WRITE 的 exactly-once / verify-before-retry recovery
**未覆盖**，留待 production hardening（当前注册表无 EXTERNAL_WRITE 能力，
见 Production-readiness Review 第 9 节）。

### B-class Validation Gaps 全部补齐（H5 B1–B5 实证闭环）

v1.0.0 Known Limitations 中 B 类四项（另含 v1 冻结前已完成的 B1）全部由
B 系列测试实证，"supported, not yet demonstrated" 状态清零：

| 项 | 验证内容 | 测试 |
|---|---|---|
| B1 Literature influence | 文献知识 B→A 影响决策（v1.0.0 冻结前完成） | `test_h5_literature_influence.py` |
| B2 Complex planning | 7 步计划+依赖图+revision+fallback+方法约束变化+全链 replay | `test_h5_complex_planning.py` |
| B3 Long-horizon recovery | 跨天/跨会话中断恢复：危险窗口恢复、语义等价、KSDS 预算延续 | `test_h5_long_horizon_recovery.py` |
| B4 Concurrent isolation | 并发任务隔离：3 Case + 6 负路径 | `test_h5_concurrent_isolation.py` |
| B5 Multi-omics | 代谢组/蛋白组经数据契约+方法规则适配端到端处理，零核心改动 | `test_h5_multi_omics.py` |

- Tests：394 → 436 passed / 2 skipped（含 B2/B3/B4/B5 与 B4.1 新增用例）
- 遗留：C 类（production gaps）不变。

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
