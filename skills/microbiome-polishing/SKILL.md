---
name: microbiome-polishing
description: 菌群论文英文润色，纠正因果表述错误、统计术语混淆和中式英文表达
---


# microbiome-polishing：英文润色与表述纠偏

## 职责

针对菌群论文特有的语言问题进行精准纠正，重点处理因果混淆、统计术语误用和中式英文表达。

## 高频问题类型

### 1. 因果表述混淆（最严重）
| 错误表述 | 正确表述 |
|---------|---------|
| A bacteria caused B disease | A bacteria was significantly associated with B disease |
| gut microbiota regulates host immunity | gut microbiota was correlated with host immune markers |
| intervention altered microbiome → improved outcomes | intervention was associated with both microbiome changes and improved outcomes |

### 2. 统计术语误用
| 错误 | 正确 |
|-----|-----|
| significant difference (p < 0.05) | significant difference (Wilcoxon test, FDR-adjusted p < 0.05) |
| we found A correlates with B | A was positively correlated with B (Spearman r = 0.52, p = 0.003) |
| high AUC proves the model is good | the model showed good discriminatory performance (AUC = 0.85, 95% CI: 0.78–0.92) |

### 3. 中式英文表达
- "in this study, we mainly focus on..." → "We investigated..."
- "the result showed that there was a significant..." → "X was significantly higher in..."
- "which indicated that" 连续出现多次 → 替换为 suggesting, implying, consistent with

### 4. 措辞强度分级
- **观察性数据**：observed, found, identified, detected
- **统计关联**：was associated with, correlated with, linked to
- **有机制支撑**：may contribute to, potentially mediated by
- **有因果证据**：drove, caused（仅限动物实验 + 移植验证后）

## 使用方式
用户粘贴段落文本后，本 Skill 逐句审查并给出修改建议，说明每处修改的原因。

## 调用关系
- 论文整体结构问题 → `microbiome-writing`
- 投稿前综合审查 → `microbiome-prereview`
