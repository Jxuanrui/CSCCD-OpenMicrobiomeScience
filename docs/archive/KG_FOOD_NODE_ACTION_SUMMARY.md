> **状态：已被 KG_LAYER_REQUIREMENTS.md 取代（2026-09-24）**——内容并入《Harness Architecture v1.0 + KG Layer Requirements》，本文保留作历史参考。

# KG / Food Node Action Summary（图谱侧执行摘要 · 2026-09-24）

> 源自《项目全链路运行审计报告·知识架构升级版》（Knowledge_Graph-mra 仓根目录）。
> 本页只保留图谱侧需要执行的内容；图谱侧使命是**完成 Local Knowledge 能力**，
> 不是吸收本轮 Food–Pathway–Phage 研究结果。

## 1. Food 节点缺口（最优先）

- 当前快照（2026-09-21：5,869 节点/19,001 边）六类节点中**无 Food 类**；
  需求单（Food 节点最优先）已在途，本页补充落地要求。
- 首批建议覆盖：atlas `food_node_hints` 所指 16 类（Fruit/Vegetable/Whole grain/
  Refined grain/Red Meat/Processed Meat/Poultry/Fish/Egg/Nuts/Soy/Legume/Dairy/
  Sugary…），与 cohort_config 的 food_exposure_table 对齐。

## 2. 实体 / 别名 / 标识要求

- 每个 Food 节点：标准名（英文规范词）+ 中文别名 + 稳定标识（建议
  `LFS:FOOD:<Slug>` 或对接 foodon/NCBI 既有前缀体系）。
- Microbe 侧维持 NCBITaxon；命名服务 kg_resolve 需能把口语食物名解析到节点。

## 3. provenance 要求（与现有 curated 口径一致）

- 每条 Food–Microbe 边必须带 `source_type`（curated/llm_extracted）+
  `evidence_tier`（A/B，Tier-C/predicted 维持拒入）+ `pmids`/`years`/`confidence`。
- merged TSV 模式与现有一致（mra 侧快照消费零改动）。

## 4. source registry 对接

- 新数据源接入 mra 时走 `SourceDescriptor` 注册（license 三层/transport/
  input/output schema/provenance 字段/人工审核）——图谱侧若新增 ETL 源，
  建议同款字段自描述，便于两侧 source 台账对账。

## 5. tier / confidence 表达

- 维持 A=curated 多源支持、B=llm_extracted 单源的两档；Food 边若来自
  LLM 抽取一律 B 档起步，多文献共证可升 A（经人工复核）。

## 6. Knowledge Router 所需查询接口

- Router MVP 已上线（mra `knowledge/route`）：`kg_resolve` + `kg_neighbors`
  即为 Local KG 查询面，**图谱侧只需保证 Food 节点可被 resolve、边带证据**。
- 建议图谱侧提供"覆盖度自描述"（哪些类别/前缀已收录），供 Router 判定
  local miss 是否可信（未来 P2-3）。

## 7. Research Evidence 与 Local KG 隔离规则（铁律）

- **禁止 current-study findings 自动生成 KG 边**：课题结果（atlas 命中/
  findings/假设）只能存 Research Evidence（AgentLab/registry），不得回流
  merged 图谱；唯一升级通道是人工 curated ingestion（mra knowledge store
  状态机：candidate→verified 须人工 approved）。
- 本轮 Food–Pathway–Phage 的全部结论**不要求图谱侧收录**。

## 8. 边界提醒

- mra 侧对图谱只读（快照消费）；任何图谱写入只发生在图谱仓自身管线。
- 快照刷新约定：Food 节点合并后通知 mra 侧 `create_snapshot` 换新快照 id。
