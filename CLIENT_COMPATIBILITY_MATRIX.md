# CLIENT_COMPATIBILITY_MATRIX.md v1（2026-09-24）

**命题**：Scientific Harness ≠ DeepSeek-specific Agent。
**核心指标**（用户裁决）：同一 scientific capability 跨 implementation / runtime / client
保持稳定语义、治理、provenance 与 Evidence contract——不以能力数量为指标。

## 金丝雀跨客户端验证结果（v1 实测）

| capability_id | Client A：dsh headless（钉版 0.1.7-rc.1，MCP attach + llm-replay） | Client B：MCP Inspector（modelcontextprotocol 官方参考客户端，CLI 直调） | 语义等价 |
|---|---|---|---|
| method.query | METHOD_KNOWLEDGE / method-zero-variance-guard-001 | 同左 | **PASS** |
| knowledge.route | LOCAL_KG / tier_A / 10 邻居 / tiers=[A] | 同左 | **PASS** |
| gap.check | entity_gaps_only / 1/2 实体 / 0/1 方法 | 同左 | **PASS** |

**零修改清单（六项全守住）**：Scientific Core ✓｜Registry schema ✓｜capability_id ✓｜
Workspace/Evidence Schema ✓｜Knowledge Router / Method KB / Gap Detector ✓｜
仅 client adapter/transport 层变化 ✓。

## 客户端兼容矩阵（首版）

| client | transport | session support | MCP support | streaming | auth | workspace binding | 已知限制 | 状态 |
|---|---|---|---|---|---|---|---|---|
| ZCode（终端 Agent） | CLI/Python 直调 + MCP 可挂 | 本会话 | ✓（MCP 工具面） | — | 环境变量 | MRA_WORKSPACE_ROOT | 交互式驱动，非协议客户端 | ✅ 实测（全程开发即用它） |
| DeepSeek Harness (dsh) | ACP + MCP attach | ✓（durable session ledger v4） | ✓（mcp-client 桥） | ✓ | profile/credentials | session↔workspace 经 ID 互锚 | developer preview；模型需 key 或 llm-replay | ✅ 实测（金丝雀三连） |
| MCP Inspector | stdio MCP | ✗（单次调用） | ✓（参考实现） | ✗ | — | 调用侧自行锚定 | CLI 参数不吃 dash 开头（需 wrapper 脚本）；无会话概念 | ✅ 实测（Client B） |
| Claude Code | MCP attach | 客户端侧 | ✓ 预期 | 客户端侧 | 客户端侧 | 同 MCP 通道 | 未实测 | ⏳ 待验证 |
| OpenCode 1.18.31 | MCP(stdio)+ACP+bash 工具 | ✓ | ✓（mra 已挂载实测） | ✓ | auth.json | 经 chain 脚本锚定 | 全链实测通过（kimi-k3 真实自主规划） | ✅ **Agent-runtime Portability 已验证** |
| WorkBuddy | 待调研 | ? | ? | ? | ? | ? | 未调研 | ⏳ 待验证 |

## G8 Phase 2 — Agent-runtime Portability（2026-09-24 实测通过）

**第二独立 Agent runtime = OpenCode 1.18.31**（Full-Auto agent，kimi-k3 模型，
真实自主规划——非回放、非直调）。任务链全通：

```
ResearchTask → method.query(MCP) → gap.check(MCP) → association.partial_spearman(bash→Registry)
→ CandidateResult → GovernanceDecision → workspace.record_execution
→ workspace.record_evidence(账本六验) → replay
```

**dsh 路径 vs OpenCode 路径对比**（g8-opencode-slice：5 事件/1 决策/1 候选/1 证据）：

| 维度 | 稳定性判定 |
|---|---|
| capability_id / Registry schema / CandidateResult schema / Core | ✅ 两路径零修改 |
| tool inputs（method_query/gap_check 参数） | ✅ 语义一致 |
| governance checks（六查+账本六验） | ✅ 同 policy scientific-governance@1.1.0，decision 可验证 |
| Evidence lineage / replay | ✅ 完整可重建 |
| compute 绕过 governance | ✅ 两路径均无此通道 |

**差异分型**：
- 允许（Agent planning 差异）：模型不同（llm-replay fixture vs kimi-k3）、步骤编排顺序、
  自然语言报告措辞、是否附加 sensitivity 演示（OpenCode 路径 falsification=none——合法状态）；
- 不允许（scientific semantics 差异）：**观测为零**。

**结论：换独立 Agent runtime，Scientific Registry / Governance / Evidence 语义成立 →
具备进入 G2 Scientific Planning / Governance Plugin 的前提。**

## 结论与边界

1. **三能力×两客户端生态（dsh / modelcontextprotocol）语义等价全 PASS**——
   portability 命题在 v1 范围内成立；ZCode 作为开发客户端全程在用（CLI 直调）。
2. Claude Code / OpenCode / WorkBuddy 待逐项实测后填表；**不假设任何客户端
   天然支持同一协议**（用户原则）。
3. Client B 无 session 概念——其执行经调用侧锚定入 Scientific Workspace。
4. **Phase 1（Protocol/Client Portability）与 Phase 2（Agent-runtime
   Portability）明确分档**：Phase 1=同契约跨客户端可调用（Inspector 参考客户端）；
   Phase 2=独立 Agent runtime 真实自主规划下科研语义不变（OpenCode 实测）。
4. 升级门禁不变：upstream 升级 = dsh 侧金丝雀三连全过才进主分支。
