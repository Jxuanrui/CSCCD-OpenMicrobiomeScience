#!/usr/bin/env python3
"""P0-6 写入准入控制（2026-09-29 监工令）：带授权的写入闸门 + 审计账本。

背景：09-29 08:30（articles.jsonl 被无执行记录重生成）与 10:47（merge_qc 刷新 +
Neo4j 无 provenance 写入 +13 Entity）两起无主写入事件的防复发控制。

契约：对 data/merged/、data/pubtator/articles.jsonl、Neo4j 物化三类写入面，
执行方必须携带 (execution_id, authorization_key)；authorization_key 必须已登记于
data/registry/write_authorizations.tsv 且状态 active，否则写入拒绝（fail-closed）。
放行的写入逐条追加 data/logs/write_audit.jsonl（append-only 审计账本）。
"""
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUTH_TSV = ROOT / "data/registry/write_authorizations.tsv"
AUDIT = ROOT / "data/logs/write_audit.jsonl"

_EXEC_RE = re.compile(r"^EX-[A-Za-z0-9._-]+$")


class WriteGuardError(RuntimeError):
    """写入被拒绝（fail-closed）。"""


def _load_auths():
    if not AUTH_TSV.exists():
        return {}
    auths = {}
    lines = AUTH_TSV.read_text(encoding="utf-8").splitlines()
    for line in lines[1:]:
        c = line.split("\t")
        if len(c) >= 4 and c[3].strip() == "active":
            auths[c[0].strip()] = {"targets": c[1].strip(),
                                   "granted_by": c[2].strip(),
                                   "note": c[4].strip() if len(c) > 4 else ""}
    return auths


def guard_write(target: str, execution_id: str, authorization_key: str) -> dict:
    """写入前置闸门：校验通过 → 记审计并返回放行记录；否则 raise WriteGuardError。"""
    if not execution_id or not _EXEC_RE.match(execution_id):
        raise WriteGuardError(f"[write_guard] 非法 execution_id：{execution_id!r}（须 EX-* 格式）")
    auths = _load_auths()
    auth = auths.get(authorization_key)
    if not auth:
        raise WriteGuardError(
            f"[write_guard] 未登记/未激活的授权键 {authorization_key!r}——"
            f"写入 {target} 被拒绝（fail-closed）。登记文件：{AUTH_TSV}")
    allowed = auth["targets"]
    if allowed != "*" and target not in allowed.split(";"):
        raise WriteGuardError(
            f"[write_guard] 授权键 {authorization_key!r} 不覆盖目标 {target}（允许：{allowed}）")
    record = {"ts": datetime.now(timezone.utc).isoformat(),
              "execution_id": execution_id, "target": target,
              "authorization_key": authorization_key,
              "granted_by": auth["granted_by"]}
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def new_execution_id(prefix: str) -> str:
    return f"EX-{prefix}-{uuid.uuid4().hex[:8]}"
