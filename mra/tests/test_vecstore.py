"""向量层行为检查：入库/查询/覆盖（真实本地嵌入模型，~15s）。"""
from __future__ import annotations

from mra.vecstore import query, upsert_texts


def test_upsert_and_semantic_query(tmp_path):
    items = [
        {"id": "a", "text": "Faecalibacterium prausnitzii 产生 丁酸 抗炎", "source": "t", "meta": {}},
        {"id": "b", "text": "Albendazole 药物 驱虫", "source": "t", "meta": {}},
    ]
    assert upsert_texts(items, "smoke_t", db_dir=tmp_path) == 2
    hits = query("哪种菌产短链脂肪酸", "smoke_t", k=2, db_dir=tmp_path)
    assert hits and hits[0]["id"] == "a"


def test_upsert_overwrites_same_id(tmp_path):
    upsert_texts([{"id": "x", "text": "旧文本", "source": "t", "meta": {}}], "smoke_t2", db_dir=tmp_path)
    upsert_texts([{"id": "x", "text": "新文本 肥胖", "source": "t", "meta": {}}], "smoke_t2", db_dir=tmp_path)
    hits = query("肥胖", "smoke_t2", k=5, db_dir=tmp_path)
    assert len(hits) == 1 and hits[0]["text"].startswith("新文本")
