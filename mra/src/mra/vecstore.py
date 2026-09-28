"""向量混合检索层（LanceDB + fastembed 本地嵌入，零 API）。

两路数据源入库：
- 图谱实体（name+category+别名）→ kg_entities 表
- 文献速读缓存（标题+摘要）→ lit_papers 表
查询：语义 top-k，返回来源与原文片段，供研究循环/人用。
存储：var/vecstore/lancedb（gitignored）。
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_DB_DIR = Path(__file__).resolve().parents[3] / "var" / "vecstore" / "lancedb"
MODEL_NAME = "BAAI/bge-small-zh-v1.5"
_BATCH = 64


def _model():
    from fastembed import TextEmbedding

    return TextEmbedding(MODEL_NAME)


def _db(db_dir: Path | None = None):
    import lancedb

    return lancedb.connect(str(db_dir or DEFAULT_DB_DIR))


def _ensure_table(db, table: str):
    import pyarrow as pa

    if table not in db.table_names():
        schema = pa.schema([
            ("id", pa.string()), ("text", pa.string()), ("source", pa.string()),
            ("meta", pa.string()), ("vector", pa.list_(pa.float32(), 512))])
        db.create_table(table, schema=schema)


def upsert_texts(items: list[dict], table: str, db_dir: Path | None = None) -> int:
    """items: [{id, text, source, meta(dict)}]；同 id 覆盖（删旧插新）。"""
    if not items:
        return 0
    db = _db(db_dir)
    _ensure_table(db, table)
    tbl = db.open_table(table)
    old_ids = [i["id"] for i in items]
    try:
        tbl.delete(f"id IN ({','.join(repr(i) for i in old_ids)})")
    except Exception:  # noqa: BLE001 —— 空表/旧版本兼容
        pass
    texts = [i["text"] for i in items]
    vectors = [list(map(float, v)) for v in _model().embed(texts)]
    rows = [{"id": i["id"], "text": i["text"], "source": i["source"],
             "meta": json.dumps(i.get("meta", {}), ensure_ascii=False), "vector": vec}
            for i, vec in zip(items, vectors)]
    for start in range(0, len(rows), 200):
        tbl.add(rows[start:start + 200])
    return len(rows)


def query(text: str, table: str, k: int = 8, db_dir: Path | None = None) -> list[dict]:
    db = _db(db_dir)
    if table not in db.table_names():
        return []
    vec = list(map(float, next(iter(_model().embed([text])))))
    rows = (db.open_table(table).search(vec).limit(k).to_list())
    return [{"id": r["id"], "text": r["text"], "source": r["source"],
             "meta": json.loads(r["meta"]), "score": r.get("_distance")} for r in rows]


PRED_ZH = {
    "produces": "产生", "consumes": "消耗", "alleviates": "缓解", "aggravates": "加重",
    "increases_abundance_in": "在...中富集", "decreases_abundance_in": "在...中减少",
    "sensitive_to": "受...抑制", "biotransforms": "生物转化",
    "participates_in": "参与通路", "affects": "影响",
}


def index_graph_entities(graph, db_dir: Path | None = None,
                         max_nodes: int = 8000) -> int:
    """图谱实体批量入库：name+类别+别名 + 一跳事实（谓词中文化，携带语义）。"""
    from collections import defaultdict

    facts = defaultdict(list)
    for edge in graph.edges:
        zh = PRED_ZH.get(edge.predicate, edge.predicate)
        facts[edge.subject].append(f"{zh} {edge.object}")
        facts[edge.object].append(f"被{zh}于 {edge.subject}")
    items = []
    for node in list(graph.nodes.values())[:max_nodes]:
        text = f"{node.name}（{node.category}）"
        if node.aliases:
            text += " 别名:" + "/".join(node.aliases[:4])
        node_facts = facts.get(node.id, [])
        if node_facts:
            name_of = {nid: (graph.nodes[nid].name if graph.nodes.get(nid) else nid)
                       for nid in set(f.rsplit(" ", 1)[-1] for f in node_facts)}
            text += "。" + "；".join(
                f.rsplit(" ", 1)[0] + " " + name_of[f.rsplit(" ", 1)[-1]]
                for f in node_facts[:8])
        items.append({"id": node.id, "text": text[:500], "source": "kg",
                      "meta": {"category": node.category}})
    return upsert_texts(items, "kg_entities", db_dir)


def index_litread_cache(cache_dir: Path, db_dir: Path | None = None) -> int:
    """文献速读缓存入库（每篇 title+abstract 截断）。"""
    items = []
    for path in sorted(Path(cache_dir).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for p in data.get("papers", []):
            if not p.get("title"):
                continue
            items.append({"id": f"pmid:{p['pmid']}", "source": "litread",
                          "text": f"{p['title']} {p.get('abstract', '')[:300]}",
                          "meta": {"query": data.get("query", "")}})
    return upsert_texts(items, "lit_papers", db_dir)
