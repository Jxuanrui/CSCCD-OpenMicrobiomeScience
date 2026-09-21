# Reuse Review 2026-09-10：ModelRuntime 底层适配库选型

> 第二份 Reuse Review，六段式模板（见 2026-09-10-wisp-science.md）。
> 执行分工：Codex 检索与评估（PyPI 在其沙箱不可达，事实表标"待核实"），Kimi K3 补核 PyPI 一手数据并共同审核，结论双方一致。

## 1. 功能问题与检索记录

- **问题**：为 ModelRuntime 最小接口（PLANNING.md 5.7 + 第 9 章 Q10 MVP 范围）选择底层 Python 适配库：支撑"ARK（火山方舟，OpenAI 兼容 /chat/completions）对话 + 工具调用 + Pydantic 结构化输出 + usage/cost 记录 + 重试/fallback 事件记录"。
- **检索**：2026-09-10，固定候选清单（openai、langchain-core、langchain-openai、litellm、pydantic、instructor、pydantic-ai），不扩散。Codex 读 PLANNING.md 后逐库评估；PyPI JSON API 由 Kimi K3 补核（见第 2 节）。

## 2. 候选核实表（PyPI JSON API，2026-09-10 一手）

| 包 | 最新版 | 许可证 | Python 要求 | 最近发布 | 活跃度 |
|---|---|---|---|---|---|
| openai | 3.11.0 | Apache-2.0 | ≥3.10 | 2026-09-09 | 高 |
| pydantic | 2.13.5 | MIT | ≥3.9 | 2026-08-28 | 高 |
| langchain-core | 1.6.2 | MIT | ≥3.10 | 2026-09-04 | 高 |
| langchain-openai | 1.6.2 | MIT | ≥3.10 | 2026-09-09 | 高 |
| litellm | 1.100.1 | MIT（核心；enterprise/ 目录例外） | ≥3.10 | 2026-09-10 | 高 |
| instructor | 1.17.0 | MIT | ≥3.9 | 2026-09-09 | 高 |
| pydantic-ai | 2.42.0 | MIT | ≥3.10 | 2026-09-09 | 高 |

全部宽松许可证、活跃维护、兼容本项目 requires-python ≥3.10。

## 3. 评估（能力/用量/失败处理）

- **能力声明**：OpenAI `/models` 不提供可靠 tool/schema 能力矩阵；LangChain profile、LiteLLM `supports_*`、PydanticAI `ModelProfile` 均为静态声明表且 ARK 型号未必在表内 → **平台必须自维护按 provider/model/version 版本化的权威能力表**（`policies/model_capabilities.yaml`，Git 版本化），以回归测试校准。
- **usage/cost**：各家口径不一，ARK 可能缺缓存/推理 token 细项 → 审计存**原始值 + 标准化值 + 缺失标志**；金额按**平台价格快照**（`policies/model_pricing.yaml`）自算，不依赖第三方实时价格表。
- **重试/fallback**：底层库隐式重试与平台层叠加会造成重复调用、成本失真、事件缺口 → **关闭底层隐式重试，ModelRuntime 逐次记录 AttemptRecord**；自动 fallback 仅限 TIMEOUT/RATE_LIMIT/TRANSIENT（5.7 基线），备用模型须满足同一能力子集。
- **结构化输出**：ARK 的 OpenAI 兼容可能只覆盖部分参数 → 基线走 **tool calling + Pydantic 校验**，校验失败记为模型错误（FATAL，不 fallback），计入回归门指标。
- **pydantic-ai 不引入**：其 Agent/模型/fallback 抽象与既定 LangGraph + 自有 ModelRuntime 重叠。

## 4. 最终决策（2026-09-10 用户确认，PLANNING.md 第 9 章 Q12）

- **MVP 安装**：`openai==3.11.0` + `pydantic==2.13.5`（uv 锁定）。
- **v1 再加**：`langchain-core` + `langchain-openai`（5.7 首选适配）；optional extras：`router=[litellm]`、`structured=[instructor]`。
- **MVP 级简化（用户知情确认）**：MVP 用 openai SDK 直连、暂不装 LangChain（单一 OpenAI 兼容端点下无增量价值，v1 恢复首选适配）；5.7 接口定义与治理规则不受影响。
- 接口契约（ModelRef/Capability/能力表/ModelRequest/ModelResponse/AttemptRecord/行为规则 5 条）如用户确认的草案 v0.1，落于 `src/mra/model_runtime/`。

## 5. 借鉴清单

- LiteLLM 的标准化 usage 思路 → Usage 类"原始+标准化+缺失标志"设计参照。
- LangChain `with_fallbacks` / PydanticAI `FallbackModel` 的回调点 → FallbackRuntime 事件记录设计参照（v1）。
- instructor 的校验重试模式 → v1 structured extra 的备选。

## 6. 风险与核实记录

- ARK OpenAI 兼容参数覆盖度未全量实测：首个真实运行前必须跑 ARK 集成回归（工具调用/schema/usage 字段），并回填 `model_capabilities.yaml` 初始条目（当前为待校准声明）。
- 模型能力与价格会变化：`model_pricing.yaml` 首版占位（UNVERIFIED），真实计费前需以 arkcli pricing 或官方价格页回填并记快照日期。
- Codex 沙箱内 PyPI 不可达，其报告中版本/活跃度判断已全部由 Kimi K3 以 PyPI 一手数据替换，未采用其"待核实"标注之外的任何未验证声明。
