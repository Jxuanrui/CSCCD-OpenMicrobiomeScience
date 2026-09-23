"""可执行节点：ToolCard 注册表 + 受治理执行器。

ToolCard = 论文方法的可执行包装（入口命令 + 参数 schema + 环境要求 + 验证记录），
Registry 落盘 var/exec_registry/registry.json（gitignored），执行走 systemd 资源
限制 + 审计账本（与 R 沙箱同款治理）。MCP server 的 r_association 之外，
研究循环未来经 run_tool() 调用论文方法。
"""
from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_REGISTRY_DIR = Path(__file__).resolve().parents[3] / "var" / "exec_registry"


@dataclass
class ToolCard:
    name: str                      # 唯一工具名（如 gut_microbiome_embeddings）
    repo: str                      # owner/name
    pmcid: str                     # 来源论文
    entrypoint: list[str]          # 完整命令（在工作目录 repo 根执行）
    args_schema: dict = field(default_factory=dict)   # JSON Schema（可选）
    env_reqs: list[str] = field(default_factory=list) # 如 ["nextflow>=23", "java17"]
    workdir: str = ""              # 相对 repo 根的执行目录
    verified: str = "unverified"   # unverified / verified / partial / blocked
    verify_note: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


class Registry:
    def __init__(self, root: Path = DEFAULT_REGISTRY_DIR):
        self.root = Path(root)
        self.path = self.root / "registry.json"
        self.root.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def add(self, card: ToolCard) -> None:
        data = self._load()
        data[card.name] = card.to_dict()
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, name: str) -> ToolCard | None:
        raw = self._load().get(name)
        return ToolCard(**raw) if raw else None

    def list(self) -> list[str]:
        return sorted(self._load())

    def repos_dir(self) -> Path:
        d = self.root / "repos"
        d.mkdir(parents=True, exist_ok=True)
        return d


def run_tool(card: ToolCard, extra_args: list[str] | None = None,
             timeout_s: int = 900, max_memory_mb: int = 2048) -> dict:
    """受治理执行：systemd 资源限制 + 审计账本留痕（复用 gate 的治理件）。"""
    from .pep.ledger import AuditLedger
    from .pep.systemd_runner import systemd_scope_runner
    from .pep.types import AuditEvent

    ledger = AuditLedger(str(DEFAULT_REGISTRY_DIR / "audit.db"))
    event = AuditEvent(
        id=uuid.uuid4().hex, ts=datetime.now(timezone.utc).isoformat(),
        principal_type="agent", principal_id="exec-tools",
        action="execute_task", resource_kind="paper_tool",
        resource_id=card.name, request_id=None, subject_hash=None,
        decision="allow", approval_required=False,
        reason_codes=("exec-node",),
        constraints={"max_memory_mb": max_memory_mb, "timeout_s": timeout_s},
    )
    ledger.record_event(event)
    cwd = (DEFAULT_REGISTRY_DIR / "repos" / card.repo.split("/")[-1] / card.workdir
           if card.workdir else DEFAULT_REGISTRY_DIR / "repos" / card.repo.split("/")[-1])
    argv = [*card.entrypoint, *(extra_args or [])]
    constraints = {"max_memory_mb": max_memory_mb}
    try:
        proc = systemd_scope_runner(argv[0], argv[1:], constraints, timeout_s)
    except OSError:
        proc = subprocess.run(argv, cwd=cwd if cwd.exists() else None,
                              capture_output=True, text=True, timeout=timeout_s)
    ok = proc.returncode == 0
    return {"ok": ok, "returncode": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-800:], "stderr_tail": (proc.stderr or "")[-800:]}
