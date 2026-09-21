---
name: microbiome-stats
description: 菌群统计分析规范审查，覆盖多重检验矫正、PERMANOVA 协变量控制、生存分析和成分数据统计方法
---


# microbiome-stats：统计分析规范审查

## 职责

专注于菌群数据统计方法的正确性审查，不做分析执行，只做统计规范判断。

## 核心审查项

### 多重检验矫正
- 差异分析涉及 ≥ 10 个特征时，**必须** FDR 校正（Benjamini-Hochberg）
- 报告调整后 q 值，不能只报告原始 p 值
- 常见错误：只做组间两两比较，不矫正；或矫正方法过于保守（Bonferroni）

### Beta 多样性统计
- PERMANOVA（adonis2）是标准，但对组内方差不齐敏感
- **前置检验**：betadisper + permutest（若显著，PERMANOVA 结果需注明）
- 协变量必须纳入模型：`adonis2(dist ~ Group + Age + BMI, data=meta)`

### 成分数据统计
- 相对丰度是成分数据，直接用 t-test/ANOVA 在理论上错误
- 正确选择：MaAsLin2 / ANCOM-BC / ALDEx2 / DESeq2
- 相关性分析：需先 CLR 变换，再用 Pearson；或直接用 SparCC

### 纵向数据统计
- 配对样本不能用独立样本检验（Wilcoxon paired）
- 多时间点：线性混合效应模型（lme4::lmer）
- 同一个体的多样本必须在同一数据集内（不得跨训练/测试集）

### 生存分析（菌群 + 临床终点）
- 菌群高/低丰度分组：使用 MaxStat 或 median split（说明依据）
- 检验比例风险假设（Schoenfeld 残差检验）
- 多因素 Cox 模型中纳入菌群 + 临床混杂因素

### 样本量计算
- Alpha 多样性差异检测：基于效应量和 Wilcoxon 检验的功效分析
- 机器学习：AUC 置信区间宽度反推最小样本量（DeLong 法）

## 调用关系
- 统计规范问题通常在 `microbiome-prereview` 中集中暴露
- 具体执行时配合 `microbiome-bioinformatics`
