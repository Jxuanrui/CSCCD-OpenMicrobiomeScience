---
name: microbiome-paper-reader
description: 7模块并行精读Agent，从PubTator3全文提取论证逻辑链、实验设计、写作工艺，并自动入库知识图谱
---

# microbiome-paper-reader：文献精读 Agent

## 快速调用示例

```
/microbiome-paper-reader 39367251
/microbiome-paper-reader 37932270 --depth quick
/microbiome-paper-reader 33753486
```

`--depth quick`：只运行 M1+M2+M3（跳过 M4 论证链，节省约 60% 时间）
`--depth full`（默认）：运行全部 7 个模块

---

## 执行流程

收到 PMID 后，**严格按以下并发架构执行**：

### 步骤 0 — 初始化任务追踪

```python
TaskCreate("数据获取: PubTator3 + PDF 双路")
TaskCreate("M1+M2+M3: 并行分析")
TaskCreate("M4: 论证逻辑链")
TaskCreate("M5+M6: 并行分析")
TaskCreate("M7: 知识图谱入库")
```

### 步骤 1 — 数据获取（双路 C 选项）

```bash
export http_proxy=VPN本地代理 && export https_proxy=VPN本地代理
python3 .claude/commands/microbiome-paper-reader/scripts/fetch_paper.py <PMID> \
  --out /tmp/paper_reader_cache
```

数据来源优先级：
1. **PubTator3 全文**（195+ passages，含 Methods/Results 全文）→ 标记 `source: pubtator3`
2. **PubMed 摘要 + PDF 全文**（pdfplumber 解析 PMC/Unpaywall）→ 标记 `source: pubmed+pdf`
3. **仅 PubMed 摘要**（全文不可用时兜底）→ 标记 `source: pubmed_only`，提示用户精读深度受限

更新 TaskCreate("数据获取") → completed。

### 步骤 2 — Phase 1：M1/M2/M3 并行

```bash
python3 .claude/commands/microbiome-paper-reader/scripts/analyze_paper.py <PMID> \
  --raw /tmp/paper_reader_cache/<PMID>_raw.json \
  --out knowledge/concepts/entries \
  --graph knowledge/graph/nodes.json
```

脚本内部并发执行 Module 1/2/3，完成后自动进入 Phase 2。

更新 TaskCreate("M1+M2+M3") → completed。

### 步骤 3 — Phase 2：M4 等待 M2+M3；M5/M6 并行

Module 4（论证逻辑链）使用 M2+M3 的输出作为上下文，和 M5/M6 同时运行。

关键输出说明：

**M4 论证逻辑链** — 这是整个精读的核心：
- 不只是数据分析链条，要覆盖**所有实验类型**：
  - 计算/生信分析（差异分析、机器学习、网络分析）
  - 体外实验（细胞系、类器官）
  - 动物模型（无菌鼠、FMT、抗生素清除）
  - 临床干预（RCT、前瞻性队列）
- 每步之间要回答：**为什么需要下一个实验？上一个实验哪里不够？**
- 标注每个中间结论的证据强度（观察性/关联性/因果性）

更新 TaskCreate("M4") 和 TaskCreate("M5+M6") → completed。

### 步骤 4 — Phase 3：M7 入库

自动完成：
- 写入 `knowledge/concepts/entries/<PMID>.md`（结构化精读 markdown）
- 更新 `knowledge/graph/nodes.json`（知识图谱索引）

更新 TaskCreate("M7") → completed。

---

## 7 个模块的职责

| 模块 | 名称 | 核心问题 | 依赖 |
|------|------|---------|------|
| M1 | 定位与背景 | 这篇文章是什么，解决什么问题 | 无 |
| M2 | 数据与队列 | 用了什么数据，质量如何 | 无 |
| M3 | 实验设计解构 | 做了哪些实验，每个解决什么 | 无 |
| M4 | 论证逻辑链 | 各实验如何串联成完整因果论证 | M2+M3 |
| M5 | 写作工艺分析 | 如何构建高水平学术论文 | 无 |
| M6 | 局限性与质疑预判 | 审稿人会质疑什么 | 无 |
| M7 | 知识图谱入库 | 整合所有结果，更新知识库 | 全部 |

---

## 输出文件

精读完成后，以下文件会被创建/更新：

```
knowledge/concepts/entries/<PMID>.md     ← 完整结构化精读
knowledge/graph/nodes.json               ← 知识图谱索引（追加节点）
```

### 精读文件结构（`<PMID>.md`）

```markdown
# [PMID] 论文标题
- Journal / Year / DOI / Authors
- Node Type: conceptual/executable | Source: pubtator3/pubmed+pdf/pubmed_only

## M1：定位与背景
## M2：数据与队列
## M3：实验设计解构
## M4：论证逻辑链        ← 核心，包含完整数据/实验因果链条
## M5：写作工艺分析
## M6：局限性与质疑预判
```

---

## 注意事项

- `source: pubmed_only` 时 M3/M4 精度受限（无 Methods/Results 全文），需提示用户
- `--depth quick` 时跳过 M4/M5/M6，适合快速入库大量文献
- 脚本执行需要 `ANTHROPIC_API_KEY` 环境变量

---

## 调用关系

- 精读后想复现代码 → `/microbiome-article-analysis`
- 精读后想找对标文献 → `/microbiome-search`
- 精读后想设计研究 → `/microbiome-design`
- 查看已精读的文献列表 → `Read knowledge/graph/nodes.json`


> 调用参数提示：<PMID> [--depth quick|full]

> 建议授权工具：Bash, Read, TaskCreate, TaskUpdate

# microbiome-paper-reader：文献精读 Agent

## 快速调用示例

```
/microbiome-paper-reader 39367251
/microbiome-paper-reader 37932270 --depth quick
/microbiome-paper-reader 33753486
```

`--depth quick`：只运行 M1+M2+M3（跳过 M4 论证链，节省约 60% 时间）
`--depth full`（默认）：运行全部 7 个模块

---

## 执行流程

收到 PMID 后，**严格按以下并发架构执行**：

### 步骤 0 — 初始化任务追踪

```python
TaskCreate("数据获取: PubTator3 + PDF 双路")
TaskCreate("M1+M2+M3: 并行分析")
TaskCreate("M4: 论证逻辑链")
TaskCreate("M5+M6: 并行分析")
TaskCreate("M7: 知识图谱入库")
```

### 步骤 1 — 数据获取（双路 C 选项）

```bash
export http_proxy=VPN本地代理 && export https_proxy=VPN本地代理
python3 .claude/commands/microbiome-paper-reader/scripts/fetch_paper.py <PMID> \
  --out /tmp/paper_reader_cache
```

数据来源优先级：
1. **PubTator3 全文**（195+ passages，含 Methods/Results 全文）→ 标记 `source: pubtator3`
2. **PubMed 摘要 + PDF 全文**（pdfplumber 解析 PMC/Unpaywall）→ 标记 `source: pubmed+pdf`
3. **仅 PubMed 摘要**（全文不可用时兜底）→ 标记 `source: pubmed_only`，提示用户精读深度受限

更新 TaskCreate("数据获取") → completed。

### 步骤 2 — Phase 1：M1/M2/M3 并行

```bash
python3 .claude/commands/microbiome-paper-reader/scripts/analyze_paper.py <PMID> \
  --raw /tmp/paper_reader_cache/<PMID>_raw.json \
  --out knowledge/concepts/entries \
  --graph knowledge/graph/nodes.json
```

脚本内部并发执行 Module 1/2/3，完成后自动进入 Phase 2。

更新 TaskCreate("M1+M2+M3") → completed。

### 步骤 3 — Phase 2：M4 等待 M2+M3；M5/M6 并行

Module 4（论证逻辑链）使用 M2+M3 的输出作为上下文，和 M5/M6 同时运行。

关键输出说明：

**M4 论证逻辑链** — 这是整个精读的核心：
- 不只是数据分析链条，要覆盖**所有实验类型**：
  - 计算/生信分析（差异分析、机器学习、网络分析）
  - 体外实验（细胞系、类器官）
  - 动物模型（无菌鼠、FMT、抗生素清除）
  - 临床干预（RCT、前瞻性队列）
- 每步之间要回答：**为什么需要下一个实验？上一个实验哪里不够？**
- 标注每个中间结论的证据强度（观察性/关联性/因果性）

更新 TaskCreate("M4") 和 TaskCreate("M5+M6") → completed。

### 步骤 4 — Phase 3：M7 入库

自动完成：
- 写入 `knowledge/concepts/entries/<PMID>.md`（结构化精读 markdown）
- 更新 `knowledge/graph/nodes.json`（知识图谱索引）

更新 TaskCreate("M7") → completed。

---

## 7 个模块的职责

| 模块 | 名称 | 核心问题 | 依赖 |
|------|------|---------|------|
| M1 | 定位与背景 | 这篇文章是什么，解决什么问题 | 无 |
| M2 | 数据与队列 | 用了什么数据，质量如何 | 无 |
| M3 | 实验设计解构 | 做了哪些实验，每个解决什么 | 无 |
| M4 | 论证逻辑链 | 各实验如何串联成完整因果论证 | M2+M3 |
| M5 | 写作工艺分析 | 如何构建高水平学术论文 | 无 |
| M6 | 局限性与质疑预判 | 审稿人会质疑什么 | 无 |
| M7 | 知识图谱入库 | 整合所有结果，更新知识库 | 全部 |

---

## 输出文件

精读完成后，以下文件会被创建/更新：

```
knowledge/concepts/entries/<PMID>.md     ← 完整结构化精读
knowledge/graph/nodes.json               ← 知识图谱索引（追加节点）
```

### 精读文件结构（`<PMID>.md`）

```markdown
# [PMID] 论文标题
- Journal / Year / DOI / Authors
- Node Type: conceptual/executable | Source: pubtator3/pubmed+pdf/pubmed_only

## M1：定位与背景
## M2：数据与队列
## M3：实验设计解构
## M4：论证逻辑链        ← 核心，包含完整数据/实验因果链条
## M5：写作工艺分析
## M6：局限性与质疑预判
```

---

## 注意事项

- `source: pubmed_only` 时 M3/M4 精度受限（无 Methods/Results 全文），需提示用户
- `--depth quick` 时跳过 M4/M5/M6，适合快速入库大量文献
- 脚本执行需要 `ANTHROPIC_API_KEY` 环境变量

---

## 调用关系

- 精读后想复现代码 → `/microbiome-article-analysis`
- 精读后想找对标文献 → `/microbiome-search`
- 精读后想设计研究 → `/microbiome-design`
- 查看已精读的文献列表 → `Read knowledge/graph/nodes.json`
