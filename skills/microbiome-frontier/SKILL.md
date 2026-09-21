---
name: microbiome-frontier
description: 分析肠道菌群研究前沿方向、识别创新点、评估顶刊发表规律，帮助研究者找到数据的高水平发表路径
---

# microbiome-frontier：前沿方向与创新点分析

## 快速调用示例

```
/microbiome-frontier 宏基因组 IBD患者120例(60UC+60CD) vs 60HC 横断面
/microbiome-frontier 16S 2型糖尿病80例 vs 80对照 病例对照
/microbiome-frontier 多组学(宏基因组+代谢组) 肝硬化纵向队列30例×3时间点
/microbiome-frontier 宏基因组 新生儿队列200例 追踪至1岁 纵向设计
```

## 执行流程

收到用户数据描述后，**严格按两阶段顺序执行**，不得跳过 Phase 1 直接给出结论。

---

## Phase 1：实时文献扫描

**目标**：获取当前领域真实的最新研究动态，而非依赖静态知识。两路并行：Phase 1A 查学术数据库（精确），Phase 1B 查搜索引擎（补充最新动态）。

### 步骤 1.0 — 创建扫描任务

用 TaskCreate 创建以下追踪任务：
- `Phase 1A: PubMed 直查近2年高分文章`
- `Phase 1B: WebSearch 趋势与热点扫描`
- `Phase 2: 综合分析与方向决策`

### Phase 1A — PubMed 直查（学术数据库，精确来源）

根据用户描述，构造并执行以下 3 组 PubMed 检索（将 `[DISEASE]` 替换为用户实际疾病/表型）：

**查询 A — 近期高分研究（竞争水位）**
```bash
export http_proxy=VPN本地代理 && export https_proxy=VPN本地代理
bash .claude/commands/microbiome-frontier/scripts/pubmed_search.sh "[DISEASE] gut microbiome metagenomics" 10 2023
```

**查询 B — 新兴热点方向**
```bash
export http_proxy=VPN本地代理 && export https_proxy=VPN本地代理
bash .claude/commands/microbiome-frontier/scripts/pubmed_search.sh "[DISEASE] microbiome phageome strain-level spatial multi-omics" 8 2023
```

**查询 C — 对标数据规模研究**
```bash
export http_proxy=VPN本地代理 && export https_proxy=VPN本地代理
bash .claude/commands/microbiome-frontier/scripts/pubmed_search.sh "[DISEASE] microbiome cohort longitudinal intervention" 8 2023
```

从 PubMed 结果中提取：
- 顶刊（Nature/Cell/Gut 系列）近 2 年同类文章的数据规模和设计类型
- 哪些期刊在密集发表该方向文章（判断竞争强度）
- 是否有与用户数据规模接近的已发表研究

更新 `Phase 1A` 任务为 completed。

### Phase 1B — WebSearch 补充（最新预印本、趋势动态）

发出以下 2 组 WebSearch（补充 PubMed 未收录的最新预印本和评论）：

**组 D — 领域趋势综述**
```
[DISEASE] gut microbiome research trends saturated emerging directions review 2024 2025
```

**组 E — 新兴热点（预印本/新技术）**
```
gut microbiome [DISEASE] foundation model spatial virome 2025 2026 bioRxiv
```

更新 `Phase 1B` 任务为 completed。

### 步骤 1.3 — 分级筛选

读取 `references/microbiome-profile.md`，对所有检索结果按三级分类：
- **core**：与用户数据直接相关
- **proxy**：方法或疾病背景相邻，有参考价值
- **noise**：无关，丢弃

---

## Phase 2：基于调研的决策分析

**目标**：结合 Phase 1 的真实检索结果 + 内置领域知识，给出精准的方向判断。

按以下结构输出，每个板块都必须引用 Phase 1 的具体发现（不得凭空生成）：

### 1. 数据定性

直接判断用户数据能支撑的研究层次（顶刊 / 主流高分 / 稳妥发表）。

说明依据：Phase 1 中同类规模的文章发表在什么级别期刊？用户数据与当前竞争水位的差距在哪里？

### 2. 方向匹配（2-3 个）

每个方向包含：
- **为什么适合**：结合 Phase 1 扫描到的研究空白或热点
- **分析核心**：具体要做什么分析（不泛化）
- **竞争强度**：Phase 1 发现该方向近 2 年已有多少同类文章（高/中/低）
- **对标文献类型**：给出搜索关键词，让用户自行核实

### 3. 期刊定位

根据 Phase 1 的竞争水位 + 用户数据规模，给出：
- **保底**：当前数据可稳定发表的期刊
- **合理目标**：需要做扎实但有机会的期刊
- **冲刺**：需要补充什么条件才能达到

### 4. 升级路径

| 追加内容 | 期刊档次提升 | Phase 1 依据 |
|---------|------------|-------------|
| （基于扫描结果填写） | | |

### 5. 基金选题视角（仅当用户明确提到基金时输出）

如何将现有数据作为"预实验支撑"，重点是科学假说的原创性而非数据体量。

---

## 内置背景知识（Phase 1 检索不足时的兜底）

仅当 WebSearch 返回结果稀少或质量低时，参考以下静态知识补充：

### 持续强势方向
- 功能解构 + 机制闭环，单纯 16S 差异分析不足以支撑高分期刊
- 菌株级分辨率（strain-level）成为宏基因组文章标配
- 因果推断：MR + 无菌动物 / 抗生素清除 + FMT 三者缺一顶刊通常不接受
- 大队列（> 500）+ 独立多中心验证是 *Gut* / *Nature Medicine* 基本门槛

### 2024-2026 新兴热点
- **噬菌体组（phageome）**：virome 富集 + 长读长测序，*Nature Communications* 已有多篇
- **菌群-中枢神经轴（gut-brain axis）**：AD/PD 方向快速增长，机制要求高
- **菌群基础模型（foundation model）**：MGFM、MGM、Waypoint 系列，NeurIPS 2024 已发表
- **空间微生物组**：与空间转录组联用，方法难度高但创新性强

### 已饱和方向
- 单纯 16S 差异物种堆砌，无机制验证、无外部队列
- 常见疾病简单横断面菌群描述（肥胖、T2DM、IBD 均已饱和）
- 无外部独立验证集的单队列机器学习诊断模型
- 仅描述多样性变化无功能注释的宏基因组研究

---

## 调用关系
- 找对标文献详情 → `/microbiome-search`
- 精读某篇代表性文章 → `/microbiome-paper-reader`
- 确定方向后细化设计 → `/microbiome-design`
- 用于基金申请 → `/microbiome-grant`


> 调用参数提示：<数据类型(16S/宏基因组/多组学)> <疾病/表型> <样本量和设计>

> 建议授权工具：Bash, WebSearch, WebFetch, TaskCreate, TaskUpdate, Read

# microbiome-frontier：前沿方向与创新点分析

## 快速调用示例

```
/microbiome-frontier 宏基因组 IBD患者120例(60UC+60CD) vs 60HC 横断面
/microbiome-frontier 16S 2型糖尿病80例 vs 80对照 病例对照
/microbiome-frontier 多组学(宏基因组+代谢组) 肝硬化纵向队列30例×3时间点
/microbiome-frontier 宏基因组 新生儿队列200例 追踪至1岁 纵向设计
```

## 执行流程

收到用户数据描述后，**严格按两阶段顺序执行**，不得跳过 Phase 1 直接给出结论。

---

## Phase 1：实时文献扫描

**目标**：获取当前领域真实的最新研究动态，而非依赖静态知识。两路并行：Phase 1A 查学术数据库（精确），Phase 1B 查搜索引擎（补充最新动态）。

### 步骤 1.0 — 创建扫描任务

用 TaskCreate 创建以下追踪任务：
- `Phase 1A: PubMed 直查近2年高分文章`
- `Phase 1B: WebSearch 趋势与热点扫描`
- `Phase 2: 综合分析与方向决策`

### Phase 1A — PubMed 直查（学术数据库，精确来源）

根据用户描述，构造并执行以下 3 组 PubMed 检索（将 `[DISEASE]` 替换为用户实际疾病/表型）：

**查询 A — 近期高分研究（竞争水位）**
```bash
export http_proxy=VPN本地代理 && export https_proxy=VPN本地代理
bash .claude/commands/microbiome-frontier/scripts/pubmed_search.sh "[DISEASE] gut microbiome metagenomics" 10 2023
```

**查询 B — 新兴热点方向**
```bash
export http_proxy=VPN本地代理 && export https_proxy=VPN本地代理
bash .claude/commands/microbiome-frontier/scripts/pubmed_search.sh "[DISEASE] microbiome phageome strain-level spatial multi-omics" 8 2023
```

**查询 C — 对标数据规模研究**
```bash
export http_proxy=VPN本地代理 && export https_proxy=VPN本地代理
bash .claude/commands/microbiome-frontier/scripts/pubmed_search.sh "[DISEASE] microbiome cohort longitudinal intervention" 8 2023
```

从 PubMed 结果中提取：
- 顶刊（Nature/Cell/Gut 系列）近 2 年同类文章的数据规模和设计类型
- 哪些期刊在密集发表该方向文章（判断竞争强度）
- 是否有与用户数据规模接近的已发表研究

更新 `Phase 1A` 任务为 completed。

### Phase 1B — WebSearch 补充（最新预印本、趋势动态）

发出以下 2 组 WebSearch（补充 PubMed 未收录的最新预印本和评论）：

**组 D — 领域趋势综述**
```
[DISEASE] gut microbiome research trends saturated emerging directions review 2024 2025
```

**组 E — 新兴热点（预印本/新技术）**
```
gut microbiome [DISEASE] foundation model spatial virome 2025 2026 bioRxiv
```

更新 `Phase 1B` 任务为 completed。

### 步骤 1.3 — 分级筛选

读取 `references/microbiome-profile.md`，对所有检索结果按三级分类：
- **core**：与用户数据直接相关
- **proxy**：方法或疾病背景相邻，有参考价值
- **noise**：无关，丢弃

---

## Phase 2：基于调研的决策分析

**目标**：结合 Phase 1 的真实检索结果 + 内置领域知识，给出精准的方向判断。

按以下结构输出，每个板块都必须引用 Phase 1 的具体发现（不得凭空生成）：

### 1. 数据定性

直接判断用户数据能支撑的研究层次（顶刊 / 主流高分 / 稳妥发表）。

说明依据：Phase 1 中同类规模的文章发表在什么级别期刊？用户数据与当前竞争水位的差距在哪里？

### 2. 方向匹配（2-3 个）

每个方向包含：
- **为什么适合**：结合 Phase 1 扫描到的研究空白或热点
- **分析核心**：具体要做什么分析（不泛化）
- **竞争强度**：Phase 1 发现该方向近 2 年已有多少同类文章（高/中/低）
- **对标文献类型**：给出搜索关键词，让用户自行核实

### 3. 期刊定位

根据 Phase 1 的竞争水位 + 用户数据规模，给出：
- **保底**：当前数据可稳定发表的期刊
- **合理目标**：需要做扎实但有机会的期刊
- **冲刺**：需要补充什么条件才能达到

### 4. 升级路径

| 追加内容 | 期刊档次提升 | Phase 1 依据 |
|---------|------------|-------------|
| （基于扫描结果填写） | | |

### 5. 基金选题视角（仅当用户明确提到基金时输出）

如何将现有数据作为"预实验支撑"，重点是科学假说的原创性而非数据体量。

---

## 内置背景知识（Phase 1 检索不足时的兜底）

仅当 WebSearch 返回结果稀少或质量低时，参考以下静态知识补充：

### 持续强势方向
- 功能解构 + 机制闭环，单纯 16S 差异分析不足以支撑高分期刊
- 菌株级分辨率（strain-level）成为宏基因组文章标配
- 因果推断：MR + 无菌动物 / 抗生素清除 + FMT 三者缺一顶刊通常不接受
- 大队列（> 500）+ 独立多中心验证是 *Gut* / *Nature Medicine* 基本门槛

### 2024-2026 新兴热点
- **噬菌体组（phageome）**：virome 富集 + 长读长测序，*Nature Communications* 已有多篇
- **菌群-中枢神经轴（gut-brain axis）**：AD/PD 方向快速增长，机制要求高
- **菌群基础模型（foundation model）**：MGFM、MGM、Waypoint 系列，NeurIPS 2024 已发表
- **空间微生物组**：与空间转录组联用，方法难度高但创新性强

### 已饱和方向
- 单纯 16S 差异物种堆砌，无机制验证、无外部队列
- 常见疾病简单横断面菌群描述（肥胖、T2DM、IBD 均已饱和）
- 无外部独立验证集的单队列机器学习诊断模型
- 仅描述多样性变化无功能注释的宏基因组研究

---

## 调用关系
- 找对标文献详情 → `/microbiome-search`
- 精读某篇代表性文章 → `/microbiome-paper-reader`
- 确定方向后细化设计 → `/microbiome-design`
- 用于基金申请 → `/microbiome-grant`
