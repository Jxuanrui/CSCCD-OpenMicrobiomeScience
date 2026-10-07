# Agent Skills 验证记录

本目录 23 个技能遵循 [Agent Skills 开放标准](https://agentskills.io)（SKILL.md + frontmatter），
仅在 Knowledge_Graph 项目内生效（开发期不安装到用户级目录）。

## 验证状态（2026-09-19）

| 端 | 状态 | 说明 |
|---|---|---|
| MCP 兼容客户端（GLM） | ✅ 通过 | 结构 23/23；运行时实跑 microbiome-kg 全部命令（多跳查询/预测表/审核表） |
| MCP 兼容客户端（Anthropic） | ✅ 通过（2026-09-19 用户执行清单验证） | 开放标准原生支持 |
| Codex / 其他 | 📋 开放标准预期兼容 | Codex 官方支持 Agent Skills（developers.openai.com/codex/skills）；未实测 |

## MCP 兼容客户端（Anthropic） 一键验证清单

```bash
cd $KG_HOME
# 1) 让 MCP 兼容客户端（Anthropic） 发现项目技能（复制到项目级技能目录，仅本项目生效）
mkdir -p .claude/skills && cp -r skills/* .claude/skills/
# 2) 在 MCP 兼容客户端（Anthropic） 中依次执行：
#    a. /microbiome-kg  → 问："Faecalibacterium prausnitzii 与哪些疾病相关？"
#       预期：调用 graph_analysis query 返回扇出表（Neo4j 无关，纯本地）
#    b. /microbiome-kg  → 问："链接预测给出哪些 T2D 相关假设？"
#       预期：读取 data/merged/candidate_v3/rotate_predictions.tsv 过滤 Type_II_diabetes_mellitus
#    c. /microbiome-frontier 宏基因组 IBD 120例 vs 60对照
#       预期：走两阶段流程（文献扫描+方向分析；如需实时检索会请求 WebSearch 授权）
# 3) 验收标准：三条命令均按技能文档流程执行且产出含证据分级/PMID
# 4) 验证完成后可删除 .claude/skills/（保持项目整洁）或保留供日常使用
```

## Codex / 其他 agent 接入说明

SKILL.md 为开放标准（Anthropic 发起，40+ agent 客户端采纳）。Codex 使用方式见其官方文档
（`developers.openai.com/codex/skills`）；通用做法：将 `skills/<name>` 目录放入对应
agent 的技能发现路径（如 `~/.codex/skills/`）或在会话中直接引用 SKILL.md 路径。
脚本层为纯 Python CLI，与 agent 无关；LLM 依赖（仅 lightrag_qa）需 OPENAI_* 环境变量。

## 技能清单

- `microbiome-kg`（本图谱原生）：多跳查询 / LightRAG 问答 / 链接预测 / Tier-C 待审
- 22 个自 MicrobeScholar 泛化迁移：选题/生信/写作/投稿/基金全流程（结构见各自 SKILL.md）
