# MicrobiomeResearchAgent 规划交接文档

版本：v0.3（MVP 已实现）
生成日期：2026-09-10（v0.1）；2026-09-10 由 Kimi K3 接手更新（见第 9 章接手日志）
状态：**MVP"合成数据最小治理闭环"已实现并全量 85 测试通过、4 条验收全过**（见第 9 章第 23 条）。治理链（ModelRuntime→OPA→PEP→确定性审计→受控工作流→systemd 资源限制→审计账本）贯通；迭代 2 起依次补 LangGraph、Apptainer、知识层、文献检索、MLflow、Web 工作台等（见第 9 章第 23 条"剩余"清单）。

---

## 1. 项目背景与目标

项目X（目标队列肠道菌群+膳食科技部项目）需要评估是否引入"自主科研 Agent"能力。经过对 Kosmos、Biomni、Medea、AutoBA、Agent Laboratory 等现有项目的调研（见附录 A），结论是：

- 该领域尚未成熟到可以直接套用于肠道菌群+膳食课题（微生物组场景基本是空白，通用系统的实证集中在转录组/蛋白组/代谢组）；
- 但该领域暴露的核心风险——**把统计显著性等同于科学价值、过度拟合、幻觉式机制解释**——恰好是本项目 Project04 已经踩过的坑（DMR-FM 假说被 donor-level 分析推翻、国际队列批次混淆等），值得作为设计约束提前规避；
- 因此不采购/移植任何现成系统，而是**自建一个模型可插拔、知识可审计、权限可控的肠道菌群科研 Agent 平台**，作为独立子项目开发，并首先在 项目X 中真实试用（不是仅做演示）。

**关键定位**：这不是"模型 + RAG"的问答系统，而是覆盖知识、工具、执行、工作流、验证治理的完整科研 Agent 平台。RAG（向量检索）只是知识层的一个可选辅助手段，不是架构核心。

---

## 2. 项目边界与路径

- **独立项目根路径**（已批准）：
  `~/work/Project/projroot/项目X/MicrobiomeResearchAgent/`
- 该路径下开发的代码、配置、环境声明、运行入口均集中管理；大体量制品（容器镜像、参考数据库、向量索引缓存等）**不**放入该路径下的 Git 仓库，具体外部存储路径将在开发落地前单独列出，取得用户批准后才能创建。
- 项目X 现有数据、代码、结果目录（`Data/`、`Code/`、`Method.md` 等）对本平台默认**只读**，任何新目录/路径创建仍需遵守 CLAUDE.md 的路径审批规则。
- 本文档仅记录规划共识，**不代表已执行任何开发或部署操作**。

---

## 3. 强制开发治理规则（继承自 CLAUDE.md + 本次讨论新增）

### 3.1 文档与代码文件创建规则（CLAUDE.md 原有）
- 不主动创建说明性文档，除非用户明确要求。
- 保持代码文件精简，不随意新建文件。

### 3.2 路径创建审核规则（CLAUDE.md 原有）
- 任何新目录/路径创建（尤其数据路径、下载目标路径）必须先说明具体路径清单，取得明确同意后才能执行 `mkdir` 或写入操作。

### 3.3 任务执行分工规则（CLAUDE.md 原有）
- CC 作为总协调者拆解任务，Codex 优先并行执行 → 失败转 Gemini → 都失败先报告用户，不自动兜底。
- 执行完成结果需 CC 与 Codex 共同审核准确性，发现问题主动修改优化，审核结果需向用户说明。

### 3.4 Reuse-First 强制开发政策（本次新增，MicrobiomeResearchAgent 专用）

**这是本项目最重要的新增规则**：开发任何新增能力（工具、知识连接器、模型适配器、工作流节点）之前，必须先完成 **Reuse Review** 记录，包含：

1. 要解决的功能问题和边界；
2. 检索记录：在 GitHub、PubMed/PMC、Bioconductor/Bioconda、nf-core/bio.tools、Hugging Face 等使用的关键词和日期；
3. 候选项目/论文/工具链接；
4. 功能匹配度、许可证与再分发限制、数据/隐私边界、安全风险、维护状态、可复现性、领域证据、迁移/退出成本；
5. 最终决策：直接采用 → 适配封装 → 组合融合 → **仅当以上均不满足时才允许最小范围自研**，并写明理由。

**例外**：安全漏洞、数据损坏、阻塞性故障可以先修复，但修复后必须补做 Reuse Review。"重新实现已有能力只是因为更方便"不构成自研理由。

---

## 4. 总体架构分层

```
研究人员（PI / 统计审核员 / 数据管理员 / 平台管理员）
        │
   本地 Web 工作台（首版主界面，回环地址）── CLI（仅平台管理员维护用）
        │
   LangGraph 受控工作流（角色化节点，非自由多 Agent 协作）
        │
   ┌────┴─────────────────────────────────┐
   │                                        │
ModelRuntime（模型可插拔接口）        OPA/Rego 权限决策（PDP）
   │                                        │
   └──────────────┬─────────────────────────┘
                   │
          统一 Python 执行拦截点（PEP，唯一强制执行入口）
                   │
   ┌───────┬───────┼───────┬────────────┐
   │       │       │       │            │
 知识层  工具层  执行层  审计/溯源层   数据/结果边界
(SQLite (Nextflow (Apptainer (审计账本+ (只读源数据+
 +包)   +注册表) 双档案)  MLflow+PROV) 最小投影+staging)
```

**关键原则**：模型可以随时更换，但知识、工具适配器、统计审计规则、权限策略和证据溯源是长期资产。OPA 权限决策独立于模型和 MCP；MCP 只负责工具/上下文接入，不是模型协议。

---

## 5. 已确认的关键决策（逐条列出，均已用户确认）

### 5.1 自主权边界：多维权限矩阵（非单一 L0-L5 等级）

采用"研究阶段 × 能力权限 × 输出状态"三维模型，拆成五条独立权限轴：

1. **数据权限**：能否读取/写入/导出哪些数据；
2. **执行权限**：能否运行代码、安装依赖、消耗多少资源；
3. **网络与凭据权限**：能否访问公网、数据库、API 密钥；
4. **研究决策权限**：能否改变分析计划、协变量、统计模型、验证集；
5. **结论与发布权限**：能否写入知识库、生成最终结论、对外发布。

输出状态区分："建议草案" / "可执行结果" / "候选发现" / "已验证结论"。Agent **可自主执行、不可自主定论**——候选发现不能自动升级为已验证结论或领域知识。

> 注：调研中 Gemini 提出的 L0-L5 分级、Nature AMR 论文引用（`s41586-024-00000-0`，经核实为 404 死链）、"AI Scientist 耗尽 TB 级磁盘"等说法未获一手证据支持，已被 Codex 审计剔除，不作为设计依据。

### 5.2 权限载体：OPA 为核心

- **策略唯一事实源**：经人工评审、Git 版本化、签名发布的 OPA bundle（Rego 策略 + 资源分类 + 角色映射 + 输入 schema）；
- **PDP（决策点）**：本机 OPA 进程，返回允许/拒绝/需审批/资源上限/允许挂载范围；
- **PEP（执行拦截点）**：统一受信任 Python 控制程序，所有数据读写、容器启动、网络、凭据、计划变更、结果晋升、发布都必须经过它；Agent 不直接拥有项目写权限、容器控制接口、网络或凭据；
- **人工审批状态**：LangGraph + SQLite checkpointer，与 OPA 策略是两个系统（策略回答"这类操作谁能批准"，审批状态回答"这一次具体请求是否已批准"）；
- 为何不用 Cedar：Cedar 官方 Python 集成路径和决策日志控制面均不如 OPA 成熟（详见附录 B 审计结果）。

> **2026-09-10 修订（第 9 章 Q17，用户确认）**：bundle 中的"角色映射"降级——本项目当前为**单人使用**，执法主体简化为 **human / agent 两类**；原"四类逻辑角色"降级为审批收据上的**可选签核标签**（仅审计追溯，不做权限路由）。策略决策改为**三档**：直接放行 / 需人审批 / 仅限人。资源分类、输入 schema、签名发布、决策日志等机制不变。

### 5.3 审批角色（四类逻辑角色 + 职责分离）

1. **PI/课题负责人**：研究问题、分析计划、纳排、结论晋升、对外发布；
2. **数据管理员**：数据接入、字段级投影、导出；无权批准科学结论；
3. **统计审核员**：统计单位、批次混杂、伪重复、多重检验、验证设计；无权改数据边界或发布；
4. **平台管理员**：策略 bundle、镜像、工具白名单、资源配额；无权读研究数据或代替 PI 审批。

**强制职责分离**：改冻结分析计划、候选结果晋升、数据导出/发布、权限策略变更后审批高风险运行——以上动作不得由同一人同时发起并批准。一人可兼任多角色，但每次审批需记录当时角色。

> **2026-09-10 修订（第 9 章 Q17，用户确认）**：本节四角色 + 职责分离是针对**多用户团队**的设计；本项目当前为**单人使用**，执法主体简化为 human/agent 两类，四角色降级为审批收据的**可选签核标签**（保留审计追溯价值），职责分离的"不同人"要求不再适用（单人无 SoD）。本节保留为未来多用户扩展时的角色基线。

### 5.4 数据与结果边界

- 项目X 现有原始数据、处理结果、脚本默认**只读**；
- 不整体挂载项目目录；由数据管理员按获批分析计划生成**最小化数据投影**（必要样本+字段+协变量）；
- Agent 产物先写入独立的隔离 **staging** 区域，不得覆盖 项目X 已有文件；
- 只有通过 QC、统计审计和相应审批后，才允许 PEP 执行一次"**结果晋升**"操作，把固定哈希的产物写入正式位置；
- 首版**禁止**删除、移动、覆盖既有数据与结果。

### 5.5 知识架构：四类知识包 + SQLite 核心，图谱后置

**首版只接收人工批准、版本化的四类知识包**（禁止自动全仓扫描/自动灌库）：

1. **平台治理包**：权限规则、审批规则、数据分级、模型升级门、工具白名单；
2. **微生物组方法包**：组成数据处理、零值策略、批次/混杂/伪重复审计、多重检验、外部验证规则（规则而非唯一正确答案）；
3. **已确认反证经验包**：例如 DMR-FM 假说被 donor-level 推翻、国际队列批次混淆等，每条须标注适用范围；
4. **项目X 研究上下文包**：已批准的研究问题、术语、队列描述、数据字典摘要、方法计划、正式结论；**不含真实样本级数据、原始问卷、完整丰度矩阵**。

**存储架构**：版本化知识包文件（原始事实载体）+ **SQLite 结构化索引与 FTS5 全文检索**（包版本、证据等级、适用条件、来源、审批、废弃状态）为核心；向量检索仅作为长文本语义召回的可选辅助，命中必须回链原始条目；**知识图谱/GraphRAG 延后**到积累足够人工复核关系后再建。

外部文献/数据库检索结果只是"候选证据"，带来源和日期，**不自动升级为内部知识**。

### 5.6 首版工作流：LangGraph 角色化节点（非自由多 Agent 协作）

固定六个角色节点，每个节点独立模型角色、工具白名单、输入输出 schema、OPA 权限边界：

1. **研究协调/规划**：转化研究目标为任务计划和审批点；不能改计划或执行代码；
2. **证据检索与核验**：检索文献/数据库，输出带来源定位和证据等级的事实；不能把摘要直接升级为知识；
3. **数据契约与方法设计**：定义输入需求、统计单位、协变量、缺失值策略、方法候选；只能提草案；
4. **受控执行**：PEP 批准后调用预注册工具/受限代码，产生 staging 产物；不能自装依赖、扩大数据范围、联网或写正式结果；
5. **统计与反证审计**（**必需节点**）：检查统计单位、批次混杂、伪重复、成分数据处理、多重性、泄漏、零模型、敏感性分析；职责是主动找"这个结果为什么可能是错的"；
6. **可视化与科学写作草稿**：生成图表规格、可审计图表草稿、结果表、论文段落草稿；**不得**把候选结果措辞为已证实机制，不执行投稿。

节点间只通过 Pydantic 校验的结构化对象传递信息，不能自由聊天或自行扩权。

> **2026-09-10 修订（第 9 章 Q14，用户确认）**：本节"六角色 persona 节点"的编排方式已松绑——六个角色不再作为固定 LLM 人设节点存在。治理角色（5.3）保留为确定性 OPA 权限信封；统计审计改为"确定性检查器 + 可选对抗复核"，不再是独立 LLM 审计人设。本节原文保留作为演进记录，**实施以新增 5.15 为准**。

### 5.7 模型可插拔层

> **自有窄 `ModelRuntime` 接口 + LangChain 首选适配 + LiteLLM 可选 SDK Router（首版不部署独立 Proxy）+ Pydantic/Instructor 结构化校验 + MCP 仅用于工具/上下文 + 模型升级必须通过回归门。**

- `ModelRuntime` 只负责请求/响应、工具调用、结构化输出、多模态、流式、能力声明、usage/cost、重试/fallback 记录、provider/模型版本记录；
- **不负责**工作流状态、科研知识、统计语义、工具执行、权限判断；
- 模型升级**不能自动切换生产版本**：允许自动 fallback 的范围仅限超时/限流/临时错误，且备用模型须满足同一能力契约；正式升级需固定回归集（工具调用/schema、引用溯源、统计审计、OPA 拒绝越权、成本延迟、失败恢复）+ canary/shadow + 显式批准 + 可回滚。

### 5.8 执行器：Apptainer 优先，Docker 可选后端

用户质疑成立：目标主机很可能没有 root 权限，rootless Docker 依赖 `subuid/subgid` + cgroup v2 + systemd delegation，落地阻力显著大于 Apptainer（Apptainer ≥1.1.0 默认即为 rootless，只需内核支持 user namespace）。

- **可信分析轨**：预先构建、人工审核、固定 SHA-256/签名的 SIF 镜像，供 Nextflow 和固定生信工具使用；输入只读投影，输出只写 staging；`--containall --cleanenv`；不挂载项目根目录/`$HOME`/容器控制接口/凭据目录；
- **动态代码轨**（Agent 生成 Python/R 代码）：**首版默认禁用**。只有当目标主机的网络隔离、CPU/内存/进程/磁盘限制被平台启动自检**实际验证生效**后，才允许开启，且仍需 PEP 生成结构化任务、不接受 Agent 自由传参；
- Apptainer 默认会继承宿主 `$HOME`、当前目录、环境变量和网络命名空间，必须显式加固，不能假设默认安全；
- OCI→SIF 转换在不同机器上产生的 SHA-256 不保证一致，**生产环境不应现场转换镜像**，应使用预构建并登记哈希的 SIF；
- Docker（+ 可选 gVisor）作为管理员配合度高的服务器上的备选/未来升级路径，不是首版依赖。

### 5.9 部署与依赖锁定

> **`uv`（Python 锁定）+ `renv`（R 锁定）+ Docker Compose（仅管理平台服务，非必需）+ Nextflow（生信 DAG）+ Apptainer（执行器）**

- `uv.lock` 只锁 Python 包，不锁 R/OS/内核；`renv.lock` 只锁 R 包及来源，不锁 R 本身/编译器/BLAS；
- Git 只保存源码、配置、lockfile、镜像 digest、参数 schema、小型测试夹具；
- **不提交**：`.venv/`、`renv/library/`、容器缓存、`.sif` 文件、大型参考数据库、真实数据；
- Nix/Devbox、Temporal、OpenFGA、Kubernetes、HashiCorp Vault（当前主线 BUSL 非纯开源）**首版不引入**，作为后续扩展选项（Vault 可用 OpenBao 替代）。

### 5.10 交互界面

- **首版以本地 Web 工作台为主**，默认只监听回环地址（不开放局域网/公网）；
- CLI 仅供平台管理员维护（策略 bundle 发布、镜像准备、诊断、应急暂停）；
- Web 工作台呈现：运行前权限清单/执行计划、待审批队列及计划差异、运行状态、候选结果与统计审计结论、图表草稿与证据溯源、知识包版本、权限矩阵只读视图；
- **前端不构成安全边界**，所有授权判断仍在后端 OPA/PEP 重新核验。

### 5.11 身份与部署边界

- 首版单机部署，具名本地账号对应四类角色（可一人兼任，但审批记录绑定当时角色）；
- 不引入机构 SSO/OIDC/TLS 终止/多租户隔离；未来多人协作时再扩展。

> **2026-09-10 修订（第 9 章 Q17）**：具名本地账号不再对应四类角色；改为两个主体（human/agent）+ 可选签核标签。未来多人协作再扩展回角色模型。

### 5.12 首个真实工作流与黄金基准（重要：已推迟，非当前范围）

- **首个目标工作流**定义为："项目X 已批准数据上的可审计菌群—膳食关联分析闭环"（检索证据→方案草案→人工审批→沙箱执行→QC/统计审计→图表与结果草稿→暂存→人工决定是否晋升）；
- **黄金基准验收方式**已确认（硬条件 + 指标容差 + 差异报告 + 不自动确认科学结论），但**具体选哪个流程作为黄金基准本身被用户明确否决为"当前不做"**：因为 项目X 目前**没有已完成、已冻结的菌群—膳食关联分析结果**可用于复现（只读盘点发现：现有的是 FFQ 清洗、营养换算、Goldberg 筛查、MetaPhlAn4/HUMAnN3 交付矩阵和一份数据审计，但关联模型尚未实现，原始清洗脚本也不能直接重跑）；
- 因此正确顺序是：**先完成平台基础设施 → 项目X 在正常科研推进中完成并冻结真实分析流程 → 再从中遴选黄金基准 → 用黄金基准测试平台 → 复现通过后开放受限探索**。
- 后续开发人员**不应**跳过这个顺序，直接拿当前未完成/未冻结的数据当作验收基准。

### 5.13 首版交付范围（基础设施优先 + 合成数据演示）

**首版做**：
- 权限策略与审批状态机；
- 模型运行时和模型评测/升级门；
- 受控工具注册、数据投影与 staging 接口；
- Apptainer 双档案（可信分析轨 + 默认禁用的动态代码轨）；
- 文献受控检索、四类知识包管理（SQLite 为核心）；
- 审计账本、MLflow、RO-Crate/PROV 快照；
- 本地 Web 工作台 + 管理员 CLI；
- 一套使用**合成数据和模拟工具**的端到端安全演示。

**首版不做**（后置）：
- 真实 项目X 数据接入；
- 全量知识图谱/GraphRAG；
- 全部组学工具适配（QIIME2/MetaPhlAn/HUMAnN 全套）；
- 自动关联发现；
- 正式论文投稿流、期刊模板排版、最终 PDF 投稿包。

可视化与写作节点首版只交付：结构化图表规格、可审计图表草稿、结果表/图注草稿、区分描述性/探索性/已验证结论的论文段落草稿。

### 5.14 首版验收标准（Q1-Q5，均按推荐方案确认）

1. **固定可信分析档案权限边界**：PEP 按已批准计划生成最小输入投影；SIF 非 root 运行；输入只读；只挂载指定参考资源和 staging 输出；禁止挂载项目根/`$HOME`/容器控制接口/凭据目录；写正式目录只能通过"结果晋升"操作。
2. **SIF/参考数据库制品管理**：项目内只保存清单（来源/版本/哈希/签名/许可证）；实际制品存于项目外受控目录（具体路径部署前另行审批）；不提交 Git。
3. **主机无硬资源限制时的处理**：平台启动自检必须确认 CPU/内存/进程/磁盘/超时限制确实生效，否则拒绝执行任何资源密集型任务；仅允许经确认的极小合成数据单元测试例外，且不得接触真实数据或正式目录。
4. **首版验收定义**（不依赖 项目X 黄金基准）：用合成数据+故障夹具做端到端验证，覆盖——
   - 未授权读/写/导出/网络/凭据请求均被拒绝；
   - 审批前无副作用，审批后只执行固定哈希请求；
   - 任务只能访问获批投影和 staging；
   - 容器资源限制/超时/无网络策略真实生效；
   - 模型替换后工具调用/结构化输出/拒绝越权回归通过；
   - 失败任务可暂停/恢复/过期/回滚；
   - 每次运行留痕：策略版本、审批 receipt、模型/工具/镜像版本、输入输出哈希、审计事件；
   - **任一安全硬门失败，禁止进入 项目X 真实数据试用**。
5. **首版工具/环境范围**：最小固定工具集+可重建环境——Python（数据契约/表格/统计/图表/结构化结果）、R（一个固定统计入口 + `renv` 锁定）、Nextflow（一个最小可审计流程）；只接入首个工作流实际需要的工具，不全量接入 QIIME2/MetaPhlAn/HUMAnN 和全部数据库。

### 5.15 Harness 分层（2026-09-10 新增，Q14/Q15 用户确认）

模型加速演进下，把"对模型能力的假设硬编码进 prompt/人设"的 Harness 会随模型变强而过时（Anthropic《Scaling Managed Agents》2026-04：*harnesses encode assumptions that go stale as models improve*）。据此把"角色"拆为两类分别处置：

1. **治理角色（PI/数据管理员/统计审核员/平台管理员）= 确定性权限信封**：由 OPA/Rego + PEP 强制，非模型、不进提示词，不束缚模型智力；**保留不变**（5.1–5.4）。
2. **工作流人设（原 5.6 六节点）= 薄 Harness，松绑**：改为"**单个研究 Agent 在权限信封内自主工作** + 审批门（PEP/OPA）+ **确定性审计检查**（代码规则）+ 按需加载的知识/方法 skills"。统计审计不再是独立 LLM 人设节点，而是**确定性规则检查器**（伪重复/批次混淆/多重检验/数据泄漏/成分数据处理等）+ 可选的一次**对抗性复核**（策略门控触发，参照 wisp `audit-biomedical-paper-evidence` 的范围守卫与反幻觉措辞）。LangGraph 仍作为审批状态机底子（`interrupt()` + SQLite checkpointer），但节点不再是 persona。
3. **Harness 版本化**：系统提示词、skills、工具 schema 作为版本化、可回归测试、可替换的构件，纳入 5.7 模型升级门同款机制；模型换代时只换薄 Harness，OPA/PEP/session/审计账本等稳定接口不动。

依据（一手，已核实）：Anthropic《Building Effective Agents》2024-12（简单可组合优于复杂框架）；《Effective Context Engineering》2025-09（system prompt 写对"海拔"、避免脆弱硬编码逻辑、模型越强越少规定式工程、渐进披露）；《Scaling Managed Agents》2026-04（大脑/手/会话解耦：接口稳定、实现随意换）。第 7 章"流畅性掩盖不确定性、必须用领域零模型审计"的教训是第 2 点"确定性审计"的直接依据。独立第三方对照证据暂缓补充（Q16），方向定稿后按需补。

---

## 6. 尚待接手团队进一步细化的开放事项

以下事项已有方向性共识，但**尚未细化到可直接编码的程度**，需要接手开发人员在实施前进一e步设计并确认（部分建议回到用户处二次确认）：

- OPA Rego 策略的具体 schema 设计（角色、资源分类、动作枚举的完整字段定义）；
- LangGraph 状态机的具体 State 结构、每个节点的输入输出 Pydantic 模型；
- ModelRuntime 接口的具体方法签名与能力声明格式；
- 合成数据集与故障夹具的具体设计（需要覆盖哪些异常场景）；
- ~~本地 Web 工作台的技术栈选型~~ **已定（2026-09-10，第 9 章 Q5）**：FastAPI 后端 + 轻前端（服务端渲染/HTMX 路线），不引入 Node 构建链与独立 SPA；安全边界仍在后端 OPA/PEP；
- ~~主机资源隔离能力的实际验证结果~~ **已完成（2026-09-10，见下方"主机只读核查结果"）**；
- SIF 镜像制品和参考数据库的具体外部存储路径（需按 CLAUDE.md 规则单独提出并取得批准；注意 `/data` 已用 93%，选址需评估容量）；
- 知识包的具体 SQLite schema 和 FTS5 索引设计；
- Reuse Review 记录的具体载体格式（建议做成 PR/任务模板的强制字段，具体格式待定）。

### 主机只读核查结果（2026-09-10，Kimi K3 执行，只读 + 一次瞬态 systemd scope 实测）

| 检查项 | 结果 | 结论 |
|---|---|---|
| Apptainer | 1.4.5（≥1.1.0，默认 rootless） | 可信分析轨可行 |
| 非特权 user namespace | 已开启（`unprivileged_userns_clone=1`，`max_user_namespaces=6190576`） | Apptainer 前置条件满足 |
| cgroup | v2（cgroup2fs），user slice 已委托 `memory pids` 控制器 | 资源限制有内核基础 |
| 资源限制实测 | `systemd-run --user --scope -p MemoryMax=100M -p CPUQuota=50%` **真实生效**（memory.max=104857600） | 5.14-Q3"自检确认限制生效"有现成实现路径（systemd --user scope 包裹执行） |
| Docker daemon | socket 无权限访问 | 反向印证 5.8"Apptainer 优先"决策正确 |
| Nextflow / Java | 均未安装（Nextflow 依赖 Java） | 接入前需安装，届时按路径审批规则单独申请 |
| OPA | 未安装 | 同上 |
| R | 4.5.0 已装 | renv 锁定链可行 |
| uv / git | 已装 | 依赖锁定与版本管理可行 |
| 磁盘 | `/data` 已用 93%，剩余约 9.8T | 制品外部存储选址需评估容量 |

**强调**：以上任何一项在编码前，仍应遵循本文档第 3.4 条 Reuse-First 规则，先检索是否有可直接复用的成熟方案。

---

## 7. 关键审计纠正记录（供后续人员避免重复踩坑）

1. **Kosmos 不适用于微生物组场景**：其原始论文（arXiv:2511.02824）7 个案例集中在代谢组学/材料科学/连接组学/统计遗传学/蛋白质组学，**不含微生物组**；独立第三方评测（arXiv:2511.13825，Henry Ford Health 团队，与原作者无关联）用"证伪审计法"重新核验其 3 个假设，1 个证实、1 个"似是而非但不确定"（疑似过拟合）、1 个被证明是假的（DDR-p53 假设与随机噪声无法区分）。核心教训：**AI 生成解释的"流畅性"会掩盖底层不确定性**，必须用领域零模型审计。
2. **Gemini 曾提供的伪造/夸大证据，已被 Codex 审计剔除**，不应再被引用：
   - `https://www.nature.com/articles/s41586-024-00000-0`（404 死链，非真实论文）；
   - "AI Scientist 耗尽 TB 级磁盘"（无一手证据，只有"递归自我调用"和"修改超时脚本"两项属实）；
   - "ALCOA+ 要求每行代码单独加时间戳"（错误归因，ALCOA+ 只要求可归属/清晰/同时记录/原始/准确等原则，不是逐行时间戳）；
   - "FDA PCCP = 冻结分析计划"（错误类比，PCCP 实际是预先声明允许的变更范围，不是禁止变更）；
   - "相对丰度必须强制 CLR/ALR、禁止欧氏距离"（过度绝对化，CLR 后欧氏正是 Aitchison 距离，Bray-Curtis 等也可能合理，需按数据尺度和敏感性分析决定）；
   - "必须标签置换检验"（错误，仅在零假设有意义且标签可交换时适用，配对/纵向/簇集数据不可任意打乱）；
   - "L0-L5 自主分级"为项目自定义框架，非行业统一标准。
3. **首版执行器从"Docker+gVisor"改为"Apptainer 优先"**：因为目标主机很可能无 root 权限，rootless Docker 前置条件（`subuid/subgid`+cgroup v2 delegation）在传统科研服务器上很难获得管理员支持，而 Apptainer 设计为无需管理员配置即可运行。

---

## 8. 附录：调研来源摘要

### 附录 A：领域调研（Kosmos 及同类项目）
- Kosmos 原始论文：arXiv:2511.02824
- Kosmos 独立评测：arXiv:2511.13825（Henry Ford Health / Michigan State / Toronto Metropolitan University，与原作者无关联）
- 同类项目候选（许可证已核实）：
  - Biomni (Stanford) — Apache-2.0 — github.com/snap-stanford/Biomni
  - Medea (Harvard MIMS Lab) — MIT — github.com/mims-harvard/Medea
  - AutoBA (KAUST/华为) — MIT — github.com/JoshuaChou2018/AutoBA
  - Agent Laboratory — MIT — github.com/SamuelSchmidgall/AgentLaboratory（有可选人工检查点，非强制）
  - PaperQA2 — Apache-2.0 — github.com/Future-House/paper-qa
  - ChemCrow — MIT — github.com/ur-whitelab/chemcrow-public（README 明说公开仓库不能完全复现论文结果）

### 附录 B：权限/工作流/执行器技术选型审计（Codex 独立核查）
- OPA — Apache-2.0 — 决策日志/bundle 签名成熟，Python 走 REST sidecar
- Cedar — Apache-2.0 — 类型验证强，但缺 OPA 同级决策日志控制面，无官方 Python binding
- LangGraph — MIT — `interrupt()` + SQLite/Postgres checkpointer 支持人工审批
- Apptainer — BSD-3-Clause 及第三方许可组合 — 默认 rootless，但默认继承宿主 `$HOME`/网络命名空间，需 `--containall --cleanenv` 加固
- Nextflow — Apache-2.0；nf-core/tools — MIT
- uv — MIT/Apache-2.0；renv — MIT

### 附录 C：模型可插拔层技术选型审计
- LangChain/LangGraph — MIT
- LiteLLM 核心 — MIT（`enterprise/` 目录例外）
- PydanticAI — MIT；Instructor — MIT
- MCP Python SDK — MIT（主项目规范正从 MIT 向 Apache-2.0 过渡）

---

## 9. 接手开发日志

### 2026-09-10（Kimi K3 接手，用户逐条确认以下决策）

1. **接手策略（Q1）**：接口先行——先细化第 6 章最上游三组接口（① ModelRuntime 方法签名+能力声明；② OPA Rego 策略 schema；③ LangGraph State + 六节点 Pydantic 模型），每项先做 Reuse Review 再定稿，然后才进入编码；主机核查结果回写本文档作为第 0 步（已完成，见第 6 章）。
2. **基线地位（Q2）**：第 5 章 14 条决策视为**冻结基线**，不静默偏离；如发现与现实冲突，以"修订建议+理由"形式提交用户裁决，批准后才改文档和代码。
3. **骨架路径（Q3，已按 CLAUDE.md 规则获批）**：`MicrobiomeResearchAgent/` 内初始化 Git 仓库 + `.gitignore` + `pyproject.toml`（uv 管理）+ `src/mra/` + `tests/` + `policies/`（OPA Rego bundle 源码）。`knowledge/`、`web/` 等目录待对应设计定稿后另行报批；`renv` 待真正接入 R 统计入口时再引入。
4. **首个 ModelRuntime 适配目标（Q4）**：火山方舟 ARK（豆包系列，OpenAI 兼容接口），凭据与 arkcli 工具链现成；后续模型（Kimi/DeepSeek/本地模型）经同一抽象层追加适配器。结构化输出回归门（5.7）以 tool-calling 稳定的模型为基准。
5. **Web 工作台技术栈（Q5）**：FastAPI 后端 + 轻前端（服务端渲染/HTMX 路线）；不引入 Node 构建链与独立 SPA；前端不构成安全边界（5.10 原判不变）。
6. **执行分工（Q6，按用户修正定稿）**：**Kimi K3 主导**接口设计与协调；Codex 做独立评审（延续"共同审核"模式）；批量编码、Reuse Review 检索、测试编写按 CLAUDE.md 规则交 Codex 并行执行，失败降级路径不变（Codex → Gemini → 报告用户，不自动兜底）。
7. **主机只读核查**：已完成并回写第 6 章，该开放项闭环。
8. **辅助环境**：opencode 全局配置已设 Full-Auto 主 Agent（用户明确要求），仅影响开发辅助工具链，与本平台自身的 OPA/PEP 权限体系无关。
9. **wisp-bio 处置（Q7）**：wisp-science 的数据库连接器（约 80 库）**入 backlog，首版不做**；首版证据检索节点用 Python 直连 PubMed/EuropePMC 等 HTTP API（现有 paper-lookup skill 已覆盖 11 库）；确有需求时再评估"wisp-bio 独立进程 MCP 化"（AGPL 不传染的唯一组合通道）。
10. **Reuse Review 载体（Q8）**：定型为 `policies/reuse-reviews/YYYY-MM-DD-<对象>.md`，六段式模板（检索记录→架构对照→许可证→决策→借鉴清单→风险），首份记录 `2026-09-10-wisp-science.md` 已入库。
11. **底盘路线（Q9，用户裁决）**：**路线 C**——wisp-science 原样作为研究人员个人探索"卫星工具"（不修改、不 fork、不承担其 AGPL 与 Rust 维护成本）；本平台按第 5 章既定 Python 组件组装路线自建，设计思想按 Reuse Review 借鉴清单吸收；**首版范围进一步瘦身为 MVP"合成数据最小治理闭环"**（瘦身方案见下条记录，细化后与第 5.13 条并存：5.13 为首版最终形态，MVP 为第一个可演示切片）。
12. **二开 wisp 的否决记录**：用户曾提议以 wisp 为底盘二次开发，经分析（AGPL 整体传染、治理核心在其架构中完全缺失需开膛改造、Rust 栈与团队 Python/R 生态错位、上游高速迭代导致 fork 分化）后，用户裁决不采用。此结论不再重议，除非 wisp 上游发生重大变化（如改许可证、原生支持多角色治理）。

13. **MVP 瘦身方案（Q10，用户确认）**：首版第一个可演示切片定为"合成数据最小治理闭环"——做 7 项（PEP 最小拦截点；OPA sidecar+最小 Rego bundle[4 角色/约 6 动作/默认拒绝]；LangGraph 三节点状态机[规划→受控执行→统计审计]+interrupt 审批+SQLite checkpointer；ModelRuntime 最小接口+ARK 适配器[单模型]；合成数据+1 个模拟分析工具；staging→结果晋升[哈希校验]；SQLite 审计账本），推迟 6 项（Web 工作台[MVP 审批走 CLI]；Apptainer SIF 管线；知识层四类包+FTS5 与文献检索节点；MLflow/RO-Crate/PROV[审计账本先行]；模型升级回归门；Nextflow 与 R/renv，动态代码轨维持禁用）。验收 4 条硬演示：越权拒绝留痕；审批前零副作用+固定哈希执行；资源限制真实生效；审计账本可复盘。新增目录已批：`tools/`（外部二进制，git 忽略）、`var/staging|results|audit/`（运行产物，git 忽略）。5.13 仍为首版最终形态，MVP 为其第一个切片。
14. **MVP 执行器（Q11，用户明示裁决，对 5.8 的 MVP 级过渡而非废弃）**：MVP 受控执行的资源隔离暂用 `systemd-run --user` scope（MemoryMax/CPUQuota 已于 2026-09-10 实测生效）；Apptainer SIF 管线推迟到迭代 2（届时另行报批制品外部存储路径）。依据：MVP 仅跑预注册模拟工具+合成数据、无不可信代码；动态代码轨维持禁用不变。迭代 2 起恢复 5.8 既定路线。

15. **ModelRuntime 接口定稿（Q12，用户确认）**：Reuse Review `policies/reuse-reviews/2026-09-10-modelruntime-adapter-libs.md` 已入库（Codex 检索评估 + Kimi K3 PyPI 一手补核，结论一致）。接口契约 v0.1 落于 `src/mra/model_runtime/`（types.py：ModelRef/Capability/TraceTags/Message/ToolCall/ToolSpec/ModelRequest/Usage/AttemptOutcome/AttemptRecord/ModelResponse；base.py：ModelRuntime Protocol + ModelRuntimeError[携带 attempts]）。行为规则 5 条见 base.py docstring（统一重试记录并关闭底层隐式重试；fallback 仅限 TIMEOUT/RATE_LIMIT/TRANSIENT；结构化输出=tool calling+Pydantic 校验，校验失败记 FATAL；职责边界；凭据不入请求与日志）。能力表 `policies/model_capabilities.yaml`（首条目 unverified，待 ARK 集成回归校准）与价格快照 `policies/model_pricing.yaml`（占位 UNVERIFIED，计费前需回填）已建。依赖：openai==3.11.0 + pydantic==2.13.5 + pyyaml（uv 锁定）；dev：pytest+respx。**MVP 级简化（用户知情确认）**：MVP 用 openai SDK 直连，LangChain 首选适配推迟至 v1；stream/多模态 v1 再进接口。

**下一步**：Codex 并行执行——①ArkRuntime 实现+单测（按已定契约）；②OPA Rego 策略 schema 的 Reuse Review 检索。Kimi K3 随后提交 OPA Rego schema 设计草案供用户确认。

16. **ArkRuntime 实现完成 + OPA 权限层 Reuse Review 完成（2026-09-10）**：
    - `src/mra/model_runtime/ark.py`（Codex 编码，330 行）+ `tests/model_runtime/test_ark.py`（18 用例，Kimi K3 独立复跑 `uv run pytest` 全绿）。覆盖：请求转换、结构化输出（工具调用+Pydantic 校验）、usage 归一化+cost（价格快照）、异常分类（TIMEOUT/RATE_LIMIT/TRANSIENT/FATAL）、重试退避 1s/2s、fallback 能力校验、凭据不落日志。契约文件未改动（SHA-256 校验一致）。
    - 共同审核唯一真缺口"TraceTags 无出向通道"已裁定：留痕持久化由 PEP/审计层按 request_id 关联，TraceTags 不回传（base.py 规则 4 已注明），不改数据结构。
    - OPA Reuse Review `policies/reuse-reviews/2026-09-10-opa-policy-layer.md` 入库：直接采用 OPA 工具链（Apache-2.0）+ 自研五轴 ABAC/SoD；DUO 后置；决策入口 `POST /v1/data/mra/authz/decision` 返回 `allow/approval_required/reason_codes/constraints/policy_revision`；关键风险已记录（PEP 从受信任身份表构造输入、故障即拒绝、SoD 比较用户 ID、input 最小化、OPA 仅 loopback、生产禁 --skip-verify）。
    - OPA 1.20.2 已装至 `tools/opa`（SHA-256 校验），配 `tools/fetch_tools.sh` 可复现下载脚本。
    - 新增目录 `policies/opa/`（bundle + tests）待随 Rego schema 报批。

**下一步**：提交 OPA Rego 策略 schema 设计草案（角色→动作矩阵 + input schema + Rego 包结构）供用户确认；确认后落码 `policies/opa/` 并实现 PEP 最小拦截点，随后设计 LangGraph 三节点 State。

17. **Harness 分层定稿（Q14/Q15，用户确认，2026-09-10）**：采纳"治理角色保留、工作流人设松绑"。治理角色（PI/数据管理员/统计审核员/平台管理员）= 确定性 OPA/PEP 权限信封，保留不变；原 5.6 六节点 persona 工作流松绑为"单 Agent + 权限信封 + 审批门 + 确定性审计检查器 + 按需加载 skills"；新增 5.15 记录。**对 MVP 的修订**：原 item 13 的"三节点（规划→受控执行→统计审计）"中，统计审计节点改为**确定性规则检查器**（代码规则：伪重复/批次混淆/多重检验/数据泄漏等）+ 可选对抗复核，不再设独立 LLM 审计人设节点；LangGraph 仅作审批状态机底子。治理权限矩阵（item 13/待批 Q13）不受影响。
18. **独立证据补充（Q16，暂缓）**：Anthropic 三篇工程博客已核实为方向依据；独立第三方对照证据暂不阻塞，待方向落地后按需补。已确认的 ModelRuntime/ark.py/OPA review 代码与记录不受本次修订影响。

**下一步（Q13 仍待确认）**：①先请用户确认 OPA Rego 权限矩阵 schema（Q13 尚未回复，权限层与 Harness 松绑无关，矩阵原样有效）；②随后按 5.15 设计"单 Agent + 审批门 + 确定性审计检查器"的工作流与 LangGraph 最小 State（确定性审计检查器的规则集需做 Reuse Review——统计检验规则库如 statsmodels 等）；③再落码 PEP 最小拦截点。

19. **权限模型瘦身（Q17，用户确认，2026-09-10）**：本项目为**单人使用**，原四角色+职责分离属多用户设计，按用户裁决瘦身为——执法主体 **human/agent 两类**；四角色降级为审批收据上**可选签核标签**（枚举如 stat_review/release/general，仅审计追溯，不进 OPA 决策）；策略决策改**三档**（直接放行 / 需人审批 / 仅限人）。**保留**：5 条权限轴、staging→结果晋升、候选发现→已验证结论、确定性统计审计。已修订 5.2/5.3/5.11（冻结基线修订横幅），5.1 权限轴不变。Q13 的原 4×6 矩阵就此作废，改由三档动作表替代。

**下一步**：①按 Q17 三档动作表设计并落码 OPA Rego bundle + `opa test` 回归门（Codex 编码）；②Kimi K3 设计 PEP 最小拦截点（受信任输入构造 + OPA 调用 + 审批状态 + 审计账本 + 结果晋升）后交 Codex 实现；③随后设计"单 Agent + 确定性审计检查器"工作流（审计规则集 Reuse Review）。

20. **OPA Rego bundle 落码完成（2026-09-10，Codex 编码 + Kimi K3 共同审核）**：
    - 交付：`policies/opa/bundle/{.manifest,data.json}` + `mra/authz/{decision,receipt,attributes}.rego` + `mra/schemas/input-v1.schema.json`（additionalProperties:false）+ `tests/mra/authz/*_test.rego`。
    - 三档决策已实现：human 全动作放行；agent read_projection 限投影内 / execute_task 限注册任务+network deny+无凭据（返回资源上限 constraints）/ promote_result 需有效收据否则 approval_required / export_data+manage_policy+approve_request 拒绝；默认拒绝。
    - 回归门（Kimi K3 独立复跑）：`opa check --strict --schema` 通过；`opa test --fail-on-empty --coverage --threshold 100` → **17/17 通过、覆盖率 100%**；冒烟 eval 验证 agent 无收据晋升 → `{allow:false, approval_required:true}`。
    - 共同审核修正：①移除 data.json 中 `action_tiers` 死字段（描述性与 decision.rego 硬编码逻辑重复、易误导）；②确认收据校验绑定 request_id+subject_hash+policy_revision（防篡改）；③decision.rego 默认分支 `policy_revision` 用字面量 `draft-1`（Rego default 规则值不能含 ref，属必要 workaround，待首次 git commit 后与 `data.mra.meta.revision` 统一）。
    - 输入结构说明：`request_id`/`subject_hash` 在 `context` 下，收据在顶层 `approval_receipt`（与早前草案 context.approval.receipt 不同，但自洽，PEP 实现以本 schema 为准）。
    - 执行备注：Codex 末段陷入测试语法重复编辑循环（39 分钟无产出），按降级规则由 Kimi K3 终止进程并独立核验磁盘状态全绿，产物未受损。

**下一步**：①Kimi K3 设计 PEP 最小拦截点（受信任输入构造 + OPA sidecar 调用 + 审批状态 SQLite + 审计账本 + 结果晋升），提交用户确认后交 Codex 实现；②随后设计"单 Agent + 确定性审计检查器"工作流（审计规则集 Reuse Review，候选 statsmodels 等）。

21. **审计检查器 Reuse Review 完成 + 合成数据生成器完成 + PEP 设计确认（2026-09-11）**：
    - 审计检查器 Reuse Review `policies/reuse-reviews/2026-09-10-audit-checker-libs.md` 入库：statsmodels（多重检验）/scipy（检验+置换）/numpy（校验+RNG）/pandas（表格）；scikit-bio 0.7.3 BSD-3 条件可用（CLR）、pingouin 0.6.1 **GPL-3 排除**（Kimi K3 PyPI 补核）。MVP 规则清单 5 条（AUDIT-MULT/ID/BATCH/COMP/PERM-001），判定 PASS/WARN/FAIL/REVIEW_REQUIRED/NOT_APPLICABLE，只报可证明结构事实、无法判断绝不当通过。
    - 合成数据生成器 `src/mra/demo/synthetic.py`（Codex 编码，4 测试通过）：200 样本/40 类群，5 真相关（信号系数求和为零防成分闭合泄漏）、批次均值精确匹配、可开关批次混淆。依赖新增 numpy/scipy/statsmodels/pandas。
    - **PEP 设计（Q18，用户确认）**：五职责（受信任输入构造/OPA 决策/审批状态/审计账本/结果晋升）+ 四决策（D1 子进程 opa eval、D2 SQLite 单文件账本两表、D3 内部代码路径定身份、D4 四动作 API）+ 两强路径（execute_task：evaluate→systemd-run scope→staging；promote_result：evaluate→审计检查器→哈希校验→results/）+ 故障语义（OPA 失败即拒绝/写账失败阻断/subject_hash 篡改拒绝）。注：确定性审计检查器由编排层在 promote 前调用，PEP 保持纯拦截点不直接依赖审计模块（解耦以并行实现）。
    - **git checkpoint**：两次提交（888eac8 骨架+已核验组件；58e5e50 合成数据+审计选型）。policy_revision 仍 draft-1，待 bundle 签名/版本策略定稿后改 git:<sha>。
    - **环境发现**：Codex `workspace-write` 沙箱屏蔽 PyPI 网络（numpy 下载失败）——后续 Codex 编码任务一律"禁网、仅用已装依赖"，依赖由 Kimi K3 用 uv 预装。

**下一步（Phase 2 并行进行中）**：①Codex 实现 PEP 核心（evaluate/approve/execute_task/promote_result + 审计账本 + 子进程 opa eval，hermetic 测试）；②Codex 实现确定性审计检查器（5 规则）；两者互不依赖并行。③完成后进入 Phase 3：单 Agent 工作流（LangGraph 最小 State，依赖 PEP+合成数据）；④端到端合成数据演示 + 4 条 MVP 验收。

22. **PEP + 审计检查器实现完成（2026-09-11，Codex 编码 + Kimi K3 共同审核）**：
    - `src/mra/pep/`：OpaClient（子进程 `opa eval --stdin-input`，输入走 stdin 不落临时文件；OSError/超时/非0退出/缺 result/决策矛盾态[allow&&approval_required]一律 fail-closed；build_input 严格校验 input-v1.schema 的枚举与字段白名单）；AuditLedger（SQLite 两表 audit_events/approval_requests，WAL，先记账后执行副作用）；Pep 门面（evaluate/request_approval/approve/query_audit/execute_task/promote_result）。安全要点已实现并测试：收据**从账本取信**（调用方提供的收据与账本不符→视为无收据）；execute_task 无 shell、精简环境（不含密钥）、constraints 白名单仅 network_mode/credential_refs；promote_result 路径穿越防护（_safe_request_component + relative_to staging 边界）+ 哈希校验（不符→DENY_SUBJECT_HASH_MISMATCH 记账）+ 独占写防覆盖。
    - `src/mra/audit/`：5 规则实现（AUDIT-MULT/ID/BATCH/COMP/PERM-001），判定 PASS/WARN/FAIL/REVIEW_REQUIRED/NOT_APPLICABLE，statsmodels 重算校正值、scipy Fisher/卡方、numpy 复算 CLR，全部确定性可复现。
    - 测试：**全量 78 通过**（ark 18 + synthetic 4 + audit 31 + pep 25）；PEP 含真实 tools/opa 集成测试（缺失则 skip）。
    - 共同审核结论：代码安全正确、无需修改。已知 MVP 边界：execute_task 默认 runner 仅 subprocess+超时，**OS 级内存/CPU/网络隔离留待 systemd/Apptainer 集成层**（对应 Q10 验收"资源限制真实生效"，需集成层落地后才能满足）；审计检查器由编排层在 promote 前调用（PEP 保持纯拦截点，符合解耦设计）。
    - git：700c321 提交 PEP+审计检查器。

**下一步（Phase 3）**：①单 Agent 工作流（LangGraph 最小 State + Agent 循环，依赖 PEP+ModelRuntime+合成数据+审计检查器，全部已就绪）；②实现 execute_task 的 systemd-run scope 集成层（满足资源限制验收）；③端到端合成数据演示 + 4 条 MVP 验收（越权拒绝/固定哈希执行/资源限制/审计复盘）。

23. **MVP 闭环完成（2026-09-11，Codex 编码 + Kimi K3 共同审核）**：
    - 工作流设计（Q19 确认）：D-A **LangGraph 暂缓**（MVP 用确定性编排 + PEP 审批态，interrupt/checkpoint 迭代 2 引入）；D-B 双层（E2E 演示确定性脚本驱动、真实 ARK 模型 smoke 单独可跳过）；D-C 受控工具集即 ACI。
    - `src/mra/workflow/`：受控工具层 tools.py（run_registered_task/audit_results/propose_promotion，JSON Schema 暴露供未来 tool-calling）+ 编排 orchestrate.py（确定性闭环；工作流**独占 --seed/--out 防参数重定向**；产物与 seed 绑定期望逐字节校验；审计 FAIL 即阻断不 propose；晋升留调用方在 approve 后显式执行）。
    - `src/mra/pep/systemd_runner.py`：systemd-run --user --scope 施加 MemoryMax/CPUQuota（Kimi K3 实测：64M 限制下 2GB 分配被 OOM 杀死 exit=-9；小任务正常）。
    - `src/mra/demo/mock_analysis.py` + `tests/e2e/test_demo.py`：4 条 MVP 验收全部落地（A 越权拒绝并留痕 / B promote 哈希绑定+篡改拒绝 / C systemd 资源限制 / D 审计账本复盘 deny+allow+收据+双摘要）。
    - **全量 85 测试通过**（ark 18 + synthetic 4 + audit 31 + pep 25 + systemd 2 + e2e 5）。
    - git：7ab9918 提交。**MVP"合成数据最小治理闭环"到此实现完毕**。

**MVP 验收结论（对照 Q10 四条）**：①越权拒绝 ✅（agent export_data/unregistered task → deny+记账）；②审批前零副作用+固定哈希执行 ✅（promote 篡改哈希→DENY_SUBJECT_HASH_MISMATCH，工作流产物逐字节校验）；③资源限制真实生效 ✅（systemd scope OOM 实测）；④审计账本可复盘 ✅（策略版本/审批收据/输入输出摘要可查）。**任一安全硬门均通过 → 具备进入真实数据试用的治理基础（但按 5.12/5.14 顺序，真实数据接入仍需 项目X 冻结关联流程后才启动）。**

**剩余（迭代 2 起）**：LangGraph interrupt/checkpoint（长任务暂停/恢复）；Apptainer SIF 双轨（外部制品路径另行审批）；知识层四类包+FTS5；文献检索节点；MLflow/RO-Crate；模型升级回归门；真实 ARK 模型 tool-calling smoke 与 ModelRuntime 升级门；Web 工作台（FastAPI+轻前端）。

24. **真实 ARK 集成验证完成（2026-09-11，优先级 1 核心假设验证）**：
    - **关键发现**：用户提供的 ARK key 是 **Coding Plan 套餐 key**——正确端点为 `/api/coding/v3`（非 `/api/v3`），模型用**短名**（`doubao-seed-2.0-lite`/`kimi-k3`/`deepseek-v4-pro` 等）。`/api/v3` 直接调模型 ID 返回 `InvalidEndpointOrModel.NotFound`，`/api/coding/v3` 走短名正常。
    - **实测通过**（真实端点 + ArkRuntime 真码）：`doubao-seed-2.0-lite` 的结构化输出（tool calling + Pydantic 校验 `Finding(organism='Akkermansia', significant=True)`）、工具调用（`lookup_sample` 正确传参）、usage 归一化（input/output tokens + missing 细项列表）全部正常。
    - 修正：ark.py 默认 base_url → `/api/coding/v3`；model_capabilities.yaml 回填 verified 条目。全量 85 测试不回归。
    - **凭据纪律**：key 仅经环境变量 `ARK_API_KEY` 传递，未写入任何文件/git/日志。提醒用户：key 已出现在对话中，如需可轮换。
    - 结论：**核心假设成立**——真实模型在 ModelRuntime + 受控工具 + PEP 信封内可正常驱动（越界拦截属 PEP 确定性机制，与模型无关，已由 85 测试覆盖）。

**下一步**：①安全复审（Codex 对抗视角，进行中）；②据此修补发现的漏洞；③按优先级 3 协调 项目X 冻结（黄金基准+真实数据）；④迭代 2 基础设施按需推进。

25. **安全复审 + 漏洞修复完成（2026-09-11）**：
    - **Codex 对抗视角审查**发现 8 类问题，Kimi K3 逐条复核分三档：🔴必修（B 身份伪造 / G 任意命令 / F env 泄漏）、🟡低危（D TOCTOU）、⚪暂缓（A 网络隔离、E 账本篡改、C 审批人机认证——依赖迭代 2 的 OS 级隔离/多用户；H human 全放行为 Q17 确认的设计）。
    - **修复（commit 96ce663）**：B——execute_task 固定 agent、promote_result 固定 human 且强制账本审批绑定（request_id/action/subject_hash/policy_revision 全校验，DENY_APPROVAL_INVALID）；G——execute_task 命令白名单（受信任 task_registry，调用方不可传 command/args）；F——systemd runner 最小环境排除 ARK_API_KEY；D——单 fd 哈希+复制 + fstat 常规文件校验封死 TOCTOU。
    - **共同审核发现并修正的回归**：F 修复最初剥掉 XDG_RUNTIME_DIR/DBUS_SESSION_BUS_ADDRESS 导致 systemd-run --user 连不上用户总线（Codex 沙箱无 systemd-run 故测试被跳过掩盖）；Kimi K3 在真实主机复跑发现，补回总线变量后恢复。**教训：依赖宿主能力的测试（systemd）必须在真实环境复跑，不能只信沙箱结果。**
    - 全量 89 测试通过。**仍待迭代 2**：网络隔离（Apptainer）、审计账本签名/哈希链、approve 人机认证（多用户时）。
    - **结论**：单机 MVP 安全边界已达到"Agent 无法伪造身份、无法任意执行、无法窃取密钥、无法篡改晋升"的目标；生产级（多用户/不可信代码）需迭代 2 的 Apptainer + 独立审计账户补足。

**下一步**：①协调 项目X 冻结关联分析（黄金基准+真实数据试用，优先级 3）；②迭代 2 基础设施（LangGraph/Apptainer/知识层/文献检索/MLflow/Web）按需推进；③可考虑把安全复审固化为 CI 门（每次提交跑 opa test + pytest + 对抗复审清单）。

26. **战略重构 + 黄金基准调研 + 路线定稿（2026-09-11）**：
    - **认知纠偏（用户决策）**：项目X 不再是黄金基准，而是平台建好、测试通过后的**第一个真实使用项目**。理由：黄金基准必须"有已知真值"，而 项目X 是"无答案的新研究"。科研（项目X）由用户在另一终端推进，本平台专注开发完毕。
    - **Q5 定稿**：平台"开发完毕"范围 = 5.13 首版全量（Apptainer 双轨 / LangGraph / 知识层四类包+检索 / 文献受控检索 / MLflow+RO-Crate / 模型升级门 / Web 工作台+CLI），验收达到 5.14 完整 5 条。
    - **Q6 定稿（建设顺序）**：Web 工作台+CLI → Apptainer 双轨 → LangGraph → 知识层+文献检索 → MLflow/RO-Crate → 模型升级门。
    - **Q7 定稿（黄金基准）**：Gemini 检索确认"微生物组方向无开箱即用黄金标准"，采用**合成 MRA-Bench**三方案：①治理防御（注入越权/恶意调用，测 100% 拦截+审计留痕，不依赖外部数据，现在可建）→ ②经典论文复现（David 2014/Zeevi 2015 等，Jaccard/MSE 打分，选篇待定）→ ③中间态断言（专家 DAG 校验工具调用轨迹）。优先①。
    - **知识层候选调研（Gemini，已核实分级）**：**第一梯队可审计可版本化**（PMID 回链）：OmniPath（学术许可/资源分级、Python+R 客户端）、curatedMetagenomicData（Bioconductor/Artistic-2.0）、Disbiome（根特大学/学术）、gutMGene v2（哈医大/学术下载）、MGnify（EMBL-EBI）；**第二梯队黑盒风险降级为"假设生成"**：MINERVA（HEM Pharma+Harvard LSP，LLM 从 13 万摘要提取，数据待公开，输出须回链 PMID 二次核实，**禁止自动灌入已确认知识库**）。microbeMASST（UCSD，MIT 源码/CC-BY 数据）为"菌种-代谢物"反查。
    - **Web 工作台选型（Gemini，已核实）**：**FastAPI + Jinja2 + HTMX + Tabler 静态资源**（无 Node 构建链、前端 0 逻辑、审批队列用 HTMX 轮询、安全全在后端 OPA/PEP）。备选：百度 Amis（JSON 驱动低代码）、fastapi-admin-kit 脚手架。SQLAdmin/FastAPI-Admin 因"数据 CRUD 而非业务审批流"不采用。

**下一步（执行中）**：①建 MRA-Bench 方案1"治理防御基准"（curated 攻击语料 + 拦截率评分 + 审计留痕断言）；②随后按 Q6 顺序启动 Web 工作台（FastAPI+HTMX+Tabler）；③把知识层候选与 Web 选型写入 5.5/5.10 对应修订。

28. **GovernanceAgent 受控 Agent 循环 + HERO 真模型验证通过（2026-09-12，核心假设成立）**：
    - `src/mra/workflow/agent.py`：真实模型驱动的受控循环。4 个受控工具（run_registered_task / read_current_result / audit_current_results / propose_promotion），全部经 PEP/OPA；ACI 少参数化（自动跟踪当前产物）；审计 FAIL 守卫拒绝晋升；未知工具/越界拦截回填给模型（让模型学习边界）；当前结果只回 keys+数值摘要防上下文污染。
    - **HERO 测试（真实 doubao-seed-2.0-lite + 用户 ARK key）**：13.4 秒走完"跑任务→读结果→审计→提议晋升"四步，轨迹完全正确（turn0 选对注册任务、turn2 审计 BATCH/COMP PASS、turn3 无 FAIL 才提议）。**核心假设"真 LLM 能在治理信封里自主完成受控科研任务"验证成立。**
    - 全量测试：105 通过 + 1 跳过（跳过=无 ARK key 环境）。git ccb923f。
    - 进度说明：周期审核任务已完成（用户指示：完成后取消）。

**下一步（Q6 顺序继续）**：①Apptainer 双轨（外部制品路径待报批）；②LangGraph；③知识层+文献检索；④MLflow/RO-Crate；⑤模型升级门；⑥MRA-Bench 方案②（经典论文复现，需用户选 2-3 篇开源论文）。Web 工作台已可实际使用（127.0.0.1:8001）。

30. **知识层 v1 完成（2026-09-12，Codex 编码 + Kimi K3 共同审核）**：
    - `src/mra/knowledge/`：KnowledgeStore（SQLite entries 表 + FTS5 fts_entries），ingest 严格校验（必需字段/四包枚举/证据等级枚举/approval 必为 approved+来源非空），search 参数化 `MATCH ?`（防 FTS 注入）、默认排除 retired、命中带回证据等级/适用范围/来源摘要；get/list/mark_retired；版本替换（同 id 新版本覆盖）。
    - **种子知识包 12 条已入库**（`knowledge/` 下 4 类）：governance 4（两主体/三档决策/Harness 分层/结论边界）、methods 4（批次双轨/Goldberg 双方案/零值策略/多重检验）、counterevidence 2（DMR-FM 被推翻/AI 过度解释）、context 2（项目X 队列/进度，无样本级数据）。中文 FTS5 检索验证命中正确。
    - **Agent 集成**：GovernanceAgent 增量加可选 `knowledge_search(query)` 受控工具（只读、返回 title|证据等级|适用范围|来源 ref 的精简文本，不灌全文）；knowledge=None 时不暴露，既有行为不变（HERO 测试不回归）。
    - 全量 **113 测试通过**（+8）。git d66ee40 / 9edb1eb。
    - 执行备注：Codex 末段再次陷入重复编辑循环，按降级规则由 Kimi K3 终止并独立验证全绿（与 Rego 任务同模式）。

**下一步**：①MRA-Bench 方案②（经典论文复现）——需用户选 2-3 篇开源菌群-膳食论文，入库为知识包后做复现打分；②文献受控检索节点；③知识层可进一步接 Web 工作台"知识包版本"视图（5.10 已规划）。

32. **知识层内容升级决策（2026-09-12，用户裁决 + 共同审核）**：
    - **排查结论**：既有 12 条种子全部为 1 行规则片段（200-300 字/条、0 PMID），知识库确为"架子"而非"知识"。
    - **共同审核关键发现**：Gemini 调研提供的 7 个 PMID 经 NCBI E-utilities 核验 **5 个为编造**（Gloor/LinDA/MaAsLin2/批次效应/STORMS 错，仅 ANCOM-BC 与 FAIR 真）——实践验证"外部检索结果必须逐条核验、绝不自动入库"的治理假设；该教训拟入反证包。
    - **Q22-24 确认**：方法条目强制 `PMID/known_limits/anti_patterns/examples`、反证条目强制 `superseded_by`；Bootstrap 首批方法清单约 10-20 主题待定；先 Schema 深化 + paper-lookup 核验 PMID 起草。
    - **Q25 方向（用户）**：**不自建，先搜成品开源知识库部署+优化**——排除了"从零策展 20 条"的自建路径，改为"找到可直接部署/导入的成熟知识库 → 部署 → 针对本平台优化"。搜索与核验进行中。

**下一步**：①Gemini 检索可部署开源知识库候选 + Kimi K3 逐项核验（许可证/可得性/PMID 回链）→ 推荐 1-3 个部署目标；②选定后做部署 + 针对四类知识包的优化；③MRA-Bench 方案②论文待用户选。

31. **知识层真正部署完成（2026-09-12，Gemini 复用审查后落地）**：
    - Reuse Review `policies/reuse-reviews/2026-09-12-knowledge-layer-libs.md` 结论已执行：保留 SQLite+FTS5（不引入 Chroma/Tantivy），引入 `jieba==0.42.1`（MIT，PyPI 核实）做中文预分词；Git YAML 是事实源，SQLite 是可重建派生索引。
    - 运行时库 `var/knowledge/knowledge.db` 已初始化，CLI 批量入库 **12/12 成功**；库不进 Git（`*.db` 忽略），可由 `knowledge/` YAML 重建。
    - 中文检索实测：多字词“目标队列/批次/Goldberg/三档”可命中；单字“哈”不误报；“批次混杂”无命中已识别为种子内容覆盖缺口而非 FTS 故障。
    - Web 工作台已接入实际知识库：`GET /knowledge` 概览、`/knowledge/search?q=...` 搜索均实机返回 200 并命中真实条目；服务监听 `127.0.0.1:8001`。
    - **真实 Agent+知识 HERO 冒烟通过**：真实 `doubao-seed-2.0-lite` 按目标先调用 `knowledge_search`，再调用 `run_registered_task`、`audit_current_results`、`propose_promotion`，最终 `WAITING_APPROVAL`；知识工具返回精简证据字段（title/evidence_level/applicability/source），未将全文灌入上下文。
    - 共同审核结论：代码/内容/运行时/Web/真实 Agent 接入五层均已部署；知识图谱、向量检索、自动文献灌库仍按 5.5 后置，未擅自扩大范围。

29. **执行顺序再调整（2026-09-12，用户决策）**：**Apptainer 双轨后置**。理由（与 Ponytail 一致）：模拟/合成阶段用不上 OS 级隔离，其门槛时刻是"真实 项目X 数据 + 不可信代码"进入平台。新优先序——**手头可验证的先行**：①MRA-Bench 方案②（经典论文复现，阻塞于用户选论文）；②知识层四类包 + FTS5（设计先行）；③文献受控检索；之后才回 Apptainer / LangGraph / MLflow / 模型升级门。

27. **MRA-Bench 方案①治理防御基准完成（2026-09-11）**：
    - `src/mra/benchmark/governance_defense.py`：**14 个攻击向量（GOV-001~014）**覆盖 9 类威胁（越权/身份伪造/命令注入/路径穿越/收据重放/密钥泄漏/哈希篡改/自批/资源不匹配），每个场景映射具体拦截理由码（DENY_DEFAULT/DENY_AGENT_RESTRICTED_ACTION/APPROVAL_REQUIRED_PROMOTE_RESULT/DENY_APPROVAL_INVALID/DENY_SUBJECT_HASH_MISMATCH/DENY_STAGED_PATH_OUT_OF_SCOPE）并断言审计账本留痕。
    - 结果：**interception_rate=1.0，14/14 BLOCKED**，Kimi K3 独立复跑 **91 测试全绿**。可作为平台"治理防御"黄金基准门长期回归。
    - Web 工作台启动（Q6-1）：选型落地 FastAPI+Jinja2+HTMX（htmx.min.js 已 vendor 本地，非 CDN）；Tabler 按 Ponytail 砍掉（下载源不可达且 v0.1 无需）；误建的 项目X/src 残留已清理。构建中（Codex）。

**下一步**：①Web 工作台 v0.1 验收（审批队列/审计复盘/权限视图/运行溯源）；②按 Q6 顺序推进 Apptainer 双轨；③知识层候选与 Web 选型回写 5.5/5.10。

---

33. **主模型与审核关系调整（2026-09-15，用户裁决）**：
    - Codex（gpt-6-astra）因网络限制不可用，已从主 Agent 角色移除；如恢复仅作交叉审核备用渠道。
    - **主 Agent（总协调者）＝ DeepSeek（deepseek-v4-pro，Volcano Engine／coding-plan）**，即本平台当前开发会话的运行模型。
    - 审核改为**跨渠道交叉**：主 Agent 最终综合审核；代码/测试由 Kimi K3（opencode-go/kimi-k3，备用 xianyu-temp-kimi/kimi-k3）交叉；外部事实由 Gemini；可加另一渠道 DeepSeek 或恢复后的 Codex。同一模型同一渠道不得既执行又自审。
    - 分工规则已同步写入 AGENTS.md（主 Agent=deepseek-v4-pro；执行=Kimi K3/DeepSeek/kimi-k2.7-code；网络=Gemini；执行后跨渠道交叉审核）。
    - **待办**：Source Registry + CandidateEvidence 已实现并通过 222 测试（Kimi K3 实现、DeepSeek 修复+审核、三模型诊断收敛、Ponytail 放行），尚未提交（待用户确认提交时点）；下一步 Europe PMC Source Adapter（验证欧洲 PMC→候选证据闭环）。

34. **知识层两层架构闭环完成（2026-09-15，跨渠道审核）**：
    - 闭环已端到端打通并实测：来源注册表 → 候选证据入库（只降不升）→ 人工审阅(pending/approved/rejected) → draft-entry 晋升草稿 → 已批准知识（YAML+ingest）。
    - 已提交（git 前 4 条）：`77d6a06` Source Registry+CandidateEvidence；`a7e6a5c` Europe PMC Adapter；`9eeead5` CLI source-search + Web /sources 只读页；`3080547` 候选证据审阅生命周期。
    - **已提交（2026-09-20 补提交，此前按用户要求暂缓在工作树）**：
      * `draft-entry` 晋升草稿助手（src/mra/knowledge/cli.py + tests/knowledge/test_cli_source_search.py）——出处机器抄录、approval=pending 不可 ingest、强制先 approved；
      * Web 候选审阅动作 `POST /sources/review`（src/mra/web/app.py + templates/sources.html + tests/web/test_web_sources.py）——人类网页审阅、无外部网络、表单参数规避斜杠路径；
      * **OmniPath Adapter**（src/mra/knowledge/sources/omnipath.py + tests/knowledge/test_omnipath_adapter.py）——第二来源；`proteins=EGFR` 实测 200；互作 references 为数据库引用（非 PMID）→ 注册为 hypothesis_only 源，存库强制 hypothesis_only；真实冒烟 3 条全降级。
    - 渠道现实记录：主 Agent（deepseek-v4-pro）负责写码；opencode run 写任务反复停滞、Gemini 余额不足(402)、Codex 不可用；跨渠道只读审核由 XianYu Kimi K3 完成多次 PASS。
    - 全量测试 **286 passed, 1 skipped**。
    - 运行库 var/knowledge（gitignored）：1 来源(europe-pmc)、若干候选（含 approved）、12 已批准知识条目。
    - **Track B（MRA-Bench 方案②论文检索）后台进行**：已核实 David 2014(Nature, PMID 24336217)、Zeevi 2015(Cell, PMID 26590418) 真实；GitHub 仓库/数据 accession 待核验（后台）。

35. **MRA-Bench 方案② 基准论文选定（2026-09-15，用户按推荐执行）**：
    - **标准**：CNS 级肠道菌群研究 + GitHub 官方代码 + 整理好数据 + 可复现。
    - **已排除**：David 2014 / Zeevi 2015——经典但无官方 GitHub 代码、无复现流程，不达标（已实测核验）。
    - **选定基准：Wirbel et al. 2019, Nature Medicine**《Meta-analysis of fecal metagenomes reveals global microbial signatures that are specific for colorectal cancer》（PMID 30890793）。
    - 核验证据（2026-09-15 补充核验后更正）：官方仓库 `WaldronLab/crc_metagenome` 存在，含全部脚本/工作流（`src/prepare_data.R` 等）、`parameters.yaml`、`requirements.R`；**但 `data/*` 目录为空**，需跑 `prepare_data.R` 从 `data.location` 拉取后生成。README 明确：**curated 数据当前仅 EMBL 内网可用**（"only available from within the EMBL intranet, will be moved to Zenodo soon"）。
    - raw 数据可得性（ENA/SRA 实测）：PRJEB6070 / PRJEB7774 / SRP057027 / SRP117781 均公开返回 reads（SRP105448 待复核）。
    - 依赖：R 3.5.1 + SIAMCAT 1.1.0（gitlab.embl.de）+ GMMs（指定 commit）+ requirements.R；8 队列 n=768，核心结果=29 个 CRC 富集 species（FDR<1e-5）+ 功能/胆汁酸推断。
    - **结论**：Wirbel 2019 = 代码✅ + raw 数据✅ + **处理数据暂不可得**（EMBL 内网/待 Zenodo）。作为复现基准有二选：①从 raw fastq 自建流程重跑（重，且需自建流程）；②等 curated 数据发布。故列为**候选 A（有前提）**，非"完全达标"。
    - **待办**：Track B 继续找"代码+处理数据均公开"的低阻力候选（候选 B），使方案②至少有 1 个无前提基准。

*本文档基于用户与 Claude Code 的规划访谈整理，所有架构决策均已经用户逐条确认。接手开发前请通读第 5 章全部子项，第 6 章为必须在实施前进一步细化的开放事项（含已完成的主机核查结果），第 7 章为已知的调研纠错记录，避免重复引用被推翻的说法，第 9 章为接手开发后的决策与进展日志。*
