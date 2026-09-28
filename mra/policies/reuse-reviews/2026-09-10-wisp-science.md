# Reuse Review 2026-09-10：wisp-science

> 首份 Reuse Review 记录，同时作为后续所有记录的模板（六段式：检索记录 → 架构对照 → 许可证 → 决策 → 借鉴清单 → 风险）。
> 评审执行：Kimi K3 主导独立审查；Codex 评审停滞（34 分钟无输出，已终止），按 CLAUDE.md 降级路径改由 Gemini 独立完成，两方结论高度收敛。

## 1. 功能问题与检索记录

- **问题**：评估 `xuzhougeng/wisp-science` 能否融入 MicrobiomeResearchAgent（整体采用 / 组件级复用 / 仅借鉴）。
- **检索**：2026-09-10，`git clone --depth 1` 自 GitHub（commit `1b58d8c`，main，v1.10.0；GitHub API 未认证限流、raw 域名被重置，git 协议可用）。本地只读审查 README(zh)、LICENSE、Cargo.toml、docs/（development/publication-evidence/basic-configuration 等）、crates/ 全部 12 个 crate 入口、skills/ 目录。
- **对象概况**：开源本地优先 AI 科研工作台（WISP = Workspace for Intelligent Scientific Practice）。Rust workspace（14 crate + Tauri v2 桌面端 + Leptos 前端 + 浏览器扩展，约 168k 行 Rust），**AGPL-3.0-only**，开发极活跃（评审当日有合并提交），Zenodo DOI 10.5281/zenodo.22009273。功能：OpenAI/Anthropic 双协议模型抽象 + 分档路由、单 Agent 循环 + 三层上下文压缩、文件/shell 工具 + 危险命令门控、逐工具审批（allow/ask/deny + 记忆授权 + IM 一次性审批码）、持久 Python/R 内核（本地/WSL/SSH）、wisp-bio 原生数据库检索（23 域模块/61 独立 URL，**无微生物组域**）、MCP 客户端、ACP v1 桥接外部编码 Agent、Run 控制面、SQLite+OS keyring 存储、证据胶囊（publication evidence）、35 个 SKILL.md、加密同步。

## 2. 架构对照（对本平台八层）

| 本平台层（PLANNING.md 第 4 章） | wisp 对应物 | 匹配度 |
|---|---|---|
| ModelRuntime | wisp-llm（Provider trait + RoutedProvider） | 形似，缺能力声明/成本治理/升级门 |
| OPA/Rego PDP | 无（仅单用户工具级审批弹窗） | **完全无对应** |
| 统一 Python PEP | 无（crate 内直通 shell/REPL） | **完全无对应** |
| LangGraph 六角色工作流 | 无（单 Agent 循环 / ACP 委派） | **完全无对应** |
| 知识层（四类包+SQLite/FTS5） | wisp-store（会话/产物库）+ wisp-skills | 部分形似，抽象目标不同 |
| Apptainer 双轨执行 | 无（宿主机直跑；其文档自认"仅进程级隔离，非容器沙箱"） | **完全无对应** |
| 审计/溯源（MLflow+PROV） | wisp-runs + 证据胶囊 | 有参考价值 |
| Web 工作台（FastAPI） | Tauri+Leptos 桌面端 | 栈不兼容 |

本平台核心差异项（多角色审批矩阵、最小数据投影、staging→结果晋升、反证审计）在 wisp 中全部缺失。

## 3. 许可证分析（AGPL-3.0-only）

- (a) 源码复制/改造进 Python 平台：**高风险不可行**。衍生作品传染整个平台（含治理资产），且未来多用户网络服务触发 AGPL 网络条款，须整体开源。
- (b) 独立进程 + MCP 协议通信：**低风险**。臂长通信不构成衍生作品，是唯一可行的组合融合通道。
- (c) 阅读借鉴设计思想自行实现：**无风险**。思想不受版权保护，净室重写无传染。

## 4. 最终决策（2026-09-10 用户确认，PLANNING.md 第 9 章 Q9）

- **底盘路线：路线 C**——wisp-science 原样作为研究人员个人探索"卫星工具"使用（不修改、不 fork）；本平台按既定 Python 组件组装路线自建；设计思想按借鉴清单吸收。
- **不直接采用 / 不做源码级适配封装**（治理模型根本错位 + Rust 栈认知税 + AGPL + 上游高速迭代）。
- **wisp-bio 数据库连接器：入 backlog**（首版不做；证据检索节点首版用 Python 直连 PubMed/EuropePMC 等 HTTP API，现有 paper-lookup skill 已覆盖 11 库；确有 80 库需求时再评估独立进程 MCP 化）。

## 5. 借鉴清单（设计参考，出处备查）

1. **证据胶囊**（docs/publication-evidence.md）：不可变绑定精确 ArtifactVersion（绑定后不跟随 latest）、Candidate/Selected/Rejected 选择态、Public/Restricted/Private 可见性、archived→traceable→re_executable→reproduced 能力分级、冻结前 finalization check + PHI/PII 确认 → 直接启发本平台"结果晋升"元数据与审计快照设计。
2. **SKILL.md 渐进加载**（crates/wisp-skills）：`search_skills`/`use_skill` 两步按需取回，不污染上下文 → 知识层 FTS5 检索模式参照。
3. **上下文压缩 archive-first**（docs/development.md）：压缩前归档、旧轮次绝不静默丢弃 → 审计友好的会话管理原则。
4. **Run 独立生命周期对象**（crates/wisp-runs）：提交/监控/收割解耦，长任务不阻塞会话 → 受控执行节点的任务模型参照。
5. **OS keyring 凭据**（crates/wisp-store）：密钥只进系统密钥环不落 SQLite → 凭据隔离参照（Python 侧对应 keyring 库）。
6. **审批 UX**（docs/basic-configuration.md）：逐工具 allow/ask/deny + 记忆授权 + IM 渠道一次性审批码、远程不可开完全权限 → 工作台审批队列 UX 参照。
7. **反证审计 skill**（skills/audit-biomedical-paper-evidence）：范围守卫、"不得声称已读取未提供的全文"反幻觉条款、相关性/方向性/必要性/充分性/因果性分层 → 滋养"统计与反证审计"节点规则措辞。
8. **演示种子会话**（seed/ 内置 RNA-seq 演示轨迹）→ 合成数据端到端演示可采用"录制轨迹"形式。

## 6. 风险与核实记录

- README"约 80 个科学数据库"口径有水分：实为 wisp-bio 硬编码 Rust 客户端（23 个域模块、61 个独立 URL），非标准 MCP server；外部复用须经其 CLI/桥接。
- 隔离度不足：其 Clean Verification 文档自承"process-level isolation rather than a hardened OS/container network sandbox"，不满足本平台 5.8/5.14 基线。
- 无细粒度数据边界：Agent 对项目目录任意读写，无"只读源数据 + staging 晋升"分离。
- 无微生物组能力：wisp-bio 与 35 个 skills 均无微生物组域，与 PLANNING.md 附录 A"该领域空白"结论互证。
- Gemini 报告中两处小误差已校正：版本号实为 v1.10.0（非 v0.2）；"约 247 个生物信息工具"未经独立核实，不作为依据。
