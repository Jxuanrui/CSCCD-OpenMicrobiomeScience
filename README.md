![Gut Microbiome Knowledge Graph](docs/images/01-header.jpg)

# Gut Microbiome Knowledge Graph（肠道菌群知识图谱）

> **版本**：v1.3.0（2026-10-02 发布） ｜ **模式**：单开发 ｜ **路径**：本目录为唯一项目路径
> **定位**：Scientific Research Harness 的 **Local Knowledge Layer**——存储研究开始前已有的治理知识，绝不存当前研究结果。

全菌群领域知识图谱：整合策展数据库与文献抽取，支持"输入一个菌 → 拉出尽可能多的跨域关联信息（疾病/代谢物/基因/药物/食物/通路）"。

## 项目终态（v1.3.0 发布数据）

| 维度 | 值 |
|---|---|
| Neo4j 节点 | **12,015**（6,272 实体 + 5,742 断言 + 1 MP） |
| Neo4j 边 | **20,840**（全部带溯源四列真值） |
| serving 快照 | **candidate_v2**（5,742 断言，2026-10-02 U3 授权切换） |
| 语料 | 76,108 篇（PubTator 3.0，12 疾病域 + radar 增量） |
| Food 精度 | **59/60**（两轮盲标合并） |
| context 精度 | **9/12 维过线**（≥85%），2 维未验证（6/8、5/8 明示），2 维未评测 |
| 溯源完备 | source_id 六源 100% / retrieved_at = registry / knowledge_layer 两枚举 |
| 治理 | G0-G7 全关口闭环，13 份独立审查报告，write_guard 授权键全部核销 |

## 发布三关制（所有发布必须通过）

1. **机检**：`release_gate_check.py` 全过（预注册门禁常量，测试锁定）
2. **独立审查**：阶段关口审核（只审不干，否决权）
3. **用户授权**：明确批示后执行，授权键一次执行即核销

## 版本线说明

- `CHANGELOG.md`：只管 Harness 软件版本线（v1.2.0 → v1.3.0）
- `snapshot_manifest.json`：数据快照号（candidate_v2），两条线不合并

## 目录结构

```
Knowledge_Graph/                  ← 唯一项目路径（main 分支）
├── README.md                     ← 本文档（唯一说明文档）
├── CHANGELOG.md                  ← 版本历史（软件版本线）
├── HARNESS_ARCHITECTURE_V1.md   ← 架构约束文档（最高约束）
├── status.sh                     ← 执行健康核验（统一入口）
├── .env                          ← 密钥（gitignored，600 权限）
├── .github/workflows/            ← CI（mra-tests.yml）
├── .githooks/pre-commit          ← secret 扫描钩子
│
├── src/                          ← 知识内容管线（Python）
│   ├── 01_seed_etl/             ← 策展源 ETL（Maier/BugSigDB/gutMGene/gutMDisorder/KEGG）
│   ├── 02_pubtator/             ← PubTator 语料拉取
│   ├── 03_llm_relation/         ← LLM 关系分类（含 v7 确定性否决）
│   ├── 04_merge_qc/             ← 合并+质检（含 provenance 回填）
│   ├── 05_analysis/             ← 多跳查询/交互式图谱/社区/RotatE 嵌入
│   ├── 06_qa/                   ← LightRAG 问答（待环境修复）
│   ├── 07_capability/           ← 能力层（write_guard/release_gate/review_prep/safe_clean）
│   ├── 07_monitor/              ← 定时审计（kg_audit.sh，crontab 每 30 分钟）
│   └── 08_route_eval/           ← 路由评估（role gold/mesh_normalize/cross_family/c2e）
│
├── mra/                          ← MRA 子系统（Agent 平台/治理框架）
│   ├── src/mtra/                ← 核心模块（kg/knowledge/web/eval）
│   ├── tests/                   ← 测试（含 mra/tests/kg 契约测试）
│   ├── knowledge/               ← 知识源接入（EuropePMC 等）
│   └── radar/data/daily/        ← radar 增量数据（crontab 写入）
│
├── radar/                        ← Meta-SeuBiomed（radar 主模块）
├── artifact_engine/              ← 文献工件引擎（classifier/extractor/providers）
├── schema/                       ← LinkML schema（microbiome_kg.linkml.yaml）
├── skills/                       ← 23 个 Agent Skills（微生物组科研全流程）
├── docs/archive/                 ← 历史文档归档（7 份，不再维护）
│
└── data/                         ← 数据（gitignored）
    ├── merged/candidate_v2/     ← 发布数据（serving 源，444 保护）
    ├── pubtator/articles.jsonl  ← 语料（76,108 篇）
    ├── seed/                    ← 策展源 TSV
    ├── staging/                 ← LLM 中间结果
    ├── registry/                ← Source Registry + write_authorizations
    ├── logs/                    ← 运行日志 + 审计记录
    ├── backups/                 ← 快照/worktree 备份
    └── rag/                     ← LightRAG 索引（待重建）
```

## 核心流水线

```
策展源 ETL（5 源）──┐
                    ├──→ merge_qc ──→ candidate_v2 ──→ Neo4j 物化 ──→ serving
PubTator 语料 ──→ LLM 分类 ──┘         (provenance     (guard_write    (三关制
                                        四列真值)        + 清库重建)     发布)
```

## 技术栈

| 层 | 选型 | 依据 |
|---|---|---|
| 文献获取+实体归一 | PubTator 3.0 API | 官方 AIONER 归一化（物种→NCBITaxon/疾病→MeSH/化学物→ChEBI/基因→NCBIGene） |
| LLM 关系分类 | LLM API（GLM/DeepSeek），仅做关系判定不做实体抽取 | 降低幻觉；数据公开可用 API |
| 图存储 | Neo4j 5.x（Docker 本地部署） | 生态最全：Cypher/GDS/向量索引 |
| Schema 定义 | LinkML | YAML 定义，可生成校验器 |
| ETL 结构 | KG-Hub download→transform→merge 三段式 | 成熟流水线模式 |
| 分析 | Cypher 多跳查询 + Neo4j GDS + PyKEEN(RotatE) 链接预测 | 医学 KG 标准做法 |
| 问答 | LightRAG 挂 Neo4j（待环境修复） | 轻量、支持增量、成本低 |

## 关键设计决策

| 决策 | 依据 |
|---|---|
| 三级证据分级 | A=curated 直接入图，B=LLM ≥2 篇自动，C=单篇 pending_review |
| provenance 四列 | source_id/retrieved_at/version/knowledge_layer，merge_qc 收尾内联回填 |
| 预注册门禁 | 85% 精度线、迭代 ≤2 轮、种子预锁定，防事后选口径 |
| write_guard | 授权键+execution_id+审计账本，一次执行即核销 |
| 双终端→单开发 | worktree 已删除，唯一路径，crontab/status.sh 全部指向主目录 |
| 化合物统一到 ChEBI | PubTator 标注天然归一 ChEBI，食物成分进 Metabolite，Food 节点仅整体食物 |
| 食物成分→ChEBI/食物整体→Food | `contained_in` 边关联 |

## 已知限制与待办

| 优先级 | 事项 | 说明 |
|---|---|---|
| 🔴 | 论文方法章节 | 所有数字已定版，可直接引用 |
| 🟡 | 2 维未验证修复 | anatomical_site/disease_subtype——下周期新预注册+新方法 |
| 🟡 | LightRAG 索引重建 | 环境损坏（numpy/jax 冲突），需独立 venv |
| 🟡 | CI src/** 触发 | PAT 缺 workflow scope |
| 🟢 | 3 项写死 PASS 改真实计算 | Span norm/Batch completion/Comparability Gate |
| 🟢 | 语料扩至 13 万篇 | 等论文+Food 收口后 |

## 执行规则

1. **修改 src/ 前**必须在 README 进展日志中注明
2. **API 密钥**只走 `.env`（gitignored），永不入 git/crontab
3. **data/merged 写入**须过 write_guard 闸门（授权键+execution_id+审计账本）
4. **不可逆操作**（删除/覆盖）先备份、再报审、后执行
5. **进程操作**按精确 PID，禁宽匹配 kill

## 安全事件与整改记录（2026-09-29 R1）

详见 git 历史 docs/security 归档提交。要点：Neo4j 密码明文入 README（已轮换+历史重写+零暴露）；crontab 明文 API key（已移除+key 作废）。

## 进展日志（摘要）

<details>
<summary>点击展开完整日志（按时间倒序，仅保留关键节点）</summary>

- 2026-10-02 **U3 三合一执行完毕——项目完整交付**。⑦发布授权+serving 切换 candidate_v2+worktree 删除（唯一路径落地）。冒烟验证全绿。
- 2026-10-02 **G7 复审 C1-C3 闭环**。图中直查 hold=0/MP 哈希一致/README 如实披露。38 PASS。
- 2026-10-02 **G7 打回 P0×5 整改**。重物化（5742 断言/hold 隔离 0）+哈希三口径一致+hold 按 ID 核验。
- 2026-10-01 **G5 收口**。补标 4 判定+四项拍板+scispaCy 书面评估（0/5 必须句法，不引入）。
- 2026-10-01 **G5 第 2 轮**。Food 30/30=100%；context 9 维过线；2 维未过（stop_rule 2/2 用尽）。
- 2026-10-01 **C2b-e 全链**。三规则先红后绿+冻结 v4+全量重算+两轮抽样（3 次作废）。
- 2026-10-01 **G0-G3 四关口**。Q 轨/合入/P2 溯源真值/P3 对账/归因。4 键核销。
- 2026-10-01 **对方返工核验**。占位值坐实→打回；loader 改造+重物化+测试清零→G7 通过。
- 2026-10-01 **项目整合启动**。用户决策：叫停对方→返工→整合合二为一。
- 2026-09-30 **阶段二（路由评估）定版**。test_100 D 84%/mesh 86%（D 正式·跨家族升级获批）。
- 2026-09-30 **跨家族复核**。DeepSeek 82%（temperature=0），D 结论获独立第二意见支持。
- 2026-09-30 **test_100 全量预测锁定**。100 条先于 gold 锁定（盲评前提成立）。
- 2026-09-30 **Phase 2 知识可审计化**。provenance 四列 100% 归因+knowledge_layer 五枚举+vecstore 层标识。
- 2026-09-29 **安全事件 R1 整改**。密钥轮换+git 历史重写+pre-commit secret 扫描。
- 2026-09-25 **首次完整闭环**。producer→evidence→governance→release→materialization→snapshot→consumer。

</details>

---

> **服务状态**：Neo4j（bolt://127.0.0.1:17687）✅ ｜ Streamlit（127.0.0.1:8765）✅ ｜ 审计 cron（每 30 分钟）✅
> **GitHub**：https://github.com/Jxuanrui/CSCCD-OpenMicrobiomeScience ｜ **internal-archive**：本地裸仓（备份）
