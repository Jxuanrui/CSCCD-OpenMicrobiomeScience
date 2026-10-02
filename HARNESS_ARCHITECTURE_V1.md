# Scientific Research Harness Architecture v1.0

**版本**：v1.0 ｜ **裁决**：用户 2026-09-24 ｜ **地位**：后续开发最高约束文档
（本文件与《知识架构审计报告》配套：审计报告描述现状，本文件定义约束与目标形态）

---

## 0. 定位宣言

本项目不是"一个生物信息 Agent"，而是 **Model-agnostic Scientific Research
Harness**：为通用基础模型提供**知识、工具、记忆、治理、证据追踪**，使其能够
可靠执行专业科研任务的 Harness。

**模型可以替换。** Harness 不绑定 GPT / Claude / Gemini / Qwen / DeepSeek /
MCP 兼容客户端 中的任何一个；任何支持
CLI / MCP / API 的 Agent 客户端都应能调用 Harness 能力。

**Harness 架构原则（最高约束）：**

1. Research Evidence 不进入 Local Knowledge（知识边界铁律，沿用）。
2. Harness 不依赖具体模型。
3. 所有能力通过标准接口暴露：CLI / API / MCP。
4. 当前研究课题（Project01）只是 Harness 的一个验证场景，不是 Harness 本身。
5. Food–Pathway–Phage 是 **End-to-end validation benchmark**，不是系统边界。

原有知识架构原则（四知识域分层、provenance、缓存≠入库等 15 条）全部保留，
归位为本架构 Layer 2 / Layer 4 的子原则。

---

## 1. 六层架构与当前实现映射

| 层 | 定义 | 当前实现 | 成熟度 |
|---|---|---|---|
| **L0 Model Interface** | 任意 MCP 兼容客户端 | 实测：MCP 兼容客户端（mcp_server，8 工具） | H3 |
| **L1 Harness Core** | context assembly / memory / planning / tool orchestration / state management | research/loop（planner_fn 注入、state_digest 紧凑摘要、meta_review、session 断点续跑、白名单派发）；记忆为会话级 | H3（缺跨会话 workspace 记忆） |
| **L2 Knowledge Layer** | Local / Live / Method Knowledge | KG 快照（curated A 18913 + llm B 88）+ KnowledgeStore（16 条，12 条方法规则）+ litread（EXTERNAL_LIVE 全 provenance）+ Router MVP + Gap Detector | **H4-min** |
| **L3 Tool Layer** | R / Python / bioinfo tools / MCP tools / external APIs | R 治理沙箱（rtools+gate）、atlas、litread、vecstore；kg/tools TOOL_SPECS（JSON Schema）先例；exec_registry（论文工具卡） | H3（无统一 tool registry） |
| **L4 Governance Layer** | validation / audit / provenance / evidence grading | 治理门（资源限制+审计账本+五审计规则）、四道闸、预注册判定实践、source_type 五分、curated ingestion 状态机 | **H4** |
| **L5 Research Workspace** | current study / findings / hypothesis / evidence | AgentLab（findings/evidence_matrix/canonical 纪律）+ 课题仓 registry + var/research | H3（无统一 evidence schema/workspace API） |

## 2. 六能力评估（证据定位）

| 能力 | 现状 | 证据 | 缺口 |
|---|---|---|---|
| **Model portability** | 接口级✅ | `run_session(planner_fn=...)` 与 planner 解耦、offline_plan 零 API 路径；ModelRuntime 适配器体系（ark/glm 双后端+预算护栏） | 仅 2 个 provider 适配器；缺一个 OpenAI 兼容通用适配器（可覆盖大多数模型生态） |
| **Interface abstraction** | 双通道✅ | CLI 全量入口（含 `python -m mra` 欢迎页自检）；MCP server 8 工具（本轮补齐 method_query/knowledge_route/gap_check） | 无 HTTP API（web/ 模块待核验定位） |
| **Tool registry** | 先例级△ | kg/tools.py TOOL_SPECS（纯函数+JSON Schema）；loop ACTION_NAMES 白名单；R 沙箱白名单脚本 | 三套工具面未统一注册：无跨 R/Python/Knowledge/MCP 的统一 registry 与 schema |
| **Context orchestration** | 会话级✅ | state_digest（防上下文膨胀）+ meta_review（确定性自评）+ session 断点 | 跨会话记忆/工作区未建（归 P1-2） |
| **Knowledge routing** | **H4**✅ | Router MVP（Local→miss→LITERATURE 降级，真实网络实测）+ Gap Detector（实体/方法双探测+建议动作）+ Method KB（16 条可检索） | entity-aware 检索模板（P1-3）；外部生物库通道（P2） |
| **Evidence isolation** | **H4**✅ | 结构性零回流（图谱只读）；candidate 状态机（approved 需人工）；source_type 五分契约测试；canonical/superseded 纪律 | Evidence 统一 schema（散在 tsv/md/db） |

## 3. Harness Maturity 模型（替代 Agent Level 定级）

| 级 | 定义 |
|---|---|
| H0 | 单体验证脚本 |
| H1 | 可复用工具集（CLI 可用） |
| H2 | 集成研究平台（数据契约+治理+统计+知识单后端） |
| H3 | 多客户端可接入（CLI+MCP 标准接口、模型可注入） |
| H4 | 知识驱动 + 证据隔离（四知识域接线、路由、缺口探测） |
| H5 | **Harness Research Loop**（任意客户端走完七步闭环） |
| H6 | 自主科学发现系统 |

**当前定级：H4-min。** 依据：L2/L4 层达 H4（路由/隔离/治理实测）；L0/L1/L3 在 H3
（MCP 面本轮补齐知识工具、模型注入接口在、统一 registry 缺）；H5 的闭环未成。
与此前 Agent 视角 L5-min 的关系：L5-min 证明"单客户端内具备知识驱动能力"；
Harness 视角要求这些能力**标准化到接口层供任意客户端复用**——这正是 H5 的内容。

## 4. P1-2 重定义：Harness Research Loop

P1-2 不再是"planner 升级"，而是把研究循环提升为**客户端无关的标准流程**：

```
提出问题（任意客户端）
  → Harness 检查知识缺口（gap_check）
  → 获取知识（Router：Local→Live→Method）
  → 调用工具（统一 tool registry）
  → 执行分析（治理门内）
  → 产生 Evidence（统一 schema + provenance + source_type）
  → 更新 Research Workspace（findings/hypothesis；不回写 Local KG）
```

**验收标准（预注册）**：
1. 任意 MCP / CLI 客户端可驱动全流程，全程不绑定特定模型；
2. 每步产物带 provenance 与 source_type 五分标记；
3. Workspace 更新与 Local KG 结构隔离（沿用铁律）；
4. 全程审计账本可回放。

**实现路径**：loop 阶段化状态机（design→gap→acquire→execute→evidence→workspace
各为显式可暂停阶段）→ MCP 暴露 loop 步 → evidence/workspace schema v1 →
统一 tool registry v1。

## 5. 迁移缺口（按优先级）

| # | 缺口 | 层 | 级别 |
|---|---|---|---|
| G1 | MCP 面补齐知识工具（method_query/knowledge_route/gap_check） | L0 | ✅ 本版本随附完成 |
| G2 | OpenAI 兼容通用 ModelRuntime 适配器（一个适配器覆盖多数模型生态） | L1 | P1 |
| G3 | 统一 tool registry v1（跨 R/Python/Knowledge/MCP，JSON Schema 描述） | L3 | P1 |
| G4 | Evidence/Workspace schema v1（Research Evidence 结构化落盘） | L5 | P1（与 P1-2 同期） |
| G5 | entity-aware 检索模板（五类） | L2 | P1-3 |
| G6 | HTTP API 通道（web/ 模块定位核验后决定） | L0 | P2 |
| G7 | 外部生物库通道（UniProt/Ensembl，BioTool schema 参考，SourceDescriptor 注册） | L2 | P2 |

## 6. 约束条款（最高约束清单）

1. 本文档五条 Harness 原则 + 15 条知识架构原则为全部后续开发的硬约束。
2. 任何新能力必须同时回答：暴露到哪个标准接口（CLI/MCP/API）？带什么
   provenance？经不经治理？
3. 任何模型相关代码只能存在于 Model Interface / planner_fn 注入点，
   不得渗入 Core/Knowledge/Tool/Governance 层。
4. 课题仓与工具仓边界、密钥环境变量纪律、Data 只读等运维铁律继续有效。
5. 本文档修订需用户裁决；修订历史在文末记录。

| 修订 | 日期 | 内容 |
|---|---|---|
| v1.0 | 2026-09-24 | 定位升级为 Model-agnostic Scientific Research Harness；六层架构/六能力评估/Harness Maturity（H4-min）/P1-2 重定义为 Harness Research Loop |

---

## 附录 A（v1.1 修订，2026-09-24 用户裁决）

**一、G 序调整为 G4 → G3 → G2**：G4 Evidence/Workspace Schema 第一阶段（状态载体
先行）；G3 Unified Tool Registry 第二阶段；G2 Model Adapter 待内核稳定后第三阶段。
原则：模型可替换，Harness 不变。

**二、G4 Schema v1 已实现**（`mra/workspace.py`）：五类记录 ResearchTask /
KnowledgeProvenance / ToolExecution / Evidence / WorkspaceState；append-only
JSONL 事件流 + replay 重建；Evidence.source_type 锁定 CURRENT_STUDY（隔离铁律
进 schema 层）；ToolExecution 以 governance_event_id 锚接审计账本。实测：本仓
Food–Pathway–Phage 战役 9 事件（4 证据覆盖全部四种证伪状态）已回填课题仓
AgentLab/food-pathway-phage/ 并回放验证。

**三、新增缺口 G8：Client Contract**——"MCP 可调用"不等于"客户端生态完成"。
需单独评估：session / auth / workspace / error schema / provenance return /
streaming（P2，H5 前置）。

**四、图谱侧同步内容升级**为《Harness Architecture v1.0 + KG Layer
Requirements》（KG_LAYER_REQUIREMENTS.md）：KG 是 Harness 的 Local Knowledge
Layer，不是研究结果仓库。

---

## 附录 B（v1.2 修订，2026-09-24 用户裁决）

总原则升级为 **DeepSeek-compatible / upstream-friendly Scientific Research
Harness**：通用 Harness 基础设施（模型适配/通用 dispatch/客户端协议）复用
upstream（deepseek-ai/deepseek-harness，MIT，经源码级审计 @46a7f68）；我方只维护
Scientific Research Core（知识层/科学治理/Evidence 双账本/能力语义）。禁止 fork
改 upstream core；Scientific Core 零 dsh import，耦合限制在 adapter 薄层。
G2/G3/G8 全部重定义（G3=Scientific Capability Registry；G8=Client Compatibility
& Research Extension Contract；G2=Scientific Planning/Governance Plugin）。
完整审计与 13 项决策见 DEEPSEEK_HARNESS_INTEGRATION_DECISION.md（G0.5）。

---

## 附录 C（v1.3，2026-09-24）：Scientific Compute / Evidence Governance 契约

**CandidateResult ≠ Evidence**（schema 强制）：计算能力（COMPUTE_ONLY）产出
CandidateResult（analysis_id/双版本/input_fingerprint/effect_estimate/uncertainty/
assumptions_checked/warnings/provenance/deterministic/tool_execution_id…16 字段，
`mra.workspace.CandidateResult`），语义="工具算出了什么"；经 **Scientific
Governance Gate**（`mra.governance.evaluate_candidate`，六查：provenance/input
lineage/method constraints/sensitivity/execution governance/阻断 warning +
canonical 资格）裁决 allow 后，才可经 `workspace.record_evidence`（无 allow
裁决即拒绝——治理不可绕过为结构性强制）进入 Scientific Ledger。

**Evidence 状态机**（append-only，历史不覆盖，现值由流重建）：Candidate →
Evidence → {canonical | sensitivity_passed | downgraded | refuted}。六铁律已入
测试（tests/test_evidence_governance.py）：refuted 不得静默恢复 canonical；
再升级须新治理事件；set_canonical 须 supporting_lineage；compute capability
禁直调 set_canonical（context 调用方标记拦截）；全 mutation 可 replay；现值
由事件流重建。

**首个 Golden Compute Slice 实测**（golden-compute-slice workspace，5 事件）：
method.query→gap.check→association.partial_spearman(真实治理门R：n=1060/30检验/
4显著+审计verdicts)→CandidateResult→gate(allow)→record_execution→
record_evidence→mark_downgraded(supersedes_seq=4)→replay 一致；无裁决提交被拦。

---

## 附录 D（v1.4，2026-09-24）：G2 Scientific Research Loop 契约

**一等对象**：ResearchTask（12字段契约，research_question 不可静默变更——变更须
显式 revision/child task，parent_task_id 指回）；ResearchPlan（版本化，
steps 只面向 capability_id，禁止绑定 implementation_id，plan_gate 拦截；
修订 append-only：v2 以 supersedes_plan_id 指回 v1）；LoopEvent
（stage 迁移/四门裁决/计划采纳/终止——可重放的研究过程）。

**七阶段固定状态机**（不依赖自然语言隐式表示进度）：gap_assessment →
knowledge_acquisition → method_constraint_resolution → planning →
governed_execution → evidence_evaluation → workspace_update。

**四门穿透**（plan → gate → execute → gate → evidence → gate → workspace）：
plan_gate（能力存在/无实现绑定/方法约束/停止条件）、execution_gate（能力可用/
任务约束 forbid:*/治理等级/EXTERNAL_WRITE 须 governed）、evidence_gate
（GovernanceDecision 裁决 + 账本六验）、state_mutation_gate（workspace.*
内建，set_canonical 须 canonical_eligible 裁决）。

**六种合法停止**（"不能继续做"是合法科研结果而非 Agent failure）：
task_completed / insufficient_data / unresolved_method_gap /
blocking_governance / no_valid_capability / evidence_insufficient。

**双 runtime 金环实测**（standalone + OpenCode kimi-k3 真实自主驱动同一引擎）：
两侧账本逐项一致——20 事件 / plans=1 / candidates=2 / decisions=1 / evidence=1 /
8 阶段序列相同 / 4 门裁决序列相同（plan:allow→execution:allow→evidence:allow→
execution:allow）/ terminal=task_completed / 同 policy@1.1.0。差异仅在
Agent 层（模型不同/措辞不同）——scientific loop 语义 runtime-independent 成立。

---

## 附录 E（v1.5，2026-09-24 用户裁决）：v1 核心架构冻结

**九项冻结**：Capability Registry / CandidateResult / GovernanceDecision / Evidence+state-transition invariants / ResearchTask / ResearchPlan / LoopEvent / ScientificLoop / Workspace。

**冻结规则**：
- 允许：向后兼容 optional 字段追加 / 不改语义的 metadata 扩展 / implementation 增加 / capability 新增。
- 必须升级版本：删除字段 / 改名 / 类型变化 / optional→required / 默认行为变化 / 状态语义变化 / governance invariant 变化 / replay 语义变化 / capability_id 语义变化。
- 禁止"只是加字段"绕过 contract versioning。

**各 contract 建立四件套**：schema_version / compatibility policy / migration policy / deprecation policy。

**v1 Stabilization Phase**（当前阶段）：
P0 = Regression Gate 落地（Tier 1 PR / Tier 2 nightly / Tier 3 release-gate）→
B1 Literature influence B→A → Release Readiness Review → 统一 v1 release。

**H6 暂不自动启动**；自主假设提出/跨会话记忆/多 Agent 协作为候选，待 v1 稳定后单独评估。

---

## 附录 F（v1.6，2026-09-24 用户裁决）：B4.1 身份域与幂等（isolation/recovery 前置底座）

**原则**：same scientific command + retry = same scientific state——恢复重试
（网络重试/进程恢复/ACK 丢失/caller 不确定）不得被误认为新的科研事件。

**身份域**：候选与裁决的正式 lookup 身份为 `(research_task_id, analysis_id)`
（两者新增加性可选字段 `research_task_id`）。task scope 内重复 analysis_id 由
`Workspace.append` 结构性硬拒（isolation invariant，系统保证而非调用方约定）；
跨 task 同名合法；无域裁决遇跨 task 同名候选拒绝歧义解析；legacy 无域条目
行为不变（向后兼容）。非确定性重算须版本化 analysis_id。

**幂等提交**：`workspace.record_evidence` 同 (evidence_id, 内容, decision)
重试 → `already_committed` + 既有事件引用，零新事件；只有新
GovernanceDecision / 新科学内容 / 新 rationale / 显式 revision intent 才
允许新增 append-only 事件。mutation（revise/downgrade/refute/canonical）
同状态+同理由重试 → `already_applied`。

**恢复语义（B3 实证）**：执行状态由账本派生（候选在场=compute 完成；
有效裁决在场=governance 完成；Evidence 在场=commit 完成），loop 四写点
（adopt/execute/evaluate/commit）幂等重驱动——不引入第二套 checkpoint /
workflow engine。

**冻结合规**：均为加性字段与行为收紧（幂等去重只消除冗余事件，不改变
合法事件的语义与顺序）；指标口径同步收紧——Cross-task 类指标一律加
"Unauthorized" 限定，合法跨任务推翻须显式 target/provenance/actor/
rationale/GovernanceDecision/supporting lineage。
