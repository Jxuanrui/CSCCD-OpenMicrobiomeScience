# DEEPSEEK_HARNESS_INTEGRATION_DECISION.md

**裁决背景**：用户 2026-09-24 最高优先级决策——暂停自行扩展通用 Harness 基础设施，
插入 G0.5 架构适配审计；总原则调整为 **DeepSeek-compatible / upstream-friendly
Scientific Research Harness**，禁止 fork 改 upstream core，只经公开 extension points
扩展；Scientific Research Core 保持领域独立。
**证据基线**：upstream 存在性已独立核验（官方公告+仓库）；本审计基于源码级事实——
浅克隆 `deepseek-ai/deepseek-harness` @ commit `46a7f68`（2026-09-23，v0.1.7-rc.1，
MIT，developer preview 含明确 breaking-changes 警告）。

---

## 1. DeepSeek Harness 架构能力地图（源码核实）

| 域 | upstream 能力（证据定位） |
|---|---|
| 插件体系 | Cordis 元框架，"一切皆插件"；`ctx.*` 服务注册表即 capability seams（docs/capability-seams.md） |
| 工具系统 | `ctx.tools` 注册 model-facing capability，schema 自动进 prompt 装配；`ToolDefinition extends ToolSchema`（packages/core/tools/src/index.ts:223）；扩展手册 cookbook/adding-a-tool |
| 会话日志 | durable 分代 JSONL（session.vN.jsonl.zstd，已提交分代永不改名/替换/删除）；**"Model-visible means logged" 运行时不变量**；`ctx.sessionProjections` 投影接缝；`SessionEventMap` 可扩展 durable 状态；session-persistence / session-query-sqlite |
| Agent 循环 | agent-loop + `agent/*`、`tools/*` 拦截事件（`agent/turn-stopping` 可停轮）；`agent.inject()` 注入下轮上下文；agent preset 按 session 组合能力集 |
| 模型适配 | `ctx.llm` 适配器注册表（llm-pi-ai / llm-deepseek / llm-replay 在列）；cookbook/adding-an-llm-adapter |
| 治理 | guard 包=**建议性循环卫生**（repeat-tool-reminder / timeout-policy）+ sandbox/confine + credentials——**非科学/统计治理** |
| 客户端 | ACP 包（JSON-RPC stdio：create/list/resume/close session、**挂载标准 MCP server**、权限应答、取消）；官方 **Python SDK**（deepseek-harness-sdk，NDJSON-RPC over stdio，profile 组合）；web/cli/desktop 三前端 |
| MCP | packages/mcp（client + resources），ACP 客户端可直接 attach 标准 MCP server |

## 2. G1–G8 逐项映射与 KEEP / ADAPT / REPLACE / DROP

| 项 | 原计划 | upstream 对应 | 决策 | 理由 |
|---|---|---|---|---|
| G1 MCP 面补齐 | 已完成 | ACP 可挂载标准 MCP server | **KEEP（已升值）** | 我们的 mcp_server 8 工具可被 dsh 会话直接 attach——零重写接入 upstream |
| G4 Workspace/Evidence | 自建（已 v1） | session log + projections + SessionEventMap | **KEEP + ADAPT（双账本）** | 见 §7：upstream=Runtime Ledger，我们=Scientific Ledger，ID lineage 互锚 |
| G3 统一 Tool Registry | 自建通用 dispatch | ctx.tools + ToolDefinition | **REPLACE（重定义）** | 不建第二套 dispatch；改为 Scientific Capability Registry + adapter 注册到 ctx.tools（§8） |
| G8 Client Contract | 自定完整协议 | ACP + Python SDK + web/cli | **REPLACE（重定义）** | 不发明协议；只定义科研扩展字段 + compatibility matrix（§9） |
| G2 Model Adapter | 自建 OpenAI 兼容适配器 | ctx.llm 适配器注册表 | **DROP 自建 / ADAPT** | 模型接入交 upstream；我们的 model_runtime 降级为 standalone 模式专用，停止扩展（§10） |
| 研究循环（P1-2） | 自建 loop 升级 | agent-loop + 拦截事件 | **ADAPT** | Harness Research Loop 实现为 Scientific Planning/Governance 层：通用（model turn/dispatch/streaming/cancel/session lifecycle）交 upstream，科研语义自持（§10） |
| 知识层全套 | Router/GapDetector/MethodKB/litread/KG | 无对应 | **KEEP（核心差异化）** | upstream 无任何科学知识层——这是我们的领域核心，不得弱化 |
| 治理门/审计账本 | gate/audit/四道闸 | guard=建议性 | **KEEP（核心差异化）** | upstream guard 不做统计审计；科学治理完全自持 |
| planner.py | 继续增强 | — | **KEEP 语义 / 冻结机制** | 系统 prompt=领域知识保留；ARK/GLM 重试兜底机制冻结不扩展 |

## 3. 不应继续扩展的现有代码（冻结清单）

- `model_runtime/`（ark/glm）：保留作 standalone 模式后端；不再新增 provider；
- `research/loop.py` 的**通用编排**部分（会话生命周期/迭代管理）：语义保留、功能冻结，
  H5 闭环改走 upstream agent-loop + 插件；
- `mcp_server.py`：保持现状即 attach 面，不加通用客户端功能；
- 欢迎页/CLI 通用基础设施：冻结（已够用）。

## 4. 改造成 DeepSeek plugin 的模块

优先级序：① **Scientific Capability Registry → ctx.tools adapter**（§8）；
② **研究循环治理插件**（监听 `tools/*` 事件，deny 时 `agent/turn-stopping` 级联）；
③ **gap_check/method_query/knowledge_route 已是 MCP 工具**——经 ACP attach 即可用，
  后续可平移为原生 ToolDefinition 获得更强 schema 装配。

## 5. Python / R / MCP 现有能力接入路径（零重写）

- **Python（mra 全栈）**：官方 Python SDK（NDJSON-RPC stdio）驱动 dsh runtime，
  mra 作为 capability 实现库被 adapter 调用；
- **R 治理沙箱**：r_association 保持 mra 内实现；对 dsh 暴露为 capability
  `association.partial_spearman`（governance_level=guarded）；
- **MCP**：`python -m mra.mcp_server` 经 ACP 直接挂载（最快通道，今天即可演示）。

## 6. Runtime Ledger 与 Scientific Ledger 权威边界（§7，对应用户问题 7）

| | Runtime Ledger（dsh session log） | Scientific Ledger（mra workspace） |
|---|---|---|
| 记录 | model interaction / tool call / tool result / runtime event / client-model trace | ResearchTask / Evidence / hypothesis / canonical result / falsification / knowledge lineage / governance verdict |
| 不变式 | Model-visible means logged | Evidence.source_type=CURRENT_STUDY 锁定；append-only+replay |
| 互锚 | session event id ↔ | ToolExecution.session_event_id / Evidence.lineage |
| 回放 | upstream session replay | mra Workspace.replay() |
**结论：双账本各自权威、ID lineage 互指、互不替代。**

## 7–10. G3 / G8 / G2 新设计

**G3 = Scientific Capability Registry**（mra 侧，纯 Python，零 dsh 依赖）：
字段按用户清单（capability_id / implementation_id / scientific IO schema /
provenance_contract / governance_level / side_effect / deterministic / auth_scope /
availability / version / validation_status）；adapter（独立薄层）把 capability 注册为
dsh ToolDefinition（ctx.tools）。**Planner 面 capability 不面 implementation**：
`literature.search` → {PubMed, EuropePMC, litread, MCP provider} 任一实现；
能力语义与治理归 Scientific Harness，通用 dispatch 归 upstream。

**G8 = Client Compatibility & Research Extension Contract**：复用 upstream
transport/session/protocol；我们只定义科研扩展字段（research_task_id / workspace_id /
evidence_id / knowledge_source / provenance / governance_verdict /
falsification_state / research_event）。**Compatibility matrix（首版假设待逐项验证，
不假设任何客户端天然支持同一协议）**：ZCode=MCP attach（已实测）/ACP 待验证；
Claude Code=MCP attach；OpenCode=MCP/ACP 待验证；WorkBuddy=待调研。

**G2 = Scientific Planning/Governance Plugin**：Harness Research Loop 七步
（task→gap→knowledge→method→plan→execution→evidence→workspace）以插件+capability
实现于 upstream agent-loop 之上；通用部分（model turn/tool dispatch/streaming/
cancel/session lifecycle）全部交 upstream。

## 11. upstream breaking-change 隔离策略

developer preview 明示将有破坏性变更（v0.1.7-rc.1）。隔离三原则：
① **Scientific Core 零 dsh import**——所有耦合限制在单一 adapter 薄层包内；
② adapter 对 upstream 版本**钉版 + 契约测试**（升级=只改 adapter 层）；
③ 每次 upstream 升级跑 **Golden Benchmark 回放**（Food–Pathway–Phage 双账本
重放结果必须 bit 级一致才算通过）。

## 12. DeepSeek 不可用时的降级边界

当前 standalone 模式**今天已完整可用**（CLI + 自有 loop + MCP attach 他客户端）。
dsh 不可用时损失=upstream 客户端生态/会话 UX/通用 dispatch；**保留=全部科研能力、
知识层、治理、双账本中的 Scientific Ledger**。降级即回到 H4-min 形态，无功能悬崖。

## 13. Cross-client compatibility 验收方案

1. 同一 capability（如 method_query）经三通道调用结果一致：dsh 内工具调用 /
   ACP 客户端 / 直接 MCP attach（ZCode）；
2. 双账本 lineage 完整性：每条 Evidence 可溯至 runtime session event，
   每次治理内工具调用在两账本均有记录且 governance verdict 一致；
3. provenance 穿透：source_type 五分在任一桥接路径不丢失不降格；
4. 隔离验证：任一通道产生的 research evidence 不得出现在任何 KG 写入路径
   （契约测试持续护航）；
5. Golden Benchmark：Food–Pathway–Phage 任务在新客户端下可重放且结论不变。

---

## 执行裁决（待用户确认后生效）

- 立即冻结：§3 清单；
- 下一步实施序：**G3 Scientific Capability Registry（含 dsh adapter 薄层）→
  ACP attach 实测（把 mra MCP 面挂上 dsh 跑通一次）→ G8 matrix 逐客户端验证 →
  G2 插件化**；
- 本文档为 G0.5 审计结论，纳入 HARNESS_ARCHITECTURE 最高约束体系（v1.2 附录引用）。

---

## 附录二（2026-09-24 用户裁决记录）

**一、S2/G3 边界确认**：Registry 只负责 capability definition / implementation
mapping / IO contract / provenance contract / governance / side-effect /
availability & version；不实现 dispatch、session runtime、streaming、通用重试、
通用客户端协议、通用 agent loop。Planner 只面向 capability_id。

**二、三条 Golden Capabilities 全部切片通过**（Upstream Compatibility Canary）：
- method.query（METHOD_KNOWLEDGE 基准，S1 切片+复跑）；
- knowledge.route（LOCAL_KG 命中 tier_A/10 邻居/provenance 无损；miss→受控
  LITERATURE 降级契约在 Registry 映射）；
- gap.check（真缺口检出；只建议不动手；不写 KG 不改 Evidence）。
**金丝雀规则：每次 upstream 升级三条必须全部重跑通过，任一失败不得进主分支。**

**三、Python SDK 非强依赖**：PyPI SDK（最高 0.1.5rc1）与钉版 runtime
0.1.7-rc.1 不对齐；正式通道=CLI/ACP/MCP；SDK 待版本契约对齐后再升级为
supported adapter。不为 SDK 完整性放松 commit pinning。

**四、fixture 资产固化**：upstream 模板 vendor 入仓（MIT 署名）+
Contract CI 层（每次必跑，无 dsh 依赖）+ Upstream Integration Slice
（Node+opt-in 自动；否则 manual/nightly/release-gate；不得删除复现资产）。
