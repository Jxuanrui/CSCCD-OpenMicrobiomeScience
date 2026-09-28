"""Method Knowledge 检索（P0-3 Method KB 最小可用版）。

规则正文以固定键 YAML 存于 KnowledgeStore Entry.content（package=methods）：
trigger / risk / action / contraindication / evidence_source / validation_status /
provenance；旧自由文本条目以 legacy 兼容返回。检索走 KnowledgeStore 的 FTS5
（jieba 中文分词），Agent 侧入口为研究循环 method_query 动作——方法是"可检索
的知识"而非静态文档。条目源文件在 mra/knowledge/methods/*.yaml（版本化）。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .store import KnowledgeStore

DEFAULT_DB_PATH = Path(__file__).resolve().parents[3] / "var" / "knowledge" / "knowledge.db"
METHOD_PACKAGE = "methods"
METHOD_KEYS = ("trigger", "risk", "action", "contraindication",
               "evidence_source", "validation_status", "provenance")


def parse_rule(entry) -> dict:
    """把 methods 条目解析为统一结构；旧自由文本返回 legacy 标记。"""
    base = {"rule_id": entry.id, "title": entry.title,
            "applicable_data": entry.applicability,
            "evidence_level": entry.evidence_level, "version": entry.version,
            "sources": entry.source}
    try:
        content = yaml.safe_load(entry.content)
    except yaml.YAMLError:
        content = None
    if isinstance(content, dict) and METHOD_KEYS[:-1] <= tuple(content):
        base.update({k: content[k] for k in METHOD_KEYS if k in content})
        base["structured"] = True
    else:
        base["content"] = entry.content
        base["structured"] = False  # legacy 自由文本（seed 期 4 条）
    return base


def ingest_method_dir(yaml_dir: Path, db_path: Path | None = None) -> int:
    """把目录下全部方法规则 YAML ingest 入库，返回条数（幂等，同 id 覆盖）。"""
    store = KnowledgeStore(str(db_path or DEFAULT_DB_PATH))
    n = 0
    for path in sorted(Path(yaml_dir).glob("*.yaml")):
        store.ingest(path)
        n += 1
    store.close()
    return n


def search_method_rules(query: str, k: int = 5,
                        db_path: Path | None = None) -> list[dict]:
    """FTS 召回 + 朴素词频重排（store 的 FTS 按 id 序返回，无相关性排序）。"""
    store = KnowledgeStore(str(db_path or DEFAULT_DB_PATH))
    try:
        hits = store.search(query, package=METHOD_PACKAGE)
    finally:
        store.close()
    terms = [t for t in query.split() if t.strip()]
    def score(hit) -> int:
        text = f"{hit.entry.title} {hit.entry.applicability} {hit.entry.content}"
        return sum(text.count(t) for t in terms)
    ranked = sorted(hits, key=lambda h: (-score(h), h.entry.id))
    return [parse_rule(hit.entry) for hit in ranked[:k]]


__all__ = ["METHOD_KEYS", "ingest_method_dir", "parse_rule", "search_method_rules"]
