---
name: microbiome-annotation
description: 临床元数据规范化，帮助研究者在采集前设计标准化的元数据字段、样本采集SOP和格式规范，避免后期分析被卡死
---


# microbiome-annotation：临床元数据规范化

## 快速调用示例

```
/microbiome-annotation 横断面 IBD 宏基因组
/microbiome-annotation FMT 难辨梭菌感染/IBD 16S+宏基因组
/microbiome-annotation 纵向干预 2型糖尿病益生菌RCT 16S
/microbiome-annotation 多部位 口腔-肠道轴 16S
/microbiome-annotation 纵向 新生儿队列 16S
```

## 职责与边界

**本技能负责**：元数据字段设计、采集 SOP 规范、文件格式标准、数据库提交字段映射。

**不负责**（由其他技能处理）：
- 论文 Methods 部分的 STORMS/MIMARKS 合规核查 → `/microbiome-reporting`
- 元数据设计完成后的分析方案 → `/microbiome-design`

**执行顺序**：收到用户描述后，**先执行步骤 0（RAG 文献参考检索）**，再按 Layer 1→2→3→4 逐层输出，最后给出可直接复制的 TSV 示例表头。

---

## 步骤 0 — RAG 文献参考字段提取（优先执行）

在给出字段清单之前，先从本地知识库检索同类高分文献的元数据设计，提取「非常规但关键」的字段。

```bash
# 检索同疾病的已精读文献
cat knowledge/concepts/topics/{disease_topic}.md 2>/dev/null | head -80
grep -l "{disease}" knowledge/concepts/entries/*.md 2>/dev/null | head -8
```

对每篇检索到的精读文章，从其 **M2（数据与队列）** 和 **M3（实验设计）** 部分提取：

1. **质控变量**：文章做了哪些关键亚组分析（活动期/缓解期、用药分层、疗效分组），对应需要提前收集哪些字段
2. **感兴趣变量**：文章中的核心结局指标和中间变量（如粪便钙卫蛋白、SCFA浓度、菌株定植率），如果你的研究也想做类似比较，就需要提前采集
3. **对比标准**：文章用了哪些临床标准检测作为对照（如 calprotectin vs 菌群模型），可以参考纳入

**输出格式**：在 Layer 3 之前，先输出一个「文献参考字段」表格：

```
### 参考文献 [PMID XXXXX]（期刊，年份）的元数据设计经验

| 字段名 | 文章用途 | 你的研究是否需要 | 原因 |
|-------|---------|--------------|------|
| calprotectin | 与菌群模型头对头比较 | 建议采集 | 能直接证明菌群诊断优于临床标准 |
| mayo_score（分层）| 活动期/缓解期亚组分析 | 必须采集 | 不收集则无法回应「菌群差异只是炎症副产品」的质疑 |
```

若知识库中有可执行代码节点（`knowledge/papers/`）与该研究类型匹配，同时提示用户调用 `/microbiome-article-analysis <PMID>` 获取分析代码。

---

## Layer 1 — 通用必填字段（所有研究）

基于 MIMARKS v6.0、STORMS（*Nature Medicine* 2021）及 2025 年 PMC 临床菌群标准化研究。

### 1.1 宿主基础信息

| 字段名（英文） | 类型 | 格式示例 | 为何必须 | 引用来源 |
|------------|------|---------|---------|---------|
| `sample_id` | 字符串 | `S001` | 唯一标识符，无空格无特殊字符 | MIMARKS v6 |
| `host_age` | 连续数值 | `45` | 菌群随年龄显著变化，是最强混杂之一 | MIMARKS v6 |
| `host_sex` | 分类 | `male/female` | 激素水平影响菌群组成 | STORMS |
| `host_body_mass_index` | 连续数值 | `24.5` | 肥胖独立影响菌群，是混杂非结局 | MIMARKS v6 |
| `host_height` | 连续数值（cm） | `170` | 用于计算 BMI 验证 | MIMARKS v6 |
| `host_weight` | 连续数值（kg） | `68` | 用于计算 BMI 验证 | MIMARKS v6 |
| `diagnosis` | 字符串（ICD-10） | `K50.0` | 疾病诊断标准化，便于多中心合并 | STORMS |
| `disease_duration_months` | 连续数值 | `24` | 病程是独立的菌群影响因素 | STORMS |
| `disease_severity` | 字符串 | `mild/moderate/severe` | 活动度影响菌群，如 Mayo/CDAI 评分 | STORMS |
| `group` | 分类 | `Case/Control` | 主分组变量，必须用英文 | QIIME2 规范 |

### 1.2 关键混杂因素（菌群研究专用）

| 字段名 | 类型 | 格式示例 | 说明 |
|-------|------|---------|------|
| `antibiotic_use_1m` | 布尔 | `yes/no` | 近 1 个月内使用抗生素 |
| `antibiotic_use_3m` | 布尔 | `yes/no` | 近 3 个月内使用（建议作为主要排除标准时间窗）|
| `antibiotic_type` | 字符串 | `amoxicillin` | 具体品种，影响菌群方向不同 |
| `probiotic_use_1m` | 布尔 | `yes/no` | 近 1 个月内使用益生菌/益生元 |
| `diet_record` | 字符串 | `FFQ/3day_diary/none` | 饮食是菌群最强决定因素，记录方式需说明 |
| `bristol_stool_type` | 整数（1-7） | `4` | Bristol 粪便分型，影响采集质量和菌群组成 |
| `smoking_status` | 分类 | `never/former/current` | 吸烟显著影响肠道菌群 |
| `alcohol_frequency` | 分类 | `none/occasional/regular` | 饮酒影响菌群多样性 |
| `gi_surgery_history` | 布尔 | `yes/no` | 消化道手术（胆囊/阑尾切除）强烈影响菌群 |

### 1.3 样本采集操作信息

| 字段名 | 类型 | 格式示例 | 依据 |
|-------|------|---------|------|
| `collection_date` | 日期 | `2024-03-15` | MIMARKS v6 必填 |
| `collection_time` | 时间 | `08:30` | 菌群存在昼夜节律变化 |
| `time_to_freeze_min` | 整数（分钟） | `10` | **≤ 15 分钟冷冻**是保证样本稳定性的关键阈值（参考：Gorzelak et al. 2015, *Microbiome*；Vogtmann et al. 2017, *BMC Microbiology*）|
| `storage_condition` | 字符串 | `-80C/RNAlater` | 影响 DNA 质量 |
| `freeze_thaw_cycles` | 整数 | `0` | 应为 0，多次冻融显著改变菌群组成 |
| `dna_extraction_kit` | 字符串 | `MagBead_PowerSoil` | STORMS 必填，影响提取效率 |
| `collection_method` | 字符串 | `self_collected/clinical` | 不同采集方式菌群差异约 2-5% |

---

## Layer 2 — 研究类型专属字段

根据用户的研究类型，**在 Layer 1 基础上额外添加**以下字段。

### 2A：标准横断面 / 纵向队列

| 字段名 | 类型 | 说明 |
|-------|------|------|
| `timepoint` | 字符串 | 纵向研究必填：`T0/T1/T2`，明确时间节点 |
| `days_from_baseline` | 整数 | 相对基线的天数，便于计算时间间隔 |
| `medication_name` | 字符串 | 所有当前用药（详细）|
| `medication_dose` | 字符串 | 剂量（mg/day）|

### 2B：FMT 研究（供体+受体双表）

**受体表（在 Layer 1 基础上添加）**：

| 字段名 | 类型 | 说明 |
|-------|------|------|
| `fmt_timepoint` | 字符串 | `pre_FMT/post_FMT_1w/post_FMT_1m/post_FMT_3m` |
| `fmt_route` | 字符串 | `colonoscopy/enema/capsule/NG_tube` |
| `fmt_dose_g` | 数值 | 供体粪便克数 |
| `donor_id` | 字符串 | 与供体表关联的唯一 ID |
| `pre_treatment` | 字符串 | 预处理方案（万古霉素/利福昔明/无）|
| `clinical_outcome` | 字符串 | `remission/response/no_response` |

**供体表（独立记录）**：

| 字段名 | 类型 | 说明 |
|-------|------|------|
| `donor_id` | 字符串 | 与受体表关联 |
| `donor_age` | 数值 | 供体年龄 |
| `donor_sex` | 分类 | 供体性别 |
| `donor_bmi` | 数值 | 供体 BMI |
| `donor_antibiotic_6m` | 布尔 | 近 6 个月抗生素使用 |
| `donor_screening_pass` | 布尔 | 是否通过标准供体筛查 |
| `stool_bank_id` | 字符串 | 若来自粪便库，记录批次 |

### 2C：多部位采样

根据采样部位，补充以下**部位特异性字段**：

| 采样部位 | 额外必填字段 | 说明 |
|---------|-----------|------|
| **口腔** | `oral_collection_method`（漱口水/牙龈下刮取/唾液）| 方法不同，菌群差异 >10% |
| **口腔** | `last_meal_time_h`（最近进食距采集时间，小时）| 建议空腹 2 小时以上 |
| **阴道** | `menstrual_phase`（follicular/luteal/menstruation）| 月经周期显著影响阴道菌群 |
| **阴道** | `sexual_activity_72h`（布尔）| 影响阴道菌群组成 |
| **皮肤** | `skin_site`（forehead/arm/back）| 不同部位菌群差异极大 |
| **皮肤** | `cleanser_use_24h`（布尔）| 清洁剂使用影响皮肤菌群 |
| **呼吸道** | `collection_type`（BAL/sputum/nasal_swab）| 操作方式不同不可比 |

### 2D：干预研究（RCT / 益生菌 / 饮食干预）

| 字段名 | 类型 | 说明 |
|-------|------|------|
| `intervention_arm` | 字符串 | `treatment/placebo` |
| `intervention_dose` | 字符串 | 干预剂量（CFU/g/dose）|
| `intervention_duration_days` | 整数 | 干预持续天数 |
| `compliance_rate` | 小数（0-1） | 依从率（通过日记/剩余量计算）|
| `washout_completed` | 布尔 | 是否完成洗脱期（纵向研究）|
| `adverse_event` | 字符串 | 不良事件记录 |

---

## Layer 3 — 疾病特异性必收字段

在 Layer 1+2 基础上，根据疾病添加以下关键字段：

| 疾病 | 必收字段 | 为何关键 |
|------|---------|---------|
| **IBD** | `disease_subtype`（UC/CD/IBDU）| 两种亚型菌群特征不同 |
| | `mayo_score` 或 `cdai_score` | 疾病活动度是独立混杂变量 |
| | `medication_class`（5ASA/immunosuppressant/biologic）| 不同药物对菌群影响方向不同 |
| | `colonoscopy_date`（最近肠镜距采样天数）| 肠镜准备方案显著清除菌群 |
| **T2DM/代谢** | `hba1c` | 血糖控制水平 |
| | `fasting_glucose` | 空腹血糖 |
| | `metformin_use`（yes/no/dose）| **最关键**：二甲双胍通过菌群发挥降糖作用 |
| | `diabetes_duration_years` | 病程影响菌群累积改变 |
| **肝病** | `child_pugh_score`（A/B/C）| 肝功能分级 |
| | `ascites`（yes/no）| 腹水改变肠道通透性 |
| | `ppi_use`（yes/no）| 质子泵抑制剂显著改变菌群 |
| | `etiology`（NAFLD/alcoholic/viral）| 病因不同，菌群特征不同 |
| **肿瘤/免疫治疗** | `chemotherapy_regimen` | 化疗方案影响菌群 |
| | `immunotherapy_agent`（PD-1/PD-L1/CTLA-4）| 免疫治疗疗效与菌群强关联 |
| | `antibiotic_3m`（再次确认）| 肿瘤患者抗生素使用频繁 |
| **新生儿/发育** | `gestational_age_weeks` | 早产/足月显著影响菌群定植 |
| | `delivery_mode`（vaginal/cesarean）| 分娩方式是新生儿菌群最强决定因素 |
| | `feeding_type`（breast/formula/mixed）| 喂养方式影响早期菌群建立 |
| | `maternal_id` | 与母亲样本关联 |
| **神经退行性** | `cognitive_score`（MMSE/MoCA）| 认知评分作为结局变量 |
| | `constipation_duration_years` | 便秘是帕金森前驱症状，影响菌群 |

---

## Layer 4 — 格式规范与提交准备

### 4.1 TSV 示例表头（可直接复制）

**基础横断面研究**（粘贴到 Excel 第一行，另存为 .tsv）：

```
sample_id	group	host_age	host_sex	host_body_mass_index	diagnosis	antibiotic_use_3m	probiotic_use_1m	bristol_stool_type	collection_date	time_to_freeze_min	storage_condition	freeze_thaw_cycles	medication_name	smoking_status
S001	Case	45	male	24.5	K50.0	no	no	4	2024-03-15	10	-80C	0	mesalazine	never
S002	Control	43	female	22.1	healthy	no	no	4	2024-03-15	12	-80C	0	none	never
```

**FMT 研究（受体）**：

```
sample_id	subject_id	donor_id	fmt_timepoint	fmt_route	fmt_dose_g	clinical_outcome	host_age	host_sex	pre_treatment	collection_date	time_to_freeze_min
R001_T0	R001	D003	pre_FMT	colonoscopy	NA	NA	35	female	vancomycin	2024-03-01	8
R001_T1	R001	D003	post_FMT_1m	colonoscopy	50	remission	35	female	vancomycin	2024-04-01	10
```

### 4.2 常见格式错误（直接导致下游报错）

| 错误 | 后果 | 正确做法 |
|------|------|---------|
| 分组名用中文（病例/对照） | QIIME2 / R 脚本报错 | 改为 `Case/Control` |
| 连续变量存为字符（`"45岁"`）| 无法纳入线性模型 | 只存数值：`45` |
| 缺失值用空格或 `-` | 读取时被误判为有效值 | 统一用 `NA` |
| 样本 ID 含空格（`S 001`）| 文件匹配失败 | 用下划线：`S001` 或 `S_001` |
| 日期格式不统一（`03/15/2024`）| 排序错误，跨平台读取失败 | 统一用 `YYYY-MM-DD` |
| 同一变量多种写法（`Yes/yes/YES`）| 分类时产生多个级别 | 统一小写英文 |
| 表头有中文括号或空格 | 列名读取失败 | 字母+下划线，如 `host_age` |

### 4.3 NCBI/GSA 提交对应字段

以下 Layer 1 字段与数据库提交直接对应，确保采集时就记录正确格式：

| 本技能字段名 | NCBI BioSample 字段 | GSA 字段 | 备注 |
|-----------|-------------------|---------|------|
| `host_age` | `host_age` | `host_age` | 必填 |
| `host_sex` | `host_sex` | `host_sex` | 必填 |
| `collection_date` | `collection_date` | `collection_date` | YYYY-MM-DD 格式 |
| `geo_loc_name` | `geo_loc_name` | `region` | 省/市级别 |
| `diagnosis` | `host_disease` | `host_phenotype` | 建议 ICD-10 代码 |
| `dna_extraction_kit` | `nucl_acid_ext` | `extraction_method` | STORMS 必填 |

---

## 调用关系

- 采集前先确认研究设计 → `/microbiome-design`（获取分析需要哪些字段的输入）
- 投稿前 STORMS/MIMARKS 合规核查 → `/microbiome-reporting`
- 数据提交至 NCBI/GSA → `/microbiome-data`
- 下游分析开始 → `/microbiome-bioinformatics`
