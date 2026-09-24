# Production-readiness Review — Scientific Research Harness v1.1.0 candidate

评审日期：2026-09-24（用户裁决启动）｜评审对象：`dev/zcode-mra` @ B4.1+B3 之后
基线：**447 passed / 2 skipped**（含 B1–B5、B4.1、production hardening 全部用例）

评审问题：**当前 Harness 能否安全、稳定、可控地进入真实持续使用？**

判级口径（每项独立判级，不给总体 PASS）：
- **READY** — 已具备进入真实使用的证据
- **READY WITH GUARDRAIL** — 可用，但必须保留列明的限制条件
- **NOT READY / BLOCKER** — 未解决前不能开放的路径
- **NOT APPLICABLE** — 当前形态下不适用（触发条件列明）

版本口径：v1.0.0 为已发布历史基线；本评审针对 **v1.1.0 candidate**
（B4.1 行为变化：同 decision+同内容重复提交 → `already_committed` 零新事件）。

---

## 1. Reliability

| 项 | 判级 | 证据 |
|---|---|---|
| Process crash / restart / resume | READY | B3 Case A–F（`test_h5_long_horizon_recovery.py`）：五类中断窗口 + 多步中途恢复，Duplicate/Illegal/Replay 指标全零 |
| Duplicate request（retry≠event） | READY | B4.1（`test_b41_identity_idempotency.py` 8 用例）：幂等 commit、mutation 同义去重、loop 四写点幂等重驱动 |
| Concurrent task isolation | READY | B4（3 Case + 6 负路径）+ B4.1 task-scoped 身份域与歧义解析拒绝 |
| Partial commit（Workspace 路径） | READY | B3 Case D（ACK 丢失重试零新事件）+ B4.1 幂等键（Evidence 模型归一化比较） |
| Corrupted ledger | **READY WITH GUARDRAIL** | 负路径已测（半行/篡改/尾部丢失：读与**写**均 fail-closed，`test_production_readiness.py::test_corrupt_ledger_append_also_fails_closed`）。**Guardrail（政策，已定）**：损坏账本的恢复=人工检视+备份还原，禁止任何自动"修复"或猜测恢复成功；运维须保持账本目录的例行备份 |
| Disk full / write failure | READY | 故障注入用例（目录+文件只读）：append 响亮失败、账本零污染（`test_disk_write_failure_fails_loud_ledger_intact`） |
| Abrupt kill during append | READY WITH GUARDRAIL | append 为单行单 write，kill 落下最多产生半行 → 下次读/写 fail-closed（有测试）。**Guardrail**：未调用 fsync——进程崩溃安全（每 append 即 close），**OS 级崩溃/掉电**可能丢失缓冲尾部，表现为损坏并被 fail-closed 检出；如需更强保证，v1.1.0 可加 fsync（性能权衡另议） |
| Lineage integrity | READY | supersedes/revision 链全 append-only；B3 Case A 断言 lineage 不丢；六验负路径 5 条 |
| Release baseline | READY | v1.0.0 双远端（GitHub origin + internal-archive）已发布；tag 在场 |

**小结**：Workspace 内部可靠性 READY。两条 Guardrail（损坏恢复政策、fsync）记入
使用约束，不阻塞单机单项目使用。

## 2. External Knowledge Instability

判级：**READY WITH GUARDRAIL**（litread/PubMed 路径 READY，全路径未穷尽）

| 场景 | 状态 | 证据（`test_production_readiness.py`，零真实网络故障注入） |
|---|---|---|
| A. API timeout | READY | 抛出 TimeoutError（≠no evidence），失败结果不落缓存 |
| B. Rate limit / unavailable | READY | 异常（unavailable）与空命中（negative result）结构性分离 |
| C. Malformed response | READY | JSONDecodeError 抛出；不入 provenance、不落缓存 |
| D. Source changed | READY | provenance 携带 retrieved_at + raw_response_sha256 + query（可审计"同查询不同时间"） |
| E. Cached ≠ live | READY | from_cache=True 明示；保留原始 retrieved_at/raw identity；命中零网络（注入断言证明） |

**Guardrail**：以上锁定的是 `litread`（NCBI E-utilities）路径；`knowledge/sources/`
的 EuropePMC/OmniPath 适配器遵循"只产 candidate evidence、永不写 store"的协议
约束，但未做同等故障注入套件。接入新外部源时必须先补 A–E 五件套再放开。

## 3. Credential & Permission Hardening

判级：**READY WITH GUARDRAIL**（账本层 READY；OS 级/协议级加固未建）

已满足（1–3 结构性、有测试；4–6 政策性、已遵守）：
1. ✅ credential 不进入 CandidateResult/Evidence/任何账本记录——`Workspace.append`
   递归扫描疑似凭据键名硬拒（3 负路径：候选 provenance/Evidence 深层嵌套/误伤对照）；
2. ✅ 同上（账本是唯一持久层，单一守卫点全覆盖）；
3. ✅ 同上；
4. ✅ model-visible context 不注入 secret（litread `max_calls=0` 路径惰性构造
   runtime，不触碰 LLM 凭据；planner 注入的是数据契约白名单，非凭据）；
5. ✅ secret 从环境/`.env` 获取，`.env` 已 gitignore（根 `.gitignore:12`）；
6. ⚠️ 日志 redact：当前无统一日志框架，输出面小（CLI JSON），记为 Guardrail。

**Guardrail / 未建项**：
- capability `auth_scope` 字段未建（Registry 16 字段无凭据维度）；
- EXTERNAL_WRITE 最小权限无 OS 级沙箱承载（无 systemd/Apptainer 约束接入该面）；
- 上两项为**多用户/不可信代码场景的 BLOCKER**，单信任域使用不阻塞。

## 4. Human Review Boundary

判级：**READY WITH GUARDRAIL**（分类已定义并有结构性对应；审批界面/工作流未建）

| 层级 | 动作 | 结构性对应 |
|---|---|---|
| Fully automatic | READ_ONLY 查询（kg/vec/literature/method/gap）；COMPUTE_ONLY 候选产出 | 侧效分类+execution_gate |
| Governed automatic | record_evidence；revise/downgrade/refute；set_canonical（须 canonical_eligible 裁决） | WORKSPACE_WRITE guarded/governed + 账本六验 |
| Human approval required | ① 破坏性外部写入（首个 EXTERNAL_WRITE 能力落地时）；② publication/export；③ 不可逆数据集变更；④ high-impact canonical 结论（跨任务推翻性）；⑤ 凭据/权限升级；⑥ 覆盖外部维护的研究资产 | ①当前无该类能力（见第 9 节）；②–⑥ 为**政策边界**，由人类操作者执行（Harness 是单操作者科研工具，非无人系统） |

**Guardrail**：人审边界是操作纪律而非代码强制（无审批 UI/队列）。原则已定：
只放在不可逆/高风险边界，不做全步骤确认（避免 Human-in-the-loop 退化为
橡皮图章）。

## 5. Cost / Resource Budget

判级：**READY WITH GUARDRAIL**（现有闸门可用；全量核算未建）

已有：LLM 日预算闸（`budget.py`，`LLM_DAILY_CALL_CAP`，`test_budget.py`）；
会话级上限（ResearchSession.llm_call_cap，跨天延续有测试）；litread max_calls
硬顶。

**未建（记为 v1.1.0 candidate 工作项，不破坏冻结契约）**：
- 计量维度：tokens / wall time / tool calls / external API calls / compute
  time / retry count / cache hit-miss（当前只计 LLM 调用次数）；
- ResearchTask 预算约束（max_model_calls / max_external_queries /
  max_compute_time / max_cost / max_plan_revisions）——按用户裁决先做
  **runtime/governance metadata**，不动 frozen contract；
- `resource_budget_exhausted` 合法停止终态（新终态=契约行为变化，归 v1.1.0）。

**Guardrail**：真实持续使用期间人工监控日预算账本；长任务前显式设
max_calls。

## 6. EXTERNAL_WRITE Recovery

判级：**NOT APPLICABLE（今日）/ BLOCKER（启用首个 EXTERNAL_WRITE 能力时）**

事实：默认注册表 17 能力中**无任何 EXTERNAL_WRITE**（schema 支持、
execution_gate 有 governed 约束，但零实现）。B3 的恢复证明边界因此明确为
"Scientific Workspace 内部状态"（见 CHANGELOG/H5 边界声明）。

启用前必须落地（政策已定，实现未做）：
- 三分类恢复策略：idempotent external API（幂等键重试）／queryable external
  state（verify-before-retry）／irreversible-unknown（**默认不自动重试** →
  `recovery_requires_review`）；
- Workspace 的 exactly-once 经验**不得**外推到外部系统；
- 首个 EXTERNAL_WRITE 能力落地时须附 A–E 级故障注入 + 三分类恢复测试。

## 7. Benchmark Suite（小型，扩大 failure surface 而非数量）

判级：**READY**

| Case | 场景 | 承载 |
|---|---|---|
| 001 | Food–Pathway–Phage Golden | `tests/e2e/` golden 基准（双 runtime 金环，35 事件） |
| 002 | Literature-driven decision | `test_h5_literature_influence.py`（B1） |
| 003 | Concurrent conflicting tasks | `test_h5_concurrent_isolation.py`（B4） |
| 004 | Interrupted/resumed long plan | `test_h5_long_horizon_recovery.py` Case E（B3） |
| 005 | No-valid-conclusion | `test_production_readiness.py::test_case005_honest_stop_no_forced_conclusion`（证据不足→诚实终止，零强产证据） |
| 006 | Multi-omics heterogeneous compute | `test_h5_multi_omics.py`（B5） |

## 8. Multi-center / Multi-workspace Boundary

判级：**NOT READY（按需 FUTURE SCALE WORK）——当前系统明确为单项目信任域**

单信任域边界（事实清单）：
- workspace/study_id 为**扁平命名空间**，无 project/tenant 层；
- task ID 命名无租户维度（task-scoped identity 在 workspace 内唯一即可）；
- 凭据为**进程级环境变量**——同进程内所有 workspace 共享同一凭据边界；
- KG 快照与图谱检索对进程内全部任务**全局可见**（无 per-project KG 可见性）；
- Evidence 可见性=workspace 目录可见性（文件系统权限即边界）；
- 跨项目 provenance/export 权限未设计。

多项目/多机构部署前需：workspace 命名空间分层、per-project 凭据注入、
KG/Evidence 可见性矩阵、export 审批——全部列为 FUTURE SCALE WORK。

## 9. Observability

判级：**READY**（最小版；查询/报告层已落地）

- `Workspace.lineage(research_task_id)`（本轮新增）：Task→Plan→候选→裁决→
  证据（含 mutation）→terminal 全链只读查询，版本随行
  （capability/implementation/model/client/policy_version）——直接回答
  "这个科研结论是怎么来的"（测试锁定含版本断言与跨任务不串线）；
- replay（hash/语义双层）+ audit 账本（research.db）+ LoopEvent 全程可重放；
- 未建 dashboard（不必要——单操作者场景 CLI/API 查询层足够）。

**已知缺口（Guardrail 级）**：dataset version / graph snapshot id 未作为一等
字段随 Evidence 携带（graph snapshot 版本在 MCP 层管理）——跨快照结论追溯
需人工对齐快照清单；v1.1.0 候选可加 `graph_snapshot_id` 加性字段。

---

## 最终输出（四部）

### READY NOW（可进入真实使用）
- 受治理科研循环全链：知识驱动规划→四门受治理执行→账本可验证证据→
  append-only 状态演化→replay 复现（B1–B5 + Golden 双 runtime 基准）
- 单操作者、单项目、单机场景下的持续研究使用：
  崩溃恢复 / 断点续跑 / 重试幂等 / 并发任务隔离 / 诚实停止
- 外部文献知识（PubMed/litread）在生产不稳态下的行为（A–E 已锁）
- 账本凭据纪律（结构性硬拒）与血缘查询

### READY WITH GUARDRAILS（可用，保留限制条件）
- 损坏账本恢复：人工检视+备份还原（fail-closed 政策，禁自动修复）；
  **须保持账本例行备份**
- OS 级崩溃可能丢账本缓冲尾部（未 fsync）——表现为损坏并被检出，不静默
- 外部知识 A–E 仅 litread 路径锁定；**新外部源接入须先补五件套**
- 人审边界为操作纪律（无审批 UI）；凭据 auth_scope/OS 沙箱未建——
  **仅限单信任域**
- 成本治理只有 LLM 调用计数闸；tokens/时长/重试/cache 计量未建，
  长任务须人工监控

### BLOCKERS（未解决前不能开放）
- **任何 EXTERNAL_WRITE 能力的启用**（三分类恢复策略+故障注入未实现；
  当前零注册，故今日无阻塞路径）
- 多用户/不可信代码场景（凭据隔离与 OS 级沙箱缺失）

### FUTURE SCALE WORK
- multi-center / 多租户（命名空间分层、per-project 凭据、可见性矩阵）
- distributed execution；更大 benchmark suite
- H6：自主假设提出 / 跨会话记忆 / 多 Agent 协作（v1.1.0 后单独评估）

---

## v1.1.0 Release Gate（评审通过后再决定）

- [x] 447+ existing tests 全绿（447 passed / 2 skipped @ 9724d45+本轮）
- [x] production negative tests 全绿（11/11：secret×3 / 磁盘 / 损坏 / 外部 A–E×4 / 血缘 / Case005）
- [x] Golden regression 全绿（e2e golden 在套件内）
- [x] B3 recovery regression 全绿（13/13）
- [x] B4 concurrency regression 全绿（9/9 + B4.1 8/8）
- [x] secret leakage = 0（结构性拦截+3 负路径）
- [x] unauthorized mutation = 0（B4/B4.1 负路径）
- [x] duplicate semantic commit = 0（幂等用例）
- [x] ledger corruption fail-closed（读写双向，有测试）
- [ ] **no critical blocker**：今日无阻塞路径成立（EXTERNAL_WRITE 零注册、
      单信任域使用），✅ 在既定 Guardrails 内成立
- [ ] CHANGELOG 完整（本轮已补 v1.1.0 candidate 段；release 时定稿）
- [ ] migration / compatibility note（v1.0.0→v1.1.0：加性字段
      research_task_id×2、信封加性键 already_committed/already_applied、
      行为变化=同 decision 同内容重复提交不再产生冗余 revision——
      release 时随 tag 出正式 note）

**评审结论**：在"单操作者、单项目、单机、无 EXTERNAL_WRITE"既定边界内，
Harness 具备进入真实持续使用的条件；超出该边界的每一项扩展都有明确的
BLOCKER/Gate 指引。
