# Reuse Review 2026-09-10：OPA 权限层

> 第三份 Reuse Review，六段式模板。
> 执行分工：Codex 检索评估（含用本机 OPA 1.20.2 二进制现场验证 bundle/manifest/eval/build 行为），Kimi K3 共同审核；结论一致。

## 1. 功能问题与检索记录

- **问题**：为 MVP（PLANNING.md 第 9 章 Q10/Q13）建立 OPA 权限层——OPA sidecar、4 逻辑角色、约 6 动作、默认拒绝、职责分离（SoD），支撑 PEP 统一拦截点的权限决策。
- **检索**：2026-09-10，固定范围（OPA 官方 bundle/decision logs/policy testing/REST API 文档；open-policy-agent/example-api-authz；GA4GH DUO）。**沙箱网络不可达**，官方文档 URL 与社区仓库许可/维护状态标"待联网复核"；但 Codex 用本机 OPA 1.20.2 二进制**现场验证**了以下事实（非文档推断）：bundle 为 gzip tar；`.manifest` 支持 `revision/roots/rego_version`；`.signatures.json` 用文件 SHA-256 + JWT、默认 RS256；`opa test` 支持 schema、覆盖率阈值、空测试失败；`opa build` 打包产物含 `/data.json` 与 `/.manifest`。

## 2. 架构对照

- OPA 官方 RBAC 经典模式（"用户→角色、角色→权限"两级数据表）可直接复用；本平台在此基础上叠加 ABAC（项目、资源类型、数据分类、结果状态、分析计划、网络、凭据、审批收据）。
- `open-policy-agent/example-api-authz`：仅作模式参考，许可证/维护状态待核实 → **不复制源码**。
- GA4GH DUO 描述数据使用限制，不等同授权实现；未核到权威 DUO→Rego 包；MVP 不引入，未来可把 DUO term ID 作为资源/研究目的属性。

## 3. 许可证

- OPA 本体 Apache-2.0，可直接部署与封装，无再分发障碍。
- 社区示例与 DUO 的具体许可证/NOTICE 待联网复核，本轮仅采用通用建模模式，不复制源码。
- 自编 Rego 不引入额外运行依赖。

## 4. 最终决策（待用户确认 Rego schema 后落定；本记录为技术选型结论）

- **直接采用** OPA 官方工具链：sidecar、bundle + 签名、REST API、decision logs、`opa test`/`opa check`。
- **适配**：数据驱动 RBAC（两级表）+ 最小自研本平台特有的五轴 ABAC、职责分离规则、稳定决策输出。
- **后置**：DUO、GraphRAG 式资源关系。
- 决策入口固定为 `POST /v1/data/mra/authz/decision`，返回稳定对象 `allow / approval_required / reason_codes / constraints / policy_revision`。

## 5. 借鉴清单（Codex 提出，经 Kimi K3 与 5.3 对齐）

- **角色授权基线**（与 PLANNING.md 5.3 一致）：PI 可读获批投影、执行预注册任务、晋升结果、审批他人导出；数据管理员可读/导出（无权批准科学结论）；统计审核员只读最小审计投影、审批结果晋升；平台管理员管策略/镜像/配额/联网、**恒不得读研究数据**。
- **动作→权限轴映射**：`read_projection`→数据轴；`execute_task`→执行+网络凭据轴；`promote_result`→研究决策+结论轴；`export_data`→数据+发布轴；`approve_request`→审批（SoD 元动作）；`manage_policy`→跨轴控制面。
- **回归门**：`opa check --strict --schema` + `opa test --schema --fail-on-empty --coverage --threshold 100`，覆盖完整允许矩阵 + 越权/缺字段/自批/错哈希案例。

## 6. 风险与核实记录（重点，PEP 实现必读）

- **输入来源**：PEP 必须从受信任身份表 + SQLite 审批状态构造 input，**绝不接受 Agent 自报角色或审批**（角色由 bundle 映射解析，不放可伪造的 principal 输入）。
- **故障即拒绝**：OPA 超时、非 2xx、缺 `result`、输出不合 schema，一律拒绝。
- **职责分离**：比较稳定用户 ID（不比较角色）；审批收据绑定不可变请求哈希 + 策略版本。
- **数据最小化**：input 不含样本值/密钥，只含逻辑 ID、分类、哈希、opaque credential ref；decision log 脱敏，与 SQLite 审计账本用 `decision_id` 关联；审计落账失败须在副作用前阻断。
- **部署边界**：OPA 仅监听 loopback/Unix socket；Agent 不得访问 policy/data 写接口；生产 bundle 固定验签公钥，禁止 `--skip-verify`。
- Codex 报告中"网络不可达"的官方文档结论，已由其用本机二进制实测交叉印证（bundle/manifest/签名/测试工具链四项）；外部社区示例许可仍未核实，故不复制源码。
