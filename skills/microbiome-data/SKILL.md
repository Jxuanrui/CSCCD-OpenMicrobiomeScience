---
name: microbiome-data
description: 原始测序数据脱敏、FAIR 原则合规和公共数据库提交规范，支持 NCBI/GSA 提交流程
---


# microbiome-data：数据共享与 FAIR 合规

## 职责

帮助研究者在投稿前完成数据共享合规，处理数据脱敏、选择合适的数据库和完成提交流程。

## FAIR 原则核查

| 原则 | 要求 | 菌群研究实践 |
|------|------|------------|
| **F**indable | 有持久化唯一标识符 | BioProject accession（PRJNA/PRJCA）|
| **A**ccessible | 可通过标准协议访问 | NCBI/GSA 公开数据 |
| **I**nteroperable | 使用标准格式和术语 | MIxS/MIMARKS 元数据格式 |
| **R**eusable | 有清晰的使用许可 | CC BY 4.0 或 CC0 |

## 数据脱敏要求

在上传元数据前，必须去除：
- 姓名、身份证号、医院病历号
- 联系方式（手机、邮箱、地址）
- 精确出生日期（可保留年份）
- 精确居住地址（可保留省/市级别）

数据上传只包含：
- 原始 FASTQ 文件（测序数据，不含个人信息）
- 规范化元数据（样本特征，已脱敏）

## 提交流程

### NCBI SRA（国际）
1. 注册 NCBI 账户
2. 创建 BioProject（描述研究目的）
3. 创建 BioSample（每个样本一个，填 MIMARKS 字段）
4. 上传 FASTQ 文件（SRA Submission Portal）
5. 获得 accession 号，写入论文 Data Availability

### GSA（国内，NGDC）
1. 注册 NGDC 账户
2. 提交 BioProject + BioSample（字段与 NCBI 兼容）
3. 上传原始数据
4. 审核周期约 1-2 周，比 NCBI 快

## 数据可用性声明模板

```
The raw sequencing data generated in this study have been deposited in the 
[NCBI SRA / NGDC GSA] under accession number [PRJNA/CRA######].
The processed OTU/ASV table and metadata are available as Supplementary Data.
Analysis code is available at [GitHub URL].
```

## 调用关系
- 数据格式规范 → `microbiome-reporting`（MIxS/MIMARKS）
- 审稿前数据合规核查 → `microbiome-prereview`
