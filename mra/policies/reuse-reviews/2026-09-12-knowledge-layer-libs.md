# Reuse Review 2026-09-12：知识层部署选型（FTS/中文分词/版本化/Agent 检索）

> 第五份 Reuse Review。执行：Gemini 联网检索评估，Kimi K3 复核（jieba 0.42.1 MIT 经 PyPI 核实）。

## 1. 功能问题与检索记录

- **问题**：知识层（本地单机、治理优先、中文内容、SQLite+FTS5、Agent 只读渐进检索）部署前做复用审查——自写 FTS5+自写 CJK 分词是否重复造轮子？是否有更成熟开源方案应替换？
- **检索**：2026-09-12，固定候选（sqlite-vec/sqlite-vss、Chroma、FAISS、txtai、whoosh、tantivy-py、lunr、TinyDB、jieba/pkuseg/THULAC/HanLP、Anthropic agent skills/Contextual RAG 模式）。

## 2. 候选核实表

| 候选 | 结论 |
|---|---|
| sqlite-vec / sqlite-vss | 向量检索（非全文），MIT/Apache；语义搜索可后置引入，不替代 FTS5 |
| Chroma / FAISS | 重量级向量库，不符合轻量单机定位 |
| whoosh / lunr / TinyDB | 停更/性能弱/不支持高效 FTS，排除 |
| tantivy-py | 性能强但脱离 SQLite 引入第二套存储，治理复杂性↑，排除 |
| **jieba** | **0.42.1，MIT（PyPI 核实）**，`cut_for_search` 预分词 + FTS5 unicode61 是中文检索公认最佳实践 |
| pkuseg | MIT 但活跃度低；THULAC/HanLP 有商业/双许可限制，排除 |

## 3. 评估

- **存储架构**：Git YAML 为事实源 + SQLite 只读 FTS 索引 = 业界 Hybrid 最佳实践（Obsidian/Docusaurus 同款），保留，**不做**数据库版本历史表（Git 天然抗篡改+Diff 溯源）。
- **中文检索**：FTS5 默认 unicode61 对中文分词差；trigram 分词器无法检索 1-2 字条目；公认方案是 **Python 层 jieba.cut_for_search 预分词存入空格分隔文本**。
- **Agent 检索**：Anthropic 渐进加载（元数据先行、命中才取详情）+ Contextual RAG（检索结果带上下文前缀）防上下文污染与张冠李戴。

## 4. 最终决策

1. **保留自写 SQLite+FTS5** 架构（不引入 Chroma/Tantivy）。
2. **引入 jieba（MIT）** 替换自写 CJK 逐字分词（store.py 的 _index_text/_query_expression 改为 jieba.cut_for_search 预分词 + 参数化 FTS 查询）。
3. **不做**数据库版本历史表（Git 是事实源，与 5.5 一致）。
4. Agent 检索工具返回结果**带知识包上下文前缀**（参考 Contextual RAG）且不 dump 全文（现状已符合渐进加载）。

## 5. 借鉴清单

- jieba.cut_for_search 预分词模式（中文 FTS 标准做法）。
- Anthropic Contextual RAG：检索结果带 `[包名: title]` 前缀，提升 Agent 引用确信度。
- Git-as-source-of-truth 混合存储（版本化/审计免费获得）。

## 6. 风险与核实记录

- jieba 0.42.1、MIT 已由 Kimi K3 经 PyPI JSON 核实（2026-09-12）。
- sqlite-vec 等"活跃度"判断来自 Gemini 检索，未逐一深核版本号；因不引入，不影响决策。
- 中文检索质量需在 jieba 落地后与旧逐字分词做对比验收（部署验收项）。