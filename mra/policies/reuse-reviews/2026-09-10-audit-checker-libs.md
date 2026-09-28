# Reuse Review 2026-09-10：确定性统计审计检查器

> 第四份 Reuse Review。执行：Codex 检索评估 + Kimi K3 补核 PyPI 许可证，结论一致。

## 1. 功能问题与检索记录

- **问题**：为 MVP"确定性统计审计检查器"选型 Python 统计库，实现多重检验校正、伪重复检测、批次混淆、成分数据、零模型/置换、数据泄漏六类检查（PLANNING.md 5.15/9.17：统计审计改为确定性代码规则 + 可选对抗复核）。
- **检索**：2026-09-10，固定候选（scipy、statsmodels、numpy、scikit-bio、pingouin、pandas）。Codex 沙箱 PyPI 部分不可达，scipy/statsmodels/numpy/pandas 以其本地已装 wheel 元数据核实，scikit-bio/pingouin 由 Kimi K3 补核 PyPI JSON。

## 2. 候选核实表

| 库 | 版本 | 许可证 | Python | 结论 |
|---|---|---|---|---|
| scipy | 1.15.3（本地） | BSD | — | 用（批次关联、置换检验） |
| statsmodels | 0.14.6（本地） | BSD | — | 用（多重检验统一入口 Bonferroni/Holm/BH/BY） |
| numpy | 2.2.5（本地） | BSD | — | 用（数组校验、受控 RNG） |
| pandas | 2.3.3（本地） | BSD-3-Clause | — | 用（ID/列联表/重复行检测，仅作表格层依赖） |
| scikit-bio | 0.7.3 | **BSD-3-Clause** | ≥3.10 | **条件采用**（仅组成数据 CLR/ILR+零替换） |
| pingouin | 0.6.1 | **GPL-3.0** | ≥3.10 | **排除**（copyleft，能力与 scipy/statsmodels 重叠无不可替代性） |

## 3. 评估（确定性边界，关键）

检查器只报告**可证明的结构事实**；方法选择、交换性、隐性依赖、因果混杂统一进 `REVIEW_REQUIRED`（可触发一次对抗复核），**不把"无法判断"当通过**。结果态：`PASS / WARN / FAIL / REVIEW_REQUIRED / NOT_APPLICABLE`。

## 4. 最终决策（组合融合）

statsmodels 做多重检验统一实现；SciPy 做检验与置换；NumPy 做输入验证与受控 RNG；pandas 复用为表格层；scikit-bio 条件封装组成数据接口（BSD-3 已核实）；Pingouin 不引入。

## 5. MVP 规则清单（Codex 产出，Kimi K3 复核采纳）

| ID | 优先级 | 检查内容 | FAIL 条件 |
|---|---|---|---|
| AUDIT-MULT-001 | P0 | p 值域校验 + statsmodels 重算 Bonferroni/BH 与提交值比对 | 非法 p 值 / 缺族定义 / 结果不一致 |
| AUDIT-ID-001 | P0 | 重复样本 ID、独立单位多行、train/val/test ID 交集 | 跨分区单位重叠 |
| AUDIT-BATCH-001 | P0 | 暴露×批次列联表，检测完全混淆/空单元/稀疏 | 完全混淆 |
| AUDIT-COMP-001 | P1 | 非负性/行和/零值；声明 CLR/ILR 时复算验证 | 有零未声明零策略 |
| AUDIT-PERM-001 | P1 | 置换配置完整性；固定 RNG 重跑；违规标签置换 | 配置不完整/违反交换块 |

## 6. 风险与核实记录

- 规则必须读取**结构化分析规格**，不能从源码推断检验族/独立单位/预处理边界。
- 重复 ID 是结构信号 ≠ 自动伪重复（技术重复/纵向/重复测量可能合理）。
- 批次与暴露显著相关 ≠ 已证明混杂；完全混淆可硬失败，其余报告列联表+效应量。
- 卡方期望频数过小时不可靠（2×2 用 Fisher，高维稀疏警告）。
- CLR/ILR 零值无定义，零替换算法必须进分析计划+审计账本，检查器不静默选择。
- 置换可复现 ≠ 零模型科学成立；须记录 NumPy/SciPy 版本、BitGenerator、次数、交换块。
