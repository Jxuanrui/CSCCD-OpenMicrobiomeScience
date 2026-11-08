# kg-tool

Gut Microbiome Multi-layer Knowledge Platform —— 输入疾病名与宏基因组测序数据，
输出带全链路证据溯源的机制假设卡片（菌 → 代谢物 → 基因 → 通路）。

## 特性

- **确定性内核**：同输入永远逐字节同输出（四票全机械排序，零 LLM 零拟合零黑箱）
- **证据溯源**：每张假设卡片的每一步可回溯文献原句（PMID 级）
- **一条命令全流程**：`kg run --disease <key> --cohort <key> …`（六步依赖序+断点续跑）
- **AI 原生**：`kg mcp` 起 stdio MCP 插座，任何 MCP 客户端（Claude/DeepResearch 类代理）可直连调用
- **方法配方**：`kg recipes` 给出带实测证据的分析方法推荐

## 快速开始

见 [QUICKSTART.md](QUICKSTART.md)。安装：`pip install -e .`（依赖仅 typer/requests/mcp，已钉扎）。

## 数据

图谱数据包（CC BY-NC 4.0）与 tier2 批次经 `kg init`（发布通道）分发，不入本仓。
`configs/local/` 放本机数据路径（gitignore）。

## 引用

DOI 见 Releases（Zenodo）。许可证：代码 MIT / 数据 CC BY-NC 4.0 分层。

## 测试

`pytest -m "not needs_data"`（公网 CI 口径；带数据的完整测试需自备数据腿）。
