"""P4 Multi-workspace Isolation——workspace 成为真正的安全边界，不是文件夹名。

核心验收标准（用户裁决 2026-09-25）：两个项目同时存在时，系统**不知道、
不读取、不修改**另一个项目不应该看到的科研事实。

边界模型（身份层级：Workspace → ResearchTask → Analysis → Candidate →
Evidence；workspace scope 由账本物理隔离承载，核心对象经 ctx/guard 获得授权边界）：

- **查询隔离**：Workspace 实例只读本 study 账本（构造性保证，B4 已测）；
  capability API 面（record_evidence / mutation / record_execution）加
  **ctx 激活式写入守卫**——ctx 携带 workspace_id 且目标 study 不同且无
  匹配 grant → 拒绝。ctx 无 workspace_id = legacy 单信任域（向后兼容）。
- **跨库引用**：默认 workspace-private；跨库使用必须显式
  `cross_workspace_reference`（source/target workspace + authorization +
  provenance，落为目标账本里的 CrossWorkspaceReference 记录）——引用而
  非复制，且须 read grant。
- **KG 可见性**：snapshot identity ≠ access permission——可见性由独立注册表
  裁定（shared / private(workspace) / restricted(permission)）。
- **凭据边界**：workspace auth_scope 注册表（允许的外部 source 集合）；
  查询前 check，violation = 0。
- **workspace 预算池**：ResourceBudget 以 `__workspace__` scope 声明时约束
  全 workspace 所有 task 的合计用量——child task 不能分裂绕过（P1 防绕过
  的 workspace 级延伸）。

P4 明确不做：多租户 SaaS / 用户管理系统 / RBAC 全平台 / 网络隔离 / 云部署。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .resources import WORKSPACE_BUDGET_SCOPE  # noqa: F401 （P4 workspace 预算哨兵，单一来源）

_GRANT_SCOPES = ("read_evidence", "write_evidence", "mutate_evidence", "backup")


class IsolationError(PermissionError):
    """跨 workspace 边界违例（查询/写入/凭据/可见性/预算逃逸）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


class CrossWorkspaceGrant:
    """显式跨库授权（source → target，限定 scope；authorization 记录批准者/理由）。"""

    def __init__(self, source_workspace_id: str, target_workspace_id: str,
                 scope: str, authorization: str, provenance: str = "",
                 grant_id: str | None = None):
        if scope not in _GRANT_SCOPES:
            raise IsolationError(f"grant scope 须为 {_GRANT_SCOPES} 之一")
        self.grant_id = grant_id or f"GRANT-{uuid.uuid4().hex[:10]}"
        self.source_workspace_id = source_workspace_id
        self.target_workspace_id = target_workspace_id
        self.scope = scope
        self.authorization = authorization
        self.provenance = provenance
        self.created_at = _now()

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in
                ("grant_id", "source_workspace_id", "target_workspace_id",
                 "scope", "authorization", "provenance", "created_at")}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CrossWorkspaceGrant":
        return cls(d["source_workspace_id"], d["target_workspace_id"],
                   d["scope"], d["authorization"], d.get("provenance", ""),
                   d.get("grant_id"))


class IsolationRegistries:
    """workspace 根目录级的隔离元数据（grants / KG 可见性 / 凭据 scope）。

    文件落位 <root>/_isolation/*.json——不属于任何 study 账本，天然不进
    per-study 备份（Backup scope violation = 0 的构造性保证之一）。
    """

    def __init__(self, root: Path | str):
        self.dir = Path(root) / "_isolation"
        self.dir.mkdir(parents=True, exist_ok=True)

    # ---- grants ----
    def add_grant(self, grant: CrossWorkspaceGrant) -> None:
        grants = _load_json(self.dir / "grants.json", [])
        grants.append(grant.to_dict())
        (self.dir / "grants.json").write_text(
            json.dumps(grants, ensure_ascii=False, indent=1), encoding="utf-8")

    def grants(self) -> list[dict[str, Any]]:
        return _load_json(self.dir / "grants.json", [])

    # ---- KG 可见性 ----
    def set_kg_visibility(self, snapshot_id: str, visibility: str,
                          workspace_id: str = "", required_permission: str = "") -> None:
        if visibility not in ("shared", "private", "restricted"):
            raise IsolationError("visibility 须为 shared/private/restricted")
        if visibility == "private" and not workspace_id:
            raise IsolationError("private 快照必须声明归属 workspace")
        vis = _load_json(self.dir / "kg_visibility.json", {})
        vis[snapshot_id] = {"visibility": visibility, "workspace_id": workspace_id,
                            "required_permission": required_permission}
        (self.dir / "kg_visibility.json").write_text(
            json.dumps(vis, ensure_ascii=False, indent=1), encoding="utf-8")

    def check_kg_access(self, workspace_id: str, snapshot_id: str,
                        permission: str = "") -> bool:
        """snapshot identity ≠ access permission：可见性以注册表为准。"""
        vis = _load_json(self.dir / "kg_visibility.json", {})
        entry = vis.get(snapshot_id)
        if entry is None or entry["visibility"] == "shared":
            return True  # 未登记默认 shared（向后兼容）
        if entry["visibility"] == "private":
            return entry.get("workspace_id") == workspace_id
        required = entry.get("required_permission", "")
        return bool(permission) and permission == required

    # ---- 凭据 scope ----
    def set_auth_scope(self, workspace_id: str, allowed_sources: list[str]) -> None:
        scopes = _load_json(self.dir / "auth_scopes.json", {})
        scopes[workspace_id] = allowed_sources
        (self.dir / "auth_scopes.json").write_text(
            json.dumps(scopes, ensure_ascii=False, indent=1), encoding="utf-8")

    def check_credential_scope(self, workspace_id: str, source_id: str) -> None:
        scopes = _load_json(self.dir / "auth_scopes.json", {})
        allowed = scopes.get(workspace_id)
        if allowed is None or "*" in allowed:
            return  # 未登记默认全域（legacy 单信任域）
        if source_id not in allowed:
            raise IsolationError(
                f"credential scope 违例：workspace {workspace_id} 无权访问 "
                f"外部源 {source_id}（允许：{allowed}）")


def guard_workspace_write(study_id: str, ctx: dict[str, Any],
                          scope: str = "write_evidence") -> None:
    """capability 写入守卫（ctx 激活式）：ctx 带 workspace_id 且目标不同 →
    须有匹配 grant，否则拒绝。ctx 无 workspace_id = legacy（不拦）。"""
    ws = ctx.get("workspace_id")
    if not ws or ws == study_id:
        return
    for g in ctx.get("cross_workspace_grants") or []:
        if (g.get("source_workspace_id") == ws
                and g.get("target_workspace_id") == study_id
                and g.get("scope") == scope):
            return
    raise IsolationError(
        f"跨 workspace 写入拒绝：ctx 绑定 {ws}，目标 {study_id}，"
        f"需要 scope={scope} 的显式 grant（cross-workspace isolation invariant）")


def query_with_scope(adapter: Any, query_str: str, workspace_id: str,
                     registries: IsolationRegistries, page_size: int = 25):
    """凭据边界内的契约查询：先 check_credential_scope，再走统一 A–E 契约。"""
    from .knowledge.sources.contract import query
    registries.check_credential_scope(workspace_id, adapter.describe().source_id)
    return query(adapter, query_str, page_size)


def cross_workspace_reference(target_ws, evidence_id: str,
                              source_workspace_id: str,
                              grant: dict[str, Any],
                              registries: IsolationRegistries,
                              provenance: str = "") -> dict[str, Any]:
    """显式跨库证据引用：read grant 校验后，在目标账本落 CrossWorkspaceReference。

    引用而非复制：源 Evidence 仍留在源账本；目标侧只持有带授权与来源的
    类型化引用。grant 必须是 source→target 且 scope=read_evidence。
    """
    from .workspace import CrossWorkspaceReference
    if not (grant.get("source_workspace_id") == source_workspace_id
            and grant.get("target_workspace_id") == target_ws.study_dir.name
            and grant.get("scope") == "read_evidence"):
        raise IsolationError(
            "跨库引用需要 source→target 且 scope=read_evidence 的有效 grant")
    # grant 必须真实登记在注册表（形状正确 ≠ 已授权——防伪造授权字典）
    registered = any(
        (grant.get("grant_id") and g.get("grant_id") == grant.get("grant_id"))
        or g == grant for g in registries.grants())
    if not registered:
        raise IsolationError("grant 未在隔离注册表登记（伪造授权拒绝）")
    ref = CrossWorkspaceReference(
        reference_id=f"XREF-{uuid.uuid4().hex[:10]}",
        evidence_id=evidence_id,
        source_workspace_id=source_workspace_id,
        target_workspace_id=target_ws.study_dir.name,
        approval=grant.get("authorization", ""),
        provenance=provenance or grant.get("provenance", ""))
    target_ws.append(ref)
    return ref.model_dump()


__all__ = ["CrossWorkspaceGrant", "IsolationError", "IsolationRegistries",
           "WORKSPACE_BUDGET_SCOPE", "cross_workspace_reference",
           "guard_workspace_write", "query_with_scope"]
