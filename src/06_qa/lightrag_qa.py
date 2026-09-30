#!/usr/bin/env python3
"""LightRAG 问答层：把主图 TSV 实体/边以 custom KG 导入 LightRAG（成熟轮子，
不做二次抽取），提供 hybrid 检索问答。

存储用 LightRAG 默认 JSON+NetworkX 后端：Neo4j Community 版仅支持单用户库，
复用主图实例会污染图谱（README 有记录）。
LLM 走 OpenAI 兼容端点（环境变量），嵌入用本地 sentence-transformers。

用法:
  python3 lightrag_qa.py ingest                 # 从 merged TSV 构建索引（幂等重建工作区）
  python3 lightrag_qa.py ask "普拉梭菌与哪些疾病相关？"
"""
import argparse
import asyncio
import re
import os
import shutil
from functools import partial
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
NODES = ROOT / "data" / "merged" / "merged_nodes.tsv"
EDGES = ROOT / "data" / "merged" / "merged_edges.tsv"
WORKDIR = ROOT / "data" / "rag"
# 多语言嵌入（中英查询均可检索英文实体名；纯英文模型会让中文查询失效）
EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
PRED_CN = {"increases_abundance_in": "在…中丰度升高", "decreases_abundance_in": "在…中丰度降低",
           "alleviates": "缓解", "aggravates": "加重", "produces": "产生", "consumes": "消耗",
           "affects": "影响", "regulates_host_gene": "调控宿主基因", "modulates_host_gene": "调节宿主基因",
           "sensitive_to": "对…敏感", "participates_in": "参与…通路", "biotransforms": "生物转化"}

_st = None

# 接地约束：只允许基于图谱上下文作答并引用证据，禁止参数知识补白。
GROUNDING_PROMPT_ZH = ("请用中文回答。你只能依据提供的上下文中的实体与关系作答，"
                       "逐条标注实体名与证据等级/PMID；上下文证据不足时明确说明"
                       "'知识库中证据不足'；严禁用上下文之外的知识补充答案。")
GROUNDING_PROMPT_EN = ("Answer ONLY from the provided context (entities/relationships). "
                       "Cite entity names and evidence tier/PMID per claim; state clearly "
                       "when context is insufficient. Never use outside knowledge.")


def embedder():
    global _st
    if _st is None:
        from sentence_transformers import SentenceTransformer
        _st = SentenceTransformer(EMBED_MODEL, device="cpu")
    return _st


async def embed_func(texts):
    return embedder().encode(texts, normalize_embeddings=True)


def build_rag():
    from lightrag import LightRAG
    from lightrag.base import EmbeddingFunc
    from lightrag.llm.openai import openai_complete_if_cache
    base, key = os.getenv("OPENAI_BASE_URL"), os.getenv("OPENAI_API_KEY")
    model = os.getenv("RAG_LLM_MODEL", "glm-5.3")  # flash 偶发空响应，默认主模型
    if not base or not key:
        raise SystemExit("请设置 OPENAI_API_KEY / OPENAI_BASE_URL")

    async def llm_func(prompt, system_prompt=None, history_messages=None, **kw):
        # 上游端点偶发空响应/429（与批量任务共享 key 时会撞并发上限），统一退避重试。
        out = None
        for attempt in range(6):
            try:
                out = await openai_complete_if_cache(
                    prompt=prompt, system_prompt=system_prompt,
                    history_messages=history_messages or [], model=model,
                    base_url=base, api_key=key, **kw)
            except Exception:
                out = None
            if out:
                return out
            await asyncio.sleep(min(30, 2 ** attempt))
        return out

    WORKDIR.mkdir(parents=True, exist_ok=True)
    return LightRAG(
        working_dir=str(WORKDIR),
        llm_model_func=llm_func,
        embedding_func=EmbeddingFunc(embedding_dim=384, max_token_size=512, func=embed_func),
    )


def load_custom_kg():
    nodes = pd.read_csv(NODES, sep="\t").fillna("")
    edges = pd.read_csv(EDGES, sep="\t").fillna("")
    name = dict(zip(nodes["id"], nodes["name"]))
    cat = dict(zip(nodes["id"], nodes["category"]))
    entities = [{"entity_id": i, "entity_name": str(n), "type": str(c),
                 "description": f"{n}（{c} 类型实体，ID {i}）", "source_id": "ROOT"}
                for i, n, c in zip(nodes["id"], nodes["name"], nodes["category"])]
    rels = []
    for r in edges.to_dict("records"):
        s, o = r["subject"], r["object"]
        if s not in name or o not in name:
            continue
        pred_cn = PRED_CN.get(r["predicate"], r["predicate"].replace("_", " "))
        desc = (f"{name[s]} {pred_cn} {name[o]}"
                f"（证据等级 {r.get('evidence_tier','')}，来源 {r.get('source_type','')}，"
                f"PMID: {r.get('pmids','') or '无'}）")
        try:
            w = max(1.0, float(r.get("confidence") or 1.0) * 10.0)
        except (TypeError, ValueError):
            w = 5.0
        rels.append({"src_id": s, "tgt_id": o, "description": desc,
                     "keywords": r["predicate"], "weight": w})
    print(f"[kg] entities={len(entities)} relationships={len(rels)}")
    return {"entities": entities, "relationships": rels}


async def ingest():
    if WORKDIR.exists():  # 幂等：全量重建
        shutil.rmtree(WORKDIR)
    rag = build_rag()
    kg = load_custom_kg()
    await rag.initialize_storages()
    await rag.ainsert_custom_kg(kg)
    await rag.finalize_storages()
    write_layer_identity(kg)
    print(f"[out] LightRAG 索引完成 -> {WORKDIR}")


def write_layer_identity(kg):
    """Phase 2 vecstore 层标识（2026-09-30）：向索引工作区写自描述文件。

    消费方（Router/审计）据此确认该向量库是 Local KG 的派生检索面，
    且不包含 research_evidence/live_knowledge/method_knowledge 域数据。
    """
    import json
    from datetime import datetime, timezone

    edges = pd.read_csv(EDGES, sep="\t", dtype=str).fillna("")
    layers = sorted(set(edges.get("knowledge_layer", pd.Series(dtype=str))))
    identity = {
        "knowledge_architecture": "local_kg_derived_vector_index",
        "allowed_layers": ["local_kg_curated", "local_kg_llm_extracted"],
        "indexed_layers": layers,  # 空 = 旧 TSV 无该列（回填前构建）
        "forbidden_layers": ["live_knowledge", "research_evidence", "method_knowledge"],
        "source_nodes_tsv": str(NODES),
        "source_edges_tsv": str(EDGES),
        "entity_count": len(kg["entities"]),
        "relationship_count": len(kg["relationships"]),
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    (WORKDIR / "layer_identity.json").write_text(
        json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[identity] 层标识写入 {WORKDIR / 'layer_identity.json'}（indexed_layers={layers}）")


async def ask(question, mode):
    from lightrag import QueryParam
    rag = build_rag()
    await rag.initialize_storages()
    try:
        q, sys_prompt = question, GROUNDING_PROMPT_EN
        # 中文查询先译成英文：实体名/关键词匹配层是英文（MeSH 倒序名等），
        # 多语言嵌入只救了向量层；预翻译是通用修复。语言指令走 system_prompt
        # 避免污染检索关键词。
        if re.search(r"[\u4e00-\u9fff]", question):
            en = (await rag.llm_model_func(
                f"把下面的问题翻译成英文，只输出译文：\n{question}") or "").strip().splitlines()[0].strip()
            if en:
                q = en
                sys_prompt = GROUNDING_PROMPT_ZH
        print(await rag.aquery(q, param=QueryParam(mode=mode, top_k=100),
                               system_prompt=sys_prompt))
    finally:
        await rag.finalize_storages()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest")
    a = sub.add_parser("ask")
    a.add_argument("question")
    a.add_argument("--mode", default="hybrid", choices=["hybrid", "mix", "local", "global", "naive"])
    args = ap.parse_args()
    asyncio.run(ingest() if args.cmd == "ingest" else ask(args.question, args.mode))


if __name__ == "__main__":
    main()
