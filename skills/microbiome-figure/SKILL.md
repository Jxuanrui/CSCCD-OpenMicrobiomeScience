---
name: microbiome-figure
description: 菌群论文图表规划、可视化方案设计、代码模板提供和图例撰写，覆盖主图配置到美化输出
---


# microbiome-figure：图表规划与可视化

## 职责

帮助研究者规划论文图表布局，推荐合适的可视化方案，提供 R/Python 代码模板入口，撰写符合期刊规范的图例。

## 标准主图配置（推荐结构）

| Figure | 内容 | 核心图形类型 |
|--------|------|------------|
| Fig 1 | 研究设计 + 队列基线 | 示意图 + Table 1 |
| Fig 2 | 整体群落景观 | PCoA + 堆叠柱图 + Alpha 多样性箱线图 |
| Fig 3 | 差异物种/功能标志物 | LEfSe 柱图 / 热图 / 火山图 |
| Fig 4 | 预测模型（如有） | ROC 曲线 + SHAP 图 + 混淆矩阵 |
| Fig 5 | 多组学关联（如有） | 关联热图 + 网络图 |

## 常用图形快查

| 场景 | 推荐图形 | 主要 R 包 |
|------|---------|---------|
| 物种组成 | 堆叠柱图 | ggplot2 |
| Alpha 多样性 | 箱线图 + 显著性 | ggplot2 + ggpubr |
| Beta 多样性 | PCoA/NMDS | ggplot2 + vegan |
| 差异丰度 | LEfSe 柱图 / 火山图 | ggplot2 + microbiomeMarker |
| 物种相对丰度 | 热图 | pheatmap / ComplexHeatmap |
| 菌群网络 | 网络图 | igraph + ggraph |
| 系统发育 | Cladogram | ggtree |
| 机器学习性能 | ROC + AUC | pROC + ggplot2 |
| 多组学关联 | 气泡图 + 相关矩阵 | corrplot + ggplot2 |
| 韦恩图 | 组间共有/特有物种 | ggVennDiagram |

## 图例撰写规范（*Microbiome* 风格）

标准格式：
```
Figure X. [一句话标题，说明图的内容]
(A) [子图说明，包含统计方法和样本量].
(B) ...
统计方法：Wilcoxon rank-sum test, Benjamini–Hochberg correction.
*p < 0.05, **p < 0.01, ***p < 0.001, ns: not significant.
```

## 美化建议
- **配色**：优先使用 color-blind friendly 配色（ggsci 包的 `scale_color_npg()`、`scale_color_lancet()`）
- **字体大小**：正文 ≥ 8pt，图轴标签 ≥ 10pt
- **分辨率**：投稿用 ≥ 300 DPI（tiff/pdf 格式）
- **图幅**：单栏 ≤ 8.5 cm，双栏 ≤ 17.8 cm

## 代码模板入口
- 多样性图：`code_templates/R/diversity_analysis.R`
- 差异丰度图：`code_templates/R/differential_abundance.R`

## 调用关系
- 需要文献中特定图形的代码 → `microbiome-article-analysis`
- 图例英文表述 → `microbiome-polishing`
