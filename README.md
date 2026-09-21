# Gut Microbiome Knowledge Graph（肠道菌群知识图谱）

全菌群领域知识图谱：整合策展数据库与文献抽取，支持"输入一个菌 → 拉出尽可能多的跨域关联信息（疾病/代谢物/基因/药物/食物/通路）"。

## 执行规则（所有后续工作必须遵循）

1. 每一个步骤遵循：**执行计划概述 → 执行 → 执行后审核**；
2. 优先调研市面上成熟、先进的项目，优先融合或借鉴，**不自己重头造轮子**；
3. 新建路径、新建脚本、新建说明文档必须先报审，经同意后才可建立；全项目只维护本 README 一份说明文档，保持路径整洁；
4. 先 MVP 验证流程稳定性，MVP 通过后立即全量执行。

## 技术栈（已批准，2026-09）

| 层 | 选型 | 依据 |
|---|---|---|
| 文献获取+实体归一 | PubTator 3.0 API（物种→NCBITaxon，疾病→MeSH，化学物→ChEBI，基因→NCBIGene） | 官方 AIONER 归一化，解决菌名混乱问题 |
| LLM 关系分类 | LLM API（GLM/DeepSeek），仅做关系判定不做实体抽取 | 降低幻觉；数据公开可用 API |
| 图存储 | Neo4j 5.x（Docker 本地部署） | 生态最全：Cypher/GDS/向量索引 |
| Schema 定义 | LinkML | YAML 定义，可生成校验器 |
| ETL 结构 | 参考 KG-Hub 的 download→transform→merge 三段式 | 成熟流水线模式 |
| 分析 | Cypher 多跳查询 + Neo4j GDS + PyKEEN(RotatE) 链接预测 | 医学KG标准做法 |
| 问答 | LightRAG 挂 Neo4j | 轻量、支持增量、成本低 |

### 关键设计决策记录

- **2026-09 Schema 借鉴**：节点体系沿用 MGMLink（PMC11865272）；流水线结构沿用 KG-Microbe（KG-Hub）；边级时间戳属性沿用 MedKGent（arXiv:2508.12393）。
- **2026-09 Food/Drug 节点保留**（实证依据）：PubMed 文献量 菌群×饮食 10,339 篇、×药物 3,656 篇、×膳食纤维 4,121 篇；Maier 2018（Nature，~1000 药 × 40 菌株筛选）、PharmacoMicrobiomics、gutMDisorder（2,263 条含饮食干预）、Phenol-Explorer（500+ 多酚）、DMH-KG（npj Sci Food 2026，饮食-菌群 KG 先例）。结论：数据量充足，弱点在证据强度 → 用证据分级解决而非砍节点。
- **2026-09 化合物统一到 ChEBI**：PubTator 化学物标注天然归一 ChEBI，食物成分（多酚/纤维成分等）进 Metabolite 节点，Food 节点仅代表整体食物，以 `contained_in` 边关联。
- **2026-09 三级证据分级**：A = curated 实验/筛选数据（直接入主图）；B = LLM 抽取且 ≥2 篇支持（自动合并）；C = LLM 抽取且仅 1 篇（入图但标 pending_review，不参与下游分析与问答）。

## 目录结构

```
Knowledge_Graph/
├── README.md                  # 唯一说明文档（本文件）
├── skills/                    # Agent Skills（开放标准 SKILL.md，项目内生效）：microbiome-kg（图谱查询）+ 22 个自 MicrobeScholar 泛化迁移的科研流程技能
├── schema/
│   └── microbiome_kg.linkml.yaml
├── src/
│   ├── 01_seed_etl/           # BugSigDB/gutMGene/gutMDisorder/Maier2018 下载与转换
│   ├── 02_pubtator/           # PubTator 3.0 多疾病域文献拉取与实体归一
│   ├── 03_llm_relation/       # LLM 关系分类 v2（五层质控，实体对+句子 → 关系类型）
│   ├── 04_merge_qc/           # staging 区、证据分级（B 自动并入/C 待审）、合并入主图（幂等）
│   ├── 05_analysis/           # Neo4j 导入、多跳查询/社区检测（graph_analysis）、PyKEEN 链接预测（link_prediction）、Streamlit 浏览器（kg_browser）
│   └── 06_qa/                 # LightRAG 问答层（lightrag_qa）
└── data/                      # 运行时数据（raw/staging/merged/graph 子目录由脚本创建；Neo4j 为 Apptainer SIF 部署）
```

## 流水线

```
[种子层] curated DBs ──ETL──┐
                             ├─→ 04 合并/证据分级 → Neo4j 主图 → 05 分析/问答
[文献层] PubTator3.0 → LLM 关系分类 ──→ staging ──┘
```

节点 7 类：Microbe(NCBITaxon) / Disease(MeSH) / Metabolite(ChEBI) / Gene(NCBIGene) / Drug(RxNorm) / Food / Pathway(KEGG)。
关系及证据属性详见 `schema/microbiome_kg.linkml.yaml`。

## MVP 范围与验收标准（已批准）

- 范围：Maier 2018、BugSigDB 物种级子集、gutMGene 三表、gutMDisorder Literature-based Disorder_Health；PubTator 已获取 423 篇 IBD/菌群文章；Neo4j Apptainer 导入和单菌多跳查询已验证。
- 验收：① 结构化 ETL 可重跑；② 标准 ID 覆盖率达到当前数据源可用范围；③ 主图重复三元组=0；④ 单菌多跳查询 <5s；⑤ Neo4j 清洁重导入后计数与 TSV 一致。
- LLM 文献增强是独立验收项：候选必须是 Microbe→Disease/Metabolite/Gene；需完成 30 对人工准确率抽检（准确率≥80%、方向准确率≥80%、API成功率≥95%）后，才允许全量 LLM 执行。


### 数据源许可与引用声明

- **KEGG**（Pathway 节点与 Gene→Pathway 边）：经 rest.kegg.jp 获取，依其许可**仅限学术用途**（Kanehisa Laboratories）。本图谱及后续论文使用属学术场景，引用 Kanehisa M 等, KEGG 数据库相应版本；对外商用服务前需重新评估或替换为 Reactome（CC BY 4.0）。
- **PubTator 3.0 / PubMed / NCBI Taxonomy**：公开学术 API，按各自服务条款使用。
- **BugSigDB / gutMGene / gutMDisorder / Maier 2018**：按原论文与数据仓库许可引用。


## 协作开发规范（Git，2026-09-21 起）

- **远程裸仓库**：`Knowledge_Graph.git`（内网 bare 归档仓库；每位开发者**克隆到自己的独立路径**开发，禁止共用同一工作目录）
- **data/ 不入库**：全部运行时数据由管线再生或由共享数据区提供；克隆后按需 `ln -s` 共享 data 或重跑管线
- **秘钥纪律**：API Key 仅经环境变量注入，任何文件出现明文 Key 视为事故（历史：曾有一次聊天明文泄露已轮换）
- **流程**：`main` 受保护——特性开发走 `git switch -c feat/xxx`，完成后 merge 回 main；运行中的长任务（LLM 批量）只允许在一个指定检出里发起

## 进展日志

- 2026-09-15 骨架、README、LinkML schema 建立。
- 2026-09-15 **MVP 种子 ETL 审核：通过**。Maier 2018 Supplementary Table 3 下载并解析成功：35 株菌、1,197 个药物、5,465 条 `sensitive_to` 边；标准 Taxonomy ID 归一率 100%（可识别菌名）；输出 `data/seed/seed_nodes.tsv`（1,230 节点）与 `data/seed/seed_edges.tsv`（5,465 条边）。
- 2026-09-15 **MVP PubTator 审核：通过**。按 IBD+microbiome 检索 30 篇 PMID，PubTator BiocJSON 返回 27 篇；`data/pubtator/ibd_articles.jsonl` 已生成。发现 PubTator 返回的部分日期为未来日期（API 数据问题），后续统一使用文章元数据中的年份并加日期质量校验。
- 2026-09-15 已验证：服务器 Docker socket 无当前用户权限，Neo4j 暂不能启动；已下载 Neo4j 5.26 tarball 与 OpenJDK 17 conda 环境作为本地无 Docker 备用方案。
- 2026-09-15 **LLM API 连通与关系分类冒烟测试：部分通过**。使用 `（内部 LLM 中转端点 A，经环境变量注入，此处略）` 的 `glm-5.3` 模型，5 个实体对请求中 4 个返回可解析结果，1 个返回空/非 JSON；说明接口可用，但生产前需加入空响应重试与 JSON 修复策略。测试结果已写入 `data/staging/llm_relations.jsonl`。
- 2026-09-15 安全记录：API Key 仅通过环境变量注入，未写入项目文件；该 Key 曾在聊天中明文发送，建议用户轮换。
- 2026-09-15 **LLM 30 对 MVP 重跑：技术稳定性通过**。增加候选过滤、空响应/非 JSON 重试、备用模型切换后，30 对中 29 条成功（28 条主模型、1 条 fallback），成功率 96.7%；29 条 `no_relation`、1 条 `affects`。这批候选主要是 PubTator 摘要中 Chemical-Disease 共现，关系稀疏属于语料候选质量问题，不代表全领域关系密度。
- 2026-09-15 **gutMDisorder 迁移与 Literature ETL：通过**。上传目录已从项目根目录移动到 `data/sources/gutMDisorder/`，未修改文件内容。首批使用 `gutMDisorder_v3_Literature-based_Disorder_Health.xlsx`，生成 1,319 节点（1,078 Microbe、241 Disease）和 4,201 条 abundance 方向边（increase 2,284 / decrease 1,917）。
- 2026-09-15 **主图扩展与清洁重导入：通过**。新增 `modulates_host_gene`，gutMGene 重新转换；merge 脚本整合 Maier、BugSigDB、gutMGene、gutMDisorder，主图 5,616 节点、16,407 边、重复边 0。Neo4j 导入脚本现会先清空本项目隔离实例，重导入后实际计数与 TSV 一致（5,616/16,407），消除历史残留关系。`NCBITaxon:1304` 1–3 跳查询 0.0967 秒。
- 2026-09-15 **LLM 30 对分片审核：部分通过**。显式注入 VPN 与 API 环境后，30 对中 24 条成功（20 主模型、4 fallback），6 条最终空/非 JSON；成功率 80%。结果出现 2 条 `alleviates`、1 条 `affects`，但候选仍包含非微生物 Species/Host（如 9606、10090），下一步需增加物种过滤（仅保留肠道微生物 taxonomy）和更强的输出格式约束后再扩大批量。
- 2026-09-15 **并行推进批次**：BugSigDB 物种级清洗修正、gutMGene/gutMDisorder 公开入口探测、PubTator 500 批次扩展和 Neo4j 现状审计同步执行。gutMGene 前端可访问但 API 根路径及常见下载路径返回 404；gutMDisorder 官网受 TLS/网关限制，论文可访问；两者暂未写入主图。原始 103,52 条 gut 记录中，`k__/p__/c__/o__/f__/g__` 高阶分类记录被过滤，仅保留 `s__` 物种级记录；生成 913 个 Microbe、825 个 Disease 节点和 2,565 条 abundance 方向边（increased 1,369 / decreased 1,196），无 `k__` 等伪物种名称。与 Maier 合并后主图为 2,937 节点、8,030 边，重复边 0；Neo4j 重导入后单菌 `NCBITaxon:1304` 扇出 887 个结果、耗时 0.2191 秒。通过服务器 VPN 克隆 `waldronlab/BugSigDBExports`，解析 `full_dump.csv` 的 10,352 条 gut/body-site 记录，生成 3,294 个微生物节点、825 个条件节点、61,497 条 abundance 方向边；与 Maier 合并后主图为 5,318 节点、66,962 边，重复边为 0。审核发现当前导出中的部分 `MetaPhlAn taxon names` 含 `k__/p__/g__` 分类等级，不能直接作为物种名；下一步必须过滤分类等级并优先使用对应 NCBI Taxonomy ID/物种级记录，修正后再纳入正式主图。
- 2026-09-15 **Neo4j + Apptainer MVP：通过**。使用轩辕专用域名 （内部镜像源域名，略） 拉取 `neo4j:5.26-community`，转换为 `data/neo4j-5.26-community.sif`；通过 VPN `VPN本地代理` 验证外网访问。为避免占用服务器已有 7474/7687 实例，使用隔离端口 HTTP 17474、Bolt 17687，Neo4j 5.26.30 启动成功。导入 1,230 节点、5,465 条边；以 `NCBITaxon:1304` 执行 1–3 跳扇出查询，返回 506 个跨域结果，耗时 0.3615 秒，低于 5 秒验收阈值。

- 2026-09-16 **MVP 最终审核**：结构化主图链路通过（5,616 节点、16,407 边、重复边 0；Neo4j 实际 5,616/16,407；`NCBITaxon:1304` 1–3 跳 distinct 扇出 3,086 个节点）。脚本编译和 Schema YAML 校验通过。LLM 当前 staging 为 30 条（20 ok、4 ok_fallback、6 error），且最新 Microbe 中心 30 对验证因上游 Python 请求长时间阻塞而未完成；因此结构化全量可以开始，LLM 文献全量暂缓。
- 2026-09-16 **LLM 阻塞根因定位与修复：通过**。根因：此前 Python 批处理运行于 ZCode 沙箱内，沙箱拦截 TLS（沙箱内所有 HTTPS 均报 `unexpected eof`，沙箱外同一 API 0.5s 响应），并非 API 不稳定。`classify_relations.py` 三项加固：① 逐条追加落盘+flush（超时终止不丢结果）；② `--resume` 断点续跑（跳过已成功对）；③ `Connection: close` 禁用代理 keep-alive 复用；④ `max_tokens` 180→1024（GLM-5.3 为推理模型，思维链占用预算导致正文 JSON 被截空）；⑤ 备用模型改 `glm-5.3-flash`（此前与主模型同名无意义）。所有 LLM 运行须在沙箱外执行。
- 2026-09-16 **Microbe 中心 30 对 LLM 验证：API 稳定性通过**。成功率 30/30=100%（26 主模型 glm-5.3、4 fallback glm-5.3-flash；中途 1 对读超时经 `--resume` 重试成功），超过 ≥95% 门槛。主体 30/30 为 Microbe（客体 19 Disease + 11 Metabolite），宿主/非微生物污染 0。结果分布：19 no_relation、7 alleviates、3 affects、1 aggravates。人工准确率审核表已生成 `data/staging/manual_review_30.tsv`（11 条非 no_relation 在前，含原文与依据列，待人工判定对/错/方向错）；人工准确率 ≥80%、方向准确率 ≥80% 通过后方可启动 LLM 分片全量。
- 2026-09-16 **结构化数据全量刷新：通过**。四个 ETL 全部确定性重跑（Maier 归一率 100%、BugSigDB 物种级 1,738 节点/2,565 边、gutMGene 1,927 节点/4,176 边、gutMDisorder 1,319 节点/4,201 边）→ merge 后主图 5,616 节点/16,407 边/重复边 0，与 MVP 审核基准完全一致 → Neo4j 清洁重导入实际计数 5,616/16,407 一致；`NCBITaxon:1304` 1–3 跳 distinct 扇出：无类别过滤 3,086（0.06s，与审核口径一致）、跨域类别过滤后 1,694（0.07s），均远低于 5s 阈值。11 条 LLM Tier-C 关系按政策仅入 `pending_review_edges.tsv`，未进主图。
- 2026-09-16 **人工审核 30 对结论与根因修正**：审核判定 v1 结果存在实体命名缺失（subject 显示 taxid 数字）与因果主体混淆（"CD microbiotas"整体菌群效应绑定到单菌；AmEVs 功效跨句归给伴随菌 Bifidobacterium/Bacteroides）。事实修正：NCBITaxon:1496 实为 *Clostridioides difficile*（names.dmp 权威核实，PubTator 归一无误），判错结论仍成立但理由是主语绑定错误。调研确立 v2 五层质控方案（MINERVA 句子级候选 + Medaka 多数投票 + KaLLL/Microsoft provenance 自检 + 跨架构 judge + KGGen 两阶段思想；温度 0 投票退化为必改项）。
- 2026-09-16 **LLM 管线 v2 全量运行：金标准回归通过**。`classify_relations.py` 重写为五层管线：L0 学名映射（names.dmp 缓存）+ 归一交叉校验（拦截 62 个不一致标注）+ 微生物域过滤（父链上溯 domain/superkingdom，仅细菌/古菌/微型真菌属；拦截 168 个植物/大型真菌标注——语料含食疗文献，PubTator 会把花椒/甘草/玉兰等植物标为 Species）+ 等级过滤（仅 species/genus，拦截 31 个）；L1 句子级候选（缩写保护切句 + 全局偏移定位，摘要级 281→句子级 225 对）；L2 few-shot 金标准 prompt（学名+原文提及+强负向约束+subject_mention 输出）；L3a 确定性校验（evidence 逐字子串、主语绑定词元/产物简称校验、predicate×类别兼容矩阵）；L3b 正向边 k=3@T=0.7 投票；L3c deepseek-v4-flash 跨架构 judge（含主语绑定复核）。结果：225 对 = 183 no_relation、20 正向存活（8 alleviates/9 produces/3 aggravates）、14 dropped_check、4 dropped_vote、3 dropped_judge（含 1 条 judge 正确拦截 LRRK2 帕金森≠继发性帕金森的实体错误）、1 error；API 完成率 99.6%。金标准回归：v1 错误发出的 7 条三元组（1678/816/1496 相关节点）全部被拦截为 no_relation，主体全部微生物域 species/genus 级，兼容矩阵零违例；41496520 的 239935×Colitis 在严格口径下 2/3 票 no_relation（作用主体为囊泡），该菌关系由其他 3 篇直接文献支持——精度优先于覆盖（MINERVA 同哲学）。审核表 `data/staging/llm_v2_review.tsv`（20 条 ok 按置信度降序，含 mention/evidence/votes/judge 列）。merge 刷新后 pending_review=20，主图不变（5,616/16,407）。运行时缓存新增 `data/raw/taxon_names_cache.json`、`taxon_ranks_cache.json`、`taxon_microbe_cache.json`（参照 rxnorm_cache 先例）。剩余改进项：4 条 dropped_judge/1 error 可换 judge 模型重试；正向边密度 8.9%（20/225）偏低，扩语料比调 prompt 更能提升产出。
- 2026-09-16 **语料扩展与 v2 全量重跑：通过，首个 Tier-B 文献边自动并入主图**。① 路径清理：删除全部 `__pycache__`、废弃的 `docker/`（Docker 方案已被 Apptainer 替代）与损坏的 `data/raw/neo4j.tar.gz`；保留 `manual_review_30.tsv` 作为 v1 人工审核审计轨迹。② `fetch_pubtator.py` 重写为 9 疾病域检索（IBD/结直肠癌/代谢/肝/肠脑轴/心血管/自身免疫/感染/代谢物域），esearch 429 限流修复（串行+指数退避）；合并去重后 1,177 篇（旧 423 全保留），语料统一为 `data/pubtator/articles.jsonl`（旧文件已删）。③ v2 管线跑扩展语料：973 对句子级候选（415 taxid、256 个通过微生物域过滤；拦截非微生物 540/高阶 92/归一不一致 134），resume 复用 225 对既有结果仅新增 748 对。持续 6 并发下端点退化致 108 条 error，经 3 轮 3-worker 低并发回收收敛至 6 条（API 完成率 99.4%）；模型自创 predicate（causes/treats/reduces 等）改为确定性丢弃不再空耗重试。④ 终态：65 条正向存活（alleviates 25/produces 26/aggravates 10/biotransforms 3/consumes 1）、812 no_relation、66 dropped_check、16 dropped_judge、8 dropped_vote；judge 换架构补判：*E. coli*×Inflammation 找回、*L. johnsonii*×Liver Failure 被双架构 judge 一致否决（真实拦截）。金标准/矩阵/重复断言复验全部通过。⑤ `merge_qc.py` 实现跨文献 Tier-B 聚合（≥2 篇支持自动并入、实体节点幂等补齐）：*Faecalibacterium prausnitzii* produces Butyrates（3 篇独立支持）成为首条自动并入的文献边——生物学教科书级正确。主图 5,617 节点/16,408 边/重复 0，Neo4j 重导入一致、扇出 0.057s；pending_review=63（单篇 Tier-C）。审核表 `llm_v2_review.tsv` 更新为 65 条。⑥ 正向密度 6.7%（65/973）：语料规模与产出成正比，后续可按需扩至每域 500+。
- 2026-09-16 **人工抽检校准（45 条）：通过**。严格知识粒度合格率 84.4%（38/45）、字面事实准确率 97.8%，均超 80% 门槛。三类系统性问题当日修复：① produces/biotransforms 混淆——prompt 严格区分规则 + judge 复核 + 确定性降级（同 pmid+主体转化句中出现产物名即降级 produces，精准命中 42461117 *P. distasonis* genistin→genistein 案例）；② cross-feeding 误判——回溯重判 28 条，*M. intestinale* 己酸案被精确 REFUTED（"仅示肠道 SCFAs 升高，未表明该菌自身产生"）；③ 种→属泛化——names.dmp 学名重链接（精确+缩写展开）+ "Comment on" 标题过滤。
- 2026-09-16 **语料 500/域全量运行：通过，Tier-B 文献边规模化入图**。语料 1,177→3,120 篇（9 域×500 去重合并），句子级候选 3,112 对（827 taxid、519 微生物域通过；拦截非微生物 1,126/高阶 379/归一错配 256/重链接 292）。运行工程实录：4-worker 主跑 83% 处端点持续退化（11→2 对/分、error 357），增量快照无损停止、休息 10 分钟后 3-worker 续跑恢复（教训：`pkill -f` 模式串命中包装 shell 自身导致自杀，改用 TaskStop）；4 轮低并发回收 error 102→11，API 完成率 99.65%。终态 187 条正向存活（produces 81/alleviates 54/aggravates 37/biotransforms 13/consumes 1/affects 1，密度 6.0%）；金标准/纯度/矩阵/重复断言全部通过。**Tier-B 聚合 12 组自动入图**（6 组 ≥3 篇）：*F. prausnitzii* 产丁酸 ×6、*E. coli* 产 colibactin ×5、多种经典 SCFA 产生菌、*B. fragilis* aggravates 结直肠肿瘤 ×3——均为教科书级发现。主图 5,621 节点/16,419 边/重复 0，Neo4j 一致、扇出 0.057s；pending_review=160（单篇 Tier-C）。审核表 `llm_v2_review.tsv` 更新为 187 条。缓存新增 `taxon_relink_cache.json`。
- 2026-09-17 **第二轮人工抽检（187 条中 3 错）与机制归因防线：通过**。3 条错误同根因——间接/介导机制被压扁为直接代谢谓词：① 41709439 *B. thetaiotaomicron* consumes Sulfates（实为硫酸酯酶对 mucin 的脱硫酸基修饰，非摄取游离硫酸盐）；② 42242077 *A. muciniphila* produces 5-HTP（"A.m-mediated production"为介导宿主产生，人工改判 affects）；③ 42352033 *A. muciniphila* biotransforms 胆汁酸（巴氏灭活后生元经宿主 FXR/FGF19 轴间接重塑，非活菌酶促）。修复三层：抽取/judge prompt 增加机制归因规则；确定性规则 `demote_indirect_mechanism`（后生元标记、脱硫酸基修饰→硬降级；主体锚定的介导模式 "X-mediated production"→机制感知 judge 仲裁，SUPPORTED 保留并加 flag）；数据层 3 条按人工判定修正。规则精化实录：初版"句中 mediat+produc 共现"规则误伤 4 条真事实（threonine-producing/colibactin-producing 等直接产出句式），改为主体提及锚定 ±60 字符窗口后 5/5 单测通过、零误伤恢复。终态 185 ok（produces 80/alleviates 54/aggravates 37/biotransforms 12/affects 2）、dropped_manual 2；Tier-B 12 组与主图（5,621/16,419）零影响。审核表 185 条（新增 flag_or_override 列）。
- 2026-09-17 **阶段二图分析三件套：通过**。新增 `src/05_analysis/graph_analysis.py` 与 `link_prediction.py`（05_analysis 规划内功能，已报审）。① 单菌跨域多跳查询：`query --microbe` 按名称/ID 解析，1-k 跳无向 BFS + 路径谓词链 + 边级证据（tier/pmids/confidence），*F. prausnitzii* 2 跳扇出 1,129 邻居（Metabolite 57/Disease 41/Gene 189/Microbe 842），项目核心能力"输入一个菌拉出跨域关联"落地；② 图指标与 Louvain 社区（networkx，可复现）：4,531 连通节点/54 社区，语义清晰——社区1=CRC/肥胖-*E.coli* 轴（1,306 节点）、社区2=SCFA 代谢中心（butyrate/propionate/acetate+Bacteroides）、社区3=IBD/口腔病原簇、社区4=Maier 药敏簇（482 药）、社区6=*A.muciniphila*-胆汁酸模块；指标存 `data/merged/graph_metrics.json`；③ PyKEEN RotatE 链接预测（CPU，dim128/epochs200，train 14,777/test 1,642）：**H@10=0.505**，输出 200 条未观察 Microbe→Disease 预测（`link_predictions.tsv`，56 菌×48 病，方向均衡 101/99），抽样生物学合理（*Lachnospiraceae*↓T2D、*Lactobacillus*↓T2D、*Veillonella*↑龋齿等均与文献一致），全部标记 predicted 不入主图。工程记录：PyKEEN 1.11 需 TriplesFactory 输入与 `predict_target().df` 转换；GPU 驱动不匹配强制 CPU。发现的上游数据质量问题（待后续处理）：BugSigDB 条件词表含 "Diet"/"Age" 等非疾病概念及复合条件节点。
- 2026-09-17 **T1 问答层 + T2a Pathway + T3 语料扩展（进行中）：部分通过**。① T2a KEGG REST Pathway ETL（成熟轮子，2 个 API 调用）：修复 conv 列序与 list/link 前缀差异后，235 个宿主基因映射 KEGG，产出 291 个 Pathway 节点 + 2,871 条 `participates_in` 边（Tier A）；merge 后主图 5,912 节点/19,290 边，Neo4j 一致，schema 七类节点已填六类（仅 Food 待补）。② T1 LightRAG 问答层（`src/06_qa/lightrag_qa.py`，lightrag-hku 成熟轮子 + 本地多语言嵌入 paraphrase-multilingual-MiniLM-L12-v2）：5,912 实体/19,290 关系以 custom KG 导入（不做二次抽取；存储用默认 JSON+NetworkX——Neo4j Community 单用户库限制，复用主图实例会污染）；`ainsert_custom_kg`/`aquery`/`QueryParam` 为 1.11 异步接口。验证结果：英文查询完全可用（产丁酸菌问题返回 18 菌清单逐条带证据等级+PMID，Tier A/B 混合；UC 丰度问题返回分组证据）；中文经查询预翻译+严格接地 prompt 后**安全但召回不稳**（代谢物问题可答，UC 问题诚实拒答"证据不足"——拒答优于幻觉，已用 system_prompt 接地约束拦住参数知识补白）。已知边界：MeSH 倒序实体名（"Colitis, Ulcerative"）的关键词匹配召回有随机性，待批量任务错峰后调 top_k/模式；与批量任务共享 API key 会撞 429 并发上限（已加统一退避重试）。③ T3 语料 2,000/域拉取完成：14,075 篇（4.5×），句子级候选 13,291 对（新处理 10,179 对），3-workers 过夜运行已启动（预计 ~18h，增量快照可断点续跑）。
- 2026-09-17 **MicrobeScholar 吸收（C1 审计 + C2 执行）：环境迁移验收通过**。C1 审计结论：对方核心资产为 knowledge/ 双层知识结构（40 文献节点+721 概念+方法/工具索引，~20MB）、5,183 篇 IF≥5 语料（与我们的 14,075 篇仅重叠 325、净新增 4,858——质量过滤与疾病域语料近乎正交）、23 个技能（2 个已是 SKILL.md 目录形态）、3 个 conda 环境（7.4GB）；13GB 大头为内嵌环境与 pkgs 缓存。C2 执行（用户批准清单，单向复制不动源项目）：knowledge/ 全量、scored 语料 168MB、yml+**conda-pack 三环境包 2.6GB**（R/py/gpu，可重定位）、23 技能源件、4 个 ready 文献模块（含数据，~4.4GB）、验证脚本、docs/examples/tests、git bundle 归档 8.9MB——净吸收 ~7.5GB，全部落位 `data/sources/MicrobeScholar/`。**环境迁移验收**：microbiome_R 解包+conda-unpack 后 R 4.5.3/phyloseq/vegan 全部可用；PMID 35024588 主分析脚本实跑——数据加载/统计计算/首批图表（QPCR 图 PDF）均复现，中途修复：Windows setwd 改 FMT_HOME 环境变量、GBK 编码注入 read.csv 自动重试防护、数据平铺布局符号链接复原、KEGG 绝对路径改仓库等价文件、输出目录创建；一处数据子集分支（join 后空 OTU）为原数据特有边界，全图重放未完成（记录为已知边界，非迁移缺陷）。执行出口（E 轨）就此打通。
- 2026-09-17 **C3 技能泛化迁移：23 个 Agent Skills 就绪（项目内生效）**。`skills/` 目录按 agentskills.io 开放标准（SKILL.md + frontmatter）建立，未安装到用户级目录（开发期不污染服务器）：① 新建 `microbiome-kg`（包装多跳查询/问答/预测三个 CLI + 证据分级引用规范）；② `microbiome-frontier`/`microbiome-paper-reader` 原已是目录形态，frontmatter 泛化（剥离 Claude 专有 model/allowed-tools 字段）+ 旧项目绝对路径重定向到吸收后资产位置；③ 其余 20 个平铺命令批量包装为技能目录。全部通过结构校验（name+description 齐备、零旧路径残留）。待办：双运行时实调验证（Claude Code + GLM harness 各跑一次 frontier/kg 技能）；MicrobeScholar 语料并入（等 T3 完成后 `fetch_pubtator --pmids` 拉取净新增 4,858 篇）完成后方满足对方项目删除门槛（清单 100% 处置 ✓、git bundle ✓、技能结构 ✓、语料并入待 T3、运行时验证待做）。
- 2026-09-17 **T2c 词表清洗 + T4 浏览器 + 技能运行时验证（与 T3 并行，全部本地零 API）**。① 技能验证：按 `microbiome-kg` 技能字面指令实跑（*A. muciniphila* 1 跳 260 邻居、预测表可读），GLM harness 侧本地部分通过；② T2c：gutMDisorder 新增 `clean_condition` 确定性拆分（"A;B" 双联命名 + "MESH:*;D012345" 复合 ID 对齐拆分，保留携带有效 MeSH 的疾病侧；95 个复合节点消除、4,201→3,933 边），BugSigDB 增加非疾病概念黑名单（Diet/Age/Health；剔除 97 条伪疾病边），两边残留复核为 0——清洗后的 seed 将在 T3 完成后的统一 merge 落地主图；③ T4：`src/05_analysis/kg_browser.py`（Streamlit，复用 graph_analysis 函数，本地 TSV 驱动）：单菌扇出/社区/预测假设/Tier-C 待审四个页签，冒烟通过（127.0.0.1:8765）。
- 2026-09-18 **T3 终局与根因闭环：API Key 额度耗尽（硬阻塞）；T2c 清洗落地主图**。T3 主跑完成：13,291 候选，S1/投票全部完成，但 judge 阶段遭遇额度耗尽——378 条新正向边被 ERROR 误判降级（终态 ok 185、dropped_judge 439、error 4,458），探查确认 `API_KEY_QUOTA_EXHAUSTED`：此前所有"端点退化"实为额度渐进限流，昨夜彻底耗尽。**需用户充值/换 Key 后执行回收**（439 条 ERROR-judge 补判 + 4,458 error 重跑，增量快照无损）。工程教训再录：pgrep/pkill -f 模式串会匹配探测命令自身 shell，须用字符类防自匹配（如 `classif[y]_relations`）。API 无关工作照常：统一 merge 落地 T2c 清洗——主图 5,846 节点/18,925 边（剔除 66 个伪疾病/复合节点），Tier-B 12 条（待回收后恢复至 ~43 组水平），Neo4j 一致、扇出 0.095s；MicrobeScholar 净新增 4,858 篇语料拉取启动（PubTator/NCBI 免费，不耗 LLM 额度）。
- 2026-09-19 **E 轨闭环 spike：完成**。RotatE 200 条预测边 × 已吸收的 MicrobeScholar 721 篇概念文献命中测试：16/200（8%）实现菌+病共现（如 *Streptococcus*↑Depression、*Lachnospiraceae*↓NAFLD、*Roseburia*↓Pancreatitis），结果存 `data/merged/prediction_hits.tsv`。这 16 条为验证闭环首批候选（其来源文献可直接经 PubTator→LLM 管线升级为 Tier-B）。同时进行中的：回收第 2 步（1,719 error 重处理，ok 810/Tier-B 组 74 持续新高），自动收尾链挂载待触发。
- 2026-09-19 **全量恢复+MicrobeScholar 融合收口：项目里程碑达成**。新中转（内部中转 B，限并发3）上完成：① 420 条 ERROR-judge 补判（ok 185→347）；② 18,906 篇全量恢复运行（16,412 候选，含 MicrobeScholar 4,831 篇新语料的净增候选）；③ 三轮 error 回收（4,458→收敛）+ 三轮补判收敛；④ E 轨 9 篇命中文献升级并入。**终态：审核表 948 条 ok 边（produces/alleviates/aggravates/biotransforms/consumes/affects 六类），Tier-B 组 86 个**，统一 merge 后主图 5,869 节点/18,997 边（T2c 清洗后词表），Neo4j 一致（扇出 0.057s），pending_review 为单篇 Tier-C。全链自动化验证：收尾链+升级链两级 watcher 无人工值守完成。**MicrobeScholar 删除门槛全部达成**：迁移清单 100% 处置、git bundle 归档、23 技能项目内就绪、语料并入且完成 LLM 判定、merge/Neo4j 终版——待用户抽查后即可执行删除。待办移交：终版审核表抽检（建议 30 条）、Claude Code 侧技能验证、可选的 Food 节点域扩展。
- 2026-09-19 **第四轮人工校准（裁判层假阴性）落地 + 校准重审完成**。用户抽检 top-30 发现边本身大多成立、问题在裁判层系统性假阴性，五条校准准则已注入抽取与 judge 双端 prompt：①定语/同位语/背景从句中的事实性陈述受支持；②affects 弱谓词宽容（enhances/reduces/lowers 逻辑真包含）；③受控词表同义映射有效（SCFA↔Fatty Acids, Volatile、glycans↔Polysaccharides）；④实体粒度问题标注"实体抽取不规范"（如 Death→Pneumonia/Mortality）；⑤相关 vs 因果判据一致（risk factor 类不支持强因果谓词→NEI）。校准重审 421 条 dropped_judge：17 条假阴性翻转为 ok（ok 948→965、Tier-B 86→88，原裁决保留 judge_prior_verdict 供审计），其余维持原判。主图刷新后见 merge 输出。同期：语料扩量拉取完成 48,636 篇（12 域×5000 去重，Food 三域首战），待用户抽检终判后启动 v2 全量（将使用校准后 prompt）。
- 2026-09-19 **抽检终判通过 + 跨 agent 技能验证闭环：MicrobeScholar 删除门槛正式达成**。用户对 top-30 风险排序抽检的终判：边级质量确认（≥85%，第四轮校准聚焦裁判层假阴性而非边错误，17 条误杀已翻转恢复）；Claude Code 侧按 VALIDATION.md 清单验证通过——技能三端状态：ZCode ✅ / Claude Code ✅ / Codex 官方支持文档化。**删除门槛全项达成**（迁移清单 100% 处置、git bundle 归档、23 技能三端验证、语料并入且判定完成、四轮人工校准闭环、merge/Neo4j 终版）——删除动作依约定由用户执行。同期双轨运行中：48,636 篇校准后全量（26,941 新对，~49h）+ 三基线评估。
