---
name: microbiome-response
description: 菌群论文审稿意见逐点回复策略，包括生信重分析建议、话术设计和修回逻辑
---


# microbiome-response：审稿意见回复

## 职责

帮助研究者分析审稿意见的本质诉求，设计逐点回复策略，区分"可以做"和"无法完全满足但可以有效回应"的意见。

## 意见分类处理

### A类：方法学质疑（最难，需认真对待）
常见：样本量不足、缺外部验证、统计方法有问题、数据泄漏
→ 处理：如果确实有问题，补做分析；如果有误解，用数据解释

### B类：需要补充分析
常见：请补充亚组分析、请做多因素回归控制混杂
→ 处理：通常可以做，做了后论文更强

### C类：文献引用问题
常见：忽略了某篇重要文献、与某研究结论矛盾
→ 处理：引用并讨论，如有矛盾说明差异原因（队列、人群、测序方式不同）

### D类：解读争议
常见：结论过度推断、机制解释不够严谨
→ 处理：弱化语言（caused → associated with），增加局限性说明

## 回复话术模板

### 接受并修改
```
We thank the reviewer for this insightful comment. We agree that [问题].
We have now [补充的分析/修改], and the results are presented in [图/表位置].
The revised text now reads: "[修改后的文字]"
```

### 有数据支撑的不同意见
```
We appreciate this concern. However, [解释理由 + 数据支撑].
Specifically, [统计结果]. This suggests that [你的结论依然成立的原因].
We have added a sentence in the Discussion to clarify this point.
```

### 无法完全满足但合理回应
```
We acknowledge this limitation. [解释为什么无法完全满足].
To partially address this concern, we have [能做的补充措施].
We have explicitly noted this limitation in the Discussion.
```

## 调用关系
- 补做统计分析 → `microbiome-stats` 或 `microbiome-bioinformatics`
- 语言润色 → `microbiome-polishing`
- 报告规范补充 → `microbiome-reporting`
