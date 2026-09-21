---
name: microbiome-bioinformatics
description: 传统菌群生物信息学分析审计与执行引导，覆盖 16S/宏基因组上游质控到下游多样性、差异丰度全流程，融合官方文档、知识图谱参数参考和最新工具动态
---


# microbiome-bioinformatics：传统菌群生信流程审计与执行

## 快速调用示例

```
/microbiome-bioinformatics 16S 全流程 120例×平均50000reads
/microbiome-bioinformatics 宏基因组 物种+功能注释 60例×10G
/microbiome-bioinformatics 16S 差异丰度分析 已有ASV表和元数据
/microbiome-bioinformatics 宏基因组 MAGs组装 30例×15G
```

## 执行逻辑（四层信息融合）

收到用户描述后，**按以下顺序并行执行三路检索**，再综合输出：

---

## Step 0 — 数据可行性预判（执行检索前先做）

根据用户描述的测序类型和样本规模，快速判断：

| 检查项 | 16S 标准 | 宏基因组标准 |
|-------|---------|------------|
| 最低测序深度 | ≥ 20,000 reads/样本（V3-V4）| ≥ 5G/样本（物种级）；≥ 10G（菌株级/MAGs）|
| 深度差异阈值 | 最大/最小比 > 3× → 必须稀释 | 深度差异 > 2× → 建议注明处理方式 |
| 最低样本量 | 差异分析 ≥ 10/组；ML ≥ 50/组 | 同左 |
| 批次效应风险 | 多中心/多批次？→ 需 MMUPHin 校正 | 同左，且需统一流程版本 |
| 计算资源估算 | 16S 轻量，普通服务器即可 | 宏基因组：每样本 10G × 60 = 600G 存储；MetaPhlAn4 约 16G 内存/样本 |

若数据不满足最低要求，**先告知用户限制**，再继续分析建议。

---

## Step 1 — 官方文档检索（工具权威参数）

针对用户涉及的工具，用 `WebFetch` 抓取官方文档，或 `WebSearch` 检索最新教程：

**16S 流程工具**：
```
# DADA2 官方
WebFetch: https://benjjneb.github.io/dada2/tutorial.html
WebSearch: "DADA2 1.26 truncLen parameter best practices V3-V4 2024 2025"

# QIIME2 最新版
WebSearch: "QIIME2 2024 tutorial 16S ASV DADA2 parameter"
```

**宏基因组流程工具**：
```
WebFetch: https://huttenhower.sph.harvard.edu/metaphlan/
WebSearch: "MetaPhlAn4 mpa_vJan21_CHOCOPhlAnSGB_202103 database 2024 2025"
WebSearch: "HUMAnN3 tutorial parameter UniRef90 2024"
WebSearch: "KneadData bowtie2 host removal parameter 2024"
```

**统计分析工具**：
```
WebFetch: https://huttenhower.sph.harvard.edu/maaslin2/
WebSearch: "MaAsLin2 reference_level normalization fixed_effects 2024"
WebSearch: "ANCOM-BC2 tutorial 2024 compositional data"
```

重点提取：推荐参数值、已知陷阱、版本变化说明。

---

## Step 2 — 知识图谱参数参考（双路检索）

**路径 A：已精读概念节点（高分文献参数）**
```bash
# 检索同类型分析的精读文献
grep -l "DADA2\|MetaPhlAn\|MaAsLin2\|HUMAnN\|QIIME" knowledge/concepts/entries/*.md 2>/dev/null | head -8
# 读取最相关条目的 M2（测序参数）和 M3（分析流程）
grep -A5 "测序深度\|质控\|truncLen\|MetaPhlAn\|MaAsLin2" knowledge/concepts/entries/{relevant_pmid}.md 2>/dev/null
```

**路径 B：可执行代码节点（直接可用代码）**
```bash
# 检查知识库中是否有同类工具的可运行代码
ls knowledge/papers/pmid_*.json | xargs python3 -c "
import json, sys
for f in sys.argv[1:]:
    d = json.load(open(f))
    tools = d.get('main_tools', [])
    pmid = d.get('pmid', '')
    if any(t in str(tools) for t in ['DADA2','MetaPhlAn','QIIME2','MaAsLin2','HUMAnN']):
        print(f'PMID {pmid}: {tools[:4]}')
" 2>/dev/null
```

若找到匹配的可执行节点，在输出末尾**主动提示**：
> 知识库中 PMID XXXXX（已有可运行代码）使用了相同工具，建议调用 `/microbiome-article-analysis XXXXX` 直接获取适配代码。

---

## Step 3 — 最新工具动态检索

```
WebSearch: "microbiome bioinformatics new tools 2024 2025 DADA2 alternative"
WebSearch: "gut microbiome pipeline benchmark comparison 2024 2025"
```

重点关注：
- 有无比当前主流工具更快/更准的替代品（如 Amplicon Sequence Variant 领域的 VSEARCH、Deblur）
- 长读长测序（Oxford Nanopore）分析工具的新发展
- R 生态的新包（phyloseq → mia/TreeSummarizedExperiment 的迁移趋势）

---

## 综合输出框架

完成三路检索后，按以下结构输出：

### 1. 可行性判断
数据规模是否满足目标分析，计算资源估算（内存/存储/时间）。

### 2. 推荐流程与工具选择

#### 16S 流程

**工具选择决策**：

| 场景 | 推荐工具 | 备注 |
|------|---------|------|
| 标准 16S（V3-V4，Illumina PE250）| DADA2 ≥ 1.26 | 当前金标准，ASV 分辨率 |
| 大样本（>500）低内存环境 | Deblur（QIIME2 插件）| 比 DADA2 快，精度稍低 |
| 快速概览（不需要 ASV）| VSEARCH + 97% OTU | 不推荐用于发表，仅探索 |
| 长读长（PacBio/Nanopore）| DADA2 + `BAND_SIZE=32` | 参数需调整 |

**OTU vs ASV 选择原则**：
- 发表用途 → **必须 ASV**（OTU 97% 相似度的精度不足以支撑菌种级结论）
- 跨研究合并分析 → ASV 可直接比较，OTU 不可（参考序列不同）
- 遗留数据（只有 OTU 表）→ 可继续，但在 Methods 中说明局限性

**DADA2 关键参数（基于官方文档 + 高分文献参考）**：

```r
# V3-V4 区，Illumina PE250，标准参数
filterAndTrim(
  truncLen    = c(230, 200),   # F/R 截断长度：保证合并后有≥20bp重叠
  maxEE       = c(2, 2),       # 最大期望错误数（越严格样本量越少）
  truncQ      = 2,             # 质量分低于2时截断
  rm.phix     = TRUE,          # 去除PhiX对照序列
  compress    = TRUE
)

# pooling 策略（影响稀有ASV检测）
dada(derep, pool = FALSE)        # 默认，最快
dada(derep, pool = "pseudo")     # 推荐：平衡速度和敏感性，适合大多数研究
dada(derep, pool = TRUE)         # 最灵敏但最慢，适合珍稀序列研究
```

**引用来源**：截断长度选择参考 [DADA2 官方教程](https://benjjneb.github.io/dada2/tutorial.html)；pseudo-pooling 参考 [DADA2 1.8 Release Notes](https://benjjneb.github.io/dada2/ReleaseNotes_1_8.html)

#### 宏基因组流程

**MetaPhlAn4 关键参数**：

```bash
# 物种级分类（推荐参数）
metaphlan input.fastq.gz \
  --input_type fastq \
  --nproc 8 \
  --bowtie2db /path/to/mpa_vJan21_CHOCOPhlAnSGB_202103 \  # 锁定数据库版本！
  --index mpa_vJan21_CHOCOPhlAnSGB_202103 \
  -o output_profile.txt

# 重要：数据库版本必须在 Methods 中报告
# 当前最新：mpa_vOct22_CHOCOPhlAnSGB_202212（2022年版）
# 多样本合并
merge_metaphlan_tables.py *_profile.txt > merged_abundance.txt
```

**HUMAnN3 功能注释**：

```bash
humann --input sample.fastq.gz \
  --output humann_output/ \
  --threads 8 \
  --protein-database /path/to/uniref90_annotated/  # UniRef90 vs UniRef50：精度 vs 速度
```

### 3. 参数配置版本清单（可直接贴入 Methods）

输出一份标准化版本清单，格式如下（根据用户实际使用工具填写）：

```
质控：fastp v0.23.4（参数：--detect_adapter_for_pe --qualified_quality_phred 20）
宿主去除：KneadData v0.12.0 + human genome hg37
物种注释：MetaPhlAn v4.1.0 + 数据库 mpa_vJan21_CHOCOPhlAnSGB_202103
功能注释：HUMAnN v3.9 + UniRef90（2023-09-14）
差异分析：MaAsLin2 v1.14.1（R 4.3.1）
统计平台：R v4.3.1 / Python v3.10
```

### 4. 格式转换地图（常见卡壳点）

| 上游输出 | 下游需求 | 转换方法 |
|---------|---------|---------|
| DADA2 `seqtab.nochim` | phyloseq `otu_table` | `otu_table(seqtab.nochim, taxa_are_rows=FALSE)` |
| phyloseq 对象 | MaAsLin2 输入 | `as(otu_table(ps), "matrix")` + `as(sample_data(ps), "data.frame")` |
| MetaPhlAn4 merged table | HUMAnN3 协同分析 | 直接作为 `--taxonomic-profile` 输入 |
| DADA2 ASV 表 | QIIME2 artifact | `qiime tools import --type FeatureTable[Frequency]` |
| Kraken2 output | Bracken 丰度估算 | `bracken -d DB -i kraken_report -o bracken_output` |
| phyloseq | mia（TreeSummarizedExperiment）| `makeTreeSummarizedExperimentFromPhyloseq(ps)` |

**注意**：phyloseq 正在被 mia/TreeSummarizedExperiment 逐步取代（Bioconductor 2024 推荐），新项目建议直接用 mia，旧项目可用转换函数过渡。

### 5. 关键审查点（投稿前必查）

- [ ] 稀释（rarefaction）策略：深度差异 > 3× 必须稀释，或使用 DESeq2/MaAsLin2 内置归一化
- [ ] PERMANOVA 前是否做 `betadisper` 检验（组内方差齐性）
- [ ] 差异分析：FDR 校正（BH）是否已做；协变量是否完整纳入模型
- [ ] 机器学习：特征选择是否在每个内部交叉验证折内独立执行（防数据泄漏）
- [ ] 工具版本和数据库版本是否记录（STORMS 必填项）
- [ ] 宏基因组：宿主去除比例是否报告（通常 1-30%，异常值需解释）

### 6. 最新工具补充（基于 Step 3 检索结果）

根据 WebSearch 检索结果，补充当前主流工具的最新替代或升级选项。

---

## 调用关系
- 数据可行性有问题 → `/microbiome-design`（重新评估研究方案）
- 统计方法选择疑问 → `/microbiome-stats`（专项统计规范审查）
- 结果可视化 → `/microbiome-figure`
- 代码直接获取 → `/microbiome-article-analysis <PMID>`（知识库可执行节点）
- 宏基因组特有分析（MAGs/耐药基因）→ `/microbiome-metagenomics`
- 结果写成论文 → `/microbiome-writing`
