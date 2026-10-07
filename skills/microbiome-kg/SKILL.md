---
name: microbiome-kg
description: 查询肠道菌群知识图谱（Gut Microbiome Knowledge Graph）：单菌跨域多跳关联（疾病/代谢物/基因/药物/通路）、证据等级与 PMID 回链、LightRAG 问答、RotatE 链接预测假设。当用户询问"某菌与什么疾病/代谢物相关""某病的菌群变化""图谱预测/假设生成"时使用。
---

# microbiome-kg：肠道菌群知识图谱查询技能

## 数据资产（项目内固定路径）

- 主图（serving v3）：`data/merged/candidate_v3/merged_nodes.tsv` 与 `merged_edges.tsv`（6,840 实体 + 985 断言 + 20,309 边，全边 4 列 provenance）。Neo4j 镜像运行于 bolt://127.0.0.1:17687，2026-10-06 已物化 candidate_v3（含 provenance 属性），与 TSV 同步；跨断言查询仍建议走 TSV
- 待审文献边（Tier C）：`data/merged/candidate_v3/pending_review_edges.tsv`（823 行，含逐字证据/judge 理由）
- 链接预测：`data/merged/candidate_v3/rotate_predictions.tsv`（RotatE v3，evidence=predicted，非实测证据）
- 图指标与社区：按需计算 `python3 src/05_analysis/graph_analysis.py metrics`（根目录旧 graph_metrics.json 停留在 v1，勿引用）
- 证据分级：A=策展实验数据；B=LLM 抽取且 ≥2 篇支持；C=单篇待审。predicted=模型假设。

## 用法（在 Knowledge_Graph 项目根目录执行）

### 1. 单菌跨域扇出查询（首选入口）

```bash
python3 src/05_analysis/graph_analysis.py query --microbe "Faecalibacterium prausnitzii" --hops 2 [--out /tmp/fanout.tsv]
```

支持菌名或 NCBITaxon ID。输出按 7 类节点分列，含每跳谓词链与边级证据（tier/PMIDs/置信度）。

### 2. 自然语言问答（需 VPN；用独立环境 `.venv-lightrag/bin/python` 避免 numpy/jax 冲突）

```bash
.venv-lightrag/bin/python src/06_qa/lightrag_qa.py ask "哪些微生物可以产生丁酸盐？"
```

英文检索召回最稳；中文会自动预翻译并以中文作答。**接地双路径**（2026-10-07 重建后）：实体型问题走确定性证据路径（问题匹配实体→TSV 拉 1 跳边→E 编号引用，PMID 保证真实）；非实体问题走向量检索（mix 模式）。证据不足时诚实拒答（优于编造）。回答引用的 PMID 均可用 `grep` 在 `candidate_v3/merged_edges.tsv` 回查。

### 3. 链接预测（假设生成）

```bash
head -50 data/merged/candidate_v3/rotate_predictions.tsv
```

或重新训练：`python3 src/05_analysis/rotate_embed.py`（自包含 RotatE，无需 PyKEEN）

### 4. 图指标/社区

```bash
python3 src/05_analysis/graph_analysis.py metrics --json data/logs/graph_metrics_v3.json
```

## 注意事项

- LLM 相关命令必须在沙箱外执行且需 `HTTPS_PROXY=VPN本地代理`；与批量任务共用 API key 时注意 429。
- 预测边只能作为假设线索引用，不得写成已证实结论；入图证据边引用时须带证据等级与 PMID。
- Tier C 边（pending_review）不得用于下游结论。
