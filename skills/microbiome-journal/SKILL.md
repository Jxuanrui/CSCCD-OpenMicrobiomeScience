---
name: microbiome-journal
description: 菌群论文选刊推荐、期刊风格分析和投稿梯度规划
---


# microbiome-journal：选刊推荐与期刊分析

## 职责

根据研究的数据类型、样本量、分析深度和创新点，推荐合理的目标期刊，分析期刊的审稿偏好。

## 期刊分级参考

### Tier 1（IF > 30，顶刊）
- *Nature* / *Science* / *Cell*：需要颠覆性发现 + 多重机制验证
- *Nature Medicine*：强调临床转化价值，需大队列
- *Nature Microbiology*：微生物学重大发现，基础 or 临床均可
- *Cell Host & Microbe*：菌群-宿主互作机制，重视机制闭环

### Tier 2（IF 10-30，主流高分）
- *Gut*（IF~25）：临床队列强，IBD/代谢病，英国 BMJ 旗下
- *Microbiome*（IF~13）：纯生信/菌群研究友好，方法类文章也接受
- *Nature Communications*（IF~14）：综合性，接受面广，审稿相对快
- *Gastroenterology*：消化科顶刊，强调临床意义

### Tier 3（IF 5-10，稳妥选择）
- *Gut Microbes*：专注菌群，接受多种类型
- *ISME J*：生态学视角，重视群落功能
- *mSystems*：开放获取，技术和工具类文章受欢迎
- *EBioMedicine*：临床转化，兰塞特旗下

## 选刊决策树

```
样本量 > 500 + 多中心验证？
  → 是：考虑 Gut / Nature Medicine / Gastroenterology
  → 否：

有机制实验（动物/细胞）？
  → 是：考虑 Cell Host Microbe / Nature Microbiology
  → 否：

多组学整合？
  → 是：Nature Communications / Microbiome
  → 否：

16S 横断面，无外部验证？
  → Gut Microbes / mSystems / EBioMedicine
```

## 期刊审稿偏好特点

| 期刊 | 审稿重点 |
|------|---------|
| *Gut* | 临床混杂是否控制；是否有独立验证队列 |
| *Microbiome* | 方法是否规范（STORMS）；数据是否开放 |
| *Cell Host Microbe* | 机制是否闭环；是否有体内验证 |
| *mSystems* | 重现性（代码和数据是否公开）|

## 调用关系
- 投稿前审查 → `microbiome-prereview`
- 论文写作风格调整 → `microbiome-writing`
