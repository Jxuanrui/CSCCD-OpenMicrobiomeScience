---
name: microbiome-prereview
description: 投稿前模拟审稿，识别菌群论文的方法学硬伤、混杂因素遗漏、数据泄漏和过度推断问题
---


# microbiome-prereview：投稿前模拟审稿

## 职责

在正式投稿前，以审稿人视角全面扫描论文的方法学缺陷，区分"直接拒稿硬伤"和"修回可解决的问题"。

## 审查清单

### A. 直接拒稿硬伤（Blockers）
- [ ] 差异分析无多重检验矫正
- [ ] 两组基线（年龄/性别/BMI）有显著差异，未做匹配或多元回归控制
- [ ] 机器学习：全样本特征选择后再划分数据集（数据泄漏）
- [ ] 声称因果关系，但无动物实验或 MR 支撑
- [ ] 测序深度差异 > 5×，未稀释或说明处理方式
- [ ] 样本量 < 10/组，无统计功效分析

### B. 需补充说明的问题（Minor/Major Revision）
- [ ] 未报告引物序列和扩增区段
- [ ] 未说明 DNA 提取方法
- [ ] PERMANOVA 未做 betadisper 前置检验
- [ ] 机器学习未报告 95% CI
- [ ] 缺乏外部独立验证集
- [ ] 未与相关领域先前文献进行充分比较

### C. 表述问题（语言修回）
- [ ] 使用因果性语言（caused, led to）但证据不足
- [ ] 粪便菌群等同于肠黏膜菌群
- [ ] "significant" 未注明具体统计检验

## STORMS 规范核查
STORMS（Strengthening The Organization and Reporting of Microbiome Studies）核心条目：
- 样本采集标准化说明
- DNA 提取方法详述
- 测序平台和参数
- 生信流程版本
- 统计方法充分描述
- 原始数据提交至公共数据库（NCBI/GSA）

## 回答格式

```
🔴 硬伤（建议修复后再投）：
  [问题描述] → [修复建议]

🟡 需补充说明：
  [问题描述] → [建议处理方式]

🟢 表述修改建议：
  [段落/句子] → [修改后]
```

## 调用关系
- 统计问题详解 → `microbiome-stats`
- 语言问题 → `microbiome-polishing`
- STORMS 报告规范 → `microbiome-reporting`
