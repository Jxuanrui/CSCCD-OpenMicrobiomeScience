# 项目全链路运行审计报告 · 知识架构升级版

**审计日期**：2026-09-24 ｜ **审计人**：mra 侧 Agent（ZCode）｜ **架构基准**：用户 2026-09-24 定稿
《Knowledge-driven Research Agent 顶层设计》（四知识域分层 + 知识边界 + 双知识输入 + 研究证据输出）

---

## 0. 结论速览

| 维度 | 结论 |
|---|---|
| 四知识域成熟度 | Local **中高** / Live **低-中** / Research Evidence **中-高** / Method **低-中** |
| 项目成熟度定级 | **Level 4 初段（Auditable Research Loop）**；距 Level 5 的核心缺口=知识路由与方法知识咨询未接入研究设计 |
| 自证循环风险 | **当前为零**（不存在任何 research→KG 写入路径），且 ingestion 状态机在设计上阻止未来发生（候选证据不得自称 approved） |
| 最重要发现 | `knowledge/` 模块**已内置用户构想的 curated ingestion 全套骨架**（source 注册/license/人工审核/证据等级只降不升/raw hash），**但从未投产**（source_registry=0，candidate_evidence=0），也未接入研究循环工具面 |
| P0 缺口 | ① Knowledge Router MVP；② litread 补 provenance（retrieved_at/NCBI key/retry）；③ Method Knowledge Base 起步 |

---

## 1. 四类知识域现状盘点

### 1.1 Local Knowledge（"运行课题前已知什么"）——成熟度：中高

| 载体 | 内容 | 治理 | 缺口 |
|---|---|---|---|
| KG 快照（var/kg_snapshots/2026-09-21） | 5,869 节点 / 19,001 边；六类节点（Microbe 1871/Drug 1196/Disease 1027/Gene 850/Metabolite 634/Pathway 291） | **source_type 分层真实存在**：curated 18,913 条（Tier A）+ llm_extracted 88 条（Tier B）；Tier-C/predicted 设计上拒入；快照不可变 + sha256 溯源 | **无 Food 节点**（需求单已在图谱侧）；无 Knowledge Router 消费的"覆盖度自描述" |
| KnowledgeStore（var/knowledge/knowledge.db） | **12 条人工批准知识条目**（YAML→approval 必须人工 signed→版本化+FTS5 检索+可退役） | approval 状态机、source(kind/ref/date)、evidence_level | **source_registry=0、candidate_evidence=0——从未投产**；不在研究循环工具面 |
| vecstore（仓库根 var/vecstore/lancedb） | kg_entities + lit_papers 两表语义索引 | 每条带 source 字段（kg/litread） | 无 tier/timestamp/freshness 字段 |
| 别名/标识解析 | kg_resolve（NCBITaxon/MeSH/ChEBI）、species_to_term | — | — |

### 1.2 Live Knowledge（"外部世界现在知道什么"）——成熟度：低-中

| 接口 | 现状（十问详见 §3） | 备注 |
|---|---|---|
| litread（NCBI E-utilities） | timeout 60/120s ✓、限速 sleep(0.4) ✓、缓存≠KG ✓、agent 可调（lit_search_read）✓ | **无 retry、缓存无 retrieved_at、未使用 NCBI_API_KEY** |
| knowledge/sources/europepmc.py | 产出规范 CandidateEvidence（retrieved_at/sha256/无引用自动降级）、timeout 15s | **未投产**（无已注册 source） |
| knowledge/sources/omnipath.py | 适配器存在 | 同上 |
| radar（cron 文献雷达） | 独立管线，密钥在 radar/config.env **未入 git** ✓ | 与 mra 主链路无共享获取层 |
| model_runtime（ARK/GLM） | 计算接口（非知识事实）；预算双层护栏 ✓ | — |

**核心缺口**：三套外部访问各自实现（litread 用 urllib、knowledge sources 另一套、radar 又一套），**无统一 Knowledge Acquisition Interface**。

### 1.3 Research Evidence（"本课题产生了什么"）——成熟度：中-高

| 载体 | 内容 |
|---|---|
| var/research/ | atlas v1/v3(superseded)/v4(**canonical**, CANONICAL/SUPERSEDED 标记)、研究会话、litread 产物 |
| var/audit/research.db | 治理账本：每次统计执行的 allow/deny + 审计 verdict + 输出 digest |
| 课题仓 AgentLab/ | findings.md（叙事卡 v4）、evidence_matrix、delta 报告、全部敏感性/诊断 TSV |
| 课题仓 DietaryMicrobiomePlatfor/var/registry | 36 项分析注册 + sha256 溯源 + 交叉审核状态 |

- **canonical 纪律刚建立**（atlas v4 冻结、563 条零漂移核对）。
- **无 research→KG 写入路径**：图谱快照只读、单向 KG→agent；vecstore 的 lit_papers 只装外部文献。**自证循环当前为零**。
- 缺口：无统一 evidence schema/store（散在 tsv/md/db 三处）；negative finding 已记录但未结构化。

### 1.4 Meta / Method Knowledge（"为什么相信这个结果"）——成熟度：低-中

**已编码**（可执行的方法知识）：audit/rules.py 五条确定性规则（MULT/BATCH/COMP/PERM/ID）、治理门四道闸
（常数暴露拒绝×3 层、资源限制、审计账本）、**回归测试固化的真实案例**（常数列伪相关、1068vs1060 错位拦截）。

**未编码**（散落在 findings.md/commit message/对话记录）：rank 变换压缩幅度信号、伪计数支配区、
mean-of-logs 广谱幅度轴、特异性对照可否决表面层间关联、BH 族内校正口径、association≠mechanism、
parallel responses≠mediation、ATLAS top200 并列破断原则……

**缺口**：无 Method Knowledge Base 条目化/可查询化——Agent 设计课题时无法"咨询方法学规则"。

---

## 2. 四层全链路地图（升级版）

```
【知识输入层】
  Local Knowledge ──── KG快照(✓接) / KnowledgeStore(✗未接研究循环) / vecstore(✓接,无分层字段)
  Live Knowledge ───── litread(✓接,provenance不全) / europepmc适配器(✗未投产) / radar(△独立管线)
  Method Knowledge ─── audit规则(△仅执行后把关) / 方法教训(✗散落未编码)
        │
        ↓  【研究设计层】
  Research Planner（ARK LLM，每轮1个白名单动作）
  ✗ 无 Knowledge Router（KG miss 不降级外部查询）
  ✗ 设计前不查 KnowledgeStore / Method Knowledge
  △ 研究问题主要由人类用户提出（本轮课题实证：问题/协议/判定规则全部来自用户）
        │
        ↓  【研究执行层】
  Data Contract(只读) → 治理门(资源限制+审计账本+统计审计) → R统计沙箱 → Atlas
  → Sensitivity(预注册阈值) → Falsification(特异性对照否决实例 ✓)
        │
        ↓  【研究知识输出层】
  Research Evidence(✓ AgentLab/registry/audit ledger, canonical 纪律)
  → Evidence Tier(✓ 预注册三档判定) → Narrative/Hypothesis(✓ v4 冻结)
  ✗ 默认不回流 Local KG —— 合规（且无回流路径，安全的缺失）
```

---

## 3. External Tool & Live Knowledge Capability 审计（十问）

| # | 问题 | litread/NCBI | europepmc 适配器 | radar | ARK/GLM |
|---|---|---|---|---|---|
| 1 | 已有外部 API | ✓ E-utilities | ✓（未投产） | ✓ NCBI+LLM | ✓ |
| 2 | 统一 wrapper | ✗（urllib 各自实现） | ✗ | ✗ | ✓ model_runtime |
| 3 | timeout/retry/rate-limit | timeout✓ retry✗ 限速✓ | timeout✓ retry✗ | 未核验 | timeout✓ 客户端 retries=0+planner 1s/2s 重试✓ |
| 4 | 记录来源 | △ pmid/year 在缓存 | ✓ source_id | ✓ journal_info | — |
| 5 | 记录查询时间 | ✗ 缓存无 retrieved_at | ✓ retrieved_at | 未核验 | — |
| 6 | 缓存 | ✓ 参数哈希键 | — | ✓ | — |
| 7 | cache 与 KG 入库分离 | ✓ var/litread 纯缓存 | ✓ 候选≠入库（状态机） | ✓ | — |
| 8 | 统一 schema | △ papers dict/notes JSON | ✓ CandidateEvidence | ✗ | — |
| 9 | 可被 Agent 调用 | ✓ lit_search_read | ✗ | ✗（cron only） | ✓ planner |
| 10 | 经过治理 | △ 仅 LLM 预算护栏 | ✓ 注册/审核状态机 | ✗ 独立 | ✓ 双层预算+账本 |

**总评**：单点质量尚可（litread 的缓存分离、europepmc 的候选状态机都是正确方向），**缺的是统一获取层与治理全覆盖**——这正是 Knowledge Router 的立项理由。

---

## 4. BioTool / BioinfoMCP Capability Mapping

### 4.1 BioTool（github.com/gxx27/BioTool，ACL 2026 配套）

| 维度 | 事实 | 对本项目的含义 |
|---|---|---|
| 定位 | LLM 工具调用 benchmark（7,040 三元组/127 工具）+ 全部工具的 Python wrapper | **schema 与 wrapper 参考源**，非运行时依赖 |
| 能力 | NCBI E-utilities 全套+BLAST、UniProt 13 子工具、Ensembl 16 子工具；tools.json 统一 JSON-Schema + function_mapping | 我们只有 NCBI 子集 → UniProt/Ensembl 是**缺失能力**，其 schema 约定可直接参考 |
| License | 代码 Apache-2.0；数据集研究用途（上游 API 许可随库） | 只用代码/schema 无障碍 |
| 维护 | 2 commits/7 stars，极早期 | **维护风险高**：fork 或只抄 schema，不做依赖 |
| 治理 | 无 retry/backoff 文档 | 接入必须套我方 SourceDescriptor+gate |
| 接入优先级 | **P2**（需要 UniProt/Ensembl 时） | 方式：以 SourceDescriptor 注册 + 适配其函数签名 |

### 4.2 BioinfoMCP（arXiv:2510.02139，MIT）

| 维度 | 事实 | 对本项目的含义 |
|---|---|---|
| 定位 | LLM 从 CLI 手册自动生成 MCP server；38 个已转换工具（FastQC/Bowtie2/samtools/MACS3/R 包等测序向） | **方法论参考**，非能力缺口填补 |
| 与我们重叠 | 平台化 MCP 暴露（我们已有手写 mcp_server.py） | 重复度低（域不同：测序 CLI vs 菌群 KG+统计） |
| 值得吸收 | Converter 自动化思路；两层 benchmark（单工具+管线级）验证思路 | 未来若需包装外部 CLI 工具再启用评估 |
| 风险 | 生成式 server 质量依赖 LLM；需 OpenAI key；**生成物不经过我方治理体系** | 若引入，产物必须过 systemd 沙箱+审计账本才能上线 |
| 接入优先级 | **P3**（记录备忘，不集成） | — |

**决策**：两者均不合并入仓。BioTool 的 `tools.json` schema 约定与 provenance 字段设计作为 P2 参考；BioinfoMCP 仅作方法论备忘。

---

## 5. 四项关键问题回答

### Q1 当前系统的"知识"分别存在哪里？（四域成熟度见 §1）
一句话：**Local 在 KG+KnowledgeStore（结构齐全、投产不足）；Live 在三套分散接口（有缓存纪律、无统一层）；Research Evidence 在 AgentLab/registry/audit ledger（纪律刚立、schema 未统一）；Method 在 audit 规则+回归测试（核心已编码、新教训散落）。**

### Q2 Agent 的研究设计有多少是 knowledge-driven？
**少**。实证：本轮 Food–Pathway–Phage 课题的科学问题、敏感性协议、预注册判定规则**全部由人类用户提出**；
planner 未参与设计。执行中 planner 可查 KG（kg_neighbors/evidence tier 强制携带 ✓）与文献（lit_search_read），
但：设计前不查 KnowledgeStore（12 条不可被 agent 访问）、不咨询方法规则（审计只在执行后把关）、
KG miss 不自动转外部。**当前 = "LLM + 人工指令 + 图谱局部先验"；执行层 governance-driven 程度高，设计层 knowledge-driven 程度低。**

### Q3 是否具备 Dynamic Knowledge Acquisition？
**不具备完整能力，有单点雏形**。planner 在会话内可自主调用 lit_search_read（≤3 次/预算内）——这是
"人工预设的 live retrieval 点"。但缺：知识缺口识别（"我不知道"）、KG miss → 外部源自动降级路由、
候选证据投产管线（europepmc 适配器从未运行）。

### Q4 距离 Knowledge-driven Research Agent 还缺什么？（P0–P3）

| 级别 | 缺口 | 验收标准 |
|---|---|---|
| **P0-1** | Knowledge Router MVP | kg_neighbors 无结果时自动降级 litread/europepmc，返回值带 source_type=EXTERNAL_LIVE + retrieved_at + database_version |
| **P0-2** | litread provenance 补齐 | 缓存含 retrieved_at；支持 NCBI_API_KEY；失败 retry 1 次 |
| **P0-3** | Method Knowledge Base 起步 | 本轮 10+ 方法教训写成 YAML knowledge package（package=method）入 KnowledgeStore，可 FTS 检索 |
| **P1-1** | 候选管线投产 | europe-pmc source 注册 → litread 结果落 candidate_evidence（review_status 流转） |
| **P1-2** | KnowledgeStore 接入研究循环 | 白名单新增 knowledge_query 动作；planner prompt 声明三知识源 |
| **P1-3** | Research Evidence 统一 schema | findings/evidence_matrix 结构化（study/exposure/target/effect/q/atlas_version/source_type=CURRENT_STUDY） |
| **P2-1** | BioTool schema 参考接入（UniProt/Ensembl 按需） | 新 source 经 SourceDescriptor 注册+人工审核 |
| **P2-2** | vecstore 分层字段 | 每 vector 带 tier/timestamp |
| **P2-3** | 图谱 Food 域落地后的 Router 覆盖度自描述 | KG 能回答"我知道什么/不知道什么" |
| **P3-1** | freshness/confidence 全链路统一 | 检索结果按 (source,timestamp,version) 排序可用 |
| **P3-2** | BioinfoMCP Converter 方法论评估 | 仅当需包外部 CLI 工具时 |
| **P3-3** | curated ingestion 半自动化 | 人工审核保留，机械步骤（去重/实体解析/provenance）自动化 |

---

## 6. 项目成熟度定级（Level 0–6）

| 级别 | 定义 | 本项目证据 |
|---|---|---|
| L0 Code Prototype | — | 已过 |
| L1 Runnable Tools | — | 已过 |
| L2 Integrated Analysis Platform | — | 已过 |
| L3 Real-study Validated | — | 已过（哈尔滨真实课题 36+ 项分析） |
| **L4 Auditable Research Loop** | 执行可审计、可证伪 | **当前所处（初段）**：治理账本逐次记录、四道闸+真实拦截案例、预注册判定+特异性对照**否决**了表面层间联系（falsification 实例）、canonical 冻结+零漂移 delta、negative finding 留档 |
| L5 Knowledge-driven Research Agent | 识别知识缺口→查 Local/Live/Method→设计→证据→反证→改假设，且知道知识边界 | **未达**：P0-1/2/3 + P1-1/2 是 L5 的门槛件 |
| L6 Autonomous Scientific Discovery | — | 远期 |

**L5 达成路径（最小闭环）**：P0-1 Router + P0-3 Method KB + P1-2 工具面接入 ≈ 让 planner 在设计阶段
可问三句话："生物学已知什么（KG）？外部最新什么（live）？怎么可靠地研究（method）？"

---

## 7. 15 条架构原则符合度

| # | 原则 | 现状 |
|---|---|---|
| 1 | 知识不同类不混放 | △ 分层原语齐（tier/source_type/状态机），但 vecstore/散文件未带分层 |
| 2 | 四域分层 | △ 物理上已分（KG/接口/AgentLab/audit），逻辑声明未统一 |
| 3 | API/MCP 是接口不是事实 | ✓ litread 缓存≠入库；候选状态机 |
| 4 | Research Evidence 不自动进 KG | ✓（且无路径） |
| 5 | Live 缓存≠入库 | ✓ |
| 6 | Hypothesis≠Evidence | △ 叙事卡有 tier，schema 未统一 |
| 7 | Evidence≠Established Knowledge | ✓ 候选证据等级只降不升 |
| 8 | 每条知识带 provenance | △ KG✓ 候选✓ litread 缓存✗（无 retrieved_at） |
| 9 | Agent 识别"自己不知道" | ✗ 最大缺口（Router） |
| 10 | 允许证据推翻假设 | ✓ 本轮有实证（层间联系被对照否决并如实降级） |

---

## 8. 总结

系统的**骨架与用户架构愿景的吻合度超预期**：分层原语（tier/source_type/approval 状态机/等级只降不升/
raw hash/缓存分离）在 KG 与 knowledge/ 模块中真实存在且被测试保护；**真正缺的是"接线与运营"**——
知识路由未建、候选管线未投产、方法知识未条目化、研究循环的设计阶段仍是 LLM+人工。L4 已站稳；
通往 L5 的路是明确的 P0/P1 清单，而非架构重构。

---

## 附录 A：正式定级与阶段裁决记录（2026-09-24 用户裁决）

**一、P0 三件套验收通过**（commit 3142e05）：Method KB 可检索 / Live Knowledge
完整 provenance / Knowledge Router MVP，五项核心验收目标全绿。

**二、正式定级：Level 5-min —— Knowledge-driven Research Agent 基础形态**。
依据八项已具备能力：Local 查询 / Live 获取 / Method 约束 / Evidence-Knowledge 隔离 /
provenance 完整追踪 / source-aware retrieval / 知识缺失受控外查 / 当前研究结果不污染
外部知识层。

**三、暂不定义完整 L5**。缺口 = **Knowledge Gap Detection**：Agent 主动识别
"当前研究设计依赖哪些未知知识"，并自主决定查询 KG / 文献 / API / 方法库 / 设计补充分析。
下一阶段主线：**Knowledge Retrieval → Knowledge-driven Research Planning**。

**四、P0 三件套冻结**（接口与行为稳定，后续扩展不得破坏契约）；PubMed 检索质量列 P1
（entity-name retrieval → entity-aware query planning，五类模板：phage-host /
microbe-phenotype / gene-function / pathway-organism / food-microbiome）。

**五、L5-full 能力建设优先级**：
- **P1-1 Knowledge Gap Detector**（最优先）；
- P1-2 Research Planner knowledge-aware 升级（设计→gap检查→自动补齐→再设计的闭环）；
- P1-3 Query strategy optimization（实体感知检索模板）。
并行依赖：图谱侧完成 Source Registry / provenance / Food schema。

**六、长期原则重申**：Research Evidence 不回写 Local KG；下一阶段目标是
"Agent 知道什么时候需要知识，并主动获取知识推动研究设计"。

### P0–P3 缺口清单状态更新

| 项 | 状态 |
|---|---|
| P0-1 Router MVP / P0-2 litread provenance / P0-3 Method KB | ✅ 完成并冻结 |
| P1-1 Knowledge Gap Detector | 🚧 本轮启动（MVP） |
| P1-2 Planner knowledge-aware 升级 | 待 P1-1 稳定 |
| P1-3 检索模板（原 P1 内容并入） | 待 |
| P1 候选管线投产 / KnowledgeStore 全量接入 / Evidence schema | 顺延保持 |
| P2/P3 各项 | 不变 |

---

## 附录 B：项目定位升级（2026-09-24 用户裁决）

项目最高层定位由"bioinformatics Agent"升级为 **Model-agnostic Scientific
Research Harness**（为通用基础模型提供知识/工具/记忆/治理/证据追踪的可复用
Harness）。成熟度视角由 Agent Level 转为 **Harness Maturity（当前 H4-min）**；
P1-2 重定义为 **Harness Research Loop**（客户端无关七步闭环）。完整架构、
六能力评估与最高约束见《Scientific Research Harness Architecture v1.0》
（HARNESS_ARCHITECTURE_V1.md，后续开发最高约束文档）。本报告附录 A 的
L5-min 定级保留为 Agent 视角历史记录。
