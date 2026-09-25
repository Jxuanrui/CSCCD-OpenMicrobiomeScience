"""P2 统一 External Knowledge Adapter Contract——五件套 + 失败分类法 + 多源冲突。

任何外部知识源接入 Research Loop 前必须满足五件套（用户裁决 2026-09-24）：
1. **Provenance**：source_id / query / retrieval path / evidence origin；
2. **Retrieved_at**：历史缓存不得伪装当前实时知识；
3. **Raw identity/hash**：raw response identity + content hash（replay/审计）；
4. **Failure taxonomy**：timeout / rate_limited / malformed / unavailable /
   empty 五态精确区分——禁止一切失败折叠为"没有结果"；
   （valid empty = 知识结果；unavailable = 系统状态，二者不得混淆）
5. **Cache semantics**：cache hit ≠ live retrieval success（from_cache +
   原始 retrieved_at + raw hash 保留）。

边界（P2 明确不做）：不自动裁决哪个 source 更可信、不自动解决冲突、
不替代人工领域判断——冲突进入 manual_review_required 状态并保留全部
provenance。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..types import CandidateEvidence

STATUSES = ("success", "empty", "timeout", "rate_limited", "malformed", "unavailable")
#: 正当知识结果（可缓存）；其余为系统状态
RESULT_OK = ("success", "empty")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class ExternalQueryResult:
    """统一查询信封：一个外部源对一次查询的完整、可分类回应。"""

    status: str                       # STATUSES 之一
    source_id: str
    query: str
    retrieved_at: str = ""
    raw_hash: str = ""                # envelope 级 raw identity（逐条在 evidence 上）
    evidence_items: list[CandidateEvidence] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    from_cache: bool = False
    cache_fallback_after: str = ""    # 缓存兜底时所覆盖的失败态（明示非 live）
    error: str = ""                   # 失败态的错误事实

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"status 须为 {STATUSES} 之一")
        if self.status in RESULT_OK and self.error:
            raise ValueError("正当结果不得携带 error（失败才描述失败）")
        if self.status not in RESULT_OK and self.evidence_items:
            raise ValueError("失败态不得携带 evidence（禁止失败伪装结果）")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExternalQueryResult":
        items = [CandidateEvidence(**i) for i in d.get("evidence_items", [])]
        return cls(**{**d, "evidence_items": items})


def classify_exception(exc: Exception) -> str:
    """失败分类法（契约层集中实现）：无 status_hint 的原始异常按类型判定。

    timeout（TimeoutError/socket.timeout）/ rate_limited（HTTP 429）/
    malformed（JSON 解析失败）/ unavailable（其余网络/HTTP 错误）。
    """
    import json as _json
    import urllib.error as _urlerr
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, _json.JSONDecodeError):
        return "malformed"
    if isinstance(exc, _urlerr.HTTPError):
        if exc.code == 429 or "Too Many Requests" in str(exc):
            return "rate_limited"
        return "unavailable"
    if isinstance(exc, _urlerr.URLError):
        reason = str(getattr(exc, "reason", ""))
        if "timed out" in reason:
            return "timeout"
        return "unavailable"
    return "unavailable"


def query(adapter: Any, query_str: str, page_size: int = 25) -> ExternalQueryResult:
    """契约查询：把任意适配器（EuropePMC/OmniPath/…）纳入统一信封。

    调用方契约错误（ValueError：空 query 等）原样抛出——那是 bad request，
    不是源失败。源失败优先读适配器 status_hint；注入 fetcher 的原始异常
    由 classify_exception 按类型归类（分类法唯一权威在契约层）。
    """
    desc = adapter.describe()
    try:
        items = adapter.search(query_str, page_size)
    except (ValueError, TypeError):
        raise
    except Exception as exc:  # noqa: BLE001 —— 分类后入信封，不静默
        hint = getattr(exc, "status_hint", None) or classify_exception(exc)
        return ExternalQueryResult(
            status=hint,
            source_id=desc.source_id, query=query_str, retrieved_at=_now(),
            error=f"{type(exc).__name__}: {exc}")
    raw = "sha256:" + hashlib.sha256(
        "".join(i.raw_response_hash for i in items).encode("utf-8")).hexdigest()
    return ExternalQueryResult(
        status="success" if items else "empty",
        source_id=desc.source_id, query=query_str,
        retrieved_at=items[0].retrieved_at if items else _now(),
        raw_hash=raw, evidence_items=items,
        provenance={"source_version": desc.version,
                    "source_type": desc.source_type,
                    "evidence_origin": desc.evidence_origin,
                    "default_use": desc.default_use,
                    "provenance_fields": list(desc.provenance_fields),
                    "retrieval_path": f"{desc.transport}:{desc.endpoint}"})


class CachedSource:
    """缓存包装（五件套第 5 条）：cache hit ≠ live success。

    只缓存正当知识结果（success/empty）；失败态零入缓存。live 失败且有缓存
    → 返回缓存并显式标注（from_cache=True + cache_fallback_after=<失败态>），
    绝不伪装成刚刚检索成功。无缓存则如实失败。
    """

    def __init__(self, inner: Any, cache_dir: Path | str) -> None:
        self.inner = inner
        self.cache_dir = Path(cache_dir)

    def describe(self) -> Any:
        return self.inner.describe()

    def search(self, query_str: str, page_size: int = 25) -> list[CandidateEvidence]:
        return self.inner.search(query_str, page_size)  # 透传（不缓存）

    def query(self, query_str: str, page_size: int = 25) -> ExternalQueryResult:
        desc = self.inner.describe()
        key = hashlib.sha256(
            f"{desc.source_id}|{query_str}|{page_size}".encode("utf-8")).hexdigest()[:20]
        cache = self.cache_dir / f"{key}.json"
        live = query(self.inner, query_str, page_size)
        if live.status in RESULT_OK:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(live.to_dict(), ensure_ascii=False, indent=1),
                             encoding="utf-8")
            return live  # live 成功：from_cache=False（默认）
        if cache.is_file():  # live 失败 + 缓存在场：明示缓存兜底
            cached = ExternalQueryResult.from_dict(
                json.loads(cache.read_text(encoding="utf-8")))
            # live 失败态由 cache_fallback_after 承载（正当结果不带 error）；
            # from_cache=True + 原始 retrieved_at/raw_hash 保留——绝不伪装 live
            return ExternalQueryResult(
                status=cached.status, source_id=cached.source_id,
                query=cached.query, retrieved_at=cached.retrieved_at,
                raw_hash=cached.raw_hash, evidence_items=cached.evidence_items,
                provenance=cached.provenance, from_cache=True,
                cache_fallback_after=live.status)
        return live  # 无缓存 → 如实失败


def multi_query(adapters: list[Any], query_str: str,
                page_size: int = 25) -> dict[str, Any]:
    """多源查询编排：逐源保留状态（silent masking = 0），不合并不裁决。

    汇总口径：has_usable_evidence 只看正当结果里的非空证据——部分源失败
    不拖垮整体结论，也不把失败源静默丢弃；全部证据保持 candidate 级
    （升级只走 curated ingestion / 人工审查）。
    """
    results = [query(a, query_str, page_size) for a in adapters]
    return {
        "query": query_str,
        "results": results,
        "sources_ok": [r.source_id for r in results if r.status in RESULT_OK],
        "sources_failed": {r.source_id: r.status for r in results
                           if r.status not in RESULT_OK},
        "has_usable_evidence": any(r.evidence_items for r in results),
        "evidence_status": "candidate",
        "n_evidence_items": sum(len(r.evidence_items) for r in results),
    }


def detect_conflicts(report: dict[str, Any],
                     key_fn: Callable[[CandidateEvidence], str | None],
                     value_fn: Callable[[CandidateEvidence], str | None]) -> dict[str, Any]:
    """跨源冲突检测：同 key 不同结论（来自不同 source）→ conflict state。

    原则：保留全部 provenance、不强行平均、不隐藏冲突、不自动选择——
    每个冲突标注 manual_review_required，裁决权在人工领域判断。
    """
    groups: dict[str, dict[tuple[str, str], list[str]]] = {}
    for r in report.get("results", []):
        for ev in r.evidence_items:
            k, v = key_fn(ev), value_fn(ev)
            if k is None or v is None:
                continue
            groups.setdefault(k, {}).setdefault((r.source_id, v), []).append(
                ev.evidence_id)
    conflicts = []
    for k, positions in groups.items():
        values = {v for (_, v) in positions}
        sources = {s for (s, _) in positions}
        if len(values) > 1 and len(sources) > 1:
            conflicts.append({
                "key": k,
                "positions": [{"source_id": s, "value": v, "evidence_ids": ids}
                              for (s, v), ids in sorted(positions.items())],
                "resolution": "manual_review_required"})
    return {"conflict_detected": bool(conflicts), "conflicts": conflicts,
            "items_retained": report.get("n_evidence_items", 0),
            "auto_resolution": "disabled"}


__all__ = ["CachedSource", "ExternalQueryResult", "RESULT_OK", "STATUSES",
           "classify_exception", "detect_conflicts", "multi_query", "query"]
