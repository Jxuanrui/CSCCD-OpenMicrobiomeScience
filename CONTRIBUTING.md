# Contributing & Governance

## 执行规则（治理）

1. **计划→执行→审核**：改动先列计划再执行，执行后独立审查；质量门禁以审查裁决为准（有条件通过须先整改后开工）
2. **修改 src/ 前**必须在进展记录中注明
3. **新建路径**（目录/数据管线/工具）先报审获准再创建，禁止未审先建
4. **单一说明文档**：每项机制只保留一份说明文档，其余并入或删除，禁止多份并存漂移
5. **MVP 先行**：新功能先做最小可用版本并通过验收，再考虑扩展
6. **P0 冻结**：发布周期内 P0 问题未修完不开新轨道（分级：P0 必改 → P1 强烈建议 → P2 可延后）
7. **执行模型**：单仓库、单 worktree、单路径开发；多终端并行须先经批准
8. **API 密钥**只走 `.env`（gitignored），永不入 git/crontab
9. **data/merged 写入**须过 write_guard 闸门（授权键+execution_id+审计账本）
10. **不可逆操作**（删除/覆盖）先备份、再报审、后执行
11. **进程操作**按精确 PID，禁宽匹配 kill

## radar 数据落盘的批次授权模型（v4 重启 08:30 cron 前实施）

每日 PubMed 抓取需写 `data/pubtator`（write_guard 保护），与一次性授权键天然冲突。方案：
1. **按批次签发**：每次扩语料批次开始时，为该批次签发**限路径、限量、限有效期**的批次键（registry 新增 scope 列：`path=data/pubtator; quota=<篇数>; expires=<日期>`）；
2. **cron 侧换券**：fetch_pubtator 首次用批次键换取**本地会话凭证**（root-only 文件），有效期内每日 cron 持会话凭证而非原始键——write_guard 校验会话凭证与批次 scope；
3. **fail-closed 不变**：会话凭证过期/超量即拒绝，恢复需重新签发——不放宽闸门，只把"每日要钥匙"变成"每批换一次钥匙"；
4. radar 每日 PMID 列表（07:30 run_daily 写 radar/data/daily）不经 write_guard，不受影响。

## 版本记录

历史变更见 [CHANGELOG.md](CHANGELOG.md)（v1.2.0 → v3.0.6 完整条目）。
