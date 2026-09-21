# Microbiome Research Profile — 关键词分级库

用于 Phase 1 扫描结果的 core / proxy / noise 三级分类。

## Core keywords（直接相关，优先保留）

```
gut microbiome
gut microbiota
intestinal microbiome
metagenomics shotgun
16S rRNA amplicon
fecal microbiota transplantation FMT
strain-level resolution
microbial diversity alpha beta
differential abundance
dysbiosis
short-chain fatty acids SCFA
bile acids microbiome
microbiome biomarker
longitudinal microbiome
cohort microbiome
```

## Proxy keywords（方法/疾病相邻，有参考价值）

```
metabolomics microbiome
multi-omics integration
metatranscriptomics
metaproteomics
host-microbiome interaction
microbiome machine learning
random forest microbiome
Mendelian randomization microbiome
germ-free mouse microbiome
antibiotic perturbation
phageome virome gut
spatial metagenomics
foundation model microbiome
gut-brain axis
Lactobacillus Bifidobacterium probiotic
Akkermansia Faecalibacterium butyrate
```

## Noise keywords（无关，丢弃）

```
soil microbiome
ocean microbiome
plant microbiome
skin microbiome only
oral microbiome only
veterinary animal microbiome
purely computational no biological data
review without new data
conference abstract
```

## 疾病相关性映射

当用户描述特定疾病时，补充以下 core 关键词：

| 疾病/表型 | 补充 core 关键词 |
|---------|----------------|
| IBD / 炎症性肠病 | `Crohn's disease microbiome`, `ulcerative colitis microbiome`, `mucosal microbiome IBD` |
| 2型糖尿病 / 代谢综合征 | `type 2 diabetes microbiome`, `insulin resistance gut bacteria`, `obesity microbiome` |
| 肝病 / 肝硬化 | `NAFLD microbiome`, `liver cirrhosis gut bacteria`, `gut-liver axis` |
| 结直肠癌 | `colorectal cancer microbiome`, `Fusobacterium nucleatum`, `CRC fecal biomarker` |
| 肿瘤免疫治疗 | `immunotherapy microbiome`, `PD-1 gut microbiota`, `anti-tumor immune response microbiome` |
| 神经退行性疾病 | `Alzheimer's microbiome`, `Parkinson's gut microbiota`, `gut-brain axis neurodegeneration` |
| 新生儿 / 早期发育 | `infant microbiome`, `neonatal gut colonization`, `maternal microbiome transmission` |
| 自身免疫 | `rheumatoid arthritis microbiome`, `lupus gut microbiota`, `multiple sclerosis microbiome` |

## 期刊相关性分级

Phase 1 检索到文章后，按期刊评估竞争水位：

| 期刊 | 竞争信号含义 |
|------|------------|
| Nature / Science / Cell | 该方向已有顶级关注，但也说明话题热度高 |
| Nature Medicine / Cell Host Microbe | 强临床转化或机制要求，用户需评估能否达到 |
| Gut / Gastroenterology | 临床队列标杆，样本量和混杂控制要求高 |
| Microbiome / Nature Communications | 主流高分可及范围，方法严格即可 |
| Gut Microbes / mSystems / ISME J | 稳妥目标，接受多种研究类型 |
