# 安全事故报告：Neo4j 密码明文 + 实例无认证暴露（2026-09-29 R1 整改）

> 状态：已闭环（轮换 + 历史清理 + 防复发）。本报告不含任何新凭据值。

## 1. 事故概要

| 项 | 内容 |
|---|---|
| 泄露类型 | Neo4j 数据库密码（README.md 进展日志明文）+ 附带发现：实例 auth 禁用且监听全网卡 |
| 首次进入 git | commit `0ef76ac`（2026-09-25，P0-closure；历史重写后对应 `32e44a3`） |
| 暴露窗口 | 2026-09-25 ~ 2026-09-29 11:45 CST |
| 是否公开 | **GitHub origin（公开仓库）从未包含该字符串**：推送前远端 main 停在 `7aae54f`（v1.2.0 release，内容级验证不含密），本轮推送为快进合并；internal-archive（本地裸仓）曾包含，已强制更新清理 |
| forks/clones 风险 | GitHub 零暴露 → 无 fork/公开 clone 风险；本地其他 clone（如双终端）需 `git fetch && git reset --hard origin/main` 同步重写后历史 |
| 轮换完成时间 | 2026-09-29 11:45 CST |
| 历史清理结果 | `git filter-repo --replace-text` 全历史替换为 `***REDACTED-NEO4J-PASSWORD-2026***`；双远端已推送（GitHub 快进 / internal-archive 强推）；全历史 `git grep` 残留 = 0；重写前全量备份 `/data/LYteamwork/JiXuanRui/Project/kg-pre-rewrite-backup-20260929.bundle` |
| 异常访问迹象 | 未发现（http.log 无 401；query.log/security.log 无非 127.0.0.1 来源）。局限：auth 禁用期间来源记录有限，不能完全排除内网访问 |

## 2. 比密码泄露更严重的附带发现

- 运行实例 `dbms.security.auth_enabled=false`（显式配置）——README 所述"9/25 密码重置"对运行实例**从未生效**（无 auth.ini，且 auth 关闭时任意凭据均可连接）。
- 监听地址 `0.0.0.0:17474/17687`（全部网卡）——实际暴露面为"**内网无认证读写**"，与密码是否泄露无关。
- 整改：`auth_enabled=true` + 监听收紧 `127.0.0.1`；已生效并验证（旧密码 AuthError、新密码可用、端口仅本地）。

## 3. 处置时间线（CST 2026-09-29）

1. 11:15 在线 `ALTER CURRENT USER SET PASSWORD` 尝试 → 发现 auth 被禁用（暴露面升级）
2. 11:30 conf 修改（auth on + 127.0.0.1）、优雅停库（日志确认 Stopped）、auth.ini 设新密、重启
3. 11:36 **过程中二次泄露**：`NEO4J_PASSWORD` 环境变量被容器入口点物化为 `neo4j.conf` 明文 `PASSWORD=` 行（conf 644 权限，本地短暂可见 ~10 分钟）→ 清除该行并**再次轮换**；教训：**严禁向该 Neo4j 容器传递 `NEO4J_PASSWORD` 环境变量**（启动命令需 `env -u NEO4J_PASSWORD`）
4. 11:40 发现 5.x 真实用户库存于 system 数据库（auth.ini 仅首次引导）→ **外科手术**：备份移走 system 库（保留于 `data/neo4j-data/system_db_backup_20260929/`，可回退）→ 重建 system 库从 auth.ini 引导新密；图数据 neo4j.db 未动（节点 12,485 / 关系 20,911 与停机前一致）
5. 11:45 验证通过：旧密码失效 / 新密码有效 / 仅 127.0.0.1 监听
6. 12:0x README 明文脱敏（commit `5755caa` → 重写后历史内含）+ 全仓扫描（`sk-` 模式零命中）
7. 12:1x `git filter-repo` 历史重写 + 双远端推送 + 备份 bundle

## 4. 防复发措施

- `.githooks/pre-commit`：暂存新增行 secret 模式扫描（sk- key / NEO4J_PASSWORD= / api_key / password 赋值），`git config core.hooksPath .githooks` 已启用；误报出口 `--no-verify`（须在 commit message 说明理由）
- 凭据纪律重申：仅 `.env`（已 gitignore，600 权限）/ 环境变量；本报告与任何整改文档**禁止**出现新密码
- 监工审核清单已含秘钥纪律检查项（`.claude/agents/supervisor.md`）
- 后续可补充：CI 侧 secret scanning / push protection（GitHub 仓库设置，属平台操作，待用户执行）

## 5. 遗留事项

- 旧 system 数据库备份（`system_db_backup_20260929/`）保留 30 天后清理（确认无回退需求）
- 备份 bundle 含**重写前的含密历史**（`kg-pre-rewrite-backup-20260929.bundle`，本地文件，不对外），保留至确认双远端与各 clone 稳定后删除
- Neo4j 实例启动方式已变更（新实例名 `kg_neo4j`，需 `env -u NEO4J_PASSWORD` 启动）——运维入口以本报告与 README 为准
