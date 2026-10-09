<div align="center">

<img src="docs/images/01-header.jpg" alt="Gut Microbiome Knowledge Graph" width="880">

<br/>
<br/>

**A gut-microbiome knowledge graph with test-locked quality gates.**

89K papers in, 20,309 provenance-carrying edges out — every release passes machine checks, an independent reviewer, and a user-issued write key before it is served.

<br/>

[![CI](https://github.com/Jxuanrui/CSCCD-OpenMicrobiomeScience/actions/workflows/mra-tests.yml/badge.svg)](https://github.com/Jxuanrui/CSCCD-OpenMicrobiomeScience/actions/workflows/mra-tests.yml)

[Quickstart](#quickstart) · [What you can do](#what-you-can-do) · [How it works](#how-it-works) · [Quality gates](#quality-gates--metrics) · [Changelog](CHANGELOG.md)

</div>

<br/>

## What it is

A local knowledge layer for gut-microbiome research: curated databases (drug–microbe sensitivity, microbe–disease signatures, host–gene regulation) reconciled with LLM-extracted relations from 89,404 PubMed abstracts. It answers "which microbes, which diseases, what evidence" with a PMID on every claim — and it refuses to answer when the graph has no evidence.

- **Every edge carries provenance.** Four columns (`source_id / retrieved_at / version / knowledge_layer`) on all 20,309 edges; every PMID cited in QA is greppable in the serving TSVs.
- **Quality gates are test-locked, not vibes.** Thresholds (context precision ≥ 0.85, flip rate ≤ 0.15, …) live in `PRE_REGISTERED_GATES` constants asserted by tests — they cannot drift between rounds.
- **Three-gate release.** Machine checks → independent supervisor review → user-issued one-time write key. No single actor can publish alone.
- **Cross-family judging.** Extraction (DeepSeek) and adjudication (GLM) use different model families, so one model's bias cannot stamp its own work.
- **Honest refusals.** The QA layer answers only from graph context and says "知识库中证据不足" when the graph lacks the relation — verified by a test that asks for a relation the graph does not have.

## Quickstart

```bash
git clone https://github.com/Jxuanrui/CSCCD-OpenMicrobiomeScience.git
cd CSCCD-OpenMicrobiomeScience

# multi-hop fan-out query (no services needed)
python3 src/05_analysis/graph_analysis.py query \
    --microbe "Faecalibacterium prausnitzii" --hops 2

# grounded QA (local embeddings; needs one LLM endpoint in .env)
.venv-lightrag/bin/python src/06_qa/lightrag_qa.py ask \
    "Which diseases are associated with Faecalibacterium prausnitzii?"

# interactive browser (5 tabs: fan-out / network / communities / predictions / review)
python3 -m streamlit run src/05_analysis/kg_browser.py

# full test suite
cd mra && uv run pytest -q        # 563 passed / 15 skipped / 1 xfailed
```

## First question

```text
$ python3 src/05_analysis/graph_analysis.py query --microbe "Faecalibacterium prausnitzii" --hops 1
[query] Faecalibacterium prausnitzii (NCBITaxon:853) 1-1 跳扇出: 97 个跨域邻居
  按类别: {'Metabolite': 21, 'Disease': 39, 'Gene': 37}
1  Disease  Colitis, Ulcerative  MESH:D003093  decreases_abundance_in  A  26296733|27833384|28039159  ...
```

Every row carries the evidence tier (`A` curated · `B` ≥2 papers · `C` single paper pending review · `predicted` = hypothesis, never a claim) and the PMIDs behind it.

## What you can do

| Task | What happens |
| ---- | ------------- |
| **Query the graph** | Single-microbe multi-hop fan-out across 7 entity types, with per-edge tier + PMID. |
| **Ask questions** | Grounded QA over the serving KG: entity questions get a deterministic evidence list (E1…En, verbatim PMIDs); the rest get hybrid retrieval. |
| **Generate hypotheses** | RotatE link prediction (`rotate_predictions.tsv`) — always labelled `evidence=predicted`. |
| **Run governed research** | The `mra/` research loop plans → executes → audits → files evidence, with R-side statistics behind an audit gate that denies constant exposures. |
| **Audit any release** | Release-gate report, write-key ledger, snapshot manifests and sha256-checked archives are all in-repo. |
| **Rebuild everything** | Serving KG is git-tracked; `neo4j_materialize.py` rebuilds the live graph from TSVs. |

## How it works

```text
89,404 PubMed abstracts (PubTator 3.0)
  → S1 relation classification · S2 three-vote confirmation · S3 cross-family judge (DeepSeek ↔ GLM)
      → v7 veto rules (subject-attribution, weak-predicate downgrade, multi-food marking)
  → merge_qc (stage=3 contract: only fully validated rows enter)
      → release_gate_check · 41 machine assertions · PRE_REGISTERED_GATES (test-locked)
  → serving: data/merged/candidate_v3 (git-tracked) → Neo4j + LightRAG index
```

- **Write guard.** Anything under `data/merged` needs an authorization key + `execution_id`; keys are one-time and every use lands in an audit ledger (12 consumed, 0 active).
- **Knowledge layers.** `local_kg_curated` / `local_kg_llm_extracted` live in the graph; `research_evidence` is a boundary sentinel — study findings stay in the research workspace and never flow back into the KG.
- **STOP rule.** Evaluation cycles cap at 2 iterations; v3 shipped at 91.3% context precision (B5 blind annotation) rather than chase a longer tail.

## Quality gates & metrics

| Gate (pre-registered, test-locked) | Threshold | v3 result |
| --- | --- | --- |
| Context precision (blind-annotated) | ≥ 0.85 | **0.913** (42/46, B5 blind) |
| Food dimension precision | ≥ 0.85 | 0.933 (Wilson [0.84, 0.98]) |
| Machine release checks | 41 PASS / 0 FAIL | **41 / 0** |
| Provenance completeness | 4 columns on every edge | 20,309 / 20,309 |
| Link prediction baseline | report only | RotatE hits@10 = 1.0%, MRR = 0.0073 (raw, n=200) |

| Compared with | [MicrobiomeKG](https://www.frontiersin.org/journals/systems-biology/articles/10.3389/fsysb.2025.1544432/full) | [MINERVA](https://academic.oup.com/bib/article/26/5/bbaf472/8261764) |
| --- | --- | --- |
| Per-edge provenance | ✅ 4-column | partial | PMID link only |
| Pre-registered gates | ✅ test-locked | ❌ | ❌ |
| Cross-family judge | ✅ DS + GLM | ❌ | ❌ |
| Context precision | 91.3% (blind) | unreported* | unreported* |

\* As stated in their publications; not independently verified by us (candidate_research).

Known limitations are recorded per release in the [CHANGELOG](CHANGELOG.md) (anatomical_site 75% n=4; geography 0% n=2; 1,473 isolated lexicon entities; corpus at 68.8% of the 130K pre-registered target — deferred to v4).

## Data sources & acknowledgements

| Source | Use | License |
| --- | --- | --- |
| Maier 2018 *Nat Chem Biol* ST3 | drug–microbe sensitivity | CC BY 4.0 |
| BugSigDB | microbe–disease signatures | CC BY 4.0 |
| gutMGene / gutMDisorder | diet/disease–microbe, host-gene | academic (cite paper) |
| KEGG REST | pathway membership | academic-only ([verified 2026-10-09](https://www.kegg.jp/kegg/legal.html)) |
| PubTator 3.0 / NLM MeSH 2026 | corpus, vocabulary | public / public domain |
| DiMB-RE (JAMIA 2025) | relation-extraction evaluation | paper CC BY 4.0; repo unlabelled |
| Europe PMC OA | full-text whitelist per paper | per-paper CC BY/CC0/PD |

Full registry with retrieval dates and versions: [`data/registry/source_registry.tsv`](data/registry/source_registry.tsv). The `radar/` daily literature radar is MIT (© 2026 yin-huamin).

## Repository

```text
src/01_seed_etl      curated source ETL
src/02_pubtator      corpus fetcher
src/03_llm_relation  S1→S2→S3 classification + v7 veto
src/04_merge_qc      merge, QC, provenance backfill
src/05_analysis      fan-out queries · browser · RotatE
src/06_qa            grounded QA (LightRAG, dual-path)
src/07_capability    write_guard · release gate · review prep · contracts
src/08_route_eval    role gold · MeSH normalisation · cross-family eval
mra/                 research loop, governance, knowledge base (uv, Python 3.10/3.11)
schema/              LinkML schema (EvidenceTier A/B/C, KnowledgeLayer)
skills/              23 agent skills
data/merged/candidate_v3/   serving KG (git-tracked)
data/archive/               sha256-manifested history
```

```bash
python3 -m pytest src/ -q                              # pipeline contract tests
python3 src/07_capability/test_merged_dir_defaults.py  # path-defaults contract
cd mra && uv sync && uv run pytest -q                  # research-loop suite
```

## 执行规则（治理）

1. **计划→执行→审核**：改动先列计划再执行，执行后由监工独立审查；质量门禁以监工裁决为准（有条件通过须先整改后开工）
2. **修改 src/ 前**必须在 README 进展日志中注明
3. **新建路径**（目录/数据管线/工具）先报审获准再创建，禁止未审先建
4. **单一说明文档**：每项机制只保留一份说明文档，其余并入或删除，禁止多份并存漂移
5. **MVP 先行**：新功能先做最小可用版本并通过验收，再考虑扩展
6. **P0 冻结**：发布周期内 P0 问题未修完不开新轨道（v1.2+ 分级：P0 必改 → P1 强烈建议 → P2 可延后）
7. **执行模型**：单仓库、单 worktree、单路径开发；多终端并行须先经用户与监工批准
8. **API 密钥**只走 `.env`（gitignored），永不入 git/crontab
9. **data/merged 写入**须过 write_guard 闸门（授权键+execution_id+审计账本）
10. **不可逆操作**（删除/覆盖）先备份、再报审、后执行
11. **进程操作**按精确 PID，禁宽匹配 kill

### radar 数据落盘的批次授权模型（X5 草案，v4 重启 08:30 cron 前实施）

每日 PubMed 抓取需写 `data/pubtator`（write_guard 保护），与一次性授权键天然冲突。方案：
1. **按批次签发**：每次扩语料批次开始时，用户为该批次签发**限路径、限量、限有效期**的批次键（registry 新增 scope 列：`path=data/pubtator; quota=<篇数>; expires=<日期>`）；
2. **cron 侧换券**：fetch_pubtator 首次用批次键换取**本地会话凭证**（root-only 文件），有效期内每日 cron 持会话凭证而非原始键——write_guard 校验会话凭证与批次 scope；
3. **fail-closed 不变**：会话凭证过期/超量即拒绝，恢复需用户重新签发——不放宽闸门，只把"每日要钥匙"变成"每批换一次钥匙"；
4. radar 每日 PMID 列表（07:30 run_daily 写 radar/data/daily）不经 write_guard，不受影响。

## 进展日志（摘要）

- 2026-10-09 **U2-B/X2/X3/X5（监工 G-U2 有条件通过）**：测试与真表解耦——mra/tests/conftest.py（新建，监工处方）autouse 合成队列契约（独立 mktemp 目录，避免污染测试 tmp_path 断言）；真表保留 1 条部署级冒烟（REAL_COHORT_CONFIG 缺省自动 skip，实测 1,068 样本通过）；①标 xfail(U1)。**实测 563 passed / 15 skipped / 1 xfailed / 0 failed**（CI 模拟：COHORT_CONFIG 指向不存在路径）。X3 CI 双版本矩阵已完成但 **a51fb89 因 PAT 缺 workflow scope 推送被拒（GitHub 原文报错）——U5 从待办变实证阻塞**。X2 许可核实：KEGG 官方条款（学术免费/商业须授权/自称非公共库）+ DiMB-RE 论文 CC BY 4.0/仓库无 LICENSE，写入注册表 license 列附 URL+日期；**注册表首次强制入库（补报审：治理数据应入库，先例同 candidate_v3）**。X5 批次授权模型草案写入 radar 节。X1 dump 冻结转拍板（监工：活库在心血管项目路径下跨项目风险+serving KG 已在 git 可重物化，dump 或非必要）
- 2026-10-08 **G3 复审整改+归档条件**：C1 gate R 检查挪至零方差守卫后（无 R 环境常数暴露仍入账 deny）；C2 doctor 与执行层同源解析 RSCRIPT；C3 CHANGELOG 补 v3.0.4/v3.0.3；归档条件 test_no_r_environment_contract 锁定无 R 契约（gate 套件 6 passed）
- 2026-10-07 **E1 执行（G1 门通过后）**：A4 四失败复现归因修正——监工预警应验，②③④原"零方差"归因全错（真因：RSCRIPT_BIN 未设→`.`/cohort_config 未部署/方法 KB 的 DB 空——"零方差 常数列"实为测试传入的 analysis_type 名）；按裁决顺序修复：rtools RSCRIPT 回退 which+gate 前置检查→KB ingest 16 条→cohort_config 填 Harbin 真实三表；mra 1f/562p/14s（+6 净通过，3 个曾 skip 的 R 测试真跑全绿）；① prov 四件套转用户拍板；A3 孤立实体归因完成（LFS 词表 1,143 占 77.6%+MESH 237+NCBITaxon 78 含非肠道生物）；CHANGELOG 勘误段+P1（monkeypatch 化 env/README 部署注记）
- 2026-10-07 **复审整改（监工有条件放行 3 条）**：mra 侧 10 处指向统一 candidate_v3；CHANGELOG 更正 stash 说法；git-tracked 证据（24 文件）入账；契约测试扩域 mra；kg_snapshots 初始化；08:30 cron 注释暂停
- 2026-10-07 **路径规整（监工打回后整改）**：KG_MERGED_DIR 五处默认值统一 candidate_v3（消除 v2 覆盖活库风险）；merged 根层 69 文件归档（sha256 核验）；staging 去重 430MB；radar 双 cron 断链修复
- 2026-10-07 **C2 LightRAG 重建完成**：candidate_v3 全量索引+接地双路径（实体问题确定性证据清单，PMID 逐字真实）
- 2026-10-07 **v3.0.0 发布**：B3-B6 全链（985 断言/91.3%/机检 41PASS/物化/serving 切换）
- 2026-10-02 **v1.3.0 发布**：双终端合二为一，单开发模式
- 2026-09-29 **安全事件 R1**：密钥轮换+git 历史重写+pre-commit 扫描

## License

TBD — code and data will be licensed separately (the data side is constrained to non-commercial terms by academic-only upstreams, incl. KEGG). See the [data source table](#data-sources--acknowledgements).

## Citation

TBD (paper in preparation).
