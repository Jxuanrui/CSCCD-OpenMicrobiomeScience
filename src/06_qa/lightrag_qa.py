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
MERGED = Path(os.environ.get("KG_MERGED_DIR", str(ROOT / "data" / "merged" / "candidate_v3")))
NODES = MERGED / "merged_nodes.tsv"
EDGES = MERGED / "merged_edges.tsv"
WORKDIR = ROOT / "data" / "rag"
# 多语言嵌入（中英查询均可检索英文实体名；纯英文模型会让中文查询失效）
EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
PRED_CN = {"increases_abundance_in": "在…中丰度升高", "decreases_abundance_in": "在…中丰度降低",
           "alleviates": "缓解", "aggravates": "加重", "produces": "产生", "consumes": "消耗",
           "affects": "影响", "regulates_host_gene": "调控宿主基因", "modulates_host_gene": "调节宿主基因",
           "sensitive_to": "对…敏感", "participates_in": "参与…通路", "biotransforms": "生物转化"}

_st = None

# 接地约束：只允许基于图谱上下文作答并引用证据，禁止参数知识补白。
# 逐字引用规则（2026-10-07 实测模型会用预训练知识编造 PMID/谓词后收紧）：
# PMIDs/PMC 必须逐字出自上下文，上下文没有该证据的断言整条丢弃。
GROUNDING_PROMPT_ZH = ("请用中文回答。你只能依据提供的上下文中的实体与关系作答，"
                       "逐条标注实体名与证据等级/PMID；引用的 PMID/PMC 编号必须逐字"
                       "出现在提供的上下文中——上下文里没有对应证据编号的断言必须整条"
                       "省略，不得凭记忆补编号或谓词；上下文证据不足时明确说明"
                       "'知识库中证据不足'；严禁用上下文之外的知识补充答案。")
GROUNDING_PROMPT_EN = ("Answer ONLY from the provided context (entities/relationships). "
                       "Cite entity names and evidence tier/PMID per claim. Every cited "
                       "PMID/PMC identifier must appear VERBATIM in the provided context — "
                       "omit any claim whose evidence ID is not literally present; never "
                       "reconstruct IDs, predicates, or associations from memory. State "
                       "clearly when context is insufficient.")


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
    # 端点优先级：RAG_LLM_*（专用覆盖）→ BIGMODEL（GLM 官方）→ DEEPSEEK_OFFICIAL → OPENAI 兼容
    _ENDPOINTS = [
        ("RAG_LLM_BASE_URL", "RAG_LLM_KEY", "RAG_LLM_MODEL"),
        ("BIGMODEL_API_BASE", "BIGMODEL_KEY", "BIGMODEL_MODEL"),
        ("DEEPSEEK_OFFICIAL_BASE_URL", "DEEPSEEK_OFFICIAL_KEY", "DEEPSEEK_OFFICIAL_MODEL"),
        ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"),
    ]
    base = key = None
    model = "glm-5.3"  # flash 偶发空响应，默认主模型
    for bvar, kvar, mvar in _ENDPOINTS:
        base, key = os.getenv(bvar), os.getenv(kvar)
        if base and key:
            model = os.getenv(mvar) or model
            break
    if not (base and key):
        raise SystemExit("请在 .env 设置 BIGMODEL_* / DEEPSEEK_OFFICIAL_* / OPENAI_API_KEY 之一（含 base_url 与 key）")

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


def entity_evidence(question, limit=40):
    """实体子图确定性证据：问题文本匹配节点名 → 从 TSV 拉 1 跳边（携带真实 PMID）。

    LightRAG 向量检索能取到正确证据但 GLM 不遵守逐字引用（2026-10-07 实测会用
    预训练记忆替换上下文中的 PMID），实体型问题改走本路径：证据清单由代码生成，
    引用编号与 PMID 全部可回查。
    """
    nodes = pd.read_csv(NODES, sep="\t").fillna("")
    edges = pd.read_csv(EDGES, sep="\t").fillna("")
    name = dict(zip(nodes["id"], nodes["name"]))
    q = question.lower()
    hit_ids = {}
    for r in nodes.to_dict("records"):
        n = str(r["name"])
        if len(n) >= 4 and n.lower() in q:
            hit_ids[r["id"]] = n
    if not hit_ids:
        return None, hit_ids
    lines = []
    for r in edges.to_dict("records"):
        s, o = r["subject"], r["object"]
        if s in hit_ids or o in hit_ids:
            if len(lines) >= limit:
                break
            pred_cn = PRED_CN.get(r["predicate"], r["predicate"])
            lines.append(f"E{len(lines)+1}: {name.get(s, s)} {pred_cn} {name.get(o, o)}"
                         f"（tier={r.get('evidence_tier','')}, "
                         f"pmid={r.get('pmids','') or '无'}, "
                         f"source={r.get('source_id','')}）")
    return ("\n".join(lines) if lines else None), hit_ids


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
        evidence, hits = entity_evidence(q)
        if evidence:
            # 确定性路径：证据清单即答案边界，禁止引用清单外的编号
            prompt = (f"{q}\n\n可用证据清单（回答的唯一依据；逐条引用 E 编号，"
                      f"PMID 必须逐字取自对应条目；清单不足以回答时明确说明"
                      f"'知识库中证据不足'）：\n{evidence}")
            out = await rag.llm_model_func(prompt, system_prompt=sys_prompt)
            print(out)
        else:
            # 向量路径（非实体问题）：预置关键词跳过 LLM 关键词抽取——GLM 偶发
            # 返回 markdown 列表导致解析失败、检索空转；多语言嵌入允许整句问题
            # 直接作为向量检索关键词，mix 模式同时覆盖实体与关系分支。
            param = QueryParam(mode=mode, top_k=60, ll_keywords=[q], hl_keywords=[q])
            print(await rag.aquery(q, param=param, system_prompt=sys_prompt))
    finally:
        await rag.finalize_storages()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest")
    a = sub.add_parser("ask")
    a.add_argument("question")
    a.add_argument("--mode", default="mix", choices=["mix", "hybrid", "local", "global", "naive"])
    args = ap.parse_args()
    asyncio.run(ingest() if args.cmd == "ingest" else ask(args.question, args.mode))


if __name__ == "__main__":
    main()
