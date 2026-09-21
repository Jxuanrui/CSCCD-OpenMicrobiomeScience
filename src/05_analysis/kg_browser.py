#!/usr/bin/env python3
"""Streamlit 图谱浏览器（对标 MINERVA 的 Web 可达性；本地 TSV 驱动，无需 Neo4j）。

用法: streamlit run src/05_analysis/kg_browser.py --server.headless true --server.port 8765
页面：单菌跨域扇出 / Louvain 社区 / 链接预测假设 / Tier-C 待审边。
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
from graph_analysis import EDGES, NODES, load_graph, resolve  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
PREDS = ROOT / "data/merged/link_predictions.tsv"
METRICS = ROOT / "data/merged/graph_metrics.json"
REVIEW = ROOT / "data/staging/llm_v2_review.tsv"

st.set_page_config(page_title="肠道菌群知识图谱浏览器", layout="wide", page_icon="🦠")
st.title("🦠 肠道菌群知识图谱浏览器")
st.caption("证据分级：A=策展实验数据 · B=LLM抽取且≥2篇支持 · C=单篇待审 · predicted=模型假设（非实测证据）")

TABS = st.tabs(["单菌跨域扇出", "社区（Louvain）", "链接预测（假设）", "Tier-C 待审边"])


def fanout(g, meta, nid, hops):
    import networkx as nx
    und = nx.Graph(g)
    dist = {nid: 0}
    frontier = [nid]
    for d in range(1, hops + 1):
        nxt = []
        for u in frontier:
            for v in und.neighbors(u):
                if v not in dist:
                    dist[v] = d
                    nxt.append(v)
        frontier = nxt
    rows = []
    for v, d in dist.items():
        if v == nid:
            continue
        direct = None
        if g.has_edge(nid, v):
            direct = next(iter(g[nid][v].values()))
        elif g.has_edge(v, nid):
            direct = next(iter(g[v][nid].values()))
        rows.append({"hop": d, "name": meta[v]["name"], "id": v,
                     "category": meta[v]["category"],
                     "predicate": direct["predicate"] if direct else "",
                     "tier": direct["tier"] if direct else "",
                     "pmids": direct["pmids"] if direct else "",
                     "confidence": direct["conf"] if direct else ""})
    return pd.DataFrame(rows)


with TABS[0]:
    col1, col2, col3 = st.columns([3, 1, 1])
    q = col1.text_input("微生物名称或 ID", value="Faecalibacterium prausnitzii")
    hops = col2.slider("跳数", 1, 3, 2)
    if st.button("查询", type="primary"):
        g, meta, _ = load_graph()
        nid = resolve(q, meta)
        if not nid:
            st.error(f"未找到实体：{q}")
        else:
            df = fanout(g, meta, nid, hops)
            st.success(f"{meta[nid]['name']}（{nid}）1-{hops} 跳扇出 {len(df)} 个邻居")
            c1, c2 = st.columns(2)
            c1.metric("按类别", df.category.value_counts().to_dict().__str__()[1:-1].replace("'", ""))
            c2.metric("按跳数", df.hop.value_counts().sort_index().to_dict().__str__()[1:-1].replace("'", ""))
            cats = st.multiselect("类别过滤", sorted(df.category.unique()),
                                  default=[c for c in ["Disease", "Metabolite", "Drug", "Gene", "Pathway"] if c in set(df.category)])
            show = df[df.category.isin(cats)].sort_values(["hop", "category", "name"])
            st.dataframe(show, use_container_width=True, height=520)
            st.download_button("下载 TSV", show.to_csv(sep="\t", index=False).encode(),
                               f"fanout_{nid.split(':')[-1]}.tsv", "text/tab-separated-values")

with TABS[1]:
    if METRICS.exists():
        import json
        m = json.loads(METRICS.read_text(encoding="utf-8"))
        comms = m.get("louvain_communities_top15", [])
        pick = st.selectbox("选择社区", comms,
                            format_func=lambda c: f"社区{c['id']}（{c['size']}节点 · {c['categories']}")
        st.write("代表成员：")
        st.write(pick["top_members"])

with TABS[2]:
    if PREDS.exists():
        p = pd.read_csv(PREDS, sep="\t")
        st.caption(f"RotatE 链接预测 Top {len(p)}（evidence=predicted，仅作假设线索，不得作为已证实结论引用）")
        s = st.text_input("按菌名/病名过滤（子串）")
        show = p[p.apply(lambda r: s.lower() in str(r.to_dict()).lower(), axis=1)] if s else p
        st.dataframe(show, use_container_width=True, height=520)

with TABS[3]:
    if REVIEW.exists():
        r = pd.read_csv(REVIEW, sep="\t")
        st.caption(f"Tier-C 单篇文献边 {len(r)} 条（待审，不参与下游结论）")
        s2 = st.text_input("过滤（子串）", key="review_filter")
        show2 = r[r.apply(lambda row: s2.lower() in str(row.to_dict()).lower(), axis=1)] if s2 else r
        cols = [c for c in ["pmid", "subject_name", "predicate", "object_name",
                            "confidence", "evidence", "flag_or_override"] if c in show2.columns]
        st.dataframe(show2[cols], use_container_width=True, height=520)
