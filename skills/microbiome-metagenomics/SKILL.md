---
name: microbiome-metagenomics
description: 宏基因组特有分析流程指导，涵盖功能通路注释、MAGs 组装、耐药基因和毒力因子挖掘
---


# microbiome-metagenomics：宏基因组分析

## 快速调用示例

```
/microbiome-metagenomics 物种组成 60例×10G IBD横断面队列
/microbiome-metagenomics MAG组装 30例×15G 发现新菌种
/microbiome-metagenomics 耐药基因筛查 ICU患者粪便 20例×8G
/microbiome-metagenomics 功能通路 50例×10G 代谢综合征
/microbiome-metagenomics 病毒组 30例×20G 噬菌体多样性
```

## 职责与边界

- 本 Skill 仅覆盖 shotgun metagenomics，不覆盖 16S/ITS 扩增子分析。
- 核心任务：物种组成、功能通路、MAG、耐药基因、毒力因子、病毒/噬菌体、菌株水平分析。
- 统计建模、文章复现、图件设计超出本 Skill 边界，按触发条件调用其他 Skill。
- 默认优先保证可复现性：工具版本、数据库版本、参数、输入输出路径必须记录。

---

## Step 0：数据可行性预判

### 0.1 用户需求解析

- 若用户只说"做宏基因组分析"，先判断目标：物种谱、功能谱、MAG、ARG、VF、病毒、strain-level 或综合。
- 若用户已有 FASTQ → 从 QC、宿主去除和 read-based profiling 开始。
- 若用户已有 contigs → 判断是否进入基因预测、功能注释、ARG/VF 或病毒识别。
- 若用户已有 bins/MAGs → 判断是否做 CheckM2、GTDB-Tk、功能注释和丰度回贴。
- 若用户已有 abundance/profile 表 → 检查工具来源和数据库版本，再转向统计分析。

### 0.2 测序深度与分析可行性

| 分析目标 | 推荐 clean data | 最低可行 | 不足时降级方案 |
|---------|----------------|---------|--------------|
| 物种组成 | 3-5 Gbp/样本 | 1-2 Gbp | 降到属水平解释 |
| HUMAnN 功能谱 | 5-10 Gbp/样本 | 3 Gbp | 只做主要通路 |
| ARG 筛查 | 5-10 Gbp/样本 | 3 Gbp | 结果仅探索性 |
| VF 筛查 | 5-10 Gbp/样本 | 3 Gbp | 强调验证不足 |
| 病毒/噬菌体 | ≥10 Gbp/样本 | 5 Gbp | 仅做候选 contig |
| MAG 单样本 | ≥10 Gbp/样本 | 5 Gbp | 不建议强解释 |
| MAG co-assembly | ≥5 Gbp/样本 | 3 Gbp（多样本） | 谨慎处理批次 |
| Strain-level | ≥10 Gbp/样本 | 5 Gbp | 不做菌株结论 |

### 0.3 计算资源预估

| 流程类型 | CPU | 内存 | 磁盘（原始数据倍数）| 主要耗时步骤 |
|---------|-----|------|-------------------|-----------:|
| QC + MetaPhlAn | 8-16 threads | 32-64 GB | 3-5× | Bowtie2 比对 |
| Kraken2 + Bracken | 16-32 threads | 64-256 GB | 3-5× | 数据库加载 |
| HUMAnN | 16-32 threads | 64-128 GB | 5-8× | nucleotide/protein search |
| 单样本 assembly | 16-32 threads | 128-256 GB | 5-10× | MEGAHIT/metaSPAdes |
| co-assembly + binning | 32-64 threads | 256-512 GB | 10×+ | assembly、binning |
| GTDB-Tk | 16-32 threads | 128-256 GB | 100-300 GB 额外 | marker placement |

**资源适配建议**：

| 资源档位 | 推荐配置 | 可执行策略 | 代价 |
|---------|---------|-----------|------|
| 理想配置 | 64 threads、512 GB RAM、≥5 TB scratch | MAG single-assembly、分组 co-assembly、dRep、GTDB-Tk 可完整执行 | 时间和失败率较可控 |
| 最低配置 | 32 threads、128 GB RAM、≥3 TB scratch | 优先 MEGAHIT；减少并行样本数；分批 assembly/binning；避免大规模 metaSPAdes | 总耗时通常延长 2-3× |
| 受限配置 | ≤16 threads、≤64 GB RAM | 只做 read-based profiling、HUMAnN 或候选 contig 分析；MAG 仅探索性 | 不建议承诺新菌种发现 |
| 云/HPC 配置 | 多节点或作业队列 | 每个样本独立投递；GTDB-Tk、dRep 单独排队；保留中间文件路径 | 需明确并行度和存储清理策略 |

- 若用户未说明计算资源，输出中必须询问 CPU、内存、可用磁盘、是否有 HPC/云环境。
- 若资源不足，不直接否定分析目标，应给出降级方案：降低并行度、使用 MEGAHIT 替代 metaSPAdes、按批次处理、优先做 read-based 结果。
- 对 MAG/新菌种发现场景，磁盘瓶颈通常比 CPU 更早出现，需提醒保留 raw、clean、contigs、BAM/depth、bins、MAG 注释结果。

**时间线估算需声明并行度假设**：

| 场景 | 并行度假设 | 主要瓶颈 | 预计耗时 | 备注 |
|-----|-----------|---------|---------|------|
| 30例×15G read-based profiling | 5-10 样本并行 | HUMAnN search、数据库 I/O | 2-5 天 | 依数据库和磁盘 I/O 波动 |
| 30例×15G single-assembly MAG | 3-5 样本并行 | assembly、binning、BAM 回贴 | 7-14 天 | 推荐用于个体差异明显的肠道样本 |
| 30例×15G co-assembly MAG | 1-2 组并行 | co-assembly 内存峰值 | 10-21 天 | 需按分组设计，避免跨批次混合 |
| dRep + GTDB-Tk + annotation | 1-3 任务并行 | GTDB-Tk placement、数据库读取 | 2-5 天 | 依 MAG 数量变化显著 |
| 低资源环境 MAG | 1 样本并行 | assembly 内存和磁盘 | 3-6 周 | 需分批执行和清理中间文件 |

- 输出时间线时必须写清楚：样本数、每样本数据量、并行样本数、CPU/内存假设。
- 若无法确认用户资源，应给出“理想配置”和“最低配置”两套时间估算。

### 0.4 质量风险预判

- 宿主污染率 >10%：需解释宿主去除策略；>30%：评估有效 microbial reads 是否足够。
- clean reads 保留率 <70%：检查测序质量、接头污染或宿主比例。
- MetaPhlAn classified fraction 很低：检查数据库版本、样本类型是否为非肠道。
- 数据库版本不同、工具策略不同、批次处理不同的结果**不可直接合并比较**。

---

## Step 1：官方文档与版本确认

### 1.1 WebFetch / WebSearch 触发条件

- 用户使用"最新""推荐""最佳实践""投稿""复现""当前版本"时，**必须 WebSearch**。
- 涉及工具版本、数据库版本、参数变化时，**必须 WebFetch 官方文档或 GitHub release**。

```
# MetaPhlAn / HUMAnN 最新版本和数据库
WebFetch: https://huttenhower.sph.harvard.edu/metaphlan/
WebSearch: "MetaPhlAn4 latest database version 2025"
WebSearch: "HUMAnN4 release 2024 2025"

# MAG 工具链
WebSearch: "CheckM2 vs CheckM1 2024 MAG quality"
WebSearch: "GTDB-Tk latest release 2025"
WebSearch: "SemiBin2 benchmark 2024"

# 专项工具
WebSearch: "CARD RGI latest version 2025"
WebSearch: "geNomad viral detection benchmark 2024 2025"
```

### 1.2 版本记录规范（Methods 必填）

| 项目 | 必须记录 | 示例 |
|-----|---------|------|
| 软件 | 名称、版本、关键参数 | MetaPhlAn v4.1.0, --nproc 8 |
| 数据库 | 名称、版本、下载日期 | mpa_vOct22_CHOCOPhlAnSGB_202212 |
| 参考基因组 | 物种、版本、来源 | human GRCh38 / mouse GRCm39 |
| Kraken2 库 | 构建日期、包含数据库 | RefSeq bacteria/viral/human |
| 过滤阈值 | identity、coverage、e-value | identity ≥80%, coverage ≥70% |
| 归一化方法 | 单位 | ARG copies per million reads |

### 1.3 WebSearch 失败降级策略

- 若 WebSearch/WebFetch 返回 503、超时、DNS 错误、权限错误或网络不可用，必须在输出中明确说明：**本次未完成联网确认**。
- 降级回答时只能基于本 Skill、项目知识库、已检索到的本地文献和通用稳定原则给出保守建议。
- 涉及“最新版本”“最新数据库 release”“最新 benchmark”的内容，必须标注为“需联网复核”或“需用户执行前确认”。
- 不得把本地知识库结论表述为最新官方结论。
- 若用户请求投稿级 Methods，应提醒执行前核验工具版本、数据库版本和下载日期。
- 降级提示推荐格式：

```
联网确认状态：WebSearch/WebFetch 未完成（原因：503/超时/不可用）。
以下方案基于本地知识库与通用 MAG 最佳实践，版本号和数据库 release 需在执行前复核。
```

---

## Step 2：需求分流与流程决策

### 2.0 知识图谱检索摘要输出规范

当任务涉及 MAG、功能通路、ARG/VF、病毒、strain-level 或机制解释时，应先检索项目知识库，并在正式方案前输出摘要。

推荐格式：

```
知识图谱检索摘要：
- 概念/文献节点：检索到 N 条相关记录
- 可执行流程节点：检索到 M 条可复用流程
- 推荐参考：PMID XXXXXXXX（推荐原因：流程完整/样本类型相近/工具链可复现）
- 可进一步调用：/microbiome-article-analysis PMID
```

MAG 场景优先摘要内容：

| 检索对象 | 输出要求 |
|---------|---------|
| MAG 相关文献 | 报告命中数量和最相关 PMID，不只输出单篇文献 |
| 可执行流程 | 标注是否包含 QC、assembly、binning、CheckM2/CheckM、GTDB-Tk、dRep、annotation |
| 新菌种发现 | 标注是否包含 ANI/AAI、系统发育、GTDB 分类、命名或验证流程 |
| 后续 Skill | 若命中可复现文章，建议调用 `/microbiome-article-analysis PMID` 获取流程细节 |

示例：

```
知识图谱检索：
- 概念节点：74 篇 MAG 相关文献
- 可执行节点：2 个（PMID 34614189, PMID 33606979）
- 推荐：PMID 34614189（完整 MAG 流程，可调用 /microbiome-article-analysis）
```

### 2.1 分析目标决策树

- **物种组成/菌群结构** → QC → 宿主去除 → MetaPhlAn 或 Kraken2+Bracken
- **功能通路/代谢潜力** → QC → 宿主去除 → MetaPhlAn → HUMAnN
- **组装基因/KEGG/COG** → QC → assembly → gene prediction → eggNOG/KEGG
- **MAG/新菌基因组** → 先判断深度 → assembly → binning → CheckM2 → GTDB-Tk
- **耐药基因** → 按数据量选 read-based 或 assembly-based RGI/CARD
- **毒力因子** → contigs/genes → Diamond → VFDB（严格过滤）
- **病毒/噬菌体** → assembly → viral detection → host prediction → abundance
- **Strain-level** → 判断深度和物种丰度 → StrainPhlAn / inStrain / MIDAS
- **差异分析/关联分析** → 本 Skill 准备 abundance table → 调用 `/microbiome-bioinformatics`
- **文章复现/机制解释** → 调用 `/microbiome-article-analysis`
- **画图/结果展示** → 调用 `/microbiome-figure`

### 2.2 Read-based 物种分类

| 工具 | 策略 | 优势 | 局限 | 推荐场景 |
|-----|------|------|------|---------|
| MetaPhlAn4 | marker gene | 精度高，种水平稳健 | 只报告 marker 覆盖物种 | 肠道队列、HUMAnN 前置 |
| Kraken2 | k-mer | 快，分类范围广 | 假阳性和数据库依赖强 | 大样本快速分类 |
| Bracken | Bayesian re-estimation | 改善 Kraken 丰度估计 | 依赖 Kraken report | Kraken2 后处理 |

**重要**：MetaPhlAn 与 Kraken2/Bracken 的丰度表**不可直接混用或合并比较**。

### 2.3 功能注释流程

| 路线 | 输入 | 输出 | 推荐场景 |
|-----|------|------|---------|
| HUMAnN3 | clean FASTQ + MetaPhlAn profile | UniRef gene family、MetaCyc pathway | 队列功能谱 |
| Assembly + eggNOG | contigs → proteins | KEGG/COG/GO | 基因目录构建 |
| MAG annotation | MAG fasta | per-MAG 功能基因 | 链接分类与功能 |
| CAZy/dbCAN | proteins/genes | 碳水化合物活性酶 | 膳食纤维相关机制 |

### 2.4 MAG 分析流程

| 步骤 | 推荐工具 | 输出 | 关键判断 |
|-----|---------|------|---------|
| 组装 | MEGAHIT / metaSPAdes | contigs fasta | N50、总长度 |
| 回贴估 depth | Bowtie2 | BAM + depth table | coverage 均一性 |
| 分箱 | MetaBAT2 / SemiBin2 | bin fasta | bin 数量 |
| 精化 | DAS Tool | refined bins | consensus bin refinement |
| 质控 | CheckM2 | completeness/contamination | ≥50%, ≤5% |
| 跨样本去冗余 | dRep / fastANI | dereplicated MAGs + cluster table | species-level ANI 95%；strain-level ANI 99% |
| 分类 | GTDB-Tk | taxonomy | 记录 GTDB release |
| 功能注释 | Prokka + eggNOG / DRAM | function table | 记录数据库版本 |

**MAG 质量分级**：

| 等级 | 完整度 | 污染度 | 适用范围 |
|-----|-------:|-------:|---------|
| High-quality | ≥90% | ≤5% | 可较强解释 |
| Medium-quality | ≥50% | ≤10% | 谨慎解释 |
| Low-quality | <50% | 任意 | 不进入核心结果 |
| **本 Skill 推荐** | **≥50%** | **≤5%** | 肠道项目更保守 |

#### 2.4.1 组装策略决策表

| 策略 | 适用场景 | 优势 | 风险 | 推荐判断 |
|-----|---------|------|------|---------|
| Single-assembly | 每样本 ≥10 Gbp；个体差异大；目标是样本级 MAG 和新菌候选 | 保留个体差异，减少跨样本混合 | 低丰度物种可能组装不足 | 肠道 30例×15G 默认优先 |
| Group co-assembly | 同一组内样本相似；每样本 3-10 Gbp；希望恢复低丰度基因组 | 提高低丰度物种 contig 连续性 | 组内 strain 混杂，内存高 | 可按疾病组/时间点/批次分组 |
| Global co-assembly | 样本数少且高度同质；环境复杂度低 | 可能获得更多 contigs | 肠道样本易产生 strain 混合和 chimeric bins | 不建议用于 30例肠道样本整体合并 |
| Hybrid：single + dRep | 多样本 MAG 发现、新菌种筛选 | 兼顾样本级恢复和跨样本去冗余 | 计算量较高 | 新菌种发现推荐主线 |
| Co-binning | 多样本 coverage pattern 可用 | 改善分箱 | 依赖样本间覆盖差异和工具参数 | 可作为 SemiBin2/MetaBAT2 补充 |

- 对“30例×15G 肠道样本，希望发现新菌种”：优先推荐 **single-assembly → per-sample binning → CheckM2 → dRep → GTDB-Tk → ANI/系统发育验证**。
- 若样本有明确分组且资源充足，可补充分组 co-assembly，但需避免跨批次混合。
- 若用户资源不足，优先保留 single-assembly 主线，降低并行度，不优先选择全样本 co-assembly。

#### 2.4.2 新菌种发现专项判断

| 判断层级 | 推荐方法 | 常用阈值/标准 | 输出解释 |
|---------|---------|--------------|---------|
| MAG 基础质量 | CheckM2 | completeness ≥50%，contamination ≤5%；高质量优先 ≥90%/≤5% | 低质量 MAG 不作为新菌种核心证据 |
| 物种边界 | fastANI / dRep / GTDB-Tk | 与最近参考基因组 ANI <95% | 候选新物种 |
| 属级边界 | AAI / GTDB taxonomy / phylogeny | AAI 约 <65-70% 或 GTDB 无明确属级归属 | 只能作为候选新属线索 |
| 系统发育位置 | GTDB-Tk marker tree / PhyloPhlAn | 与近缘参考形成独立分支 | 支持分类新颖性 |
| 基因组完整性 | CheckM2 + single-copy marker | 污染低且 marker 合理 | 排除混合 bin |
| 16S/marker 支持 | Barrnap / RNAmmer / marker genes | 有 16S rRNA 或保守 marker 更佳 | 无 16S 时需保守表述 |
| 命名与描述 | SeqCode/GTDB 风格 | 未培养 MAG 使用候选命名 | 避免正式命名过度声明 |

新菌种发现场景必须输出：

- ANI 阈值解释：通常 ANI <95% 支持候选新物种；不能只凭 GTDB 未分类直接认定新物种。
- 验证清单：CheckM2 质量、dRep 去冗余、GTDB-Tk 分类、近缘基因组 ANI、系统发育树、功能注释、丰度和样本分布。
- 保守表述：优先使用“candidate novel species”“候选新物种 MAG”“putative novel taxon”，避免直接写成“发现并命名新菌种”。
- 若用于投稿，需补充最近参考基因组下载日期、GTDB release、ANI 工具版本和参数。

#### 2.4.3 MAG 后续专项分析

| 分析方向 | 推荐工具/方法 | 适用问题 | 交付物 |
|---------|--------------|---------|--------|
| MAG 比较基因组学 | Roary / Panaroo / PPanGGOLiN / anvi'o | core/accessory genes、菌株差异 | pangenome matrix、core gene tree |
| 代谢潜力评估 | DRAM / eggNOG / KEGG Decoder | 碳源利用、氨基酸/胆汁酸/短链脂肪酸潜力 | pathway completeness table |
| CAZy 分析 | dbCAN | 膳食纤维降解、碳水化合物酶 | CAZyme family table |
| ARG/VF 筛查 | RGI/CARD、VFDB + Diamond | 安全性和潜在风险 | ARG/VF hit table |
| 丰度回贴 | Bowtie2 / CoverM | MAG 在样本间的分布 | MAG abundance table |
| 系统发育展示 | GTDB-Tk tree / PhyloPhlAn | 新颖性和近缘关系展示 | marker tree、注释树 |

- MAG 后续丰度差异、关联分析和多变量模型不在本 Skill 内完成，应调用 `/microbiome-bioinformatics`。
- 系统发育树和出版级图件应调用 `/microbiome-figure`。
- 文章流程复现或参考 PMID 的 Methods 对齐应调用 `/microbiome-article-analysis`。

### 2.5 ARG / VF 专项分析

| 目标 | 推荐工具/数据库 | 推荐阈值 | 注意 |
|-----|---------------|---------|------|
| ARG | RGI + CARD | strict/perfect model | 区分 read-based vs assembly-based |
| ARG 补充 | AMRFinderPlus / ResFinder | 依数据库建议 | 验证主要 ARG 发现 |
| VF | Diamond + VFDB | identity ≥80%, coverage ≥70%, e-value ≤1e-5 | 不等同于致病性 |
| ARG/VF 丰度 | reads mapping 回贴 | CPM 或 copies per million reads | 归一化必须记录 |

### 2.6 病毒与噬菌体分析

| 步骤 | 推荐工具 | 审查点 |
|-----|---------|-------|
| Viral contig 识别 | VIBRANT / geNomad / VirSorter2 | contig 长度阈值（≥1 kb）、score |
| 深度学习预测 | DeepVirFinder | 短 contig 假阳性风险 |
| 宿主预测 | CRISPR spacer / tRNA / k-mer | 置信度分级，避免强断言 |
| 丰度估计 | Bowtie2 回贴 | multi-mapping 处理方式 |
| 功能注释 | eggNOG / PHROG | 数据库偏倚 |

### 2.7 Strain-level 分析

| 工具 | 用途 | 数据要求 |
|-----|------|---------|
| StrainPhlAn | 菌株系统发育 | 目标物种丰度足够 |
| inStrain | SNP、ANI、strain sharing | 高覆盖度 |
| MIDAS | species/pangenome/SNP | 数据库覆盖充分 |
| PanPhlAn | 菌株基因组成 | 目标物种数据库 |

---

## Step 3：输出规范与审查清单

### 3.1 推荐流程一览

| 目标 | 推荐流程 | 不可省略步骤 |
|-----|---------|-----------|
| 物种谱 | FASTQ → QC → 宿主去除 → MetaPhlAn | 数据库版本固定 |
| 大样本分类 | FASTQ → QC → Kraken2 → Bracken | Bracken 校正、数据库记录 |
| 功能谱 | FASTQ → MetaPhlAn → HUMAnN | MetaPhlAn/HUMAnN 数据库匹配 |
| MAG | FASTQ → QC → assembly → binning → CheckM2 → GTDB-Tk | 深度判断、质控阈值 |
| ARG | FASTQ/contigs → RGI/CARD | 版本、阈值、归一化 |
| VF | contigs/genes → Diamond → VFDB | identity/coverage/e-value |
| 病毒 | FASTQ → assembly → viral detection → abundance | contig 长度过滤 |

### 3.2 输入输出格式转换地图

| 工具链 | 输入 | 输出 | 下游 |
|-------|------|------|-----|
| KneadData | raw FASTQ | clean FASTQ | MetaPhlAn/Kraken/HUMAnN/assembly |
| MetaPhlAn | clean FASTQ / bowtie2out | taxa profile TSV | 物种差异、HUMAnN |
| Kraken2 | clean FASTQ | kraken report | Bracken |
| Bracken | kraken report | species/genus abundance | 物种差异 |
| HUMAnN | clean FASTQ + MetaPhlAn profile | gene family, pathway abundance/coverage | 功能差异 |
| MEGAHIT/metaSPAdes | clean FASTQ | contigs fasta | binning/gene prediction |
| Bowtie2 回贴 | reads + contigs | depth table | MetaBAT2/SemiBin2 |
| MetaBAT2/SemiBin2 | contigs + depth | bin fasta | CheckM2 |
| CheckM2 | bin fasta | completeness/contamination table | MAG 过滤 |
| GTDB-Tk | filtered MAG fasta | taxonomy table | MAG 解释 |
| Prokka | contigs/MAG | GFF, FAA, FFN | eggNOG/ARG/VF |
| eggNOG-mapper | protein FAA | COG/KEGG/GO table | 功能解释 |
| RGI | contigs/proteins | ARG table | ARG 丰度/统计 |
| Diamond + VFDB | proteins/genes | VF table | VF 丰度/统计 |
| VIBRANT/geNomad | contigs | viral contigs | host prediction/abundance |

### 3.3 质量审查清单（投稿前）

- [ ] clean reads 保留率 ≥70%；宿主污染比例已记录
- [ ] MetaPhlAn/Kraken2 数据库版本一致（跨批次同版本）
- [ ] HUMAnN mapping rate 已报告；pathway coverage 低者保守解释
- [ ] MAG completeness ≥50%，contamination ≤5%；阈值写入 Methods
- [ ] GTDB-Tk release 版本已记录（跨 release 分类名称可能不同）
- [ ] ARG/VF identity/coverage/e-value 阈值已明确
- [ ] ARG/VF 结果已注明不等同于表型耐药或致病性
- [ ] 未将 MetaPhlAn 与 Kraken2/Bracken 丰度表直接混用
- [ ] 数据库版本不同的批次未直接合并比较
- [ ] 低深度数据未强行做 MAG 或 strain-level 主要结论
- [ ] compositional data 已使用 MaAsLin2/ALDEx2/ANCOM-BC 等适配方法

### 3.4 Methods 写作骨架（按用到的模块填写）

```
Raw metagenomic reads were quality-controlled using [QC tool, version] with [key parameters].
Host-derived reads were removed by aligning against [reference genome, version] using [aligner, version].

Taxonomic profiling was performed using [MetaPhlAn/Kraken2+Bracken, version] with the [database, version, date].
Species-level relative abundance tables were generated for downstream ecological and statistical analyses.

Functional profiling was performed using [HUMAnN, version] with [ChocoPhlAn/UniRef, database version].
Pathway abundance tables were normalized using [normalization method].

For assembly-based analyses, reads were assembled using [MEGAHIT/metaSPAdes, version] with [parameters].
Open reading frames were predicted using [Prokka/Prodigal, version], and functional annotation was performed
using [eggNOG-mapper, version] against [database, version].

Metagenome-assembled genomes were recovered using [binning tools, versions] based on contig coverage
and sequence composition. MAG quality was assessed using CheckM2 [version]; bins with completeness
≥[X]% and contamination ≤[Y]% were retained. Taxonomic classification was performed using
GTDB-Tk [version] with GTDB [release].

Antimicrobial resistance genes were identified using RGI [version] against CARD [version, date].
Virulence factor candidates were identified using Diamond [version] against VFDB [version, date];
hits were retained with identity ≥X%, coverage ≥Y%, and e-value ≤Z.

Viral contigs were identified using [VIBRANT/geNomad, version] and filtered by [length/score criteria].
Abundance was estimated by mapping clean reads to viral contigs using [aligner, version].
```

### 3.5 输出末尾 Skill 调用检查

宏基因组方案输出末尾应根据当前任务给出 Skill 调用建议，推荐格式：

```
## Skill 调用建议

- [ ] 若需要复现参考文章流程：调用 `/microbiome-article-analysis PMID`
- [ ] 若 MAG 分箱完成后需审查 dRep/ANI/分类策略：调用 `/microbiome-stats`
- [ ] 若已获得 species/pathway/MAG/ARG/VF abundance table：调用 `/microbiome-bioinformatics`
- [ ] 若需要系统发育树、MAG 分布图或出版级图件：调用 `/microbiome-figure`
```

MAG/新菌种发现场景必须包含：

- [ ] 现在可调用 `/microbiome-article-analysis 34614189` 或知识图谱推荐 PMID，获取参考代码和 Methods。
- [ ] dRep 和 ANI 聚类方案确定后，可调用 `/microbiome-stats` 审查阈值和统计设计。
- [ ] 获得 MAG abundance table 后，调用 `/microbiome-bioinformatics` 做差异丰度、关联模型或协变量校正。
- [ ] 最终展示新菌种系统发育和功能潜力时，调用 `/microbiome-figure` 设计图件。

---

## 调用关系

| 触发条件 | 调用 Skill | 本 Skill 交付物 |
|---------|-----------|----------------|
| α/β 多样性、PERMANOVA、MaAsLin2、LEfSe | `/microbiome-bioinformatics` | species/pathway/ARG/VF abundance table |
| MAG abundance 差异、MAG 与表型关联、协变量校正 | `/microbiome-bioinformatics` | MAG abundance table、metadata、MAG taxonomy |
| MAG dRep/ANI 阈值审查、聚类统计策略 | `/microbiome-stats` | ANI matrix、dRep cluster table、MAG quality table |
| 文章流程复现、Methods 对齐 | `/microbiome-article-analysis` | 工具链、参数、数据库清单 |
| 菌种-疾病-通路机制解释 | `/microbiome-article-analysis` | 候选菌、功能、通路、MAG 信息 |
| 图件设计与出版级可视化 | `/microbiome-figure` | 整理后的 abundance/result table |
