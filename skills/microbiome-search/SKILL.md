---
name: microbiome-search
description: 检索菌群相关文献和公共数据库，整合本地知识库与在线资源，帮助研究者快速定位参考文献和公开数据集
---


# microbiome-search：文献与公共数据库检索

## 职责

两类检索：本地已入库文献 + 外部数据库（PubMed / 公共菌群数据库）。

## 检索路径

### 路径1：本地知识库检索
读取 `knowledge/papers/pmid_*.json`，按以下字段过滤：
- `analysis_types`：分析类型匹配
- `year`：年份范围
- `journal` / `tier`：期刊层级
- `plain_description`：关键词语义匹配

### 路径2：外部数据库检索（在线）
- **PubMed / PubTator3**：文献检索，输出 PMID + 摘要
- **GMrepo**：人肠道菌群表型关联数据
- **curatedMetagenomicData**：标准化的公开宏基因组数据集
- **HMP（人类微生物组计划）**：基线菌群参考数据
- **NCBI SRA / ENA**：原始测序数据

### 公共数据使用注意事项
- 确认测序平台、引物区段与自有数据兼容
- 区分"独立验证集"（不同队列）vs"背景数据"（无法做外部验证）
- 检查是否有配对的多组学数据（如宏基因组 + 代谢组）
- 警告 Data Leakage：不得将公开数据混入训练集后声称外部验证

## 回答格式

### 本地知识库结果
```
📄 [plain_description]
   期刊: XXX | 年份: XXXX | PMID: XXXXX
   分析类型: [analysis_types]
   代码: [有 → 可调用 microbiome-article-analysis]
```

### 外部数据库结果
```
🔍 数据集: [名称]
   来源: [数据库]
   样本量: [N]
   数据类型: [16S/宏基因组/代谢组]
   兼容性评估: [与用户数据的兼容分析]
```

## 调用关系
- 找到本地有代码的文献后，提示用户使用 `microbiome-article-analysis`
- 找到外部数据集后，引导 `microbiome-data` 处理数据格式和批次效应
