---
name: microbiome-citation
description: 菌群研究领域的精准引用检索、参考文献格式化和 RIS/BibTeX 导出
---


# microbiome-citation：精准引用检索与导出

## 职责

帮助研究者找到特定论点的最合适引用文献，生成标准格式的参考文献，支持 BibTeX/RIS 导出。

## 引用检索策略

### 按论点类型检索
- **方法引用**（"PERMANOVA 用于菌群 beta 多样性"）→ 找原始方法文献 + 菌群应用文献
- **现象引用**（"2型糖尿病患者菌群多样性降低"）→ 找系统综述 + 代表性队列研究
- **工具引用**（"使用 MetaPhlAn4"）→ 找工具原始发表论文

### 优先引用原则
1. **原始文献优先**：引用工具/方法的原始发表（如 QIIME2 的 2019 PeerJ 文章）
2. **高引用量优先**：对于已有大量研究的领域，优先引用被引 > 100 次的文献
3. **近期文献优先**（讨论前沿时）：优先 2022-2026 年

### 本地知识库检索
读取 `knowledge/papers/pmid_*.json`，按 `analysis_types` 和 `plain_description` 匹配

## 常用工具引用速查

| 工具 | 建议引用文献 |
|------|------------|
| QIIME2 | Bolyen et al. 2019, Nature Biotechnology |
| DADA2 | Callahan et al. 2016, Nature Methods |
| MetaPhlAn4 | Blanco-Miguez et al. 2023, Nature Biotechnology |
| HUMAnN3 | Beghini et al. 2021, eLife |
| MaAsLin2 | Mallick et al. 2021, PLOS Computational Biology |
| ANCOM-BC | Lin & Peddada 2020, Nature Communications |
| vegan（R） | Oksanen et al. CRAN package（引用版本）|
| LEfSe | Segata et al. 2011, Genome Biology |

## 输出格式

### BibTeX
```bibtex
@article{作者姓_年份_关键词,
  title   = {文章标题},
  author  = {第一作者 and 其他作者},
  journal = {期刊名},
  year    = {年份},
  volume  = {卷},
  pages   = {页码},
  doi     = {10.xxxx/xxxx}
}
```

## 调用关系
- 找文献 → `microbiome-search`
- 引用文献的方法需要复现 → `microbiome-article-analysis`
