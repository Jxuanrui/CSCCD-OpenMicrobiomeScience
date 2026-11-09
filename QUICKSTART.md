# QUICKSTART —— 从零到 43,773 张机制假设卡片

> 三步：装工具 → 配三张卡 → 一条命令跑全程。全程确定性（同输入同输出逐字节一致）。

## 0. 前置

- Python ≥3.10；本仓克隆后进入根目录
- 数据腿就位（见 §2 第三张卡：主图/文献库在仓内 data/；NCBI dmp 在 data/raw/；gutMGene 按其许可自取）

## 1. 安装（一次性）

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .          # 装出 kg 命令（依赖仅 typer+requests，已钉扎）
kg --version              # 应输出 kg x.y.z
```

## 2. 三张卡（都在 configs/，换病/换数/换机只动卡不动代码）

| 卡 | 路径 | 管什么 | 现成样例 |
|---|---|---|---|
| 病卡 | `configs/diseases/htn.json` | 研究什么病（MeSH/DOID/检索式/外证源） | ✅ htn |
| 队列卡 | `configs/cohorts/htn.json` | 用哪份数据（观测层/菌名桥/源名单/样本数锚） | ✅ htn |
| 数据腿登记册 | `configs/data_sources.json`（+`configs/local/` 本机件） | 数据在哪台机器（十条腿+指纹锁：文件被动过启动即罢工） | ✅ 已配 |

新机器只需改 `configs/local/`（gitignore 区，放本机绝对路径）；所有腿启动时自动验指纹。

## 2.5 两阶段心智模型（先看懂这个再跑）

工具分两个使用阶段：**数据准备期**（`kg ingest`+`kg bridge`——数据冻结时各跑一次，产出带指纹的观测层和菌名桥，属治理动作不进一键流程）与**分析期**（`kg run` 六步——可反复跑，同输入同输出逐字节一致）。QUICKSTART 以下主要讲分析期。

**环境变量（两个，按需）**：`KG_ROOT`=仓根定位（pip 打包安装到别处时必设）；`KG_LLM_BASE_URL`/`KG_LLM_API_KEY`/`KG_LLM_MODEL` 三件=`kg explain --llm` 的可选前置（不设用默认模板，完全离线）。

**分析前先查配方**：`kg recipes --disease htn`——推荐哪些分析方法（带我们自己的实测证据）。

## 3. 一条命令跑全程（HTN 首实例）

```bash
kg run --disease htn --cohort htn \
       --tier2-batch data/kg/20261101_115704_tier2_htn \
       --out-dir data/kg/run_htn_<日期>
```

- 约 30-60 秒出全套：子图 → 对账 → 三跳机制链（43,773 条）→ 队列标注 → 四票假设卡片
- 中断续跑：同命令加 `--resume`（已完且指纹相符的步自动跳过，秒级）
- **先看再跑**：加 `--dryrun` 只列"每步会写什么"，不执行
- 空证对照臂（诚实性自检）：hypothesize 加 `--null-arm-input <掏空外证文件> --null-arm-out <输出>`——掏空外部队列证据后重算，7 票档应全消（票①贡献的机器证明）
- 网页版报告：`kg report --subgraph-dir <out-dir>/subgraph --out 报告.html`（8KB 单文件，只转印不计算）
- AI 助手直连：`kg mcp` 起 stdio 插座——DeepSeek/Claude 等 MCP 客户端配置后可对话式查卡/跑管线（五工具纯转发，AI 不碰排序）
- 结果解读：`<out-dir>/subgraph/hypothesis_cards.tsv` 全量卡片；`top_hypotheses.tsv` TOP 榜；配合 `kg delivery --subgraph-dir <out-dir>/subgraph --out-dir <交付目录> --top-n 100` 生成实验室交付包；`kg explain --subgraph-dir <out-dir>/subgraph` 出人话版解读（`--llm` 可选）

## 4. 换一个病/换一份数据

- 新病：复制 `configs/diseases/htn.json` 改成你的病（MeSH/DOID/检索式/外证源；无外证源时 ext102 处置见模块提示）
- 新数据：复制 `configs/cohorts/htn.json` 改源名单/样本数锚，`configs/local/cohorts_local.json` 加你的数据路径
- 建议第一跑先 `--dryrun` 核对读写清单

## 5. 结果可信度的三句话

1. 卡片四票**全机械打分**（零 AI 裁量、零拟合）——排序可手算复核；
2. 每张卡带 `kg_version`/证据粒度/门禁状态三列披露与票①混杂警示句（外部队列佐证=描述性旁证，不作因果解释）；
3. 全链确定性：同输入重跑产物逐字节一致（sha 指纹对账，仓内 golden_check 可复验）。


## 6. 十三子命令总表（`kg --help` 同源）

| 命令 | 用途 | 阶段 |
|---|---|---|
| ingest / bridge | 数据入库 / 菌名桥 | 数据准备期（各一次） |
| subgraph / interact / deepchain / inference / hypothesize | 六步单步直调（逃生门，每步独立 manifest） | 分析期 |
| run | 一键六步+断点续跑 | 分析期 |
| report / explain | 网页报告 / 讲人话 | 后处理 |
| recipes | 分析方法配方 | 分析前 |
| mcp | AI 插座（stdio 本地） | AI 直连 |
| delivery | 实验室交付包 | 交付 |
