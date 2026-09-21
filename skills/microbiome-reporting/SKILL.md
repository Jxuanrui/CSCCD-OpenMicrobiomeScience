---
name: microbiome-reporting
description: STORMS、MIMARKS、MIxS 等微生物组学报告规范逐条核查，确保论文符合期刊和数据库提交要求
---


# microbiome-reporting：微生物组学报告规范审计

## 职责

按照国际微生物组学报告规范，逐条核查论文是否满足要求，给出具体的缺项补充建议。

## 主要规范

### STORMS（Strengthening The Organization and Reporting of Microbiome Studies）
适用：所有微生物组学研究论文

核心条目：
- **样本**：采集时间、保存条件、冻融次数、运输方式
- **宿主**：年龄、性别、BMI、疾病诊断标准、纳排标准
- **环境因素**：饮食记录、抗生素使用（近3个月）、益生菌使用
- **DNA提取**：提取试剂盒名称和批次、提取部位（粪便/黏膜活检）
- **测序**：平台、引物序列、扩增区段（V3-V4等）、测序深度
- **生信流程**：工具名称和版本、参数设置、数据库版本
- **统计**：多重检验矫正方法、协变量列表
- **数据共享**：原始数据提交数据库和登录号

### MIMARKS（Minimum Information about a MARKer gene Sequence）
适用：16S/ITS 扩增子研究

额外要求：
- 引物名称和序列（正向 + 反向）
- PCR 条件
- 测序文库构建方法

### MIxS（Minimum Information about any Sequence）
适用：宏基因组研究

额外要求：
- 测序仪型号
- 插入片段大小
- 宿主基因组去除方法

## 数据提交规范

### 国内数据库（推荐）
- **GSA（Genome Sequence Archive，NGDC）**：国内合规首选，审查快
- 提交前准备：BioProject + BioSample + 原始 FASTQ

### 国际数据库
- **NCBI SRA**：BioProject + BioSample + SRA submission
- **ENA**：欧洲数据库，部分欧洲期刊要求

## 回答格式
按规范条目逐一核查，标注：
- ✅ 已满足
- ❌ 缺失（给出补充建议）
- ⚠ 描述不清晰（给出标准表述示例）

## 调用关系
- 数据共享规范详情 → `microbiome-data`
- 综合投稿前审查 → `microbiome-prereview`
