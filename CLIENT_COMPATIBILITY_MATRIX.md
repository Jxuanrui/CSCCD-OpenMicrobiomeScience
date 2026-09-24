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
| OpenCode | MCP/ACP | 客户端侧 | ✓ 预期 | 客户端侧 | 客户端侧 | 同上 | 未实测 | ⏳ 待验证 |
| WorkBuddy | 待调研 | ? | ? | ? | ? | ? | 未调研 | ⏳ 待验证 |

## 结论与边界

1. **三能力×两客户端生态（dsh / modelcontextprotocol）语义等价全 PASS**——
   portability 命题在 v1 范围内成立；ZCode 作为开发客户端全程在用（CLI 直调）。
2. Claude Code / OpenCode / WorkBuddy 待逐项实测后填表；**不假设任何客户端
   天然支持同一协议**（用户原则）。
3. Client B 无 session 概念——其执行经调用侧锚定入 Scientific Workspace
   （workspace 15 事件 / 7 任务 / 7 执行，含 client=mcp-inspector 记录）。
4. 升级门禁不变：upstream 升级 = dsh 侧金丝雀三连全过才进主分支。
