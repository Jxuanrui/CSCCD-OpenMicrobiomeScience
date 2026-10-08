# MRA — MicrobiomeResearchAgent

模型可插拔、知识可审计、权限可控的菌群科研 Agent 平台。设计决策与治理规则见本地 `PLANNING.md`（不入库）。

## 能力一览

- **研究循环**：LLM planner 在受控动作面（图谱检索 / R 统计沙箱 / 文献速读）上自主迭代，KSDS 会话状态全程落盘、断点续跑、预算双闸（会话级 + 全局日额度）
- **治理底盘**：执行闸门（systemd 资源限制 + 审计账本 + 统计审计规则，FAIL 硬阻断）；OPA/PEP 完整权限信封（多用户/不可信代码场景启用）
- **知识层**：KG 快照只读检索（证据分级 A/B 随行）+ SQLite/FTS5 知识包 + PubMed 批量速读（参数级缓存）
- **评测**：回放检索回归 / 时序留出（PoT-HINDSIGHT 对齐，预见缺口可量化）/ 统一入口 `python -m mra.benchmark.microbiome_eval`
- **MCP 出口**：`python -m mra.mcp_server` 把五工具按 MCP 标准暴露（宿主配置示例见 `mcp_host.example.json`）
- **Atlas 扫描器**：中心级全暴露 × 全特征偏 Spearman + 图谱三分类定级

## 快速开始

```bash
cd mra && uv sync && uv run pytest -q          # 全量测试

# 数据契约（不入库）：复制 cohort_config.example.json 到 var/cohort_config.json，
# 填写部署机的暴露/特征/协变量表绝对路径。

# 首次部署必须初始化方法知识库（否则 gap_check/method_query 全空、研究环以
# unresolved_method_gap 停止——2026-10-07 G1 实测）：
uv run python -c "from pathlib import Path; from mra.knowledge.method_rules import ingest_method_dir; print(ingest_method_dir(Path('knowledge/methods').resolve()))"
# 注意勿在仓库根跑 mra/knowledge/cli.py（相对路径会在错误位置建 var/）；
# R 沙箱路径经 RSCRIPT_BIN 注入，未设时回退系统 PATH 的 Rscript。
# 部署自检：uv run python -m mra（doctor 会检查 KG_MERGED_DIR/cohort_config/快照）。

# 一次自主研究会话（需 ARK_API_KEY；GLM_API_KEY 为可选备胎）
ARK_API_KEY=... python -m mra.research \
    --question "<研究问题>" --target "<数据/队列描述>" --max-iterations 16
# 断点续跑：--resume <run_id>

# 队列全景扫描 / 评测 / MCP 服务
python -m mra.research.atlas
python -m mra.benchmark.microbiome_eval 2022
python -m mra.mcp_server
```

## 环境变量

| 变量 | 用途 | 必需 |
|---|---|---|
| `COHORT_CONFIG` | 队列表注册表 JSON（缺省 `var/cohort_config.json`） | 数据分析类工具 |
| `KG_MERGED_DIR` | 主图谱 merged TSV 目录（快照源） | 图谱类工具 |
| `RSCRIPT_BIN` | R 沙箱 Rscript 路径 | r_association |
| `ARK_API_KEY` / `GLM_API_KEY`(+`GLM_BASE_URL`) | 主模型 / 备胎中转 | LLM 类 |
| `LLM_DAILY_CALL_CAP` | 全局日预算（默认 500） | 否 |
| `KG_SNAPSHOTS_ROOT` | MCP 进程的快照根 | MCP 场景 |

## 仓库布局

```
mra/src/mra/      # kg(图检索) research(循环/沙箱/闸门/atlas/litread) eval benchmark
                   # model_runtime(ARK/GLM) pep(治理) audit(统计审计) knowledge web
mra/tests/         # pytest（宿主依赖用例在 CI 自动跳过）
mra/policies/      # OPA bundle、模型能力表、价格快照
mra/var/           # 运行态（gitignored）：快照/会话/审计/预算/缓存
```

## 协作与保密约定

- 队列数据与研究思路只存在于部署机本地（`var/`，不入库）；代码零环境特定默认值
- 凭据一律环境变量注入，禁止写入任何文件
- 相邻子系统：`../radar`（文献雷达，日更 cron）、`../artifact_engine`（论文代码仓库发现 + 三路分流）
