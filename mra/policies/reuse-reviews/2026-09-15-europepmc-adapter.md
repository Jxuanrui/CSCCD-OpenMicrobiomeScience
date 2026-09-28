# Reuse Review 2026-09-15：Europe PMC Adapter

> 第六份 Reuse Review。执行：主 Agent（deepseek-v4-pro / Volcano Engine）复核；Gemini 渠道本轮因 `BALANCE_INSUFFICIENT`（HTTP 402）不可用，经用户授权降级（见文末"降级记录"）。

## 1. 功能问题与检索记录

- **问题**：实现 Europe PMC Source Adapter，把文献检索结果映射为 `CandidateEvidence`，用于验证"外部来源 → 候选证据收件箱"最小闭环。
- **检索**：2026-09-15。**仓库内既有资产优先**（Ponytail 第 2 级）——`paper-lookup` skill 的 `references/europepmc.md`（2026-07-27 实测，含端点/参数/响应/坑）；未联网。

## 2. 候选核实表

| 候选 | 结论 |
|---|---|
| `paper-lookup/references/europepmc.md`（仓库内） | **直接复用其 API 知识**：端点、`resultType=core`、`pageSize≤1000`、`cursorMark` 分页 |
| Python 第三方 Europe PMC 客户端（pyeuropepmc 等） | 不采用。Adapter 仅做薄映射，**Python 标准库 `urllib` 已足够**（Ponytail 第 3 级）；且避免新增依赖与许可证核验成本 |
| `httpx`（传递依赖） | 不采用，未在 pyproject 声明为直接依赖，避免隐式依赖 |

## 3. 评估（复用 key 知识：三个坑）

`references/europepmc.md` 明确记载、Adapter 必须处理：

1. **错误藏在 HTTP 200**：`pageSize` 越界等返回 `{"errCode":..,"errMsg":..}`，无 `resultList` —— 不能只看 HTTP 状态。
2. **`"Y"/"N"` 是字符串**，不是 JSON 布尔；truthiness 会把 `"N"` 判真。
3. **`pmcid` 缺失而非 null**（不在 PMC 时键可能不存在）；`id` 单值不唯一，须用 `{source}/{id}` 对。

## 4. 最终决策

- 标准库直连 REST，薄 Adapter，无新依赖。
- `describe()` 返回 `status="candidate"`、`human_review="pending"`、`default_use="candidate"`——**不自称 verified**；如需 verified 须走人工批准 + `update_source_status`。
- `search()` 纯函数返回 `CandidateEvidence` 列表，**不写数据库**；写入由调用方经 `save_candidate_evidence` 完成，证据等级由存储层"只降不升"规则裁决。
- `evidence_id` 采用确定性 `europe-pmc:{source}/{id}`，重复检索同一记录会被存储层幂等拒绝。
- `fetcher` 可注入 → 离线 fixture 测试，**CI 不联网**。

## 5. 借鉴清单

- 游标分页与端点知识（来自仓库内 paper-lookup 资产）。
- 逐条记录哈希（`raw_response_hash`）以支持回链核验。

## 6. 风险与核实记录

- 本 Adapter 只取 `resultType=core` 的元数据/摘要，**不代表文献结论正确**；`license_status` 置 `pending`，未核 Europe PMC 逐篇许可。
- **降级记录**：Gemini CLI 于 2026-09-15 返回 `BALANCE_INSUFFICIENT`，网络调研降级为"Kimi K3/DeepSeek 检索 + 主 Agent 复核官方来源"（用户已授权）；Gemini 恢复后补做第三方复核。
- 第三方客户端库与 Europe PMC 数据许可的官方原文本轮**未核验**（该部分原计划由 Gemini 完成），标记**待核验**，不影响本 Adapter 的"标准库直连"决策。
