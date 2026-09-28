# Changelog

## v1.2.0 (2026-09-25) — Production Hardening（资源治理·外部契约·备份恢复·多库隔离·外部写准入）

> **正式定位**：Scientific Research Harness v1.2.0。
> Production-ready for：v1.1.0 全部范围 **+ 可验证备份/恢复 + single-host
> multi-workspace isolation + 全维资源预算治理 + 外部知识 A–E 契约 +
> EXTERNAL_WRITE 准入框架（框架 READY，execution 不开放——registry 实现
> 数仍为 0，首个能力注册须单独 capability review + recovery/governance/
> integration tests）**。
> Not yet production-ready for：EXTERNAL_WRITE execution / untrusted
> multi-user / SaaS multi-tenancy / distributed execution / H6 自主能力。
> 评审：Production Hardening Review 五项复核全过（见
> PRODUCTION_READINESS_REVIEW.md v1.2.0 节）；最终门禁 **505 passed /
> 2 skipped**。

### Migration / Compatibility（v1.1.0 → v1.2.0）

- **加性记录类型 ×4**（旧账本完全可读可 replay，缺省兼容）：
  `ResourceUsage` / `ResourceBudget`（P1）、`CrossWorkspaceReference`（P4）、
  `ExternalWriteRecord`（P5）；WorkspaceState 对应计数器加性新增。
- **加性字段**：无破坏性字段变更（预算/隔离/外部写均经新记录类型与 ctx
  挂接，frozen ResearchTask v1 顶层语义零改动）。
- **行为变化**（v1 versioning policy 据此进入 v1.2.0）：
  1. `execute_step` 增加前置预算门——预算耗尽（task 或 workspace 池）→
     `resource_budget_exhausted` 新合法停止（未配置预算 = unbounded 不受影响）；
  2. capability 写入面增加 ctx 激活式跨库守卫——ctx 携带 workspace_id 且
     目标不同且无 grant 时拒绝（legacy ctx 无绑定，行为不变）；
  3. 外部写域默认拒绝（新增能力面，v1.1.0 无此路径故无存量影响）；
  4. `CachedSource` 默认 hit_first（同参数重复查询零 API；fallback 显式
     选择）。adapter 缓存为 P2 新增面，无存量影响。
- **升级动作**：无（加性演进，无 schema 迁移；备份/恢复跨 v1.1↔v1.2
  兼容——schema 兼容表内）。

### Known Limitations（v1.2.0）

- planner/dsh 侧 model 调用计量经公开入口 `loop.record_usage()` 注入
  （loop 执行面已自动计量；capability 内自发外部调用经 resource_usage
  扩展上报，literature.* 已接，其余待逐个接线）；
- H6（自主假设/跨会话记忆/多 Agent 协作）未启动；
- multi-tenant/SaaS、分布式执行、网络隔离明确排除；
- 首个 EXTERNAL_WRITE capability 落地时自动成为 release blocker（须携
  契约 + 三分类恢复 + 治理 + 集成测试）。

### Production Hardening Review（发布前复核，5 用例 + 故障矩阵）

- 整合复核：Governance 全链 / Reproducibility / Isolation 合并口径 /
  Resource Governance——四维各一 E2E 用例（`tests/test_production_hardening_review.py`）；
- **统一故障矩阵**（七行代码锁定）：ledger corruption→fail closed /
  checksum mismatch→reject restore / external timeout→unknown-unavailable /
  malformed source→reject evidence / budget exhaustion→legal stop /
  workspace violation→reject / external unknown state→recovery_requires_review；
- 评审发现并修复：① backup replay_summary 补 P4/P5 记录类型计数（两侧
  同口径）；② CachedSource 缓存策略显式化（hit_first 默认 / fallback 可选）。
- Tests：500 → **505 passed / 2 skipped**（+5 PHR 用例）

### P5 — EXTERNAL_WRITE Recovery Framework

> P5 不开放外部写。它建立**任何未来 EXTERNAL_WRITE capability 的准入标准**：
> 系统知道什么时候可以写、写失败后发生什么、什么时候绝不能自动重试。
> 内部 Workspace 的 exactly-once 语义**不得外推**到外部世界。

- **ExternalWriteContract**（`mra/external_write.py`）：capability_id /
  target_system / write_type / idempotency_support / verification_support /
  rollback_support / side_effect_level / approval_requirement；
  **类型-能力一致性准入校验**——idempotent 须声明幂等支持、queryable 须
  声明可查询、irreversible 不得声明两者（Missing recovery policy = 0）。
- **三分类恢复策略（固化）**：Type A idempotent（账本派生幂等键，restart
  后稳定，同键重试结果等价）；Type B queryable（**unknown 先 verify 再
  决定**，verify 不可用/仍未知 → 保持 unknown 禁止盲重试；verify=failed
  才允许重试）；Type C irreversible/unknown（**不自动重试** →
  `recovery_requires_review` 吸收态，唯一出口是人工 `resolve_review`
  （须署名 reviewer））。
- **生命周期状态机**：planned → authorized → submitted → acknowledged →
  verified → completed；任意点可 → unknown（一等公民，非 success/failed
  二值）；failed → submitted（明确失败后的新尝试）；非法迁移硬拒。
- **治理铁律**：默认不可执行——authorize = GovernanceDecision 裁决（须在
  账本且允许）+ **workspace 外部写域**（P4 IsolationRegistries 新增
  `check_external_write_scope`，**默认拒绝**，capability 注册存在 ≠ 允许
  写）双闸；approval lineage（decision_id + approval）随记录前向携带。
- **ExternalWriteRecord**（新账本记录类型）：write_id / 幂等键 / intent /
  decision_id / approval / state / payload-result digest / verification /
  error / note——每次迁移 append-only，全部既有事实前向携带。
- **Replay 零重执行（结构性）**：transport 不在 Workspace 内——replay/
  restore 只重建状态（Case F：restore 到全新 root 后 writes() 完整重建，
  transport 调用数不变）。
- Cases A–F + 指标 8 用例（`tests/test_p5_external_write.py`）：A 幂等重试
  零重复副作用；B ACK 丢失 + restart 同键恢复；C queryable 先查再决（盲
  重试 = 0）；D irreversible 进人工处置（自动路径全封死、缺署名拒绝）；
  E workspace 写域边界（A 允许 B 拒绝 + 未授权不可执行）；F backup/replay
  记录完整 + 零重执行；契约一致性拒绝；同 intent 重复计划拒绝。
- 验收指标全锁死：Unauthorized external write / Blind retry on unknown
  state / Duplicate external side effect / Missing recovery policy /
  Missing approval lineage / Replay-triggered external execution = 0。
- P5 明确不做：自动发布/部署系统、自动修改第三方数据库、自动发送不可
  撤销消息——安全框架，不是开放写权限。
- **注册表 EXTERNAL_WRITE 实现数仍为 0**（准入门槛就位，首个能力落地须
  携带契约 + 三分类恢复测试）。
- Tests：492 → **500 passed / 2 skipped**（+8 P5 用例）

### P4 — Multi-workspace Isolation

> P4 验收标准：两个项目同时存在时，系统**不知道、不读取、不修改**另一个
> 项目不应该看到的科研事实。workspace 成为真正的安全边界，不是文件夹名。

- **隔离层**（`mra/isolation.py`）：`CrossWorkspaceGrant`（source→target ×
  scope=read/write/mutate/backup，authorization+provenance）+
  `IsolationRegistries`（grants / KG 可见性 / 凭据 scope，落位
  `<root>/_isolation/*.json`——不属于任何 study 账本，天然不进 per-study 备份）。
- **capability 写入守卫（ctx 激活式）**：`record_evidence /
  record_execution / revise / downgrade / refute / set_canonical` 统一经
  `guard_workspace_write`——ctx 绑定 workspace_id 且目标 study 不同且无匹配
  scope grant → 拒绝；ctx 无 workspace_id = legacy 单信任域（全部既有行为
  零变化）。scope 精确匹配：write grant 不开 mutation，反之亦然。
- **跨库证据引用**：默认 workspace-private；跨库唯一合法形态 =
  `CrossWorkspaceReference` 记录（source/target workspace + approval +
  provenance，**引用而非复制**——源证据不离开源账本）；grant 必须**真实
  登记在注册表**（形状正确 ≠ 已授权，伪造授权字典被拒）。
- **KG 可见性**：snapshot identity ≠ access permission——shared（默认，
  向后兼容）/ private（workspace 归属）/ restricted（permission）；
  loop 显式快照须过权限检查（他人 private → IsolationError），自动解析
  只取当前 workspace 可见的最新快照（全部不可见 → 不使用任何快照）。
- **凭据边界**：workspace auth_scope 注册表（允许的外部 source 集合，
  未登记=legacy 全域）；`query_with_scope` 查询前强制 check
  （Credential scope violation = 0）。
- **workspace 预算池**：`ResourceBudget` 以 `__workspace__` scope 声明即约束
  全 workspace 所有 task 的合计用量（`resource_usage(__workspace__)` 汇总）；
  loop 预算门叠加 workspace 池判定（`workspace:` 前缀标识），child task
  无法分裂绕过（Budget escape across workspace = 0）。
- Cases A–G + 指标 10 用例（`tests/test_p4_multi_workspace.py`）：A 双库并行
  独立；B 跨库引用须登记 grant（伪造拒绝）、引用≠复制；C 跨库 mutation
  拒绝（read grant 也不行，write/mutate scope 分离）；D shared KG 允许但
  Evidence 不共享；E private KG 不可见（显式/自动解析双路径）；F backup
  不含他库数据；G workspace 池耗尽后 task/child 均被拦；凭据 scope 违例
  拦截；replay 零污染。
- 验收指标全锁死：Cross-workspace data leakage / Unauthorized evidence
  access / Unauthorized mutation / Replay contamination / Credential scope
  violation / Backup scope violation / Budget escape = 0。
- P4 明确不做：多租户 SaaS / 用户管理系统 / RBAC 全平台 / 网络隔离 / 云部署。
- Tests：482 → **492 passed / 2 skipped**（+10 P4 用例）

### P3 — Backup / Restore Automation

> P3 原则：**Backup 是 Scientific State Snapshot，不是文件复制。**恢复后的
> 系统必须是同一个科研系统——identity / sequence / lineage / replay 语义
> 全部一致。

- **BackupManifest**（`mra/backup.py`）：backup_id / created_at /
  source_workspace_id / **ledger_head_seq / ledger_hash / snapshot_hash** /
  schema_version / capability_registry_version / policy_version /
  graph_snapshot_id / replay_summary（恢复等价性的判定基线）/ file_inventory
  （逐文件 checksum）。
- **内容边界**：只备份 events.jsonl + manifest.json（结构上无凭据/缓存/
  runtime 可泄）；创建与恢复双侧对账本做凭据二次扫描（append 守卫外再一道，
  Secret leakage in backup = 0）；工作区目录中的临时文件不进入备份。
- **Full / Incremental**：full 为基础正确性目标；incremental 只备份
  seq > from_seq 的段（引用 base_backup_id，段-基准衔接校验：首 seq 须为
  from_seq+1），恢复 = base + 段拼接后整体校验。
- **Restore fail-closed 流水线**：load manifest → schema 兼容判定
  （compatible | **migration_required，禁止静默升级**；1.1→1.2 加性演进
  兼容表）→ 逐文件 checksum → ledger_hash → staging 拷贝 → staging 上
  **replay 摘要 + seq 严格连续性**验证 → 原子换入目标。任何一步失败 →
  staging 丢弃、既有目标零改动（含回滚路径）。
- **时间点语义**：恢复显式回到 manifest.ledger_head_seq；备份后的未来
  事件不混入；恢复后新事件从 head+1 顺延（Sequence collision = 0），
  历史事件零改写（Historical evidence mutation = 0）。
- Cases A–E + 增量链 + 内容边界/凭据 8 用例（`tests/test_p3_backup_restore.py`）：
  A 正常恢复逐事件等价（含 provenance）；B 篡改 ledger/manifest/checksum
  三路拒绝且目标零污染；C schema 判定（同版/加性旧版 compatible、未知版
  migration_required）；D 恢复后继续科研（新任务新 seq、历史前缀逐事件
  不变、lineage 完好）；E 时间点恢复（head=备份点、未来事件不混入）。
- 验收指标全锁死：Restore lineage/replay mismatch = 0；Corrupted backup
  accepted = 0；Secret leakage in backup = 0；Sequence collision = 0；
  Historical evidence mutation = 0。
- P3 明确不做：多节点复制 / RAFT / 分布式数据库 / cloud orchestration
  （single-host 可靠恢复）。
- Tests：474 → **482 passed / 2 skipped**（+8 P3 用例）

### P2 — External Knowledge A–E Contract

> P2 原则：**外部世界不可靠时系统守证据纪律。**目标不是加数据源数量，
> 而是验证外部知识源数量增加、可靠性下降、结果冲突时，科研纪律不破。

### P2 落地内容（Production Hardening Phase 2 · P2）

- **统一契约层**（`mra/knowledge/sources/contract.py`）：任何外部知识源接入
  前必须满足五件套——Provenance / Retrieved_at / Raw identity+hash /
  Failure taxonomy / Cache semantics。
- **失败分类法（集中权威）**：六态封闭 `success / empty / timeout /
  rate_limited / malformed / unavailable`；**valid empty=知识结果，
  unavailable=系统状态**，不得混淆；信封不变式结构性强制（失败态不得带
  证据、正当结果不得带 error、状态集封闭）。无 status_hint 的原始异常由
  `classify_exception` 按类型判定（TimeoutError→timeout、HTTP 429→
  rate_limited、JSONDecodeError→malformed、其余→unavailable）。
- **ExternalQueryResult 统一信封**：source_id / query / retrieved_at /
  raw_hash / evidence_items / provenance（source_version/evidence_origin/
  retrieval_path）/ from_cache / cache_fallback_after / error。
- **适配器接入**：EuropePMC 与 OmniPath 的错误对象携带 `status_hint`
  （向后兼容——既有 185 个 knowledge 测试零改动全绿）；litread（PubMed）
  的 A–E 已在 v1.1.0 前锁定。
- **CachedSource 缓存包装（五件套第 5 条）**：只缓存正当结果（success/
  empty）；失败态零入缓存；live 失败 + 缓存在场 → `from_cache=True` +
  `cache_fallback_after=<失败态>` + 原始 retrieved_at/raw_hash——**cache hit
  ≠ live retrieval success**，绝不伪装刚刚检索成功。
- **multi_query 多源编排**：逐源保留状态（silent masking = 0）；部分源
  失败不拖垮整体（`has_usable_evidence` 只看正当结果），也不静默丢弃失败源。
- **detect_conflicts 冲突检测**：同 key 跨源不同结论 → conflict state
  （双方证据全保留、完整 provenance、`resolution=manual_review_required`、
  `auto_resolution=disabled`）——不自动裁决、不平均、不隐藏（裁决权在人工）。

### 对抗 Cases A–E + 验收指标（`tests/knowledge/test_external_contract.py`，10 用例）

- A 部分源失败 ≠ 无证据（timeout ≠ empty 语义分离实测）
- B 缓存兜底明示（from_cache + 覆盖的失败态 + 原始时间/hash 保留）
- C 跨源冲突 → conflict state（不自动选择；无冲突不误报）
- D malformed 零进入 cache/provenance/evidence（恢复后无脏缓存）
- E rate limit 后恢复（失败零入缓存、lineage 完整、候选级不变）
- 指标：provenance completeness=100%、Timeout-as-negative-error=0、
  Malformed ingestion=0、Cache/live confusion=0、Silent masking=0、
  Unauthorized promotion=0（全部 candidate 级，升级只走 curated ingestion）。

- Tests：464 → **474 passed / 2 skipped**（+10 P2 用例）
- P2 明确不做：自动可信度排序、自动冲突消解、替代人工领域判断。

### P1 — Budget / Resource Metering

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
